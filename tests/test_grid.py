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


def test_confirmed_components_radius_2px():
    """L15: an object is confirmed when the other model has a pixel >= its threshold within 2 px (disk)."""
    from macroplastic.grid.confirm import confirmed_components, disk, label_of_records

    assert disk(2).sum() == 13  # Euclidean disk r=2: corners (2,1),(2,2) excluded
    prob = np.zeros((60, 60), np.uint8)
    water = np.ones((60, 60), np.uint8)
    prob[10, 10:13] = 250                   # object A
    prob[40, 40:43] = 250                   # object B
    prob[20, 50:52] = 250                   # object C
    labels, n, _ = clean_mask(prob, water, 0.5)
    assert n == 3
    partner = np.zeros((60, 60), bool)
    partner[12, 12] = True                  # 2 px below A's end -> within radius 2: A confirmed
    partner[43, 45] = True                  # dy=3 from B -> not within 2 px: B not confirmed
    partner[22, 53] = True                  # dy=2, dx=2 from C's end: distance 2.83 > 2 -> not confirmed
    conf = confirmed_components(labels, n, partner, 2)
    recs = [{"pixel": (10, 11)}, {"pixel": (40, 41)}, {"pixel": (20, 51)}]
    labs = label_of_records(labels, recs)
    assert [bool(conf[k - 1]) for k in labs] == [True, False, False]
    partner[22, 52] = True                  # dy=2, dx=1: distance 2.24 > 2 -> still not
    assert not confirmed_components(labels, n, partner, 2)[labels[20, 51] - 1]
    partner[21, 52] = True                  # dy=1, dx=1 -> confirmed
    assert confirmed_components(labels, n, partner, 2)[labels[20, 51] - 1]
    assert not confirmed_components(labels, n, np.zeros_like(partner), 2).any()


# ---------------------------------------------------------------- L28 cloud guards (synthetic)
from macroplastic.grid.cloudmask import (drop_components, near_cloud_components,  # noqa: E402
                                         shadow_components, spectral_cloud)


def _labels(shape, boxes):
    lab = np.zeros(shape, np.int32)
    for k, (r0, r1, c0, c1) in enumerate(boxes, 1):
        lab[r0:r1, c0:c1] = k
    return lab


def test_cloud_buffer_drops_only_components_within_5px():
    cloud = np.zeros((60, 60), bool)
    cloud[:, :10] = True  # cloud occupies columns 0..9; distance from column c to cloud = c - 9
    lab = _labels(cloud.shape, [(20, 23, 14, 17),   # nearest column 14 -> 5 px: dropped (<= 5)
                                (30, 33, 15, 18),   # 6 px: kept
                                (40, 43, 40, 43)])  # far: kept
    near = near_cloud_components(lab, 3, cloud, 5)
    assert near.tolist() == [True, False, False]
    assert near_cloud_components(lab, 3, cloud, 0).tolist() == [False, False, False]
    assert near_cloud_components(lab, 3, np.zeros_like(cloud), 5).tolist() == [False, False, False]
    lab2, n2 = drop_components(lab, 3, near)
    assert n2 == 2 and set(np.unique(lab2)) == {0, 1, 2} and lab2[20, 14] == 0 and lab2[30, 15] == 1


def test_spectral_cloud_bright_swir_blob_only():
    shape = (80, 80)
    water = np.ones(shape, bool)
    b2, b11 = np.full(shape, 0.02, np.float32), np.full(shape, 0.002, np.float32)
    b2[5:25, 5:25], b11[5:25, 5:25] = 0.09, 0.08    # 400 px cloud missed by SCL -> masked
    b2[50:53, 50:53], b11[50:53, 50:53] = 0.09, 0.08  # 9 px bright object (boat / debris) -> not a cloud
    b2[60:75, 5:20] = 0.09                           # bright in visible only (turbid/glint), dark SWIR -> not cloud
    c = spectral_cloud(b2, b11, water)
    assert c[15, 15] and not c[51, 51] and not c[67, 12]
    assert 300 <= c.sum() <= 400


