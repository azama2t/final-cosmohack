"""Is the detector result evaluable on a scene? Shared by the studio API (service/routes_v3_studio.py, L95) and the
ADIS pair registry (scripts/search/adis_pairs.py, L100), so that both give the same verdict for the same scene.

Rule (chosen by L95 on the studio scenes, reports/tasklog/95_studio_api.md): the detector is «не оценивается» when
 - the sun is low: solar zenith at the scene centre and acquisition time >= SUN_ZENITH_MAX (58 deg) — every scene with a
   mass response (> 130 ppm of usable water, up to 7575 «objects») outside the coastal Guanabara crops had 59.7–61.2 deg
   (ADIS 30.10.2021, 17.11.2020, 25.11.2023); below 58 deg the maximum was 160 ppm;
 - or the water signal is weak: median B3 reflectance of usable water < WATER_B3_MIN (0.003) — ADIS 30.10.2021: 0.0001.
"""
from __future__ import annotations

import datetime as _dt
import math
from pathlib import Path
from typing import Optional

import numpy as np

SUN_ZENITH_MAX = 58.0
WATER_B3_MIN = 0.003


def solar_zenith(iso: Optional[str], bounds) -> Optional[float]:
    """Solar zenith angle (deg) at the centre of lon/lat `bounds` [w, s, e, n] and time `iso` (NOAA solar position,
    error < 0.5 deg). None if time or bounds are missing/unparseable."""
    if not iso or not bounds:
        return None
    try:
        t = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(_dt.timezone.utc)
    except ValueError:
        return None
    lon, lat = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
    doy = t.timetuple().tm_yday
    hour = t.hour + t.minute / 60 + t.second / 3600
    g = 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g)
                    - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    tst = hour * 60 + eqt + 4 * lon
    ha = math.radians(tst / 4 - 180)
    la = math.radians(lat)
    cz = math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)
    return round(math.degrees(math.acos(max(-1.0, min(1.0, cz)))), 1)


def nearest_resize(a: np.ndarray, ow: int, oh: int) -> np.ndarray:
    h, w = a.shape[:2]
    if (w, h) == (ow, oh):
        return a
    yi = np.minimum((np.arange(oh) + 0.5) * h / oh, h - 1).astype(int)
    xi = np.minimum((np.arange(ow) + 0.5) * w / ow, w - 1).astype(int)
    return a[yi][:, xi]


def rgb_png_b3(path: Path, thumb: int = 512) -> np.ndarray:
    """Green reflectance inverted from rgb.png of pair_quality.py (fixed stretch 0..0.16, gamma 1/1.8 ->
    reflectance = 0.16 * (v/255)^1.8), thumbnail <= `thumb` px (nearest)."""
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((thumb, thumb), Image.NEAREST)
        v = np.asarray(im)[:, :, 1].astype(np.float32)
    return 0.16 * (v / 255.0) ** 1.8


def water_median(g: np.ndarray, q: Optional[np.ndarray], water_code: int = 1) -> Optional[float]:
    """Median of `g` over water pixels of the quality raster `q` (resized nearest to g); all finite pixels if < 50."""
    h, w = g.shape
    water = nearest_resize(q, w, h) == water_code if q is not None else None
    vals = g[water] if water is not None and water.sum() >= 50 else g[np.isfinite(g)]
    vals = vals[np.isfinite(vals)]
    return round(float(np.median(vals)), 4) if vals.size else None


def detector_evaluable(sun_zenith_deg: Optional[float], water_b3_median: Optional[float],
                       zenith_max: float = SUN_ZENITH_MAX, b3_min: float = WATER_B3_MIN) -> tuple[bool, bool, bool]:
    """-> (evaluable, low_sun, weak_signal). Unknown inputs do not make the scene non-evaluable."""
    low = sun_zenith_deg is not None and sun_zenith_deg >= zenith_max
    weak = water_b3_median is not None and water_b3_median < b3_min
    return (not (low or weak)), low, weak
