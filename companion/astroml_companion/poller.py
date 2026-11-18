"""One thread owns the USB link: it reconnects, polls, steps the session, and runs queued
requests in order. Nothing else talks to the camera."""
from __future__ import annotations

import queue
import threading

from . import protocol
from .camera import Camera, CameraError
from .mount import Mount, MountError, MountStatus
from .session import Session
from .state import SharedStatus

RECONNECT_S = 3.0
SESSION_INTERVAL_S = 1.0  # faster polling while a session waits on the camera


class Poller:
    def __init__(self, camera: Camera, status: SharedStatus, interval: float = 2.0,
                 session: Session | None = None, mount: Mount | None = None):
        self.camera = camera
        self.mount = mount
        self._mount_connected = False
        self.status = status
        self.interval = interval
        self.session = session or Session()
        self._requests: queue.Queue[tuple] = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="camera-poller", daemon=True)
        self._connected = False

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._requests.put(("wake",))
        self._thread.join(timeout=5)

    # Requests from the web thread, run on the poller thread.
    def request_solve(self) -> None:
        self._requests.put(("solve",))

    def request_exposure(self, seconds: float) -> None:
        self._requests.put(("expose", seconds))

    def request_cancel(self) -> None:
        self._requests.put(("cancel",))

    def _poll_mount(self) -> None:
        """The mount is optional and independent: its errors never drop the camera link."""
        if self.mount is None:
            return
        try:
            if not self._mount_connected:
                self.mount.connect()
                self._mount_connected = True
            self.status.update(mount_status=self.mount.status())
        except MountError as error:
            self.mount.close()
            self._mount_connected = False
            self.status.update(mount_status=MountStatus(connected=False, detail=str(error)))

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._poll_mount()
            try:
                if not self._connected:
                    self._connect()
                self._poll()
                delay = SESSION_INTERVAL_S if self.session.active() else self.interval
            except CameraError as error:
                self._disconnect(str(error))
                delay = RECONNECT_S
            self._wait(delay)
        self._disconnect("stopped")
        if self.mount is not None:
            self.mount.close()

    def _connect(self) -> None:
        self.camera.connect()
        layout, api = self.camera.version()
        if layout != protocol.LAYOUT or api < protocol.API:
            raise CameraError(f"camera speaks layout {layout} / API {api}; this app needs "
                              f"layout {protocol.LAYOUT} and API {protocol.API}: update the module")
        self._connected = True
        self.status.update(connected=True, layout=layout, error="")

    def _poll(self) -> None:
        solution = self.camera.solution()
        camera_status = self.camera.status()
        changes = {"reference": self.camera.reference(), "camera_status": camera_status,
                   "camera": camera_status.camera}
        if solution is not None:  # None: the camera was busy writing; keep the last one
            previous = self.status.get().solution
            if previous is None or previous.sequence != solution.sequence:
                changes["message"] = ""  # a new solve answers any "Solving..." note
            changes["solution"] = solution
        self.status.update(**changes)
        self.session.step(self.camera, camera_status)

    def _disconnect(self, reason: str) -> None:
        self.camera.close()
        self._connected = False
        self.status.update(connected=False, error=reason)

    def _wait(self, seconds: float) -> None:
        try:
            request = self._requests.get(timeout=seconds)
        except queue.Empty:
            return
        if not self._connected or request[0] == "wake":
            return
        try:
            if request[0] == "solve":
                accepted = self.camera.request_solve()
                self.status.update(message="Solving Live View on the camera..." if accepted
                                   else "Camera busy: finish or cancel the current action")
            elif request[0] == "expose":
                accepted = self.camera.expose(request[1])
                self.status.update(message=f"Exposing {request[1]:g} s..." if accepted
                                   else "Camera refused the exposure (busy, or out of range)")
            elif request[0] == "cancel":
                self.camera.cancel()
                self.status.update(message="Cancel sent")
        except CameraError as error:
            self._disconnect(str(error))
