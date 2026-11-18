"""`astroml qemu ...`: build qemu-eos, prepare a virtual SD card and drive an emulated camera
(any camera in cameras.toml; --camera, default 6D)."""
from __future__ import annotations

import lzma
import pathlib
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import zipfile

import typer

from . import cameras, paths

app = typer.Typer(help="Emulate a camera from cameras.toml with qemu-eos.", no_args_is_help=True)

RUN_DIR = paths.BUILD / "qemu-run"
BASE_SD_IMAGE = paths.ML_TREE / "platform" / "sd.qcow2.xz"
MONITOR = RUN_DIR / "monitor"

# Optional network/storage backends are disabled: the camera needs none of them, and QEMU
# 4.2.1 no longer compiles against current libnfs and friends.
CONFIGURE = ["--target-list=arm-softmmu", "--enable-plugins", "--disable-docs", "--enable-vnc",
             "--enable-gtk", "--disable-vte", "--disable-werror", "--disable-libnfs",
             "--disable-libiscsi", "--disable-glusterfs", "--disable-libssh", "--disable-curl",
             "--disable-rbd", "--disable-smartcard", "--disable-usb-redir", "--disable-spice",
             "--disable-xen", "--disable-nettle", "--disable-gnutls", "--disable-gcrypt"]
DATA_FILES = ("STARS.BIN", "STARS.IDX", "STARS.NAM", "SKY.DIR", "SKY.PAT", "SKY.FLD",
              "DSO.BIN", "DSO.KEY", "DSO.NAM", "CONST.BIN")


