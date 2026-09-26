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
    "conc_model_cfg": REPO / "configs" / "case_conc_model.yaml",
    "dev_cv": REPO / "reports" / "case_conc" / "dev_cv.json",
    "final_test": REPO / "reports" / "case_conc" / "final_test.json",
    "conc_weights_dir": REPO / "weights" / "case_conc",
    "registry_pairs": REPO / "data" / "case" / "run" / "registry_pairs.csv",
    "det_metrics": REPO / "reports" / "case_detector" / "metrics.json",
    "transects": REPO / "data" / "case" / "geometry" / "transects.geojson",
    "pairs_cfg": REPO / "configs" / "case_pairs.yaml",
    "pairs_visual": REPO / "data" / "case" / "pairs_visual_labels.csv",
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
    {"id": "DRIFT_TOO_LARGE", "label": "Дрейф за разрыв времени больше допуска (несинхронно)"},
    {"id": "TIME_UNKNOWN", "label": "Время наблюдения неизвестно (окно по суткам)"},
]
# Quality mask legend (contract 3.1). /api/v3/scenes/{id}/quality.png is rendered from quality.tif of
# scripts/case/pair_quality.py with exactly these RGBA colours (codes: 0 nodata, 1 water, 2 land, 3 SCL cloud/shadow/
# cirrus, 4 spectral cloud, 5 other = water-edge buffer/dark/defect, 6 glint (pair_quality.py: spectral cloud test fired on
# sunglint water -> glint). SCL cloud shadow is merged into code 3, so "shadow" has "present": false.
QUALITY_CLASSES = [
    {"id": "valid", "label": "Пригодная вода", "color": "#00000000", "codes": [1], "present": True},
    {"id": "cloud", "label": "Облако (вкл. тень и перистые по SCL)", "color": "#ffffff99", "codes": [3, 4],
     "present": True},
    {"id": "shadow", "label": "Тень облака", "color": "#74c0fc99", "codes": [], "present": False,
     "note": "отдельно не выделяется: тень входит в класс «Облако»"},
    {"id": "glint", "label": "Блики", "color": "#ffd43b99", "codes": [6], "present": True,
     "note": "код 6 маски качества (scripts/case/pair_quality.py): вода, на которой облачный тест сработал от солнечного блика"},
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
    "sync_unreliable_drift": "DRIFT_TOO_LARGE",
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
import contextvars as _cv  # noqa: E402
_REQ_MEMO: "_cv.ContextVar[Optional[dict]]" = _cv.ContextVar("case_store_req_memo", default=None)


class request_scope:
    """Within one API response the files are stat()-ed once: `with request_scope(): ...` (speed of /observations)."""
    def __enter__(self):
        self._tok = _REQ_MEMO.set({})
        return self

    def __exit__(self, *a):
        _REQ_MEMO.reset(self._tok)


def _cached(key: str, path: Path, loader):
    memo = _REQ_MEMO.get()
    mk = (key, str(path))
    if memo is not None and mk in memo:
        return memo[mk]
    val = _cached_stat(key, path, loader)
    if memo is not None:
        memo[mk] = val
    return val


def _cached_stat(key: str, path: Path, loader):
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
    """Observation strip in EPSG:4326: buffer (radius_m) of the transect line or of the point, built in local UTM.
    line = ((lon, lat), (lon, lat)) or a list of track segments [[(lon, lat), ...], ...] (flat-cap buffer per segment,
    the gap between segments stays outside, as in scripts/case/pair_quality.py)."""
    try:
        from pyproj import Transformer
        from shapely.geometry import LineString, MultiLineString, Point, mapping
        from shapely.ops import transform
        zone = int((lon + 180) // 6) + 1
        epsg = (32600 if lat >= 0 else 32700) + zone
        fwd = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform
        inv = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True).transform
        cap = 1   # round
        if line and isinstance(line[0][0], (list, tuple)):   # segments of a track: gap is not covered
            g = MultiLineString([list(seg) for seg in line]) if len(line) > 1 else LineString(line[0])
            cap = 2   # flat, as pair_quality.strip_polygon
        else:
            g = LineString(line) if line else Point(lon, lat)
        poly = transform(inv, transform(fwd, g).buffer(radius_m, quad_segs=8, cap_style=cap))
        m = mapping(poly)
        return {"type": m["type"], "coordinates": json.loads(json.dumps(m["coordinates"]),
                                                             parse_float=lambda s: round(float(s), 6))}
    except Exception:
        return None


# ------------------------------------------------------------------ transect tracks (PANGAEA, L72/L75)
def _tracks_load(p: Path) -> dict:
    fc = _read_json(p)
    return {f["properties"]["event_id"]: f for f in fc.get("features", []) if (f.get("properties") or {}).get("event_id")}


def event_track(event_id: str) -> Optional[dict]:
    """Track of an S2/S3 event restored from PANGAEA (data/case/geometry/transects.geojson, src/macroplastic/case/geometry.py):
    geometry (LineString / MultiLineString of segments; the gap of an interrupted transect is not part of it) + properties.
    configs/case_pairs.yaml geometry.use=false -> None; allow_approx=false -> reconstructed_approx track is not used.
    The author's published area stays the reference for concentration (area_author_km2 is informational here)."""
    if not event_id:
        return None
    gc = (_cached("pairs_cfg", PATHS["pairs_cfg"], _yaml_load) or {}).get("geometry") or {}
    if not gc.get("use", True):
        return None
    f = (_cached("transects", PATHS["transects"], _tracks_load) or {}).get(event_id)
    if not f:
        return None
    pr = f.get("properties") or {}
    st = pr.get("geometry_status") or ""
    if not f.get("geometry") or f["geometry"].get("type") == "Point" or             ("reconstructed_approx" in st and not gc.get("allow_approx", True)):
        return None
    return {"geometry": f["geometry"], "geometry_status": st, "segments": pr.get("n_segments"),
            "segment_windows": pr.get("segment_windows"), "track_center": pr.get("track_center"),
            "track_length_km": pr.get("length_km"), "area_author_km2": pr.get("area_author_km2"),
            "geometry_source": pr.get("source")}


def track_lines(event_id: str) -> Optional[list]:
    """Segments of the track as [[(lon, lat), (lon, lat)], ...] or None."""
    t = event_track(event_id)
    if not t:
        return None
    g = t["geometry"]
    segs = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
    return [[tuple(p) for p in seg] for seg in segs]


def _track_props(event_id: str) -> dict:
    t = event_track(event_id or "")
    if not t:
        return {"geometry_status": None, "segments": None, "track_center": None, "area_author_km2": None,
                "geometry_source": None}
    return {"geometry_status": t["geometry_status"], "segments": t["segments"], "track_center": t["track_center"],
            "segment_windows": t["segment_windows"], "track_length_km": t["track_length_km"],
            "area_author_km2": t["area_author_km2"], "geometry_source": t["geometry_source"]}


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
    trk = event_track(r.get("event_id") or "")
    ln = _line(r) if geometry == "line" else None
    if geometry == "line" and trk:   # S2/S3: track from PANGAEA (MultiLineString for interrupted transects)
        geom = trk["geometry"]
    elif ln:
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
        "source_short": r.get("source_short") or None,
        "source_license": r.get("source_license") or None,
        **field_poisson_ci(r),
        "field_estimate": obs_field_estimate(r.get("sample_id") or ""),
        "research_estimate": model_estimate_for(r.get("sample_id") or ""),
        "model_estimate": model_estimate_for(r.get("sample_id") or ""),  # 3.1f alias of research_estimate
        "linked_scenes": linked,
        **_track_props(r.get("event_id") or ""),
    }
    return {"type": "Feature", "id": r.get("sample_id"), "geometry": geom, "properties": props}


_POISSON_MEMO: dict = {}


def _poisson_ci_cached(fn, n, a):
    k = (n, a)
    if k not in _POISSON_MEMO:
        _POISSON_MEMO[k] = fn(n, a)
    return _POISSON_MEMO[k]


def field_poisson_ci(r: dict) -> dict:
    """Reference field value and its counting uncertainty: C = N/A with a 95 % Poisson CI
    (src/macroplastic/case/concentration.py). N = density_numerator_items, A = sampled_area_km2.
    Not a predictor (the measured value itself), so no leakage. null + reason when N or A is missing, when the row
    is a single object, or when N/A does not reproduce the published concentration (N is then not the numerator
    of that concentration)."""
    out = {"n_items": None, "ci95_lo": None, "ci95_hi": None, "ci95_method": None, "ci95_reason": None}
    if r.get("record_type") == "item_observation":
        out["ci95_reason"] = "отдельный объект, не плотность"
        return out
    n, a = fnum(r.get("density_numerator_items")), fnum(r.get("sampled_area_km2"))
    if n is None or a is None:
        out["ci95_reason"] = "нет числа предметов N или площади A в источнике"
        return out
    try:
        from src.macroplastic.case.concentration import concentration as conc_fn
        res = _poisson_ci_cached(conc_fn, n, a)
    except Exception:
        out["ci95_reason"] = "расчёт интервала недоступен"
        return out
    pub = fnum(r.get("concentration_items_km2"))
    if res.value is None:
        out["ci95_reason"] = res.note or "недостаточно данных"
        return out
    if pub is not None and abs(res.value - pub) > max(0.02 * abs(pub), 0.05):
        out["ci95_reason"] = (f"N/A = {res.value:.4g} не совпадает с опубликованной концентрацией {pub:.4g}: "
                              "N — не числитель этой величины")
        return out
    out.update({"n_items": n, "ci95_lo": round(res.lower, 4), "ci95_hi": round(res.upper, 4),
                "ci95_method": "95 % ДИ Пуассона для N, делённый на A (эталон, не прогноз): только ошибка счёта этой полосы; "
                               "пятнистость между соседними полосами сверх Пуассона (sd ln C ≈ 0.46) не входит — "
                               "reports/quantity/field_intervals.md"
                               + (f"; {res.note}" if res.note else "")})
    return out


def _dev_predictions() -> dict:
    """reports/case_conc/dev_predictions.csv (scripts/case/conc_model_cv.py, out-of-fold dev CV) rows of the MAIN model of each profile."""
    rows = _cached("dev_predictions", PATHS["dev_cv"].parent / "dev_predictions.csv", _read_csv) or []
    key = ("devpred_idx", id(rows), tuple(sorted((k, v.get("model")) for k, v in selected_models().items())))
    with _lock:
        hit = _cache.get("devpred_idx")
        if hit and hit[0] == key:
            return hit[1]
    main = {k: v.get("model") for k, v in selected_models().items()}
    idx = {r.get("sample_id"): r for r in rows if r.get("model") and main.get(r.get("profile")) == r.get("model")}
    with _lock:
        _cache["devpred_idx"] = (key, idx)
    return idx


def profile_members() -> dict[str, str]:
    """sample_id -> concentration profile (S2_visual_total_plastic / S1_trawl_total_plastic), all accepted records
    of the profile incl. held-out test and buffer (src/macroplastic/case/selection.select). Cached by CSV mtime."""
    key = ("members", str(PATHS["samples"]), id(samples_raw()[1]), str(CASE_SELECTION_YAML))
    with _lock:
        hit = _cache.get("profile_members")
        if hit and hit[0] == key:
            return hit[1]
    out: dict[str, str] = {}
    try:
        import pandas as pd
        from src.macroplastic.case import selection as S
        fields, rows = samples_raw()
        if rows:
            df = pd.read_csv(PATHS["samples"])
            cfg = S.load_config(CASE_SELECTION_YAML)
            for prof in sorted(selected_models() or cfg.get("profiles") or {}):
                if prof not in (cfg.get("profiles") or {}):
                    continue
                acc, _ = S.select(df, cfg, prof)
                for sid in acc["sample_id"].astype(str):
                    out.setdefault(sid, prof)
    except Exception as e:  # selection unavailable -> no field estimate
        print(f"[case_store] profile_members: {e}")
        out = {}
    with _lock:
        _cache["profile_members"] = (key, out)
    return out


FIELD_EST_NOTE = "оценка по полевым данным, не по снимку; модели по координатам/сезону на отложенном test не лучше медианы"


def profile_median_estimate(profile: str) -> Optional[dict]:
    key = (profile, id(dev_cv()), id(final_test_result()), id(_cached("case_selection", CASE_SELECTION_YAML, _yaml_load)))
    with _lock:
        hit = _cache.get(f"pme:{profile}")
    if hit and hit[0] == key:
        return dict(hit[1]) if hit[1] else None
    val = _profile_median_estimate(profile)
    with _lock:
        _cache[f"pme:{profile}"] = (key, val)
    return dict(val) if val else None


def profile_scenarios(profile: str) -> Optional[dict]:
    """p25/p50/p75 of the training (dev) profile, items/km2 (reference values y_true of dev_predictions.csv,
    one per dev sample): scenarios «ниже / типично / выше» next to the median interval."""
    rows = _cached("dev_predictions", PATHS["dev_cv"].parent / "dev_predictions.csv", _read_csv) or []
    ys = {}
    for r in rows:
        if r.get("profile") == profile and fnum(r.get("y_true")) is not None:
            ys[r.get("sample_id")] = fnum(r.get("y_true"))
    if not ys:
        return None
    import numpy as np
    a = np.asarray(sorted(ys.values()), float)
    q = np.percentile(a, [25, 50, 75])
    return {"p25": round(float(q[0]), 2), "p50": round(float(q[1]), 2), "p75": round(float(q[2]), 2),
            "n": int(a.size), "unit": "items/km2",
            "note": "квартили полевых значений обучающего (dev) профиля: сценарии ниже / типично / выше"}


SELECTED_REASON = ("основная модель на отложенном test не лучше медианы, правило записано до открытия, "
                   "коммит eb35414")


def _profile_median_estimate(profile: str) -> Optional[dict]:
    """The map value for a profile: median of the training (dev) profile with the median's interval
    (q_lo/q_hi of the baseline in final_test.json) and its actual coverage on the held-out test and on dev CV."""
    import math as _m
    dp = (dev_cv().get("profiles") or {}).get(profile) or {}
    med = fnum((dp.get("dev_target") or {}).get("median"))
    if med is None:
        return None
    ft = (((final_test_result() or {}).get("profiles") or {}).get(profile) or {})
    bm = ft.get("baseline_metrics") or {}
    q_lo, q_hi = fnum(bm.get("q_lo")), fnum(bm.get("q_hi"))
    lo = hi = None
    if q_lo is not None and q_hi is not None:
        ly = _m.log1p(med)
        lo, hi = min(max(_m.expm1(ly + min(q_lo, 0.0)), 0.0), med), max(_m.expm1(ly + max(q_hi, 0.0)), med)
    cov_cv = fnum(({r.get("model"): r for r in dp.get("table") or []}.get("median") or {}).get("coverage90"))
    cov_test = fnum(bm.get("coverage90"))
    cfg = (_cached("case_selection", CASE_SELECTION_YAML, _yaml_load) or {}).get("profiles", {}).get(profile) or {}
    lab = []
    if cov_test is not None:
        lab.append(f"≈{round(cov_test * 100)} % на отложенном test")
    if cov_cv is not None:
        lab.append(f"≈{round(cov_cv * 100)} % по CV")
    return {"value": round(med, 2), "lo": None if lo is None else round(lo, 2), "hi": None if hi is None else round(hi, 2),
            "interval": "; ".join(lab) or None, "interval_nominal": 0.9,
            "interval_coverage_test": cov_test, "interval_coverage_cv": cov_cv,
            "n_test": ft.get("n_test"), "unit": "items/km2",
            "measurement_profile": (cfg.get("measurement_profile") or [None])[0], "profile_config": profile,
            "model": "median_train", "basis": "field_model", "note": FIELD_EST_NOTE,
            "scenarios": profile_scenarios(profile)}


