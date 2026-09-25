"""Org_to_map: organiser chips -> map data root (scripts/tools/org_to_map.py), synthetic, < 30 s.

4 georeferenced 64x64 chips (UTM 33N, MARIDA-like 11 bands, reflectance) of one scene in a 2x2 layout, a bright
debris line in chip 0, prob rasters as inference.py writes them (<stem>_prob.tif) -> data root valid
(validate_service_data: 0 errors), kind organizer, one region/date, detections present.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
pytest.importorskip("h3")
from rasterio.transform import from_origin  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, arr: np.ndarray, x0: float, y0: float, crs="EPSG:32633", georef=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = arr if arr.ndim == 3 else arr[None]
    kw = dict(crs=crs, transform=from_origin(x0, y0, 10.0, 10.0)) if georef else {}
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[1], width=arr.shape[2], count=arr.shape[0],
                       dtype=arr.dtype, **kw) as ds:
        ds.write(arr)


def make_chips(root: Path, georef=True) -> tuple[Path, Path, Path]:
    rng = np.random.default_rng(0)
    chips, preds, labels = root / "chips", root / "pred", root / "labels"
    x00, y00 = 400000.0, 4800000.0
    for k in range(4):
        r, c = divmod(k, 2)
        img = np.empty((11, 64, 64), np.float32)
        base = [0.03, 0.035, 0.04, 0.025, 0.02, 0.015, 0.012, 0.01, 0.009, 0.004, 0.003]  # dark water
        for b, v in enumerate(base):
            img[b] = v + rng.normal(0, 0.001, (64, 64))
        prob = np.full((64, 64), 5, np.uint8)
        lab = np.zeros((64, 64), np.uint8)
        if k == 0:
            img[7, 20:23, 10:40] = 0.06  # B8 up: floating line (NDWI < 0, filled as a hole in water)
            img[8, 20:23, 10:40] = 0.05
            prob[20:23, 10:40] = 230
            lab[20:23, 10:40] = 1
        if k == 3:
            img[:, 40:, 40:] = 0.25  # land corner: NDWI ~ 0 -> not water
            img[7, 40:, 40:] = 0.35
        stem = f"S2_12-3-21_33TVF_{k}"
        _write(chips / f"{stem}.tif", img, x00 + c * 640, y00 - r * 640, georef=georef)
        _write(preds / f"{stem}_prob.tif", prob, x00 + c * 640, y00 - r * 640, georef=georef)
        _write(labels / f"{stem}_cl.tif", lab, x00 + c * 640, y00 - r * 640, georef=georef)
    return chips, preds, labels


@pytest.fixture(scope="module")
def tool():
    return _load(REPO / "scripts" / "tools" / "org_to_map.py", "org_to_map")


def test_org_to_map_builds_valid_root(tmp_path, tool):
    chips, preds, labels = make_chips(tmp_path)
    out = tmp_path / "org_map"
    rc = tool.main(["--chips", str(chips), "--pred", f"lgbm={preds}", "--threshold", "lgbm=0.5",
                    "--labels", str(labels), "--out", str(out)])
    assert rc == 0
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert man["kind"] == "organizer"
    assert [r["id"] for r in man["regions"]] == ["s2_12_3_21_33tvf"]
    reg = man["regions"][0]
    assert reg["dates"][0]["date"] == "2021-03-12"  # MARIDA-style D-M-YY
    assert reg["summary"]["n_detections"] >= 1
    assert man["demo"]["region"] == reg["id"]
    sj = json.loads((Path(str(out) + "_live") / reg["id"] / "2021-03-12" / "scene.json").read_text(encoding="utf-8"))
    assert sj["n_chips"] == 4 and sj["shape"] == [128, 128]
    assert sj["models"]["lgbm"]["labels"]["tp"] == 90
    val = _load(REPO / "scripts" / "validate_service_data.py", "validate_service_data")
    v = val.validate(out)
    assert not v.errors, v.errors
    det = json.loads((out / reg["id"] / "2021-03-12" / "lgbm" / "detections.geojson").read_text(encoding="utf-8"))
    assert len(det["features"]) >= 1


def test_org_to_map_rejects_ungeoreferenced(tmp_path, tool, capsys):
    chips, preds, _ = make_chips(tmp_path, georef=False)
    rc = tool.main(["--chips", str(chips), "--pred", f"lgbm={preds}", "--out", str(tmp_path / "o")])
    assert rc == 2
    assert "без геопривязки" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


def test_grouping_names_and_clusters(tool):
    assert tool.date_from_name("S2_1-12-19_48MYU_0") == "2019-12-01"
    assert tool.date_from_name("T33TVF_20210312T100031_B02") == "2021-03-12"
    assert tool.date_from_name("chip_0042") is None
    assert tool.name_prefix("S2_1-12-19_48MYU_17") == "S2_1-12-19_48MYU"
    assert tool.name_prefix("scene_20200101_3") == "scene_20200101"
    boxes = np.array([[0, 0, 640, 640], [640, 0, 1280, 640], [50000, 0, 50640, 640]], float)
    assert sorted(map(sorted, tool.cluster_boxes(boxes, 100.0))) == [[0, 1], [2]]


def test_date_unknown_and_unique_names(tmp_path, tool):
    """Rehearsal fixes: anonymous chips (no date) in two UTM zones -> unique region ids/names, date_unknown in scene.json and in
    the manifest dates; an all-zero prob chip is «no signal», not a binary mask; P*255 folder keeps 0/1 chips low."""
    rng = np.random.default_rng(1)
    chips, preds = tmp_path / "chips", tmp_path / "pred"
    base = [0.03, 0.035, 0.04, 0.025, 0.02, 0.015, 0.012, 0.01, 0.009, 0.004, 0.003]
    for k, (crs, x0) in enumerate([("EPSG:32633", 400000.0), ("EPSG:32633", 400640.0), ("EPSG:32634", 400000.0)]):
        img = np.stack([v + rng.normal(0, 0.001, (64, 64)) for v in base]).astype(np.float32)
        prob = np.zeros((64, 64), np.uint8)
        if k == 0:
            img[7, 20:23, 10:40] = 0.06
            prob[20:23, 10:40] = 250
        if k == 2:
            prob[5:8, 5:8] = 1  # P = 1/255 in a P*255 folder: must NOT become P = 1
        _write(chips / f"tile_{k:04d}.tif", img, x0, 4800000.0, crs=crs)
        _write(preds / f"tile_{k:04d}.tif", prob, x0, 4800000.0, crs=crs)
    out = tmp_path / "m"
    assert tool.main(["--chips", str(chips), "--pred", f"lgbm={preds}", "--threshold", "lgbm=0.9", "--out", str(out)]) == 0
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    ids = [r["id"] for r in man["regions"]]
    names = [r["name"] for r in man["regions"]]
    assert len(ids) == 2 and len(set(ids)) == 2 and len(set(names)) == 2, (ids, names)
    assert all("utm33n" in i or "utm34n" in i for i in ids)
    for r in man["regions"]:
        assert r["date_unknown"] is True and all(d["date_unknown"] is True for d in r["dates"])
    assert man["models"]["lgbm"]["threshold"] == 0.9 and "MARIDA" not in man["models"]["lgbm"]["name"]
    live = Path(str(out) + "_live")
    for r in man["regions"]:
        sj = json.loads((live / r["id"] / "1900-01-01" / "scene.json").read_text(encoding="utf-8"))
        assert sj["date_unknown"] is True and sj["date_source"] == "unknown"
        pj = json.loads((live / r["id"] / "1900-01-01" / "prob_lgbm.json").read_text(encoding="utf-8"))
        assert not pj["warnings"], pj["warnings"]  # no «бинарная маска» for all-zero / low-P chips
    z = [json.loads(p.read_text(encoding="utf-8"))["zero_signal_chips"] for p in live.glob("*/*/prob_lgbm.json")]
    assert sorted(sum(z, [])) == ["tile_0001"]
    import rasterio as rio

    r34 = next(r["id"] for r in man["regions"] if "utm34n" in r["id"])
    with rio.open(live / r34 / "1900-01-01" / f"prob_lgbm.tif") as ds:
        assert int(ds.read(1).max()) == 1  # stays P = 1/255


def test_unique_names_fallbacks(tool):
    sc = [{"name": "tile_p1", "crs": "EPSG:32616", "tile": ""}, {"name": "tile_p1", "crs": "EPSG:32616", "tile": ""},
          {"name": "tile_p1", "crs": "EPSG:32751", "tile": ""}, {"name": "x", "crs": None, "tile": "16PCC"}]
    out = tool.unique_names(sc)
    assert len({s["region"] for s in out}) == 4 and len({s["name"] for s in out}) == 4
    assert out[2]["name"] == "tile_p1_utm51s" and out[3]["name"] == "x"
    assert tool.crs_label("EPSG:32616") == "utm16n" and tool.crs_label("EPSG:3857") == "epsg3857"
