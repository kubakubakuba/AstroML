"""Dithering for drizzle: move the mount a few pixels between exposures, at random, so hot
pixels and sensor patterns land on different sky positions.

Each dither picks a new random offset from the *original* position (inside `radius_px`) and moves
by the difference, so the pointing never wanders away. RA-only trackers dither along RA only.
Moves are timed guide pulses: guide rate x sidereal speed, with RA slowed by cos(Dec) on the sky.
"""
from __future__ import annotations

import json
import math
import random
from importlib import resources

from .mount import EAST, NORTH, SOUTH, WEST, Mount

SIDEREAL_ARCSEC_S = 15.041
MIN_FRACTION = 0.3   # each dither moves at least this share of the radius from the origin
MIN_COS_DEC = 0.1    # near the pole RA moves almost nothing on the sky; cap the pulse length


def camera_facts() -> dict:
    """Sensor and photo widths per camera, exported from cameras.toml (`astroml cameras companion`)."""
    packaged = resources.files(__package__).joinpath("cameras.json")
    return json.loads(packaged.read_text()) if packaged.is_file() else {}


def pixel_scale_arcsec(fov_deg: float, image_width_px: int) -> float:
    return fov_deg * 3600 / image_width_px


class Dither:
    def __init__(self, mount: Mount, radius_px: float = 8.0, image_width_px=5472,
                 rng: random.Random | None = None):
        """image_width_px: an int, or a callable returning one (the connected camera's)."""
        self.mount = mount
        self.radius_px = radius_px
        self.image_width_px = image_width_px
        self.rng = rng or random.Random()
        self.offset = (0.0, 0.0)  # current offset from the origin, pixels (RA, Dec)

    def reset(self) -> None:
        """A new panel starts from its own origin."""
        self.offset = (0.0, 0.0)

    def _next_offset(self) -> tuple[float, float]:
        r = self.radius_px * (MIN_FRACTION + (1 - MIN_FRACTION) * self.rng.random())
        if self.mount.axes == "ra":
            return r * self.rng.choice((-1, 1)), 0.0
        angle = self.rng.uniform(0, 2 * math.pi)
        return r * math.cos(angle), r * math.sin(angle)

    def pulses(self, fov_deg: float, dec_deg: float) -> list[tuple[str, int]]:
        """Plan the next dither as (direction, ms) guide pulses, and remember the new offset."""
        target = self._next_offset()
        width = self.image_width_px() if callable(self.image_width_px) else self.image_width_px
        scale = pixel_scale_arcsec(fov_deg, width)
        rate_ra, rate_dec = self.mount.guide_rate
        d_ra = (target[0] - self.offset[0]) * scale
        d_dec = (target[1] - self.offset[1]) * scale
        cos_dec = max(MIN_COS_DEC, math.cos(math.radians(dec_deg)))
        out = []
        if d_ra:
            ms = abs(d_ra) / (SIDEREAL_ARCSEC_S * rate_ra * cos_dec) * 1000
            out.append((WEST if d_ra > 0 else EAST, round(ms)))
        if d_dec:
            ms = abs(d_dec) / (SIDEREAL_ARCSEC_S * rate_dec) * 1000
            out.append((NORTH if d_dec > 0 else SOUTH, round(ms)))
        self.offset = target
        return out

    def __call__(self, fov_deg: float, dec_deg: float) -> list[tuple[str, int]]:
        moves = self.pulses(fov_deg, dec_deg)
        for direction, ms in moves:
            self.mount.pulse(direction, ms)
        return moves
