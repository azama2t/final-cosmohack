"""MADOS training: MADOS loader + content de-duplication key (fast: synthetic MADOS scene in tmp_path; 1 real crop if present)."""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from macroplastic.data import mados as MD  # noqa: E402

S2B_BANDS = [(442, 60), (492, 10), (559, 10), (665, 10), (704, 20), (739, 20), (780, 20), (833, 10), (864, 20),
             (1610, 20), (2186, 20)]


def _write(path: Path, arr: np.ndarray):
    import rasterio

    path.parent.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
                           dtype=arr.dtype) as ds:
            ds.write(arr, 1)


@pytest.fixture()
def fake_root(tmp_path):
    sc, crop = "Scene_7", 3
    for i, (wl, res) in enumerate(S2B_BANDS):
        n = 240 // (res // 10)
        a = np.full((n, n), 0.01 * (i + 1), np.float32)
        a[0, 0] = 0.5 + i  # marker of the top-left native pixel
        _write(tmp_path / sc / str(res) / f"{sc}_L2R_rhorc_{wl}_{crop}.tif", a)
    cl = np.zeros((240, 240), np.uint8)
    cl[10:20, 10:20] = 1   # debris
    cl[30:40, 30:40] = 6   # oil spill (MADOS-only)
    cl[50:60, 50:60] = 12  # waves & wakes
    cl[70:80, 70:80] = 15  # sea snot (MADOS-only)
    _write(tmp_path / sc / "10" / f"{sc}_L2R_cl_{crop}.tif", cl)
    _write(tmp_path / sc / "10" / f"{sc}_L2R_conf_{crop}.tif", (cl > 0).astype(np.uint8))
    (tmp_path / "splits").mkdir()
    (tmp_path / "splits" / "train_X.txt").write_text(f"{sc}_{crop}\n")
    return tmp_path


def test_band_order_and_upsampling(fake_root):
    img, cl, conf, info = MD.load_patch("Scene_7_3", root=fake_root)
    assert img.shape == (11, 240, 240) and img.dtype == np.float32
    assert info["wavelengths"] == [w for w, _ in S2B_BANDS] and info["sensor"] == "S2B"
    for i, (_, res) in enumerate(S2B_BANDS):  # MARIDA order B1..B12 == sorted wavelengths
        f = res // 10
        assert np.allclose(img[i, :f, :f], 0.5 + i) and img[i, f, f] == pytest.approx(0.01 * (i + 1))
    assert MD.list_patches("all", root=fake_root) == ["Scene_7_3"] == MD.list_patches("train", root=fake_root)


def test_class_mapping(fake_root):
    _, cl, _, info = MD.load_patch("Scene_7_3", root=fake_root, extra=True)
    assert cl[15, 15] == 1 and cl[35, 35] == 16 and cl[55, 55] == 12 and cl[75, 75] == 19 and cl[0, 0] == 0
    _, cl2, _, _ = MD.load_patch("Scene_7_3", root=fake_root, extra=False)
    assert cl2[15, 15] == 1 and cl2[35, 35] == 0 and cl2[75, 75] == 0 and cl2.max() <= 15
    assert list(MD.map_classes(np.arange(16), extra=False)) == [0, 1, 2, 3, 4, 5, 0, 7, 8, 9, 10, 11, 12, 0, 0, 0]


def test_lbp_key_is_radiometry_robust_and_localises_offset():
    T = pytest.importorskip("train_lgbm_mados")
    rng = np.random.default_rng(0)
    b4 = rng.uniform(0.01, 0.08, (300, 300)).astype(np.float32)
    b8 = rng.uniform(0.01, 0.08, (300, 300)).astype(np.float32)
    ka, va = T.lbp_key(b4[:256, :256], b8[:256, :256])
    # the "other dataset": shifted crop with an additive offset and a small gain (different processing)
    kb, vb = T.lbp_key(b4[20:260, 30:270] * 1.02 + 0.003, b8[20:260, 30:270] * 1.02 - 0.002)
    assert va.sum() > 0 and vb.sum() > 0
    ref = {int(k): i for i, k in enumerate(ka[va])}
    ya, xa = np.nonzero(va)
    yb, xb = np.nonzero(vb)
    offs = [(ya[ref[int(k)]] - y, xa[ref[int(k)]] - x) for k, y, x in zip(kb[vb], yb, xb) if int(k) in ref]
    vals, cnt = np.unique(np.array(offs), axis=0, return_counts=True)
    assert tuple(vals[cnt.argmax()]) == (20, 30) and cnt.max() > 1000


@pytest.mark.skipif(not (ROOT / "data" / "MADOS" / "Scene_0" / "10").is_dir(), reason="MADOS not unpacked")
def test_real_crop():
    img, cl, conf, info = MD.load_patch("Scene_0_1")
    assert img.shape == (11, 240, 240) and cl.shape == (240, 240) and conf.shape == (240, 240)
    assert np.nanmedian(img) < 0.3 and cl.max() <= 19 and len(info["wavelengths"]) == 11
