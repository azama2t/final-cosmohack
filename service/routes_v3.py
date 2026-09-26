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


JSON_UTF8 = "application/json; charset=utf-8"
GEOJSON_UTF8 = "application/geo+json; charset=utf-8"
CSV_UTF8 = "text/csv; charset=utf-8"


class JSONUtf8(JSONResponse):
    """JSON with an explicit charset: Windows PowerShell 5.1 (Invoke-RestMethod/WebRequest) otherwise decodes the
    body as ISO-8859-1 and shows Cyrillic as mojibake (reports/selfcheck/clean_clone_0003.md)."""
    media_type = JSON_UTF8


def _ok(obj, status: int = 200) -> JSONResponse:
    return JSONUtf8(obj, status_code=status, headers=CORS)


def _err(e: ApiError) -> JSONResponse:
    return JSONUtf8({"error": {"code": e.code, "message": e.message, "details": e.details}},
                        status_code=e.status, headers=CORS)


# Allowed query-string parameters per endpoint (contract names). Unknown -> 400 BAD_PARAM with the allowed list
# (jury-4: `sources=` in export was silently ignored). Plural aliases (the names of the saved-query object) are
# accepted explicitly and mapped to the contract names. Endpoints not listed here are not checked.
_OBS_P = {"bbox", "date_from", "date_to", "source", "profile", "scope", "record_type", "geometry", "limit", "offset"}
_PAIR_P = {"sample_id", "scene_id", "status", "max_dt_hours", "date_from", "date_to", "source", "limit", "offset"}
_SCENE_P = {"bbox", "date_from", "date_to", "mission", "status", "limit", "offset"}
_ZONE_P = {"bbox", "date_from", "date_to", "scene_id", "status", "profile", "min_area_km2", "source", "scope",
           "detection_status", "concentration_status", "limit", "offset"}
PARAMS: dict = {
    "meta": set(), "observations": _OBS_P, "observation": {"geometry"}, "pairs": _PAIR_P, "scenes": _SCENE_P,
    "scene": set(), "scene_rgb": set(), "scene_quality": set(), "scene_mask": set(), "zones": _ZONE_P,
    "zone": set(), "metrics": set(), "query_list": set(), "query_get": set(), "query_run": set(),
    "query_add": set(), "query_delete": set(),
    "export": None,  # checked inside (depends on layer / query_id)
    "scene_zones": None, "scene_zone": set(), "sz_scenes": set(), "sz_scene_png": set(), "sz_crop": set(),  # 3.10
}
_SZ_P = {"bbox", "date_from", "date_to", "status", "detection_status", "concentration_status", "scene_kind",
         "scene_key", "limit", "offset",
         # jury 12:56 T5: the UI's «Акватория» (field source) / profile / scope reach the zone export too; + region, is_find
         "source", "profile", "scope", "region", "is_find"}
PARAMS["scene_zones"] = _SZ_P
ALIASES = {"sources": "source", "profiles": "profile", "scopes": "scope", "statuses": "status", "missions": "mission"}


def check_params(q: dict, allowed: set, where: str) -> dict:
    """Map explicit plural aliases, reject unknown parameters (400) and alias + name given together."""
    out = {}
    for k, v in q.items():
        name = ALIASES.get(k, k) if ALIASES.get(k) in allowed else k
        if name != k and name in q:
            raise ApiError(400, "BAD_PARAM", f"{k} и {name} — один и тот же параметр, укажите один",
                           {"param": k, "alias_of": name})
        out[name] = v
    unknown = sorted(k for k in out if k not in allowed)
    if unknown:
        raise ApiError(400, "BAD_PARAM", f"{where}: неизвестные параметры {', '.join(unknown)}",
                       {"unknown": unknown, "allowed": sorted(allowed),
                        "aliases": {a: n for a, n in ALIASES.items() if n in allowed}})
    return out


def api(fn):
    """ApiError -> contract error JSON; anything else -> 500 INTERNAL (never an HTML/stack trace).
    Query-string parameters are validated against PARAMS[fn.__name__] (a hidden Request argument is injected)."""
    sig = inspect.signature(fn)
    has_req = any(p.annotation is Request or p.annotation == "Request" for p in sig.parameters.values())
    allowed = PARAMS.get(fn.__name__, None)
    where = fn.__name__

    def _pre(kw):
        rq = kw.pop("_api_request", None) if not has_req else next(
            (v for v in kw.values() if isinstance(v, Request)), None)
        if rq is not None and allowed is not None:
            check_params(dict(rq.query_params.items()), allowed, rq.url.path)

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def wrapper(*a, **kw):
            try:
                _pre(kw)
                with cs.request_scope():  # files are stat()-ed once per request
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
                _pre(kw)
                with cs.request_scope():
                    return fn(*a, **kw)
            except ApiError as e:
                return _err(e)
            except Exception as e:
                traceback.print_exc()
                return _err(ApiError(500, "INTERNAL", "Внутренняя ошибка сервера", {"type": type(e).__name__}))
    if not has_req:
        params = list(sig.parameters.values()) + [
            inspect.Parameter("_api_request", inspect.Parameter.KEYWORD_ONLY, annotation=Request)]
        wrapper.__signature__ = sig.replace(parameters=params)
    return wrapper


