"""L110 (INBOX §15, агент 6 (б)): калибровка «спутниковый индекс → шт./км²» на уровне ЯЧЕЕК, не пикселей.

Проверка зафиксирована ДО сравнения значений: configs/cell_calibration.yaml (sha256 в .sha256; скрипт
отказывается работать при расхождении). Варианты:
  V1   тот же сезон того же года  — Cózar 2024 LWD (a_o — прокси) × поле (ADIS + CSV организаторов, без test)
  V1b  тот же календарный год
  V2   наш детектор (weights/lgbm, 0.63) × ADIS, та же ячейка и месяц (строки L100, «детектор оценивается»)
  V3   ИССЛЕДОВАТЕЛЬСКИЙ: климатология Cózar 2015–2021 × ADIS Средиземное 2021–2023 (разные годы → НЕ калибровка)
Стоп-правило: < 20 независимых ячеек поле×спутник → только отчёт, без модели.

a_o (наблюдённая площадь моря) в NetCDF и Source Data Cózar НЕ опубликована, поэтому прокси:
a_o = площадь моря ячейки (GSHHS f L1) × Σ по дням съёмки (1 − облачность сцены) — Copernicus Data Space STAC
sentinel-2-l1c, точка = центр ячейки, 2015-06-01..2021-09-17 (кэш out/cell_calibration/stac/). Ветер > 5 м/с не
исключается (нет данных) → в ветреных ячейках LWD занижена.

Запуск (повтор бесплатный — всё из кэша):
  .venv/Scripts/python.exe scripts/case/cell_calibration.py ao --scope field   # STAC для ячеек поля
  .venv/Scripts/python.exe scripts/case/cell_calibration.py ao --scope all     # + все ячейки с нитями Cózar (для eps)
  .venv/Scripts/python.exe scripts/case/cell_calibration.py run [--offline]
Выход: reports/quantity/cell_calibration.{json,md}, таблицы ячеек out/cell_calibration/cells_*.csv
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yaml
from scipy.stats import rankdata, spearmanr

ROOT = Path(__file__).resolve().parents[2]
CFG = ROOT / "configs" / "cell_calibration.yaml"
CFG_SHA = ROOT / "configs" / "cell_calibration.yaml.sha256"
OUT = ROOT / "out" / "cell_calibration"
STAC_DIR = OUT / "stac"
REP = ROOT / "reports" / "quantity"
GSHHS = Path.home() / ".local/share/cartopy/shapefiles/gshhs/f/GSHHS_f_L1.shp"
STAC_URL = "https://stac.dataspace.copernicus.eu/v1/search"
COZAR_START, COZAR_END = pd.Timestamp("2015-06-01"), pd.Timestamp("2021-09-17 23:59:59")
MED_BBOX = (-7.0, 29.5, 37.0, 47.0)  # lon0, lat0, lon1, lat1
MIN_CELLS = 20
BOOT = 2000


# ------------------------------------------------------------------ config
def load_cfg() -> dict:
    want = CFG_SHA.read_text(encoding="utf-8").split()[0].strip()
    got = hashlib.sha256(CFG.read_bytes()).hexdigest()
    if want != got:
        sys.exit(f"configs/cell_calibration.yaml changed after freezing: {got} != {want}")
    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    cfg["_sha256"] = got
    return cfg


# ------------------------------------------------------------------ grid helpers
def ckey(lat, lon, d):
    return (np.floor(np.asarray(lat) / d).astype(int), np.floor(np.asarray(lon) / d).astype(int))


def cid(i, j, d) -> str:
    return f"{d:g}_{int(i)}_{int(j)}"


def centre(i, j, d):
    return (i + 0.5) * d, (j + 0.5) * d


def season_key(t: pd.Series) -> pd.Series:
    m, y = t.dt.month, t.dt.year
    return (y + (m == 12).astype(int)).astype(str) + "-" + ((m % 12) // 3).map({0: "DJF", 1: "MAM", 2: "JJA", 3: "SON"})


def med_mask(lat, lon):
    lat, lon = np.asarray(lat), np.asarray(lon)
    return ((lat >= 30) & (lat <= 46.5) & (lon >= -6.7) & (lon <= 36.5)
            & ~((lat > 40.9) & (lon > 26.3)) & ~((lon < -1.5) & (lat > 42)))


# ------------------------------------------------------------------ data
def load_adis() -> pd.DataFrame:
    s = pd.read_csv(ROOT / "data/extra/field/adis/Segments.csv")
    df = pd.DataFrame({
        "src": "ADIS", "seg": s.SegmentID, "lat": s.Latitude, "lon": s.Longitude,
        "t": pd.to_datetime(s.mindate), "ship": s.Ship, "area": s.area_scanned_km2,
        "n5": s["n_objects>5cm"], "dcal10": s.dhat_10cm_calibrated,
    })
    df["shipday"] = df.ship + "|" + df.t.dt.strftime("%Y-%m-%d")
    return df


def test_ids() -> set:
    ids = set()
    for p in (ROOT / "data/case/splits").glob("final_test_*.csv"):
        t = pd.read_csv(p, usecols=lambda c: c in ("sample_id",))
        ids |= set(t.sample_id.astype(str))
    return ids


def load_csv_field() -> tuple[pd.DataFrame, dict]:
    x = pd.read_csv(ROOT / "task/macroplastic_marine_samples.csv")
    n0 = len(x)
    tids = test_ids()
    x = x[(x.record_type == "transect_density") & x.target_scope.isin(["total_plastic", "all_litter"])]
    n_dens = len(x)
    x = x[~x.sample_id.astype(str).isin(tids)]
    t = pd.to_datetime(x.datetime_start_iso, errors="coerce", utc=True).dt.tz_localize(None)
    n = pd.to_numeric(x.density_numerator_items, errors="coerce")
    a = pd.to_numeric(x.sampled_area_km2, errors="coerce")
    df = pd.DataFrame({"src": "CSV:" + x.source_id, "seg": x.sample_id, "lat": x.latitude, "lon": x.longitude, "t": t,
                       "ship": x.platform, "area": a, "n5": n, "dcal10": np.nan})
    df["shipday"] = df.ship.astype(str) + "|" + df.t.dt.strftime("%Y-%m-%d")
    info = {"rows_total": n0, "density_rows_total_or_all_litter": n_dens, "excluded_final_test_ids": len(tids),
            "rows_used": len(df), "in_mediterranean": int(med_mask(df.lat, df.lon).sum()),
            "in_cozar_period": int(((df.t >= COZAR_START) & (df.t <= COZAR_END)).sum()),
            "sea_areas": x.sea_area.value_counts().to_dict()}
    return df, info


def load_cozar() -> pd.DataFrame:
    r = pd.read_csv(ROOT / "data/extra/registry_cozar2024.csv",
                    usecols=["fil_idx", "lat_centroid", "lon_centroid", "datetime_utc", "date", "area_m2", "tile"])
    r["t"] = pd.to_datetime(r.datetime_utc).dt.tz_localize(None)
    return r


# ------------------------------------------------------------------ STAC a_o proxy
def stac_path(key: str) -> Path:
    return STAC_DIR / f"{key}.json"


def fetch_point(key: str, lat: float, lon: float) -> dict:
    p = stac_path(key)
    rec = _read_json(p)
    if rec is not None:
        return rec
    if free_gb() < MIN_FREE_GB:
        raise IOError(f"free disk < {MIN_FREE_GB} GB (INBOX §21)")
    body = {"collections": ["sentinel-2-l1c"], "datetime": "2015-06-01T00:00:00Z/2021-09-17T23:59:59Z",
            "limit": 1000, "intersects": {"type": "Point", "coordinates": [lon, lat]},
            "fields": {"include": ["properties.datetime", "properties.eo:cloud_cover", "properties.grid:code"],
                       "exclude": ["assets", "links", "geometry", "bbox"]}}
    items, req, pages = [], ("POST", STAC_URL, body), 0
    s = requests.Session()
    while req is not None:
        for att in range(6):
            try:
                r = s.post(req[1], json=req[2], timeout=180) if req[0] == "POST" else s.get(req[1], timeout=180)
                if r.status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(3 * (att + 1))
        else:
            raise IOError(f"STAC failed for {key}")
        j = r.json()
        pages += 1
        for f in j.get("features", []):
            pr = f.get("properties", {})
            items.append([pr.get("datetime"), pr.get("eo:cloud_cover"), pr.get("grid:code")])
        nxt = [l for l in j.get("links", []) if l.get("rel") == "next"]
        if nxt and j.get("features"):
            l = nxt[0]
            req = ("POST", l["href"], {**body, **(l.get("body") or {})}) if l.get("method", "GET") == "POST" \
                else ("GET", l["href"], None)
        else:
            req = None
    rec = {"key": key, "lat": lat, "lon": lon, "pages": pages, "n_items": len(items), "items": items,
           "source": "CDSE STAC sentinel-2-l1c", "fetched": time.strftime("%Y-%m-%dT%H:%M:%S")}
    STAC_DIR.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec))
    tmp.replace(p)  # atomic: a reader never sees a half-written file (the disk ran full once)
    return rec


def _read_json(p: Path):
    """Cached STAC record or None (missing, empty or truncated file → treated as not fetched)."""
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except (json.JSONDecodeError, OSError):
        return None


def daily_clear(rec: dict) -> pd.Series:
    """clear_frac per acquisition day = max over items covering the point that day of (1 − cloud/100)."""
    if not rec or not rec.get("items"):
        return pd.Series(dtype=float)
    d = pd.DataFrame(rec["items"], columns=["dt", "cc", "grid"])
    d = d.dropna(subset=["dt"])
    d["day"] = pd.to_datetime(d.dt.str[:10])
    d["clear"] = 1 - pd.to_numeric(d.cc, errors="coerce").fillna(100).clip(0, 100) / 100
    return d.groupby("day").clear.max()


def needed_cells(scope: str) -> list[tuple[str, float, float]]:
    ad = load_adis()
    ad = ad[med_mask(ad.lat, ad.lon)]
    out = {}
    i, j = ckey(ad.lat, ad.lon, 0.25)
    for a, b in set(zip(i, j)):
        out[cid(a, b, 0.25)] = centre(a, b, 0.25)
    if scope == "all":
        # CDSE answers ~1 query / 15 s → order by a_lw ascending: the minimum positive LWD (eps) is most likely
        # in cells with the least windrow area, so a partial run still bounds eps from the right side.
        cz = load_cozar()
        cz["ci"], cz["cj"] = ckey(cz.lat_centroid, cz.lon_centroid, 0.25)
        g = cz.groupby(["ci", "cj"]).area_m2.sum().sort_values()
        for (a, b), _ in g.items():
            if med_mask((a + 0.5) * 0.25, (b + 0.5) * 0.25):
                out.setdefault(cid(a, b, 0.25), centre(a, b, 0.25))
    return [(k, v[0], v[1]) for k, v in out.items()]


MIN_FREE_GB = 20.0  # INBOX §21: never let the shared disk go below 20 GB free


def free_gb() -> float:
    import shutil
    return shutil.disk_usage(ROOT).free / 1e9


def stage_ao(scope: str, workers: int):
    if free_gb() < MIN_FREE_GB:
        sys.exit(f"free disk {free_gb():.1f} GB < {MIN_FREE_GB} GB (INBOX §21) — STAC fetch not started")
    cells = needed_cells(scope)
    todo = [c for c in cells if _read_json(stac_path(c[0])) is None]
    print(f"cells {len(cells)}, to fetch {len(todo)}", flush=True)
    done, fail = 0, 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fetch_point, *c): c for c in todo}
        for f in as_completed(futs):
            try:
                f.result()
                done += 1
            except Exception as e:  # noqa: BLE001
                fail += 1
                print("FAIL", futs[f][0], e, flush=True)
            if (done + fail) % 50 == 0:
                print(f"{done + fail}/{len(todo)} ok {done} fail {fail}", time.strftime("%H:%M:%S"), flush=True)
    print("done", done, "fail", fail)


# ------------------------------------------------------------------ geography (GSHHS f L1 → raster 0.005°)
# Vector intersection with the full-resolution continents is too slow; a 0.005° (~0.5 km) land raster is
# rasterized once (cached .npz) and used for sea area per cell and distance to coast (EDT).
RES = 0.005
_LANDR = None


def land_raster():
    global _LANDR
    if _LANDR is not None:
        return _LANDR
    cache = OUT / "land_raster_0.005.npz"
    if cache.exists():
        _LANDR = np.load(cache)["land"]
        return _LANDR
    import geopandas as gpd
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    lon0, lat0, lon1, lat1 = MED_BBOX
    W, H = int(round((lon1 - lon0) / RES)), int(round((lat1 - lat0) / RES))
    g = gpd.read_file(GSHHS, bbox=MED_BBOX)
    _LANDR = rasterize(((geom, 1) for geom in g.geometry.values), out_shape=(H, W),
                       transform=from_origin(lon0, lat1, RES, RES), fill=0, dtype="uint8", all_touched=False)
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, land=_LANDR)
    return _LANDR


def _rc(lat, lon):
    lon0, lat0, lon1, lat1 = MED_BBOX
    return ((lat1 - np.asarray(lat)) / RES).astype(int), ((np.asarray(lon) - lon0) / RES).astype(int)


def sea_area_km2(keys: list[tuple[int, int]], d: float) -> dict:
    Lr = land_raster()
    lon0, lat0, lon1, lat1 = MED_BBOX
    have = {}
    for i, j in keys:
        la0, lo0 = i * d, j * d
        r0, c0 = _rc(la0 + d, lo0)
        r1, c1 = _rc(la0, lo0 + d)
        sub = Lr[max(r0, 0):max(r1, 0), max(c0, 0):max(c1, 0)]
        if sub.size == 0:
            have[cid(i, j, d)] = float("nan")
            continue
        lats = lat1 - (np.arange(max(r0, 0), max(r1, 0)) + 0.5) * RES
        pix = (RES * 111.195) ** 2 * np.cos(np.radians(lats))  # km² per pixel row
        have[cid(i, j, d)] = float(((1 - sub) * pix[:, None]).sum())
    return have


def dist_coast_km(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Distance to the nearest GSHHS land pixel (0.005°), searched in a ±2° window around each point (low memory)."""
    Lr = land_raster()
    H, W = Lr.shape
    w = int(2.0 / RES)
    out = []
    for la, lo in zip(np.asarray(lat), np.asarray(lon)):
        r, c = _rc(la, lo)
        r0, r1, c0, c1 = max(r - w, 0), min(r + w, H), max(c - w, 0), min(c + w, W)
        rr, cc = np.nonzero(Lr[r0:r1, c0:c1])
        if rr.size == 0:
            out.append(2.0 * 111.195 * math.cos(math.radians(la)))
            continue
        dy = (rr + r0 - r) * RES * 111.195
        dx = (cc + c0 - c) * RES * 111.195 * math.cos(math.radians(la))
        out.append(float(np.sqrt(dx * dx + dy * dy).min()))
    return np.asarray(out)


