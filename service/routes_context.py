"""Context layer - objects from OpenStreetMap, which of them the demo drift cloud touches, repeated finds.

Files: service/context/<region>.geojson (scripts/fetch_osm_context.py; override dir with $MACROPLASTIC_CONTEXT).
Contract: docs/CONTRACTS.md, section «Контекст: объекты OSM, угрозы, постоянные источники».
Team decision: no numeric warnings (no hours, no percentages), nothing for the event feed, no
"source/polluter" label and no linking of finds to river mouths as culprits.

- GET /api/context?region=[&kind=]        -> GeoJSON FeatureCollection of OSM objects (ODbL attribution)
- GET /api/threats?region=&date=[&buffer_m=300]
      list of OSM objects the drift particle cloud (drift.json) touches - for the drift panel only; no hours/percent
- GET /api/repeats[?region=][&min_dates=2]  (internal, not in the OpenAPI schema)
      finds (not artifacts) in one H3 res 8 cell on >= 2 reliable dates -> «повторяемость, требует проверки»
"""
from __future__ import annotations

import json
import math
import os
import threading
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from . import core, place

router = APIRouter(tags=["context"])

CONTEXT_ENV = "MACROPLASTIC_CONTEXT"
OSM_SOURCE = "© OpenStreetMap contributors (ODbL)"
TOUCH_KINDS = ("aquaculture", "beach", "protected_area", "marina", "port")
KIND_RU = {"aquaculture": "аквакультура / ферма", "beach": "пляж", "port": "порт", "marina": "марина",
           "protected_area": "охраняемая зона", "river_mouth": "устье реки / канала",
           "outfall": "выпуск / трубопровод стоков", "wastewater_plant": "очистные сооружения"}
DRIFT_NOTE = "демо-прогноз, не валидирован"
DRIFT_RULE = ("объекты OSM (фермы, пляжи, охраняемые зоны, марины, порты), в буфер {b:g} м которых заходит облако "
              "частиц демо-прогноза дрейфа за 72 ч (не менее 1 % частиц); только для панели дрейфа, без сроков и "
              "вероятностей, не предупреждение")
REPEAT_CAPTION = "повторяемость (исследовательский слой)"
REPEAT_RULE = ("находки (не артефакты, любая модель) в одной ячейке H3 res 8 на ≥ {n} надёжных датах района "
               "(надёжность даты — то же правило, что в /api/calendar); площадь — сумма по датам, на дату — "
               "максимум по моделям")
DEFAULT_BUFFER_M = 300.0
MIN_SHARE = 0.01  # internal noise floor: an object counts as touched if >= 1 % of particles enter its buffer
_lock = threading.Lock()
_cache: dict = {}
_stores: dict = {}


# ------------------------------------------------------------------ helpers
def context_dir() -> Path:
    env = os.environ.get(CONTEXT_ENV)
    return Path(env) if env else core.SERVICE_DIR / "context"


def store() -> core.Store:
    root, how = core.resolve_root(None)
    key = str(root)
    st = _stores.get(key)
    if st is None:
        st = core.Store(root, how)
        _stores[key] = st
    return st


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _cached(key, fn):
    with _lock:
        if key in _cache:
            return _cache[key]
    val = fn()
    with _lock:
        if len(_cache) > 256:
            _cache.clear()
        _cache[key] = val
    return val


def _check_name(rid: str) -> str:
    if not rid or not core._NAME_RE.match(rid):
        raise core.BadRequest("region: латиница, цифры, _ и -")
    return rid


def load_context(rid: str, cdir: Optional[Path] = None) -> dict:
    _check_name(rid)
    fp = (cdir or context_dir()) / f"{rid}.geojson"
    if not fp.is_file():
        raise core.NotFound(f"нет объектов OSM для района {rid!r}; запустите scripts/fetch_osm_context.py")
    return _cached(("ctx", str(fp), _mtime(fp)), lambda: json.loads(fp.read_text(encoding="utf-8")))


class LocalProj:
    """Equirectangular metres around a reference point (error << buffer at region scale ~50 km)."""

    def __init__(self, lon0: float, lat0: float):
        self.lon0, self.lat0 = lon0, lat0
        self.kx = 111320.0 * math.cos(math.radians(lat0))
        self.ky = 110574.0

    def xy(self, lon, lat):
        return (np.asarray(lon) - self.lon0) * self.kx, (np.asarray(lat) - self.lat0) * self.ky

    def geom(self, g):
        import shapely

        return shapely.transform(g, lambda c: np.column_stack([(c[:, 0] - self.lon0) * self.kx,
                                                                (c[:, 1] - self.lat0) * self.ky]))


