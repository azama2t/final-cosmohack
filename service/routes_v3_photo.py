"""API v3 «Счётчик предметов по фото» (INBOX §15 «Агент 5», L109) — docs/CONTRACTS_V3.md, раздел 3.10.

GET  /api/v3/photo/meta     модель, версия весов, порог, метрики (FML test), ограничения
POST /api/v3/photo/count    тело = байты изображения (image/jpeg|png|webp; или multipart/form-data, поле file)
                            ?threshold=0..1  (по умолчанию — порог, выбранный на val FML)
                            ?frame_area_m2=  (площадь водной поверхности в кадре, м²; только если известна)
                            ?survey=water_camera|aerial  (тип съёмки; aerial — надир с дрона/самолёта, модель Winans 2023)
                            ?gsd_m=  (размер пикселя на земле, м; для aerial -> площадь кадра = W*H*GSD^2)
                            -> рамки [x1,y1,x2,y2] в пикселях исходного фото, score, число предметов на кадр
                               (+ для aerial: шт./км² на площади кадра, сырое и с поправкой на пропуски p(размер), интервал)

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
SURVEY_LABELS = {
    "water_camera": SURVEY,
    "aerial": "аэро/дрон, надир (вид сверху), берег; модель на Winans 2023, GSD 2 см; шт. на 164 м² кадра берега",
}
LIMITATIONS = [
    "Тип съёмки — камера у воды (надводный аппарат, вид от первого лица, 1920×1080); на похожих фото.",
    "Результат — число предметов на кадр. Для шт./км² нужна известная площадь водной поверхности в кадре.",
    "Дроны и спутник — не проверено для подсчёта; проверка переноса на дрон-надир (Maharjan 2022) без дообучения: "
    "mAP@0,5 ≈ 0,01 — не работает.",
    "Один класс «мусор» (garbage): не различает пластик/дерево/водоросли по типу материала.",
    "Официальный test FML — соседние кадры видео (~90 % в пределах 2 с от кадра train): метрики оптимистичны; "
    "честная оценка — на отложенных сессиях съёмки (см. docs/PHOTO_COUNT.md).",
]
LIMITATIONS_AERIAL = [
    "Тип съёмки — аэро/дрон, надир; обучено на берегах Гавайев (Winans 2023, GSD 0,02 м, кадр 640×640 = 163,8 м²).",
    "Берег ≠ вода: на плавающем мусоре эта модель не проверялась.",
    "Шт./км² — на площади кадра (W·H·GSD²; у Winans 640×640 × 0,02 м = 163,84 м² — это чип берега: песок, вода, "
    "растительность), не спутник: Sentinel-2 (10 м) так не считает — предметы меньше пикселя.",
    "Число на кадр точнее, чем кажется по рамкам: пропуски и ложные срабатывания частично гасят друг друга "
    "(отложенный test: 2,1 пропуска и 1,8 ложных на кадр при ошибке числа 1,85).",
    "Поправка на пропуски p(размер) оценена на отложенных кадрах val при пороге по умолчанию; при другом пороге — только сырое число.",
    "Серии перекрывающихся кадров не сшиваются: считаем по одиночному кадру (при съёмке серией плотность завышается).",
    "При GSD вне 0,01–0,04 м результат не проверен.",
]
DENSITY_NOTE = "на площади кадра, не спутник"

router = APIRouter(prefix="/api/v3", tags=["v3-photo"])
_photo = APIRouter(prefix="/api/v3", tags=["v3-photo"])
_lock = threading.Lock()


def _card(survey="water_camera"):
    from macroplastic.photo_count.model import load_card
    return load_card(survey)


def _composition(survey, boxes, card):
    """Class composition (L123, INBOX §30 п.4): only if macroplastic.labels.composition_for exists and says so."""
    try:
        from macroplastic.labels import composition_for  # type: ignore
        return composition_for(survey, boxes, card)
    except Exception:  # noqa: BLE001 - module absent or not ready -> honest default
        return {"status": "not_determined", "text": "всего предметов, состав не определён",
                "reason": "модели материала с проверкой на отложенном источнике нет"}


def _count_interval(card, n):
    """95 % interval of the true count for a frame with n found items: quantiles of (true - found) on VAL frames,
    coverage checked on TEST (model card "count_interval")."""
    ci = (card or {}).get("count_interval")
    if not ci:
        return None
    row = next((r for r in ci["by_pred_count"] if r["pred_from"] <= n <= r["pred_to"]), None)
    lo, hi = (row["q025"], row["q975"]) if row else tuple(ci["overall_val"])
    return {"interval": [max(0.0, n + lo), max(0.0, n + hi)], "coverage_on_test": ci.get("coverage_on_test"),
            "method": ci.get("method")}


def _headline(card):
    t = (card or {}).get("test_grouped")
    if not t:
        return None
    return {"count_mae_per_frame": t["count_mae"], "count_mae_ci95": t.get("count_mae_ci95"),
            "n_images": t["n_images"], "test": "FML, отложенные сессии съёмки (независимый тест)",
            "baseline_median_mae": (card.get("baseline_median_mae_test") if card else None),
            "text": f"Счётчик предметов по фото: ошибка {t['count_mae']:.2f} шт./кадр на независимом тесте".replace(".", ",")}


def _meta_obj():
    card = _card()
    aer = _card("aerial")
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
            "metrics": {**{k: card[k] for k in ("test_official", "test_grouped") if k in card},
                        **({"test_official": card["reference_published_weights"]["test_official"]}
                           if "test_official" in card.get("reference_published_weights", {}) else {})},
            "reference": card.get("reference_published_weights"),
            "caveat": card.get("caveat"),
        },
        "surveys": {
            "water_camera": {"label": SURVEY_LABELS["water_camera"], "available": card is not None,
                             "version": (card or {}).get("version"), "threshold": (card or {}).get("threshold"),
                             "count_interval": (card or {}).get("count_interval"), "limitations": LIMITATIONS},
            "aerial": {"label": SURVEY_LABELS["aerial"], "available": aer is not None,
                       "version": (aer or {}).get("version"), "threshold": (aer or {}).get("threshold"),
                       "gsd_train_m": (aer or {}).get("gsd_train_m"), "dataset": (aer or {}).get("dataset"),
                       "weights_license": (aer or {}).get("weights_license"),
                       "metrics": (aer or {}).get("test_spatial"), "correction": (aer or {}).get("correction"),
                       "baseline": (aer or {}).get("baseline"), "count_interval": (aer or {}).get("count_interval"),
                       "limitations": LIMITATIONS_AERIAL},
        },
        "headline": _headline(card),
        "composition_rule": "разбивка по материалу — только при размеченном классе и матрице ошибок на отложенном "
                            "источнике (INBOX §30 п.4, L123); иначе «всего предметов, состав не определён»",
        "unit": "штук на кадр",
        "density_note": DENSITY_NOTE,
        "limitations": LIMITATIONS,
        "upload": {"method": "POST", "path": "/api/v3/photo/count",
                   "body": "байты изображения (Content-Type image/*) или multipart/form-data с полем file",
                   "max_bytes": MAX_BYTES, "params": ["threshold", "frame_area_m2", "survey", "gsd_m"]},
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
    _check_params(request, {"threshold", "frame_area_m2", "survey", "gsd_m"})
    thr = _float_param(request, "threshold", 0.0, 1.0)
    area = _float_param(request, "frame_area_m2", 1e-6, 1e12)
    gsd = _float_param(request, "gsd_m", 1e-4, 10.0)
    survey = request.query_params.get("survey") or "water_camera"
    if survey not in SURVEY_LABELS:
        raise ApiError(400, "BAD_PARAM", "survey: water_camera | aerial", {"param": "survey", "value": survey,
                                                                          "allowed": sorted(SURVEY_LABELS)})
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
        from macroplastic.photo_count.model import Counter, corrected_count, density_per_km2
        with _lock:
            t0 = time.time()
            c = Counter.get(survey)
            boxes, scores, used = c.count(im, thr)
            ms = (time.time() - t0) * 1000
    except FileNotFoundError:
        raise ApiError(503, "MODEL_UNAVAILABLE", f"Нет весов счётчика для «{survey}» (weights_exp/photo_count/); "
                       "см. docs/PHOTO_COUNT.md", {"survey": survey}) from None
    n = int(len(boxes))
    W, H = im.size
    area_src = "frame_area_m2" if area is not None else None
    if area is None and gsd is not None:
        area, area_src = W * H * gsd * gsd, "gsd_m"
    dens = density_per_km2(n, area)
    density = None
    if dens is not None:
        density = {"items_per_km2": dens, "frame_area_m2": area, "area_from": area_src, "note": DENSITY_NOTE}
        corr = c.card.get("correction") if survey == "aerial" else None
        if corr and gsd is not None and abs(used - c.threshold) < 1e-9:
            k = corrected_count(boxes, gsd, corr["factor"])
            lo = corrected_count(boxes, gsd, [x[0] for x in corr["factor_ci95"]])
            hi = corrected_count(boxes, gsd, [x[1] for x in corr["factor_ci95"]])
            density.update({"corrected_count": round(k, 2), "items_per_km2_corrected": density_per_km2(k, area),
                            "items_per_km2_interval": [density_per_km2(lo, area), density_per_km2(hi, area)],
                            "correction_note": "поправка на пропуски p(размер) по отложенным кадрам val; "
                                               "интервал — неопределённость p (бутстреп), без счётной ошибки кадра"})
    at_default = abs(used - c.threshold) < 1e-9
    cint = _count_interval(c.card, n) if at_default else None
    if density is not None and cint is not None:
        density["items_per_km2_count_interval"] = [density_per_km2(cint["interval"][0], area),
                                                   density_per_km2(cint["interval"][1], area)]
        density["items_per_m2"] = n / area
    gsd_warn = None
    if survey == "aerial":
        g0 = c.card.get("gsd_train_m", 0.02)
        if gsd is None:
            gsd_warn = "GSD не задан — только штуки на кадр"
        elif not (g0 / 2 <= gsd <= g0 * 2):
            gsd_warn = f"GSD {gsd} м вне проверенного диапазона ({g0 / 2}–{g0 * 2} м): результат не проверен"
    out = {
        "count": n,
        "unit": "штук на кадр",
        "threshold": used,
        "threshold_default": c.threshold,
        "model_version": c.card.get("version"),
        "survey_type": survey,
        "image": {"width": W, "height": H},
        "boxes": [{"x1": round(float(b[0]), 1), "y1": round(float(b[1]), 1), "x2": round(float(b[2]), 1),
                   "y2": round(float(b[3]), 1), "score": round(float(s), 4), "label": "мусор"}
                  for b, s in zip(boxes, scores)],
        "count_interval": cint,
        "composition": _composition(survey, boxes, c.card),
        "gsd_m": gsd,
        "gsd_warning": gsd_warn,
        "density": density,
        "density_reason": None if density is not None else "площадь кадра не задана — только штуки на кадр",
        "survey": SURVEY_LABELS[survey],
        "limitations": LIMITATIONS if survey == "water_camera" else LIMITATIONS_AERIAL,
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
