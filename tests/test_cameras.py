"""cameras.toml: schema checks, the generated C table, and the focus-shortcut policy."""
from __future__ import annotations

import ctypes as C
import pathlib

import pytest

from native import port
from tools import cameras

GOOD = """
[cameras.6D]
firmware = "1.1.6"
ml_platform = "6D.116"
symbols = "6D_116.sym"
sensor_width_mm = 35.8
image_width_px = 5472
focus_key = 0x20
focus_key_name = "LCD light"
q_key_name = "Q"
qemu_model = "6D"
qemu_serial_flash = true
qemu_dcim = "100CANON"
status = "qemu"
"""


def write(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    path = tmp_path / "cameras.toml"
    path.write_text(text)
    return path


def test_repository_config_is_valid():
    loaded = cameras.load()
    assert list(loaded) == ["6D", "600D", "100D"]
    assert loaded["6D"].sensor_width_x10 == 358 and loaded["600D"].sensor_width_x10 == 223
    assert loaded["100D"].focus_key == cameras.NO_KEY and loaded["100D"].q_key_name == "Av"


def test_header_rows(tmp_path):
    text = cameras.header_text(cameras.load(write(tmp_path, GOOD)))
    assert '{"6D", "1.1.6", 358, 32, "LCD light", "Q"},' in text


@pytest.mark.parametrize("change,message", [
    (("status = \"qemu\"", "status = \"tested\""), "status"),
    (("sensor_width_mm = 35.8", "sensor_width_mm = 358"), "sensor_width_mm"),
    (("focus_key_name = \"LCD light\"\n", ""), "together"),
    (("qemu_dcim = \"100CANON\"\n", ""), "qemu_dcim"),
    (("firmware = \"1.1.6\"", "firmware = 116"), "firmware"),
    (("status = \"qemu\"", "status = \"qemu\"\nflash = 1"), "unknown"),
])
def test_bad_entries_are_named(tmp_path, change, message):
    with pytest.raises(ValueError, match=message):
        cameras.load(write(tmp_path, GOOD.replace(*change)))


class ShortcutState(C.Structure):
    _fields_ = [("enabled", C.c_int), ("photo_liveview", C.c_int), ("menu_open", C.c_int),
                ("busy", C.c_int), ("electronic_af", C.c_int)]


PASS, AUTOFOCUS, TOGGLE = 0, 1, 2


@pytest.mark.parametrize("raw,configured,state,expected", [
    (0x20, 0x20, (1, 1, 0, 0, 1), AUTOFOCUS),
    (0x20, 0x20, (1, 1, 0, 0, 0), TOGGLE),       # manual lens: toggle the readout
    (0x08, 0x08, (1, 1, 0, 0, 1), AUTOFOCUS),    # 600D DISP
    (0x20, -1, (1, 1, 0, 0, 1), PASS),           # camera without a spare button
    (0x08, 0x20, (1, 1, 0, 0, 1), PASS),         # some other button
    (0x20, 0x20, (1, 0, 0, 0, 1), PASS),         # not in photo Live View
    (0x20, 0x20, (1, 1, 1, 0, 1), PASS),         # menu open
    (0x20, 0x20, (1, 1, 0, 1, 1), PASS),         # busy
    (0x20, 0x20, (0, 1, 0, 0, 1), PASS),         # shortcut off
])
def test_focus_shortcut_policy(raw, configured, state, expected):
    lib = port()
    lib.astro_shortcut_decide.argtypes = [C.c_int, C.c_int, C.POINTER(ShortcutState)]
    assert lib.astro_shortcut_decide(raw, configured, C.byref(ShortcutState(*state))) == expected
