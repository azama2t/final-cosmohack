"""Fast tests (synthetic rasters, < 30 s) for the tools tools:
inspect_dataset, label_forensics, organizer_adapter, provenance_check."""
from __future__ import annotations

import csv
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
from rasterio.transform import from_origin  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "scripts" / "tools"
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(TOOLS))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, arr: np.ndarray, res=10.0, x0=500000.0, y0=5000000.0, crs="EPSG:32633", desc=None, nodata=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = arr if arr.ndim == 3 else arr[None]
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[1], width=arr.shape[2], count=arr.shape[0],
                       dtype=arr.dtype, crs=crs, transform=from_origin(x0, y0, res, res), nodata=nodata) as ds:
        ds.write(arr)
        if desc:
            ds.descriptions = tuple(desc)


BANDS = ["B2", "B3", "B4", "B8", "B11", "B12"]


def make_dataset(root: Path, n=8, size=32, dn=True):
    """S2-like DN images whose mask is exactly NDVI > 0.3 (derivable by one threshold), 2 'scenes'."""
    rng = np.random.default_rng(0)
    for i in range(n):
        refl = rng.uniform(0.02, 0.1, (len(BANDS), size, size)).astype(np.float32)
        veg = np.zeros((size, size), bool)
        veg[4 + i % 5: 14 + i % 5, 6:20] = True
        refl[3][veg] = rng.uniform(0.3, 0.5, veg.sum())  # B8 high -> NDVI high
        m = np.zeros((size, size), np.uint8)
        ndvi = (refl[3] - refl[2]) / (refl[3] + refl[2])
        m[ndvi > 0.3] = 1
        m[0, 0] = 255  # nodata label
        scene = "20200101_T33UUP" if i < n // 2 else "20200601_T33UVP"
        img = np.round(refl * 10000).astype(np.uint16) if dn else refl
        _write(root / "images" / f"S2_{scene}_{i:03d}.tif", img, x0=500000 + 320 * i, desc=BANDS)
        _write(root / "labels" / f"S2_{scene}_{i:03d}_mask.tif", m, x0=500000 + 320 * i, nodata=255)
    return root


def test_name_template():
    ins = _load("inspect_dataset")
    assert ins.name_template("S2_1-12-19_48MYU_0_cl") == ("S2_{date}_{tile}_{n}_cl", "1-12-19_48MYU_0")
    assert ins.name_template("AF_tr_000001_VIIRS_I1-I5")[1] == "000001"


def test_inspect(tmp_path):
    ins = _load("inspect_dataset")
    make_dataset(tmp_path / "d")
    s = ins.run(tmp_path / "d", tmp_path / "out", sample_per_group=4)
    assert (tmp_path / "out" / "index.html").is_file()
    groups = {g["group"]: g for g in s["groups"]}
    masks = [g for g in groups.values() if g["mask_like"]]
    imgs = [g for g in groups.values() if not g["mask_like"]]
    assert len(masks) == 1 and len(imgs) == 1
    assert {r["value"] for r in masks[0]["class_balance"]} == {0, 1, 255}
    assert imgs[0]["descriptions"] == BANDS
    assert any("DN" in h for h in imgs[0]["hints"])
    rows = list(csv.DictReader(open(tmp_path / "out" / "pairs_guess.csv")))
    assert len(rows) == 8


def test_forensics_finds_threshold(tmp_path):
    lf = _load("label_forensics")
    make_dataset(tmp_path / "d")
    out = tmp_path / "f"
    rc = lf.main(["--images", str(tmp_path / "d/images/*.tif"), "--masks", str(tmp_path / "d/labels/*.tif"),
                  "--ignore", "255", "--group-regex", r"S2_(\d{8}_T\w{5})_", "--folds", "2", "--depths", "3",
                  "--no-lgbm", "--threads", "2", "--out", str(out)])
    assert rc == 0
    res = json.loads((out / "forensics.json").read_text(encoding="utf-8"))
    c1 = res["classes"]["1"]
    assert c1["tree3_f1"] > 0.95
    assert "NDVI" in c1["best_thresholds"][0]["rule"] or c1["best_thresholds"][0]["cv_f1"] > 0.95
    assert res["n_groups"] == 2
    assert (out / "forensics.html").is_file()


