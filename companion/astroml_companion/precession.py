"""IAU 1976 precession between J2000 (the plate solver's frame) and the mean equator of date
(what most mount firmware works in). Nutation and aberration are ignored: they stay under about
40 arcseconds, and a sync after each solve absorbs them anyway."""
from __future__ import annotations

import datetime as dt
import math

J2000 = dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc)
ARCSEC = math.pi / 180 / 3600


def _matrix(when: dt.datetime) -> list[list[float]]:
    t = (when - J2000).total_seconds() / (36525 * 86400)
    zeta = (2306.2181 * t + 0.30188 * t * t + 0.017998 * t ** 3) * ARCSEC
    z = (2306.2181 * t + 1.09468 * t * t + 0.018203 * t ** 3) * ARCSEC
    theta = (2004.3109 * t - 0.42665 * t * t - 0.041833 * t ** 3) * ARCSEC
    cz, sz = math.cos(zeta), math.sin(zeta)
    cZ, sZ = math.cos(z), math.sin(z)
    ct, st = math.cos(theta), math.sin(theta)
    return [[cZ * ct * cz - sZ * sz, -cZ * ct * sz - sZ * cz, -cZ * st],
            [sZ * ct * cz + cZ * sz, -sZ * ct * sz + cZ * cz, -sZ * st],
            [st * cz, -st * sz, ct]]


def _rotate(matrix, ra_deg: float, dec_deg: float, transpose: bool) -> tuple[float, float]:
    a, d = math.radians(ra_deg), math.radians(dec_deg)
    v = (math.cos(d) * math.cos(a), math.cos(d) * math.sin(a), math.sin(d))
    if transpose:
        matrix = [list(row) for row in zip(*matrix)]
    x, y, w = (sum(matrix[i][j] * v[j] for j in range(3)) for i in range(3))
    return math.degrees(math.atan2(y, x)) % 360, math.degrees(math.asin(max(-1.0, min(1.0, w))))


def to_date(ra_deg: float, dec_deg: float, when: dt.datetime) -> tuple[float, float]:
    return _rotate(_matrix(when), ra_deg, dec_deg, transpose=False)


def to_j2000(ra_deg: float, dec_deg: float, when: dt.datetime) -> tuple[float, float]:
    return _rotate(_matrix(when), ra_deg, dec_deg, transpose=True)
