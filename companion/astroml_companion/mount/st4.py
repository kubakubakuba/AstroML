"""An ST-4 guide port driven from the Pi's GPIO pins through optocouplers.

Pins (BCM): RA+ 17, RA- 27, Dec+ 22, Dec- 23. Each pin lights the LED of a PC817 whose
transistor connects that ST-4 line to the port's common. A tracker such as the iOptron
SkyGuider only uses the RA lines. RA+ is taken as west and Dec+ as north (the usual ST-4
convention); for dithering only the size of the move matters, not its sign.
"""
from __future__ import annotations

import time

from . import EAST, NORTH, SOUTH, WEST, MountError, MountStatus

DEFAULT_PINS = {WEST: 17, EAST: 27, NORTH: 22, SOUTH: 23}
MAX_PULSE_MS = 10_000


def gpio_output(pin: int):
    try:
        from gpiozero import DigitalOutputDevice
    except ImportError as error:
        raise MountError("gpiozero missing: it ships with Raspberry Pi OS") from error
    return DigitalOutputDevice(pin, active_high=True, initial_value=False)


class St4Mount:
    can_goto = False

    def __init__(self, axes: str = "ra", guide_rate: float = 0.5, pins=None,
                 output_factory=gpio_output, sleep=time.sleep):
        self.axes = axes
        self.guide_rate = (guide_rate, guide_rate)
        self.pins = dict(pins or DEFAULT_PINS)
        self.output_factory = output_factory
        self.sleep = sleep
        self.outputs: dict = {}
        self.name = "ST-4 guide port" + (" (RA only)" if axes == "ra" else "")

    def connect(self) -> None:
        if not self.outputs:
            used = (WEST, EAST) if self.axes == "ra" else (WEST, EAST, NORTH, SOUTH)
            self.outputs = {d: self.output_factory(self.pins[d]) for d in used}

    def close(self) -> None:
        for output in self.outputs.values():
            output.off()
            if hasattr(output, "close"):
                output.close()
        self.outputs = {}

    def status(self) -> MountStatus:
        return MountStatus(connected=bool(self.outputs), detail=self.name)

    def goto(self, ra_deg: float, dec_deg: float) -> None:
        raise MountError("an ST-4 port cannot slew")

    def sync(self, ra_deg: float, dec_deg: float) -> None:
        raise MountError("an ST-4 port cannot sync")

    def stop(self) -> None:
        for output in self.outputs.values():
            output.off()

    def pulse(self, direction: str, ms: int) -> None:
        output = self.outputs.get(direction)
        if output is None:
            raise MountError(f"no ST-4 line for direction {direction!r} on this setup")
        ms = max(0, min(MAX_PULSE_MS, int(ms)))
        if not ms:
            return
        output.on()
        try:
            self.sleep(ms / 1000)
        finally:
            output.off()
