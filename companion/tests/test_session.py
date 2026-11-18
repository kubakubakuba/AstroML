"""The session state machine on a fake camera with a fake clock."""
from __future__ import annotations

import pytest

from astroml_companion.camera import FakeCamera
from astroml_companion.plan import Plan
from astroml_companion.session import DONE, EXPOSING, WAIT_MATCH, Session


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def run(session, camera, clock, seconds, step=0.5):
    for _ in range(int(seconds / step)):
        clock.now += step
        session.step(camera, camera.status())


@pytest.fixture()
def rig(tmp_path):
    clock = Clock()
    camera = FakeCamera(clock=clock)
    session = Session(tmp_path / "session.json", clock=clock)
    return clock, camera, session


def test_mosaic_runs_every_panel_and_frame(rig):
    clock, camera, session = rig
    session.set_plan(Plan(ra_deg=83.8, dec_deg=-5.4, columns=2, rows=1, frames=3,
                          exposure_s=10), fov_deg=15.6)
    session.start()
    run(session, camera, clock, 300)
    assert session.phase == DONE
    assert camera.exposures == [(0, 10), (0, 10), (0, 10), (1, 10), (1, 10), (1, 10)]


def test_waits_for_the_match_before_exposing(rig):
    clock, camera, session = rig
    session.set_plan(Plan(ra_deg=83.8, dec_deg=-5.4, frames=1, exposure_s=5), fov_deg=15.6)
    session.start()
    run(session, camera, clock, 1.0)
    assert session.phase == WAIT_MATCH and camera.exposures == []
    run(session, camera, clock, FakeCamera.MATCH_AFTER_S + 2)
    assert camera.exposures and session.phase in (EXPOSING, DONE)


def test_skip_framing(rig):
    clock, camera, session = rig
    session.set_plan(Plan(ra_deg=83.8, dec_deg=-5.4, frames=1, exposure_s=5), fov_deg=15.6)
    session.start()
    run(session, camera, clock, 0.5)
    session.skip_framing()
    run(session, camera, clock, 1.5)
    assert camera.exposures


def test_pause_holds_and_resume_continues(rig):
    clock, camera, session = rig
    session.set_plan(Plan(ra_deg=83.8, dec_deg=-5.4, frames=4, exposure_s=5), fov_deg=15.6)
    session.start()
    run(session, camera, clock, 12)
    session.pause()
    taken = len(camera.exposures)
    run(session, camera, clock, 60)
    assert len(camera.exposures) == taken
    session.start()
    run(session, camera, clock, 60)
    assert session.phase == DONE and len(camera.exposures) == 4


def test_restored_session_comes_back_paused(rig, tmp_path):
    clock, camera, session = rig
    session.set_plan(Plan(ra_deg=83.8, dec_deg=-5.4, columns=2, frames=2, exposure_s=5),
                     fov_deg=15.6)
    session.start()
    run(session, camera, clock, 20)
    again = Session(tmp_path / "session.json", clock=clock)
    assert again.paused and again.panel_index == session.panel_index
    assert len(again.panels) == 2 and again.as_json()["fov_deg"] == 15.6


def test_plan_needs_a_field_of_view(rig):
    _, _, session = rig
    with pytest.raises(ValueError, match="field of view"):
        session.set_plan(Plan(ra_deg=1.0, dec_deg=2.0), fov_deg=0.0)


def test_dither_between_frames(rig):
    clock, camera, session = rig
    calls = []
    session.dither = lambda fov, dec: calls.append((len(camera.exposures), fov, round(dec, 1)))
    session.set_plan(Plan(ra_deg=83.8, dec_deg=-5.4, frames=4, exposure_s=5, dither_every=2,
                          settle_s=3), fov_deg=15.6)
    session.start()
    run(session, camera, clock, 120)
    assert session.phase == DONE and calls == [(2, 15.6, -5.4)]  # after frame 2, never the last


def test_goto_mount_slews_corrects_and_frames(tmp_path):
    from astroml_companion.mount.fake import FakeMount
    from astroml_companion.session import SLEWING

    clock = Clock()
    mount = FakeMount(clock=clock, slew_s=4.0, error_deg=0.8)
    camera = FakeCamera(clock=clock, pointing=mount.actual)
    session = Session(tmp_path / "s.json", clock=clock, mount=mount)
    session.set_plan(Plan(ra_deg=83.8, dec_deg=-5.4, columns=2, frames=1, exposure_s=5),
                     fov_deg=15.6)
    session.start()
    assert session.phase == SLEWING
    run(session, camera, clock, 120)
    assert session.phase == DONE
    kinds = [entry[0] for entry in mount.log]
    # panel 1: goto, then the 0.8 degree pointing error is synced away and the mount slews again
    assert kinds[:3] == ["goto", "sync", "goto"] and kinds.count("goto") == 3
    assert [p for p, _ in camera.exposures] == [0, 1]


def test_mount_error_pauses_the_session(tmp_path):
    from astroml_companion.mount import MountError
    from astroml_companion.mount.fake import FakeMount

    class Broken(FakeMount):
        def goto(self, ra, dec):
            raise MountError("cable unplugged")

    clock = Clock()
    session = Session(tmp_path / "s.json", clock=clock, mount=Broken(clock=clock))
    session.set_plan(Plan(ra_deg=83.8, dec_deg=-5.4, frames=1, exposure_s=5), fov_deg=15.6)
    session.start()
    run(session, FakeCamera(clock=clock), clock, 5)
    assert session.paused and "cable unplugged" in session.message