def test_shadow_component_dark_ring_no_nir_excess():
    shape = (120, 120)
    water = np.ones(shape, bool)
    b2 = np.full(shape, 0.03, np.float32)
    b3, b4 = np.full(shape, 0.02, np.float32), np.full(shape, 0.01, np.float32)
    b8 = np.full(shape, 0.004, np.float32)
    # shadow region: all bands darker; the "detection" inside it is even darker in NIR
    for b, k in ((b2, 0.5), (b3, 0.5), (b4, 0.5), (b8, 0.5)):
        b[10:50, 10:50] *= k
    b8[25:30, 25:30] = 0.001
    # real floating patch on normal water: NIR excess
    b8[80:84, 80:84] = 0.02
    lab = _labels(shape, [(25, 30, 25, 30), (80, 84, 80, 84)])
    sh = shadow_components(lab, 2, b2, b3, b4, b8, water)
    assert sh.tolist() == [True, False]
    # a dark patch on bright (non-shadowed) water is not called shadow (ring not darkened)
    b2[10:50, 10:50], b3[10:50, 10:50], b4[10:50, 10:50] = 0.03, 0.02, 0.01
    assert shadow_components(lab, 2, b2, b3, b4, b8, water).tolist() == [False, False]


# ---------------------------------------------------------------- L37: linear artefacts and ships
from scipy import ndimage as _ndi  # noqa: E402

from macroplastic.grid.artifacts import classify, component_shape  # noqa: E402


def _bands(shape, vis=0.03, b8=0.004):
    return {k: np.full(shape, vis, np.float32) for k in ("B2", "B3", "B4")} | {"B8": np.full(shape, b8, np.float32)}


def _label(mask):
    lab, n = _ndi.label(mask, structure=np.ones((3, 3), bool))
    return lab.astype(np.int32), n


def test_artifact_straight_long_line_is_wake_and_track_line_is_seam():
    m = np.zeros((300, 300), bool)
    for i in range(150):  # diagonal NE-SW (azimuth +45 deg), 3 px thick, ~2.1 km
        m[50 + i, 200 - i:203 - i] = True
    m[40:260, 20:22] = True  # vertical line 2.2 km, 2 px: along the S2 track
    lab, n = _label(m)
    art, f = classify(lab, n, _bands(m.shape), np.ones(m.shape, bool))
    k_diag, k_vert = lab[50, 200] - 1, lab[100, 20] - 1
    assert art[k_diag] == "wake" and art[k_vert] == "seam"
    assert abs(f["azimuth_deg"][k_diag] - 45) < 3 and abs(f["azimuth_deg"][k_vert]) < 2
    assert f["dev"][k_diag] < 0.02


def test_artifact_short_line_on_brightness_step_is_seam_but_not_in_uniform_water():
    shape = (120, 120)
    m = np.zeros(shape, bool)
    m[30:55, 60] = True  # 250 m, 1 px, right on the boundary column
    lab, n = _label(m)
    b = _bands(shape)
    for k in ("B2", "B3", "B4"):
        b[k][:, 61:] = 0.045  # brighter water mass to the east: step ~ 0.5 x median
    art, f = classify(lab, n, b, np.ones(shape, bool))
    assert art == ["seam"] and f["step_rel"][0] > 0.3 and f["step_cons"][0] > 0.9
    art2, f2 = classify(lab, n, _bands(shape), np.ones(shape, bool))
    assert art2 == [None] and f2["step_rel"][0] < 0.01
    # a bright filament ON the step is material, not an edge response: brighter than both sides
    for k in ("B2", "B3", "B4"):
        b[k][30:55, 60] = 0.09
    art3, _ = classify(lab, n, b, np.ones(shape, bool))
    assert art3 == [None]


