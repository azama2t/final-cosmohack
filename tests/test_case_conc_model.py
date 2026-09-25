"""Модель концентрации: отложенный test концентрации, утечки в X, интервалы, детерминизм (Т3/Т4, О2).

Метки отложенного test здесь не читаются: проверки состава — по составу (sample_id, event_id, день рейса),
прогон кода final_test_conc — на псевдо-test, вырезанном из dev.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "case"))

from macroplastic.case import conc_models as M  # noqa: E402
from macroplastic.case import selection as S  # noqa: E402
from macroplastic.case import splits as P  # noqa: E402

PROFILES = ["S2_visual_total_plastic", "S1_trawl_total_plastic"]
CFG = S.load_config()
FZ = CFG["final_test"]


@pytest.fixture(scope="module")
def samples():
    return S.load_samples()


def _ft(prof):
    """Состав отложенного test: отслеживаемая копия reports/case_splits (data/ в .gitignore)."""
    return pd.read_csv(ROOT / "reports" / "case_splits" / f"final_test_{prof}.csv")


def _composition(samples, prof):
    acc, _ = S.select(samples, CFG, prof)
    return acc.drop(columns=["target"])


# ---------------------------------------------------------------- отложенный test
@pytest.mark.parametrize("prof", PROFILES)
def test_final_test_no_overlap_with_dev(samples, prof):
    acc = _composition(samples, prof)
    ft = _ft(prof)
    te, dv, bf = ft[ft.role == "test"], ft[ft.role == "dev"], ft[ft.role == "buffer"]
    assert len(te) and len(dv) and len(te) + len(dv) + len(bf) == len(acc)
    assert not set(te.event_id) & set(dv.event_id)
    assert not set(te.cruise_day) & set(dv.cruise_day)
    assert not set(te.date_utc) & set(dv.date_utc)
    assert not set(bf.event_id) & (set(te.event_id) | set(dv.event_id))
    # ~20 % событий, один непрерывный участок маршрута
    assert 0.15 <= te.event_id.nunique() / acc.event_id.nunique() <= 0.30
    assert te.block.nunique() == 1
    d_all = sorted(pd.to_datetime(ft.date_utc).unique())
    d_te = sorted(pd.to_datetime(te.date_utc).unique())
    i0 = d_all.index(d_te[0])
    assert d_all[i0:i0 + len(d_te)] == d_te
    # буфер: ближайшее время test ↔ dev > 1 сут
    a = acc.set_index("sample_id")
    t_te, t_dv = P.times_days(a.loc[te.sample_id]), P.times_days(a.loc[dv.sample_id])
    assert np.abs(t_te[:, None] - t_dv[None, :]).min() > FZ["buffer_days"]


@pytest.mark.parametrize("prof", PROFILES)
def test_final_test_frozen_sha_and_rule(samples, prof):
    acc = _composition(samples, prof)
    ft = P.final_test_split(acc, prof, seed=FZ["seed"], k_blocks=FZ["k_blocks"], buffer_days=FZ["buffer_days"])
    exp = FZ["profiles"][prof]
    assert P.composition_sha256(ft, "test") == exp["test_sha256"]
    assert P.composition_sha256(ft, "dev") == exp["dev_sha256"]
    saved = _ft(prof)
    assert P.composition_sha256(saved, "test") == exp["test_sha256"]
    copy = pd.read_csv(ROOT / "reports" / "case_splits" / f"final_test_{prof}.csv")
    assert copy.equals(saved)
    # правило не зависит от меток: состав тот же при перемешанной целевой колонке
    shuffled = acc.assign(concentration_items_km2=np.random.default_rng(0).permutation(
        pd.to_numeric(acc.concentration_items_km2).to_numpy()))
    ft2 = P.final_test_split(shuffled, prof, seed=FZ["seed"], k_blocks=FZ["k_blocks"], buffer_days=FZ["buffer_days"])
    assert P.composition_sha256(ft2, "test") == exp["test_sha256"]


@pytest.mark.parametrize("prof", PROFILES)
def test_dev_part_excludes_test(prof):
    parts = M.load_profile_parts(prof)
    dev = parts["dev"]
    ft = _ft(prof)
    assert set(dev.sample_id) == set(ft[ft.role == "dev"].sample_id)
    assert (dev.role == "dev").all()


def test_dev_predictions_only_dev_events():
    pr = pd.read_csv(ROOT / "reports" / "case_conc" / "dev_predictions.csv")
    for prof in PROFILES:
        ft = _ft(prof)
        ids = set(pr[pr.profile == prof].sample_id)
        assert ids == set(ft[ft.role == "dev"].sample_id)
        assert not ids & set(ft[ft.role != "dev"].sample_id)


# ---------------------------------------------------------------- утечки
def test_no_leak_fields_in_X():
    S.assert_no_leak(M.RAW_INPUTS)
    parts = M.load_profile_parts(PROFILES[0])
    for prof in PROFILES:
        dev = M.load_profile_parts(prof)["dev"]
        X = M.FeatureSpec.fit(M.base_features(dev)).transform(M.base_features(dev))
        assert not set(X.columns) & set(S.LEAK_EXPLICIT)
        assert not any(S.is_leak(c) for c in X.columns)
        assert "sampled_area_km2" not in X.columns and "target" not in X.columns
    assert parts is not None


@pytest.mark.parametrize("name", M.MODEL_NAMES)
def test_predictions_ignore_leak_columns(name):
    """Порча всех утечных полей test-строк не меняет прогноз (модель их не читает)."""
    dev = M.load_profile_parts(PROFILES[1])["dev"]
    tr, te = dev.iloc[:40].copy(), dev.iloc[40:].copy()
    m = M.fit_model(name, tr)
    p0 = m.predict(te)
    bad = te.copy()
    for c in S.LEAK_EXPLICIT + ["target", "quality_flags", "notes", "calculation_method"]:
        if c in bad:
            bad[c] = 1e6 if pd.api.types.is_numeric_dtype(bad[c]) else "x"
    np.testing.assert_allclose(m.predict(bad), p0)


# ---------------------------------------------------------------- интервалы
def test_interval_contains_point_prediction():
    rng = np.random.default_rng(1)
    yhat = rng.uniform(0, 500, 200)
    for q in [(-1.0, 1.0), (0.3, 0.8), (-0.9, -0.2), (0.0, 0.0)]:
        qq = M.log_residual_quantiles(np.expm1(np.log1p(yhat) + rng.normal(q[0], 0.1, 200)), yhat)
        lo, hi = M.interval(yhat, qq)
        assert (lo <= yhat + 1e-12).all() and (yhat <= hi + 1e-12).all() and (lo >= 0).all()
    pr = pd.read_csv(ROOT / "reports" / "case_conc" / "dev_predictions.csv")
    assert (pr.lo <= pr.y_pred + 1e-9).all() and (pr.y_pred <= pr.hi + 1e-9).all()
    assert (pr.lo >= 0).all()


def test_selected_config_matches_cv_and_rule():
    cfg = yaml.safe_load((ROOT / "configs" / "case_conc_model.yaml").read_text(encoding="utf-8"))
    js = json.loads((ROOT / "reports" / "case_conc" / "dev_cv.json").read_text(encoding="utf-8"))
    for prof in PROFILES:
        t = pd.DataFrame(js["profiles"][prof]["table"])
        primary, _ = M.select_primary(t, cfg["protocol"]["baseline"])
        assert cfg["selected"][prof]["model"] == primary
        w = json.loads((ROOT / cfg["selected"][prof]["weights"]).read_text(encoding="utf-8"))
        assert w["model"]["name"] == primary
        q = cfg["selected"][prof]["interval"]
        assert q["q_lo"] <= 0 <= q["q_hi"]


# ---------------------------------------------------------------- детерминизм
@pytest.mark.parametrize("name", ["median", "knn5_log", "ridge_log", "poisson_glm", "lgbm_log"])
def test_nested_cv_deterministic(name):
    dev = M.load_profile_parts(PROFILES[0])["dev"]
    a = M.nested_cv(name, dev)
    b = M.nested_cv(name, dev)
    pd.testing.assert_frame_equal(a, b)


def test_dev_predictions_reproducible():
    """Повторный прогон CV даёт те же предсказания, что сохранены в reports/case_conc/dev_predictions.csv."""
    saved = pd.read_csv(ROOT / "reports" / "case_conc" / "dev_predictions.csv")
    cfg = yaml.safe_load((ROOT / "configs" / "case_conc_model.yaml").read_text(encoding="utf-8"))
    for prof in PROFILES:
        name = cfg["selected"][prof]["model"]
        dev = M.load_profile_parts(prof)["dev"]
        now = M.nested_cv(name, dev).set_index("sample_id").sort_index()
        old = saved[(saved.profile == prof) & (saved.model == name)].set_index("sample_id").sort_index()
        np.testing.assert_allclose(now.y_pred.to_numpy(), old.y_pred.to_numpy(), rtol=1e-9)
        np.testing.assert_allclose(now.hi.to_numpy(), old.hi.to_numpy(), rtol=1e-9)


# ---------------------------------------------------------------- final_test_conc.py (не читает test)
def test_final_test_script_refuses_existing_result(tmp_path):
    import final_test_conc as F
    out = tmp_path / "final_test.json"
    out.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        F.main(["--out", str(out), "--allow-early"])


def test_final_test_script_refuses_before_acceptance(tmp_path):
    import final_test_conc as F
    if datetime.now() >= datetime.fromisoformat(str(FZ["not_before"])):
        pytest.skip("после приёмки проверка времени не нужна (и прогон читал бы test)")
    with pytest.raises(SystemExit):
        F.main(["--out", str(tmp_path / "x.json")])
    assert not (tmp_path / "x.json").exists()


def test_final_test_code_path_on_pseudo_test(tmp_path, monkeypatch):
    """evaluate_profile на псевдо-test из dev (последний участок dev) — проверка кода без меток test."""
    import final_test_conc as F
    cfg = yaml.safe_load((ROOT / "configs" / "case_conc_model.yaml").read_text(encoding="utf-8"))
    prof = PROFILES[0]
    dev = M.load_profile_parts(prof)["dev"]
    fold = M.route_folds(dev, 5)
    fake = dev.copy()
    fake["role"] = np.where(fold == 4, "test", np.where(fold == 3, "buffer", "dev"))
    pseudo_dev = fake[fake.role == "dev"].reset_index(drop=True)
    monkeypatch.setattr(M, "load_profile_parts", lambda p, cfg=None: {"all": fake, "dev": pseudo_dev})
    sel = dict(cfg["selected"][prof])
    wpath = tmp_path / "w.json"
    wpath.write_text(json.dumps({"model": M.fit_model(sel["model"], pseudo_dev).to_dict()}, default=float),
                     encoding="utf-8")
    sel["weights"] = str(wpath)
    monkeypatch.setattr(F, "REPO", Path("/"))
    res, pr = F.evaluate_profile(prof, sel, cfg["protocol"])
    assert res["n_test"] == int((fold == 4).sum())
    assert set(pr.model) == {cfg["protocol"]["baseline"], *cfg["protocol"]["candidates"]}
    main = pr[pr.role == "main"]
    assert (main.lo <= main.y_pred + 1e-9).all() and (main.y_pred <= main.hi + 1e-9).all()
    assert np.isfinite(res["main"]["mae"]) and 0 <= res["main"]["coverage90"] <= 1