def _obj_point(g) -> tuple[float, float]:
    p = g.representative_point() if g.geom_type != "Point" else g
    return round(p.x, 6), round(p.y, 6)


# ------------------------------------------------------------------ drift contacts (no hours, no percent)
def compute_contacts(ctx: dict, drift: dict, buffer_m: float = DEFAULT_BUFFER_M, min_share: float = MIN_SHARE) -> dict:
    """OSM objects whose buffer the drift particle cloud enters (any time 0..72 h), >= min_share of particles."""
    import shapely
    from shapely.geometry import shape

    paths = [p.get("path") or [] for p in drift.get("particles") or []]
    paths = [p for p in paths if p]
    n = len(paths)
    out = {"buffer_m": buffer_m, "note": DRIFT_NOTE, "rule": DRIFT_RULE.format(b=buffer_m), "n_objects": 0,
           "objects": []}
    objs = [f for f in ctx.get("features") or [] if (f.get("properties") or {}).get("kind") in TOUCH_KINDS]
    if not n or not objs:
        return out
    pts = np.array([[q[0], q[1], k] for k, p in enumerate(paths) for q in p], dtype=float)
    proj = LocalProj(float(np.mean(pts[:, 0])), float(np.mean(pts[:, 1])))
    geoms_ll = [shape(f["geometry"]) for f in objs]
    tree = shapely.STRtree([proj.geom(g).buffer(buffer_m) for g in geoms_ll])
    x, y = proj.xy(pts[:, 0], pts[:, 1])
    ipt, iobj = tree.query(shapely.points(x, y), predicate="intersects")
    hit: dict[int, set] = {}
    for a, b in zip(ipt, iobj):
        hit.setdefault(int(b), set()).add(int(pts[a, 2]))
    need = max(1, math.ceil(min_share * n))
    rows = []
    for b, ks in hit.items():
        if len(ks) < need:
            continue
        pr = objs[b]["properties"]
        lon, lat = _obj_point(geoms_ll[b])
        kind_ru = pr.get("kind_ru") or KIND_RU.get(pr.get("kind"), pr.get("kind"))
        nm = f" «{pr['name']}»" if pr.get("name") else ""
        rows.append({"object_id": pr.get("id"), "kind": pr.get("kind"), "kind_ru": kind_ru, "name": pr.get("name"),
                     "osm_id": pr.get("osm_id"), "lon": lon, "lat": lat,
                     "text": f"{kind_ru}{nm} — облако частиц демо-прогноза задевает объект",
                     "source": pr.get("source") or OSM_SOURCE})
    order = {k: i for i, k in enumerate(TOUCH_KINDS)}
    rows.sort(key=lambda r: (order.get(r["kind"], 99), r["name"] is None, r["name"] or "", r["object_id"] or ""))
    out["objects"] = rows
    out["n_objects"] = len(rows)
    return out


def contacts(st: core.Store, rid: str, date: Optional[str], buffer_m: float = DEFAULT_BUFFER_M,
             cdir: Optional[Path] = None) -> dict:
    d = st.date_entry(rid, date)
    date = d["date"]
    rel = d.get("drift") or f"{rid}/{date}/drift.json"
    try:
        p = core.safe_path(st.root, rel)
    except core.NotFound:
        raise core.NotFound(f"нет прогноза дрейфа для {rid} {date}") from None
    ctx = load_context(rid, cdir)
    cfp = (cdir or context_dir()) / f"{rid}.geojson"
    key = ("drift", str(p), _mtime(p), str(cfp), _mtime(cfp), float(buffer_m))
    res = _cached(key, lambda: compute_contacts(ctx, st.read_json(rel), buffer_m))
    return {"region": rid, "date": date, "feed": False, "osm_source": OSM_SOURCE, **res}


