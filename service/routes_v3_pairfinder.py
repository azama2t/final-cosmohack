"""L71: POST /api/v3/pairfinder — есть ли синхронная (с учётом дрейфа) сцена Sentinel-2/Landsat для нового или
планируемого полевого наблюдения. Логика: src/macroplastic/case/pairfinder.py.

Body (JSON):
  geometry       GeoJSON Point/LineString | {lon, lat} | {lon_start, lat_start, lon_end, lat_end}   (обязательно)
  datetime       "YYYY-MM-DDTHH:MM:SSZ" или "YYYY-MM-DD" (дата без времени -> time_known=false)     (обязательно)
  window_days    окно поиска ±сут (0..15, по умолчанию 5)
  drift_scenario low|typical|high (configs/case_pairs.yaml) ИЛИ speed_ms (м/с, > 0)
  tolerance_km   допуск (по умолчанию ширина/2 + 3 км); width_m, wind_ms, max_cloud — необязательно
  quality        true -> маска качества в полосе для 1–3 лучших кандидатов (сеть; кэш data/pairs/pairfinder)
Ответ 200: {sync_window, candidates[], best, count, n_synchronous, empty_reason, ...}; пустой результат — 200 + empty_reason.
Ошибки: {"error": {"code", "message", "details"}}, 400 (не JSON) / 422 (некорректные значения).
Офлайн (только кэш STAC data/pairs/cache): MACROPLASTIC_PAIRFINDER_OFFLINE=1.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
from macroplastic.case import pairfinder as pf  # noqa: E402

router = APIRouter(prefix="/api/v3", tags=["v3-pairfinder"])
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"}


def _error(status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "details": details or {}}},
                        status_code=status, headers=CORS)


@router.options("/pairfinder", include_in_schema=False)
def pairfinder_options():
    return Response(status_code=204, headers=CORS)


@router.post("/pairfinder", summary="Поиск синхронной сцены S2/Landsat для полевого наблюдения (с учётом дрейфа)")
async def pairfinder(request: Request):
    raw = await request.body()
    if not raw.strip():
        return _error(400, "BAD_PARAM", "Пустое тело запроса: нужен JSON с geometry и datetime",
                      {"example": {"geometry": {"type": "Point", "coordinates": [30.93, 43.07]},
                                   "datetime": "2024-06-02T09:00:00Z"}})
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as e:
        return _error(400, "BAD_PARAM", "Тело запроса — не JSON", {"parse_error": str(e)[:120]})
    try:
        cfg = pf.load_cfg()
        q = pf.parse_request(body, cfg)
        res = await run_in_threadpool(pf.find, q, cfg)
    except pf.PairfinderError as e:
        return _error(e.status, e.code, e.message, e.details)
    except Exception as e:  # noqa: BLE001
        return _error(500, "INTERNAL", "Внутренняя ошибка поиска пар", {"type": type(e).__name__,
                                                                       "trace": traceback.format_exc()[-400:]})
    return JSONResponse(res, headers=CORS)
