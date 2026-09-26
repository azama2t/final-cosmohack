"""§51 п.6/п.7/п.10 — важность и алерты. Правило записано (до расчёта) в docs/ALERTS.md; этот модуль — его код.

Используется из service/case_store.py (боевая отдача в /api/v3/scene_zones* и выгрузку) и scripts/case/alerts.py
(офлайн-проверка распределения по уровням на реальных зонах). Ничего не выдумывает: расстояние до берега — из
Natural Earth 1:110m (service/frontend_v2/public/land-110m.geojson, уже используется офлайн-подложкой карты); доля
выброса дрейфом — из уже опубликованных прогонов OpenDrift (data/live/<region>/<date>/drift.json, stats.stranded_pct,
§47 п.6/§48); при отсутствии данных — честные None + причина, не 0 и не выдумка.
"""
from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[3]
LAND_GEOJSON = ROOT / "service" / "frontend_v2" / "public" / "land-110m.geojson"


def _data_root() -> Path:
    """Same resolution order as service/core.py (ENV_VAR): explicit env, else service/data, else demo/demo_fixtures."""
    env = os.environ.get("MACROPLASTIC_DATA")
    if env:
        return Path(env)
    for cand in ("data", "demo", "demo_fixtures"):
        d = ROOT / "service" / cand
        if (d / "manifest.json").is_file():
            return d
    return ROOT / "service" / "data"


@lru_cache(maxsize=1)
def _drift_index() -> dict:
    """region|date -> drift.json path, from manifest.json — the same mapping the frontend uses
    (service/frontend_v2/src/case/drift.ts:driftPaths) to decide which scenes have a published run."""
    mf = _data_root() / "manifest.json"
    if not mf.is_file():
        return {}
    try:
        m = json.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, str] = {}
    for r in m.get("regions") or []:
        rid = r.get("id")
        for d in r.get("dates") or []:
            if d.get("drift"):
                out[f"{rid}|{d.get('date')}"] = d["drift"]
    return out


def stranded_pct_for(region: Optional[str], date: Optional[str]) -> tuple[Optional[float], Optional[str]]:
    """docs/ALERTS.md §2: (stranded_pct_72h, reason). reason is set only when the value is None."""
    if not region or not date:
        return None, "нет региона/даты сцены"
    rel = _drift_index().get(f"{region}|{date[:10]}")
    if not rel:
        return None, "нет прогноза дрейфа для этой сцены"
    fp = _data_root() / rel
    if not fp.is_file():
        return None, "файл прогноза не найден"
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
        pct = (d.get("stats") or {}).get("stranded_pct")
        return (float(pct), None) if pct is not None else (None, "в прогоне нет stranded_pct")
    except Exception as e:  # pragma: no cover
        return None, f"прогноз повреждён: {e}"

# docs/ALERTS.md §3: пороги множителей ранга важности
SHORE_FACTOR_KM = (5.0, 20.0)  # <=5 -> 3, <=20 -> 2, else 1
DRIFT_FACTOR_PCT = (50.0, 10.0)  # >=50 -> 3, >=10 -> 2, else 1; нет прогноза -> 2 (нейтрально)
RANK_LEVEL = (18, 6)  # >=18 -> высокий, >=6 -> средний, else слабый

# docs/ALERTS.md §5: ключевые слова материала полевых записей/фото (список правил, не словарь всех формулировок)
MATERIAL_HIGH_KW = ("net", "fishing", "rope")  # сети / промысловое снаряжение — не ниже «средний»
MATERIAL_LOW_KW = ("bottle", "bag", "cap", "lid", "wrapper", "foil", "balloon")  # мелкая упаковка — не выше «слабый»

LEVELS = ["слабый", "средний", "высокий"]


