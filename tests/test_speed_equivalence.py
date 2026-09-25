"""Lane L17: the fast inference path gives the same result as the original one (20 MARIDA val chips).

Reference = a frozen copy of the pre-L17 feature code (below) + lightgbm.Booster.predict per chip.
Checked: features bit-identical; CPU probabilities bit-identical (batched and per-chip API);
CUDA kernel (only with MACROPLASTIC_TEST_CUDA=1, e.g. through scripts/gpu_queue.py) max |prob_u8 diff|
<= 1 and identical masks; the DN pre-check equals the full median rule.
"""
from __future__ import annotations

import os
from typing import Sequence

import numpy as np
import pytest
from scipy import ndimage

N_CHIPS = 20

# ----------------------------------------------------------------------------- frozen reference (pre-L17 pixel.py)
L4, L8, L11 = 664.8, 832.9, 1612.05


def _ref_ratio(a, b):
    den = a + b
    out = np.full(a.shape, np.nan, dtype=np.float32)
    np.divide(a - b, den, out=out, where=np.abs(den) > 1e-6)
    out[np.isnan(a) | np.isnan(b)] = np.nan
    return out


def _ref_indices(b):
    B2, B3, B4, B6, B8, B11 = b["B2"], b["B3"], b["B4"], b["B6"], b["B8"], b["B11"]
    out = {}
    out["FDI"] = B8 - (B6 + (B11 - B6) * ((L8 - L4) / (L11 - L4)) * 10.0)
    out["FAI"] = B8 - (B4 + (B11 - B4) * ((833.0 - 665.0) / (1614.0 - 665.0)))
    out["NDVI"] = _ref_ratio(B8, B4)
    out["NDWI"] = _ref_ratio(B3, B8)
    out["NDMI"] = _ref_ratio(B8, B11)
    out["SI"] = np.cbrt((1.0 - B2) * (1.0 - B3) * (1.0 - B4))
    num = (B11 + B4) - (B8 + B2)
    den = (B11 + B4) + (B8 + B2)
    bsi = np.full(B2.shape, np.nan, dtype=np.float32)
    np.divide(num, den, out=bsi, where=np.abs(den) > 1e-6)
    bsi[np.isnan(num)] = np.nan
    out["BSI"] = bsi
    out["NRD"] = B8 - B4
    return {k: v.astype(np.float32, copy=False) for k, v in out.items()}


def _ref_win_mean_std(x, w):
    valid = ~np.isnan(x)
    xv = np.where(valid, x, 0.0).astype(np.float64)
    vf = valid.astype(np.float64)
    cnt = ndimage.uniform_filter(vf, size=w, mode="reflect")
    s1 = ndimage.uniform_filter(xv, size=w, mode="reflect")
    s2 = ndimage.uniform_filter(xv * xv, size=w, mode="reflect")
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = s1 / cnt
        var = s2 / cnt - mean * mean
    var = np.maximum(var, 0.0)
    bad = cnt < 1e-6
    mean[bad] = np.nan
    var[bad] = np.nan
    return mean.astype(np.float32), np.sqrt(var).astype(np.float32)


