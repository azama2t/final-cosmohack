"""Контрольные примеры концентрации шт./км² и группированных сплитов (кейс, Т3/Т4)."""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from macroplastic.case import concentration as C  # noqa: E402
from macroplastic.case import selection as S  # noqa: E402
from macroplastic.case import splits as P  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[1] / "task" / "macroplastic_marine_samples.csv"


# ---------------------------------------------------------------- постановка
def test_control_example_12_over_020_is_60():
    r = C.concentration(12, 0.20)
    assert r.value == pytest.approx(60.0)
    assert r.status == C.STATUS_DETECTED
    assert r.unit == "шт./км²"


def test_control_example_mae_15():
    assert C.mae([60.0], [75.0]) == pytest.approx(15.0)
    assert C.rmse([60.0], [75.0]) == pytest.approx(15.0)
    assert C.median_ae([60.0], [75.0]) == pytest.approx(15.0)


def test_poisson_interval_12_over_020():
    r = C.concentration(12, 0.20)
    assert r.lower == pytest.approx(31.0, abs=0.05)
    assert r.upper == pytest.approx(104.8, abs=0.05)
    assert r.lower < r.value < r.upper


# ---------------------------------------------------------------- единицы
def test_units_m2_ha_km2():
    assert C.area_to_km2(200_000, "m2") == pytest.approx(0.20)
    assert C.area_to_km2(20, "ha") == pytest.approx(0.20)
    assert C.area_to_km2(0.2, "km2") == pytest.approx(0.20)
    with pytest.raises(ValueError):
        C.area_to_km2(1, "acre")


def test_strip_area_km_times_m():
    # 25.143 км × 10 м = 0.25143 км² (строка MPL-0001, в источнике 0.251)
    assert C.strip_area_km2(25.143, 10) == pytest.approx(0.25143)
    assert C.strip_area_km2(20, 10) == pytest.approx(0.20)
    assert C.strip_area_km2(20_000, 10, length_unit="m") == pytest.approx(0.20)
    a = C.strip_area_km2(20, 10)
    assert C.concentration(12, a).value == pytest.approx(60.0)
    assert C.strip_area_km2(None, 10) is None
    assert C.strip_area_km2(-1, 10) is None


# ---------------------------------------------------------------- некорректные входы
@pytest.mark.parametrize("n,a", [(5, 0), (5, 0.0), (5, -0.1), (None, 0.2), (5, None),
                                 (float("nan"), 0.2), (5, float("inf")), (-1, 0.2), ("x", 0.2)])
def test_bad_inputs_insufficient_no_division(n, a):
    r = C.concentration(n, a)
    assert r.status == C.STATUS_INSUFFICIENT
    assert r.value is None and r.lower is None and r.upper is None


def test_zero_count_not_detected_with_upper_bound():
    r = C.concentration(0, 0.5)
    assert r.value == 0.0
    assert r.status == C.STATUS_NOT_DETECTED
    assert r.lower == 0.0
    assert r.upper == pytest.approx(3.6889 / 0.5, rel=1e-3)
    lo, hi = C.poisson_count_interval(0)
    assert lo == 0.0 and hi == pytest.approx(3.6889, rel=1e-3)


def test_model_statuses():
    assert C.model_status(75, "unconfirmed") == C.STATUS_RESEARCH
    assert C.model_status(75, "none") == C.STATUS_UNAVAILABLE
    assert C.model_status(None, "confirmed") == C.STATUS_UNAVAILABLE
    assert C.model_status(75, "confirmed") == C.STATUS_DETECTED
    assert C.model_status(0, "confirmed") == C.STATUS_NOT_DETECTED
    assert set(C.ALL_STATUSES) >= {"обнаружено", "не обнаружено", "недостаточно данных",
                                   "исследовательская оценка", "концентрация недоступна"}


def test_metrics_validation_and_log1p_with_zeros():
    with pytest.raises(ValueError):
        C.mae([], [])
    with pytest.raises(ValueError):
        C.mae([1, 2], [1])
    with pytest.raises(ValueError):
        C.mae([1, np.nan], [1, 2])
    # нули допустимы для log1p-ошибки (MAPE тут не определён)
    assert C.log1p_mae([0.0, 0.0], [0.0, math.e - 1]) == pytest.approx(0.5)
    m = C.all_metrics([60, 0], [75, 0])
    assert m["mae"] == pytest.approx(7.5) and m["n"] == 2


