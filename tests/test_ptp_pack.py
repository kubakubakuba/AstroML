"""The USB (PTP) record: byte layout, endianness, and the north angle it reports."""
from __future__ import annotations

import ctypes as C
import math
import struct

import pytest

from native import H, W, Wcs, port

RECORD_BYTES = 96


class PtpRecord(C.Structure):
    _fields_ = [("kind", C.c_int), ("valid", C.c_int), ("source", C.c_int), ("ra", C.c_uint32),
                ("dec", C.c_int32), ("fov_cdeg", C.c_int32), ("north_ddeg", C.c_int32),
                ("matched", C.c_int32), ("elapsed_ms", C.c_uint32), ("solved_utc", C.c_int64),
                ("sequence", C.c_uint32), ("wcs", Wcs), ("camera", C.c_char * 16)]


@pytest.fixture(scope="module")
def lib():
    lib = port()
    lib.astro_ptp_pack.argtypes = [C.POINTER(PtpRecord), C.c_char_p]
    lib.astro_ptp_north_from_up.argtypes = [C.POINTER(Wcs), C.c_int, C.c_int,
                                            C.POINTER(C.c_int32)]
    return lib


def wcs_with_roll(roll_deg: float) -> Wcs:
    """North up, east left at roll 0; roll turns the sky clockwise on screen."""
    k = (360 / 15.6) / 1000
    c, s = math.cos(math.radians(roll_deg)), math.sin(math.radians(roll_deg))
    m = [round(v * 2**24) for v in (-k * c, k * s, -k * s, -k * c)]
    return Wcs(852300, -11620, (C.c_int32 * 4)(*m), (C.c_int32 * 2)(W * 65536 // 2, H * 65536 // 2))


def pack(lib, record: PtpRecord) -> bytes:
    out = C.create_string_buffer(RECORD_BYTES)
    lib.astro_ptp_pack(C.byref(record), out)
    return out.raw


def test_layout(lib):
    wcs = wcs_with_roll(0)
    record = PtpRecord(1, 1, 2, 852300, -11620, 1563, -45, 19, 360, -1234567890123, 7, wcs,
                       b"6D")
    data = pack(lib, record)
    assert len(data) == RECORD_BYTES and data[:4] == b"ASTR"
    layout, kind, flags = struct.unpack_from("<HHI", data, 4)
    assert (layout, kind, flags) == (1, 1, 1 | (2 << 8))
    assert struct.unpack_from("<IiiiiI", data, 12) == (852300, -11620, 1563, -45, 19, 360)
    assert struct.unpack_from("<qI", data, 36) == (-1234567890123, 7)
    assert struct.unpack_from("<Ii4i2i", data, 48) == (wcs.ra0, wcs.dec0, *wcs.m, *wcs.t)
    assert data[80:96] == b"6D" + bytes(14)


def test_long_camera_name_stays_terminated(lib):
    record = PtpRecord(camera=b"X" * 16)
    assert pack(lib, record)[95] == 0


@pytest.mark.parametrize("roll", [0, 30, -90, 170])
def test_north_from_up(lib, roll):
    """At roll 0 north points up; the sky turned clockwise by roll moves north clockwise."""
    out = C.c_int32()
    assert lib.astro_ptp_north_from_up(C.byref(wcs_with_roll(roll)), W, H, C.byref(out)) == 1
    assert abs(out.value - roll * 10) <= 2


def test_unusable_transform(lib):
    zero = Wcs(0, 0, (C.c_int32 * 4)(0, 0, 0, 0), (C.c_int32 * 2)(0, 0))
    assert lib.astro_ptp_north_from_up(C.byref(zero), W, H, C.byref(C.c_int32())) == 0


class PtpStatus(C.Structure):
    _fields_ = [("busy", C.c_int), ("running_action", C.c_int), ("liveview_ready", C.c_int),
                ("exposure_state", C.c_int), ("exposure_number", C.c_uint32),
                ("exposure_ms", C.c_uint32), ("photo_folder", C.c_int), ("photo_file", C.c_int),
                ("match_running", C.c_int), ("match_matched", C.c_int),
                ("match_rotation_valid", C.c_int), ("match_separation", C.c_uint32),
                ("match_rotation_ddeg", C.c_int32), ("solution_sequence", C.c_uint32),
                ("target_panel", C.c_int32), ("camera", C.c_char * 16)]


def test_status_layout(lib):
    lib.astro_ptp_pack_status.argtypes = [C.POINTER(PtpStatus), C.c_char_p]
    status = PtpStatus(1, 14, 1, 2, 5, 120000, 100, 184, 1, 1, 1, 870, -32, 9, 3, b"6D")
    out = C.create_string_buffer(RECORD_BYTES)
    lib.astro_ptp_pack_status(C.byref(status), out)
    data = out.raw
    assert data[:4] == b"ASTR" and struct.unpack_from("<HH", data, 4) == (1, 3)
    assert struct.unpack_from("<I", data, 8)[0] == 0b11111
    assert struct.unpack_from("<IIIIIIIiIi", data, 12) == (14, 2, 5, 120000, 100, 184, 870,
                                                            -32, 9, 3)
    assert data[80:82] == b"6D"
