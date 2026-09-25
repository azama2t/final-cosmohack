"""Zones, place card, observation calendar and image crops (lane L19, INBOX 1.4-1.5).

No FastAPI imports here. Everything is read from the service data root (docs/CONTRACTS.md) and, for crops at
native 10 m and false colour, from the live scene folder <live>/<region>/<date>/bands.tif
(<live> = $MACROPLASTIC_LIVE or <repo>/data/live; optional - without it crops come from rgb.png).

"Приоритет обследования" is a ranking by a formula (src/macroplastic/grid/zones.py), not a measured hazard;
the index is a share of observed water with debris signs on one image, not plastic mass.
"""
from __future__ import annotations

import functools
import io
import math
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import numpy as np

from . import core

REPO = core.SERVICE_DIR.parent
LIVE_ENV = "MACROPLASTIC_LIVE"
ACCENT = (255, 107, 74)  # #ff6b4a, same as prob.png "P >= threshold"
H3_RES = 8
REPEAT_BONUS = 0.5  # = macroplastic.grid.zones.REPEAT_BONUS
FORMULA = "score = flagged_water_px × mean_prob × (1 + 0.5 × (repeat_dates − 1))"
FORMULA_TEXT = ("Приоритет обследования = число пикселей воды с признаками мусора в ячейке × средняя уверенность "
                "модели на этих пикселях × бонус повторяемости (1 + 0.5 за каждую дополнительную дату, на которой в "
                "ячейке тоже были находки). Ранжируются только ячейки с наблюдаемой водой ≥ 50 %. Это порядок "
                "обследования, а не измеренная опасность и не масса пластика.")
LIMITATIONS = [
    "Индекс — доля наблюдаемой воды с признаками мусора на одном снимке (‰), не масса и не концентрация пластика.",
    "Приоритет обследования — ранжирование по формуле, не измеренная экологическая опасность.",
    "Согласие двух моделей ≠ проверка на месте; подтверждение возможно только обследованием.",
    "Разрешение Sentinel-2 — 10 м: видны только крупные скопления (пятна, полосы), не отдельные предметы.",
    "Пена, водоросли (саргассум), следы судов, блики и тонкие облака могут давать ложные срабатывания.",
    "Модель LightGBM обучена на ACOLITE rhorc (MARIDA/MADOS); на снимках L2A — сдвиг домена, вероятности не откалиброваны.",
    "Календарь — только реальные даты снимков, без интерполяции между ними.",
]
CAL_RULES = {
    "no_image": "на дату нет слоя выбранной модели (снимок не обработан этой моделью)",
    "unreliable": "облачность > 50 %, или дымка/блик (quality.glint_or_haze / haze), или наблюдаемой воды < 30 % "
                  "(доля ячеек H3 с водой, где индекс определён)",
    "detected": "надёжное наблюдение и ≥ 1 находка (пятно) модели",
    "clean": "надёжное наблюдение и 0 находок",
}
CLOUD_MAX = 0.5
OBS_WATER_MIN = 0.3
# /api/place statuses: date-level rule is the same as the calendar (date_reliability), then the cell itself
PLACE_RULES = {
    "no_image": CAL_RULES["no_image"],
    "unreliable": CAL_RULES["unreliable"] + " — то же правило, что в календаре; has_findings показывает, были ли "
                  "признаки в ячейке",
    "no_observation": "дата надёжна, но сама ячейка не наблюдалась (облако/тень над ней)",
    "found": "надёжное наблюдение и в ячейке есть пиксели с признаками мусора или пятно",
    "clean": "надёжное наблюдение ячейки, признаков нет",
}


# ---------------------------------------------------------------- small helpers
def _h3():
    import h3

    return h3


def cell_of(lon: float, lat: float, res: int = H3_RES) -> str:
    return _h3().latlng_to_cell(float(lat), float(lon), res)


def cell_center(h3id: str) -> tuple[float, float]:
    lat, lon = _h3().cell_to_latlng(h3id)
    return round(lon, 6), round(lat, 6)