def test_artifact_curved_filament_untouched():
    shape = (300, 300)
    m = np.zeros(shape, bool)
    for c in range(20, 280):  # 2.6 km long wavy filament, 2 px thick, amplitude 12 px
        r = int(150 + 12 * np.sin(c / 25.0))
        m[r:r + 2, c] = True
    lab, n = _label(m)
    assert n == 1
    art, f = classify(lab, n, _bands(shape), np.ones(shape, bool))
    assert art == [None] and f["dev"][0] > 0.08 and f["length_px"][0] > 200


def test_artifact_bright_ship_and_ship_with_tail():
    shape = (200, 200)
    b = _bands(shape)
    water = np.ones(shape, bool)
    for r0, c0 in ((50, 50), (150, 40)):  # two ships 3x6 px: bright in all visible bands and NIR
        for k in ("B2", "B3", "B4"):
            b[k][r0:r0 + 3, c0:c0 + 6] = 0.15
        b["B8"][r0:r0 + 3, c0:c0 + 6] = 0.2
    m = np.zeros(shape, bool)
    m[53:55, 52:56] = True        # 8 px fringe right next to ship 1
    m[151, 47:127] = True         # 800 m straight tail starting at ship 2
    m[100:104, 150:154] = True    # 16 px blob far away from both ships
    lab, n = _label(m)
    art, f = classify(lab, n, b, water)
    assert art[lab[53, 53] - 1] == "ship"
    assert art[lab[151, 80] - 1] == "wake"
    assert art[lab[101, 151] - 1] is None
    # bright shore (big bright area, ring not water) is not a ship
    b2 = _bands(shape)
    for k in ("B2", "B3", "B4", "B8"):
        b2[k][:, :30] = 0.2
    w2 = np.ones(shape, bool)
    w2[:, :30] = False
    m2 = np.zeros(shape, bool)
    m2[60:62, 31:35] = True
    lab2, n2 = _label(m2)
    art2, _ = classify(lab2, n2, b2, w2)
    assert art2 == [None]


def test_artifact_vessel_at_end_of_wake_l41():
    """L41: elongated object (>= 250 m) with a compact grey-white bright cluster <= 5 px beyond one end -> wake."""
    shape = (160, 160)
    m = np.zeros(shape, bool)
    m[60:62, 40:80] = True  # 400 m straight strip, 2 px: too short for the ship-free wake rule (1 km)
    lab, n = _label(m)
    water = np.ones(shape, bool)
    b = _bands(shape)
    for k in ("B2", "B3", "B4"):
        b[k][59:63, 83:86] = 0.05  # small boat 4 px beyond the east end: visible min < 2 x water, so not a ship blob
    b["B8"][59:63, 83:86] = 0.08
    art, f = classify(lab, n, b, water)
    assert art == ["wake"] and f["end_ship"][0] and f["ship_frac"][0] == 0
    # the same strip in empty water: untouched
    art0, f0 = classify(lab, n, _bands(shape), water)
    assert art0 == [None] and not f0["end_ship"][0]
    # green bright spot (floating vegetation: red dip, B4 << B3) at the end: not a vessel
    bg = _bands(shape)
    bg["B3"][59:63, 83:86], bg["B2"][59:63, 83:86], bg["B4"][59:63, 83:86] = 0.09, 0.05, 0.02
    bg["B8"][59:63, 83:86] = 0.08
    assert classify(lab, n, bg, water)[0] == [None]
    # boat next to the middle of the strip (not at an end): untouched
    bm = _bands(shape)
    for k in ("B2", "B3", "B4"):
        bm[k][64:67, 58:61] = 0.05
    bm["B8"][64:67, 58:61] = 0.08
    assert classify(lab, n, bm, water)[0] == [None]
    # curved filament (dev > 0.08) without a bright point at its ends stays unmarked even with the L41 rule
    mc = np.zeros(shape, bool)
    for c in range(20, 140):
        r = int(100 + 8 * np.sin(c / 12.0))
        mc[r:r + 2, c] = True
    labc, nc = _label(mc)
    artc, fc = classify(labc, nc, _bands(shape), water)
    assert artc == [None] and not fc["end_ship"][0]


