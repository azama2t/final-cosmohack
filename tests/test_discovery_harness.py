"""L133: discovery harness (scripts/discovery/harness.py) — scoring, LODO isolation, leaderboard, set freezing."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "discovery"))
import harness as H  # noqa: E402


def _s(sid, set_, group, N=np.nan, c=np.nan, bg=None, roi_px=64 * 64, val=0.0):
    roi = np.zeros((128, 128), bool)
    roi.ravel()[:roi_px] = True
    bands = {b: np.full((128, 128), val, np.float32) for b in H.BANDS11}
    bands["SCL"] = np.zeros((128, 128), np.uint8)
    meta = dict(roi=roi, roi_area_km2=roi_px * 1e-4, pixel_m=10.0, epsg=32635, bounds=[0, 0, 1280, 1280], lon=0.0,
                lat=0.0, date="20190418", datetime=None, has_scl=False)
    return H.Sample(sid, set_, group, "synthetic", bands, meta, dict(N_true=N, obs_density_km2=c, background=bg))


def _toy():
    a1 = [_s(f"a{i}", "A1", f"d{i}", N=n, val=float(i)) for i, n in enumerate([0.0, 360.0, 1000.0, 3738.0])]
    a2 = [_s(f"f{i}", "A2", f"f{i}", c=c, roi_px=128 * 128) for i, c in enumerate([0.0, 4.0, 50.0])]
    a3 = [_s(f"w{i}", "A3", f"w{i}", N=0.0, bg=b, roi_px=32 * 32) for i, b in enumerate(["ship", "cloud", "water"])]
    a4 = [_s(f"z{i}", "A4", f"z{i}", roi_px=40) for i in range(2)]
    return a1 + a2 + a3 + a4


def test_zero_method_scores():
    s = _toy()
    p = H.per_sample(H.run(lambda b, m: (0, 0, 0), samples=s), s)
    r = H.score(p, n_boot=200)
    e1 = np.mean(np.log10(np.array([0.0, 360.0, 1000.0, 3738.0]) + 1))
    assert r["point"]["E1"] == pytest.approx(e1)
    assert r["point"]["E2"] == pytest.approx(np.mean(np.log10(np.array([0.0, 4.0, 50.0]) + 1)))
    assert r["point"]["FP3"] == 0.0 and r["point"]["G2"] == 0.0
    assert r["point"]["P4"] == 1.0  # N̂ = 0 is implausible inside a windrow
    assert r["point"]["SCORE"] == pytest.approx(H.combine(e1, r["point"]["E2"], 0.0, 0.0, 1.0))
    lo, hi = r["ci"]["SCORE"]
    assert lo <= r["point"]["SCORE"] <= hi


def test_oracle_on_astar_and_fp_rules():
    s = _toy()
    truth = {x.meta["bounds"][0]: 0 for x in s}  # unused; oracle below uses band value -> date index
    ns = [0.0, 360.0, 1000.0, 3738.0]

    def pred(b, m):
        if m["roi_area_km2"] == pytest.approx(64 * 64 * 1e-4):
            n = ns[int(b["B1"][0, 0])]
            return n, n * 0.9, n * 1.1
        return 5000.0, 4000.0, 6000.0  # "thousands" everywhere else

    p = H.per_sample(H.run(pred, samples=s), s)
    r = H.score(p, n_boot=100)
    assert r["point"]["E1"] == pytest.approx(0.0) and r["point"]["cov1"] == 1.0
    assert r["point"]["FP3"] == 1.0          # N̂ ≥ 1 on every background window
    assert r["point"]["G2"] == pytest.approx(1.0)  # ĉ ≈ 3 049/km² ≥ 1000 while c < 100 on all three
    # A4: 5000 items on 40 px (0.004 km²) = 1.25e6 /km² -> inside [1e4, 1e8]
    assert r["point"]["P4"] == 0.0
    del truth


def test_lodo_fit_never_sees_heldout():
    s = _toy()
    seen = []

    def fit(train):
        seen.append(sorted(t["N"] for t in train))

    held = []

    def pred(b, m):
        held.append(seen[-1] if seen else None)
        return 0, 0, 0

    H.run(pred, fit, samples=s)
    ns = [0.0, 360.0, 1000.0, 3738.0]
    for i, n in enumerate(ns):  # the i-th A1 prediction was made after a fit without date i
        assert sorted(x for j, x in enumerate(ns) if j != i) == held[i]
    assert seen[-1] == sorted(ns)  # final fit on all A1 dates before A2..A4
    assert len(seen) == len(ns) + 1


def test_errors_count_as_zero():
    s = _toy()

    def bad(b, m):
        raise RuntimeError("boom")

    p = H.per_sample(H.run(bad, samples=s), s)
    r = H.score(p, n_boot=50)
    assert r["n_errors"] == len(s)
    assert (p.N_hat == 0).all()


def test_negative_and_nan_predictions():
    s = _toy()[:2]
    p = H.run(lambda b, m: (-5, -1, 2), samples=s)
    assert (p.N_hat == 0).all() and (p.lo == 0).all()
    p = H.run(lambda b, m: (np.nan, 0, 0), samples=s)
    assert (p.error != "").all()


def test_public_meta_has_no_answers():
    raw = dict(epsg=32635, bounds=[462000.0, 4328000.0, 463280.0, 4329280.0], date="20190418", datetime=None,
               sample_id="P1-PLP2019-20190418", set="A1", background="ship", item_id="S2B_35SMD_20190418_0_L2A")
    m = H._public_meta(raw, np.ones((128, 128), bool), True, "astar")
    assert set(m) == {"roi", "roi_area_km2", "pixel_m", "epsg", "bounds", "lon", "lat", "date", "datetime", "has_scl"}
    assert 26 < m["lon"] < 27 and 39 < m["lat"] < 40
    assert H._marida_date("1-12-19") == "20191201"


def test_record_and_filter(tmp_path):
    s = _toy()
    p = H.per_sample(H.run(lambda b, m: (0, 0, 0), samples=s), s)
    r = H.score(p, n_boot=50)
    r.update(name="b2_zero", code_sha256="abc", runtime_s=1.0, sets_sha256="x", set_version="vT", gate_product="n/a")
    f = tmp_path / "lb.csv"
    H.record(r, f)
    r2 = dict(r, set_version="vU")
    H.record(r2, f)
    lb = H.read_leaderboard(f)
    assert list(lb.columns) == H.LB_COLUMNS and len(lb) == 2
    assert len(H.read_leaderboard(f, version="vT")) == 1


def test_verify_sets_detects_change(tmp_path, monkeypatch):
    d = tmp_path / "vX"
    d.mkdir()
    (d / "manifest.csv").write_text("a,b\n1,2\n")
    (d / "labels.csv").write_text("sample_id,N_true\nx,1\n")
    monkeypatch.setattr(H, "set_dir", lambda cfg: d)
    cfg = dict(version="vX", frozen=dict(manifest_sha256=H.sha256_file(d / "manifest.csv"),
                                         labels_sha256=H.sha256_file(d / "labels.csv")))
    assert len(H.verify_sets(cfg)) == 16
    (d / "labels.csv").write_text("sample_id,N_true\nx,2\n")
    with pytest.raises(RuntimeError):
        H.verify_sets(cfg)


def test_paired_delta_vs_reference():
    s = _toy()
    p0 = H.per_sample(H.run(lambda b, m: (0, 0, 0), samples=s), s)
    ns = [0.0, 360.0, 1000.0, 3738.0]
    p1 = H.per_sample(H.run(lambda b, m: (ns[int(b["B1"][0, 0])] if m["roi_area_km2"] > 0.4 and
                                          m["roi_area_km2"] < 0.42 else 0, 0, 0), samples=s), s)
    r = H.score(p1, ref=p0, n_boot=500)
    assert r["dE1"][0] < 0 and r["dE1"][2] <= 0


@pytest.mark.skipif(not (ROOT / "data" / "discovery" / "v1" / "manifest.csv").exists(), reason="sets not built")
def test_frozen_sets_integrity():
    cfg = H.load_cfg()
    assert cfg["version"] == "v1"
    H.verify_sets(cfg)
    m = pd.read_csv(ROOT / "data" / "discovery" / "v1" / "manifest.csv")
    ok = m[m.status == "ok"]
    assert (ok.set == "A1").sum() >= 13  # 6 PLP + 8 Maathuis + Themistocleous (fetched)
    assert set(ok.set) == {"A1", "A2", "A3", "A4"}
