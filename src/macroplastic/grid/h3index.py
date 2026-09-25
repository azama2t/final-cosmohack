"""H3 res-8 index: "доля наблюдаемой воды с признаками мусора" (per-mille) per cell.

Each pixel is assigned to the H3 cell containing its centre. Implemented by rasterizing the H3 cell
boundaries (projected to the scene CRS) onto the scene grid (pixel-centre rule), then np.bincount —
no per-pixel Python calls, ~1 s on 2500x2500.

    observed_water_px = pixels of observed water (water_mask == 1) in the cell
    flagged_water_px  = flagged pixels among observed (see build: cleaned components)
    observed_frac     = observed_water_px / (cell_area_m2 / pixel_area_m2), clipped to [0, 1]
    share_permille    = 1000 * flagged / observed, or None if observed_frac < 0.5
"""
from __future__ import annotations

import h3
import numpy as np
from pyproj import CRS, Transformer
from rasterio import features as rio_features
from rasterio.warp import transform_bounds
from shapely.geometry import Polygon

from . import H3_RES, MIN_OBSERVED_FRAC, PIXEL_AREA_M2


def raster_bounds(transform, shape) -> tuple[float, float, float, float]:
    h, w = shape
    xs = [transform.c, transform.c + transform.a * w]
    ys = [transform.f, transform.f + transform.e * h]
    return min(xs), min(ys), max(xs), max(ys)


def cells_covering(transform, crs, shape, res: int = H3_RES, pad_deg: float = 0.012) -> list[str]:
    """H3 cells whose centre lies within the raster's WGS84 envelope padded by ~one cell."""
    w, s, e, n = transform_bounds(CRS.from_user_input(crs), "EPSG:4326", *raster_bounds(transform, shape), densify_pts=21)
    lat_pad = pad_deg
    lon_pad = pad_deg / max(np.cos(np.radians((s + n) / 2)), 0.2)
    w, s, e, n = w - lon_pad, s - lat_pad, e + lon_pad, n + lat_pad
    poly = h3.LatLngPoly([(s, w), (s, e), (n, e), (n, w)])
    return sorted(h3.polygon_to_cells(poly, res))


def cell_raster(transform, crs, shape, res: int = H3_RES) -> tuple[np.ndarray, list[str]]:
    """int32 raster: 0 = no cell, k = cells[k-1]. Only cells that own >= 1 pixel centre are kept."""
    cells = cells_covering(transform, crs, shape, res)
    if not cells:
        return np.zeros(shape, np.int32), []
    tr = Transformer.from_crs("EPSG:4326", CRS.from_user_input(crs), always_xy=True)
    rings = [h3.cell_to_boundary(c) for c in cells]  # [(lat, lng), ...]
    sizes = [len(r) for r in rings]
    lat = np.array([p[0] for r in rings for p in r])
    lng = np.array([p[1] for r in rings for p in r])
    x, y = tr.transform(lng, lat)
    shapes, o = [], 0
    for k, m in enumerate(sizes):
        shapes.append((Polygon(np.column_stack([x[o:o + m], y[o:o + m]])), k + 1))
        o += m
    ras = rio_features.rasterize(shapes, out_shape=shape, transform=transform, fill=0, dtype="int32")
    present = np.bincount(ras.ravel(), minlength=len(cells) + 1)[1:] > 0
    remap = np.zeros(len(cells) + 1, np.int32)
    remap[1:][present] = np.arange(1, int(present.sum()) + 1, dtype=np.int32)
    kept = [c for c, p in zip(cells, present) if p]
    return remap[ras], kept


def h3_stats(cellr: np.ndarray, cells: list[str], water: np.ndarray, flagged: np.ndarray,
             prob_u8: np.ndarray, det_pixels: list | None = None, pixel_area_m2: float = PIXEL_AREA_M2,
             min_observed_frac: float = MIN_OBSERVED_FRAC) -> list[dict]:
    """Per-cell statistics (one dict per cell that has at least one pixel centre in the raster)."""
    n = len(cells)
    if n == 0:
        return []
    water = np.asarray(water).astype(bool)
    flagged = np.asarray(flagged).astype(bool) & water
    total = np.bincount(cellr.ravel(), minlength=n + 1)
    observed = np.bincount(cellr[water], minlength=n + 1)
    flag = np.bincount(cellr[flagged], minlength=n + 1)
    psum = np.bincount(cellr[flagged], weights=prob_u8[flagged].astype(np.float64) / 255.0, minlength=n + 1)
    ndet = np.zeros(n + 1, np.int64)
    for r, c in det_pixels or []:
        ndet[cellr[int(r), int(c)]] += 1
    out = []
    for k, c in enumerate(cells, start=1):
        area = h3.cell_area(c, unit="m^2")
        frac = min(1.0, observed[k] / (area / pixel_area_m2))
        share = 1000.0 * flag[k] / observed[k] if (frac >= min_observed_frac and observed[k] > 0) else None
        lat, lng = h3.cell_to_latlng(c)
        ring = [[round(p[1], 6), round(p[0], 6)] for p in h3.cell_to_boundary(c)]
        ring.append(ring[0])
        out.append({"h3": c, "total_px": int(total[k]), "observed_water_px": int(observed[k]),
                    "flagged_water_px": int(flag[k]), "observed_frac": round(float(frac), 4),
                    "share_permille": None if share is None else round(float(share), 4),
                    "n_detections": int(ndet[k]), "cell_area_m2": round(float(area), 1),
                    "debris_area_m2": round(float(flag[k]) * pixel_area_m2, 1),
                    "mean_prob": round(float(psum[k] / flag[k]), 4) if flag[k] else None,
                    "lon": round(lng, 6), "lat": round(lat, 6), "ring": ring})
    return out


def h3_feature_collection(stats: list[dict], date: str, model: str, threshold: float, res: int = H3_RES) -> dict:
    feats = []
    for s in stats:
        props = {"h3": s["h3"], "res": res, "date": date, "flagged_water_px": s["flagged_water_px"],
                 "observed_water_px": s["observed_water_px"], "observed_frac": s["observed_frac"],
                 "share_permille": s["share_permille"], "n_detections": s["n_detections"],
                 "threshold": float(threshold), "model": model, "mean_prob": s["mean_prob"]}
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [s["ring"]]},
                      "properties": props})
    return {"type": "FeatureCollection", "features": feats}


def cell_polygon_wgs84(cell: str) -> Polygon:
    return Polygon([(p[1], p[0]) for p in h3.cell_to_boundary(cell)])


__all__ = ["cell_raster", "cells_covering", "h3_stats", "h3_feature_collection", "cell_polygon_wgs84"]
