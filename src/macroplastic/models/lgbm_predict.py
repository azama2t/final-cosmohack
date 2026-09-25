"""Lane L3 predictor: pixel LightGBM for Marine Debris (trained on MARIDA by scripts/train_lgbm.py).

    from macroplastic.models.lgbm_predict import load_predictor
    pred = load_predictor()                       # weights/lgbm/{model.txt, meta.json}
    prob = pred.predict_proba(arr, channel_names)  # arr (C,H,W) float32 reflectance 0..1 -> (H,W) float32 P(MD)
    mask = prob >= pred.threshold
    # live Sen2Cor L2A scenes (domain shift vs ACOLITE rhorc): optional per-scene water-median harmonization
    pred = load_predictor(harmonize="water_median"); prob = pred.predict_proba(arr, names, water_mask=scl == 6)

The 11 MARIDA bands are picked by name (extra bands such as B9 of L2A are ignored). Pixels with
any NaN/inf band -> probability 0. Large tiles are processed in 512 px blocks with a halo
(memory ~ a few hundred MB for 2500x2500). Registered in the model registry as 'lgbm'.
Domain note: trained on ACOLITE rhorc (L1C, Rayleigh-corrected); on Sen2Cor L2A the reflectance
levels differ (see reports/l3_lgbm.md), so the probability is not calibrated there.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Sequence

import numpy as np

from ..features.pixel import BANDS11, compute_features_blocked, feature_names, select_bands

_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WEIGHTS = _ROOT / "weights" / "lgbm"
# MARIDA train "Marine Water" (class 7) per-band medians, used if meta.json has no water_ref
_WATER_REF_FALLBACK = [0.0368, 0.0342, 0.0268, 0.0175, 0.0142, 0.014, 0.0148, 0.0121, 0.014, 0.0086, 0.0062]


def _threads() -> int:
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        try:
            import psutil

            return len(psutil.Process().cpu_affinity())
        except Exception:
            return os.cpu_count() or 4


class LGBMPredictor:
    def __init__(self, weights_dir: str | os.PathLike = DEFAULT_WEIGHTS, block: int = 512, num_threads: int | None = None,
                 harmonize: str | None = None):
        import lightgbm as lgb

        wd = Path(weights_dir)
        if wd.is_file():  # allow passing model.txt directly
            wd = wd.parent
        self.meta = json.loads((wd / "meta.json").read_text(encoding="utf-8"))
        self.booster = lgb.Booster(model_file=str(wd / "model.txt"))
        self.name = "lgbm"
        self.threshold = float(self.meta["threshold"])
        self.task = self.meta["task"]
        self.classes = list(self.meta["classes"])
        self.level = self.meta["feature_level"]
        self.features = list(self.meta["features"])
        all_names = feature_names(self.level)
        if all_names != self.features:
            # model trained on a subset of the 'win' features
            all_names = feature_names("win")
            self.level = "win"
        self.fidx = [all_names.index(n) for n in self.features]
        self.min_px = int(self.meta.get("postprocess_min_px", 0))
        self.block = int(block)
        self.num_threads = num_threads or _threads()
        if harmonize not in (None, "none", "water_median"):
            raise ValueError(f"harmonize must be None or 'water_median', got {harmonize!r}")
        self.harmonize = None if harmonize in (None, "none") else harmonize
        self.water_ref = np.asarray(self.meta.get("water_ref", _WATER_REF_FALLBACK), np.float32)
        self.last_offset = None  # per-band offset applied by the last harmonized call (diagnostics)

    def _predict_rows(self, X: np.ndarray) -> np.ndarray:
        p = self.booster.predict(X, num_threads=self.num_threads)
        if self.task == "multiclass":
            p = p[:, self.classes.index(int(self.meta.get("md_class", 1)))]
        return np.asarray(p, dtype=np.float32)

    def water_offset(self, arr: np.ndarray, channel_names: Sequence[str], water_mask: np.ndarray | None = None):
        """Per-band offset (BANDS11) that moves the scene's open-water median to the MARIDA water median.

        water_mask: optional (H,W) bool (e.g. SCL == 6 and not cloud). Without it a spectral mask is
        used: NDWI(B3,B8) > 0.1, B2 < 0.2, B11 < 0.05 (dark open water, no clouds/land). Returns None if
        fewer than 2000 water pixels.
        """
        b = select_bands(arr, channel_names)
        ok = np.isfinite(b).all(0)
        if water_mask is None:
            with np.errstate(invalid="ignore", divide="ignore"):
                ndwi = (b[2] - b[7]) / (b[2] + b[7])
            water_mask = (ndwi > 0.1) & (b[1] < 0.2) & (b[9] < 0.05)
        m = ok & np.asarray(water_mask, bool)
        if m.sum() < 2000:
            return None
        step = max(1, int(np.sqrt(m.sum() / 200000)))  # subsample big scenes
        med = np.median(b[:, ::step, ::step][:, m[::step, ::step]], axis=1)
        return (self.water_ref - med).astype(np.float32)

    def predict_proba(self, arr: np.ndarray, channel_names: Sequence[str], water_mask: np.ndarray | None = None) -> np.ndarray:
        arr = np.asarray(arr)
        if arr.ndim != 3:
            raise ValueError(f"expected (C,H,W), got {arr.shape}")
        if self.harmonize == "water_median":
            off = self.water_offset(arr, channel_names, water_mask)
            self.last_offset = off
            if off is not None:
                arr = select_bands(arr, channel_names) + off[:, None, None]
                channel_names = list(BANDS11)
        H, W = arr.shape[1:]
        out = np.zeros((H, W), np.float32)
        for (y0, y1, x0, x1), f in compute_features_blocked(arr, channel_names, self.level, block=self.block):
            f = f[self.fidx]
            F, h, w = f.shape
            X = f.reshape(F, -1).T
            ok = ~np.isnan(X).all(1)  # compute_features sets every feature to NaN where the input is NaN
            p = np.zeros(len(X), np.float32)
            if ok.any():
                p[ok] = self._predict_rows(np.ascontiguousarray(X[ok]))
            out[y0:y1, x0:x1] = p.reshape(h, w)
        bad = ~np.isfinite(select_bands(arr, channel_names)).all(0)
        out[bad] = 0.0
        if self.min_px > 0:
            out = self._postprocess(out)
        return out

    def _postprocess(self, prob: np.ndarray) -> np.ndarray:
        """Zero out MD components (prob >= threshold) smaller than min_px pixels (8-connectivity)."""
        from scipy import ndimage

        m = prob >= self.threshold
        lab, n = ndimage.label(m, structure=np.ones((3, 3), bool))
        if n:
            sizes = np.bincount(lab.ravel())
            small = sizes < self.min_px
            small[0] = False
            prob = prob.copy()
            prob[small[lab]] = np.minimum(prob[small[lab]], self.threshold * 0.999)
        return prob


def load_predictor(weights_dir: str | os.PathLike | None = None, weights: str | os.PathLike | None = None,
                   device: str | None = None, **kw) -> LGBMPredictor:
    """Registry loader. `weights` (dir or model.txt) is an alias of `weights_dir`; `device` is ignored (CPU)."""
    wd = weights_dir or weights or DEFAULT_WEIGHTS
    return LGBMPredictor(wd, **{k: v for k, v in kw.items() if k in ("block", "num_threads", "harmonize")})


try:  # register in the L1 registry if available
    from .registry import register as _register

    _register("lgbm", load_predictor)
except Exception:  # pragma: no cover
    pass
