"""§54 п.1: NASA GIBS «ежедневно» — обзорный слой снимков NASA по датам (не обнаружение пластика).

- GET /api/v3/nasa/layers            -> слои GIBS WMTS (EPSG:3857) с шаблоном URL тайла ({date}, {z}/{y}/{x}),
                                        разрешением, атрибуцией, подписью «что это и чего это не доказывает»
- GET /api/v3/nasa/latest?layer=     -> последняя доступная дата слоя: реальный запрос одного тайла за сегодня / вчера / ...
                                        (UTC), таймаут 5 с, кэш 30 мин; сети нет -> «вчера» + unverified = true
- GET /api/v3/nasa/regions           -> папка «NASA · ежедневно»: те же районы, что в кейсе (bbox/центр по снимкам района)
- GET /api/v3/fresh_s2               -> папка «Реальное время» (§55 п.1): новые снимки S2 за 30 сут., наш детектор;
                                        regions → dates → zones_url; scenes_30d / zones_30d / finds_30d, last_update,
                                        new_scenes_last_run; «автоматически, не проверено человеком»
                                        (data/case/fresh_s2, scripts/case/live_batch.py)
- POST /api/v3/fresh_s2/refresh      -> фоновая обработка новых снимков за --days (409, если уже идёт)
- GET /api/v3/fresh_s2/{key}/crops/{name} -> вырезка зоны (jpg)
- GET /api/v3/fresh_s2/{key}/zones   -> GeoJSON зон снимка (не входят в основные зоны кейса)
- GET /api/v3/fresh_s2/{key}/rgb.jpg -> RGB-вырезка снимка (EPSG:4326 по bounds)

- GET /api/v3/nasa/tile/{layer}/{date}/{z}/{y}/{x}.{ext} -> §60 А2: тайл GIBS через НАШ сервер (тот же домен, без VPN):
                                        только слои из LAYERS, дата не позже сегодня (UTC) и не раньше года назад,
                                        z ≤ max_zoom слоя; дисковый кэш data_cache/nasa_tiles (≤ 500 МБ, старые удаляются),
                                        таймаут 8 с, Cache-Control, атрибуция в заголовке X-Attribution.
tile_url в /layers и /latest — путь прокси (относительный, тот же домен); прямой адрес GIBS — tile_url_gibs.
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


def gibs_url(layer: str, date: Optional[str] = None) -> str:
    """Direct GIBS XYZ template (GoogleMapsCompatible = standard web-mercator XYZ; WMTS order is {z}/{y}/{x})."""
    m = LAYERS[layer]
    return f"{GIBS}/{layer}/default/{date or '{date}'}/{m['matrix_set']}/{{z}}/{{y}}/{{x}}.{m['ext']}"


def tile_url(layer: str, date: Optional[str] = None) -> str:
    """§60 А2: the template the browser uses — our proxy on the same domain (relative path)."""
    m = LAYERS[layer]
    return f"{P}/tile/{layer}/{date or '{date}'}/{{z}}/{{y}}/{{x}}.{m['ext']}"


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
            "tile_url": tile_url(lid), "tile_url_gibs": gibs_url(lid), "proxied": True, "attribution": ATTRIBUTION, "caption": m["caption"], "not_what": NOT_WHAT,
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
        ok = _probe(gibs_url(lid, d).format(z=z, y=y, x=x))
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
                "tile_url_gibs": gibs_url(lid, res["date"]),
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


# ---------------------------------------------------------------- §60 А2: GIBS tile proxy (same domain, no VPN)
TILE_CACHE = Path(__file__).resolve().parents[1] / "data_cache" / "nasa_tiles"
TILE_CACHE_MAX_BYTES = 500 * 1024 * 1024
TILE_TIMEOUT_S = 8.0
TILE_MAX_AGE_DAYS = 365
ATTRIBUTION_HDR = "Imagery: NASA EOSDIS GIBS (Global Imagery Browse Services), https://earthdata.nasa.gov/gibs"
_tile_lock = threading.Lock()
_tile_sem = threading.BoundedSemaphore(8)  # upstream requests at once
_tile_bytes: dict = {"total": None}


def _fetch_tile(url: str) -> tuple[int, Optional[bytes], str]:
    """-> (status, body, content_type); status 0 = network failure/timeout."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "macroplastic-case/1.0 (tile proxy)"})
        with urllib.request.urlopen(req, timeout=TILE_TIMEOUT_S) as r:
            return r.status, r.read(), str(r.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as e:
        return e.code, None, ""
    except Exception:
        return 0, None, ""


def _cache_total() -> int:
    if _tile_bytes["total"] is None:
        _tile_bytes["total"] = sum(f.stat().st_size for f in TILE_CACHE.rglob("*") if f.is_file()) if TILE_CACHE.exists() else 0
    return _tile_bytes["total"]


def _evict() -> None:
    """Keep the disk cache ≤ TILE_CACHE_MAX_BYTES: delete the oldest tiles (mtime) down to 90 % of the limit."""
    files = sorted((f for f in TILE_CACHE.rglob("*") if f.is_file()), key=lambda f: f.stat().st_mtime)
    total = sum(f.stat().st_size for f in files)
    for f in files:
        if total <= TILE_CACHE_MAX_BYTES * 0.9:
            break
        try:
            sz = f.stat().st_size
            f.unlink()
            total -= sz
        except OSError:
            pass
    _tile_bytes["total"] = total


def _tile_headers(date: dt.date, today: dt.date, hit: bool) -> dict:
    # a past day is final in GIBS; today (UTC) is still being filled by new overpasses -> short cache
    age = 1800 if date >= today else 7 * 86400
    return {"Cache-Control": f"public, max-age={age}", "X-Attribution": ATTRIBUTION_HDR,
            "X-Source": "NASA GIBS via our server", "X-Cache": "HIT" if hit else "MISS"}


def _tile_error(code: int, msg: str) -> JSONResponse:
    return JSONResponse({"detail": msg}, status_code=code, headers={"Cache-Control": "no-store"})


@router.get(P + "/tile/{layer}/{date}/{z}/{y}/{name}", summary="Тайл NASA GIBS через наш сервер (§60 А2): кэш, без VPN")
def nasa_tile(layer: str, date: str, z: int, y: int, name: str):
    from fastapi.responses import Response
    m = LAYERS.get(layer)
    if m is None:
        return _tile_error(404, f"слой не разрешён: {layer}")
    stem, _, ext = name.partition(".")
    if ext != m["ext"] or not stem.isdigit():
        return _tile_error(404, f"ожидается {{x}}.{m['ext']}")
    x = int(stem)
    try:
        d = dt.date.fromisoformat(date)
    except ValueError:
        return _tile_error(422, "дата — YYYY-MM-DD")
    today = _today_utc()
    if d > today or d < today - dt.timedelta(days=TILE_MAX_AGE_DAYS):
        return _tile_error(422, f"дата вне диапазона {today - dt.timedelta(days=TILE_MAX_AGE_DAYS)} … {today} (UTC)")
    if not (0 <= z <= m["max_zoom"]) or not (0 <= x < 2 ** z and 0 <= y < 2 ** z):
        return _tile_error(422, f"z ≤ {m['max_zoom']}, 0 ≤ x, y < 2^z")
    p = TILE_CACHE / layer / d.isoformat() / str(z) / str(y) / f"{x}.{ext}"
    if p.is_file():
        return FileResponse(p, media_type=m["format"], headers=_tile_headers(d, today, True))
    with _tile_sem:
        status, body, ctype = _fetch_tile(gibs_url(layer, d.isoformat()).format(z=z, y=y, x=x))
    if status == 0:
        return _tile_error(504, "NASA GIBS не ответил за 8 с")
    if status != 200 or not body or not ctype.startswith("image/"):
        return _tile_error(404 if status in (200, 400, 404) else 502, f"NASA GIBS: нет тайла (HTTP {status})")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".part")
        tmp.write_bytes(body)
        tmp.replace(p)
        with _tile_lock:
            _tile_bytes["total"] = _cache_total() + len(body)
            if _tile_bytes["total"] > TILE_CACHE_MAX_BYTES:
                _evict()
    except OSError:
        pass  # cache is best-effort; the tile is still served
    return Response(body, media_type=ctype.split(";")[0] or m["format"], headers=_tile_headers(d, today, False))


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


