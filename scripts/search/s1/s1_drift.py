"""L88 / §11 п.1: search zone ("зона розыска") for S1 GPGP aerial mosaics (02.10.2016, 06.10.2016).

Drift is a SEARCH AREA, not proof that a satellite object is the same debris the aircraft counted.
Forcing (both cached in data/search/s1/cache/):
  currents - HYCOM GOFS 3.1 GLBv0.08 expt_57.2 (analysis, May 2016 - Jan 2017), surface water_u/v, 3-hourly,
             OPeNDAP https://tds.hycom.org/thredds/dodsC/GLBv0.08/expt_57.2
  wind     - ERA5 10 m wind via Open-Meteo archive API (NOT CDS), 1 deg point grid, hourly.
Particles: 200 per mosaic centre, start uniform over the UTC date (flight time unknown), 3 km Gaussian jitter,
windage alpha ~ U(0, 3 %), horizontal diffusion Kh = 10 m2/s, Euler 1 h, integrated -48..+48 h.
Outputs: drift_particles.npz, drift_zones.geojson, drift_stats.csv, forcing_meta.json
Run: .venv/Scripts/python.exe data/search/s1/s1_drift.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import xarray as xr
from shapely.geometry import MultiPoint, mapping

OUT = Path(__file__).resolve().parent
CACHE = OUT / "cache"
CACHE.mkdir(exist_ok=True)
LAT0, LAT1, LON0, LON1 = 27.0, 37.0, -148.0, -130.0
T0, T1 = "2016-09-29T00:00", "2016-10-09T00:00"
HYCOM = "https://tds.hycom.org/thredds/dodsC/GLBv0.08/expt_57.2"
RNG = np.random.default_rng(88)
N_PER, HOURS, KH, ALPHA_MAX = 200, 48, 10.0, 0.03


def hycom() -> xr.Dataset:
    f = CACHE / "hycom_gofs31_expt572_surface.nc"
    if f.exists():
        return xr.open_dataset(f)
    ds = xr.open_dataset(HYCOM, decode_times=False)
    t = ds["time"]
    units = t.attrs.get("units", "hours since 2000-01-01 00:00:00")
    base = pd.Timestamp(units.split("since")[1].strip()[:19])
    tt = base + pd.to_timedelta(t.values, unit="h")
    ti = np.where((tt >= pd.Timestamp(T0)) & (tt <= pd.Timestamp(T1)))[0]
    lat, lon = ds["lat"].values, ds["lon"].values
    yi = np.where((lat >= LAT0) & (lat <= LAT1))[0]
    l0, l1 = (LON0 % 360, LON1 % 360) if lon.max() > 180 else (LON0, LON1)
    xi = np.where((lon >= l0) & (lon <= l1))[0]
    sub = {}
    for v in ("water_u", "water_v"):
        for k in range(4):
            try:
                a = ds[v].isel(time=slice(ti[0], ti[-1] + 1), depth=0,
                               lat=slice(yi[0], yi[-1] + 1), lon=slice(xi[0], xi[-1] + 1)).load()
                break
            except Exception as e:  # noqa: BLE001 - flaky OPeNDAP, retry
                print("hycom retry", v, k, str(e)[:120]); time.sleep(10)
        sub[v] = a
    out = xr.Dataset({"u": sub["water_u"].drop_vars(["time", "depth"], errors="ignore"),
                      "v": sub["water_v"].drop_vars(["time", "depth"], errors="ignore")})
    out = out.assign_coords(time=tt[ti[0]:ti[-1] + 1].values, lon=((out.lon.values + 180) % 360) - 180)
    out.attrs["source"] = HYCOM + " (surface, depth=0 m)"
    out.to_netcdf(f)
    return out


def era5_wind() -> xr.Dataset:
    f = CACHE / "era5_openmeteo_wind.nc"
    if f.exists():
        return xr.open_dataset(f)
    lats = np.arange(LAT0, LAT1 + 0.01, 1.0)
    lons = np.arange(LON0, LON1 + 0.01, 1.0)
    pts = [(la, lo) for la in lats for lo in lons]
    res = {}
    for b in range(0, len(pts), 25):
        chunk = pts[b:b + 25]
        params = {"latitude": ",".join(f"{p[0]:.2f}" for p in chunk),
                  "longitude": ",".join(f"{p[1]:.2f}" for p in chunk),
                  "start_date": T0[:10], "end_date": T1[:10], "hourly": "wind_speed_10m,wind_direction_10m",
                  "wind_speed_unit": "ms", "models": "era5", "timezone": "GMT"}
        cf = CACHE / f"openmeteo_era5_{b:04d}.json"
        if cf.exists():
            d = json.loads(cf.read_text())
        else:
            for k in range(5):
                r = requests.get("https://archive-api.open-meteo.com/v1/archive", params=params, timeout=120)
                if r.status_code == 200:
                    break
                print("open-meteo", r.status_code, r.text[:200]); time.sleep(15)
            r.raise_for_status()
            d = r.json()
            cf.write_text(json.dumps(d))
        d = d if isinstance(d, list) else [d]
        for p, e in zip(chunk, d):
            res[p] = e["hourly"]
    tt = pd.to_datetime(next(iter(res.values()))["time"])
    U = np.full((len(tt), len(lats), len(lons)), np.nan)
    V = np.full_like(U, np.nan)
    for (la, lo), h in res.items():
        i, j = int(round(la - LAT0)), int(round(lo - LON0))
        s = np.array(h["wind_speed_10m"], float)
        dr = np.deg2rad(np.array(h["wind_direction_10m"], float))
        U[:, i, j] = -s * np.sin(dr)  # meteorological "from" direction -> vector "to"
        V[:, i, j] = -s * np.cos(dr)
    ds = xr.Dataset({"u10": (("time", "lat", "lon"), U), "v10": (("time", "lat", "lon"), V)},
                    coords={"time": tt.values, "lat": lats, "lon": lons})
    ds.attrs["source"] = "ERA5 via Open-Meteo archive API (models=era5), 1 deg point grid"
    ds.to_netcdf(f)
    return ds


def sample(ds: xr.Dataset, a: str, b: str, t, la, lo):
    kw = dict(time=xr.DataArray(t, dims="p"), lat=xr.DataArray(la, dims="p"), lon=xr.DataArray(lo, dims="p"))
    s = ds[[a, b]].interp(**kw, method="linear")
    return np.nan_to_num(s[a].values), np.nan_to_num(s[b].values)


def main() -> None:
    cur, wnd = hycom(), era5_wind()
    ev = pd.read_csv(OUT / "events.csv")
    n = len(ev) * N_PER
    eid = np.repeat(ev.event_id.values, N_PER)
    edate = np.repeat(pd.to_datetime(ev.date).values, N_PER)
    lat0 = np.repeat(ev.lat.values, N_PER) + RNG.normal(0, 3 / 111, n)
    lon0 = np.repeat(ev.lon.values, N_PER) + RNG.normal(0, 3 / 93, n)
    t_seed = edate + (RNG.uniform(0, 24, n) * 3600).astype("timedelta64[s]")
    alpha = RNG.uniform(0, ALPHA_MAX, n)
    steps = np.arange(-HOURS, HOURS + 1)
    LA = np.full((n, len(steps)), np.nan); LO = np.full_like(LA, np.nan)
    LA[:, HOURS], LO[:, HOURS] = lat0, lon0
    sig = np.sqrt(2 * KH * 3600.0)
    for direction in (1, -1):
        la, lo = lat0.copy(), lon0.copy()
        for k in range(1, HOURS + 1):
            t = t_seed + np.timedelta64(int(direction * (k - 1) * 3600), "s")
            uc, vc = sample(cur, "u", "v", t, la, lo)
            uw, vw = sample(wnd, "u10", "v10", t, la, lo)
            u = uc + alpha * uw; v = vc + alpha * vw
            dx = direction * u * 3600 + RNG.normal(0, sig, n)
            dy = direction * v * 3600 + RNG.normal(0, sig, n)
            la = la + dy / 111_000.0
            lo = lo + dx / (111_000.0 * np.cos(np.deg2rad(la)))
            LA[:, HOURS + direction * k], LO[:, HOURS + direction * k] = la, lo
    np.savez_compressed(OUT / "drift_particles.npz", lat=LA.astype("float32"), lon=LO.astype("float32"),
                        steps_h=steps, event_id=np.asarray(eid, dtype="U16"), t_seed=t_seed.astype("datetime64[s]").astype(str),
                        alpha=alpha.astype("float32"))
    # stats + zones
    km = lambda la1, lo1, la2, lo2: np.hypot((la2 - la1) * 111, (lo2 - lo1) * 111 * np.cos(np.deg2rad(la1)))
    rows, feats = [], []
    date_of = dict(zip(ev.event_id, ev.date))
    for h in (-48, -24, 24, 48):
        d = km(LA[:, HOURS], LO[:, HOURS], LA[:, HOURS + h], LO[:, HOURS + h])
        for dt in sorted(ev.date.unique()):
            m = np.array([date_of[e] == dt for e in eid])
            rows.append({"flight_date": dt, "horizon_h": h, "disp_km_p10": np.percentile(d[m], 10).round(1),
                         "disp_km_median": np.median(d[m]).round(1), "disp_km_p90": np.percentile(d[m], 90).round(1)})
            hull = MultiPoint(list(zip(LO[m, HOURS + h], LA[m, HOURS + h]))).convex_hull.buffer(0.02)
            feats.append({"type": "Feature", "geometry": mapping(hull),
                          "properties": {"flight_date": dt, "horizon_h": h, "area_km2":
                                         round(hull.area * 111 * 111 * np.cos(np.deg2rad(32)), 0),
                                         "note": "search zone (convex hull of particles), NOT proof of identity"}})
    # union over the whole -48..+48 envelope per flight date
    for dt in sorted(ev.date.unique()):
        m = np.array([date_of[e] == dt for e in eid])
        hull = MultiPoint(list(zip(LO[m].ravel()[::7], LA[m].ravel()[::7]))).convex_hull
        feats.append({"type": "Feature", "geometry": mapping(hull),
                      "properties": {"flight_date": dt, "horizon_h": "envelope_-48..48",
                                     "area_km2": round(hull.area * 111 * 111 * np.cos(np.deg2rad(32)), 0)}})
    pd.DataFrame(rows).to_csv(OUT / "drift_stats.csv", index=False)
    (OUT / "drift_zones.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    meta = {"currents": cur.attrs.get("source"), "wind": wnd.attrs.get("source"),
            "cur_time": [str(cur.time.values[0]), str(cur.time.values[-1])],
            "cur_speed_ms_median": float(np.nanmedian(np.hypot(cur.u, cur.v))),
            "wind_speed_ms_median": float(np.nanmedian(np.hypot(wnd.u10, wnd.v10))),
            "n_particles": int(n), "windage": f"U(0,{ALPHA_MAX})", "Kh_m2s": KH,
            "seed_time": "uniform over UTC flight date (time of flight unknown)"}
    (OUT / "forcing_meta.json").write_text(json.dumps(meta, indent=1))
    print(pd.DataFrame(rows).to_string()); print(meta)


if __name__ == "__main__":
    main()