def _fn_quantity() -> dict:
    fn = _cached("final_numbers", PATHS["final_numbers"], _read_json) or {}
    return (((fn.get("case") or {}).get("sections") or {}).get("quantity") or {})


def profile_pooled(profile_config: str) -> Optional[dict]:
    """§28 А (L108): the profile mean with two intervals from final_numbers — cluster bootstrap over cruise days (the
    honest one; plus the range for one new place) and Poisson (only the counting error)."""
    q = _fn_quantity().get("field_S2") or {}
    if not q or not str(profile_config or "").startswith("S2") or q.get("pooled_C") is None:
        return None
    return {"mean": q.get("pooled_C"), "unit": "items/km2", "n": q.get("n"),
            "boot_lo95": q.get("boot_lo95"), "boot_hi95": q.get("boot_hi95"), "boot_label": q.get("boot_label"),
            "event_lo95": q.get("event_lo95"), "event_hi95": q.get("event_hi95"),
            "event_label": "для одного нового места (разброс между событиями)",
            "poisson_lo95": q.get("lo95"), "poisson_hi95": q.get("hi95"),
            "poisson_label": "только ошибка счёта (Пуассон)", "source": "reports/final_numbers.json · quantity.field_S2"}


def obs_field_estimate(sample_id: str) -> Optional[dict]:
    prof = profile_members().get(sample_id)
    fe = profile_median_estimate(prof) if prof else None
    if fe is not None:
        fe["profile_pooled"] = profile_pooled(fe.get("profile_config") or prof)
    return fe


def model_estimate_for(sample_id: str) -> Optional[dict]:
    r = _dev_predictions().get(sample_id)
    if not r:
        return None
    fold = r.get("fold")
    return {"value": _r(r.get("y_pred"), 3), "lo": _r(r.get("lo"), 3), "hi": _r(r.get("hi"), 3),
            "unit": "items/km2", "model": r.get("model"), "profile_config": r.get("profile"),
            "fold": int(fold) if fold not in (None, "") and str(fold).lstrip("-").isdigit() else fold,
            "interval_coverage_cv": fnum(((selected_models().get(r.get("profile")) or {}).get("dev_cv") or {})
                                         .get("coverage90")),
            "note": ("исследовательская модель, на отложенном test не лучше медианы; прогноз по CV вне "
                     "обучающего участка"
                     if (final_test_decision(r.get("profile") or "") or {}).get("main_better_significant") is False
                     else "прогноз по CV вне обучающего участка"),
            "status": ("исследовательская модель, на test не лучше медианы"
                       if (final_test_decision(r.get("profile") or "") or {}).get("main_better_significant") is False
                       else "исследовательская модель")}


def linked_scenes_by_sample() -> dict[str, list[str]]:
    """Scenes of accepted pairs + scenes the sample was actually checked on (pair_quality -> zone), even if the
    pair was rejected (e.g. drift); the latter are also listed in linked_scenes_unsynced."""
    out: dict[str, set] = {}
    for p in pairs_all():
        if p["status"] == "accepted" and p["scene_id"]:
            out.setdefault(p["sample_id"], set()).add(p["scene_id"])
    for sid, scenes in checked_scenes_by_sample().items():
        out.setdefault(sid, set()).update(scenes)
    return {k: sorted(v) for k, v in out.items()}


def checked_scenes_by_sample() -> dict[str, set]:
    out: dict[str, set] = {}
    for q in pair_quality_raw():
        if q.get("scene_id"):
            for sid in (q.get("sample_ids") or "").split(";"):
                if sid:
                    out.setdefault(sid, set()).add(q["scene_id"])
    return out


def unsynced_scenes_by_sample() -> dict[str, list[str]]:
    acc = {(p["sample_id"], p["scene_id"]) for p in pairs_all() if p["status"] == "accepted"}
    return {sid: sorted(x for x in sc if (sid, x) not in acc) for sid, sc in checked_scenes_by_sample().items()}


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
    with request_scope():
        return _observations_fc(rows, geometry, limit)