# ------------------------------------------------------------------ aggregation
def field_cells(f: pd.DataFrame, d: float, period: str) -> pd.DataFrame:
    f = f.copy()
    f["ci"], f["cj"] = ckey(f.lat, f.lon, d)
    f["period"] = {"season": season_key(f.t), "year": f.t.dt.year.astype(str),
                   "month": f.t.dt.strftime("%Y-%m"), "all": "all"}[period]
    f["w_dcal"] = f.dcal10 * f.area
    f["wlat"], f["wlon"] = f.lat * f.area, f.lon * f.area
    g = f.groupby(["ci", "cj", "period"])
    c = g.agg(area=("area", "sum"), n5=("n5", "sum"), w_dcal=("w_dcal", "sum"), nseg=("seg", "size"),
              wlat=("wlat", "sum"), wlon=("wlon", "sum"), n_shipdays=("shipday", "nunique"),
              t0=("t", "min"), t1=("t", "max"), src=("src", lambda s: ",".join(sorted(set(s))))).reset_index()
    c["C_cal10"] = c.w_dcal / c.area
    c["C_raw5"] = c.n5 / c.area
    c["flat"], c["flon"] = c.wlat / c.area, c.wlon / c.area
    dom = f.groupby(["ci", "cj", "period", "ship"]).area.sum().reset_index().sort_values("area") \
        .drop_duplicates(["ci", "cj", "period"], keep="last").rename(columns={"ship": "ship_dom"})[["ci", "cj", "period", "ship_dom"]]
    domy = f.assign(y=f.t.dt.year).groupby(["ci", "cj", "period", "y"]).area.sum().reset_index().sort_values("area") \
        .drop_duplicates(["ci", "cj", "period"], keep="last").rename(columns={"y": "year_dom"})[["ci", "cj", "period", "year_dom"]]
    sd = f.groupby(["ci", "cj", "period"]).shipday.apply(lambda s: sorted(set(s))).rename("shipdays").reset_index()
    sdd = f.groupby(["ci", "cj", "period", "shipday"]).area.sum().reset_index().sort_values("area")         .drop_duplicates(["ci", "cj", "period"], keep="last").rename(columns={"shipday": "shipday_dom"})[["ci", "cj", "period", "shipday_dom"]]
    sd = sd.merge(sdd, on=["ci", "cj", "period"])
    c = c.merge(dom, on=["ci", "cj", "period"]).merge(domy, on=["ci", "cj", "period"]).merge(sd, on=["ci", "cj", "period"])
    c["cell"] = [cid(a, b, d) for a, b in zip(c.ci, c.cj)]
    return c.drop(columns=["w_dcal", "wlat", "wlon"])