def test_component_shape_basic():
    m = np.zeros((50, 50), bool)
    m[10, 5:45] = True  # horizontal 40 px line
    lab, n = _label(m)
    f = component_shape(lab, n)
    assert f["length_px"][0] == 40 and f["thick_px"][0] == 1
    assert abs(abs(f["azimuth_deg"][0]) - 90) < 1e-6


def test_zone_score_agreement_and_haze_penalty():
    stats = [{"h3": h, "flagged_water_px": px, "share_permille": 1.0, "mean_prob": 0.5, "n_detections": 1,
              "lon": 0.0, "lat": 0.0, "observed_frac": 1.0} for h, px in (("a", 100), ("b", 80))]
    z = rank_zones(stats, n_dates=1)
    assert [x["h3"] for x in z] == ["a", "b"]
    z = rank_zones(stats, n_dates=1, confirmed_px={"b": 80})  # b fully confirmed: 80*0.5*2 = 80 > 50
    assert [x["h3"] for x in z] == ["b", "a"] and z[0]["score"] == 80.0
    t = z[0]["score_terms"]
    assert t["agreement"]["value"] == 2.0 and t["date_penalty"]["value"] == 1.0 and t["score"] == 80.0
    assert "согласие 2.00" in z[0]["why"]
    zh = rank_zones(stats, n_dates=1, unreliable=True)
    assert zh[0]["score"] == 25.0 and zh[0]["score_terms"]["date_penalty"]["value"] == 0.5
    assert zone_score(10, 0.5, 1, 0.5, True) == 10 * 0.5 * 1.5 * 0.5


def _boat(b, r0, r1, c0, c1):
    for k in ("B2", "B3", "B4"):
        b[k][r0:r1, c0:c1] = 0.05
    b["B8"][r0:r1, c0:c1] = 0.08


def test_artifact_collinear_pieces_grouped_l42():
    """L42: a strip broken into short collinear pieces is checked as one strip; curved / far pieces are not merged."""
    from macroplastic.grid.artifacts import collinear_groups
    shape = (160, 160)
    water = np.ones(shape, bool)
    m = np.zeros(shape, bool)
    for c0 in (40, 51, 62, 73):  # four 8-px pieces (80 m each, gaps 3 px) along one row: 410 m in total
        m[60:62, c0:c0 + 8] = True
    lab, n = _label(m)
    assert n == 4
    b = _bands(shape)
    _boat(b, 59, 63, 84, 87)  # boat 3 px beyond the east end of the whole strip
    art, f = classify(lab, n, b, water, group=False)
    assert art == [None] * 4  # each piece alone is too short for any rule
    art, f = classify(lab, n, b, water)
    assert art == ["wake"] * 4 and len(set(f["group"])) == 1
    # gaps of 10 px: no group, no mark
    m2 = np.zeros(shape, bool)
    for c0 in (20, 38, 56, 74):
        m2[60:62, c0:c0 + 8] = True
    lab2, n2 = _label(m2)
    b2 = _bands(shape)
    _boat(b2, 59, 63, 85, 88)
    art2, f2 = classify(lab2, n2, b2, water)
    assert art2 == [None] * 4 and len(set(f2["group"])) == 4
    # dashed straight line 1.2 km (9-px pieces, 3-px gaps), no boat: as one strip it is a ship-free wake
    ms = np.zeros(shape, bool)
    for c in range(20, 140):
        if c % 12 < 9:
            ms[80:82, c] = True
    labs, ns = _label(ms)
    assert classify(labs, ns, _bands(shape), water, group=False)[0] == [None] * ns
    assert classify(labs, ns, _bands(shape), water)[0] == ["wake"] * ns
    # the same dashed filament bent into a wave: pieces may chain locally, but the union is curved -> untouched
    mc = np.zeros(shape, bool)
    for c in range(20, 140):
        if c % 12 < 9:
            r = int(80 + 12 * np.sin(c / 14.0))
            mc[r:r + 2, c] = True
    labc, nc = _label(mc)
    artc, fc = classify(labc, nc, _bands(shape), water)
    assert all(a is None for a in artc) and len(set(fc["group"])) > 1
    # parallel (side-by-side) pieces are not collinear
    mp = np.zeros(shape, bool)
    mp[60:62, 40:50] = True
    mp[66:68, 40:50] = True
    labp, np_ = _label(mp)
    assert len(set(collinear_groups(component_shape(labp, np_)))) == 2


