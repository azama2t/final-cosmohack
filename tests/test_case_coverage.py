"""Спутниковая доля покрытия (м²/км²), калибровочные расчёты и исследовательский сценарий (L93)."""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from macroplastic.case import coverage as V  # noqa: E402


def test_coverage_counts_only_valid_water():
    water = np.zeros((10, 10), bool)
    water[:5] = True                      # 50 px = 5000 м² = 0.005 км²
    det = np.zeros((10, 10), bool)
    det[0, 0] = det[0, 1] = True          # 2 px на воде
    det[9, 9] = True                      # на суше/облаке — не считается
    r = V.coverage(det, water)
    assert r.detected_px == 2 and r.water_px == 50
    assert r.detected_area_m2 == pytest.approx(200.0)
    assert r.water_area_km2 == pytest.approx(0.005)
    assert r.fraction == pytest.approx(0.04)
    assert r.m2_per_km2 == pytest.approx(40000.0)      # 200 м² / 0.005 км²
    assert r.floor_m2_per_km2 == pytest.approx(20000.0)  # 1 пиксель на 0.005 км²
    assert r.status == V.STATUS_OK


def test_coverage_zero_and_no_water():
    water = np.ones((4, 4), bool)
    r = V.coverage(np.zeros((4, 4), bool), water)
    assert r.m2_per_km2 == 0.0 and r.status == V.STATUS_ZERO
    r = V.coverage(np.ones((4, 4), bool), np.zeros((4, 4), bool))
    assert r.fraction is None and r.m2_per_km2 is None and r.status == V.STATUS_INSUFFICIENT


def test_coverage_shape_mismatch():
    with pytest.raises(ValueError):
        V.coverage(np.zeros((2, 2)), np.zeros((3, 3)))


def test_detection_mask_uint8_threshold_and_min_px():
    prob = np.zeros((6, 6), np.uint8)
    prob[1, 1] = 161                       # 161/255 = 0.631 ≥ 0.63
    prob[1, 2] = 160                       # 0.627 < 0.63
    prob[4, 4] = prob[4, 5] = 255          # объект из 2 px
    water = np.ones((6, 6), bool)
    m1 = V.detection_mask(prob, water, 0.63, min_px=1)
    assert m1.sum() == 3
    m2 = V.detection_mask(prob, water, 0.63, min_px=2)
    assert m2.sum() == 2 and m2[4, 4] and not m2[1, 1]
    water[4, 3] = False                    # объект касается непригодного пикселя
    m3 = V.detection_mask(prob, water, 0.63, min_px=2, drop_touching_invalid=True)
    assert m3.sum() == 0


def test_coverage_by_threshold_monotone_and_range():
    rng = np.random.default_rng(0)
    prob = rng.random((50, 50)).astype(np.float32)
    water = np.ones((50, 50), bool)
    rows = V.coverage_by_threshold(prob, water, [0.5, 0.7, 0.9])
    vals = [r["m2_per_km2"] for r in rows]
    assert vals[0] > vals[1] > vals[2]
    rg = V.coverage_range(rows)
    assert rg["min"] == pytest.approx(vals[2]) and rg["max"] == pytest.approx(vals[0])
    assert rows[0]["threshold"] == 0.5 and rows[0]["n_objects"] >= 1


def test_threshold_band():
    curve = [[0.1, 0.80], [0.3, 0.905], [0.5, 0.91], [0.7, 0.912], [0.9, 0.85]]
    b = V.threshold_band(curve, tol=0.01)
    assert b["best_threshold"] == 0.7 and b["lo"] == 0.3 and b["hi"] == 0.7 and b["n"] == 3


def test_detection_floor():
    # один объект из 2 пикселей на 0.8 км² воды = 200 м² / 0.8 км² = 250 ppm
    assert V.detection_floor_ppm(0.8, min_px=2) == pytest.approx(250.0)
    assert V.detection_floor_ppm(0.0) is None


def test_log_var_decomposition():
    c = [10.0, 20.0, 40.0, 80.0]
    n = [4, 4, 4, 4]
    d = V.log_var_decomposition(c, n)
    assert d["var_poisson"] == pytest.approx(0.25)
    assert d["var_lnC_total"] == pytest.approx(np.var(np.log(c), ddof=1))
    assert d["var_extra"] == pytest.approx(d["var_lnC_total"] - 0.25)


def test_n_pairs_formulas():
    # σ = 1, ×/÷2 при 95 %: (1.96/ln2)² = 7.99 → 8
    assert V.n_pairs_for_factor(1.0, 2.0) == 8
    # r = 0.5, α = 0.05, мощность 0.8: классическое значение 29–30
    assert V.n_pairs_for_correlation(0.5) in (29, 30)
    assert V.n_pairs_for_correlation(0.3) in (84, 85)
    assert V.slope_se(1.0, 1.0, 101) == pytest.approx(0.1)
    # интервал предсказания ×/÷2 недостижим при σ = 0.5 > ln2/1.96 = 0.354
    assert V.n_pairs_for_prediction(0.5, 2.0) is None
    assert V.n_pairs_for_prediction(0.3, 2.0) >= 2


def test_items_from_area_research_range():
    r = V.items_from_area_research(100.0, (0.2, 1.0), (0.001, 0.01))
    assert r["items_min"] == pytest.approx(100 * 0.2 / 0.01)   # 2000
    assert r["items_max"] == pytest.approx(100 * 1.0 / 0.001)  # 100000
    assert r["ratio_max_min"] == pytest.approx(50.0)
    assert "исследовательский" in r["label"]
    with pytest.raises(ValueError):
        V.items_from_area_research(100.0, (0.0, 1.0), (0.001, 0.01))
