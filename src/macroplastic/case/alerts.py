"""§51 п.6/п.7/п.10 — важность и алерты. Правило записано (до расчёта) в docs/ALERTS.md; этот модуль — его код.

Используется из service/case_store.py (боевая отдача в /api/v3/scene_zones* и выгрузку) и scripts/case/alerts.py
(офлайн-проверка распределения по уровням на реальных зонах). Ничего не выдумывает: расстояние до берега — из
Natural Earth 1:10m (data_cache/natural_earth/ne_10m_land.shp, вне git — см. scripts/fetch_natural_earth_10m.py;
запасной вариант — 1:110m, service/frontend_v2/public/land-110m.geojson, тот же контур, что офлайн-подложка карты,
но слишком грубый у берега — L142 нашёл 19 из 77 находок с shore_km=0, потому что центр зоны в море попадал внутрь
упрощённого полигона суши); доля выброса дрейфом — из уже опубликованных прогонов OpenDrift
(data/live/<region>/<date>/drift.json, stats.stranded_pct, §47 п.6/§48); при отсутствии данных — честные None +
причина, не 0 и не выдумка. Точка внутри контура суши (даже уточнённого) — тоже честный None + причина, не 0.
"""
from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[3]
LAND_10M_SHP = ROOT / "data_cache" / "natural_earth" / "ne_10m_land.shp"
LAND_GEOJSON_110M = ROOT / "service" / "frontend_v2" / "public" / "land-110m.geojson"


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
def _land_geoms_10m():
    """Natural Earth 1:10m land polygons (data_cache/natural_earth/ne_10m_land.shp, ~100 m–1 km accuracy at the
    coast — the 1:110m contour used before this collapsed real sea points into land, §51 bugfix from L142:
    19/77 finds got shore_km=0). Loaded once via geopandas. None if the file/geopandas is unavailable."""
    if not LAND_10M_SHP.is_file():
        return None
    try:
        import geopandas as gpd
        from shapely.ops import unary_union
        gdf = gpd.read_file(LAND_10M_SHP)
        return unary_union(list(gdf.geometry))
    except Exception:
        return None


@lru_cache(maxsize=1)
def _land_geoms_110m():
    """Fallback only (data_cache/natural_earth not fetched): the coarse Natural Earth 1:110m contour, the same
    file the offline basemap uses. Returns a shapely (Multi)Polygon, or None if unavailable."""
    try:
        from shapely.geometry import shape
    except Exception:
        return None
    if not LAND_GEOJSON_110M.is_file():
        return None
    try:
        gj = json.loads(LAND_GEOJSON_110M.read_text(encoding="utf-8"))
        geoms = [shape(f["geometry"]) for f in gj.get("features") or [] if f.get("geometry")]
        if not geoms:
            return None
        from shapely.ops import unary_union
        return unary_union(geoms)
    except Exception:
        return None


def _land() -> tuple:
    """(geometry, source_label). Prefers the 1:10m contour; falls back to 1:110m with a coarser note."""
    land = _land_geoms_10m()
    if land is not None:
        return land, "Natural Earth 1:10m"
    land = _land_geoms_110m()
    if land is not None:
        return land, "Natural Earth 1:110m (грубо — 1:10m не скачан)"
    return None, ""


def shore_km(lon: Optional[float], lat: Optional[float]) -> tuple[Optional[float], Optional[str]]:
    """docs/ALERTS.md §1: (distance_km, reason). reason is set only when distance_km is None — including the
    honest case where the point falls on land even by the finer contour (not silently 0)."""
    if lon is None or lat is None:
        return None, "нет геометрии"
    land, src = _land()
    if land is None:
        return None, "контур берега недоступен (ни ne_10m_land.shp, ни land-110m.geojson)"
    try:
        from shapely.geometry import Point
        from shapely.ops import nearest_points
        pt = Point(lon, lat)
        if land.contains(pt):
            return None, f"точка на суше по контуру берега ({src})"
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


def size_factor(is_large: bool, major_axis_m: Optional[float]) -> int:
    """docs/ALERTS.md §3: size on the same «крупное скопление» rule as §51 п.4 (service/case_store.py:sz_large) —
    mask area >= large_zone_km2 OR major axis >= large_axis_m -> 3. Otherwise a middling axis (>= 100 m, an order
    below the «large» threshold) -> 2; smaller/unknown -> 1. Not the raw contour area (that includes the 150 m
    buffer, §51 п.4 note) — «крупное» and the size factor now agree on one definition."""
    if is_large:
        return 3
    if major_axis_m is not None and major_axis_m >= 100:
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


def importance_rank(is_large: bool, major_axis_m: Optional[float], distance_km: Optional[float],
                     stranded_pct: Optional[float]) -> int:
    """docs/ALERTS.md §3: size_factor x shore_factor x drift_factor, each in {1,2,3} -> rank in [1,27]."""
    return size_factor(is_large, major_axis_m) * shore_factor(distance_km) * drift_factor(stranded_pct)


def alert_level_from_rank(rank: int, confirmed: bool, likely_organic: bool = False) -> str:
    """docs/ALERTS.md §4: level from rank, capped at «средний» when not independently confirmed, or when the zone
    is flagged «вероятно органика» (§51 п.9 — experimental NDVI/FAI flag; an organics guess should not read as a
    high-priority plastic alert)."""
    hi, mid = RANK_LEVEL
    level = "высокий" if rank >= hi else "средний" if rank >= mid else "слабый"
    if (not confirmed or likely_organic) and level == "высокий":
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


def zone_alert(is_find: bool, is_large: bool, major_axis_m: Optional[float], lon: Optional[float],
               lat: Optional[float], region: Optional[str], date: Optional[str], confirmed: bool,
               likely_organic: bool = False) -> dict:
    """One call per satellite zone -> all §51 п.6/п.7/п.10 fields (docs/ALERTS.md). Non-finds get honest nulls —
    the rule ranks risk of a real find, not every detector row. `is_large`/`major_axis_m` — the same «крупное
    скопление» fields as §51 п.4 (service/case_store.py:sz_large), not a separate area threshold."""
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
    rank = importance_rank(is_large, major_axis_m, d_km, stranded_pct)
    level = alert_level_from_rank(rank, confirmed, likely_organic)
    return {
        "shore_km": d_km, "shore_km_reason": shore_reason, "shore_km_note": "грубо, Natural Earth 1:110m",
        "stranded_pct_72h": stranded_pct, "drift_reason": drift_reason,
        "importance_rank": rank, "importance_rank_max": 27,
        "alert_level": level,
        "alert_material": None, "alert_material_note": "материал по снимку не определяется",
    }
