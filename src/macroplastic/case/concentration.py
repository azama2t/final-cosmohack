"""Концентрация плавающего макромусора в шт./км²: C = N / A.

N — число предметов выбранной совокупности (заявленный профиль: целевая совокупность,
материал, размерный класс), A — обследованная площадь в км². Для полевой трансекты
A = длина (км) × ширина полосы (м) / 1000.

Контроль из постановки: 12 предметов на 0,20 км² → C = 60 шт./км²; модель дала 75 →
абсолютная ошибка 15 шт./км². Индекс (FDI и др.) или доля/площадь маски концентрацией
НЕ являются: для перехода к шт./км² нужна калибровка, поэтому функции ниже принимают
только число предметов и площадь.

Статусы результата (постановка, раздел «Статусы и выгрузка»):
- «обнаружено»           — C > 0 по полевому счёту или подтверждённой модели;
- «не обнаружено»        — N = 0 на корректной площади (ноль относится только к профилю);
- «недостаточно данных»  — нет N или A, A ≤ 0, N < 0, нечисловые значения (деления на ноль нет);
- «исследовательская оценка» — модельная оценка, перенос на участок не подтверждён;
- «концентрация недоступна» — модельной оценки нет или перенос неприменим.

Неопределённость полевой плотности: число предметов на обследованной площади считается
пуассоновским, точный (Гарвуд) интервал для N делится на A:
  N_low = χ²(α/2; 2N)/2 (0 при N = 0),  N_up = χ²(1−α/2; 2N+2)/2.
Для N = 0 и 95 % верхняя граница 3,689 предмета — ноль на малой площади не означает
«чисто». Интервал учитывает только счётную (пуассоновскую) изменчивость, а не ошибки
наблюдателя, ширины полосы и пропуски мелких предметов.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd
from scipy.stats import chi2

STATUS_DETECTED = "обнаружено"
STATUS_NOT_DETECTED = "не обнаружено"
STATUS_INSUFFICIENT = "недостаточно данных"
STATUS_RESEARCH = "исследовательская оценка"
STATUS_UNAVAILABLE = "концентрация недоступна"
ALL_STATUSES = (STATUS_DETECTED, STATUS_NOT_DETECTED, STATUS_INSUFFICIENT,
                STATUS_RESEARCH, STATUS_UNAVAILABLE)

UNIT = "шт./км²"

_AREA_TO_KM2 = {"km2": 1.0, "km²": 1.0, "м2": 1e-6, "m2": 1e-6, "m²": 1e-6, "м²": 1e-6,
                "ha": 0.01, "га": 0.01}
_LEN_TO_KM = {"km": 1.0, "км": 1.0, "m": 1e-3, "м": 1e-3, "nmi": 1.852, "nm": 1.852}


# ---------------------------------------------------------------- единицы
def _finite_number(x) -> Optional[float]:
    """float(x), если x — конечное число; иначе None (None, NaN, inf, строки)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def area_to_km2(value, unit: str = "km2") -> Optional[float]:
    """Перевод площади в км². м² → ×1e-6, га → ×0.01. Некорректное значение → None."""
    v = _finite_number(value)
    key = str(unit).strip().lower()
    if key not in _AREA_TO_KM2:
        raise ValueError(f"неизвестная единица площади: {unit!r}")
    return None if v is None else v * _AREA_TO_KM2[key]


def strip_area_km2(length, width, length_unit: str = "km", width_unit: str = "m") -> Optional[float]:
    """Площадь полосы обзора: длина × ширина, в км². По умолчанию длина в км, ширина в м:
    A[км²] = L[км] × W[м] / 1000. Пример: 25.143 км × 10 м = 0.25143 км².
    Нет значения / отрицательное → None (дальше это даёт «недостаточно данных»)."""
    L, W = _finite_number(length), _finite_number(width)
    if L is None or W is None or L < 0 or W < 0:
        return None
    lu, wu = str(length_unit).lower(), str(width_unit).lower()
    if lu not in _LEN_TO_KM or wu not in _LEN_TO_KM:
        raise ValueError(f"неизвестная единица длины: {length_unit!r}/{width_unit!r}")
    return L * _LEN_TO_KM[lu] * W * _LEN_TO_KM[wu]


# ---------------------------------------------------------------- интервал
def poisson_count_interval(n, conf: float = 0.95) -> tuple[float, float]:
    """Точный (Гарвуд) двусторонний интервал для среднего Пуассона по наблюдённому N."""
    v = _finite_number(n)
    if v is None or v < 0:
        raise ValueError(f"N должно быть числом ≥ 0, получено {n!r}")
    a = 1.0 - conf
    lo = 0.0 if v == 0 else float(chi2.ppf(a / 2, 2 * v) / 2)
    hi = float(chi2.ppf(1 - a / 2, 2 * v + 2) / 2)
    return lo, hi


# ---------------------------------------------------------------- расчёт C
@dataclass
class ConcentrationResult:
    value: Optional[float]          # шт./км² или None
    lower: Optional[float]
    upper: Optional[float]
    status: str
    n_items: Optional[float]
    area_km2: Optional[float]
    unit: str = UNIT
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def concentration(n_items, area_km2, conf: float = 0.95) -> ConcentrationResult:
    """Полевая концентрация C = N / A (шт./км²) с интервалом и статусом.

    A ≤ 0, пропуск, NaN/inf, N < 0 → статус «недостаточно данных», value=None
    (никакого деления на ноль). N = 0 при A > 0 → «не обнаружено», C = 0 и верхняя
    граница интервала 3.689/A (95 %)."""
    N, A = _finite_number(n_items), _finite_number(area_km2)
    if N is None or A is None:
        return ConcentrationResult(None, None, None, STATUS_INSUFFICIENT, N, A,
                                   note="нет числа предметов или площади")
    if A <= 0:
        return ConcentrationResult(None, None, None, STATUS_INSUFFICIENT, N, A,
                                   note="площадь ≤ 0")
    if N < 0:
        return ConcentrationResult(None, None, None, STATUS_INSUFFICIENT, N, A,
                                   note="отрицательное число предметов")
    lo, hi = poisson_count_interval(N, conf)
    note = "" if float(N).is_integer() else "N не целое (оценка) — интервал приближённый"
    status = STATUS_DETECTED if N > 0 else STATUS_NOT_DETECTED
    return ConcentrationResult(N / A, lo / A, hi / A, status, N, A, note=note)


