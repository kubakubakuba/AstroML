"""The camera as the app sees it: a real one over USB, or a fake one for development."""
from __future__ import annotations

import datetime as dt
import math
import struct
import time
from typing import Protocol

from . import protocol
from .protocol import CameraStatus, Record
from .ptp import DEVICE_BUSY, PtpError, Session

CANON_VENDOR = 0x04A9
STILL_IMAGE_CLASS = 6
USB_TIMEOUT_MS = 5000


class CameraError(Exception):
    """Anything that makes the camera unusable right now; the poller reconnects later."""


class Camera(Protocol):
    def connect(self) -> None: ...
    def close(self) -> None: ...
    def version(self) -> tuple[int, int]: ...
    def solution(self) -> Record | None: ...
    def reference(self) -> Record | None: ...
    def request_solve(self) -> bool: ...
    def status(self) -> CameraStatus: ...
    def expose(self, seconds: float) -> bool: ...
    def set_target(self, ra_deg: float, dec_deg: float, north_deg: float, fov_deg: float,
                   panel: int) -> bool: ...
    def cancel(self) -> None: ...


class _UsbTransport:
    def __init__(self, device, out_endpoint, in_endpoint):
        self.device, self.out_endpoint, self.in_endpoint = device, out_endpoint, in_endpoint

    def write(self, data: bytes) -> None:
        self.out_endpoint.write(data, USB_TIMEOUT_MS)

    def read(self, size: int) -> bytes:
        return bytes(self.in_endpoint.read(size, USB_TIMEOUT_MS))


class UsbCamera:
    """A Canon body on USB with the AstroML module loaded."""

    def __init__(self):
        self.device = None
        self.interface = None
        self.session: Session | None = None

    def connect(self) -> None:
        import usb.core
        import usb.util

        device = usb.core.find(idVendor=CANON_VENDOR)
        if device is None:
            raise CameraError("no Canon camera on USB (cable in the Pi's USB port, camera on?)")
        try:
            interface = self._ptp_interface(device)
            if device.is_kernel_driver_active(interface.bInterfaceNumber):
                device.detach_kernel_driver(interface.bInterfaceNumber)
            usb.util.claim_interface(device, interface.bInterfaceNumber)
        except usb.core.USBError as error:
            raise CameraError(f"camera busy or not permitted ({error}); "
                              "is gphoto2/gvfs holding it?") from error
        endpoints = {usb.util.endpoint_direction(e.bEndpointAddress): e for e in interface
                     if usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK}
        self.device, self.interface = device, interface
        self.session = Session(_UsbTransport(device, endpoints[usb.util.ENDPOINT_OUT],
                                             endpoints[usb.util.ENDPOINT_IN]))
        self._guard(self.session.open)

    @staticmethod
    def _ptp_interface(device):
        for configuration in device:
            for interface in configuration:
                if interface.bInterfaceClass == STILL_IMAGE_CLASS:
                    return interface
        raise CameraError("the Canon device has no PTP interface")

    def close(self) -> None:
        import usb.util

        if self.session is not None:
            try:
                self.session.close()
            except Exception:  # the camera may already be gone
                pass
        if self.device is not None:
            usb.util.dispose_resources(self.device)
        self.device = self.interface = self.session = None

    def _guard(self, call, *args):
        import usb.core

        try:
            return call(*args)
        except PtpError as error:
            raise CameraError(str(error)) from error
        except (usb.core.USBError, ValueError) as error:
            raise CameraError(f"USB: {error}") from error

    def _run(self, sub: int, *params: int):
        if self.session is None:
            raise CameraError("not connected")
        return self._guard(self.session.run, protocol.OPCODE, (sub, *params), False)

    def version(self) -> tuple[int, int]:
        response = self._run(protocol.VERSION)
        if response.code != 0x2001 or len(response.params) < 2:
            raise CameraError("AstroML not answering: is the module loaded on the camera?")
        return response.params[0], response.params[1]

    def _accepted(self, response) -> bool:
        if response.code != 0x2001:
            raise CameraError(f"AstroML answered 0x{response.code:04X}")
        return bool(response.params and response.params[0])

    def status(self) -> CameraStatus:
        response = self._run(protocol.STATUS)
        if response.code != 0x2001:
            raise CameraError(f"AstroML answered 0x{response.code:04X}")
        try:
            return protocol.parse_status(response.data)
        except ValueError as error:
            raise CameraError(str(error)) from error

    def expose(self, seconds: float) -> bool:
        return self._accepted(self._run(protocol.EXPOSE, round(seconds * 1000)))

    def set_target(self, ra_deg, dec_deg, north_deg, fov_deg, panel) -> bool:
        sub, *params = protocol.target_params(ra_deg, dec_deg, north_deg, fov_deg, panel)
        return self._accepted(self._run(sub, *params))

    def cancel(self) -> None:
        self._run(protocol.CANCEL)

    def _record(self, sub: int) -> Record | None:
        response = self._run(sub)
        if response.code == DEVICE_BUSY:
            return None  # the camera was writing a new solution; ask again next time
        if response.code != 0x2001:
            raise CameraError(f"AstroML answered 0x{response.code:04X}")
        try:
            return protocol.parse_record(response.data)
        except ValueError as error:
            raise CameraError(str(error)) from error

    def solution(self) -> Record | None:
        return self._record(protocol.SOLUTION)

    def reference(self) -> Record | None:
        return self._record(protocol.REFERENCE)

    def request_solve(self) -> bool:
        response = self._run(protocol.SOLVE)
        return bool(response.params and response.params[0])