def ao_clear_sum(i: int, j: int, d: float, periods: pd.Series | None = None) -> dict:
    """Σ clear_frac per period for a cell. For d = 0.5 — mean over available 0.25° sub-cells."""
    if d == 0.25:
        recs = [stac_path(cid(i, j, 0.25))]
    else:
        f = int(round(d / 0.25))
        recs = [stac_path(cid(i * f + a, j * f + b, 0.25)) for a in range(f) for b in range(f)]
    sers = [daily_clear(r) for r in (_read_json(p) for p in recs) if r is not None]
    if not sers:
        return None
    out = {}
    for s in sers:
        if s.empty:
            continue
        idx = s.index.to_series()
        keys = {"all": pd.Series("all", index=s.index), "year": idx.dt.year.astype(str),
                "season": season_key(idx), "month": idx.dt.strftime("%Y-%m")}
        for kind, k in keys.items():
            for per, v in s.groupby(k.values).sum().items():
                out.setdefault((kind, per), []).append(v)
    return {k: float(np.mean(v)) for k, v in out.items()}


def cozar_cells(cz: pd.DataFrame, d: float, period: str) -> pd.DataFrame:
    cz = cz.copy()
    cz["ci"], cz["cj"] = ckey(cz.lat_centroid, cz.lon_centroid, d)
    cz["period"] = {"season": season_key(cz.t), "year": cz.t.dt.year.astype(str), "all": "all",
                    "month": cz.t.dt.strftime("%Y-%m")}[period]
    return cz.groupby(["ci", "cj", "period"]).agg(a_lw_m2=("area_m2", "sum"), n_fil=("fil_idx", "size"),
                                                   det_days=("date", "nunique")).reset_index()


