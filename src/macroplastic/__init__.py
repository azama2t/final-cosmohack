"""macroplastic: floating marine debris detection on Sentinel-2 (MARIDA-trained).

Modules (import lazily, each is lightweight):
    utils    - ROOT, seed_everything, available_cpus, cuda_usable, get_logger, load_yaml
    io       - GeoTIFF read/write as (C,H,W) float32 + rasterio profile, channel sets, win_path
    splits   - official MARIDA splits, scene_of(patch), scene overlap check
    metrics  - confusion matrix pooled over pixels (ignore=0), per-class P/R/F1/IoU, mIoU, binary MD
    indices  - spectral indices (FDI, FAI, NDVI, NDWI, NDMI, SI, BSI, NRD)
    models   - predictor registry: get_predictor(name).predict_proba(arr, channel_names) -> (H,W) prob

Class ids of MARIDA masks (_cl.tif) are in CLASS_NAMES; 0 = unlabeled (ignored).
"""
from __future__ import annotations

__version__ = "0.1.0"

IGNORE_INDEX = 0
MARINE_DEBRIS = 1

CLASS_NAMES = {
    0: "Unlabeled",
    1: "Marine Debris",
    2: "Dense Sargassum",
    3: "Sparse Sargassum",
    4: "Natural Organic Material",
    5: "Ship",
    6: "Clouds",
    7: "Marine Water",
    8: "Sediment-Laden Water",
    9: "Foam",
    10: "Turbid Water",
    11: "Shallow Water",
    12: "Waves",
    13: "Cloud Shadows",
    14: "Wakes",
    15: "Mixed Water",
}
N_CLASSES = 16  # including 0 (unlabeled)

# The MARIDA authors' baseline merges classes 12..15 into 7 (Marine Water).
MARIDA_MERGE_TO_WATER = {12: 7, 13: 7, 14: 7, 15: 7}

__all__ = ["CLASS_NAMES", "N_CLASSES", "IGNORE_INDEX", "MARINE_DEBRIS", "MARIDA_MERGE_TO_WATER", "__version__"]