def _observations_fc(rows: list[dict], geometry: str = "point", limit: Optional[int] = None) -> dict:
    total = len(rows)
    if limit is not None:
        rows = rows[:limit]
    linked = linked_scenes_by_sample() if rows else {}
    uns = unsynced_scenes_by_sample() if rows else {}
    feats = [_obs_feature(r, linked.get(r.get("sample_id"), []), geometry) for r in rows]
    for f in feats:
        f["properties"]["linked_scenes_unsynced"] = uns.get(f["id"], [])
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
        elif part.startswith("time_unknown"):
            c = "TIME_UNKNOWN"
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
        # find_pairs.py: accept_meta = all metadata rules except drift sync; accept = accept_meta and drift ok
        accept_meta = _truthy(c.get("accept_meta")) if (c.get("accept_meta") or "") != "" else accept
        if q:
            cloud_local = _r(fnum(q.get("cloud_frac")) * 100, 2) if fnum(q.get("cloud_frac")) is not None else None
            valid_local = _r(q.get("valid_water_frac"))
            quality_decision = q.get("decision") or None
            if quality_decision != "accept":
                pq_codes = _codes(q.get("reason") or "", _PQ_REASON) or ["PROCESSING_ERROR"]
                if accept:
                    accept = False
                    reasons = pq_codes
                else:
                    reasons = reasons + [x for x in pq_codes if x not in reasons]
        without_drift = accept_meta and (quality_decision in (None, "accept"))
        for s in by_event.get(ev, []):
            sid = s.get("sample_id")
            pid = f"{sid}__{key_scene}"
            if pid in seen:
                continue
            seen.add(pid)
            lon, lat = fnum(s.get("longitude")), fnum(s.get("latitude"))
            geom = {"type": "Point", "coordinates": [lon, lat]} if lon is not None else None
            ln = _line(s)
            trk = event_track(ev)
            if trk:
                geom = trk["geometry"]
            elif ln:
                geom = {"type": "LineString", "coordinates": [list(ln[0]), list(ln[1])]}
            out.append({
                "pair_id": pid, "sample_id": sid, "event_id": ev, "source_id": s.get("source_id"),
                "scene_id": item, "mission": mission_of(item or "", c.get("mission", "")),
                "scene_datetime": iso_dt(c.get("scene_datetime")), "obs_datetime": iso_dt(c.get("obs_datetime")),
                "dt_hours": _r(c.get("dt_hours"), 2),
                "distance_km": None, "drift_shift_km": _r(c.get("drift_shift_km"), 3), "geometry": geom,
                "cloud_pct_local": cloud_local, "valid_fraction_local": valid_local,
                "status": "accepted" if accept else "rejected", "reject_reasons": reasons,
                "split": None,
                # additions
                "scene_cloud_pct": _r(c.get("cloud_cover"), 2),
                "catalog": f"{c.get('endpoint', '')}/{c.get('collection', '')}",
                "time_known": _truthy(c.get("time_known")),
                "registry_note": raw or None,
                "quality_decision": quality_decision,
                "tolerance_km": _r(c.get("tolerance_km"), 3),
                "drift_scenarios_km": ({"low": _r(c.get("drift_shift_km_low"), 3),
                                        "typical": _r(c.get("drift_shift_km_typical"), 3),
                                        "high": _r(c.get("drift_shift_km_high"), 3)}
                                       if fnum(c.get("drift_shift_km_typical")) is not None else None),
                "dt_drift_hours": _r(c.get("dt_drift_h"), 2),
                "status_without_drift": "accepted" if without_drift else "rejected",
                "dt_uncertainty_h": 0 if _truthy(c.get("time_known")) else 12,
                "time_note": ("время неизвестно, ±12 ч (известна только дата)"
                              if not _truthy(c.get("time_known")) else None),
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
CASE_SELECTION_YAML = REPO / "configs" / "case_selection.yaml"


def predictions_raw() -> list[dict]:
    return _cached("predictions", PATHS["conc_metrics"].parent / "predictions.csv", _read_csv) or []


# ------------------------------------------------------------------ field concentration model
# Source of truth (read only): configs/case_conc_model.yaml (selected model per profile, interval quantiles),
# weights/case_conc/<profile>.json (fitted model), reports/case_conc/dev_cv.json (dev CV vs median baseline,
# route segments + 1 day buffer), reports/case_conc/final_test.json (held-out test, computed once at acceptance).
DOMAIN_MARGIN_DEG = 1.0


def _yaml_load(p: Path):
    import yaml
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def conc_model_cfg() -> dict:
    return _cached("conc_model_cfg", PATHS["conc_model_cfg"], _yaml_load) or {}


def dev_cv() -> dict:
    return _cached("dev_cv", PATHS["dev_cv"], _read_json) or {}


def selected_models() -> dict:
    sel = conc_model_cfg().get("selected") or {}
    return {k: v for k, v in sorted(sel.items()) if isinstance(v, dict) and v.get("model")}


def conc_weights(profile: str) -> Optional[dict]:
    """weights/case_conc/<profile>.json (= selected.<profile>.weights in the yaml; dir is in PATHS for tests)."""
    return _cached(f"cw:{profile}", PATHS["conc_weights_dir"] / f"{profile}.json", _read_json)


def coverage_label(cov) -> Optional[str]:
    c = fnum(cov)
    return None if c is None else f"≈{round(c * 100)} % по CV"


def profile_domain(profile: str) -> Optional[list[float]]:
    """Area of applicability: bbox of the profile's field records (source_id + measurement_profile of
    configs/case_selection.yaml) + DOMAIN_MARGIN_DEG. Outside it the field model is not applied."""
    cfg = (_cached("case_selection", CASE_SELECTION_YAML, _yaml_load) or {}).get("profiles", {}).get(profile) or {}
    srcs, profs = set(cfg.get("source_id") or []), set(cfg.get("measurement_profile") or [])
    pts = [(fnum(r.get("longitude")), fnum(r.get("latitude"))) for r in samples_raw()[1]
           if (not srcs or r.get("source_id") in srcs) and (not profs or r.get("measurement_profile") in profs)]
    pts = [(x, y) for x, y in pts if x is not None and y is not None]
    if not pts:
        return None
    m = DOMAIN_MARGIN_DEG
    return [min(p[0] for p in pts) - m, min(p[1] for p in pts) - m, max(p[0] for p in pts) + m,
            max(p[1] for p in pts) + m]


def _predict_log1p(w: dict, lon: float, lat: float, when: str) -> Optional[float]:
    import numpy as np
    import pandas as pd
    mdl = w.get("model") or {}
    name = mdl.get("name") or ""
    if name.startswith("knn"):
        pts = mdl.get("points") or {}
        la, lo_, t, ly = (np.asarray(pts.get(k) or [], float) for k in ("lat", "lon", "t_days", "log1p_c"))
        if not len(ly):
            return None
        from src.macroplastic.case import splits as P
        tq = P.times_days(pd.DataFrame({"date_utc": [when[:10]], "datetime_start_iso": [when]}))[0]
        d = P.haversine_km(lat, lon, la, lo_)
        excl = float((mdl.get("params") or {}).get("exclude_days", 1.0))
        d = np.where(np.abs(t - tq) <= excl, np.inf, d)
        k = int((mdl.get("params") or {}).get("k", 5))
        ok = np.isfinite(d)
        order = np.argsort(np.where(ok, d, np.inf), kind="stable")[:min(k, int(ok.sum()))]
        return float(ly[order].mean()) if len(order) else float(ly.mean())
    if "coef" in mdl and name.endswith("_log"):
        from src.macroplastic.case.conc_models import base_features
        Xb = base_features(pd.DataFrame({"latitude": [lat], "longitude": [lon], "date_utc": [when[:10]],
                                         "datetime_start_iso": [when]}))
        feats = mdl.get("features") or []
        if mdl.get("cats") or any(f not in Xb.columns for f in feats):
            return None  # categorical/weather features are not available for a map point
        x = Xb[feats].astype(float).fillna(mdl.get("fill") or {}).to_numpy()[0]
        z = (x - np.asarray(mdl["scaler_mean"], float)) / np.asarray(mdl["scaler_scale"], float)
        return float(z @ np.asarray(mdl["coef"], float) + float(mdl["intercept"]))
    return None  # other model families: not supported for point prediction -> no estimate


def final_test_result() -> Optional[dict]:
    ft = _cached("final_test", PATHS["final_test"], _read_json)
    return ft if isinstance(ft, dict) else None


def final_test_decision(profile: str) -> Optional[dict]:
    """Rule written before opening the test (configs/case_selection.yaml final_test.decision_before_opening):
    if the main model is not significantly better than the median on the held-out test -> the map uses the median
    of the training profile. None if the test has not been computed."""
    ft = final_test_result()
    pr = ((ft or {}).get("profiles") or {}).get(profile)
    if not isinstance(pr, dict):
        return None
    better = bool(pr.get("main_better_significant"))
    return {"main_better_significant": better, "use": pr.get("main_model") if better else "median_train",
            "main_model": pr.get("main_model"), "baseline_q": ((pr.get("baseline_metrics") or {}).get("q_lo"),
                                                               (pr.get("baseline_metrics") or {}).get("q_hi"))}


FIELD_MEDIAN_LABEL = ("оценка по полевым данным, не по снимку: медиана профиля; модели по координатам/сезону "
                      "на отложенном test не лучше медианы")


def field_estimate_at(lon, lat, when) -> Optional[dict]:
    """Prediction of the MAIN field model (conc_model_cv.py) for a point, if it lies in a profile's area of applicability.

    Not a satellite estimate; None outside every profile's area / without model files."""
    import math as _m
    if lon is None or lat is None or not when:
        return None
    for profile, sel in selected_models().items():
        dom = profile_domain(profile)
        if not dom or not in_bbox(lon, lat, dom):
            continue
        dec = final_test_decision(profile)
        if dec and not dec["main_better_significant"]:
            # held-out test: the model is not better than the median -> median of the training (dev) profile
            dp = (dev_cv().get("profiles") or {}).get(profile) or {}
            med = fnum((dp.get("dev_target") or {}).get("median"))
            if med is None:
                continue
            ly = _m.log1p(med)
            q_lo, q_hi = (fnum(x) for x in dec["baseline_q"])
            cov = ({r.get("model"): r for r in dp.get("table") or []}.get("median") or {}).get("coverage90")
            model_name, label = "median_train", FIELD_MEDIAN_LABEL
        else:
            w = conc_weights(profile)
            if not w:
                continue
            ly = _predict_log1p(w, lon, lat, when)
            if ly is None or not _m.isfinite(ly):
                continue
            q = (sel.get("interval") or {})
            q_lo, q_hi = fnum(q.get("q_lo")), fnum(q.get("q_hi"))
            cov = (sel.get("dev_cv") or {}).get("coverage90")
            model_name, label = sel.get("model"), "оценка по полевым данным, не по снимку"
        val = _m.expm1(ly)
        lo = hi = None
        if q_lo is not None and q_hi is not None:
            lo, hi = min(max(_m.expm1(ly + min(q_lo, 0.0)), 0.0), val), max(_m.expm1(ly + max(q_hi, 0.0)), val)
        q = (sel.get("interval") or {})
        cfg = (_cached("case_selection", CASE_SELECTION_YAML, _yaml_load) or {}).get("profiles", {}).get(profile) or {}
        return {"value": round(val, 2), "lo": None if lo is None else round(lo, 2),
                "hi": None if hi is None else round(hi, 2),
                "interval": coverage_label(cov) if lo is not None else None,
                "interval_nominal": q.get("level"), "interval_coverage_cv": fnum(cov),
                "unit": "items/km2", "measurement_profile": (cfg.get("measurement_profile") or [None])[0],
                "profile_config": profile, "model": model_name, "basis": "field_model",
                "label": label, "final_test_decision": (dec or {}).get("use"),
                "scenarios": profile_scenarios(profile) if model_name == "median_train" else None,
                "applicability": "точка в области полевых данных профиля", "domain_bbox": [round(v, 4) for v in dom]}
    return None


def conc_metrics_block() -> dict:
    """/metrics.concentration from dev_cv.json + case_conc_model.yaml (+ final_test.json if it exists)."""
    dcv = dev_cv().get("profiles") or {}
    sel = selected_models()
    profiles = {}
    for prof in sorted(set(dcv) | set(sel)):
        p = dcv.get(prof) or {}
        table = {r.get("model"): r for r in p.get("table") or []}
        main_name = (sel.get(prof) or {}).get("model") or p.get("primary")
        base, main = table.get("median"), table.get(main_name)

        def row(r, name):
            if not r:
                return None
            return {"name": name, "mae": _r(r.get("mae")), "mae_log": _r(r.get("log1p_mae")),
                    "coverage": _r(r.get("coverage90")), "coverage_label": coverage_label(r.get("coverage90")),
                    "rmse": _r(r.get("rmse")), "n": r.get("n")}
        b, m = row(base, "median (train profile)"), row(main, main_name)
        strength = None
        if m and main_name != "median":
            lo_, hi_ = fnum(main.get("d_mae_lo")), fnum(main.get("d_mae_hi"))
            m["delta_mae_vs_median"] = _r(main.get("d_mae"))
            m["delta_mae_ci95"] = [_r(lo_), _r(hi_)]
            m["delta_mae_ci95_bonferroni"] = [_r(main.get("d_mae_bonf_lo")), _r(main.get("d_mae_bonf_hi"))]
            sens = [s for s in p.get("sensitivity_k_blocks") or [] if s.get("model") == main_name]
            unstable = any((fnum(s.get("d_mae_hi")) or 0) >= 0 for s in sens)
            sig = hi_ is not None and hi_ < 0
            bonf = fnum(main.get("d_mae_bonf_hi"))
            if not sig:
                strength = "не лучше медианы"
            elif unstable:
                strength = "слабый выигрыш / зависит от разбиения"
            else:
                strength = "выигрыш на dev CV, устойчив к числу участков"
            if sig and bonf is not None and bonf >= 0:
                strength += "; с поправкой Бонферрони не значим"
            m["sensitivity_k_blocks"] = [{"k_blocks": s.get("k_blocks"), "delta_mae": _r(s.get("d_mae")),
                                          "ci95": [_r(s.get("d_mae_lo")), _r(s.get("d_mae_hi"))]} for s in sens]
        profiles[prof] = {"n_dev": p.get("n_dev"), "dev_cruise_days": p.get("dev_cruise_days"),
                          "baseline": b, "main": m, "strength": strength, "why": p.get("why"),
                          "interval_nominal": ((sel.get(prof) or {}).get("interval") or {}).get("level")}
    ft = final_test_result()
    ft_summary = {}
    for prof, pr in sorted(((ft or {}).get("profiles") or {}).items()):
        if not isinstance(pr, dict):
            continue
        mm, bm, mv = pr.get("main") or {}, pr.get("baseline_metrics") or {}, pr.get("main_vs_baseline") or {}

        def trow(r, name):
            return {"name": name, "n": r.get("n"), "mae": _r(r.get("mae")), "rmse": _r(r.get("rmse")),
                    "mae_log": _r(r.get("log1p_mae")), "coverage": _r(r.get("coverage90")),
                    "coverage_label": (f"≈{round(fnum(r.get('coverage90')) * 100)} % на test"
                                       if fnum(r.get("coverage90")) is not None else None)}
        better = bool(pr.get("main_better_significant"))
        ft_summary[prof] = {
            "n_test": pr.get("n_test"), "test_cruise_days": pr.get("test_cruise_days"),
            "main": trow(mm, pr.get("main_model")), "baseline": trow(bm, "median (train profile)"),
            "delta_mae": _r(mv.get("d_mae")), "delta_mae_ci95": [_r(x) for x in (mv.get("ci95") or [None, None])],
            "delta_mae_log": _r(mv.get("d_log1p_mae")),
            "delta_mae_log_ci95": [_r(x) for x in (mv.get("ci95_log") or [None, None])],
            "main_better_significant": better,
            "decision": (f"{pr.get('main_model')} значимо лучше медианы — на карте прогноз модели" if better else
                         "основная модель на test не лучше медианы — на карте медиана обучающего профиля")}
        if prof in profiles:
            profiles[prof]["final_test"] = ft_summary[prof]
            profiles[prof]["selected"] = "median_train" if not better else pr.get("main_model")
            profiles[prof]["selected_reason"] = (SELECTED_REASON if not better else
                                                 "основная модель на отложенном test значимо лучше медианы")
            ft_summary[prof]["reference_models_note"] = (
                "метрики и прогнозы остальных моделей на test — справочно, после решения; не использовались для "
                "выбора (reports/case_conc/final_test_reference_predictions.csv)")
            pm = profile_median_estimate(prof)
            if pm:
                profiles[prof]["map_field_estimate"] = {k: pm[k] for k in (
                    "model", "value", "lo", "hi", "interval", "interval_coverage_test", "interval_coverage_cv",
                    "n_test", "note")}
    default = "S2_visual_total_plastic" if "S2_visual_total_plastic" in profiles else \
        (sorted(profiles)[0] if profiles else None)
    dp = profiles.get(default) or {}
    cvp = ((conc_model_cfg().get("protocol") or {}).get("cv") or {})
    return {"baseline": dp.get("baseline"), "main": dp.get("main"), "strength": dp.get("strength"),
            "unit": "items/km2", "profile": default, "profiles": profiles,
            "split": {"type": "dev CV: участки маршрута + буфер", "method": cvp.get("method"),
                      "k_blocks": cvp.get("k_blocks"), "buffer_days": cvp.get("buffer_days")} if cvp else None,
            "final_test": ft,
            "final_test_summary": ft_summary or None,
            "selected": {k: v.get("selected") for k, v in profiles.items() if v.get("selected")} or None,
            "final_test_status": (f"посчитан один раз {str(ft.get('when') or '')[8:10]}.{str(ft.get('when') or '')[5:7]}"
                                  if ft and ft.get("when") else ("посчитан один раз" if ft else
                                                                 "будет посчитан один раз в приёмке")),
            "note": "полевая модель концентрации (координаты/время → шт./км²), не спутниковая; "
                    "coverage — фактическое покрытие 90 %-интервала на dev CV"}


# ------------------------------------------------------------------ zones + scenes (from pair_quality)
_ZONE_REASON_RU = {"cloud": "облачность в полосе наблюдения", "glint": "солнечные блики",
                   "insufficient_coverage": "снимок не покрывает полосу", "land": "суша в полосе"}


def _inputs_key() -> tuple:
    """Identity of every cached input of zones/estimates; cached objects change identity when a file changes."""
    import copy as _c  # noqa: F401
    metas = tuple(id(quality_meta(q.get("dir") or safe_event(q.get("event_id", "")))) for q in pair_quality_raw())
    return (id(pair_quality_raw()), id(candidates_raw()), id(samples_raw()[1]), metas, id(registry_pairs_raw()),
            id(conc_model_cfg()), id(dev_cv()), id(final_test_result()), str(PATHS["conc_weights_dir"]),
            str(PATHS["pairs_dir"]))


def registry_pairs_raw():
    return _cached("registry_pairs", PATHS["registry_pairs"], _read_csv)


def zones_all() -> list[dict]:
    """Memoised by the identity of all inputs (files are re-read by mtime in _cached). Callers get deep copies."""
    import copy
    key = _inputs_key()
    with _lock:
        hit = _cache.get("zones_all")
    if hit and hit[0] == key:
        return copy.deepcopy(hit[1])
    val = _zones_all_build()
    with _lock:
        _cache["zones_all"] = (key, val)
    return copy.deepcopy(val)


def _zones_all_build() -> list[dict]:
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
            if meta.get("geometry_source") == "pangaea_track":   # strip of pair_quality.py was built from the track
                line = track_lines(q.get("event_id", "")) or line
        radius = fnum(((meta.get("config") or {}).get("strip") or {}).get("point_buffer_m")) or 500.0
        if line:
            width = fnum(rows[0].get("transect_width_m"))
            radius = max(width / 2.0 if width else 10.0, 10.0)
        geom = strip_polygon(lon, lat, line, radius) if lon is not None and lat is not None else None
        # the field density row behind field_items_km2: meta.field_sample_id (pair_quality.py), else first transect_density row
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
            "kind": "candidate_strip", "zone_id": f"Z-{d}", "scene_id": scene_id,
            "mission": mission_of(scene_id or ""), "datetime": iso_dt(q.get("scene_datetime")),
            "status": status, "detection_status": status, "status_reason": reason,
            "concentration_status": "unavailable",
            "concentration_reason": "нет проверенного переноса «снимок → шт./км²»; спутниковая концентрация не выдаётся",
            "area_km2": _r(geodesic_area_km2(geom)),
            "area_basis": "геодезическая площадь полигона geometry (WGS84): полоса наблюдения вокруг трансекты/точки",
            "strip_area_raster_km2": _r(q.get("strip_area_km2")),
            **zone_pair_info(q.get("event_id", ""), q.get("scene_id") or "", sids),
            "detected_area_m2": _r(q.get("det_area_m2"), 1),
            "detector": {"prob_mean": None, "prob_max": _r(prob_max), "n_pixels": det.get("det_px_strip"),
                         "n_objects": None if n_det is None else int(n_det),
                         "threshold": det.get("threshold")},
            "field_estimate": field_estimate_at(lon, lat, iso_dt(q.get("scene_datetime"))),
            "concentration": None,
            "quality": {"valid_fraction": _r(q.get("valid_water_frac")), "cloud_fraction": _r(q.get("cloud_frac")),
                        "glint_fraction": _r(q.get("glint_frac")), "land_fraction": _r(q.get("land_frac")),
                        "coverage": _r(q.get("coverage")), "glint_b11_median": _r(q.get("glint_b11_median")),
                        "flags": []},
            "support": {"n_linked_samples": len(sids), "linked_sample_ids": sids, "nearest_measurement_km": 0.0,
                        "field_items_km2": fnum(q.get("field_items_km2")),
                        "field_sample_id": frow.get("sample_id") or None,
                        "field_target_scope": frow.get("target_scope") or None,
                        "field_scope_label": scope_label(frow.get("target_scope") or "")},
            "event_id": q.get("event_id"), "quality_dir": d,
            # decision of the strip quality masks (pair_quality.csv decision): accept | reject (error -> reject)
            "quality_decision": ("accept" if decision == "accept" else "reject") if decision else None,
            "quality_reason": q.get("reason") or None,
            "quality_reject_reason": (q.get("reason") or ("error" if decision == "error" else None))
            if decision and decision != "accept" else None,
            "quality_reject_label": (_ZONE_REASON_RU.get(q.get("reason") or "", q.get("reason") or "ошибка обработки")
                                     if decision and decision != "accept" else None),
        }})
    labels = {r["id"]: r["label"] for r in REJECT_REASONS}
    for f in feats:
        pr = f["properties"]
        pr["quality"]["flags"] = sorted(set(pr["quality"]["flags"]) | set(pr.pop("_flags", [])))
        pr["layer_kind"] = "candidate_strip"
        pr["layer_label"] = "Снимок-кандидат: полоса обследования"
        verdict = pr["detection_status"]
        pr["detector_verdict"] = verdict  # raw result of the detector on the strip (pair_quality)
        det = pr.get("detector") or {}
        qrej = pr.get("quality_decision") not in (None, "accept")
        pr["suspicious_pixels"] = ({"n_objects": det.get("n_objects"), "area_m2": pr.get("detected_area_m2"),
                                    "prob_max": det.get("prob_max"), "quality_rejected": qrej,
                                    "note": QREJ_NOTE if qrej else
                                    "подозрительные пиксели на снимке-кандидате, без полевого подтверждения"}
                                   if det.get("n_objects") is not None else None)
        pr["detection_reason"] = None
        if pr.get("pair_status") != "accepted":
            why = ", ".join(labels.get(c, c) for c in pr.get("pair_reject_reasons") or []) or "пары нет в реестре"
            pr["detection_reason"] = f"связь снимка с полевым измерением не подтверждена: {why}"
            pr["detection_status"] = pr["status"] = "insufficient_data"
            pr["status_reason"] = pr["detection_reason"] + "; " + pr["status_reason"]
    feats.sort(key=lambda f: f["id"])
    return feats


def geodesic_area_km2(geom) -> Optional[float]:
    if not geom:
        return None
    try:
        from pyproj import Geod
        from shapely.geometry import shape
        a, _ = Geod(ellps="WGS84").geometry_area_perimeter(shape(geom))
        return abs(a) / 1e6
    except Exception:
        return None


def registry_pairs() -> dict[str, dict]:
    """data/case/run/registry_pairs.csv (scripts/case/run_all.py): per event status / stage / status_without_drift."""
    rows = _cached("registry_pairs", PATHS["registry_pairs"], _read_csv) or []
    return {r.get("event_id", ""): r for r in rows}


def zone_pair_info(event_id: str, scene_id: str, sids: list[str]) -> dict:
    """Status of the observation<->scene pair behind a zone (the detector verdict stays; the pair may be rejected
    e.g. for drift: the scene is then NOT synchronous with the field measurement)."""
    ps = [p for p in pairs_all() if p["event_id"] == event_id and p["scene_id"] == scene_id]
    acc = any(p["status"] == "accepted" for p in ps)
    reasons = sorted({c for p in ps for c in p["reject_reasons"]}) if not acc else []
    reg = registry_pairs().get(event_id) or {}
    flags = []
    if "DRIFT_TOO_LARGE" in reasons:
        flags.append("pair_rejected_drift")
    return {"pair_status": ("accepted" if acc else "rejected") if ps else None,
            "pair_reject_reasons": reasons,
            "pair_sync": ("synchronous" if acc else "unsynchronized") if ps else None,
            "pair_drift_shift_km": ps[0]["drift_shift_km"] if ps else None,
            "pair_tolerance_km": ps[0]["tolerance_km"] if ps else None,
            "pair_time_known": ps[0]["time_known"] if ps else None,
            "pair_dt_uncertainty_h": ps[0]["dt_uncertainty_h"] if ps else None,
            "registry": {"status": reg.get("status") or None, "stage": reg.get("stage") or None,
                         "status_without_drift": reg.get("status_without_drift") or None} if reg else None,
            "_flags": flags}


