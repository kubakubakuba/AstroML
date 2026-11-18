from __future__ import annotations

import math
import random

import pytest

from astroml_companion.dither import SIDEREAL_ARCSEC_S, Dither
from astroml_companion.mount import EAST, NORTH, SOUTH, WEST


class Recorder:
    def __init__(self, axes):
        self.axes = axes
        self.guide_rate = (0.5, 0.5)
        self.moves = []

    def pulse(self, direction, ms):
        self.moves.append((direction, ms))


def test_ra_only_tracker_moves_only_in_ra():
    mount = Recorder("ra")
    dither = Dither(mount, radius_px=8, image_width_px=5472, rng=random.Random(1))
    for _ in range(20):
        dither(15.6, 40.0)
    assert {d for d, _ in mount.moves} <= {EAST, WEST}
    assert all(abs(x) <= 8 for x, _ in [dither.offset])


def test_offsets_stay_inside_the_radius_and_move_back():
    dither = Dither(Recorder("both"), radius_px=10, rng=random.Random(3))
    for _ in range(50):
        dither.pulses(10.0, 0.0)
        r = math.hypot(*dither.offset)
        assert 3 - 1e-9 <= r <= 10 + 1e-9  # MIN_FRACTION 0.3 of the radius


def test_pulse_length_from_pixel_scale_and_guide_rate():
    """6 px at 10.26"/px (6D at 135 mm, FOV 15.6) on the equator at 0.5x: 61.6" / 7.52"/s."""
    mount = Recorder("both")
    dither = Dither(mount, radius_px=6, image_width_px=5472, rng=random.Random(0))
    dither.offset = (0.0, 0.0)
    dither._next_offset = lambda: (6.0, 0.0)
    (move,) = dither.pulses(15.6, 0.0)
    scale = 15.6 * 3600 / 5472
    assert move == (WEST, round(6 * scale / (SIDEREAL_ARCSEC_S * 0.5) * 1000))


def test_ra_pulses_lengthen_with_declination():
    a, b = Dither(Recorder("ra")), Dither(Recorder("ra"))
    a._next_offset = b._next_offset = lambda: (5.0, 0.0)
    (_, at_equator), = a.pulses(10.0, 0.0)
    (_, at_60), = b.pulses(10.0, 60.0)
    assert at_60 == pytest.approx(2 * at_equator, rel=0.01)


def test_width_can_come_from_the_connected_camera():
    dither = Dither(Recorder("both"), radius_px=5, image_width_px=lambda: 5184)
    dither._next_offset = lambda: (0.0, 5.0)
    ((direction, ms),) = dither.pulses(10.0, 0.0)
    assert direction == NORTH and ms == round(5 * 10.0 * 3600 / 5184 / (SIDEREAL_ARCSEC_S * 0.5) * 1000)
    dither._next_offset = lambda: (0.0, 0.0)
    assert dither.pulses(10.0, 0.0)[0][0] == SOUTH  # back towards the origin
