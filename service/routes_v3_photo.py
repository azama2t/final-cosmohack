"""API v3 «Счётчик предметов по фото» (INBOX §15 «Агент 5», L109) — docs/CONTRACTS_V3.md, раздел 3.10.

GET  /api/v3/photo/meta     модель, версия весов, порог, метрики (FML test), ограничения
POST /api/v3/photo/count    тело = байты изображения (image/jpeg|png|webp; или multipart/form-data, поле file)
                            ?threshold=0..1  (по умолчанию — порог, выбранный на val FML)
                            ?frame_area_m2=  (площадь водной поверхности в кадре, м²; только если известна)
                            -> рамки [x1,y1,x2,y2] в пикселях исходного фото, score, число предметов на кадр

Отдельный модуль, НЕ спутниковая задача: камера у воды (надводный аппарат, вид от первого лица).
Результат — ШТУКИ НА КАДР. Шт./км² — только если передана известная площадь кадра (подпись «на площади кадра,
не спутник»); у FML GSD/площади кадра нет. Дроны и спутник — не проверено.
Нет весов (weights_exp/photo_count/model_card.json) -> 503 MODEL_UNAVAILABLE. Ошибки — {"error": {...}}.
GET-маршруты вставляются в router routes_v3 перед его GET catch-all (как routes_v3_oil); routes_v3.py не меняется.
"""
from __future__ import annotations

import io
import sys
import threading
import time
import traceback
from pathlib import Path

from fastapi import APIRouter, Request

from . import routes_v3 as v3
from .case_store import ApiError

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

MAX_BYTES = 25 * 1024 * 1024
MAX_SIDE = 8000
SURVEY = "камера у воды (надводный аппарат, вид от первого лица)"
LIMITATIONS = [
    "Тип съёмки — камера у воды (надводный аппарат, вид от первого лица, 1920×1080); на похожих фото.",
    "Результат — число предметов на кадр. Для шт./км² нужна известная площадь водной поверхности в кадре.",
    "Дроны (вид сверху) и спутник — не проверено.",
    "Один класс «мусор» (garbage): не различает пластик/дерево/водоросли по типу материала.",
    "Официальный test FML — соседние кадры видео (~90 % в пределах 2 с от кадра train): метрики оптимистичны; "
    "честная оценка — на отложенных сессиях съёмки (см. docs/PHOTO_COUNT.md).",
]
DENSITY_NOTE = "на площади кадра, не спутник"

router = APIRouter(prefix="/api/v3", tags=["v3-photo"])
_photo = APIRouter(prefix="/api/v3", tags=["v3-photo"])
_lock = threading.Lock()


def _card():
    from macroplastic.photo_count.model import load_card
    return load_card()


def _meta_obj():
    card = _card()
    return {
        "module": "photo_count",
        "task_note": "отдельный модуль, не решение спутниковой задачи",
        "survey": SURVEY,
        "available": card is not None,
        "model": None if card is None else {
            "version": card.get("version"), "architecture": card.get("architecture"),
            "threshold": card.get("threshold"), "threshold_rule": card.get("threshold_rule"),
            "dataset": card.get("dataset"), "source": card.get("source"),
            "weights_license": card.get("weights_license"),
            "metrics": {k: card[k] for k in ("test_official", "test_grouped") if k in card},
            "caveat": card.get("caveat"),
        },
        "unit": "штук на кадр",
        "density_note": DENSITY_NOTE,
        "limitations": LIMITATIONS,
        "upload": {"method": "POST", "path": "/api/v3/photo/count",
                   "body": "байты изображения (Content-Type image/*) или multipart/form-data с полем file",
                   "max_bytes": MAX_BYTES, "params": ["threshold", "frame_area_m2"]},
    }


def _wrap(fn):
    import functools

    @functools.wraps(fn)
    async def inner(request: Request):
        try:
            return await fn(request)
        except ApiError as e:
            return v3._err(e)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return v3._err(ApiError(500, "INTERNAL", "Внутренняя ошибка сервера", {"type": type(e).__name__}))
    return inner


def _check_params(request: Request, allowed: set):
    bad = sorted(set(request.query_params) - allowed)
    if bad:
        raise ApiError(400, "BAD_PARAM", f"Неизвестные параметры: {', '.join(bad)}",
                       {"unknown": bad, "allowed": sorted(allowed)})


def _float_param(request, name, lo=None, hi=None):
    v = request.query_params.get(name)
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except ValueError:
        raise ApiError(400, "BAD_PARAM", f"{name} должно быть числом", {"param": name, "value": v}) from None
    if (lo is not None and x < lo) or (hi is not None and x > hi) or x != x:
        raise ApiError(400, "BAD_PARAM", f"{name} вне допустимого диапазона", {"param": name, "value": v,
                                                                              "min": lo, "max": hi})
    return x


