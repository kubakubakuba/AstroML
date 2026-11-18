"""iOptron mounts over RS-232 (the CEM60 through its hand-controller cable), following
"iOptron Mount RS-232 Command Language 2014", version 2.5, which covers the CEM60 and CEM60-EC
with firmware 20161101 and later.

Formats used (all digits zero-padded, commands case sensitive):
  :MountInfo#             -> "0060"  model; must be sent first on every link
  :GEC#                   -> "sTTTTTTTTXXXXXXXX#"  Dec in 0.01", RA in ms
  :GLS#                   -> "sSSSSSSSSSSSSnnnnnn#"  14th digit is the system status
  :SrXXXXXXXX#  :SdsTTTTTTTT#  -> "1"   set the commanded RA (ms) and Dec (0.01")
  :MS#                    -> "1" slewing, "0" below the altitude limit or mechanical limits
  :CM#                    -> "1"   sync to the commanded coordinates
  :Q#                     -> "1"   stop slewing
  :MnXXXXX# :MsXXXXX# :MeXXXXX# :MwXXXXX#  (no reply)  move at the guide rate for ms
  :AG#                    -> "nnnn#"  RA and Dec guide rates, 0.nn x sidereal
The firmware is assumed to work in coordinates of date; `epoch="j2000"` switches that off.
"""
from __future__ import annotations

import datetime as dt
import time
from typing import Protocol

from .. import precession
from . import EAST, NORTH, SOUTH, WEST, MountError, MountStatus

BAUD = 9600  # all products except the CEM40 series (115200)
TIMEOUT_S = 2.0
MAX_PULSE_MS = 99999
MODELS = {"0060": "CEM60", "0061": "CEM60-EC", "0040": "CEM40", "0041": "CEM40-EC",
          "0045": "iEQ45 Pro", "0030": "iEQ30 Pro", "0025": "CEM25", "0026": "CEM25-EC"}
STATUS = {"0": "stopped", "1": "tracking", "2": "slewing", "3": "guiding",
          "4": "meridian flip", "5": "tracking (PEC)", "6": "parked", "7": "at home"}
TRACKING = {"1", "3", "5"}


class Port(Protocol):
    def write(self, data: bytes) -> None: ...
    def read(self, size: int) -> bytes: ...   # returns b"" on timeout
    def reset_input_buffer(self) -> None: ...
    def close(self) -> None: ...


def open_serial(path: str) -> Port:
    import serial  # pyserial, only needed on the Pi

    try:
        return serial.Serial(path, BAUD, bytesize=8, parity="N", stopbits=1, timeout=TIMEOUT_S)
    except serial.SerialException as error:
        raise MountError(f"cannot open {path}: {error}") from error


class IOptronMount:
    can_goto = True
    axes = "both"

    def __init__(self, path: str = "/dev/ttyUSB0", epoch: str = "jnow", port_factory=open_serial,
                 clock=lambda: dt.datetime.now(dt.timezone.utc)):
        self.path = path
        self.epoch = epoch
        self.port_factory = port_factory
        self.clock = clock
        self.port: Port | None = None
        self.name = "iOptron"
        self.guide_rate = (0.5, 0.5)

    # ---- link ----

    def connect(self) -> None:
        self.port = self.port_factory(self.path)
        self.port.reset_input_buffer()
        self._write(":MountInfo#")
        model = self._read_exact(4)
        self._read_optional(b"#")
        self.name = MODELS.get(model, f"iOptron {model}")
        rates = self._query(":AG#")
        if len(rates) == 4 and rates.isdigit():
            self.guide_rate = (int(rates[:2]) / 100, int(rates[2:]) / 100)

    def close(self) -> None:
        if self.port is not None:
            try:
                self.port.close()
            finally:
                self.port = None

    def _write(self, command: str) -> None:
        if self.port is None:
            raise MountError("mount not connected")
        self.port.write(command.encode("ascii"))

    def _read_exact(self, size: int) -> str:
        data = b""
        while len(data) < size:
            chunk = self.port.read(size - len(data))
            if not chunk:
                raise MountError("mount did not answer (cable, power, baud rate?)")
            data += chunk
        return data.decode("ascii", "replace")

    def _read_optional(self, expected: bytes) -> None:
        """Some firmware ends short replies with '#', some does not."""
        saved = getattr(self.port, "timeout", None)
        try:
            if saved is not None:
                self.port.timeout = 0.2
            self.port.read(len(expected))
        finally:
            if saved is not None:
                self.port.timeout = saved

    def _query(self, command: str) -> str:
        """A command whose reply ends with '#'. Returns the reply without it."""
        self._write(command)
        data = b""
        while not data.endswith(b"#"):
            chunk = self.port.read(1)
            if not chunk:
                raise MountError(f"no reply to {command}")
            data += chunk
        return data[:-1].decode("ascii", "replace")

    def _accepted(self, command: str) -> bool:
        self._write(command)
        return self._read_exact(1) == "1"

    # ---- coordinates ----

    def _to_mount(self, ra: float, dec: float) -> tuple[float, float]:
        return (ra, dec) if self.epoch == "j2000" else precession.to_date(ra, dec, self.clock())

    def _from_mount(self, ra: float, dec: float) -> tuple[float, float]:
        return (ra, dec) if self.epoch == "j2000" else precession.to_j2000(ra, dec, self.clock())

    def _set_target(self, ra_deg: float, dec_deg: float) -> None:
        ra, dec = self._to_mount(ra_deg, dec_deg)
        ra_ms = round(ra / 15 * 3600 * 1000) % 86_400_000
        dec_cas = round(dec * 3600 * 100)
        if not (self._accepted(f":Sr{ra_ms:08d}#")
                and self._accepted(f":Sd{'+' if dec_cas >= 0 else '-'}{abs(dec_cas):08d}#")):
            raise MountError("mount refused the coordinates")

    # ---- Mount interface ----

    def status(self) -> MountStatus:
        position = self._query(":GEC#")
        if len(position) != 17 or position[0] not in "+-":
            raise MountError(f"unexpected position reply {position!r}")
        dec = int(position[:9]) / 360000
        ra = int(position[9:]) / 1000 / 3600 * 15
        ra, dec = self._from_mount(ra, dec)
        flags = self._query(":GLS#")
        state = flags[13] if len(flags) >= 19 else "?"
        return MountStatus(connected=True, ra_deg=ra, dec_deg=dec, slewing=state in "24",
                           tracking=state in TRACKING, parked=state == "6",
                           detail=f"{self.name}: {STATUS.get(state, 'unknown')}")

    def goto(self, ra_deg: float, dec_deg: float) -> None:
        self._set_target(ra_deg, dec_deg)
        if not self._accepted(":MS#"):
            raise MountError("target below the altitude limit or past the mechanical limits")

    def sync(self, ra_deg: float, dec_deg: float) -> None:
        self._set_target(ra_deg, dec_deg)
        if not self._accepted(":CM#"):
            raise MountError("mount refused the sync")

    def stop(self) -> None:
        self._accepted(":Q#")

    def pulse(self, direction: str, ms: int) -> None:
        if direction not in (NORTH, SOUTH, EAST, WEST):
            raise ValueError(f"direction {direction!r}")
        ms = max(0, min(MAX_PULSE_MS, int(ms)))
        if ms:
            self._write(f":M{direction}{ms:05d}#")
            time.sleep(ms / 1000)  # the mount does not reply; wait until the move is done