def _q(request: Request) -> dict:
    """Query parameters with plural aliases mapped to contract names (validation is done in `api`)."""
    q = {k: v for k, v in request.query_params.items()}
    return {(ALIASES[k] if k in ALIASES and ALIASES[k] not in q else k): v for k, v in q.items()}


def _page(q: dict, n_default: int = 100000) -> tuple:
    return (cs.parse_int(q.get("limit"), "limit", 1, 100000, n_default),
            cs.parse_int(q.get("offset"), "offset", 0, 10 ** 7, 0))


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
            "min_area_km2": cs.parse_float(q.get("min_area_km2"), "min_area_km2", 0, 1e9),
            "sources": cs.parse_list(q.get("source"), "source", cs.SOURCE_IDS),
            "scopes": cs.parse_list(q.get("scope"), "scope", cs.SCOPE_IDS),
            "detection_statuses": cs.parse_list(q.get("detection_status"), "detection_status",
                                                cs.DETECTION_STATUS_IDS),
            "concentration_statuses": cs.parse_list(q.get("concentration_status"), "concentration_status",
                                                    cs.CONCENTRATION_STATUS_IDS)}


def _sz_filters(q: dict) -> dict:
    a, b = cs.parse_dates(q.get("date_from"), q.get("date_to"))
    return {"bbox": cs.parse_bbox(q.get("bbox")), "date_from": a, "date_to": b,
            "statuses": cs.parse_list(q.get("status"), "status", cs.STATUS_IDS),
            "detection_statuses": cs.parse_list(q.get("detection_status"), "detection_status",
                                                cs.DETECTION_STATUS_IDS),
            "concentration_statuses": cs.parse_list(q.get("concentration_status"), "concentration_status",
                                                    cs.CONCENTRATION_STATUS_IDS),
            "scene_kinds": cs.parse_list(q.get("scene_kind"), "scene_kind", ["demo", "live", "drift"]),
            "scene_key": q.get("scene_key") or None,
            # field-record filters: satellite zones have none -> 0 zones (cs.filter_scene_zones), as the v2 UI
            "sources": cs.parse_list(q.get("source"), "source", cs.SOURCE_IDS),
            "profiles": cs.parse_list(q.get("profile"), "profile", cs.PROFILE_IDS),
            "scopes": cs.parse_list(q.get("scope"), "scope", cs.SCOPE_IDS),
            "regions": cs.parse_list(q.get("region"), "region", [r["id"] for r in cs.sz_regions()]),
            "is_find": {None: None, "": None, "true": True, "1": True, "false": False, "0": False}.get(
                q.get("is_find"), "bad")}


def _sz_check(f: dict) -> dict:
    if f["is_find"] == "bad":
        raise ApiError(400, "BAD_PARAM", "is_find: true | false", {"param": "is_find", "allowed": ["true", "false"]})
    return f


def _sz_field_filter(f: dict) -> bool:
    return bool(f.get("sources") or f.get("profiles") or f.get("scopes"))


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
    limit, offset = _page(q, 5000)
    rows = cs.filter_samples(**f)
    fc = cs.observations_fc(rows[offset:], geom, limit)
    fc["total"], fc["offset"], fc["limit"] = len(rows), offset, limit
    return _ok(fc)


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
    q = _q(request)
    items = cs.filter_pairs(**_pair_filters(q))
    limit, offset = _page(q)
    page = items[offset:offset + limit]
    return _ok({"count": len(page), "total": len(items), "offset": offset, "limit": limit,
                "empty_reason": cs.pairs_empty_reason(len(page)) if page or not items
                else "Нет пар на этой странице (offset больше числа пар)", "pairs": page})


@router.get("/scenes", summary="Снимки из реестра пар")
@api
def scenes(request: Request):
    q = _q(request)
    items = cs.filter_scenes(**_scene_filters(q))
    limit, offset = _page(q)
    page = items[offset:offset + limit]
    return _ok({"count": len(page), "total": len(items), "offset": offset, "limit": limit,
                "empty_reason": cs.scenes_empty_reason(len(page)) if page or not items
                else "Нет снимков на этой странице (offset больше числа снимков)", "scenes": page})


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


