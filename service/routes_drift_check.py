"""Drift check: drift forecast check against the next snapshot + compact current/wind fields for animated particles.

GET /api/drift_check                         -> summary (reports/drift_check.json) + pairs with data/figure URLs
GET /api/drift_check/{region}/{date1}[?date2=] -> particle cloud at t2, 90 % contour (GeoJSON), 2nd-snapshot
                                                detections (GeoJSON, flag hit), zero-drift baseline
GET /api/drift_check/figure/{name}.png       -> reports/figures/drift_check_<...>.png
GET /api/flow?region=&date=&kind=currents|wind&t=<hour 0..72>
                                             -> u/v grid (<= 64x64, rows north->south) from data_cache/forcing/*.nc
GET /api/flow/list                           -> region/date pairs that have forcing files

Roots: $MACROPLASTIC_DRIFT_CHECK (folder with drift_check.json, drift_check/pairs/, figures/) else <repo>/reports;
$MACROPLASTIC_FORCING else <repo>/data_cache/forcing. Contract: docs/CONTRACTS.md "Проверка дрейфа и поля течений/ветра".
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import threading
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter(tags=["drift"])

REPO = Path(__file__).resolve().parents[1]
ID_RE = re.compile(r"^[a-z0-9_]{1,40}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_SIDE = 64
EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
KINDS = {"currents": ("x_sea_water_velocity", "y_sea_water_velocity", 3),
         "wind": ("x_wind", "y_wind", 2)}


def check_root() -> Path:
    return Path(os.environ.get("MACROPLASTIC_DRIFT_CHECK") or REPO / "reports")


def forcing_root() -> Path:
    return Path(os.environ.get("MACROPLASTIC_FORCING") or REPO / "data_cache" / "forcing")


def _ids(region: str, date: str):
    if not ID_RE.match(region or ""):
        raise HTTPException(422, "плохой region")
    if not DATE_RE.match(date or ""):
        raise HTTPException(422, "дата должна быть YYYY-MM-DD")


def _load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(404, f"нет файла {p.name}") from None


# ---------------------------------------------------------------- drift check
def _pair_urls(p: dict) -> dict:
    stem = f"{p['region']}_{p['date1']}_{p['date2']}"
    return {"url": f"/api/drift_check/{p['region']}/{p['date1']}?date2={p['date2']}",
            "figure_url": f"/api/drift_check/figure/drift_check_{stem}.png"}


@router.get("/api/drift_check", summary="Проверка прогноза дрейфа следующим снимком: сводка и пары")
def drift_check_summary():
    s = _load_json(check_root() / "drift_check.json")
    s = dict(s)
    s["pairs"] = [{**p, **_pair_urls(p)} for p in s.get("pairs", [])]
    return s


@router.get("/api/drift_check/figure/{name}", summary="Картинка пары (PNG)")
def drift_check_figure(name: str):
    if not re.match(r"^drift_check_[a-z0-9_]+_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}\.png$", name):
        raise HTTPException(422, "плохое имя картинки")
    p = check_root() / "figures" / name
    if not p.is_file():
        raise HTTPException(404, "нет картинки")
    return FileResponse(p, media_type="image/png")


@router.get("/api/drift_check/{region}/{date1}", summary="Облако частиц на момент 2-го снимка, контур, находки, база")
def drift_check_pair(region: str, date1: str, date2: str | None = Query(None, description="YYYY-MM-DD; "
                                                                                            "по умолчанию — ближайший")):
    _ids(region, date1)
    if date2 is not None and not DATE_RE.match(date2):
        raise HTTPException(422, "date2 должна быть YYYY-MM-DD")
    root = check_root()
    s = _load_json(root / "drift_check.json")
    rows = [p for p in s.get("pairs", []) if p["region"] == region and p["date1"] == date1
            and (date2 is None or p["date2"] == date2)]
    if not rows:
        raise HTTPException(404, f"нет пары для {region}/{date1}" + (f"/{date2}" if date2 else ""))
    row = sorted(rows, key=lambda p: p["dt_h"])[0]
    d = _load_json(root / "drift_check" / "pairs" / f"{region}_{date1}_{row['date2']}.json")

    def feat(geom, **props):
        return {"type": "Feature", "geometry": geom, "properties": props} if geom else None

    return {
        "region": region, "date1": date1, "date2": row["date2"], "dt_h": row["dt_h"], "hour_used": row["hour_used"],
        "start_time": d["start_time"], "time2": d["time2"], "criterion": s.get("criterion"),
        "contour_pct": d["contour_pct"], "cloud_source": d["cloud_source"], "scene2": d["scene2"],
        "stats": {k: row.get(k) for k in ("n_det2", "hits", "hits_baseline", "hits_shift", "contour_area_km2",
                                          "baseline_area_km2", "expected_random_hits", "median_dist_forecast_km",
                                          "median_dist_baseline_km", "mean_displacement_km", "stranded_pct_t2",
                                          "n_det2_lgbm", "hits_lgbm", "hits_lgbm_baseline")},
        "particles": d["particles_t2"], "stranded": d.get("stranded_t2", []),
        "contour": feat(d["contour"], kind="forecast", pct=d["contour_pct"], bandwidth_m=d["bandwidth_m"]["forecast"]),
        "detections": d["detections2"],
        "baseline": {"particles": d["particles_t0"],
                     "contour": feat(d["baseline_contour"], kind="baseline_zero_drift", pct=d["contour_pct"],
                                     bandwidth_m=d["bandwidth_m"]["baseline"])},
        "shift_contour": feat(d.get("shift_contour"), kind="baseline_shift_back", pct=d["contour_pct"]),
        "other_pairs": [p["date2"] for p in rows if p["date2"] != row["date2"]],
        **_pair_urls(row),
    }


# ---------------------------------------------------------------- flow fields
@lru_cache(maxsize=48)
def _load_nc(path: str, mtime: float):
    import xarray as xr
    with xr.open_dataset(path, decode_times=False) as ds:
        kind = "wind" if "x_wind" in ds else "currents"
        un, vn, _ = KINDS[kind]
        return {"t": np.asarray(ds["time"].values, "float64"), "lat": np.asarray(ds["lat"].values, "float64"),
                "lon": np.asarray(ds["lon"].values, "float64"),
                "u": np.asarray(ds[un].values, "float32"), "v": np.asarray(ds[vn].values, "float32")}


_START_RE = re.compile(r'"start_time"\s*:\s*"([^"]+)"')


def _start_time(region: str, date: str, side: dict) -> tuple[dt.datetime, str]:
    """Drift start = scene time (drift.json start_time); fallback: forcing window t0 + 3 h."""
    for p in (REPO / "data" / "live" / region / date / "drift.json",
              REPO / "service" / "data" / region / date / "drift.json"):
        if p.is_file():
            with p.open("r", encoding="utf-8") as f:
                m = _START_RE.search(f.read(600))
            if m:
                return dt.datetime.fromisoformat(m.group(1).replace("Z", "+00:00")), "drift.json"
    t0 = dt.datetime.fromisoformat(side["window"][4])
    return t0 + dt.timedelta(hours=3), "окно форсинга (t0 + 3 ч)"


_FLOW_CACHE: OrderedDict = OrderedDict()
_FLOW_LOCK = threading.Lock()


def flow_field(region: str, date: str, kind: str, t: float) -> dict:
    key = (region, date, kind, round(float(t), 3))
    with _FLOW_LOCK:
        if key in _FLOW_CACHE:
            _FLOW_CACHE.move_to_end(key)
            return _FLOW_CACHE[key]
    nc = forcing_root() / f"{region}_{date}_{kind}.nc"
    if not nc.is_file():
        raise HTTPException(404, f"нет поля {kind} для {region}/{date}")
    side_p = nc.with_suffix(".json")
    side = json.loads(side_p.read_text(encoding="utf-8")) if side_p.is_file() else {}
    g = _load_nc(str(nc), nc.stat().st_mtime)
    start, start_src = _start_time(region, date, side) if side else (None, None)
    if start is None:
        start = EPOCH + dt.timedelta(hours=float(g["t"][0]) + 3)
        start_src = "первый шаг поля + 3 ч"
    th = (start - EPOCH).total_seconds() / 3600 + float(t)
    tt = g["t"]
    avail = [round(float(tt[0] - (start - EPOCH).total_seconds() / 3600), 2),
             round(float(tt[-1] - (start - EPOCH).total_seconds() / 3600), 2)]
    if th <= tt[0]:
        i0 = i1 = 0; w = 0.0
    elif th >= tt[-1]:
        i0 = i1 = len(tt) - 1; w = 0.0
    else:
        i1 = int(np.searchsorted(tt, th)); i0 = i1 - 1
        w = float((th - tt[i0]) / (tt[i1] - tt[i0]))
    u = (1 - w) * g["u"][i0] + w * g["u"][i1]
    v = (1 - w) * g["v"][i0] + w * g["v"][i1]
    lat, lon = g["lat"], g["lon"]
    sy = max(1, math.ceil(len(lat) / MAX_SIDE))
    sx = max(1, math.ceil(len(lon) / MAX_SIDE))
    lat, lon, u, v = lat[::sy], lon[::sx], u[::sy, ::sx], v[::sy, ::sx]
    # rows north -> south (earth.nullschool / GRIB order), columns west -> east
    if lat[0] < lat[-1]:
        lat, u, v = lat[::-1], u[::-1], v[::-1]
    nd = KINDS[kind][2]

    def pack(a):
        a = np.round(a.astype("float64"), nd).ravel()
        return [None if not math.isfinite(x) else x for x in a.tolist()]

    spd = np.hypot(u, v)
    dx = float(np.median(np.diff(lon))) if len(lon) > 1 else 0.0
    dy = float(np.median(np.abs(np.diff(lat)))) if len(lat) > 1 else 0.0
    out = {
        "region": region, "date": date, "kind": kind, "t": float(t),
        "time": (EPOCH + dt.timedelta(hours=th)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "start_time": start.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "start_time_source": start_src,
        "source": side.get("source"), "units": "m/s",
        "nx": int(len(lon)), "ny": int(len(lat)), "lo1": round(float(lon[0]), 4), "la1": round(float(lat[0]), 4),
        "lo2": round(float(lon[-1]), 4), "la2": round(float(lat[-1]), 4), "dx": round(dx, 5), "dy": round(dy, 5),
        "bbox": [round(float(lon.min()), 4), round(float(lat.min()), 4), round(float(lon.max()), 4),
                 round(float(lat.max()), 4)],
        "order": "строки с севера на юг (la1 → la2), столбцы с запада на восток (lo1 → lo2); u[k], v[k], k = row*nx + col",
        "u": pack(u), "v": pack(v), "null": "суша / нет данных",
        "max_speed": round(float(np.nanmax(spd)), nd) if np.isfinite(spd).any() else None,
        "t_available_h": avail, "interp": "линейно по времени между соседними шагами поля; вне диапазона — крайний шаг",
    }
    with _FLOW_LOCK:
        _FLOW_CACHE[key] = out
        while len(_FLOW_CACHE) > 512:
            _FLOW_CACHE.popitem(last=False)
    return out


@router.get("/api/flow", summary="Сетка u/v течений или ветра (≤ 64×64) на час t прогноза дрейфа")
def flow(region: str = Query(..., examples=["honduras"]), date: str = Query(..., examples=["2026-05-30"]),
         kind: str = Query("currents", description="currents | wind"),
         t: float = Query(0, description="час от начала прогноза, 0..72")):
    _ids(region, date)
    if kind not in KINDS:
        raise HTTPException(422, "kind: currents | wind")
    if not (0 <= t <= 72) or not math.isfinite(t):
        raise HTTPException(422, "t: час 0..72")
    return JSONResponse(flow_field(region, date, kind, t), headers={"Cache-Control": "public, max-age=3600"})


@router.get("/api/flow/list", summary="Для каких района/даты есть поля течений и ветра")
def flow_list():
    items = {}
    for p in sorted(forcing_root().glob("*_*.nc")):
        m = re.match(r"^([a-z0-9_]+)_(\d{4}-\d{2}-\d{2})_(currents|wind)\.nc$", p.name)
        if m:
            items.setdefault((m.group(1), m.group(2)), []).append(m.group(3))
    return [{"region": r, "date": d, "kinds": sorted(k)} for (r, d), k in sorted(items.items())]