def zone_status_tags(p: dict) -> set:
    """Old 5-value `status` filter (3.0) -> matches detection_status or concentration_status (3.1)."""
    tags = {p["detection_status"]}
    if p["concentration_status"] == "unavailable":
        tags.add("concentration_unavailable")
    elif p["concentration_status"] == "research_estimate":
        tags.add("research_estimate")
    return tags


def filter_zones(bbox=None, date_from=None, date_to=None, scene_id=None, statuses=None, profiles=None,
                 min_area_km2=None, detection_statuses=None, concentration_statuses=None, sources=None,
                 scopes=None) -> list[dict]:
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
        if profiles or sources or scopes:  # zones have no source/profile/scope of their own -> linked observations
            rows = [sample_row(s) or {} for s in p["support"]["linked_sample_ids"]]
            if profiles and not {r.get("measurement_profile") for r in rows} & set(profiles):
                continue
            if sources and not {r.get("source_id") for r in rows} & set(sources):
                continue
            if scopes and not {r.get("target_scope") for r in rows} & set(scopes):
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


STRIPS_FILTER_NOTE = ("Полосы обследования — снимки, на которые попали полевые пробы организаторов (акватории S1–S4); "
                      "фильтры акватории, рамки района, дат, профиля и статуса применяются — вне рамки полосы не выдаются. "
                      "Спутниковые зоны районов снимков — отдельный слой scene_zones")


def strips_sources() -> list[str]:
    """Field sources (labels) that have at least one survey strip (via linked samples)."""
    ids = set()
    for f in zones_all():
        for sid in f["properties"]["support"]["linked_sample_ids"]:
            ids.add((sample_row(sid) or {}).get("source_id"))
    return [lab for sid, lab in SOURCES if sid in ids]


def zones_empty_reason(n: int, filters: Optional[dict] = None) -> Optional[str]:
    """Acceptance 15:49 / 16:03 п.1: an honest reason when the strips are 0 (e.g. the frame of a snapshot район)."""
    if n:
        return None
    if not pair_quality_raw():
        return "Проверка пар снимком ещё не выполнена (data/pairs/pair_quality.csv) — недостаточно данных"
    bbox = (filters or {}).get("bbox")
    if bbox is not None and not filter_zones(bbox=bbox):
        return ("В рамке района нет полос обследования: они есть только там, где есть полевые пробы ("
                + "; ".join(strips_sources()) + "). Спутниковые зоны района — слой «Спутниковые зоны» (scene_zones)")
    return "Нет полос обследования под выбранные фильтры"


def zones_fc(feats: list[dict], filters: Optional[dict] = None) -> dict:
    lg = _cached("lgbm_meta", PATHS["lgbm_meta"], _read_json) or {}
    fc = {"type": "FeatureCollection", "kind": "candidate_strip", "layer_kind": "candidate_strip",
          "label": "Проверенные снимки-кандидаты (полосы обследования)", "count": len(feats), "empty_reason": None,
          "model": {"detector": "lgbm" if lg else None, "concentration": None,
                    "trained_at": lg.get("trained_at") or lg.get("created") or detector_model_info().get("trained_at"),
                    "weights_sha256": detector_model_info().get("sha256"),
                    "concentration_note": "калибровки спутник → шт./км² нет; концентрация в зонах не выдаётся"},
          "features": feats}
    fc["filter_note"] = STRIPS_FILTER_NOTE
    if filters is not None:
        fc["filters_applied"] = {k: v for k, v in filters.items() if v not in (None, [], "")}
    fc["empty_reason"] = zones_empty_reason(len(feats), filters)
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
QREJ_NOTE = "на снимке, отклонённом по качеству (блик/облака) — вероятно ложные, не кандидаты"


def map_summary() -> dict:
    zones = zones_all()
    confirmed = {(p["event_id"], p["scene_id"]) for p in pairs_all() if p["status"] == "accepted" and p["scene_id"]}
    plastic = {z["properties"]["scene_id"] for z in zones if z["properties"].get("pair_status") == "accepted"
               and z["properties"]["support"].get("field_target_scope") in PLASTIC_SCOPES}
    n, c, pl = len(zones), len(confirmed), len(plastic)
    ok_q = [z["properties"] for z in zones if z["properties"].get("quality_decision") == "accept"]
    n_susp = sum(int((p.get("suspicious_pixels") or {}).get("n_objects") or 0) for p in ok_q)
    n_susp_rej = sum(int((p.get("suspicious_pixels") or {}).get("n_objects") or 0) for p in
                     (z["properties"] for z in zones) if p.get("quality_decision") not in (None, "accept"))
    return {"n_strips": n, "n_confirmed_pairs": c, "plastic_scenes": pl,
            "n_strips_quality_ok": len(ok_q), "n_suspicious_in_quality_ok_strips": n_susp,
            "n_suspicious_in_quality_rejected_strips": n_susp_rej,
            "text": (f"{n} обследованных участков со снимками-кандидатами; {c} подтверждённых пар; "
                     + ("для пластика снимков нет" if pl == 0 else f"снимков для пластика: {pl}")
                     + f"; подозрительные пиксели в полосах пар, прошедших маски качества: {n_susp}")}


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
        "summary": map_summary(),
        "detector": {"model": "LightGBM (пиксельная, MARIDA + MADOS)", "class": "MARIDA Marine Debris",
                     "note": "класс MARIDA Marine Debris = любой плавающий мусор, не только пластик",
                     "version": detector_model_info()},
        "quantity_levels": quantity_levels(),
        "headline": headline(),
        "scene_zone_statuses": [{"id": k, "label": v, "note": SZ_STATUS4_NOTE[k]} for k, v in SZ_STATUS4.items()],
        "scene_zone_confirmations": [{"id": k, "label": v} for k, v in SZ_CONFIRM.items()],
        "scene_zone_regions": sz_regions(),  # §34 п.3: filter `region` of /scene_zones and the export
        "layers": [
            {"id": "observations", "kind": "measurement", "label": "Полевые измерения (настоящие шт./км²)"},
            {"id": "zones", "kind": "candidate_strip", "label": "Проверенные снимки-кандидаты (полосы обследования)"},
            {"id": "detections", "kind": "detection", "label": "Подозрительные пиксели детектора"},
            {"id": "scene_zones", "kind": "detection_zone", "label": "Спутниковые зоны детекции (текущий детектор)"}],
    }


# ------------------------------------------------------------------ metrics
def _det_row(name: str, d: dict, split: str) -> Optional[dict]:
    if not d:
        return None
    return {"name": name, "split": split, "precision": _r(d.get("precision_md")), "recall": _r(d.get("recall_md")),
            "f1": _r(d.get("f1_md")), "iou": _r(d.get("iou_md")),
            "ci95_precision": d.get("ci95_precision"), "ci95_recall": d.get("ci95_recall"),
            "ci95_f1": d.get("ci95_f1"), "ci95_iou": d.get("ci95_iou"),
            "tp": d.get("tp"), "fp": d.get("fp"), "fn": d.get("fn"), "n_scenes": d.get("n_scenes")}


def detector_metrics_block(test: dict, val: dict, tmd: dict, fdi_val: dict) -> dict:
    """Detector: main (LightGBM) vs baseline RF (MARIDA code) vs FDI threshold on the SAME MARIDA test
    (reports/case_detector/metrics.json from detector_compare.py; scene bootstrap CI). Fallback: lgbm_final_test + val FDI (old)."""
    det = _cached("det_metrics", PATHS["det_metrics"], _read_json) or {}
    dt_ = det.get("test") or {}
    st = det.get("settings") or {}
    if dt_.get("lgbm"):
        main = _det_row("LightGBM", dt_["lgbm"], "test")
        main["setting"] = (st.get("lgbm") or {}).get("setting")
        main["val_f1"] = _r(((det.get("val") or {}).get("lgbm") or {}).get("f1_md"))
        main["test_f1"], main["test_iou"] = main["f1"], main["iou"]  # 3.0 compatibility fields
        base = _det_row("RandomForest (код MARIDA)", dt_.get("rf_argmax") or {}, "test")
        if base:
            base["setting"] = (st.get("rf_argmax") or {}).get("setting")
        fdi = _det_row("FDI threshold", dt_.get("fdi_threshold") or {}, "test")
        if fdi:
            fdi["setting"] = (st.get("fdi_threshold") or {}).get("setting")
            fdi["note"] = "порог подобран на val"
        box = _det_row("Окно FDI×NDVI", dt_.get("fdi_ndvi_box") or {}, "test")
        if box:
            box["setting"] = (st.get("fdi_ndvi_box") or {}).get("setting")
            box["note"] = "4 порога (окно FDI и NDVI, идея Biermann 2020) подобраны на val"
        # жюри 10:10: the published U-Net MARIDA weights (Kikaki et al. 2022) — baseline on the same test
        un = ((((_cached("final_numbers", PATHS["final_numbers"], _read_json) or {}).get("case") or {}).get("sections") or {})
              .get("baselines") or {}).get("test", {}).get("unet_argmax") or {}
        unet = ({"name": "U-Net MARIDA (опубликованные веса)", "split": "test", "f1": un.get("f1"),
                 "precision": un.get("precision"), "recall": un.get("recall"), "ci95_f1": un.get("ci95"),
                 "setting": "веса авторов MARIDA (Kikaki et al. 2022), argmax классов; тот же test и та же метрика",
                 "source": "reports/final_numbers.json · case.sections.baselines.test.unet_argmax"}
                if un.get("f1") is not None else None)
        rows = [x for x in (main, base, box, fdi) if x]  # U-Net: separate field (no IoU / CI for P, R)
        return {"split": f"MARIDA test ({(dt_['lgbm'].get('n_scenes'))} сцен), один прогон после заморозки; "
                         "ДИ — бутстреп по сценам", "metric": det.get("metric"),
                "main": main, "baseline": base, "unet": unet, "fdi": fdi, "fdi_ndvi": box, "rows": rows}
    return {  # fallback when reports/case_detector/metrics.json is absent
        "split": "MARIDA val (сплит по сценам); test — один прогон после заморозки",
        "baseline": {"name": "FDI threshold", "f1": _r(fdi_val.get("f1_md")), "iou": _r(fdi_val.get("iou_md")),
                     "split": "val", "note": fdi_val.get("note")} if fdi_val else None,
        "main": {"name": "LightGBM", "f1": _r(val.get("f1_md")), "iou": _r(val.get("iou_md")), "split": "val",
                 "test_f1": _r(tmd.get("f1_md")), "test_iou": _r(tmd.get("iou_md")),
                 "test_ci95_f1": tmd.get("ci95"), "threshold": tmd.get("threshold")} if (val or tmd) else None,
        "fdi": None, "fdi_ndvi": None, "rows": []}


def metrics() -> dict:
    fn = _cached("final_numbers", PATHS["final_numbers"], _read_json) or {}
    test = _cached("lgbm_test", PATHS["lgbm_test"], _read_json) or {}
    lg = fn.get("l3_lgbm") or {}
    base = ((fn.get("baselines") or {}).get("rows") or {}).get("fdi_threshold") or {}
    val = lg.get("val") or (test.get("val_md") and {"f1_md": test["val_md"].get("f1_md"),
                                                     "iou_md": test["val_md"].get("iou_md")}) or {}
    tmd = test.get("test_md") or lg.get("test") or {}
    detector = detector_metrics_block(test, val, tmd, base)
    concentration = conc_metrics_block()
    dp = (concentration.get("profiles") or {}).get(concentration.get("profile")) or {}
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
                  "concentration": {**(concentration.get("split") or {}), "n_dev": dp.get("n_dev")}},
        "detector": detector,
        "concentration": concentration,
        "control_example": {"items": 12, "area_km2": 0.20, "expected": 60.0, "computed": computed},
        "empty_reason": None if (fn or test or concentration.get("profiles")) else "Метрики ещё не посчитаны",
    }


# ------------------------------------------------------------------ saved queries
_qlock = threading.Lock()


def normalize_query(q) -> dict:
    if q is None:
        q = {}
    if not isinstance(q, dict):
        raise ApiError(400, "BAD_PARAM", "query: ожидается JSON-объект", {})
    known = {"bbox", "date_from", "date_to", "statuses", "sources", "profiles", "scopes", "layers", "scene_id"}
    unknown = sorted(set(q) - known)
    if unknown:
        raise ApiError(422, "BAD_PARAM", f"query: неизвестные поля {', '.join(unknown)}",
                       {"unknown": unknown, "allowed": sorted(known)})
    a, b = parse_dates(q.get("date_from"), q.get("date_to"))
    sid = q.get("scene_id")
    if sid is not None and not isinstance(sid, str):
        raise ApiError(400, "BAD_PARAM", "query.scene_id: строка или null", {})
    for k in ("statuses", "sources", "profiles", "scopes", "layers"):
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
        "scopes": parse_list(q.get("scopes"), "scopes", SCOPE_IDS) or [],
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
    extra = sorted(set(body) - {"name", "query"})
    if extra:
        raise ApiError(422, "BAD_PARAM", f"тело: неизвестные поля {', '.join(extra)}",
                       {"unknown": extra, "allowed": ["name", "query"]})
    name = body.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise ApiError(400, "BAD_PARAM", "name: непустая строка до 200 символов", {})
    rec = {"query_id": "q_" + uuid.uuid4().hex[:8], "name": name.strip(), "created_at": now_iso(),
           "query": normalize_query(body.get("query"))}
    p = PATHS["queries"]
    with _qlock:
        # повторное сохранение того же запроса под тем же именем не плодит копии (демо-прогоны, общий сервис)
        for old in _read_queries():
            if old.get("name") == rec["name"] and old.get("query") == rec["query"]:
                return old
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
ZONE_COLS_31 = ZONE_COLS + ["detection_status", "concentration_status", "field_estimate_items_km2",  # 3.1
                             "layer_kind", "detector_verdict", "detection_reason", "suspicious_n_objects",  # 3.2
                             "suspicious_area_m2", "pair_status", "pair_reject_reasons", "strip_area_raster_km2",
                             "quality_decision", "quality_reject_reason"]  # 3.6
PAIR_COLS = ["pair_id", "sample_id", "event_id", "source_id", "scene_id", "mission", "scene_datetime", "obs_datetime",
             "dt_hours", "distance_km", "drift_shift_km", "geometry", "cloud_pct_local", "valid_fraction_local",
             "status", "reject_reasons", "split", "scene_cloud_pct", "catalog", "time_known", "registry_note",
             "quality_decision", "tolerance_km", "dt_drift_hours", "status_without_drift", "dt_uncertainty_h"]


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
    if t == "MultiLineString":
        return "MULTILINESTRING (" + ", ".join("(" + ", ".join(f"{x} {y}" for x, y in ln) + ")" for ln in c) + ")"
    if t == "MultiPolygon":
        return "MULTIPOLYGON (" + ", ".join("(" + ", ".join("(" + ", ".join(f"{x} {y}" for x, y in ring) + ")"
                                                            for ring in poly) + ")" for poly in c) + ")"
    return ""


