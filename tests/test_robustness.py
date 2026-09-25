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


_TINY = ("lgbm features crash on inputs with a side <= 3 px: pixel._local_median subsamples x[f//2::f] "
         "(f = 6 for the 31-px window) -> empty array -> IndexError")
KNOWN_FAIL = {
    "5.lgbm.1x1.direct": _TINY,
    "5.lgbm.5x3.direct": _TINY,
    "5.cli": "one tiny (<= 3 px) chip makes inference.py exit 2 and abort the whole run: the other files "
             "(also already featurized ones) get no outputs",
    "6.val_u16dn_off1000": "--scale auto multiplies DN by 1e-4 but does not remove the L2A BOA_ADD_OFFSET "
                           "(-1000, baseline >= 04.00): +0.1 on every band, all detections lost",
    "7.missing_b8_desc": "missing band named by descriptions -> ValueError in prepare_rows -> exit 2 (model error) "
                         "and abort, instead of exit 1 + skipping the file",
    "9.aux": "io.list_images uses Path.is_file(), False for Windows reserved names -> file silently ignored",
    "9.con": "io.list_images uses Path.is_file(), False for Windows reserved names -> file silently ignored",
    "9.nul": "io.list_images uses Path.is_file(), False for Windows reserved names -> file silently ignored",
    "9.com1": "io.list_images uses Path.is_file(), False for Windows reserved names -> file silently ignored",
    "9.cli": "reserved-name files are not counted (files=2 of 6) and exit code is 0",
}

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
