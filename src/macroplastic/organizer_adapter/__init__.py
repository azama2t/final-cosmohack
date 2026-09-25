"""Organizer dataset adapter: YAML config -> internal (C,H,W) float32 reflectance + uint8 mask.

    from macroplastic.organizer_adapter import load_config, list_samples, load_sample, iter_samples, convert
    CLI: python -m macroplastic.organizer_adapter --config configs/adapter_marida.yaml --out data/organizer
"""
from .adapter import (  # noqa: F401
    CANON,
    Sample,
    canon_band,
    convert,
    iter_samples,
    list_samples,
    load_config,
    load_image,
    load_mask,
    load_sample,
    verify_against_marida,
)
