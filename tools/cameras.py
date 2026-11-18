"""`astroml cameras ...`: the supported cameras, read from `cameras.toml` (the only place they
are defined), and the C table generated from it for the module."""
from __future__ import annotations

import dataclasses
import pathlib
import tomllib

import typer

from . import paths

app = typer.Typer(help="Supported cameras (cameras.toml).", no_args_is_help=True)

CONFIG = paths.REPO / "cameras.toml"
STATUSES = ("build", "qemu", "hardware")
NO_KEY = -1


@dataclasses.dataclass(frozen=True)
class Camera:
    model: str
    firmware: str
    ml_platform: str
    symbols: str
    sensor_width_mm: float
    image_width_px: int
    q_key_name: str
    qemu_model: str
    qemu_serial_flash: bool
    qemu_dcim: str
    status: str
    focus_key: int = NO_KEY
    focus_key_name: str = ""

    @property
    def sensor_width_x10(self) -> int:
        return round(self.sensor_width_mm * 10)

    @property
    def symbol_file(self) -> pathlib.Path:
        return paths.ML_TREE / "platform" / self.ml_platform / "build" / self.symbols

    @property
    def roms(self) -> pathlib.Path:
        return paths.ROMS / self.model


REQUIRED = {"firmware": str, "ml_platform": str, "symbols": str, "sensor_width_mm": float,
            "image_width_px": int,
            "q_key_name": str, "qemu_model": str, "qemu_serial_flash": bool,
            "qemu_dcim": str, "status": str}
OPTIONAL = {"focus_key": int, "focus_key_name": str}


def _check(model: str, table: dict, source: pathlib.Path) -> Camera:
    where = f"{source.name} [cameras.{model}]"
    unknown = set(table) - set(REQUIRED) - set(OPTIONAL)
    if unknown:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
    for key, kind in REQUIRED.items():
        if key not in table:
            raise ValueError(f"{where}: missing '{key}'")
    for key, kind in {**REQUIRED, **OPTIONAL}.items():
        value = table.get(key)
        if value is None:
            continue
        if kind is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            raise ValueError(f"{where}: '{key}' must be {kind.__name__}")
        table[key] = value
    if table["status"] not in STATUSES:
        raise ValueError(f"{where}: status must be one of {', '.join(STATUSES)}")
    if not 10.0 <= table["sensor_width_mm"] <= 40.0:
        raise ValueError(f"{where}: sensor_width_mm looks wrong")
    if not 1000 <= table["image_width_px"] <= 20000:
        raise ValueError(f"{where}: image_width_px looks wrong")
    if ("focus_key" in table) != ("focus_key_name" in table):
        raise ValueError(f"{where}: give focus_key and focus_key_name together")
    if "focus_key" in table and not 0 <= table["focus_key"] <= 0xFF:
        raise ValueError(f"{where}: focus_key must be a raw button code 0..0xFF")
    return Camera(model=model, **table)


def load(source: pathlib.Path = CONFIG) -> dict[str, Camera]:
    """All cameras, in file order. Raises ValueError with the offending entry."""
    with source.open("rb") as f:
        data = tomllib.load(f)
    tables = data.get("cameras")
    if not isinstance(tables, dict) or not tables:
        raise ValueError(f"{source.name}: no [cameras.<model>] tables")
    return {model: _check(model, dict(table), source) for model, table in tables.items()}


def get(model: str) -> Camera:
    cameras = load()
    if model not in cameras:
        raise typer.BadParameter(f"unknown camera {model}; cameras.toml has {', '.join(cameras)}")
    return cameras[model]


def _c_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def header_text(cameras: dict[str, Camera]) -> str:
    rows = "\n".join(
        f"    {{{_c_string(c.model)}, {_c_string(c.firmware)}, {c.sensor_width_x10}, "
        f"{c.focus_key}, {_c_string(c.focus_key_name)}, {_c_string(c.q_key_name)}}},"
        for c in cameras.values())
    return ("/* Generated from cameras.toml by `astroml cameras header`. Do not edit. */\n"
            "/* model, firmware, sensor width (0.1 mm), raw focus key or -1, its name, Q key name */\n"
            f"{rows}\n")


@app.command("list")
def list_cameras():
    """Show the supported cameras."""
    for c in load().values():
        key = f"{c.focus_key_name} (0x{c.focus_key:02x})" if c.focus_key != NO_KEY else "none"
        typer.echo(f"{c.model:5s} fw {c.firmware}  {c.sensor_width_mm:4.1f} mm  focus key {key:16s}"
                   f"  Q key {c.q_key_name:2s}  verified: {c.status}")


@app.command("header")
def write_header(dest: pathlib.Path = typer.Argument(..., help="camera_profiles.h to write")):
    """Generate the module's camera table."""
    dest.write_text(header_text(load()))
    typer.echo(f"{dest}: {len(load())} cameras")


@app.command("platforms")
def list_platforms():
    """ML platform directories to build, one per line (for scripts)."""
    for c in load().values():
        typer.echo(c.ml_platform)


@app.command("check-symbols")
def check_symbols(dep: pathlib.Path = typer.Argument(..., help="astro.dep from the module build")):
    """Fail unless every symbol the module imports exists in every camera's ML build."""
    needed = set(dep.read_text().split())
    failed = False
    for c in load().values():
        if not c.symbol_file.is_file():
            typer.echo(f"{c.model}: {c.symbol_file} missing; build ML {c.ml_platform} "
                       "(scripts/setup_env.sh)", err=True)
            failed = True
            continue
        exported = {line.split()[1] for line in c.symbol_file.read_text().splitlines()
                    if len(line.split()) >= 2}
        missing = sorted(needed - exported)
        if missing:
            typer.echo(f"{c.model}: not exported by its autoexec.bin: {' '.join(missing)}", err=True)
            failed = True
        else:
            typer.echo(f"{c.model}: all {len(needed)} imported symbols present")
    if failed:
        raise typer.Exit(1)


@app.command("companion")
def write_companion(dest: pathlib.Path = typer.Argument(
        paths.REPO / "companion" / "astroml_companion" / "cameras.json",
        help="Camera facts packaged with the companion app.")):
    """What the companion needs per camera: sensor width and photo width, for pixel scales."""
    import json

    data = {c.model: {"sensor_width_mm": c.sensor_width_mm, "image_width_px": c.image_width_px}
            for c in load().values()}
    dest.write_text(json.dumps(data, indent=1) + "\n")
    typer.echo(f"{dest}: {', '.join(data)}")
