"""Поиск синхронной сцены для нового или планируемого полевого наблюдения (задача L71, доп. функция к О6).

Вход: точка или отрезок трансекты, момент наблюдения (или дата без времени), окно поиска, сценарий дрейфа
(или своя скорость), допуск. Выход: сцены-кандидаты Sentinel-2 L2A/L1C и Landsat C2 L2 с dt, смещением воды
по сценариям дрейфа, допуском и решением о синхронности с причиной. Отдельно считается «окно синхронизации»:
максимальное |dt|, при котором смещение воды не выходит за допуск.
    max_abs_dt_h = tolerance_km / (speed_ms * 3.6)      (0.2 м/с и 3 км дают 4.17 ч)

Правила взяты из scripts/case/find_pairs.py (L59/L59b): тот же поиск STAC (bbox точки ±0.01°, те же коллекции,
тот же ключ кэша data/pairs/cache/<sha1>.json), те же сценарии дрейфа (configs/case_pairs.yaml, секция drift).
Для даты без времени: полдень, окно расширено на 0.5 сут, дрейф считается как |dt| + 12 ч (худший случай), и
дополнительно выдаётся интервал времени, в который наблюдение надо провести, чтобы пара стала синхронной.
Для будущей даты (планирование): прошлые пролёты над точкой проецируются вперёд с периодом повторения орбиты
(Sentinel-2A/B/C — 10 сут, Landsat 8/9 — 16 сут); такие кандидаты помечены predicted=true.
Маски качества (quality=true) считаются функциями scripts/case/pair_quality.py без изменений (детектор не
запускается). Скрипты find_pairs.py и pair_quality.py не меняются: модуль импортирует их как есть.

Офлайн (только кэш): переменная окружения MACROPLASTIC_PAIRFINDER_OFFLINE=1 или offline=True.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
CFG_PATH = ROOT / "configs" / "case_pairs.yaml"
QCACHE = ROOT / "data" / "pairs" / "pairfinder"
OFFLINE_ENV = "MACROPLASTIC_PAIRFINDER_OFFLINE"

TARGET = {"S2A", "S2B", "S2C", "L8", "L9"}
REPEAT_DAYS = {"S2A": 10, "S2B": 10, "S2C": 10, "L8": 16, "L9": 16}
MAX_WINDOW_DAYS = 15
MAX_QUALITY = 3
MAX_LINE_KM = 200
LEVEL_RANK = {"L2A": 0, "L2SP": 0, "L1C": 1}

_mods: dict = {}
_lock = threading.Lock()


def _script(name: str):
    """scripts/case/<name>.py как модуль (без изменений файла); один экземпляр на процесс."""
    with _lock:
        if name not in _mods:
            if name == "pair_quality":
                src = str(ROOT / "src")
                if src not in sys.path:
                    sys.path.insert(0, src)
            spec = importlib.util.spec_from_file_location(f"case_{name}_pf", ROOT / "scripts" / "case" / f"{name}.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _mods[name] = mod
        return _mods[name]


def find_pairs_mod():
    return _script("find_pairs")


class PairfinderError(Exception):
    """Ошибка входа: status 400/422, code в стиле docs/CONTRACTS_V3.md."""

    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or {}


def load_cfg(path: Path = CFG_PATH) -> dict:
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def is_offline(flag: bool | None = None) -> bool:
    if flag is not None:
        return bool(flag)
    return os.environ.get(OFFLINE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


# ------------------------------------------------------------------------------------------ drift rule
def sync_window_hours(tolerance_km: float, speed_ms: float) -> float:
    """Максимальное |dt| (ч), при котором вода смещается не дальше допуска: tol / (v * 3.6)."""
    if speed_ms <= 0:
        return math.inf
    return tolerance_km / (speed_ms * 3.6)


def shift_km(speed_ms: float, dt_h: float) -> float:
    return speed_ms * abs(dt_h) * 3.6


def scenario_speeds(dc: dict, wind_ms: float | None) -> dict[str, float]:
    w = wind_ms or 0.0
    return {k: sc["current_ms"] + sc["windage"] * w for k, sc in dc["scenarios"].items()}


# ------------------------------------------------------------------------------------------ request
@dataclass
class Query:
    lon: float
    lat: float
    line: list | None
    obs: pd.Timestamp
    time_known: bool
    window_days: float
    scenario: str
    speed_ms: float
    speeds: dict
    tolerance_km: float
    tolerance_rule: str
    width_m: float | None
    wind_ms: float | None
    max_cloud: float
    quality: bool
    offline: bool
    raw: dict = field(default_factory=dict)


def _num(body: dict, key: str, lo: float | None = None, hi: float | None = None, default=None, code="BAD_PARAM"):
    v = body.get(key, default)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise PairfinderError(422, code, f"Параметр {key} должен быть числом", {"param": key, "value": v})
    try:
        f = float(v)
    except ValueError:
        raise PairfinderError(422, code, f"Параметр {key} должен быть числом", {"param": key, "value": v}) from None
    if not math.isfinite(f) or (lo is not None and f < lo) or (hi is not None and f > hi):
        rng = f"[{lo}; {hi}]" if hi is not None else f"≥ {lo}"
        raise PairfinderError(422, code, f"Параметр {key} вне допустимого диапазона {rng}", {"param": key, "value": v})
    return f


def _lonlat(lon, lat, what: str):
    for k, v, lim in (("lon", lon, 180), ("lat", lat, 90)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > lim:
            raise PairfinderError(422, "BAD_GEOMETRY", f"{what}: {k} должен быть числом в диапазоне ±{lim}",
                                  {"param": k, "value": v})
    return float(lon), float(lat)


def parse_geometry(g) -> tuple[float, float, list | None]:
    """GeoJSON Point/LineString (2 точки и больше: берутся первая и последняя), {lon, lat} или
    {lon_start, lat_start, lon_end, lat_end}. Возвращает центр и отрезок [(lon, lat), (lon, lat)] либо None."""
    if not isinstance(g, dict):
        raise PairfinderError(422, "BAD_GEOMETRY", "Нужна geometry: GeoJSON Point/LineString, {lon, lat} или "
                                                   "{lon_start, lat_start, lon_end, lat_end}", {"geometry": g})
    if g.get("type") == "Point":
        c = g.get("coordinates")
        if not isinstance(c, (list, tuple)) or len(c) < 2:
            raise PairfinderError(422, "BAD_GEOMETRY", "Point: coordinates = [lon, lat]", {"geometry": g})
        lon, lat = _lonlat(c[0], c[1], "Point")
        return lon, lat, None
    if g.get("type") == "LineString":
        c = g.get("coordinates")
        if not isinstance(c, (list, tuple)) or len(c) < 2 or not all(isinstance(p, (list, tuple)) and len(p) >= 2 for p in c):
            raise PairfinderError(422, "BAD_GEOMETRY", "LineString: coordinates = [[lon, lat], [lon, lat], ...]", {"geometry": g})
        a, b = _lonlat(c[0][0], c[0][1], "LineString start"), _lonlat(c[-1][0], c[-1][1], "LineString end")
        return _line(a, b)
    if "type" in g:
        raise PairfinderError(422, "BAD_GEOMETRY", f"Тип геометрии {g.get('type')!r} не поддерживается (Point или LineString)",
                              {"type": g.get("type")})
    if all(k in g for k in ("lon_start", "lat_start", "lon_end", "lat_end")):
        a = _lonlat(g["lon_start"], g["lat_start"], "start")
        b = _lonlat(g["lon_end"], g["lat_end"], "end")
        return _line(a, b)
    if "lon" in g and "lat" in g:
        lon, lat = _lonlat(g["lon"], g["lat"], "point")
        return lon, lat, None
    raise PairfinderError(422, "BAD_GEOMETRY", "Нужна geometry: GeoJSON Point/LineString, {lon, lat} или "
                                               "{lon_start, lat_start, lon_end, lat_end}", {"geometry": g})


def _line(a, b):
    km = _haversine_km(a, b)
    if km > MAX_LINE_KM:
        raise PairfinderError(422, "BAD_GEOMETRY", f"Отрезок {km:.0f} км длиннее {MAX_LINE_KM} км — разбейте трансекту",
                              {"length_km": round(km, 1)})
    if km < 0.001:
        return a[0], a[1], None
    return (a[0] + b[0]) / 2, (a[1] + b[1]) / 2, [a, b]


def _haversine_km(a, b) -> float:
    (lo1, la1), (lo2, la2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def parse_datetime(v) -> tuple[pd.Timestamp, bool]:
    """"YYYY-MM-DD" -> полдень UTC, time_known=False; ISO-время (Z или смещение; без зоны = UTC) -> time_known=True."""
    if not isinstance(v, str) or not v.strip():
        raise PairfinderError(422, "BAD_DATE", "Нужен datetime: \"YYYY-MM-DDTHH:MM:SSZ\" или дата \"YYYY-MM-DD\"",
                              {"datetime": v})
    s = v.strip()
    try:
        if len(s) == 10:
            d = dt.date.fromisoformat(s)
            return pd.Timestamp(d.isoformat(), tz="UTC") + pd.Timedelta(hours=12), False
        t = pd.Timestamp(s.replace("Z", "+00:00") if s.endswith("Z") else s)
    except (ValueError, TypeError):
        raise PairfinderError(422, "BAD_DATE", f"Не удалось разобрать datetime {s!r}: нужен ISO 8601, например "
                                               "2024-06-02T09:00:00Z или 2024-06-02", {"datetime": v}) from None
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    if t.year < 1980 or t.year > 2100:
        raise PairfinderError(422, "BAD_DATE", "Год вне диапазона 1980–2100", {"datetime": v})
    return t, True


def parse_request(body, cfg: dict | None = None) -> Query:
    if not isinstance(body, dict):
        raise PairfinderError(400, "BAD_PARAM", "Тело запроса должно быть JSON-объектом", {})
    cfg = cfg or load_cfg()
    dc = cfg["drift"]
    known = {"geometry", "datetime", "window_days", "drift_scenario", "speed_ms", "tolerance_km", "quality",
             "width_m", "wind_ms", "max_cloud", "offline"}
    extra = sorted(set(body) - known)
    if extra:
        raise PairfinderError(422, "BAD_PARAM", f"Неизвестные параметры: {', '.join(extra)}", {"unknown": extra,
                                                                                              "allowed": sorted(known)})
    lon, lat, line = parse_geometry(body.get("geometry"))
    obs, known_t = parse_datetime(body.get("datetime"))
    window = _num(body, "window_days", 0.0, MAX_WINDOW_DAYS, default=5)
    width = _num(body, "width_m", 0.0, 10000)
    wind = _num(body, "wind_ms", 0.0, 60)
    max_cloud = _num(body, "max_cloud", 0.0, 100, default=60)
    speeds = scenario_speeds(dc, wind)
    scen = body.get("drift_scenario")
    spd = _num(body, "speed_ms", 0.0, 5.0)
    if spd is not None and scen is not None:
        raise PairfinderError(422, "BAD_PARAM", "Укажите либо drift_scenario, либо speed_ms, не оба",
                              {"drift_scenario": scen, "speed_ms": spd})
    if spd is not None:
        if spd == 0:
            raise PairfinderError(422, "BAD_PARAM", "speed_ms должен быть > 0 (при нулевом дрейфе любая пара «синхронна»)",
                                  {"speed_ms": spd})
        scen, speed = "custom", spd
        speeds = {**speeds, "custom": spd}
    else:
        scen = dc["accept_scenario"] if scen is None else scen
        if scen not in speeds:
            raise PairfinderError(422, "BAD_PARAM", f"drift_scenario должен быть одним из {sorted(speeds)}",
                                  {"drift_scenario": scen})
        speed = speeds[scen]
    tol = _num(body, "tolerance_km", 0.0, 1000)
    if tol is not None:
        if tol == 0:
            raise PairfinderError(422, "BAD_PARAM", "tolerance_km должен быть > 0", {"tolerance_km": tol})
        rule = "задан в запросе"
    else:
        t = dc["tolerance"]
        w = width if width is not None else t["default_width_m"]
        tol = round(w / 2000 + t["buffer_km"], 3)
        rule = f"ширина полосы {w:g} м / 2 + буфер {t['buffer_km']} км (configs/case_pairs.yaml, drift.tolerance)"
    q = body.get("quality", False)
    if not isinstance(q, bool):
        raise PairfinderError(422, "BAD_PARAM", "quality должен быть true/false", {"quality": q})
    off = body.get("offline")
    if off is not None and not isinstance(off, bool):
        raise PairfinderError(422, "BAD_PARAM", "offline должен быть true/false", {"offline": off})
    return Query(lon=lon, lat=lat, line=line, obs=obs, time_known=known_t, window_days=window, scenario=scen,
                 speed_ms=speed, speeds=speeds, tolerance_km=tol, tolerance_rule=rule, width_m=width, wind_ms=wind,
                 max_cloud=max_cloud, quality=q, offline=is_offline(off), raw=body)


# ------------------------------------------------------------------------------------------ STAC (cache of find_pairs)
def cache_key(url: str, coll: str, bbox, dt0: str, dt1: str) -> str:
    """Тот же ключ, что в find_pairs.stac_search (проверяется тестом)."""
    body = {"collections": [coll], "bbox": bbox, "datetime": f"{dt0}/{dt1}", "limit": 100}
    return hashlib.sha1(json.dumps([url, body], sort_keys=True).encode()).hexdigest()


def search_args(lon: float, lat: float, t: pd.Timestamp, half_days: float):
    """bbox и интервал так же, как find_pairs.query_event (для точек реестра — попадание в кэш)."""
    dt0 = (t - pd.Timedelta(days=half_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    dt1 = (t + pd.Timedelta(days=half_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    d = 0.01
    return [lon - d, lat - d, lon + d, lat + d], dt0, dt1


class Searcher:
    """Кэш data/pairs/cache (общий с find_pairs). offline=True: только кэш, промах -> статус cache_miss."""

    def __init__(self, offline: bool):
        self.offline = offline
        self.network_calls = 0

    def __call__(self, url, coll, bbox, dt0, dt1):
        fp = find_pairs_mod()
        path = fp.CACHE / f"{cache_key(url, coll, bbox, dt0, dt1)}.json"
        if path.exists():
            return "cache", json.loads(path.read_text())
        if self.offline:
            return "cache_miss", None
        self.network_calls += 1
        fp.CACHE.mkdir(parents=True, exist_ok=True)
        try:
            return "network", fp.stac_search(url, coll, bbox, dt0, dt1, retries=2)
        except Exception as e:  # noqa: BLE001
            return f"error:{type(e).__name__}", None


# ------------------------------------------------------------------------------------------ core
def _iso(t) -> str | None:
    if t is None or pd.isna(t):
        return None
    return pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _footprint(item_geom, lon, lat, line):
    from shapely.geometry import LineString, Point, shape
    if not item_geom:
        return None, None
    try:
        fp = shape(item_geom)
        pt = Point(lon, lat)
        if line:
            ln = LineString(line)
            return bool(fp.intersects(ln)), round(float(fp.intersection(ln).length / ln.length), 3)
        return bool(fp.intersects(pt)), 1.0 if fp.intersects(pt) else 0.0
    except Exception:  # noqa: BLE001
        return None, None


def _day_bounds(obs: pd.Timestamp):
    d0 = obs.normalize()
    return d0, d0 + pd.Timedelta(days=1)


def evaluate(q: Query, items: list[dict], cfg: dict, predicted: bool = False) -> list[dict]:
    """items: [{source, endpoint, collection, level, feature}] -> кандидаты с решением о синхронности."""
    fp = find_pairs_mod()
    dc = cfg["drift"]
    wmax = sync_window_hours(q.tolerance_km, q.speed_ms)
    extra = 0.0 if q.time_known else float(dc["unknown_time_extra_h"])
    out = []
    for it in items:
        f = it["feature"]
        mission = fp.PLATFORM.get(str(f.get("platform") or "").lower(), str(f.get("platform")))
        st = pd.Timestamp(f["datetime"])
        st = st.tz_localize("UTC") if st.tzinfo is None else st.tz_convert("UTC")
        dth = (st - q.obs).total_seconds() / 3600
        inside, frac = _footprint(f.get("geometry"), q.lon, q.lat, q.line)
        coll = it["collection"]
        tile = f"{f.get('wrs_path')}/{f.get('wrs_row')}" if coll.startswith("landsat") else str(f.get("mgrs") or "").replace("MGRS-", "")
        cc = f.get("cloud")
        dtd = abs(dth) + extra
        shifts = {k: round(shift_km(v, dtd), 2) for k, v in q.speeds.items()}
        shift = round(shift_km(q.speed_ms, dtd), 2)
        reasons = []
        if mission not in TARGET:
            reasons.append(f"mission_not_target({mission})")
        if inside is False:
            reasons.append("outside_footprint")
        elif q.line and frac is not None and frac < 1:
            reasons.append(f"partial_footprint({frac:.0%})")
        # до минуты и с запасом внутрь окна (начало вверх, конец вниз)
        win_from = (st - pd.Timedelta(hours=wmax)).ceil("min") if math.isfinite(wmax) else st - pd.Timedelta(days=36500)
        win_to = (st + pd.Timedelta(hours=wmax)).floor("min") if math.isfinite(wmax) else st + pd.Timedelta(days=36500)
        hard = [r for r in reasons if not r.startswith("partial")]
        if hard:
            decision = "reject"
        elif q.time_known:
            if shift <= q.tolerance_km:
                decision = "synchronous"
            else:
                decision = "not_synchronous"
                reasons.append(f"drift_too_large({shift:g}>{q.tolerance_km:g} km)")
        else:
            d0, d1 = _day_bounds(q.obs)
            if win_from < d1 and win_to > d0:
                decision = "synchronous_if_time_in_window"
                reasons.append("time_unknown")
            else:
                decision = "not_synchronous"
                reasons += ["time_unknown", f"drift_too_large(day_outside_sync_window)"]
        if cc is None:
            reasons.append("scene_cloud_unknown")
        elif cc > q.max_cloud:
            reasons.append(f"scene_cloud_cover>{q.max_cloud:g}")
        if predicted:
            reasons.append("predicted_overpass")
        out.append(dict(
            scene_id=f["id"], mission=mission, collection=coll, level=it["level"], endpoint=it["endpoint"],
            scene_datetime=_iso(st), dt_hours=round(dth, 2), scene_cloud_cover=None if cc is None else round(float(cc), 2),
            tile=tile, footprint={"intersects": inside, "geometry_fraction_inside": frac},
            drift={"dt_drift_hours": round(dtd, 2), "time_worst_case": not q.time_known,
                   "shift_km": shifts, "shift_km_selected": shift, "scenario": q.scenario},
            tolerance_km=q.tolerance_km,
            sync_time_window={"from": _iso(win_from), "to": _iso(win_to), "max_abs_dt_hours": round(wmax, 2)},
            decision=decision, synchronous=decision == "synchronous", reasons=reasons,
            reason=_reason_text(decision, reasons, shift, q, wmax, st, win_from, win_to), predicted=predicted,
            quality=None))
    return out


def _reason_text(decision, reasons, shift, q: Query, wmax, st, win_from, win_to) -> str:
    if decision == "synchronous":
        return (f"|dt| ≤ {wmax:.1f} ч: вода смещается на {shift:g} км ≤ допуска {q.tolerance_km:g} км "
                f"(сценарий {q.scenario}, {q.speed_ms:g} м/с)")
    if decision == "synchronous_if_time_in_window":
        return (f"время наблюдения не задано; пара синхронна, если наблюдение было/будет между "
                f"{win_from:%Y-%m-%d %H:%M} и {win_to:%Y-%m-%d %H:%M} UTC (пролёт {st:%Y-%m-%d %H:%M} UTC)")
    if decision == "not_synchronous":
        return (f"за |dt| вода смещается на {shift:g} км > допуска {q.tolerance_km:g} км; нужно |dt| ≤ {wmax:.1f} ч"
                if q.time_known else
                f"окно синхронизации пролёта ({win_from:%Y-%m-%d %H:%M} – {win_to:%Y-%m-%d %H:%M} UTC) не пересекает "
                f"сутки наблюдения: синхронной пары быть не может")
    return "; ".join(r for r in reasons if not r.startswith("scene_cloud"))


def dedupe(cands: list[dict]) -> list[dict]:
    """Одна съёмка из ES и PC / L2A и L1C -> одна строка (приоритет L2A/L2SP, затем earth-search, затем id);
    остальные id — в also_available."""
    # ES и PC дают разное время одной съёмки (08:58 vs 08:46 — время тайла и начало датастрипа),
    # поэтому ключ — миссия + тайл (MGRS / path-row) + дата: над одним тайлом спутник пролетает раз в сутки.
    key = lambda c: (c["mission"], c["tile"], (c["scene_datetime"] or "")[:10], c["predicted"])  # noqa: E731
    order = sorted(cands, key=lambda c: (key(c), LEVEL_RANK.get(c["level"], 2), c["endpoint"], c["scene_id"]))
    out, seen = [], {}
    for c in order:
        k = key(c)
        if k in seen:
            seen[k]["also_available"].append(f"{c['endpoint']}/{c['collection']}:{c['scene_id']}")
            continue
        c = dict(c, also_available=[])
        seen[k] = c
        out.append(c)
    return out


DEC_RANK = {"synchronous": 0, "synchronous_if_time_in_window": 1, "not_synchronous": 2, "reject": 3}


def rank(cands: list[dict], max_cloud: float) -> list[dict]:
    def k(c):
        cc = c["scene_cloud_cover"]
        return (DEC_RANK[c["decision"]], 0 if (cc is not None and cc <= max_cloud) else 1, abs(c["dt_hours"]),
                cc if cc is not None else 101, LEVEL_RANK.get(c["level"], 2), c["scene_id"])
    return sorted(cands, key=k)


def _collect(q: Query, t: pd.Timestamp, half: float, searcher, fp) -> tuple[list[dict], list[dict]]:
    bbox, dt0, dt1 = search_args(q.lon, q.lat, t, half)
    items, status = [], []
    for name, url, coll, level in fp.SOURCES:
        st, feats = searcher(url, coll, bbox, dt0, dt1)
        status.append({"endpoint": name, "collection": coll, "status": st, "n": None if feats is None else len(feats),
                       "datetime": f"{dt0}/{dt1}"})
        for f in feats or []:
            items.append(dict(endpoint=name, collection=coll, level=level, feature=f))
    return items, status


def forecast_items(q: Query, history: list[dict], now: pd.Timestamp) -> list[dict]:
    """Прошлые пролёты -> будущие с периодом повторения орбиты (тот же относительный орбитальный трек)."""
    out, seen = [], set()
    for it in history:
        f = it["feature"]
        fp = find_pairs_mod()
        mission = fp.PLATFORM.get(str(f.get("platform") or "").lower(), str(f.get("platform")))
        if mission not in REPEAT_DAYS:
            continue
        st = pd.Timestamp(f["datetime"])
        st = st.tz_localize("UTC") if st.tzinfo is None else st.tz_convert("UTC")
        cyc = pd.Timedelta(days=REPEAT_DAYS[mission])
        k0 = math.ceil((q.obs - pd.Timedelta(days=q.window_days) - st) / cyc)
        k1 = math.floor((q.obs + pd.Timedelta(days=q.window_days) - st) / cyc)
        for k in range(max(k0, 1), k1 + 1):
            t = st + k * cyc
            if t <= now:
                continue
            nf = dict(f, id=f"predicted:{f['id']}+{k * REPEAT_DAYS[mission]}d", datetime=_iso(t), cloud=None)
            key = (it["collection"], it["endpoint"], nf["id"])
            if key not in seen:
                seen.add(key)
                out.append(dict(it, feature=nf))
    return out


def find(q: Query, cfg: dict | None = None, searcher=None, now: pd.Timestamp | None = None,
         quality_fn=None) -> dict:
    cfg = cfg or load_cfg()
    fp = find_pairs_mod()
    searcher = searcher or Searcher(q.offline)
    now = now or pd.Timestamp.now(tz="UTC")
    half = q.window_days + (0 if q.time_known else 0.5)
    mode = "archive"
    notes = []
    if q.obs - pd.Timedelta(days=half) > now:
        # планирование: истории нет — берём 20 сут архива до «сейчас» и проецируем пролёты вперёд
        mode = "forecast"
        ref = (now - pd.Timedelta(days=10)).floor("D")
        items, status = _collect(q, ref, 10.0, searcher, fp)
        items = forecast_items(q, items, now)
        notes.append("Дата в будущем: пролёты предсказаны по архиву за 20 сут до сегодняшнего дня с периодом повторения "
                     "орбиты (S2 — 10 сут на спутник, Landsat 8/9 — 16 сут). Облачность будущей сцены неизвестна.")
    else:
        items, status = _collect(q, q.obs, half, searcher, fp)
        if q.obs + pd.Timedelta(days=half) > now:
            notes.append("Окно поиска частично в будущем: предстоящие пролёты не показаны (архив ещё не содержит их).")
    cands = evaluate(q, items, cfg, predicted=(mode == "forecast"))
    cands = rank(dedupe(cands), q.max_cloud)
    wmax = sync_window_hours(q.tolerance_km, q.speed_ms)
    by_sc = {k: round(sync_window_hours(q.tolerance_km, v), 2) for k, v in q.speeds.items()}
    # маски качества для лучших кандидатов
    if q.quality:
        pick = [c for c in cands if c["decision"] in ("synchronous", "synchronous_if_time_in_window")
                and not c["predicted"]][:MAX_QUALITY]
        if not pick:
            pick = [c for c in cands if c["decision"] != "reject" and not c["predicted"]][:1]
        for c in pick:
            c["quality"] = (quality_fn or strip_quality)(q, c, cfg)
    n_sync = sum(c["synchronous"] for c in cands)
    n_cond = sum(c["decision"] == "synchronous_if_time_in_window" for c in cands)
    empty = None
    miss = [s for s in status if s["status"] == "cache_miss"]
    errs = [s for s in status if s["status"].startswith("error")]
    if not cands:
        if miss and len(miss) == len(status):
            empty = "Офлайн-режим: этого запроса нет в кэше data/pairs/cache — повторите без офлайна или для точки из реестра."
        elif errs and len(errs) + len(miss) == len(status):
            empty = "STAC-каталоги недоступны: " + "; ".join(f"{s['endpoint']}/{s['collection']}: {s['status']}" for s in errs)
        elif q.obs < pd.Timestamp("2013-02-11", tz="UTC") - pd.Timedelta(days=half):
            empty = "На эту дату Landsat 8 и Sentinel-2 ещё не запущены."
        else:
            empty = f"Нет сцен Sentinel-2/Landsat над точкой в окне ±{half:g} сут."
    elif not (n_sync or n_cond):
        empty = None
        notes.append(f"Сцены есть, но ни одна не синхронна: нужно |dt| ≤ {wmax:.1f} ч при {q.speed_ms:g} м/с и допуске "
                     f"{q.tolerance_km:g} км. Ближайший пролёт: {cands[0]['scene_datetime']} "
                     f"(dt {cands[0]['dt_hours']:+.1f} ч) — сдвиньте наблюдение в его окно синхронизации.")
    if miss and cands:
        notes.append(f"Офлайн: {len(miss)} из {len(status)} запросов нет в кэше — список может быть неполным.")
    best = next((c for c in cands if c["decision"] in ("synchronous", "synchronous_if_time_in_window")), None)
    return {
        "version": "3.1", "kind": "pairfinder",
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "query": {"geometry": ({"type": "LineString", "coordinates": [list(p) for p in q.line]} if q.line
                               else {"type": "Point", "coordinates": [q.lon, q.lat]}),
                  "center": [round(q.lon, 6), round(q.lat, 6)],
                  "obs_datetime": _iso(q.obs), "time_known": q.time_known, "window_days": q.window_days,
                  "search_half_window_days": half, "mode": mode, "offline": q.offline, "max_cloud": q.max_cloud,
                  "width_m": q.width_m, "wind_ms": q.wind_ms},
        "sync_window": {"scenario": q.scenario, "speed_ms": round(q.speed_ms, 4), "tolerance_km": q.tolerance_km,
                        "tolerance_rule": q.tolerance_rule, "max_abs_dt_hours": round(wmax, 2),
                        "max_abs_dt_hours_by_scenario": by_sc,
                        "unknown_time_extra_hours": 0 if q.time_known else cfg["drift"]["unknown_time_extra_h"],
                        "rule": "drift_shift_km = speed_ms × 3.6 × (|dt| [+12 ч, если время не задано]); синхронно, "
                                "если drift_shift_km ≤ tolerance_km, т. е. |dt| ≤ tolerance_km / (3.6 × speed_ms)"},
        "count": len(cands), "n_synchronous": n_sync, "n_synchronous_if_time_in_window": n_cond,
        "best": best["scene_id"] if best else None,
        "candidates": cands,
        "empty_reason": empty,
        "search": {"sources": status, "network_calls": getattr(searcher, "network_calls", None),
                   "cache": "data/pairs/cache"},
        "notes": notes,
        "units": {"dt_hours": "hours", "shift_km": "km", "tolerance_km": "km", "scene_cloud_cover": "%"},
    }


# ------------------------------------------------------------------------------------------ quality (pair_quality.py)
class _NoDetector:
    """Детектор не нужен: process_s2 зовёт predict_proba — отдаём нули (маски качества считаются как в L61)."""
    threshold = 1.0
    last_offset = None

    def predict_proba(self, bands, names, water_mask=None):
        import numpy as np
        return np.zeros(bands.shape[1:], np.float32)


def _qkey(q: Query, c: dict) -> str:
    s = json.dumps([c["scene_id"], c["collection"], c["endpoint"], round(q.lon, 6), round(q.lat, 6), q.line, q.width_m],
                   sort_keys=True, default=str)
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def strip_quality(q: Query, c: dict, cfg: dict) -> dict:
    """Доли воды/облаков/блика/суши в полосе наблюдения — функциями pair_quality.py (L61), результат кэшируется
    в data/pairs/pairfinder/<key>/quality.json (+ quality.png). Офлайн без кэша -> status unavailable."""
    key = _qkey(q, c)
    d = QCACHE / key
    cached = d / "quality.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8")) | {"cached": True}
    if q.offline:
        return {"status": "unavailable", "reason": "офлайн-режим: маска качества для этой сцены не посчитана заранее"}
    try:
        pq = _script("pair_quality")
        g = dict(lat=q.lat, lon=q.lon, width_m=q.width_m, line=[tuple(p) for p in q.line] if q.line else None)
        row = SimpleNamespace(endpoint=c["endpoint"], collection=c["collection"], item_id=c["scene_id"])
        if c["collection"].startswith("landsat"):
            res = pq.process_landsat(row, g, cfg, d)
        else:
            res = pq.process_s2(row, g, cfg, _NoDetector(), d)
            for n in ("prob.tif", "mask.png"):   # детектор не запускался — эти файлы пустые
                (d / n).unlink(missing_ok=True)
        qq = res["quality"]
        out = {"status": "ok", "decision": res["decision"], "reason": res["reason"] or None,
               "scene_id_read": res["scene_id"], "strip_rule": res["strip_rule"],
               "coverage": qq.get("coverage"), "valid_water_frac": qq.get("valid_water_frac"),
               "cloud_frac": qq.get("cloud_frac"), "glint_frac": qq.get("glint_frac"), "land_frac": qq.get("land_frac"),
               "nodata_frac": qq.get("nodata_frac"), "glint_b11_median": qq.get("glint_b11_median"),
               "strip_area_km2": qq.get("strip_area_km2"),
               "substitution_note": res.get("substitution_note") or None,
               "files": [f"data/pairs/pairfinder/{key}/{n}" for n in ("quality.png", "rgb.png") if (d / n).exists()],
               "thresholds": cfg["decision"]}
        d.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return out
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "reason": f"{type(e).__name__}: {str(e)[:160]}"}


def run(body: dict, cfg: dict | None = None, **kw) -> dict:
    cfg = cfg or load_cfg()
    return find(parse_request(body, cfg), cfg, **kw)