# ---------------------------------------------------------------- агрегация
def test_aggregate_ratio_of_sums_not_mean_of_densities():
    df = pd.DataFrame({"event": ["a", "a", "b", "c"],
                       "n_items": [1, 5, 12, 3],
                       "area_km2": [0.01, 1.0, 0.20, 0.0]})
    g = C.aggregate(df, "event").set_index("event")
    assert g.loc["a", "concentration_items_km2"] == pytest.approx(6 / 1.01)
    assert g.loc["a", "concentration_items_km2"] != pytest.approx((100 + 5) / 2)
    assert g.loc["b", "concentration_items_km2"] == pytest.approx(60.0)
    assert g.loc["c", "status"] == C.STATUS_INSUFFICIENT and g.loc["c", "n_invalid"] == 1
    lo, hi = C.poisson_count_interval(6)
    assert g.loc["a", "upper"] == pytest.approx(hi / 1.01)


# ---------------------------------------------------------------- отбор и утечки
@pytest.fixture(scope="module")
def samples():
    if not SAMPLES.exists():
        pytest.skip("нет task/macroplastic_marine_samples.csv")
    return S.load_samples(SAMPLES)


def test_selection_default_profile_and_rejections(samples):
    cfg = S.load_config()
    acc, rej = S.select(samples, cfg)
    assert len(acc) + len(rej) == len(samples)
    assert rej["reason"].ne("").all()
    assert acc["target_scope"].nunique() == 1 and acc["measurement_profile"].nunique() == 1
    assert acc["event_id"].nunique() == 63


def test_consistency_c_equals_n_over_a(samples):
    chk = S.consistency_check(samples)
    assert len(chk) > 0
    assert chk["match"].mean() == 1.0


def test_no_leak_features():
    S.assert_no_leak(["latitude", "longitude", "wind_speed_kn", "sea_state_beaufort"])
    for bad in ["concentration_items_km2", "items_count", "density_numerator_items",
                "reported_concentration_items_km2", "parent_concentration_items_km2",
                "source_object_filtered_items", "source_reported_total_items"]:
        with pytest.raises(ValueError):
            S.assert_no_leak(["latitude", bad])


# ---------------------------------------------------------------- сплиты
def _toy():
    # два события по 2 строки рядом (одна цепочка), одно далеко, одно далеко по времени
    return pd.DataFrame({
        "sample_id": list("ABCDEFG"),
        "event_id": ["e1", "e1", "e2", "e2", "e3", "e4", "e5"],
        "latitude": [30.0, 30.0, 30.3, 30.3, 10.0, 30.0, -20.0],
        "longitude": [-60.0, -60.0, -60.0, -60.0, 0.0, -60.0, 100.0],
        "date_utc": ["2015-04-01"] * 4 + ["2015-04-01", "2015-06-01", "2015-04-01"],
        "datetime_start_iso": [None] * 7,
    })


def test_spatiotemporal_groups_components():
    g = P.spatiotemporal_groups(_toy(), radius_km=50, days=1)
    assert g[0] == g[1] == g[2] == g[3]      # e1–e2 ~33 км, тот же день
    assert g[4] != g[0] and g[5] != g[0]      # далеко / через 2 месяца
    assert len(set(g)) == 4


@pytest.mark.parametrize("method", ["event", "daycell", "st", "scene"])
def test_split_no_group_overlap(method):
    df = _toy()
    scene = [None, None, None, None, "S2A_X", "S2A_X", None] if method == "scene" else None
    sp = P.make_split(df, method, n_folds=3, seed=1, radius_km=50, days=1, scene_id=scene)
    P.assert_no_overlap(sp)
    for ev, g in sp.groupby("event_id"):
        assert g["fold"].nunique() == 1
    if method == "scene":
        assert sp.loc[4, "fold"] == sp.loc[5, "fold"]
    chk = P.check_split(sp, df)
    assert (chk.shared_events == 0).all() and (chk.shared_groups == 0).all()