def check_h3(h3id: str) -> str:
    try:
        ok = _h3().is_valid_cell(h3id)
    except Exception:
        ok = False
    if not ok:
        raise core.BadRequest(f"h3: недопустимый индекс ячейки {h3id!r}")
    return h3id


def live_dir(rid: str, date: str) -> Optional[Path]:
    base = Path(os.environ.get(LIVE_ENV) or (REPO / "data" / "live"))
    p = base / rid / date
    return p if p.is_dir() else None


def bands_path(rid: str, date: str) -> Optional[Path]:
    d = live_dir(rid, date)
    return d / "bands.tif" if d is not None and (d / "bands.tif").is_file() else None


def _opt(fn, *a):
    try:
        return fn(*a)
    except core.NotFound:
        return None


def date_cloud(st: core.Store, rid: str, date: str) -> Optional[float]:
    rj = _opt(st.read_json, f"{rid}/{date}/rgb.json") or {}
    c = rj.get("cloud_frac")
    if c is None:
        c = st.date_entry(rid, date).get("cloud_frac")
    return c


def model_threshold(st: core.Store, model: str, default: float = 0.5) -> float:
    m = (st.manifest().get("models") or {}).get(model) or {}
    try:
        return float(m.get("threshold", default))
    except (TypeError, ValueError):
        return default


def crop_url(rid: str, date: str, lon: float, lat: float, size_m: int = 1500, bands: str = "rgb",
             model: Optional[str] = None, highlight: int = 1, cell: Optional[str] = None) -> str:
    q = {"region": rid, "date": date, "lon": round(float(lon), 6), "lat": round(float(lat), 6),
         "size_m": int(size_m), "highlight": int(highlight)}
    if model:
        q["model"] = model
    if bands != "rgb":
        q["bands"] = bands
    if cell:
        q["h3"] = cell
    return "/api/crop?" + urlencode(q)


def _features(fc) -> list[dict]:
    return core._features(fc)


def det_lonlat(f: dict) -> tuple[Optional[float], Optional[float]]:
    p = f.get("properties") or {}
    if p.get("lon") is not None and p.get("lat") is not None:
        return float(p["lon"]), float(p["lat"])
    return core.centroid(f.get("geometry"))


def detections_in_cell(st: core.Store, rid: str, date: str, model: str, h3id: str) -> list[dict]:
    fc = st._optional(rid, date, model, "detections")
    out = []
    for f in _features(fc):
        lon, lat = det_lonlat(f)
        if lon is None:
            continue
        if cell_of(lon, lat) == h3id:
            p = f["properties"]
            out.append({"id": p.get("id"), "date": date, "model": model, "lon": lon, "lat": lat,
                        "area_m2": p.get("area_m2"), "mean_prob": p.get("mean_prob"), "max_prob": p.get("max_prob"),
                        "confirmed": p.get("confirmed"), "confirmed_by": p.get("confirmed_by")})
    return out


def h3_cell(st: core.Store, rid: str, date: str, model: str, h3id: str) -> Optional[dict]:
    fc = st._optional(rid, date, model, "h3")
    for f in _features(fc):
        if f["properties"].get("h3") == h3id:
            return f["properties"]
    return None


# ---------------------------------------------------------------- zone + "why"
def zone_score(flagged_px, mean_prob, repeat_dates) -> float:
    return float(flagged_px or 0) * float(mean_prob or 0.0) * (1.0 + REPEAT_BONUS * max(int(repeat_dates or 1) - 1, 0))


