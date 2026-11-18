"""Mount control: go-to and sync for a computerised mount, guide pulses for dithering.

Every mount works in J2000 here; drivers convert to whatever their firmware expects. Methods a
mount cannot do raise MountError, and `can_goto` tells the session whether to slew at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

NORTH, SOUTH, EAST, WEST = "n", "s", "e", "w"


class MountError(Exception):
    """The mount is unusable right now; the poller reconnects later."""


@dataclass(frozen=True)
class MountStatus:
    connected: bool
    ra_deg: float | None = None   # J2000
    dec_deg: float | None = None
    slewing: bool = False
    tracking: bool = False
    parked: bool = False
    detail: str = ""


class Mount(Protocol):
    name: str
    can_goto: bool
    axes: str                     # "ra" (RA-only tracker) or "both"
    guide_rate: tuple[float, float]  # RA and Dec guide rate, x sidereal

    def connect(self) -> None: ...
    def close(self) -> None: ...
    def status(self) -> MountStatus: ...
    def goto(self, ra_deg: float, dec_deg: float) -> None: ...
    def sync(self, ra_deg: float, dec_deg: float) -> None: ...
    def stop(self) -> None: ...
    def pulse(self, direction: str, ms: int) -> None: ...
