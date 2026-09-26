"""Эволюция §35 Б — вариант evo_anom_v1: аномалия к локальной воде → сумма избытка NIR → N через k из fit (LOCO).

Пиксель-кандидат: вода (или рядом с водой), z(ΔB8) > T8 и z(ΔB8A) > T8A (независимый шум 10 м / 20 м), ΔFDI > 0,
где Δ = полоса − медиана 9×9, z = Δ / (1.4826·MAD Δ по воде окна). Пороги — по НЕЗАВИСИМОМУ фону
(out/evo/bg.npz: 640 окон сцен data/live без cozar_demo + 300 окон MARIDA train/val без мусора) — доля фоновых окон
1.64 км² хотя бы с одним кандидатом ≈ ALPHA. N = k · Σ ΔB8 по кандидатам в ROI; k — из fit (медиана log(N/S) по
обучающим кампаниям), без fit — физический априор 100 м² × 16.64 бут./м² / 0.1 отражения. Нет кандидатов → None.
"""
import hashlib

import numpy as np
from scipy import ndimage

NAME = "evo_anom_v1"
KSIZE = 9
T8, T8A = 6.0, 3.0          # заменяются калибровкой по фону (см. CALIB)
CALIB = "alpha=0.10 on out/evo/bg.npz"
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
        w = (np.asarray(scl) == 6) & np.isfinite(b8)
    else:
        w = spec & np.isfinite(b8)
    return w


def _delta(a):
    a = np.where(np.isfinite(a), a, np.nanmedian(a) if np.isfinite(a).any() else 0.0).astype(np.float32)
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
    b6, b8, b11 = (np.asarray(wb[x], np.float32) for x in ("B6", "B8", "B11"))
    fdi = b8 - (b6 + (b11 - b6) * (842 - 665) / (1610 - 665) * 10)
    d8, d8a, dfdi = _delta(wb["B8"]), _delta(wb["B8A"]), _delta(fdi)
    z8, z8a = _z(d8, w), _z(d8a, w)
    near_w = ndimage.binary_dilation(w, iterations=2)
    f = dict(w=w, near_w=near_w, d8=d8, z8=z8, z8a=z8a, dfdi=dfdi, fin=np.isfinite(b8))
    if len(_cache) > 4000:
        _cache.clear()
    _cache[k] = f
    return f


def candidates(f, t8=None, t8a=None):
    t8 = T8 if t8 is None else t8
    t8a = T8A if t8a is None else t8a
    return f["near_w"] & f["fin"] & (f["z8"] > t8) & (f["z8a"] > t8a) & (f["dfdi"] > 0)


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
        _state["k"] = float(np.exp(np.median(r)))
        _state["spread"] = float(max(np.std(r), np.log(1.5)))
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
