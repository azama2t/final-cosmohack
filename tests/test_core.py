"""Core infrastructure tests: io, win_path, metrics, splits, indices, registry, inference.py.

All synthetic (temp GeoTIFFs); the last test uses real MARIDA and is skipped if it is not unpacked.
Run: .venv\\Scripts\\python.exe -m pytest -q tests\\test_core.py
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from macroplastic import CLASS_NAMES
from macroplastic import indices as ix
from macroplastic.io import (bands_dict, channel_names, guess_channel_names, list_images, normalize_band,
                             prob_to_uint8, read_mask, read_raster, win_path, write_raster)
from macroplastic.metrics import ConfusionAccumulator, binary_scores, confusion_matrix, scores_from_confusion
from macroplastic.models import FDIRulePredictor, ModelError, get_predictor
from macroplastic.models import registry as reg
from macroplastic.splits import parse_patch, read_splits, scene_of, scene_overlap
from macroplastic.utils import ROOT, available_cpus, cuda_usable, load_yaml, seed_everything

REPO = Path(__file__).resolve().parents[1]
MARIDA = channel_names("marida")

# reflectance of a "debris" pixel and a "water" pixel for the 11 MARIDA bands
DEBRIS = dict(B1=0.05, B2=0.05, B3=0.06, B4=0.04, B5=0.05, B6=0.03, B7=0.07, B8=0.08, B8A=0.07, B11=0.02, B12=0.01)
WATER = dict(B1=0.06, B2=0.05, B3=0.04, B4=0.02, B5=0.012, B6=0.01, B7=0.008, B8=0.005, B8A=0.004, B11=0.002,
             B12=0.001)


def _profile(h=32, w=32, count=11, dtype="float32"):
    from rasterio.crs import CRS
    from affine import Affine

    return {"driver": "GTiff", "height": h, "width": w, "count": count, "dtype": dtype,
            "crs": CRS.from_epsg(32616), "transform": Affine(10, 0, 300000, 0, -10, 1800000)}


def _tile(h=32, w=32, debris_box=(8, 16, 8, 16), nan_px=True) -> np.ndarray:
    arr = np.stack([np.full((h, w), WATER[b], np.float32) for b in MARIDA])
    if debris_box is not None:
        r0, r1, c0, c1 = debris_box
        for i, b in enumerate(MARIDA):
            arr[i, r0:r1, c0:c1] = DEBRIS[b]
    if nan_px:
        arr[:, 0, 0] = np.nan
    return arr


def _write_tile(path: Path, arr: np.ndarray):
    import rasterio

    prof = _profile(arr.shape[1], arr.shape[2], arr.shape[0])
    os.makedirs(os.path.dirname(win_path(path)), exist_ok=True)
    with rasterio.open(win_path(path), "w", **prof) as dst:
        dst.write(arr)
    return prof


# ----------------------------------------------------------------------------- utils / io

def test_utils_basics():
    assert (ROOT / "src" / "macroplastic").is_dir()
    assert available_cpus() >= 1
    seed_everything(1)
    a = np.random.rand(3)
    seed_everything(1)
    assert np.array_equal(a, np.random.rand(3))
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    assert cuda_usable() is False
    cfg = load_yaml("configs/channels.yaml")
    assert len(cfg["marida"]) == 11 and len(cfg["s2_l2a_12"]) == 12 and len(cfg["mdd_input"]) == 12


def test_channel_sets():
    assert MARIDA == ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
    assert channel_names("s2_l2a_12")[9] == "B9"
    assert normalize_band("b08") == "B8" and normalize_band("B8a") == "B8A" and normalize_band("B11") == "B11"
    assert guess_channel_names(11) == MARIDA
    assert guess_channel_names(12) == channel_names("s2_l2a_12")
    assert guess_channel_names(3, ["B04", "B03", "B02"]) == ["B4", "B3", "B2"]
    assert guess_channel_names(5) is None
    with pytest.raises(KeyError):
        channel_names("nope")


def test_io_roundtrip(tmp_path):
    arr = _tile()
    prof = _write_tile(tmp_path / "in.tif", arr)
    back, p2 = read_raster(tmp_path / "in.tif")
    assert back.dtype == np.float32 and back.shape == (11, 32, 32)
    np.testing.assert_array_equal(np.isnan(back), np.isnan(arr))
    np.testing.assert_allclose(np.nan_to_num(back), np.nan_to_num(arr))
    assert p2["crs"] == prof["crs"] and p2["transform"] == prof["transform"]
    # uint8 and float32 writes keep the georeference
    u8 = prob_to_uint8(np.linspace(0, 1, 32 * 32).reshape(32, 32))
    write_raster(tmp_path / "o" / "p.tif", u8, p2, dtype="uint8")
    b8, p3 = read_raster(tmp_path / "o" / "p.tif", dtype=None)
    assert b8.dtype == np.uint8 and b8[0, 0, 0] == 0 and b8[0, -1, -1] == 255
    assert p3["crs"] == prof["crs"] and p3["transform"] == prof["transform"] and p3["count"] == 1
    write_raster(tmp_path / "f.tif", back[:2], p2, dtype="float32", descriptions=["B1", "B2"])
    f, p4 = read_raster(tmp_path / "f.tif")
    assert f.shape == (2, 32, 32) and p4["descriptions"] == ["B1", "B2"]
    with pytest.raises(ValueError):
        write_raster(tmp_path / "bad.tif", np.zeros((5, 5)), p2)
    with pytest.raises(FileNotFoundError):
        read_raster(tmp_path / "missing.tif")
    assert prob_to_uint8(np.array([np.nan, 0.5, 2.0])).tolist() == [0, 128, 255]


def test_win_path_reserved_names(tmp_path):
    for name in ("aux", "con", "nul"):
        d = tmp_path / name
        arr = _tile(8, 8, None, False)
        _write_tile(d / "x.tif", arr)
        back, _ = read_raster(d / "x.tif")
        assert back.shape == (11, 8, 8)
    if sys.platform == "win32":
        assert win_path(tmp_path / "aux").startswith("\\\\?\\")
        assert win_path(win_path(tmp_path)) == win_path(tmp_path)
    else:
        assert win_path(tmp_path / "aux") == str(tmp_path / "aux")


def test_list_images_and_mask(tmp_path):
    _write_tile(tmp_path / "s" / "a_1.tif", _tile(8, 8))
    import rasterio

    prof = _profile(8, 8, 1, "uint8")
    cl = np.zeros((1, 8, 8), np.uint8)
    cl[0, 2, 2] = 1
    with rasterio.open(tmp_path / "s" / "a_1_cl.tif", "w", **prof) as dst:
        dst.write(cl)
    (tmp_path / "s" / "a_1_conf.tif").write_bytes(b"")
    (tmp_path / "a_prob.tif").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("x")
    assert [p.name for p in list_images(tmp_path)] == ["a_1.tif"]
    m = read_mask(tmp_path / "s" / "a_1_cl.tif")
    assert m.dtype == np.uint8 and m.shape == (8, 8) and m.sum() == 1


# ----------------------------------------------------------------------------- metrics

def test_metrics_known_matrix():
    gt = np.array([0, 1, 1, 2, 2, 7])
    pr = np.array([1, 1, 2, 2, 2, 7])
    cm = confusion_matrix(pr, gt, 16)
    assert cm.sum() == 5  # gt==0 ignored
    assert cm[1, 1] == 1 and cm[1, 2] == 1 and cm[2, 2] == 2 and cm[7, 7] == 1
    s = scores_from_confusion(cm)
    c1, c2, c7 = s["per_class"][1], s["per_class"][2], s["per_class"][7]
    assert c1["precision"] == 1.0 and c1["recall"] == 0.5
    assert math.isclose(c1["f1"], 2 / 3) and math.isclose(c1["iou"], 0.5)
    assert math.isclose(c2["precision"], 2 / 3) and c2["recall"] == 1.0
    assert math.isclose(c2["f1"], 0.8) and math.isclose(c2["iou"], 2 / 3)
    assert c7["iou"] == 1.0
    assert math.isclose(s["miou"], (0.5 + 2 / 3 + 1.0) / 3)  # absent classes are not averaged
    assert 0 not in s["per_class"] and s["per_class"][1]["name"] == CLASS_NAMES[1]
    assert math.isnan(s["per_class"][5]["f1"])  # 0/0 -> NaN
    md = binary_scores(pr, gt)
    assert (md["tp"], md["fp"], md["fn"]) == (1, 0, 1) and math.isclose(md["f1"], 2 / 3)
    # bool prediction, pooled over a list
    md2 = binary_scores([pr == 1, np.array([True])], [gt, np.array([3])])
    assert (md2["tp"], md2["fp"], md2["fn"]) == (1, 1, 1) and math.isclose(md2["iou"], 1 / 3)


def test_metrics_accumulator_pooled_and_empty():
    acc = ConfusionAccumulator(16)
    acc.update(np.array([[1, 7]]), np.array([[1, 7]]))
    acc.update(np.array([[7, 1]]), np.array([[1, 0]]))  # FN on MD; FP on ignored pixel does not count
    r = acc.compute()
    assert (r["md"]["tp"], r["md"]["fp"], r["md"]["fn"]) == (1, 0, 1)
    assert math.isclose(r["md"]["f1"], 2 / 3)
    # micro pooling != mean over patches
    assert r["per_class"][1]["support"] == 2
    empty = ConfusionAccumulator(16)
    empty.update(np.zeros((4, 4), int), np.zeros((4, 4), int))  # everything unlabeled
    e = empty.compute()
    assert math.isnan(e["md"]["f1"]) and math.isnan(e["miou"]) and e["n_pixels"] == 0
    # md_pred overrides pred==1
    acc2 = ConfusionAccumulator(16)
    acc2.update(np.array([7, 7]), np.array([1, 7]), md_pred=np.array([True, False]))
    assert acc2.compute()["md"]["f1"] == 1.0
    with pytest.raises(ValueError):
        confusion_matrix(np.zeros(3), np.zeros(4))


# ----------------------------------------------------------------------------- splits

def test_splits_parsing(tmp_path):
    p = parse_patch("11-6-18_16PCC_0")
    assert p.scene == "S2_11-6-18_16PCC" and p.tile == "16PCC" and p.index == 0
    assert (p.date.year, p.date.month, p.date.day) == (2018, 6, 11)
    assert scene_of("S2_1-12-19_48MYU_12.tif") == "S2_1-12-19_48MYU"
    assert scene_of("S2_1-12-19_48MYU_12_cl.tif") == "S2_1-12-19_48MYU"
    assert parse_patch("S2_1-12-19_48MYU").index is None
    with pytest.raises(ValueError):
        parse_patch("garbage_name")
    sp = tmp_path / "splits"
    sp.mkdir()
    (sp / "train_X.txt").write_bytes(b"11-6-18_16PCC_0\r\n11-6-18_16PCC_1\r\n1-12-19_48MYU_0\r\n")
    (sp / "val_X.txt").write_bytes(b"11-6-18_16PCC_2\r\n\r\n")
    (sp / "test_X.txt").write_bytes(b"22-3-20_36JUN_4\r\n")
    s = read_splits(tmp_path)
    assert s["train"] == ["11-6-18_16PCC_0", "11-6-18_16PCC_1", "1-12-19_48MYU_0"] and s["val"] == ["11-6-18_16PCC_2"]
    r = scene_overlap(s)
    assert r["shared_scenes"]["train&val"] == ["S2_11-6-18_16PCC"]
    assert r["shared_scenes"]["train&test"] == [] and r["scene_disjoint"] is False
    assert r["n_scenes"] == {"train": 2, "val": 1, "test": 1}


# ----------------------------------------------------------------------------- indices

def test_indices_known_numbers():
    b = {k: np.array([v], np.float32) for k, v in DEBRIS.items()}
    r = (832.9 - 664.8) / (1612.05 - 664.8)
    assert math.isclose(float(ix.fdi(b)[0]), 0.08 - (0.03 + (0.02 - 0.03) * r * 10), rel_tol=1e-5)
    assert math.isclose(float(ix.fdi(b)[0]), 0.0677461, rel_tol=1e-4)
    assert math.isclose(float(ix.fdi(b, variant="marida")[0]), 0.0606407, rel_tol=1e-4)
    assert math.isclose(float(ix.fai(b)[0]), 0.0435406, rel_tol=1e-4)
    assert math.isclose(float(ix.ndvi(b)[0]), 1 / 3, rel_tol=1e-5)
    assert math.isclose(float(ix.ndwi(b)[0]), -1 / 7, rel_tol=1e-5)
    assert math.isclose(float(ix.ndmi(b)[0]), 0.6, rel_tol=1e-5)
    assert math.isclose(float(ix.si(b)[0]), (0.95 * 0.94 * 0.96) ** (1 / 3), rel_tol=1e-5)
    assert math.isclose(float(ix.bsi(b)[0]), -0.07 / 0.19, rel_tol=1e-5)
    assert math.isclose(float(ix.nrd(b)[0]), 0.04, rel_tol=1e-5)
    # zero denominator -> NaN, no warnings
    z = {"B8": np.zeros(1, np.float32), "B4": np.zeros(1, np.float32)}
    assert np.isnan(ix.ndvi(z)[0])
    # array + names input and zero-padded names
    arr = _tile(4, 4, (0, 2, 0, 2), False)
    d = ix.compute_indices(arr, MARIDA)
    assert list(d) == list(ix.INDEX_NAMES) and d["FDI"].shape == (4, 4)
    assert math.isclose(float(d["FDI"][0, 0]), 0.0677461, rel_tol=1e-4)
    st = ix.stack_indices(arr, [n.replace("B", "B0") if len(n) == 2 else n for n in MARIDA])
    assert st.shape == (8, 4, 4) and st.dtype == np.float32
    with pytest.raises(KeyError):
        ix.fdi({"B8": np.zeros(1)})
    assert bands_dict(arr, MARIDA)["B8A"].shape == (4, 4)


# ----------------------------------------------------------------------------- registry

def test_registry_fdi_rule_and_fallback(monkeypatch):
    arr = _tile()
    p = get_predictor("fdi_rule")
    prob = p.predict_proba(arr, MARIDA)
    assert prob.shape == (32, 32) and prob.dtype == np.float32
    assert prob[10, 10] > 0.9 and prob[25, 25] < 0.05 and prob[0, 0] == 0.0  # debris / water / NaN
    with pytest.raises(KeyError):
        get_predictor("no_such_model")
    monkeypatch.setitem(reg.LAZY_MODULES, "ghost", "macroplastic.models._does_not_exist")
    fb = get_predictor("ghost")
    assert isinstance(fb, FDIRulePredictor) and fb.fallback_from == "ghost"
    with pytest.raises(ModelError):
        get_predictor("ghost", fallback=False)

    class Const:
        name, threshold = "const", 0.3

        def __init__(self, v=0.7, **kw):
            self.v = v

        def predict_proba(self, a, names):
            return np.full(a.shape[1:], self.v, np.float32)

    monkeypatch.setitem(reg._REGISTRY, "const", Const)
    assert float(get_predictor("const", v=0.2).predict_proba(arr, MARIDA)[0, 0]) == pytest.approx(0.2)


# ----------------------------------------------------------------------------- inference.py

def _cli(*args, timeout=300):
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="")
    return subprocess.run([sys.executable, str(REPO / "inference.py"), *map(str, args)], capture_output=True,
                          text=True, cwd=str(REPO), timeout=timeout, env=env)


def test_inference_two_tiles(tmp_path):
    data = tmp_path / "data"
    _write_tile(data / "scene_a" / "t1.tif", _tile())
    _write_tile(data / "t2.tif", _tile(debris_box=None))
    # a MARIDA-style mask next to the image must be skipped
    import rasterio

    with rasterio.open(data / "scene_a" / "t1_cl.tif", "w", **_profile(32, 32, 1, "uint8")) as dst:
        dst.write(np.ones((1, 32, 32), np.uint8))
    out = tmp_path / "out"
    r = _cli("--data-dir", data, "--output", out, "--device", "cpu", "--model", "fdi_rule", "--profile")
    assert r.returncode == 0, r.stderr
    assert "[profile] total" in r.stdout
    names = sorted(p.name for p in out.iterdir())
    assert names == ["t1_mask.tif", "t1_prob.tif", "t2_mask.tif", "t2_prob.tif"]
    prob, pp = read_raster(out / "t1_prob.tif", dtype=None)
    mask, _ = read_raster(out / "t1_mask.tif", dtype=None)
    assert prob.dtype == np.uint8 and mask.dtype == np.uint8 and prob.shape == (1, 32, 32)
    _, pin = read_raster(data / "scene_a" / "t1.tif")
    assert pp["crs"] == pin["crs"] and pp["transform"] == pin["transform"]
    assert mask[0, 8:16, 8:16].all() and mask[0].sum() == 64 and set(np.unique(mask)) <= {0, 1}
    assert prob[0, 10, 10] > 200 and prob[0, 25, 25] < 20
    m2, _ = read_raster(out / "t2_mask.tif", dtype=None)
    assert m2.sum() == 0
    # threshold 0 -> everything flagged; DN input is auto-scaled
    r = _cli("--data-dir", data, "--output", tmp_path / "o2", "--device", "cuda", "--model", "fdi_rule",
             "--threshold", "0")
    assert r.returncode == 0, r.stderr
    assert read_raster(tmp_path / "o2" / "t2_mask.tif", dtype=None)[0].all()
    dn = tmp_path / "dn"
    _write_tile(dn / "t1.tif", _tile(nan_px=False) * 10000)
    r = _cli("--data-dir", dn, "--output", tmp_path / "o3", "--device", "cpu", "--model", "fdi_rule")
    assert r.returncode == 0, r.stderr
    assert read_raster(tmp_path / "o3" / "t1_mask.tif", dtype=None)[0].sum() == 64


def test_inference_exit_codes(tmp_path):
    data = tmp_path / "data"
    _write_tile(data / "t1.tif", _tile(8, 8))
    # missing / empty data dir -> 1
    r = _cli("--data-dir", tmp_path / "nope", "--output", tmp_path / "o", "--model", "fdi_rule")
    assert r.returncode == 1 and "ERROR" in r.stderr and "Traceback" not in r.stderr
    (tmp_path / "empty").mkdir()
    r = _cli("--data-dir", tmp_path / "empty", "--output", tmp_path / "o", "--model", "fdi_rule")
    assert r.returncode == 1 and "no input" in r.stderr
    # unknown model -> 2
    r = _cli("--data-dir", data, "--output", tmp_path / "o", "--model", "does_not_exist")
    assert r.returncode == 2 and "Traceback" not in r.stderr
    # wrong band count -> 1
    bad = tmp_path / "bad"
    _write_tile(bad / "b.tif", np.zeros((5, 8, 8), np.float32))
    r = _cli("--data-dir", bad, "--output", tmp_path / "o", "--model", "fdi_rule")
    assert r.returncode == 1 and "bands" in r.stderr
    r = _cli("--data-dir", data, "--output", tmp_path / "o", "--model", "fdi_rule", "--channels", "s2_l2a_12")
    assert r.returncode == 1
    # corrupt tif -> 1, good file still processed
    (data / "broken.tif").write_bytes(b"not a tiff")
    r = _cli("--data-dir", data, "--output", tmp_path / "o4", "--model", "fdi_rule")
    assert r.returncode == 1 and (tmp_path / "o4" / "t1_prob.tif").exists()
    (data / "broken.tif").unlink()
    # default model (lgbm) works either with the real model or via fallback to fdi_rule
    r = _cli("--data-dir", data, "--output", tmp_path / "o5")
    assert r.returncode in (0, 2), r.stderr
    assert "Traceback" not in r.stderr


# ----------------------------------------------------------------------------- real MARIDA (optional)

def test_real_marida(marida_root):
    from macroplastic.io import read_marida_patch

    s = read_splits(marida_root)
    assert {k: len(v) for k, v in s.items()} == {"train": 694, "val": 328, "test": 359}
    rep = scene_overlap(s)
    assert all(len(v) == 0 for v in rep["duplicate_patches"].values())
    patch = s["val"][0]
    d = read_marida_patch(marida_root, patch)
    assert d["image"].shape == (11, 256, 256) and d["image"].dtype == np.float32
    assert d["cl"] is not None and d["cl"].shape == (256, 256) and int(d["cl"].max()) <= 15
    fin = d["image"][np.isfinite(d["image"])]
    assert fin.size and float(np.median(fin)) < 1.5  # reflectance, not DN
    prob = get_predictor("fdi_rule").predict_proba(d["image"], d["channels"])
    assert prob.shape == (256, 256) and np.isfinite(prob).all()
