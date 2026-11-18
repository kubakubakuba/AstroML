"""What the web page shows: the latest readings, shared between the poller and the server."""
from __future__ import annotations

import dataclasses
import datetime as dt
import threading

from .mount import MountStatus
from .protocol import CameraStatus, Record, dec_dms, ra_hms


def _record_json(record: Record | None) -> dict | None:
    if record is None or not record.valid:
        return None
    age = None
    if record.solved_utc is not None:
        age = round((dt.datetime.now(dt.timezone.utc) - record.solved_utc).total_seconds())
    return {
        "ra": ra_hms(record.ra_deg), "dec": dec_dms(record.dec_deg),
        "ra_deg": round(record.ra_deg, 4), "dec_deg": round(record.dec_deg, 4),
        "fov_deg": record.fov_deg, "north_deg": record.north_deg, "matched": record.matched,
        "elapsed_ms": record.elapsed_ms, "source": record.source, "sequence": record.sequence,
        "age_s": age,
    }


def _camera_status_json(s: CameraStatus | None) -> dict | None:
    if s is None:
        return None
    return {"busy": s.busy, "liveview_ready": s.liveview_ready,
            "exposure": {"state": s.exposure_state, "number": s.exposure_number,
                         "seconds": s.exposure_ms / 1000, "photo": s.photo},
            "match": {"running": s.match_running, "matched": s.matched,
                      "separation_deg": round(s.separation_deg, 2),
                      "rotation_deg": round(s.rotation_deg, 1) if s.rotation_valid else None,
                      "panel": s.panel + 1 if s.panel >= 0 else None}}


def _mount_json(m: MountStatus | None) -> dict | None:
    if m is None:
        return None
    return {"connected": m.connected, "detail": m.detail, "slewing": m.slewing,
            "tracking": m.tracking, "parked": m.parked,
            "ra": ra_hms(m.ra_deg) if m.ra_deg is not None else None,
            "dec": dec_dms(m.dec_deg) if m.dec_deg is not None else None}


@dataclasses.dataclass
class Status:
    connected: bool = False
    camera: str = ""
    layout: int | None = None
    solution: Record | None = None
    reference: Record | None = None
    camera_status: CameraStatus | None = None
    mount_status: MountStatus | None = None
    error: str = ""
    message: str = ""
    updated: dt.datetime | None = None


class SharedStatus:
    def __init__(self):
        self._lock = threading.Lock()
        self._status = Status()

    def update(self, **changes) -> None:
        with self._lock:
            self._status = dataclasses.replace(self._status, updated=dt.datetime.now(dt.timezone.utc),
                                               **changes)

    def get(self) -> Status:
        with self._lock:
            return self._status

    def as_json(self) -> dict:
        s = self.get()
        return {
            "connected": s.connected, "camera": s.camera, "layout": s.layout,
            "solution": _record_json(s.solution), "reference": _record_json(s.reference),
            "camera_status": _camera_status_json(s.camera_status),
            "mount": _mount_json(s.mount_status),
            "error": s.error, "message": s.message,
            "updated": s.updated.isoformat(timespec="seconds") if s.updated else None,
        }