FRESH_LABEL = "автоматически, не проверено человеком"
FRESH_FOLDER = "Реальное время"
FRESH_SUBFOLDER = "Sentinel-2 · обработано нашей моделью"
FRESH_HONESTY = {
    "detector_on": "Sentinel-2 L2A, 10 м — каждый новый снимок района (новый снимок каждого места раз в 2–5 дней)",
    "nasa": ("NASA MODIS/VIIRS (250–375 м на пиксель) — только ежедневный обзор (облака, цветение, пятна). Наш детектор "
             "обучен на Sentinel-2 10 м; на пикселе 250–375 м скопления физически не видны, поэтому модель на кадрах "
             "NASA не запускаем — это было бы подделкой"),
    "hls": "NASA HLS 30 м — модель на нём не запускается (не сделано; было бы только экспериментом: модель обучена на 10 м)",
    "quantity": "количества (шт., масса, шт./км²) по спутниковому снимку нет",
    "class": "класс детектора — любой плавающий материал, не только пластик",
}
ROOT_DIR = Path(__file__).resolve().parents[1]
REFRESH_LOCK = ROOT_DIR / "data_cache" / "fresh_s2" / "refresh.lock"
REFRESH_LOG = ROOT_DIR / "out" / "live_batch" / "refresh_api.log"
_refresh_lock = threading.Lock()
_refresh_state: dict = {"proc": None, "started": None}