def explain(zone: dict, zones: list[dict], n_dates: int, quality: Optional[dict] = None) -> dict:
    px = int(zone.get("flagged_water_px") or 0)
    mp = float(zone.get("mean_prob") or 0.0)
    rep = int(zone.get("repeat_dates") or 1)
    rep_f = 1.0 + REPEAT_BONUS * max(rep - 1, 0)
    score = zone_score(px, mp, rep)
    terms = [
        {"name": "flagged_water_px", "label": "пиксели воды с признаками мусора (10×10 м)", "value": px,
         "weight": 1.0, "contribution": float(px)},
        {"name": "mean_prob", "label": "средняя уверенность модели на этих пикселях", "value": round(mp, 4),
         "weight": 1.0, "contribution": round(mp, 4)},
        {"name": "repeat_dates", "label": "на скольких датах района в ячейке были находки", "value": rep,
         "weight": REPEAT_BONUS, "contribution": round(rep_f, 3)},
    ]
    rank = zone.get("rank")
    scored = sorted((zone_score(z.get("flagged_water_px") or 0, z.get("mean_prob"), z.get("repeat_dates") or 1),
                     z.get("rank")) for z in zones)
    text = []
    area_ha = px * 100 / 1e4
    head = (f"{px} пикс. × {mp:.2f} × {rep_f:.1f} = {score:.1f}")
    if rank == 1:
        others = [s for s, r in scored if r != 1]
        if others:
            second = max(others)
            ratio = score / second if second > 0 else None
            text.append(f"Первая в списке: {head}" + (f" — в {ratio:.1f} раза больше, чем у второй зоны ({second:.1f})."
                                                   if ratio else "."))
        else:
            text.append(f"Единственная зона на эту дату: {head}.")
    elif rank:
        prev = next((zone_score(z.get("flagged_water_px") or 0, z.get("mean_prob"), z.get("repeat_dates") or 1)
                     for z in zones if z.get("rank") == rank - 1), None)
        text.append(f"{rank}-я в списке: {head}" + (f"; у зоны выше — {prev:.1f}." if prev is not None else "."))
    else:
        text.append(f"Ячейка не входит в топ зон на эту дату: {head}.")
    # dominant factor
    parts = [f"главный вклад — площадь признаков: {px} пикс. ≈ {area_ha:.1f} га наблюдаемой воды"]
    if mp >= 0.8:
        parts.append(f"уверенность модели высокая ({mp:.2f})")
    elif mp:
        parts.append(f"уверенность модели умеренная ({mp:.2f})")
    if rep > 1:
        parts.append(f"находки повторяются на {rep} из {n_dates} дат (множитель ×{rep_f:.1f})")
    elif n_dates > 1:
        parts.append(f"находки только на этой дате из {n_dates} (без бонуса повторяемости)")
    text.append("; ".join(parts).capitalize() + ".")
    if zone.get("n_confirmed"):
        text.append(f"{zone['n_confirmed']} находк(и) в ячейке подтверждены второй моделью (согласие моделей, "
                    "не проверка на месте).")
    if quality and (quality.get("haze") or quality.get("glint_or_haze")):
        text.append("Внимание: на снимке дымка/блик — находки могут быть завышены.")
    text.append("Это приоритет обследования (ранжирование по формуле), а не измеренная опасность и не масса пластика.")
    return {"formula": FORMULA, "formula_text": FORMULA_TEXT, "score": round(score, 3), "terms": terms,
            "text": " ".join(text)}


