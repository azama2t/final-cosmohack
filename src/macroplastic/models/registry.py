"""Predictor registry: one interface for every model used by inference.py and the service pipeline.

A predictor is any object with
    predict_proba(arr: np.ndarray (C,H,W) float32 reflectance 0..1, channel_names: list[str])
        -> np.ndarray (H,W) float32 probability of Marine Debris in [0,1]
and optional attributes `name` (str) and `threshold` (float, default 0.5).

    from macroplastic.models import get_predictor, register
    pred = get_predictor("lgbm")            # falls back to 'fdi_rule' if lgbm is unavailable
    prob = pred.predict_proba(arr, names)

Registering a model (other lanes):
    register("lgbm", loader)        # loader(**kw) -> predictor
Lazy models: for names in LAZY_MODULES the module is imported on first get_predictor(name).
The module either calls register(name, loader) at import time, or defines
`load_predictor(**kw) -> predictor`. Planned modules:
    'lgbm' -> macroplastic.models.lgbm_predict   (lane L3)
    'mdd'  -> macroplastic.models.mdd_predict    (lane L6, marinedebrisdetector)
If import/loading fails and fallback=True, a warning is logged and the built-in 'fdi_rule' is
returned; its attribute `fallback_from` holds the requested name.
"""
from __future__ import annotations

import importlib
from typing import Callable, Protocol

import numpy as np

from ..utils import get_logger


class Predictor(Protocol):
    name: str
    threshold: float

    def predict_proba(self, arr: np.ndarray, channel_names: list[str]) -> np.ndarray: ...


_REGISTRY: dict[str, Callable[..., Predictor]] = {}

LAZY_MODULES = {
    "lgbm": "macroplastic.models.lgbm_predict",
    "mdd": "macroplastic.models.mdd_predict",
    "unet": "macroplastic.models.unet.model",
}


class ModelError(RuntimeError):
    """Model cannot be loaded or run (inference.py maps it to exit code 2)."""


def register(name: str, loader: Callable[..., Predictor], overwrite: bool = True) -> None:
    """Register `loader(**kw) -> predictor` under `name`."""
    if not overwrite and name in _REGISTRY:
        raise KeyError(f"predictor {name!r} already registered")
    _REGISTRY[name] = loader


def available() -> list[str]:
    """Registered names plus lazy names (the latter may still fail to load)."""
    return sorted(set(_REGISTRY) | set(LAZY_MODULES))


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return (1.0 / (1.0 + np.exp(-np.clip(x, -50, 50)))).astype(np.float32)


class FDIRulePredictor:
    """Weight-free fallback: prob = sig((FDI - fdi0)/fdi_s) * sig((NDVI - ndvi0)/ndvi_s) * sig((b2max - B2)/b2_s).

    Floating debris: FDI > 0 and moderately positive NDVI; clear water has FDI ~ 0 and NDVI < 0;
    the B2 term suppresses bright clouds. Defaults are hand-set (not tuned on MARIDA val) and meant
    only to keep inference working without weights. NaN pixels -> 0.
    """

    def __init__(self, fdi0: float = 0.01, fdi_s: float = 0.004, ndvi0: float = 0.0, ndvi_s: float = 0.05,
                 b2max: float = 0.15, b2_s: float = 0.02, threshold: float = 0.5, **_ignored):
        self.name = "fdi_rule"
        self.threshold = float(threshold)
        self.fdi0, self.fdi_s, self.ndvi0, self.ndvi_s = fdi0, fdi_s, ndvi0, ndvi_s
        self.b2max, self.b2_s = b2max, b2_s
        self.fallback_from: str | None = None

    def predict_proba(self, arr: np.ndarray, channel_names: list[str]) -> np.ndarray:
        from ..indices import bands_of, fdi, ndvi

        b = bands_of(np.asarray(arr, dtype=np.float32), channel_names)
        with np.errstate(invalid="ignore", over="ignore"):
            p = (_sigmoid((fdi(b) - self.fdi0) / self.fdi_s)
                 * _sigmoid((ndvi(b) - self.ndvi0) / self.ndvi_s)
                 * _sigmoid((self.b2max - b["B2"]) / self.b2_s))
        return np.nan_to_num(p, nan=0.0).astype(np.float32)


register("fdi_rule", FDIRulePredictor)


def _load(name: str, **kw) -> Predictor:
    if name not in _REGISTRY and name in LAZY_MODULES:
        mod = importlib.import_module(LAZY_MODULES[name])
        if name not in _REGISTRY:
            if not hasattr(mod, "load_predictor"):
                raise ModelError(f"{LAZY_MODULES[name]} neither registers {name!r} nor defines load_predictor()")
            register(name, mod.load_predictor)
    if name not in _REGISTRY:
        raise KeyError(f"unknown model {name!r}; available: {available()}")
    pred = _REGISTRY[name](**kw)
    if not hasattr(pred, "predict_proba"):
        raise ModelError(f"loader of {name!r} returned an object without predict_proba")
    if not hasattr(pred, "name"):
        try:
            pred.name = name
        except Exception:
            pass
    return pred


def get_predictor(name: str = "fdi_rule", fallback: bool = True, **kw) -> Predictor:
    """Instantiate predictor `name` with loader kwargs (e.g. device='cpu', weights=path).

    Unknown names always raise KeyError. If a known model fails to import/load (missing module,
    weights, package) and fallback=True -> warning + FDIRulePredictor with fallback_from=name;
    with fallback=False -> ModelError.
    """
    if name not in _REGISTRY and name not in LAZY_MODULES:
        raise KeyError(f"unknown model {name!r}; available: {available()}")
    try:
        return _load(name, **kw)
    except Exception as e:  # ImportError, FileNotFoundError, ModelError, ...
        if not fallback or name == "fdi_rule":
            raise ModelError(f"cannot load model {name!r}: {type(e).__name__}: {e}") from e
        get_logger("macroplastic.models").warning(
            "model %r unavailable (%s: %s) -> falling back to 'fdi_rule'", name, type(e).__name__, e)
        p = FDIRulePredictor()
        p.fallback_from = name
        return p
