"""Helpers for discovery methods (L133): band stacking, FDI, water mask, current detector with a disk cache.

Methods may import this module (``from lib import detector_prob, fdi, stack`` after the harness put scripts/discovery on
sys.path) — every function here depends only on the window pixels, never on answers.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
BANDS11 = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
DET_CACHE = ROOT / "data" / "discovery" / "detcache"
_PRED = None

# zone_estimate.yaml (L131): items per detector pixel, PLP2019 A* dates 18.04 / 18.05.2019
ITEMS_PER_PX_LO, ITEMS_PER_PX_HI = 470.0, 670.0
ITEMS_PER_PX_MID = float(np.sqrt(ITEMS_PER_PX_LO * ITEMS_PER_PX_HI))  # 561.2, geometric mean (zone_estimate point)


def stack(wb: dict) -> np.ndarray:
    """window_bands dict -> (11, H, W) float32 in BANDS11 order."""
    return np.stack([np.asarray(wb[b], np.float32) for b in BANDS11])


def fdi(wb: dict) -> np.ndarray:
    """Floating Debris Index (Biermann et al. 2020), same form as scripts/count_bridge/plp_bridge_s2.fdi."""
    b6, b8, b11 = (np.asarray(wb[k], np.float32) for k in ("B6", "B8", "B11"))
    return b8 - (b6 + (b11 - b6) * (842 - 665) / (1610 - 665) * 10)


def water_mask(wb: dict) -> np.ndarray:
    """SCL == 6 where SCL is known; else spectral: NDWI(B3,B8) > 0.1, B2 < 0.2, B11 < 0.05 (as lgbm water_offset)."""
    b3, b8, b2, b11 = (np.asarray(wb[k], np.float32) for k in ("B3", "B8", "B2", "B11"))
    with np.errstate(invalid="ignore", divide="ignore"):
        spec = ((b3 - b8) / (b3 + b8) > 0.1) & (b2 < 0.2) & (b11 < 0.05)
    scl = wb.get("SCL")
    if scl is not None and np.asarray(scl).any():
        return (np.asarray(scl) == 6) & np.isfinite(b8)
    return spec & np.isfinite(b8)


def _predictor():
    global _PRED
    if _PRED is None:
        from macroplastic.models.lgbm_predict import load_predictor
        _PRED = load_predictor(ROOT / "weights" / "lgbm", harmonize="none", device="cpu")
    return _PRED


def detector_threshold() -> float:
    return float(_predictor().threshold)


def detector_prob(wb: dict) -> np.ndarray:
    """P(marine debris) of the current detector (weights/lgbm, no harmonisation) for the whole window (H, W).
    Cached on disk by the sha1 of the band bytes (deterministic function of the pixels)."""
    arr = stack(wb)
    key = hashlib.sha1(arr.tobytes()).hexdigest()
    f = DET_CACHE / f"{key}.npy"
    if f.exists():
        return np.load(f)
    p = _predictor().predict_proba(arr, BANDS11).astype(np.float32)
    DET_CACHE.mkdir(parents=True, exist_ok=True)
    np.save(f, p)
    return p
