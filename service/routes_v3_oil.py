"""API v3 «Нефтяное пятно» (INBOX §14, L101) — docs/CONTRACTS_V3.md, раздел 3.9. ЭКСПЕРИМЕНТАЛЬНЫЙ слой.

GET /api/v3/oil/meta                                   класс, цвет, единица, метрики (val/test MADOS), ограничения
GET /api/v3/oil/scenes?bbox=&date_from=&date_to=&region=&kind=&scene_id=&limit=&offset=
                                                       сцены, прогнанные головой «нефть»: площадь км², доля воды
GET /api/v3/oil/spills?bbox=&date_from=&date_to=&region=&kind=&scene_id=&min_area_km2=&limit=&offset=
                                                       GeoJSON полигонов пятен (EPSG:4326), class="oil_spill"
GET /api/v3/oil/export?format=geojson|csv&<те же фильтры, что spills>
                                                       выгрузка отдельного класса (Content-Disposition: attachment)
Данные — только с диска: data/case/oil/index.json + data/case/oil/<scene_key>.geojson
(scripts/oil/train_oil.py infer). Нет файлов -> HTTP 200 + пустая коллекция + empty_reason.
Единица — ПЛОЩАДЬ пятна (км²) и доля пикселей наблюдаемой воды сцены; не объём и не масса; штуки не применяются.
Ошибки — форма контракта {"error": {...}}; неизвестные параметры -> 400 BAD_PARAM; charset=utf-8; CORS *.
Маршруты вставляются в router routes_v3 перед его catch-all (как routes_v3_studio); routes_v3.py/app.py не меняются.
"""
from __future__ import annotations

import csv
import functools
import io
import json
import os
import threading
import traceback
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response

from . import case_store as cs
from . import routes_v3 as v3
from .case_store import ApiError

REPO = Path(__file__).resolve().parents[1]
ROOTS: dict[str, Path] = {  # tests monkeypatch this
    "oil": Path(os.environ.get("MACROPLASTIC_OIL_DIR") or (REPO / "data" / "case" / "oil")),
}
CLASS_ID = "oil_spill"
CLASS_LABEL = "Нефтяное пятно"
UNIT_NOTE = "площадь, не объём/масса"
COLOR = {"fill": "#c026d3", "fill_opacity": 0.45, "line": "#f0abfc", "line_width": 1.5,
         "note": "фуксия — не пересекается с тёплой палитрой мусора (красный/оранжевый/жёлтый) и голубым дрейфом"}
KINDS = ["live", "pair"]
LIMITATIONS = [
    "Оптика (Sentinel-2) видит только часть пятен: тонкие плёнки и пятна вне солнечного блика почти не отличаются от воды.",
    "Путаница: тени облаков, слики водорослей/поверхностно-активных веществ, мутные шлейфы, ветровая тень.",
    "Для нефти обычно используют радар Sentinel-1 (SAR) — вне рамок проекта.",
    "Обучено на MADOS (ACOLITE, из L1C); сцены сервиса — Sen2Cor L2A: сдвиг домена, на них метрик нет.",
    "Площадь пятна — не объём и не масса; штуки к нефти не применяются.",
]

router = APIRouter(prefix="/api/v3", tags=["v3-oil"])
_oil = APIRouter(prefix="/api/v3", tags=["v3-oil"])

_FILTER_P = {"bbox", "date_from", "date_to", "region", "kind", "scene_id", "limit", "offset"}
PARAMS = {
    "oil_meta": set(),
    "oil_scenes": set(_FILTER_P),
    "oil_spills": _FILTER_P | {"min_area_km2"},
    "oil_export": _FILTER_P | {"min_area_km2", "format"},
}

_lock = threading.Lock()
_cache: dict = {}


def _api(fn):
    allowed = PARAMS[fn.__name__]

    @functools.wraps(fn)
    def wrapper(request: Request, *a, **kw):
        try:
            v3.check_params(dict(request.query_params.items()), allowed, request.url.path)
            return fn(request, *a, **kw)
        except ApiError as e:
            return v3._err(e)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return v3._err(ApiError(500, "INTERNAL", "Внутренняя ошибка сервера", {"type": type(e).__name__}))
    return wrapper


# ------------------------------------------------------------------ data
def _read_json(p: Path):
    key = str(p)
    try:
        st = p.stat()
    except FileNotFoundError:
        return None
    sig = (st.st_mtime_ns, st.st_size)
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == sig:
            return hit[1]
    obj = json.loads(p.read_text(encoding="utf-8"))
    with _lock:
        _cache[key] = (sig, obj)
    return obj


def _index() -> dict | None:
    return _read_json(ROOTS["oil"] / "index.json")


def _scene_features(key: str) -> list[dict]:
    fc = _read_json(ROOTS["oil"] / f"{key}.geojson")
    return (fc or {}).get("features", [])


