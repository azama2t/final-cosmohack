"""L45: score the drift forecast against the next snapshot (criterion fixed in reports/tasklog/45_drift_check.md).

  .venv/Scripts/python.exe scripts/experiments/l45_eval.py
  -> reports/drift_check.json, reports/drift_check/pairs/<region>_<date1>_<date2>.json, reports/figures/drift_check_*.png

Pairs: out/l45/fetched.json (status ok, not glint_or_haze, prob_mdd.tif present). Cloud at t2: drift.json main run
(hour round(dt_h)) when dt_h <= 72.5, else out/l45/ext/<region>_<date1>.npz (same seeds/settings, extended).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from macroplastic.drift.run import centroids_from_prob  # noqa: E402

OUT_JSON = ROOT / "reports" / "drift_check.json"
PAIR_DIR = ROOT / "reports" / "drift_check" / "pairs"
FIG_DIR = ROOT / "reports" / "figures"
EXT = ROOT / "out" / "l45" / "ext"
H_MIN_M = 500.0
LEVEL_PCT = 90
GRID_M = 100.0
STATIC_KM = 0.1  # post-hoc: a 2nd-snapshot detection within 100 m of a start particle = "stayed in place"
CRITERION = (
    "Находка второго снимка (компонента MDD P≥0.5 на воде, центроид) попадает, если лежит внутри 90%-го контура облака "
    "частиц на момент второго снимка (область наибольшей плотности гауссова KDE, окно = Скотт, но ≥ 500 м; уровень — "
    "10-й перцентиль плотности в частицах). Пара «попала», если попала ≥ 1 находка; hit_rate = K/N по парам. "
    "Базовая линия «нулевой дрейф» — то же для стартовых позиций частиц (час 0). Доп. линия «сдвиг назад» — облако "
    "прогноза минус средний вектор смещения (та же площадь). Критерий записан до подсчёта (tasklog/45).")


# ---------------------------------------------------------------- geometry helpers
class Local:
    """Equirectangular metres around (lon0, lat0) -- fine for < 150 km."""

    def __init__(self, lon0, lat0):
        self.lon0, self.lat0 = lon0, lat0
        self.kx = 111320.0 * math.cos(math.radians(lat0))
        self.ky = 110574.0

    def xy(self, lon, lat):
        return (np.asarray(lon) - self.lon0) * self.kx, (np.asarray(lat) - self.lat0) * self.ky

    def ll(self, x, y):
        return np.asarray(x) / self.kx + self.lon0, np.asarray(y) / self.ky + self.lat0


class KDERegion:
    """HDR region of an isotropic Gaussian KDE containing LEVEL_PCT % of the particles."""

    def __init__(self, x, y, h_min=H_MIN_M, pct=LEVEL_PCT, h_fixed=None):
        self.x, self.y = np.asarray(x, float), np.asarray(y, float)
        n = len(self.x)
        sd = math.sqrt(0.5 * (self.x.var() + self.y.var())) if n > 1 else 0.0
        self.h = float(h_fixed) if h_fixed else max(h_min, sd * n ** (-1 / 6))
        self.level = float(np.percentile(self.density(self.x, self.y), 100 - pct))

    def density(self, qx, qy, chunk=20000):
        qx, qy = np.atleast_1d(np.asarray(qx, float)).ravel(), np.atleast_1d(np.asarray(qy, float)).ravel()
        out = np.empty(len(qx))
        inv = 1.0 / (2 * self.h * self.h)
        for i in range(0, len(qx), chunk):
            dx = qx[i:i + chunk, None] - self.x[None]
            dy = qy[i:i + chunk, None] - self.y[None]
            out[i:i + chunk] = np.exp(-(dx * dx + dy * dy) * inv).sum(1)
        return out

    def inside(self, qx, qy):
        if len(np.atleast_1d(qx)) == 0:
            return np.zeros(0, bool)
        return self.density(qx, qy) >= self.level

    def grid(self, step=GRID_M, max_cells=1_200_000):
        pad = 4 * self.h
        x0, x1 = self.x.min() - pad, self.x.max() + pad
        y0, y1 = self.y.min() - pad, self.y.max() + pad
        step = max(step, math.sqrt((x1 - x0) * (y1 - y0) / max_cells))
        gx = np.arange(x0, x1 + step, step)
        gy = np.arange(y0, y1 + step, step)
        X, Y = np.meshgrid(gx, gy)
        Z = self.density(X.ravel(), Y.ravel()).reshape(X.shape)
        return gx, gy, Z, step


def contour_geojson(gx, gy, Z, level, loc: Local, simplify_m=30.0):
    """Filled region Z >= level as GeoJSON MultiPolygon (lon/lat, 5 decimals)."""
    import contourpy
    from shapely.geometry import MultiPolygon, Polygon, mapping
    from shapely.ops import unary_union
    cg = contourpy.contour_generator(gx, gy, Z, fill_type=contourpy.FillType.OuterOffset)
    pts_list, offs_list = cg.filled(level, float(Z.max()) + 1.0)
    polys = []
    for pts, offs in zip(pts_list, offs_list):
        rings = [pts[offs[i]:offs[i + 1]] for i in range(len(offs) - 1)]
        rings = [r for r in rings if len(r) >= 4]
        if rings:
            polys.append(Polygon(rings[0], rings[1:]).buffer(0))
    if not polys:
        return None, 0.0
    geom = unary_union(polys).simplify(simplify_m)
    area_km2 = geom.area / 1e6
    parts = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    out = []
    for p in parts:
        if p.is_empty or p.geom_type != "Polygon":
            continue
        ext = np.array(p.exterior.coords)
        lon, lat = loc.ll(ext[:, 0], ext[:, 1])
        rings = [np.column_stack([lon, lat]).round(5).tolist()]
        for hole in p.interiors:
            h = np.array(hole.coords)
            lo, la = loc.ll(h[:, 0], h[:, 1])
            rings.append(np.column_stack([lo, la]).round(5).tolist())
        out.append(rings)
    return {"type": "MultiPolygon", "coordinates": out}, round(area_km2, 3)


def water_frac_of_region(kde: KDERegion, gx, gy, Z, step, loc: Local, scene2: Path):
    """(contour area on observed water of scene 2 [km2], observed water area [km2])."""
    import rasterio
    from pyproj import Transformer
    with rasterio.open(scene2 / "water_mask.tif") as src:
        wm = src.read(1) > 0
        tr, crs = src.transform, src.crs
    water_km2 = float(wm.sum()) * abs(tr.a * tr.e) / 1e6
    X, Y = np.meshgrid(gx, gy)
    m = Z >= kde.level
    if not m.any():
        return 0.0, water_km2
    lon, lat = loc.ll(X[m], Y[m])
    ux, uy = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(lon, lat)
    col = np.floor((ux - tr.c) / tr.a).astype(int)
    row = np.floor((uy - tr.f) / tr.e).astype(int)
    ok = (col >= 0) & (col < wm.shape[1]) & (row >= 0) & (row < wm.shape[0])
    on = np.zeros(len(col), bool)
    on[ok] = wm[row[ok], col[ok]]
    return float(on.sum()) * step * step / 1e6, water_km2


# ---------------------------------------------------------------- pair evaluation
def cloud_at(region, date1, h):
    d = json.loads((ROOT / "data" / "live" / region / date1 / "drift.json").read_text(encoding="utf-8"))
    p0 = np.array([p["path"][0][:2] for p in d["particles"]])
    if h <= 72:
        pt = np.array([p["path"][h][:2] for p in d["particles"]])
        strand = np.array([bool(p.get("stranded")) and p.get("stranded_hour", 999) <= h for p in d["particles"]])
        return p0, pt, strand, "drift.json (основной прогон wdf 0.02)", d
    z = np.load(EXT / f"{region}_{date1}.npz")
    pt = np.column_stack([z["lon"][:, h], z["lat"][:, h]])
    meta = json.loads((EXT / f"{region}_{date1}.json").read_text(encoding="utf-8"))
    return p0, pt, z["stranded"].astype(bool), (f"продлённый прогон до {meta['hours']} ч (те же семена и настройки; "
                                                f"{meta['currents'].split(',')[0]} + {meta['wind'].split(',')[0]})"), d


def eval_pair(f: dict) -> dict:
    region, date1, date2 = f["region"], f["date1"], f["date2"]
    s1 = json.loads((ROOT / "data" / "live" / region / date1 / "scene.json").read_text(encoding="utf-8"))
    s2dir = ROOT / "data" / "drift_check" / region / date2
    s2 = json.loads((s2dir / "scene.json").read_text(encoding="utf-8"))
    t1 = dt.datetime.fromisoformat(s1["datetime"].replace("Z", "+00:00"))
    t2 = dt.datetime.fromisoformat(s2["datetime"].replace("Z", "+00:00"))
    dt_h = (t2 - t1).total_seconds() / 3600
    h = int(round(dt_h))
    p0, pt, strand, src, d = cloud_at(region, date1, h)
    loc = Local(float(np.mean(np.r_[p0[:, 0], pt[:, 0]])), float(np.mean(np.r_[p0[:, 1], pt[:, 1]])))
    x0, y0 = loc.xy(p0[:, 0], p0[:, 1])
    xt, yt = loc.xy(pt[:, 0], pt[:, 1])
    mx, my = float((xt - x0).mean()), float((yt - y0).mean())
    kf = KDERegion(xt, yt)
    kb = KDERegion(x0, y0)
    ks = KDERegion(xt - mx, yt - my)
    dets = {}
    for model in ("mdd", "lgbm"):
        c, _ = centroids_from_prob(s2dir, model)
        dets[model] = c
    c = dets["mdd"]
    dx, dy = loc.xy(c[:, 0], c[:, 1]) if len(c) else (np.zeros(0), np.zeros(0))
    hit_f, hit_b, hit_s = kf.inside(dx, dy), kb.inside(dx, dy), ks.inside(dx, dy)

    def nearest_km(px, py):
        if len(dx) == 0:
            return np.zeros(0)
        return np.sqrt(((dx[:, None] - px[None]) ** 2 + (dy[:, None] - py[None]) ** 2).min(1)) / 1000

    dist_f, dist_b = nearest_km(xt, yt), nearest_km(x0, y0)
    # post-hoc sensitivity: fixed 1 km bandwidth for both clouds (Scott's rule over-smooths multi-patch clouds)
    kf1, kb1 = KDERegion(xt, yt, h_fixed=1000.0), KDERegion(x0, y0, h_fixed=1000.0)
    hit_f1, hit_b1 = kf1.inside(dx, dy), kb1.inside(dx, dy)
    g1f, g1b = kf1.grid(), kb1.grid()
    w1f = water_frac_of_region(kf1, *g1f, loc, s2dir)
    w1b = water_frac_of_region(kb1, *g1b, loc, s2dir)
    cl = dets["lgbm"]
    lx, ly = loc.xy(cl[:, 0], cl[:, 1]) if len(cl) else (np.zeros(0), np.zeros(0))
    lg_f, lg_b = kf.inside(lx, ly), kb.inside(lx, ly)
    geo, area = {}, {}
    wat = {}
    for tag, k in (("forecast", kf), ("baseline", kb), ("shift", ks)):
        gx, gy, Z, step = k.grid()
        geo[tag], area[tag] = contour_geojson(gx, gy, Z, k.level, loc)  # shift: already at the t0 place
        wat[tag] = water_frac_of_region(k, gx, gy, Z, step, loc, s2dir)
    n2 = int(len(c))
    water_km2 = wat["forecast"][1]
    exp_f = n2 * wat["forecast"][0] / water_km2 if water_km2 > 0 else 0.0
    exp_b = n2 * wat["baseline"][0] / water_km2 if water_km2 > 0 else 0.0
    disp_km = float(np.hypot(xt - x0, yt - y0).mean() / 1000)
    stem = f"{region}_{date1}_{date2}"
    detail = {
        "region": region, "date1": date1, "date2": date2, "dt_h": round(dt_h, 2), "hour_used": h,
        "start_time": d["start_time"], "time2": s2["datetime"], "cloud_source": src,
        "scene2": {"scene_id": s2["scene_id"], "crop_cloud_frac": s2["crop_cloud_frac"], "valid_frac": s2["valid_frac"],
                   "water_b11_median": s2["water_b11_median"], "glint_or_haze": s2["glint_or_haze"],
                   "bounds_wgs84": s2["bounds_wgs84"], "dir": str(s2dir.relative_to(ROOT)).replace("\\", "/")},
        "particles_t2": pt.round(5).tolist(), "particles_t0": p0.round(5).tolist(),
        "stranded_t2": [int(i) for i in np.where(strand)[0]],
        "contour_pct": LEVEL_PCT,
        "contour": geo["forecast"], "baseline_contour": geo["baseline"], "shift_contour": geo["shift"],
        "bandwidth_m": {"forecast": round(kf.h), "baseline": round(kb.h), "shift": round(ks.h)},
        "detections2": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(float(c[i, 0]), 5),
                                                                              round(float(c[i, 1]), 5)]},
             "properties": {"model": "mdd", "area_m2": round(float(c[i, 2])), "hit": bool(hit_f[i]),
                            "hit_baseline": bool(hit_b[i]), "hit_shift": bool(hit_s[i]),
                            "dist_forecast_km": round(float(dist_f[i]), 2),
                            "dist_baseline_km": round(float(dist_b[i]), 2)}} for i in range(n2)]},
    }
    PAIR_DIR.mkdir(parents=True, exist_ok=True)
    (PAIR_DIR / f"{stem}.json").write_text(json.dumps(detail, ensure_ascii=False, separators=(",", ":")),
                                           encoding="utf-8")
    fig = FIG_DIR / f"drift_check_{stem}.png"
    try:
        figure(detail, s2dir, fig)
    except Exception as e:  # a figure must not kill the scoring
        print("figure failed", stem, repr(e))
    return {
        "region": region, "date1": date1, "date2": date2, "dt_h": round(dt_h, 2), "hour_used": h,
        "n_det2": n2, "hits": int(hit_f.sum()), "hits_baseline": int(hit_b.sum()), "hits_shift": int(hit_s.sum()),
        "pair_hit": bool(hit_f.any()), "pair_hit_baseline": bool(hit_b.any()), "pair_hit_shift": bool(hit_s.any()),
        "contour_pct": LEVEL_PCT, "contour_area_km2": area["forecast"], "baseline_area_km2": area["baseline"],
        "contour_water_km2": round(wat["forecast"][0], 3), "baseline_water_km2": round(wat["baseline"][0], 3),
        "scene2_water_km2": round(water_km2, 2),
        "expected_random_hits": round(exp_f, 3), "expected_random_hits_baseline": round(exp_b, 3),
        "median_dist_forecast_km": round(float(np.median(dist_f)), 2) if n2 else None,
        "median_dist_baseline_km": round(float(np.median(dist_b)), 2) if n2 else None,
        "mean_displacement_km": round(disp_km, 2), "stranded_pct_t2": round(100 * float(strand.mean()), 1),
        # post-hoc diagnostics (not part of the pre-registered criterion)
        "n_det2_static": int((dist_b <= STATIC_KM).sum()),
        "hits_moving": int((hit_f & (dist_b > STATIC_KM)).sum()),
        "hits_baseline_moving": int((hit_b & (dist_b > STATIC_KM)).sum()),
        "verifiable": bool(wat["forecast"][0] > 0),
        "hits_h1km": int(hit_f1.sum()), "hits_baseline_h1km": int(hit_b1.sum()),
        "area_km2_h1km": round(float((g1f[2] >= kf1.level).sum()) * g1f[3] ** 2 / 1e6, 3),
        "baseline_area_km2_h1km": round(float((g1b[2] >= kb1.level).sum()) * g1b[3] ** 2 / 1e6, 3),
        "expected_random_hits_h1km": round(n2 * w1f[0] / w1f[1], 3) if w1f[1] else 0.0,
        "expected_random_hits_baseline_h1km": round(n2 * w1b[0] / w1b[1], 3) if w1b[1] else 0.0,
        "n_det2_lgbm": int(len(cl)), "hits_lgbm": int(lg_f.sum()), "hits_lgbm_baseline": int(lg_b.sum()),
        "scene2_id": s2["scene_id"], "scene2_crop_cloud": s2["crop_cloud_frac"], "scene2_valid": s2["valid_frac"],
        "scene2_b11": s2["water_b11_median"], "cloud_source": src,
        "detail": f"reports/drift_check/pairs/{stem}.json", "figure": f"reports/figures/drift_check_{stem}.png",
        "scene2_dir": str(s2dir.relative_to(ROOT)).replace("\\", "/"),
    }


# ---------------------------------------------------------------- figure
def figure(d: dict, s2dir: Path, out: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MplPoly
    rgbj = json.loads((s2dir / "rgb.json").read_text(encoding="utf-8"))
    img = plt.imread(s2dir / "rgb.png")
    b = rgbj["bounds"]
    fig, ax = plt.subplots(figsize=(8, 7.2), dpi=110)
    ax.set_facecolor("#0b1622")
    ax.imshow(img, extent=[b[0], b[2], b[1], b[3]], origin="upper", zorder=0)

    def draw(geom, color, ls, label):
        if not geom:
            return
        first = True
        for poly in geom["coordinates"]:
            ax.add_patch(MplPoly(np.array(poly[0]), closed=True, fill=False, ec=color, lw=1.6, ls=ls, zorder=4,
                                 label=label if first else None))
            first = False

    p0, pt = np.array(d["particles_t0"]), np.array(d["particles_t2"])
    bb = d["scene2"]["bounds_wgs84"]
    ax.plot([bb[0], bb[2], bb[2], bb[0], bb[0]], [bb[1], bb[1], bb[3], bb[3], bb[1]], color="#a0aec0", lw=0.8,
            ls=":", zorder=2, label="вырезка 25 км (снимки 1 и 2)")
    ax.scatter(p0[:, 0], p0[:, 1], s=6, c="#f7fafc", ec="#1a202c", lw=0.3, zorder=3, label="частицы, старт (снимок 1)")
    ax.scatter(pt[:, 0], pt[:, 1], s=4, c="#ff6b4a", zorder=3, label=f"частицы, +{d['hour_used']} ч (прогноз)")
    draw(d["contour"], "#ff6b4a", "-", "90% контур прогноза")
    draw(d["baseline_contour"], "#e2e8f0", "--", "90% контур «нулевой дрейф»")
    feats = d["detections2"]["features"]
    if feats:
        xy = np.array([f["geometry"]["coordinates"] for f in feats])
        hit = np.array([f["properties"]["hit"] for f in feats])
        hb = np.array([f["properties"]["hit_baseline"] for f in feats])
        ax.scatter(xy[~hit & ~hb, 0], xy[~hit & ~hb, 1], s=26, marker="o", facecolors="none", edgecolors="#63b3ed",
                   lw=1.2, zorder=5, label="находка снимка 2 — вне обоих контуров")
        if (hit & ~hb).any():
            ax.scatter(xy[hit & ~hb, 0], xy[hit & ~hb, 1], s=36, marker="o", c="#48bb78", ec="k", zorder=6,
                       label="находка 2 — только в прогнозе")
        if (hb & ~hit).any():
            ax.scatter(xy[hb & ~hit, 0], xy[hb & ~hit, 1], s=36, marker="s", c="#ecc94b", ec="k", zorder=6,
                       label="находка 2 — только в «нулевом дрейфе»")
        if (hb & hit).any():
            ax.scatter(xy[hb & hit, 0], xy[hb & hit, 1], s=36, marker="D", c="#b794f4", ec="k", zorder=6,
                       label="находка 2 — в обоих")
    allx = np.r_[p0[:, 0], pt[:, 0], b[0], b[2]]
    ally = np.r_[p0[:, 1], pt[:, 1], b[1], b[3]]
    padx, pady = 0.03 * (allx.max() - allx.min()) + 0.005, 0.03 * (ally.max() - ally.min()) + 0.005
    ax.set_xlim(allx.min() - padx, allx.max() + padx)
    ax.set_ylim(ally.min() - pady, ally.max() + pady)
    ax.set_aspect(1 / math.cos(math.radians(float(np.mean(ally)))))
    nh = sum(f["properties"]["hit"] for f in feats)
    nb = sum(f["properties"]["hit_baseline"] for f in feats)
    ax.set_title(f"{d['region']}: снимок {d['date1']} → {d['date2']} (Δt = {d['dt_h']:.0f} ч)\n"
                 f"находок MDD на снимке 2: {len(feats)}; в контуре прогноза {nh}, в «нулевом дрейфе» {nb}",
                 fontsize=10)
    ax.legend(loc="lower left", fontsize=7, framealpha=0.85)
    ax.tick_params(labelsize=7)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)


def main():
    fetched = json.loads((ROOT / "out" / "l45" / "fetched.json").read_text(encoding="utf-8"))
    pairs, skipped = [], []
    for f in sorted(fetched, key=lambda r: (r["region"], r["date1"], r["date2"])):
        why = None
        s2dir = ROOT / "data" / "drift_check" / f["region"] / f["date2"]
        if f.get("status") not in ("ok", "exists"):
            why = f.get("status")
        elif not (s2dir / "scene.json").exists():
            why = "no scene"
        else:
            s2 = json.loads((s2dir / "scene.json").read_text(encoding="utf-8"))
            if s2.get("glint_or_haze"):
                why = f"glint_or_haze (B11 water {s2.get('water_b11_median')})"
            elif s2.get("crop_cloud_frac", 1) >= 0.4:
                why = f"crop cloud {s2.get('crop_cloud_frac')}"
            elif not (s2dir / "prob_mdd.tif").exists() or not (s2dir / "prob_lgbm.tif").exists():
                why = "no prob_mdd/prob_lgbm"
        if why is None:
            s1 = json.loads((ROOT / "data" / "live" / f["region"] / f["date1"] / "scene.json").read_text(encoding="utf-8"))
            dt_h = (dt.datetime.fromisoformat(s2["datetime"].replace("Z", "+00:00")) -
                    dt.datetime.fromisoformat(s1["datetime"].replace("Z", "+00:00"))).total_seconds() / 3600
            if round(dt_h) > 72 and not (EXT / f"{f['region']}_{f['date1']}.npz").exists():
                why = "dt > 72 h and no extended run"
        if why:
            skipped.append({"region": f["region"], "date1": f["date1"], "date2": f["date2"], "reason": why})
            print("skip", f["region"], f["date1"], f["date2"], why, flush=True)
            continue
        r = eval_pair(f)
        print(json.dumps({k: r[k] for k in ("region", "date1", "date2", "dt_h", "n_det2", "hits", "hits_baseline",
                                            "hits_shift", "contour_area_km2", "baseline_area_km2",
                                            "expected_random_hits", "median_dist_forecast_km",
                                            "median_dist_baseline_km", "n_det2_lgbm", "hits_lgbm",
                                            "hits_lgbm_baseline")}), flush=True)
        pairs.append(r)
    n = len(pairs)
    k = sum(p["pair_hit"] for p in pairs)
    kb = sum(p["pair_hit_baseline"] for p in pairs)
    ks = sum(p["pair_hit_shift"] for p in pairs)
    nd = sum(p["n_det2"] for p in pairs)
    summ = {
        "criterion": CRITERION, "contour_pct": LEVEL_PCT, "bandwidth_min_m": H_MIN_M,
        "n_pairs": n, "n_pairs_with_det2": sum(p["n_det2"] > 0 for p in pairs),
        "k_hit": k, "k_hit_baseline": kb, "k_hit_shift": ks,
        "hit_rate": round(k / n, 3) if n else None, "baseline_hit_rate": round(kb / n, 3) if n else None,
        "shift_hit_rate": round(ks / n, 3) if n else None,
        "n_det2_total": nd, "det_hits": sum(p["hits"] for p in pairs),
        "det_hits_baseline": sum(p["hits_baseline"] for p in pairs), "det_hits_shift": sum(p["hits_shift"] for p in pairs),
        "det_hit_rate": round(sum(p["hits"] for p in pairs) / nd, 4) if nd else None,
        "det_baseline_hit_rate": round(sum(p["hits_baseline"] for p in pairs) / nd, 4) if nd else None,
        "expected_random_hits": round(sum(p["expected_random_hits"] for p in pairs), 2),
        "expected_random_hits_baseline": round(sum(p["expected_random_hits_baseline"] for p in pairs), 2),
        "pairs_forecast_better": sum(p["hits"] > p["hits_baseline"] for p in pairs),
        "pairs_baseline_better": sum(p["hits"] < p["hits_baseline"] for p in pairs),
        "posthoc": {
            "note": "после подсчёта, не входит в заранее зафиксированный критерий",
            "n_det2_static_le_100m": sum(p["n_det2_static"] for p in pairs),
            "det_hits_moving": sum(p["hits_moving"] for p in pairs),
            "det_hits_baseline_moving": sum(p["hits_baseline_moving"] for p in pairs),
            "n_pairs_verifiable": sum(p["verifiable"] for p in pairs),
            "h1km": {"k_hit": sum(p["hits_h1km"] > 0 for p in pairs),
                     "k_hit_baseline": sum(p["hits_baseline_h1km"] > 0 for p in pairs),
                     "det_hits": sum(p["hits_h1km"] for p in pairs),
                     "det_hits_baseline": sum(p["hits_baseline_h1km"] for p in pairs),
                     "expected_random_hits": round(sum(p["expected_random_hits_h1km"] for p in pairs), 2),
                     "expected_random_hits_baseline": round(sum(p["expected_random_hits_baseline_h1km"] for p in pairs), 2),
                     "area_km2_mean": round(sum(p["area_km2_h1km"] for p in pairs) / max(n, 1), 1),
                     "baseline_area_km2_mean": round(sum(p["baseline_area_km2_h1km"] for p in pairs) / max(n, 1), 1)},
            "k_hit_verifiable": sum(p["pair_hit"] for p in pairs if p["verifiable"]),
            "k_hit_baseline_verifiable": sum(p["pair_hit_baseline"] for p in pairs if p["verifiable"]),
            "by_horizon": {lab: {"n": len(g), "k_hit": sum(p["pair_hit"] for p in g),
                                 "k_hit_baseline": sum(p["pair_hit_baseline"] for p in g),
                                 "det_hits": sum(p["hits"] for p in g), "det_hits_baseline": sum(p["hits_baseline"] for p in g),
                                 "n_det2": sum(p["n_det2"] for p in g)}
                           for lab, g in (("le_72h", [p for p in pairs if p["hour_used"] <= 72]),
                                          ("gt_72h", [p for p in pairs if p["hour_used"] > 72]))},
        },
        "lgbm_det_hits": sum(p["hits_lgbm"] for p in pairs),
        "lgbm_det_hits_baseline": sum(p["hits_lgbm_baseline"] for p in pairs),
        "lgbm_n_det2_total": sum(p["n_det2_lgbm"] for p in pairs),
        "verdict": None, "pairs": pairs, "skipped": skipped,
        "generated": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "script": "scripts/experiments/l45_eval.py",
        "note": "демонстрационная проверка на малой выборке; находки снимка 2 — другие/новые объекты или ложные "
                "срабатывания, идентичность объектов не устанавливается",
    }
    ef, eb = summ["expected_random_hits"], summ["expected_random_hits_baseline"]
    summ["lift_vs_random"] = round(summ["det_hits"] / ef, 2) if ef else None
    summ["lift_vs_random_baseline"] = round(summ["det_hits_baseline"] / eb, 2) if eb else None
    if n:
        formal = ("формально прогноз лучше «нулевого дрейфа»" if k > kb else
                  "прогноз НЕ лучше «нулевого дрейфа»" if k == kb else "прогноз ХУЖЕ «нулевого дрейфа»")
        extra = [f"K/N {k}/{n} против {kb}/{n}"]
        if ks >= k:
            extra.append(f"но облако той же площади без адвекции («сдвиг назад») попадает не реже: {ks}/{n} — "
                         f"вклад направления дрейфа не подтверждён")
        if summ["lift_vs_random"] and summ["lift_vs_random_baseline"]:
            extra.append(f"попаданий относительно случайного при той же площади: прогноз ×{summ['lift_vs_random']}, "
                         f"нулевой дрейф ×{summ['lift_vs_random_baseline']}")
        if n < 30:
            extra.append(f"N = {n}, разница в {abs(k - kb)} пар(у) — статистически не значима")
        summ["verdict"] = formal + "; " + "; ".join(extra)
    OUT_JSON.write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k2: v for k2, v in summ.items() if k2 not in ("pairs", "skipped", "criterion")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
