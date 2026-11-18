"""Frame matching: shift, rotation, scale, mirror and outline between two plate solutions.

Solutions are built from an independent model: a tangent point plus pixel = centre + A * (east,
north) in millidegrees, where A combines scale, an on-screen roll (clockwise positive, y down)
and an optional mirror.
"""
from __future__ import annotations

import ctypes as C
import math
import pathlib

import pytest

from native import DATA, MAX_STARS, H, W, SkySolution, Star, Wcs, port, port_instance

SCALE = 360 / 15.6  # pixels per degree: RAW2's 15.6 degree field
DEG = 10000  # 1e-4 degree units


class FrameMatch(C.Structure):
    _fields_ = [("separation", C.c_uint32), ("centre_visible", C.c_int),
                ("centre_q4", C.c_int32 * 2), ("arrow_q14", C.c_int32 * 2),
                ("rotation_ddeg", C.c_int), ("rotation_valid", C.c_int),
                ("scale_permille", C.c_int), ("mirrored", C.c_int),
                ("outline_valid", C.c_int), ("outline_q4", (C.c_int32 * 2) * 4),
                ("matched", C.c_int)]


@pytest.fixture(scope="module")
def lib():
    lib = port()
    lib.astro_frame_compare.argtypes = [C.POINTER(Wcs), C.POINTER(Wcs), C.c_int, C.c_int,
                                        C.POINTER(FrameMatch)]
    return lib