def test_artifact_marks_propagate_between_models_l42():
    """L42: objects of model A on / within 2 px of an artefact of model B get B's mark (artifact_from = B)."""
    from macroplastic.grid.artifacts import propagate_artifacts
    shape = (120, 120)
    mdd = np.zeros(shape, bool)
    mdd[50:52, 20:80] = True  # MDD wake strip
    mdd[100:103, 100:103] = True  # MDD real object
    lab_b, n_b = _label(mdd)
    art_b = ["wake", None]
    lg = np.zeros(shape, bool)
    lg[49:51, 25:32] = True  # LGBM piece on the wake
    lg[53:54, 40:46] = True  # 1 px below the strip edge (within 2 px)
    lg[70:73, 70:73] = True  # far from any artefact
    lg[99:102, 99:102] = True  # on MDD's real object
    lg[54:70, 60:76] = True  # big patch touching the wake corridor with a thin edge (share < 0.3)
    lab_a, n_a = _label(lg)
    art_a = [None] * n_a
    out = propagate_artifacts(lab_a, n_a, art_a, lab_b, art_b, "mdd")
    marked = {k: (a, frm) for k, a, frm, _ in out}
    k_of = {name: int(lab_a[r, c]) - 1 for name, (r, c) in
            {"on": (49, 26), "below": (53, 41), "far": (71, 71), "real": (100, 100), "big": (60, 65)}.items()}
    assert marked.get(k_of["on"]) == ("wake", "mdd") and marked.get(k_of["below"]) == ("wake", "mdd")
    for name in ("far", "real", "big"):
        assert k_of[name] not in marked
    # a curved arc leaving the other model's straight strip (>= 30 % of it on the strip) does not take a line mark
    arc = np.zeros(shape, bool)
    for c in range(60, 100):
        r = int(50 + 0.02 * (c - 60) ** 2)
        arc[r:r + 2, c] = True
    lab_c, n_c = _label(arc)
    f_c = component_shape(lab_c, n_c)
    assert n_c == 1 and f_c["dev"][0] > 0.08
    lab_s, _ = _label(mdd & (np.arange(120)[:, None] < 60))  # the strip alone (share of the arc near it: 0.35)
    assert propagate_artifacts(lab_c, n_c, [None], lab_s, ["wake"], "mdd") == []
    # own marks are kept (not overwritten)
    art_a2 = list(art_a)
    art_a2[k_of["on"]] = "ship"
    assert k_of["on"] not in {k for k, *_ in propagate_artifacts(lab_a, n_a, art_a2, lab_b, art_b, "mdd")}


def test_confirmation_ignores_partner_artifact_pixels_l42():
    """L42: a detection next to an artefact of the other model only is not confirmed."""
    from macroplastic.grid.confirm import confirmed_components
    shape = (60, 60)
    a = np.zeros(shape, bool)
    a[10:13, 10:13] = True
    lab_a, n_a = _label(a)
    partner = np.zeros(shape, bool)
    partner[14, 10:20] = True  # 1 px below the detection
    lab_p, n_p = _label(partner)
    is_art = np.array([False, True])  # the partner's only object is an artefact
    assert confirmed_components(lab_a, n_a, partner)[0]
    assert not confirmed_components(lab_a, n_a, partner & ~is_art[lab_p])[0]


