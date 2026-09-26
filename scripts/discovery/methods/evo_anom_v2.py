"""Эволюция §35 Б — evo_anom_v2 (мутация v1): аномалия к локальной воде + отсев ярких объектов, берега и формы спектра.

Кандидат: z(ΔB8) > T8 и z(ΔB8A) > T8A и ΔFDI > 0 (Δ = полоса − медиана 9×9; z по MAD воды окна), плюс
  • доля воды в 9×9 ≥ WFRAC (берег/суша не считаются);
  • ΔB8 ≤ DMAX и нет «яркого» пикселя (ΔB8 > DMAX или B2 > 0.15) в радиусе 2 пикс. (суда, облака, их ореолы) —
    субпиксельный плавающий мусор не бывает ярче DMAX (мишени PLP ~0.01–0.06);
  • ΔB8 > ΔB2 (NIR-избыток больше синего: пластик/растительность, а не пена/облако/дымка), если NIR_SHAPE.
Пороги T8/T8A — по независимому фону out/evo/bg.npz (см. CALIB). N = k · Σ ΔB8 в ROI; k из fit (LOCO), иначе априор.
"""
import hashlib

import numpy as np
from scipy import ndimage

NAME = "evo_anom_v2"
KSIZE = 9
T8, T8A = 6.0, 4.5
WFRAC = 0.8
DMAX = 0.08
NIR_SHAPE = True
CALIB = "set after calib on out/evo/bg.npz"
K_PRIOR = 100.0 * 16.64 / 0.1
SPREAD_PRIOR = np.log(3.0)
_state = {"k": K_PRIOR, "spread": SPREAD_PRIOR}
_cache = {}


def _key(wb):
    return hashlib.sha1(np.ascontiguousarray(wb["B8"]).tobytes() + np.ascontiguousarray(wb["B11"]).tobytes()).hexdigest()


def _water(wb):
    b3, b8, b2, b11 = (np.asarray(wb[k], np.float32) for k in ("B3", "B8", "B2", "B11"))
    with np.errstate(invalid="ignore", divide="ignore"):
        spec = ((b3 - b8) / (b3 + b8) > 0.1) & (b2 < 0.2) & (b11 < 0.05)
    scl = wb.get("SCL")
    if scl is not None and np.asarray(scl).any():
        return (np.asarray(scl) == 6) & np.isfinite(b8)
    return spec & np.isfinite(b8)


def _fill(a):
    a = np.asarray(a, np.float32)
    return np.where(np.isfinite(a), a, np.nanmedian(a) if np.isfinite(a).any() else 0.0).astype(np.float32)


def _delta(a):
    a = _fill(a)
    return a - ndimage.median_filter(a, size=KSIZE, mode="reflect")


def _z(d, w):
    ref = d[w] if w.sum() >= 200 else d.ravel()
    mad = np.median(np.abs(ref - np.median(ref))) * 1.4826
    return d / max(mad, 1e-5)


def features(wb):
    k = _key(wb)
    f = _cache.get(k)
    if f is not None:
        return f
    w = _water(wb)
    b2, b6, b8, b11 = (_fill(wb[x]) for x in ("B2", "B6", "B8", "B11"))
    fdi = b8 - (b6 + (b11 - b6) * (842 - 665) / (1610 - 665) * 10)
    d8, d8a, dfdi, d2 = _delta(b8), _delta(wb["B8A"]), _delta(fdi), _delta(b2)
    wfrac = ndimage.uniform_filter(w.astype(np.float32), KSIZE)
    bright = (d8 > DMAX) | (b2 > 0.15)
    near_bright = ndimage.binary_dilation(bright, iterations=2)
    f = dict(w=w, d8=d8, d2=d2, z8=_z(d8, w), z8a=_z(d8a, w), dfdi=dfdi, fin=np.isfinite(np.asarray(wb["B8"])),
             ok=(wfrac >= WFRAC) & ~near_bright)
    if len(_cache) > 4000:
        _cache.clear()
    _cache[k] = f
    return f


def candidates(f, t8=None, t8a=None):
    t8 = T8 if t8 is None else t8
    t8a = T8A if t8a is None else t8a
    c = f["ok"] & f["fin"] & (f["z8"] > t8) & (f["z8a"] > t8a) & (f["dfdi"] > 0)
    if NIR_SHAPE:
        c &= f["d8"] > f["d2"]
    return c


def signal(wb, roi):
    f = features(wb)
    c = candidates(f) & roi
    return float(np.clip(f["d8"][c], 0, None).sum()), int(c.sum())


def fit(train):
    r = []
    for t in train:
        n = t["N"]
        if not np.isfinite(n) or n <= 0:
            continue
        s, _ = signal(t["bands"], t["meta"]["roi"])
        if s > 0:
            r.append(np.log(n / s))
    if len(r) >= 2:
        _state["k"], _state["spread"] = float(np.exp(np.median(r))), float(max(np.std(r), np.log(1.5)))
    elif len(r) == 1:
        _state["k"], _state["spread"] = float(np.exp(r[0])), SPREAD_PRIOR
    else:
        _state["k"], _state["spread"] = K_PRIOR, SPREAD_PRIOR


def predict(window_bands, meta):
    s, n = signal(window_bands, meta["roi"])
    if n == 0:
        return None
    N = _state["k"] * s
    if N < 1:
        return None
    e = np.exp(1.645 * _state["spread"])
    return N, N / e, N * e