def solution(ra: float, dec: float, roll_deg: float = 0.0, scale: float = SCALE,
             mirror: bool = False) -> Wcs:
    """North up and east left at roll 0, like a normal sky view; centred on (ra, dec)."""
    k = scale / 1000  # pixels per millidegree
    c, s = math.cos(math.radians(roll_deg)), math.sin(math.radians(roll_deg))
    east = -1.0 if not mirror else 1.0
    a = [[k * c * east, k * s], [k * s * east, -k * c]]  # rotation applied to (east*x, -y)
    m = [round(a[0][0] * 2**24), round(a[0][1] * 2**24), round(a[1][0] * 2**24),
         round(a[1][1] * 2**24)]
    return Wcs(round(ra * DEG) % (360 * DEG), round(dec * DEG), (C.c_int32 * 4)(*m),
               (C.c_int32 * 2)(W * 65536 // 2, H * 65536 // 2))


def compare(lib, reference: Wcs, current: Wcs) -> FrameMatch:
    out = FrameMatch()
    assert lib.astro_frame_compare(C.byref(reference), C.byref(current), W, H, C.byref(out)) == 1
    return out


def test_identical_frames_match(lib):
    ref = solution(83.8, -5.4, roll_deg=20)
    out = compare(lib, ref, ref)
    assert out.matched and not out.mirrored
    assert out.separation <= 2 and abs(out.rotation_ddeg) <= 1
    assert abs(out.scale_permille - 1000) <= 2
    corners = [(0, 0), (W, 0), (W, H), (0, H)]
    for (x, y), (qx, qy) in zip(corners, out.outline_q4):
        assert abs(qx / 16 - x) < 1 and abs(qy / 16 - y) < 1


@pytest.mark.parametrize("roll", [5, -5, 30, -120, 179])
def test_rotation_sign_and_size(lib, roll):
    """Content rotated clockwise on screen reports a positive rotation."""
    out = compare(lib, solution(310.0, 42.0), solution(310.0, 42.0, roll_deg=roll))
    expected = (roll + 180) % 360 - 180
    assert abs(out.rotation_ddeg - expected * 10) <= 2, out.rotation_ddeg
    assert out.separation <= 2
    assert out.matched == (abs(expected) * 10 <= 5)


def test_rotated_outline_turns_about_the_centre(lib):
    roll = 10
    out = compare(lib, solution(10.7, 41.3), solution(10.7, 41.3, roll_deg=roll))
    c, s = math.cos(math.radians(roll)), math.sin(math.radians(roll))
    for (x, y), (qx, qy) in zip([(0, 0), (W, 0), (W, H), (0, H)], out.outline_q4):
        dx, dy = x - W / 2, y - H / 2
        assert abs(qx / 16 - (W / 2 + c * dx - s * dy)) < 1.5
        assert abs(qy / 16 - (H / 2 + s * dx + c * dy)) < 1.5


def test_shift_points_back_to_the_reference(lib):
    """The camera moved 1 degree north: the reference centre now lies below the middle."""
    out = compare(lib, solution(83.8, -5.4), solution(83.8, -4.4))
    assert abs(out.separation - DEG) <= 20
    assert out.centre_visible and not out.matched
    assert abs(out.centre_q4[0] / 16 - W / 2) < 1
    assert abs(out.centre_q4[1] / 16 - (H / 2 + SCALE)) < 1
    assert out.arrow_q14[1] > 16000 and abs(out.arrow_q14[0]) < 400


def test_small_shift_still_matches(lib):
    out = compare(lib, solution(83.8, -5.4), solution(83.8, -5.35))
    assert out.matched and out.separation <= 520


def test_different_lens_is_reported(lib):
    """A 1.5x longer lens shows two thirds of the field."""
    out = compare(lib, solution(279.2, 38.8), solution(279.2, 38.8, scale=SCALE * 1.5))
    assert abs(out.scale_permille - 667) <= 5


def test_mirrored_image_never_matches(lib):
    out = compare(lib, solution(279.2, 38.8), solution(279.2, 38.8, mirror=True))
    assert out.mirrored and not out.matched


def test_far_away_reference_still_gives_a_direction(lib):
    """Beyond the tangent-plane limit only the arrow is usable."""
    out = compare(lib, solution(0.0, 0.0), solution(180.0, 10.0))
    assert out.separation > 160 * DEG and not out.centre_visible and not out.outline_valid
    assert not out.rotation_valid and not out.matched
    assert out.arrow_q14[0] or out.arrow_q14[1]


def test_near_the_pole(lib):
    out = compare(lib, solution(37.9, 89.3), solution(37.9, 89.3, roll_deg=3))
    assert abs(out.rotation_ddeg - 30) <= 3 and out.separation <= 3


@pytest.mark.skipif(not (DATA / "SKY.DIR").exists(), reason="data missing: run `astroml data all`")
def test_real_solution_against_itself_rotated(lib):
    """RAW2's own solution, turned 5 degrees about the image centre."""
    solver = port_instance("frame_match")
    assert solver.harness_open_sky(str(DATA).encode(), b"SKY") == 0
    solver.harness_detect.argtypes = [C.c_char_p, C.c_int, C.c_int, C.c_int, C.POINTER(Star),
                                      C.c_int]
    frame = (pathlib.Path(__file__).parent / "data" / "RAW2.PGM").read_bytes()[-W * H:]
    stars = (Star * MAX_STARS)()
    count = solver.harness_detect(C.create_string_buffer(frame, W * H), W, H, W, stars, MAX_STARS)
    sol = SkySolution()
    assert solver.harness_solve(stars, count, W, H, None, C.byref(sol)) == 1

    ref = sol.wcs
    c, s = math.cos(math.radians(5)), math.sin(math.radians(5))
    m = list(ref.m)
    rotated = [round(c * m[0] - s * m[2]), round(c * m[1] - s * m[3]),
               round(s * m[0] + c * m[2]), round(s * m[1] + c * m[3])]
    # keep the image centre on the same sky point: t' = centre + R (t - centre)
    cx, cy = W * 65536 / 2, H * 65536 / 2
    tx, ty = ref.t[0] - cx, ref.t[1] - cy
    cur = Wcs(ref.ra0, ref.dec0, (C.c_int32 * 4)(*rotated),
              (C.c_int32 * 2)(round(cx + c * tx - s * ty), round(cy + s * tx + c * ty)))
    out = compare(lib, ref, cur)
    assert abs(out.rotation_ddeg - 50) <= 2 and out.separation <= 20 and not out.matched


def from_pointing(lib, ra, dec, north_ddeg, fov_cdeg, mirrored=False):
    lib.astro_wcs_from_pointing.argtypes = [C.c_uint32, C.c_int32, C.c_int32, C.c_int32, C.c_int,
                                            C.c_int, C.c_int, C.POINTER(Wcs)]
    out = Wcs()
    assert lib.astro_wcs_from_pointing(round(ra * DEG), round(dec * DEG), north_ddeg, fov_cdeg,
                                       int(mirrored), W, H, C.byref(out)) == 1
    return out


@pytest.mark.parametrize("north", [0, 125, -450, 1790])
def test_pointing_round_trip(lib, north):
    """Built from RA/Dec/north/FOV, the solution reports the same north, FOV and centre."""
    lib.astro_ptp_north_from_up.argtypes = [C.POINTER(Wcs), C.c_int, C.c_int, C.POINTER(C.c_int32)]
    wcs = from_pointing(lib, 279.23, 38.78, north, 1563)
    angle = C.c_int32()
    assert lib.astro_ptp_north_from_up(C.byref(wcs), W, H, C.byref(angle)) == 1
    assert abs(angle.value - north) <= 2
    assert abs(lib.astro_wcs_fov_cdeg(C.byref(wcs), W, H) - 1563) <= 2
    same = compare(lib, wcs, wcs)
    assert same.matched


def test_pointing_matches_the_test_model(lib):
    """Same as the independent model at roll 0 (north up, east left)."""
    fov = 2 * math.degrees(math.atan(W / 2 / SCALE / math.degrees(1)))
    ref = solution(83.8, -5.4)
    built = from_pointing(lib, 83.8, -5.4, 0, round(fov * 100))
    out = compare(lib, ref, built)
    assert out.separation <= 2 and abs(out.rotation_ddeg) <= 1 and not out.mirrored
    assert abs(out.scale_permille - 1000) <= 3


def test_pointing_offset_is_found(lib):
    """A target 2 degrees east of the current view: frame match points there."""
    current = solution(83.8, -5.4)
    target = from_pointing(lib, 85.8, -5.4, 0, 1560)
    out = compare(lib, target, current)
    assert abs(out.separation / DEG - 2 * math.cos(math.radians(5.4))) < 0.02
    assert out.arrow_q14[0] < -15000  # east is to the left on an unmirrored view


def test_mirrored_pointing(lib):
    out = compare(lib, from_pointing(lib, 10.7, 41.3, 0, 1500),
                  from_pointing(lib, 10.7, 41.3, 0, 1500, mirrored=True))
    assert out.mirrored
