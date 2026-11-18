"""The record parser, including a contract test against the camera's own C packer."""
from __future__ import annotations

import ctypes as C
import datetime as dt
import pathlib
import struct
import sys

import pytest

from astroml_companion import protocol

REPO = pathlib.Path(__file__).resolve().parents[2]


def record_bytes(**fields) -> bytes:
    values = {"kind": 1, "flags": 1 | (1 << 8), "ra": 852317, "dec": -11833, "fov": 1563,
              "north": -125, "matched": 19, "elapsed": 360, "solved": 832_000_000, "sequence": 4}
    values.update(fields)
    head = struct.pack("<4sHHIIiiiiIqI", b"ASTR", 1, values["kind"], values["flags"],
                       values["ra"], values["dec"], values["fov"], values["north"],
                       values["matched"], values["elapsed"], values["solved"], values["sequence"])
    wcs = struct.pack("<Ii4i2i", 852317, -11833, -301310, -238023, 238023, -301310, 11788350,
                      7861905)
    return head + wcs + b"6D".ljust(16, b"\0")


def test_parse():
    r = protocol.parse_record(record_bytes())
    assert r.valid and r.source == "gallery" and r.camera == "6D" and r.sequence == 4
    assert r.ra_deg == pytest.approx(85.2317) and r.dec_deg == pytest.approx(-1.1833)
    assert r.fov_deg == pytest.approx(15.63) and r.north_deg == pytest.approx(-12.5)
    assert r.solved_utc == protocol.J2000 + dt.timedelta(seconds=832_000_000)
    assert r.wcs[0] == 852317


def test_unknown_time_and_invalid_flag():
    r = protocol.parse_record(record_bytes(flags=0, solved=0))
    assert not r.valid and r.solved_utc is None


@pytest.mark.parametrize("bad", [b"", b"x" * 95, b"XXXX" + bytes(92)])
def test_rejects_other_data(bad):
    with pytest.raises(ValueError):
        protocol.parse_record(bad)


def test_formatting():
    assert protocol.ra_hms(85.2317) == "05h40m56s"
    assert protocol.dec_dms(-1.1833) == "-01°11'00\""
    assert protocol.dec_dms(41.269) == "+41°16'08\""


def test_matches_the_camera_packer():
    """Bytes produced by astro/core/ptp_pack.c parse to the same values here."""
    sys.path.insert(0, str(REPO / "tests"))
    sys.path.insert(0, str(REPO))
    try:
        from native import Wcs, port
        from test_ptp_pack import PtpRecord
    except Exception as error:  # the AstroML host build is not available
        pytest.skip(f"AstroML host library unavailable: {error}")
    lib = port()
    lib.astro_ptp_pack.argtypes = [C.POINTER(PtpRecord), C.c_char_p]
    wcs = Wcs(852317, -11833, (C.c_int32 * 4)(-301310, -238023, 238023, -301310),
              (C.c_int32 * 2)(11788350, 7861905))
    out = C.create_string_buffer(protocol.RECORD_BYTES)
    lib.astro_ptp_pack(C.byref(PtpRecord(2, 1, 1, 852317, -11833, 1563, -125, 19, 360,
                                         832_000_000, 4, wcs, b"6D")), out)
    r = protocol.parse_record(out.raw)
    assert (r.kind, r.valid, r.source, r.camera, r.sequence) == (2, True, "gallery", "6D", 4)
    assert (r.ra_deg, r.dec_deg, r.fov_deg, r.north_deg) == (85.2317, -1.1833, 15.63, -12.5)
    assert r.wcs == (852317, -11833, -301310, -238023, 238023, -301310, 11788350, 7861905)


def test_status_matches_the_camera_packer():
    sys.path.insert(0, str(REPO / "tests"))
    sys.path.insert(0, str(REPO))
    try:
        from native import port
        from test_ptp_pack import PtpStatus
    except Exception as error:
        pytest.skip(f"AstroML host library unavailable: {error}")
    lib = port()
    lib.astro_ptp_pack_status.argtypes = [C.POINTER(PtpStatus), C.c_char_p]
    out = C.create_string_buffer(protocol.RECORD_BYTES)
    lib.astro_ptp_pack_status(C.byref(PtpStatus(1, 12, 1, 2, 5, 120000, 100, 184, 1, 1, 1, 870,
                                                 -32, 9, 3, b"6D")), out)
    s = protocol.parse_status(out.raw)
    assert (s.busy, s.liveview_ready, s.match_running, s.matched, s.rotation_valid) == (True,) * 5
    assert (s.exposure_state, s.exposure_number, s.exposure_ms, s.photo) == ("done", 5, 120000,
                                                                             "100-0184")
    assert (s.separation_deg, s.rotation_deg, s.solution_sequence, s.panel, s.camera) == (
        0.087, -3.2, 9, 3, "6D")


def test_target_params():
    sub, ra, dec, north_fov, panel = protocol.target_params(303.0273, -38.3549, -12.5, 15.63, 2)
    assert sub == protocol.SET_TARGET and ra == 3030273 and dec == (-383549) & 0xFFFFFFFF
    assert north_fov & 0xFFFF == (-125) & 0xFFFF and north_fov >> 16 == 1563 and panel == 2
    with pytest.raises(ValueError):
        protocol.target_params(0, 0, 0, 0.1, 0)
