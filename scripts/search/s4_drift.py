"""S4: particle cloud "search zone" between the survey time window and each same-day scene (drift = search area, NOT proof).

Forcing (point time series at the transect mid-point, hourly, cached in data/search/s4/drift_cache/):
  wind 10 m  - ERA5 via Open-Meteo archive API (archive-api.open-meteo.com, models=era5); CDS is not used;
  current    - Open-Meteo marine API ocean_current_velocity/direction (= Copernicus Marine global SMOC analysis).
               HYCOM ESPC-D-V02 (scripts/run_drift.py) starts Aug 2024 -> no June 2024; HYCOM GOFS 3.1 GLBy0.08 aggregation
               returned an OPeNDAP I/O failure (26.09 01:15) -> not used.
Particles (N=1000): release time ~ U(feasible observation window of the transect, data/search/s4/transect_times.csv),
release position ~ uniform disk r=2 km around the reported mid-point (transect length unknown; assumption),
velocity = current + windage * wind, windage ~ U(1 %, 3 %), + random walk K = 10 m2/s; integrated to the scene time
(forward or backward). Output: data/search/s4/drift_zones.geojson (95 % hull per event x scene), drift_zones.csv.
  PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/search/s4_drift.py
"""
from __future__ import annotations

import json
import math
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
S = ROOT / "data/search/s4"
CACHE = S / "drift_cache"
N = 1000
K = 10.0
RNG = np.random.default_rng(87)


def get_json(url, f: Path):
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    for a in range(4):
        try:
            js = json.loads(urllib.request.urlopen(url, timeout=60).read())
            CACHE.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(js), encoding="utf-8")
            return js
        except Exception:  # noqa: BLE001
            time.sleep(3 * (a + 1))
    raise RuntimeError(url)


def forcing(lat, lon, date):
    d0 = (pd.Timestamp(date) - pd.Timedelta(days=1)).date()
    d1 = (pd.Timestamp(date) + pd.Timedelta(days=1)).date()
    w = get_json(f"https://archive-api.open-meteo.com/v1/archive?latitude={lat:.4f}&longitude={lon:.4f}&start_date={d0}"
                 f"&end_date={d1}&hourly=wind_speed_10m,wind_direction_10m&wind_speed_unit=ms&models=era5&timezone=GMT",
                 CACHE / f"wind_{lat:.3f}_{lon:.3f}_{date}.json")
    c = get_json(f"https://marine-api.open-meteo.com/v1/marine?latitude={lat:.4f}&longitude={lon:.4f}&start_date={d0}"
                 f"&end_date={d1}&hourly=ocean_current_velocity,ocean_current_direction&timezone=GMT",
                 CACHE / f"cur_{lat:.3f}_{lon:.3f}_{date}.json")
    t = pd.to_datetime(w["hourly"]["time"])
    ws = np.array(w["hourly"]["wind_speed_10m"], float)
    wd = np.radians(np.array(w["hourly"]["wind_direction_10m"], float))  # FROM direction
    wu, wv = -ws * np.sin(wd), -ws * np.cos(wd)
    tc = pd.to_datetime(c["hourly"]["time"])
    cs = np.array(c["hourly"]["ocean_current_velocity"], float) / 3.6  # km/h -> m/s
    cd = np.radians(np.array(c["hourly"]["ocean_current_direction"], float))  # TOWARDS direction (oceanographic)
    cu, cv = cs * np.sin(cd), cs * np.cos(cd)
    f = pd.DataFrame(dict(time=t, wu=wu, wv=wv, ws=ws)).merge(pd.DataFrame(dict(time=tc, cu=cu, cv=cv, cs=cs)), on="time", how="left")
    f = f.interpolate(limit_direction="both")
    f.attrs["current_ok"] = bool(np.isfinite(f.cs).any())
    if not f.attrs["current_ok"]:  # coastal cell without SMOC data -> wind only (flagged in the output)
        f[["cu", "cv", "cs"]] = 0.0
    return f


def sample_release(r) -> np.ndarray:
    iv = []
    for s in str(r.feasible_intervals_utc).split(";"):
        if "-" in s:
            a, b = s.strip().split("-")
            iv.append((pd.Timestamp(f"{r.date} {a}"), pd.Timestamp(f"{r.date} {b}")))
    L = np.array([(b - a).total_seconds() + 300 for a, b in iv])
    k = RNG.choice(len(iv), N, p=L / L.sum())
    off = RNG.uniform(0, 1, N) * L[k]
    t0 = np.array([iv[i][0].value for i in k]) + (off * 1e9).astype(np.int64)
    return t0  # ns


