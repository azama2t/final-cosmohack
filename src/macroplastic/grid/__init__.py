"""Grid layer (lane L7): probability raster -> debris polygons, H3 res-8 index, survey-priority zones, timeseries.

Index name (UI): "доля наблюдаемой воды с признаками мусора" (per-mille). It is NOT plastic mass or concentration.

    vectorize  - clean_mask(), vectorize_detections(), fit_geojson_size()
    h3index    - h3_stats() -> per-cell observed/flagged pixels, observed_frac, share_permille
    zones      - rank_zones() -> top <= 10 cells ("приоритет обследования")
    timeseries - timeseries_row(), mean_index()
"""
from __future__ import annotations

H3_RES = 8
PIXEL_AREA_M2 = 100.0  # 10 m Sentinel-2 grid
MIN_OBSERVED_FRAC = 0.5

__all__ = ["H3_RES", "PIXEL_AREA_M2", "MIN_OBSERVED_FRAC"]