def zone_info(st: core.Store, rid: str, date: Optional[str], model: Optional[str], h3id: str) -> dict:
    check_h3(h3id)
    rid, date, model = st.resolve(rid, date, model)
    zj = st._optional(rid, date, model, "zones") or {}
    zones = zj.get("zones") or []
    zone = next((dict(z) for z in zones if z.get("h3") == h3id), None)
    cell = h3_cell(st, rid, date, model, h3id)
    if zone is None:
        if cell is None:
            raise core.NotFound(f"ячейка {h3id} не пересекает снимок {rid}/{date}")
        lon, lat = cell_center(h3id)
        zone = {"rank": None, "h3": h3id, "index": cell.get("share_permille"), "lon": lon, "lat": lat,
                "area_m2": round(float(cell.get("flagged_water_px") or 0) * 100.0, 1),
                "flagged_water_px": cell.get("flagged_water_px") or 0, "mean_prob": cell.get("mean_prob"),
                "repeat_dates": 1, "observed_frac": cell.get("observed_frac"),
                "n_detections": cell.get("n_detections") or 0}
    dets = detections_in_cell(st, rid, date, model, h3id)
    probs = [d["max_prob"] for d in dets if d.get("max_prob") is not None]
    n_dates = sum(1 for d in st.region_dates(rid) if model in (d.get("models") or [model]))
    quality = zj.get("quality") or st.date_entry(rid, date).get("quality")
    out = {
        "region": rid, "date": date, "model": model, "threshold": zj.get("threshold", model_threshold(st, model)),
        "rank": zone.get("rank"), "h3": h3id, "lon": zone.get("lon"), "lat": zone.get("lat"),
        "index": zone.get("index"), "area_m2": zone.get("area_m2"),
        "flagged_water_px": zone.get("flagged_water_px"),
        "n_detections": zone.get("n_detections", len(dets)),
        "mean_prob": zone.get("mean_prob"), "max_prob": max(probs) if probs else None,
        "repeat_dates": zone.get("repeat_dates"), "n_dates": n_dates,
        "observed_frac": zone.get("observed_frac", (cell or {}).get("observed_frac")),
        "cloud_frac": date_cloud(st, rid, date), "quality": quality,
        "reason": zone.get("reason"),
        "detections": dets,
        "why": explain(zone, zones, n_dates, quality),
        "crop": crop_url(rid, date, zone["lon"], zone["lat"], model=model, cell=h3id),
        "crop_false_color": (crop_url(rid, date, zone["lon"], zone["lat"], model=model, bands="false", cell=h3id)
                             if bands_path(rid, date) else None),
        "place": f"/api/place?region={rid}&h3={h3id}&model={model}",
        "pdf": f"/api/place_report.pdf?region={rid}&h3={h3id}&model={model}&date={date}",
    }
    if "n_confirmed" in zone:
        out["n_confirmed"] = zone["n_confirmed"]
    elif dets and any(d.get("confirmed") is not None for d in dets):
        out["n_confirmed"] = sum(1 for d in dets if d.get("confirmed"))
    return out