def model_status(value, transfer: str = "unconfirmed") -> str:
    """Статус спутниковой/модельной оценки (она всегда «модельная оценка»).

    transfer: 'confirmed' — перенос на участок подтверждён сопоставлением с полевыми
    данными → «обнаружено»/«не обнаружено»; 'unconfirmed' → «исследовательская оценка»;
    'none' или нет значения → «концентрация недоступна»."""
    v = _finite_number(value)
    if transfer == "none" or v is None or v < 0:
        return STATUS_UNAVAILABLE
    if transfer == "unconfirmed":
        return STATUS_RESEARCH
    if transfer == "confirmed":
        return STATUS_DETECTED if v > 0 else STATUS_NOT_DETECTED
    raise ValueError(f"transfer ∈ confirmed|unconfirmed|none, получено {transfer!r}")


# ---------------------------------------------------------------- агрегация
def aggregate(df: pd.DataFrame, by: str | Sequence[str], n_col: str = "n_items",
              a_col: str = "area_km2", conf: float = 0.95) -> pd.DataFrame:
    """Концентрация по событию/зоне: C_group = ΣN / ΣA (отношение сумм).

    Почему не среднее плотностей: средняя из C_i = N_i/A_i даёт равный вес короткой
    трансекте (0,01 км², 1 предмет → 100 шт./км²) и длинной (1 км², 5 предметов →
    5 шт./км²): среднее 52,5, тогда как на всей обследованной площади 6 предметов
    на 1,01 км² = 5,9 шт./км². ΣN/ΣA — это концентрация объединённой площади
    (оценка максимального правдоподобия для общей интенсивности Пуассона), и сумма
    счётов остаётся пуассоновской, поэтому интервал Гарвуда к ней применим.
    Строки без N или с A ≤ 0 не входят в суммы и считаются в n_invalid.
    """
    keys = [by] if isinstance(by, str) else list(by)
    d = df.copy()
    n = pd.to_numeric(d[n_col], errors="coerce")
    a = pd.to_numeric(d[a_col], errors="coerce")
    valid = n.notna() & a.notna() & (a > 0) & (n >= 0) & np.isfinite(n) & np.isfinite(a)
    d["_n"], d["_a"], d["_v"] = n.where(valid), a.where(valid), valid
    rows = []
    for key, g in d.groupby(keys, sort=True, dropna=False):
        gv = g[g["_v"]]
        if len(gv) == 0:
            res = concentration(None, None, conf)
        else:
            res = concentration(gv["_n"].sum(), gv["_a"].sum(), conf)
        key = key if isinstance(key, tuple) else (key,)
        row = dict(zip(keys, key))
        row.update(n_records=len(g), n_invalid=int((~g["_v"]).sum()),
                   n_items=res.n_items, area_km2=res.area_km2,
                   concentration_items_km2=res.value, lower=res.lower, upper=res.upper,
                   status=res.status)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- метрики
def _pair(y_true: Iterable, y_pred: Iterable) -> tuple[np.ndarray, np.ndarray]:
    t = np.asarray(list(y_true), dtype=float)
    p = np.asarray(list(y_pred), dtype=float)
    if t.shape != p.shape:
        raise ValueError(f"разные длины: {t.shape} vs {p.shape}")
    if t.size == 0:
        raise ValueError("пустая выборка")
    if not (np.isfinite(t).all() and np.isfinite(p).all()):
        raise ValueError("NaN/inf в эталоне или предсказании")
    return t, p


def mae(y_true, y_pred) -> float:
    t, p = _pair(y_true, y_pred)
    return float(np.mean(np.abs(p - t)))


def rmse(y_true, y_pred) -> float:
    t, p = _pair(y_true, y_pred)
    return float(np.sqrt(np.mean((p - t) ** 2)))


def median_ae(y_true, y_pred) -> float:
    t, p = _pair(y_true, y_pred)
    return float(np.median(np.abs(p - t)))


def log1p_mae(y_true, y_pred) -> float:
    """Средняя |log1p(pred) − log1p(true)| — относительная ошибка, определённая при нулях
    (MAPE при C = 0 не определён и не используется). exp(x)−1 ≈ «во сколько раз» ошибаемся."""
    t, p = _pair(y_true, y_pred)
    if (t < 0).any() or (p < 0).any():
        raise ValueError("концентрация не может быть отрицательной")
    return float(np.mean(np.abs(np.log1p(p) - np.log1p(t))))


def rmsle(y_true, y_pred) -> float:
    t, p = _pair(y_true, y_pred)
    if (t < 0).any() or (p < 0).any():
        raise ValueError("концентрация не может быть отрицательной")
    return float(np.sqrt(np.mean((np.log1p(p) - np.log1p(t)) ** 2)))


def all_metrics(y_true, y_pred) -> dict:
    t, p = _pair(y_true, y_pred)
    return {"n": int(t.size), "mae": mae(t, p), "rmse": rmse(t, p), "median_ae": median_ae(t, p),
            "log1p_mae": log1p_mae(t, p), "rmsle": rmsle(t, p), "bias": float(np.mean(p - t))}