def run(cmd: list[str], cwd: pathlib.Path | None = None) -> None:
    typer.echo("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


@app.command()
def setup(jobs: int = typer.Option(0, help="make -j (0: all CPUs).")):
    """Clone qemu-eos (qemu-eos-v4.2.1) and build it into .build/qemu-eos-build."""
    import os
    jobs = jobs or os.cpu_count() or 4
    if not paths.QEMU_SOURCE.exists():
        run(["git", "clone", "--branch", paths.QEMU_BRANCH, "--depth", "1",
             paths.QEMU_REPO_URL, str(paths.QEMU_SOURCE)])
    paths.QEMU_BUILD.mkdir(parents=True, exist_ok=True)
    run([str(paths.QEMU_SOURCE / "configure"), *CONFIGURE], cwd=paths.QEMU_BUILD)
    run(["make", f"-j{jobs}"], cwd=paths.QEMU_BUILD)
    run(["make", "plugins"], cwd=paths.QEMU_BUILD)
    plugins = paths.QEMU_BUILD / "arm-softmmu" / "plugins"
    plugins.mkdir(exist_ok=True)
    shutil.copy2(paths.QEMU_BUILD / "tests" / "plugin" / "libmagiclantern.so", plugins)
    typer.echo(f"qemu-eos ready: {paths.QEMU_BUILD / 'arm-softmmu' / 'qemu-system-arm'}")


def fat_offset(raw: pathlib.Path) -> int:
    """Byte offset of the first MBR partition (the card's FAT volume)."""
    with raw.open("rb") as f:
        mbr = f.read(512)
    if mbr[510:512] != b"\x55\xaa":
        raise typer.BadParameter(f"{raw}: no MBR signature")
    (start_lba,) = struct.unpack_from("<I", mbr, 446 + 8)
    return start_lba * 512


class Card:
    """mtools access to the FAT volume inside a raw disk image."""

    def __init__(self, raw: pathlib.Path):
        self.image = f"{raw}@@{fat_offset(raw)}"

    def mkdir(self, path: str) -> None:
        subprocess.run(["mmd", "-D", "s", "-i", self.image, f"::/{path}"],
                       check=False, capture_output=True)

    def put(self, source: pathlib.Path | bytes, path: str) -> None:
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        parts = parent.split("/") if parent else []
        for i in range(len(parts)):
            self.mkdir("/".join(parts[:i + 1]))
        if isinstance(source, bytes):
            with tempfile.NamedTemporaryFile() as tmp:
                tmp.write(source)
                tmp.flush()
                self.put(pathlib.Path(tmp.name), path)
            return
        subprocess.run(["mcopy", "-o", "-i", self.image, str(source), f"::/{path}"], check=True)

    def folders(self, path: str) -> list[str]:
        listing = subprocess.run(["mdir", "-b", "-i", self.image, f"::/{path}"],
                                 capture_output=True, text=True, check=False).stdout
        return [line.rstrip("/").rsplit("/", 1)[-1] for line in listing.splitlines()
                if line.endswith("/")]

    def remove_tree(self, path: str) -> None:
        subprocess.run(["mdeltree", "-i", self.image, f"::/{path}"], check=True,
                       capture_output=True)


def keep_one_dcim_folder(card: Card, folder: str) -> None:
    """The base image comes with DCIM/100EOS5D. DCF folder numbers must be unique: with two
    folders numbered 100, Canon ignores both, playback finds no photos and the camera opens an
    empty 101 folder. Only the camera's own folder is kept."""
    for name in card.folders("DCIM"):
        if name[:3].isdigit() and name != folder:
            card.remove_tree(f"DCIM/{name}")


def install_ml_zip(card: Card, ml_zip: pathlib.Path) -> None:
    with zipfile.ZipFile(ml_zip) as z, tempfile.TemporaryDirectory() as tmp:
        z.extractall(tmp)
        for item in sorted(pathlib.Path(tmp).rglob("*")):
            if item.is_file():
                card.put(item, str(item.relative_to(tmp)))


CAMERA_OPTION = typer.Option("6D", "--camera", help="Camera from cameras.toml.")


def camera_build(camera: cameras.Camera) -> pathlib.Path:
    return paths.ML_TREE / "platform" / camera.ml_platform / "build"


@app.command()
def sd(camera_name: str = CAMERA_OPTION,
       ml_zip: pathlib.Path = typer.Option(None, help="ML build ZIP. Default: this tree's build "
                                                      "for the camera."),
       module: pathlib.Path = typer.Option(paths.MODULE_BUILD, help="astro.mo to install."),
       data: pathlib.Path = typer.Option(paths.DATA, help="ML/ASTRO data files."),
       extra: list[pathlib.Path] = typer.Option([], help="Files for ML/ASTRO. Default: the "
                                                        "synthetic TEST*.PGM and tests/data/*.PGM."),
       dcim: list[pathlib.Path] = typer.Option([], help="Photos for the camera's DCIM folder "
                                                        "(qemu_dcim). Default: IMG_*.JPG.")):
    """Build sd.qcow2/cf.qcow2 with ML, ASTRO.MO (enabled) and its data, for --boot runs."""
    camera = cameras.get(camera_name)
    build = camera_build(camera)
    ml_zip = ml_zip or build / "magiclantern.zip"
    test_frames = sorted(data.glob("TEST*.PGM")) + sorted((paths.REPO / "tests/data").glob("*.PGM"))
    extra = extra or test_frames
    dcim = dcim or sorted(data.glob("IMG_*.JPG"))
    if not BASE_SD_IMAGE.exists():
        raise typer.BadParameter(f"{BASE_SD_IMAGE} missing: clone ML first (scripts/setup_env.sh)")
    if not ml_zip.is_file():
        raise typer.BadParameter(f"{ml_zip} missing: build ML {camera.ml_platform} or pass --ml-zip")
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    qcow = RUN_DIR / "base.qcow2"
    raw = RUN_DIR / "sd.raw"
    qcow.write_bytes(lzma.open(BASE_SD_IMAGE).read())
    run(["qemu-img", "convert", "-f", "qcow2", "-O", "raw", str(qcow), str(raw)])

    card = Card(raw)
    install_ml_zip(card, ml_zip)
    if module.is_file():
        card.put(module, "ML/MODULES/ASTRO.MO")
        card.put(b"", "ML/SETTINGS/ASTRO.EN")  # ML loads a module only if this file exists
    else:
        typer.echo(f"warning: {module} missing, card has stock ML only")
    for name in DATA_FILES:
        if (data / name).is_file():
            card.put(data / name, f"ML/ASTRO/{name}")
    for item in extra:
        card.put(item, f"ML/ASTRO/{item.name.upper()}")
    keep_one_dcim_folder(card, camera.qemu_dcim)
    card.mkdir("DCIM")
    card.mkdir(f"DCIM/{camera.qemu_dcim}")
    for photo in dcim:
        card.put(photo, f"DCIM/{camera.qemu_dcim}/{photo.name.upper()}")

    build.mkdir(parents=True, exist_ok=True)
    sd_image = build / "sd.qcow2"
    run(["qemu-img", "convert", "-f", "raw", "-O", "qcow2", str(raw), str(sd_image)])
    shutil.copy2(sd_image, build / "cf.qcow2")  # some models also expect a CF drive
    raw.unlink()
    qcow.unlink()
    typer.echo(f"{camera.model} card image ready: {sd_image}")


def rom_dir(camera: cameras.Camera, trim_rom0: bool) -> pathlib.Path:
    """QEMU_EOS_WORKDIR for the camera. With trim_rom0, a ROM0 dump that holds the flash twice
    (as the 6D's does) is halved into .build/roms/."""
    source = camera.roms
    missing = [name for name in ("ROM0.BIN", "ROM1.BIN") if not (source / name).is_file()]
    if camera.qemu_serial_flash and not (source / "SFDATA.BIN").is_file():
        missing.append("SFDATA.BIN (dump it with the sf_dump module)")
    if missing:
        raise typer.BadParameter(f"{source}: missing {', '.join(missing)}")
    if not trim_rom0:
        return paths.ROMS
    target = paths.BUILD / "roms" / camera.model
    target.mkdir(parents=True, exist_ok=True)
    rom0 = (source / "ROM0.BIN").read_bytes()
    half = len(rom0) // 2
    if rom0[:half] != rom0[half:]:
        raise typer.BadParameter("ROM0 halves differ: not a mirrored dump, refusing to trim")
    (target / "ROM0.BIN").write_bytes(rom0[:half])
    for name in ("ROM1.BIN", "SFDATA.BIN"):
        link = target / name
        if (source / name).exists() and not link.exists():
            link.symlink_to(source / name)
    return target.parent


CRASH_LOCK = "ML/MODULES/LOADING.LCK"


def clear_crash_lock(sd_image: pathlib.Path) -> bool:
    """ML writes LOADING.LCK at boot and deletes it on a clean shutdown; if it is still there,
    the next boot loads no modules. Stopping the emulator is never a clean shutdown, so the
    lock is removed before each run. Returns True if one was removed."""
    with tempfile.TemporaryDirectory(dir=paths.BUILD) as tmp:
        raw = pathlib.Path(tmp) / "sd.raw"
        run(["qemu-img", "convert", "-f", "qcow2", "-O", "raw", str(sd_image), str(raw)])
        card = Card(raw)
        removed = subprocess.run(["mdel", "-i", card.image, f"::/{CRASH_LOCK}"],
                                 capture_output=True).returncode == 0
        if removed:
            run(["qemu-img", "convert", "-f", "raw", "-O", "qcow2", str(raw), str(sd_image)])
            shutil.copy2(sd_image, sd_image.with_name("cf.qcow2"))
    return removed


def debugmsg_address(camera: cameras.Camera) -> str | None:
    """DryosDebugMsg address from the camera's ML stubs, for the qemu-eos logging plugin."""
    stubs = paths.ML_TREE / "platform" / camera.ml_platform / "stubs.S"
    for line in stubs.read_text().splitlines():
        if "DryosDebugMsg" in line and line.strip().startswith("NSTUB("):
            return line.split("(", 1)[1].split(",", 1)[0].strip()
    return None


@app.command("run")
def run_camera(camera_name: str = CAMERA_OPTION,
               display: str = typer.Option("gtk", help="gtk (a window) or none (headless)."),
               boot: bool = typer.Option(True, help="Boot autoexec.bin from the card."),
               trim_rom0: bool = typer.Option(False, help="Use a halved copy of a mirrored ROM0."),
               gdb: bool = typer.Option(False, help="Wait for gdb on port 1234."),
               debugmsg: bool = typer.Option(True, help="Log DryosDebugMsg to the serial log."),
               keep_crash_lock: bool = typer.Option(False, help="Leave ML's crash lock on the card "
                                                                "(ML then skips loading modules).")):
    """Start the emulated camera. Monitor socket and serial log are in .build/qemu-run/."""
    import os
    camera = cameras.get(camera_name)
    build = camera_build(camera)
    qemu = paths.QEMU_BUILD / "arm-softmmu" / "qemu-system-arm"
    sd_image = build / "sd.qcow2"
    if not qemu.exists():
        raise typer.BadParameter("qemu-eos missing: run `astroml qemu setup`")
    if not sd_image.exists():
        raise typer.BadParameter(f"card image missing: run `astroml qemu sd --camera {camera.model}`")
    workdir = rom_dir(camera, trim_rom0)
    if not keep_crash_lock and clear_crash_lock(sd_image):
        typer.echo(f"removed {CRASH_LOCK} left by the previous run")
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    monitor = RUN_DIR / "monitor"
    monitor.unlink(missing_ok=True)
    cmd = [str(qemu), "-drive", f"if=sd,file={sd_image}",
           "-drive", f"if=ide,file={build / 'cf.qcow2'}",
           "-chardev", f"socket,server,nowait,path={monitor},id=monsock",
           "-mon", "chardev=monsock,mode=readline", "-name", camera.model,
           "-M", f"{camera.qemu_model},firmware=boot={1 if boot else 0}", "-display", display,
           "-serial", f"file:{RUN_DIR / 'serial.log'}"]
    if gdb:
        cmd += ["-S", "-gdb", "tcp::1234"]
    address = debugmsg_address(camera) if debugmsg else None
    if address:
        plugin = paths.QEMU_BUILD / "arm-softmmu" / "plugins" / "libmagiclantern.so"
        cmd += ["-plugin", f"{plugin},arg=debugmsg_addr={address}", "-d", "debugmsg"]
    env = dict(os.environ, QEMU_EOS_WORKDIR=str(workdir))
    typer.echo(f"QEMU_EOS_WORKDIR={env['QEMU_EOS_WORKDIR']} " + " ".join(cmd))
    with (RUN_DIR / "qemu.log").open("w") as log:
        subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)


