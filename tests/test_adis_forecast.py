"""L119: ADIS-прогноз — конфиг заморожен (sha256), сплит по группам без пересечений, без утечки счёта."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "case"))

import adis_forecast as AF  # noqa: E402
from macroplastic.case.concentration import poisson_count_interval  # noqa: E402

CFG_PATH = ROOT / "configs" / "adis_forecast.yaml"
SHA_PATH = ROOT / "configs" / "adis_forecast.yaml.sha256"
REPORT_JSON = ROOT / "reports" / "quantity" / "adis_forecast.json"
HAVE_DATA = (ROOT / "data" / "extra" / "field" / "adis" / "Segments.csv").exists()


def test_config_sha256_frozen():
    want = SHA_PATH.read_text(encoding="utf-8").split()[0]
    assert hashlib.sha256(CFG_PATH.read_bytes()).hexdigest() == want
    cfg = AF.load_cfg()
    assert cfg["_sha256"] == want


def test_config_has_rule_and_no_leaky_features():
    cfg = yaml.safe_load(CFG_PATH.read_text(encoding="utf-8"))
    assert cfg["profile"]["primary"] == "gt10cm"
    assert {"val", "selection", "test", "otherwise"} <= set(cfg["acceptance_rule"])
    assert cfg["features"]["used"] == AF.FEATURES
    bad = ("n_objects", "dhat", "std_", "var_", "N_", "C_", "area", "A")
    for f in AF.FEATURES:
        assert not f.startswith(bad), f


def test_test_group_selection_deterministic_and_capped():
    g = pd.Series(["a"] * 50 + ["b"] * 30 + ["c"] * 10 + ["d"] * 5 + ["e"] * 5 + [f"x{i}" for i in range(20)])
    t1 = AF.test_groups(g, 119, 0.2, 0.35, 0.10)
    t2 = AF.test_groups(g, 119, 0.2, 0.35, 0.10)
    assert t1 == t2
    sz = g.value_counts() / len(g)
    assert all(sz[x] <= 0.10 for x in t1)
    assert sz[t1].sum() <= 0.35


def test_garwood_matches_case_interval():
    n = np.array([0, 1, 3, 12, 250])
    lo, hi = AF.garwood(n)
    for k, a, b in zip(n, lo, hi):
        ra, rb = poisson_count_interval(int(k))
        assert a == pytest.approx(ra, rel=1e-9, abs=1e-12)
        assert b == pytest.approx(rb, rel=1e-9)


@pytest.mark.skipif(not HAVE_DATA, reason="сырьё ADIS не в git (data/extra/field/adis)")
@pytest.mark.parametrize("scheme", ["region", "ship"])
def test_split_no_group_overlap(scheme):
    cfg = AF.load_cfg()
    seg = AF.load_segments(cfg)
    role = AF.make_split(seg, cfg, scheme)
    col = AF.group_col(scheme)
    tr, te = set(seg.loc[role == "train", col]), set(seg.loc[role == "test", col])
    assert tr and te and not (tr & te)
    share = float((role == "test").mean())
    assert cfg["splits"]["test_fraction_target"] <= share <= cfg["splits"]["test_fraction_cap"]
    # val-фолды внутри train тоже без пересечения групп
    trd = seg[role == "train"].reset_index(drop=True)
    for a, b in AF.gkf(trd[col].to_numpy(), 5):
        assert not (set(trd[col].iloc[a]) & set(trd[col].iloc[b]))
    assert not set(AF.FEATURES) & {c for c in seg.columns if c.startswith("N_")}


@pytest.mark.skipif(not REPORT_JSON.exists(), reason="отчёт ещё не построен")
def test_report_consistent_with_config_and_rule():
    o = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    assert o["config_sha256"] == SHA_PATH.read_text(encoding="utf-8").split()[0]
    assert o["stage"] == "final"
    for p, d in o["decision"].items():
        if d["selected_on_val"] is None:
            assert d["final"] == "M0_median"
        for scheme in ("region", "ship"):
            val = o["schemes"][scheme]["profiles"][p]["val"]
            for c in AF.CANDIDATES:
                bd = val[c]["vs_M0"]
                assert val[c]["passes_rule"] == (bd["mae"]["hi"] < 0 and bd["log1p_mae"]["delta"] <= 0)
    for scheme, sc in o["schemes"].items():
        assert sc["groups_test"] >= 5  # бутстреп по группам не вырожден


@pytest.mark.skipif(not REPORT_JSON.exists(), reason="отчёт ещё не построен")
def test_external_calibrated_is_reference_only():
    o = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    e = o["external_adis_calibrated"]
    ch = e["checks"]["readme_label_10cm"]
    assert ch["cal10_zero_where_n10_zero"] and ch["cal10_positive_where_n50_zero"] > 0  # класс > 10 см
    g = e["profiles"]["gt10cm"]
    assert g["authors_observed_over_raw_median"] == pytest.approx(2.0, rel=1e-3)
    assert g["calibrated_lo_typ"] <= g["calibrated_C"] <= g["calibrated_hi_typ"]
    assert not any("dhat" in f or "calibrated" in f for f in AF.FEATURES)  # не признак
    assert o["decision_primary"] == "M0_median"  # внешнее число не меняет решение
