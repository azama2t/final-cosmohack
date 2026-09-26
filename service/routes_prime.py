"""§54 п.5 PRIME MODE · синтетика: предсобранные синтетические сцены (scripts/case/prime_scenes.py).

GET /api/prime/scenes          -> data/case/prime/index.json (сцены, правда генератора, метрики с путями к файлам)
GET /api/prime/img/{name}.png  -> data/case/prime/<name>.png
Всё — СИНТЕТИКА, не наблюдение (плашка приходит в поле badge).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter(tags=["prime"])
PRIME = Path(__file__).resolve().parents[1] / "data" / "case" / "prime"


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
