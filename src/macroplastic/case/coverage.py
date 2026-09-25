"""Спутниковая площадь и доля покрытия подозрительного материала (м² на км² пригодной воды).

Это НЕ концентрация шт./км². Величина по аналогии с Cózar et al. 2024 (Nat. Commun. 15:4637,
doi:10.1038/s41467-024-48674-7): litter-windrow density LWD = a_lw / a_o · 10⁶ (ppm = м² на км²),
где a_lw — площадь пикселей, отнесённых к скоплению, a_o — наблюдённая пригодная поверхность моря
(без облаков; у Cózar ещё без участков с ветром > 5 м/с). Площадь считается целыми пикселями
(10 м × 10 м = 100 м²), доля мусора внутри пикселя не оценивается.

Функции:
- coverage()               — площадь маски детектора и доля покрытия пригодной воды;
- detection_mask()         — маска «prob ≥ порога» на пригодной воде + фильтр малых компонент;
- coverage_by_threshold()  — та же доля для набора порогов (диапазон по порогу детектора);
- threshold_band()         — пороги, равноценные рабочему по кривой F1 на валидации (задаётся ДО сцен);
- detection_floor_ppm()    — наименьшая ненулевая доля покрытия (один объект минимального размера);
- log_var_decomposition(), n_pairs_for_factor(), n_pairs_for_correlation(), slope_se() — калибровка;
- items_from_area_research() — ИССЛЕДОВАТЕЛЬСКИЙ сценарий «площадь / размер предмета = штуки»
  (только диапазон min–max с допущениями, не основное число).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Iterable, Optional, Sequence

import numpy as np
from scipy import ndimage
from scipy.stats import norm

PIXEL_AREA_M2 = 100.0            # Sentinel-2, 10 м × 10 м
UNIT_PPM = "м²/км² (ppm)"
STATUS_OK = "измерено"
STATUS_ZERO = "нет срабатываний"
STATUS_INSUFFICIENT = "недостаточно данных"
EIGHT = np.ones((3, 3), bool)

# Коды data/pairs/quality/<event>/quality.tif (scripts/case/pair_quality.py)
QUALITY_VALID_WATER = 1
RESEARCH_LABEL = "исследовательский сценарий (допущения), не измерение"


# ---------------------------------------------------------------- доля покрытия
@dataclass
class CoverageResult:
    detected_px: int
    water_px: int
    pixel_area_m2: float
    detected_area_m2: float
    water_area_km2: float
    fraction: Optional[float]        # доля пригодной воды под маской, 0..1
    m2_per_km2: Optional[float]      # = fraction · 10⁶ (ppm, как LWD у Cózar 2024)
    floor_m2_per_km2: Optional[float]  # наименьшая ненулевая величина на этой площади (1 пиксель)
    status: str
    unit: str = UNIT_PPM

    def to_dict(self) -> dict:
        return asdict(self)


def coverage(detected: np.ndarray, valid_water: np.ndarray, pixel_area_m2: float = PIXEL_AREA_M2) -> CoverageResult:
    """Доля покрытия = (пиксели маски на пригодной воде) / (пиксели пригодной воды).

    Пиксели маски вне пригодной воды не считаются (облако, суша, блик, край — не наблюдение).
    Пригодной воды нет → «недостаточно данных» (без деления на ноль). Ноль срабатываний на
    пригодной воде → «нет срабатываний»: это не «чисто», а «ниже предела обнаружения»
    (floor_m2_per_km2 — одна сработавшая клетка на этой площади)."""
    det = np.asarray(detected).astype(bool)
    water = np.asarray(valid_water).astype(bool)
    if det.shape != water.shape:
        raise ValueError(f"разные размеры маски и воды: {det.shape} vs {water.shape}")
    if not (pixel_area_m2 > 0):
        raise ValueError("площадь пикселя должна быть > 0")
    nw = int(water.sum())
    nd = int((det & water).sum())
    area_m2 = nd * float(pixel_area_m2)
    water_km2 = nw * float(pixel_area_m2) / 1e6
    if nw == 0:
        return CoverageResult(nd, 0, float(pixel_area_m2), area_m2, 0.0, None, None, None, STATUS_INSUFFICIENT)
    frac = nd / nw
    return CoverageResult(nd, nw, float(pixel_area_m2), area_m2, water_km2, frac, frac * 1e6,
                          1e6 / nw, STATUS_OK if nd > 0 else STATUS_ZERO)


def _as_prob(prob: np.ndarray) -> np.ndarray:
    """uint8 (P·255, как в prob.tif проекта) → float 0..1; float оставляем; NaN → 0."""
    p = np.asarray(prob)
    if p.dtype == np.uint8:
        return p.astype(np.float32) / 255.0
    return np.nan_to_num(p.astype(np.float32), nan=0.0)


def detection_mask(prob: np.ndarray, valid_water: np.ndarray, threshold: float, min_px: int = 1,
                   drop_touching_invalid: bool = False) -> np.ndarray:
    """prob ≥ threshold на пригодной воде; компоненты (8-связность) < min_px пикселей убираются;
    drop_touching_invalid — убрать компоненты, касающиеся непригодных пикселей (облако/суша/край)."""
    p = _as_prob(prob)
    water = np.asarray(valid_water).astype(bool)
    if p.shape != water.shape:
        raise ValueError(f"разные размеры prob и воды: {p.shape} vs {water.shape}")
    # uint8: сравнение как в grid.vectorize.threshold_u8 (наименьшее v с v/255 ≥ порога)
    if np.asarray(prob).dtype == np.uint8:
        raw = (np.asarray(prob) >= int(np.ceil(float(threshold) * 255.0 - 1e-6))) & water
    else:
        raw = (p >= float(threshold)) & water
    if min_px <= 1 and not drop_touching_invalid:
        return raw
    lab, n = ndimage.label(raw, structure=EIGHT)
    if n == 0:
        return raw
    keep = np.bincount(lab.ravel(), minlength=n + 1) >= int(max(min_px, 1))
    if drop_touching_invalid and (~water).any():
        near = ndimage.binary_dilation(~water, structure=EIGHT)
        keep[np.unique(lab[near & (lab > 0)])] = False
    keep[0] = False
    return keep[lab]


def n_components(mask: np.ndarray) -> int:
    return int(ndimage.label(np.asarray(mask).astype(bool), structure=EIGHT)[1])


def coverage_by_threshold(prob: np.ndarray, valid_water: np.ndarray, thresholds: Iterable[float],
                          min_px: int = 1, pixel_area_m2: float = PIXEL_AREA_M2,
                          drop_touching_invalid: bool = False) -> list[dict]:
    """Доля покрытия для каждого порога: [{threshold, detected_px, n_objects, m2_per_km2, ...}]."""
    out = []
    for t in thresholds:
        m = detection_mask(prob, valid_water, t, min_px=min_px, drop_touching_invalid=drop_touching_invalid)
        r = coverage(m, valid_water, pixel_area_m2).to_dict()
        r["threshold"] = round(float(t), 4)
        r["n_objects"] = n_components(m)
        out.append(r)
    return out


def coverage_range(rows: Sequence[dict]) -> dict:
    """min–max доли покрытия по набору порогов (строки coverage_by_threshold)."""
    v = [r["m2_per_km2"] for r in rows if r.get("m2_per_km2") is not None]
    if not v:
        return {"min": None, "max": None, "n_thresholds": 0}
    return {"min": float(min(v)), "max": float(max(v)), "n_thresholds": len(v)}


def threshold_band(curve: Sequence[Sequence[float]], tol: float = 0.01) -> dict:
    """Пороги, у которых F1 на валидации ≥ max F1 − tol («равноценные» рабочему).

    curve — [[порог, F1], ...] (weights/lgbm/meta.json: threshold_curve, MARIDA val).
    Правило фиксируется до расчёта на сценах: диапазон по порогу = разброс доли покрытия
    между порогами, которые валидация не различает."""
    arr = np.asarray(curve, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 2 or len(arr) == 0:
        raise ValueError("curve: [[порог, F1], ...]")
    best = float(arr[:, 1].max())
    ok = arr[arr[:, 1] >= best - tol]
    return {"best_f1": best, "best_threshold": float(arr[arr[:, 1].argmax(), 0]), "tol": tol,
            "lo": float(ok[:, 0].min()), "hi": float(ok[:, 0].max()), "n": int(len(ok))}


def detection_floor_ppm(water_area_km2: float, min_px: int = 1, pixel_area_m2: float = PIXEL_AREA_M2) -> Optional[float]:
    """Наименьшая ненулевая доля покрытия: один объект из min_px пикселей на water_area_km2."""
    if water_area_km2 is None or water_area_km2 <= 0:
        return None
    return min_px * pixel_area_m2 / water_area_km2


# ---------------------------------------------------------------- калибровка (расчёт числа пар)
def log_var_decomposition(conc: Sequence[float], n_items: Sequence[float]) -> dict:
    """Разложение дисперсии ln C между событиями: пуассоновская часть ≈ среднее 1/N (дельта-метод:
    Var(ln N) ≈ 1/N), остальное — «природная» изменчивость (пятнистость, дрейф, наблюдатель).
    Только события с N > 0 (ln 0 не определён)."""
    c = np.asarray(conc, float)
    n = np.asarray(n_items, float)
    ok = np.isfinite(c) & np.isfinite(n) & (c > 0) & (n > 0)
    if ok.sum() < 3:
        raise ValueError("нужно ≥ 3 события с N > 0")
    lc = np.log(c[ok])
    total = float(np.var(lc, ddof=1))
    pois = float(np.mean(1.0 / n[ok]))
    extra = max(total - pois, 0.0)
    return {"n_events": int(ok.sum()), "var_lnC_total": total, "sd_lnC_total": math.sqrt(total),
            "var_poisson": pois, "sd_poisson": math.sqrt(pois), "var_extra": extra, "sd_extra": math.sqrt(extra),
            "poisson_share": pois / total if total > 0 else None}


def n_pairs_for_factor(sigma_ln: float, factor: float, conf: float = 0.95) -> int:
    """Сколько независимых пар нужно, чтобы 95 % ДИ калибровочного множителя k (ln C = ln k + ln S + ε,
    ε ~ N(0, σ²)) был в пределах ×/÷ factor: n = ⌈(z·σ / ln factor)²⌉."""
    if sigma_ln < 0 or factor <= 1:
        raise ValueError("sigma ≥ 0, factor > 1")
    z = float(norm.ppf(0.5 + conf / 2))
    return max(int(math.ceil((z * sigma_ln / math.log(factor)) ** 2)), 2)


def n_pairs_for_correlation(r: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """Число пар, чтобы обнаружить корреляцию r (двусторонний тест, преобразование Фишера):
    n = ((z_{1−α/2} + z_{power}) / atanh r)² + 3."""
    if not (0 < abs(r) < 1):
        raise ValueError("0 < |r| < 1")
    za, zb = float(norm.ppf(1 - alpha / 2)), float(norm.ppf(power))
    return int(math.ceil(((za + zb) / math.atanh(abs(r))) ** 2 + 3))


def slope_se(sigma_resid: float, sd_x: float, n: int) -> float:
    """Стандартная ошибка наклона ln C ~ a + b·ln S: σ_ε / (sd_x · √(n−1))."""
    if n < 3 or sd_x <= 0:
        raise ValueError("n ≥ 3, sd_x > 0")
    return sigma_resid / (sd_x * math.sqrt(n - 1))


def n_pairs_for_prediction(sigma_resid: float, factor: float, conf: float = 0.95) -> Optional[int]:
    """Сколько пар нужно, чтобы 95 % интервал ПРЕДСКАЗАНИЯ одной новой пары был в ×/÷ factor.
    Интервал ≥ z·σ·√(1 + 1/n) > z·σ: если z·σ ≥ ln factor — недостижимо ни при каком n (None)."""
    z = float(norm.ppf(0.5 + conf / 2))
    lim = math.log(factor) / z
    if sigma_resid >= lim:
        return None
    return max(int(math.ceil(1.0 / ((lim / sigma_resid) ** 2 - 1))), 2) if sigma_resid > 0 else 2


# ---------------------------------------------------------------- исследовательский сценарий
def items_from_area_research(area_m2: float, fill_frac: tuple[float, float],
                             item_area_m2: tuple[float, float]) -> dict:
    """«Площадь / размер предмета = штуки» — ТОЛЬКО исследовательский сценарий.

    N = площадь_маски · f / a, f — доля пикселя, реально занятая мусором (Cózar 2024: обнаружение
    при покрытии пикселя около 20 %), a — площадь одного предмета (допущение о размерах).
    min = площадь·f_min/a_max, max = площадь·f_max/a_min. Предметы не перекрываются, все видимые
    предметы плавают на поверхности, маска — только мусор (без пены/водорослей) — всё это допущения."""
    f_lo, f_hi = map(float, fill_frac)
    a_lo, a_hi = map(float, item_area_m2)
    if not (0 < f_lo <= f_hi <= 1) or not (0 < a_lo <= a_hi):
        raise ValueError("0 < f_min ≤ f_max ≤ 1, 0 < a_min ≤ a_max")
    if area_m2 is None or area_m2 < 0:
        raise ValueError("площадь ≥ 0")
    return {"label": RESEARCH_LABEL, "area_m2": float(area_m2), "fill_frac": [f_lo, f_hi],
            "item_area_m2": [a_lo, a_hi], "items_min": area_m2 * f_lo / a_hi, "items_max": area_m2 * f_hi / a_lo,
            "ratio_max_min": (f_hi / f_lo) * (a_hi / a_lo)}
