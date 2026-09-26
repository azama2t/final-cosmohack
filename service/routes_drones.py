"""§54 п.2 (L151): «Дроны» — detailed frames (drone / aircraft / vessel), NOT satellite.

GET /api/v3/drones                      -> sets: name, sensor, region, coordinates (if any), licence, link, frame count,
                                           labelled class groups (plastic / algae / wood / other — only if labelled)
GET /api/v3/drones/{set}/frames         -> frames: image URL, boxes/polygons (normalised 0..1, x/y/w/h), classes,
                                           items per frame by the labels, шт./м² and шт./км² ONLY with a known frame
                                           area (else "площадь кадра неизвестна"), our counter's prediction only where
                                           its metrics were checked (FML, Winans)
GET /api/v3/drones/{set}/img/{frame}.jpg -> preview (<= 200 KB)

Data: data/case/drones/index.json + data/case/drones/img/ (built by scripts/case/drones_index.py; in git).
Registered automatically by service/app.py (every service/routes_*.py with `router`).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "data" / "case" / "drones"
INDEX = DIR / "index.json"
BANNER = "дрон, не спутник — со спутника считаются зоны, не отдельные предметы"
_ID = re.compile(r"^[a-z0-9_]{1,40}$")

router = APIRouter(prefix="/api/v3", tags=["v3-drones"])
_cache: dict = {}


def _index() -> dict:
    if not INDEX.is_file():
        raise HTTPException(503, {"code": "NO_INDEX", "message": "нет data/case/drones/index.json — "
                                                                  "запустите scripts/case/drones_index.py"})
    m = INDEX.stat().st_mtime
    if _cache.get("m") != m:
        _cache.update(m=m, d=json.loads(INDEX.read_text(encoding="utf-8")))
    return _cache["d"]


def _set(set_id: str) -> dict:
    if not _ID.match(set_id):
        raise HTTPException(404, {"code": "NO_SET", "message": f"набора «{set_id}» нет"})
    for s in _index()["sets"]:
        if s["meta"]["id"] == set_id:
            return s
    raise HTTPException(404, {"code": "NO_SET", "message": f"набора «{set_id}» нет"})


@router.get("/drones", summary="Дроны и детальные кадры: наборы (дрон / самолёт / судно — не спутник)")
def drones_sets():
    d = _index()
    return {"banner": d.get("banner", BANNER), "groups": d["groups"], "catalog": d.get("catalog"),
            "built": d.get("built"), "not_included": d.get("not_included", []),
            "sets": [{**s["meta"], "frames_url": f"/api/v3/drones/{s['meta']['id']}/frames",
                      "cover": s["frames"][0]["image"] if s["frames"] else None} for s in d["sets"]]}


@router.get("/drones/{set_id}/frames", summary="Кадры набора: изображение, рамки/полигоны, классы, число, шт./м² при известной площади")
def drones_frames(set_id: str):
    s = _set(set_id)
    d = _index()
    return {"set": s["meta"], "banner": d.get("banner", BANNER), "groups": d["groups"], "frames": s["frames"]}


@router.get("/drones/{set_id}/img/{name}", summary="Превью кадра (JPEG ≤ 200 КБ)")
def drones_img(set_id: str, name: str):
    _set(set_id)
    if not re.match(r"^[a-z0-9_]{1,60}\.jpg$", name):
        raise HTTPException(404, "нет кадра")
    p = DIR / "img" / set_id / name
    if not p.is_file():
        raise HTTPException(404, "нет кадра")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
