"""`astroml-companion`: run the hotspot page, or read the camera once."""
from __future__ import annotations

import pathlib

import typer

from . import protocol
from .camera import CameraError, FakeCamera, UsbCamera
from .dither import Dither, camera_facts
from .poller import Poller
from .session import Session
from .state import SharedStatus
from .web import make_server

DEFAULT_IMAGE_WIDTH = 5472


def make_mount(kind: str, serial: str, epoch: str, st4_axes: str, guide_rate: float):
    if kind == "none":
        return None
    if kind == "ioptron":
        from .mount.ioptron import IOptronMount
        return IOptronMount(serial, epoch=epoch)
    if kind == "st4":
        from .mount.st4 import St4Mount
        return St4Mount(axes=st4_axes, guide_rate=guide_rate)
    if kind == "fake":
        from .mount.fake import FakeMount
        return FakeMount()
    raise typer.BadParameter(f"unknown mount {kind!r}: none, ioptron, st4 or fake")


app = typer.Typer(help="AstroML companion: plate solutions from the camera, on your phone.",
                  no_args_is_help=True)


@app.command()
def run(fake: bool = typer.Option(False, help="Use a simulated camera (no USB)."),
        host: str = typer.Option("0.0.0.0", help="Address to listen on."),
        port: int = typer.Option(80, help="HTTP port; 80 is what captive portals expect."),
        portal_host: str = typer.Option(None, help="Redirect every other host name here, "
                                                   "e.g. 10.42.0.1 on the hotspot."),
        interval: float = typer.Option(2.0, help="Seconds between camera reads."),
        state_dir: pathlib.Path = typer.Option(pathlib.Path.home() / ".local/state/astroml-companion",
                                               help="Where the plan and session progress are kept."),
        mount: str = typer.Option("none", help="none, ioptron (CEM60 over serial), st4 (GPIO "
                                               "guide port, e.g. SkyGuider) or fake."),
        serial: str = typer.Option("/dev/ttyUSB0", help="Serial port of the iOptron cable."),
        mount_epoch: str = typer.Option("jnow", help="Coordinates the mount firmware uses: "
                                                     "jnow (of date) or j2000."),
        st4_axes: str = typer.Option("ra", help="ST-4 lines wired: ra (RA-only tracker) or both."),
        guide_rate: float = typer.Option(0.5, help="ST-4 guide rate, x sidereal (the mount's "
                                                   "setting; iOptron mounts report their own)."),
        dither_px: float = typer.Option(8.0, help="Dither radius in photo pixels.")):
    """Poll the camera and serve the status page until stopped."""
    status = SharedStatus()
    the_mount = make_mount(mount, serial, mount_epoch, st4_axes, guide_rate)
    dither = None
    if the_mount is not None:
        facts = camera_facts()

        def image_width() -> int:
            model = status.get().camera.replace(" (fake)", "")
            return facts.get(model, {}).get("image_width_px", DEFAULT_IMAGE_WIDTH)

        dither = Dither(the_mount, radius_px=dither_px, image_width_px=image_width)
    session = Session(state_dir / "session.json", dither=dither, mount=the_mount)
    camera = FakeCamera() if fake else UsbCamera()
    if fake and mount == "fake":
        camera.pointing = the_mount.actual
    poller = Poller(camera, status, interval, session, the_mount)
    server = make_server(host, port, status, poller, portal_host)
    poller.start()
    typer.echo(f"serving on http://{host}:{port}/ ({'fake camera' if fake else 'USB camera'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        poller.stop()


@app.command()
def status():
    """Connect over USB once and print what the camera reports."""
    camera = UsbCamera()
    try:
        camera.connect()
        layout, api = camera.version()
        typer.echo(f"AstroML layout {layout}, API {api}")
        for name, record in (("solution", camera.solution()), ("reference", camera.reference())):
            if record is None or not record.valid:
                typer.echo(f"{name}: none")
                continue
            typer.echo(f"{name}: {protocol.ra_hms(record.ra_deg)} {protocol.dec_dms(record.dec_deg)}"
                       f"  field {record.fov_deg:.1f} deg  north {record.north_deg:+.1f} deg"
                       f"  {record.matched} stars  ({record.source}, {record.camera})")
    except CameraError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1)
    finally:
        camera.close()


