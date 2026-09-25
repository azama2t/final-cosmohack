"""L86 — S3 drift "search zones": particle cloud along each transect advected to every optical scene time within +-48 h.

  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/search/s3/s3_drift.py

Currents: HYCOM GOFS 3.1 reanalysis (GLBv0.08 expt_53.X for 2014, expt_56.3 for 2016), surface water_u/water_v, 3-hourly,
NCSS subset (cached in data/search/s3/drift/hycom_<cruise>.nc). GOFS 3.1 has NO tides -> tidal excursion added as a random
per-particle displacement U_tide/omega*(sin(w t1+phi)-sin(w t0+phi)) along a random axis (M2, U_tide 0.5 m/s, scenario).
Wind: ERA5 10 m via Open-Meteo archive API (NOT CDS), hourly at the transect centre (cached JSON); windage 1-3 % per particle.
Diffusion: random walk K = 10 m2/s.
Output: drift/zones.geojson (EPSG:4326 polygons, convex hull of particles + 0.5 km), drift/zones.csv (per event x scene time:
zone area, centroid shift from the transect centre, max distance of particles from the strip).
Drift = search area only, NOT evidence that the imaged water is the surveyed water.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import xarray as xr
from pyproj import Transformer
from shapely.geometry import LineString, MultiPoint, Point, mapping
from shapely.ops import transform as shp_transform

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data" / "search" / "s3"
DR = OUT / "drift"
DR.mkdir(parents=True, exist_ok=True)
HYCOM = {"2014": "GLBv0.08/expt_53.X/data/2014", "2016": "GLBv0.08/expt_56.3"}
N_PART, DT_S, K_DIFF, U_TIDE, OMEGA = 400, 900.0, 10.0, 0.5, 2 * np.pi / (12.42 * 3600)
RNG = np.random.default_rng(86)
TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32632", always_xy=True)
TO_LL = Transformer.from_crs("EPSG:32632", "EPSG:4326", always_xy=True)


def hycom(cruise: str, year: str, bbox, t0: str, t1: str) -> xr.Dataset:
    f = DR / f"hycom_{cruise}.nc"
    if not f.exists():
        url = (f"https://ncss.hycom.org/thredds/ncss/{HYCOM[year]}?var=water_u&var=water_v&north={bbox[3]}&west={bbox[0]}"
               f"&east={bbox[2]}&south={bbox[1]}&disableProjSubset=on&horizStride=1&time_start={t0}&time_end={t1}"
               f"&timeStride=1&vertCoord=0&accept=netcdf4")
        for a in range(4):
            try:
                r = requests.get(url, timeout=600)
                if r.status_code == 200 and len(r.content) > 1000:
                    f.write_bytes(r.content); break
                print("hycom http", r.status_code, r.text[:200])
            except Exception as e:  # noqa: BLE001
                print("hycom err", e)
            time.sleep(5 * (a + 1))
    ds = xr.open_dataset(f).isel(depth=0).load()
    return ds


def wind(lat: float, lon: float, d0: str, d1: str) -> pd.DataFrame:
    f = DR / f"wind_{lat:.2f}_{lon:.2f}_{d0}_{d1}.json"
    if not f.exists():
        url = (f"https://archive-api.open-meteo.com/v1/archive?latitude={lat:.3f}&longitude={lon:.3f}&start_date={d0}"
               f"&end_date={d1}&hourly=wind_speed_10m,wind_direction_10m&wind_speed_unit=ms&models=era5&timezone=GMT")
        r = requests.get(url, timeout=90); r.raise_for_status(); f.write_text(r.text, encoding="utf-8")
    j = json.loads(f.read_text(encoding="utf-8"))["hourly"]
    w = pd.DataFrame(j); w["time"] = pd.to_datetime(w.time, utc=True)
    rad = np.deg2rad(w.wind_direction_10m)  # direction the wind blows FROM
    w["u"] = -w.wind_speed_10m * np.sin(rad); w["v"] = -w.wind_speed_10m * np.cos(rad)
    return w.set_index("time")


def seed(seg: pd.DataFrame, n: int):
    """Particles uniform along the transect segments, each with its own observation time (linear along the segment)."""
    lens = seg.length_km.to_numpy(float); p = lens / lens.sum()
    k = RNG.choice(len(seg), n, p=p); f = RNG.random(n)
    s = seg.iloc[k]
    lon = s.lon_start.to_numpy() + f * (s.lon_end.to_numpy() - s.lon_start.to_numpy())
    lat = s.lat_start.to_numpy() + f * (s.lat_end.to_numpy() - s.lat_start.to_numpy())
    ts = pd.to_datetime(s.time_start_utc).to_numpy().astype("datetime64[s]").astype(float)
    te = pd.to_datetime(s.time_end_utc).to_numpy().astype("datetime64[s]").astype(float)
    return lon, lat, ts + f * (te - ts)


def advect(lon, lat, t_obs, t_target: float, ds: xr.Dataset, w: pd.DataFrame, windage):
    """Integrate each particle from its own obs time to t_target (forward or backward), Euler 15 min."""
    x, y = TO_UTM.transform(lon, lat)
    x, y = np.array(x, float), np.array(y, float)
    t = t_obs.copy()
    tt = ds.time.values.astype("datetime64[s]").astype(float)
    wt = w.index.values.astype("datetime64[s]").astype(float)
    sign = np.sign(t_target - t)
    remaining = np.abs(t_target - t)
    while (remaining > 0).any():
        step = np.minimum(remaining, DT_S)
        lo, la = TO_LL.transform(x, y)
        tm = np.clip(t, tt[0], tt[-1])
        uc = ds.water_u.interp(time=xr.DataArray(tm.astype("datetime64[s]"), dims="p"), lat=xr.DataArray(la, dims="p"),
                               lon=xr.DataArray(lo, dims="p")).values
        vc = ds.water_v.interp(time=xr.DataArray(tm.astype("datetime64[s]"), dims="p"), lat=xr.DataArray(la, dims="p"),
                               lon=xr.DataArray(lo, dims="p")).values
        uc = np.nan_to_num(uc); vc = np.nan_to_num(vc)  # land / outside -> no current
        uw = np.interp(t, wt, w.u.values); vw = np.interp(t, wt, w.v.values)
        u = uc + windage * uw; v = vc + windage * vw
        x += sign * u * step + np.sqrt(2 * K_DIFF * step) * RNG.standard_normal(len(x)) * (step > 0)
        y += sign * v * step + np.sqrt(2 * K_DIFF * step) * RNG.standard_normal(len(x)) * (step > 0)
        t += sign * step
        remaining -= step
    return x, y


def main():
    tr = pd.read_csv(ROOT / "data" / "case" / "geometry" / "transects.csv")
    tr = tr[tr.source_id == "S3_SE_NORTH_SEA"].copy()
    tr["cruise"] = tr.event_id.str.extract(r"S3:(HE\d+)_")[0]
    inv = pd.read_csv(OUT / "inventory.csv")
    opt = inv[(inv.role == "optical") & (inv.dt_h.abs() <= 48)].drop_duplicates(["event_id", "scene_datetime"])
    feats, rows = [], []
    for cr, g in tr.groupby("cruise"):
        year = str(pd.to_datetime(g.time_start_utc).min().year)
        bbox = [round(min(g.lon_start.min(), g.lon_end.min()) - 1.0, 2), round(min(g.lat_start.min(), g.lat_end.min()) - 1.0, 2),
                round(max(g.lon_start.max(), g.lon_end.max()) + 1.0, 2), round(max(g.lat_start.max(), g.lat_end.max()) + 1.0, 2)]
        t0 = (pd.to_datetime(g.time_start_utc).min() - pd.Timedelta(days=2)).strftime("%Y-%m-%dT00:00:00Z")
        t1 = (pd.to_datetime(g.time_end_utc).max() + pd.Timedelta(days=2)).strftime("%Y-%m-%dT23:59:00Z")
        ds = hycom(cr, year, bbox, t0, t1)
        spd = np.hypot(ds.water_u, ds.water_v)
        print(cr, "HYCOM", dict(ds.sizes), "surface speed mean", round(float(spd.mean()), 3), flush=True)
        for eid, seg in g.groupby("event_id"):
            sc = opt[opt.event_id == eid]
            if sc.empty:
                continue
            clat = float(np.r_[seg.lat_start, seg.lat_end].mean()); clon = float(np.r_[seg.lon_start, seg.lon_end].mean())
            w = wind(clat, clon, t0[:10], t1[:10])
            lon, lat, tobs = seed(seg, N_PART)
            windage = RNG.uniform(0.01, 0.03, N_PART)
            phase = RNG.uniform(0, 2 * np.pi, N_PART); ang = RNG.uniform(0, np.pi, N_PART)
            line = LineString([TO_UTM.transform(r.lon_start, r.lat_start) for r in seg.itertuples()] +
                              [TO_UTM.transform(seg.iloc[-1].lon_end, seg.iloc[-1].lat_end)])
            cx, cy = TO_UTM.transform(clon, clat)
            for s in sc.itertuples():
                tsc = pd.Timestamp(s.scene_datetime).to_datetime64().astype("datetime64[s]").astype(float)
                x, y = advect(lon, lat, tobs, tsc, ds, w, windage)
                # tidal excursion (no tides in GOFS 3.1): M2 displacement between obs and scene time, random phase/axis
                dtid = U_TIDE / OMEGA * (np.sin(OMEGA * tsc + phase) - np.sin(OMEGA * tobs + phase))
                xt, yt = x + dtid * np.cos(ang), y + dtid * np.sin(ang)
                hull0 = MultiPoint(list(zip(x, y))).convex_hull.buffer(500)
                hull = MultiPoint(list(zip(xt, yt))).convex_hull.buffer(500)
                dist = np.array([line.distance(Point(a, b)) for a, b in zip(xt, yt)])
                mx, my = float(np.mean(xt)), float(np.mean(yt))
                wwin = w[(w.index >= pd.Timestamp(min(tobs.min(), tsc), unit="s", tz="UTC")) &
                         (w.index <= pd.Timestamp(max(tobs.max(), tsc), unit="s", tz="UTC"))]
                row = dict(event_id=eid, scene_datetime=s.scene_datetime, dt_mid_h=s.dt_mid_h,
                           zone_area_km2=round(hull.area / 1e6, 1), zone_area_no_tide_km2=round(hull0.area / 1e6, 1),
                           centroid_shift_km=round(float(np.hypot(mx - cx, my - cy)) / 1000, 2),
                           centroid_shift_no_tide_km=round(float(np.hypot(x.mean() - cx, y.mean() - cy)) / 1000, 2),
                           dist_from_track_km_p50=round(float(np.median(dist)) / 1000, 2),
                           dist_from_track_km_p90=round(float(np.percentile(dist, 90)) / 1000, 2),
                           wind_ms_mean=round(float(wwin.wind_speed_10m.mean()), 1) if len(wwin) else None,
                           hycom_speed_mean_ms=round(float(spd.mean()), 3))
                rows.append(row)
                geom = shp_transform(lambda a, b, z=None: TO_LL.transform(a, b), hull)
                feats.append(dict(type="Feature", geometry=mapping(geom), properties=row))
                print(row, flush=True)
    pd.DataFrame(rows).to_csv(DR / "zones.csv", index=False)
    (DR / "zones.geojson").write_text(json.dumps(dict(type="FeatureCollection", features=feats)), encoding="utf-8")


if __name__ == "__main__":
    main()
