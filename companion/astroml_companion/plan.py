"""Imaging plans: a target or a mosaic of panels, and how many frames to take at each.

Panels are laid out on the target's tangent plane along the camera frame's own axes, so every
panel has the same orientation there. The north angle each panel needs (north's direction on
screen, clockwise from up) is recomputed at its centre on its own tangent plane, which is how
the camera measures it: near the pole, north turns across a mosaic even though the frames do
not.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

ASPECT = 2 / 3  # height / width of a 3:2 frame


@dataclass
class Plan:
    ra_deg: float
    dec_deg: float
    name: str = ""
    north_deg: float = 0.0      # camera rotation at the target: north clockwise from up
    columns: int = 1
    rows: int = 1
    overlap: float = 0.2        # fraction of the frame shared with a neighbour
    frames: int = 10            # per panel
    exposure_s: float = 60.0
    dither_every: int = 0       # 0: never (dithering arrives with mount control)
    settle_s: float = 5.0       # wait after a dither before the next exposure
    fov_deg: float | None = None  # horizontal field of view; None: take it from the last solve
    mirrored: bool = False

    def validate(self) -> None:
        problems = []
        if not -90 <= self.dec_deg <= 90:
            problems.append("declination must be -90..90")
        if not (1 <= self.columns <= 10 and 1 <= self.rows <= 10):
            problems.append("columns and rows must be 1..10")
        if not 0 <= self.overlap < 0.9:
            problems.append("overlap must be 0..90 %")
        if not 1 <= self.frames <= 999:
            problems.append("frames must be 1..999")
        if not 1 <= self.exposure_s <= 1800:
            problems.append("exposure must be 1..1800 s")
        if self.dither_every < 0 or self.settle_s < 0:
            problems.append("dither and settle cannot be negative")
        if self.fov_deg is not None and not 0.2 <= self.fov_deg <= 120:
            problems.append("field of view must be 0.2..120 degrees")
        if problems:
            raise ValueError("; ".join(problems))

    def to_json(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "Plan":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class Panel:
    index: int
    column: int
    row: int                    # 0 is the top row of the mosaic
    ra_deg: float
    dec_deg: float
    north_deg: float
    offset: tuple[float, float]  # frame widths right and down from the mosaic centre

    def to_json(self) -> dict:
        return dataclasses.asdict(self)


def tangent_to_sky(ra0: float, dec0: float, xi: float, eta: float) -> tuple[float, float]:
    """Inverse gnomonic projection; xi east and eta north in radians on the tangent plane."""
    a0, d0 = math.radians(ra0), math.radians(dec0)
    rho = math.hypot(xi, eta)
    if rho == 0:
        return ra0 % 360, dec0
    c = math.atan(rho)
    dec = math.asin(math.cos(c) * math.sin(d0) + eta * math.sin(c) * math.cos(d0) / rho)
    ra = a0 + math.atan2(xi * math.sin(c),
                         rho * math.cos(d0) * math.cos(c) - eta * math.sin(d0) * math.sin(c))
    return math.degrees(ra) % 360, math.degrees(dec)


def sky_to_tangent(ra0: float, dec0: float, ra: float, dec: float) -> tuple[float, float]:
    a0, d0, a, d = map(math.radians, (ra0, dec0, ra, dec))
    cos_c = math.sin(d0) * math.sin(d) + math.cos(d0) * math.cos(d) * math.cos(a - a0)
    xi = math.cos(d) * math.sin(a - a0) / cos_c
    eta = (math.cos(d0) * math.sin(d) - math.sin(d0) * math.cos(d) * math.cos(a - a0)) / cos_c
    return xi, eta


def frame_axes(north_deg: float, mirrored: bool) -> tuple[tuple[float, float], tuple[float, float]]:
    """Screen right and screen down as (east, north) unit vectors on the sky."""
    t = math.radians(north_deg)
    east = 1.0 if mirrored else -1.0
    right = (east * math.cos(t), math.sin(t))
    down = (east * math.sin(t), -math.cos(t))
    return right, down


STEP = 1e-5  # radians, for the local direction of the frame axis


def panel_north(plan: Plan, xi: float, eta: float) -> float:
    """North angle (clockwise from up, as the camera measures it on the panel's own tangent
    plane) for a frame whose horizontal axis stays parallel to the mosaic's on the target plane.
    Measuring north on the target plane instead would tilt outer panels by up to a degree."""
    right, _down = frame_axes(plan.north_deg, plan.mirrored)
    ra, dec = tangent_to_sky(plan.ra_deg, plan.dec_deg, xi, eta)
    ahead = tangent_to_sky(plan.ra_deg, plan.dec_deg, xi + STEP * right[0], eta + STEP * right[1])
    rx, ry = sky_to_tangent(ra, dec, *ahead)  # screen-right on the panel's own plane
    east = 1.0 if plan.mirrored else -1.0
    return math.degrees(math.atan2(ry, east * rx))


def panels(plan: Plan, fov_deg: float) -> list[Panel]:
    """Panel centres, top row first, left to right."""
    half_width = math.tan(math.radians(fov_deg) / 2)
    step_x = 2 * half_width * (1 - plan.overlap)
    step_y = 2 * half_width * ASPECT * (1 - plan.overlap)
    right, down = frame_axes(plan.north_deg, plan.mirrored)
    out = []
    for row in range(plan.rows):
        for column in range(plan.columns):
            u = (column - (plan.columns - 1) / 2) * step_x
            v = (row - (plan.rows - 1) / 2) * step_y
            xi = u * right[0] + v * down[0]
            eta = u * right[1] + v * down[1]
            ra, dec = tangent_to_sky(plan.ra_deg, plan.dec_deg, xi, eta)
            out.append(Panel(index=len(out), column=column, row=row, ra_deg=ra, dec_deg=dec,
                             north_deg=panel_north(plan, xi, eta),
                             offset=(u / (2 * half_width), v / (2 * half_width))))
    return out
