"""Data layer of API v3 (case «Макропластик», docs/CONTRACTS_V3.md): field observations, pair registry, scenes,
zones (from pair_quality), metrics, saved queries.

Every source file is optional: a missing/broken file gives an empty collection (never an exception).
Paths live in PATHS (tests monkeypatch them). Files are cached by (path, mtime, size).
Nothing here invents numbers: model concentration for zones is null (status «Концентрация недоступна»),
because there is no validated satellite -> items/km2 calibration (docs/CASE_ANALYSIS.md).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[1]
PATHS: dict[str, Path] = {
    "samples": REPO / "task" / "macroplastic_marine_samples.csv",
    "pairs_dir": REPO / "data" / "pairs",
    "conc_metrics": REPO / "reports" / "case_conc" / "metrics.json",
    "lgbm_meta": REPO / "weights" / "lgbm" / "meta.json",
    "lgbm_test": REPO / "reports" / "lgbm_final_test.json",
    "final_numbers": REPO / "reports" / "final_numbers.json",
    "queries": REPO / "service" / "labels" / "queries.jsonl",
}

# ------------------------------------------------------------------ dictionaries (contract section 2)
STATUSES = [
    {"id": "detected", "label": "Обнаружено", "color": "#d9480f"},
    {"id": "not_detected", "label": "Не обнаружено", "color": "#2b8a3e"},
    {"id": "insufficient_data", "label": "Недостаточно данных", "color": "#868e96"},
    {"id": "research_estimate", "label": "Исследовательская оценка", "color": "#7048e8"},
    {"id": "concentration_unavailable", "label": "Концентрация недоступна", "color": "#adb5bd"},
]
STATUS_IDS = [s["id"] for s in STATUSES]
SOURCES = [
    ("S1_GPGP2018", "Большое тихоокеанское мусорное пятно, 2018"),
    ("S2_SARGASSO_MSM41", "Саргассово море, MSM41"),
    ("S3_SE_NORTH_SEA", "Юго-восток Северного моря"),
    ("S4_BLACK_SEA_DOORS3", "Чёрное море, DOORS"),
]
SOURCE_IDS = [s[0] for s in SOURCES]
PROFILES = [
    {"id": "S1_trawl_5_to_50", "label": "Трал, 5–50 см", "size_class": "5-50 cm"},
    {"id": "S1_trawl_GT5_H", "label": "Трал, >5 см (H)", "size_class": ">5 cm"},
    {"id": "S1_trawl_GT5_N", "label": "Трал, >5 см (N)", "size_class": ">5 cm"},
    {"id": "S1_trawl_GT5_F", "label": "Трал, >5 см (F)", "size_class": ">5 cm"},
    {"id": "S1_aerial_GT50", "label": "Авиасъёмка, >50 см", "size_class": ">50 cm"},
    {"id": "S2_visual_GT2", "label": "Визуально с судна, >2 см", "size_class": ">2 cm"},
    {"id": "S3_visual_GT2", "label": "Визуально с судна, >2 см", "size_class": ">2 cm"},
    {"id": "S4_visual_GT2_5", "label": "Визуально с судна, >2.5 см", "size_class": ">2.5 cm"},
]
PROFILE_IDS = [p["id"] for p in PROFILES]
SCOPES = [
    {"id": "total_plastic", "label": "Весь пластик"},
    {"id": "plastic_category", "label": "Категория пластика"},
    {"id": "fisheries_litter_category", "label": "Рыболовный мусор"},
    {"id": "all_litter", "label": "Весь мусор (не только пластик)"},
    {"id": "object_context", "label": "Отдельный объект (не плотность)"},
]
SCOPE_IDS = [s["id"] for s in SCOPES]
RECORD_TYPES = [{"id": "transect_density", "label": "Плотность на трансекте"},
                {"id": "item_observation", "label": "Отдельный объект"}]
RECORD_TYPE_IDS = [r["id"] for r in RECORD_TYPES]
MISSIONS = ["Sentinel-2", "Landsat-8", "Landsat-9"]
REJECT_REASONS = [
    {"id": "NO_SCENE_IN_WINDOW", "label": "Нет снимка в окне ±N ч"},
    {"id": "CLOUD", "label": "Облачность над точкой"},
    {"id": "GLINT", "label": "Солнечные блики"},
    {"id": "NODATA", "label": "Нет данных/край снимка"},
    {"id": "LAND_OR_COAST", "label": "Суша/берег рядом"},
    {"id": "DT_TOO_LARGE", "label": "Слишком большой разрыв по времени"},
    {"id": "POSITION_UNCERTAIN", "label": "Неточная позиция наблюдения"},
    {"id": "NOT_DENSITY", "label": "Отдельный объект, а не плотность"},
    {"id": "SCOPE_MISMATCH", "label": "Другая целевая совокупность (не пластик)"},
    # additions (contract: only additions allowed)
    {"id": "MISSION_NOT_TARGET", "label": "Миссия не целевая (Landsat-7)"},
    {"id": "PROCESSING_ERROR", "label": "Ошибка обработки снимка"},
]
# Quality mask legend (contract 3.1). /api/v3/scenes/{id}/quality.png is rendered from quality.tif of
# scripts/case/pair_quality.py with exactly these RGBA colours (codes: 0 nodata, 1 water, 2 land, 3 SCL cloud/shadow/
# cirrus, 4 spectral cloud, 5 other = water-edge buffer/dark/defect). The mask has no per-pixel shadow and glint classes:
# SCL shadow is merged into code 3, glint is a strip-level check (B11 median), so "present": false for them.
QUALITY_CLASSES = [
    {"id": "valid", "label": "Пригодная вода", "color": "#00000000", "codes": [1], "present": True},
    {"id": "cloud", "label": "Облако (вкл. тень и перистые по SCL)", "color": "#ffffff99", "codes": [3, 4],
     "present": True},
    {"id": "shadow", "label": "Тень облака", "color": "#74c0fc99", "codes": [], "present": False,
     "note": "отдельно не выделяется: тень входит в класс «Облако»"},
    {"id": "glint", "label": "Блики", "color": "#ffd43b99", "codes": [], "present": False,
     "note": "блики оцениваются по полосе целиком (медиана B11), не по пикселям"},
    {"id": "land", "label": "Суша", "color": "#495057cc", "codes": [2], "present": True},
    {"id": "nodata", "label": "Нет данных / непригодно", "color": "#00000066", "codes": [0, 5], "present": True},
]
DETECTION_STATUSES = STATUSES[:3]
CONCENTRATION_STATUSES = [
    {"id": "measured_nearby", "label": "Есть полевое измерение рядом", "color": "#1c7ed6"},
    {"id": "research_estimate", "label": "Исследовательская оценка", "color": "#7048e8"},
    {"id": "unavailable", "label": "Концентрация недоступна", "color": "#adb5bd"},
]
DETECTION_STATUS_IDS = [s["id"] for s in DETECTION_STATUSES]
CONCENTRATION_STATUS_IDS = [s["id"] for s in CONCENTRATION_STATUSES]
PLASTIC_SCOPES = {"total_plastic", "plastic_category"}


def scope_label(scope: str) -> Optional[str]:
    for sc in SCOPES:
        if sc["id"] == scope:
            return sc["label"]
    return None


def _rgba(hex8: str) -> tuple:
    h = hex8.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4, 6))


def render_quality_png(tif: Path) -> bytes:
    """quality.tif (codes 0..5) -> RGBA PNG in the QUALITY_CLASSES palette."""
    def load(path):
        import numpy as np
        import rasterio
        from PIL import Image
        with rasterio.open(path) as ds:
            a = ds.read(1)
        lut = np.zeros((256, 4), dtype=np.uint8)
        lut[:] = _rgba("#00000066")  # unknown code = nodata
        for qc in QUALITY_CLASSES:
            for c in qc["codes"]:
                lut[c] = _rgba(qc["color"])
        img = Image.fromarray(lut[a.astype(np.uint8)], "RGBA")
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue()
    return _cached(f"qpng:{tif}", tif, load)


QUERY_LAYERS = ["scene", "quality", "observations", "zones", "pairs"]

_CAND_REASON = {
    "no_scene_in_window": "NO_SCENE_IN_WINDOW", "mission_not_launched": "NO_SCENE_IN_WINDOW",
    "dt>1d": "DT_TOO_LARGE", "cloud_cover>60": "CLOUD", "point_outside_footprint": "NODATA",
}
_PQ_REASON = {"cloud": "CLOUD", "glint": "GLINT", "insufficient_coverage": "NODATA", "land": "LAND_OR_COAST",
              "low_valid_water": "NODATA"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or {}


# ------------------------------------------------------------------ small helpers
_cache: dict[str, tuple] = {}
_lock = threading.Lock()


def _cached(key: str, path: Path, loader):
    try:
        st = path.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        sig = None
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == (str(path), sig):
            return hit[1]
    try:
        val = loader(path) if sig is not None else None
    except Exception as e:  # broken file = no data
        print(f"[case_store] {key}: cannot read {path}: {e}")
        val = None
    with _lock:
        _cache[key] = ((str(path), sig), val)
    return val


def _read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def fnum(x) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        v = float(x)
    else:
        s = str(x).strip()
        if s == "" or s.lower() in ("nan", "none", "null"):
            return None
        try:
            v = float(s)
        except ValueError:
            return None
    return v if math.isfinite(v) else None


def _r(x, nd=4):
    v = fnum(x)
    return None if v is None else round(v, nd)


def _truthy(x) -> bool:
    return str(x).strip().lower() in ("true", "1", "yes")


def iso_dt(s) -> Optional[str]:
    """'2016-04-08 10:40:22.03+00:00' / '...Z' -> '2016-04-08T10:40:22Z' (UTC)."""
    if s is None or str(s).strip() in ("", "nan"):
        return None
    t = str(s).strip().replace(" ", "T")
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        d = dt.datetime.fromisoformat(t)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mission_of(item_id: str, mission_raw: str = "") -> Optional[str]:
    s = (item_id or "").upper()
    if s.startswith("S2"):
        return "Sentinel-2"
    if s.startswith("LC08"):
        return "Landsat-8"
    if s.startswith("LC09"):
        return "Landsat-9"
    if s.startswith("LE07"):
        return "Landsat-7"
    m = (mission_raw or "").upper()
    if m in ("L8",):
        return "Landsat-8"
    if m in ("L9",):
        return "Landsat-9"
    if m.startswith("S2"):
        return "Sentinel-2"
    return None


def safe_event(eid: str) -> str:
    return eid.replace(":", "_").replace("/", "_")


# ------------------------------------------------------------------ parameter parsing (-> ApiError 400)
def parse_bbox(v) -> Optional[list[float]]:
    if v is None or v == "" or v == []:
        return None
    parts = v if isinstance(v, (list, tuple)) else str(v).split(",")
    try:
        b = [float(p) for p in parts]
    except (TypeError, ValueError):
        raise ApiError(400, "BAD_BBOX", "bbox: нужны 4 числа minLon,minLat,maxLon,maxLat", {"bbox": v})
    if len(b) != 4 or not all(math.isfinite(x) for x in b):
        raise ApiError(400, "BAD_BBOX", "bbox: нужны 4 числа minLon,minLat,maxLon,maxLat", {"bbox": v})
    if not (-180 <= b[0] <= 180 and -180 <= b[2] <= 180 and -90 <= b[1] <= 90 and -90 <= b[3] <= 90):
        raise ApiError(400, "BAD_BBOX", "bbox: долгота в [-180, 180], широта в [-90, 90]", {"bbox": b})
    if b[0] > b[2] or b[1] > b[3]:
        raise ApiError(400, "BAD_BBOX", "bbox: minLon ≤ maxLon и minLat ≤ maxLat (переход через 180° не поддерживается)",
                       {"bbox": b})
    return b


def parse_date(v, name: str) -> Optional[str]:
    if v is None or v == "":
        return None
    try:
        return dt.date.fromisoformat(str(v).strip()).isoformat()
    except ValueError:
        raise ApiError(400, "BAD_DATE", f"{name}: дата в формате ГГГГ-ММ-ДД", {name: v})


def parse_dates(d_from, d_to) -> tuple[Optional[str], Optional[str]]:
    a, b = parse_date(d_from, "date_from"), parse_date(d_to, "date_to")
    if a and b and a > b:
        raise ApiError(400, "BAD_DATE", "date_from позже date_to", {"date_from": a, "date_to": b})
    return a, b


def parse_list(v, name: str, allowed: Optional[list[str]] = None) -> Optional[list[str]]:
    if v is None or v == "" or v == []:
        return None
    items = v if isinstance(v, (list, tuple)) else str(v).split(",")
    out = sorted({str(x).strip() for x in items if str(x).strip()})
    if not out:
        return None
    if allowed is not None:
        bad = [x for x in out if x not in allowed]
        if bad:
            raise ApiError(400, "BAD_PARAM", f"{name}: неизвестные значения {', '.join(bad)}",
                           {"param": name, "unknown": bad, "allowed": allowed})
    return out


def parse_choice(v, name: str, allowed: list[str], default: Optional[str] = None) -> Optional[str]:
    if v is None or v == "":
        return default
    if v not in allowed:
        raise ApiError(400, "BAD_PARAM", f"{name}: допустимо {' | '.join(allowed)}", {"param": name, "value": v})
    return v


def parse_float(v, name: str, lo: float = -math.inf, hi: float = math.inf) -> Optional[float]:
    if v is None or v == "":
        return None
    x = fnum(v)
    if x is None or not (lo <= x <= hi):
        raise ApiError(400, "BAD_PARAM", f"{name}: нужно число в [{lo}, {hi}]", {"param": name, "value": v})
    return x


def parse_int(v, name: str, lo: int, hi: int, default: int) -> int:
    if v is None or v == "":
        return default
    try:
        x = int(str(v))
    except ValueError:
        raise ApiError(400, "BAD_PARAM", f"{name}: нужно целое в [{lo}, {hi}]", {"param": name, "value": v})
    if not (lo <= x <= hi):
        raise ApiError(400, "BAD_PARAM", f"{name}: нужно целое в [{lo}, {hi}]", {"param": name, "value": v})
    return x


def in_bbox(lon, lat, b) -> bool:
    if b is None:
        return True
    if lon is None or lat is None:
        return False
    return b[0] <= lon <= b[2] and b[1] <= lat <= b[3]


def bbox_intersects(bounds, b) -> bool:
    if b is None:
        return True
    if not bounds:
        return False
    return not (bounds[2] < b[0] or bounds[0] > b[2] or bounds[3] < b[1] or bounds[1] > b[3])


def in_dates(date: Optional[str], a: Optional[str], b: Optional[str]) -> bool:
    if a is None and b is None:
        return True
    if not date:
        return False
    d = date[:10]
    return (a is None or d >= a) and (b is None or d <= b)


# ------------------------------------------------------------------ raw tables
def samples_raw() -> tuple[list[str], list[dict]]:
    def load(p):
        with open(p, encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f)
            rows = list(r)
            return list(r.fieldnames or []), rows
    v = _cached("samples", PATHS["samples"], load)
    return v if v else ([], [])


def candidates_raw() -> list[dict]:
    return _cached("candidates", PATHS["pairs_dir"] / "candidates.csv", _read_csv) or []


def pair_quality_raw() -> list[dict]:
    return _cached("pair_quality", PATHS["pairs_dir"] / "pair_quality.csv", _read_csv) or []


def quality_meta(dirname: str) -> Optional[dict]:
    if not dirname or "/" in dirname or "\\" in dirname or ".." in dirname:
        return None
    p = PATHS["pairs_dir"] / "quality" / dirname / "meta.json"
    return _cached(f"qmeta:{dirname}", p, _read_json)


# ------------------------------------------------------------------ geometry
def _transformer(epsg: int):
    from pyproj import Transformer
    return Transformer.from_crs(f"EPSG:{int(epsg)}", "EPSG:4326", always_xy=True)


def utm_bounds_to_4326(bounds_utm, epsg) -> Optional[list[float]]:
    try:
        tr = _transformer(int(epsg))
        x0, y0, x1, y1 = [float(v) for v in bounds_utm]
        xs, ys = tr.transform([x0, x1, x1, x0], [y0, y0, y1, y1])
        return [round(min(xs), 6), round(min(ys), 6), round(max(xs), 6), round(max(ys), 6)]
    except Exception:
        return None


def bounds_polygon(b) -> Optional[dict]:
    if not b:
        return None
    return {"type": "Polygon", "coordinates": [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]]}


def strip_polygon(lon, lat, line, radius_m: float) -> Optional[dict]:
    """Observation strip in EPSG:4326: buffer (radius_m) of the transect line or of the point, built in local UTM."""
    try:
        from pyproj import Transformer
        from shapely.geometry import LineString, Point, mapping
        from shapely.ops import transform
        zone = int((lon + 180) // 6) + 1
        epsg = (32600 if lat >= 0 else 32700) + zone
        fwd = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform
        inv = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True).transform
        g = LineString(line) if line else Point(lon, lat)
        poly = transform(inv, transform(fwd, g).buffer(radius_m, quad_segs=8))
        m = mapping(poly)
        return {"type": m["type"], "coordinates": json.loads(json.dumps(m["coordinates"]),
                                                             parse_float=lambda s: round(float(s), 6))}
    except Exception:
        return None


# ------------------------------------------------------------------ observations
def _line(r: dict):
    """Transect start->end as ((lon, lat), (lon, lat)); None if missing or degenerate (start == end)."""
    a = (fnum(r.get("lon_start")), fnum(r.get("lat_start")))
    b = (fnum(r.get("lon_end")), fnum(r.get("lat_end")))
    if None in a or None in b or a == b:
        return None
    return a, b


def _obs_feature(r: dict, linked: list[str], geometry: str = "point") -> dict:
    lon, lat = fnum(r.get("longitude")), fnum(r.get("latitude"))
    tr = None
    if fnum(r.get("lat_start")) is not None and fnum(r.get("lon_start")) is not None:
        tr = {"lat_start": fnum(r.get("lat_start")), "lon_start": fnum(r.get("lon_start")),
              "lat_end": fnum(r.get("lat_end")), "lon_end": fnum(r.get("lon_end")),
              "length_km": fnum(r.get("transect_length_km")), "width_m": fnum(r.get("transect_width_m"))}
    geom = {"type": "Point", "coordinates": [lon, lat]} if lon is not None and lat is not None else None
    ln = _line(r) if geometry == "line" else None
    if ln:
        geom = {"type": "LineString", "coordinates": [list(ln[0]), list(ln[1])]}
    flags = [x.strip() for x in (r.get("quality_flags") or "").split(";") if x.strip()]
    is_item = r.get("record_type") == "item_observation"  # a single object, not a density
    missions = [x.strip() for x in (r.get("missions_calendar_eligible") or "").split(";") if x.strip()]
    props = {
        "kind": "measurement",
        "sample_id": r.get("sample_id"),
        "event_id": r.get("event_id") or None,
        "source_id": r.get("source_id") or None,
        "region": r.get("region") or None,
        "sea_area": r.get("sea_area") or None,
        "record_type": r.get("record_type") or None,
        "date_utc": r.get("date_utc") or None,
        "time_start_utc": r.get("time_start_utc") or None,
        "time_end_utc": r.get("time_end_utc") or None,
        "target_scope": r.get("target_scope") or None,
        "measurement_profile": r.get("measurement_profile") or None,
        "size_class": r.get("size_class") or None,
        "target_scope_label": scope_label(r.get("target_scope") or ""),
        "is_plastic_scope": (r.get("target_scope") or "") in PLASTIC_SCOPES,
        "concentration_items_km2": None if is_item else fnum(r.get("concentration_items_km2")),
        "concentration_g_km2": None if is_item else fnum(r.get("concentration_g_km2")),
        "sampled_area_km2": fnum(r.get("sampled_area_km2")),
        "transect": tr,
        "position_role": r.get("position_role") or None,
        "quality_flags": flags,
        "zero_scope": r.get("zero_scope") or None,
        "missions_calendar_eligible": missions,
        "litter_item_type": r.get("litter_item_type") or None,
        "material": r.get("material") or None,
        "notes": r.get("notes") or None,
        "source_doi": r.get("source_doi") or None,
        "linked_scenes": linked,
    }
    return {"type": "Feature", "id": r.get("sample_id"), "geometry": geom, "properties": props}


def linked_scenes_by_sample() -> dict[str, list[str]]:
    out: dict[str, set] = {}
    for p in pairs_all():
        if p["status"] == "accepted" and p["scene_id"]:
            out.setdefault(p["sample_id"], set()).add(p["scene_id"])
    return {k: sorted(v) for k, v in out.items()}


def filter_samples(bbox=None, date_from=None, date_to=None, sources=None, profiles=None, scopes=None,
                   record_type=None) -> list[dict]:
    _, rows = samples_raw()
    out = []
    for r in rows:
        if sources and r.get("source_id") not in sources:
            continue
        if profiles and r.get("measurement_profile") not in profiles:
            continue
        if scopes and r.get("target_scope") not in scopes:
            continue
        if record_type and r.get("record_type") != record_type:
            continue
        if not in_dates(r.get("date_utc"), date_from, date_to):
            continue
        if not in_bbox(fnum(r.get("longitude")), fnum(r.get("latitude")), bbox):
            continue
        out.append(r)
    out.sort(key=lambda r: r.get("sample_id") or "")
    return out


def observations_fc(rows: list[dict], geometry: str = "point", limit: Optional[int] = None) -> dict:
    total = len(rows)
    if limit is not None:
        rows = rows[:limit]
    linked = linked_scenes_by_sample() if rows else {}
    feats = [_obs_feature(r, linked.get(r.get("sample_id"), []), geometry) for r in rows]
    fc = {"type": "FeatureCollection", "kind": "measurement", "count": len(feats), "total": total,
          "empty_reason": None, "features": feats}
    if not feats:
        fc["empty_reason"] = ("Файл полевых наблюдений не найден" if not samples_raw()[1]
                              else "Нет наблюдений под выбранные фильтры")
    return fc


def sample_row(sample_id: str) -> Optional[dict]:
    for r in samples_raw()[1]:
        if r.get("sample_id") == sample_id:
            return r
    return None


# ------------------------------------------------------------------ pairs
def _pq_index() -> dict[tuple[str, str], dict]:
    return {(r.get("event_id", ""), r.get("scene_id", "")): r for r in pair_quality_raw()}


def _codes(raw: str, table: dict) -> list[str]:
    out = []
    for part in (raw or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if part.startswith("mission_not_target"):
            c = "MISSION_NOT_TARGET"
        elif part.startswith("read_error") or part.startswith("error"):
            c = "PROCESSING_ERROR"
        else:
            c = table.get(part)
        if c and c not in out:
            out.append(c)
    return out


def _pairs_build(cands, pq_rows, samples) -> list[dict]:
    pq = {(r.get("event_id", ""), r.get("scene_id", "")): r for r in pq_rows}
    by_event: dict[str, list[dict]] = {}
    for s in samples:
        by_event.setdefault(s.get("event_id", ""), []).append(s)
    seen = set()
    out = []
    for c in cands:
        ev = c.get("event_id", "")
        item = (c.get("item_id") or "").strip() or None
        key_scene = item or f"none:{c.get('endpoint', '')}:{c.get('collection', '')}"
        accept = _truthy(c.get("accept"))
        raw = c.get("reject_reason") or ""
        reasons = [] if accept else _codes(raw, _CAND_REASON)
        if not accept and not reasons:
            reasons = ["NO_SCENE_IN_WINDOW"] if not item else []
        q = pq.get((ev, item or ""))
        cloud_local = valid_local = None
        quality_decision = None
        if q:
            cloud_local = _r(fnum(q.get("cloud_frac")) * 100, 2) if fnum(q.get("cloud_frac")) is not None else None
            valid_local = _r(q.get("valid_water_frac"))
            quality_decision = q.get("decision") or None
            if accept and quality_decision != "accept":
                accept = False
                reasons = _codes(q.get("reason") or "", _PQ_REASON) or ["PROCESSING_ERROR"]
        for s in by_event.get(ev, []):
            sid = s.get("sample_id")
            pid = f"{sid}__{key_scene}"
            if pid in seen:
                continue
            seen.add(pid)
            lon, lat = fnum(s.get("longitude")), fnum(s.get("latitude"))
            geom = {"type": "Point", "coordinates": [lon, lat]} if lon is not None else None
            ln = _line(s)
            if ln:
                geom = {"type": "LineString", "coordinates": [list(ln[0]), list(ln[1])]}
            out.append({
                "pair_id": pid, "sample_id": sid, "event_id": ev, "source_id": s.get("source_id"),
                "scene_id": item, "mission": mission_of(item or "", c.get("mission", "")),
                "scene_datetime": iso_dt(c.get("scene_datetime")), "obs_datetime": iso_dt(c.get("obs_datetime")),
                "dt_hours": _r(c.get("dt_hours"), 2),
                "distance_km": None, "drift_shift_km": None, "geometry": geom,
                "cloud_pct_local": cloud_local, "valid_fraction_local": valid_local,
                "status": "accepted" if accept else "rejected", "reject_reasons": reasons,
                "split": None,
                # additions
                "scene_cloud_pct": _r(c.get("cloud_cover"), 2),
                "catalog": f"{c.get('endpoint', '')}/{c.get('collection', '')}",
                "time_known": _truthy(c.get("time_known")),
                "registry_note": raw or None,
                "quality_decision": quality_decision,
            })
    out.sort(key=lambda p: p["pair_id"])
    return out


def pairs_all() -> list[dict]:
    cands, pq = candidates_raw(), pair_quality_raw()
    fields, samples = samples_raw()
    key = ("pairs", id(cands), id(pq), id(samples))
    with _lock:
        hit = _cache.get("pairs_built")
        if hit and hit[0] == key:
            return hit[1]
    val = _pairs_build(cands, pq, samples) if cands and samples else []
    with _lock:
        _cache["pairs_built"] = (key, val)
    return val


def filter_pairs(sample_id=None, scene_id=None, status="all", max_dt_hours=None, date_from=None, date_to=None,
                 sources=None) -> list[dict]:
    out = []
    for p in pairs_all():
        if sample_id and p["sample_id"] != sample_id:
            continue
        if scene_id and p["scene_id"] != scene_id:
            continue
        if status in ("accepted", "rejected") and p["status"] != status:
            continue
        if max_dt_hours is not None and (p["dt_hours"] is None or abs(p["dt_hours"]) > max_dt_hours):
            continue
        if sources and p["source_id"] not in sources:
            continue
        if not in_dates(p["obs_datetime"], date_from, date_to):
            continue
        out.append(p)
    return out


def pairs_empty_reason(n: int) -> Optional[str]:
    if n:
        return None
    if not candidates_raw():
        return "Реестр пар не найден (data/pairs/candidates.csv) — запустите scripts/case/find_pairs.py"
    return "Нет пар под выбранные фильтры"


# ------------------------------------------------------------------ field model predictions (field_estimate)
KNN_MODELS = ("knn5", "knn5_log")
CASE_SELECTION_YAML = REPO / "configs" / "case_selection.yaml"


def predictions_raw() -> list[dict]:
    return _cached("predictions", PATHS["conc_metrics"].parent / "predictions.csv", _read_csv) or []


def main_split(profile: str) -> Optional[str]:
    """Main (honest) split of the concentration models (L60b): metrics.json[profile].main_split, else built from
    configs/case_selection.yaml main_split (route + buffer_days -> 'route_buf1'). Never the leaky 'event' split."""
    cm = _cached("conc_metrics", PATHS["conc_metrics"], _read_json) or {}
    ms = (cm.get(profile) or {}).get("main_split") if isinstance(cm.get(profile), dict) else None
    if isinstance(ms, str) and ms:
        return ms
    def _yaml(p):
        import yaml
        return yaml.safe_load(p.read_text(encoding="utf-8"))
    cfg = (_cached("case_selection", CASE_SELECTION_YAML, _yaml) or {}).get("main_split") or {}
    if not cfg.get("method"):
        return None
    bd = fnum(cfg.get("buffer_days"))
    return f"{cfg['method']}_buf{int(bd) if bd and float(bd).is_integer() else bd}" if bd else str(cfg["method"])


def field_model_choice(profile: str) -> dict:
    """Which field model is the main one for a profile on the main split.

    kNN is used only if EVERY kNN variant beats the train median significantly (95 % bootstrap CI of ΔMAE
    entirely < 0 in metrics.json bootstrap_mae_diff_vs_median) — L60b: a win that depends on the variant is not a
    result. Otherwise the train-profile median ("median_train")."""
    split = main_split(profile)
    cm = _cached("conc_metrics", PATHS["conc_metrics"], _read_json) or {}
    boot = ((cm.get(profile) or {}) if isinstance(cm.get(profile), dict) else {}).get(
        "bootstrap_mae_diff_vs_median") or {}
    cis = {m: boot.get(f"{split}:{m}") for m in KNN_MODELS}
    sig = bool(split) and all(isinstance(c, list) and len(c) == 3 and fnum(c[2]) is not None and c[2] < 0
                              for c in cis.values())
    if sig:
        rows = {(r.get("split"), r.get("model")): r for r in (cm.get(profile) or {}).get("overall", [])}
        best = min(KNN_MODELS, key=lambda m: fnum((rows.get((split, m)) or {}).get("mae")) or math.inf)
        return {"split": split, "model": best, "name": best, "knn_significant": True,
                "delta_mae_ci95": {m: cis[m] for m in KNN_MODELS}}
    return {"split": split, "model": "median", "name": "median_train", "knn_significant": False,
            "delta_mae_ci95": {m: cis[m] for m in KNN_MODELS}}


def field_estimate(sample_ids: list[str]) -> Optional[dict]:
    """Out-of-fold prediction of the main field model on the MAIN split for a linked sample.

    Not a satellite estimate. None when no linked sample is in reports/case_conc/predictions.csv
    (predictions exist only for S1/S2 profiles; pair_quality zones are S3/S4) or the main split is unknown."""
    if not sample_ids:
        return None
    wanted = set(sample_ids)
    choices: dict[str, dict] = {}
    for r in predictions_raw():
        if r.get("sample_id") not in wanted:
            continue
        prof = r.get("profile") or ""
        ch = choices.get(prof) or choices.setdefault(prof, field_model_choice(prof))
        if not ch["split"] or r.get("split") != ch["split"] or r.get("model") != ch["model"]:
            continue
        srow = sample_row(r["sample_id"]) or {}
        is_median = ch["name"] == "median_train"
        return {"value": _r(r.get("y_pred"), 3), "lo": None, "hi": None, "interval": None,
                "unit": "items/km2", "measurement_profile": srow.get("measurement_profile") or None,
                "profile_config": prof, "model": ch["name"], "split": ch["split"],
                "knn_significant": ch["knn_significant"], "delta_mae_ci95_vs_median": ch["delta_mae_ci95"],
                "sample_id": r["sample_id"],
                "label": ("оценка по полевым данным, не по снимку: медиана обучающих участков маршрута "
                          "(kNN не значимо лучше медианы)" if is_median else
                          "оценка по полевым данным, не по снимку: kNN по соседним участкам маршрута"),
                "interval_note": "интервал прогноза не считался", "basis": "field_model"}
    return None


# ------------------------------------------------------------------ zones + scenes (from pair_quality)
_ZONE_REASON_RU = {"cloud": "облачность в полосе наблюдения", "glint": "солнечные блики",
                   "insufficient_coverage": "снимок не покрывает полосу", "land": "суша в полосе"}


def zones_all() -> list[dict]:
    feats = []
    for q in pair_quality_raw():
        d = q.get("dir") or safe_event(q.get("event_id", ""))
        meta = quality_meta(d) or {}
        qm = meta.get("quality") or {}
        det = meta.get("detector") or {}
        sids = sorted(x for x in (q.get("sample_ids") or "").split(";") if x)
        rows = [sample_row(s) for s in sids]
        rows = [r for r in rows if r]
        lon = lat = None
        line = None
        if rows:
            r0 = rows[0]
            lon, lat = fnum(r0.get("longitude")), fnum(r0.get("latitude"))
            line = _line(r0) if meta.get("has_line", True) else None
        radius = fnum(((meta.get("config") or {}).get("strip") or {}).get("point_buffer_m")) or 500.0
        if line:
            width = fnum(rows[0].get("transect_width_m"))
            radius = max(width / 2.0 if width else 10.0, 10.0)
        geom = strip_polygon(lon, lat, line, radius) if lon is not None and lat is not None else None
        # the field density row behind field_items_km2: meta.field_sample_id (L61), else first transect_density row
        frow = sample_row(meta.get("field_sample_id") or "") or next(
            (r for r in rows if r.get("record_type") == "transect_density"), None) or {}
        decision = q.get("decision") or ""
        n_det = fnum(q.get("n_det"))
        if decision == "accept" and n_det is not None and n_det > 0:
            status, reason = "detected", f"детектор: {int(n_det)} объект(ов) в полосе наблюдения"
        elif decision == "accept" and n_det is not None:
            status, reason = "not_detected", "детектор не нашёл мусора в полосе наблюдения (порог LightGBM)"
        else:
            rs = q.get("reason") or "нет результата детектора"
            status, reason = "insufficient_data", "снимок непригоден: " + _ZONE_REASON_RU.get(rs, rs)
        scene_id = q.get("scene_id") or None
        prob_max = fnum(q.get("prob_max"))
        feats.append({"type": "Feature", "id": f"Z-{d}", "geometry": geom, "properties": {
            "kind": "model_estimate", "zone_id": f"Z-{d}", "scene_id": scene_id,
            "mission": mission_of(scene_id or ""), "datetime": iso_dt(q.get("scene_datetime")),
            "status": status, "detection_status": status, "status_reason": reason,
            "concentration_status": "unavailable",
            "concentration_reason": "нет проверенного переноса «снимок → шт./км²»; спутниковая концентрация не выдаётся",
            "area_km2": _r(q.get("strip_area_km2")),
            "area_basis": "полоса наблюдения (буфер трансекты/точки), по которой проверен снимок",
            "detected_area_m2": _r(q.get("det_area_m2"), 1),
            "detector": {"prob_mean": None, "prob_max": _r(prob_max), "n_pixels": det.get("det_px_strip"),
                         "n_objects": None if n_det is None else int(n_det),
                         "threshold": det.get("threshold")},
            "field_estimate": field_estimate(sids),
            "concentration": None,
            "quality": {"valid_fraction": _r(q.get("valid_water_frac")), "cloud_fraction": _r(q.get("cloud_frac")),
                        "glint_fraction": None, "land_fraction": _r(q.get("land_frac")),
                        "coverage": _r(q.get("coverage")), "glint_b11_median": _r(q.get("glint_b11_median")),
                        "flags": []},
            "support": {"n_linked_samples": len(sids), "linked_sample_ids": sids, "nearest_measurement_km": 0.0,
                        "field_items_km2": fnum(q.get("field_items_km2")),
                        "field_sample_id": frow.get("sample_id") or None,
                        "field_target_scope": frow.get("target_scope") or None,
                        "field_scope_label": scope_label(frow.get("target_scope") or "")},
            "event_id": q.get("event_id"), "quality_dir": d,
        }})
    feats.sort(key=lambda f: f["id"])
    return feats


def zone_status_tags(p: dict) -> set:
    """Old 5-value `status` filter (3.0) -> matches detection_status or concentration_status (3.1)."""
    tags = {p["detection_status"]}
    if p["concentration_status"] == "unavailable":
        tags.add("concentration_unavailable")
    elif p["concentration_status"] == "research_estimate":
        tags.add("research_estimate")
    return tags


def filter_zones(bbox=None, date_from=None, date_to=None, scene_id=None, statuses=None, profiles=None,
                 min_area_km2=None, detection_statuses=None, concentration_statuses=None) -> list[dict]:
    out = []
    for f in zones_all():
        p = f["properties"]
        if scene_id and p["scene_id"] != scene_id:
            continue
        if statuses and not (set(statuses) & zone_status_tags(p)):
            continue
        if detection_statuses and p["detection_status"] not in detection_statuses:
            continue
        if concentration_statuses and p["concentration_status"] not in concentration_statuses:
            continue
        if min_area_km2 is not None and (p["area_km2"] is None or p["area_km2"] < min_area_km2):
            continue
        if not in_dates(p["datetime"], date_from, date_to):
            continue
        if bbox is not None:
            g = f["geometry"]
            if not g or not bbox_intersects(_geom_bounds(g), bbox):
                continue
        if profiles:
            profs = {(sample_row(s) or {}).get("measurement_profile") for s in p["support"]["linked_sample_ids"]}
            if not profs & set(profiles):
                continue
        out.append(f)
    return out


def _geom_bounds(g) -> Optional[list[float]]:
    pts = []

    def walk(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            pts.append(c)
        elif isinstance(c, (list, tuple)):
            for x in c:
                walk(x)
    walk(g.get("coordinates"))
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def zones_fc(feats: list[dict]) -> dict:
    lg = _cached("lgbm_meta", PATHS["lgbm_meta"], _read_json) or {}
    fc = {"type": "FeatureCollection", "kind": "model_estimate", "count": len(feats), "empty_reason": None,
          "model": {"detector": "lgbm" if lg else None, "concentration": None,
                    "trained_at": lg.get("trained_at") or lg.get("created") or None,
                    "concentration_note": "калибровки спутник → шт./км² нет; концентрация в зонах не выдаётся"},
          "features": feats}
    if not feats:
        fc["empty_reason"] = ("Проверка пар снимком ещё не выполнена (data/pairs/pair_quality.csv) — недостаточно данных"
                              if not pair_quality_raw() else "Нет зон под выбранные фильтры")
    return fc


def quality_file(scene_id: str, name: str) -> Optional[Path]:
    """PNG of the first (sorted) pair_quality dir with this scene."""
    dirs = sorted((q.get("dir") or safe_event(q.get("event_id", ""))) for q in pair_quality_raw()
                  if q.get("scene_id") == scene_id)
    for d in dirs:
        p = PATHS["pairs_dir"] / "quality" / d / name
        if p.is_file():
            return p
    return None


def scenes_all() -> list[dict]:
    pairs = pairs_all()
    zones = zones_all()
    by_scene: dict[str, dict] = {}
    for p in pairs:
        sid = p["scene_id"]
        if not sid:
            continue
        s = by_scene.setdefault(sid, {"scene_id": sid, "mission": p["mission"], "source": p["catalog"].split("/")[0],
                                       "collection": p["catalog"].split("/", 1)[-1],
                                       "datetime": p["scene_datetime"], "cloud_pct": p["scene_cloud_pct"],
                                       "_acc": False, "_reasons": set(), "_samples": set()})
        s["_samples"].add(p["sample_id"])
        if p["status"] == "accepted":
            s["_acc"] = True
        else:
            s["_reasons"].update(p["reject_reasons"])
    zone_by_scene: dict[str, list[dict]] = {}
    for z in zones:
        if z["properties"]["scene_id"]:
            zone_by_scene.setdefault(z["properties"]["scene_id"], []).append(z)
    for q in pair_quality_raw():  # scenes that only exist in pair_quality
        sid = q.get("scene_id")
        if sid and sid not in by_scene:
            by_scene[sid] = {"scene_id": sid, "mission": mission_of(sid), "source": (q.get("source") or "").split("/")[0],
                             "collection": (q.get("source") or "").split("/", 1)[-1],
                             "datetime": iso_dt(q.get("scene_datetime")), "cloud_pct": None,
                             "_acc": q.get("decision") == "accept", "_reasons": set(),
                             "_samples": {x for x in (q.get("sample_ids") or "").split(";") if x}}
    out = []
    for sid in sorted(by_scene):
        s = by_scene[sid]
        zs = zone_by_scene.get(sid, [])
        bounds = None
        valid = None
        for z in zs:
            meta = quality_meta(z["properties"]["quality_dir"]) or {}
            if meta.get("bounds_utm") and meta.get("epsg"):
                bounds = utm_bounds_to_4326(meta["bounds_utm"], meta["epsg"])
                valid = z["properties"]["quality"]["valid_fraction"]
                break
        has_rgb = quality_file(sid, "rgb.png") is not None
        has_q = quality_file(sid, "quality.tif") is not None  # PNG is rendered from the tif (meta.quality_classes)
        has_m = quality_file(sid, "mask.png") is not None
        base = f"/api/v3/scenes/{sid}"
        reasons = sorted(s["_reasons"]) if not s["_acc"] else []
        out.append({
            "scene_id": sid, "mission": s["mission"], "source": s["source"] or None, "collection": s["collection"] or None,
            "datetime": s["datetime"],
            "footprint": bounds_polygon(bounds), "bounds": bounds,
            "footprint_note": "bounds = вырезка вокруг наблюдения (UTM → EPSG:4326), не вся сцена" if bounds else
                              "геометрия сцены не сохранена в реестре",
            "cloud_pct": s["cloud_pct"], "valid_water_fraction": valid,
            "preview_url": f"{base}/rgb.png" if has_rgb else None,
            "quality_url": f"{base}/quality.png" if has_q else None,
            "prob_url": None,
            "mask_url": f"{base}/mask.png" if has_m else None,
            "tiles": None,
            "n_zones": len(zs), "n_linked_samples": len(s["_samples"]),
            "status": "accepted" if s["_acc"] else "rejected", "reject_reasons": reasons,
        })
    return out


def filter_scenes(bbox=None, date_from=None, date_to=None, missions=None, status="all") -> list[dict]:
    out = []
    for s in scenes_all():
        if missions and s["mission"] not in missions:
            continue
        if status in ("accepted", "rejected") and s["status"] != status:
            continue
        if not in_dates(s["datetime"], date_from, date_to):
            continue
        if bbox is not None and not bbox_intersects(s["bounds"], bbox):
            continue
        out.append(s)
    return out


def scenes_empty_reason(n: int) -> Optional[str]:
    if n:
        return None
    if not candidates_raw() and not pair_quality_raw():
        return "Реестр снимков не найден (data/pairs/) — недостаточно данных"
    return "Нет снимков под выбранные фильтры"


# ------------------------------------------------------------------ meta
def meta() -> dict:
    _, rows = samples_raw()
    dates = sorted(r["date_utc"] for r in rows if r.get("date_utc"))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("source_id", "")] = counts.get(r.get("source_id", ""), 0) + 1
    sdates = sorted(p["scene_datetime"][:10] for p in pairs_all() if p["scene_datetime"])
    return {
        "version": "3.0",
        "generated_at": now_iso(),
        "units": {"concentration": "items/km2", "mass": "g/km2", "area": "km2", "distance": "km", "time_shift": "hours"},
        "date_range": {"min": dates[0] if dates else None, "max": dates[-1] if dates else None},
        "scene_date_range": {"min": sdates[0], "max": sdates[-1]} if sdates else None,
        "scene_date_range_basis": "даты снимков-кандидатов реестра пар (принятых и отклонённых)",
        "detection_statuses": DETECTION_STATUSES,
        "concentration_statuses": CONCENTRATION_STATUSES,
        "statuses": STATUSES,
        "sources": [{"id": i, "label": lab, "n": counts.get(i, 0)} for i, lab in SOURCES],
        "measurement_profiles": PROFILES,
        "target_scopes": SCOPES,
        "record_types": RECORD_TYPES,
        "missions": MISSIONS,
        "reject_reasons": REJECT_REASONS,
        "quality_classes": QUALITY_CLASSES,
        "data_status": {"observations": len(rows), "pairs": len(pairs_all()), "pair_quality": len(pair_quality_raw())},
    }


# ------------------------------------------------------------------ metrics
def metrics() -> dict:
    fn = _cached("final_numbers", PATHS["final_numbers"], _read_json) or {}
    test = _cached("lgbm_test", PATHS["lgbm_test"], _read_json) or {}
    lg = fn.get("l3_lgbm") or {}
    base = ((fn.get("baselines") or {}).get("rows") or {}).get("fdi_threshold") or {}
    val = lg.get("val") or (test.get("val_md") and {"f1_md": test["val_md"].get("f1_md"),
                                                     "iou_md": test["val_md"].get("iou_md")}) or {}
    tmd = test.get("test_md") or lg.get("test") or {}
    detector = {
        "split": "MARIDA val (сплит по сценам); test — один прогон после заморозки",
        "baseline": {"name": "FDI threshold", "f1": _r(base.get("f1_md")), "iou": _r(base.get("iou_md")),
                     "split": "val", "note": base.get("note")} if base else None,
        "main": {"name": "LightGBM", "f1": _r(val.get("f1_md")), "iou": _r(val.get("iou_md")), "split": "val",
                 "test_f1": _r(tmd.get("f1_md")), "test_iou": _r(tmd.get("iou_md")),
                 "test_ci95_f1": tmd.get("ci95"), "threshold": tmd.get("threshold")} if (val or tmd) else None,
    }
    cm = _cached("conc_metrics", PATHS["conc_metrics"], _read_json) or {}
    conc_profiles = {}
    for prof, d in sorted(cm.items()):
        if not isinstance(d, dict) or "overall" not in d:
            continue
        rows = {(r.get("split"), r.get("model")): r for r in d["overall"]}

        ch = field_model_choice(prof)
        split = ch["split"]

        def pick(model, name=None):
            r = rows.get((split, model))
            if not r:
                return None
            return {"name": name or model, "mae": _r(r.get("mae")), "mae_log": _r(r.get("log1p_mae")),
                    "coverage": None, "n": r.get("n"), "split": split}
        best_knn = min(KNN_MODELS, key=lambda m: fnum((rows.get((split, m)) or {}).get("mae")) or math.inf)
        challenger = pick(best_knn)
        if challenger:
            ci = ch["delta_mae_ci95"].get(best_knn)
            challenger["delta_mae_vs_median_ci95"] = ci
            challenger["significant"] = bool(isinstance(ci, list) and len(ci) == 3 and ci[2] < 0)
        conc_profiles[prof] = {"rows": d.get("rows"), "events": d.get("events"), "main_split": split,
                               "baseline": pick("median", "median by profile (train)"), "main": challenger,
                               "selected_for_field_estimate": ch["name"], "knn_significant_all_variants":
                                   ch["knn_significant"],
                               "by_split": {f"{s}:{m}": {"mae": _r(r.get("mae")), "mae_log": _r(r.get("log1p_mae"))}
                                            for (s, m), r in sorted(rows.items())}}
    default = "S2_visual_total_plastic" if "S2_visual_total_plastic" in conc_profiles else \
        (sorted(conc_profiles)[0] if conc_profiles else None)
    dp = conc_profiles.get(default) or {}
    concentration = {"baseline": dp.get("baseline"), "main": dp.get("main"), "unit": "items/km2",
                     "profile": default, "profiles": conc_profiles,
                     "note": "полевая концентрация по координатам/времени, основной сплит — участки маршрута + "
                             "буфер 1 сут (configs/case_selection.yaml main_split, L60b); kNN считается лучше медианы "
                             "только если все варианты значимы; coverage = null: интервалы предсказаний не считались"}
    try:
        from src.macroplastic.case.concentration import concentration as conc_fn
        ce = conc_fn(12, 0.20)
        computed = _r(ce.value)
    except Exception:
        computed = round(12 / 0.20, 4)
    return {
        "split": {"type": "grouped", "group_by": ["event_id", "scene_id"], "n_train": None, "n_val": None, "n_test": None,
                  "detector": {"n_val_scenes": (test.get("val_md") or {}).get("n_scenes"),
                               "n_test_scenes": tmd.get("n_scenes")},
                  "concentration": {"type": "участки маршрута (5 блоков) + буфер 1 сут", "name": dp.get("main_split"),
                                    "n": dp.get("rows")}},
        "detector": detector,
        "concentration": concentration,
        "control_example": {"items": 12, "area_km2": 0.20, "expected": 60.0, "computed": computed},
        "empty_reason": None if (fn or test or cm) else "Метрики ещё не посчитаны",
    }


# ------------------------------------------------------------------ saved queries
_qlock = threading.Lock()


def normalize_query(q) -> dict:
    if q is None:
        q = {}
    if not isinstance(q, dict):
        raise ApiError(400, "BAD_PARAM", "query: ожидается JSON-объект", {})
    known = {"bbox", "date_from", "date_to", "statuses", "sources", "profiles", "layers", "scene_id"}
    unknown = sorted(set(q) - known)
    if unknown:
        raise ApiError(400, "BAD_PARAM", f"query: неизвестные поля {', '.join(unknown)}", {"unknown": unknown})
    a, b = parse_dates(q.get("date_from"), q.get("date_to"))
    sid = q.get("scene_id")
    if sid is not None and not isinstance(sid, str):
        raise ApiError(400, "BAD_PARAM", "query.scene_id: строка или null", {})
    for k in ("statuses", "sources", "profiles", "layers"):
        if q.get(k) is not None and not isinstance(q.get(k), list):
            raise ApiError(400, "BAD_PARAM", f"query.{k}: ожидается список", {"param": k})
    bbox = q.get("bbox")
    if bbox is not None and not isinstance(bbox, list):
        raise ApiError(400, "BAD_BBOX", "query.bbox: список [minLon, minLat, maxLon, maxLat] или null", {})
    return {
        "bbox": parse_bbox(bbox),
        "date_from": a, "date_to": b,
        "statuses": parse_list(q.get("statuses"), "statuses", STATUS_IDS) or [],
        "sources": parse_list(q.get("sources"), "sources", SOURCE_IDS) or [],
        "profiles": parse_list(q.get("profiles"), "profiles", PROFILE_IDS) or [],
        "layers": parse_list(q.get("layers"), "layers", QUERY_LAYERS) or [],
        "scene_id": sid or None,
    }


def _read_queries() -> list[dict]:
    p = PATHS["queries"]
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def list_queries() -> list[dict]:
    with _qlock:
        return sorted(_read_queries(), key=lambda q: (q.get("created_at", ""), q.get("query_id", "")))


def get_query(qid: str) -> dict:
    for q in list_queries():
        if q.get("query_id") == qid:
            return q
    raise ApiError(404, "NOT_FOUND", f"Сохранённый запрос {qid} не найден", {"query_id": qid})


def add_query(body) -> dict:
    if not isinstance(body, dict):
        raise ApiError(400, "BAD_PARAM", "тело: ожидается {\"name\": ..., \"query\": {...}}", {})
    name = body.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise ApiError(400, "BAD_PARAM", "name: непустая строка до 200 символов", {})
    rec = {"query_id": "q_" + uuid.uuid4().hex[:8], "name": name.strip(), "created_at": now_iso(),
           "query": normalize_query(body.get("query"))}
    p = PATHS["queries"]
    with _qlock:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def delete_query(qid: str) -> None:
    p = PATHS["queries"]
    with _qlock:
        rows = _read_queries()
        keep = [r for r in rows if r.get("query_id") != qid]
        if len(keep) == len(rows):
            raise ApiError(404, "NOT_FOUND", f"Сохранённый запрос {qid} не найден", {"query_id": qid})
        tmp = p.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep), encoding="utf-8")
        tmp.replace(p)


# ------------------------------------------------------------------ CSV export
ZONE_COLS = ["zone_id", "scene_id", "mission", "datetime", "status", "area_km2", "detected_area_m2",
             "concentration_items_km2", "conc_lo", "conc_hi", "interval", "unit", "measurement_profile", "size_class",
             "target_scope", "prob_mean", "valid_fraction", "cloud_fraction", "flags", "n_linked_samples",
             "linked_sample_ids", "centroid_lon", "centroid_lat", "kind"]
ZONE_COLS_31 = ZONE_COLS + ["detection_status", "concentration_status", "field_estimate_items_km2"]  # 3.1: appended
PAIR_COLS = ["pair_id", "sample_id", "event_id", "source_id", "scene_id", "mission", "scene_datetime", "obs_datetime",
             "dt_hours", "distance_km", "drift_shift_km", "geometry", "cloud_pct_local", "valid_fraction_local",
             "status", "reject_reasons", "split", "scene_cloud_pct", "catalog", "time_known", "registry_note",
             "quality_decision"]


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return ";".join(str(x) for x in v)
    return str(v)


def _csv(cols: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in rows:
        w.writerow([_cell(v) for v in r])
    return buf.getvalue()


def wkt(g) -> str:
    if not g:
        return ""
    t, c = g.get("type"), g.get("coordinates")
    if t == "Point":
        return f"POINT ({c[0]} {c[1]})"
    if t == "LineString":
        return "LINESTRING (" + ", ".join(f"{x} {y}" for x, y in c) + ")"
    if t == "Polygon":
        return "POLYGON (" + ", ".join("(" + ", ".join(f"{x} {y}" for x, y in ring) + ")" for ring in c) + ")"
    return ""


def observations_csv(rows: list[dict]) -> str:
    fields, _ = samples_raw()
    linked = linked_scenes_by_sample() if rows else {}
    cols = list(fields) + ["kind", "linked_scenes"]
    return _csv(cols, [[r.get(k, "") for k in fields] + ["measurement", linked.get(r.get("sample_id"), [])]
                       for r in rows])


def pairs_csv(pairs: list[dict]) -> str:
    return _csv(PAIR_COLS, [[wkt(p["geometry"]) if k == "geometry" else p.get(k) for k in PAIR_COLS] for p in pairs])


def zones_csv(feats: list[dict]) -> str:
    out = []
    for f in feats:
        p = f["properties"]
        c = p.get("concentration") or {}
        b = _geom_bounds(f["geometry"]) if f.get("geometry") else None
        cen = [round((b[0] + b[2]) / 2, 6), round((b[1] + b[3]) / 2, 6)] if b else [None, None]
        out.append([p["zone_id"], p["scene_id"], p["mission"], p["datetime"], p["status"], p["area_km2"],
                    p["detected_area_m2"], c.get("value"), c.get("lo"), c.get("hi"), c.get("interval"),
                    c.get("unit") or "items/km2", c.get("measurement_profile"), c.get("size_class"),
                    c.get("target_scope"), p["detector"].get("prob_mean"), p["quality"]["valid_fraction"],
                    p["quality"]["cloud_fraction"], p["quality"]["flags"], p["support"]["n_linked_samples"],
                    p["support"]["linked_sample_ids"], cen[0], cen[1], "model_estimate",
                    p["detection_status"], p["concentration_status"], (p.get("field_estimate") or {}).get("value")])
    return _csv(ZONE_COLS_31, out)
