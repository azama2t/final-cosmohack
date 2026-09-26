"""§54 п.1: NASA GIBS «ежедневно» — обзорный слой снимков NASA по датам (не обнаружение пластика).

- GET /api/v3/nasa/layers            -> слои GIBS WMTS (EPSG:3857) с шаблоном URL тайла ({date}, {z}/{y}/{x}),
                                        разрешением, атрибуцией, подписью «что это и чего это не доказывает»
- GET /api/v3/nasa/latest?layer=     -> последняя доступная дата слоя: реальный запрос одного тайла за сегодня / вчера / ...
                                        (UTC), таймаут 5 с, кэш 30 мин; сети нет -> «вчера» + unverified = true
- GET /api/v3/nasa/regions           -> папка «NASA · ежедневно»: те же районы, что в кейсе (bbox/центр по снимкам района)
- GET /api/v3/fresh_s2               -> «свежий Sentinel-2» (доп.): отдельный набор последних снимков, наш детектор,
                                        «автоматически, не проверено человеком» (data/case/fresh_s2, scripts/case/fresh_s2.py)
- GET /api/v3/fresh_s2/{key}/zones   -> GeoJSON зон снимка (не входят в основные зоны кейса)
- GET /api/v3/fresh_s2/{key}/rgb.jpg -> RGB-вырезка снимка (EPSG:4326 по bounds)

Тайлы грузит браузер напрямую с gibs.earthdata.nasa.gov (сервис их не проксирует).
Контракт: docs/CONTRACTS_V3.md, раздел «NASA · ежедневно (§54 п.1)».
"""
from __future__ import annotations

import datetime as dt
import json
import math
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter(tags=["v3-nasa"])
P = "/api/v3/nasa"

GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best"
ATTRIBUTION = ("Снимки: NASA EOSDIS GIBS (Global Imagery Browse Services), "
               "https://earthdata.nasa.gov/gibs")
SOURCE_LABEL = "NASA"
CAPTION = ("ежедневный обзорный снимок NASA 250–375 м; пластик на таком разрешении не обнаруживается — "
           "для обзора облачности/цветения/пятен")
CAPTION_HLS = ("снимок NASA HLS 30 м (Landsat / Sentinel-2, гармонизированный), не каждый день — только в дни пролёта, "
               "в остальные дни тайл пустой; наш детектор на нём не запускается — это обзор, не обнаружение пластика")
NOT_WHAT = "не обнаружение пластика и не оценка количества; наш детектор на этих снимках не запускается"
TODAY_NOTE = ("снимок за текущие сутки (UTC) собирается по мере пролётов спутника — часть Земли может быть ещё пустой; "
              "полный день — latest_full")

# layer id -> description. Checked against GIBS GetCapabilities 26.09.2026: TileMatrixSet, format, time dimension.
LAYERS: dict[str, dict] = {
    "VIIRS_SNPP_CorrectedReflectance_TrueColor": {
        "title": "VIIRS (Suomi NPP) · истинные цвета", "satellite": "Suomi NPP", "sensor": "VIIRS",
        "resolution_m": 375, "matrix_set": "GoogleMapsCompatible_Level9", "max_zoom": 9, "ext": "jpg",
        "format": "image/jpeg", "cadence": "daily", "lookback_days": 3, "caption": CAPTION, "default": True},
    "MODIS_Terra_CorrectedReflectance_TrueColor": {
        "title": "MODIS (Terra) · истинные цвета", "satellite": "Terra", "sensor": "MODIS",
        "resolution_m": 250, "matrix_set": "GoogleMapsCompatible_Level9", "max_zoom": 9, "ext": "jpg",
        "format": "image/jpeg", "cadence": "daily", "lookback_days": 3, "caption": CAPTION, "default": False},
    "MODIS_Aqua_CorrectedReflectance_TrueColor": {
        "title": "MODIS (Aqua) · истинные цвета", "satellite": "Aqua", "sensor": "MODIS",
        "resolution_m": 250, "matrix_set": "GoogleMapsCompatible_Level9", "max_zoom": 9, "ext": "jpg",
        "format": "image/jpeg", "cadence": "daily", "lookback_days": 3, "caption": CAPTION, "default": False},
    # HLS есть в GIBS как тайлы (Level12, PNG с прозрачностью), но не ежедневно: только в дни пролёта над районом
    "HLS_S30_Nadir_BRDF_Adjusted_Reflectance": {
        "title": "HLS S30 (Sentinel-2) · 30 м", "satellite": "Sentinel-2A/B/C", "sensor": "MSI (HLS)",
        "resolution_m": 30, "matrix_set": "GoogleMapsCompatible_Level12", "max_zoom": 12, "ext": "png",
        "format": "image/png", "cadence": "sparse", "lookback_days": 7, "caption": CAPTION_HLS, "default": False},
    "HLS_L30_Nadir_BRDF_Adjusted_Reflectance": {
        "title": "HLS L30 (Landsat) · 30 м", "satellite": "Landsat 8/9", "sensor": "OLI (HLS)",
        "resolution_m": 30, "matrix_set": "GoogleMapsCompatible_Level12", "max_zoom": 12, "ext": "png",
        "format": "image/png", "cadence": "sparse", "lookback_days": 7, "caption": CAPTION_HLS, "default": False},
}
DEFAULT_LAYER = "VIIRS_SNPP_CorrectedReflectance_TrueColor"
PROBE_TILE = (0, 0, 0)  # z, y, x — one global tile: exists for a date iff the date is published in GIBS
PROBE_TIMEOUT_S = 5.0
CACHE_TTL_S = 30 * 60

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def tile_url(layer: str, date: Optional[str] = None) -> str:
    """XYZ template (GoogleMapsCompatible = standard web-mercator XYZ; WMTS order is {z}/{y}/{x})."""
    m = LAYERS[layer]
    return f"{GIBS}/{layer}/default/{date or '{date}'}/{m['matrix_set']}/{{z}}/{{y}}/{{x}}.{m['ext']}"


