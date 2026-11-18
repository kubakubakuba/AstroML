"""Runs a plan: for each panel, frame it with the camera's guidance, then take its exposures.

With a go-to mount the session first slews to the panel. While the camera's framing guidance
runs it plate-solves every Live View frame; whenever a fresh solution shows the panel more than
GOTO_TOLERANCE_DEG away, the mount is synced to that solution and slews again. Camera rotation
stays manual. Between frames the optional `dither(fov_deg, dec_deg)` moves the mount.

`step()` is called by the poller thread (the only USB user) with a fresh camera status; it never
blocks. The web thread only calls the control methods, which take the lock. Progress is saved
after every change, and a session found on disk at start-up comes back paused.
"""
from __future__ import annotations

import json
import math
import pathlib
import threading
import time

from .camera import Camera
from .mount import Mount, MountError
from .plan import Panel, Plan, panels
from .protocol import CameraStatus

# Phases. Framing and exposing each have a request phase (send once) and a wait phase.
(IDLE, SLEWING, FRAMING, WAIT_MATCH, STOP_MATCH, START_EXPOSURE, EXPOSING, SETTLING, DONE, STOPPED,
 ERROR) = ("idle", "slewing", "framing", "waiting for match", "ending match", "starting exposure",
           "exposing", "settling", "done", "stopped", "error")
ACTIVE = {SLEWING, FRAMING, WAIT_MATCH, STOP_MATCH, START_EXPOSURE, EXPOSING, SETTLING}
GOTO_TOLERANCE_DEG = 0.25  # closer than this, the camera's guidance finishes the framing
MAX_CORRECTIONS = 3        # sync-and-slew rounds per panel
MIN_SLEW_S = 2.0           # the mount may not report "slewing" straight after the command
EXPOSURE_GRACE_S = 120  # card writing and camera overhead on top of the exposure itself
FINISHED_EXPOSURE = {"done", "cancelled", "failed"}