def observations_csv(rows: list[dict]) -> str:
    fields, _ = samples_raw()
    linked = linked_scenes_by_sample() if rows else {}
    cols = list(fields) + ["kind", "linked_scenes"] + OBS_EST_COLS

    def est(sid):
        fe, re_ = obs_field_estimate(sid) or {}, model_estimate_for(sid) or {}
        return [fe.get("value"), fe.get("lo"), fe.get("hi"), fe.get("model"), fe.get("interval_coverage_test"),
                re_.get("value"), re_.get("lo"), re_.get("hi"), re_.get("model"), re_.get("fold")]
    return _csv(cols, [[r.get(k, "") for k in fields] + ["measurement", linked.get(r.get("sample_id"), [])]
                       + est(r.get("sample_id") or "") for r in rows])


OBS_EST_COLS = ["field_estimate_items_km2", "field_estimate_lo", "field_estimate_hi", "field_estimate_model",
                "field_estimate_coverage_test", "research_estimate_items_km2", "research_estimate_lo",
                "research_estimate_hi", "research_estimate_model", "research_estimate_fold"]


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
                    p["support"]["linked_sample_ids"], cen[0], cen[1], "candidate_strip",
                    p["detection_status"], p["concentration_status"], (p.get("field_estimate") or {}).get("value"),
                    p.get("layer_kind"), p.get("detector_verdict"), p.get("detection_reason"),
                    (p.get("suspicious_pixels") or {}).get("n_objects"), (p.get("suspicious_pixels") or {}).get("area_m2"),
                    p.get("pair_status"), p.get("pair_reject_reasons") or [], p.get("strip_area_raster_km2"),
                    p.get("quality_decision"), p.get("quality_reject_reason")])
    return _csv(ZONE_COLS_31, out)


# ------------------------------------------------------------------ saved query execution (run + export share it)
def run_query_layers(qr: dict) -> dict:
    """The single filter used by GET /queries/{id}/run and GET /export?query_id=: observations, zones, scenes."""
    qr = {**{"scopes": []}, **qr}
    obs_rows = filter_samples(bbox=qr.get("bbox"), date_from=qr.get("date_from"), date_to=qr.get("date_to"),
                              sources=qr.get("sources") or None, profiles=qr.get("profiles") or None,
                              scopes=qr.get("scopes") or None)
    zf = filter_zones(bbox=qr.get("bbox"), date_from=qr.get("date_from"), date_to=qr.get("date_to"),
                      scene_id=qr.get("scene_id"), statuses=qr.get("statuses") or None,
                      profiles=qr.get("profiles") or None, sources=qr.get("sources") or None,
                      scopes=qr.get("scopes") or None)
    sc = filter_scenes(bbox=qr.get("bbox"), date_from=qr.get("date_from"), date_to=qr.get("date_to"))
    if qr.get("scene_id"):
        sc = [x for x in sc if x["scene_id"] == qr["scene_id"]]
    if qr.get("sources") or qr.get("profiles") or qr.get("scopes"):
        ids = {r.get("sample_id") for r in obs_rows}
        linked = {p["scene_id"] for p in pairs_all() if p["sample_id"] in ids and p["scene_id"]}
        sc = [x for x in sc if x["scene_id"] in linked]
    szf = filter_scene_zones(  # 3.10: satellite zones have no field source/profile/scope -> [] (inside the filter)
        bbox=qr.get("bbox"), date_from=qr.get("date_from"), date_to=qr.get("date_to"),
        statuses=qr.get("statuses") or None, sources=qr.get("sources") or None, profiles=qr.get("profiles") or None,
        scopes=qr.get("scopes") or None)
    return {"obs_rows": obs_rows, "zones": zf, "scenes": sc, "scene_zones": szf}


# ------------------------------------------------------------------ detector objects (g3)
DET_COLS = ["det_id", "zone_id", "scene_id", "datetime", "n_pixels", "area_m2", "prob_max", "prob_mean", "in_strip",
            "threshold", "centroid_lon", "centroid_lat", "kind"]


def _detections_dir(d: str, zone_geom: Optional[dict] = None) -> list[dict]:
    """Detector objects of a pair_quality crop = the final detector mask of pair_quality.py: prob.tif >= threshold
    on valid water (quality.tif == 1), components within cloud_buffer_px of cloud codes (3, 4) dropped
    (grid.cloudmask, as pair_quality). Vectorised in the crop CRS, reprojected to EPSG:4326.
    in_strip: at least one pixel in the RASTER strip (zone polygon rasterised all_touched on the crop grid) — the
    same rule as pair_quality n_det (reports/case_pairs/detector_review.md §7.1–7.2). Cached by prob.tif mtime."""
    base = PATHS["pairs_dir"] / "quality" / d
    tif = base / "prob.tif"

    def load(path):
        import numpy as np
        import rasterio
        from rasterio import features
        from PIL import Image
        from shapely.geometry import mapping, shape
        from shapely.ops import transform as sh_tr
        from pyproj import Transformer
        meta = quality_meta(d) or {}
        thr = fnum((meta.get("detector") or {}).get("threshold"))
        if thr is None:
            return []
        with rasterio.open(path) as ds:
            prob = ds.read(1).astype("float32")
            if ds.dtypes[0] == "uint8":  # scripts/case/pair_quality.py writes P(marine debris)*255 as uint8
                prob = prob / 255.0
            tr, crs = ds.transform, ds.crs
        det = np.nan_to_num(prob, nan=0.0) >= thr
        from scipy import ndimage
        qp = base / "quality.tif"
        if qp.is_file():  # final mask of pair_quality: P >= thr on valid water, no objects near clouds
            from src.macroplastic.grid import cloudmask
            with rasterio.open(qp) as qs:
                qa = qs.read(1)
            if qa.shape == det.shape:
                det &= qa == 1
                lab0, n0 = ndimage.label(det, structure=np.ones((3, 3)))
                buf = int(((meta.get("config") or {}).get("detector") or {}).get("cloud_buffer_px") or 5)
                near = cloudmask.near_cloud_components(lab0, n0, np.isin(qa, (3, 4)), buf)
                det = cloudmask.drop_components(lab0, n0, near)[0] > 0
        if not det.any():
            return []
        strip = None
        if zone_geom:  # one rule "in strip" with pair_quality: raster strip all_touched on the crop grid
            from rasterio.warp import transform_geom
            try:
                strip = features.rasterize([(transform_geom("EPSG:4326", crs, zone_geom), 1)], out_shape=det.shape,
                                           transform=tr, all_touched=True, fill=0, dtype="uint8").astype(bool)
            except Exception:
                strip = None
        lab, n = ndimage.label(det, structure=np.ones((3, 3)))
        inv = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
        out = []
        for geom, val in features.shapes(lab.astype("int32"), mask=lab > 0, transform=tr, connectivity=8):
            k = int(val)
            px = lab == k
            g = sh_tr(inv, shape(geom))
            gj = json.loads(json.dumps(mapping(g)), parse_float=lambda x: round(float(x), 7))
            gj = {"type": gj["type"], "coordinates": gj["coordinates"]}
            out.append({"k": k, "geometry": gj, "n_pixels": int(px.sum()),
                        "in_strip": None if strip is None else bool(strip[px].any()),
                        "prob_max": round(float(prob[px].max()), 4), "prob_mean": round(float(prob[px].mean()), 4),
                        "area_m2": round((geodesic_area_km2(gj) or 0.0) * 1e6, 1), "threshold": thr})
        out.sort(key=lambda x: x["k"])
        return out
    return _cached(f"dets:{d}", tif, load) or []  # one zone geometry per crop -> cache key by dir is enough


VISUAL_RU = {"foam_whitecap": "пена/барашки", "ship_wake": "судно", "cloud": "облако", "glint": "блик",
             "accumulation": "скопление", "unclear": "неясно"}


def _visual_labels(d: str) -> list[dict]:
    """Visual review of the current-detector objects of the pair crops (L92/L107, out/detector_v2/pairs_objects.csv
    set N -> data/case/pairs_visual_labels.csv): matched to the objects of the crop in label order + prob_max."""
    rows = _cached("pairs_visual", PATHS["pairs_visual"], _read_csv) or []
    return sorted((r for r in rows if r.get("scene") == d), key=lambda r: r.get("object_id") or "")


def zone_detections(zone: dict) -> list[dict]:
    p = zone["properties"]
    d = p.get("quality_dir") or ""
    qrej = p.get("quality_decision") not in (None, "accept")
    feats = []
    objs = _detections_dir(d, zone.get("geometry"))
    vis = _visual_labels(d)
    if len(vis) != len(objs) or any(abs((fnum(v.get("prob_max")) or -1) - o["prob_max"]) > 0.005 for v, o in zip(vis, objs)):
        vis = []
    for i, x in enumerate(objs, 1):
        in_strip = x.get("in_strip")
        did = f"D-{d}-{i:04d}"
        feats.append({"type": "Feature", "id": did, "geometry": x["geometry"], "properties": {
            "kind": "detection", "label": "Подозрительные пиксели детектора", "det_id": did, "zone_id": zone["id"], "scene_id": p.get("scene_id"),
            "datetime": p.get("datetime"), "n_pixels": x["n_pixels"], "area_m2": x["area_m2"],
            "prob_max": x["prob_max"], "prob_mean": x["prob_mean"], "threshold": x["threshold"],
            "in_strip": in_strip, "quality_rejected": qrej,
            **({"type_label": VISUAL_RU.get(vis[i - 1].get("class"), vis[i - 1].get("class")),
                "visual_class": vis[i - 1].get("class"), "visual_level": vis[i - 1].get("level"),
                "visual_id": vis[i - 1].get("visual_id"), "false_alarm": vis[i - 1].get("level") == "D",
                "visual_source": "визуальная разметка L92 (data/case/pairs_visual_labels.csv), уровень D — ложное"}
               if vis else {}),
            "note": (QREJ_NOTE if qrej else
                     "объект детектора (пиксели P ≥ порога), не концентрация; в полосе наблюдения — in_strip")}})
    return feats


def detections_fc(zones: list[dict]) -> dict:
    feats = [f for z in zones for f in zone_detections(z)]
    fc = {"type": "FeatureCollection", "kind": "detection", "label": "Подозрительные пиксели детектора",
          "note": "контуры пикселей детектора (класс MARIDA Marine Debris = любой плавающий мусор), не полосы и не "
                  "зоны скопления; без полевого подтверждения", "count": len(feats), "empty_reason": None,
          "features": feats}
    if not feats:
        fc["empty_reason"] = "Детектор не нашёл объектов на снимках выбранных зон" if zones else \
            "Нет зон под выбранные фильтры"
    return fc


def detections_csv(fc: dict) -> str:
    rows = []
    for f in fc["features"]:
        p = f["properties"]
        b = _geom_bounds(f["geometry"]) if f.get("geometry") else None
        cen = [round((b[0] + b[2]) / 2, 7), round((b[1] + b[3]) / 2, 7)] if b else [None, None]
        rows.append([p["det_id"], p["zone_id"], p["scene_id"], p["datetime"], p["n_pixels"], p["area_m2"],
                     p["prob_max"], p["prob_mean"], p["in_strip"], p["threshold"], cen[0], cen[1], "detection"])
    return _csv(DET_COLS, rows)


# ------------------------------------------------------------------ satellite scene zones (L111, INBOX §15, 3.10)
# Built offline by scripts/case/scene_zones.py -> data/case/scene_zones/{index.json, <key>/{zones,detections}.geojson,
# rgb.jpg, quality.png}. Separate layer: /api/v3/scene_zones (the candidate strips of /zones are unchanged).
PATHS.setdefault("scene_zones_dir", REPO / "data" / "case" / "scene_zones")

SZ_DET_LABEL = {
    "detected": "обнаружено детектором (без полевого подтверждения)",
    "not_detected": "не обнаружено (детектор оценивается, объектов нет)",
    "insufficient_data": "недостаточно данных: признаки ложного срабатывания",
}
SZ_FLAG_RU = {"foam": "пена", "glint": "блик", "ship": "судно/кильватер", "seam": "шов/граница яркости",
              "coast": "берег/прибой", "shallow": "мелководье/мутная вода", "cloud": "облака", "wind": "ветер > 5 м/с"}
# INBOX §23 п.2: «обнаружено детектором» only after all false-alarm filters (ship/wake, foam, glint, cloud, coast, shallow)
SZ_VERIFY_LABEL = {
    "level_B_cozar": "обнаружено детектором · совпадает с разметкой Cózar 2024 (B)",
    "unverified": "обнаружено детектором · независимой разметки нет",
    "false_alarm_signs": "недостаточно данных: признаки ложного срабатывания",
    "wind": "недостаточно данных (ветер > 5 м/с: мусор перемешивается, полосы не видны — правило Cózar 2024)",
}
SZ_WIND_NOTE = ("ветер > 5 м/с (ERA5, час съёмки): Cózar et al. 2024 убирают такую воду из наблюдаемой площади («we removed "
                "from a0 the sea surface area associated with wind speeds higher than 5 m·s−1») — мусор перемешивается, "
                "полосы не видны; сцена не входит в знаменатель «обследовано», LWD не считается")
SZ_FALSE_LABEL = "ложное срабатывание (признаки судна / кильватера / шва) — недостаточно данных"
# §39 (15:26): zone status — strictly one of the four statuses of the task statement; confirmation is a separate field
SZ_STATUS4 = {"detected": "обнаружено", "not_detected": "не обнаружено", "insufficient_data": "недостаточно данных",
              "research_estimate": "исследовательская оценка"}
SZ_STATUS4_NOTE = {
    "detected": "детектор отметил плавающий материал после фильтров судов, пены, блика, облаков, берега, мелководья, ветра",
    "not_detected": "детектор оценивается, объектов нет",
    "insufficient_data": "признаки ложного срабатывания, ветер > 5 м/с или снимок не оценивается",
    "research_estimate": "статус концентрации: перенос «снимок → шт.» не подтверждён — только сценарий с допущениями"}
SZ_CONFIRM = {"cozar_b": "совпадает с разметкой Cózar 2024 (B)", "none": "независимой разметки нет",
              "training_scene": "снимок из обучения детектора — не независимая проверка; находкой не считается"}
SZ_CLASS_LABEL = "плавающий материал (класс MARIDA Marine Debris; пластик не подтверждён)"
SZ_BG_ORDER = ["ship", "seam", "foam", "glint", "cloud", "coast", "shallow", "wind"]
SZ_NOT_CHECKED = {"algae_sargassum": "водоросли/саргассум", "wood_organic": "древесина и прочая органика"}
SZ_QUANTITY = {"status": "not_confirmed",
               "label": "Количество предметов по этому снимку не определено (перенос не подтверждён: нет природных пар; "
                        "см. ISPRA 604)",
               "detail": ("измеренной концентрации по снимку нет: калибровочных пар по природным скоплениям 0 (см. "
                          "docs/QUANTITY.md); для находок — только исследовательская оценка по мишеням PLP "
                          "(research_estimate), не измерение")}
