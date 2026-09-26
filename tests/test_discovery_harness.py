"""Discovery harness v2 (scripts/discovery/harness.py): LOCO by campaign, abstention, false zones, A4 recall, A5 rule,
leaderboard, frozen sets."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "discovery"))
import harness as H  # noqa: E402

NS = [0.0, 360.0, 1000.0, 3738.0]
CAMPS = ["Maathuis2026", "Maathuis2026", "PLP2019", "PLP2018"]


def _s(sid, set_, group, N=np.nan, c=np.nan, bg=None, roi_px=64 * 64, val=0.0, kind="synthetic", fold=None,
       cover=np.nan, camp=None):
    roi = np.zeros((128, 128), bool)
    roi.ravel()[:roi_px] = True
    bands = {b: np.full((128, 128), val, np.float32) for b in H.BANDS11}
    bands["SCL"] = np.zeros((128, 128), np.uint8)
    meta = dict(roi=roi, roi_area_km2=roi_px * 1e-4, pixel_m=10.0, epsg=32635, bounds=[0, 0, 1280, 1280], lon=0.0,
                lat=0.0, date="20190418", datetime=None, has_scl=False)
    return H.Sample(sid, set_, group, kind, bands, meta,
                    dict(N_true=N, obs_density_km2=c, background=bg, cover_m2=cover, campaign=camp), fold or group)


def _toy():
    a1 = [_s(f"a{i}", "A1", f"d{i}", N=n, val=float(i), kind="astar", fold=CAMPS[i], camp=CAMPS[i])
          for i, n in enumerate(NS)]
    a1c = [_s("c0", "A1", "dc0", kind="astar_cover", fold="PLP2021", cover=616.0, camp="PLP2021", val=9.0)]
    a2 = [_s(f"f{i}", "A2", f"f{i}", c=c, roi_px=128 * 128) for i, c in enumerate([0.0, 4.0, 50.0])]
    a3 = [_s(f"w{i}", "A3", f"w{i}", N=0.0, bg=b, roi_px=32 * 32) for i, b in enumerate(["ship", "cloud", "water"])]
    a4 = [_s(f"z{i}", "A4", f"z{i}", roi_px=40) for i in range(2)]
    return a1 + a1c + a2 + a3 + a4


def _eval(pred, fit=None, cover=None, ref=None, n_boot=200):
    s = _toy()
    preds, tiles = H.run(pred, fit, s, cover)
    p = H.per_sample(preds, s)
    zt = H.zones_table(tiles, None)
    return p, zt, H.score(p, zt, ref, n_boot=n_boot)


def test_zero_method():
    p, zt, r = _eval(lambda b, m: (0, 0, 0))
    e1 = np.mean(np.log10(np.array(NS) + 1))
    assert r["point"]["E1"] == pytest.approx(e1) and r["point"]["B1"] == pytest.approx(-e1)
    assert r["point"]["ABST2"] == 1.0 and r["point"]["FP3"] == 0.0 and r["point"]["R4"] == 0.0
    assert r["point"]["Z3"] == 0.0 and np.isnan(r["point"]["Ec"])
    assert r["n"]["A1"] == 4 and r["n"]["A1_cover"] == 1


def test_abstention_is_correct_on_a2_and_not_a_detection():
    p, zt, r = _eval(lambda b, m: None)
    assert r["point"]["ABST2"] == 1.0 and r["point"]["R4"] == 0.0 and r["point"]["FP3"] == 0.0
    assert r["n_abstain"] == len(p) and r["point"]["C1"] == 0.0  # abstention never covers N on A1


def test_numbers_everywhere_are_penalised():
    p, zt, r = _eval(lambda b, m: (5000.0, 4000.0, 6000.0))
    assert r["point"]["ABST2"] == 0.0 and r["point"]["FP3"] == 1.0 and r["point"]["R4"] == 1.0
    assert r["point"]["G2"] == 1.0  # 5000 / 1.64 km² ≥ 1000 while c < 100
    # every tile positive -> one 4x4 connected zone per A2/A3 window: 6 zones on 6 x 1.6384 km²
    assert r["point"]["Z3"] == pytest.approx(6 / (6 * 1.6384))
    assert r["point"]["B1"] > 0


def test_loco_fit_never_sees_heldout_campaign():
    seen, held = [], []

    def fit(train):
        seen.append(sorted({t["campaign"] for t in train}))

    def pred(b, m):
        held.append((float(b["B1"][0, 0]), seen[-1] if seen else None))
        return 0, 0, 0

    H.run(pred, fit, _toy(), tile_scan=False)
    for v, camps in held[:5]:
        if v == 9.0:
            assert "PLP2021" not in camps
        else:
            assert CAMPS[int(v)] not in camps
    assert seen[-1] == sorted(set(CAMPS) | {"PLP2021"})  # final fit on all A1 campaigns
    assert len(seen) == len(set(CAMPS)) + 1 + 1          # one per campaign fold (incl. PLP2021) + final


def test_predict_cover_scored_on_plp2021():
    p, zt, r = _eval(lambda b, m: (0, 0, 0), cover=lambda b, m: (616.0, 500.0, 700.0))
    assert r["point"]["Ec"] == pytest.approx(0.0)


def test_zone_components_and_types():
    tiles = pd.DataFrame([dict(sample_id="x", set="A3", group="g", tile_r=r, tile_c=c, pos=(r, c) in {(0, 0), (0, 1), (3, 3)},
                               error="") for r in range(4) for c in range(4)])
    types = pd.DataFrame([dict(sample_id="x", tile_r=r, tile_c=c, type="ship" if (r, c) == (0, 1) else
                               ("debris" if (r, c) == (2, 2) else "water")) for r in range(4) for c in range(4)])
    zt = H.zones_table(tiles, types)
    row = zt.iloc[0]
    assert row.zones == 2 and row.zones_ship == 1 and row.zones_water == 1
    assert row.area_km2 == pytest.approx(15 * 0.1024)  # the 'debris' tile is excluded


def test_errors_negative_nan():
    s = _toy()[:2]
    p, _ = H.run(lambda b, m: (-5, -1, 2), samples=s, tile_scan=False)
    assert (p.N_hat == 0).all() and (p.lo == 0).all()
    p, _ = H.run(lambda b, m: (np.nan, 0, 0), samples=s, tile_scan=False)
    assert (p.error != "").all()


def test_public_meta_has_no_answers():
    raw = dict(epsg=32635, bounds=[462000.0, 4328000.0, 463280.0, 4329280.0], date="20190418", datetime=None,
               sample_id="P1-PLP2019-20190418", set="A1", background="ship", item_id="S2B_35SMD_20190418_0_L2A")
    m = H._public_meta(raw, np.ones((128, 128), bool), True, "astar")
    assert set(m) == {"roi", "roi_area_km2", "pixel_m", "epsg", "bounds", "lon", "lat", "date", "datetime", "has_scl"}
    assert 26 < m["lon"] < 27 and 39 < m["lat"] < 40
    assert H._marida_date("1-12-19") == "20191201"


def test_natural_counter_rule():
    assert "ни один метод" in H.natural_counter_verdict(0)
    assert "ни один метод" in H.natural_counter_verdict(4)
    assert "ни один метод" not in H.natural_counter_verdict(5)


def test_record_and_filter(tmp_path):
    p, zt, r = _eval(lambda b, m: (0, 0, 0), n_boot=50)
    r.update(name="b2_zero", code_sha256="abc", runtime_s=1.0, sets_sha256="x", set_version="vT", gate_product="n/a",
             n_a5=0)
    f = tmp_path / "lb.csv"
    H.record(r, f)
    H.record(dict(r, set_version="vU"), f)
    lb = H.read_leaderboard(f)
    assert list(lb.columns) == H.LB_COLUMNS and len(lb) == 2
    assert len(H.read_leaderboard(f, version="vT")) == 1
    assert lb.natural_counter.iloc[0].startswith("нет")


def test_paired_delta_vs_reference():
    p0, _, _ = _eval(lambda b, m: (0, 0, 0))

    def oracle(b, m):
        v = int(b["B1"][0, 0])
        return (NS[v], NS[v], NS[v]) if v < 4 and abs(m["roi_area_km2"] - 0.4096) < 1e-6 else (0, 0, 0)

    _, _, r = _eval(oracle, ref=p0, n_boot=500)
    assert r["point"]["E1"] == pytest.approx(0.0) and r["point"]["C1"] == 1.0
    assert r["dE1"][0] < 0 and r["dE1"][2] <= 0


def test_verify_sets_detects_change(tmp_path, monkeypatch):
    d = tmp_path / "vX"
    d.mkdir()
    for n, t in (("manifest.csv", "a,b\n1,2\n"), ("labels.csv", "sample_id,N_true\nx,1\n"), ("tile_types.csv", "t\n1\n")):
        (d / n).write_text(t)
    monkeypatch.setattr(H, "set_dir", lambda cfg: d)
    cfg = dict(version="vX", frozen={f"{k}_sha256": H.sha256_file(d / f"{k}.csv") for k in ("manifest", "labels", "tile_types")})
    assert len(H.verify_sets(cfg)) == 16
    (d / "tile_types.csv").write_text("t\n2\n")
    with pytest.raises(RuntimeError):
        H.verify_sets(cfg)


@pytest.mark.skipif(not (ROOT / "data" / "discovery" / "v2" / "manifest.csv").exists(), reason="sets not built")
def test_frozen_sets_integrity():
    cfg = H.load_cfg()
    assert cfg["version"] == "v2" and cfg["sets"]["A5"]["n"] == 0
    H.verify_sets(cfg)
    m = pd.read_csv(ROOT / "data" / "discovery" / "v2" / "manifest.csv")
    ok = m[m.status == "ok"]
    a1 = ok[ok.set == "A1"]
    assert (a1.kind == "astar").sum() == 15 and (a1.kind == "astar_cover").sum() >= 14
    assert set(a1.fold) == {"PLP2018", "PLP2019", "Maathuis2026", "Themistocleous2020", "PLP2021"}
    v1 = H.load_cfg(ROOT / "configs" / "discovery_sets.yaml")
    assert v1["version"] == "v1"
    H.verify_sets(v1)  # v1 is kept and still intact
