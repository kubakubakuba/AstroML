"""Mosaic geometry: spacing, rotation, and north's turn across a mosaic near the pole."""
from __future__ import annotations

import math

import pytest

from astroml_companion.plan import Plan, panels, sky_to_tangent


def separation(ra1, dec1, ra2, dec2):
    a1, d1, a2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(a1 - a2)
    return math.degrees(math.acos(min(1.0, c)))


def test_single_panel_is_the_target():
    (p,) = panels(Plan(ra_deg=83.82, dec_deg=-5.39, north_deg=12.0), 15.6)
    assert (p.ra_deg, p.dec_deg) == pytest.approx((83.82, -5.39))
    assert p.north_deg == pytest.approx(12.0, abs=1e-6) and p.offset == (0.0, 0.0)


@pytest.mark.parametrize("overlap", [0.0, 0.2, 0.35])
def test_spacing_is_field_times_one_minus_overlap(overlap):
    """Neighbours sit (1 - overlap) frame widths apart on the target's tangent plane."""
    fov = 10.0
    plan = Plan(ra_deg=0.0, dec_deg=0.0, columns=3, rows=2, overlap=overlap)
    grid = panels(plan, fov)
    width = 2 * math.tan(math.radians(fov / 2))
    x = [sky_to_tangent(0, 0, p.ra_deg, p.dec_deg) for p in grid]
    assert abs(x[1][0] - x[0][0]) == pytest.approx(width * (1 - overlap), rel=1e-9)
    assert abs(x[3][1] - x[0][1]) == pytest.approx(width * 2 / 3 * (1 - overlap), rel=1e-9)
    assert [p.index for p in grid] == list(range(6))


def test_layout_follows_the_frame():
    """At rotation 0 north is up and east is left: column 0 is east of column 1, row 0 north."""
    grid = panels(Plan(ra_deg=180.0, dec_deg=10.0, columns=2, rows=2, overlap=0.1), 8.0)
    top_left, top_right, bottom_left = grid[0], grid[1], grid[2]
    assert top_left.ra_deg > top_right.ra_deg
    assert top_left.dec_deg > bottom_left.dec_deg


def test_rotation_turns_the_grid():
    """Rotated 90 degrees clockwise (north to the right), the columns run along declination."""
    grid = panels(Plan(ra_deg=180.0, dec_deg=0.0, north_deg=90.0, columns=2, overlap=0.0), 8.0)
    left, right = grid
    assert right.dec_deg > left.dec_deg
    assert left.ra_deg == pytest.approx(right.ra_deg, abs=1e-6)
    assert separation(left.ra_deg, left.dec_deg, right.ra_deg, right.dec_deg) == pytest.approx(
        2 * math.degrees(math.atan(math.tan(math.radians(4)))), abs=0.05)


def test_north_turns_across_a_mosaic_near_the_pole():
    grid = panels(Plan(ra_deg=0.0, dec_deg=85.0, columns=3, overlap=0.2), 10.0)
    assert grid[1].north_deg == pytest.approx(0.0, abs=1e-6)
    assert grid[0].north_deg == pytest.approx(-grid[2].north_deg, abs=1e-6)
    assert abs(grid[0].north_deg) > 3  # it matters: the camera must turn per panel


@pytest.mark.parametrize("field,value", [("dec_deg", 95), ("columns", 0), ("overlap", 0.95),
                                         ("exposure_s", 0), ("fov_deg", 200)])
def test_validation(field, value):
    with pytest.raises(ValueError):
        Plan(**{"ra_deg": 10.0, "dec_deg": 20.0, field: value}).validate()


@pytest.mark.parametrize("dec,north", [(0.0, 0.0), (41.3, 25.0), (86.0, -40.0)])
def test_panel_frames_are_parallel_on_the_camera(dec, north):
    """Each panel is turned into a camera solution with astro_wcs_from_pointing (what
    SET_TARGET runs). Mapped back onto the target's tangent plane, every frame's horizontal axis
    points the same way, so neighbouring frames tile without a wedge."""
    import ctypes as C
    import pathlib
    import sys

    repo = pathlib.Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(repo / "tests"), str(repo)]
    try:
        from native import H, W, Wcs, port
    except Exception as error:
        pytest.skip(f"AstroML host library unavailable: {error}")
    lib = port()
    lib.astro_wcs_from_pointing.argtypes = [C.c_uint32, C.c_int32, C.c_int32, C.c_int32, C.c_int,
                                            C.c_int, C.c_int, C.POINTER(Wcs)]
    lib.astro_wcs_unproject.argtypes = [C.POINTER(Wcs), C.c_int32, C.c_int32,
                                        C.POINTER(C.c_uint32), C.POINTER(C.c_int32)]
    fov = 10.0
    plan = Plan(ra_deg=120.0, dec_deg=dec, north_deg=north, columns=3, rows=2, overlap=0.2)
    directions = []
    for p in panels(plan, fov):
        wcs = Wcs()
        assert lib.astro_wcs_from_pointing(round(p.ra_deg * 1e4) % 3600000, round(p.dec_deg * 1e4),
                                           round(p.north_deg * 10), round(fov * 100), 0, W, H,
                                           C.byref(wcs)) == 1
        ends = []
        for x in (0, W * 16):  # left and right edge midpoints
            ra, de = C.c_uint32(), C.c_int32()
            assert lib.astro_wcs_unproject(C.byref(wcs), x, H * 8, C.byref(ra), C.byref(de))
            ends.append(sky_to_tangent(plan.ra_deg, plan.dec_deg, ra.value / 1e4, de.value / 1e4))
        (x0, y0), (x1, y1) = ends
        directions.append(math.atan2(y1 - y0, x1 - x0))
    spread = max(abs(math.remainder(d - directions[0], 2 * math.pi)) for d in directions)
    assert math.degrees(spread) < 0.12  # the camera stores north to 0.1 degree