class Session:
    def __init__(self, store: pathlib.Path | None = None, clock=time.monotonic, dither=None,
                 mount: Mount | None = None):
        self.store = store
        self.clock = clock
        self.dither = dither  # dither(fov_deg, dec_deg); None: no dithering
        self.mount = mount
        self._lock = threading.Lock()
        self.plan: Plan | None = None
        self.panels: list[Panel] = []
        self.phase = IDLE
        self.paused = False
        self.panel_index = 0
        self.frame_index = 0
        self.message = ""
        self._since = 0.0
        self._expected_exposure = 0
        self._skip_match = False
        self._goto_sent = False
        self._quiet_until = 0.0     # ignore solutions until a slew has had time to start
        self._solution_floor = 0    # only solutions newer than this sequence count
        self._corrections = 0
        self._load()

    # ---- control (web thread) ----

    def set_plan(self, plan: Plan, fov_deg: float) -> list[Panel]:
        plan.validate()
        with self._lock:
            if self.phase in ACTIVE:
                raise ValueError("stop the running session before changing the plan")
            if not (plan.fov_deg or fov_deg):
                raise ValueError("field of view unknown: solve a frame first or enter it")
            self.plan = plan
            self._planned_fov = plan.fov_deg or fov_deg
            self.panels = panels(plan, self._planned_fov)
            self.phase, self.panel_index, self.frame_index, self.message = IDLE, 0, 0, ""
            self._save()
            return self.panels

    def start(self) -> None:
        with self._lock:
            if self.plan is None:
                raise ValueError("no plan yet")
            if self.phase in ACTIVE:
                self.paused = False
            else:
                self.panel_index, self.frame_index = 0, 0
                self._begin_panel()
            self.paused = False
            self._save()

    def pause(self) -> None:
        with self._lock:
            self.paused = True
            self.message = "paused (an exposure in progress still finishes)"
            self._save()

    def stop(self) -> None:
        with self._lock:
            if self.phase in ACTIVE:
                self._enter(STOPPED)
            self.paused = False
            self._save()

    def skip_framing(self) -> None:
        """Start exposing the current panel without waiting for a match."""
        with self._lock:
            self._skip_match = True

    # ---- poller thread ----

    def active(self) -> bool:
        return self.phase in ACTIVE and not self.paused

    def step(self, camera: Camera, status: CameraStatus) -> None:
        with self._lock:
            if not self.active():
                return
            handler = getattr(self, "_" + self.phase.replace(" ", "_"))
            handler(camera, status)

    def _has_goto(self) -> bool:
        return self.mount is not None and self.mount.can_goto

    def _begin_panel(self) -> None:
        self._goto_sent, self._corrections, self._skip_match = False, 0, False
        if hasattr(self.dither, "reset"):
            self.dither.reset()
        self._enter(SLEWING if self._has_goto() else FRAMING)

    def _mount_call(self, call, *args) -> bool:
        try:
            call(*args)
            return True
        except MountError as error:
            self.paused = True
            self.message = f"mount: {error}; press Resume to retry"
            return False

    def _slewing(self, camera, status):
        p = self.panels[self.panel_index]
        if not self._goto_sent:
            if self._mount_call(self.mount.goto, p.ra_deg, p.dec_deg):
                self._goto_sent = True
                self.message = f"slewing to panel {p.index + 1} of {len(self.panels)}"
            return
        try:
            slewing = self.mount.status().slewing
        except MountError as error:
            self.paused, self.message = True, f"mount: {error}"
            return
        if not slewing and self.clock() - self._since >= MIN_SLEW_S:
            self._enter(FRAMING)

    def _correct_pointing(self, camera, status) -> None:
        """Sync and slew again while a fresh plate solution shows the panel too far away."""
        if self.clock() < self._quiet_until or self._corrections >= MAX_CORRECTIONS:
            return
        try:
            if self.mount.status().slewing:
                return
        except MountError as error:
            self.paused, self.message = True, f"mount: {error}"
            return
        if status.solution_sequence <= self._solution_floor:
            return
        solution = camera.solution()
        self._solution_floor = status.solution_sequence
        if solution is None or not solution.valid:
            return
        p = self.panels[self.panel_index]
        if separation(solution.ra_deg, solution.dec_deg, p.ra_deg, p.dec_deg) <= GOTO_TOLERANCE_DEG:
            return
        if self._mount_call(self.mount.sync, solution.ra_deg, solution.dec_deg) and \
                self._mount_call(self.mount.goto, p.ra_deg, p.dec_deg):
            self._corrections += 1
            self._quiet_until = self.clock() + MIN_SLEW_S
            self.message = f"pointing corrected ({self._corrections}/{MAX_CORRECTIONS})"

    def _framing(self, camera, status):
        p = self.panels[self.panel_index]
        if camera.set_target(p.ra_deg, p.dec_deg, p.north_deg, self._fov(), p.index):
            self._skip_match = False
            self._solution_floor = status.solution_sequence
            self.message = (f"Frame panel {p.index + 1} of {len(self.panels)} with the camera's "
                            "Live View guidance")
            self._enter(WAIT_MATCH)
        else:
            self.message = "camera busy; open 1x photo Live View for framing"

    def _waiting_for_match(self, camera, status):
        if self._has_goto():
            self._correct_pointing(camera, status)
            if self.paused:
                return
        if self._skip_match or (status.matched and status.panel == self.panels[self.panel_index].index):
            camera.cancel()
            self._enter(STOP_MATCH)

    def _ending_match(self, camera, status):
        if not status.busy:
            self._enter(START_EXPOSURE)

    def _starting_exposure(self, camera, status):
        if camera.expose(self.plan.exposure_s):
            self._expected_exposure = status.exposure_number + 1
            self.message = f"exposure {self.frame_index + 1} of {self.plan.frames}"
            self._enter(EXPOSING)
        else:
            self.message = "camera refused the exposure (busy?)"

    def _exposing(self, camera, status):
        if (status.exposure_number >= self._expected_exposure
                and status.exposure_state in FINISHED_EXPOSURE):
            if status.exposure_state != "done":
                self.paused = True
                self.message = f"exposure {status.exposure_state} on the camera; press Resume"
                self._enter(START_EXPOSURE)
                return
            self.frame_index += 1
            self._after_frame(camera)
        elif self.clock() - self._since > self.plan.exposure_s + EXPOSURE_GRACE_S:
            self.message = "exposure did not finish; check the camera"
            self._enter(ERROR)

    def _after_frame(self, camera):
        every = self.plan.dither_every
        if self.dither is not None and every and self.frame_index % every == 0 \
                and self.frame_index < self.plan.frames:
            try:
                self.dither(self._fov(), self.panels[self.panel_index].dec_deg)
            except MountError as error:
                self.message = f"dither skipped: {error}"
            self._enter(SETTLING)
        else:
            self._next()

    def _settling(self, camera, status):
        if self.clock() - self._since >= self.plan.settle_s:
            self._next()

    def _next(self):
        if self.frame_index < self.plan.frames:
            self._enter(START_EXPOSURE)
        elif self.panel_index + 1 < len(self.panels):
            self.panel_index += 1
            self.frame_index = 0
            self._begin_panel()
        else:
            self.message = f"finished: {len(self.panels)} panel(s) x {self.plan.frames} frames"
            self._enter(DONE)

    def _fov(self) -> float:
        return self.plan.fov_deg or self._planned_fov

    def _enter(self, phase: str) -> None:
        self.phase = phase
        self._since = self.clock()
        self._save()

    # ---- persistence ----

    def as_json(self) -> dict:
        with self._lock:
            return {"phase": self.phase, "paused": self.paused, "message": self.message,
                    "panel": self.panel_index + 1 if self.panels else 0,
                    "panels": len(self.panels), "frame": self.frame_index,
                    "frames": self.plan.frames if self.plan else 0, "fov_deg": self._planned_fov,
                    "plan": self.plan.to_json() if self.plan else None,
                    "panel_list": [p.to_json() for p in self.panels]}

    def _save(self) -> None:
        if self.store is None:
            return
        data = {"plan": self.plan.to_json() if self.plan else None,
                "panels": [p.to_json() for p in self.panels], "phase": self.phase,
                "panel_index": self.panel_index, "frame_index": self.frame_index,
                "fov_deg": self._planned_fov}
        self.store.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.store.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=1))
        temporary.replace(self.store)

    def _load(self) -> None:
        self._planned_fov = 0.0
        if self.store is None or not self.store.is_file():
            return
        try:
            data = json.loads(self.store.read_text())
            self.plan = Plan.from_json(data["plan"]) if data.get("plan") else None
            self.panels = [Panel(**{**p, "offset": tuple(p["offset"])}) for p in data["panels"]]
            self.panel_index, self.frame_index = data["panel_index"], data["frame_index"]
            self.phase = data["phase"]
            self._planned_fov = float(data.get("fov_deg") or 0.0)
        except (ValueError, KeyError, TypeError) as error:
            self.message = f"saved session unreadable ({error}); starting fresh"
            self.plan, self.panels, self.phase = None, [], IDLE
            return
        if self.phase in ACTIVE:
            # After a restart nobody knows what the camera did meanwhile: frame again, paused.
            self.phase, self.paused = SLEWING if self._has_goto() else FRAMING, True
            self.message = "session restored after a restart; press Resume"


def separation(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    a1, d1, a2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(a1 - a2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))
