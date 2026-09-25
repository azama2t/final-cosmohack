"""S4: same-day S2 L2A scenes -> quality mask + LightGBM detector (weights/lgbm, no harmonisation) + RGB / FDI / SWIR
false colour + detector mask, and the drift search zone from ERA5 wind (Open-Meteo archive API, not CDS).

Reuses scripts/case/pair_quality.py (imported, not modified): event_geometry / process_s2 (same masks, same thresholds).
Crop context is widened to the drift search radius (high scenario) so that the whole search zone is inside the crop.
Output per pair: data/search/s4/crops/<event>/<scene>/{rgb,quality,mask,fdi,swir}.png, prob.tif, quality.tif, meta.json
and data/search/s4/wind_era5.csv, data/search/s4/crops_summary.csv.
  CUDA_VISIBLE_DEVICES="" PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/search/s4_crops.py [--force]
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

os.environ["CUDA_VISIBLE_DEVICES"] = ""
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "case"))
import pair_quality as pq  # noqa: E402
from macroplastic.live import stac  # noqa: E402

S = ROOT / "data/search/s4"
OUT = S / "crops"
MAX_ABS_DT_H = 9.0  # same-day scenes (dt_min over the feasible window is what matters; kept all same-day S2)

_last = {}
_orig_read = stac.read_crop


def _read_and_keep(*a, **k):
    r = _orig_read(*a, **k)
    _last["crop"] = r
    return r


pq.stac.read_crop = _read_and_keep


def wind(lat, lon, date) -> pd.DataFrame:
    f = S / "wind_cache" / f"{lat:.3f}_{lon:.3f}_{date}.json"
    if not f.exists():
        d0 = (pd.Timestamp(date) - pd.Timedelta(days=1)).date()
        d1 = (pd.Timestamp(date) + pd.Timedelta(days=1)).date()
        url = (f"https://archive-api.open-meteo.com/v1/archive?latitude={lat:.4f}&longitude={lon:.4f}&start_date={d0}"
               f"&end_date={d1}&hourly=wind_speed_10m,wind_direction_10m&wind_speed_unit=ms&models=era5&timezone=GMT")
        for a in range(4):
            try:
                js = json.loads(urllib.request.urlopen(url, timeout=60).read())
                break
            except Exception:  # noqa: BLE001
                time.sleep(3 * (a + 1))
        else:
            return pd.DataFrame()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(js), encoding="utf-8")
    js = json.loads(f.read_text(encoding="utf-8"))
    h = js["hourly"]
    return pd.DataFrame(dict(time=pd.to_datetime(h["time"]), u10=h["wind_speed_10m"], dir=h["wind_direction_10m"]))


def drift_radius_km(w: pd.DataFrame, t0: pd.Timestamp, t1: pd.Timestamp, cur_ms: float, windage: float) -> float:
    """upper bound: |current| + windage * mean wind over [t0, t1], collinear (as in configs/case_pairs.yaml)."""
    a, b = min(t0, t1), max(t0, t1)
    m = w[(w.time >= a.floor("h")) & (w.time <= b.ceil("h"))]
    u = float(m.u10.mean()) if len(m) else float(w.u10.mean())
    return (cur_ms + windage * u) * abs((t1 - t0).total_seconds()) / 1000


def fdi(b):
    # Biermann et al. 2020: FDI = R8 - (R6 + (R11 - R6) * (l8 - l4) / (l11 - l4) * 10)
    return b[7] - (b[5] + (b[10] - b[5]) * (832.8 - 664.6) / (1613.7 - 664.6) * 10)


def save_views(outdir: Path, crop: dict, strip_outline: np.ndarray, det: np.ndarray | None):
    b = crop["bands"]
    f = fdi(b)
    fv = np.nan_to_num(np.clip((f + 0.02) / 0.06, 0, 1))
    img = (np.stack([fv, fv, fv], -1) * 255).astype(np.uint8)
    img[f > 0.01] = (255, 60, 60)  # FDI > 0.01 (floating-matter-like) in red, for orientation only
    img[strip_outline] = (255, 0, 255)
    pq._save_png(outdir / "fdi.png", img)
    sw = b[[11, 8, 3]]  # B12, B8A, B4
    v = (np.clip(np.nan_to_num(sw) / np.array([0.08, 0.12, 0.12])[:, None, None], 0, 1) ** (1 / 1.8) * 255).astype(np.uint8)
    v = v.transpose(1, 2, 0).copy()
    v[strip_outline] = (255, 0, 255)
    pq._save_png(outdir / "swir.png", v)
    return dict(fdi_p99_crop=float(np.nanpercentile(f, 99)), fdi_gt001_px=int(np.nansum(f > 0.01)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    cfg0 = pq.load_cfg(ROOT / "configs/case_pairs.yaml")
    tt = pd.read_csv(S / "transect_times.csv", parse_dates=["t_est", "t_lo", "t_hi"]).set_index("event_id")
    sc = pd.read_csv(S / "scenes.csv", parse_dates=["scene_datetime"])
    s2 = sc[(sc.endpoint == "earth-search") & (sc.collection == "sentinel-2-l2a")]
    s2 = s2[s2.scene_datetime.dt.normalize() == pd.to_datetime(s2.obs_date)]
    # one item per (event, tile, date): latest processing
    s2 = s2.sort_values("item_id").groupby(["event_id", "tile"], as_index=False).last()
    samples = pd.read_csv(ROOT / "task/macroplastic_marine_samples.csv", low_memory=False)
    from macroplastic.models.lgbm_predict import load_predictor
    pred = load_predictor(ROOT / cfg0["detector"]["weights"], harmonize=None)
    rows, wrows = [], []
    for r in s2.itertuples():
        if a.only and r.event_id not in a.only:
            continue
        t = tt.loc[r.event_id]
        w = wind(t.lat, t.lon, t.date)
        if len(w):
            ww = w.assign(event_id=r.event_id)
            wrows.append(ww)
        t_scene = r.scene_datetime
        dt_worst = max(abs((t_scene - t.t_lo).total_seconds()), abs((t_scene - t.t_hi).total_seconds())) / 3600
        rad = {k: drift_radius_km(w, t_scene, t.t_est, v["current_ms"], v["windage"]) for k, v in cfg0["drift"]["scenarios"].items()}
        rad_worst_high = drift_radius_km(w, t_scene, t_scene + pd.Timedelta(hours=dt_worst), 0.5, 0.03)
        cfg = copy.deepcopy(cfg0)
        cfg["strip"]["context_m"] = int(max(3000, min(10000, 1000 * rad_worst_high + 1000)))
        outdir = OUT / pq.safe(r.event_id) / r.item_id
        if (outdir / "meta.json").exists() and not a.force:
            rows.append(json.loads((outdir / "meta.json").read_text(encoding="utf-8")))
            continue
        g = pq.event_geometry(samples, r.event_id, cfg)
        row = SimpleNamespace(endpoint="earth-search", collection="sentinel-2-l2a", item_id=r.item_id)
        t0 = time.time()
        try:
            res = pq.process_s2(row, g, cfg, pred, outdir)
            crop = _last["crop"]
            from rasterio.features import rasterize
            from shapely.geometry import Point
            item = pq.get_item("earth-search", "sentinel-2-l2a", r.item_id)
            epsg = stac.item_epsg(item)
            poly, _ = pq.strip_polygon(g, epsg, cfg)
            H, W = crop["scl"].shape
            strip = rasterize([(poly, 1)], out_shape=(H, W), transform=crop["transform"], all_touched=True, fill=0,
                              dtype="uint8").astype(bool)
            views = save_views(outdir, crop, pq._outline(strip), None)
            # detections inside the drift search zone (typical / high radius around the point)
            import rasterio
            with rasterio.open(outdir / "prob.tif") as src:
                prob = src.read(1) / 255.0
            with rasterio.open(outdir / "quality.tif") as src:
                qa = src.read(1)
            zone = {}
            for k, rk in (("typical", rad["typical"]), ("high", rad_worst_high)):  # typical at dt_est; high at worst |dt|
                zp = Point(poly.centroid.x, poly.centroid.y).buffer(max(500.0, 1000 * rk))
                zm = rasterize([(zp, 1)], out_shape=(H, W), transform=crop["transform"], fill=0, dtype="uint8").astype(bool)
                dm = (prob >= float(pred.threshold)) & (qa == pq.Q_WATER) & zm
                zone[k] = dict(radius_km=round(max(0.5, rk), 2), zone_px=int(zm.sum()),
                               zone_water_frac=round(float((zm & (qa == pq.Q_WATER)).sum() / max(zm.sum(), 1)), 4),
                               det_px=int(dm.sum()))
            res.update(views=views, search_zone=zone)
        except Exception as e:  # noqa: BLE001
            import traceback
            res = dict(scene_id=r.item_id, decision="error", reason=f"{type(e).__name__}: {e}"[:300],
                       error=traceback.format_exc()[-1200:])
            print("[error]", r.event_id, r.item_id, e, flush=True)
        base = dict(event_id=r.event_id, transect=t.transect, field_items_km2=t.items_km2, obs_date=str(t.date),
                    t_est=str(t.t_est), feasible_intervals_utc=t.feasible_intervals_utc, t_est_method=t.t_est_method,
                    scene_datetime=str(t_scene), dt_est_h=r.dt_est_h, dt_min_abs_h=r.dt_min_abs_h, dt_max_abs_h=r.dt_max_abs_h,
                    drift_radius_km={k: round(v, 2) for k, v in rad.items()}, drift_radius_worst_high_km=round(rad_worst_high, 2),
                    wind_ms_mean_day=round(float(w.u10.mean()), 2) if len(w) else None, context_m=cfg["strip"]["context_m"])
        base.update(res)
        base["elapsed_s"] = round(time.time() - t0, 1)
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "meta.json").write_text(json.dumps(base, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        rows.append(base)
        q, d = base.get("quality") or {}, base.get("detector") or {}
        print(f"{r.event_id} {r.item_id}: {base.get('decision')} {base.get('reason')} water={q.get('valid_water_frac')} "
              f"glint={q.get('glint_frac')} n_det={d.get('n_det')} n_det_crop={d.get('n_det_crop')} dt_est={r.dt_est_h} "
              f"zone={base.get('search_zone')} {base['elapsed_s']} s", flush=True)
    if wrows:
        pd.concat(wrows).drop_duplicates(["event_id", "time"]).to_csv(S / "wind_era5.csv", index=False)
    flat = []
    for m in rows:
        q, d, z = m.get("quality") or {}, m.get("detector") or {}, m.get("search_zone") or {}
        flat.append(dict(event_id=m["event_id"], scene_id=m.get("scene_id"), tile=m.get("tile"), scene_datetime=m["scene_datetime"],
                         t_est=m["t_est"], feasible_intervals_utc=m["feasible_intervals_utc"], dt_est_h=m["dt_est_h"],
                         dt_min_abs_h=m["dt_min_abs_h"], dt_max_abs_h=m["dt_max_abs_h"], field_items_km2=m["field_items_km2"],
                         wind_ms=m.get("wind_ms_mean_day"), drift_typ_km=(m.get("drift_radius_km") or {}).get("typical"),
                         drift_worst_high_km=m.get("drift_radius_worst_high_km"), decision=m.get("decision"), reason=m.get("reason"),
                         valid_water_frac=q.get("valid_water_frac"), glint_frac=q.get("glint_frac"), cloud_frac=q.get("cloud_frac"),
                         glint_b11_median=q.get("glint_b11_median"), n_det_strip=d.get("n_det"), n_det_crop=d.get("n_det_crop"),
                         det_frac_water_crop=d.get("det_frac_water_crop"), prob_max_strip=d.get("prob_max"),
                         zone_typ_det_px=(z.get("typical") or {}).get("det_px"), zone_high_det_px=(z.get("high") or {}).get("det_px"),
                         zone_high_water_frac=(z.get("high") or {}).get("zone_water_frac"),
                         fdi_gt001_px=(m.get("views") or {}).get("fdi_gt001_px")))
    pd.DataFrame(flat).to_csv(S / "crops_summary.csv", index=False)
    print(pd.DataFrame(flat).to_string())


if __name__ == "__main__":
    main()
