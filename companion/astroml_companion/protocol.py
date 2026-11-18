"""AstroML's PTP record (layout 1). Mirrors astro/core/ptp_pack.{c,h}; keep both in step."""
from __future__ import annotations

import datetime as dt
import struct
from dataclasses import dataclass

OPCODE = 0xA570
VERSION, SOLUTION, REFERENCE, SOLVE, STATUS, EXPOSE, SET_TARGET, CANCEL = range(8)
LAYOUT = 1
API = 2  # status, exposures and panel targets need API 2
RECORD_BYTES = 96
SOURCES = ("Live View", "gallery", "file", "companion")
KIND_SOLUTION, KIND_REFERENCE, KIND_STATUS = 1, 2, 3
EXPOSURE_STATES = ("idle", "exposing", "done", "cancelled", "failed")
J2000 = dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc)

_FIXED = struct.Struct("<4sHHIIiiiiIqI")      # up to the WCS, 48 bytes
_WCS = struct.Struct("<Ii4i2i")               # ra0, dec0, m_q24[4], t_q16[2], 32 bytes
_STATUS = struct.Struct("<4sHHIIIIIIIIiIi")  # header + status fields, 52 bytes


@dataclass(frozen=True)
class Record:
    kind: int
    valid: bool
    source: str
    ra_deg: float
    dec_deg: float
    fov_deg: float
    north_deg: float          # north's direction, clockwise from screen up
    matched: int
    elapsed_ms: int
    solved_utc: dt.datetime | None
    sequence: int
    wcs: tuple[int, ...]
    camera: str


def parse_record(data: bytes) -> Record:
    if len(data) != RECORD_BYTES:
        raise ValueError(f"record is {len(data)} bytes, expected {RECORD_BYTES}")
    (magic, layout, kind, flags, ra, dec, fov, north, matched, elapsed, solved,
     sequence) = _FIXED.unpack_from(data, 0)
    if magic != b"ASTR" or layout != LAYOUT:
        raise ValueError(f"not an AstroML layout-{LAYOUT} record")
    source = (flags >> 8) & 0xFF
    return Record(
        kind=kind,
        valid=bool(flags & 1),
        source=SOURCES[source] if source < len(SOURCES) else f"source {source}",
        ra_deg=ra / 1e4,
        dec_deg=dec / 1e4,
        fov_deg=fov / 100,
        north_deg=north / 10,
        matched=matched,
        elapsed_ms=elapsed,
        solved_utc=J2000 + dt.timedelta(seconds=solved) if solved else None,
        sequence=sequence,
        wcs=_WCS.unpack_from(data, _FIXED.size),
        camera=data[80:96].split(b"\0", 1)[0].decode("ascii", "replace"),
    )


def ra_hms(deg: float) -> str:
    seconds = round(deg / 15 * 3600) % 86400
    return f"{seconds // 3600:02d}h{seconds // 60 % 60:02d}m{seconds % 60:02d}s"


def dec_dms(deg: float) -> str:
    arcsec = round(abs(deg) * 3600)
    sign = "-" if deg < 0 else "+"
    return f"{sign}{arcsec // 3600:02d}°{arcsec // 60 % 60:02d}'{arcsec % 60:02d}\""


@dataclass(frozen=True)
class CameraStatus:
    busy: bool
    running_action: int
    liveview_ready: bool
    exposure_state: str
    exposure_number: int
    exposure_ms: int
    photo: str                # "100-0184" after the last exposure, "" before any
    match_running: bool
    matched: bool
    rotation_valid: bool
    separation_deg: float
    rotation_deg: float
    solution_sequence: int
    panel: int                # -1: the reference is not a planned panel
    camera: str


def parse_status(data: bytes) -> CameraStatus:
    if len(data) != RECORD_BYTES:
        raise ValueError(f"status is {len(data)} bytes, expected {RECORD_BYTES}")
    (magic, layout, kind, flags, action, state, number, ms, folder, file, separation, rotation,
     sequence, panel) = _STATUS.unpack_from(data, 0)
    if magic != b"ASTR" or layout != LAYOUT or kind != KIND_STATUS:
        raise ValueError("not an AstroML status record")
    return CameraStatus(
        busy=bool(flags & 1), running_action=action, liveview_ready=bool(flags & 2),
        exposure_state=EXPOSURE_STATES[state] if state < len(EXPOSURE_STATES) else str(state),
        exposure_number=number, exposure_ms=ms,
        photo=f"{folder:03d}-{file:04d}" if number and file else "",
        match_running=bool(flags & 4), matched=bool(flags & 8), rotation_valid=bool(flags & 16),
        separation_deg=separation / 1e4, rotation_deg=rotation / 10, solution_sequence=sequence,
        panel=panel,
        camera=data[80:96].split(b"\0", 1)[0].decode("ascii", "replace"),
    )


def target_params(ra_deg: float, dec_deg: float, north_deg: float, fov_deg: float,
                  panel: int) -> tuple[int, int, int, int, int]:
    """SET_TARGET's five PTP parameters (see astro/core/ptp_pack.h)."""
    north = round(north_deg * 10)
    north = (north + 1800) % 3600 - 1800
    fov = round(fov_deg * 100)
    if not 20 <= fov <= 12000:
        raise ValueError(f"field of view {fov_deg} deg out of range")
    return (SET_TARGET, round(ra_deg % 360 * 1e4), round(dec_deg * 1e4) & 0xFFFFFFFF,
            (north & 0xFFFF) | (fov << 16), panel & 0xFFFFFFFF)