class FakeCamera:
    """A simulated 6D near Orion, for trying the app and for tests. `clock` can be replaced to
    run sessions faster than real time."""

    MATCH_AFTER_S = 3.0  # a framing request "matches" after this long

    def __init__(self, clock=time.monotonic, pointing=None):
        self.clock = clock
        self.pointing = pointing  # () -> (ra, dec) where the optics point, e.g. a FakeMount
        self.started = clock()
        self.sequence = 0
        self.exposure_number = 0
        self.exposure_ms = 0
        self.exposure_ends: float | None = None
        self.exposure_state = "idle"
        self.match_since: float | None = None
        self.panel = -1
        self.file = 183
        self.target = (85.24, -1.16, -12.5, 15.6)
        self.exposures: list[tuple[int, float]] = []  # (panel, seconds), for tests

    def connect(self) -> None:
        pass

    def close(self) -> None:
        pass

    def version(self) -> tuple[int, int]:
        return protocol.LAYOUT, protocol.API

    def _record(self, kind: int, ra: float, dec: float, north: float, fov: float,
                sequence: int) -> Record:
        return Record(kind=kind, valid=True, source="Live View", ra_deg=ra, dec_deg=dec,
                      fov_deg=fov, north_deg=north, matched=18, elapsed_ms=420,
                      solved_utc=dt.datetime.now(dt.timezone.utc), sequence=sequence,
                      wcs=(0,) * 8, camera="6D (fake)")

    def solution(self) -> Record | None:
        ra, dec, north, fov = self.target
        if self.pointing is not None:
            ra, dec = self.pointing()
        else:
            t = self.clock() - self.started
            ra, dec = ra + 0.2 * math.sin(t / 30), dec + 0.1 * math.cos(t / 30)
        return self._record(1, ra, dec, north, fov, self.sequence)

    def _on_target(self) -> bool:
        if self.pointing is None:
            return True
        ra, dec = self.pointing()
        return abs(ra - self.target[0]) < 0.1 and abs(dec - self.target[1]) < 0.1

    def reference(self) -> Record | None:
        ra, dec, north, fov = self.target
        return self._record(2, ra, dec, north, fov, 0)

    def request_solve(self) -> bool:
        self.sequence += 1
        return True

    def _update(self) -> None:
        if self.exposure_ends is not None and self.clock() >= self.exposure_ends:
            self.exposure_ends = None
            self.exposure_state = "done"
            self.file += 1

    def status(self) -> CameraStatus:
        self._update()
        exposing = self.exposure_ends is not None
        matching = self.match_since is not None
        if matching:
            self.sequence += 1  # framing solves every Live View frame
        matched = (matching and self._on_target()
                   and self.clock() - self.match_since >= self.MATCH_AFTER_S)
        return CameraStatus(
            busy=exposing or matching, running_action=0, liveview_ready=True,
            exposure_state=self.exposure_state, exposure_number=self.exposure_number,
            exposure_ms=self.exposure_ms,
            photo=f"100-{self.file:04d}" if self.exposure_number else "",
            match_running=matching, matched=matched, rotation_valid=matching,
            separation_deg=0.05 if matched else 1.2, rotation_deg=0.2 if matched else 3.5,
            solution_sequence=self.sequence, panel=self.panel, camera="6D (fake)")

    def expose(self, seconds: float) -> bool:
        self._update()
        if self.exposure_ends is not None or self.match_since is not None or not 1 <= seconds <= 1800:
            return False
        self.exposure_number += 1
        self.exposure_ms = round(seconds * 1000)
        self.exposure_state = "exposing"
        self.exposure_ends = self.clock() + seconds
        self.exposures.append((self.panel, seconds))
        return True

    def set_target(self, ra_deg, dec_deg, north_deg, fov_deg, panel) -> bool:
        self._update()
        if self.exposure_ends is not None:
            return False
        protocol.target_params(ra_deg, dec_deg, north_deg, fov_deg, panel)  # validates
        self.target = (ra_deg, dec_deg, north_deg, fov_deg)
        self.panel = panel
        self.match_since = self.clock()
        return True

    def cancel(self) -> None:
        self.match_since = None