mount_app = typer.Typer(help="Try the mount or the ST-4 board on their own, without a session.",
                        no_args_is_help=True)
app.add_typer(mount_app, name="mount")

MOUNT_KIND = typer.Option("ioptron", "--mount", help="ioptron, st4 or fake.")
SERIAL = typer.Option("/dev/ttyUSB0", help="Serial port of the iOptron cable.")
EPOCH = typer.Option("jnow", "--mount-epoch", help="jnow (of date) or j2000.")
ST4_AXES = typer.Option("ra", help="ST-4 lines wired: ra or both.")
GUIDE_RATE = typer.Option(0.5, help="ST-4 guide rate, x sidereal.")


def _open(kind, serial, epoch, st4_axes, guide_rate):
    from .mount import MountError

    mount = make_mount(kind, serial, epoch, st4_axes, guide_rate)
    if mount is None:
        raise typer.BadParameter("choose a mount")
    try:
        mount.connect()
    except MountError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1)
    return mount


def _report(mount) -> None:
    s = mount.status()
    where = (f"{protocol.ra_hms(s.ra_deg)} {protocol.dec_dms(s.dec_deg)} (J2000)"
             if s.ra_deg is not None else "position unknown")
    typer.echo(f"{s.detail}: {where}; guide rate {mount.guide_rate[0]:.2f}/{mount.guide_rate[1]:.2f}x")


@mount_app.command("status")
def mount_status(mount: str = MOUNT_KIND, serial: str = SERIAL, mount_epoch: str = EPOCH,
                 st4_axes: str = ST4_AXES, guide_rate: float = GUIDE_RATE):
    """Connect and print the model, state, position and guide rates."""
    m = _open(mount, serial, mount_epoch, st4_axes, guide_rate)
    try:
        _report(m)
    finally:
        m.close()


@mount_app.command("goto")
def mount_goto(ra_deg: float = typer.Argument(..., help="J2000 RA in degrees."),
               dec_deg: float = typer.Argument(..., help="J2000 Dec in degrees."),
               mount: str = MOUNT_KIND, serial: str = SERIAL, mount_epoch: str = EPOCH):
    """Slew to J2000 coordinates (no sync is ever sent by this command)."""
    from .mount import MountError

    m = _open(mount, serial, mount_epoch, "ra", 0.5)
    try:
        m.goto(ra_deg, dec_deg)
        typer.echo("slewing; `astroml-companion mount stop` aborts")
    except MountError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1)
    finally:
        m.close()


@mount_app.command("stop")
def mount_stop(mount: str = MOUNT_KIND, serial: str = SERIAL):
    """Stop a slew."""
    m = _open(mount, serial, "jnow", "ra", 0.5)
    try:
        m.stop()
    finally:
        m.close()


@mount_app.command("pulse")
def mount_pulse(direction: str = typer.Argument(..., help="n, s, e or w."),
                ms: int = typer.Argument(..., help="Pulse length in milliseconds."),
                mount: str = MOUNT_KIND, serial: str = SERIAL, st4_axes: str = ST4_AXES,
                guide_rate: float = GUIDE_RATE):
    """One guide pulse, e.g. to watch the ST-4 board's LEDs or the mount's RA axis move."""
    from .mount import MountError

    m = _open(mount, serial, "jnow", st4_axes, guide_rate)
    try:
        m.pulse(direction, ms)
        typer.echo(f"pulsed {direction} for {ms} ms")
    except (MountError, ValueError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1)
    finally:
        m.close()


if __name__ == "__main__":
    app()