# ---------------------------------------------------------------- place card
def place_info(st: core.Store, rid: str, h3id: str, model: Optional[str] = None) -> dict:
    check_h3(h3id)
    reg = st.region(rid)
    dates = st.region_dates(rid)
    if not model:
        all_models = {m for d in dates for m in (d.get("models") or [])}
        model = "mdd" if "mdd" in all_models or not all_models else sorted(all_models)[0]
    lon, lat = cell_center(h3id)
    history = []
    for d in dates:
        date = d["date"]
        models = d.get("models") or []
        row = {"date": date, "scene_id": d.get("scene_id"), "cloud_frac": d.get("cloud_frac"),
               "quality": d.get("quality"), "model": model}
        rel = date_reliability(st, rid, d, model)
        row.update(quality_flags=rel["quality_flags"], observed_frac_water=rel["observed_frac_water"])
        if (models and model not in models) or not rel["has_layer"]:
            row.update(status="no_image", index=None, n_detections=0, detections=[])
        else:
            cell = h3_cell(st, rid, date, model, h3id)
            dets = detections_in_cell(st, rid, date, model, h3id)
            has_find = bool(cell and cell.get("share_permille") is not None
                            and ((cell.get("flagged_water_px") or 0) > 0 or dets))
            if rel["unreliable"]:
                # same rule as /api/calendar: the date as a whole is not trusted (haze/glint, clouds, little water)
                status = "unreliable"
                row["reason"] = "; ".join(rel["reasons"])
            elif cell is None or cell.get("share_permille") is None:
                status = "no_observation"
            elif has_find:
                status = "found"
            else:
                status = "clean"
            row["has_findings"] = has_find or bool(dets)
            zj = st._optional(rid, date, model, "zones") or {}
            z = next((z for z in zj.get("zones") or [] if z.get("h3") == h3id), None)
            row.update(status=status, index=(cell or {}).get("share_permille"),
                       observed_frac=(cell or {}).get("observed_frac"),
                       flagged_water_px=(cell or {}).get("flagged_water_px"),
                       mean_prob=(cell or {}).get("mean_prob"),
                       n_detections=len(dets), detections=dets,
                       max_prob=max((x["max_prob"] for x in dets if x.get("max_prob") is not None), default=None),
                       zone_rank=(z or {}).get("rank"))
            if any(x.get("confirmed") is not None for x in dets):
                row["n_confirmed"] = sum(1 for x in dets if x.get("confirmed"))
            others = {}
            for om in models:
                if om == model:
                    continue
                od = detections_in_cell(st, rid, date, om, h3id)
                others[om] = {"n_detections": len(od)}
            row["other_models"] = others
        row["crop"] = crop_url(rid, date, lon, lat, model=model, cell=h3id)
        history.append(row)
    found = [h for h in history if h["status"] == "found"]
    observed = [h for h in history if h["status"] in ("found", "clean")]
    unreliable = [h for h in history if h["status"] == "unreliable"]
    man = st.manifest()
    return {
        "region": rid, "region_name": reg.get("name"), "country": reg.get("country"), "h3": h3id, "res": H3_RES,
        "lon": lon, "lat": lat, "boundary": [[round(x, 6), round(y, 6)] for y, x in _h3().cell_to_boundary(h3id)],
        "model": model, "model_name": ((man.get("models") or {}).get(model) or {}).get("name"),
        "threshold": model_threshold(st, model),
        "summary": {"n_dates": len(history), "n_observed": len(observed), "n_found": len(found),
                    "n_unreliable": len(unreliable),
                    "n_unreliable_with_findings": sum(1 for h in unreliable if h.get("has_findings")),
                    "first_found": found[0]["date"] if found else None,
                    "last_found": found[-1]["date"] if found else None,
                    "n_detections_total": sum(h.get("n_detections") or 0 for h in history),
                    "max_index": max((h["index"] for h in history if h.get("index") is not None), default=None)},
        "history": history,
        "formula": FORMULA, "formula_text": FORMULA_TEXT,
        "sources": man.get("sources") or [],
        "limitations": LIMITATIONS,
        "status_rules": PLACE_RULES,
        "pdf": f"/api/place_report.pdf?region={rid}&h3={h3id}&model={model}",
    }


# ---------------------------------------------------------------- calendar
def date_reliability(st: core.Store, rid: str, d: dict, model: str) -> dict:
    """ONE rule for «ненадёжно» (used by /api/calendar AND /api/place, L27).

    A date is `unreliable` when any of: cloud_frac > 50 %; quality flag glint_or_haze / haze; observed water < 30 %
    (share of water H3 cells where the index is defined). `has_layer` is False when the model has no layer on the
    date (→ status `no_image` in both endpoints).
    """
    date = d["date"]
    models = d.get("models") or []
    q = d.get("quality") or {}
    flags = [k for k in ("glint_or_haze", "haze") if q.get(k)]
    cloud = date_cloud(st, rid, date)
    cells = _features(st._optional(rid, date, model, "h3")) if (not models or model in models) else []
    out = {"cloud_frac": cloud, "quality_flags": flags, "observed_frac_water": None, "has_layer": bool(cells),
           "reasons": [], "unreliable": False}
    if not cells:
        return out
    water = [c["properties"] for c in cells if (c["properties"].get("observed_water_px") or 0) > 0
             or c["properties"].get("share_permille") is not None]
    obs = [c for c in water if c.get("share_permille") is not None]
    ofw = round(len(obs) / len(water), 3) if water else 0.0
    out["observed_frac_water"] = ofw
    reasons = []
    if cloud is not None and cloud > CLOUD_MAX:
        reasons.append(f"облачность {cloud * 100:.0f} % > 50 %")
    if flags:
        reasons.append("дымка/блик — находки могут быть завышены")
    if ofw < OBS_WATER_MIN:
        reasons.append(f"наблюдаемой воды {ofw * 100:.0f} % < 30 %")
    out["reasons"] = reasons
    out["unreliable"] = bool(reasons)
    return out


