"""§54 п.5 PRIME MODE · синтетика: предсобранные синтетические сцены (scripts/case/prime_scenes.py).

GET /api/prime/scenes          -> data/case/prime/index.json (сцены, правда генератора, метрики с путями к файлам)
GET /api/prime/img/{name}.png  -> data/case/prime/<name>.png
Всё — СИНТЕТИКА, не наблюдение (плашка приходит в поле badge).

§55а (L154) PRIME MODE · демо-данные по строкам CSV организаторов (scripts/case/prime_csv.py demo):
GET /api/prime/csv_scenes            -> data/case/prime_csv/index.json (meta.badge + scenes: строка CSV, координаты,
                                        N / площадь / шт./км² из CSV с подписью «демо-значение», рамки, картинки)
GET /api/prime/csv_scenes?lite=1     -> то же без рамок и списка категорий (для слоя точек на карте)
GET /api/prime/csv_img/{name}.jpg    -> data/case/prime_csv/<name>.jpg (<id>.jpg — кадр, <id>_boxes.jpg — с рамками)
Это ДЕМО-МАКЕТ: изображения и числа демонстрационные, не результат модели; метрик качества нет.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter(tags=["prime"])
PRIME = Path(__file__).resolve().parents[1] / "data" / "case" / "prime"
PRIME_CSV = Path(__file__).resolve().parents[1] / "data" / "case" / "prime_csv"
_CSV_CACHE: dict = {}


@router.get("/api/prime/scenes")
def prime_scenes():
    f = PRIME / "index.json"
    if not f.is_file():
        raise HTTPException(404, "нет data/case/prime/index.json — запустите scripts/case/prime_scenes.py")
    return JSONResponse(json.loads(f.read_text(encoding="utf-8")))


@router.get("/api/prime/img/{name}.png")
def prime_img(name: str):
    if not re.fullmatch(r"[a-z0-9_]{1,64}", name):
        raise HTTPException(404, "bad name")
    f = PRIME / f"{name}.png"
    if not f.is_file():
        raise HTTPException(404, "no image")
    return FileResponse(f, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


def _csv_index():
    f = PRIME_CSV / "index.json"
    if not f.is_file():
        raise HTTPException(404, "нет data/case/prime_csv/index.json — запустите scripts/case/prime_csv.py demo")
    m = f.stat().st_mtime
    if _CSV_CACHE.get("mtime") != m:
        _CSV_CACHE.update(mtime=m, data=json.loads(f.read_text(encoding="utf-8")))
    return _CSV_CACHE["data"]


@router.get("/api/prime/csv_scenes")
def prime_csv_scenes(lite: int = 0):
    d = _csv_index()
    if not lite:
        return JSONResponse(d)
    drop = {"boxes", "categories"}
    return JSONResponse({"meta": d["meta"], "scenes": [{k: v for k, v in s.items() if k not in drop} for s in d["scenes"]]})


@router.get("/api/prime/csv_img/{name}.jpg")
def prime_csv_img(name: str):
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", name):
        raise HTTPException(404, "bad name")
    f = PRIME_CSV / f"{name}.jpg"
    if not f.is_file():
        raise HTTPException(404, "no image")
    return FileResponse(f, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})