def test_artifact_small_boat_l43():
    """L43: a small detection on a bright point with a SWIR response (a dry hull) -> ship; a dim spot, a bright but
    wet point (no SWIR excess: floating / awash material) and a bright point on the shore are not boats."""
    shape = (80, 80)
    water = np.ones(shape, bool)
    m = np.zeros(shape, bool)
    m[40:42, 40:42] = True  # 2x2 px detection
    lab, n = _label(m)

    def bands(b8, vis, b11):
        b = _bands(shape) | {"B11": np.full(shape, 0.002, np.float32)}
        for k in ("B2", "B3", "B4"):
            b[k][40, 42] = vis
        b["B8"][40, 42], b["B11"][40, 42] = b8, b11
        return b
    art, f = classify(lab, n, bands(0.08, 0.05, 0.03), water)  # B8 20x, vis 1.7x water, B11 excess 0.37 x B8 excess
    assert art == ["ship"] and f["boat"][0]
    assert classify(lab, n, bands(0.012, 0.035, 0.004), water)[0] == [None]  # dim spot (B8 3x): kept
    assert classify(lab, n, bands(0.08, 0.05, 0.002), water)[0] == [None]  # no SWIR excess (wet material): kept
    assert classify(lab, n, _bands(shape) | {"B11": np.full(shape, 0.002, np.float32)}, water)[0] == [None]
    shore = water.copy()
    shore[:, 43:] = False  # land right next to the bright point: not a boat
    assert classify(lab, n, bands(0.08, 0.05, 0.03), shore)[0] == [None]
    # a large object is never a "small boat"
    mb = np.zeros(shape, bool)
    mb[30:50, 30:40] = True
    labb, nb = _label(mb)
    bb = bands(0.08, 0.05, 0.03)
    assert classify(labb, nb, bb, water)[0] == [None]


def test_artifact_vessel_on_axis_continuation_l43():
    """L43: the vessel may have moved on: a bright cluster on the continuation of the major axis of a straight strip
    (+-2 px corridor, <= 20 px beyond the end) -> wake; the same cluster 6 px off the axis -> untouched."""
    shape = (160, 160)
    m = np.zeros(shape, bool)
    m[60:62, 40:80] = True  # 400 m strip, east end at column 79
    lab, n = _label(m)
    water = np.ones(shape, bool)
    b = _bands(shape)
    _boat(b, 60, 62, 91, 94)  # 12 px beyond the end, on the axis (the L41 rule looks only 5 px)
    art, f = classify(lab, n, b, water)
    assert art == ["wake"] and f["end_ship"][0]
    bo = _bands(shape)
    _boat(bo, 66, 68, 91, 94)  # 12 px beyond, 5.5 px off the axis
    assert classify(lab, n, bo, water)[0] == [None]
    bf = _bands(shape)
    _boat(bf, 60, 62, 104, 107)  # 25 px beyond: too far
    assert classify(lab, n, bf, water)[0] == [None]


def test_artifact_line_continues_in_image_l43():
    """L43: a straight thin detection (600 m) lying on a bright line that goes on in the image beyond both ends
    (an old ship wake / lane) -> wake when the whole line is >= 1 km; without the line beyond the ends -> untouched."""
    shape = (300, 300)
    m = np.zeros(shape, bool)
    m[150:152, 120:180] = True
    lab, n = _label(m)
    water = np.ones(shape, bool)
    b = _bands(shape)
    for k in ("B2", "B3", "B4"):
        b[k][150:152, 20:280] = 0.036  # faint line across 2.6 km
    art, f = classify(lab, n, b, water)
    assert art == ["wake"] and f["trace_px"][0] * 10 >= 1000
    b0 = _bands(shape)
    for k in ("B2", "B3", "B4"):
        b0[k][150:152, 120:180] = 0.036  # bright only under the detection
    art0, f0 = classify(lab, n, b0, water)
    assert art0 == [None] and f0["trace_px"][0] * 10 < 1000
    # a brightness step (edge of a plume) along the detection is not a line: the brighter side is not darker than it
    be = _bands(shape)
    for k in ("B2", "B3", "B4"):
        be[k][:152, :] = 0.036
    assert classify(lab, n, be, water)[1]["trace_px"][0] * 10 < 1000