def _bbox_of(geom) -> list[float]:
    xs, ys = [], []

    def walk(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        else:
            for x in c:
                walk(x)
    walk(geom.get("coordinates", []))
    return [min(xs), min(ys), max(xs), max(ys)] if xs else [0, 0, 0, 0]


def _inter(a, b) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _filters(q: dict) -> dict:
    a, b = cs.parse_dates(q.get("date_from"), q.get("date_to"))
    return {"bbox": cs.parse_bbox(q.get("bbox")), "date_from": a, "date_to": b,
            "regions": cs.parse_list(q.get("region"), "region"),
            "kinds": cs.parse_list(q.get("kind"), "kind", KINDS),
            "scene_ids": cs.parse_list(q.get("scene_id"), "scene_id"),
            "min_area_km2": cs.parse_float(q.get("min_area_km2"), "min_area_km2", 0, 1e9)}


def _scene_ok(s: dict, f: dict) -> bool:
    if f["date_from"] and (s.get("date") or "") < f["date_from"]:
        return False
    if f["date_to"] and (s.get("date") or "9999") > f["date_to"]:
        return False
    if f["regions"] and s.get("region") not in f["regions"]:
        return False
    if f["kinds"] and s.get("kind") not in f["kinds"]:
        return False
    if f["scene_ids"] and s.get("scene_id") not in f["scene_ids"] and s.get("scene_key") not in f["scene_ids"]:
        return False
    if f["bbox"] and s.get("bbox") and not _inter(s["bbox"], f["bbox"]):
        return False
    return True


def _scene_row(s: dict, idx: dict) -> dict:
    return {"scene_key": s["scene_key"], "scene_id": s.get("scene_id"), "date": s.get("date"),
            "region": s.get("region"), "kind": s.get("kind"), "source": s.get("source"),
            "class": CLASS_ID, "experimental": bool(idx.get("experimental", True)),
            "status": s.get("status", "ok"), "status_reason": s.get("status_reason"),
            "oil_km2": s.get("oil_km2"), "oil_frac_water": s.get("oil_frac_water"),  # null = нет оценки (не 0)
            "n_spills": s.get("n_spills"), "water_km2": s.get("water_km2"), "valid_water_frac": s.get("valid_water_frac"),
            "bbox": s.get("bbox"), "unit_note": UNIT_NOTE}


SPILLS_LIMIT_DEFAULT = 1000  # all spills of all scenes = ~13 000 polygons / ~29 MB: ask per scene or bbox


def _collect(q: dict, default_limit: int = SPILLS_LIMIT_DEFAULT):
    f = _filters(q)
    limit = cs.parse_int(q.get("limit"), "limit", 1, 100000, default_limit)
    offset = cs.parse_int(q.get("offset"), "offset", 0, 10 ** 7, 0)
    idx = _index()
    if not idx:
        return f, [], 0, "нет данных слоя «нефть»: не запускался scripts/oil/train_oil.py infer (data/case/oil/index.json)"
    feats = []
    for s in sorted(idx.get("scenes", []), key=lambda s: s["scene_key"]):
        if not _scene_ok(s, f):
            continue
        for ft in _scene_features(s["scene_key"]):
            p = ft.get("properties", {})
            if f["min_area_km2"] is not None and (p.get("area_km2") or 0) < f["min_area_km2"]:
                continue
            if f["bbox"] and not _inter(_bbox_of(ft.get("geometry") or {}), f["bbox"]):
                continue
            feats.append(ft)
    feats.sort(key=lambda ft: (-(ft["properties"].get("area_km2") or 0), ft["id"]))  # largest first, deterministic
    total = len(feats)
    empty = None
    if total == 0:
        empty = ("по фильтрам нет сцен" if not any(_scene_ok(s, f) for s in idx.get("scenes", []))
                 else "в выбранных сценах голова «нефть» не отметила пятен (порог val MADOS)")
    return f, feats[offset:offset + limit], total, empty


# ------------------------------------------------------------------ endpoints
@_oil.get("/oil/meta", summary="Слой «Нефтяное пятно»: описание, цвет, метрики")
@_api
def oil_meta(request: Request):
    idx = _index() or {}
    return v3._ok({
        "class": CLASS_ID, "class_label": CLASS_LABEL, "experimental": bool(idx.get("experimental", True)),
        "unit": "km²", "unit_note": UNIT_NOTE, "counts_applicable": False, "color": COLOR,
        "model": idx.get("model"), "selected_run": idx.get("selected_run"), "threshold": idx.get("threshold"),
        "min_px": idx.get("min_px"), "harmonize": idx.get("harmonize"),
        "gates": {"min_valid_water_frac": idx.get("min_valid_water_frac"), "max_cloud_frac": idx.get("max_cloud_frac"),
                  "max_scene_frac": idx.get("max_scene_frac"),
                  "note": "маска качества (вода без облаков/теней/блика/суши и буферов) применяется ДО поиска пятен; "
                          "сцена вне ворот — «нет оценки» (null), не 0"},
        "metrics": {"dataset": "MADOS (класс 6 Oil Spill), сплит по сценам — configs/oil_eval.yaml",
                    "val": idx.get("metrics_val"), "val_ci95": idx.get("metrics_val_ci95"),
                    "test": idx.get("metrics_test"), "test_ci95": idx.get("metrics_test_ci95"),
                    "baseline_osi": idx.get("baseline_osi")},
        "n_scenes": len(idx.get("scenes", [])), "generated_at": idx.get("generated_at"),
        "domain_note": idx.get("domain_note"), "limitations": LIMITATIONS, "doc": "docs/OIL.md",
        "empty_reason": None if idx else "нет data/case/oil/index.json",
    })


@_oil.get("/oil/scenes", summary="Сцены слоя «нефть» (площадь км², доля воды)")
@_api
def oil_scenes(request: Request):
    q = dict(request.query_params.items())
    f = _filters(q)
    limit = cs.parse_int(q.get("limit"), "limit", 1, 100000, 100000)
    offset = cs.parse_int(q.get("offset"), "offset", 0, 10 ** 7, 0)
    idx = _index()
    rows = [] if not idx else [_scene_row(s, idx) for s in sorted(idx.get("scenes", []), key=lambda s: s["scene_key"])
                               if _scene_ok(s, f)]
    return v3._ok({"items": rows[offset:offset + limit], "total": len(rows), "limit": limit, "offset": offset,
                   "class": CLASS_ID, "unit_note": UNIT_NOTE,
                   "empty_reason": None if rows else ("нет data/case/oil/index.json" if not idx else "по фильтрам нет сцен")})


def _fc(feats, total, f, empty, idx):
    return {"type": "FeatureCollection", "features": feats, "total": total, "count": len(feats), "class": CLASS_ID,
            "class_label": CLASS_LABEL, "experimental": bool((idx or {}).get("experimental", True)),
            "total_area_km2": round(sum(ft["properties"].get("area_km2") or 0 for ft in feats), 6),
            "unit_note": UNIT_NOTE, "color": COLOR, "empty_reason": empty}


@_oil.get("/oil/spills", summary="GeoJSON пятен нефти (экспериментальный слой)")
@_api
def oil_spills(request: Request):
    q = dict(request.query_params.items())
    f, feats, total, empty = _collect(q)
    body = json.dumps(_fc(feats, total, f, empty, _index()), ensure_ascii=False)
    return Response(body, media_type=v3.GEOJSON_UTF8, headers=v3.CORS)


@_oil.get("/oil/export", summary="Экспорт класса «нефть»: GeoJSON или CSV")
@_api
def oil_export(request: Request):
    q = dict(request.query_params.items())
    fmt = cs.parse_choice(q.pop("format", None), "format", ["geojson", "csv"], "geojson")
    f, feats, total, empty = _collect(q, default_limit=100000)  # export: everything that matches
    hdr = {**v3.CORS, "Content-Disposition": f'attachment; filename="oil_spill.{fmt}"'}
    if fmt == "csv":
        return Response(_csv(feats), media_type=v3.CSV_UTF8, headers=hdr)
    return Response(json.dumps(_fc(feats, total, f, empty, _index()), ensure_ascii=False),
                    media_type=v3.GEOJSON_UTF8, headers=hdr)


def _csv(feats) -> str:
    fields = ["id", "class", "class_label", "scene_id", "scene_key", "date", "region", "source", "experimental",
              "area_km2", "n_px", "scene_frac", "prob_mean", "lon", "lat", "model", "unit_note"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for ft in feats:
        w.writerow({k: ft["properties"].get(k) for k in fields})
    return buf.getvalue()


# ------------------------------------------------------------------ mount before the v3 catch-all
def _splice() -> None:
    vr = v3.router
    if getattr(vr, "_oil_spliced", False):
        return
    n0 = len(vr.routes)
    pre = vr.prefix or ""
    for r in _oil.routes:
        vr.add_api_route(r.path[len(pre):], r.endpoint, methods=sorted(r.methods), summary=r.summary,
                         tags=["v3-oil"], name=r.name)
    new = vr.routes[n0:]
    del vr.routes[n0:]
    idx = next((i for i, r in enumerate(vr.routes) if getattr(r, "path", "") in ("/api/v3/{rest:path}", "/{rest:path}")
                and "GET" in (getattr(r, "methods", None) or set())), len(vr.routes))
    vr.routes[idx:idx] = new
    if hasattr(vr, "_mark_routes_changed"):
        vr._mark_routes_changed()
    vr._oil_spliced = True


_splice()