def _scene_public(s: dict) -> dict:
    keep = ("key", "region", "region_name", "date", "datetime", "scene_id", "tile", "bounds", "evaluable",
            "not_evaluated_reason", "n_zones_total", "by_status", "n_zones", "n_finds", "whole_crop_status",
            "cloud_pct", "crop_cloud_frac", "wind10m_ms", "wind_high", "processed_at", "processing_s", "error")
    d = {k: s.get(k) for k in keep if k in s}
    d.update({"rgb_url": f"/api/v3/fresh_s2/{s['key']}/rgb.jpg", "zones_url": f"/api/v3/fresh_s2/{s['key']}/zones",
              "label": FRESH_LABEL, "human_checked": False, "in_case_numbers": False,
              "source": s.get("source") or FRESH_SOURCE, "mission": "Sentinel-2"})
    try:  # возраст снимка: старые снимки не называть «сегодня»
        age = (_today_utc() - dt.date.fromisoformat(str(s.get("date"))[:10])).days
        d["age_days"] = age
        d["age_label"] = "снят сегодня" if age == 0 else "снят вчера" if age == 1 else f"снят {age} сут. назад"
    except Exception:
        pass
    return d


def _fresh_tree(scenes: list[dict]) -> list[dict]:
    """районы → даты (свежие первыми); зоны — по zones_url каждой даты."""
    by: dict[str, dict] = {}
    for s in scenes:
        r = by.setdefault(s["region"], {"id": s["region"], "label": s.get("region_name") or s["region"],
                                        "n_scenes": 0, "n_zones": 0, "n_finds": 0, "last_date": None, "bbox": None,
                                        "dates": []})
        r["n_scenes"] += 1
        r["n_zones"] += int(s.get("n_zones") or 0)
        r["n_finds"] += int(s.get("n_finds") or 0)
        r["last_date"] = max(r["last_date"] or "", s.get("date") or "") or None
        b = s.get("bounds")
        if b and len(b) == 4:
            r["bbox"] = list(b) if not r["bbox"] else [min(r["bbox"][0], b[0]), min(r["bbox"][1], b[1]),
                                                     max(r["bbox"][2], b[2]), max(r["bbox"][3], b[3])]
        r["dates"].append(s)
    for r in by.values():
        r["dates"].sort(key=lambda d: d.get("date") or "", reverse=True)
        if r["bbox"]:
            r["center"] = [round((r["bbox"][0] + r["bbox"][2]) / 2, 5), round((r["bbox"][1] + r["bbox"][3]) / 2, 5)]
    return sorted(by.values(), key=lambda r: r["label"])


def _funnel_label(f: Optional[dict]) -> Optional[str]:
    if not f:
        return None
    zs = f.get("zone_status") or {}
    s = (f"{f.get('window_label')}: найдено {f['found']} → скачано {f['downloaded']} → исключено "
         f"{f.get('excluded', f['found'] - f['passed_quality'])} → обработано {f['processed']}")
    if f.get("pending"):
        s += f" (ещё в очереди {f['pending']})"
    s += f" → снимков с находками {f['with_finds']} → находок {f.get('finds', 0)}"
    if zs:
        s += (f" → зон «не обнаружено» {zs.get('not_detected', 0)} / «недостаточно данных» "
              f"{zs.get('insufficient_data', 0)}")
    return s + "; не проверено человеком"


@router.get("/api/v3/fresh_s2/catalog", summary="Реальное время: КАЖДЫЙ найденный снимок S2 — пригодность, причина, статус")
def fresh_s2_catalog(region: Optional[str] = None, status: Optional[str] = None):
    p = FRESH_DIR / "catalog.json"
    cat = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"scenes": []}
    rows = [e for e in cat.get("scenes") or [] if (not region or e.get("region") == region)
            and (not status or e.get("status") == status)]
    for e in rows:
        if e.get("key"):
            e["zones_url"] = f"/api/v3/fresh_s2/{e['key']}/zones"
    idx = _fresh_index()
    return {"generated": cat.get("generated"), "source": cat.get("source"), "status_labels": cat.get("status_labels"),
            "funnel": idx.get("funnel"), "search_window": idx.get("search_window"), "n": len(rows), "scenes": rows}


def refresh_running() -> bool:
    p = _refresh_state.get("proc")
    return bool(p is not None and p.poll() is None)