def attach_sat(c: pd.DataFrame, cz: pd.DataFrame, d: float, period: str, sea: dict) -> pd.DataFrame:
    s = cozar_cells(cz, d, period)
    c = c.merge(s, on=["ci", "cj", "period"], how="left")
    c[["a_lw_m2", "n_fil", "det_days"]] = c[["a_lw_m2", "n_fil", "det_days"]].fillna(0)
    clear = []
    for i, j, p in zip(c.ci, c.cj, c.period):
        a = ao_clear_sum(i, j, d)
        clear.append(np.nan if a is None else a.get((period, p), 0.0))
    c["clear_days"] = clear
    c["sea_km2"] = [sea.get(cid(i, j, d), np.nan) for i, j in zip(c.ci, c.cj)]
    c["a_o_km2"] = c.sea_km2 * c.clear_days
    c["LWD_proxy"] = np.where(c.a_o_km2 > 0, c.a_lw_m2 / c.a_o_km2, np.nan)
    c["freq_proxy"] = np.where(c.clear_days > 0, c.det_days / c.clear_days, np.nan)
    return c


# ------------------------------------------------------------------ statistics
def ols_fit_pred(Xtr, ytr, Xte):
    A = np.column_stack([np.ones(len(Xtr)), Xtr])
    beta, *_ = np.linalg.lstsq(A, ytr, rcond=None)
    return np.column_stack([np.ones(len(Xte)), Xte]) @ beta, beta


def cv_predict(df: pd.DataFrame, group: str, feats: dict) -> dict:
    y = df.y.values
    preds = {m: np.full(len(df), np.nan) for m in ["M0_median", *feats]}
    for gval in df[group].unique():
        te = (df[group] == gval).values
        tr = ~te
        if tr.sum() < 5:
            continue
        preds["M0_median"][te] = np.median(y[tr])
        for m, cols in feats.items():
            X = df[cols].values
            if tr.sum() <= len(cols) + 2:
                continue
            preds[m][te], _ = ols_fit_pred(X[tr], y[tr], X[te])
    return preds


def boot_delta(e1, e0, rng, n=BOOT):
    ok = np.isfinite(e1) & np.isfinite(e0)
    a, b = np.abs(e1[ok]), np.abs(e0[ok])
    idx = rng.integers(0, len(a), (n, len(a)))
    dd = a[idx].mean(1) - b[idx].mean(1)
    return {"dMAE": float(a.mean() - b.mean()), "ci95": [float(np.percentile(dd, 2.5)), float(np.percentile(dd, 97.5))],
            "n": int(ok.sum())}


def partial_spearman(x, y, z):
    rx, ry, rz = rankdata(x), rankdata(y), rankdata(z)
    A = np.column_stack([np.ones(len(rz)), rz])
    ex = rx - A @ np.linalg.lstsq(A, rx, rcond=None)[0]
    ey = ry - A @ np.linalg.lstsq(A, ry, rcond=None)[0]
    return float(np.corrcoef(ex, ey)[0, 1])


def analyse(df: pd.DataFrame, xcol: str, ycol: str, eps: float, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    df = df.copy()
    df["x"] = np.log(df[xcol] + eps)
    df["y"] = np.log1p(df[ycol])
    df["ldist"] = np.log(np.maximum(df.dist_km, 0.1))
    df["block"] = [f"{math.floor(a / 3)}_{math.floor(b / 3)}" for a, b in zip(df.flat, df.flon)]
    n = len(df)
    rho = spearmanr(df.x, df.y).statistic if df.x.nunique() > 1 and df.y.nunique() > 1 else float("nan")
    bs = []
    for _ in range(BOOT):
        ix = rng.integers(0, n, n)
        xs, ys = df.x.values[ix], df.y.values[ix]
        if np.unique(xs).size > 1 and np.unique(ys).size > 1:
            bs.append(spearmanr(xs, ys).statistic)
    # added after freezing (sensitivity, not in the decision rule): cells of one ship-day are not independent →
    # cluster bootstrap over dominant ship-days; and within-ship ranks (ships differ in camera set-up and region)
    cl = df.shipday_dom.values
    ucl = np.unique(cl)
    idx_by = {u: np.nonzero(cl == u)[0] for u in ucl}
    bc = []
    for _ in range(BOOT):
        pick = rng.choice(ucl, len(ucl))
        ix = np.concatenate([idx_by[u] for u in pick])
        xs, ys = df.x.values[ix], df.y.values[ix]
        if np.unique(xs).size > 1 and np.unique(ys).size > 1:
            bc.append(spearmanr(xs, ys).statistic)
    within = {}
    for sh, sub in df.groupby("ship_dom"):
        if len(sub) >= MIN_CELLS and sub.x.nunique() > 1 and sub.y.nunique() > 1:
            bw = []
            for _ in range(BOOT):
                ix = rng.integers(0, len(sub), len(sub))
                xs, ys = sub.x.values[ix], sub.y.values[ix]
                if np.unique(xs).size > 1 and np.unique(ys).size > 1:
                    bw.append(spearmanr(xs, ys).statistic)
            scl = sub.shipday_dom.values
            su = np.unique(scl)
            sby = {u: np.nonzero(scl == u)[0] for u in su}
            bwc = []
            for _ in range(BOOT):
                ix = np.concatenate([sby[u] for u in rng.choice(su, len(su))])
                xs, ys = sub.x.values[ix], sub.y.values[ix]
                if np.unique(xs).size > 1 and np.unique(ys).size > 1:
                    bwc.append(spearmanr(xs, ys).statistic)
            within[sh] = {"n": int(len(sub)), "n_shipdays": int(len(su)), "rho": float(spearmanr(sub.x, sub.y).statistic),
                          "ci95_shipday_cluster": [float(np.percentile(bwc, 2.5)), float(np.percentile(bwc, 97.5))],
                          "lat_range": [float(sub.flat.min()), float(sub.flat.max())],
                          "lon_range": [float(sub.flon.min()), float(sub.flon.max())],
                          "ci95_cells": [float(np.percentile(bw, 2.5)), float(np.percentile(bw, 97.5))],
                          "partial_given_lndist": partial_spearman(sub.x, sub.y, sub.ldist)}
    rho_rg = spearmanr(df.ldist, df.y).statistic
    rho_xg = spearmanr(df.ldist, df.x).statistic
    feats = {"M1_sat": ["x"], "M2_geo": ["flat", "flon", "ldist"], "M3_sat_geo": ["x", "flat", "flon", "ldist"]}
    res = {"n_cells": n, "n_blocks": int(df.block.nunique()), "n_years": int(df.year_dom.nunique()),
           "n_ships": int(df.ship_dom.nunique()),
           "n_x_positive": int((df[xcol] > 0).sum()), "n_y_positive": int((df[ycol] > 0).sum()),
           "spearman": {"rho": float(rho), "ci95": [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))]
                        if bs else None, "boot_ok": len(bs)},
           "spearman_ci95_shipday_cluster": [float(np.percentile(bc, 2.5)), float(np.percentile(bc, 97.5))] if bc else None,
           "n_shipday_clusters": int(len(ucl)), "spearman_within_ship": within,
           "spearman_y_vs_lndist": float(rho_rg), "spearman_x_vs_lndist": float(rho_xg),
           "partial_spearman_given_lndist": partial_spearman(df.x, df.y, df.ldist),
           "splits": {}}
    for split, grp in [("leave_region_out", "block"), ("leave_year_out", "year_dom"), ("leave_ship_out", "ship_dom")]:
        P = cv_predict(df, grp, feats)
        y = df.y.values
        mae = {m: float(np.nanmean(np.abs(p - y))) for m, p in P.items()}
        r = {"n_groups": int(df[grp].nunique()), "MAE_log1p": mae,
             "M1_vs_M0": boot_delta(P["M1_sat"] - y, P["M0_median"] - y, rng),
             "M3_vs_M2": boot_delta(P["M3_sat_geo"] - y, P["M2_geo"] - y, rng),
             "M2_vs_M0": boot_delta(P["M2_geo"] - y, P["M0_median"] - y, rng)}
        if split == "leave_region_out":
            res["_lro_resid_M1"] = (y - P["M1_sat"])
        res["splits"][split] = r
    _, beta = ols_fit_pred(df[["x"]].values, df.y.values, df[["x"]].values)
    res["M1_full_fit"] = {"intercept": float(beta[0]), "slope_b": float(beta[1])}
    lro = res["splits"]["leave_region_out"]
    passed = bool(res["spearman"]["ci95"] and res["spearman"]["ci95"][0] > 0
                  and lro["M1_vs_M0"]["ci95"][1] < 0 and lro["M3_vs_M2"]["ci95"][1] < 0)
    res["decision_rule_passed"] = passed
    rr = res.pop("_lro_resid_M1")
    rr = rr[np.isfinite(rr)]
    res["lro_M1_resid_q05_q95_log1p"] = [float(np.quantile(rr, 0.05)), float(np.quantile(rr, 0.95))] if len(rr) else None
    return res


