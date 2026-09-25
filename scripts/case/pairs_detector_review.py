"""Detector review on the real S2 L2A crops of the case pairs (criteria O3 / T2).

  set CUDA_VISIBLE_DEVICES= & .venv\\Scripts\\python.exe scripts\\case\\pairs_detector_review.py            # fetch bands if missing + review
  .venv\\Scripts\\python.exe scripts\\case\\pairs_detector_review.py --offline                               # review from cache only

Input (read-only): data/pairs/quality/<dir>/{prob.tif, quality.tif, meta.json} of scripts/case/pair_quality.py,
data/pairs/pair_quality.csv. The spectral bands of the same crop (same item, epsg, bounds_utm as meta.json) are re-read
from STAC once and cached in out/l78_bands/<dir>.npz (not in git; the review itself runs offline from the cache).

Per crop the detector objects are rebuilt exactly as pair_quality.py does (prob >= thr on quality code 1 "valid water",
8-connected, cloud-buffer and shadow filters), the observation strip is rebuilt with pair_quality.strip_polygon on the
crop grid. Each object gets ONE type by fixed rules (first match wins, no tuning on field data):
    glint_scene   the crop is a glint scene (pair_quality: B11 median over strip water > 0.01) or the object lies on
                  locally glinting water (median B11 of its 3..10 px water ring > 0.01)
    cloud_edge    within 15 px (150 m) of cloud / spectral cloud / nodata-other (codes 3, 4, 5 inside a non-water area)
                  - the band of the pair_quality cloud buffer (5 px) plus haze fringe
    ship / wake / seam   grid.artifacts.classify on the crop bands (same rules as the service grid)
    coast         within 30 px (300 m) of land (code 2)
    other         none of the above: "possible floating material" (not confirmed)
Outputs: reports/case_pairs/detector_review.{md,json}, reports/case_pairs/review_img/*.png.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "case"))

PAIRS = ROOT / "data" / "pairs"
QDIR = PAIRS / "quality"
CACHE = ROOT / "out" / "l78_bands"
OUT = ROOT / "reports" / "case_pairs"
IMG = OUT / "review_img"
HARM = True  # --no-harm switches the harmonisation check off
BANDS = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"]


def s2_dirs() -> list[Path]:
    return sorted(p.parent for p in QDIR.glob("*/prob.tif"))


# ------------------------------------------------------------------------------------------ fetch (network, once)

def fetch(d: Path) -> Path:
    out = CACHE / f"{d.name}.npz"
    if out.is_file():
        return out
    import pair_quality as pq
    from macroplastic.live import stac
    m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    endpoint, coll = m["source"].split("/", 1)
    item = pq.get_item(endpoint, coll, m["scene_id"], 4)
    crop = stac.read_crop(item, int(m["epsg"]), m["bounds_utm"], workers=8)
    b = crop["bands"]
    if b.shape[1:] != (m["height"], m["width"]):
        raise RuntimeError(f"{d.name}: crop shape {b.shape} != meta {(m['height'], m['width'])}")
    CACHE.mkdir(parents=True, exist_ok=True)
    enc = np.where(np.isfinite(b), np.clip(np.round(b * 10000) + 1000, 1, 65535), 0).astype(np.uint16)
    np.savez_compressed(out, bands=enc, scl=crop["scl"])
    return out


def load_bands(d: Path):
    f = CACHE / f"{d.name}.npz"
    if not f.is_file():
        return None, None
    z = np.load(f)
    enc = z["bands"].astype(np.float32)
    b = np.where(enc > 0, (enc - 1000) / 10000.0, np.nan).astype(np.float32)
    return b, z["scl"]


# ------------------------------------------------------------------------------------------ rebuild strip + detections

Q_NAMES = {0: "nodata", 1: "water", 2: "land", 3: "cloud_scl", 4: "cloud_spectral", 5: "other", 6: "glint"}
CLOUD_EDGE_PX = 15      # 150 m: the pair_quality cloud buffer (5 px) + haze fringe
COAST_PX = 30           # 300 m
GLINT_B11 = 0.01        # the same threshold as the pair decision rule (configs/case_pairs.yaml decision.glint_b11)
GLINT_WIN = 21          # local water B11 mean in a 21x21 px window (210 m)
SMALL_CLOUD_B11 = 0.01  # object SWIR excess over the local water mean (31x31 px), same number as the glint threshold
TYPES = ["ship", "wake", "seam", "cloud_edge", "cloud_small", "glint", "coast", "other_multi", "other_single"]
TYPE_RU = {"ship": "судно", "wake": "кильватер", "seam": "шов / ступень яркости", "cloud_edge": "край облака", "cloud_small": "мелкое облако вне маски",
           "glint": "блик", "coast": "берег (≤ 300 м)", "other_multi": "остальное, ≥ 2 пикселей (возможное скопление)",
           "other_single": "остальное, 1 пиксель (возможное скопление / шум)"}
FALSE_TYPES = ["ship", "wake", "seam", "cloud_edge", "cloud_small", "glint"]


def strip_mask(meta: dict, shape, transform) -> tuple[np.ndarray, str]:
    """Strip raster exactly as in the delivered meta.json (all_touched rasterisation of pair_quality.strip_polygon).
    meta without geometry_source was produced before the PANGAEA-track geometry: rebuild from the CSV line then."""
    import pair_quality as pq
    from rasterio.features import rasterize
    samples = load_samples()
    cfg = dict(meta["config"])
    use_track = meta.get("geometry_source") == "pangaea_track"
    cfg["geometry"] = {"use": use_track, "allow_approx": True}
    g = pq.event_geometry(samples, meta["event_id"], cfg)
    poly, rule = pq.strip_polygon(g, int(meta["epsg"]), cfg)
    strip = rasterize([(poly, 1)], out_shape=shape, transform=transform, all_touched=True, fill=0,
                      dtype="uint8").astype(bool)
    return strip, rule


_SAMPLES = None


def load_samples():
    global _SAMPLES
    if _SAMPLES is None:
        _SAMPLES = pd.read_csv(ROOT / "task" / "macroplastic_marine_samples.csv", low_memory=False)
    return _SAMPLES


def rebuild_detections(prob, qa, bands, meta):
    """pair_quality.process_s2 detector block on the saved rasters: prob >= thr on valid water (code 1), 8-connected,
    minus objects near cloud (codes 3, 4 within cloud_buffer_px) and cloud-shadow-like objects (needs bands)."""
    from scipy import ndimage
    from macroplastic.grid import cloudmask
    thr = float(meta["detector"]["threshold"])
    water_ok = qa == 1
    det = (prob >= thr) & water_ok
    lab, n = ndimage.label(det, structure=np.ones((3, 3), bool))
    drop = np.zeros(n, bool)
    if n:
        cloud = np.isin(qa, (3, 4))
        drop |= cloudmask.near_cloud_components(lab, n, cloud, meta["config"]["detector"]["cloud_buffer_px"])
        if bands is not None:
            drop |= cloudmask.shadow_components(lab, n, bands[1], bands[2], bands[3], bands[7], water_ok)
    lab, n2 = cloudmask.drop_components(lab, n, drop)
    return lab, n2, int(n), int(drop.sum())


def classify_objects(lab, n, qa, bands, strip, meta):
    from scipy import ndimage
    from macroplastic.grid import artifacts
    water = qa == 1
    idx = np.arange(1, n + 1)
    n_px = np.bincount(lab.ravel(), minlength=n + 1)[1:]
    in_strip = np.bincount(lab[strip].ravel(), minlength=n + 1)[1:] > 0
    cloud = np.isin(qa, (3, 4))
    d_cloud = ndimage.distance_transform_edt(~cloud) if cloud.any() else np.full(qa.shape, np.inf)
    land = qa == 2
    d_land = ndimage.distance_transform_edt(~land) if land.any() else np.full(qa.shape, np.inf)
    mind_cloud = np.asarray(ndimage.minimum(d_cloud, lab, idx)) if n else np.zeros(0)
    mind_land = np.asarray(ndimage.minimum(d_land, lab, idx)) if n else np.zeros(0)
    art = [None] * n
    feats = {}
    b11_loc = np.full(n, np.nan)
    if bands is not None and n:
        bd = {k: bands[i] for i, k in enumerate(BANDS)}
        art, feats = artifacts.classify(lab, n, bd, water)
        b11 = np.nan_to_num(bands[10])
        w = water.astype(np.float32)
        num = ndimage.uniform_filter(b11 * w, GLINT_WIN)
        den = ndimage.uniform_filter(w, GLINT_WIN)
        loc = np.where(den > 0.05, num / np.maximum(den, 1e-6), np.nan)
        b11_loc = np.asarray(ndimage.mean(np.nan_to_num(loc, nan=0.0), lab, idx))
    q = meta["quality"]
    scene_glint = bool(q.get("glint_scene")) or (q.get("glint_b11_median") or 0) > GLINT_B11
    typ = []
    for k in range(n):
        if art[k] in ("ship", "wake", "seam"):
            t = art[k]
        elif mind_cloud[k] <= CLOUD_EDGE_PX:
            t = "cloud_edge"
        elif (b11_loc[k] > GLINT_B11) if np.isfinite(b11_loc[k]) else scene_glint:
            t = "glint"
        elif mind_land[k] <= COAST_PX:
            t = "coast"
        else:
            t = "other_multi" if n_px[k] >= 2 else "other_single"
        typ.append(t)
    return dict(n_px=n_px, in_strip=in_strip, type=np.array(typ, dtype=object), art=art,
                d_cloud=mind_cloud, d_land=mind_land, b11_loc=b11_loc, feats=feats)


# ------------------------------------------------------------------------------------------ API layer (read-only)

def api_layer() -> dict:
    """zones + detections exactly as the service builds them (service/case_store.py, read-only use)."""
    sys.path.insert(0, str(ROOT / "service"))
    try:
        import case_store as cs
    except Exception as e:  # noqa: BLE001
        return {"error": repr(e)}
    out = {}
    for z in cs.zones_all():
        p = z["properties"]
        d = p.get("quality_dir")
        if not d or not (QDIR / d / "prob.tif").is_file():
            continue
        dets = cs.zone_detections(z)
        out[d] = dict(zone_n_objects=(p.get("detector") or {}).get("n_objects"),
                      zone_n_pixels=(p.get("detector") or {}).get("n_pixels"),
                      zone_area_m2=p.get("detected_area_m2"),
                      zone_valid_fraction=(p.get("quality") or {}).get("valid_fraction"),
                      zone_glint_fraction=(p.get("quality") or {}).get("glint_fraction"),
                      n_det=len(dets), n_in_strip=sum(1 for f in dets if f["properties"]["in_strip"]),
                      px_in_strip=sum(f["properties"]["n_pixels"] for f in dets if f["properties"]["in_strip"]),
                      area_in_strip_m2=round(sum(f["properties"]["area_m2"] for f in dets if f["properties"]["in_strip"]), 1),
                      in_strip_centroids=[_centroid(f["geometry"]) for f in dets if f["properties"]["in_strip"]],
                      zone_geometry=z.get("geometry"))
    return out


def _centroid(g):
    from shapely.geometry import shape
    c = shape(g).centroid
    return [c.x, c.y]


# ------------------------------------------------------------------------------------------ per crop

def review_crop(d: Path, api: dict) -> tuple[dict, dict]:
    import rasterio
    from scipy import ndimage
    m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    with rasterio.open(d / "prob.tif") as ds:
        prob = ds.read(1).astype(np.float32) / 255.0
        tr, crs = ds.transform, ds.crs
    with rasterio.open(d / "quality.tif") as ds:
        qa = ds.read(1)
    bands, _ = load_bands(d)
    strip, rule = strip_mask(m, qa.shape, tr)
    lab, n, n_raw, n_drop = rebuild_detections(prob, qa, bands, m)
    c = classify_objects(lab, n, qa, bands, strip, m)
    thr = float(m["detector"]["threshold"])
    det_raw = prob >= thr
    water = qa == 1
    typ, ins, npx = c["type"], c["in_strip"], c["n_px"]
    idx = np.arange(1, n + 1)
    sig = {}
    if bands is not None and n:
        wbg = water & (lab == 0)
        wf = wbg.astype(np.float32)
        den = ndimage.uniform_filter(wf, 31)
        for bi, bn in ((1, "B2"), (2, "B3"), (3, "B4"), (7, "B8"), (10, "B11")):
            x = np.nan_to_num(bands[bi])
            bg = ndimage.uniform_filter(x * wf, 31) / np.maximum(den, 1e-6)
            sig[bn] = np.asarray(ndimage.maximum(x - bg, lab, idx))
    # small clouds missed by the quality mask (spectral_cloud keeps blobs >= 100 px only): SWIR excess of the object over
    # its local water >= GLINT_B11. Glint on water is smooth at 300 m, a cloud puff / its fringe is bright in B11.
    sub = np.array([None] * n, dtype=object)
    for k in range(n):
        if "B11" in sig and typ[k] in ("glint", "coast", "other_multi", "other_single") and sig["B11"][k] >= SMALL_CLOUD_B11:
            typ[k] = "cloud_small"
        if typ[k] in ("other_multi", "other_single") and sig:
            e2, e3, e4, e8, e11 = (sig[b_][k] for b_ in ("B2", "B3", "B4", "B8", "B11"))
            if e8 > 0 and min(e2, e3, e4) >= 0.5 * e8 and e11 < 0.5 * SMALL_CLOUD_B11:
                sub[k] = "white_flat"      # white in VIS and NIR, no SWIR: whitecap / foam / white object
            elif e8 >= 0.005 and max(e2, e4) < 0.3 * e8:
                sub[k] = "nir_only"        # NIR excess without visible: floating vegetation / organic-like
            else:
                sub[k] = "mixed"

    def by_type(sel):
        return {t: dict(n=int(((typ == t) & sel).sum()), px=int(npx[(typ == t) & sel].sum())) for t in TYPES}

    out_sel, in_sel = ~ins, ins
    water_out_km2 = float((water & ~strip).sum()) * 1e-4
    q = m["quality"]
    b11_strip_frac = None
    if bands is not None:
        sw = strip & water
        b11_strip_frac = round(float((np.nan_to_num(bands[10]) > GLINT_B11)[sw].mean()), 4) if sw.any() else None
    row = dict(dir=d.name, event_id=m["event_id"], scene_id=m["scene_id"], decision=m["decision"], reason=m["reason"],
               crop_px=int(qa.size), crop_water_km2=round(float(water.sum()) * 1e-4, 2),
               strip_px=int(strip.sum()), strip_px_meta=q["strip_px"], strip_rule=rule,
               glint_b11_median=q.get("glint_b11_median"), glint_frac_meta=q.get("glint_frac"),
               valid_water_frac_meta=q.get("valid_water_frac"), strip_water_b11_gt_thr_frac=b11_strip_frac,
               n_obj_raw=n_raw, n_obj_dropped_cloud_shadow=n_drop, n_obj=int(n),
               n_obj_meta=m["detector"]["n_det_crop"], n_in_strip=int(ins.sum()), n_in_strip_meta=m["detector"]["n_det"],
               px_in_strip=int(npx[ins].sum()), px_out_strip=int(npx[~ins].sum()),
               area_in_strip_m2=int(npx[ins].sum()) * 100, area_out_strip_m2=int(npx[~ins].sum()) * 100,
               n_out_per_km2_water=round(float((~ins).sum()) / max(water_out_km2, 1e-9), 2) if water_out_km2 > 0 else None,
               det_px_frac_water=round(float((lab > 0).sum()) / max(int(water.sum()), 1), 6),
               raw_px_by_quality={Q_NAMES[k]: int((det_raw & (qa == k)).sum()) for k in Q_NAMES},
               area_px_by_quality={Q_NAMES[k]: int((qa == k).sum()) for k in Q_NAMES},
               by_type_out=by_type(out_sel), by_type_in=by_type(in_sel),
               bands_available=bands is not None, harmonize_offset=m["detector"].get("harmonize_offset"))
    a = api.get(d.name) or {}
    row["api"] = {k: v for k, v in a.items() if k not in ("in_strip_centroids", "zone_geometry")}
    # API in-strip objects vs raster strip: which API objects are not in the raster strip
    extra = []
    if a.get("in_strip_centroids"):
        from pyproj import Transformer
        fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        inv_t = ~tr
        for lon, lat in a["in_strip_centroids"]:
            x, y = fwd.transform(lon, lat)
            col, r_ = inv_t * (x, y)
            r_, col = int(np.floor(r_)), int(np.floor(col))
            if 0 <= r_ < qa.shape[0] and 0 <= col < qa.shape[1]:
                k = int(lab[r_, col])
                dist = float(ndimage.distance_transform_edt(~strip)[r_, col])
                extra.append(dict(lon=round(lon, 6), lat=round(lat, 6), row=r_, col=col, label=k,
                                  in_raster_strip=bool(strip[r_, col]) or (k > 0 and bool(ins[k - 1])),
                                  dist_to_raster_strip_px=round(dist, 1),
                                  in_final_mask=k > 0, qa=int(qa[r_, col]), prob=round(float(prob[r_, col]), 3)))
    row["api_in_strip_objects"] = extra
    # proposed single definition, service side without rerun: API zone polygon rasterised on the crop grid (all_touched)
    if a.get("zone_geometry"):
        from rasterio.features import rasterize
        from rasterio.warp import transform_geom
        zg = transform_geom("EPSG:4326", crs, a["zone_geometry"])
        zr = rasterize([(zg, 1)], out_shape=qa.shape, transform=tr, all_touched=True, fill=0, dtype="uint8").astype(bool)
        zin = np.bincount(lab[zr].ravel(), minlength=n + 1)[1:] > 0 if n else np.zeros(0, bool)
        row["api_zone_raster"] = dict(px=int(zr.sum()), px_xor_strip=int((zr ^ strip).sum()), n_in=int(zin.sum()))
    # proposed glint patch of pair_quality.py: per-pixel glint = strip water with B11 > glint_b11 is not "valid water"
    if bands is not None:
        import pair_quality as pq
        gpx = water & (np.nan_to_num(bands[10]) > m["config"]["decision"]["glint_b11"])
        nsv = max(int((strip & (qa != 0)).sum()), 1)
        q2 = dict(q)
        q2["glint_frac"] = round(float((strip & ((qa == 6) | gpx)).sum() / nsv), 4)
        q2["valid_water_frac"] = round(float((strip & water & ~gpx).sum() / max(int(strip.sum()), 1)), 4)
        dec2 = pq.decide(q2, m["config"])
        # variant: detector restricted to non-glint water too (det & ~glint_px)
        from macroplastic.grid import cloudmask
        keep = np.ones(n, bool)
        if n:
            hit = np.bincount(lab[(lab > 0) & ~gpx].ravel(), minlength=n + 1)[1:]
            keep = hit > 0
        lab_g = np.where(gpx, 0, lab)
        n_g = int(len(np.unique(lab_g[lab_g > 0])))
        n_g_in = int(len(np.unique(lab_g[(lab_g > 0) & strip])))
        row["glint_patch_det"] = dict(n_obj=n_g, n_in_strip=n_g_in)
        # interim service filter (quality.tif code 1 + cloud buffer, no shadow test: bands are not in the service)
        det_i = (prob >= thr) & water
        lab_i, n_i = ndimage.label(det_i, structure=np.ones((3, 3), bool))
        if n_i:
            near = cloudmask.near_cloud_components(lab_i, n_i, np.isin(qa, (3, 4)), m["config"]["detector"]["cloud_buffer_px"])
            lab_i, n_i = cloudmask.drop_components(lab_i, n_i, near)
        row["service_interim_filter_n_obj"] = int(n_i)
        row["glint_patch"] = dict(glint_frac=q2["glint_frac"], valid_water_frac=q2["valid_water_frac"],
                                  decision=dec2[0], reason=dec2[1],
                                  decision_unchanged=(dec2[0] == m["decision"] and (dec2[1] or "") == (m["reason"] or "")))
    objs = []
    com = np.array(ndimage.center_of_mass(lab > 0, lab, idx)).reshape(-1, 2) if n else np.zeros((0, 2))
    pmax = np.asarray(ndimage.maximum(prob, lab, idx)) if n else np.zeros(0)
    for k in range(n):
        objs.append(dict(**{f"ex_{bn}": round(float(v[k]), 4) for bn, v in sig.items()},
                         k=k + 1, n_px=int(npx[k]), in_strip=bool(ins[k]), type=typ[k], other_spectrum=sub[k],
                         artifact=c["art"][k],
                         row=round(float(com[k, 0]), 1), col=round(float(com[k, 1]), 1),
                         prob_max=round(float(pmax[k]), 3),
                         d_cloud_px=None if not np.isfinite(c["d_cloud"][k]) else round(float(c["d_cloud"][k]), 1),
                         d_land_px=None if not np.isfinite(c["d_land"][k]) else round(float(c["d_land"][k]), 1),
                         b11_local=None if not np.isfinite(c["b11_loc"][k]) else round(float(c["b11_loc"][k]), 4)))
    row["other_spectrum"] = {k_: sum(1 for o in objs if o["other_spectrum"] == k_) for k_ in ("white_flat", "nir_only", "mixed")}
    row["other_spectrum_in_strip"] = {k_: sum(1 for o in objs if o["other_spectrum"] == k_ and o["in_strip"])
                                      for k_ in ("white_flat", "nir_only", "mixed")}
    row["_strip"] = strip
    if HARM:
        harmonize_check(d, row, lab)
    row.pop("_strip", None)
    ctx = dict(meta=m, prob=prob, qa=qa, bands=bands, strip=strip, lab=lab, objs=objs, tr=tr)
    return row, ctx


# ------------------------------------------------------------------------------------------ harmonisation check

_PRED = {}


def harmonize_check(d: Path, row: dict, lab_h) -> None:
    """Re-run the same LightGBM on the cached bands with harmonize=None (pair_quality uses 'water_median': a per-scene
    offset that moves the water median to the MARIDA water median) and with 'water_median' (must reproduce prob.tif)."""
    import rasterio
    from macroplastic.live import stac
    from macroplastic.models.lgbm_predict import load_predictor
    m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    bands, scl = load_bands(d)
    if bands is None:
        row["no_harmonize"] = None
        return
    with rasterio.open(d / "quality.tif") as ds:
        qa = ds.read(1)
    with rasterio.open(d / "prob.tif") as ds:
        p_saved = ds.read(1).astype(np.int16)
    res = {}
    for h in ("water_median", None):
        if h not in _PRED:
            _PRED[h] = load_predictor(ROOT / m["config"]["detector"]["weights"], harmonize=h)
        p = np.nan_to_num(_PRED[h].predict_proba(bands, stac.BANDS, water_mask=(scl == 6)), nan=0.0)
        pu = np.clip(np.round(p * 255), 0, 255)
        lab, n, _, _ = rebuild_detections(pu.astype(np.float32) / 255.0, qa, bands, m)
        strip = row["_strip"]
        res[str(h)] = dict(n_obj=int(n), px=int((lab > 0).sum()),
                           n_in_strip=int(len(np.unique(lab[(lab > 0) & strip]))),
                           max_abs_diff_vs_saved_u8=(int(np.abs(pu.astype(np.int16) - p_saved).max()) if h else None))
        if h is None:
            # which harmonised objects survive without harmonisation
            surv = np.unique(lab_h[(lab_h > 0) & (lab > 0)])
            res["None"]["n_harmonised_objects_kept"] = int(len(surv))
    row["no_harmonize"] = res


# ------------------------------------------------------------------------------------------ gallery

T_COL = {"ship": "#ff2020", "wake": "#ff9900", "seam": "#b040ff", "cloud_edge": "#00e5ff", "cloud_small": "#ffffff", "glint": "#ffee00",
         "coast": "#a0522d", "other_multi": "#40ff40", "other_single": "#20a020"}
Q_COL = {0: (0, 0, 0), 1: (40, 90, 200), 2: (150, 120, 70), 3: (240, 240, 240), 4: (255, 170, 0), 5: (120, 120, 120),
         6: (255, 255, 120)}


def _stretch(x, lo=None, hi=None):
    x = np.nan_to_num(x, nan=0.0)
    lo = np.percentile(x, 1) if lo is None else lo
    hi = np.percentile(x, 99.5) if hi is None else hi
    return np.clip((x - lo) / max(hi - lo, 1e-6), 0, 1)


def _composite(bands, win, idx):
    return np.dstack([_stretch(bands[i][win]) for i in idx]) ** (1 / 1.4)


def frame(ctx, center, half, title, path: Path, note: str = ""):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from scipy import ndimage
    H, W = ctx["qa"].shape
    r0, c0 = int(center[0]), int(center[1])
    y0, y1 = max(r0 - half, 0), min(r0 + half, H)
    x0, x1 = max(c0 - half, 0), min(c0 + half, W)
    win = (slice(y0, y1), slice(x0, x1))
    b = ctx["bands"]
    lab = ctx["lab"][win]
    strip = ctx["strip"][win]
    so = strip & ~ndimage.binary_erosion(strip)
    fig, ax = plt.subplots(1, 4, figsize=(17, 4.9))
    rgb = _composite(b, win, (3, 2, 1))
    fc = _composite(b, win, (10, 7, 3))
    ax[0].imshow(rgb); ax[0].set_title("RGB (B4 B3 B2)")
    ax[1].imshow(fc); ax[1].set_title("SWIR ложный цвет (B11 B8 B4)")
    ov = rgb.copy()
    ov[so] = (1, 0, 1)
    ax[2].imshow(ov); ax[2].set_title("детекции (контур) + полоса (пурпур)")
    qa = ctx["qa"][win]
    qi = np.zeros(qa.shape + (3,), np.uint8)
    for k, cc in Q_COL.items():
        qi[qa == k] = cc
    qi[so] = (255, 0, 255)
    ax[3].imshow(qi); ax[3].set_title("маска качества (синий вода, белый облако,\nоранж. спектр. облако, жёлт. блик, серый прочее)", fontsize=8)
    for o in ctx["objs"]:
        if y0 <= o["row"] < y1 and x0 <= o["col"] < x1:
            for a_ in (ax[2], ax[3]):
                m_ = lab == o["k"]
                if m_.sum() >= 3:
                    a_.contour(m_, levels=[0.5], colors=[T_COL[o["type"]]], linewidths=1.2)
                a_.scatter([o["col"] - x0], [o["row"] - y0], s=60, facecolors="none", edgecolors=T_COL[o["type"]], linewidths=1.3)
    for a_ in ax:
        a_.set_xticks([]); a_.set_yticks([])
    from matplotlib.lines import Line2D
    present = sorted({o["type"] for o in ctx["objs"] if y0 <= o["row"] < y1 and x0 <= o["col"] < x1}, key=TYPES.index)
    ax[2].legend(handles=[Line2D([], [], marker="o", ls="", mfc="none", mec=T_COL[t], label=TYPE_RU[t]) for t in present],
                 loc="lower left", fontsize=7, framealpha=0.7)
    fig.suptitle(f"{title}\nокно {(x1 - x0) * 10 / 1000:.1f}×{(y1 - y0) * 10 / 1000:.1f} км, центр (стр {r0}, кол {c0}); {note}", fontsize=10)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=80)
    plt.close(fig)


def overview(ctx, title, path: Path, note=""):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    b = ctx["bands"]
    k = max(1, int(np.ceil(max(b.shape[1:]) / 900)))
    sub = b[:, ::k, ::k]
    full = (slice(None), slice(None))
    rgb = _composite(sub, full, (3, 2, 1))
    fig, ax = plt.subplots(1, 2, figsize=(14, 12 * b.shape[1] / b.shape[2] / 1.6 + 1.5))
    ax[0].imshow(rgb)
    st = ctx["strip"][::k, ::k]
    ys, xs = np.nonzero(st)
    for a_ in ax:
        a_.scatter(xs, ys, s=0.3, c="#ff00ff")
    qa = ctx["qa"][::k, ::k]
    qi = np.zeros(qa.shape + (3,), np.uint8)
    for kk, cc in Q_COL.items():
        qi[qa == kk] = cc
    ax[1].imshow(qi)
    for t in TYPES:
        pts = [(o["col"] / k, o["row"] / k) for o in ctx["objs"] if o["type"] == t]
        if pts:
            p = np.array(pts)
            for a_ in ax:
                a_.scatter(p[:, 0], p[:, 1], s=9, c=T_COL[t], label=f"{TYPE_RU[t]}: {len(pts)}" if a_ is ax[0] else None)
    ax[0].legend(loc="upper left", fontsize=8, framealpha=0.8)
    ax[0].set_title("RGB + объекты детектора (точка = объект) + полоса (пурпур)")
    ax[1].set_title("маска качества + объекты")
    for a_ in ax:
        a_.set_xticks([]); a_.set_yticks([])
    fig.suptitle(f"{title}\n{note}", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=80)
    plt.close(fig)


# ------------------------------------------------------------------------------------------ main

def pick_frames(rows, objs_by_dir) -> list[dict]:
    """Rule-based frame list (no hand picking): see the module docstring of the report."""
    fr = []
    he = "S3_HE460_MarLitter_transect03"
    if he in objs_by_dir:
        ob = objs_by_dir[he]
        fr.append(dict(dir=he, kind="overview", name="01_HE460_t03_overview",
                       title="HE460 t03 (S2A 2016-04-11, принята по маскам): все объекты детектора на вырезке 15×27 км"))
        ins = [o for o in ob if o["in_strip"]]
        if ins:
            o = max(ins, key=lambda o: (o["n_px"], o["prob_max"]))
            fr.append(dict(dir=he, center=(o["row"], o["col"]), half=50, name="02_HE460_t03_in_strip",
                           title=f"HE460 t03: объект в полосе обследования ({o['n_px']} пикс., P {o['prob_max']}; всего в полосе {len(ins)})"))
        outs = [o for o in ob if not o["in_strip"] and o["type"].startswith("other")]
        if outs:
            big = max(outs, key=lambda o: (o["n_px"], o["prob_max"]))
            fr.append(dict(dir=he, center=(big["row"], big["col"]), half=50, name="03_HE460_t03_largest_other",
                           title=f"HE460 t03: самый крупный объект «остальное» вне полосы ({big['n_px']} пикс., P {big['prob_max']})"))
            single = sorted([o for o in outs if o["n_px"] == 1], key=lambda o: -o["prob_max"])
            if single:
                s = single[len(single) // 2]
                fr.append(dict(dir=he, center=(s["row"], s["col"]), half=40, name="04_HE460_t03_typical_single",
                               title=f"HE460 t03: типичный одиночный пиксель вне полосы (медиана по P среди 1-пикс., P {s['prob_max']})"))
        ships = [o for o in ob if o["type"] in ("ship", "wake")]
        if ships:
            s = max(ships, key=lambda o: o["n_px"])
            fr.append(dict(dir=he, center=(s["row"], s["col"]), half=40, name="05_HE460_t03_ship",
                           title=f"HE460 t03: объект, помеченный grid.artifacts как {s['type']}"))
    i = 6
    for d in ("S4_DOORS3_T14", "S4_DOORS3_T20", "S4_DOORS3_T21"):
        ins = [o for o in objs_by_dir.get(d, []) if o["in_strip"]]
        if ins:
            c = np.mean([[o["row"], o["col"]] for o in ins], 0)
            r = next(x for x in rows if x["dir"] == d)
            fr.append(dict(dir=d, center=c, half=70, name=f"{i:02d}_{d[10:]}_in_strip_glint_scene",
                           title=f"{d[10:]}: отклонена по блику (B11 медиана {r['glint_b11_median']} > 0.01), объект в полосе"))
            i += 1
    allo = [(d, o) for d, ob in objs_by_dir.items() for o in ob]
    ce = [x for x in allo if x[1]["type"] == "cloud_edge"]
    if ce:
        d, o = max(ce, key=lambda x: x[1]["n_px"])
        fr.append(dict(dir=d, center=(o["row"], o["col"]), half=60, name=f"{i:02d}_{d.split('_', 1)[1]}_cloud_edge",
                       title=f"{d}: крупнейший объект у края облака ({o['n_px']} пикс.)"))
        i += 1
    for t in ("seam", "wake"):
        xs = [x for x in allo if x[1]["type"] == t and x[0] != he]
        if xs:
            d, o = max(xs, key=lambda x: x[1]["n_px"])
            fr.append(dict(dir=d, center=(o["row"], o["col"]), half=60, name=f"{i:02d}_{d.split('_', 1)[1]}_{t}",
                           title=f"{d}: крупнейший объект типа «{TYPE_RU[t]}» ({o['n_px']} пикс.)"))
            i += 1
    t16 = [o for o in objs_by_dir.get("S4_DOORS3_T16", []) if o["type"].startswith("other")]
    if t16:
        o = max(t16, key=lambda o: (o["n_px"], o["prob_max"]))
        fr.append(dict(dir="S4_DOORS3_T16", center=(o["row"], o["col"]), half=60, name=f"{i:02d}_T16_glint_scene_other",
                       title=f"T16 (отклонена по блику): крупнейший объект «остальное» вне бликового участка ({o['n_px']} пикс.)"))
        i += 1
    acc = [x for x in allo if next(r for r in rows if r["dir"] == x[0])["decision"] == "accept" and x[0] != he]
    if acc:
        d, o = max(acc, key=lambda x: (x[1]["n_px"], x[1]["prob_max"]))
        fr.append(dict(dir=d, center=(o["row"], o["col"]), half=60, name=f"{i:02d}_{d.split('_', 1)[1]}_accepted_largest",
                       title=f"{d} (принята): крупнейший объект ({o['n_px']} пикс., {TYPE_RU[o['type']]})"))
        i += 1
    return fr[:12]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="do not fetch bands (cache only)")
    ap.add_argument("--no-img", action="store_true")
    ap.add_argument("--no-harm", action="store_true", help="skip the harmonize=None re-run of the detector")
    a = ap.parse_args()
    global HARM
    HARM = not a.no_harm
    t0 = time.time()
    dirs = s2_dirs()
    if not a.offline:
        for d in dirs:
            try:
                fetch(d)
            except Exception as e:  # noqa: BLE001
                print(f"[fetch] {d.name}: {e!r}", flush=True)
    api = api_layer()
    rows, objs_by_dir = [], {}
    for d in dirs:
        row, ctx = review_crop(d, api if isinstance(api, dict) and "error" not in api else {})
        rows.append(row)
        objs_by_dir[d.name] = ctx["objs"]
        print(f"{d.name}: {row['decision']:6s} obj {row['n_obj']:4d} (meta {row['n_obj_meta']}) in strip {row['n_in_strip']} "
              f"(meta {row['n_in_strip_meta']}, api {row['api'].get('n_in_strip')}) "
              + " ".join(f"{t}={row['by_type_out'][t]['n'] + row['by_type_in'][t]['n']}" for t in TYPES), flush=True)
    frames = pick_frames(rows, objs_by_dir)
    if not a.no_img:
        IMG.mkdir(parents=True, exist_ok=True)
        for old in IMG.glob("*.png"):
            old.unlink()
        HARM = False
        for d in sorted({f["dir"] for f in frames}):
            _, ctx = review_crop(QDIR / d, {})
            if ctx["bands"] is None:
                continue
            for f in frames:
                if f["dir"] != d:
                    continue
                p = IMG / f"{f['name']}.png"
                if f.get("kind") == "overview":
                    overview(ctx, f["title"], p, note=f"{ctx['meta']['scene_id']}")
                else:
                    frame(ctx, f["center"], f["half"], f["title"], p, note=ctx["meta"]["scene_id"])
                f["file"] = str(p.relative_to(ROOT)).replace("\\", "/")
    res = dict(created=time.strftime("%Y-%m-%dT%H:%M:%S"), runtime_s=round(time.time() - t0, 1),
               rules=dict(cloud_edge_px=CLOUD_EDGE_PX, coast_px=COAST_PX, glint_b11=GLINT_B11, glint_win_px=GLINT_WIN,
                          order=["ship/wake/seam (grid.artifacts.classify)", "cloud_edge", "cloud_small (object B11 excess >= 0.01)", "glint", "coast", "other"]),
               crops=rows, frames=[{k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in f.items()} for f in frames],
               objects={d: ob for d, ob in objs_by_dir.items()}, api_error=api.get("error") if isinstance(api, dict) else None)
    res["summary"] = summarize(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "detector_review.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=_js), encoding="utf-8")
    write_md(res)
    print(json.dumps(res["summary"]["totals"], ensure_ascii=False))
    print(f"done in {time.time() - t0:.1f} s")


def _js(x):
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, np.ndarray):
        return x.tolist()
    return str(x)


def _group(r):
    if r["decision"] == "accept":
        return "accept"
    return "reject_" + (r["reason"] or "other").split("(")[0]


def summarize(rows) -> dict:
    def agg(rs):
        t = {}
        for where in ("in", "out"):
            for ty in TYPES:
                t.setdefault(ty, dict(n=0, px=0, n_in=0, n_out=0))
                for r in rs:
                    v = r[f"by_type_{where}"][ty]
                    t[ty]["n"] += v["n"]; t[ty]["px"] += v["px"]; t[ty][f"n_{where}"] += v["n"]
        n = sum(v["n"] for v in t.values())
        px = sum(v["px"] for v in t.values())
        false_n = sum(t[ty]["n"] for ty in FALSE_TYPES)
        return dict(n_crops=len(rs), n_obj=n, px=px,
                    n_in_strip=sum(r["n_in_strip"] for r in rs), n_out_strip=sum(r["n_obj"] - r["n_in_strip"] for r in rs),
                    px_in_strip=sum(r["px_in_strip"] for r in rs), px_out_strip=sum(r["px_out_strip"] for r in rs),
                    water_km2=round(sum(r["crop_water_km2"] for r in rs), 2),
                    n_dropped_cloud_shadow=sum(r["n_obj_dropped_cloud_shadow"] for r in rs),
                    by_type=t, share_n={ty: round(t[ty]["n"] / n, 4) if n else None for ty in TYPES},
                    share_px={ty: round(t[ty]["px"] / px, 4) if px else None for ty in TYPES},
                    false_share_n=round(false_n / n, 4) if n else None,
                    linear_share_n=round((t["seam"]["n"] + t["wake"]["n"]) / n, 4) if n else None,
                    other_spectrum={k: sum(r["other_spectrum"][k] for r in rs) for k in ("white_flat", "nir_only", "mixed")},
                    raw_px_by_quality={k: sum(r["raw_px_by_quality"][k] for r in rs) for k in Q_NAMES.values()},
                    area_px_by_quality={k: sum(r["area_px_by_quality"][k] for r in rs) for k in Q_NAMES.values()})
    groups = {}
    for r in rows:
        groups.setdefault(_group(r), []).append(r)
    he = [r for r in rows if r["dir"] == "S3_HE460_MarLitter_transect03"]
    rest = [r for r in rows if r["dir"] != "S3_HE460_MarLitter_transect03"]
    return dict(totals=agg(rows), by_group={g: agg(rs) for g, rs in sorted(groups.items())},
                he460_t03=agg(he), without_he460_t03=agg(rest),
                repro=dict(n_obj_equal=all(r["n_obj"] == r["n_obj_meta"] for r in rows),
                           n_in_strip_equal=all(r["n_in_strip"] == r["n_in_strip_meta"] for r in rows),
                           strip_px_equal=all(r["strip_px"] == r["strip_px_meta"] for r in rows),
                           api_in_strip_diff={r["dir"]: [r["n_in_strip"], r["api"].get("n_in_strip")] for r in rows
                                              if r["api"] and r["api"].get("n_in_strip") != r["n_in_strip"]},
                           api_n_diff={r["dir"]: [r["n_obj"], r["api"].get("n_det")] for r in rows
                                       if r["api"] and r["api"].get("n_det") != r["n_obj"]}))


def _pct(x, nd=1):
    return "—" if x is None else f"{100 * x:.{nd}f} %"


def write_md(res):
    S = res["summary"]
    T, rows = S["totals"], res["crops"]
    he = next((r for r in rows if r["dir"] == "S3_HE460_MarLitter_transect03"), None)
    L = ["# Детектор на реальных снимках пар (22 вырезки Sentinel-2 L2A)", "",
         "Скрипт: `scripts/case/pairs_detector_review.py` (CPU, " + f"{res['runtime_s']:.0f} с" + "). Числа: `reports/case_pairs/detector_review.json`. "
         "Кадры: `reports/case_pairs/review_img/`.", "",
         "Вход: вырезки `data/pairs/quality/<пара>/` (скрипт `scripts/case/pair_quality.py`: `prob.tif`, `quality.tif`, `meta.json`). "
         "Каналы тех же вырезок (тот же снимок, та же проекция и рамка из `meta.json`) один раз перечитаны из STAC в `out/l78_bands/`, "
         "дальше расчёт идёт без сети.", ""]
    rp = S["repro"]
    L += ["## 1. Воспроизведение детекций", "",
          f"Объекты детектора пересобраны так же, как в `pair_quality.py`: P ≥ 0.63 только на пикселях «пригодная вода» (код 1), 8-связность, "
          f"отброс объектов в 5 пикс. от облака и объектов-теней. Сверка с `meta.json` по 22 вырезкам: число объектов — "
          f"{'совпало' if rp['n_obj_equal'] else 'НЕ совпало'}, объекты в полосе — {'совпали' if rp['n_in_strip_equal'] else 'НЕ совпали'}, "
          f"площадь полосы в пикселях — {'совпала' if rp['strip_px_equal'] else 'НЕ совпала'}.", "",
          "Правила типов (порядок = приоритет, ничего не подбиралось по полевым данным):", "",
          "| Тип | Правило |", "|---|---|",
          "| судно / кильватер / шов | `macroplastic.grid.artifacts.classify` на каналах вырезки: те же правила, что в сетке сервиса (яркое пятно в видимом и NIR; длинная прямая полоса; объект на ступени яркости воды) |",
          f"| край облака | до облака (код 3 или 4 маски качества) ≤ {CLOUD_EDGE_PX} пикс. (150 м). В 5 пикс. объекты уже отброшены в `pair_quality`, поэтому это полоса 5–15 пикс. |",
          f"| мелкое облако вне маски | избыток B11 объекта над средним по воде в окне 31×31 пикс. ≥ {SMALL_CLOUD_B11} (облачко или его кайма ярки в SWIR; блик на воде в масштабе 300 м гладкий) |",
          f"| блик | среднее B11 воды в окне {GLINT_WIN}×{GLINT_WIN} пикс. вокруг объекта > {GLINT_B11}. Это тот же порог, что в правиле отказа пары по блику (медиана B11 воды полосы) |",
          f"| берег | до суши (код 2) ≤ {COAST_PX} пикс. (300 м) |",
          "| остальное | ни одно правило не сработало: «возможное скопление», без подтверждения; отдельно 1 пиксель и ≥ 2 пикселей |", ""]
    L += ["## 2. Все 22 вырезки", "",
          "Площадь воды — пиксели с кодом 1 маски качества на всей вырезке (полоса ± 3 км). «Вне/км²» — объекты вне полосы на км² воды вне полосы, это грубый фон срабатываний.", "",
          "| Пара | Решение | Вода, км² | Объектов | в полосе | вне | вне/км² | Судно | Кильв. | Шов | Край обл. | Мелк. обл. | Блик | Берег | Ост. ≥2 | Ост. 1 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        bt = {t: r["by_type_in"][t]["n"] + r["by_type_out"][t]["n"] for t in TYPES}
        L.append(f"| {r['event_id']} | {r['decision']} {r['reason'] or ''} | {r['crop_water_km2']} | {r['n_obj']} | {r['n_in_strip']} | "
                 f"{r['n_obj'] - r['n_in_strip']} | {r['n_out_per_km2_water'] if r['n_out_per_km2_water'] is not None else '—'} | "
                 + " | ".join(str(bt[t]) for t in TYPES) + " |")
    L += ["", "### По группам сцен", "",
          "| Группа | Вырезок | Вода, км² | Объектов | в полосе | вне | Пикс. | Ложные типы* | в т.ч. блик | край обл. | судно+кильв. | шов | Остальное ≥2 | Остальное 1 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    def grow(name, g):
        sh = g["share_n"]
        return (f"| {name} | {g['n_crops']} | {g['water_km2']} | {g['n_obj']} | {g['n_in_strip']} | {g['n_out_strip']} | {g['px']} | "
                f"{_pct(g['false_share_n'])} | {_pct(sh['glint'])} | {_pct(sh['cloud_edge'])} | {_pct((sh['ship'] or 0) + (sh['wake'] or 0))} | "
                f"{_pct(sh['seam'])} | {_pct(sh['other_multi'])} | {_pct(sh['other_single'])} |")
    L.append(grow("все", T))
    for gname, g in S["by_group"].items():
        L.append(grow(gname, g))
    L.append(grow("HE460 t03", S["he460_t03"]))
    L.append(grow("все без HE460 t03", S["without_he460_t03"]))
    L += ["", "\\* Ложные типы = судно + кильватер + шов + край облака + мелкое облако вне маски + блик (доля объектов). «Край обл.» здесь без мелких облаков. «Остальное» не значит «мусор»: "
          "это то, что правила не объяснили.", ""]
    L += ["### Где детектор срабатывает до масок качества", "",
          "Пиксели P ≥ 0.63 на всей вырезке по классам маски качества (до фильтра «только пригодная вода»). "
          "Код 3 объединяет облако, тень облака и перистые облака по SCL: тень отдельно не выделена.", "",
          "| Класс маски | Пикселей класса | P ≥ 0.63 | доля класса |", "|---|---|---|---|"]
    for k in Q_NAMES.values():
        a_ = T["area_px_by_quality"][k]; d_ = T["raw_px_by_quality"][k]
        L.append(f"| {k} | {a_} | {d_} | {_pct(d_ / a_ if a_ else None, 3)} |")
    L.append("")
    res["_md"] = L
    (OUT / "detector_review.md").write_text("\n".join(L + extra_md(res)) + "\n", encoding="utf-8")


def extra_md(res):
    S, rows = res["summary"], res["crops"]
    T = S["totals"]
    by = {r["dir"]: r for r in rows}
    he = by.get("S3_HE460_MarLitter_transect03")
    ob = res["objects"].get("S3_HE460_MarLitter_transect03", [])
    oth = [o for o in ob if o["type"].startswith("other")]

    def q(k, p):
        return float(np.percentile([o[k] for o in oth], p)) if oth else float("nan")

    npx = np.bincount([o["n_px"] for o in ob]) if ob else np.zeros(1, int)
    nh = {r["dir"]: (r.get("no_harmonize") or {}).get("None") for r in rows}
    n_noh = sum((v or {}).get("n_obj", 0) for v in nh.values())
    L = []
    # ---------------------------------------------------------------- 3. HE460 t03
    if he:
        api_out = he["api"].get("n_det", 0) - he["api"].get("n_in_strip", 0)
        other_rates = [(r["n_out_per_km2_water"] or 0) for r in rows if r["dir"] != he["dir"]]
        L += ["## 3. HE460 t03: что такое «ещё 720 вне полосы»", "",
              f"Вырезка 15,4 × 27,1 км вокруг трансекты длиной 22,9 км, вода {he['crop_water_km2']} км² (100 % пригодной воды, облаков 0 %). "
              f"Объектов детектора: **{he['n_obj']}** ({he['px_in_strip'] + he['px_out_strip']} пикселей, "
              f"{_pct(he['det_px_frac_water'], 3)} воды). В полосе обследования (10 м) их {he['n_in_strip']}, вне полосы {he['n_obj'] - he['n_in_strip']}. "
              f"На карте написано «ещё {api_out} вне полосы», потому что API относит к полосе 4 объекта, а не 3 (см. раздел 7.1). "
              f"Это **{he['n_out_per_km2_water']} объекта на км²** воды вне полосы. Выше фон только на T16 ({max(other_rates):.2f}/км², отклонена по блику, раздел 4). "
              "На принятых сценах Чёрного моря — не больше 0,55/км².", "",
              "| Признак | Значение | Как читать |", "|---|---|---|",
              f"| Размер | 1 пикс.: {int(npx[1]) if len(npx) > 1 else 0}, 2 пикс.: {int(npx[2]) if len(npx) > 2 else 0}, "
              f"≥ 3 пикс.: {int(npx[3:].sum()) if len(npx) > 3 else 0} | почти всё — одиночные пиксели 10 × 10 м, протяжённых полос и пятен нет |",
              f"| Типы по правилам | судно {he['by_type_out']['ship']['n'] + he['by_type_in']['ship']['n']}, "
              f"кильватер / шов / блик / облако / берег 0, остальное {len(oth)} | облаков, бликов и берега на вырезке нет, "
              "правила grid.artifacts нашли 2 судна |",
              f"| P детектора, медиана (10–90 %) | {q('prob_max', 50):.2f} ({q('prob_max', 10):.2f}–{q('prob_max', 90):.2f}) | "
              "уверенные срабатывания, не у порога 0.63 |",
              f"| Избыток над локальной водой B2 / B4 / B8 / B11, медиана | {q('ex_B2', 50):.4f} / {q('ex_B4', 50):.4f} / "
              f"{q('ex_B8', 50):.4f} / {q('ex_B11', 50):.4f} | пиксель одинаково ярче воды в видимом и NIR и не ярче в SWIR: белая мокрая поверхность |",
              f"| Спектр «белый плоский» (min(B2, B3, B4) ≥ 0.5·B8, B11 < 0.005) | {he['other_spectrum']['white_flat']} из {len(oth)} | "
              "так выглядят барашки и пена, а также белые предметы |",
              "| Размещение | равномерно по всей вырезке, с полосой не связано (кадр 01) | нет линий, фронтов и сгущений вдоль течений |", "",
              "**Что это, скорее всего.** Сцена — ветреное море: на кадрах 03 и 04 рябь и белые крапины по всей вырезке. "
              "Одиночные белые пиксели без отклика в SWIR, разбросанные равномерно, — это барашки и пена. Мусор так не выглядит. "
              "Проверка на физическую возможность: избыток яркости 0.015 в пикселе 100 м² — это около 4 м² белого материала "
              "с отражением 0.3–0.5 (≈ 0.015 / 0.4 × 100 м²). Поле насчитало 52,3 предмета > 2 см на км², в основном сантиметрового размера. "
              "Один предмет 10 см даёт в пикселе 10 м избыток около 0.00004, в 400 раз меньше наблюдаемого. "
              "Значит, **724 срабатывания не могут быть предметами, которые считало поле**. Это либо крупные объекты (несколько м², "
              "1,7 на км²), либо состояние поверхности моря. Плоский белый спектр и равномерный разброс говорят за второе. Это оценка по правилам: "
              "полевого подтверждения объектов нет.", ""]
        h = (he.get("no_harmonize") or {})
        L += ["**Главная проверка: гармонизация.** `pair_quality.py` запускает LightGBM с `harmonize=\"water_median\"`. "
              "На каждой сцене все каналы сдвигаются так, чтобы медиана воды совпала с медианой воды MARIDA; смещения записаны в `meta.json`. "
              "У HE460 t03 это +0.029 / +0.024 в B1 / B2 и +0.013…+0.016 в B5–B8A. У сцен Чёрного моря B1–B3 сдвигаются вниз. "
              "Ниже та же модель на тех же каналах, пересчитанная в этом скрипте:", "",
              "| Вырезка | Объектов с harmonize=water_median (как в пайплайне, совпало с prob.tif) | Объектов с harmonize=None | из них те же объекты |",
              "|---|---|---|---|"]
        for r in rows:
            v = r.get("no_harmonize") or {}
            if r["n_obj"] or (v.get("None") or {}).get("n_obj"):
                L.append(f"| {r['event_id']} | {(v.get('water_median') or {}).get('n_obj')} | {(v.get('None') or {}).get('n_obj')} | "
                         f"{(v.get('None') or {}).get('n_harmonised_objects_kept')} |")
        L += ["", f"Без гармонизации на всех 22 вырезках остаётся **{n_noh} объектов вместо {T['n_obj']}** "
              f"(HE460 t03: {(h.get('None') or {}).get('n_obj')}, в полосе {(h.get('None') or {}).get('n_in_strip')}). "
              "Пропадает и настоящее судно с кильватером (T25, кадр 11). Значит, на L2A-вырезках пар **ответ детектора почти целиком "
              "определяется сдвигом гармонизации**. Без сдвига модель молчит даже на явном судне. Со сдвигом она срабатывает и на судах, "
              "и на белых крапинах моря. Ни один вариант на L2A не проверен разметкой. Качество на MARIDA test (F1 0.871) "
              "получено на данных MARIDA, на эти вырезки оно не переносится.", ""]
    # ---------------------------------------------------------------- 4. glint strips
    L += ["## 4. Полосы, отклонённые по блику: T14, T20, T21", "",
          "| Полоса | Медиана B11 воды полосы | Объект в полосе | Тип по правилам | Что на кадре |", "|---|---|---|---|---|"]
    notes = {"S4_DOORS3_T14": "рябь с бликом по всей вырезке; одиночный пиксель на бликующей воде (кадр 06)",
             "S4_DOORS3_T20": "маленькое кучевое облачко: белое пятно с цветной каймой, рядом его тень. Маска его не видит: "
                              "спектральный тест облаков оставляет только пятна ≥ 100 пикс. (кадр 07)",
             "S4_DOORS3_T21": "то же: мелкие облака с тенями на бликующей воде, 6 пикселей на краю облачка (кадр 08)"}
    for d in ("S4_DOORS3_T14", "S4_DOORS3_T20", "S4_DOORS3_T21"):
        r = by.get(d)
        if not r:
            continue
        ins = [o for o in res["objects"][d] if o["in_strip"]]
        L.append(f"| {d[10:]} | {r['glint_b11_median']} | "
                 + "; ".join(f"{o['n_px']} пикс., P {o['prob_max']}, избыток B11 {o['ex_B11']}" for o in ins)
                 + f" | {', '.join(TYPE_RU[o['type']] for o in ins)} | {notes[d]} |")
    g = S["by_group"].get("reject_glint")
    if g:
        L += ["", f"На всех вырезках, отклонённых по блику ({g['n_crops']} шт.), **{g['n_obj']} объектов**: "
              f"{g['by_type']['glint']['n']} — блик, {g['by_type']['cloud_small']['n']} — мелкие облака вне маски, "
              f"{g['by_type']['cloud_edge']['n']} — край облака (ложные типы {_pct(g['false_share_n'])}). Ещё "
              f"{g['by_type']['other_multi']['n'] + g['by_type']['other_single']['n']} — «остальное», и все они на T16. "
              "Там объекты лежат в тёмной воде между бликующими полосами, их спектр — избыток только в NIR (+0.01) без видимого. "
              "Это шум B8 на тёмной воде или плавающая органика (кадр 12). "
              "**Все 3 объекта в бликовых полосах — ложные срабатывания на сложном фоне:** один блик и два мелких облака, которые пропустила маска. "
              "Отказ по блику поставлен правильно, но карта всё равно показывает эти объекты со значком «1».", ""]
    # ---------------------------------------------------------------- 5. shares + MARIDA
    L += ["## 5. Доля ложных на сложном фоне и сравнение с MARIDA test", "",
          "| Набор | Объектов | Блик | Облако (край + мелкие) | Судно + кильватер | Шов | Все ложные типы | «Остальное» |",
          "|---|---|---|---|---|---|---|---|"]

    def srow(name, g_):
        sh = g_["share_n"]
        return (f"| {name} | {g_['n_obj']} | {_pct(sh['glint'])} | {_pct((sh['cloud_edge'] or 0) + (sh['cloud_small'] or 0))} | "
                f"{_pct((sh['ship'] or 0) + (sh['wake'] or 0))} | {_pct(sh['seam'])} | {_pct(g_['false_share_n'])} | "
                f"{_pct((sh['other_multi'] or 0) + (sh['other_single'] or 0))} |")
    L += [srow("все 22 вырезки", T), srow("без HE460 t03", S["without_he460_t03"])]
    for k_, g_ in S["by_group"].items():
        L.append(srow(k_, g_))
    L += ["",
          f"- Линейные артефакты (кильватер + шов по `grid.artifacts`) — {_pct(T['linear_share_n'])} объектов. На краях облаков (5–15 пикс.) — "
          f"{_pct(T['share_n']['cloud_edge'])}, на бликующей воде — {_pct(T['share_n']['glint'])}, на мелких облаках вне маски — "
          f"{_pct(T['share_n']['cloud_small'])}.",
          f"- До масок качества детектор почти не срабатывает внутри облаков: {T['raw_px_by_quality']['cloud_scl']} + "
          f"{T['raw_px_by_quality']['cloud_spectral']} пикселей из {T['area_px_by_quality']['cloud_scl'] + T['area_px_by_quality']['cloud_spectral']} облачных. "
          f"На бликовых кодах (T3–T5, где вся вода в блике) — {T['raw_px_by_quality']['glint']} из {T['area_px_by_quality']['glint']}. "
          "Ложные срабатывания на сложном фоне сидят на **краях** облаков и на бликующей воде, которую маска считает пригодной.",
          "- **MARIDA test** (`reports/case_detector/compare.md`): облака 0.003 % пикселей (1 из 32 843), тени облаков 0, волны 0.64 %, пена 0.26 %, "
          "кильватеры 0.32 %, суда 2.3 %; класса «блик» в MARIDA нет. "
          "Там это доли пикселей фона, здесь — доли объектов, поэтому напрямую числа не сравниваются. Но картина другая: "
          f"на реальных сценах пар без HE460 t03 ложные типы дают {_pct(S['without_he460_t03']['false_share_n'])} объектов, и главные из них — "
          "блик и облака, которых в MARIDA test почти нет. Суда (6 объектов) и кильватеры (1) — единицы.",
          f"- Доля помеченных пикселей воды на 22 вырезках — {_pct(T['px'] / max(T['area_px_by_quality']['water'], 1), 4)}. "
          "На MARIDA test неразмеченная вода помечается в 0.013 % пикселей. Уровни одного порядка, но на парах почти все эти пиксели "
          f"дают две сцены: HE460 t03 ({_pct(he['det_px_frac_water'], 3) if he else '—'}) и T16.", ""]
    # ---------------------------------------------------------------- 6. implications
    A = S["by_group"].get("accept") or T
    L += ["## 6. Что это значит для применения", "",
          "1. **Маски качества обязательны, но их недостаточно.** Решение по паре (облака, блик по медиане B11) верно отклонило T12–T16, T20, T21 "
          "и HE460 t01. Но попиксельная маска пропускает мелкие облака (< 100 пикс.) и бликующую воду с B11 0.01–0.03: "
          "такая вода считается «пригодной» (раздел 7.3).",
          "2. **Детекции на отклонённых сценах нельзя показывать как кандидатов.** На бликовых и облачных вырезках все объекты, кроме T16, — "
          "ложные типы: блик и облака. В полосах T14, T20, T21 — только ложные. Слой детекций должен показывать объекты только у пар, принятых по маскам. "
          "У отклонённых их нужно скрывать или рисовать серым с подписью «сцена отклонена: блик». Значок «1 объект в полосе» у отклонённой полосы вводит в заблуждение.",
          f"3. **На принятых сценах детектор без полевой проверки не отличает мусор от состояния моря.** "
          f"{_pct((A['share_n']['other_multi'] or 0) + (A['share_n']['other_single'] or 0), 0)} объектов на принятых вырезках — «остальное», "
          "и почти все они — белые одиночные пиксели HE460 t03, похожие на барашки. Число объектов на км² вырезки нельзя читать как плотность мусора.",
          f"4. **Гармонизация `water_median` — главный источник ложных срабатываний на L2A.** Без неё остаётся {n_noh} объектов вместо {T['n_obj']}, "
          "но пропадают и суда. Гармонизацию нужно либо проверить на размеченных L2A-сценах, либо показывать детекции L2A только как "
          "«подозрительные пиксели без проверки» — так сейчас и сделано. Подбирать сдвиг по этим парам нельзя: полевых меток на уровне пикселя нет.",
          "5. Итог «0 подтверждённых пар» не меняется. Статус `insufficient_data` у всех зон остаётся верным.", ""]
    # ---------------------------------------------------------------- 7. inconsistencies
    L += ["## 7. Несогласованности из проверки жюри и точные исправления", "",
          "Файлы `scripts/case/pair_quality.py` и `service/case_store.py` этим скриптом **не менялись**: у них другие владельцы. "
          "Ниже — точные правки. Их эффект посчитан в этом скрипте: поля `api_zone_raster`, `glint_patch`, "
          "`service_interim_filter_n_obj` в JSON.", ""]
    L += patch_md(rows, S)
    # ---------------------------------------------------------------- 8. gallery
    L += ["## 8. Кадры (`reports/case_pairs/review_img/`)", "",
          "Кадры отбирает правило `pick_frames`, а не человек. Панели: RGB | ложный цвет SWIR (B11, B8, B4) | "
          "контуры детекций по типам и полоса (пурпур) | маска качества. Растяжка яркости своя в каждом окне (1–99.5 %), "
          "поэтому шум тёмной воды выглядит сильнее, чем есть.", ""]
    for f in res["frames"]:
        if f.get("file"):
            L.append(f"- `{Path(f['file']).name}` — {f['title']}")
    L += ["", "## 9. Ограничения", "",
          "- Типы назначены правилами, не ручной разметкой. «Остальное» — не «мусор», а то, что правила не объяснили. "
          "Точность детектора на L2A этим разбором не измерена: для неё нужна ручная разметка 50–100 объектов.",
          "- Правило «мелкое облако» (избыток B11 объекта над водой в окне 31 пикс. ≥ 0.01) может сработать и на сухом корпусе лодки. "
          "Поэтому суда проверяются раньше, правилами `grid.artifacts`.",
          "- Тень облака в маске качества отдельно не выделена: SCL 3 входит в код 3.",
          "- Каналы перечитаны из STAC. Совпадение с пайплайном проверено: prob, пересчитанный с гармонизацией, совпал с `prob.tif` "
          "точно (разница 0) на 21 вырезке из 22, на HE460 t01 — до 4/255. Объекты совпали на всех 22.", ""]
    return L


def patch_md(rows, S):
    by = {r["dir"]: r for r in rows}
    he = by.get("S3_HE460_MarLitter_transect03", {})
    x = next((o for o in he.get("api_in_strip_objects", []) if not o["in_raster_strip"]), None)
    t21 = by.get("S4_DOORS3_T21", {})
    unchanged = all((r.get("glint_patch") or {}).get("decision_unchanged", True) for r in rows)
    h1 = by.get("S3_HE460_MarLitter_transect01", {})
    n_interim = sum(r.get("service_interim_filter_n_obj", 0) for r in rows)
    L = ["### 7.1. HE460 t03: 3 объекта в зоне и 4 в слое детекций", "",
         "- Зона (`/zones`, `n_objects` = `meta.detector.n_det`) считает объект «в полосе», если хотя бы один его пиксель лежит в **растровой** полосе. "
         "Растровая полоса — полигон полосы, растеризованный на сетке вырезки с `all_touched=True`, как в `pair_quality.py`.",
         "- Слой детекций (`case_store.zone_detections`) использует `in_strip = zone_polygon.intersects(контур объекта)` по **векторам** в WGS84. "
         "`intersects` считает и касание границы."]
    if x:
        L.append(f"- Лишний объект — пиксель (стр {x['row']}, кол {x['col']}), P {x['prob']}, в {x['dist_to_raster_strip_px']} пикс. "
                 "от растровой полосы. Квадрат пикселя касается границы полигона, но в растр полосы пиксель не попал.")
    L += ["- **Предлагаю одно определение:** «объект детектора в полосе» — связная компонента итоговой маски детектора, у которой хотя бы один пиксель "
          "лежит в растровой полосе (`rasterize(polygon, all_touched=True)` на сетке вырезки). На этих же пикселях считаются `coverage`, `valid_water_frac`, "
          "`cloud_frac`, `strip_area_km2` и `n_det`. Поэтому верное число — у зоны: 3 объекта, 4 пикселя, 400 м². "
          "Векторное `intersects` добавляет объекты, которые лишь касаются границы.",
          "- Проверено: если растеризовать полигон зоны из API на сетку вырезки (`all_touched=True`), объектов в полосе столько же, "
          f"сколько `n_det` в `meta.json`, на всех 22 вырезках (HE460 t03: {he.get('api_zone_raster', {}).get('n_in')}).", "",
          "Патч `service/case_store.py` (перезапуск pair_quality не нужен):", "", "```diff",
          "-def _detections_dir(d: str) -> list[dict]:",
          "+def _detections_dir(d: str, zone_geom: Optional[dict] = None) -> list[dict]:",
          "@@     def load(path):",
          "         det = np.nan_to_num(prob, nan=0.0) >= thr",
          "+        strip = None",
          "+        if zone_geom:  # одно правило «в полосе» с pair_quality: растровая полоса all_touched на сетке вырезки",
          "+            from rasterio.warp import transform_geom",
          "+            strip = features.rasterize([(transform_geom(\"EPSG:4326\", crs, zone_geom), 1)], out_shape=det.shape,",
          "+                                       transform=tr, all_touched=True, fill=0, dtype=\"uint8\").astype(bool)",
          "@@",
          "             out.append({\"k\": k, \"geometry\": gj, \"n_pixels\": int(px.sum()),",
          "+                        \"in_strip\": None if strip is None else bool(strip[px].any()),",
          "@@ def zone_detections(zone: dict) -> list[dict]:",
          "-    for i, x in enumerate(_detections_dir(d), 1):",
          "-        in_strip = None",
          "-        if zg is not None:",
          "-            try:",
          "-                in_strip = bool(zg.intersects(shape(x[\"geometry\"])))",
          "-            except Exception:",
          "-                in_strip = None",
          "+    for i, x in enumerate(_detections_dir(d, zone.get(\"geometry\")), 1):",
          "+        in_strip = x.get(\"in_strip\")",
          "```", "",
          "Кэш `_cached(f\"dets:{d}\", ...)` можно не трогать: у одной вырезки одна геометрия зоны. "
          "Проверку «Σ in_strip слоя = zone.n_objects» стоит добавить в `consistency_check.py`.", "",
          "### 7.2. Слой детекций: 1000 объектов вместо 975 (HE460 t01: 73 вместо 48)", "",
          "- `_detections_dir` оставляет только пиксели, красные в `mask.png`. Но `mask.png` уменьшен до 1600 пикс. по длинной стороне (`_save_png`). "
          "На вырезках больше 1600 пикс. (HE460 t01, HE460 t03) размеры не совпадают, и фильтр **молча не применяется**. "
          f"Поэтому у HE460 t01 в слое {h1.get('api', {}).get('n_det')} объектов, а в итоговой маске `pair_quality` — {h1.get('n_obj')}: "
          "в слой попадают объекты, которые `pair_quality` отбросил у облаков.",
          "- Патч `service/case_store.py`: вместо `mask.png` фильтровать по `quality.tif` и буферу облаков. Проверено: так получается "
          f"{n_interim} объектов, столько же, сколько в `meta.json` ({S['totals']['n_obj']}), на всех 22 вырезках. "
          "Фильтр теней на этих вырезках ничего не отбросил.", "", "```diff",
          "-        mp = base / \"mask.png\"",
          "-        if mp.is_file():",
          "-            a = np.asarray(Image.open(mp).convert(\"RGB\"))",
          "-            if a.shape[:2] == det.shape:",
          "-                det &= (a[..., 0] == 255) & (a[..., 1] == 40) & (a[..., 2] == 40)",
          "+        qp = base / \"quality.tif\"",
          "+        if qp.is_file():  # итоговая маска pair_quality: P >= thr на пригодной воде, без объектов в 5 пикс. от облака",
          "+            from scipy import ndimage",
          "+            from src.macroplastic.grid import cloudmask  # как остальные импорты src.* в case_store",
          "+            with rasterio.open(qp) as qs:",
          "+                qa = qs.read(1)",
          "+            det &= qa == 1",
          "+            lab0, n0 = ndimage.label(det, structure=np.ones((3, 3)))",
          "+            near = cloudmask.near_cloud_components(lab0, n0, np.isin(qa, (3, 4)),",
          "+                                                   int(meta[\"config\"][\"detector\"][\"cloud_buffer_px\"]))",
          "+            det = cloudmask.drop_components(lab0, n0, near)[0] > 0",
          "```", "",
          "Надёжнее в долгосрочной перспективе: пусть `pair_quality.py` сам пишет итоговую маску `det.tif` в полном разрешении, а `case_store` её векторизует. "
          "`cloudmask` зависит только от numpy и scipy и импортируется так же, как `src.macroplastic.case.*` в case_store.", "",
          "### 7.3. T21: причина «блик», а в карточке «пригодная вода 100 %»", "",
          "- Отказ по блику в `decide()` идёт по **медиане** B11 воды полосы > 0.01 (у T21 — 0.0165).",
          "- `glint_frac` и код 6 маски ставятся только когда бликует вся сцена (`glint_scene`: спектральный облачный тест B2 ≥ 0.06 и B11 ≥ 0.03 "
          "срабатывает на > 50 % воды). Вода с B11 0.01–0.03 остаётся в `water_ok`, поэтому `valid_water_frac` = 1.0, а `glint_frac` = 0.",
          f"- У T21 доля пикселей воды полосы с B11 > 0.01 — {t21.get('strip_water_b11_gt_thr_frac')}: бликует вся полоса.",
          "- Кроме того, в `case_store.zones_all` жёстко стоит `\"glint_fraction\": None`.", "",
          "Патч `scripts/case/pair_quality.py`, `process_s2` (правило решения то же, меняются только доли):", "", "```diff",
          "     qa[glint & ~scl_cloud] = Q_GLINT",
          "+    # блик по пикселю: тот же порог, что в правиле отказа (decision.glint_b11); такая вода не считается пригодной",
          "+    glint_px = water_ok & (np.nan_to_num(bands[10]) > gc[\"glint_b11\"])",
          "",
          "     ns = int(strip.sum())",
          "@@",
          "-             valid_water_frac=round(float((strip & water_ok).sum() / max(ns, 1)), 4),",
          "+             valid_water_frac=round(float((strip & water_ok & ~glint_px).sum() / max(ns, 1)), 4),",
          "@@",
          "-             glint_frac=round(float((strip & glint).sum() / nsv), 4), glint_scene=glint_scene,",
          "+             glint_frac=round(float((strip & (glint | glint_px)).sum() / nsv), 4), glint_scene=glint_scene,",
          "```", "",
          "`b11w` (медиана для правила), `det` (детектор на `water_ok`) и `quality.tif` не меняются. "
          "Код 6 в `quality.tif` намеренно не ставится: иначе фильтр слоя детекций по коду 1 из 7.2 разойдётся с детектором. "
          f"Проверено через `pair_quality.decide`: **решения и причины у всех 22 вырезок остаются прежними** ({'да' if unchanged else 'НЕТ'}). "
          "Меняются только доли. У T12–T15, T20, T21 и T3–T5: `valid_water_frac` 0.0, `glint_frac` 0.96–1.0. У T16: 0.074 и 0.926. У T11 `valid_water_frac` 0.9956. "
          "У HE460 t01 `valid_water_frac` падает с 0.10 до 0.023, причина остаётся «cloud». "
          "Нужен перезапуск `pair_quality.py --force --no-landsat` (сеть, около 22 × 15 с), затем `--summary-only` для csv.", "",
          "Патч `service/case_store.py`, `zones_all`:", "", "```diff",
          "-                        \"glint_fraction\": None, \"land_fraction\": _r(q.get(\"land_frac\")),",
          "+                        \"glint_fraction\": _r(q.get(\"glint_frac\")), \"land_fraction\": _r(q.get(\"land_frac\")),",
          "```", "",
          "Посчитан и вариант «убрать бликовые пиксели из детектора» (`det &= ~glint_px`). Он убирает все объекты в полосах T14, T20 и T21, "
          "но вместе с ними и суда: сухой корпус ярок в B11. Поэтому предлагаю не маскировать пиксели, а ввести правило на уровне сцены: "
          "детекции отклонённых пар не показывать как кандидатов (раздел 6, п. 2).", ""]
    return L


if __name__ == "__main__":
    main()