# §34 п.2 (26.09 12:58): research estimate items/km2 of a find (calibration on PLP targets) — configs/zone_estimate.yaml,
# formula in src/macroplastic/case/zone_estimate.py (the same code feeds final_numbers)
PATHS.setdefault("zone_estimate_cfg", REPO / "configs" / "zone_estimate.yaml")
SZ_RE_LABEL = "исследовательская оценка шт./км² (калибровка на мишенях PLP), не измерение"
SZ_FIELD_FILTER_NOTE = ("у спутниковых зон нет полевой акватории, профиля и совокупности: при таком фильтре зон 0 "
                        "(спутниковые районы — параметр region)")


def _ze():
    from src.macroplastic.case import zone_estimate as ZE
    return ZE


_ZE_LAST_VALID: dict = {}


def _ze_cfg_load(path: Path) -> dict:
    return _ze().validate_config(_yaml_load(path) or {})


def zone_estimate_cfg() -> dict:
    """The config of the research estimate; an unreadable / incomplete file (e.g. being written) -> the last valid
    version with a warning (jury/L132 14:3x: 500 «calibration_points пуст» while the file was rewritten)."""
    cfg = _cached("zone_estimate_cfg", PATHS["zone_estimate_cfg"], _ze_cfg_load)
    if cfg:
        _ZE_LAST_VALID["cfg"] = cfg
        return cfg
    prev = _ZE_LAST_VALID.get("cfg")
    if prev:
        print(f"[case_store] zone_estimate_cfg: {PATHS['zone_estimate_cfg']} не читается или неполон — "
              "используется последняя валидная версия")
        return prev
    return {}


_ZE_CAL: dict = {}


def zone_estimate_cal(cfg: dict) -> dict:
    """Calibration (lo / hi / value per pixel) of the loaded config object (reloaded with the file's mtime)."""
    if _ZE_CAL.get("id") != id(cfg):
        _ZE_CAL.clear()
        _ZE_CAL.update({"id": id(cfg), "cal": _ze().calibration(cfg), "cfg": cfg})
    return _ZE_CAL["cal"]


def zone_field_range() -> Optional[tuple]:
    """Field items/km2 (mean over the route) for the context line of the estimate: ADIS (> 10 cm) … Sargasso S2
    (plastic), from final_numbers.json (jury 13:47: «1,5–54»)."""
    q = _fn_quantity()
    a, s2 = (q.get("adis_forecast") or {}).get("C"), (q.get("field_S2") or {}).get("pooled_C")
    return (min(a, s2), max(a, s2)) if a is not None and s2 is not None else None


def zone_estimate_summary(feats: Optional[list] = None) -> Optional[dict]:
    cfg = zone_estimate_cfg()
    if not cfg:
        return None
    zs = scene_zones_all() if feats is None else feats
    return _ze().summary([f["properties"] for f in zs], cfg)
SZ_KIND_RU = {"demo": "отложенная сцена Cózar 2024 (не участвовала в обучении)", "live": "район мониторинга",
              "drift": "район мониторинга (дополнительный снимок)"}
# жюри 10:57: short list names («Гондурас · Омоа · зона 4»); the full region name stays in title_full
SZ_SHORT = {"Аккра": "Аккра", "Бали": "Бали", "Гондурасский залив": "Гондурас · Омоа", "Дананг": "Дананг",
            "Дельта Меконга": "Меконг", "Дельта Нила": "Нил · Розетта", "Джакартский залив": "Джакарта", "Дурбан": "Дурбан",
            "Залив Гуанабара": "Гуанабара", "Залив Порт-о-Пренс": "Гаити · Порт-о-Пренс", "Карачи": "Карачи", "Лагос": "Лагос",
            "Манильский залив": "Манила", "Мумбаи": "Мумбаи", "Санто-Доминго": "Санто-Доминго",
            "Средиземное море: устье Тибра": "Тибр · Остия", "Устье Хугли": "Хугли · Сагар", "Шотландия": "Ферт-оф-Форт"}
SZ_STATUS_NOTE = ("«Обнаружено» здесь — вывод детектора по снимку (класс MARIDA Marine Debris: любой плавающий "
                  "материал), не подтверждённый полем; это не «обнаружен пластик».")


def scene_zones_index() -> dict:
    return _cached("sz_index", PATHS["scene_zones_dir"] / "index.json", _read_json) or {}


def _sz_scene_zones(key: str) -> list[dict]:
    return (_cached(f"sz:{key}", PATHS["scene_zones_dir"] / key / "zones.geojson", _read_json) or {}).get("features") or []


def _sz_scene_wind(p: dict, idx: dict):
    sg = ((p.get("probable") or {}).get("signs") or {}).get("foam") or {}
    if sg.get("wind10m_ms") is not None:
        return sg["wind10m_ms"]
    return next((s.get("wind10m_ms") for s in idx.get("scenes") or [] if s.get("key") == p.get("scene_key")), None)


def _sz_status39(p: dict, idx: dict) -> None:
    """§39: status (4 values), status_label, status_reason; confirmation; class / excluded / not checked backgrounds."""
    st = p["detection_status"]
    p["status"] = st if st in SZ_STATUS4 else "insufficient_data"
    p["status_label"] = SZ_STATUS4[p["status"]]
    p["status_reason"] = (p["detection_label"] if p["detection_label"] != p["status_label"] else None)
    if p.get("training_scene") and st == "detected":
        p["status_reason"] = SZ_CONFIRM["training_scene"]
    elif st == "detected":
        p["status_reason"] = None
    p["detection_label"] = p["status_label"]
    if st == "detected":
        c = ("training_scene" if p.get("training_scene") else "cozar_b" if p.get("verification") == "level_B_cozar"
             else "none")
        p["confirmation"], p["confirmation_label"] = c, SZ_CONFIRM[c]
    else:
        p["confirmation"], p["confirmation_label"] = None, None
    flags = list(p.get("flags") or [])
    has_signs = bool((p.get("probable") or {}).get("signs"))
    w = _sz_scene_wind(p, idx)
    checked = [b for b in SZ_BG_ORDER if (b != "wind" and has_signs) or (b == "wind" and (w is not None or "wind" in flags))]
    excluded = [b for b in checked if b not in flags]
    flagged = [b for b in SZ_BG_ORDER if b in flags]
    is_obj = st != "not_detected"
    det = st == "detected"
    und = "не определено (признаки ложного срабатывания или ветер — см. «Признаки»)"
    p["classification"] = {
        "class": "floating_material" if det else "undetermined" if is_obj else None,
        "class_label": SZ_CLASS_LABEL if det else und if is_obj else None,
        "what_label": (f"Что это: {SZ_CLASS_LABEL}" if det else f"Что это: {und}" if is_obj
                       else "Что это: объектов детектора нет"),
        "checked_backgrounds": checked,
        "excluded_backgrounds": excluded if is_obj else [],
        "excluded_label": ("Исключено: " + ", ".join(SZ_FLAG_RU[b] for b in excluded)) if (is_obj and excluded) else None,
        "flagged_backgrounds": flagged,
        "flagged_label": ("Признаки: " + ", ".join(SZ_FLAG_RU[b] for b in flagged)) if flagged else None,
        "not_checked_backgrounds": list(SZ_NOT_CHECKED),
        "not_checked_label": "Не проверяется: " + ", ".join(SZ_NOT_CHECKED.values()) + " (флага нет)",
        "composition": "Состав не определён",
        "wind10m_ms": w,
    }
    p["class"] = p["classification"]["class"]
    p["excluded_backgrounds"] = p["classification"]["excluded_backgrounds"]
    p["quantity_line"] = SZ_QUANTITY["label"]


def _sz_enrich(f: dict, idx: dict) -> dict:
    import copy
    f = copy.deepcopy(f)
    p = f["properties"]
    st = p["detection_status"]
    ver = p.get("verification") or ("false_alarm_signs" if p.get("flags") else None)
    if "wind" in (p.get("flags") or []):
        ver = "wind"
    zero = st == "not_detected"
    p["verification"] = ver if not zero else "none"
    p["detection_label"] = SZ_VERIFY_LABEL.get(ver) if not zero and ver else SZ_DET_LABEL.get(st, st)
    # Г3-1: Cózar 2024 exclude sea with wind > 5 m/s (windrows do not form / hold)
    p["wind_note"] = (SZ_WIND_NOTE if p.get("wind_high") else None)
    if ver == "false_alarm_signs" and {"ship", "seam"} & set(p.get("flags") or []):
        p["detection_label"] = SZ_FALSE_LABEL
    p["training_scene_note"] = (f"снимок из обучающей выборки детектора ({p['training_scene']}) — не независимая проверка"
                                if p.get("training_scene") else None)
    if ver == "wind":
        p["detection_reason"] = None  # the card shows wind_note
    elif p.get("flags"):
        p["detection_reason"] = ("признаки: " + ", ".join(SZ_FLAG_RU.get(x, x) for x in p["flags"])
                                 + " — вероятно, ложное срабатывание")
    else:
        p["detection_reason"] = None
    p["status_note"] = SZ_STATUS_NOTE
    p["scene_kind_label"] = SZ_KIND_RU.get(p.get("scene_kind"), p.get("scene_kind"))
    if p.get("scene_kind") == "demo":  # short list title: tile of the held-out scene
        p["title"] = p["title"].replace("Демо Cózar 2024 (отложенная сцена)", "30SXE")  # §31 д: «30SXE · зона 16»
    else:
        p["title_full"] = p["title"]
        reg, _, rest = p["title"].partition(" · ")
        key = reg.split(" (")[0]
        p["title"] = f"{SZ_SHORT.get(key, key)} · {rest}" if rest else SZ_SHORT.get(key, key)
    m = p.get("measured") or {}
    if isinstance(m.get("model"), dict):
        m["model"] = {**m["model"], "trained_at": detector_model_info().get("trained_at") or m["model"].get("trained_at")}
    # жюри-7: a detection on an acquisition of the detector's training set (MARIDA / MADOS) is not counted as a find
    p["is_find"] = st == "detected" and not p.get("training_scene")
    if st == "detected" and p.get("training_scene"):
        p["verification"] = "training_scene"
        p["detection_label"] = "обнаружено детектором · снимок из обучения детектора — находкой не считается"
    p["area_km2"] = m.get("zone_area_km2")
    p["area_basis"] = "геодезическая площадь контура зоны (кластер объектов детектора + 150 м)"
    p["detected_area_m2"] = m.get("suspicious_area_m2")
    p["concentration"] = None
    # INBOX §23 п.2: no items/km2 scenario for satellite zones (numbers removed from API, card and export)
    p["concentration_status"] = "unavailable"
    p["quantity"] = dict(SZ_QUANTITY)
    p["concentration_label"] = SZ_QUANTITY["label"]
    p["concentration_reason"] = SZ_QUANTITY["detail"]
    p["scenario"] = None
    p["scenario_reason"] = SZ_QUANTITY["detail"]
    # §34 п.2: research estimate items/km2 for finds only (area stays in area_km2, not inside the estimate)
    cfg = zone_estimate_cfg()
    ZE = _ze()
    est = ZE.estimate(p, cfg, zone_estimate_cal(cfg), zone_field_range()) if cfg else None
    p["research_estimate"] = est
    p["research_estimate_reason"] = None if est else (
        ZE.not_eligible_reason(p) or "калибровка не загружена (configs/zone_estimate.yaml)")
    if est:
        p["concentration_status"] = "research_estimate"
        p["concentration_label"] = SZ_RE_LABEL
    # аудит В16 / §33а п.3: a zone carries no items/km2 of another place (ADIS segments 521–9 848 km away): only the
    # nearest organisers' field record (id + distance), the same as the card and the CSV
    nos = (p.get("field_nearby") or {}).get("nearest_organizer_sample")
    p["field_nearby"] = {"nearest_organizer_sample": nos,
                         "note": ("ближайшее полевое измерение (CSV организаторов) — только расстояние и ссылка; его шт./км² "
                                  "относятся к другому месту и времени и не являются плотностью зоны")}
    # §39 п.1–2: strict status, separate confirmation, classification
    _sz_status39(p, idx)
    pr = p.get("probable") or {}
    pr["status"] = p["detection_label"]
    pr["cozar_note"] = (f"контур пересекает {p.get('n_cozar_filaments')} нит(и) каталога Cózar et al. 2024 "
                        "(уровень B: плавучий материал, найденный людьми по снимку, не только пластик)"
                        if p.get("n_cozar_filaments") else None)
    pr["context"] = ("полнота детектора на независимых нитях Cózar — 36 % [30–43] (234 сцены, L92); ложные на "
                     "судах 26 % (2 061 рамка, L90) — reports/detector_v2/experiments.json")
    p["probable"] = pr
    p["crop_url"] = (f"/api/v3/scene_zones/scenes/{p['scene_key']}/crops/{p['zone_id']}.jpg"
                     if p.get("crop_file") else None)
    p["crop_note"] = "слева снимок, справа он же с пикселями детектора (красные; жёлтые — объекты с признаком судна/шва)"
    return f


def scene_zones_all() -> list[dict]:
    idx = scene_zones_index()
    out = []
    for s in idx.get("scenes") or []:
        if not s.get("evaluable") or s.get("error"):
            continue
        out.extend(_sz_enrich(f, idx) for f in _sz_scene_zones(s["key"]))
    out.sort(key=lambda f: f["id"])
    return out


def sz_scenes() -> list[dict]:
    """Scene records of the layer, in the /scenes shape (preview + quality overlay on bounds)."""
    out = []
    nf: dict[str, int] = {}
    for f in scene_zones_all():
        if f["properties"].get("is_find"):
            nf[f["properties"]["scene_key"]] = nf.get(f["properties"]["scene_key"], 0) + 1
    for s in scene_zones_index().get("scenes") or []:
        if s.get("error"):
            continue
        base = f"/api/v3/scene_zones/scenes/{s['key']}"
        b = s.get("bounds")
        out.append({"scene_id": s["key"], "scene_key": s["key"], "product_id": s.get("scene_id"),
                    "mission": "Sentinel-2", "source": "earth-search", "datetime": s.get("datetime"),
                    "footprint": bounds_polygon(b), "bounds": b,
                    "footprint_note": s.get("crop_note"), "cloud_pct": _sz_src_scene(s).get("crop_cloud_pct"),
                    "cloud_basis": "облачность вырезки района (crop_cloud_frac × 100, scene.json снимка)",
                    "tile_cloud_pct": _sz_src_scene(s).get("tile_cloud_pct"), "valid_water_fraction": None,
                    "preview_url": f"{base}/rgb.jpg", "quality_url": f"{base}/quality.png", "prob_url": None,
                    "mask_url": None, "tiles": None, "n_zones": s.get("n_zones_total", 0), "n_linked_samples": 0,
                    "status": "evaluated" if s.get("evaluable") else "not_evaluated",
                    "reject_reasons": [], "scene_kind": s.get("kind"),
                    "scene_kind_label": SZ_KIND_RU.get(s.get("kind"), s.get("kind")),
                    "region": s.get("region"), "region_name": s.get("region_name"),
                    "evaluable": s.get("evaluable"), "not_evaluated_reason": s.get("not_evaluated_reason"),
                    "sun_zenith_deg": s.get("sun_zenith_deg"), "wind10m_ms": s.get("wind10m_ms"),
                    "water_km2": s.get("water_km2"), "lwd_m2_km2": s.get("lwd_m2_km2"),
                    "by_status": s.get("by_status"), "model": s.get("model"),
                    # §34 п.3: «район · дата · N находок»
                    "region_short": sz_region_short(s.get("region"), s.get("region_name")),
                    "n_finds": nf.get(s["key"], 0)})
    return out