def test_split_deterministic_and_real_profile(samples):
    acc, _ = S.select(samples, S.load_config())
    a = P.make_split(acc, "st", n_folds=5, seed=42, radius_km=100, days=2)
    b = P.make_split(acc, "st", n_folds=5, seed=42, radius_km=100, days=2)
    assert a.equals(b)
    chk = P.check_split(a, acc)
    assert chk.shared_events.sum() == 0 and chk.shared_groups.sum() == 0
    # ни одна пара test/train не ближе R при |Δt| ≤ T (иначе они были бы в одной группе)
    assert (chk.min_dist_km_within_1d > 100).all()


def test_assert_no_overlap_detects_leak():
    sp = pd.DataFrame({"sample_id": ["A", "B"], "event_id": ["e1", "e1"], "scene_id": [None, None],
                       "group": ["g1", "g1"], "fold": [0, 1]})
    with pytest.raises(AssertionError):
        P.assert_no_overlap(sp)


# ---------------------------------------------------------------- основной сплит, утечки, правила
def test_main_split_no_cruise_day_overlap(samples):
    cfg = S.load_config()
    ms = cfg["main_split"]
    assert ms["method"] == "route" and ms["buffer_days"] >= 1.0
    for prof in cfg["profiles"]:
        acc, _ = S.select(samples, cfg, prof)
        sp = P.make_split(acc, ms["method"], seed=ms["seed"], k_blocks=ms["k_blocks"])
        P.assert_no_overlap(sp, cruise_day=True)
        chk = P.check_split(sp, acc)
        assert (chk.shared_cruise_days == 0).all() and (chk.shared_events == 0).all()
        assert sp["fold"].nunique() == ms["k_blocks"]
        # участки непрерывны по времени: максимум даты блока i < минимума блока i+1
        dates = acc.set_index("sample_id").loc[sp.sample_id, "date_utc"].to_numpy()
        rng_ = pd.DataFrame({"fold": sp.fold, "d": dates}).groupby("fold").d.agg(["min", "max"]).sort_index()
        assert (rng_["max"].to_numpy()[:-1] < rng_["min"].to_numpy()[1:]).all()
        # буфер: в train нет записей ближе 1 сут к test
        t = P.times_days(acc.set_index("sample_id").loc[sp.sample_id].reset_index())
        for f in sp.fold.unique():
            tr = P.buffered_train_mask(sp, acc, f, ms["buffer_days"])
            te = (sp.fold == f).to_numpy()
            assert np.abs(t[tr][:, None] - t[te][None, :]).min() > ms["buffer_days"]


def test_allowed_features_exclude_explicit_leaks():
    explicit = ["concentration_value_orig", "concentration_unit_orig", "concentration_items_km2",
                "concentration_g_km2", "items_count", "density_numerator_items",
                "source_object_filtered_items", "source_reported_total_items",
                "reported_concentration_items_km2", "reported_concentration_g_km2",
                "parent_sample_id", "parent_concentration_items_km2"]
    assert not set(explicit) & set(S.ALLOWED_FEATURES)
    assert all(S.is_leak(c) for c in explicit + S.LEAK_EXPLICIT)
    S.assert_no_leak(S.ALLOWED_FEATURES)


def test_team_rules_reasons(samples):
    cfg = S.load_config()
    acc, rej = S.select(samples, cfg)
    r = rej.set_index("sample_id").reason
    sm = samples.set_index("sample_id")
    assert r[sm.record_type == "item_observation"].str.startswith("item_observation").all()
    assert (sm.record_type == "item_observation").sum() == 337
    all_litter = sm.index[sm.target_scope == "all_litter"]
    assert r[all_litter].str.contains("НЕ пластик").all()
    s1a = sm.index[(sm.measurement_profile == "S1_aerial_GT50") & (sm.target_scope == "total_plastic")]
    assert len(s1a) == 16 and r[s1a].str.contains("не профиль").all()
    assert not acc.target_scope.isin(["all_litter", "fisheries_litter_category", "object_context"]).any()