def monitor_command(sock: socket.socket, command: str, delay: float) -> str:
    sock.sendall((command + "\n").encode("ascii"))
    time.sleep(delay)
    sock.setblocking(False)
    chunks = []
    try:
        while data := sock.recv(16384):
            chunks.append(data)
    except BlockingIOError:
        pass
    sock.setblocking(True)
    return b"".join(chunks).decode("latin1", errors="replace")


@app.command()
def keys(names: list[str] = typer.Argument(None, help="QEMU key names: delete up down "
                                                       "left right ret q spc ..."),
         screenshot: pathlib.Path = typer.Option(None, help="Save the LCD as PNG afterwards."),
         delay: float = typer.Option(0.6, help="Seconds after each key."),
         monitor: pathlib.Path = typer.Option(MONITOR, help="QEMU monitor socket.")):
    """Press camera buttons and capture the emulated LCD."""
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(5)
        sock.connect(str(monitor))
        try:
            sock.recv(4096)  # banner
        except socket.timeout:
            pass
        for key in names or []:
            reply = monitor_command(sock, "sendkey " + key, delay)
            typer.echo(f"key {key}" + (f": {reply.strip()[-200:]}" if "rror" in reply else ""))
        if screenshot:
            from PIL import Image
            ppm = screenshot.with_suffix(".ppm").resolve()
            ppm.parent.mkdir(parents=True, exist_ok=True)
            monitor_command(sock, f"screendump {ppm}", max(delay, 1.0))
            with Image.open(ppm) as image:
                image.convert("RGB").save(screenshot)
            ppm.unlink()
            typer.echo(f"saved {screenshot}")
