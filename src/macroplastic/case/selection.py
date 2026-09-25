"""Отбор совместимых полевых записей для концентрации шт./км² по configs/case_selection.yaml.

Каждая строка реестра получает ровно один исход: «принята» или первая причина отказа
(порядок проверок: source_id → record_type → target_scope → measurement_profile →
size_class → material → quality_flags → наличие целевой концентрации). Отказы
сохраняются с sample_id и причиной — это требование постановки «обосновать включения
и исключения».

Профиль по умолчанию — S2_visual_total_plastic (обоснование в YAML): один метод
(визуальная полоса 10 м), один размерный класс (> 2 см), суммарный пластик, у всех строк
есть N и A → C = N/A проверяется на данных. Разные профили не смешиваются: отдельный
предмет, категория, общий мусор и суммарный пластик — разные целевые величины.

Утечки: поля ниже содержат ответ или производное от него и НЕ используются как
предикторы (LEAK_COLUMNS); проверка — assert_no_leak().
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml

from .concentration import concentration

REPO = Path(__file__).resolve().parents[3]
SAMPLES_CSV = REPO / "task" / "macroplastic_marine_samples.csv"
CONFIG_YAML = REPO / "configs" / "case_selection.yaml"

LEAK_PREFIXES = ("concentration_", "reported_", "parent_")
LEAK_EXACT = {"items_count", "density_numerator_items", "source_object_filtered_items",
              "source_reported_total_items", "zero_scope", "calculation_method", "notes",
              "quality_flags", "source_row_refs", "target"}


def is_leak(col: str) -> bool:
    return col in LEAK_EXACT or col.startswith(LEAK_PREFIXES)


def assert_no_leak(features) -> None:
    bad = [c for c in features if is_leak(c)]
    if bad:
        raise ValueError(f"утечка ответа в предикторах: {bad}")


def load_samples(path: Path | str = SAMPLES_CSV) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8")


def load_config(path: Path | str = CONFIG_YAML) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _flags(s) -> list[str]:
    if s is None or (isinstance(s, float) and np.isnan(s)) or s == "":
        return []
    return [x.strip() for x in str(s).split(";") if x.strip()]


def select(df: pd.DataFrame, cfg: dict, profile: Optional[str] = None
           ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """→ (принятые записи, отказы[sample_id, event_id, reason]). Сумма длин = len(df)."""
    profile = profile or cfg["default_profile"]
    p = cfg["profiles"][profile]
    target = cfg.get("target_column", "concentration_items_km2")
    reasons = pd.Series("", index=df.index, dtype=object)

    def reject(mask, text):
        m = mask & (reasons == "")
        reasons[m] = text

    for col in ("source_id", "record_type", "target_scope", "measurement_profile",
                "size_class", "material"):
        allowed = p.get(col)
        if allowed:
            reject(~df[col].isin(allowed), f"{col}_not_in_profile")
    excl = set(p.get("exclude_quality_flags") or [])
    if excl:
        hit = df["quality_flags"].map(lambda s: sorted(set(_flags(s)) & excl))
        for i in df.index[hit.map(bool)]:
            if reasons[i] == "":
                reasons[i] = "quality_flag:" + "|".join(hit[i])
    if p.get("require_target", True):
        reject(pd.to_numeric(df[target], errors="coerce").isna(), f"{target}_missing")

    ok = reasons == ""
    acc = df[ok].copy()
    acc["profile"] = profile
    note = set(p.get("note_quality_flags") or [])
    acc["noted_flags"] = acc["quality_flags"].map(lambda s: ";".join(f for f in _flags(s) if f in note))
    acc["target"] = pd.to_numeric(acc[target], errors="coerce")
    rej = pd.DataFrame({"sample_id": df.loc[~ok, "sample_id"], "event_id": df.loc[~ok, "event_id"],
                        "reason": reasons[~ok]})
    assert len(acc) + len(rej) == len(df)
    return acc.reset_index(drop=True), rej.reset_index(drop=True)


def consistency_check(df: pd.DataFrame, rel_tol: float = 0.02, abs_tol: float = 0.05
                      ) -> pd.DataFrame:
    """Пересчёт C = N/A там, где есть density_numerator_items и sampled_area_km2, и сравнение
    с concentration_items_km2. Поле is_estimate — calculation_method говорит об оценке
    (тогда расхождение не ошибка, а свойство источника). Также сверка
    sampled_area_km2 с длиной × шириной полосы."""
    n = pd.to_numeric(df["density_numerator_items"], errors="coerce")
    a = pd.to_numeric(df["sampled_area_km2"], errors="coerce")
    c = pd.to_numeric(df["concentration_items_km2"], errors="coerce")
    m = n.notna() & a.notna() & c.notna()
    d = df.loc[m, ["sample_id", "event_id", "source_id", "target_scope", "measurement_profile",
                   "calculation_method", "transect_length_km", "transect_width_m"]].copy()
    d["N"], d["A_km2"], d["C_published"] = n[m], a[m], c[m]
    d["C_recomputed"] = [concentration(ni, ai).value for ni, ai in zip(n[m], a[m])]
    d["abs_diff"] = (d["C_recomputed"] - d["C_published"]).abs()
    d["rel_diff"] = d["abs_diff"] / d["C_published"].abs().replace(0, np.nan)
    d["match"] = d["abs_diff"] <= np.maximum(abs_tol, rel_tol * d["C_published"].abs())
    d["is_estimate"] = d["calculation_method"].fillna("").str.contains("estimate", case=False)
    L = pd.to_numeric(d["transect_length_km"], errors="coerce")
    W = pd.to_numeric(d["transect_width_m"], errors="coerce")
    d["A_strip_km2"] = L * W / 1000.0
    d["area_rel_diff"] = (d["A_strip_km2"] - d["A_km2"]).abs() / d["A_km2"]
    return d.reset_index(drop=True)


def consistency_summary(chk: pd.DataFrame) -> pd.DataFrame:
    g = chk.groupby(["source_id", "measurement_profile", "target_scope"])
    return g.agg(rows=("sample_id", "size"), match=("match", "sum"),
                 max_rel_diff=("rel_diff", "max"), estimate_rows=("is_estimate", "sum"),
                 area_strip_max_rel_diff=("area_rel_diff", "max")).reset_index()


def profile_overview(df: pd.DataFrame, target: str = "concentration_items_km2") -> pd.DataFrame:
    """Сколько строк/событий с целевой концентрацией даёт каждая комбинация профиля."""
    t = pd.to_numeric(df[target], errors="coerce")
    d = df.assign(_has=t.notna(), _num=pd.to_numeric(df["density_numerator_items"], errors="coerce").notna())
    g = d.groupby(["source_id", "record_type", "target_scope", "measurement_profile"])
    out = g.agg(rows=("sample_id", "size"), events=("event_id", "nunique"),
                rows_with_target=("_has", "sum"), rows_with_N=("_num", "sum")).reset_index()
    out["events_with_target"] = [d[(d._has) & (d.source_id == r.source_id) & (d.record_type == r.record_type)
                                   & (d.target_scope == r.target_scope)
                                   & (d.measurement_profile == r.measurement_profile)].event_id.nunique()
                                 for r in out.itertuples()]
    return out.sort_values("events_with_target", ascending=False).reset_index(drop=True)
