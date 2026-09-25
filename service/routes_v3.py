"""API v3 (case «Макропластик») — docs/CONTRACTS_V3.md. Data layer: service/case_store.py.

All endpoints parse query strings themselves so that every error has the contract shape
{"error": {"code", "message", "details"}} (400 BAD_BBOX/BAD_DATE/BAD_PARAM, 404 NOT_FOUND, 500 INTERNAL).
Empty data = HTTP 200 + empty collection + "empty_reason". CORS: Access-Control-Allow-Origin: * on every response.
Deterministic: collections are sorted by id; only generated_at/ran_at depend on the clock.
"""
from __future__ import annotations

import datetime as dt
import functools
import inspect
import json
import traceback

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from . import case_store as cs
from .case_store import ApiError

router = APIRouter(prefix="/api/v3", tags=["v3"])
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Expose-Headers": "Content-Disposition"}


def _ok(obj, status: int = 200) -> JSONResponse:
    return JSONResponse(obj, status_code=status, headers=CORS)


def _err(e: ApiError) -> JSONResponse:
    return JSONResponse({"error": {"code": e.code, "message": e.message, "details": e.details}},
                        status_code=e.status, headers=CORS)


def api(fn):
    """ApiError -> contract error JSON; anything else -> 500 INTERNAL (never an HTML/stack trace)."""
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def wrapper(*a, **kw):
            try:
                return await fn(*a, **kw)
            except ApiError as e:
                return _err(e)
            except Exception as e:
                traceback.print_exc()
                return _err(ApiError(500, "INTERNAL", "Внутренняя ошибка сервера", {"type": type(e).__name__}))
    else:
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            try:
                return fn(*a, **kw)
            except ApiError as e:
                return _err(e)
            except Exception as e:
                traceback.print_exc()
                return _err(ApiError(500, "INTERNAL", "Внутренняя ошибка сервера", {"type": type(e).__name__}))
    return wrapper


def _q(request: Request) -> dict:
    return {k: v for k, v in request.query_params.items()}


# ------------------------------------------------------------------ filter parsing (shared by list + export)
def _obs_filters(q: dict) -> dict:
    a, b = cs.parse_dates(q.get("date_from"), q.get("date_to"))
    return {"bbox": cs.parse_bbox(q.get("bbox")), "date_from": a, "date_to": b,
            "sources": cs.parse_list(q.get("source"), "source", cs.SOURCE_IDS),
            "profiles": cs.parse_list(q.get("profile"), "profile", cs.PROFILE_IDS),
            "scopes": cs.parse_list(q.get("scope"), "scope", cs.SCOPE_IDS),
            "record_type": cs.parse_choice(q.get("record_type"), "record_type", cs.RECORD_TYPE_IDS)}


def _pair_filters(q: dict) -> dict:
    a, b = cs.parse_dates(q.get("date_from"), q.get("date_to"))
    return {"sample_id": q.get("sample_id") or None, "scene_id": q.get("scene_id") or None,
            "status": cs.parse_choice(q.get("status"), "status", ["accepted", "rejected", "all"], "all"),
            "max_dt_hours": cs.parse_float(q.get("max_dt_hours"), "max_dt_hours", 0, 1e6),
            "date_from": a, "date_to": b, "sources": cs.parse_list(q.get("source"), "source", cs.SOURCE_IDS)}


def _zone_filters(q: dict) -> dict:
    a, b = cs.parse_dates(q.get("date_from"), q.get("date_to"))
    return {"bbox": cs.parse_bbox(q.get("bbox")), "date_from": a, "date_to": b, "scene_id": q.get("scene_id") or None,
            "statuses": cs.parse_list(q.get("status"), "status", cs.STATUS_IDS),
            "profiles": cs.parse_list(q.get("profile"), "profile", cs.PROFILE_IDS),
            "min_area_km2": cs.parse_float(q.get("min_area_km2"), "min_area_km2", 0, 1e9)}


def _scene_filters(q: dict) -> dict:
    a, b = cs.parse_dates(q.get("date_from"), q.get("date_to"))
    return {"bbox": cs.parse_bbox(q.get("bbox")), "date_from": a, "date_to": b,
            "missions": cs.parse_list(q.get("mission"), "mission", cs.MISSIONS + ["Landsat-7"]),
            "status": cs.parse_choice(q.get("status"), "status", ["accepted", "rejected", "all"], "all")}


def _geometry(q: dict) -> str:
    return cs.parse_choice(q.get("geometry"), "geometry", ["point", "line"], "point")


# ------------------------------------------------------------------ endpoints
@router.options("/{rest:path}", include_in_schema=False)
def preflight(rest: str):
    return Response(status_code=204, headers=CORS)


@router.get("/meta", summary="Словари, единицы, диапазоны дат")
@api
def meta():
    return _ok(cs.meta())


@router.get("/observations", summary="Полевые наблюдения (GeoJSON), фильтры bbox/date/source/profile/scope/record_type")
@api
def observations(request: Request):
    q = _q(request)
    f = _obs_filters(q)
    geom = _geometry(q)
    limit = cs.parse_int(q.get("limit"), "limit", 1, 100000, 5000)
    return _ok(cs.observations_fc(cs.filter_samples(**f), geom, limit))