@router.get("/api/v3/fresh_s2", summary="Реальное время: Sentinel-2, обработано нашей моделью (не проверено человеком)")
def fresh_s2():
    idx = _fresh_index()
    scenes = [_scene_public(s) for s in idx.get("scenes") or []]
    cut = (_today_utc() - dt.timedelta(days=30)).isoformat()
    s30 = [s for s in scenes if (s.get("date") or "") >= cut and not s.get("error")]
    n30, z30, f30 = len(s30), sum(s.get("n_zones") or 0 for s in s30), sum(s.get("n_finds") or 0 for s in s30)
    return {"folder": FRESH_FOLDER, "subfolder": FRESH_SUBFOLDER, "source": FRESH_SOURCE,
            "stac": "Earth Search (поиск сцен) + Planetary Computer (пиксели L2A)",
            "label": FRESH_LABEL, "human_checked": False, "in_case_numbers": False, "note": idx.get("note"),
            "honesty": FRESH_HONESTY, "generated": idx.get("generated"),
            "last_update": idx.get("last_update") or idx.get("generated"),
            "last_success": idx.get("last_success"), "last_success_trigger": idx.get("last_success_trigger"),
            "last_success_label": (f"последняя успешная обработка: {idx['last_success'][:16].replace('T', ' ')} UTC"
                                   if idx.get("last_success") else "успешной обработки ещё не было"),
            "new_scenes_last_run": idx.get("new_scenes_last_run"),
            "search_window": idx.get("search_window") or {"days": 30, "label": "поиск за: 1 мес"},
            "funnel": idx.get("funnel"), "funnel_30d": idx.get("funnel_30d"),
            "funnel_label": _funnel_label(idx.get("funnel")),
            "catalog_url": "/api/v3/fresh_s2/catalog",
            "scenes_30d": n30, "zones_30d": z30, "finds_30d": f30, "regions_30d": len({s["region"] for s in s30}),
            "counter": f"за 30 дней обработано {n30} снимков, найдено {z30} зон, из них находок {f30}",
            "counter_note": ("зона ≠ случай мусора: большинство зон — «недостаточно данных» (ветер > 5 м/с, облака, "
                             "блик) или «не обнаружено»; находка — только статус «обнаружено», не проверено человеком"),
            "counting": idx.get("counting"),
            "refresh": {"running": refresh_running(), "endpoint": "POST /api/v3/fresh_s2/refresh",
                        "daily": "Планировщик Windows, задача MacroplasticLiveDaily → scripts/live_daily.ps1"},
            "model": {k: (idx.get("model") or {}).get(k) for k in ("weights", "threshold", "class", "sha256_short")},
            "regions": _fresh_tree(scenes), "scenes": scenes}


def _spawn(cmd: list, log, env: dict):
    import subprocess
    return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=str(ROOT_DIR))


@router.post("/api/v3/fresh_s2/refresh", summary="Обновить «Реальное время»: новые снимки за последние дни (фоном)")
def fresh_s2_refresh(days: int = Query(3, ge=1, le=30)):
    """Запускает scripts/case/live_batch.py фоном. Параллельный запуск запрещён: процесс этого сервиса ещё идёт или
    lock-файл другого запуска (ежедневная задача, скрипт) моложе 3 ч -> 409."""
    import os
    import sys
    with _refresh_lock:
        if refresh_running():
            return JSONResponse({"started": False, "running": True, "detail": "обновление уже идёт",
                                 "since": _refresh_state.get("started")}, status_code=409)
        if REFRESH_LOCK.is_file() and time.time() - REFRESH_LOCK.stat().st_mtime < 3 * 3600:
            return JSONResponse({"started": False, "running": True,
                                 "detail": "обновление уже идёт (другой запуск: ежедневная задача или скрипт)"},
                                status_code=409)
        REFRESH_LOG.parent.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-W", "ignore", str(ROOT_DIR / "scripts" / "case" / "live_batch.py"), "--days",
               str(days), "--workers", "2", "--threads", "4", "--trigger", "api"]
        env = dict(os.environ, CUDA_VISIBLE_DEVICES="")
        log = open(REFRESH_LOG, "a", encoding="utf-8")
        _refresh_state["proc"] = _spawn(cmd, log, env)
        _refresh_state["started"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    return {"started": True, "running": True, "since": _refresh_state["started"], "days": days,
            "detail": "фоновая обработка новых снимков запущена; результат — GET /api/v3/fresh_s2 (last_update)"}


@router.get("/api/v3/fresh_s2/{key}/crops/{name}", summary="Вырезка зоны свежего снимка (jpg)")
def fresh_s2_crop(key: str, name: str):
    p = FRESH_DIR / key / "crops" / name
    if not _fresh_scene(key) or "/" in name or "\\" in name or ".." in name or not p.is_file():
        return JSONResponse({"detail": "not found"}, status_code=404)
    return FileResponse(p, media_type="image/jpeg")


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