@router.get("/scenes/{scene_id}/quality.png", summary="Маска качества RGBA (цвета = meta.quality_classes)")
@api
def scene_quality(scene_id: str):
    tif = cs.quality_file(scene_id, "quality.tif")
    if tif is None:
        raise ApiError(404, "NO_SCENE", f"Для снимка {scene_id} нет маски качества", {"scene_id": scene_id})
    body = cs.render_quality_png(tif)
    if body is None:
        raise ApiError(404, "NO_SCENE", f"Маску качества снимка {scene_id} не удалось прочитать", {"scene_id": scene_id})
    return Response(body, media_type="image/png", headers={**CORS, "Cache-Control": "public, max-age=3600"})


@router.get("/scenes/{scene_id}/mask.png", summary="Маска детектора")
@api
def scene_mask(scene_id: str):
    return _scene_png(scene_id, "mask.png")


@router.get("/zones", summary="Зоны проверки снимком (оценки модели); концентрация недоступна")
@api
def zones(request: Request):
    q = _q(request)
    feats = cs.filter_zones(**_zone_filters(q))
    limit, offset = _page(q)
    fc = cs.zones_fc(feats[offset:offset + limit])
    fc["total"], fc["offset"], fc["limit"] = len(feats), offset, limit
    return _ok(fc)


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
            out["detections"] = cs.detections_fc([f])
            out["explain"] = [p["status_reason"][:80], "Концентрация по снимку не оценивается: перенос не подтверждён",
                              "Площадь — полоса наблюдения, не пятно мусора"]
            if p["support"].get("field_target_scope") and p["support"]["field_target_scope"] not in cs.PLASTIC_SCOPES:
                out["explain"].append("Полевое измерение — весь мусор, не только пластик")
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
    if layer in ("observations", "zones") and qr.get("scopes"):
        out["scope"] = ",".join(qr["scopes"])
    if layer == "zones" and qr.get("sources"):
        out["source"] = ",".join(qr["sources"])
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
    layer = cs.parse_choice(q.get("layer"), "layer", ["observations", "pairs", "zones", "detections", "scene_zones"])
    if layer is None:
        raise ApiError(400, "BAD_PARAM", "layer: обязателен, observations | pairs | zones | detections | scene_zones",
                       {"param": "layer"})
    fmt = cs.parse_choice(q.get("format"), "format", ["geojson", "csv"], "geojson")
    base = {"layer", "format"}
    if "query_id" in q:
        if not (q.get("query_id") or "").strip():
            raise ApiError(400, "BAD_PARAM", "query_id: пустое значение (уберите параметр или укажите id)",
                           {"param": "query_id"})
        check_params(dict(request.query_params.items()), base | {"query_id", "geometry"}, "export (query_id)")
    else:
        lay = {"observations": _OBS_P, "pairs": _PAIR_P, "zones": _ZONE_P, "detections": _ZONE_P,
               "scene_zones": _SZ_P}[layer]
        check_params(dict(request.query_params.items()), base | (lay - {"limit", "offset"}),
                     f"export (layer={layer})")
    ran = None
    if q.get("query_id"):
        qr = cs.get_query(q["query_id"])["query"]
        ran = cs.run_query_layers(qr)  # the same executor as /queries/{id}/run
        q = {**_query_to_params(qr, "zones" if layer in ("detections", "scene_zones") else layer),
             **{k: v for k, v in q.items() if k in ("geometry",)}}
    stamp = dt.date.today().isoformat()
    if layer == "observations":
        rows = ran["obs_rows"] if ran is not None else cs.filter_samples(**_obs_filters(q))
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
    elif layer == "scene_zones":
        szf = _sz_check(_sz_filters(q)) if ran is None else {}
        feats = ran["scene_zones"] if ran is not None else cs.filter_scene_zones(**szf)
        if fmt == "csv":
            body = cs.scene_zones_csv(feats)
        else:
            obj = cs.scene_zones_fc(feats, field_filter=_sz_field_filter(szf) if ran is None else False)
    elif layer == "detections":  # detector objects of the zones selected by the same zone filters
        dfc = cs.detections_fc(ran["zones"] if ran is not None else cs.filter_zones(**_zone_filters(q)))
        if fmt == "csv":
            body = cs.detections_csv(dfc)
        else:
            obj = dfc
    else:
        feats = ran["zones"] if ran is not None else cs.filter_zones(**_zone_filters(q))
        if fmt == "csv":
            body = cs.zones_csv(feats)
        else:
            obj = cs.zones_fc(feats)
    name = f"{layer}_{stamp}.{fmt}"
    headers = {**CORS, "Content-Disposition": f'attachment; filename="{name}"'}
    if fmt == "csv":
        return Response(("﻿" + body).encode("utf-8"), media_type=CSV_UTF8, headers=headers)
    return Response(json.dumps(obj, ensure_ascii=False).encode("utf-8"), media_type=GEOJSON_UTF8,
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
    ran = cs.run_query_layers(qr)
    obs_rows, zf, sc = ran["obs_rows"], ran["zones"], ran["scenes"]
    by_status: dict[str, int] = {}
    by_conc: dict[str, int] = {}
    for f in zf:
        by_status[f["properties"]["status"]] = by_status.get(f["properties"]["status"], 0) + 1
        cst = f["properties"]["concentration_status"]
        by_conc[cst] = by_conc.get(cst, 0) + 1
    szf = ran.get("scene_zones") or []
    return _ok({"query_id": query_id, "name": rec.get("name"), "ran_at": cs.now_iso(), "query": qr,
                "observations": cs.observations_fc(obs_rows), "zones": cs.zones_fc(zf), "scenes": sc,
                "scene_zones": cs.scene_zones_fc(szf),
                "summary": {"n_obs": len(obs_rows), "n_zones": len(zf), "n_scenes": len(sc),
                            "n_scene_zones": len(szf),
                            "by_status": dict(sorted(by_status.items())),
                            "by_concentration_status": dict(sorted(by_conc.items()))}})


@router.delete("/queries/{query_id}", status_code=204, summary="Удалить сохранённый запрос")
@api
def query_delete(query_id: str):
    cs.delete_query(query_id)
    return Response(status_code=204, headers=CORS)


# ------------------------------------------------------------------ 3.10 satellite scene zones (L111)
@router.get("/scene_zones", summary="Спутниковые зоны детекции: измерено / вероятно / сценарий")
@api
def scene_zones(request: Request):
    q = _q(request)
    szf = _sz_check(_sz_filters(q))
    feats = cs.filter_scene_zones(**szf)
    limit, offset = _page(q)
    fc = cs.scene_zones_fc(feats[offset:offset + limit], field_filter=_sz_field_filter(szf))
    fc["total"], fc["offset"], fc["limit"] = len(feats), offset, limit
    return _ok(fc)


@router.get("/scene_zones/scenes", summary="Сцены слоя спутниковых зон (снимок + маска качества)")
@api
def sz_scenes():
    sc = cs.sz_scenes()
    return _ok({"count": len(sc), "empty_reason": None if sc else "Слой не построен (scripts/case/scene_zones.py)",
                "scenes": sc})


@router.get("/scene_zones/scenes/{key}/crops/{zone_id}.jpg", summary="Вырезка зоны: снимок | маска детектора")
@api
def sz_crop(key: str, zone_id: str):
    p = cs.sz_crop(key, zone_id)
    if p is None:
        raise ApiError(404, "NO_SCENE", f"Нет вырезки зоны {zone_id}", {"scene_key": key, "zone_id": zone_id})
    return FileResponse(p, media_type="image/jpeg", headers={**CORS, "Cache-Control": "public, max-age=3600"})


@router.get("/scene_zones/scenes/{key}/{name}", summary="rgb.jpg | quality.png сцены слоя")
@api
def sz_scene_png(key: str, name: str):
    p = cs.sz_file(key, name)
    if p is None:
        raise ApiError(404, "NO_SCENE", f"Для сцены {key} нет {name}", {"scene_key": key, "name": name})
    return FileResponse(p, media_type="image/jpeg" if name.endswith(".jpg") else "image/png",
                        headers={**CORS, "Cache-Control": "public, max-age=3600"})


@router.get("/scene_zones/{zone_id}", summary="Спутниковая зона + объекты детектора")
@api
def scene_zone(zone_id: str):
    for f in cs.scene_zones_all():
        if f["id"] == zone_id:
            out = json.loads(json.dumps(f))
            out["detections"] = cs.scene_zone_detections(zone_id)
            out["scene"] = next((s for s in cs.sz_scenes() if s["scene_key"] == f["properties"]["scene_key"]), None)
            out["examples"] = cs.sz_examples()
            return _ok(out)
    raise ApiError(404, "NOT_FOUND", f"Спутниковая зона {zone_id} не найдена", {"zone_id": zone_id})


@router.get("/{rest:path}", include_in_schema=False)
@api
def v3_404(rest: str):
    raise ApiError(404, "NOT_FOUND", f"нет такого эндпоинта: /api/v3/{rest}", {})
