"""FastAPI app of the macroplastic service: JSON API + data files + SPA frontend.

Run:  .venv\\Scripts\\python.exe -m service [--port 8000] [--data-root PATH]
Data root: --data-root / $MACROPLASTIC_DATA / service/data / service/demo / service/demo_fixtures.
Contracts: docs/CONTRACTS.md. Endpoints: see create_app().
"""
from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.middleware.gzip import GZipMiddleware

from . import core, pdf, place, review

STATIC = core.SERVICE_DIR / "static"
VERSION = "0.1.0"
mimetypes.add_type("application/geo+json", ".geojson")
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/webp", ".webp")

NO_FRONT_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Макропластик — фронт не собран</title>
<style>body{font-family:Inter,Segoe UI,sans-serif;background:#0b1622;color:#dfe8f1;max-width:760px;margin:60px auto;padding:0 20px}
code{background:#13263a;padding:2px 6px;border-radius:4px}a{color:#5fd0ff}</style></head><body>
<h1>Фронт не собран</h1>
<p>Соберите интерфейс: <code>cd service\\frontend &amp;&amp; npm ci &amp;&amp; npm run build</code> — сборка ляжет в <code>service\\static</code>.</p>
<p>API уже работает: <a href="/health">/health</a>, <a href="/api/regions">/api/regions</a>, <a href="/docs">/docs</a>.</p>
</body></html>"""


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise core.BadRequest("ожидается JSON-объект") from None
    if not isinstance(body, dict):
        raise core.BadRequest("ожидается JSON-объект")
    return body


def create_app(data_root: Optional[str | Path] = None) -> FastAPI:
    explicit = data_root
    state = {"store": core.Store.open(explicit)}

    def store() -> core.Store:
        st = state["store"]
        if not st.has_data() and not explicit:  # data may appear later (generation finished)
            st = core.Store.open(None)
            state["store"] = st
        return st

    app = FastAPI(title="Макропластик — API", version=VERSION,
                  description="Обнаружение плавающего мусора по Sentinel-2. Индекс — доля наблюдаемой воды "
                              "с признаками мусора (‰), по снимку; не масса и не концентрация пластика.")
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.exception_handler(core.NotFound)
    async def _nf(request: Request, exc: core.NotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(core.BadRequest)
    async def _bad(request: Request, exc: core.BadRequest):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    def no_data_headers(st: core.Store) -> dict:
        return {} if st.has_data() else {"X-Data-Hint": "no data; see /health hint"}

    # ------------------------------------------------------------ service
    @app.get("/health", tags=["service"])
    def health():
        st = store()
        out = {"status": "ok", "version": VERSION, "data_kind": st.data_kind(),
               "data_root": str(st.root) if st.root else None, "regions": len(st.regions()),
               "frontend_built": (STATIC / "index.html").is_file()}
        if not st.has_data():
            out["hint"] = core.GEN_HINT
        return out

    # ------------------------------------------------------------ API
    @app.get("/api/manifest", tags=["data"], summary="manifest.json как есть")
    def manifest():
        st = store()
        return JSONResponse(st.manifest(), headers=no_data_headers(st))

    @app.get("/api/regions", tags=["data"], summary="Список регионов (короткие записи)")
    def regions():
        st = store()
        out = [{"id": r.get("id"), "name": r.get("name"), "country": r.get("country"),
                "center": r.get("center"), "bounds": r.get("bounds"), "zoom": r.get("zoom"),
                "summary": r.get("summary"),
                "dates": sorted(d.get("date") for d in r.get("dates", []) if d.get("date"))}
               for r in st.regions()]
        return JSONResponse(out, headers=no_data_headers(st))

    @app.get("/api/region/{rid}", tags=["data"], summary="Запись региона из manifest + timeseries")
    def region(rid: str):
        st = store()
        r = dict(st.region(rid))
        r["timeseries"] = st.timeseries(rid)
        return r

    @app.get("/api/compare", tags=["analysis"], summary="Сравнение KPI двух районов/дат")
    def compare(a: str = Query(..., description="region[:YYYY-MM-DD]", examples=["honduras:2025-10-19"]),
                b: str = Query(..., description="region[:YYYY-MM-DD]", examples=["durban"]),
                model: Optional[str] = Query("mdd")):
        return store().compare(a, b, model)

    @app.get("/api/kpi", tags=["analysis"], summary="KPI одного района/даты (как половина /api/compare)")
    def kpi(region: str, date: Optional[str] = None, model: Optional[str] = "mdd"):
        return store().kpi(region, date, model)

    @app.get("/api/zones", tags=["data"], summary="zones.json — приоритет обследования")
    def zones(region: str, date: Optional[str] = None, model: Optional[str] = "mdd"):
        st = store()
        rid, d, m = st.resolve(region, date, model)
        return st.layer(rid, d, m, "zones")

    @app.get("/api/timeseries", tags=["data"], summary="Ряд по датам (фильтр по модели)")
    def timeseries(region: str, model: Optional[str] = None):
        return store().timeseries(region, model)

    @app.get("/api/export", tags=["export"], summary="Выгрузка слоя GeoJSON/CSV")
    def export(region: str, date: Optional[str] = None, model: Optional[str] = "mdd",
               layer: str = Query("detections", pattern="^(detections|h3|zones)$"),
               format: str = Query("geojson", pattern="^(geojson|csv)$")):
        st = store()
        rid, d, m = st.resolve(region, date, model)
        obj = st.layer(rid, d, m, layer)
        stem = f"{rid}_{d}_{m}_{layer}"
        if format == "csv":
            body = "﻿" + core.to_csv(layer, obj)  # BOM: Excel opens Cyrillic correctly
            return Response(body.encode("utf-8"), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{stem}.csv"'})
        if layer == "zones":
            obj = core.zones_geojson(obj)
        return Response(json.dumps(obj, ensure_ascii=False).encode("utf-8"), media_type="application/geo+json",
                        headers={"Content-Disposition": f'attachment; filename="{stem}.geojson"'})

    # ------------------------------------------------------------ L19: zones, place, calendar, review
    jobs = review.Jobs()

    @app.get("/api/zone", tags=["analysis"], summary="Зона приоритета обследования + «почему» (формула)")
    def zone(region: str, h3: str, date: Optional[str] = None, model: Optional[str] = "mdd"):
        return place.zone_info(store(), region, date, model, h3)

    @app.get("/api/crop", tags=["analysis"], summary="PNG-вырезка снимка вокруг точки с контурами пятен")
    def crop(region: str, lon: float, lat: float, date: Optional[str] = None,
             size_m: float = Query(1500, ge=200, le=10000), highlight: int = Query(1, ge=0, le=1),
             bands: str = Query("rgb", pattern="^(rgb|false|swir)$"), model: Optional[str] = None,
             px: int = Query(480, ge=128, le=1024), src: str = Query("auto", pattern="^(auto|bands|png)$"),
             h3: Optional[str] = None):
        body = place.crop_png(store(), region, date, lon, lat, size_m, bool(highlight), bands, model, px, src, h3)
        return Response(body, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/api/place", tags=["analysis"], summary="Карточка места (ячейка H3): история по всем датам")
    def place_card(region: str, h3: str, model: Optional[str] = None):
        return place.place_info(store(), region, h3, model)

    @app.get("/api/place_report.pdf", tags=["export"], summary="Справка по месту, PDF (2 стр.)")
    def place_report(region: str, h3: str, model: Optional[str] = None, date: Optional[str] = None):
        body = pdf.build_place_pdf(store(), region, h3, model, date, VERSION)
        name = f"place_{region}_{h3}.pdf"
        return Response(body, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/calendar", tags=["analysis"], summary="Календарь реальных наблюдений района")
    def calendar(region: str, model: Optional[str] = None):
        return place.calendar(store(), region, model)

    @app.get("/api/review/queue", tags=["review"], summary="Очередь сомнительных находок")
    def review_queue(region: str, model: Optional[str] = None, limit: int = Query(50, ge=1, le=1000),
                     include_labeled: int = Query(0, ge=0, le=1)):
        return review.queue(store(), region, model, limit, bool(include_labeled))

    @app.post("/api/review/label", tags=["review"], summary="Сохранить метку человека")
    async def review_label(request: Request):
        body = await _json_body(request)
        return review.add_record(store(), body, "label", VERSION)

    @app.post("/api/review/flag", tags=["review"], summary="Отметить находку «ложное» (в очередь проверки)")
    async def review_flag(request: Request):
        body = await _json_body(request)
        return review.add_record(store(), body, "flag_false", VERSION)

    @app.get("/api/review/labels", tags=["review"], summary="Все метки (jsonl как список)")
    def review_labels():
        return {"path": str(review.labels_path()), "items": review.read_labels()}

    @app.post("/api/review/retrain", tags=["review"], summary="Дообучить LightGBM на метках (фон)")
    def review_retrain():
        return jobs.start()

    @app.get("/api/review/retrain/{job}", tags=["review"], summary="Статус дообучения")
    def review_retrain_status(job: str):
        return jobs.get(job)

    @app.get("/api/{rest:path}", include_in_schema=False)
    def api_404(rest: str):
        return JSONResponse({"detail": f"нет такого эндпоинта: /api/{rest}"}, status_code=404)

    # ------------------------------------------------------------ files
    @app.get("/data/{path:path}", tags=["files"], summary="Файлы корня данных (png, geojson, json)")
    def data_file(path: str):
        st = store()
        if st.root is None:
            raise core.NotFound("нет данных")
        p = core.safe_path(st.root, path)
        headers = {}
        if p.suffix.lower() in (".png", ".webp", ".jpg", ".jpeg", ".tif"):
            headers["Cache-Control"] = "public, max-age=86400"
        else:
            headers["Cache-Control"] = "no-cache"
        return FileResponse(p, headers=headers)

    @app.get("/assets/{path:path}", include_in_schema=False)
    def assets(path: str):
        p = core.safe_path(STATIC / "assets", path)
        return FileResponse(p, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path:  # real file in static root (favicon.svg, robots.txt, ...)
            try:
                return FileResponse(core.safe_path(STATIC, path))
            except core.NotFound:
                pass
        index = STATIC / "index.html"
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return HTMLResponse(NO_FRONT_HTML)

    return app


app = create_app()
