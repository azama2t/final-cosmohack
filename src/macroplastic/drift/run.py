"""Seed particles around MDD detections, run OpenDrift OceanDrift for 72 h, write drift.json (docs/CONTRACTS.md).

drift.json:
  {"region","date","start_time","hours":[0..72],
   "particles":[{"id","path":[[lon,lat,t_h],...],"stranded"?:true,"stranded_hour"?:h}],
   "forcing":{"currents","wind","wind_drift_factor","model","horizontal_diffusivity_m2s","seeding"},
   "ensemble"?:[{"wind_drift_factor":0.01,"particles":[...]}, ...],
   "stats":{...}, "note":"демонстрационный прогноз, ..."}
Every path has exactly len(hours) points; a particle stranded on the coast keeps its last position afterwards.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

MODEL = "OpenDrift OceanDrift"
EARTH_R = 6371000.0


# ---------------------------------------------------------------- seeds
def load_detection_centroids(root: Path, region: str, date: str, model: str = "mdd") -> tuple[np.ndarray, str]:
    """(N,3) lon/lat/area_m2 of detections. Service detections.geojson first, else prob_<model>.tif >= threshold."""
    gj = root / "service" / "data" / region / date / model / "detections.geojson"
    if gj.exists():
        fc = json.loads(gj.read_text(encoding="utf-8"))
        pts = []
        for f in fc.get("features", []):
            p = f.get("properties", {})
            area = float(p.get("area_m2") or 100.0)
            if "lon" in p and "lat" in p:
                pts.append((float(p["lon"]), float(p["lat"]), area))
            else:
                from shapely.geometry import shape
                c = shape(f["geometry"]).centroid
                pts.append((c.x, c.y, area))
        return np.array(pts, dtype="float64").reshape(-1, 3), str(gj.relative_to(root))
    return centroids_from_prob(root / "data" / "live" / region / date, model)


def centroids_from_prob(scene_dir: Path, model: str = "mdd") -> tuple[np.ndarray, str]:
    import rasterio
    from pyproj import Transformer
    from scipy import ndimage
    meta = json.loads((scene_dir / f"prob_{model}.json").read_text(encoding="utf-8"))
    thr = float(meta.get("threshold", 0.5))
    with rasterio.open(scene_dir / f"prob_{model}.tif") as src:
        prob = src.read(1).astype("float32") / 255.0
        tr, crs = src.transform, src.crs
    flag = prob >= thr
    wm = scene_dir / "water_mask.tif"
    if wm.exists():
        with rasterio.open(wm) as src:
            flag &= src.read(1) > 0
    lab, n = ndimage.label(flag, structure=np.ones((3, 3)))
    if n == 0:
        return np.zeros((0, 3)), f"{scene_dir.name}/prob_{model}.tif (0 components)"
    cy_cx = np.array(ndimage.center_of_mass(flag, lab, range(1, n + 1)))
    xs = tr.c + (cy_cx[:, 1] + 0.5) * tr.a
    ys = tr.f + (cy_cx[:, 0] + 0.5) * tr.e
    lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(xs, ys)
    area = np.asarray(ndimage.sum(flag, lab, range(1, n + 1))) * abs(tr.a * tr.e)
    return np.column_stack([lon, lat, area]), f"prob_{model}.tif >= {thr}"


def seed_points(centroids: np.ndarray, n_total: int = 300, sigma_m: float = 150.0, max_r_m: float = 300.0,
                seed: int = 0, weights=None, min_per: int = 5) -> np.ndarray:
    """Particles around detection centroids, Gaussian offset (sigma, clipped to max_r).

    Share per detection: proportional to ``weights`` (patch area) with at least ``min_per`` each, else equal.
    If there are already >= n_total detections, the centroids themselves are used (thinned evenly to n_total)."""
    c = np.asarray(centroids, dtype="float64")[:, :2].reshape(-1, 2)
    if len(c) == 0:
        return c
    if len(c) >= n_total:
        idx = np.linspace(0, len(c) - 1, n_total).round().astype(int)
        return c[idx]
    rng = np.random.default_rng(seed)
    if weights is None or len(c) * min_per >= n_total:
        per = np.full(len(c), n_total // len(c))
    else:
        w = np.asarray(weights, dtype="float64").clip(min=1e-9)
        per = min_per + np.floor((n_total - min_per * len(c)) * w / w.sum()).astype(int)
    rest = n_total - per.sum()
    per[np.argsort(-np.asarray(weights if weights is not None else np.ones(len(c))))[:rest]] += 1
    out = []
    for (lon, lat), k in zip(c, per):
        d = rng.normal(0.0, sigma_m, size=(k, 2))
        r = np.hypot(d[:, 0], d[:, 1])
        d *= np.minimum(1.0, max_r_m / np.maximum(r, 1e-9))[:, None]
        dlat = np.degrees(d[:, 1] / EARTH_R)
        dlon = np.degrees(d[:, 0] / (EARTH_R * math.cos(math.radians(lat))))
        out.append(np.column_stack([lon + dlon, lat + dlat]))
    return np.concatenate(out)


# ---------------------------------------------------------------- simulation
def simulate(seeds: np.ndarray, start: dt.datetime, currents_nc: Path, wind_nc: Path, wind_drift_factor: float = 0.02,
             hours: int = 72, time_step_min: int = 15, horizontal_diffusivity: float = 5.0, seed: int = 0,
             loglevel: int = 50) -> dict:
    """Run OceanDrift; return lon/lat (n, hours+1) with stranded particles frozen at their last position."""
    from opendrift.models.oceandrift import OceanDrift
    from opendrift.readers import reader_netCDF_CF_generic

    np.random.seed(seed)  # OpenDrift uses the global numpy RNG for diffusion
    o = OceanDrift(loglevel=loglevel)
    o.add_reader([reader_netCDF_CF_generic.Reader(str(currents_nc)), reader_netCDF_CF_generic.Reader(str(wind_nc))])
    o.set_config("general:coastline_action", "stranding")
    o.set_config("seed:ocean_only", True)
    o.set_config("drift:stokes_drift", False)  # no wave forcing; wind_drift_factor covers windage + Stokes
    o.set_config("environment:constant:horizontal_diffusivity", float(horizontal_diffusivity))
    start_naive = start.astimezone(dt.timezone.utc).replace(tzinfo=None)
    o.seed_elements(lon=seeds[:, 0], lat=seeds[:, 1], time=start_naive, z=0.0,
                    wind_drift_factor=float(wind_drift_factor))
    o.run(duration=dt.timedelta(hours=hours), time_step=dt.timedelta(minutes=time_step_min),
          time_step_output=dt.timedelta(hours=1))
    r = o.result
    lon = np.asarray(r["lon"].values, dtype="float64")
    lat = np.asarray(r["lat"].values, dtype="float64")
    status = np.asarray(r["status"].values)
    n_t = hours + 1
    if lon.shape[1] < n_t:  # run stopped early (all particles deactivated) -> pad
        pad = n_t - lon.shape[1]
        lon = np.pad(lon, ((0, 0), (0, pad)), constant_values=np.nan)
        lat = np.pad(lat, ((0, 0), (0, pad)), constant_values=np.nan)
        status = np.pad(np.nan_to_num(status.astype("float64"), nan=-1), ((0, 0), (0, pad)), constant_values=-1)
    lon, lat, status = lon[:, :n_t], lat[:, :n_t], status[:, :n_t]
    cats = list(o.status_categories)
    stranded_code = cats.index("stranded") if "stranded" in cats else -999
    valid = np.isfinite(lon) & np.isfinite(lat)
    last_valid = np.where(valid.any(1), n_t - 1 - np.argmax(valid[:, ::-1], axis=1), -1)
    stranded = np.zeros(len(lon), bool)
    stranded_hour = np.full(len(lon), -1)
    for i in range(len(lon)):
        st = np.nan_to_num(status[i].astype("float64"), nan=-1)
        if (st == stranded_code).any():
            stranded[i] = True
            stranded_hour[i] = int(np.argmax(st == stranded_code))
        elif last_valid[i] < n_t - 1 and last_valid[i] >= 0:
            # deactivated for another reason (e.g. out of forcing domain): keep as frozen, not stranded
            pass
        # forward-fill NaNs (after deactivation) and back-fill leading NaNs with the seed position
        li, la = lon[i], lat[i]
        if not np.isfinite(li[0]):
            li[0], la[0] = seeds[i]
        for t in range(1, n_t):
            if not (np.isfinite(li[t]) and np.isfinite(la[t])):
                li[t], la[t] = li[t - 1], la[t - 1]
    return {"lon": lon, "lat": lat, "stranded": stranded, "stranded_hour": stranded_hour,
            "status_categories": cats, "readers": [str(currents_nc.name), str(wind_nc.name)]}


# ---------------------------------------------------------------- stats / json
def haversine_km(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_R / 1000 * np.arcsin(np.sqrt(a))


def track_stats(sim: dict) -> dict:
    lon, lat = sim["lon"], sim["lat"]
    step = haversine_km(lon[:, :-1], lat[:, :-1], lon[:, 1:], lat[:, 1:])
    path_km = step.sum(1)
    disp_km = haversine_km(lon[:, 0], lat[:, 0], lon[:, -1], lat[:, -1])
    return {"n_particles": int(len(lon)), "stranded_pct": round(100 * float(sim["stranded"].mean()), 1),
            "mean_path_km": round(float(path_km.mean()), 2), "mean_displacement_km": round(float(disp_km.mean()), 2),
            "max_displacement_km": round(float(disp_km.max()), 2)}


def particles_json(sim: dict, ids=None, ndigits: int = 5) -> list[dict]:
    lon, lat = sim["lon"], sim["lat"]
    ids = range(len(lon)) if ids is None else ids
    out = []
    for i in ids:
        path = [[round(float(lon[i, t]), ndigits), round(float(lat[i, t]), ndigits), t] for t in range(lon.shape[1])]
        p = {"id": int(i), "path": path}
        if sim["stranded"][i]:
            p["stranded"] = True
            p["stranded_hour"] = int(sim["stranded_hour"][i])
        out.append(p)
    return out


def build_drift_json(region: str, date: str, start: dt.datetime, sim: dict, forcing: dict, note: str,
                     ensemble: list[dict] | None = None, extra: dict | None = None) -> dict:
    n_t = sim["lon"].shape[1]
    d = {"region": region, "date": date,
         "start_time": start.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "hours": list(range(n_t)), "particles": particles_json(sim), "forcing": forcing, "note": note}
    if ensemble:
        d["ensemble"] = ensemble
    d["stats"] = track_stats(sim)
    if extra:
        d.update(extra)
    return d


def dumps_compact(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, separators=(",", ":"))


def validate_drift(d: dict, max_bytes: int = 2_000_000) -> list[str]:
    """Contract check (docs/CONTRACTS.md drift.json). Returns a list of problems (empty = valid)."""
    errs = []
    for k in ("region", "date", "start_time", "hours", "particles", "forcing", "note"):
        if k not in d:
            errs.append(f"missing key {k}")
    if errs:
        return errs
    try:
        dt.datetime.fromisoformat(d["start_time"].replace("Z", "+00:00"))
    except Exception:
        errs.append("start_time not ISO")
    try:
        dt.date.fromisoformat(d["date"])
    except Exception:
        errs.append("date not YYYY-MM-DD")
    hours = d["hours"]
    if hours != list(range(len(hours))) or len(hours) != 73:
        errs.append("hours must be [0..72]")
    if not d["particles"]:
        errs.append("no particles")
    for f in ("currents", "wind", "wind_drift_factor", "model"):
        if f not in d["forcing"]:
            errs.append(f"forcing.{f} missing")

    def check_parts(parts, tag):
        for p in parts:
            if not isinstance(p.get("id"), int) or not isinstance(p.get("path"), list):
                errs.append(f"{tag}: bad particle"); return
            if len(p["path"]) != len(hours):
                errs.append(f"{tag}: particle {p['id']} path len {len(p['path'])}"); return
            for q in p["path"]:
                if len(q) != 3 or not (-180 <= q[0] <= 180 and -90 <= q[1] <= 90) or not all(map(math.isfinite, q)):
                    errs.append(f"{tag}: particle {p['id']} bad point {q}"); return
            if [q[2] for q in p["path"]] != hours:
                errs.append(f"{tag}: particle {p['id']} t != hours"); return
    check_parts(d["particles"], "particles")
    for m in d.get("ensemble", []) or []:
        if "wind_drift_factor" not in m:
            errs.append("ensemble member without wind_drift_factor")
        check_parts(m.get("particles", []), f"ensemble {m.get('wind_drift_factor')}")
    size = len(dumps_compact(d).encode("utf-8"))
    if size > max_bytes:
        errs.append(f"size {size} > {max_bytes}")
    return errs


# ---------------------------------------------------------------- preview
def preview_png(d: dict, out: Path, seeds_lonlat: np.ndarray | None = None, title: str | None = None) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7, 6.5), dpi=110)
    try:
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature
        plt.close(fig)
        fig = plt.figure(figsize=(7, 6.5), dpi=110)
        ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
        ax.add_feature(cfeature.GSHHSFeature(scale="h", facecolor="#e8e4da", edgecolor="#8a8577", linewidth=0.5))
        kw = {"transform": ccrs.PlateCarree()}
    except Exception:
        kw = {}
    for m, col in ((d.get("ensemble") or [], None),):
        for mem, c in zip(m, ("#63b3ed", "#f6ad55")):
            for p in mem["particles"]:
                a = np.array(p["path"])
                ax.plot(a[:, 0], a[:, 1], color=c, lw=0.5, alpha=0.35, **kw)
    for p in d["particles"]:
        a = np.array(p["path"])
        ax.plot(a[:, 0], a[:, 1], color="#c53030", lw=0.6, alpha=0.45, **kw)
    ends = np.array([p["path"][-1][:2] for p in d["particles"]])
    starts = np.array([p["path"][0][:2] for p in d["particles"]])
    strand = np.array([bool(p.get("stranded")) for p in d["particles"]])
    ax.scatter(starts[:, 0], starts[:, 1], s=6, c="#2d3748", zorder=5, label="старт (детекции MDD)", **kw)
    ax.scatter(ends[~strand, 0], ends[~strand, 1], s=6, c="#c53030", zorder=5, label="+72 ч, в воде", **kw)
    if strand.any():
        ax.scatter(ends[strand, 0], ends[strand, 1], s=10, marker="x", c="#553c9a", zorder=6,
                   label="выброшены на берег", **kw)
    allp = np.concatenate([np.array(p["path"])[:, :2] for p in d["particles"]] +
                          [np.array(p["path"])[:, :2] for m in (d.get("ensemble") or []) for p in m["particles"]])
    pad = 0.05
    x0, y0 = allp.min(0) - pad
    x1, y1 = allp.max(0) + pad
    try:
        ax.set_extent([x0, x1, y0, y1], crs=kw["transform"]) if kw else (ax.set_xlim(x0, x1), ax.set_ylim(y0, y1))
        if kw:
            gl = ax.gridlines(draw_labels=True, linewidth=0.3, color="#a0aec0")
            gl.top_labels = gl.right_labels = False
    except Exception:
        ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
    s = d.get("stats", {})
    ttl = title or (f"{d['region']} {d['date']}: дрейф 72 ч, wdf={d['forcing']['wind_drift_factor']}\n"
                    f"{s.get('n_particles')} частиц, на берегу {s.get('stranded_pct')}%, "
                    f"средн. смещение {s.get('mean_displacement_km')} км")
    ax.set_title(ttl, fontsize=9)
    if d.get("ensemble"):
        from matplotlib.lines import Line2D
        h, l = ax.get_legend_handles_labels()
        h += [Line2D([], [], color="#c53030"), Line2D([], [], color="#63b3ed"), Line2D([], [], color="#f6ad55")]
        l += ["треки wdf 0.02"] + [f"треки wdf {m['wind_drift_factor']}" for m in d["ensemble"][:2]]
        ax.legend(h, l, loc="lower left", fontsize=7)
    else:
        ax.legend(loc="lower left", fontsize=7)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


# ---------------------------------------------------------------- one scene
def run_scene(root: Path, region: str, date: str, n_particles: int = 300, wind_drift_factor: float = 0.02,
              ensemble_factors: tuple = (0.01, 0.03), ensemble_particles: int = 100, refresh: bool = False,
              png_dir: Path | None = None, horizontal_diffusivity: float = 5.0) -> dict:
    from .forcing import Window, get_forcing, parse_time
    scene_dir = root / "data" / "live" / region / date
    scene = json.loads((scene_dir / "scene.json").read_text(encoding="utf-8"))
    start = parse_time(scene["datetime"])
    cents, det_src = load_detection_centroids(root, region, date)
    if len(cents) == 0:
        return {"region": region, "date": date, "status": "skipped: 0 detections", "detections_source": det_src}
    seeds = seed_points(cents[:, :2], n_total=n_particles, weights=cents[:, 2])
    win = Window.around(scene["bounds_wgs84"], start)
    frc = get_forcing(region, date, win, root / "data_cache" / "forcing", refresh=refresh)
    cur_nc, cur_name = frc["currents"]
    wind_nc, wind_name = frc["wind"]
    sim = simulate(seeds, start, cur_nc, wind_nc, wind_drift_factor, horizontal_diffusivity=horizontal_diffusivity)
    ens, ens_stats = [], {}
    sub = np.linspace(0, len(seeds) - 1, min(ensemble_particles, len(seeds))).round().astype(int)
    for k, f in enumerate(ensemble_factors):
        s2 = simulate(seeds[sub], start, cur_nc, wind_nc, f, horizontal_diffusivity=horizontal_diffusivity, seed=k + 1)
        ens.append({"wind_drift_factor": f, "particles": particles_json(s2)})
        ens_stats[str(f)] = track_stats(s2)
    forcing = {"currents": cur_name, "wind": wind_name, "wind_drift_factor": wind_drift_factor, "model": MODEL,
               "horizontal_diffusivity_m2s": horizontal_diffusivity, "stokes_drift": "не учтён отдельно (без волн)",
               "time_step_min": 15, "output_step_h": 1,
               "seeding": (f"{len(seeds)} частиц вокруг {len(cents)} центроидов детекций MDD, доля ∝ площади пятна "
                           f"(≥5 на пятно), разброс σ=150 м, ≤300 м; источник: {det_src}"),
               "files": [str(cur_nc.relative_to(root)), str(wind_nc.relative_to(root))]}
    if ens:
        forcing["ensemble"] = [e["wind_drift_factor"] for e in ens]
    cur_short = "HYCOM ESPC-D-V02" if "HYCOM" in cur_name else cur_name
    note = (f"демонстрационный прогноз, модель течений {cur_short}, ветер {wind_name.split(',')[0]}, "
            f"ветровой коэффициент {wind_drift_factor}, без валидации")
    d = build_drift_json(region, date, start, sim, forcing, note, ensemble=ens,
                         extra={"ensemble_stats": ens_stats} if ens_stats else None)
    errs = validate_drift(d)
    if errs and any(e.startswith("size") for e in errs) and ens:  # thin ensemble, then main
        for m in d["ensemble"]:
            m["particles"] = m["particles"][::2]
        errs = validate_drift(d)
    if errs:
        raise RuntimeError(f"drift.json invalid: {errs[:5]}")
    out = scene_dir / "drift.json"
    txt = dumps_compact(d)
    out.write_text(txt, encoding="utf-8")
    png = None
    if png_dir is not None:
        png = preview_png(d, png_dir / f"drift_{region}_{date}.png")
    return {"region": region, "date": date, "status": "ok", "out": str(out), "bytes": len(txt.encode("utf-8")),
            "n_detections": int(len(cents)), "detections_source": det_src, **d["stats"],
            "currents": cur_name, "wind": wind_name, "forcing_log": frc["log"], "ensemble_stats": ens_stats,
            "png": str(png) if png else None}