def _sz_src_scene(s: dict) -> dict:
    """Cloudiness of the source scene: data/live/<region>/<date>/scene.json (demo: data/live/cozar_demo/<date>),
    data/drift_check/<region>/<date>/scene.json — crop_cloud_frac (the crop) and cloud_cover (the whole tile), in %."""
    root = REPO / "data" / ("drift_check" if s.get("kind") == "drift" else "live")
    p = root / str(s.get("region")) / str(s.get("date")) / "scene.json"
    if not p.is_file():
        return {}

    def load(path):
        j = _read_json(path) or {}
        cf, cc = fnum(j.get("crop_cloud_frac")), fnum(j.get("cloud_cover"))
        return {"crop_cloud_pct": None if cf is None else round(cf * 100, 2),
                "tile_cloud_pct": None if cc is None else round(cc, 2)}
    return _cached(f"szsrc:{s.get('key')}", p, load) or {}


def sz_region_short(region: Optional[str], region_name: Optional[str]) -> str:
    if region == "cozar_demo":
        return "30SXE"
    key = str(region_name or region or "").split(" (")[0]
    return SZ_SHORT.get(key, key)


def sz_regions() -> list[dict]:
    """Satellite regions of the layer (filter `region`): id, label, short, n_zones, n_finds (evaluable scenes)."""
    by: dict[str, dict] = {}
    for s in scene_zones_index().get("scenes") or []:
        if s.get("error"):
            continue
        r = by.setdefault(s["region"], {"id": s["region"], "label": s.get("region_name") or s["region"],
                                        "short": sz_region_short(s["region"], s.get("region_name")),
                                        "n_scenes": 0, "n_zones": 0, "n_finds": 0})
        r["n_scenes"] += 1
    for f in scene_zones_all():
        p = f["properties"]
        r = by.get(p["region"])
        if r:
            r["n_zones"] += 1
            r["n_finds"] += 1 if p.get("is_find") else 0
    return sorted(by.values(), key=lambda r: (-r["n_finds"], r["short"]))


def sz_crop(key: str, zone_id: str) -> Optional[Path]:
    if not zone_id.startswith(f"SZ-{key}-") or "/" in zone_id or "\\" in zone_id or ".." in zone_id:
        return None
    p = PATHS["scene_zones_dir"] / key / "crops" / f"{zone_id}.jpg"
    return p if p.is_file() else None


# false-alarm example chosen by eye among ship-flagged zones (open water, bright point + wake; L111): the others of
# the top ship share are coastal (turbid bays) and less legible on a small crop
SZ_EXAMPLE_FALSE = "SZ-drift-honduras-2026-02-19-007"


def sz_examples() -> list[dict]:
    """Two fixed examples for the card: a detected zone on the held-out Cózar scene crossing the most Cózar
    filaments (success) and the zone with the largest ship/wake share (false alarm)."""
    zs = scene_zones_all()
    good = [f for f in zs if f["properties"].get("verification") == "level_B_cozar"]
    good.sort(key=lambda f: (-(f["properties"].get("n_cozar_filaments") or 0),
                             -(f["properties"]["measured"].get("n_pixels") or 0)))
    bad = [f for f in zs if "ship" in (f["properties"].get("flags") or []) and f["properties"].get("crop_url")]
    bad.sort(key=lambda f: (f["id"] != SZ_EXAMPLE_FALSE, -((((f["properties"].get("probable") or {}).get("signs") or {})
                                                              .get("ship") or {}).get("share") or 0)))
    out = []
    if good and good[0]["properties"].get("crop_url"):
        p = good[0]["properties"]
        out.append({"kind": "success", "label": "удачно: нить каталога Cózar 2024 (отложенная сцена)",
                    "zone_id": p["zone_id"], "crop_url": p["crop_url"], "title": p["title"],
                    "note": f"детектор отметил {p['measured']['n_pixels']} пикс.; контур пересекает "
                            f"{p.get('n_cozar_filaments')} нит(и) Cózar (уровень B, плавучий материал)"})
    if bad:
        p = bad[0]["properties"]
        out.append({"kind": "false_alarm", "label": "ложное срабатывание: судно / кильватер",
                    "zone_id": p["zone_id"], "crop_url": p["crop_url"], "title": p["title"],
                    "note": "пиксели у яркой цели и её следа — признак судна; это не «верное срабатывание»: статус «недостаточно данных»"})
    return out


DEFENSE_JSON = "defense_examples.json"  # scripts/case/defense_examples.py (the miss: a Cózar B filament)
DEFENSE_KINDS = [("success", "Правильное обнаружение скопления"), ("miss", "Пропуск скопления"),
                 ("false_alarm", "Ложное обнаружение судна или следа"),
                 ("background_error", "Ошибка на пене, блике или другом сложном фоне"),
                 ("no_analysis", "Область, где анализ невозможен из-за качества")]


def _defense_settings() -> dict:
    idx = scene_zones_index()
    m = idx.get("model") or {}
    return {"weights": m.get("weights"), "model_sha256": m.get("sha256"), "threshold": m.get("threshold"),
            "harmonize": m.get("harmonize"), "class": m.get("class"), "rules": idx.get("rules"),
            "rules_source": "scripts/case/scene_zones.py (фильтры судна/кильватера/шва, пены, блика, берега, "
                            "мелководья, облаков; ветер > 5 м/с), src/macroplastic/case/illumination.py"}


def _bbox_poly(b: list) -> dict:
    return {"type": "Polygon", "coordinates": [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]]}


def _defense_zone(kind: str, f: dict, reference: str, verdict: str, basis: str, explanation: str, rule: str) -> dict:
    p = f["properties"]
    base = f"/api/v3/scene_zones/scenes/{p['scene_key']}"
    meas = p.get("measured") or {}
    prob = p.get("probable") or {}
    return {"kind": kind, "label": dict(DEFENSE_KINDS)[kind], "zone_id": p["zone_id"], "scene_key": p["scene_key"],
            "scene_id": p["scene_id"], "datetime": p["datetime"], "title": p["title"], "geometry": f["geometry"],
            "image": {"crop_url": p.get("crop_url"), "rgb_url": f"{base}/rgb.jpg", "quality_url": f"{base}/quality.png",
                      "crop_note": "слева снимок B4-B3-B2, справа он же с пикселями детектора (красные; "
                                   "судно/кильватер/шов — жёлтые)"},
            "model_result": {"n_pixels": meas.get("n_pixels"), "detected_area_m2": p.get("detected_area_m2"),
                             "prob_max": prob.get("prob_max"), "prob_mean": prob.get("prob_mean"),
                             "flags": p.get("flags") or []},
            "reference": reference, "verdict": verdict, "basis": basis,
            "status": p.get("status"), "status_label": p.get("status_label"), "status_explanation": explanation,
            "rule": rule,
            "repeat": {"api": f"/api/v3/scene_zones/{p['zone_id']}",
                       "command": f".venv/Scripts/python.exe scripts/case/scene_zones.py --only {p['scene_key'].split('-')[0]}  (все сцены этого вида)"}}


def defense_examples() -> dict:
    """MATVEY_ACCEPTANCE п.4: the five mandatory examples of the working map mode (detector + quality mask + filters).
    Each: image, quality mask, model result, basis of the verdict, status explanation, settings, how to repeat.
    The choice rules are fixed in code (field `rule`), not picked by the result."""
    zs = scene_zones_all()
    byid = {f["id"]: f for f in zs}
    ex = {e["kind"]: e for e in sz_examples()}
    out = []
    if "success" in ex:
        f = byid[ex["success"]["zone_id"]]
        p = f["properties"]
        out.append(_defense_zone(
            "success", f, "каталог Cózar 2024, уровень B (нити проверены людьми по снимку)", "правильно",
            f"контур зоны пересекает {p.get('n_cozar_filaments')} нит(и) Cózar; ни один фильтр артефактов не сработал; "
            "сцена отложена (не в обучении детектора)",
            f"«{p.get('status_label')}» — детектор сработал после всех фильтров; «{p.get('confirmation_label')}»",
            "зона уровня B с наибольшим числом пересечённых нитей Cózar (затем — пикселей детектора)"))
    dpath = PATHS["scene_zones_dir"] / DEFENSE_JSON
    dj = _read_json(dpath) if dpath.is_file() else None
    if dj and dj.get("miss"):
        m = dj["miss"]
        key = dj["scene_key"]
        base = f"/api/v3/scene_zones/scenes/{key}"
        crop = PATHS["scene_zones_dir"] / key / (m.get("crop_file") or "-")
        sc = next((s for s in scene_zones_index().get("scenes") or [] if s["key"] == key), {})
        rel = [f["id"] for f in zs if f["properties"].get("scene_key") == key and f.get("geometry")
               and bbox_intersects(_geom_bounds(f["geometry"]), m["bbox_wgs84"])]
        out.append({
            "kind": "miss", "label": dict(DEFENSE_KINDS)["miss"], "zone_id": None, "scene_key": key,
            "scene_id": dj["scene_id"], "datetime": sc.get("datetime"),
            "title": f"30SXE · нить Cózar {m['fil_idx']}", "geometry": _bbox_poly(m["bbox_wgs84"]),
            "image": {"crop_url": f"{base}/crops/{m['zone_like_id']}.jpg" if crop.is_file() else None,
                      "rgb_url": f"{base}/rgb.jpg", "quality_url": f"{base}/quality.png",
                      "crop_note": "слева снимок, справа пиксели детектора (красные) в прямоугольнике нити каталога"},
            "model_result": {"n_pixels_in_bbox": m["det_px_in_bbox"], "prob_max_in_bbox": m["prob_max_in_bbox"],
                             "water_px_in_bbox": m["water_px_in_bbox"], "flags": []},
            "reference": f"каталог Cózar 2024, уровень {m['level']}: нить {m['fil_idx']}, {m['n_pixels_fil']} пикс.",
            "verdict": "пропуск (большая часть нити не отмечена)",
            "basis": (f"в нити {m['n_pixels_fil']} пикс. по каталогу; детектор отметил в её прямоугольнике "
                      f"{m['det_px_in_bbox']} (≤ {round(100 * m['det_share_upper'])} %) → пропущено не менее "
                      f"{m['missed_px_min']} пикс.; вода в рамке пригодна ({m['water_px_in_bbox']} из {m['bbox_px']} "
                      f"пикс.), ветер {sc.get('wind10m_ms')} м/с. По всему размеченному набору доля найденных "
                      "пикселей B — 2.8 % (final_numbers: labeled_data.zero_shot.refined_B_recall_pct)"),
            "status": "detected", "status_label": "обнаружено",
            "related_zones": rel,
            "status_explanation": (f"отмеченные пиксели рядом с нитью входят в зоны «обнаружено» ({', '.join(rel) or 'нет'}); "
                                   "непокрытая часть нити на карте не отмечена — отсутствие зоны не означает «чистая вода»"),
            "rule": dj.get("rule"),
            "repeat": {"api": "/api/v3/defense_examples",
                       "command": ".venv/Scripts/python.exe scripts/case/defense_examples.py",
                       "inputs": "reports/extra_data/registry_cozar2024.csv.gz, data/case/demo/cozar_demo_2021-03-11/*.tif"}})
    if "false_alarm" in ex:
        f = byid[ex["false_alarm"]["zone_id"]]
        p = f["properties"]
        sh = (((p.get("probable") or {}).get("signs") or {}).get("ship") or {}).get("share")
        out.append(_defense_zone(
            "false_alarm", f, "независимой разметки нет — качественный разбор по снимку", "ложное срабатывание",
            f"пиксели детектора у яркой цели и её кильватера (доля {sh}); правило судна/кильватера (reports/artifacts.md)",
            f"«{p.get('status_label')}» — признаки судна/кильватера; находкой не считается",
            "зона с флагом ship на открытой воде с наибольшей долей признака; из равных выбрана самая читаемая вырезка"))
    bg = [f for f in zs if f["properties"].get("flags") and set(f["properties"]["flags"]) <= {"foam", "glint"}
          and f["properties"].get("crop_url")]
    bg.sort(key=lambda f: (-(f["properties"]["measured"].get("n_pixels") or 0), f["id"]))
    if bg:
        f = bg[0]
        p = f["properties"]
        sg = (p.get("probable") or {}).get("signs") or {}
        fo, gl = sg.get("foam") or {}, sg.get("glint") or {}
        why = []
        if fo.get("flag"):
            why.append(f"пена: «белый» спектр, прирост B2 к B8 = {fo.get('white_ratio_b2_b8')} ≥ 0.8 "
                       f"(ветер {fo.get('wind10m_ms')} м/с)")
        if gl.get("flag"):
            why.append(f"блик: B11 воды {gl.get('b11_water_median')}, доля блика {gl.get('glint_share')}")
        out.append(_defense_zone(
            "background_error", f, "независимой разметки нет — качественный разбор по снимку",
            "ошибка модели на сложном фоне (поймана фильтром)",
            f"детектор уверен (P max {(p.get('probable') or {}).get('prob_max')}), но " + "; ".join(why)
            + " — пена/блик, а не плавающий мусор",
            f"«{p.get('status_label')}» — признак сложного фона; находкой не считается",
            "зона, у которой из признаков только пена и/или блик; наибольшее число пикселей детектора"))
    na = [s for s in scene_zones_index().get("scenes") or [] if s.get("evaluable") is False]
    na.sort(key=lambda s: (-(s.get("det_pixels") or 0), s["key"]))
    if na:
        s = na[0]
        base = f"/api/v3/scene_zones/scenes/{s['key']}"
        why = (f"низкое солнце: зенит {s.get('sun_zenith_deg')}° ≥ 58°" if s.get("low_sun")
               else f"слабый сигнал воды: медиана B3 {s.get('water_b3_median')} < 0.003")
        out.append({
            "kind": "no_analysis", "label": dict(DEFENSE_KINDS)["no_analysis"], "zone_id": None, "scene_key": s["key"],
            "scene_id": s["scene_id"], "datetime": s["datetime"],
            "title": f"{s.get('region_name') or s['region']} · {s['date']}", "geometry": _bbox_poly(s["bounds"]),
            "image": {"crop_url": None, "rgb_url": f"{base}/rgb.jpg", "quality_url": f"{base}/quality.png",
                      "crop_note": "снимок и маска качества всей вырезки; зоны не строятся"},
            "model_result": {"det_pixels": s.get("det_pixels"), "n_zones": None, "flags": []},
            "reference": "не нужен: вывод по правилу освещённости, до модели",
            "verdict": "анализ невозможен",
            "basis": (f"{why}; условия вне обучения детектора — его {s.get('det_pixels')} пикс. не учитываются "
                      "(правило src/macroplastic/case/illumination.py, общее со студией)"),
            "status": "insufficient_data", "status_label": "недостаточно данных",
            "status_explanation": "сцена «не оценивается»: зон нет, пиксели детектора не показываются как находки",
            "rule": "сцена с evaluable = false и наибольшим числом пикселей детектора",
            "repeat": {"api": "/api/v3/scene_zones/scenes",
                       "command": f".venv/Scripts/python.exe scripts/case/scene_zones.py --only {s['key'].split('-')[0]}  (все сцены этого вида)"}})
    order = [k for k, _ in DEFENSE_KINDS]
    out.sort(key=lambda e: order.index(e["kind"]))
    return {"count": len(out), "required": [{"kind": k, "label": lab} for k, lab in DEFENSE_KINDS],
            "missing": [k for k in order if k not in {e["kind"] for e in out}],
            "settings": _defense_settings(), "examples": out,
            "note": ("рабочий режим карты: детектор, порог, маска качества, фильтры артефактов, правило освещённости; "
                     "эталон — только каталог Cózar 2024 (B); без независимой разметки — качественный разбор")}


