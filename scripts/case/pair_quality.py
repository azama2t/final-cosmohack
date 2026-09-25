"""Quality masks + LightGBM detector on accepted event<->scene pairs (case criteria T1/T2).

  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/case/pair_quality.py            # all, resumable
  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/case/pair_quality.py --only S4:DOORS3:T1 --force
  .venv/Scripts/python.exe scripts/case/pair_quality.py --summary-only                      # csv + md from meta.json

Input: data/pairs/best_per_event.csv (scripts/case/find_pairs.py), task/macroplastic_marine_samples.csv (geometry), configs/case_pairs.yaml.
Geometry (configs/case_pairs.yaml geometry.use): S2/S3 strip = PANGAEA track segments (src/macroplastic/case/geometry.py)
+ buffer; interrupted transects are a MultiLineString, the gap is not part of the strip. Others: CSV line or point + buffer.
Per pair: data/pairs/quality/<event_id>/{rgb.png, quality.tif, quality.png, prob.tif, mask.png, meta.json}
(meta.json written last -> a pair with meta.json is done; rerun skips it). Table data/pairs/pair_quality.csv and
report reports/case_pairs/quality.md are rebuilt from all meta.json files.

Scenes: S2 L2A read with macroplastic.live.stac.read_crop (10 m grid, 20/60 m bands nearest). L1C pairs from find_pairs.py are
replaced by the Earth Search L2A item of the same tile/date (ES L1C COGs are jp2 in a requester-pays bucket; the
L2A of the same acquisition is the same geometry and time). Landsat C2 L2: QA_PIXEL only (no detector).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import traceback
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from macroplastic.live import stac  # noqa: E402
import rasterio  # noqa: E402
from rasterio.features import rasterize  # noqa: E402
from scipy import ndimage  # noqa: E402

PAIRS = ROOT / "data" / "pairs"
OUTQ = PAIRS / "quality"
REPORT = ROOT / "reports" / "case_pairs" / "quality.md"

# quality codes (quality.tif)
Q_NODATA, Q_WATER, Q_LAND, Q_CLOUD, Q_SPCLOUD, Q_OTHER = 0, 1, 2, 3, 4, 5
Q_GLINT = 6
Q_NAMES = {0: "nodata", 1: "valid water", 2: "land", 3: "cloud/shadow/cirrus (SCL 3,8,9,10)",
           4: "spectral cloud (grid.cloudmask)", 5: "other (water-edge buffer, dark/unclassified, defect)",
           6: "sun glint (scene-wide bright water: spectral-cloud test fires on most SCL-6 water, SCL clouds absent)"}
Q_COL = {0: (0, 0, 0), 1: (40, 90, 200), 2: (150, 120, 70), 3: (240, 240, 240), 4: (255, 170, 0), 5: (120, 120, 120),
         6: (255, 255, 120)}


def safe(eid: str) -> str:
    return eid.replace(":", "_").replace("/", "_")


def load_cfg(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def event_geometry(samples: pd.DataFrame, eid: str, cfg: dict | None = None) -> dict:
    s = samples[samples.event_id == eid]
    top = s[s.parent_sample_id.isna()] if s.parent_sample_id.isna().any() else s
    r = top.iloc[0]
    g = dict(sample_ids=";".join(s.sample_id.astype(str)), lat=float(r.latitude), lon=float(r.longitude),
             width_m=None if pd.isna(r.transect_width_m) else float(r.transect_width_m),
             length_km=None if pd.isna(r.transect_length_km) else float(r.transect_length_km),
             field_items_km2=None if pd.isna(r.concentration_items_km2) else float(r.concentration_items_km2),
             field_sample_id=str(r.sample_id), sampling_method=str(r.sampling_method))
    if all(pd.notna(r[k]) for k in ("lat_start", "lon_start", "lat_end", "lon_end")):
        g["line"] = [(float(r.lon_start), float(r.lat_start)), (float(r.lon_end), float(r.lat_end))]
    else:
        g["line"] = None
    g["lines"], g["geometry_status"], g["geometry_source"] = None, None, "samples_csv"
    gc = (cfg or {}).get("geometry") or {}
    if gc.get("use", True) and eid[:3] in ("S2:", "S3:"):
        from macroplastic.case.geometry import event_track
        t = event_track(eid, allow_approx=bool(gc.get("allow_approx", True)))
        if t is not None and t["geometry"]["type"] != "Point":
            # сегменты трека PANGAEA: полоса = сегменты + буфер, перерыв прерванной трансекты не покрывается
            g["lines"] = [[tuple(map(float, p)) for p in seg] for seg in
                          (t["geometry"]["coordinates"] if t["geometry"]["type"] == "MultiLineString"
                           else [t["geometry"]["coordinates"]])]
            g["lon"], g["lat"] = float(t["center"][0]), float(t["center"][1])
            g["geometry_status"], g["geometry_source"] = t["geometry_status"], "pangaea_track"
            g["n_segments"] = t["n_segments"]
    return g


def strip_polygon(g: dict, epsg: int, cfg: dict):
    from pyproj import Transformer
    from shapely.geometry import LineString, MultiLineString, Point
    tr = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    sc = cfg["strip"]
    half = max((g["width_m"] or 0) / 2, sc["min_half_width_m"])
    if g.get("lines"):  # трек PANGAEA (сегменты); перерыв между сегментами не входит в полосу
        segs = [[tr.transform(*p) for p in seg] for seg in g["lines"]]
        segs = [s_ for s_ in segs if LineString(s_).length >= 1.0]
        if segs:
            geom = LineString(segs[0]) if len(segs) == 1 else MultiLineString(segs)
            what = "transect line" if len(segs) == 1 else f"{len(segs)} transect segments (gap not covered)"
            return geom.buffer(half, cap_style=2), (f"{what} (PANGAEA track, {g.get('geometry_status')}) + buffer "
                                                    f"{half:.0f} m (width {g['width_m']} m / 2, min {sc['min_half_width_m']} m)")
    pts = [tr.transform(*p) for p in g["line"]] if g["line"] else None
    if pts and LineString(pts).length >= 1.0:  # degenerate line (start == end) -> point rule
        return LineString(pts).buffer(half, cap_style=2), f"transect line + buffer {half:.0f} m (width {g['width_m']} m / 2, min {sc['min_half_width_m']} m)"
    x, y = tr.transform(g["lon"], g["lat"])
    return Point(x, y).buffer(sc["point_buffer_m"]), f"point (no transect geometry in samples) + buffer {sc['point_buffer_m']} m"


def crop_box(poly, cfg: dict, snap: float = 60.0):
    c = cfg["strip"]["context_m"]
    w, s, e, n = poly.bounds
    w, s, e, n = w - c, s - c, e + c, n + c
    m = cfg["strip"]["max_crop_m"]
    cx, cy = (w + e) / 2, (s + n) / 2
    if e - w > m:
        w, e = cx - m / 2, cx + m / 2
    if n - s > m:
        s, n = cy - m / 2, cy + m / 2
    return [math.floor(w / snap) * snap, math.floor(s / snap) * snap, math.ceil(e / snap) * snap, math.ceil(n / snap) * snap]


def get_item(endpoint: str, coll: str, item_id: str, retries: int = 4):
    last = None
    for a in range(retries):
        try:
            cl = stac.open_client(endpoint)
            it = cl.get_collection(coll).get_item(item_id)
            if it is None:
                raise LookupError(f"item {item_id} not found in {endpoint}/{coll}")
            return it
        except LookupError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (a + 1))
    raise last


# ------------------------------------------------------------------------------------------ images

def _stretch_rgb(bands):
    rgb = bands[[3, 2, 1]]
    v = np.clip(np.nan_to_num(rgb) / 0.16, 0, 1) ** (1 / 1.8)
    return (v * 255).astype(np.uint8).transpose(1, 2, 0)


def _outline(strip: np.ndarray) -> np.ndarray:
    d = ndimage.binary_dilation(strip, iterations=3)
    return d & ~ndimage.binary_dilation(strip, iterations=1)


def _save_png(path: Path, img: np.ndarray, max_side: int = 1600):
    from PIL import Image
    im = Image.fromarray(img)
    k = max(im.size) / max_side
    if k > 1:
        im = im.resize((int(im.size[0] / k), int(im.size[1] / k)), Image.NEAREST)
    im.save(path, optimize=True)


def _write_tif(path, arr, transform, crs, dtype, desc):
    prof = dict(driver="GTiff", width=arr.shape[1], height=arr.shape[0], count=1, dtype=dtype, crs=crs,
                transform=transform, compress="deflate", tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr.astype(dtype)[None])
        dst.set_band_description(1, desc)


# ------------------------------------------------------------------------------------------ S2

def decide(q: dict, cfg: dict) -> tuple[str, str]:
    d = cfg["decision"]
    if q["coverage"] < d["min_coverage"]:
        return "reject", ("nodata" if q.get("in_footprint_nodata") else "insufficient_coverage")
    if q.get("glint_frac", 0) > d["max_cloud_frac"]:
        return "reject", "glint"
    if q["cloud_frac"] > d["max_cloud_frac"]:
        return "reject", "cloud"
    if q["land_frac"] > d["max_land_frac"]:
        return "reject", "land"
    if q["valid_water_frac"] < d["min_valid_water_frac"]:
        # dominant non-water class
        parts = {"cloud": q["cloud_frac"], "land": q["land_frac"], "nodata": 1 - q["coverage"]}
        return "reject", max(parts, key=parts.get) + "(valid_water<min)"
    if q.get("glint_b11_median") is not None and q["glint_b11_median"] > d["glint_b11"]:
        return "reject", "glint"
    return "accept", ""


def process_s2(row, g: dict, cfg: dict, pred, outdir: Path) -> dict:
    from macroplastic.grid import cloudmask
    endpoint, coll, item_id, note = row.endpoint, row.collection, row.item_id, ""
    if coll == "sentinel-2-l1c":
        item_id = item_id.replace("_L1C", "_L2A")
        endpoint, coll = "earth-search", "sentinel-2-l2a"
        note = f"L1C pair {row.item_id} replaced by L2A of the same acquisition {item_id} (ES L1C = requester-pays jp2)"
    item = get_item(endpoint, coll, item_id, cfg["network"]["retries"])
    epsg = stac.item_epsg(item)
    poly, strip_rule = strip_polygon(g, epsg, cfg)
    bounds = crop_box(poly, cfg)
    crop = stac.read_crop(item, epsg, bounds, workers=cfg["network"]["workers"])
    bands, scl, tr, crs = crop["bands"], crop["scl"], crop["transform"], crop["crs"]
    H, W = scl.shape
    strip = rasterize([(poly, 1)], out_shape=(H, W), transform=tr, all_touched=True, fill=0, dtype="uint8").astype(bool)

    valid = np.isfinite(bands).all(0) & (scl != 0)
    water = stac.water_mask(bands, scl, cloud_buffer_px=cfg["detector"]["cloud_buffer_px"]).astype(bool)
    spc = cloudmask.spectral_cloud(bands[1], bands[10], (scl == 6) & valid)
    scl_cloud = np.isin(scl, cloudmask.CLOUD_SCL)
    # glint vs cloud: the B2/B11 test of grid.cloudmask also fires on sun glint. If it covers most of the SCL-6 water of
    # the crop while Sen2Cor sees (almost) no cloud, the bright water is glint (uniform, whole crop), not cloud.
    w6 = (scl == 6) & valid
    gc = cfg["decision"]
    glint_scene = bool(w6.sum() and spc[w6].mean() > gc["glint_scene_spc_frac"] and scl_cloud[valid].mean() < gc["glint_scene_max_scl_cloud"])
    glint = spc & glint_scene
    spc = spc & (not glint_scene)
    cloud = (scl_cloud | spc) & valid
    land = np.isin(scl, (4, 5, 11)) & valid & ~cloud  # vegetation, bare, snow (Sen2Cor)
    water_ok = water & ~spc & ~glint & valid
    qa = np.full((H, W), Q_OTHER, np.uint8)
    qa[~valid] = Q_NODATA
    qa[water_ok] = Q_WATER
    qa[land] = Q_LAND
    qa[scl_cloud & valid] = Q_CLOUD
    qa[spc & ~scl_cloud] = Q_SPCLOUD
    qa[glint & ~scl_cloud] = Q_GLINT
    # pixel glint (reports/case_pairs/detector_review.md, 7.3): same threshold as the reject rule (decision.glint_b11); such water is not
    # counted as usable. Decision rule, b11w median, detector input (water_ok) and quality.tif codes are unchanged.
    glint_px = water_ok & (np.nan_to_num(bands[10]) > gc["glint_b11"])

    ns = int(strip.sum())
    nsv = max(int((strip & valid).sum()), 1)
    b11w = bands[10][strip & water_ok]
    q = dict(strip_px=ns, strip_area_km2=round(ns * 100 / 1e6, 4),
             coverage=round(float((strip & valid).sum() / max(ns, 1)), 4),
             valid_water_frac=round(float((strip & water_ok & ~glint_px).sum() / max(ns, 1)), 4),
             cloud_frac=round(float((strip & cloud).sum() / nsv), 4),
             scl_cloud_frac=round(float((strip & scl_cloud & valid).sum() / nsv), 4),
             spectral_cloud_frac=round(float((strip & spc).sum() / nsv), 4),
             land_frac=round(float((strip & land).sum() / nsv), 4),
             glint_frac=round(float((strip & (glint | glint_px)).sum() / nsv), 4), glint_scene=glint_scene,
             nodata_frac=round(float((strip & ~valid).sum() / max(ns, 1)), 4),
             glint_b11_median=round(float(np.nanmedian(b11w)), 4) if b11w.size >= 20 else None,
             crop_valid_frac=round(float(valid.mean()), 4),
             crop_water_frac=round(float(water_ok.mean()), 4))
    decision, reason = decide(q, cfg)

    # ----- detector on the whole crop
    t0 = time.time()
    prob = pred.predict_proba(bands, stac.BANDS, water_mask=(scl == 6))
    prob = np.nan_to_num(prob, nan=0.0)
    thr = float(pred.threshold)
    det = (prob >= thr) & water_ok
    lab, n = ndimage.label(det, structure=np.ones((3, 3), bool))
    n_raw = n
    drop = np.zeros(n, bool)
    if n:
        sizes = np.bincount(lab.ravel(), minlength=n + 1)[1:]
        drop |= sizes < cfg["detector"]["min_px"]
        drop |= cloudmask.near_cloud_components(lab, n, cloud, cfg["detector"]["cloud_buffer_px"])
        drop |= cloudmask.shadow_components(lab, n, bands[1], bands[2], bands[3], bands[7], water_ok)
    n_cloud_drop = int(drop.sum())
    lab, n = cloudmask.drop_components(lab, n, drop)
    det = lab > 0
    in_strip = np.unique(lab[strip & det])
    in_strip = in_strip[in_strip > 0]
    strip_water = strip & water_ok
    d = dict(threshold=thr, n_det_crop=int(n), n_det_crop_raw=int(n_raw), n_dropped_cloud_shadow=n_cloud_drop,
             det_px_crop=int(det.sum()), det_frac_water_crop=round(float(det.sum() / max(water_ok.sum(), 1)), 6),
             n_det=int(len(in_strip)), det_px_strip=int((det & strip).sum()),
             det_area_m2=int((det & strip).sum()) * 100,
             prob_max=round(float(prob[strip_water].max()), 4) if strip_water.any() else None,
             prob_p99_strip=round(float(np.percentile(prob[strip_water], 99)), 4) if strip_water.sum() >= 100 else None,
             prob_p99_crop_water=round(float(np.percentile(prob[water_ok], 99)), 4) if water_ok.sum() >= 100 else None,
             harmonize_offset=(np.asarray(pred.last_offset).round(5).tolist() if getattr(pred, "last_offset", None) is not None else None),
             runtime_s=round(time.time() - t0, 1))

    # ----- files
    outdir.mkdir(parents=True, exist_ok=True)
    _write_tif(outdir / "quality.tif", qa, tr, crs, "uint8", "quality code: " + json.dumps(Q_NAMES))
    _write_tif(outdir / "prob.tif", np.clip(np.round(prob * 255), 0, 255), tr, crs, "uint8", "P(marine debris)*255, lgbm")
    ol = _outline(strip)
    rgb = _stretch_rgb(bands)
    rgb_o = rgb.copy(); rgb_o[ol] = (255, 0, 255)
    _save_png(outdir / "rgb.png", rgb_o)
    qimg = np.zeros((H, W, 3), np.uint8)
    for k, c in Q_COL.items():
        qimg[qa == k] = c
    qimg[ol] = (255, 0, 255)
    _save_png(outdir / "quality.png", qimg)
    m = (rgb * 0.6).astype(np.uint8)
    dd = ndimage.binary_dilation(det, iterations=2)  # make 1-px detections visible after downscale
    m[dd] = (255, 40, 40)
    m[ol] = (255, 0, 255)
    _save_png(outdir / "mask.png", m)

    p = item.properties
    return dict(scene_id=item.id, scene_id_l59=row.item_id, source=f"{endpoint}/{coll}", level="L2A",
                scene_datetime=p["datetime"], tile=stac.tile_of(item), scene_cloud_cover=p.get("eo:cloud_cover"),
                epsg=epsg, bounds_utm=bounds, width=W, height=H, strip_rule=strip_rule,
                substitution_note=note, scale_offset_note=crop["scale_offset_note"], quality=q,
                decision=decision, reason=reason, detector=d,
                files=["rgb.png", "quality.tif", "quality.png", "prob.tif", "mask.png"])


# ------------------------------------------------------------------------------------------ Landsat (QA only)

def process_landsat(row, g: dict, cfg: dict, outdir: Path) -> dict:
    import planetary_computer
    from rasterio.windows import from_bounds
    item = get_item("planetary-computer", "landsat-c2-l2", row.item_id, cfg["network"]["retries"])
    item = planetary_computer.sign(item)
    href = item.assets["qa_pixel"].href
    with rasterio.open(href) as src:
        epsg = src.crs.to_epsg()
        poly, strip_rule = strip_polygon(g, epsg, cfg)
        b = crop_box(poly, cfg, snap=30.0)
        H, W = int(round((b[3] - b[1]) / 30)), int(round((b[2] - b[0]) / 30))
        qa_raw = src.read(1, window=from_bounds(*b, transform=src.transform), out_shape=(H, W), boundless=True, fill_value=1)
        crs = src.crs
    # sanity check of QA cloud: SR red (scale 2.75e-5, offset -0.2); open water < ~0.03, clouds >> 0.1
    with rasterio.open(item.assets["red"].href) as src:
        red = src.read(1, window=from_bounds(*b, transform=src.transform), out_shape=(H, W), boundless=True, fill_value=0)
    red = np.where(red > 0, red * 2.75e-5 - 0.2, np.nan)
    from rasterio.transform import from_origin
    tr = from_origin(b[0], b[3], 30, 30)
    strip = rasterize([(poly, 1)], out_shape=(H, W), transform=tr, all_touched=True, fill=0, dtype="uint8").astype(bool)
    bit = lambda k: (qa_raw >> k) & 1 == 1  # noqa: E731
    fill = bit(0)
    cloud = (bit(1) | bit(2) | bit(3) | bit(4)) & ~fill
    water = bit(7) & ~cloud & ~fill
    land = bit(6) & ~bit(7) & ~fill & ~cloud
    valid = ~fill
    qa = np.full((H, W), Q_OTHER, np.uint8)
    qa[fill] = Q_NODATA; qa[water] = Q_WATER; qa[land] = Q_LAND; qa[cloud] = Q_CLOUD
    ns = int(strip.sum()); nsv = max(int((strip & valid).sum()), 1)
    q = dict(strip_px=ns, strip_area_km2=round(ns * 900 / 1e6, 4),
             coverage=round(float((strip & valid).sum() / max(ns, 1)), 4),
             valid_water_frac=round(float((strip & water).sum() / max(ns, 1)), 4),
             cloud_frac=round(float((strip & cloud).sum() / nsv), 4),
             land_frac=round(float((strip & land).sum() / nsv), 4),
             nodata_frac=round(float((strip & fill).sum() / max(ns, 1)), 4), glint_b11_median=None)
    rs = red[strip & valid]
    q["red_sr_median_strip"] = round(float(np.nanmedian(rs)), 4) if np.isfinite(rs).any() else None
    rc = red[strip & cloud]
    q["red_sr_median_qa_cloud_strip"] = round(float(np.nanmedian(rc)), 4) if np.isfinite(rc).any() else None
    decision, reason = decide(q, cfg)
    if reason == "cloud" and q["red_sr_median_qa_cloud_strip"] is not None and q["red_sr_median_qa_cloud_strip"] < 0.03:
        reason = "cloud(qa_suspect:red_sr<0.03)"  # QA_PIXEL says cloud, reflectance says dark open water
    outdir.mkdir(parents=True, exist_ok=True)
    _write_tif(outdir / "quality.tif", qa, tr, crs, "uint8", "quality code (Landsat QA_PIXEL): " + json.dumps(Q_NAMES))
    qimg = np.zeros((H, W, 3), np.uint8)
    for k, c in Q_COL.items():
        qimg[qa == k] = c
    qimg[_outline(strip)] = (255, 0, 255)
    _save_png(outdir / "quality.png", qimg)
    p = item.properties
    return dict(scene_id=item.id, scene_id_l59=row.item_id, source="planetary-computer/landsat-c2-l2", level="L2SP",
                scene_datetime=p["datetime"], scene_cloud_cover=p.get("eo:cloud_cover"), epsg=epsg, bounds_utm=b,
                width=W, height=H, strip_rule=strip_rule + "; 30 m QA_PIXEL grid",
                substitution_note="Landsat: QA_PIXEL mask only (bits 0 fill, 1-4 dilated cloud/cirrus/cloud/shadow, 6 clear, 7 water); detector not run (trained on S2 bands)",
                quality=q, decision=decision, reason=reason, detector=None, files=["quality.tif", "quality.png"])


# ------------------------------------------------------------------------------------------ summary

COLS = ["event_id", "sample_ids", "scene_id", "source", "scene_datetime", "dt_hours", "coverage", "valid_water_frac",
        "cloud_frac", "land_frac", "decision", "reason", "n_det", "det_area_m2", "prob_max"]


def build_table() -> pd.DataFrame:
    rows = []
    for mf in sorted(OUTQ.glob("*/meta.json")):
        m = json.loads(mf.read_text(encoding="utf-8"))
        q, d = m.get("quality") or {}, m.get("detector") or {}
        rows.append(dict(event_id=m["event_id"], sample_ids=m["sample_ids"], scene_id=m.get("scene_id"),
                         source=m.get("source"), scene_datetime=m.get("scene_datetime"), dt_hours=m.get("dt_hours"),
                         coverage=q.get("coverage"), valid_water_frac=q.get("valid_water_frac"),
                         cloud_frac=q.get("cloud_frac"), glint_frac=q.get("glint_frac"), land_frac=q.get("land_frac"), decision=m.get("decision"),
                         reason=m.get("reason"), n_det=d.get("n_det"), det_area_m2=d.get("det_area_m2"),
                         prob_max=d.get("prob_max"),
                         # extra columns after the required ones
                         field_items_km2=m.get("field_items_km2"), strip_area_km2=q.get("strip_area_km2"),
                         glint_b11_median=q.get("glint_b11_median"), spectral_cloud_frac=q.get("spectral_cloud_frac"),
                         n_det_crop=d.get("n_det_crop"), det_frac_water_crop=d.get("det_frac_water_crop"),
                         prob_p99_strip=d.get("prob_p99_strip"), level=m.get("level"), dir=mf.parent.name))
    t = pd.DataFrame(rows)
    if len(t):
        t.to_csv(PAIRS / "pair_quality.csv", index=False)
    return t


def write_report(t: pd.DataFrame, n_total: int, cfg: dict):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    L = ["# Маски качества и детектор на принятых парах «событие ↔ сцена»", "",
         f"Скрипт `scripts/case/pair_quality.py`, пороги `configs/case_pairs.yaml`. Вход: `data/pairs/best_per_event.csv` "
         f"(реестр пар `scripts/case/find_pairs.py`, {n_total} событий с принятой парой). Таблица: `data/pairs/pair_quality.csv`. Картинки: "
         "`data/pairs/quality/<event_id>/` (двоеточия в id заменены на `_`).", ""]
    if not len(t):
        REPORT.write_text("\n".join(L + ["Пока ни одна пара не обработана."]), encoding="utf-8")
        return
    acc = t[t.decision == "accept"]
    L += [f"**Обработано {len(t)} из {n_total} пар; прошли маски: {len(acc)}; отклонены: {int((t.decision == 'reject').sum())}; "
          f"ошибки чтения: {int((t.decision == 'error').sum())}.**", "",
          "Пороги: покрытие полосы ≥ {min_coverage}, облака ≤ {max_cloud_frac}, суша ≤ {max_land_frac}, валидная вода ≥ "
          "{min_valid_water_frac}, блик: медиана B11 по воде полосы > {glint_b11}.".format(**cfg["decision"]), "",
          "## Решения по источникам", "", "| источник | всего | accept | reject |", "|---|---:|---:|---:|"]
    for s, g in t.groupby("source"):
        L.append(f"| {s} | {len(g)} | {int((g.decision == 'accept').sum())} | {int((g.decision == 'reject').sum())} |")
    L += ["", "## Причины отказа", "", "| причина | пар |", "|---|---:|"]
    for r, k in t[t.decision != "accept"].reason.value_counts().items():
        L.append(f"| {r} | {k} |")
    L += ["", "## Все пары", "",
          "| событие | сцена | Δt, ч | покрытие | вода | облака | суша | B11 воды | решение | пятен в полосе | площадь, м² | p max | поле, предм./км² |",
          "|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|"]
    f = lambda v, n=2: "—" if v is None or (isinstance(v, float) and np.isnan(v)) else (f"{v:.{n}f}" if isinstance(v, float) else str(v))  # noqa: E731
    for r in t.sort_values(["decision", "event_id"]).itertuples():
        L.append(f"| {r.event_id} | {r.scene_id} | {f(r.dt_hours, 1)} | {f(r.coverage)} | {f(r.valid_water_frac)} | "
                 f"{f(r.cloud_frac)} | {f(r.land_frac)} | {f(r.glint_b11_median, 4)} | {r.decision} {r.reason or ''} | "
                 f"{f(r.n_det, 0)} | {f(r.det_area_m2, 0)} | {f(r.prob_max)} | {f(r.field_items_km2, 1)} |")
    s2 = t[t.n_det.notna()]
    ex = pd.concat([acc[acc.n_det.notna()].sort_values("n_det", ascending=False).head(3),
                    t[(t.decision == "reject") & t.n_det.notna() & (t.reason == "cloud")].head(1),
                    t[t.event_id.isin(["S4:DOORS3:T21", "S4:DOORS3:T3"])]])
    L += ["", "## Примеры (RGB с контуром полосы наблюдения — пурпурный; маска детектора — красный; качество: синий вода, "
          "коричневый суша, белый облака SCL, оранжевый спектральные облака, серый прочее, чёрный нет данных)", ""]
    for r in ex.itertuples():
        rel = f"../../data/pairs/quality/{r.dir}"
        L += [f"### {r.event_id} — {r.decision} {r.reason or ''}", "",
              f"![rgb]({rel}/rgb.png) ![quality]({rel}/quality.png) ![mask]({rel}/mask.png)", ""]
    if len(s2):
        a2 = s2[s2.decision == "accept"]
        L += ["## Что видит детектор на 10 м (автоматическая сводка; вывод — в отчёте задачи)", "",
              f"- Пар S2 с запуском детектора: {len(s2)}; из них принятых масками: {len(a2)}.",
              f"- Принятых пар с ≥1 пятном в полосе: {int((a2.n_det > 0).sum())}; суммарно пятен {int(a2.n_det.sum()) if len(a2) else 0}, "
              f"площадь {int(a2.det_area_m2.sum()) if len(a2) else 0} м².",
              f"- Доля воды вырезки выше порога (медиана по принятым парам): "
              f"{a2.det_frac_water_crop.median() if len(a2) else float('nan'):.2e}.",
              "- Сравнение с фоном: ожидаемое по случайности число пикселей-детекций в полосе = доля воды вырезки выше "
              "порога × пикселей валидной воды полосы; по принятым парам сумма наблюдаемых "
              f"{int((a2.det_area_m2 / 100).sum()) if len(a2) else 0} px против ожидаемых "
              f"{float((a2.det_frac_water_crop * a2.valid_water_frac * a2.strip_area_km2 * 1e4).sum()) if len(a2) else 0:.1f} px.",
              "- Полевые плотности трансект: десятки–сотни предметов >2 см на км² (медиана по принятым парам "
              f"{a2.field_items_km2.median() if len(a2) else float('nan'):.0f} предм./км²), т. е. на пиксель 10×10 м (1e-4 км²) "
              "приходится в среднем ≪1 предмета; пиксельный сигнал от такого мусора не ожидается. Совпадение/несовпадение "
              "детекций с полевыми точками — не проверка концентрации.", ""]
    L += ["## Вывод (честно)", "",
          "- Маски отсекают больше половины пар: главная причина на Чёрном море 2–7 июня 2024 — солнечный блик "
          "(у сцен облачность ~0 %, но вода яркая: медиана B11 0.012–0.024, а на T3–T5 блик на всю вырезку и "
          "спектральный облачный тест grid.cloudmask срабатывает на 100 % воды — такие сцены помечены glint, а не cloud).",
          "- Детекции LightGBM, попавшие в полосу наблюдения, есть только в двух бликовых сценах (T14, T21; p до 0.97) и "
          "в одной чистой (Северное море, HE460 transect03: 3 пятна по 1–2 пикселя на полосе 25 км, при фоне 724 пятна "
          "в вырезке — число в полосе на уровне случайного). На 10 принятых черноморских парах в полосе детекций нет.",
          "- Это согласуется с физикой: полевые плотности — десятки–сотни предметов >2 см на км², а пиксель 10 м видит "
          "только скопления (линии/пятна плавающего материала площадью от долей пикселя с высоким покрытием). "
          "Отсутствие детекций не означает отсутствия мусора; наличие детекции не даёт концентрации.",
          "- Landsat: решение только по QA_PIXEL; на 4 из 6 отклонённых «облачных» сценах отражение red в полосе < 0.03 "
          "(тёмная открытая вода), т. е. флаг облака QA_PIXEL сомнителен — помечено `qa_suspect`.", ""]
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "configs" / "case_pairs.yaml"))
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-landsat", action="store_true")
    ap.add_argument("--summary-only", action="store_true")
    ap.add_argument("--min-coverage", type=float); ap.add_argument("--max-cloud", type=float)
    ap.add_argument("--max-land", type=float); ap.add_argument("--min-water", type=float)
    a = ap.parse_args()
    cfg = load_cfg(Path(a.config))
    for k, v in (("min_coverage", a.min_coverage), ("max_cloud_frac", a.max_cloud), ("max_land_frac", a.max_land),
                 ("min_valid_water_frac", a.min_water)):
        if v is not None:
            cfg["decision"][k] = v
    best = pd.read_csv(PAIRS / "best_per_event.csv")
    if not a.summary_only:
        samples = pd.read_csv(ROOT / "task" / "macroplastic_marine_samples.csv", low_memory=False)
        from macroplastic.models.lgbm_predict import load_predictor
        pred = load_predictor(ROOT / cfg["detector"]["weights"], harmonize=cfg["detector"]["harmonize"])
        order = best.assign(_ls=best.collection.str.startswith("landsat")).sort_values(["_ls", "collection", "dt_hours"])
        for row in order.itertuples():
            if a.only and row.event_id not in a.only:
                continue
            is_ls = row.collection.startswith("landsat")
            if is_ls and a.no_landsat:
                continue
            outdir = OUTQ / safe(row.event_id)
            if (outdir / "meta.json").exists() and not a.force:
                continue
            t0 = time.time()
            g = event_geometry(samples, row.event_id, cfg)
            base = dict(event_id=row.event_id, sample_ids=g["sample_ids"], field_sample_id=g["field_sample_id"],
                        field_items_km2=g["field_items_km2"], sampling_method=g["sampling_method"],
                        transect_width_m=g["width_m"], transect_length_km=g["length_km"],
                        has_line=bool(g.get("lines") or g["line"]),
                        geometry_source=g["geometry_source"], geometry_status=g["geometry_status"],
                        obs_datetime=row.obs_datetime, time_known=bool(row.time_known), dt_hours=float(row.dt_hours),
                        config=cfg)
            try:
                res = process_landsat(row, g, cfg, outdir) if is_ls else process_s2(row, g, cfg, pred, outdir)
            except Exception as e:  # noqa: BLE001
                res = dict(scene_id=row.item_id, source=f"{row.endpoint}/{row.collection}", decision="error",
                           reason=f"read_error:{type(e).__name__}", error=traceback.format_exc()[-1500:])
                print(f"[error] {row.event_id}: {e}", flush=True)
            base.update(res)
            base["elapsed_s"] = round(time.time() - t0, 1)
            base["created"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            outdir.mkdir(parents=True, exist_ok=True)
            (outdir / "meta.json").write_text(json.dumps(base, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
            q = base.get("quality") or {}
            d = base.get("detector") or {}
            print(f"{row.event_id}: {base['decision']} {base.get('reason')} cov={q.get('coverage')} water={q.get('valid_water_frac')} "
                  f"cloud={q.get('cloud_frac')} n_det={d.get('n_det')} pmax={d.get('prob_max')} {base['elapsed_s']} s", flush=True)
            t = build_table()
            write_report(t, len(best), cfg)
    t = build_table()
    write_report(t, len(best), cfg)
    if len(t):
        print(t.decision.value_counts().to_dict(), t[t.decision != "accept"].reason.value_counts().to_dict())


if __name__ == "__main__":
    main()
