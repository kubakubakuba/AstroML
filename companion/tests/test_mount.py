"""Mount drivers: iOptron command bytes (Command Language 2014 v2.5), ST-4 pulses, precession."""
from __future__ import annotations

import ctypes as C
import datetime as dt
import pathlib
import sys

import pytest

from astroml_companion import precession
from astroml_companion.mount import EAST, NORTH, WEST, MountError
from astroml_companion.mount.ioptron import IOptronMount
from astroml_companion.mount.st4 import St4Mount

WHEN = dt.datetime(2026, 11, 18, 22, 0, tzinfo=dt.timezone.utc)


class ScriptedPort:
    """Answers each command with a scripted reply and records every byte written."""

    def __init__(self, replies: dict[str, bytes]):
        self.replies = replies
        self.written: list[str] = []
        self.pending = b""
        self.timeout = 2.0

    def write(self, data: bytes) -> None:
        command = data.decode()
        self.written.append(command)
        key = command[:3] if command.startswith((":Sr", ":Sd")) else command
        self.pending += self.replies.get(key, b"")

    def read(self, size: int) -> bytes:
        chunk, self.pending = self.pending[:size], self.pending[size:]
        return chunk

    def reset_input_buffer(self) -> None:
        self.pending = b""

    def close(self) -> None:
        pass


REPLIES = {":MountInfo#": b"0060", ":AG#": b"5050#", ":GEC#": b"+0193860005T1234567#",
           ":GLS#": b"+050400324000212011#", ":Sr": b"1", ":Sd": b"1", ":MS#": b"1",
           ":CM#": b"1", ":Q#": b"1"}


def mount(replies=None, epoch="j2000"):
    port = ScriptedPort({**REPLIES, **(replies or {})})
    m = IOptronMount("/dev/null", epoch=epoch, port_factory=lambda path: port, clock=lambda: WHEN)
    m.connect()
    return m, port


def test_connect_identifies_the_mount_and_guide_rates():
    m, port = mount()
    assert port.written[:2] == [":MountInfo#", ":AG#"]
    assert m.name == "CEM60" and m.guide_rate == (0.5, 0.5)


def test_status_parses_position_and_system_state():
    m, _ = mount({":GEC#": b"-0193860005123456#"})
    s = m.status()
    assert s.dec_deg == pytest.approx(-(1938600 / 360000))  # sign + "01938600": 0.01 arcsec
    assert s.ra_deg == pytest.approx(5123456 / 1000 / 3600 * 15)
    assert s.slewing and not s.tracking and "slewing" in s.detail  # 14th digit of :GLS# is 2


def test_goto_writes_exact_target_commands():
    m, port = mount()
    port.written.clear()
    m.goto(83.8221, -5.3911)  # RA 05h35m17.304s = 20,117,304 ms; Dec -05d23'27.96" = -1,940,796
    assert port.written == [":Sr20117304#", ":Sd-01940796#", ":MS#"]


def test_goto_refused_below_limits():
    m, _ = mount({":MS#": b"0"})
    with pytest.raises(MountError, match="altitude limit"):
        m.goto(10.0, -80.0)


def test_sync_and_stop():
    m, port = mount()
    port.written.clear()
    m.sync(303.0273, 38.3549)
    m.stop()
    # 303.0273 deg = 20.20182 h = 72,726,552 ms; 38.3549 deg = 13,807,764 x 0.01"
    assert port.written == [":Sr72726552#", ":Sd+13807764#", ":CM#", ":Q#"]


def test_pulse_guiding(monkeypatch):
    m, port = mount()
    monkeypatch.setattr("astroml_companion.mount.ioptron.time.sleep", lambda s: None)
    port.written.clear()
    m.pulse(NORTH, 1250)
    m.pulse(EAST, 120000)  # clamped to the five-digit maximum
    assert port.written == [":Mn01250#", ":Me99999#"]


def test_silent_mount_is_reported():
    with pytest.raises(MountError, match="did not answer"):
        mount({":MountInfo#": b""})


def test_jnow_targets_are_precessed():
    m, port = mount(epoch="jnow")
    port.written.clear()
    m.goto(83.8221, -5.3911)
    ra_date, _ = precession.to_date(83.8221, -5.3911, WHEN)
    assert port.written[0] == f":Sr{round(ra_date / 15 * 3600 * 1000):08d}#"
    assert port.written[0] != ":Sr20117304#"


def test_precession_round_trip_and_size():
    ra, dec = precession.to_date(83.8221, -5.3911, WHEN)
    back = precession.to_j2000(ra, dec, WHEN)
    assert back == pytest.approx((83.8221, -5.3911), abs=1e-9)
    # about 50" per year in RA near the equator: 26.9 years -> roughly 0.37 degree
    assert 0.3 < ra - 83.8221 < 0.45


def test_precession_matches_astroml():
    """The companion and the camera module (astro_precess_to_date) precess alike."""
    repo = pathlib.Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(repo / "tests"), str(repo)]
    try:
        from native import sky_math
    except Exception as error:
        pytest.skip(f"AstroML host library unavailable: {error}")
    lib = sky_math()
    seconds = round((WHEN - precession.J2000).total_seconds())
    for ra, dec in ((83.8221, -5.3911), (37.95, 89.26), (279.23, 38.78)):
        out_ra, out_dec = C.c_uint32(), C.c_int32()
        lib.astro_precess_to_date(seconds, round(ra * 1e4), round(dec * 1e4), C.byref(out_ra),
                                  C.byref(out_dec))
        mine = precession.to_date(ra, dec, WHEN)
        assert abs(((mine[0] - out_ra.value / 1e4 + 180) % 360) - 180) * \
            max(0.01, abs(__import__("math").cos(__import__("math").radians(dec)))) < 2e-3
        assert abs(mine[1] - out_dec.value / 1e4) < 2e-3


class Line:
    def __init__(self, pin, log):
        self.pin, self.log = pin, log

    def on(self):
        self.log.append((self.pin, "on"))

    def off(self):
        self.log.append((self.pin, "off"))


def test_st4_pulses_the_right_pin():
    log, slept = [], []
    st4 = St4Mount(axes="ra", output_factory=lambda pin: Line(pin, log), sleep=slept.append)
    st4.connect()
    st4.pulse(WEST, 800)
    assert log == [(17, "on"), (17, "off")] and slept == [0.8]
    with pytest.raises(MountError, match="no ST-4 line"):
        st4.pulse(NORTH, 100)  # RA-only wiring
    with pytest.raises(MountError):
        st4.goto(1.0, 2.0)
