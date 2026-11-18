"""A simulated go-to mount for development and tests: slews take a moment, then it points
exactly where it was told, plus an optional pointing error to exercise solve-and-sync."""
from __future__ import annotations

import time

from . import MountStatus


class FakeMount:
    can_goto = True
    axes = "both"
    name = "fake mount"

    def __init__(self, clock=time.monotonic, slew_s: float = 2.0, error_deg: float = 0.0):
        self.clock = clock
        self.slew_s = slew_s
        self.error_deg = error_deg   # pointing error until the first sync
        self.guide_rate = (0.5, 0.5)
        self.ra, self.dec = 0.0, 0.0
        self.slew_until = 0.0
        self.synced = False
        self.log: list[tuple] = []

    def connect(self) -> None:
        pass

    def close(self) -> None:
        pass

    def status(self) -> MountStatus:
        return MountStatus(connected=True, ra_deg=self.ra, dec_deg=self.dec,
                           slewing=self.clock() < self.slew_until, tracking=True,
                           detail="fake mount")

    def actual(self) -> tuple[float, float]:
        """Where the optics really point (what a plate solve would report)."""
        offset = 0.0 if self.synced else self.error_deg
        return self.ra + offset, self.dec

    def goto(self, ra_deg: float, dec_deg: float) -> None:
        self.ra, self.dec = ra_deg, dec_deg
        self.slew_until = self.clock() + self.slew_s
        self.log.append(("goto", ra_deg, dec_deg))

    def sync(self, ra_deg: float, dec_deg: float) -> None:
        self.synced = True
        self.log.append(("sync", ra_deg, dec_deg))

    def stop(self) -> None:
        self.slew_until = 0.0

    def pulse(self, direction: str, ms: int) -> None:
        self.log.append(("pulse", direction, ms))
