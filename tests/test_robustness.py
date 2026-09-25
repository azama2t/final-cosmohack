"""Robustness of inference.py and the lgbm / fdi_rule predictors (lane L24; report: reports/robustness.md).

All checks live in scripts/robustness_report.py and run once per session (~40 s on CPU) on 5 MARIDA
*val* patches + one 512x512 crop of a live L2A scene; synthetic files go to out/robustness_pytest/
(gitignored). Each case must not be FAIL (WARN is allowed). Known failures are xfail with the reason
(strict=False: they turn into XPASS once the code is fixed).
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")


def _load_script():
    spec = importlib.util.spec_from_file_location("robustness_report", REPO / "scripts" / "robustness_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Known failures (xfail with the reason). Lane L26 fixed all 10 FAILs of L24: tiny chips (pixel._local_median),
# per-file error isolation in inference.py (5.cli, 7.missing_b8_desc), L2A BOA offset in --scale auto
# (6.val_u16dn_off1000), Windows reserved names in io.list_images (9.*). Remaining WARNs are allowed by test_case.
KNOWN_FAIL: dict[str, str] = {}

CASES = [
    # 1 empty chip
    "1.lgbm.nan.direct", "1.lgbm.zero.direct", "1.fdi_rule.nan.direct", "1.fdi_rule.zero.direct",
    "1.lgbm.half_nan.direct", "1.lgbm.cli", "1.fdi_rule.cli",
    # 2 clouds, 3 glint
    "2.lgbm", "2.fdi_rule", "3.lgbm.B8+B11", "3.lgbm.B8", "3.fdi_rule.B8+B11", "3.fdi_rule.B8",
    # 4 radiometry
    *[f"4.{m}.{k}" for m in ("lgbm", "fdi_rule") for k in ("scale0.9", "scale1.1", "offset+0.005", "noise_s0.002")],
    # 5 sizes
    *[f"5.{m}.{h}x{w}.direct" for h, w in ((100, 100), (1000, 1000), (300, 700), (700, 300), (513, 40), (1, 1), (5, 3))
      for m in ("lgbm", "fdi_rule")], "5.cli",
    # 6 dtypes
    "6.val_f64", "6.val_u16dn", "6.live_u16dn", "6.val_u16dn_off1000", "6.live_u16dn_off1000", "6.cli",
    # 7 channels
    "7.perm_desc", "7.desc_b0x_lower", "7.extra_b10_desc", "7.perm_nodesc", "7.extra_nodesc",
    "7.missing_nodesc_10", "7.missing_b8_desc", "7.channels_flag_mismatch",
    # 8 broken files
    "8.mixed_folder", "8.truncated", "8.no_images", "8.missing_dir", "8.only_broken", "8.output_is_file",
    "8.live_scene_folder",
    # 9 paths
    "9.снимок 1", "9.aux", "9.con", "9.nul", "9.com1", "9.a b.c", "9.cli",
]


@pytest.fixture(scope="session")
def robustness_results(marida_root):
    rr = _load_script()
    if not (rr.LIVE_SCENE / "bands.tif").exists():
        pytest.skip(f"live scene not found: {rr.LIVE_SCENE}")
    try:
        ctx = rr.Ctx(REPO / "out" / "robustness_pytest", n_val=5)
    except Exception as e:  # noqa: BLE001 - e.g. weights/lgbm missing
        pytest.skip(f"cannot set up robustness context: {type(e).__name__}: {e}")
    res = rr.run_checks(ctx)
    return {r["case"]: r for r in res}


def test_no_check_crashed(robustness_results):
    crashed = [r for c, r in robustness_results.items() if c.endswith(".crash")]
    assert not crashed, crashed


def test_all_cases_covered(robustness_results):
    produced = {c for c in robustness_results if not c.startswith("9.write.")}
    assert produced == set(CASES), {"unexpected": produced - set(CASES), "missing": set(CASES) - produced}


@pytest.mark.parametrize("case", [pytest.param(c, marks=pytest.mark.xfail(reason=KNOWN_FAIL[c], strict=False))
                                  if c in KNOWN_FAIL else c for c in CASES])
def test_case(robustness_results, case):
    r = robustness_results.get(case)
    if r is None:
        pytest.skip(f"case {case} not produced (setup, see test_all_cases_covered)")
    assert r["status"] != "FAIL", f"expect: {r['expect']} | result: {r['result']}"


# ---------------------------------------------------------------- lane L26: unit checks of inference.py behaviour
# (in-process, fake predictors: fast, and they cover paths the report cannot trigger with the real models)

L26_WORK = REPO / "out" / "robustness_pytest" / "l26"


def _inference_module():
    spec = importlib.util.spec_from_file_location("inference_l26", REPO / "inference.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fresh_dir(name: str) -> Path:
    import shutil

    d = L26_WORK / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


def _chip(h=16, w=16, value=0.03):
    import numpy as np

    return np.full((11, h, w), value, np.float32)


class _FakeB1:
    """prob = B1 * 10 (so the written prob encodes the reflectance the model saw); raises on 9-px-high chips."""
    name, threshold, device = "fake", 0.5, "cpu"

    def predict_proba(self, arr, names):
        import numpy as np

        if arr.shape[1] == 9:
            raise RuntimeError("boom on purpose")
        return np.clip(np.nan_to_num(arr[0], nan=0.0) * 10.0, 0, 1).astype(np.float32)


class _FakeBatched(_FakeB1):
    """Batched API like LGBMPredictor: the whole batch fails if it contains a 9-px-high chip."""

    def is_small(self, arr):
        return True

    def prepare_rows(self, arr, names):
        return {"X": arr[0].ravel(), "shape": arr.shape[1:]}

    def predict_rows_many(self, rows_list):
        import numpy as np

        if any(r["shape"][0] == 9 for r in rows_list):
            raise RuntimeError("batch boom on purpose")
        return [np.clip(r["X"].reshape(r["shape"]) * 10.0, 0, 1).astype(np.float32) for r in rows_list]


@pytest.mark.parametrize("fake", [_FakeB1, _FakeBatched], ids=["per_file", "batched"])
def test_l26_model_error_isolated_per_file(monkeypatch, fake):
    import json

    import macroplastic.models as models

    rr = _load_script()
    d = _fresh_dir(f"iso_{fake.__name__}")
    for n, h in (("a_good", 16), ("b_bad", 9), ("c_good", 16)):
        rr.write_tif(d / "in" / f"{n}.tif", _chip(h))
    monkeypatch.setattr(models, "get_predictor", lambda *a, **k: fake())
    inf = _inference_module()
    rep = d / "errors.json"
    code = inf.main(["--data-dir", str(d / "in"), "--output", str(d / "out"), "--device", "cpu", "--report", str(rep)])
    assert code == 1
    assert (d / "out" / "a_good_prob.tif").exists() and (d / "out" / "c_good_mask.tif").exists()
    assert not (d / "out" / "b_bad_prob.tif").exists()
    r = json.loads(rep.read_text(encoding="utf-8"))
    assert [Path(x["file"]).name for x in r["failed"]] == ["b_bad.tif"] and r["ok"] == 2 and r["exit_code"] == 1


def test_l26_model_load_failure_exit_2():
    inf = _inference_module()
    rr = _load_script()
    d = _fresh_dir("noload")
    rr.write_tif(d / "in" / "x.tif", _chip())
    assert inf.main(["--data-dir", str(d / "in"), "--output", str(d / "out"), "--model", "no_such_model",
                     "--strict"]) == 2


def test_l26_scale_offset_rules(monkeypatch):
    """--scale auto on L2A DN with +1000 detects the offset; manual --scale 1e-4 --offset -1000 does the same;
    manual --scale 1e-4 alone does not guess; DN without offset is left alone."""
    import json

    import numpy as np

    import macroplastic.models as models

    rr = _load_script()
    monkeypatch.setattr(models, "get_predictor", lambda *a, **k: _FakeB1())
    inf = _inference_module()
    rho = np.random.default_rng(0).uniform(0.004, 0.06, (11, 32, 32)).astype(np.float32)
    d = _fresh_dir("offset")
    rr.write_tif(d / "in" / "dn_off.tif", np.rint(rho * 1e4) + 1000, dtype="uint16")
    rr.write_tif(d / "in" / "dn.tif", np.rint(rho * 1e4), dtype="uint16")
    expect = np.clip(np.rint(np.clip(np.rint(rho[0] * 1e4) * np.float32(1e-4) * 10, 0, 1) * 255), 0, 255)

    def run(tag, *extra):
        o = d / f"out_{tag}"
        code = inf.main(["--data-dir", str(d / "in"), "--output", str(o), "--report", str(o / "r.json"), *extra])
        assert code == 0
        return {k: rr.read_out(o / f"{k}_prob.tif").astype(int) for k in ("dn_off", "dn")}, \
            json.loads((o / "r.json").read_text(encoding="utf-8"))

    auto, rep = run("auto")
    assert np.abs(auto["dn_off"] - expect).max() <= 1 and np.abs(auto["dn"] - expect).max() <= 1
    rules = {Path(x["file"]).name: x for x in rep["input_rules"]}
    assert rules["dn_off.tif"]["offset"] == -1000 and "subtracting 1000" in rules["dn_off.tif"]["rule"]
    assert rules["dn.tif"]["offset"] == 0 and "no BOA offset" in rules["dn.tif"]["rule"]
    manual, _ = run("manual", "--scale", "1e-4", "--offset", "-1000")
    assert np.abs(manual["dn_off"] - expect).max() <= 1
    plain, _ = run("plain", "--scale", "1e-4")
    assert np.abs(plain["dn"] - expect).max() <= 1 and plain["dn_off"].min() == 255  # +0.1 -> prob 1: no guessing


def test_l26_tiny_chips_all_sizes():
    import numpy as np

    from macroplastic.io import channel_names
    from macroplastic.models import get_predictor

    try:
        p = get_predictor("lgbm", fallback=False, device="cpu")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"lgbm not loadable: {e}")
    names = channel_names("marida")
    for h, w in ((1, 1), (1, 2), (2, 3), (3, 3), (3, 40), (40, 1), (4, 4)):
        a = np.random.default_rng(h * 100 + w).uniform(0.005, 0.05, (11, h, w)).astype(np.float32)
        q = p.predict_proba(a, names)
        q2 = p.predict_rows_many([p.prepare_rows(a, names)])[0]
        assert q.shape == (h, w) and np.isfinite(q).all() and np.array_equal(q, q2)