def test_adapter_scale_resample_classes(tmp_path):
    from macroplastic.organizer_adapter import list_samples, load_config, load_sample, convert

    make_dataset(tmp_path / "d", n=2)
    cfg = load_config({
        "name": "t", "root": str(tmp_path / "d"),
        "layout": {"image_glob": "images/*.tif", "mask_glob": "labels/*.tif", "id_regex": r"(S2_\d{8}_T\w{5}_\d{3})"},
        "bands": {"source": "descriptions", "output": ["B2", "B3", "B4", "B8", "B11", "B12"]},
        "radiometry": {"scale": 1e-4, "offset": 0.0}, "nodata": {"values": [0]},
        "resolution": {"target": 5, "resampling": "nearest"},
        "classes": {"map": {0: 7, 1: 1}, "default": 0, "ignore_values": [255]},
    })
    ss = list_samples(cfg)
    assert len(ss) == 2 and all(s.mask_path for s in ss)
    img, mask, names, grid = load_sample(cfg, ss[0])
    assert img.shape == (6, 64, 64) and img.dtype == np.float32 and names == BANDS
    with rasterio.open(ss[0].image_paths[0]) as ds:
        raw = ds.read().astype(np.float32) * 1e-4
    assert np.allclose(img[:, ::2, ::2], raw, atol=1e-6)  # 2x nearest upsampling of DN*1e-4
    assert set(np.unique(mask)) <= {0, 1, 7} and mask[0, 0] == 0  # 255 -> ignore(0)
    man = convert(cfg, tmp_path / "o", workers=1, verbose=False)
    rows = list(csv.DictReader(open(man)))
    assert len(rows) == 2 and int(rows[0]["n_debris_px"]) > 0
    with rasterio.open(rows[0]["image"]) as ds:
        assert ds.descriptions == tuple(BANDS) and ds.res == (5.0, 5.0)


def test_canon_band():
    from macroplastic.organizer_adapter import canon_band

    assert [canon_band(x) for x in ["B02", "b8a", "B11", "SR_B4", "SCL"]] == ["B2", "B8A", "B11", "B4", "SCL"]


def test_provenance(tmp_path):
    pc = _load("provenance_check")
    make_dataset(tmp_path / "ours", n=4)
    theirs = tmp_path / "theirs"
    theirs.mkdir()
    src = sorted((tmp_path / "ours/images").glob("*.tif"))
    shutil.copy(src[0], theirs / "copy.tif")  # identical file, other name (no tile/date)
    shutil.copy(src[1], theirs / "S2_20200101_T33UUP_999.tif")  # same scene key
    with rasterio.open(src[2]) as ds:
        a = ds.read()
    _write(theirs / "flipped.tif", a[:, ::-1, :].copy(), x0=900000, desc=BANDS)  # flipped copy elsewhere
    _write(theirs / "far.tif", np.random.default_rng(5).integers(0, 3000, (6, 32, 32)).astype(np.uint16), x0=100000,
           crs="EPSG:32610")
    rc = pc.main(["--ours", str(tmp_path / "ours/images"), "--theirs", str(theirs), "--out", str(tmp_path / "p"),
                  "--threads", "2"])
    assert rc == 0
    s = json.loads((tmp_path / "p/summary.json").read_text(encoding="utf-8"))
    assert s["theirs_identical_files"] == 2  # copy.tif + renamed copy
    assert s["theirs_with_scene_match"] == 1
    assert s["theirs_content_match"] >= 3  # two copies + flipped
    assert s["theirs_with_bbox_overlap"] == 2
    rows = list(csv.DictReader(open(tmp_path / "p/theirs_files.csv")))
    far = [r for r in rows if r["theirs"].endswith("far.tif")][0]
    assert far["n_bbox"] == "0" and far["n_identical"] == "0"


def test_parse_tile_date():
    pc = _load("provenance_check")
    assert pc.parse_tile_date("S2_1-12-19_48MYU_0.tif") == ({"48MYU"}, {"2019-12-01"})
    t, d = pc.parse_tile_date("S2A_MSIL2A_20190412T160901_N0211_R140_T16PCC_20190412T201811.SAFE")
    assert t == {"16PCC"} and d == {"2019-04-12"}