@router.get("/observations/{sample_id}", summary="Одно наблюдение + пары")
@api
def observation(sample_id: str, request: Request):
    geom = _geometry(_q(request))
    r = cs.sample_row(sample_id)
    if r is None:
        raise ApiError(404, "NOT_FOUND", f"Наблюдение {sample_id} не найдено", {"sample_id": sample_id})
    feat = cs.observations_fc([r], geom)["features"][0]
    feat["pairs"] = cs.filter_pairs(sample_id=sample_id)
    return _ok(feat)


@router.get("/pairs", summary="Реестр пар «наблюдение ↔ снимок»")
@api
def pairs(request: Request):
    items = cs.filter_pairs(**_pair_filters(_q(request)))
    return _ok({"count": len(items), "empty_reason": cs.pairs_empty_reason(len(items)), "pairs": items})


@router.get("/scenes", summary="Снимки из реестра пар")
@api
def scenes(request: Request):
    items = cs.filter_scenes(**_scene_filters(_q(request)))
    return _ok({"count": len(items), "empty_reason": cs.scenes_empty_reason(len(items)), "scenes": items})


@router.get("/scenes/{scene_id}", summary="Снимок + зоны + пары")
@api
def scene(scene_id: str):
    for s in cs.scenes_all():
        if s["scene_id"] == scene_id:
            out = dict(s)
            out["zones"] = cs.zones_fc(cs.filter_zones(scene_id=scene_id))
            out["pairs"] = cs.filter_pairs(scene_id=scene_id)
            return _ok(out)
    raise ApiError(404, "NOT_FOUND", f"Снимок {scene_id} не найден в реестре", {"scene_id": scene_id})


def _scene_png(scene_id: str, name: str):
    p = cs.quality_file(scene_id, name)
    if p is None:
        raise ApiError(404, "NO_SCENE", f"Для снимка {scene_id} нет {name}", {"scene_id": scene_id})
    return FileResponse(p, media_type="image/png", headers={**CORS, "Cache-Control": "public, max-age=3600"})


@router.get("/scenes/{scene_id}/rgb.png", summary="RGB-вырезка вокруг наблюдения")
@api
def scene_rgb(scene_id: str):
    return _scene_png(scene_id, "rgb.png")


@router.get("/scenes/{scene_id}/quality.png", summary="Маска качества (цвета — meta.quality_classes)")
@api
def scene_quality(scene_id: str):
    return _scene_png(scene_id, "quality.png")


@router.get("/scenes/{scene_id}/mask.png", summary="Маска детектора")
@api
def scene_mask(scene_id: str):
    return _scene_png(scene_id, "mask.png")


@router.get("/zones", summary="Зоны проверки снимком (оценки модели); концентрация недоступна")
@api
def zones(request: Request):
    return _ok(cs.zones_fc(cs.filter_zones(**_zone_filters(_q(request)))))


@router.get("/zones/{zone_id}", summary="Зона + связанные наблюдения")
@api
def zone(zone_id: str):
    for f in cs.zones_all():
        if f["id"] == zone_id:
            out = json.loads(json.dumps(f))
            p = f["properties"]
            sid = p["scene_id"]
            out["crop_url"] = f"/api/v3/scenes/{sid}/rgb.png" if sid and cs.quality_file(sid, "rgb.png") else None
            out["prob_crop_url"] = f"/api/v3/scenes/{sid}/mask.png" if sid and cs.quality_file(sid, "mask.png") else None
            rows = [r for r in (cs.sample_row(s) for s in p["support"]["linked_sample_ids"]) if r]
            out["linked_observations"] = cs.observations_fc(rows)
            out["explain"] = [p["status_reason"][:80], "Концентрация по снимку не оценивается: нет калибровки",
                              "Площадь — полоса наблюдения, не пятно мусора"]
            return _ok(out)
    raise ApiError(404, "NOT_FOUND", f"Зона {zone_id} не найдена", {"zone_id": zone_id})


@router.get("/metrics", summary="Метрики: детектор и концентрация, baseline vs основной")
@api
def metrics():
    return _ok(cs.metrics())


# ------------------------------------------------------------------ export
def _query_to_params(qr: dict, layer: str) -> dict:
    """Saved query object -> query-string params of the layer."""
    out = {}
    if qr.get("bbox"):
        out["bbox"] = ",".join(str(x) for x in qr["bbox"])
    for k in ("date_from", "date_to"):
        if qr.get(k):
            out[k] = qr[k]
    if layer in ("observations", "pairs") and qr.get("sources"):
        out["source"] = ",".join(qr["sources"])
    if layer in ("observations", "zones") and qr.get("profiles"):
        out["profile"] = ",".join(qr["profiles"])
    if layer == "zones":
        if qr.get("statuses"):
            out["status"] = ",".join(qr["statuses"])
        if qr.get("scene_id"):
            out["scene_id"] = qr["scene_id"]
    if layer == "pairs":
        out.pop("bbox", None)
        if qr.get("scene_id"):
            out["scene_id"] = qr["scene_id"]
    return out