def run(r, t_scene: pd.Timestamp, f: pd.DataFrame):
    t0 = sample_release(r)
    ang = RNG.uniform(0, 2 * math.pi, N)
    rad = 2000 * np.sqrt(RNG.uniform(0, 1, N))
    x, y = rad * np.cos(ang), rad * np.sin(ang)
    wind = RNG.uniform(0.01, 0.03, N)
    ft = f.time.values.astype("datetime64[ns]").astype(np.int64)
    ts = t_scene.value
    dt = 600.0  # s
    for i in range(N):
        t = t0[i]
        sgn = 1 if ts >= t else -1
        nsteps = int(abs(ts - t) / 1e9 // dt)
        rest = abs(ts - t) / 1e9 - nsteps * dt
        for s in range(nsteps + 1):
            h = dt if s < nsteps else rest
            if h <= 0:
                break
            u = np.interp(t, ft, f.cu.values) + wind[i] * np.interp(t, ft, f.wu.values)
            v = np.interp(t, ft, f.cv.values) + wind[i] * np.interp(t, ft, f.wv.values)
            x[i] += sgn * u * h + RNG.normal(0, math.sqrt(2 * K * h))
            y[i] += sgn * v * h + RNG.normal(0, math.sqrt(2 * K * h))
            t += int(sgn * h * 1e9)
    return x, y, (ts - t0) / 3.6e12


def main():
    from pyproj import Transformer
    from shapely.geometry import MultiPoint, mapping
    tt = pd.read_csv(S / "transect_times.csv").set_index("event_id")
    sc = pd.read_csv(S / "scenes.csv", parse_dates=["scene_datetime"])
    s2 = sc[(sc.endpoint == "earth-search") & (sc.collection.isin(["sentinel-2-l2a"]))
            & (sc.scene_datetime.dt.normalize() == pd.to_datetime(sc.obs_date))]
    ls = sc[(sc.collection == "landsat-c2-l2") & (sc.dt_min_abs_h.fillna(99) <= 9)]
    pairs = pd.concat([s2, ls]).drop_duplicates(["event_id", "scene_datetime"])
    feats, rows = [], []
    for p in pairs.itertuples():
        r = tt.loc[p.event_id]
        r = r.copy(); r["date"] = r.date
        f = forcing(r.lat, r.lon, r.date)
        x, y, dth = run(r, p.scene_datetime, f)
        d = np.hypot(x, y)
        cx, cy = np.median(x), np.median(y)
        keep = np.hypot(x - cx, y - cy) <= np.percentile(np.hypot(x - cx, y - cy), 95)
        epsg = 32600 + int((r.lon + 180) // 6) + 1
        tr = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        inv = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
        X0, Y0 = tr.transform(r.lon, r.lat)
        hull = MultiPoint(list(zip(X0 + x[keep], Y0 + y[keep]))).convex_hull
        hull_ll = [inv.transform(a, b) for a, b in hull.exterior.coords]
        row = dict(event_id=p.event_id, scene_id=p.item_id, collection=p.collection, scene_datetime=str(p.scene_datetime),
                   dt_h_median=round(float(np.median(dth)), 2), dt_h_p05=round(float(np.percentile(dth, 5)), 2),
                   dt_h_p95=round(float(np.percentile(dth, 95)), 2),
                   shift_median_km=round(float(np.hypot(cx, cy) / 1000), 2),
                   dist_p50_km=round(float(np.percentile(d, 50) / 1000), 2), dist_p95_km=round(float(np.percentile(d, 95) / 1000), 2),
                   zone95_area_km2=round(hull.area / 1e6, 1),
                   current_available=f.attrs.get("current_ok", True), wind_ms_mean=round(float(f.ws.mean()), 2), current_ms_mean=round(float(f.cs.mean()), 3),
                   centre_lon=round(inv.transform(X0 + cx, Y0 + cy)[0], 5), centre_lat=round(inv.transform(X0 + cx, Y0 + cy)[1], 5))
        rows.append(row)
        feats.append(dict(type="Feature", properties=row, geometry=dict(type="Polygon", coordinates=[hull_ll])))
        print(row, flush=True)
    pd.DataFrame(rows).to_csv(S / "drift_zones.csv", index=False)
    (S / "drift_zones.geojson").write_text(json.dumps(dict(type="FeatureCollection", features=feats)), encoding="utf-8")


if __name__ == "__main__":
    main()
