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
PRED = DIR / "pred.json"  # §56: our photo counter on every frame (scripts/case/drones_pred.py)
BANNER = "дрон, не спутник — со спутника считаются зоны, не отдельные предметы"
_ID = re.compile(r"^[a-z0-9_]{1,40}$")
# §60 Г: sets grouped by region (only what the set itself says; no invented places)
REGION_GROUP = {
    "tun_marinelitter": "Африка · Тунис",
    "martin2021": "Ближний Восток · Красное море (Саудовская Аравия)",
    "maharjan2022": "Юго-Восточная Азия · Лаос и Таиланд",
    "winans2023": "Океания · Гавайи (США)",
    "ucwd": "Регион не указан в наборе",
    "fml": "Регион не указан в наборе",
}

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


def _pred() -> dict:
    if not PRED.is_file():
        return {}
    m = PRED.stat().st_mtime
    if _cache.get("pm") != m:
        _cache.update(pm=m, p=json.loads(PRED.read_text(encoding="utf-8")))
    return _cache["p"]


def _model_meta(set_id: str):
    p = _pred().get("sets", {}).get(set_id)
    if not p:
        return None
    return {k: p[k] for k in ("survey", "model", "threshold", "iou", "trained_on_this_set", "training_note",
                              "checked_metric", "summary")}


def _frame_out(meta: dict, f: dict, pset: dict | None) -> dict:
    out = {k: v for k, v in f.items() if k not in ("src", "prediction")}
    out["sensor"], out["sensor_label"] = meta["sensor"], meta["sensor_label"]
    out.setdefault("date", None)  # only real dates (Martin: survey date of the beach; FML: time in the file name)
    if not out["date"]:
        out["date"], out["date_note"] = None, "дата не указана"
    out["model"] = (pset or {}).get("frames", {}).get(f["id"])
    return out


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
            "model": {k: _pred().get(k) for k in ("classes", "match_rule", "total", "built")} if _pred() else None,
            "sets": [{**s["meta"], "region_group": REGION_GROUP.get(s["meta"]["id"], "Регион не указан в наборе"),
                      "model": _model_meta(s["meta"]["id"]), "frames_url": f"/api/v3/drones/{s['meta']['id']}/frames",
                      "cover": s["frames"][0]["image"] if s["frames"] else None} for s in d["sets"]]}


@router.get("/drones/{set_id}/frames", summary="Кадры набора: изображение, рамки/полигоны, классы, число, шт./м² при известной площади")
def drones_frames(set_id: str):
    s = _set(set_id)
    d = _index()
    pset = _pred().get("sets", {}).get(set_id)
    return {"set": {**s["meta"], "model": _model_meta(set_id)}, "banner": d.get("banner", BANNER), "groups": d["groups"],
            "frames": [_frame_out(s["meta"], f, pset) for f in s["frames"]]}


@router.get("/drones/{set_id}/img/{name}", summary="Превью кадра (JPEG ≤ 200 КБ)")
def drones_img(set_id: str, name: str):
    _set(set_id)
    if not re.match(r"^[a-z0-9_]{1,60}\.jpg$", name):
        raise HTTPException(404, "нет кадра")
    p = DIR / "img" / set_id / name
    if not p.is_file():
        raise HTTPException(404, "нет кадра")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


ITEM_COLS = ["set", "frame", "source_file", "sensor", "date", "who", "n", "class_src", "group", "x", "y", "w", "h",
             "score", "matched", "frame_area_m2", "license"]


@router.get("/drones/{set_id}/frames/{frame_id}/items",
            summary="§60 Г: выгрузка предметов кадра (CSV / JSON): разметка набора и наша модель — отдельными строками")
def drones_items(set_id: str, frame_id: str, format: str = "json"):
    from fastapi.responses import Response
    s = _set(set_id)
    f = next((x for x in s["frames"] if x["id"] == frame_id), None)
    if f is None:
        raise HTTPException(404, {"code": "NO_FRAME", "message": f"кадра «{frame_id}» нет"})
    meta = s["meta"]
    fo = _frame_out(meta, f, _pred().get("sets", {}).get(set_id))
    base = {"set": set_id, "frame": frame_id, "source_file": f["source_file"], "sensor": meta["sensor_label"],
            "date": fo["date"] or "дата не указана", "frame_area_m2": f["frame_area_m2"], "license": meta["license"]}
    rows = []
    for i, o in enumerate(f["objects"] or []):
        x, y, w, h = o["bbox"]
        rows.append({**base, "who": "разметка набора", "n": i + 1, "class_src": o["cls"], "group": o["group"],
                     "x": x, "y": y, "w": w, "h": h, "score": None, "matched": None})
    m = fo.get("model")
    if m:
        mt = m.get("match")
        for i, (b, sc) in enumerate(zip(m["boxes"], m["scores"])):
            rows.append({**base, "who": "наша модель", "n": i + 1, "class_src": "предмет (модель без классов)",
                         "group": None, "x": b[0], "y": b[1], "w": b[2], "h": b[3], "score": sc,
                         "matched": (bool(mt[i]) if mt else None)})
    fn = f"drones_{set_id}_{frame_id}_items"
    if format == "csv":
        import csv
        import io
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=ITEM_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{fn}.csv"'})
    if format != "json":
        raise HTTPException(422, "format = json | csv")
    mm = _model_meta(set_id) or {}
    return {"set": set_id, "frame": frame_id, "banner": BANNER, "coords": "доли кадра 0..1 (x, y — левый верхний угол)",
            "who_note": "«разметка набора» — авторы набора; «наша модель» — наш счётчик по фото (одна категория, без материала)",
            "n_labelled": f["n_objects"], "n_model": (m or {}).get("n"), "threshold": mm.get("threshold"),
            "iou": mm.get("iou"), "items": rows}
