"""Manual review: manual artefact marks (configs/manual_artifacts.yaml) in scripts/build_service_data.py."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parents[1]
CRS = "EPSG:32616"
X0, Y0 = 400000.0, 1750000.0  # UTM 16N, ~15.8 N -88.9 E
TR = from_origin(X0, Y0, 10, 10)


def _mod():
    spec = importlib.util.spec_from_file_location("bsd_l56", ROOT / "scripts" / "build_service_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _lonlat(row: float, col: float):
    x, y = TR * (col, row)
    return Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform(x, y)


def _tif(p: Path, arr: np.ndarray):
    with rasterio.open(p, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype=arr.dtype,
                       crs=CRS, transform=TR) as ds:
        ds.write(arr, 1)


def _scene(d: Path):
    d.mkdir(parents=True)
    H = W = 80
    _tif(d / "water_mask.tif", np.ones((H, W), np.uint8))
    prob = np.zeros((H, W), np.uint8)
    prob[20:23, 20:23] = 230  # blob A (kept)
    prob[50:53, 50:53] = 230  # blob B (manual mark)
    _tif(d / "prob_mdd.tif", prob)
    (d / "prob_mdd.json").write_text(json.dumps({"threshold": 0.5}), encoding="utf-8")
    (d / "scene.json").write_text(json.dumps({"scene_id": "S2_TEST", "cloud_cover": 0}), encoding="utf-8")
    Image.fromarray(np.full((H, W, 3), 40, np.uint8)).save(d / "rgb.png")


def test_manual_marks_pick_only_objects_in_circle():
    mod = _mod()
    labels = np.zeros((40, 40), np.int32)
    labels[5:7, 5:7] = 1
    labels[30:32, 30:34] = 2
    lon, lat = _lonlat(31, 33.5)  # at the right end of object 2
    rules = [{"region": "r", "date": "2026-01-01", "model": "*", "lon": lon, "lat": lat, "radius_m": 15,
              "kind": "wake"}]
    assert mod.manual_marks(labels, 2, TR, CRS, "r", "2026-01-01", "mdd", rules) == {1: "wake"}
    # other date / other model / far point -> nothing
    assert mod.manual_marks(labels, 2, TR, CRS, "r", "2026-01-02", "mdd", rules) == {}
    assert mod.manual_marks(labels, 2, TR, CRS, "r", "2026-01-01", "mdd", [dict(rules[0], model="lgbm")]) == {}
    far = dict(rules[0], lon=_lonlat(20, 20)[0], lat=_lonlat(20, 20)[1])
    assert mod.manual_marks(labels, 2, TR, CRS, "r", "2026-01-01", "mdd", [far]) == {}


def test_load_manual_artifacts_config(tmp_path):
    mod = _mod()
    rules = mod.load_manual_artifacts(ROOT / "configs" / "manual_artifacts.yaml")
    assert rules and all(r["kind"] in mod.MANUAL_KINDS and r["reason"] and r["who"] for r in rules)
    bad = tmp_path / "bad.yaml"
    bad.write_text("artifacts:\n- {region: r, date: '2026-01-01', lon: 0, lat: 0, radius_m: 5, kind: plastic}\n",
                   encoding="utf-8")
    try:
        mod.load_manual_artifacts(bad)
        raise AssertionError("bad kind accepted")
    except SystemExit:
        pass
    assert mod.load_manual_artifacts(tmp_path / "missing.yaml") == []


def test_manual_mark_excluded_like_artifact(tmp_path):
    mod = _mod()
    live, out = tmp_path / "live", tmp_path / "out"
    _scene(live / "r" / "2026-01-01")
    lon, lat = _lonlat(51.5, 51.5)
    mod.MANUAL_RULES[:] = [{"region": "r", "date": "2026-01-01", "model": "*", "lon": lon, "lat": lat,
                            "radius_m": 20, "kind": "wake", "reason": "test", "who": "test", "when": "2026-09-25"}]
    try:
        res = mod.process_scene("r", "2026-01-01", live / "r" / "2026-01-01", ["mdd"], out, lambda *_: None)
    finally:
        mod.MANUAL_RULES.clear()
    fc = json.loads((out / "r" / "2026-01-01" / "mdd" / "detections.geojson").read_text(encoding="utf-8"))
    props = [f["properties"] for f in fc["features"]]
    assert len(props) == 2
    arts = [p for p in props if p.get("artifact")]
    assert len(arts) == 1 and arts[0]["artifact"] == "wake" and arts[0]["artifact_source"] == "manual"
    real = [p for p in props if not p.get("artifact")]
    assert len(real) == 1 and "artifact_source" not in real[0]
    r = res["models"]["mdd"]
    assert r["ts"]["n_detections"] == 1 and r["ts"]["n_artifacts"] == 1
    assert r["artifacts"]["manual"] == 1
    assert sum(s["flagged_water_px"] for s in r["stats"]) == 9  # only blob A counts in the index
