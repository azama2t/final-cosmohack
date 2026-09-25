"""Probability raster -> cleaned debris mask -> polygons (area in UTM) -> WGS84 GeoJSON features.

Post-processing (SPEC §5.4): flagged = prob >= threshold AND observed water; 8-connected components;
drop components smaller than `min_px` pixels and components touching non-water (clouds, shadows,
land, nodata) within a `buffer_px` pixel buffer.
"""
from __future__ import annotations

import json

import numpy as np
import shapely
from pyproj import CRS, Transformer
from rasterio import features as rio_features
from scipy import ndimage
from shapely.geometry import mapping, shape

from . import PIXEL_AREA_M2

EIGHT = np.ones((3, 3), dtype=bool)
MAX_GEOJSON_BYTES = 2 * 1024 * 1024
MAX_FEATURES = 15000


def threshold_u8(threshold: float) -> int:
    """Smallest uint8 value v with v/255 >= threshold."""
    return int(np.ceil(float(threshold) * 255.0 - 1e-6))


def clean_mask(prob_u8: np.ndarray, water: np.ndarray, threshold: float, min_px: int = 2,
               buffer_px: int = 1) -> tuple[np.ndarray, int, np.ndarray]:
    """Return (labels int32 of kept components 1..n, n, raw_flagged bool).

    raw_flagged = prob >= threshold among observed water (before component filtering).
    """
    water = np.asarray(water).astype(bool)
    raw = (np.asarray(prob_u8) >= threshold_u8(threshold)) & water
    lab, n = ndimage.label(raw, structure=EIGHT)
    if n == 0:
        return lab.astype(np.int32), 0, raw
    sizes = np.bincount(lab.ravel(), minlength=n + 1)
    keep = sizes >= int(min_px)
    if buffer_px > 0:
        invalid = ~water
        if invalid.any():
            near = ndimage.binary_dilation(invalid, structure=EIGHT, iterations=int(buffer_px))
            touching = np.unique(lab[near & (lab > 0)])
            keep[touching] = False
    keep[0] = False
    remap = np.zeros(n + 1, dtype=np.int32)
    remap[keep] = np.arange(1, int(keep.sum()) + 1, dtype=np.int32)
    return remap[lab], int(keep.sum()), raw


def _to_wgs84(crs) -> Transformer:
    return Transformer.from_crs(CRS.from_user_input(crs), CRS.from_epsg(4326), always_xy=True)


def _reproject(geoms, tr: Transformer, ndigits: int = 6):
    def fn(c):
        x, y = tr.transform(c[:, 0], c[:, 1])
        return np.round(np.column_stack([x, y]), ndigits)
    return shapely.transform(geoms, fn)


def component_stats(labels: np.ndarray, prob_u8: np.ndarray, n: int):
    """Per-component pixel count, mean/max prob (0..1), and (row, col) of the max-prob pixel."""
    if n == 0:
        return np.zeros(0, int), np.zeros(0), np.zeros(0), []
    idx = np.arange(1, n + 1)
    p = prob_u8.astype(np.float32) / 255.0
    count = np.bincount(labels.ravel(), minlength=n + 1)[1:]
    mean = np.asarray(ndimage.mean(p, labels, idx))
    mx = np.asarray(ndimage.maximum(p, labels, idx))
    pos = ndimage.maximum_position(p, labels, idx)
    return count, mean, mx, pos


def vectorize_detections(labels: np.ndarray, n: int, prob_u8: np.ndarray, transform, crs,
                         region: str, date: str, model: str) -> list[dict]:
    """Polygons of kept components. Returns list of records:
    {"geom_utm": shapely geometry (scene CRS), "props": {...}, "pixel": (row, col) of max prob}.
    """
    if n == 0:
        return []
    count, mean, mx, pos = component_stats(labels, prob_u8, n)
    parts: dict[int, list] = {}
    for geom, val in rio_features.shapes(labels, mask=labels > 0, connectivity=8, transform=transform):
        parts.setdefault(int(val), []).append(shape(geom))
    px_area = abs(transform.a * transform.e - transform.b * transform.d)
    projected = CRS.from_user_input(crs).is_projected
    out = []
    for k in range(1, n + 1):
        polys = parts.get(k, [])
        if not polys:
            continue
        g = polys[0] if len(polys) == 1 else shapely.union_all(polys)
        area = float(g.area) if projected else float(count[k - 1]) * PIXEL_AREA_M2
        out.append({"geom_utm": g, "pixel": pos[k - 1], "props": {
            "id": f"{region}_{date}_{model}_{len(out)}", "region": region, "date": date,
            "area_m2": round(area, 1), "mean_prob": round(float(mean[k - 1]), 4),
            "max_prob": round(float(mx[k - 1]), 4), "model": model, "n_px": int(count[k - 1]),
            "px_area_m2": round(px_area, 3)}})
    return out


def to_feature_collection(records: list[dict], crs, tolerance_m: float = 0.0) -> dict:
    if not records:
        return {"type": "FeatureCollection", "features": []}
    tr = _to_wgs84(crs)
    geoms = np.array([r["geom_utm"] for r in records], dtype=object)
    if tolerance_m > 0:
        geoms = shapely.simplify(geoms, tolerance_m, preserve_topology=True)
    wgs = _reproject(geoms, tr)
    cents = _reproject(shapely.centroid(np.array([r["geom_utm"] for r in records], dtype=object)), tr)
    feats = []
    for r, g, c in zip(records, wgs, cents):
        props = dict(r["props"])
        props["lon"], props["lat"] = round(float(c.x), 6), round(float(c.y), 6)
        feats.append({"type": "Feature", "geometry": mapping(g), "properties": props})
    return {"type": "FeatureCollection", "features": feats}


def dumps_compact(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def fit_geojson_size(records: list[dict], crs, max_bytes: int = MAX_GEOJSON_BYTES,
                     max_features: int = MAX_FEATURES) -> tuple[str, dict]:
    """Serialize detections as GeoJSON <= max_bytes: keep at most max_features largest polygons, raise the
    simplification tolerance (0, 5, 10, 20, 40 m), then keep only the largest polygons. Returns (text, info);
    info["dropped"] = number of detections not written to the file."""
    info = {"tolerance_m": 0.0, "dropped": 0}
    n_total = len(records)
    if len(records) > max_features:  # pathological noise: keep the largest polygons only (fast path)
        records = sorted(records, key=lambda r: -r["props"]["area_m2"])[:max_features]
    n_in = len(records)
    for tol in (0.0, 5.0, 10.0, 20.0, 40.0):
        fc = to_feature_collection(records, crs, tol)
        text = dumps_compact(fc)
        info["tolerance_m"] = tol
        if len(text.encode("utf-8")) <= max_bytes:
            info["dropped"] = n_total - n_in
            return text, info
    feats = sorted(fc["features"], key=lambda f: -f["properties"]["area_m2"])
    lo, hi = 0, len(feats)
    while lo < hi:  # largest k such that k biggest features fit
        mid = (lo + hi + 1) // 2
        if len(dumps_compact({"type": "FeatureCollection", "features": feats[:mid]}).encode("utf-8")) <= max_bytes:
            lo = mid
        else:
            hi = mid - 1
    info["dropped"] = n_total - lo
    return dumps_compact({"type": "FeatureCollection", "features": feats[:lo]}), info