# ------------------------------------------------------------------ repeated finds (neutral, internal)
def compute_repeats(st: core.Store, rid: str, min_dates: int = 2, model: Optional[str] = None) -> list[dict]:
    cells: dict[str, dict] = {}
    for d in st.region_dates(rid):
        date = d["date"]
        for m in d.get("models") or []:
            if model and m != model:
                continue
            try:
                rel = place.date_reliability(st, rid, d, m)
            except Exception:
                continue
            if not rel["has_layer"] or rel["unreliable"]:
                continue
            real, _ = core.split_artifacts(core._features(st._optional(rid, date, m, "detections")))
            for f in real:
                pr = f["properties"]
                lon, lat = pr.get("lon"), pr.get("lat")
                if lon is None or lat is None:
                    lon, lat = core.centroid(f.get("geometry"))
                if lon is None:
                    continue
                c = cells.setdefault(place.cell_of(lon, lat),
                                     {"dates": {}, "models": set(), "n": 0, "confirmed": 0, "ids": []})
                c["dates"].setdefault(date, {}).setdefault(m, 0.0)
                c["dates"][date][m] += float(pr.get("area_m2") or 0.0)
                c["models"].add(m)
                c["n"] += 1
                c["confirmed"] += 1 if pr.get("confirmed") else 0
                if len(c["ids"]) < 50:
                    c["ids"].append(pr.get("id"))
    out = []
    for h, c in cells.items():
        if len(c["dates"]) < min_dates:
            continue
        lon, lat = place.cell_center(h)
        dates = sorted(c["dates"])
        area = round(sum(max(v.values()) for v in c["dates"].values()), 1)
        out.append({"region": rid, "h3": h, "lon": lon, "lat": lat, "n_dates": len(dates), "dates": dates,
                    "models": sorted(c["models"]), "n_detections": c["n"], "n_confirmed": c["confirmed"],
                    "area_m2": area, "detection_ids": c["ids"],
                    "text": f"повторяющиеся находки в этой ячейке на {len(dates)} датах ({', '.join(dates)}) — "
                            f"{REPEAT_CAPTION}"})
    out.sort(key=lambda r: (-r["n_dates"], -r["area_m2"]))
    return out


def repeats(st: core.Store, rid: Optional[str] = None, min_dates: int = 2, model: Optional[str] = None) -> dict:
    if rid:
        _check_name(rid)
        st.region(rid)  # 404 for an unknown region
    rids = [rid] if rid else [r["id"] for r in st.regions()]
    man = st.root / "manifest.json" if st.root else None
    rows = []
    for r in rids:
        key = ("rep", str(st.root), _mtime(man) if man else 0, r, min_dates, model)
        rows += _cached(key, lambda r=r: compute_repeats(st, r, min_dates, model))
    return {"region": rid, "model": model, "min_dates": min_dates, "h3_res": place.H3_RES,
            "caption": REPEAT_CAPTION, "rule": REPEAT_RULE.format(n=min_dates), "n_cells": len(rows), "cells": rows}


# ------------------------------------------------------------------ API
def _err(exc: Exception) -> JSONResponse:
    code = 404 if isinstance(exc, core.NotFound) else 422
    return JSONResponse({"detail": str(exc)}, status_code=code)


@router.get("/api/context")
def api_context(region: str = Query(...), kind: Optional[str] = Query(None)):
    try:
        _check_name(region)
        st = store()
        if st.has_data():
            st.region(region)
        fc = load_context(region)
    except (core.NotFound, core.BadRequest) as e:
        return _err(e)
    if kind:
        kinds = set(kind.split(","))
        fc = {**fc, "features": [f for f in fc["features"] if f["properties"].get("kind") in kinds]}
    return JSONResponse(fc, media_type="application/geo+json")


@router.get("/api/threats")
def api_threats(region: str = Query(...), date: Optional[str] = Query(None),
                buffer_m: float = Query(DEFAULT_BUFFER_M, ge=50, le=5000)):
    """Drift panel only: OSM objects the demo drift cloud touches. No hours, no percentages, not for the feed."""
    try:
        if date and not core.DATE_RE.match(date):
            raise core.BadRequest("date: YYYY-MM-DD")
        return contacts(store(), _check_name(region), date, buffer_m)
    except (core.NotFound, core.BadRequest) as e:
        return _err(e)


@router.get("/api/repeats", include_in_schema=False)
def api_repeats(region: Optional[str] = Query(None), min_dates: int = Query(2, ge=2, le=50),
                model: Optional[str] = Query(None)):
    """Internal: repeated finds per H3 cell («повторяемость, требует проверки»); no source/polluter label."""
    try:
        if model and not core._NAME_RE.match(model):
            raise core.BadRequest("model: латиница, цифры, _ и -")
        return repeats(store(), region or None, min_dates, model or None)
    except (core.NotFound, core.BadRequest) as e:
        return _err(e)