def _ref_local_median(x, w):
    H, W = x.shape
    f = max(1, int(round(w / 5)))
    k = max(3, int(round(w / f)) | 1)
    fill = np.nanmedian(x) if np.isfinite(x).any() else 0.0
    xs = x[f // 2::f, f // 2::f]
    xs = np.where(np.isnan(xs), fill, xs)
    med = ndimage.median_filter(xs, size=k, mode="reflect")
    iy = np.minimum(np.arange(H) // f, med.shape[0] - 1)
    ix = np.minimum(np.arange(W) // f, med.shape[1] - 1)
    return med[np.ix_(iy, ix)].astype(np.float32)


def ref_compute_features(arr, channel_names: Sequence[str]):
    from macroplastic.features.pixel import BANDS11, CONTRAST, INDICES, WIN_KEYS, WIN_SIZES, select_bands

    x = select_bands(arr, channel_names)
    x[~np.isfinite(x)] = np.nan
    b = {n: x[i] for i, n in enumerate(BANDS11)}
    ind = _ref_indices(b)
    feats = [x[i] for i in range(len(BANDS11))] + [ind[k] for k in INDICES]
    src = {"B8": b["B8"], **ind}
    for k in WIN_KEYS:
        for w in WIN_SIZES:
            m, s = _ref_win_mean_std(src[k], w)
            feats += [m, s]
    for k, w in CONTRAST:
        feats.append(src[k] - _ref_local_median(src[k], w))
    out = np.stack(feats, 0).astype(np.float32, copy=False)
    out[:, np.isnan(x).any(0)] = np.nan
    return out


def ref_predict(booster, arr, names, num_threads):
    """Pre-L17 LGBMPredictor.predict_proba for a 256x256 chip (one block)."""
    from macroplastic.features.pixel import select_bands

    f = ref_compute_features(arr, names)
    F = f.shape[0]
    X = f.reshape(F, -1).T
    ok = ~np.isnan(X).all(1)
    p = np.zeros(len(X), np.float32)
    if ok.any():
        p[ok] = np.asarray(booster.predict(np.ascontiguousarray(X[ok]), num_threads=num_threads), np.float32)
    out = p.reshape(arr.shape[1:])
    out[~np.isfinite(select_bands(arr, names)).all(0)] = 0.0
    return out


# ----------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def chips(marida_root):
    from macroplastic.io import read_raster

    ids = [s.strip() for s in (marida_root / "splits" / "val_X.txt").read_text().splitlines() if s.strip()]
    step = max(1, len(ids) // N_CHIPS)
    arrs = []
    for pid in ids[::step][:N_CHIPS]:
        scene = pid.rsplit("_", 1)[0]
        arrs.append(read_raster(marida_root / "patches" / f"S2_{scene}" / f"S2_{pid}.tif")[0])
    # edge cases: NaN block, inf, zero denominators, a NaN row
    a = arrs[0].copy()
    a[:, 10:20, 10:20] = np.nan
    a[3, 50:60, 50:60] = np.inf
    a[:, 100:110, 100:110] = 0.0
    a[7, 200:203, :] = np.nan
    arrs.append(a)
    return arrs


@pytest.fixture(scope="module")
def reference(chips, repo_root):
    lgb = pytest.importorskip("lightgbm")
    from macroplastic.features.pixel import BANDS11

    model = repo_root / "weights" / "lgbm" / "model.txt"
    if not model.exists():
        pytest.skip("weights/lgbm/model.txt missing")
    booster = lgb.Booster(model_file=str(model))
    return [ref_predict(booster, a, BANDS11, 8) for a in chips]


def _u8(p):
    from macroplastic.io import prob_to_uint8

    return prob_to_uint8(p).astype(np.int16)


# ----------------------------------------------------------------------------- tests
def test_features_bit_identical(chips):
    from macroplastic.features.pixel import BANDS11, compute_features

    for a in chips:
        assert np.array_equal(compute_features(a, BANDS11), ref_compute_features(a, BANDS11), equal_nan=True)


def test_cpu_predict_identical(chips, reference):
    from macroplastic.features.pixel import BANDS11
    from macroplastic.models.lgbm_predict import load_predictor

    pred = load_predictor(device="cpu")
    assert pred.device == "cpu"
    many = pred.predict_proba_many([(a, BANDS11) for a in chips], batch_rows=300_000)  # several batches
    for a, p, r in zip(chips, many, reference):
        assert np.array_equal(p, r)
    for a, r in zip(chips[:3], reference[:3]):
        assert np.array_equal(pred.predict_proba(a, BANDS11), r)


def test_cuda_predict_equivalent(chips, reference, monkeypatch):
    if os.environ.get("MACROPLASTIC_TEST_CUDA") != "1":
        pytest.skip("set MACROPLASTIC_TEST_CUDA=1 (GPU through scripts/gpu_queue.py)")
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    from macroplastic.features.pixel import BANDS11
    from macroplastic.models.lgbm_predict import load_predictor

    pred = load_predictor(device="cuda")
    if pred.device != "cuda":
        pytest.skip(f"CUDA backend unavailable: {pred.gpu_error}")
    many = pred.predict_proba_many([(a, BANDS11) for a in chips])
    for p, r in zip(many, reference):
        assert np.abs(_u8(p) - _u8(r)).max() <= 1
        assert np.array_equal(p >= pred.threshold, r >= pred.threshold)
        assert np.abs(p - r).max() < 1e-6


def test_parse_forest_threshold_rounding(repo_root):
    from macroplastic.models.lgbm_predict import parse_forest

    f = parse_forest(repo_root / "weights" / "lgbm" / "model.txt")
    t32 = f["nodes"][:, 1].view(np.float32).astype(np.float64)
    assert f["n_features"] == 48 and len(f["node_off"]) > 0
    assert np.isfinite(t32).all()


def test_dn_precheck_matches_median():
    import inference

    rng = np.random.default_rng(0)
    cases = [rng.random((3, 8, 8), dtype=np.float32) * s for s in (1.0, 3.0, 4.0, 10000.0)]
    c = np.full((1, 4, 4), 1.0, np.float32)
    c[0, :2] = 3.0  # exactly half above 2 -> median 2.0 -> not DN
    cases += [c, np.full((1, 3, 3), np.nan, np.float32)]
    d = np.full((1, 2, 2), 2.5, np.float32)
    d[0, 0, 0] = np.inf
    cases.append(d)
    for a in cases:
        fin = a[np.isfinite(a)]
        expect = bool(fin.size and float(np.median(fin)) > 2.0)
        assert inference._median_finite_gt2(np, a) == expect
