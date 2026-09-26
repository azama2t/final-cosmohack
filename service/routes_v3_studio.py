"""API v3 «студия» (§11 п.6, L95): сцены с реальными каналами и их виды — docs/CONTRACTS_V3.md, раздел 3.8.

GET /api/v3/studio/scenes?bbox=&date_from=&date_to=&source=&level=&view=&limit=&offset=
GET /api/v3/studio/scenes/{scene_id}
GET /api/v3/studio/scenes/{scene_id}/view/{kind}.png?px=&variant=     kind = rgb | spectral | detection | quality

Сцены (только каталоги с файлами растров; всё читается с диска, сеть не нужна):
  pair.<dir>              data/pairs/quality/<dir>/ (pair_quality.py: rgb.png, quality.tif, prob.tif, mask.png, meta.json)
                          + FDI из тех же вырезок S2 L2A: data/pairs/experiment/<dir>/fdi.npy (pairs_experiment.py)
  live.<region>.<date>    data/live/<region>/<date>/ (bands.tif 12 каналов L2A, scl.tif, scene.json); детектор и маска качества —
                          ТЕКУЩИЙ режим из out/studio_cache/detector_current (scripts/case/studio_detector_current.py);
                          prob_lgbm.tif/prob_mdd.tif (прежний режим, отвергнут) как детекция не показываются
                          + service/data/<region>/<date>/ (подготовленные слои; rgb.png в EPSG:4326)
  drift.<region>.<date>   data/drift_check/<region>/<date>/ (тот же формат, что live)
  search.<src>.<name>     data/search/<src>/**/<name>/ — любой каталог с meta.json|scene.json и хотя бы одним растром
Виды строятся ТОЛЬКО из файлов сцены; нет файла → вида нет (views[kind].available=false + reason; PNG → 404 NO_VIEW).
Уровень доказательности A–D — из data/search/*/candidates.csv (поле level; ключ event_id+scene_id, затем scene_id), иначе null.

Маршруты вставляются в router routes_v3 ПЕРЕД его catch-all GET /api/v3/{rest:path} (иначе он перехватывает
/api/v3/studio/*: загрузчик app.py подключает routes_v3 раньше routes_v3_studio). routes_v3.py/app.py не меняются.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import Response

from . import case_store as cs

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
from macroplastic.indices import fdi as _fdi  # noqa: E402  (Biermann 2020, the same function as pairs_experiment.py)
from macroplastic.grid import cloudmask  # noqa: E402
from scipy import ndimage  # noqa: E402
from . import routes_v3 as v3
from .case_store import ApiError

REPO = Path(__file__).resolve().parents[1]
ROOTS: dict[str, Path] = {  # tests monkeypatch these
    "pairs": REPO / "data" / "pairs" / "quality",
    "pairs_fdi": REPO / "data" / "pairs" / "experiment",
    "live": REPO / "data" / "live",
    "service": REPO / "service" / "data",
    "drift": REPO / "data" / "drift_check",
    "search": REPO / "data" / "search",
    "cache": Path(os.environ.get("MACROPLASTIC_STUDIO_CACHE") or (REPO / "out" / "studio_cache")),
    # current-mode detector (weights/lgbm, no harmonization) on scenes with raw channels:
    # scripts/case/studio_detector_current.py -> <kind>/<region>/<date>/{prob.tif, det.tif, quality.tif, det.json}
    "det_current": REPO / "out" / "studio_cache" / "detector_current",
}
LEGACY_NOTE = "прежний режим (веса lgbm_live + гармонизация water_median), отвергнут — docs/DECISIONS.md, 25.09 22:40"
QREASON_RU = {"cloud": "облачность в полосе", "glint": "солнечные блики", "insufficient_coverage": "снимок не покрывает полосу",
              "land": "суша в полосе", "low_valid_water": "мало пригодной воды", "error": "ошибка обработки"}
DECISION_RU = {"accept": "принят масками качества", "reject": "отклонён масками качества"}
# Detector is «не оценивается» (numbers are noise, not debris) when the sun is low or the water signal is too weak.
# Chosen on the studio scenes (out/l95_frac.py, reports/tasklog/95_studio_api.md): every scene with a mass response
# (> 130 ppm of usable water, up to 7575 «objects») outside the coastal Guanabara crops has sun zenith 59.7–61.2°
# (ADIS 30.10.2021, 17.11.2020, 25.11.2023); below 58° the maximum is 160 ppm. Weak signal: median B3 of usable water
# < 0.003 (ADIS 30.10.2021: 0.0001 — the water is black after L2A correction).
SUN_ZENITH_MAX = 58.0
WATER_B3_MIN = 0.003

KINDS = [
    {"id": "rgb", "label": "Снимок", "overlay": False},
    {"id": "spectral", "label": "Спектральный", "overlay": False},
    {"id": "detection", "label": "Детекция", "overlay": True},
    {"id": "quality", "label": "Качество", "overlay": True},
]
KIND_IDS = [k["id"] for k in KINDS]
SOURCE_TYPES = [
    {"id": "pair", "label": "Снимок пары кейса"},
    {"id": "live", "label": "Сцена района"},
    {"id": "drift", "label": "Проверка дрейфа"},
    {"id": "search", "label": "Розыск"},
]
SOURCE_TYPE_IDS = [s["id"] for s in SOURCE_TYPES]
LEVELS = [
    {"id": "A", "label": "Снимок ↔ полевое число"},
    {"id": "B", "label": "Подтверждённая разметка"},
    {"id": "C", "label": "Кандидат"},
    {"id": "D", "label": "Отвергнут / отрицательный"},
]
LEVEL_IDS = [x["id"] for x in LEVELS]
PX_DEFAULT, PX_MIN, PX_MAX = 1024, 64, 4096
SEARCH_SKIP = {"cache", "src", "src_csv", "code", "__pycache__"}
S2_BANDS = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"]
RGB_STRETCH = (0.0, 0.16, 1 / 1.8)  # as data/live rgb.json note: 0..0.16 reflectance, gamma 1/1.8
STUDIO_VERSION = "3.8.3"  # 3.8.2: illumination gate, field count, water-stretched RGB

P_SCENES = {"bbox", "date_from", "date_to", "source", "level", "view", "limit", "offset"}
P_SCENE: set = set()
P_VIEW = {"px", "variant"}
ALIASES = {"sources": "source", "levels": "level", "views": "view"}

# no module-level `router`: the routes live in routes_v3.router (see _splice); app.py includes only modules with `router`
_studio = APIRouter(prefix="/api/v3/studio", tags=["v3-studio"])


# ------------------------------------------------------------------ errors / params
def _ok(obj, status: int = 200):
    return v3._ok(obj, status)


def _check(request: Request, allowed: set, where: str) -> dict:
    q = dict(request.query_params.items())
    out = {}
    for k, val in q.items():
        name = ALIASES.get(k, k) if ALIASES.get(k) in allowed else k
        if name != k and name in q:
            raise ApiError(400, "BAD_PARAM", f"{k} и {name} — один и тот же параметр, укажите один",
                           {"param": k, "alias_of": name})
        out[name] = val
    unknown = sorted(k for k in out if k not in allowed)
    if unknown:
        raise ApiError(400, "BAD_PARAM", f"{where}: неизвестные параметры {', '.join(unknown)}",
                       {"unknown": unknown, "allowed": sorted(allowed),
                        "aliases": {a: n for a, n in ALIASES.items() if n in allowed}})
    return out


def _guard(fn):
    import functools
    import traceback

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except ApiError as e:
            return v3._err(e)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return v3._err(ApiError(500, "INTERNAL", "Внутренняя ошибка сервера", {"type": type(e).__name__}))
    return wrapper


# ------------------------------------------------------------------ small cached readers
_lock = threading.Lock()
_memo: dict = {}


def _sig(paths) -> tuple:
    out = []
    for p in paths:
        try:
            st = Path(p).stat()
            out.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((str(p), None, None))
    return tuple(out)


def _cached(key, paths, loader, extra=None):
    s = (_sig(paths), extra)
    with _lock:
        hit = _memo.get(key)
        if hit is not None and hit[0] == s:
            return hit[1]
    val = loader()
    with _lock:
        _memo[key] = (s, val)
    return val


def _json(p: Path) -> Optional[dict]:
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _grid_of(p: Path) -> Optional[dict]:
    """Raster grid (crs, transform, width, height, band names) from a GeoTIFF header."""
    def load():
        try:
            import rasterio
            with rasterio.open(p) as ds:
                return {"crs": ds.crs.to_string() if ds.crs else None, "transform": list(ds.transform)[:6],
                        "width": ds.width, "height": ds.height, "count": ds.count,
                        "descriptions": list(ds.descriptions or [])}
        except Exception as e:  # noqa: BLE001
            print(f"[studio] cannot read grid {p}: {e}")
            return None
    return _cached(("grid", str(p)), [p], load)


def _npy_shape(p: Path) -> Optional[tuple]:
    def load():
        try:
            return tuple(np.load(p, mmap_mode="r").shape)
        except Exception:  # noqa: BLE001
            return None
    return _cached(("npyshape", str(p)), [p], load)


def _png_size(p: Path) -> Optional[tuple]:
    def load():
        try:
            from PIL import Image
            with Image.open(p) as im:
                return im.size
        except Exception:  # noqa: BLE001
            return None
    return _cached(("pngsize", str(p)), [p], load)


def _corners(grid: dict) -> Optional[dict]:
    """Corners (tl, tr, br, bl) in lon/lat + bounds [w, s, e, n] of a north-up raster grid."""
    try:
        a, b, c, d, e, f = grid["transform"]
        w, h = grid["width"], grid["height"]
        pts = [(c + a * x + b * y, f + d * x + e * y) for x, y in ((0, 0), (w, 0), (w, h), (0, h))]
        crs = grid.get("crs") or "EPSG:4326"
        if crs.upper() not in ("EPSG:4326", "OGC:CRS84"):
            from pyproj import Transformer
            tr = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
            pts = [tr.transform(x, y) for x, y in pts]
        pts = [[round(float(x), 6), round(float(y), 6)] for x, y in pts]
        lons, lats = [p[0] for p in pts], [p[1] for p in pts]
        px_m = abs(a) if crs.upper() not in ("EPSG:4326", "OGC:CRS84") else None
        return {"coordinates": pts, "bounds": [min(lons), min(lats), max(lons), max(lats)], "pixel_m": px_m}
    except Exception as ex:  # noqa: BLE001
        print(f"[studio] corners: {ex}")
        return None


def _platform(pid: str) -> tuple[Optional[str], Optional[str]]:
    s = (pid or "").upper()
    if s.startswith("S2A"):
        return "Sentinel-2", "Sentinel-2A"
    if s.startswith("S2B"):
        return "Sentinel-2", "Sentinel-2B"
    if s.startswith("S2C"):
        return "Sentinel-2", "Sentinel-2C"
    if s.startswith("LC08"):
        return "Landsat-8", "Landsat-8"
    if s.startswith("LC09"):
        return "Landsat-9", "Landsat-9"
    if s.startswith("S1"):
        return "Sentinel-1", s[:3].replace("S1", "Sentinel-1")
    m = cs.mission_of(pid)
    return m, m


def _catalog(src: str) -> tuple[Optional[str], Optional[str]]:
    """'earth-search/sentinel-2-l2a' -> (earth-search, sentinel-2-l2a); 'planetary-computer' -> (pc, None)."""
    if not src:
        return None, None
    if "/" in src:
        a, b = src.split("/", 1)
        return a or None, b or None
    return src, None


# ------------------------------------------------------------------ evidence levels (data/search/*/candidates.csv)
def _levels_index() -> dict:
    files = sorted(ROOTS["search"].glob("*/candidates.csv")) if ROOTS["search"].is_dir() else []

    def load():
        by_pair: dict = {}
        by_scene: dict = {}
        by_event: dict = {}
        for f in files:
            try:
                with open(f, encoding="utf-8-sig", newline="") as fh:
                    rows = list(csv.DictReader(fh))
            except Exception as e:  # noqa: BLE001
                print(f"[studio] cannot read {f}: {e}")
                continue
            for r in rows:
                lv = str(r.get("level") or "").strip().upper()[:1]
                if lv not in LEVEL_IDS:
                    continue
                rec = {"level": lv, "event_id": (r.get("event_id") or "").strip() or None,
                       "scene_id": (r.get("scene_id") or "").strip() or None,
                       "reason": _reason_ru((r.get("reason") or "").strip(), lv),
                       "reason_raw": (r.get("reason") or "").strip() or None,
                       "file": f.relative_to(REPO).as_posix() if f.is_relative_to(REPO) else str(f),
                       "_field": _field_from_row(r, f.parent.name)}
                if rec["scene_id"] and rec["event_id"]:
                    by_pair.setdefault((rec["event_id"], rec["scene_id"]), []).append(rec)
                if rec["scene_id"]:
                    by_scene.setdefault(rec["scene_id"], []).append(rec)
                if rec["event_id"]:
                    by_event.setdefault(rec["event_id"], []).append(rec)
        return {"pair": by_pair, "scene": by_scene, "event": by_event, "files": [str(f) for f in files]}
    return _cached(("levels",), files, load)


FIELD_SRC_RU = {"adis": "ADIS, The Ocean Cleanup — камера с судна", "s3": "PANGAEA, судовой визуальный трансект",
                "s4": "DOORS, судовой визуальный трансект"}


def _field_from_row(r: dict, src: str) -> Optional[dict]:
    """Field count of a search-registry row (ADIS: field_count_by_size, survey_area_km2, dhat_5cm_km2;
    S3: field_items_km2) -> Russian-labelled block. None when the row has no field numbers."""
    out: dict = {}
    fc = (r.get("field_count_by_size") or "").strip()
    if fc:
        by = {}
        for part in fc.split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                n = cs.fnum(v)
                if n is not None:
                    by[k.strip().replace(">", "> ").replace("cm", " см")] = int(n)
        if by:
            out["count_by_size"] = by
            k0 = next(iter(by))
            out["count"], out["size_class"] = by[k0], k0
    area = cs.fnum(r.get("survey_area_km2"))
    dens = cs.fnum(r.get("dhat_5cm_km2"))
    if dens is None:
        dens = cs.fnum(r.get("field_items_km2"))
    if area is not None:
        out["survey_area_km2"] = round(area, 4)
    if dens is not None:
        out["items_km2"] = round(dens, 3)
    if not out:
        return None
    out["source"] = FIELD_SRC_RU.get(src, f"реестр розыска {src}")
    parts = []
    if out.get("count") is not None:
        c = out["count"]
        parts.append(f"{c} {_plural(c, 'предмет', 'предмета', 'предметов')} {out['size_class']}")
        if area is not None:
            parts[-1] += f" на {_num_ru(area)} км² обзора"
    if dens is not None:
        parts.append(f"{'оценка ' if out.get('count') is not None else ''}{_num_ru(dens, 1)} шт./км²")
    out["label"] = "Полевой счёт: " + "; ".join(parts) + f" ({out['source']})"
    return out


LEVEL_REASON_RU = {"A": "снимок и полевое число связаны по времени, месту, площади и категории",
                   "B": "подтверждённая разметка спутникового скопления", "C": "кандидат для ручной проверки",
                   "D": "отвергнутый кандидат / отрицательный пример"}


def _reason_ru(text: str, level: str) -> str:
    """Registry reasons are written by the search workers, often in English: shown text is Russian (the reason
    itself if it is Cyrillic, else the level definition); the original is kept in reason_raw."""
    if text and re.search(r"[А-Яа-яЁё]", text):
        return text
    return LEVEL_REASON_RU.get(level, "")


def _best(recs: list) -> Optional[str]:
    lv = sorted({r["level"] for r in recs})
    return lv[0] if lv else None  # A < B < C < D: the strongest level recorded; all records are in level_records


def evidence_level(event_id: Optional[str], scene_ids: list) -> tuple[Optional[str], list, Optional[str], Optional[dict]]:
    """(level, public records, match, field block of the strongest record with field numbers)."""
    idx = _levels_index()
    found = None
    for sid in scene_ids:
        if event_id and sid and (event_id, sid) in idx["pair"]:
            found = (idx["pair"][(event_id, sid)], "event_id+scene_id")
            break
    if found is None:
        for sid in scene_ids:
            if sid and sid in idx["scene"]:
                found = (idx["scene"][sid], "scene_id")
                break
    if found is None:
        return None, [], None, None
    recs, how = found
    fld = next((r["_field"] for r in sorted(recs, key=lambda x: x["level"]) if r.get("_field")), None) \
        if how == "event_id+scene_id" else None
    return _best(recs), [{k: v for k, v in r.items() if not k.startswith("_")} for r in recs], how, fld


# ------------------------------------------------------------------ scene registry
class Scene(dict):
    """Public dict + private file map in .files (kind -> render recipe)."""
    files: dict


def _file(d: Path, *names) -> Optional[Path]:
    for n in names:
        p = d / n
        if p.is_file():
            return p
    return None


def _view(kind: str, sid: str, recipe: Optional[dict], reason: Optional[str]) -> dict:
    k = next(x for x in KINDS if x["id"] == kind)
    if recipe:
        v = {"available": True, "label": k["label"], "overlay": k["overlay"],
             "url": f"/api/v3/studio/scenes/{sid}/view/{kind}.png", "source": recipe["source_note"],
             "variants": recipe.get("variants") or None, "reason": None}
        if recipe.get("legend") is not None:
            v["legend"] = recipe["legend"]
        return v
    return {"available": False, "label": k["label"], "overlay": k["overlay"], "url": None, "source": None,
            "variants": None, "reason": reason or "нет данных для вида"}


def _detection_legend(thr: Optional[float]) -> dict:
    return {"type": "probability", "threshold": thr,
            "items": [{"label": "Объект детектора (P ≥ порога, после фильтров облака/тени)", "color": "#ff2d55f2"},
                      {"label": "0,2 ≤ P < порога", "color": "#ffd43b80"}],
            "note": "текущий режим детектора (веса lgbm, без гармонизации); при уменьшении — максимум по блоку, "
                    "чтобы мелкие объекты не пропадали"}


def _num_ru(x: float, nd: int = 2) -> str:
    return f"{x:.{nd}f}".replace(".", ",")


def _plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return one
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return few
    return many


def detector_block(run: bool, *, threshold=None, pixels=None, objects=None, strip=None, source=None,
                   reason=None, legacy=None) -> dict:
    """API field `detector`: always the CURRENT mode (weights lgbm, no harmonization) or «не запускался» + reason."""
    if not run:
        return {"run": False, "status": "not_run", "weights": None, "harmonization": None, "threshold": None,
                "pixels": None, "objects": None, "scope": None, "strip": None, "source": None,
                "label": f"Детектор на этой сцене не запускался: {reason}" if reason else
                         "Детектор на этой сцене не запускался", "legacy": legacy}
    thr = _num_ru(threshold) if threshold is not None else "—"
    objects, pixels = int(objects or 0), int(pixels or 0)
    lab = (f"Детектор (текущий режим: веса lgbm, без гармонизации, порог {thr}): "
           f"{objects} {_plural(objects, 'объект', 'объекта', 'объектов')}, "
           f"{pixels} {_plural(pixels, 'пиксель', 'пикселя', 'пикселей')} на всей вырезке")
    if strip is not None and strip.get("objects") is not None:
        so = int(strip["objects"])
        lab += f"; в полосе наблюдения — {so} {_plural(so, 'объект', 'объекта', 'объектов')}"
    return {"run": True, "status": "evaluated", "weights": "lgbm", "harmonization": False, "threshold": threshold,
            "pixels": pixels, "objects": objects, "scope": "вся вырезка", "strip": strip, "source": source,
            "label": lab, "legacy": legacy}


def _not_evaluated(det: dict, why: str) -> dict:
    """The detector ran, but on this image its response is noise: numbers are moved to `raw`, not shown as result."""
    out = dict(det)
    out.update({"status": "not_evaluated", "pixels": None, "objects": None, "strip": None,
                "raw": {"pixels": det.get("pixels"), "objects": det.get("objects"), "strip": det.get("strip"),
                        "note": "срабатывания на этом снимке — шум, не мусор; не использовать как результат"},
                "label": f"Детектор не оценивается: {why}"})
    return out


def _meta_is_current(meta: dict) -> bool:
    """pair_quality-like meta.json computed in the current mode: no harmonization offset, harmonize none in config."""
    det = meta.get("detector") if isinstance(meta.get("detector"), dict) else None
    if not det:
        return False
    cfg = (meta.get("config") or {}).get("detector") or meta.get("detector_cfg") or {}
    h = str(cfg.get("harmonize") or "none").lower()
    return det.get("harmonize_offset") is None and h in ("none", "false", "")


def _legacy_live(d: Path) -> Optional[dict]:
    pj = _json(d / "prob_lgbm.json")
    if not pj:
        return None
    return {"weights": "lgbm_live", "harmonization": "water_median", "threshold": cs.fnum(pj.get("threshold")),
            "note": LEGACY_NOTE}


def _quality_legend(kind: str) -> dict:
    return {"type": "classes", "palette": "meta.quality_classes",
            "items": [{"id": q["id"], "label": q["label"], "color": q["color"]}
                      for q in cs.QUALITY_CLASSES if q.get("present")],
            "note": ("маска качества pair_quality.py (облака SCL + спектральный тест, блики)" if kind == "pair_quality"
                     else "по SCL Sen2Cor: 6 вода, 4/5 суша, 3/8/9/10 облако/тень/перистые; 0 нет данных; "
                          "прочее (1, 2, 7, 11) — «нет данных / непригодно»; спектрального теста облаков и бликов нет")}


def _band_idx(grid: dict, names: list) -> Optional[list]:
    desc = grid.get("descriptions") or []
    if not desc or not all(desc):
        desc = S2_BANDS if grid.get("count") == 12 else []
    try:
        return [desc.index(n) + 1 for n in names]
    except ValueError:
        return None


def _build_generic(sid: str, stype: str, d: Path, meta: dict, extra_dirs: Optional[dict] = None) -> Optional[Scene]:
    """One scene from a directory with raster files. meta: pair meta.json or live scene.json."""
    extra_dirs = extra_dirs or {}
    bands = _file(d, "bands.tif")
    qtif = _file(d, "quality.tif")
    scl = _file(d, "scl.tif")
    prob = _file(d, "prob.tif", "prob_lgbm.tif")
    prob_mdd = _file(d, "prob_mdd.tif")
    rgbpng = _file(d, "rgb.png")
    fdi = None
    fdi_dir = extra_dirs.get("fdi")
    if fdi_dir is not None:
        fdi = _file(fdi_dir, "fdi.npy")
    if fdi is None:
        fdi = _file(d, "fdi.npy")
    grid_src = bands or qtif or prob or scl or prob_mdd
    grid = _grid_of(grid_src) if grid_src else None
    rgb_json = _json(d / "rgb.json") or {}
    if grid is None and rgbpng is not None:  # only a picture: needs explicit lon/lat bounds
        b = rgb_json.get("bounds") or meta.get("bounds_wgs84")
        size = _png_size(rgbpng)
        if b and size:
            w, h = size
            grid = {"crs": "EPSG:4326", "transform": [(b[2] - b[0]) / w, 0, b[0], 0, -(b[3] - b[1]) / h, b[3]],
                    "width": w, "height": h, "count": 0, "descriptions": []}
    if grid is None:
        return None
    geo = _corners(grid)
    if geo is None:
        return None
    gw, gh = grid["width"], grid["height"]

    def same_grid(p: Optional[Path]) -> bool:
        if p is None:
            return False
        if p.suffix == ".npy":
            return _npy_shape(p) == (gh, gw)
        if p.suffix == ".png":
            s = _png_size(p)  # rgb.png may be a downscaled copy of the same crop: same aspect ratio is enough
            return bool(s) and abs(s[0] / s[1] - gw / gh) <= 0.01 * gw / gh
        g = _grid_of(p)
        return bool(g) and g["width"] == gw and g["height"] == gh and g["crs"] == grid["crs"]

    rec: dict = {}
    reasons: dict = {}
    det_meta = meta.get("detector") if isinstance(meta.get("detector"), dict) else None
    is_landsat = str(meta.get("scene_id") or "").upper().startswith(("LC08", "LC09", "LE07"))
    landsat_note = "Landsat: сохранена только маска QA_PIXEL; каналы и детектор не сохранялись"

    # rgb
    rgb_idx = _band_idx(grid, ["B4", "B3", "B2"]) if bands else None
    if bands and rgb_idx:
        rec["rgb"] = {"op": "rgb_bands", "path": bands, "idx": rgb_idx, "variants": ["water", "natural"],
                      "source_note": "bands.tif B4,B3,B2 (L2A); растяжка по воде 0..p98 (water) или 0..0,16 (natural)"}
    elif rgbpng and same_grid(rgbpng):
        rec["rgb"] = {"op": "png", "path": rgbpng, "variants": ["water", "natural"],
                      "source_note": "rgb.png из тех же каналов (B4,B3,B2); растяжка по воде 0..p98 или как есть"}
    elif rgbpng and grid["crs"] == "EPSG:4326":
        rec["rgb"] = {"op": "png", "path": rgbpng, "variants": ["water", "natural"],
                      "source_note": "rgb.png (EPSG:4326); растяжка по воде 0..p98 или как есть"}
    else:
        reasons["rgb"] = landsat_note if is_landsat else "каналы сцены не сохранены"

    # spectral: FDI (bands or cached fdi.npy of the same crop) + SWIR false colour (bands only)
    variants = []
    sp: dict = {"op": "spectral"}
    if bands and _band_idx(grid, ["B6", "B8", "B11"]):
        variants = ["fdi", "swir"] if _band_idx(grid, ["B11", "B8", "B4"]) else ["fdi"]
        sp.update(path=bands, source_note="из bands.tif: FDI (Biermann 2020: B6, B8, B11) или SWIR B11/B8/B4")
    elif fdi and same_grid(fdi):
        variants = ["fdi"]
        sp.update(path=fdi, source_note="FDI (Biermann 2020) из тех же каналов S2 L2A, кэш pairs_experiment.py")
    if variants:
        sp["variants"] = variants
        sp["qmask"] = qtif if (qtif and same_grid(qtif)) else (scl if (scl and same_grid(scl)) else None)
        _cq = (extra_dirs.get("current") / "quality.tif") if extra_dirs.get("current") is not None else None
        if not qtif and _cq is not None and _cq.is_file() and same_grid(_cq):
            sp["qmask"] = _cq
        rec["spectral"] = sp
    else:
        reasons["spectral"] = (landsat_note if is_landsat else
                               "каналы сцены не сохранены (есть только готовый RGB)" if rgbpng else
                               "каналы сцены не сохранены")

    # detection: ONLY the current mode (weights lgbm, no harmonization). Pairs/search: prob.tif of pair_quality-like
    # runs with harmonize none; live/drift: out/studio_cache/detector_current (scripts/case/studio_detector_current.py).
    # The legacy prob_lgbm.tif / prob_mdd.tif of data/live are never shown as the detector.
    cur = extra_dirs.get("current")
    cur_info = _json(cur / "det.json") if cur is not None else None
    det_block = None
    if cur_info and _file(cur, "prob.tif") and _file(cur, "det.tif") and same_grid(cur / "prob.tif"):
        thr = cs.fnum(cur_info.get("threshold"))
        rec["detection"] = {"op": "detection", "prob": cur / "prob.tif", "det": cur / "det.tif", "qmask": None,
                            "thr": thr, "pixels": cur_info.get("pixels"),
                            "source_note": "текущий режим: веса lgbm, без гармонизации (studio_detector_current.py)",
                            "legend": _detection_legend(thr)}
        det_block = detector_block(True, threshold=thr, pixels=cur_info.get("pixels"), objects=cur_info.get("objects"),
                                   source="out/studio_cache/detector_current", legacy=_legacy_live(d))
    elif det_meta and _meta_is_current(meta) and prob and prob.name == "prob.tif" and same_grid(prob):
        thr = cs.fnum(det_meta.get("threshold"))
        px = det_meta.get("det_px_crop")
        rec["detection"] = {"op": "detection", "prob": prob, "det": None,
                            "qmask": qtif if (qtif and same_grid(qtif)) else None, "thr": thr, "pixels": px,
                            "source_note": "текущий режим: веса lgbm, без гармонизации (prob.tif pair_quality.py)",
                            "legend": _detection_legend(thr)}
        strip = ({"objects": det_meta.get("n_det"), "pixels": det_meta.get("det_px_strip")}
                 if det_meta.get("n_det") is not None else None)
        det_block = detector_block(True, threshold=thr, pixels=px, objects=det_meta.get("n_det_crop"), strip=strip,
                                   source="meta.json (pair_quality.py)")
    else:
        if is_landsat:
            reasons["detection"] = "Landsat: детектор обучен на каналах Sentinel-2 и на этом снимке не запускался"
        elif det_meta and not _meta_is_current(meta):
            reasons["detection"] = "вероятность посчитана прежним режимом (с гармонизацией) — не показывается"
        elif (d / "prob_lgbm.json").is_file():
            reasons["detection"] = ("текущий режим детектора (веса lgbm, без гармонизации) для этой сцены ещё не "
                                    "рассчитан; прежний режим не показывается")
        else:
            reasons["detection"] = "детектор на этой сцене не запускался"
        det_block = detector_block(False, reason=reasons["detection"],
                                   legacy=_legacy_live(d) if (d / "prob_lgbm.json").is_file() else None)

    # quality
    cq = _file(cur, "quality.tif") if (cur is not None and cur_info) else None
    if cq and not qtif and same_grid(cq):
        rec["quality"] = {"op": "quality_codes", "path": cq,
                          "source_note": "маска качества как у пар (pair_quality.py: SCL + спектральный тест облаков, "
                                         "блики), studio_detector_current.py", "legend": _quality_legend("pair_quality")}
    elif qtif and same_grid(qtif):
        rec["quality"] = {"op": "quality_codes", "path": qtif, "source_note": "quality.tif (pair_quality.py)",
                          "legend": _quality_legend("pair_quality")}
    elif scl and same_grid(scl):
        rec["quality"] = {"op": "quality_scl", "path": scl, "source_note": "scl.tif (Sen2Cor SCL) → классы качества",
                          "legend": _quality_legend("scl")}
    else:
        reasons["quality"] = "нет маски качества и SCL"

    views = {k: _view(k, sid, rec.get(k), reasons.get(k)) for k in KIND_IDS}
    s = Scene()
    s.update({"id": sid, "source_type": stype,
              "source_label": next(x["label"] for x in SOURCE_TYPES if x["id"] == stype),
              "coordinates": geo["coordinates"], "bounds": geo["bounds"], "crs": grid["crs"],
              "size_px": [gw, gh], "pixel_m": geo["pixel_m"],
              "views": views, "available_views": [k for k in KIND_IDS if views[k]["available"]]})
    s["detector"] = det_block
    s.files = rec
    cur_files = [cur / n for n in ("prob.tif", "det.tif", "quality.tif", "det.json")] if cur_info else []
    s.files["_all"] = [p for p in (bands, qtif, scl, prob, prob_mdd, rgbpng, fdi) if p] + cur_files
    return s


def _finish(s: Scene, *, product_id: Optional[str], datetime: Optional[str], src: str, title: str,
            event_id: Optional[str] = None, region: Optional[str] = None, tile: Optional[str] = None,
            extra: Optional[dict] = None) -> Scene:
    mission, platform = _platform(product_id or "")
    catalog, collection = _catalog(src)
    if collection is None and product_id and "MSIL2A" in product_id.upper():
        collection = "sentinel-2-l2a"
    if collection is None and product_id and product_id.upper().endswith("_L2A"):
        collection = "sentinel-2-l2a"
    iso = cs.iso_dt(datetime) if datetime else None
    lv, recs, how, fld = evidence_level(event_id, [product_id] if product_id else [])
    s.update({"title": title, "datetime": iso, "date": iso[:10] if iso else None,
              "mission": mission, "platform": platform, "catalog": catalog, "collection": collection,
              "product_id": product_id, "tile": tile, "region": region, "event_id": event_id,
              "level": lv, "level_match": how, "level_records": recs})
    if extra:
        s.update(extra)
    if fld:  # field count of the search registry (ADIS …) next to the level badge
        base = dict(s.get("field") or {})
        base.update({"count": fld.get("count"), "count_by_size": fld.get("count_by_size"),
                     "size_class": fld.get("size_class"), "survey_area_km2": fld.get("survey_area_km2"),
                     "items_km2": fld.get("items_km2"), "field_source": fld.get("source"), "label": fld.get("label")})
        s["field"] = base
    elif s.get("field") and s["field"].get("field_items_km2") is not None and not s["field"].get("label"):
        s["field"]["label"] = f"Полевое измерение: {_num_ru(s['field']['field_items_km2'], 1)} шт./км²"
    _gate_illumination(s)
    return s


def _gate_illumination(s: "Scene") -> None:
    """Low sun / weak water signal -> the detector result is not evaluated and the «Детекция» view is withheld."""
    zen = solar_zenith(s.get("datetime"), s.get("bounds"))
    det = s.get("detector") or {}
    low = zen is not None and zen >= SUN_ZENITH_MAX
    sig = water_signal(s) if det.get("run") and not low else None  # only where it can change the verdict
    weak = sig is not None and sig < WATER_B3_MIN
    s["illumination"] = {"sun_zenith_deg": zen, "water_b3_median": sig, "low_sun": low, "weak_signal": weak,
                         "rule": f"не оценивается при зените Солнца ≥ {SUN_ZENITH_MAX:.0f}° или медиане B3 воды < "
                                 f"{WATER_B3_MIN}"}
    if not det.get("run") or not (low or weak):
        return
    why = []
    if low:
        why.append(f"низкое солнце (зенит {_num_ru(zen, 0)}°)")
    if weak:
        why.append(f"слабый сигнал воды (B3 {_num_ru(sig, 4)})")
    reason = " и ".join(why) + " — срабатывания на таком снимке — шум, а не мусор"
    s["detector"] = _not_evaluated(det, reason)
    s.files.pop("detection", None)
    s["views"]["detection"] = _view("detection", s["id"], None, "детектор не оценивается: " + reason)
    s["available_views"] = [k for k in KIND_IDS if s["views"][k]["available"]]


def _quality_block(meta: dict, q: dict) -> dict:
    dec = meta.get("decision")
    rs = meta.get("reason") or None
    return {"decision": dec, "decision_label": DECISION_RU.get(dec) if dec else None,
            "reason": rs, "reason_label": QREASON_RU.get(rs, rs) if rs else None,
            "valid_water_frac": cs.fnum(q.get("valid_water_frac")), "cloud_frac": cs.fnum(q.get("cloud_frac")),
            "glint_frac": cs.fnum(q.get("glint_frac")), "strip_area_km2": cs.fnum(q.get("strip_area_km2"))}


def _pair_scenes() -> list:
    root = ROOTS["pairs"]
    out = []
    if not root.is_dir():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        meta = _json(d / "meta.json") or {}
        sid = f"pair.{d.name}"
        s = _build_generic(sid, "pair", d, meta, {"fdi": ROOTS["pairs_fdi"] / d.name})
        if s is None:
            continue
        q = meta.get("quality") or {}
        pid = meta.get("scene_id")
        out.append(_finish(
            s, product_id=pid, datetime=meta.get("scene_datetime"), src=meta.get("source") or "",
            title=f"{meta.get('event_id') or d.name} · {(pid or '')[:24]}", event_id=meta.get("event_id"),
            tile=meta.get("tile"),
            extra={"field": {"sample_ids": [x for x in str(meta.get("sample_ids") or "").split(";") if x],
                             "field_sample_id": meta.get("field_sample_id"),
                             "field_items_km2": cs.fnum(meta.get("field_items_km2")),
                             "obs_datetime": cs.iso_dt(meta.get("obs_datetime")) if meta.get("obs_datetime") else None,
                             "time_known": meta.get("time_known"), "dt_hours": cs.fnum(meta.get("dt_hours"))},
                   "quality": _quality_block(meta, q),
                   "links": {"v3_scene": f"/api/v3/scenes/{pid}" if pid else None,
                             "dir": d.relative_to(REPO).as_posix() if d.is_relative_to(REPO) else str(d)},
                   "footprint_note": "вырезка вокруг наблюдения, не вся сцена"}))
    return out


def _live_like(stype: str, root: Path, prefix: str) -> list:
    out = []
    if not root.is_dir():
        return out
    for rd in sorted(p for p in root.iterdir() if p.is_dir()):
        for d in sorted(p for p in rd.iterdir() if p.is_dir()):
            meta = _json(d / "scene.json") or {}
            if not meta and not _file(d, "bands.tif", "rgb.png"):
                continue
            sid = f"{prefix}.{rd.name}.{d.name}"
            s = _build_generic(sid, stype, d, meta, {"current": ROOTS["det_current"] / stype / rd.name / d.name})
            if s is None:
                continue
            links = {"dir": d.relative_to(REPO).as_posix() if d.is_relative_to(REPO) else str(d)}
            out.append(_finish(
                s, product_id=meta.get("scene_id"), datetime=meta.get("datetime") or d.name,
                src=meta.get("source") or "", title=f"{meta.get('region_name') or rd.name} · {d.name}",
                region=rd.name, tile=meta.get("tile"),
                extra={"quality": {"decision": None, "reason": None,
                                   "valid_water_frac": cs.fnum(meta.get("water_frac")),
                                   "cloud_frac": cs.fnum(meta.get("crop_cloud_frac")),
                                   "cloud_cover_scene_pct": cs.fnum(meta.get("cloud_cover")),
                                   "glint_or_haze": meta.get("glint_or_haze")},
                       "field": None, "links": links,
                       "footprint_note": "вырезка района, не вся сцена"}))
    # service/data dates without raw channels (only prepared rgb.png): rgb view only
    if stype == "live" and ROOTS["service"].is_dir():
        have = {s["id"] for s in out}
        for rd in sorted(p for p in ROOTS["service"].iterdir() if p.is_dir()):
            for d in sorted(p for p in rd.iterdir() if p.is_dir()):
                sid = f"{prefix}.{rd.name}.{d.name}"
                if sid in have or not (d / "rgb.png").is_file():
                    continue
                rj = _json(d / "rgb.json") or {}
                s = _build_generic(sid, stype, d, {"bounds_wgs84": rj.get("bounds")})
                if s is None:
                    continue
                out.append(_finish(s, product_id=rj.get("scene_id"), datetime=d.name, src="",
                                   title=f"{rd.name} · {d.name}", region=rd.name,
                                   extra={"quality": None, "field": None,
                                          "links": {"dir": d.relative_to(REPO).as_posix()
                                                    if d.is_relative_to(REPO) else str(d)},
                                          "footprint_note": "подготовленный RGB без каналов"}))
    return out


def _search_scenes() -> list:
    root = ROOTS["search"]
    out = []
    if not root.is_dir():
        return out
    for src in sorted(p for p in root.iterdir() if p.is_dir()):
        for d in _walk_dirs(src):
            rel = d.relative_to(src)
            if d == src or not ((d / "meta.json").is_file() or (d / "scene.json").is_file()):
                continue
            if not _file(d, "bands.tif", "rgb.png", "quality.tif", "prob.tif", "prob_lgbm.tif", "scl.tif", "fdi.npy"):
                continue
            meta = _json(d / "meta.json") or _json(d / "scene.json") or {}
            sid = f"search.{src.name}." + "~".join(rel.parts)
            s = _build_generic(sid, "search", d, meta)
            if s is None:
                continue
            q = meta.get("quality") if isinstance(meta.get("quality"), dict) else {}
            pid = meta.get("scene_id") or meta.get("product_id") or meta.get("item_id")
            out.append(_finish(
                s, product_id=pid, datetime=meta.get("scene_datetime") or meta.get("datetime"),
                src=meta.get("source") or meta.get("collection") or "",
                title=f"{meta.get('event_id') or src.name} · {(pid or rel.name)[:24]}",
                event_id=meta.get("event_id"), tile=meta.get("tile"), region=meta.get("region"),
                extra={"quality": _quality_block(meta, q),
                       "field": ({"sample_ids": [x for x in str(meta.get("sample_ids") or "").split(";") if x],
                                  "field_items_km2": cs.fnum(meta.get("field_items_km2")),
                                  "obs_datetime": cs.iso_dt(meta.get("obs_datetime"))
                                  if meta.get("obs_datetime") else None,
                                  "dt_hours": cs.fnum(meta.get("dt_hours"))} if meta.get("event_id") else None),
                       "links": {"dir": d.relative_to(REPO).as_posix() if d.is_relative_to(REPO) else str(d),
                                 "search_src": src.name},
                       "footprint_note": "вырезка розыска"}))
    return out


def _walk_dirs(src: Path) -> list:
    """Directories under data/search/<src>/ without cache/src/code subtrees (pruned walk)."""
    out = []
    for dp, dns, _ in os.walk(src):
        dns[:] = sorted(x for x in dns if x not in SEARCH_SKIP)
        out.append(Path(dp))
    return out


def _registry_sig_paths() -> list:
    ps = []
    for k in ("pairs", "pairs_fdi", "live", "service", "drift", "search"):
        r = ROOTS[k]
        if r.is_dir():
            ps.append(r)
            ps.extend(p for p in r.iterdir() if p.is_dir())
    if ROOTS["search"].is_dir():
        ps.extend(sorted(ROOTS["search"].glob("*/candidates.csv")))
        for src in ROOTS["search"].iterdir():
            if src.is_dir():
                ps.extend(_walk_dirs(src))
    return ps


def scenes_all() -> list:
    """All studio scenes, sorted by datetime then id. Re-scanned when any scene directory changes."""
    def load():
        items = _pair_scenes() + _live_like("live", ROOTS["live"], "live") + \
            _live_like("drift", ROOTS["drift"], "drift") + _search_scenes()
        items.sort(key=lambda s: (s.get("datetime") or "", s["id"]))
        _wsig_flush()
        return items
    # signature: directory mtimes + a 60-s tick (files rewritten in place do not touch the directory mtime)
    return _cached(("scenes", tuple(str(v) for v in ROOTS.values())), _registry_sig_paths(), load,
                   extra=int(time.time() // 60))


def scene_by_id(sid: str) -> Scene:
    for s in scenes_all():
        if s["id"] == sid:
            return s
    raise ApiError(404, "NOT_FOUND", f"Сцена {sid} не найдена в студии", {"scene_id": sid})


def public(s: Scene, brief: bool = False) -> dict:
    """brief (list): without view legends and level_records — they are in GET /studio/scenes/{id}."""
    out = {k: v for k, v in s.items()}
    if brief:
        out.pop("level_records", None)
        out["views"] = {k: {kk: vv for kk, vv in v.items() if kk != "legend"} for k, v in s["views"].items()}
    return out


# ------------------------------------------------------------------ illumination / water signal
def solar_zenith(iso: Optional[str], bounds) -> Optional[float]:
    """Solar zenith angle (deg) at the scene centre and acquisition time (NOAA solar position, error < 0.5 deg).
    Computed, not read from STAC: the scene directories keep no sun angles."""
    if not iso or not bounds:
        return None
    import datetime as _dt
    try:
        t = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(_dt.timezone.utc)
    except ValueError:
        return None
    lon, lat = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
    doy = t.timetuple().tm_yday
    hour = t.hour + t.minute / 60 + t.second / 3600
    g = 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g)
                    - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    tst = hour * 60 + eqt + 4 * lon
    ha = math.radians(tst / 4 - 180)
    la = math.radians(lat)
    cz = math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)
    return round(math.degrees(math.acos(max(-1.0, min(1.0, cz)))), 1)


def water_signal(s: "Scene") -> Optional[float]:
    """Median green reflectance (B3) of usable water: from bands.tif, or inverted from rgb.png of pair_quality.py
    (fixed stretch 0..0.16, gamma 1/1.8 -> reflectance = 0.16 * (v/255)^1.8). None if unknown."""
    rgb = s.files.get("rgb")
    qm = (s.files.get("quality") or {}).get("path")
    if not rgb:
        return None

    def load():
        try:
            import rasterio
            from PIL import Image
            if rgb["op"] == "rgb_bands":
                g = _read(rgb["path"], [rgb["idx"][1]], 512, 512, "nearest")
                h, w = g.shape
            elif rgb["op"] == "png" and rgb["path"].name == "rgb.png" and s["crs"] != "EPSG:4326":
                with Image.open(rgb["path"]) as im:
                    im = im.convert("RGB")
                    im.thumbnail((512, 512), Image.NEAREST)
                    v = np.asarray(im)[:, :, 1].astype(np.float32)
                g = 0.16 * (v / 255.0) ** 1.8
                h, w = g.shape
            else:
                return None
            water = None
            if qm is not None:
                with rasterio.open(qm) as ds:
                    q = ds.read(1)
                water = _nearest(q, w, h) == (6 if qm.name == "scl.tif" else 1)
            vals = g[water] if water is not None and water.sum() >= 50 else g[np.isfinite(g)]
            vals = vals[np.isfinite(vals)]
            return round(float(np.median(vals)), 4) if vals.size else None
        except Exception as e:  # noqa: BLE001
            print(f"[studio] water_signal {s['id']}: {e}")
            return None
    paths = [p for p in (rgb.get("path"), qm) if p]
    key = hashlib.sha1(repr(_sig(paths)).encode()).hexdigest()[:16]
    disk = _wsig_disk()
    if s["id"] in disk and disk[s["id"]][0] == key:
        return disk[s["id"]][1]
    val = _cached(("wsig", s["id"]), paths, load)
    with _lock:
        disk[s["id"]] = (key, val)
        _WSIG["dirty"] = True
    return val


_WSIG: dict = {"data": None, "dirty": False}


def _wsig_disk() -> dict:
    """Water-signal values persisted in out/studio_cache/_water_signal.json (the first scan reads every RGB once)."""
    f = ROOTS["cache"] / "_water_signal.json"
    if _WSIG["data"] is None or _WSIG.get("path") != f:  # the cache root may change (tests)
        _wsig_flush()
        _WSIG["path"], _WSIG["dirty"] = f, False
        try:
            _WSIG["data"] = {k: tuple(v) for k, v in json.loads(f.read_text(encoding="utf-8")).items()}
        except Exception:  # noqa: BLE001
            _WSIG["data"] = {}
    return _WSIG["data"]


def _wsig_flush() -> None:
    if not _WSIG["dirty"]:
        return
    try:
        f = _WSIG.get("path") or (ROOTS["cache"] / "_water_signal.json")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(_WSIG["data"]), encoding="utf-8")
        _WSIG["dirty"] = False
    except OSError as e:
        print(f"[studio] cannot write water-signal cache: {e}")


# ------------------------------------------------------------------ rendering
_png_mem: "OrderedDict[str, bytes]" = OrderedDict()
_PNG_MEM_MAX = 96


def _out_shape(gw: int, gh: int, px: int) -> tuple[int, int]:
    f = max(gw, gh) / px
    if f <= 1:
        return gw, gh
    return max(1, int(round(gw / f))), max(1, int(round(gh / f)))


def _read(path: Path, idx: list, ow: int, oh: int, resampling: str = "average") -> np.ndarray:
    import rasterio
    from rasterio.enums import Resampling
    with rasterio.open(path) as ds:
        if (ow, oh) == (ds.width, ds.height):
            return ds.read(idx).astype(np.float32) if len(idx) > 1 else ds.read(idx[0]).astype(np.float32)
        rs = getattr(Resampling, resampling)
        if len(idx) > 1:
            return ds.read(idx, out_shape=(len(idx), oh, ow), resampling=rs).astype(np.float32)
        return ds.read(idx[0], out_shape=(oh, ow), resampling=rs).astype(np.float32)


def _block_max(a: np.ndarray, ow: int, oh: int) -> np.ndarray:
    """Max-pool to (oh, ow): small detections survive downscaling."""
    h, w = a.shape
    if (w, h) == (ow, oh):
        return a
    ys = (np.arange(oh + 1) * h / oh).astype(int)
    xs = (np.arange(ow + 1) * w / ow).astype(int)
    r = np.maximum.reduceat(a, ys[:-1], axis=0)
    return np.maximum.reduceat(r, xs[:-1], axis=1)[:oh, :ow]


def _nearest(a: np.ndarray, ow: int, oh: int) -> np.ndarray:
    h, w = a.shape[:2]
    if (w, h) == (ow, oh):
        return a
    yi = np.minimum((np.arange(oh) + 0.5) * h / oh, h - 1).astype(int)
    xi = np.minimum((np.arange(ow) + 0.5) * w / ow, w - 1).astype(int)
    return a[yi][:, xi]


def _png(arr: np.ndarray, mode: str) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(arr, mode).save(buf, "PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def _lut(name: str) -> np.ndarray:
    try:
        import matplotlib
        cm = matplotlib.colormaps[name]
        return (cm(np.linspace(0, 1, 256))[:, :3] * 255).astype(np.uint8)
    except Exception:  # noqa: BLE001
        g = np.linspace(0, 255, 256).astype(np.uint8)
        return np.stack([g, g, g], 1)


def _stretch(a: np.ndarray, lo: float, hi: float, gamma: float = 1.0) -> np.ndarray:
    x = np.clip((a - lo) / max(hi - lo, 1e-9), 0, 1)
    if gamma != 1.0:
        x = x ** gamma
    return np.nan_to_num(x * 255).astype(np.uint8)


def _quality_codes_from_scl(scl: np.ndarray) -> np.ndarray:
    q = np.full(scl.shape, 5, np.uint8)
    q[scl == 0] = 0
    q[scl == 6] = 1
    q[np.isin(scl, (4, 5))] = 2
    q[np.isin(scl, (3, 8, 9, 10))] = 3
    return q


def _quality_lut() -> np.ndarray:
    lut = np.zeros((256, 4), np.uint8)
    lut[:] = cs._rgba("#00000066")
    for qc in cs.QUALITY_CLASSES:
        for c in qc["codes"]:
            lut[c] = cs._rgba(qc["color"])
    return lut


def _quality_water(s: Scene, ow: int, oh: int) -> Optional[np.ndarray]:
    """Usable water of the scene on the output grid (quality code 1 or SCL 6), None if the scene has no mask."""
    q = s.files.get("quality") or {}
    path = q.get("path")
    if path is None:
        return None
    import rasterio
    with rasterio.open(path) as ds:
        a = ds.read(1).astype(np.uint8)
    a = _nearest(a, ow, oh)
    return a == (6 if q.get("op") == "quality_scl" else 1)


def _water_mask(s: Scene, ow: int, oh: int) -> Optional[np.ndarray]:
    qm = s.files.get("spectral", {}).get("qmask")
    if qm is None:
        return None
    a = _read(qm, [1], ow, oh, "nearest").astype(np.uint8)
    return (a == 1) if qm.name == "quality.tif" else (a == 6)


def render(s: Scene, kind: str, variant: Optional[str], px: int) -> tuple[bytes, dict]:
    r = s.files.get(kind)
    if not r:
        raise ApiError(404, "NO_VIEW", f"Вид «{next(k['label'] for k in KINDS if k['id'] == kind)}» для сцены "
                       f"{s['id']} недоступен: {s['views'][kind]['reason']}",
                       {"scene_id": s["id"], "kind": kind, "reason": s["views"][kind]["reason"],
                        "available_views": s["available_views"]})
    variants = r.get("variants") or []
    if variant and variant not in (variants or []):
        raise ApiError(400, "BAD_PARAM", f"variant: для вида {kind} этой сцены допустимо "
                       f"{' | '.join(variants) if variants else '(без вариантов)'}",
                       {"param": "variant", "value": variant, "allowed": variants})
    v = variant or (variants[0] if variants else None)
    gw, gh = s["size_px"]
    ow, oh = _out_shape(gw, gh, px)
    hdr = {"X-View-Kind": kind, "X-View-Variant": v or "", "X-View-Size": f"{ow}x{oh}"}
    op = r["op"]
    if op in ("png", "rgb_bands"):
        from PIL import Image
        if op == "png":  # pair_quality/live rgb.png: fixed stretch 0..0.16, gamma 1/1.8 -> back to reflectance
            with Image.open(r["path"]) as im:
                im = im.convert("RGBA")
                if im.size != (ow, oh):
                    im = im.resize((ow, oh), Image.BILINEAR)
                arr = np.asarray(im).astype(np.float32)
            valid = arr[:, :, 3] > 0
            a = (0.16 * (arr[:, :, :3] / 255.0) ** 1.8).transpose(2, 0, 1)
        else:
            a = _read(r["path"], r["idx"], ow, oh)
            valid = np.isfinite(a).all(0)
        if (v or "water") == "natural":
            lo, hi, g = RGB_STRETCH
            rgb = np.stack([_stretch(a[i], lo, hi, g) for i in range(3)], -1)
            hdr["X-View-Scale"] = f"reflectance {lo}..{hi} gamma {g:.3f}"
        else:
            wm = _quality_water(s, ow, oh)
            sel = valid & wm if wm is not None and (valid & wm).sum() >= 200 else valid
            if sel.sum() >= 50:
                lo = 0.0  # keep the dark origin: a joint p2 floor turns open water into saturated blue
                hi = float(max(np.percentile(a[i][sel], 98) for i in range(3)))
            else:
                lo, hi = 0.0, 0.16
            hi = max(hi, lo + 0.005)
            rgb = np.stack([_stretch(a[i], lo, hi, 1 / 1.4) for i in range(3)], -1)
            hdr["X-View-Scale"] = (f"reflectance {lo:.4f}..{hi:.4f} gamma 0.714 "
                                   f"(0..p98 {'water' if sel is not valid else 'all'})")
        alpha = np.where(valid, 255, 0).astype(np.uint8)
        return _png(np.dstack([rgb, alpha]), "RGBA"), hdr
    if op == "spectral":
        wm = None
        if r["path"].suffix == ".npy":
            f = np.load(r["path"]).astype(np.float32)
            f = _read_np_resample(f, ow, oh)
        else:
            gi = _band_idx(_grid_of(r["path"]), ["B6", "B8", "B11"])
            if v == "swir":
                idx = _band_idx(_grid_of(r["path"]), ["B11", "B8", "B4"])
                a = _read(r["path"], idx, ow, oh)
                valid = np.isfinite(a).all(0)
                chans = []
                for i in range(3):
                    x = a[i][valid]
                    lo, hi = (float(np.percentile(x, 2)), float(np.percentile(x, 98))) if x.size else (0.0, 0.3)
                    chans.append(_stretch(a[i], lo, hi))
                hdr["X-View-Scale"] = "B11,B8,B4 per-band p2..p98"
                return _png(np.dstack(chans + [np.where(valid, 255, 0).astype(np.uint8)]), "RGBA"), hdr
            b6, b8, b11 = _read(r["path"], gi, ow, oh)
            f = _fdi({"B6": b6, "B8": b8, "B11": b11}, variant="paper").astype(np.float32)
        wm = _water_mask(s, ow, oh)
        valid = np.isfinite(f)
        ref = f[valid & wm] if wm is not None and (valid & wm).sum() > 100 else f[valid]
        lo, hi = (float(np.percentile(ref, 2)), float(np.percentile(ref, 99.5))) if ref.size else (-0.01, 0.02)
        rgb = _lut("magma")[_stretch(f, lo, hi)]
        hdr["X-View-Scale"] = f"FDI {lo:.4f}..{hi:.4f} (p2..p99.5 {'water' if wm is not None else 'all'})"
        return _png(np.dstack([rgb, np.where(valid, 255, 0).astype(np.uint8)]), "RGBA"), hdr
    if op == "detection":
        import rasterio
        with rasterio.open(r["prob"]) as ds:
            a = ds.read(1).astype(np.uint8)
        t = r["thr"] if r.get("thr") is not None else 0.5
        qa = None
        if r.get("qmask") is not None:
            with rasterio.open(r["qmask"]) as ds:
                qa = ds.read(1).astype(np.uint8)
        if r.get("det") is not None:  # final detector mask of studio_detector_current.py
            with rasterio.open(r["det"]) as ds:
                det = ds.read(1) > 0
        else:  # pairs/search: the same rule as pair_quality.py from prob.tif + quality.tif (without the shadow test)
            k = int(math.floor(t * 255 + 0.5))  # round(P*255) >= round(thr*255)
            det = a >= k
            if qa is not None:
                det &= qa == 1
                lab, n = ndimage.label(det, structure=np.ones((3, 3), bool))
                if n:
                    near = cloudmask.near_cloud_components(lab, n, np.isin(qa, (3, 4)), 5)
                    lab, n = cloudmask.drop_components(lab, n, near)
                det = lab > 0
        soft = a.astype(np.float32) / 255.0
        soft[det] = 0
        if qa is not None:
            soft[qa != 1] = 0
        p = _block_max(soft, ow, oh)
        hit = _block_max(det.astype(np.uint8), ow, oh) > 0
        out = np.zeros((oh, ow, 4), np.uint8)
        mid = (p >= 0.2) & ~hit
        out[mid] = (255, 212, 59, 0)
        out[mid, 3] = np.clip(60 + 120 * (p[mid] - 0.2) / max(t - 0.2, 1e-6), 60, 180).astype(np.uint8)
        out[hit] = (255, 45, 85, 242)
        hdr["X-View-Scale"] = f"P>={t:.2f}; weights=lgbm; harmonization=none"
        hdr["X-Detection-Pixels"] = str(int(r["pixels"])) if r.get("pixels") is not None else str(int(det.sum()))
        return _png(out, "RGBA"), hdr
    if op in ("quality_codes", "quality_scl"):
        import rasterio
        with rasterio.open(r["path"]) as ds:
            a = ds.read(1).astype(np.uint8)
        a = _nearest(a, ow, oh)
        codes = a if op == "quality_codes" else _quality_codes_from_scl(a)
        return _png(_quality_lut()[codes], "RGBA"), hdr
    raise ApiError(500, "INTERNAL", "неизвестный способ отрисовки", {"op": op})


def _read_np_resample(a: np.ndarray, ow: int, oh: int) -> np.ndarray:
    h, w = a.shape
    if (w, h) == (ow, oh):
        return a
    ys = (np.arange(oh + 1) * h / oh).astype(int)
    xs = (np.arange(ow + 1) * w / ow).astype(int)
    with np.errstate(invalid="ignore"):
        r = np.add.reduceat(np.nan_to_num(a), ys[:-1], axis=0)
        n = np.add.reduceat(np.isfinite(a).astype(np.float32), ys[:-1], axis=0)
        r = np.add.reduceat(r, xs[:-1], axis=1)
        n = np.add.reduceat(n, xs[:-1], axis=1)
        out = r / n
    out[n == 0] = np.nan
    return out[:oh, :ow].astype(np.float32)


def view_png(s: Scene, kind: str, variant: Optional[str], px: int) -> tuple[bytes, dict]:
    """Rendered PNG with memory + disk cache (key: scene, kind, variant, px, source files' mtime/size)."""
    sig = hashlib.sha1(repr((STUDIO_VERSION, s["id"], kind, variant, px,
                             _sig(s.files.get("_all", [])))).encode()).hexdigest()[:16]
    key = f"{s['id']}|{kind}|{variant}|{px}|{sig}"
    with _lock:
        if key in _png_mem:
            _png_mem.move_to_end(key)
            body, hdr = _png_mem[key]
            return body, {**hdr, "X-Cache": "mem", "ETag": f'"{sig}"'}
    cdir = ROOTS["cache"] / s["id"]
    cfile = cdir / f"{kind}-{variant or 'default'}-{px}-{sig}.png"
    hfile = cfile.with_suffix(".json")
    if cfile.is_file() and hfile.is_file():
        try:
            body, hdr = cfile.read_bytes(), json.loads(hfile.read_text(encoding="utf-8"))
            src = "disk"
        except Exception:  # noqa: BLE001
            body = None
    else:
        body = None
    if body is None:
        body, hdr = render(s, kind, variant, px)
        src = "render"
        try:
            cdir.mkdir(parents=True, exist_ok=True)
            for old in cdir.glob(f"{kind}-{variant or 'default'}-{px}-*"):  # stale renders of changed sources
                old.unlink(missing_ok=True)
            cfile.write_bytes(body)
            hfile.write_text(json.dumps(hdr), encoding="utf-8")
        except OSError as e:
            print(f"[studio] cache write failed: {e}")
    with _lock:
        _png_mem[key] = (body, hdr)
        while len(_png_mem) > _PNG_MEM_MAX:
            _png_mem.popitem(last=False)
    return body, {**hdr, "X-Cache": src, "ETag": f'"{sig}"'}


# ------------------------------------------------------------------ endpoints
def _filter(items: list, bbox, a, b, sources, levels, views) -> list:
    out = []
    for s in items:
        if not cs.bbox_intersects(s["bounds"], bbox):
            continue
        if not cs.in_dates(s.get("date"), a, b):
            continue
        if sources and s["source_type"] not in sources:
            continue
        if levels and (s.get("level") or "none") not in levels:
            continue
        if views and not all(v in s["available_views"] for v in views):
            continue
        out.append(s)
    return out


@_studio.get("/scenes", summary="Студия: сцены с реальными каналами (снимки пар, районы, дрейф, розыск) и их виды")
@_guard
def studio_scenes(request: Request):
    q = _check(request, P_SCENES, "/api/v3/studio/scenes")
    a, b = cs.parse_dates(q.get("date_from"), q.get("date_to"))
    bbox = cs.parse_bbox(q.get("bbox"))
    sources = cs.parse_list(q.get("source"), "source", SOURCE_TYPE_IDS)
    levels = cs.parse_list(q.get("level"), "level", LEVEL_IDS + ["none"])
    views = cs.parse_list(q.get("view"), "view", KIND_IDS)
    limit = cs.parse_int(q.get("limit"), "limit", 1, 100000, 100000)
    offset = cs.parse_int(q.get("offset"), "offset", 0, 10 ** 7, 0)
    allsc = scenes_all()
    items = _filter(allsc, bbox, a, b, sources, levels, views)
    page = items[offset:offset + limit]
    by_src: dict = {}
    for s in items:
        by_src[s["source_type"]] = by_src.get(s["source_type"], 0) + 1
    reason = None
    if not items:
        reason = ("В студии нет сцен с сохранёнными каналами" if not allsc else
                  "Нет сцен с каналами в этой области/датах/фильтрах")
    elif not page:
        reason = "Нет сцен на этой странице (offset больше числа сцен)"
    return _ok({"version": STUDIO_VERSION, "count": len(page), "total": len(items), "offset": offset, "limit": limit,
                "empty_reason": reason, "kinds": KINDS, "source_types": SOURCE_TYPES, "levels": LEVELS,
                "by_source_type": dict(sorted(by_src.items())),
                "scenes": [public(s, brief=True) for s in page]})


@_studio.get("/scenes/{scene_id}", summary="Студия: одна сцена (виды, уровень, качество, детектор, поле)")
@_guard
def studio_scene(scene_id: str, request: Request):
    _check(request, P_SCENE, f"/api/v3/studio/scenes/{scene_id}")
    s = scene_by_id(scene_id)
    out = public(s)
    # other scenes over the same place (timeline): same event or same region, any source, sorted by datetime
    key = ("event_id", s.get("event_id")) if s.get("event_id") else ("region", s.get("region"))
    out["timeline"] = [{"id": x["id"], "datetime": x.get("datetime"), "source_type": x["source_type"],
                        "platform": x.get("platform"), "level": x.get("level"),
                        "available_views": x["available_views"]}
                       for x in scenes_all() if key[1] and x.get(key[0]) == key[1]]
    return _ok(out)


@_studio.get("/scenes/{scene_id}/view/{kind}.png", summary="Студия: PNG вида сцены (rgb|spectral|detection|quality)")
@_guard
def studio_view(scene_id: str, kind: str, request: Request):
    q = _check(request, P_VIEW, f"/api/v3/studio/scenes/{scene_id}/view/{kind}.png")
    if kind not in KIND_IDS:
        raise ApiError(400, "BAD_PARAM", f"вид: допустимо {' | '.join(KIND_IDS)}",
                       {"param": "kind", "value": kind, "allowed": KIND_IDS})
    px = cs.parse_int(q.get("px"), "px", PX_MIN, PX_MAX, PX_DEFAULT)
    s = scene_by_id(scene_id)
    body, hdr = view_png(s, kind, q.get("variant") or None, px)
    etag = hdr.get("ETag")
    base = {**v3.CORS, "Cache-Control": "public, max-age=3600", "X-Evidence-Level": s.get("level") or "none",
            "X-Scene-Datetime": s.get("datetime") or "", **{k: str(v) for k, v in hdr.items()}}
    base["Access-Control-Expose-Headers"] = ("Content-Disposition, ETag, X-Evidence-Level, X-Scene-Datetime, "
                                             "X-View-Kind, X-View-Variant, X-View-Size, X-View-Scale, "
                                             "X-Detection-Pixels, X-Cache")
    if etag and request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=base)
    return Response(body, media_type="image/png", headers=base)