def calendar(st: core.Store, rid: str, model: Optional[str] = None) -> list[dict]:
    dates = st.region_dates(rid)
    if not model:
        all_models = {m for d in dates for m in (d.get("models") or [])}
        model = "mdd" if "mdd" in all_models or not all_models else sorted(all_models)[0]
    out = []
    for d in dates:
        date = d["date"]
        rel = date_reliability(st, rid, d, model)
        row = {"date": date, "model": model, "scene_id": d.get("scene_id"), "cloud_frac": rel["cloud_frac"],
               "quality_flags": rel["quality_flags"], "n_detections": 0, "observed_frac_water": None}
        if not rel["has_layer"]:
            row.update(status="no_image", reason=f"нет слоя модели {model} на эту дату")
            out.append(row)
            continue
        det = _features(st._optional(rid, date, model, "detections"))
        row["observed_frac_water"] = rel["observed_frac_water"]
        row["n_detections"] = len(det)
        if any("confirmed" in (f.get("properties") or {}) for f in det):
            row["n_confirmed"] = sum(1 for f in det if f["properties"].get("confirmed"))
        if rel["unreliable"]:
            row.update(status="unreliable", reason="; ".join(rel["reasons"]))
        elif det:
            row.update(status="detected", reason=f"{len(det)} находок модели {model}")
        else:
            row.update(status="clean", reason="надёжное наблюдение, находок нет")
        out.append(row)
    return out


# ---------------------------------------------------------------- crops
def nice_scale(size_m: float) -> int:
    target = size_m / 4
    for v in (20, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000):
        if v >= target * 0.7:
            return v
    return 10000


