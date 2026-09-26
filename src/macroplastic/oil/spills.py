"""Маска «нефть» -> полигоны пятен с площадью (км²). Единица — ПЛОЩАДЬ, не объём и не масса; штуки не применяются."""
from __future__ import annotations

import hashlib

import numpy as np

CLASS_ID = "oil_spill"
CLASS_LABEL = "Нефтяное пятно"
UNIT_NOTE = "площадь, не объём/масса"
CSV_FIELDS = ["id", "class", "class_label", "scene_id", "date", "region", "source", "experimental", "area_km2",
              "n_px", "scene_frac", "prob_mean", "lon", "lat", "model", "unit_note"]


def component_mask(mask: np.ndarray, min_px: int = 1):
    """Связные компоненты (8-связность) маски; компоненты < min_px отбрасываются. -> (labels int32, n)."""
    from scipy import ndimage

    lab, n = ndimage.label(np.asarray(mask, bool), structure=np.ones((3, 3), bool))
    if n and min_px > 1:
        sizes = np.bincount(lab.ravel())
        small = sizes < min_px
        small[0] = False
        lab[small[lab]] = 0
        lab, n = ndimage.label(lab > 0, structure=np.ones((3, 3), bool))
    return lab.astype(np.int32), int(n)


def _px_area_km2(transform) -> float:
    return abs(float(transform.a) * float(transform.e) - float(transform.b) * float(transform.d)) / 1e6


def simplify_geom(g: dict, tol: float) -> dict:
    """Упростить контур (ступеньки пикселей) — Douglas-Peucker с сохранением топологии; площадь считается НЕ по контуру,
    а по числу пикселей, поэтому упрощение на неё не влияет."""
    if tol <= 0:
        return g
    from shapely.geometry import mapping, shape

    return mapping(shape(g).simplify(tol, preserve_topology=True))


def spill_features(mask: np.ndarray, transform, crs, scene: dict, prob: np.ndarray | None = None,
                   min_px: int = 1, water_px: int | None = None, simplify_px: float = 0.5) -> list[dict]:
    """GeoJSON Features (EPSG:4326) пятен одной сцены.

    mask — (H,W) bool нефть; transform — rasterio Affine растра (метрическая СК, напр. UTM);
    scene — {'scene_id','date','region','source','experimental','model'}; water_px — число наблюдаемых
    пикселей воды сцены (для доли, иначе все пиксели растра)."""
    from rasterio import features as rfeat
    from rasterio.warp import transform_geom

    lab, n = component_mask(mask, min_px)
    if n == 0:
        return []
    a_px = _px_area_km2(transform)
    denom = float(water_px if water_px else mask.size)
    sizes = np.bincount(lab.ravel(), minlength=n + 1)
    pm = None
    if prob is not None:
        pm = np.bincount(lab.ravel(), weights=np.asarray(prob, np.float64).ravel(), minlength=n + 1)
    from scipy import ndimage

    cents = ndimage.center_of_mass(np.ones(lab.shape, np.uint8), lab, range(1, n + 1))
    geoms: dict[int, list] = {}
    for g, v in rfeat.shapes(lab, mask=lab > 0, transform=transform, connectivity=8):
        geoms.setdefault(int(v), []).append(g)
    out = []
    for k in range(1, n + 1):
        parts = geoms.get(k, [])
        if not parts:
            continue
        if len(parts) == 1:
            g = parts[0]
        else:
            g = {"type": "MultiPolygon", "coordinates": [p["coordinates"] for p in parts]}
        g = simplify_geom(g, simplify_px * abs(float(transform.a)))
        g4326 = transform_geom(str(crs), "EPSG:4326", g, precision=6)
        cy, cx = cents[k - 1]
        x, y = transform * (cx + 0.5, cy + 0.5)
        lon, lat = transform_geom(str(crs), "EPSG:4326", {"type": "Point", "coordinates": [x, y]},
                                  precision=6)["coordinates"]
        fid = "oil." + hashlib.sha1(f"{scene.get('scene_id')}:{k}:{int(sizes[k])}".encode()).hexdigest()[:12]
        props = {
            "id": fid, "class": CLASS_ID, "class_label": CLASS_LABEL,
            "scene_id": scene.get("scene_id"), "date": scene.get("date"), "region": scene.get("region"),
            "source": scene.get("source"), "experimental": bool(scene.get("experimental", True)),
            "area_km2": round(float(sizes[k]) * a_px, 6), "n_px": int(sizes[k]),
            "scene_frac": round(float(sizes[k]) / denom, 8) if denom else None,
            "prob_mean": round(float(pm[k] / sizes[k]), 4) if pm is not None else None,
            "lon": round(float(lon), 6), "lat": round(float(lat), 6),
            "model": scene.get("model"), "unit_note": UNIT_NOTE,
        }
        out.append({"type": "Feature", "id": fid, "geometry": g4326, "properties": props})
    out.sort(key=lambda f: (-f["properties"]["area_km2"], f["id"]))
    return out


def feature_collection(features: list[dict], **extra) -> dict:
    return {"type": "FeatureCollection", "features": features, **extra}


def csv_rows(features: list[dict]) -> list[dict]:
    return [{k: f["properties"].get(k) for k in CSV_FIELDS} for f in features]