@router.get("/export", summary="Выгрузка observations|pairs|zones в geojson|csv (те же фильтры или query_id)")
@api
def export(request: Request):
    q = _q(request)
    layer = cs.parse_choice(q.get("layer"), "layer", ["observations", "pairs", "zones"])
    if layer is None:
        raise ApiError(400, "BAD_PARAM", "layer: обязателен, observations | pairs | zones", {"param": "layer"})
    fmt = cs.parse_choice(q.get("format"), "format", ["geojson", "csv"], "geojson")
    if q.get("query_id"):
        q = {**_query_to_params(cs.get_query(q["query_id"])["query"], layer),
             **{k: v for k, v in q.items() if k in ("geometry", "limit")}}
    stamp = dt.date.today().isoformat()
    if layer == "observations":
        rows = cs.filter_samples(**_obs_filters(q))
        if fmt == "csv":
            body = cs.observations_csv(rows)
        else:
            obj = cs.observations_fc(rows, _geometry(q))
    elif layer == "pairs":
        items = cs.filter_pairs(**_pair_filters(q))
        if fmt == "csv":
            body = cs.pairs_csv(items)
        else:
            obj = {"type": "FeatureCollection", "kind": "pair", "count": len(items),
                   "empty_reason": cs.pairs_empty_reason(len(items)),
                   "features": [{"type": "Feature", "id": p["pair_id"], "geometry": p["geometry"],
                                 "properties": {k: v for k, v in p.items() if k != "geometry"}} for p in items]}
    else:
        feats = cs.filter_zones(**_zone_filters(q))
        if fmt == "csv":
            body = cs.zones_csv(feats)
        else:
            obj = cs.zones_fc(feats)
    name = f"{layer}_{stamp}.{fmt}"
    headers = {**CORS, "Content-Disposition": f'attachment; filename="{name}"'}
    if fmt == "csv":
        return Response(("﻿" + body).encode("utf-8"), media_type="text/csv; charset=utf-8", headers=headers)
    return Response(json.dumps(obj, ensure_ascii=False).encode("utf-8"), media_type="application/geo+json",
                    headers=headers)


# ------------------------------------------------------------------ saved queries
@router.post("/queries", status_code=201, summary="Сохранить запрос")
@api
async def query_add(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise ApiError(400, "BAD_PARAM", "тело: ожидается JSON {\"name\": ..., \"query\": {...}}", {})
    return _ok(cs.add_query(body), 201)


@router.get("/queries", summary="Список сохранённых запросов")
@api
def query_list():
    return _ok({"queries": cs.list_queries()})


@router.get("/queries/{query_id}", summary="Один сохранённый запрос")
@api
def query_get(query_id: str):
    return _ok(cs.get_query(query_id))


@router.get("/queries/{query_id}/run", summary="Выполнить сохранённый запрос")
@api
def query_run(query_id: str):
    rec = cs.get_query(query_id)
    qr = rec["query"]
    obs_rows = cs.filter_samples(bbox=qr["bbox"], date_from=qr["date_from"], date_to=qr["date_to"],
                                 sources=qr["sources"] or None, profiles=qr["profiles"] or None)
    zf = cs.filter_zones(bbox=qr["bbox"], date_from=qr["date_from"], date_to=qr["date_to"], scene_id=qr["scene_id"],
                         statuses=qr["statuses"] or None, profiles=qr["profiles"] or None)
    sc = cs.filter_scenes(bbox=qr["bbox"], date_from=qr["date_from"], date_to=qr["date_to"])
    if qr["scene_id"]:
        sc = [s for s in sc if s["scene_id"] == qr["scene_id"]]
    if qr["sources"] or qr["profiles"]:  # zones/scenes have no source of their own -> via linked observations
        ids = {r.get("sample_id") for r in obs_rows}
        zf = [f for f in zf if ids & set(f["properties"]["support"]["linked_sample_ids"])]
        linked = {p["scene_id"] for p in cs.pairs_all() if p["sample_id"] in ids and p["scene_id"]}
        sc = [s for s in sc if s["scene_id"] in linked]
    by_status: dict[str, int] = {}
    for f in zf:
        by_status[f["properties"]["status"]] = by_status.get(f["properties"]["status"], 0) + 1
    return _ok({"query_id": query_id, "name": rec.get("name"), "ran_at": cs.now_iso(), "query": qr,
                "observations": cs.observations_fc(obs_rows), "zones": cs.zones_fc(zf), "scenes": sc,
                "summary": {"n_obs": len(obs_rows), "n_zones": len(zf), "n_scenes": len(sc),
                            "by_status": dict(sorted(by_status.items()))}})


@router.delete("/queries/{query_id}", status_code=204, summary="Удалить сохранённый запрос")
@api
def query_delete(query_id: str):
    cs.delete_query(query_id)
    return Response(status_code=204, headers=CORS)


@router.get("/{rest:path}", include_in_schema=False)
@api
def v3_404(rest: str):
    raise ApiError(404, "NOT_FOUND", f"нет такого эндпоинта: /api/v3/{rest}", {})