@functools.lru_cache(maxsize=4)
def font(size: int):
    from PIL import ImageFont

    cands = []
    try:
        import matplotlib

        cands.append(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf")
    except Exception:
        pass
    cands += [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf",
              Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    for p in cands:
        if p.is_file():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def _stretch(rgb: np.ndarray) -> np.ndarray:
    """(3,h,w) reflectance -> uint8 (h,w,3); joint 1..99.5 percentile stretch over the crop (span >= 0.12), gamma 1/1.4."""
    ok = np.isfinite(rgb).all(0)
    out = np.zeros(rgb.shape[1:] + (3,), np.uint8)
    if not ok.any():
        return out
    vals = rgb[:, ok]
    lo, hi = np.percentile(vals, 1), np.percentile(vals, 99.5)
    hi = max(hi, lo + 0.12)  # uniform open water stays dark blue instead of being stretched to cyan
    x = np.clip((rgb - lo) / (hi - lo), 0, 1) ** (1 / 1.4)
    x = np.nan_to_num(x, nan=0.0)
    return (np.moveaxis(x, 0, -1) * 255).round().astype(np.uint8)


def _from_bands(path: Path, lon: float, lat: float, size_m: float, which: str):
    import rasterio
    from rasterio.warp import transform as wtransform
    from rasterio.windows import Window

    with rasterio.open(path) as ds:
        xs, ys = wtransform("EPSG:4326", ds.crs, [lon], [lat])
        inv = ~ds.transform
        col, row = inv @ (xs[0], ys[0])
        res = abs(ds.transform.a)
        half = int(round(size_m / res / 2))
        c0, r0 = int(math.floor(col)) - half, int(math.floor(row)) - half
        n = 2 * half
        names = list(ds.descriptions)
        pick = {"rgb": ("B4", "B3", "B2"), "false": ("B8", "B4", "B3"), "swir": ("B11", "B8", "B4")}[which]
        idx = [names.index(b) + 1 for b in pick]
        arr = ds.read(idx, window=Window(c0, r0, n, n), boundless=True, fill_value=np.nan).astype(np.float32)
        crs = ds.crs
        tr = ds.transform

    def to_px(lons, lats):
        xx, yy = wtransform("EPSG:4326", crs, list(lons), list(lats))
        cc = (np.asarray(xx, float) - tr.c) / tr.a  # north-up grid (no rotation terms)
        rr = (np.asarray(yy, float) - tr.f) / tr.e
        return cc - c0, rr - r0

    return arr, n, n, to_px, res


def _from_png(st: core.Store, rid: str, date: str, lon: float, lat: float, size_m: float):
    from PIL import Image

    rj = st.read_json(f"{rid}/{date}/rgb.json")
    w, s, e, n = rj["bounds"]
    p = core.safe_path(st.root, f"{rid}/{date}/rgb.png")
    im = np.asarray(Image.open(p).convert("RGBA"))
    H, W = im.shape[:2]
    dlat = size_m / 2 / 111320.0
    dlon = dlat / max(math.cos(math.radians(lat)), 0.05)
    fx = lambda lo: (np.asarray(lo, float) - w) / (e - w) * W  # noqa: E731
    fy = lambda la: (n - np.asarray(la, float)) / (n - s) * H  # noqa: E731
    c0, c1 = int(math.floor(fx(lon - dlon))), int(math.ceil(fx(lon + dlon)))
    r0, r1 = int(math.floor(fy(lat + dlat))), int(math.ceil(fy(lat - dlat)))
    h, wd = max(r1 - r0, 1), max(c1 - c0, 1)
    out = np.zeros((h, wd, 4), np.uint8)
    ys0, ys1, xs0, xs1 = max(r0, 0), min(r1, H), max(c0, 0), min(c1, W)
    if ys1 > ys0 and xs1 > xs0:
        out[ys0 - r0:ys1 - r0, xs0 - c0:xs1 - c0] = im[ys0:ys1, xs0:xs1]
    rgb = out[..., :3].copy()
    rgb[out[..., 3] == 0] = (11, 22, 34)

    def to_px(lons, lats):
        return fx(lons) - c0, fy(lats) - r0

    res_m = (e - w) / W * 111320.0 * math.cos(math.radians(lat))
    return rgb, wd, h, to_px, res_m


def _polys(geom: dict) -> list[list]:
    t = geom.get("type")
    if t == "Polygon":
        return [geom["coordinates"][0]]
    if t == "MultiPolygon":
        return [p[0] for p in geom["coordinates"]]
    return []


def render_crop(st: core.Store, rid: str, date: Optional[str], lon: float, lat: float, size_m: float = 1500,
                highlight: bool = True, bands: str = "rgb", model: Optional[str] = None, out_px: int = 480,
                src: str = "auto", cell: Optional[str] = None) -> bytes:
    """PNG crop around (lon, lat) with detection contours (#ff6b4a), optional H3 cell outline and a scale bar."""
    from PIL import Image, ImageDraw

    d = st.date_entry(rid, date)
    date = d["date"]
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise core.BadRequest("lon/lat вне диапазона")
    size_m = float(min(max(size_m, 200), 10000))
    out_px = int(min(max(out_px, 128), 1024))
    if bands not in ("rgb", "false", "swir"):
        raise core.BadRequest("bands: rgb | false | swir")
    bp = bands_path(rid, date)
    if bands != "rgb" and bp is None:
        raise core.NotFound(f"нет bands.tif для {rid}/{date}: доступна только RGB-вырезка")
    if bp is not None and (bands != "rgb" or src in ("auto", "bands")):
        arr, wd, h, to_px, res = _from_bands(bp, lon, lat, size_m, bands)
        rgb = _stretch(arr)
        label = {"rgb": "RGB (B4/B3/B2)", "false": "ложный цвет B8/B4/B3", "swir": "ложный цвет B11/B8/B4"}[bands]
    else:
        rgb, wd, h, to_px, res = _from_png(st, rid, date, lon, lat, size_m)
        label = "RGB (обзорный слой)"
    scale = out_px / max(wd, h)
    W2, H2 = max(int(round(wd * scale)), 1), max(int(round(h * scale)), 1)
    im = Image.fromarray(rgb, "RGB").resize((W2, H2), Image.NEAREST)
    dr = ImageDraw.Draw(im, "RGBA")
    if highlight:
        models = [model] if model else (d.get("models") or [])
        for m in models:
            fc = st._optional(rid, date, m, "detections")
            for f in _features(fc):
                for ring in _polys(f.get("geometry") or {}):
                    ring = np.asarray(ring, float)
                    if not len(ring):
                        continue
                    xs, ys = to_px(ring[:, 0], ring[:, 1])
                    xs, ys = xs * scale, ys * scale
                    if xs.max() < -5 or ys.max() < -5 or xs.min() > W2 + 5 or ys.min() > H2 + 5:
                        continue
                    pts = list(zip(xs.tolist(), ys.tolist()))
                    if len(pts) >= 2:
                        dr.line(pts + [pts[0]], fill=ACCENT + (255,), width=2)
    if cell:
        ring = np.asarray([(x, y) for y, x in _h3().cell_to_boundary(cell)], float)
        xs, ys = to_px(ring[:, 0], ring[:, 1])
        pts = list(zip((xs * scale).tolist(), (ys * scale).tolist()))
        dr.line(pts + [pts[0]], fill=(255, 255, 255, 150), width=1)
    # centre marker (thin ticks, the target itself stays visible)
    cx, cy = W2 / 2, H2 / 2
    for a, b in (((cx - 14, cy), (cx - 6, cy)), ((cx + 6, cy), (cx + 14, cy)),
                 ((cx, cy - 14), (cx, cy - 6)), ((cx, cy + 6), (cx, cy + 14))):
        dr.line([a, b], fill=(255, 255, 255, 200), width=1)
    # scale bar
    L = nice_scale(size_m)
    px_len = L / res * scale
    f = font(13)
    txt = f"{L} м" if L < 1000 else f"{L / 1000:g} км"
    x0, y0 = 12, H2 - 16
    dr.rectangle([x0 - 6, y0 - 22, x0 + px_len + 60, y0 + 8], fill=(11, 22, 34, 170))
    dr.rectangle([x0, y0 - 3, x0 + px_len, y0 + 2], fill=(255, 255, 255, 255))
    dr.text((x0 + px_len + 6, y0 - 9), txt, font=f, fill=(255, 255, 255, 255))
    dr.text((x0, y0 - 20), f"{date} · {label}", font=font(11), fill=(223, 232, 241, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=False)
    return buf.getvalue()


@functools.lru_cache(maxsize=256)
def _crop_cached(root: str, mtime: float, live: str, rid, date, lon, lat, size_m, highlight, bands, model, out_px, src, cell):
    st = core.Store(Path(root))
    return render_crop(st, rid, date, lon, lat, size_m, highlight, bands, model, out_px, src, cell)


def crop_png(st: core.Store, rid: str, date: Optional[str], lon: float, lat: float, size_m: float = 1500,
             highlight: bool = True, bands: str = "rgb", model: Optional[str] = None, out_px: int = 480,
             src: str = "auto", cell: Optional[str] = None) -> bytes:
    if st.root is None:
        raise core.NotFound("нет данных")
    date = st.date_entry(rid, date)["date"]
    if model:
        st.resolve(rid, date, model)
    if cell:
        check_h3(cell)
    mt = (st.root / "manifest.json").stat().st_mtime
    live = str(bands_path(rid, date))  # cache key: live scene folder may appear/disappear
    return _crop_cached(str(st.root), mt, live, rid, date, round(float(lon), 6), round(float(lat), 6), float(size_m),
                        bool(highlight), bands, model, int(out_px), src, cell)

