"""Lane L7 grid layer: synthetic inputs with known answers."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import h3
import numpy as np
import pytest
from pyproj import Transformer
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from macroplastic.grid.h3index import cell_raster, h3_feature_collection, h3_stats  # noqa: E402
from macroplastic.grid.timeseries import mean_index, timeseries_row  # noqa: E402
from macroplastic.grid.vectorize import (clean_mask, fit_geojson_size, threshold_u8,  # noqa: E402
                                         to_feature_collection, vectorize_detections)
from macroplastic.grid.zones import rank_zones, zone_score  # noqa: E402

CRS = "EPSG:32616"
LON, LAT = -88.25, 16.05


def grid(size=400):
    x, y = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform(LON, LAT)
    return from_origin(round(x) - size * 5, round(y) + size * 5, 10.0, 10.0), (size, size)


def test_threshold_u8():
    assert threshold_u8(0.5) == 128  # 128/255 = 0.502 >= 0.5, 127/255 = 0.498 < 0.5
    assert threshold_u8(0.0) == 0
    assert threshold_u8(1.0) == 255


def test_clean_mask_removes_small_and_touching():
    prob = np.zeros((50, 50), np.uint8)
    water = np.ones((50, 50), np.uint8)
    prob[10, 10] = 250                      # 1 px -> removed
    prob[20, 20:22] = 250                   # 2 px -> kept
    prob[30:33, 30:33] = 200                # 3x3 -> kept
    water[40:45, 5:10] = 0                  # cloud
    prob[42:44, 10:12] = 250                # touches cloud (adjacent) -> removed
    prob[5, 40] = prob[6, 41] = 250         # diagonal pair = one 8-connected component of 2 px -> kept
    labels, n, raw = clean_mask(prob, water, 0.5, min_px=2, buffer_px=1)
    assert n == 3
    assert labels[10, 10] == 0 and labels[42, 10] == 0
    assert labels[20, 20] > 0 and labels[31, 31] > 0 and labels[5, 40] == labels[6, 41] > 0
    assert raw.sum() == 1 + 2 + 9 + 4 + 2
    # component one pixel away from the cloud (gap of 1 water px) survives with buffer 1
    prob2 = np.zeros((50, 50), np.uint8)
    prob2[42:44, 11:13] = 250
    _, n2, _ = clean_mask(prob2, water, 0.5)
    assert n2 == 1


def test_square_area_utm_and_wgs84_polygon():
    tr, shape = grid(100)
    labels = np.zeros(shape, np.int32)
    labels[40:45, 50:55] = 1  # 5x5 px = 2500 m2
    prob = np.where(labels > 0, 200, 0).astype(np.uint8)
    prob[42, 52] = 255
    recs = vectorize_detections(labels, 1, prob, tr, CRS, "r", "2025-01-01", "mdd")
    assert len(recs) == 1
    p = recs[0]["props"]
    assert p["area_m2"] == pytest.approx(2500.0)
    assert p["max_prob"] == pytest.approx(1.0)
    assert p["mean_prob"] == pytest.approx((24 * 200 + 255) / 25 / 255, abs=1e-3)
    assert recs[0]["pixel"] == (42, 52)
    fc = to_feature_collection(recs, CRS)
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    assert all(abs(x - LON) < 0.1 and abs(y - LAT) < 0.1 for x, y in ring)
    assert fc["features"][0]["properties"]["id"] == "r_2025-01-01_mdd_0"


def test_h3_share_and_observed_frac():
    tr, shape = grid(400)
    cellr, cells = cell_raster(tr, CRS, shape)
    assert cells and cellr.max() == len(cells)
    # every pixel centre maps to the H3 cell that contains it (spot check)
    inv = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    rng = np.random.default_rng(0)
    agree = 0
    for r, c in rng.integers(0, 400, (500, 2)):
        x, y = tr @ (c + 0.5, r + 0.5)
        lon, lat = inv.transform(x, y)
        agree += cellr[r, c] > 0 and cells[cellr[r, c] - 1] == h3.latlng_to_cell(lat, lon, 8)
    assert agree >= 495  # straight-edge vs geodesic boundary: only a few boundary pixels may differ

    counts = np.bincount(cellr.ravel())
    k = int(np.argmax(counts[1:])) + 1  # the most complete (interior) cell
    water = np.ones(shape, bool)
    flagged = np.zeros(shape, bool)
    idx = np.argwhere(cellr == k)[:7]
    flagged[idx[:, 0], idx[:, 1]] = True
    prob = np.where(flagged, 204, 0).astype(np.uint8)
    stats = {s["h3"]: s for s in h3_stats(cellr, cells, water, flagged, prob, [tuple(idx[0])])}
    s = stats[cells[k - 1]]
    exp_px = h3.cell_area(cells[k - 1], unit="m^2") / 100.0
    assert s["observed_water_px"] == counts[k]
    assert s["observed_frac"] == pytest.approx(min(1.0, counts[k] / exp_px), abs=1e-3)
    assert s["observed_frac"] > 0.95
    assert s["flagged_water_px"] == 7
    assert s["share_permille"] == pytest.approx(1000 * 7 / counts[k], rel=1e-3)
    assert s["n_detections"] == 1 and s["mean_prob"] == pytest.approx(0.8)
    assert s["debris_area_m2"] == 700.0

    # cloud over 60 % of the cell -> observed_frac < 0.5 -> share None, flagged only among observed
    water2 = water.copy()
    cell_px = np.argwhere(cellr == k)
    cut = int(len(cell_px) * 0.6)
    water2[cell_px[:cut, 0], cell_px[:cut, 1]] = False
    s2 = {x["h3"]: x for x in h3_stats(cellr, cells, water2, flagged, prob)}[cells[k - 1]]
    assert s2["observed_frac"] < 0.5 and s2["share_permille"] is None
    fc = h3_feature_collection(list(stats.values()), "2025-01-01", "mdd", 0.5)
    props = fc["features"][0]["properties"]
    assert props["res"] == 8 and props["threshold"] == 0.5
    json.dumps(fc)


def test_edge_cells_partial_have_null():
    tr, shape = grid(300)
    cellr, cells = cell_raster(tr, CRS, shape)
    stats = h3_stats(cellr, cells, np.ones(shape, bool), np.zeros(shape, bool), np.zeros(shape, np.uint8))
    assert any(s["share_permille"] is None for s in stats)       # cells cut by the raster edge
    assert any(s["share_permille"] == 0.0 for s in stats)        # fully observed, nothing flagged -> 0, not null
    assert all(s["total_px"] > 0 for s in stats)


def _stat(h, flagged, mp, share=1.0, ndet=1):
    return {"h3": h, "flagged_water_px": flagged, "mean_prob": mp, "share_permille": share, "n_detections": ndet,
            "observed_frac": 0.9, "lon": 0.0, "lat": 0.0}


def test_zone_ranking():
    stats = [_stat("a", 10, 0.6), _stat("b", 8, 0.9), _stat("c", 6, 0.7), _stat("d", 50, 0.9, share=None),
             _stat("e", 0, None, share=0.0)] + [_stat(f"x{i}", 1, 0.5) for i in range(12)]
    z = rank_zones(stats, repeat={}, n_dates=1)
    assert len(z) == 10
    assert [q["h3"] for q in z[:3]] == ["b", "a", "c"]  # 7.2 > 6.0 > 4.2
    assert "d" not in {q["h3"] for q in z} and "e" not in {q["h3"] for q in z}
    z2 = rank_zones(stats, repeat={"c": 3}, n_dates=3)  # persistence: 4.2 * 2 = 8.4 -> first
    assert z2[0]["h3"] == "c" and z2[0]["repeat_dates"] == 3
    assert "повторяется на 3 из 3 дат" in z2[0]["reason"]
    assert [q["rank"] for q in z2] == list(range(1, 11))
    assert zone_score(10, 0.5, 1) == 5.0
    assert z2[0]["area_m2"] == 600.0


def test_timeseries_and_null_index():
    assert mean_index([{"share_permille": None}]) is None
    assert mean_index([{"share_permille": 1.0}, {"share_permille": 3.0}, {"share_permille": None}]) == 2.0
    row = timeseries_row("2025-01-01", "mdd", [{"props": {"area_m2": 200.0}}], [{"share_permille": 0.5}], 0.1)
    assert row["total_debris_area_m2"] == 200.0 and row["n_detections"] == 1 and row["mean_index"] == 0.5


def test_geojson_size_limit_drops_smallest():
    tr, shape = grid(200)
    labels = np.zeros(shape, np.int32)
    k = 0
    for r in range(5, 195, 6):
        for c in range(5, 195, 6):
            k += 1
            labels[r:r + 2 + (k % 3), c:c + 2] = k
    prob = np.where(labels > 0, 220, 0).astype(np.uint8)
    recs = vectorize_detections(labels, k, prob, tr, CRS, "r", "d", "m")
    text, info = fit_geojson_size(recs, CRS, max_bytes=20000)
    assert len(text.encode()) <= 20000 and info["dropped"] > 0
    kept = json.loads(text)["features"]
    assert min(f["properties"]["area_m2"] for f in kept) >= 400.0 - 1e-6


def test_h3_speed_2500():
    tr, shape = grid(2500)
    t = time.time()
    cellr, cells = cell_raster(tr, CRS, shape)
    water = np.ones(shape, bool)
    stats = h3_stats(cellr, cells, water, np.zeros(shape, bool), np.zeros(shape, np.uint8))
    dt = time.time() - t
    assert 800 < len(stats) < 1300  # 25x25 km / ~0.74 km2 per cell (+ partial edge cells)
    assert dt < 15, dt


def _build_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("bsd", ROOT / "scripts" / "build_service_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_prob_palette_fixed_colors():
    pal = _build_module().prob_palette(np.array([0.0, 0.049, 0.05, 0.275, 0.4999, 0.5, 1.0], np.float32), 0.5)
    assert pal[0, 3] == 0 and pal[1, 3] == 0
    assert tuple(pal[2]) == (0x2b, 0x6c, 0xb0, 40)
    assert tuple(pal[3, :3]) == (0xb7, 0x94, 0xf4)
    assert abs(int(pal[4, 3]) - 170) <= 1 and abs(int(pal[4, 0]) - 0xf6) <= 2
    assert tuple(pal[5]) == tuple(pal[6]) == (0xff, 0x6b, 0x4a, 230)


def test_edge_zone(tmp_path):
    z = _build_module().edge_zone(tmp_path, (20, 30), 4)
    assert z[:4].all() and z[:, -4:].all() and not z[4:16, 4:26].any()