# ------------------------------------------------------------------ mount before the v3 catch-all
def _splice() -> None:
    """Put the studio routes into routes_v3.router right before its catch-all GET /api/v3/{rest:path}.
    FastAPI >= 0.13x includes routers lazily (_IncludedRouter reads original_router.routes by version), so the
    already-included v3 router picks the studio routes up; older FastAPI copies routes at include time — then
    only apps created after this import see them (the service creates its app after importing all routers)."""
    vr = v3.router
    if getattr(vr, "_studio_spliced", False):
        return
    n0 = len(vr.routes)
    pre = vr.prefix or ""
    for r in _studio.routes:
        vr.add_api_route(r.path[len(pre):], r.endpoint, methods=sorted(r.methods), summary=r.summary,
                         tags=["v3-studio"], name=r.name)
    new = vr.routes[n0:]
    del vr.routes[n0:]
    idx = next((i for i, r in enumerate(vr.routes) if getattr(r, "path", "") in ("/api/v3/{rest:path}", "/{rest:path}")
                and "GET" in (getattr(r, "methods", None) or set())), len(vr.routes))
    vr.routes[idx:idx] = new
    if hasattr(vr, "_mark_routes_changed"):
        vr._mark_routes_changed()
    vr._studio_spliced = True


_splice()
