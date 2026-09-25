"""Synthetic scenes in the "live" format (data/live/<region>/<date>/...) on a real UTM grid.

Used to develop/test scripts/build_service_data.py before real scenes exist.

Usage: python scripts/make_live_fixture.py [--out out/live_fixture] [--size 1000] [--no-bands] [--seed 0]

Per scene: scene.json, bands.tif (float32 x12), scl.tif, water_mask.tif, rgb.png + rgb.json,
prob_mdd.tif/.json, prob_lgbm.tif/.json. Content: land corner, a cloud with shadow, a nodata strip,
debris-like blobs (one persistent across dates), 1-px noise (must be removed), a blob touching the cloud
(must be removed).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

REGIONS = [
    {"id": "honduras", "name": "Гондурасский залив", "country": "Гондурас", "tile": "16PCC", "epsg": 32616,
     "center": (-88.25, 16.05), "dates": ["2025-09-14", "2025-10-19"]},
    {"id": "durban", "name": "Дурбан", "country": "ЮАР", "tile": "36JUN", "epsg": 32736,
     "center": (31.08, -29.88), "dates": ["2025-11-07", "2026-04-21"]},
]
BANDS = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"]
MODELS = {"mdd": 0.5, "lgbm": 0.6}


def write_tif(path, arr, transform, epsg, dtype, nodata=None):
    arr = arr if arr.ndim == 3 else arr[None]
    prof = {"driver": "GTiff", "height": arr.shape[1], "width": arr.shape[2], "count": arr.shape[0], "dtype": dtype,
            "crs": f"EPSG:{epsg}", "transform": transform, "compress": "deflate", "tiled": True,
            "blockxsize": 256, "blockysize": 256}
    if nodata is not None:
        prof["nodata"] = nodata
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr.astype(dtype))


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def gauss(yy, xx, cy, cx, sy, sx, amp):
    return amp * np.exp(-(((yy - cy) / sy) ** 2 + ((xx - cx) / sx) ** 2))


def make_scene(out: Path, reg: dict, date: str, di: int, size: int, rng, bands: bool):
    d = out / reg["id"] / date
    d.mkdir(parents=True, exist_ok=True)
    tr = Transformer.from_crs("EPSG:4326", f"EPSG:{reg['epsg']}", always_xy=True)
    cx, cy = tr.transform(*reg["center"])
    x0, y0 = round((cx - size * 5) / 10) * 10, round((cy + size * 5) / 10) * 10
    transform = from_origin(x0, y0, 10.0, 10.0)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    s = size / 1000.0
    # SCL: 6 water, 5 land, 9 cloud, 3 shadow, 0 nodata
    scl = np.full((size, size), 6, np.uint8)
    scl[(yy < 150 * s) & (xx < 250 * s - yy * 0.5)] = 5
    cloud = (yy - (300 + 80 * di) * s) ** 2 + (xx - 700 * s) ** 2 < (60 * s) ** 2
    shadow = ((yy - (330 + 80 * di) * s) ** 2 + (xx - 760 * s) ** 2 < (60 * s) ** 2) & ~cloud
    scl[shadow] = 3
    scl[cloud] = 9
    scl[:, size - int(20 * s) - 1:] = 0
    water = (scl == 6).astype(np.uint8)

    prob = rng.uniform(0, 0.08, (size, size)).astype(np.float32)
    # persistent filament (same place every date) + date-specific blobs
    prob = np.maximum(prob, gauss(yy, xx, 500 * s, 400 * s, 2.5, 18, 0.92))
    for _ in range(4 + di):
        prob = np.maximum(prob, gauss(yy, xx, rng.uniform(250, 900) * s, rng.uniform(100, 600) * s,
                                      rng.uniform(1.5, 4), rng.uniform(3, 12), rng.uniform(0.6, 0.97)))
    # blob touching the cloud edge -> removed by post-processing
    prob = np.maximum(prob, gauss(yy, xx, (300 + 80 * di) * s, 700 * s - 60 * s - 3, 3, 5, 0.9))
    # isolated 1-px spikes -> removed
    for _ in range(12):
        r, c = rng.integers(200, size - 50), rng.integers(50, size - 200)
        prob[r, c] = 0.95
    prob[scl == 0] = 0

    if bands:
        base = np.array([0.03, 0.035, 0.03, 0.02, 0.02, 0.015, 0.015, 0.012, 0.01, 0.005, 0.004, 0.003], np.float32)
        arr = np.broadcast_to(base[:, None, None], (12, size, size)).copy()
        arr[:, scl == 5] = 0.2
        arr[:, (scl == 9)] = 0.6
        arr[:, (scl == 3)] *= 0.5
        arr[7:9] += 0.05 * prob[None]
        arr[:, scl == 0] = 0
        write_tif(d / "bands.tif", arr, transform, reg["epsg"], "float32")
    rgb = np.zeros((size, size, 3), np.uint8)
    rgb[...] = (15, 45, 70)
    rgb[scl == 5] = (110, 100, 70)
    rgb[scl == 9] = (235, 235, 235)
    rgb[scl == 3] = (8, 20, 35)
    rgb[scl == 0] = 0
    rgb[prob > 0.5] = (140, 140, 120)
    Image.fromarray(rgb).save(d / "rgb.png")
    bounds = transform_bounds(f"EPSG:{reg['epsg']}", "EPSG:4326", x0, y0 - size * 10, x0 + size * 10, y0, densify_pts=21)
    write_json(d / "rgb.json", {"bounds": [round(v, 6) for v in bounds], "width": size, "height": size,
                                "crs": f"EPSG:{reg['epsg']}"})
    write_tif(d / "scl.tif", scl, transform, reg["epsg"], "uint8")
    write_tif(d / "water_mask.tif", water, transform, reg["epsg"], "uint8")
    for m, thr in MODELS.items():
        p = prob if m == "mdd" else np.clip(prob * rng.uniform(0.9, 1.1, prob.shape), 0, 1)
        write_tif(d / f"prob_{m}.tif", np.round(p * 255), transform, reg["epsg"], "uint8")
        write_json(d / f"prob_{m}.json", {"model": m, "threshold": thr, "note": "synthetic fixture"})
    scene_id = f"S2B_{reg['tile']}_{date.replace('-', '')}_0_L2A"
    write_json(d / "scene.json", {"region": reg["id"], "region_name": reg["name"], "country": reg["country"],
                                  "date": date, "scene_id": scene_id, "tile": reg["tile"], "cloud_cover": 7.5,
                                  "crs": f"EPSG:{reg['epsg']}", "transform": list(transform)[:6],
                                  "bounds_wgs84": [round(v, 6) for v in bounds], "width": size, "height": size,
                                  "bands": BANDS, "source": "synthetic fixture (make_live_fixture.py)"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/live_fixture")
    ap.add_argument("--size", type=int, default=1000)
    ap.add_argument("--no-bands", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    for reg in REGIONS:
        for di, date in enumerate(reg["dates"]):
            make_scene(Path(a.out), reg, date, di, a.size, rng, not a.no_bands)
    print(f"live fixture written to {a.out} (size {a.size})")


if __name__ == "__main__":
    main()