def _extract_image_bytes(body: bytes, ctype: str) -> bytes:
    """Raw image body, or the first file part of multipart/form-data (python-multipart is not installed)."""
    if ctype.startswith("multipart/form-data"):
        import email.parser
        import email.policy
        msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
            b"Content-Type: " + ctype.encode("latin-1") + b"\r\n\r\n" + body)
        for part in msg.iter_parts():
            if part.get_filename() or part.get_param("name", header="content-disposition") in ("file", "image"):
                data = part.get_payload(decode=True)
                if data:
                    return data
        raise ApiError(400, "BAD_BODY", "В multipart нет файла (поле file)", {})
    return body


@_photo.get("/photo/meta", summary="Счётчик предметов по фото: модель, порог, метрики, ограничения")
async def photo_meta(request: Request):
    try:
        _check_params(request, set())
        return v3._ok(_meta_obj())
    except ApiError as e:
        return v3._err(e)


@_photo.post("/photo/count", summary="Загрузить фото -> рамки найденных предметов и их число на кадр")
@_wrap
async def photo_count(request: Request):
    _check_params(request, {"threshold", "frame_area_m2"})
    thr = _float_param(request, "threshold", 0.0, 1.0)
    area = _float_param(request, "frame_area_m2", 1e-6, 1e12)
    body = await request.body()
    if not body:
        raise ApiError(400, "BAD_BODY", "Пустое тело запроса: пришлите изображение", {})
    if len(body) > MAX_BYTES:
        raise ApiError(413, "TOO_LARGE", "Файл больше 25 МБ", {"max_bytes": MAX_BYTES})
    data = _extract_image_bytes(body, request.headers.get("content-type", ""))
    from PIL import Image, ImageOps
    try:
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im).convert("RGB")
    except Exception:  # noqa: BLE001
        raise ApiError(415, "BAD_IMAGE", "Не удалось прочитать изображение (нужен JPEG/PNG/WebP)", {}) from None
    if max(im.size) > MAX_SIDE:
        raise ApiError(413, "TOO_LARGE", f"Сторона изображения больше {MAX_SIDE} px", {"size": list(im.size)})
    try:
        from macroplastic.photo_count.model import Counter, density_per_km2
        with _lock:
            t0 = time.time()
            c = Counter.get()
            boxes, scores, used = c.count(im, thr)
            ms = (time.time() - t0) * 1000
    except FileNotFoundError:
        raise ApiError(503, "MODEL_UNAVAILABLE", "Нет весов счётчика (weights_exp/photo_count/model_card.json); "
                       "см. docs/PHOTO_COUNT.md", {}) from None
    n = int(len(boxes))
    dens = density_per_km2(n, area)
    out = {
        "count": n,
        "unit": "штук на кадр",
        "threshold": used,
        "threshold_default": c.threshold,
        "model_version": c.card.get("version"),
        "image": {"width": im.size[0], "height": im.size[1]},
        "boxes": [{"x1": round(float(b[0]), 1), "y1": round(float(b[1]), 1), "x2": round(float(b[2]), 1),
                   "y2": round(float(b[3]), 1), "score": round(float(s), 4), "label": "мусор"}
                  for b, s in zip(boxes, scores)],
        "density": None if dens is None else {"items_per_km2": dens, "frame_area_m2": area, "note": DENSITY_NOTE},
        "density_reason": None if dens is not None else "площадь кадра не задана — только штуки на кадр",
        "survey": SURVEY,
        "limitations": LIMITATIONS,
        "device": c.device,
        "elapsed_ms": round(ms, 1),
    }
    return v3._ok(out)


# ------------------------------------------------------------------ mount before the v3 GET catch-all
def _splice() -> None:
    vr = v3.router
    if getattr(vr, "_photo_spliced", False):
        return
    n0 = len(vr.routes)
    pre = vr.prefix or ""
    for r in _photo.routes:
        vr.add_api_route(r.path[len(pre):], r.endpoint, methods=sorted(r.methods), summary=r.summary,
                         tags=["v3-photo"], name=r.name)
    new = vr.routes[n0:]
    del vr.routes[n0:]
    idx = next((i for i, r in enumerate(vr.routes) if getattr(r, "path", "") in ("/api/v3/{rest:path}", "/{rest:path}")
                and "GET" in (getattr(r, "methods", None) or set())), len(vr.routes))
    vr.routes[idx:idx] = new
    if hasattr(vr, "_mark_routes_changed"):
        vr._mark_routes_changed()
    vr._photo_spliced = True


_splice()
