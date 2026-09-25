"""L86 — S3 (North Sea) pixel check of every optical scene within +-24 h of a transect (from inventory.csv).

  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/search/s3/s3_pairs.py [--only S3:HE419_MarLitter_transect23] [--force]

Per (event, scene) -> data/search/s3/pairs/<event>__<scene>/:
  panel.png (RGB | SWIR false colour | FDI | quality/detector), meta.json (written last; rerun skips done pairs).
Strip geometry, crop box, decision thresholds, S2 quality masks and the LightGBM detector (weights/lgbm, harmonize none as in
configs/case_pairs.yaml) are imported from scripts/case/pair_quality.py (not modified).
Landsat 8/7 C2 L2 (Planetary Computer): QA_PIXEL masks as pair_quality.process_landsat; detector NOT run (trained on S2 bands);
FDI-like index with red instead of the S2 red-edge B6 is shown for visual review only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data" / "search" / "s3"
PAIRS = OUT / "pairs"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "case"))
import pair_quality as pq  # noqa: E402
from macroplastic.live import stac  # noqa: E402
import rasterio  # noqa: E402
from rasterio.features import rasterize  # noqa: E402
from rasterio.windows import from_bounds  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from scipy import ndimage  # noqa: E402

CAP: dict = {}
_orig_read_crop = stac.read_crop


def _read_crop_capture(*a, **k):
    r = _orig_read_crop(*a, **k)
    CAP["crop"] = r
    return r


stac.read_crop = _read_crop_capture  # pair_quality uses the same module object -> we get the bands for FDI/SWIR views


def fdi_s2(b):
    # Biermann et al. 2020: FDI = B8 - (B6 + (B11 - B6) * (l8 - l4) / (l11 - l4) * 10)
    return b[7] - (b[5] + (b[10] - b[5]) * (842 - 665) / (1610 - 665) * 10)


def fdi_like_landsat(red, nir, sw1):
    # FDI-like: S2 red-edge B6 is absent on Landsat -> red as the baseline band (visual only, not the S2 index)
    return nir - (red + (sw1 - red) * (865 - 655) / (1609 - 655) * 10)


def stretch(x, lo=None, hi=None):
    v = x[np.isfinite(x)]
    if lo is None:
        lo, hi = (np.percentile(v, 2), np.percentile(v, 98)) if v.size else (0, 1)
    return np.clip((np.nan_to_num(x, nan=lo) - lo) / max(hi - lo, 1e-9), 0, 1)


def panel(path: Path, title: str, rgb, swir, fdi, qimg, strip, extra_title=""):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ol = pq._outline(strip)
    ims = []
    for im in (rgb, swir):
        im = (np.clip(im, 0, 1) * 255).astype(np.uint8).copy(); im[ol] = (255, 0, 255); ims.append(im)
    f = plt.cm.viridis(fdi)[..., :3]; f = (f * 255).astype(np.uint8); f[ol] = (255, 0, 255); ims.append(f)
    q = qimg.copy(); q[ol] = (255, 0, 255); ims.append(q)
    names = ["RGB", "SWIR false colour (SWIR1/NIR/red)", "FDI (p2-p98 water)", "quality / detector" + extra_title]
    fig, ax = plt.subplots(1, 4, figsize=(20, 5.6), dpi=70)
    for a, im, n in zip(ax, ims, names):
        a.imshow(im); a.set_title(n, fontsize=11); a.axis("off")
    fig.suptitle(title, fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=70)
    plt.close(fig)


def s2_pair(row, g, cfg, pred, od: Path) -> dict:
    r = pd.Series(dict(endpoint="planetary-computer", collection="sentinel-2-l2a", item_id=row.scene_id))
    res = pq.process_s2(r, g, cfg, pred, od)  # writes rgb.png quality.tif quality.png prob.tif mask.png
    crop = CAP.pop("crop")
    b, tr = crop["bands"], crop["transform"]
    H, W = b.shape[1:]
    item = pq.get_item("planetary-computer", "sentinel-2-l2a", row.scene_id)
    poly, _ = pq.strip_polygon(g, stac.item_epsg(item), cfg)
    strip = rasterize([(poly, 1)], out_shape=(H, W), transform=tr, all_touched=True, fill=0, dtype="uint8").astype(bool)
    with rasterio.open(od / "quality.tif") as s:
        qa = s.read(1)
    water = qa == pq.Q_WATER
    fdi = fdi_s2(b)
    fw = fdi[water]
    lo, hi = (np.percentile(fw, 2), np.percentile(fw, 98)) if fw.size > 100 else (None, None)
    swir = np.dstack([stretch(b[10], 0, 0.25), stretch(b[7], 0, 0.25), stretch(b[3], 0, 0.2)])
    rgb = np.clip(np.nan_to_num(b[[3, 2, 1]]).transpose(1, 2, 0) / 0.16, 0, 1) ** (1 / 1.8)
    qimg = np.zeros((H, W, 3), np.uint8)
    for k, c in pq.Q_COL.items():
        qimg[qa == k] = c
    with rasterio.open(od / "prob.tif") as s:
        prob = s.read(1) / 255.0
    det = (prob >= res["detector"]["threshold"]) & water
    qimg[ndimage.binary_dilation(det, iterations=2)] = (255, 40, 40)
    res["fdi_strip_water_p99"] = round(float(np.percentile(fdi[strip & water], 99)), 4) if (strip & water).sum() > 100 else None
    res["fdi_crop_water_p99"] = round(float(np.percentile(fw, 99)), 4) if fw.size > 100 else None
    res["_views"] = (rgb, swir, stretch(fdi, lo, hi), qimg, strip)
    return res


def landsat_pair(row, g, cfg, od: Path) -> dict:
    import planetary_computer
    item = planetary_computer.sign(pq.get_item("planetary-computer", "landsat-c2-l2", row.scene_id))
    names = ["blue", "green", "red", "nir08", "swir16", "qa_pixel"]
    with rasterio.open(item.assets["qa_pixel"].href) as src:
        epsg = src.crs.to_epsg(); crs = src.crs
        poly, rule = pq.strip_polygon(g, epsg, cfg)
        bx = pq.crop_box(poly, cfg, snap=30.0)
        H, W = int(round((bx[3] - bx[1]) / 30)), int(round((bx[2] - bx[0]) / 30))
    arr = {}
    for n in names:
        with rasterio.open(item.assets[n].href) as src:
            arr[n] = src.read(1, window=from_bounds(*bx, transform=src.transform), out_shape=(H, W), boundless=True,
                              fill_value=1 if n == "qa_pixel" else 0)
    qa_raw = arr.pop("qa_pixel")
    sr = {k: np.where(v > 0, v * 2.75e-5 - 0.2, np.nan).astype(np.float32) for k, v in arr.items()}
    tr = from_origin(bx[0], bx[3], 30, 30)
    strip = rasterize([(poly, 1)], out_shape=(H, W), transform=tr, all_touched=True, fill=0, dtype="uint8").astype(bool)
    bit = lambda k: (qa_raw >> k) & 1 == 1  # noqa: E731
    fill = bit(0) | ~np.isfinite(sr["red"])
    cloud = (bit(1) | bit(2) | bit(3) | bit(4)) & ~fill
    water = bit(7) & ~cloud & ~fill
    land = bit(6) & ~bit(7) & ~fill & ~cloud
    valid = ~fill
    qa = np.full((H, W), pq.Q_OTHER, np.uint8)
    qa[fill] = pq.Q_NODATA; qa[water] = pq.Q_WATER; qa[land] = pq.Q_LAND; qa[cloud] = pq.Q_CLOUD
    ns = int(strip.sum()); nsv = max(int((strip & valid).sum()), 1)
    q = dict(strip_px=ns, strip_area_km2=round(ns * 900 / 1e6, 4),
             coverage=round(float((strip & valid).sum() / max(ns, 1)), 4),
             valid_water_frac=round(float((strip & water).sum() / max(ns, 1)), 4),
             cloud_frac=round(float((strip & cloud).sum() / nsv), 4),
             land_frac=round(float((strip & land).sum() / nsv), 4),
             nodata_frac=round(float((strip & fill).sum() / max(ns, 1)), 4), glint_b11_median=None)
    rc = sr["red"][strip & cloud]
    q["red_sr_median_qa_cloud_strip"] = round(float(np.nanmedian(rc)), 4) if np.isfinite(rc).any() else None
    rw = sr["red"][strip & water]
    q["red_sr_median_water_strip"] = round(float(np.nanmedian(rw)), 4) if np.isfinite(rw).any() else None
    decision, reason = pq.decide(q, cfg)
    if reason == "cloud" and q["red_sr_median_qa_cloud_strip"] is not None and q["red_sr_median_qa_cloud_strip"] < 0.03:
        reason = "cloud(qa_suspect:red_sr<0.03)"
    fdi = fdi_like_landsat(sr["red"], sr["nir08"], sr["swir16"])
    fw = fdi[water & np.isfinite(fdi)]
    lo, hi = (np.percentile(fw, 2), np.percentile(fw, 98)) if fw.size > 100 else (None, None)
    # FDI-like outliers in usable strip water: > crop-water p99.9 and > 0 (visual review aid, not a detector)
    thr = float(np.percentile(fw, 99.9)) if fw.size > 1000 else np.inf
    out = (fdi > max(thr, 0.0)) & water
    lab, n = ndimage.label(out, structure=np.ones((3, 3), bool))
    in_strip = np.unique(lab[strip & out]); in_strip = in_strip[in_strip > 0]
    rgb = np.dstack([stretch(sr["red"], 0, 0.16), stretch(sr["green"], 0, 0.16), stretch(sr["blue"], 0, 0.16)]) ** (1 / 1.8)
    swir = np.dstack([stretch(sr["swir16"], 0, 0.25), stretch(sr["nir08"], 0, 0.25), stretch(sr["red"], 0, 0.2)])
    qimg = np.zeros((H, W, 3), np.uint8)
    for k, c in pq.Q_COL.items():
        qimg[qa == k] = c
    qimg[ndimage.binary_dilation(out, iterations=1)] = (255, 40, 40)
    od.mkdir(parents=True, exist_ok=True)
    pq._write_tif(od / "quality.tif", qa, tr, crs, "uint8", "quality code (Landsat QA_PIXEL): " + json.dumps(pq.Q_NAMES))
    p = item.properties
    return dict(scene_id=item.id, source="planetary-computer/landsat-c2-l2", level="L2SP", scene_datetime=p["datetime"],
                scene_cloud_cover=p.get("eo:cloud_cover"), platform=p.get("platform"), epsg=epsg, bounds_utm=bx,
                width=W, height=H, strip_rule=rule + "; 30 m grid", quality=q, decision=decision, reason=reason,
                detector=None, detector_note="LightGBM not run on Landsat (trained on 11 S2 bands); FDI-like outliers only",
                fdi_like=dict(thr_p999_crop_water=None if not np.isfinite(thr) else round(thr, 4), n_outlier_comp_crop=int(n),
                              n_outlier_comp_strip=int(len(in_strip)), outlier_px_strip=int((out & strip).sum()),
                              strip_water_p99=round(float(np.percentile(fdi[strip & water], 99)), 4) if (strip & water).sum() > 100 else None),
                slc_off=str(p.get("platform", "")).endswith("7"),
                _views=(rgb, swir, stretch(fdi, lo, hi), qimg, strip))


def targets() -> pd.DataFrame:
    inv = pd.read_csv(OUT / "inventory.csv")
    o = inv[(inv.role == "optical") & (inv.catalog == "planetary-computer") & (inv.line_in_footprint >= 0.3)
            & (inv.dt_h.abs() <= 24)].copy()
    o["adt"] = o.dt_h.abs()
    return o.sort_values("adt").drop_duplicates(["event_id", "scene_id"]).reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*"); ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    cfg = pq.load_cfg(ROOT / "configs" / "case_pairs.yaml")
    samples = pd.read_csv(ROOT / "task" / "macroplastic_marine_samples.csv", low_memory=False)
    from macroplastic.models.lgbm_predict import load_predictor
    pred = load_predictor(ROOT / cfg["detector"]["weights"], harmonize=pq.harmonize_mode(cfg))
    T = targets()
    print(len(T), "pairs to check", flush=True)
    for row in T.itertuples():
        if a.only and row.event_id not in a.only:
            continue
        od = PAIRS / f"{pq.safe(row.event_id)}__{row.scene_id}"
        if (od / "meta.json").exists() and not a.force:
            continue
        t0 = time.time()
        g = pq.event_geometry(samples, row.event_id, cfg)
        base = dict(event_id=row.event_id, scene_id=row.scene_id, collection=row.collection, dt_h=row.dt_h,
                    dt_mid_h=row.dt_mid_h, line_in_footprint=row.line_in_footprint, field_items_km2=g["field_items_km2"],
                    transect_width_m=g["width_m"], transect_length_km=g["length_km"],
                    geometry_status=g["geometry_status"], detector_cfg=dict(weights=cfg["detector"]["weights"],
                                                                            harmonize=cfg["detector"]["harmonize"]))
        try:
            od.mkdir(parents=True, exist_ok=True)
            res = landsat_pair(row, g, cfg, od) if row.collection.startswith("landsat") else s2_pair(row, g, cfg, pred, od)
            views = res.pop("_views")
            q = res["quality"]
            d = res.get("detector") or {}
            ttl = (f"{row.event_id}  |  {res['scene_id']}  |  dt {row.dt_h:+.1f} h  |  usable water in strip "
                   f"{q['valid_water_frac']:.2f}, cloud {q['cloud_frac']:.2f}  |  field {g['field_items_km2']} items/km2")
            extra = (f" (LightGBM: {d.get('n_det')} in strip)" if d else
                     f" (FDI-like outliers: {res['fdi_like']['n_outlier_comp_strip']} in strip)")
            panel(od / "panel.png", ttl, *views, extra_title=extra)
        except Exception as e:  # noqa: BLE001
            res = dict(decision="error", reason=f"read_error:{type(e).__name__}", error=traceback.format_exc()[-1500:])
            print(f"[error] {row.event_id} {row.scene_id}: {e}", flush=True)
        base.update(res)
        base["elapsed_s"] = round(time.time() - t0, 1)
        (od / "meta.json").write_text(json.dumps(base, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        q = base.get("quality") or {}
        print(f"{row.event_id} {row.scene_id} dt={row.dt_h}: {base['decision']} {base.get('reason')} "
              f"water={q.get('valid_water_frac')} cloud={q.get('cloud_frac')} {base['elapsed_s']} s", flush=True)


if __name__ == "__main__":
    main()