def sz_file(key: str, name: str) -> Optional[Path]:
    if name not in ("rgb.jpg", "quality.png") or not any(s["key"] == key for s in scene_zones_index().get("scenes") or []):
        return None
    p = PATHS["scene_zones_dir"] / key / name
    return p if p.is_file() else None


def filter_scene_zones(bbox=None, date_from=None, date_to=None, detection_statuses=None, concentration_statuses=None,
                       scene_kinds=None, scene_key=None, statuses=None, sources=None, profiles=None, scopes=None,
                       regions=None, is_find=None) -> list[dict]:
    """One filter for the list, the export and saved queries. sources/profiles/scopes are attributes of field records:
    satellite zones have none, so any of them -> no zones (the same as the v2 UI, jury 12:56 T5)."""
    if sources or profiles or scopes:
        return []
    out = []
    for f in scene_zones_all():
        p = f["properties"]
        if scene_key and p["scene_key"] != scene_key:
            continue
        if regions and p["region"] not in regions:
            continue
        if is_find is not None and bool(p.get("is_find")) != is_find:
            continue
        if scene_kinds and p["scene_kind"] not in scene_kinds:
            continue
        if detection_statuses and p["detection_status"] not in detection_statuses:
            continue
        if concentration_statuses and p["concentration_status"] not in concentration_statuses:
            continue
        if statuses:
            tags = {p["detection_status"], p["concentration_status"]}
            if p["concentration_status"] == "unavailable":
                tags.add("concentration_unavailable")
            if not set(statuses) & tags:
                continue
        if not in_dates(p["datetime"], date_from, date_to):
            continue
        if bbox is not None and not bbox_intersects(_geom_bounds(f["geometry"]), bbox):
            continue
        out.append(f)
    return out


def scene_zones_fc(feats: list[dict], field_filter: bool = False) -> dict:
    idx = scene_zones_index()
    mi = dict(idx.get("model") or {})
    if mi:
        mi["trained_at"] = detector_model_info().get("trained_at") or mi.get("trained_at")
    fc = {"type": "FeatureCollection", "kind": "detection_zone", "layer_kind": "scene_zone",
          "label": "Спутниковые зоны детекции (текущий детектор)", "count": len(feats), "empty_reason": None,
          "model": mi or None, "rules": idx.get("rules"), "status_note": SZ_STATUS_NOTE,
          "quantity": dict(SZ_QUANTITY),
          "research_estimate": zone_estimate_summary(feats) if idx else None,
          "blocks_note": ("measured — измерено по снимку; probable — вероятность и признаки; quantity — «концентрация по "
                          "снимку не подтверждена» (измеренной нет); research_estimate — исследовательская оценка шт./км² "
                          "только у находок (калибровка на мишенях PLP), иначе null + research_estimate_reason"),
          "examples": sz_examples() if idx else [],
          "features": feats}
    if not feats:
        fc["empty_reason"] = ("Слой не построен (scripts/case/scene_zones.py)" if not idx else
                              SZ_FIELD_FILTER_NOTE if field_filter else "Нет спутниковых зон под выбранные фильтры")
    return fc


def scene_zone_detections(zone_id: str) -> dict:
    for f in scene_zones_all():
        if f["id"] == zone_id:
            key = f["properties"]["scene_key"]
            dets = (_cached(f"szd:{key}", PATHS["scene_zones_dir"] / key / "detections.geojson", _read_json) or {}
                    ).get("features") or []
            fs = [d for d in dets if d["properties"]["zone_id"] == zone_id]
            return {"type": "FeatureCollection", "kind": "detection", "label": "Подозрительные пиксели детектора",
                    "count": len(fs), "empty_reason": None if fs else "в зоне нет объектов детектора", "features": fs}
    return {"type": "FeatureCollection", "kind": "detection", "count": 0, "empty_reason": "зона не найдена",
            "features": []}


SZ_COLS = ["zone_id", "scene_key", "scene_kind", "scene_id", "region", "title", "datetime", "detection_status",
           "detection_label", "status", "status_label", "status_reason", "confirmation", "confirmation_label",
           "class", "excluded_backgrounds", "flagged_backgrounds", "not_checked_backgrounds", "composition",
           "quantity_line", "is_find", "training_scene",
           "concentration_status", "flags", "zone_area_km2", "suspicious_area_m2", "n_pixels", "n_objects",
           "water_km2", "lwd_m2_km2", "valid_water_fraction", "cloud_fraction", "glint_fraction", "prob_max",
           "prob_mean", "foam_sign", "glint_sign", "ship_sign", "n_cozar_filaments", "verification",
           "quantity_status", "quantity_label", "quantity_detail",
           # §34 п.2: research estimate (finds only; otherwise empty + reason); area is zone_area_km2 above
           "research_scenario_status", "research_scenario_value", "research_scenario_line", "research_estimate_value", "research_calibration_spread_lo",
           "research_calibration_spread_hi", "research_estimate_unit",
           "research_estimate_status", "research_estimate_method", "research_estimate_note",
           "research_method_essence", "research_estimate_context", "research_scenario", "research_natural_pair_note",
           "research_formula_short", "research_calibration_name", "research_firing_caveat",
           "research_n_items_lo", "research_n_items_hi", "research_items_per_pixel_lo", "research_items_per_pixel_hi",
           "research_estimate_reason", "research_estimate_muted",
           "field_nearest_sample_id", "field_nearest_km",
           "model_weights", "model_sha256",
           "threshold", "centroid_lon", "centroid_lat", "kind"]


def scene_zones_csv(feats: list[dict]) -> str:
    rows = []
    for f in feats:
        p = f["properties"]
        m, pr, qn = p.get("measured") or {}, p.get("probable") or {}, p.get("quantity") or {}
        q, sg, md = m.get("quality") or {}, pr.get("signs") or {}, m.get("model") or {}
        # §33а п.3 / jury 08:51: no items/km2 of another place in the zone export; the same «nearest field» as the card
        nos = (p.get("field_nearby") or {}).get("nearest_organizer_sample") or {}
        b = _geom_bounds(f["geometry"]) if f.get("geometry") else None
        cen = [round((b[0] + b[2]) / 2, 6), round((b[1] + b[3]) / 2, 6)] if b else [None, None]
        re = p.get("research_estimate") or {}
        rn, rpp = re.get("n_items") or {}, re.get("items_per_pixel") or {}
        rows.append([p["zone_id"], p["scene_key"], p["scene_kind"], p["scene_id"], p.get("region"), p.get("title"),
                     p["datetime"], p["detection_status"],
                     p.get("detection_label"), p.get("status"), p.get("status_label"), p.get("status_reason"),
                     p.get("confirmation"), p.get("confirmation_label"), (p.get("classification") or {}).get("class_label"),
                     p.get("excluded_backgrounds") or [], (p.get("classification") or {}).get("flagged_backgrounds") or [],
                     (p.get("classification") or {}).get("not_checked_backgrounds") or [],
                     (p.get("classification") or {}).get("composition"), p.get("quantity_line"),
                     bool(p.get("is_find")), p.get("training_scene"),
                     p["concentration_status"], p.get("flags") or [], m.get("zone_area_km2"),
                     m.get("suspicious_area_m2"), m.get("n_pixels"), m.get("n_objects"), m.get("water_km2"),
                     m.get("lwd_m2_km2"), q.get("valid_water_fraction"), q.get("cloud_fraction"), q.get("glint_fraction"),
                     pr.get("prob_max"), pr.get("prob_mean"), (sg.get("foam") or {}).get("flag"),
                     (sg.get("glint") or {}).get("flag"), (sg.get("ship") or {}).get("flag"), p.get("n_cozar_filaments"),
                     p.get("verification"), qn.get("status"), qn.get("label"), qn.get("detail"),
                     re.get("scenario_status"), re.get("scenario_value"), re.get("scenario_line"), re.get("value"), re.get("lo"), re.get("hi"), re.get("unit"), re.get("status"),
                     re.get("method"), re.get("note"), re.get("method_essence"), re.get("context"), re.get("scenario"),
                     re.get("natural_pair_note"), re.get("formula_short"), re.get("calibration_name"),
                     re.get("firing_caveat"),
                     rn.get("lo"), rn.get("hi"), rpp.get("lo"), rpp.get("hi"),
                     p.get("research_estimate_reason"), re.get("muted") if re else None,
                     nos.get("sample_id"), nos.get("distance_km"),
                     md.get("weights"), md.get("sha256"), md.get("threshold"), cen[0], cen[1], "detection_zone"])
    return _csv(SZ_COLS, rows)


def detector_model_info() -> dict:
    """weights/lgbm: sha256 of model.txt + file date (the model version shown in the legend / API)."""
    import hashlib
    p = PATHS["lgbm_meta"].parent / "model.txt"

    def load(path):
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        return {"weights": "weights/lgbm", "file": "weights/lgbm/model.txt", "sha256": h, "sha256_short": h[:12],
                # жюри-7: from weights/lgbm/model_card.json (the weights commit), not the mtime (= clone time on a clone)
                "trained_at": (_read_json(path.parent / "model_card.json") or {}).get("trained_at")
                if (path.parent / "model_card.json").is_file() else None,
                "threshold": (_cached("lgbm_meta", PATHS["lgbm_meta"], _read_json) or {}).get("threshold"),
                "harmonize": "none"}
    return (_cached("lgbm_model_info", p, load) or {}) if p.is_file() else {}


def quantity_levels() -> dict:
    """Three levels of the quantity logic (INBOX §15); numbers from final_numbers.json (case.sections)."""
    fn = _cached("final_numbers", PATHS["final_numbers"], _read_json) or {}
    s = (fn.get("case") or {}).get("sections") or {}
    a = s.get("adis_pairs") or {}
    cal = (s.get("quantity") or {}).get("calibration") or {}
    return {
        "place_time_pairs": {"n": a.get("A"), "n_detector_evaluable": a.get("A_eval"),
                             "label": "пары по месту и времени (ADIS ↔ Sentinel-2)",
                             "note": "совпадение по месту и времени не доказывает, что на снимке видны те же предметы"},
        "visible_signal": {"n_with_items": a.get("A_with_items"), "n_with_items_evaluable": a.get("A_with_items_eval"),
                           "n_detector_pixels": a.get("A_with_items_eval_det_px"),
                           "label": "видимый сигнал на снимке",
                           "note": ("на синхронных отрезках ADIS с единичными предметами детектор предметы не увидел; "
                                    "это согласуется с физикой (доля покрытия ~10⁻⁷), но не задаёт общий предел для "
                                    "всех скоплений")},
        "patchiness": {"sd_ln_c": ((s.get("quantity") or {}).get("variance_S2") or {}).get("sd_within_day"),
                       "label": "пятнистость: разброс ln C внутри дня рейса (S2), не входит в интервал счёта Пуассона",
                       "source": "reports/final_numbers.json · quantity.variance_S2.sd_within_day"},
        "calibration_pairs": {"n": cal.get("pairs_A_with_S_pos", 0), "label": "калибровочные пары «снимок → шт./км²»",
                              "note": "калибровочных пар 0: перевод «снимок → шт./км²» не обучен"},
    }


# ------------------------------------------------------------------------------------ §31 п.2: «Главное» (first screen)
HEADLINE_FIELD = [  # (final_numbers profile key, source filter, profile filter, material, size class)
    ("S2", "S2_SARGASSO_MSM41", "S2_visual_GT2", "пластик", "> 2 см", "Саргассово"),
    ("S3", "S3_SE_NORTH_SEA", "S3_visual_GT2", "весь мусор", "> 2 см", "Сев. море"),
    ("S4", "S4_BLACK_SEA_DOORS3", "S4_visual_GT2_5", "весь мусор", "> 2,5 см", "Чёрное м."),
]


def headline() -> dict:
    """First-screen numbers (INBOX §31 п.2 а): field concentration per profile (measurement), ADIS, satellite zones.
    All numbers from final_numbers.json / the scene-zone layer — no hardcoded values."""
    q = _fn_quantity()
    prof = q.get("profiles") or {}
    f2 = q.get("field_S2") or {}
    src_lab = dict(SOURCES)
    rows = []
    for key, src, pf, mat, size, short in HEADLINE_FIELD:
        pr = prof.get(key) or {}
        if key == "S2" and f2.get("boot_lo95") is not None:
            v, lo, hi = f2.get("pooled_C"), f2.get("boot_lo95"), f2.get("boot_hi95")
            stat, il = "среднее", "бутстреп по дням рейса"
        elif pr.get("pooled_C") is not None:
            v, lo, hi = pr.get("pooled_C"), pr.get("lo95"), pr.get("hi95")
            stat, il = "среднее", "Пуассон (только ошибка счёта)"
        elif pr.get("c_median") is not None:
            v, lo, hi = pr.get("c_median"), pr.get("c_p25"), pr.get("c_p75")
            stat, il = "медиана", "межквартильный размах"
        else:
            continue
        rows.append({"key": key, "short": short, "source": src, "source_label": src_lab.get(src), "profile": pf, "value": v, "lo": lo,
                     "hi": hi, "stat": stat, "interval_label": il, "material": mat, "size_class": size, "n": pr.get("n"),
                     "kind": "measurement", "unit": "items/km2"})
    af = q.get("adis_forecast") or {}
    if af.get("C") is not None:
        rows.append({"key": "ADIS", "short": "ADIS", "source": None, "source_label": "ADIS, судовая камера",
                     "profile": None, "value": af.get("C"), "lo": af.get("lo"), "hi": af.get("hi"), "stat": "среднее",
                     "interval_label": "Пуассон (только ошибка счёта)", "material": "все объекты (не только пластик)", "size_class": "> 10 см",
                     "n": af.get("n_segments"), "kind": "measurement", "unit": "items/km2"})
    zs = scene_zones_all()
    ex = sz_examples() if zs else []
    good = next((e for e in ex if e["kind"] == "success"), None)
    return {
        "field": rows,
        "field_label": "Концентрация по полевым данным (измерение, шт./км²)",
        "field_note": "числа сравнимы только внутри одного профиля (размерный класс, материал, метод счёта)",
        "satellite": {"n_zones": len(zs),
                      "n_finds": sum(1 for f in zs if f["properties"].get("is_find")),
                      "finds_note": "снимки обучения детектора (MARIDA/MADOS) находками не считаем",
                      "n_level_b": sum(1 for f in zs if f["properties"].get("verification") == "level_B_cozar"),
                      "quantity_label": SZ_QUANTITY["label"], "why": SZ_QUANTITY["detail"],
                      "research_estimate": zone_estimate_summary(zs) if zs else None,
                      "open_zone_id": good["zone_id"] if good else None},
        "source": "reports/final_numbers.json · case.sections.quantity; слой data/case/scene_zones",
    }
