"""Спектральный флаг «вероятно органика» (INBOX §51 п.9, docs/ORGANIC.md) — ЭКСПЕРИМЕНТ.

Правило записано в docs/ORGANIC.md до расчёта и на MARIDA не подбиралось:
    likely_organic = (NDVI >= 0.20) and (FAI > 0)
NDVI > 0.2 — растительный отклик (Biermann et al. 2020, Sci Rep 10:5364: водоросли выше пластика по NDVI; граница
приближённая, по рисунку); FAI > 0 — плавающий материал над водой (Hu 2009, RSE 113:2118–2129).
Для зоны — медианы NDVI и FAI по пикселям детекции зоны.
"""
from __future__ import annotations

import numpy as np

NDVI_MIN = 0.20
FAI_MIN = 0.0
RULE = "NDVI ≥ 0,20 и FAI > 0 (медианы по пикселям детекции зоны)"
SOURCES = "Biermann et al. 2020 (Sci Rep 10:5364) — NDVI; Hu 2009 (RSE 113:2118) — FAI"
LABEL = "эксперимент"
NOT_SEPARATED = "органика отдельно не выделяется моделью (детектор бинарный: мусор / нет)"
# FAI baseline B4 (665 nm) -> B11 (1614 nm), NIR B8 (833 nm) — same as macroplastic.indices.fai
_K = (833.0 - 665.0) / (1614.0 - 665.0)


def ndvi(b4, b8):
    b4 = np.asarray(b4, np.float32)
    b8 = np.asarray(b8, np.float32)
    s = b8 + b4
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(np.abs(s) > 1e-6, (b8 - b4) / s, np.nan).astype(np.float32)


def fai(b4, b8, b11):
    b4 = np.asarray(b4, np.float32)
    return (np.asarray(b8, np.float32) - (b4 + (np.asarray(b11, np.float32) - b4) * _K)).astype(np.float32)


def flag_pixels(b4, b8, b11, ndvi_min: float = NDVI_MIN):
    """Per-pixel flag (bool array); NaN -> False."""
    nv, fa = ndvi(b4, b8), fai(b4, b8, b11)
    return np.nan_to_num(nv, nan=-9) >= ndvi_min, (np.nan_to_num(fa, nan=-9) > FAI_MIN)


def zone_flag(b4, b8, b11) -> dict:
    """Zone-level flag from the detection pixels of a zone (1-D arrays of reflectance)."""
    nv, fa = ndvi(b4, b8), fai(b4, b8, b11)
    ok = np.isfinite(nv) & np.isfinite(fa)
    if ok.sum() == 0:
        return {"likely_organic": None, "ndvi_median": None, "fai_median": None, "organic_px_share": None,
                "n_px": 0}
    nm, fm = float(np.median(nv[ok])), float(np.median(fa[ok]))
    px = (nv[ok] >= NDVI_MIN) & (fa[ok] > FAI_MIN)
    return {"likely_organic": bool(nm >= NDVI_MIN and fm > FAI_MIN), "ndvi_median": round(nm, 3),
            "fai_median": round(fm, 4), "organic_px_share": round(float(px.mean()), 3), "n_px": int(ok.sum())}
