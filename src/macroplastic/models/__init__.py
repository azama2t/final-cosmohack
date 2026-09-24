"""Model registry. See registry.py for the predictor interface.

    from macroplastic.models import get_predictor
    prob = get_predictor("lgbm").predict_proba(arr, channel_names)   # (H,W) float32
"""
from .registry import (FDIRulePredictor, ModelError, Predictor, available, get_predictor,  # noqa: F401
                       register, LAZY_MODULES)

__all__ = ["get_predictor", "register", "available", "FDIRulePredictor", "ModelError", "Predictor", "LAZY_MODULES"]