def _probe(url: str) -> Optional[bool]:
    """True: tile is served (200 image/*); False: GIBS answered but no tile (404/400); None: network failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "macroplastic-case/1.0"})
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_S) as r:
            return r.status == 200 and str(r.headers.get("Content-Type", "")).startswith("image/")
    except urllib.error.HTTPError as e:
        return False if e.code in (400, 404) else None
    except Exception:
        return None


def _today_utc() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def _layer_public(lid: str) -> dict:
    m = LAYERS[lid]
    return {"id": lid, "title": m["title"], "source": SOURCE_LABEL, "satellite": m["satellite"], "sensor": m["sensor"],
            "resolution_m": m["resolution_m"], "crs": "EPSG:3857", "tile_matrix_set": m["matrix_set"],
            "max_native_zoom": m["max_zoom"], "tile_size": 256, "format": m["format"], "cadence": m["cadence"],
            "tile_url": tile_url(lid), "attribution": ATTRIBUTION, "caption": m["caption"], "not_what": NOT_WHAT,
            "default": m["default"]}


def latest(lid: str, *, now: Optional[float] = None) -> dict:
    """Latest published date of a layer (cached 30 min)."""
    now = time.time() if now is None else now
    with _lock:
        hit = _cache.get(lid)
        if hit and now - hit[0] < CACHE_TTL_S:
            return dict(hit[1], cached=True)
    m = LAYERS[lid]
    today = _today_utc()
    z, y, x = PROBE_TILE
    checked, found, net_fail = [], None, False
    for k in range(m["lookback_days"]):
        d = (today - dt.timedelta(days=k)).isoformat()
        ok = _probe(tile_url(lid, d).format(z=z, y=y, x=x))
        checked.append({"date": d, "ok": ok})
        if ok is None:
            net_fail = True
            break
        if ok:
            found = d
            break
    if found:
        fd = dt.date.fromisoformat(found)
        full = found if fd < today else (fd - dt.timedelta(days=1)).isoformat()
        res = {"layer": lid, "date": found, "latest_full": full, "verified": True, "unverified": False,
               "partial": fd >= today, "note": TODAY_NOTE if fd >= today else None}
    else:
        y1 = (today - dt.timedelta(days=1)).isoformat()
        res = {"layer": lid, "date": y1, "latest_full": y1, "verified": False, "unverified": True, "partial": False,
               "note": ("нет ответа от NASA GIBS — дата «вчера» не проверена" if net_fail else
                        f"за последние {m['lookback_days']} сут. тайл не найден — дата «вчера» не проверена")}
    res.update({"today_utc": today.isoformat(), "checked": checked, "tile_url": tile_url(lid, res["date"]),
                "caption": m["caption"], "source": SOURCE_LABEL, "cached": False,
                "checked_at": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(timespec="seconds")})
    if res["verified"] or not net_fail:  # do not cache a network failure for 30 min
        with _lock:
            _cache[lid] = (now, res)
    return res


def _regions() -> list[dict]:
    from . import case_store as cs
    by: dict[str, dict] = {}
    for s in cs.scene_zones_index().get("scenes") or []:
        b = s.get("bounds")
        if s.get("error") or not b or len(b) != 4:
            continue
        r = by.setdefault(s["region"], {"id": s["region"], "label": s.get("region_name") or s["region"],
                                        "short": cs.sz_region_short(s["region"], s.get("region_name")),
                                        "bbox": list(b), "n_scenes": 0})
        r["bbox"] = [min(r["bbox"][0], b[0]), min(r["bbox"][1], b[1]), max(r["bbox"][2], b[2]), max(r["bbox"][3], b[3])]
        r["n_scenes"] += 1
    out = []
    for r in by.values():
        w, s_, e, n = r["bbox"]
        r["bbox"] = [round(v, 5) for v in r["bbox"]]
        r["center"] = [round((w + e) / 2, 5), round((s_ + n) / 2, 5)]
        span = max(e - w, n - s_, 1e-3)
        # zoom that fits the region bbox into ~600 px, capped at the GIBS daily max (9): 250–375 m layers blur above it
        r["zoom"] = max(3, min(9, int(math.log2(360 * 600 / 256 / span))))
        r["n_scenes_s2"] = r.pop("n_scenes")
        out.append(r)
    return sorted(out, key=lambda r: r["short"])


@router.get(P + "/layers", summary="Слои NASA GIBS «ежедневно» (§54 п.1)")
def nasa_layers():
    return {"folder": "NASA · ежедневно", "source": SOURCE_LABEL, "default_layer": DEFAULT_LAYER,
            "caption": CAPTION, "not_what": NOT_WHAT, "attribution": ATTRIBUTION,
            "date_rule": "по умолчанию — последняя доступная дата (/api/v3/nasa/latest); даты — UTC, YYYY-MM-DD",
            "layers": [_layer_public(k) for k in LAYERS]}


@router.get(P + "/latest", summary="Последняя доступная дата слоя NASA GIBS (§54 п.1)")
def nasa_latest(layer: str = Query(DEFAULT_LAYER)):
    if layer not in LAYERS:
        return JSONResponse({"detail": f"unknown layer {layer}", "allowed": list(LAYERS)}, status_code=422)
    return latest(layer)


@router.get(P + "/regions", summary="Папка «NASA · ежедневно»: районы кейса (§54 п.1)")
def nasa_regions():
    return {"folder": "NASA · ежедневно", "source": SOURCE_LABEL, "caption": CAPTION, "default_layer": DEFAULT_LAYER,
            "note": "те же районы, что в кейсе Sentinel-2; bbox — объединение границ снимков района; zoom ≤ 9",
            "regions": _regions()}


# ---------------------------------------------------------------- «свежий Sentinel-2» (§54 п.1, доп.)
FRESH_DIR = Path(__file__).resolve().parents[1] / "data" / "case" / "fresh_s2"
FRESH_SOURCE = "Sentinel-2 L2A (STAC), наш детектор"


def _fresh_index() -> dict:
    p = FRESH_DIR / "index.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _fresh_scene(key: str) -> Optional[dict]:
    return next((s for s in _fresh_index().get("scenes") or [] if s.get("key") == key), None)


@router.get("/api/v3/fresh_s2", summary="Свежий Sentinel-2: отдельный набор, автоматически, не проверено человеком")
def fresh_s2():
    idx = _fresh_index()
    keep = ("key", "region", "region_name", "date", "datetime", "scene_id", "tile", "bounds", "evaluable",
            "not_evaluated_reason", "n_zones_total", "by_status", "cloud_pct", "wind10m_ms", "error")
    scenes = [{k: s.get(k) for k in keep if k in s} | {"rgb_url": f"/api/v3/fresh_s2/{s['key']}/rgb.jpg",
                                                     "zones_url": f"/api/v3/fresh_s2/{s['key']}/zones"}
              for s in idx.get("scenes") or []]
    return {"source": FRESH_SOURCE, "label": idx.get("label", "автоматически, не проверено человеком"),
            "human_checked": False, "note": idx.get("note"), "generated": idx.get("generated"),
            "model": {k: (idx.get("model") or {}).get(k) for k in ("weights", "threshold", "class")},
            "in_case_numbers": False, "scenes": scenes}


@router.get("/api/v3/fresh_s2/{key}/zones", summary="Зоны свежего снимка (не входят в основные зоны кейса)")
def fresh_s2_zones(key: str):
    s = _fresh_scene(key)
    p = FRESH_DIR / key / "zones.geojson"
    if not s or not p.is_file():
        return JSONResponse({"detail": f"unknown fresh scene {key}"}, status_code=404)
    from . import case_store as cs
    fc = json.loads(p.read_text(encoding="utf-8"))
    for f in fc.get("features") or []:
        st = f["properties"].get("detection_status")
        f["properties"].update({"status_label": cs.SZ_STATUS4.get(st, st), "status_note": cs.SZ_STATUS4_NOTE.get(st),
                                "status_reason": ("ветер > 5 м/с: мусор перемешивается, полосы не видны — правило "
                                                  "Cózar 2024" if f["properties"].get("wind_high") else None)})
        f["properties"].update({"layer_kind": "fresh_s2", "auto_label": "автоматически, не проверено человеком",
                                "human_checked": False, "source": FRESH_SOURCE, "in_case_numbers": False})
    fc["label"] = "автоматически, не проверено человеком"
    return fc


@router.get("/api/v3/fresh_s2/{key}/rgb.jpg", summary="RGB свежего снимка")
def fresh_s2_rgb(key: str):
    p = FRESH_DIR / key / "rgb.jpg"
    if not _fresh_scene(key) or not p.is_file():
        return JSONResponse({"detail": f"unknown fresh scene {key}"}, status_code=404)
    return FileResponse(p, media_type="image/jpeg")