def _km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Same approximation as service/case_store.py:_km — kept independent so this module has no service import."""
    kx = 111.32 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lon1 - lon2) * kx, (lat1 - lat2) * 110.57)


@lru_cache(maxsize=1)
def _land_geoms():
    """Natural Earth 1:110m land polygons, loaded once. Returns a shapely (Multi)Polygon, or None if unavailable
    (shapely missing / file missing) — callers must degrade to shore_km=None + a reason, never guess a distance."""
    try:
        from shapely.geometry import shape
    except Exception:
        return None
    if not LAND_GEOJSON.is_file():
        return None
    try:
        gj = json.loads(LAND_GEOJSON.read_text(encoding="utf-8"))
        geoms = [shape(f["geometry"]) for f in gj.get("features") or [] if f.get("geometry")]
        if not geoms:
            return None
        from shapely.ops import unary_union
        return unary_union(geoms)
    except Exception:
        return None


def shore_km(lon: Optional[float], lat: Optional[float]) -> tuple[Optional[float], Optional[str]]:
    """docs/ALERTS.md §1: (distance_km, reason). reason is set only when distance_km is None."""
    if lon is None or lat is None:
        return None, "нет геометрии"
    land = _land_geoms()
    if land is None:
        return None, "контур берега (land-110m.geojson) недоступен"
    try:
        from shapely.geometry import Point
        from shapely.ops import nearest_points
        pt = Point(lon, lat)
        near_land, _ = nearest_points(land, pt)
        d = _km(lon, lat, near_land.x, near_land.y)
        return round(d, 1), None
    except Exception as e:  # pragma: no cover - defensive, degrade honestly rather than 500
        return None, f"расчёт расстояния не удался: {e}"


def _bucket3(value: float, hi_lo: tuple[float, float], higher_is_worse: bool) -> int:
    a, b = hi_lo
    if higher_is_worse:
        return 3 if value >= a else 2 if value >= b else 1
    return 3 if value <= a else 2 if value <= b else 1


def size_factor(area_km2: Optional[float], large_km2: float) -> int:
    if area_km2 is None:
        return 1
    if area_km2 >= large_km2:
        return 3
    if area_km2 >= 0.01:
        return 2
    return 1


def shore_factor(distance_km: Optional[float]) -> int:
    if distance_km is None:
        return 1  # docs/ALERTS.md §3: нет геометрии -> 1 (не повышаем важность без данных)
    return _bucket3(distance_km, SHORE_FACTOR_KM, higher_is_worse=False)


def drift_factor(stranded_pct: Optional[float]) -> int:
    if stranded_pct is None:
        return 2  # docs/ALERTS.md §2/§3: нет прогноза -> нейтрально, не 0 и не максимум
    return _bucket3(stranded_pct, DRIFT_FACTOR_PCT, higher_is_worse=True)


def importance_rank(area_km2: Optional[float], large_km2: float, distance_km: Optional[float],
                     stranded_pct: Optional[float]) -> int:
    """docs/ALERTS.md §3: size_factor x shore_factor x drift_factor, each in {1,2,3} -> rank in [1,27]."""
    return size_factor(area_km2, large_km2) * shore_factor(distance_km) * drift_factor(stranded_pct)


def alert_level_from_rank(rank: int, confirmed: bool) -> str:
    """docs/ALERTS.md §4: level from rank, capped at «средний» when not independently confirmed."""
    hi, mid = RANK_LEVEL
    level = "высокий" if rank >= hi else "средний" if rank >= mid else "слабый"
    if not confirmed and level == "высокий":
        level = "средний"
    return level


def material_bump(level: str, text: str) -> str:
    """docs/ALERTS.md §5: field/photo records only — nets/fishing gear up one notch, small packaging down one notch."""
    t = (text or "").lower()
    i = LEVELS.index(level) if level in LEVELS else 0
    if any(k in t for k in MATERIAL_HIGH_KW):
        i = max(i, LEVELS.index("средний"))
    elif any(k in t for k in MATERIAL_LOW_KW):
        i = min(i, LEVELS.index("слабый"))
    return LEVELS[i]


def zone_alert(is_find: bool, area_km2: Optional[float], large_km2: float, lon: Optional[float], lat: Optional[float],
               region: Optional[str], date: Optional[str], confirmed: bool) -> dict:
    """One call per satellite zone -> all §51 п.6/п.7/п.10 fields (docs/ALERTS.md). Non-finds get honest nulls —
    the rule ranks risk of a real find, not every detector row."""
    if not is_find:
        return {
            "shore_km": None, "shore_km_reason": "не находка", "shore_km_note": "грубо, Natural Earth 1:110m",
            "stranded_pct_72h": None, "drift_reason": "не находка",
            "importance_rank": None, "importance_rank_max": 27,
            "alert_level": None,
            "alert_material": None, "alert_material_note": "материал по снимку не определяется",
        }
    d_km, shore_reason = shore_km(lon, lat)
    stranded_pct, drift_reason = stranded_pct_for(region, date)
    rank = importance_rank(area_km2, large_km2, d_km, stranded_pct)
    level = alert_level_from_rank(rank, confirmed)
    return {
        "shore_km": d_km, "shore_km_reason": shore_reason, "shore_km_note": "грубо, Natural Earth 1:110m",
        "stranded_pct_72h": stranded_pct, "drift_reason": drift_reason,
        "importance_rank": rank, "importance_rank_max": 27,
        "alert_level": level,
        "alert_material": None, "alert_material_note": "материал по снимку не определяется",
    }