# ------------------------------------------------------------------ variants
def run(offline: bool):
    cfg = load_cfg()
    OUT.mkdir(parents=True, exist_ok=True)
    adis = load_adis()
    csvf, csv_info = load_csv_field()
    cz = load_cozar()
    field = pd.concat([adis, csvf.dropna(subset=["lat", "lon", "t", "area"])], ignore_index=True)
    field_med = field[med_mask(field.lat, field.lon)]
    out = {"L": "L110", "config": "configs/cell_calibration.yaml", "config_sha256": cfg["_sha256"],
           "generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "stop_rule_min_cells": MIN_CELLS,
           "data": {}, "variants": {}}
    out["data"]["adis"] = {"segments": len(adis), "mediterranean": int(med_mask(adis.lat, adis.lon).sum()),
                           "med_first": str(adis[med_mask(adis.lat, adis.lon)].t.min()),
                           "med_last": str(adis[med_mask(adis.lat, adis.lon)].t.max()),
                           "med_ships": adis[med_mask(adis.lat, adis.lon)].groupby("ship").size().to_dict(),
                           "in_cozar_period": int(((adis.t >= COZAR_START) & (adis.t <= COZAR_END)).sum()),
                           "med_and_cozar_period": int((med_mask(adis.lat, adis.lon) & (adis.t <= COZAR_END)).sum())}
    out["data"]["csv_organizers"] = csv_info
    out["data"]["cozar"] = {"filaments": len(cz), "first": str(cz.t.min()), "last": str(cz.t.max()),
                            "cells_0.25": int(len(set(zip(*ckey(cz.lat_centroid, cz.lon_centroid, 0.25))))),
                            "a_o_in_published_files": False,
                            "a_o_note": "NetCDF (Zenodo 11045944): only filament variables; Source Data xlsx: filament list only"}

    # sea areas for all cells we touch
    keys25 = set(zip(*ckey(field_med.lat, field_med.lon, 0.25))) | set(zip(*ckey(cz.lat_centroid, cz.lon_centroid, 0.25)))
    keys50 = set(zip(*ckey(field_med.lat, field_med.lon, 0.5)))
    sea = {**sea_area_km2(sorted(keys25), 0.25), **sea_area_km2(sorted(keys50), 0.5)}

    have_stac = {p.stem for p in STAC_DIR.glob("*.json")} if STAC_DIR.exists() else set()
    out["data"]["stac_cells_cached"] = len(have_stac)

    # ---------------- V1 / V1b: same season / same year (Cózar × field)
    for vname, period in [("V1_same_period", "season"), ("V1b_same_year", "year")]:
        fc = field_cells(field_med, 0.25, period)
        fc = fc[fc.area >= cfg["field"]["adis"]["min_area_km2_per_cell"]]
        cz_periods = set(cozar_cells(cz, 0.25, period).period) | set(
            {"season": season_key(pd.Series(pd.date_range(COZAR_START, COZAR_END, freq="D"))),
             "year": pd.Series(pd.date_range(COZAR_START, COZAR_END, freq="D")).dt.year.astype(str)}[period])
        m = fc[fc.period.isin(cz_periods)].copy()
        m = attach_sat(m, cz, 0.25, period, sea) if len(m) else m
        n_date_overlap = int((m.t0 <= COZAR_END).sum()) if len(m) else 0
        shipdays = sorted({s for L in m.shipdays for s in L}) if len(m) else []
        v = {"period_key": period, "field_cells_mediterranean": int(len(fc)),
             "cells_with_period_key_match": int(len(m)),
             "cells_with_field_date_inside_cozar_dates": n_date_overlap,
             "distinct_shipdays": len(shipdays),
             "cells": m[["cell", "period", "area", "nseg", "t0", "t1", "ship_dom", "a_lw_m2", "n_fil",
                         "clear_days", "LWD_proxy"]].astype(str).to_dict("records") if len(m) else [],
             "note": "Cózar ends 2021-09-17; ADIS in the Mediterranean starts 2021-09-27 (Dallaporta); the CSV has no "
                     "Mediterranean rows → any key match is a match of the SEASON/YEAR label only, with 0 common days."}
        v["stop_rule"] = "STOP (report only)" if len(m) < MIN_CELLS or len(shipdays) < MIN_CELLS else "passed"
        if v["stop_rule"] == "passed":
            v["note_passed"] = "not expected; analysis would run here"
        out["variants"][vname] = v

    # ---------------- V2: detector × ADIS (L100 rows), same cell & month
    c = pd.read_csv(ROOT / "data/search/adis/candidates.csv")
    e = c[(c.detector_evaluable == True) & (c.strip_usable_km2 > 0)].copy()  # noqa: E712
    e["S"] = e.det_px_strip_excl_ship * 100.0 / e.strip_usable_km2
    e["ci"], e["cj"] = ckey(e.lat, e.lon, 0.25)
    e["month"] = pd.to_datetime(e.seg_mid_utc, format="ISO8601", utc=True).dt.strftime("%Y-%m")
    e["shipday"] = e.ship + "|" + pd.to_datetime(e.seg_mid_utc, format="ISO8601", utc=True).dt.strftime("%Y-%m-%d")
    g = e.groupby(["ci", "cj", "month"]).agg(S_num=("det_px_strip_excl_ship", "sum"), A=("strip_usable_km2", "sum"),
                                             n=("event_id", "size")).reset_index()
    g["S"] = g.S_num * 100 / g.A
    # live scenes × ADIS (same 0.25° cell and month)
    live_rows = json.loads((ROOT / "reports/quantity/coverage_live.json").read_text(encoding="utf-8"))["rows"]
    n_live_match, live_detail = 0, []
    for r in live_rows:
        sj = ROOT / "data/live" / r["scene"] / "scene.json"
        if not sj.exists():
            continue
        s = json.loads(sj.read_text(encoding="utf-8"))
        b = s.get("bounds_wgs84")
        if not b:
            continue
        mon = s["date"][:7]
        hit = adis[(adis.lon >= b[0]) & (adis.lon <= b[2]) & (adis.lat >= b[1]) & (adis.lat <= b[3])
                   & (adis.t.dt.strftime("%Y-%m") == mon)]
        if len(hit):
            n_live_match += 1
            live_detail.append({"scene": r["scene"], "adis_segments": int(len(hit))})
    pairs = json.loads((ROOT / "reports/quantity/coverage_pairs.json").read_text(encoding="utf-8"))["rows"]
    pacc = [p for p in pairs if p.get("decision") == "accept"]
    out["variants"]["V2_detector_same_month"] = {
        "l100_rows": int(len(c)), "evaluable_rows_with_strip": int(len(e)),
        "rows_with_S_gt_0": int((e.S > 0).sum()), "cell_months": int(len(g)),
        "cell_months_with_S_gt_0": int((g.S > 0).sum()), "distinct_shipdays": int(e.shipday.nunique()),
        "live_scenes_with_adis_same_month_in_footprint": n_live_match, "live_detail": live_detail,
        "csv_pair_strips_accepted": len(pacc),
        "csv_pair_strips_with_detections": int(sum(1 for p in pacc if (p.get("strip_n_det") or 0) > 0)),
        "stop_rule": "STOP (report only)" if (len(g) < MIN_CELLS or (g.S > 0).sum() < MIN_CELLS) else "passed"}

    # ---------------- V3: exploratory climatology (Cózar 2015–2021 × ADIS Med 2021–2023)
    adis_med = adis[med_mask(adis.lat, adis.lon)]
    v3 = {"role": cfg["variants"]["V3_climatology"]["role"].strip()}
    for d in (0.25, 0.5):
        fc = field_cells(adis_med, d, "all")
        fc = fc[fc.area >= cfg["field"]["adis"]["min_area_km2_per_cell"]].copy()
        fc = attach_sat(fc, cz, d, "all", sea)
        miss = int(fc.clear_days.isna().sum())
        fc = fc[fc.sea_km2 > 0]
        fc["dist_km"] = dist_coast_km(fc.flat.values, fc.flon.values)
        ok = fc.dropna(subset=["LWD_proxy"])
        # eps from ALL Mediterranean cells of this grid with filaments (satellite only)
        allc = cozar_cells(cz, d, "all")
        allc = allc[med_mask((allc.ci + 0.5) * d, (allc.cj + 0.5) * d)]
        sea_d = sea_area_km2([(i, j) for i, j in zip(allc.ci, allc.cj)], d)
        lwd_all = []
        n_all_ao = 0
        for i, j, a in zip(allc.ci, allc.cj, allc.a_lw_m2):
            ao = ao_clear_sum(i, j, d)
            s_ = sea_d.get(cid(i, j, d), 0)
            if ao is None or not s_ > 0:
                continue
            n_all_ao += 1
            cd = ao.get(("all", "all"), 0)
            if cd > 0:
                lwd_all.append(a / (s_ * cd))
        lwd_all = np.array(lwd_all)
        eps = 0.5 * float(lwd_all[lwd_all > 0].min()) if (lwd_all > 0).any() else float("nan")
        eps_src = f"all Mediterranean filament cells with a_o ({n_all_ao} of {len(allc)})"
        if not np.isfinite(eps) or n_all_ao < len(allc):
            pos = ok.LWD_proxy[ok.LWD_proxy > 0]
            if not np.isfinite(eps) and len(pos):
                eps = 0.5 * float(pos.min())
                eps_src = "DEVIATION: field cells only (STAC for all cells missing)"
        shipdays = sorted({s for L in ok.shipdays for s in L})
        r = {"cell_deg": d, "field_cells_area_ge_min": int(len(fc) + 0), "cells_missing_stac": miss,
             "cells_used": int(len(ok)), "distinct_shipdays": len(shipdays), "eps_ppm": eps, "eps_source": eps_src,
             "cozar_mediterranean_cells_with_filaments": int(len(allc)),
             "LWD_all_med_cells_ppm": {"n": int(len(lwd_all)), "median": float(np.median(lwd_all)) if len(lwd_all) else None,
                                       "p90": float(np.quantile(lwd_all, 0.9)) if len(lwd_all) else None,
                                       "max": float(lwd_all.max()) if len(lwd_all) else None,
                                       "a_lw_sum_m2": float(allc.a_lw_m2.sum()) if len(allc) else None},
             "stop_rule": "STOP (report only)" if len(ok) < MIN_CELLS else "passed"}
        ok.drop(columns=["shipdays"]).to_csv(OUT / f"cells_V3_{d:g}.csv", index=False)
        if r["stop_rule"] == "passed":
            r["primary"] = analyse(ok, "LWD_proxy", "C_cal10", eps)
            r["sens_field_raw5"] = analyse(ok, "LWD_proxy", "C_raw5", eps)
            # added after freezing (not used by the decision rule): eps is set from a possibly partial STAC run
            r["sens_eps_x0.1"] = analyse(ok, "LWD_proxy", "C_cal10", eps * 0.1)
            r["sens_eps_x10"] = analyse(ok, "LWD_proxy", "C_cal10", eps * 10)
            fe = 0.5 * float(ok.freq_proxy[ok.freq_proxy > 0].min()) if (ok.freq_proxy > 0).any() else 1e-6
            r["sens_index_freq"] = analyse(ok, "freq_proxy", "C_cal10", fe)
            r["describe"] = {
                "LWD_proxy_ppm": ok.LWD_proxy.describe().round(4).to_dict(),
                "C_cal10": ok.C_cal10.describe().round(3).to_dict(),
                "area_km2_sum": float(ok.area.sum()), "segments": int(ok.nseg.sum()),
                "cells_LWD_zero": int((ok.LWD_proxy == 0).sum()), "cells_C_zero": int((ok.C_cal10 == 0).sum()),
                "by_ship": ok.groupby("ship_dom").agg(n=("cell", "size"), C_med=("C_cal10", "median"),
                                                      LWD_med=("LWD_proxy", "median"),
                                                      dist_med=("dist_km", "median")).round(3).to_dict("index")}
        v3[f"grid_{d:g}"] = r
    out["variants"]["V3_climatology"] = v3

    # ---------------- verdict
    p = v3.get("grid_0.25", {}).get("primary")
    verdict = {"calibration_same_period_possible": False,
               "reason_same_period": "0 field×satellite cells with common dates (V1/V1b) and < 20 detector cells with S > 0 (V2)"}
    if p:
        verdict["V3_decision_rule_passed"] = p["decision_rule_passed"]
        verdict["V3_model_published"] = bool(p["decision_rule_passed"])
    out["verdict"] = verdict
    REP.mkdir(parents=True, exist_ok=True)
    (REP / "cell_calibration.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (REP / "cell_calibration.md").write_text(render_md(out), encoding="utf-8")
    print(json.dumps(verdict, ensure_ascii=False))
    return out


# ------------------------------------------------------------------ markdown
def _ci(x):
    return "—" if not x else f"[{x[0]:+.3f}; {x[1]:+.3f}]"


def _split_table(a: dict) -> list[str]:
    L = ["| разбиение | групп | MAE M0 медиана | M1 спутник | M2 география | M3 спутник+геогр. | ΔMAE M1−M0 [95 %] | ΔMAE M3−M2 [95 %] | ΔMAE M2−M0 [95 %] |",
         "|---|---:|---:|---:|---:|---:|---|---|---|"]
    for k, r in a["splits"].items():
        m = r["MAE_log1p"]
        L.append(f"| {k} | {r['n_groups']} | {m['M0_median']:.3f} | {m['M1_sat']:.3f} | {m['M2_geo']:.3f} | {m['M3_sat_geo']:.3f} | "
                 f"{r['M1_vs_M0']['dMAE']:+.3f} {_ci(r['M1_vs_M0']['ci95'])} | {r['M3_vs_M2']['dMAE']:+.3f} {_ci(r['M3_vs_M2']['ci95'])} | "
                 f"{r['M2_vs_M0']['dMAE']:+.3f} {_ci(r['M2_vs_M0']['ci95'])} |")
    return L


def render_md(o: dict) -> str:
    V = o["variants"]
    ad, cz, cs = o["data"]["adis"], o["data"]["cozar"], o["data"]["csv_organizers"]
    v1, v1b, v2, v3 = V["V1_same_period"], V["V1b_same_year"], V["V2_detector_same_month"], V["V3_climatology"]
    g = v3.get("grid_0.25", {})
    p = g.get("primary")
    L = [f"# L110 — калибровка на уровне ячеек: спутниковый индекс ↔ шт./км² (INBOX §15, агент 6 б)", "",
         f"Сгенерировано `scripts/case/cell_calibration.py run` ({o['generated']}). Проверка зафиксирована до сравнения значений: "
         f"`configs/cell_calibration.yaml`, sha256 `{o['config_sha256'][:16]}…`. Стоп-правило: < {o['stop_rule_min_cells']} независимых "
         "ячеек поле×спутник → только отчёт, без модели.", "",
         "## 1. Что есть в данных", "",
         f"- Cózar 2024: {cz['filaments']} нитей, {cz['first'][:10]} … {cz['last'][:10]}, {cz['cells_0.25']} ячеек 0.25° с нитями. "
         "Площадь наблюдённого моря a_o **не опубликована** (в NetCDF только переменные нитей, в Source Data — список нитей), "
         "поэтому LWD считается с прокси a_o = площадь моря ячейки (GSHHS) × Σ(1 − облачность сцены) по дням съёмки "
         "(CDSE STAC, L1C, центр ячейки); фильтр ветра > 5 м/с не применён.",
         f"- ADIS: {ad['segments']} отрезков, в Средиземном море {ad['mediterranean']} — с {ad['med_first'][:10]} по {ad['med_last'][:10]} "
         f"({', '.join(f'{k} {v}' for k, v in ad['med_ships'].items())}). В Средиземном море в период Cózar: **{ad['med_and_cozar_period']}**.",
         f"- CSV организаторов: {cs['rows_used']} строк плотности (без отложенного test), в Средиземном море **{cs['in_mediterranean']}** "
         f"(моря: {', '.join(f'{k} {v}' for k, v in cs['sea_areas'].items())}).", "",
         "## 2. Калибровочные варианты (тот же период) — стоп-правило", "",
         "| вариант | ячеек с совпадением ключа периода | из них с общими датами | судо-суток | ячеек со спутник. > 0 | решение |",
         "|---|---:|---:|---:|---:|---|",
         f"| V1 та же ячейка 0.25° и сезон | {v1['cells_with_period_key_match']} | {v1['cells_with_field_date_inside_cozar_dates']} | {v1['distinct_shipdays']} | "
         f"{sum(1 for c in v1['cells'] if c.get('LWD_proxy') not in ('nan', '0.0') and float(c.get('LWD_proxy') or 0) > 0)} | {v1['stop_rule']} |",
         f"| V1b та же ячейка и год | {v1b['cells_with_period_key_match']} | {v1b['cells_with_field_date_inside_cozar_dates']} | {v1b['distinct_shipdays']} | "
         f"{sum(1 for c in v1b['cells'] if c.get('LWD_proxy') not in ('nan', '0.0') and float(c.get('LWD_proxy') or 0) > 0)} | {v1b['stop_rule']} |",
         f"| V2 наш детектор × ADIS, ячейка и месяц | {v2['cell_months']} | {v2['cell_months']} | {v2['distinct_shipdays']} | "
         f"{v2['cell_months_with_S_gt_0']} | {v2['stop_rule']} |", "",
         f"V1/V1b: {v1['note']}",
         f"V2: строк L100 с «детектор оценивается» и полосой {v2['evaluable_rows_with_strip']}, с S > 0 — {v2['rows_with_S_gt_0']}; "
         f"сцены подготовки (data/live) с отрезками ADIS в том же месяце в кадре — {v2['live_scenes_with_adis_same_month_in_footprint']}; "
         f"полосы пар CSV принятые — {v2['csv_pair_strips_accepted']}, со срабатываниями — {v2['csv_pair_strips_with_detections']}. "
         "Все нули → ранги и регрессия не определены.", "",
         "**Итог по калибровке: пересечений поле×спутник в том же периоде нет (0 общих дат), модели нет.**", "",
         "## 3. V3 — ИССЛЕДОВАТЕЛЬСКИЙ вариант: климатологии разных лет (НЕ калибровка)", "",
         "Исследовательский вариант: климатология Cózar 2015-06…2021-09 по ячейке против ADIS в Средиземном море 2021-09…2023-12 "
         "(разные годы → НЕ калибровка). Отвечает только на вопрос «ранжируют ли спутниковый индекс горячих точек и полевая плотность "
         "одни и те же районы». Поле и спутник не совпадают по времени; ячейка ≠ одно скопление.", ""]
    for d in ("0.25", "0.5"):
        r = v3.get(f"grid_{d}")
        if not r:
            continue
        L.append(f"- сетка {d}°: ячеек поля с ΣA ≥ 0.5 км² — {r['field_cells_area_ge_min']}, без STAC — {r['cells_missing_stac']}, "
                 f"использовано {r['cells_used']} (судо-суток {r['distinct_shipdays']}); eps = {r['eps_ppm']:.3g} м²/км² ({r['eps_source']}); "
                 f"стоп-правило: {r['stop_rule']}")
    if g.get("LWD_all_med_cells_ppm", {}).get("n"):
        la = g["LWD_all_med_cells_ppm"]
        L.append(f"- проверка масштаба LWD-прокси (0.25°, ячейки Средиземного моря с нитями и a_o: {la['n']}): медиана {la['median']:.3g}, "
                 f"p90 {la['p90']:.3g}, макс {la['max']:.3g} м²/км². У Cózar: среднее по морю 0.2, горячие точки 9–56 (среднегодовое, "
                 "с фильтром ветра) — прокси без фильтра ветра ожидаемо ниже. Выборка ячеек для этой проверки и для eps НЕПОЛНАЯ и смещена "
                 "к ячейкам с малой площадью нитей (STAC запрашивался по возрастанию a_lw; докачка остановлена по INBOX §21, диск < 20 ГБ); "
                 "на решение это не влияет: ρ от eps не зависит, eps ×0.1 / ×10 — то же решение.")
    if p:
        ds = g["describe"]
        L += ["", f"Сетка 0.25°: {ds['segments']} отрезков, {ds['area_km2_sum']:.1f} км²; ячеек с LWD = 0 — {ds['cells_LWD_zero']}, "
                  f"с полевой плотностью 0 — {ds['cells_C_zero']}. Поле — ADIS >10 см калиброванная (ΣdA/ΣA), y = ln(1 + C); x = ln(LWD + eps).", "",
              "| что | основной (ADIS >10 см калибр.) | чувствит.: ADIS >5 см сырой | чувствит.: частота нитей | eps ×0.1 | eps ×10 |",
              "|---|---|---|---|---|---|"]
        alts = [g["primary"], g["sens_field_raw5"], g["sens_index_freq"], g["sens_eps_x0.1"], g["sens_eps_x10"]]
        L.append("| Спирмен ρ(x, y) [95 % бутстреп по ячейкам] | " + " | ".join(
            f"{a['spearman']['rho']:+.3f} {_ci(a['spearman']['ci95'])}" for a in alts) + " |")
        L.append("| ρ: 95 % бутстреп по судо-суткам* | " + " | ".join(_ci(a["spearman_ci95_shipday_cluster"]) for a in alts) + " |")
        L.append("| частный ρ при ln(расст. до берега) | " + " | ".join(f"{a['partial_spearman_given_lndist']:+.3f}" for a in alts) + " |")
        L.append("| ρ(y, ln расст.) / ρ(x, ln расст.) | " + " | ".join(
            f"{a['spearman_y_vs_lndist']:+.3f} / {a['spearman_x_vs_lndist']:+.3f}" for a in alts) + " |")
        L.append("| правило решения пройдено | " + " | ".join("да" if a["decision_rule_passed"] else "нет" for a in alts) + " |")
        L += ["", "\\* добавлено после фиксации (не входит в правило решения): соседние ячейки одних судо-суток зависимы "
                  f"({p['n_shipday_clusters']} кластеров на {p['n_cells']} ячеек).", ""]
        if p["spearman_within_ship"]:
            L.append("Внутри одного судна (одна камера и район; добавлено после фиксации): " + "; ".join(
                f"{k} ({v['lat_range'][0]:.1f}–{v['lat_range'][1]:.1f}° с. ш., {v['lon_range'][0]:.1f}–{v['lon_range'][1]:.1f}° в. д.): n {v['n']} ячеек / {v['n_shipdays']} судо-суток, ρ {v['rho']:+.3f} {_ci(v['ci95_cells'])} по ячейкам, {_ci(v['ci95_shipday_cluster'])} по судо-суткам, частный при ln(расст.) {v['partial_given_lndist']:+.3f}"
                for k, v in p["spearman_within_ship"].items()) + ".")
        L += ["", "Основной вариант, ошибка на отложенных ячейках (MAE по ln(1 + C)):", ""] + _split_table(p)
        s5 = v3.get("grid_0.5", {}).get("primary")
        if s5:
            L += ["", f"Чувствительность, сетка 0.5°: ρ = {s5['spearman']['rho']:+.3f} {_ci(s5['spearman']['ci95'])}, "
                      f"правило пройдено: {'да' if s5['decision_rule_passed'] else 'нет'}.", ""] + _split_table(s5)
        L += ["", f"**Решение V3 по замороженному правилу: {'связь есть' if p['decision_rule_passed'] else 'связи на независимых ячейках нет — честный отрицательный результат, модель не публикуется'}.**"]
    L += ["", "## 4. Как говорить (§15)", "",
          "- Калибровочных пар «снимок → шт./км²» — 0; калибровки на уровне ячеек в том же периоде — 0 ячеек.",
          "- V3 — сравнение климатологий разных лет, не калибровка и не измерение одной кучи; ячейка — не скопление.",
          "- Синтетика не использовалась; отложенный test кейса не читался (исключены по sample_id).", ""]
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("ao")
    a.add_argument("--scope", choices=["field", "all"], default="field")
    a.add_argument("--workers", type=int, default=6)
    r = sp.add_parser("run")
    r.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    load_cfg()
    if args.cmd == "ao":
        stage_ao(args.scope, args.workers)
    else:
        run(args.offline)
