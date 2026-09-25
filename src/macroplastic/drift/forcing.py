"""Forcing for the drift demo: small space-time windows cut into local CF NetCDF files.

Primary sources (no keys needed):
  currents - HYCOM ESPC-D-V02 global 1/12 deg analysis, hourly surface velocity ssu/ssv
             (THREDDS OPeNDAP ``tds.hycom.org/thredds/dodsC/ESPC-D-V02/ice/<year>``, Aug 2024 - present);
  wind     - NCEP GFS 0.5 deg 10 m wind ugrd10m/vgrd10m, 3-hourly
             (PacIOOS THREDDS ``ncep_global/NCEP_Global_Atmospheric_Model_best.ncd``, Dec 2022 - present).
Fallbacks: Open-Meteo Marine currents + Open-Meteo historical-forecast wind on a coarse point grid; last resort
constant fields (zero current / constant wind). Every writer returns a short human-readable source string.

Output variables use OpenDrift/CF standard names (x_sea_water_velocity, y_sea_water_velocity, x_wind, y_wind) on a
regular lon/lat grid (lon in -180..180), time as ``hours since 1970-01-01``. CDS/ERA5 are deliberately not used.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

HYCOM_URL = "https://tds.hycom.org/thredds/dodsC/ESPC-D-V02/ice/{year}"
HYCOM_NAME = "HYCOM ESPC-D-V02 global 1/12° analysis, hourly surface currents (tds.hycom.org OPeNDAP)"
HYCOM_NAME_FILES = ("HYCOM ESPC-D-V02 global 1/12° analysis, surface currents every {step_h} h "
                    "(tds.hycom.org OPeNDAP, per-hour archive files)")
NCEP_URL = "https://pae-paha.pacioos.hawaii.edu/thredds/dodsC/ncep_global/NCEP_Global_Atmospheric_Model_best.ncd"
NCEP_NAME = "NCEP GFS 0.5° 10 m wind, 3-hourly (PacIOOS THREDDS)"
OM_MARINE = "https://marine-api.open-meteo.com/v1/marine"
OM_WIND = "https://historical-forecast-api.open-meteo.com/v1/forecast"
EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


@dataclass
class Window:
    west: float
    south: float
    east: float
    north: float
    t0: dt.datetime  # UTC, aware
    t1: dt.datetime

    @classmethod
    def around(cls, bounds_wgs84, start: dt.datetime, pad_deg: float = 1.0, before_h: float = 3, after_h: float = 75):
        w, s, e, n = bounds_wgs84
        t0 = (start - dt.timedelta(hours=before_h)).replace(minute=0, second=0, microsecond=0)
        t1 = (start + dt.timedelta(hours=after_h)).replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
        return cls(w - pad_deg, s - pad_deg, e + pad_deg, n + pad_deg, t0, t1)


def parse_time(s: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _hours_since_epoch(t: dt.datetime) -> float:
    return (t - EPOCH).total_seconds() / 3600.0


def _units_offset_h(units: str) -> float:
    """Hours between the CF reference date in ``units`` ('<unit> since <date>') and 1970-01-01."""
    import cftime
    ref = cftime.num2date(0, units, only_use_cftime_datetimes=False, only_use_python_datetimes=True)
    ref = ref.replace(tzinfo=dt.timezone.utc) if ref.tzinfo is None else ref
    return (ref - EPOCH).total_seconds() / 3600.0


def _lon_indices(lon360: np.ndarray, west: float, east: float) -> list[np.ndarray]:
    """Index runs of a 0..360 longitude axis covering [west, east] given in -180..180 (handles the 0 meridian)."""
    w, e = west % 360.0, east % 360.0
    if w <= e:
        return [np.where((lon360 >= w) & (lon360 <= e))[0]]
    return [np.where(lon360 >= w)[0], np.where(lon360 <= e)[0]]


def write_cf(path: Path, times_h: np.ndarray, lat: np.ndarray, lon: np.ndarray, fields: dict[str, np.ndarray],
             attrs: dict) -> Path:
    """Write a regular lon/lat CF NetCDF readable by opendrift reader_netCDF_CF_generic."""
    import xarray as xr
    lon = np.where(lon > 180, lon - 360, lon)
    order_x = np.argsort(lon)
    order_y = np.argsort(lat)
    data_vars = {}
    units = {"x_sea_water_velocity": "m s-1", "y_sea_water_velocity": "m s-1", "x_wind": "m s-1", "y_wind": "m s-1"}
    for name, arr in fields.items():
        arr = np.asarray(arr, dtype="float32")[:, order_y][:, :, order_x]
        data_vars[name] = (("time", "lat", "lon"), arr, {"standard_name": name, "units": units.get(name, "m s-1")})
    ds = xr.Dataset(
        data_vars,
        coords={
            "time": ("time", np.asarray(times_h, dtype="float64"),
                     {"standard_name": "time", "units": "hours since 1970-01-01 00:00:00", "calendar": "standard"}),
            "lat": ("lat", np.asarray(lat, dtype="float64")[order_y], {"standard_name": "latitude", "units": "degrees_north"}),
            "lon": ("lon", np.asarray(lon, dtype="float64")[order_x], {"standard_name": "longitude", "units": "degrees_east"}),
        },
        attrs={"Conventions": "CF-1.6", **{k: str(v) for k, v in attrs.items()}},
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.nc")
    ds.to_netcdf(tmp, encoding={k: {"zlib": True, "complevel": 4, "_FillValue": np.float32(np.nan)} for k in data_vars})
    tmp.replace(path)
    return path


def _retry(fn, tries: int = 3, wait: float = 5.0):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # network hiccups on OPeNDAP are common
            last = e
            log.warning("attempt %d/%d failed: %s", i + 1, tries, e)
            time.sleep(wait * (i + 1))
    raise last


# ---------------------------------------------------------------- HYCOM currents
HYCOM_FILE = ("https://tds.hycom.org/thredds/dodsC/datasets/ESPC-D-V02/data/archive/{year}/"
              "US058GCOM-OPSnce.espc-d-031-hycom_fcst_glby008_{run}12_t{tau:04d}_ice.nc")


def _url_alive(url: str, timeout: float = 15.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception as e:
        log.warning("probe %s failed: %s", url, e)
        return False


def fetch_hycom_currents(win: Window, out: Path) -> str:
    """Yearly OPeNDAP aggregation if it answers quickly, else per-hour archive files (daily 12Z run, t0000..t0023)."""
    years = sorted({win.t0.year, win.t1.year})
    # Yearly aggregations of past years answer .dds but can then hang indefinitely on data reads (seen for 2025,
    # netCDF4/OPeNDAP has no read timeout) -> use the aggregation only for the current year.
    this_year = dt.datetime.now(dt.timezone.utc).year
    if len(years) == 1 and years[0] == this_year and _url_alive(HYCOM_URL.format(year=years[0]) + ".dds"):
        try:
            return _fetch_hycom_aggregation(win, out)
        except Exception as e:
            log.warning("HYCOM aggregation failed (%s); per-hour files", e)
    return _fetch_hycom_hourly_files(win, out)


def _fetch_hycom_hourly_files(win: Window, out: Path, step_h: int = 3) -> str:
    import netCDF4
    hours = []
    t = win.t0
    while t <= win.t1:
        hours.append(t)
        t += dt.timedelta(hours=step_h)
    lat = lon = iy = runs = None
    us, vs, ts = [], [], []
    for t in hours:
        run = (t - dt.timedelta(hours=12)).date()  # the 12Z run whose t0000..t0023 covers t
        tau = int(round((t - dt.datetime(run.year, run.month, run.day, 12, tzinfo=dt.timezone.utc)).total_seconds() / 3600))
        url = HYCOM_FILE.format(year=run.year, run=run.strftime("%Y%m%d"), tau=tau)
        try:
            nc = _retry(lambda: netCDF4.Dataset(url), tries=2, wait=2)
        except Exception as e:
            log.warning("HYCOM file missing %s: %s", url, e)
            continue
        try:
            if lat is None:
                lat = np.asarray(nc["lat"][:], dtype="float64")
                lon = np.asarray(nc["lon"][:], dtype="float64")
                iy = np.where((lat >= win.south) & (lat <= win.north))[0]
                runs = _lon_indices(lon, win.west, win.east)
            fr = {}
            for var in ("ssu", "ssv"):
                parts = []
                for ix in runs:
                    arr = _retry(lambda ix=ix: nc[var][0, iy[0]:iy[-1] + 1, ix[0]:ix[-1] + 1], tries=2, wait=2)
                    parts.append(np.ma.filled(np.ma.asarray(arr, dtype="float32"), np.nan))
                fr[var] = np.concatenate(parts, axis=1)
            us.append(fr["ssu"]); vs.append(fr["ssv"]); ts.append(_hours_since_epoch(t))
        finally:
            nc.close()
    if len(ts) < 0.6 * len(hours):
        raise RuntimeError(f"HYCOM hourly files: only {len(ts)}/{len(hours)} available")
    lon_sel = np.concatenate([lon[ix[0]:ix[-1] + 1] for ix in runs])
    fields = {"x_sea_water_velocity": np.stack(us), "y_sea_water_velocity": np.stack(vs)}
    write_cf(out, np.array(ts), lat[iy[0]:iy[-1] + 1], lon_sel, fields,
             {"source": HYCOM_NAME, "url": HYCOM_FILE, "variables": f"ssu,ssv (surface), per-hour archive files every {step_h} h",
              "n_hours": len(ts)})
    return HYCOM_NAME_FILES.format(step_h=step_h)


def _fetch_hycom_aggregation(win: Window, out: Path) -> str:
    import netCDF4
    years = sorted({win.t0.year, win.t1.year})
    if len(years) > 1:
        raise RuntimeError("window spans a year boundary; HYCOM yearly aggregations not stitched")
    url = HYCOM_URL.format(year=years[0])
    nc = _retry(lambda: netCDF4.Dataset(url))
    try:
        lat = np.asarray(nc["lat"][:], dtype="float64")
        lon = np.asarray(nc["lon"][:], dtype="float64")
        tm = np.asarray(nc["time"][:], dtype="float64")
        off = _units_offset_h(nc["time"].units)  # "hours since 2000-01-01 00:00:00"
        a, b = _hours_since_epoch(win.t0) - off, _hours_since_epoch(win.t1) - off
        it = np.where((tm >= a - 1e-6) & (tm <= b + 1e-6))[0]
        if len(it) < 12:
            raise RuntimeError(f"HYCOM {url}: only {len(it)} time steps in window {win.t0}..{win.t1}")
        iy = np.where((lat >= win.south) & (lat <= win.north))[0]
        runs = _lon_indices(lon, win.west, win.east)
        fields = {}
        for var, name in (("ssu", "x_sea_water_velocity"), ("ssv", "y_sea_water_velocity")):
            parts = []
            for ix in runs:
                arr = _retry(lambda ix=ix: nc[var][it[0]:it[-1] + 1, iy[0]:iy[-1] + 1, ix[0]:ix[-1] + 1])
                parts.append(np.ma.filled(np.ma.asarray(arr, dtype="float32"), np.nan))
            fields[name] = np.concatenate(parts, axis=2)
        lon_sel = np.concatenate([lon[ix[0]:ix[-1] + 1] for ix in runs])
        times_h = tm[it[0]:it[-1] + 1] + off
    finally:
        nc.close()
    frac_nan = float(np.isnan(fields["x_sea_water_velocity"]).mean())
    if frac_nan > 0.98:
        raise RuntimeError("HYCOM window is (almost) all land/NaN")
    write_cf(out, times_h, lat[iy[0]:iy[-1] + 1], lon_sel, fields,
             {"source": HYCOM_NAME, "url": url, "variables": "ssu,ssv (surface)", "land_nan_frac": round(frac_nan, 3)})
    return HYCOM_NAME


# ---------------------------------------------------------------- NCEP wind
def fetch_ncep_wind(win: Window, out: Path) -> str:
    import netCDF4
    nc = _retry(lambda: netCDF4.Dataset(NCEP_URL))
    try:
        lat = np.asarray(nc["latitude"][:], dtype="float64")
        lon = np.asarray(nc["longitude"][:], dtype="float64")
        tm = np.asarray(nc["time"][:], dtype="float64")
        tunits = nc["time"].units  # "hours since 2022-12-01 00:00:00.000 UTC"
        scale = 24.0 if tunits.strip().startswith("days") else 1.0
        tm_h = tm * scale + _units_offset_h(tunits)
        a, b = _hours_since_epoch(win.t0) - 3, _hours_since_epoch(win.t1) + 3
        it = np.where((tm_h >= a) & (tm_h <= b))[0]
        if len(it) < 8:
            raise RuntimeError(f"NCEP: only {len(it)} time steps in window")
        pad = 0.5  # one grid cell margin
        iy = np.where((lat >= win.south - pad) & (lat <= win.north + pad))[0]
        runs = _lon_indices(lon, win.west - pad, win.east + pad)
        fields = {}
        for var, name in (("ugrd10m", "x_wind"), ("vgrd10m", "y_wind")):
            parts = []
            for ix in runs:
                arr = _retry(lambda ix=ix: nc[var][it[0]:it[-1] + 1, iy.min():iy.max() + 1, ix[0]:ix[-1] + 1])
                parts.append(np.ma.filled(np.ma.asarray(arr, dtype="float32"), np.nan))
            fields[name] = np.concatenate(parts, axis=2)
        lon_sel = np.concatenate([lon[ix[0]:ix[-1] + 1] for ix in runs])
        times_h = tm_h[it[0]:it[-1] + 1]
        lat_sel = lat[iy.min():iy.max() + 1]
    finally:
        nc.close()
    # 'best' aggregations can repeat times; keep strictly increasing
    keep = np.concatenate([[True], np.diff(times_h) > 0])
    fields = {k: v[keep] for k, v in fields.items()}
    write_cf(out, times_h[keep], lat_sel, lon_sel, fields, {"source": NCEP_NAME, "url": NCEP_URL})
    return NCEP_NAME


# ---------------------------------------------------------------- Open-Meteo fallback
def _grid_points(win: Window, n: int = 6):
    lats = np.linspace(win.south, win.north, n)
    lons = np.linspace(win.west, win.east, n)
    return lats, lons


def _om_get(base: str, params: dict) -> list[dict]:
    url = base + "?" + urllib.parse.urlencode(params, safe=",")
    def go():
        with urllib.request.urlopen(url, timeout=120) as r:
            return json.loads(r.read().decode())
    res = _retry(go)
    return res if isinstance(res, list) else [res]


def fetch_openmeteo(win: Window, out_currents: Path | None, out_wind: Path | None, n: int = 6) -> tuple[str, str]:
    lats, lons = _grid_points(win, n)
    LA, LO = np.meshgrid(lats, lons, indexing="ij")
    common = {"latitude": ",".join(f"{v:.3f}" for v in LA.ravel()), "longitude": ",".join(f"{v:.3f}" for v in LO.ravel()),
              "start_date": win.t0.date().isoformat(), "end_date": win.t1.date().isoformat(), "timezone": "UTC"}
    cur_name = wind_name = ""
    if out_currents is not None:
        res = _om_get(OM_MARINE, {**common, "hourly": "ocean_current_velocity,ocean_current_direction",
                                  "cell_selection": "sea"})
        times = np.array([_hours_since_epoch(parse_time(t + ":00Z" if len(t) == 16 else t))
                          for t in res[0]["hourly"]["time"]])
        u = np.full((len(times), n, n), np.nan, "float32"); v = u.copy()
        for k, r in enumerate(res):
            i, j = divmod(k, n)
            spd = np.array(r["hourly"]["ocean_current_velocity"], dtype="float64")
            unit = r.get("hourly_units", {}).get("ocean_current_velocity", "km/h")
            spd = spd / 3.6 if "km" in unit else spd
            dirn = np.deg2rad(np.array(r["hourly"]["ocean_current_direction"], dtype="float64"))  # direction TO
            u[:, i, j] = spd * np.sin(dirn); v[:, i, j] = spd * np.cos(dirn)
        write_cf(out_currents, times, lats, lons, {"x_sea_water_velocity": u, "y_sea_water_velocity": v},
                 {"source": "Open-Meteo Marine API (ocean_current_velocity/direction)", "grid": f"{n}x{n} points"})
        cur_name = "Open-Meteo Marine (ocean_current_velocity), сетка точек"
    if out_wind is not None:
        res = _om_get(OM_WIND, {**common, "hourly": "wind_speed_10m,wind_direction_10m", "wind_speed_unit": "ms"})
        times = np.array([_hours_since_epoch(parse_time(t + ":00Z" if len(t) == 16 else t))
                          for t in res[0]["hourly"]["time"]])
        u = np.full((len(times), n, n), np.nan, "float32"); v = u.copy()
        for k, r in enumerate(res):
            i, j = divmod(k, n)
            spd = np.array(r["hourly"]["wind_speed_10m"], dtype="float64")
            dirn = np.deg2rad(np.array(r["hourly"]["wind_direction_10m"], dtype="float64"))  # direction FROM
            u[:, i, j] = -spd * np.sin(dirn); v[:, i, j] = -spd * np.cos(dirn)
        write_cf(out_wind, times, lats, lons, {"x_wind": u, "y_wind": v},
                 {"source": "Open-Meteo historical-forecast API (wind_speed_10m/direction)", "grid": f"{n}x{n} points"})
        wind_name = "Open-Meteo historical forecast 10 m wind, сетка точек"
    return cur_name, wind_name


def write_constant(win: Window, out: Path, kind: str, u: float = 0.0, v: float = 0.0) -> str:
    """Constant field on a 2x2 grid over the window (last-resort fallback / offline tests)."""
    times = np.arange(math.floor(_hours_since_epoch(win.t0)), math.ceil(_hours_since_epoch(win.t1)) + 1, 1.0)
    lats = np.array([win.south, win.north]); lons = np.array([win.west, win.east])
    shape = (len(times), 2, 2)
    if kind == "currents":
        fields = {"x_sea_water_velocity": np.full(shape, u, "float32"), "y_sea_water_velocity": np.full(shape, v, "float32")}
    else:
        fields = {"x_wind": np.full(shape, u, "float32"), "y_wind": np.full(shape, v, "float32")}
    write_cf(out, times, lats, lons, fields, {"source": f"constant field u={u} v={v} m/s"})
    return f"постоянное поле u={u:g} v={v:g} м/с"


# ---------------------------------------------------------------- orchestration
def get_forcing(region: str, date: str, win: Window, cache_dir: Path, refresh: bool = False) -> dict:
    """Return {"currents": (path, name), "wind": (path, name), "log": [...]} trying sources in order.

    Cached files are reused (their source string is kept in a sidecar json)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    res, notes = {}, []
    for kind, primary in (("currents", fetch_hycom_currents), ("wind", fetch_ncep_wind)):
        path = cache_dir / f"{region}_{date}_{kind}.nc"
        side = path.with_suffix(".json")
        if path.exists() and side.exists() and not refresh:
            res[kind] = (path, json.loads(side.read_text(encoding="utf-8"))["source"])
            notes.append(f"{kind}: cache {path.name}")
            continue
        name = None
        t = time.time()
        try:
            name = primary(win, path)
            notes.append(f"{kind}: {name} ({time.time() - t:.0f} s)")
        except Exception as e:
            notes.append(f"{kind}: primary failed: {e!r}")
            log.warning("%s primary failed: %s", kind, e)
            try:
                c, w = fetch_openmeteo(win, path if kind == "currents" else None, path if kind == "wind" else None)
                name = c or w
                notes.append(f"{kind}: fallback {name}")
            except Exception as e2:
                notes.append(f"{kind}: Open-Meteo failed: {e2!r}; constant field")
                name = write_constant(win, path, kind)
        side.write_text(json.dumps({"source": name, "window": [win.west, win.south, win.east, win.north,
                                                               win.t0.isoformat(), win.t1.isoformat()]},
                                   ensure_ascii=False), encoding="utf-8")
        res[kind] = (path, name)
    res["log"] = notes
    return res
