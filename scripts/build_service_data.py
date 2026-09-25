"""Build the service data layer (docs/CONTRACTS.md) from lane-L6 live scenes.

Usage:
    python scripts/build_service_data.py --live-dir data/live --out service/data [--regions a,b] [--models mdd,lgbm]
        [--thresholds mdd=0.3]

Input  (per scene): <live>/<region>/<date>/{scene.json, water_mask.tif, scl.tif, rgb.png, rgb.json,
                    prob_<model>.tif, prob_<model>.json, [drift.json]}
Output: <out>/manifest.json, <out>/<region>/timeseries.json, <out>/<region>/<date>/{rgb.png, rgb.json, [drift.json]},
        <out>/<region>/<date>/<model>/{prob.png, prob.tif, detections.geojson, h3.geojson, zones.json}

rgb.png / prob.png are warped to a regular lon/lat grid over `bounds` (so the map image overlay is aligned),
<= 2048 px per side. prob.tif is the model raster on the scene UTM grid (uint8 = P*255, no nodata).
L15 "confirmed" detections: on dates with >= 2 models every detection gets `confirmed` (bool) and `confirmed_by`
(partner model or null): the other model has a pixel >= its threshold on observed water within 2 px (20 m) of the
object (macroplastic.grid.confirm). Also manifest dates[].n_confirmed {model: k} and zones[].n_confirmed.
L28 cloud guards (macroplastic.grid.cloudmask, reports/cloud_edge.md), same for all scenes: bright+SWIR-bright
water pixels missed by SCL (B2 >= 0.06 & B11 >= 0.03, blobs >= 100 px) are not observed; components within 5 px
(Euclidean) of a cloud/shadow (SCL 3, 8, 9, 10 or spectral cloud) are dropped; cloud-shadow artefacts (no NIR
excess over the 3..10 px water ring AND ring visible brightness < 0.8 x scene water median) are dropped.
Idempotent: region folders being built are recreated; with --regions, other regions of an existing
manifest are kept.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject, transform_bounds
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from macroplastic.grid import H3_RES  # noqa: E402
from macroplastic.grid.cloudmask import (CLOUD_BUFFER_PX, drop_components, near_cloud_components,  # noqa: E402
                                         shadow_components, spectral_cloud)
from macroplastic.grid.confirm import CONFIRM_RADIUS_PX, confirmed_components, label_of_records  # noqa: E402
from macroplastic.grid.h3index import cell_raster, h3_feature_collection, h3_stats, raster_bounds  # noqa: E402
from macroplastic.grid.timeseries import sort_rows, timeseries_row  # noqa: E402
from macroplastic.grid.vectorize import clean_mask, dumps_compact, fit_geojson_size, vectorize_detections  # noqa: E402
from macroplastic.grid.zones import rank_zones  # noqa: E402

MAX_PX = 2048
THUMB_PX, THUMB_MAX_BYTES = 256, 30_000
EDGE_PX = 4  # pixels next to the cutout border / nodata are not "observed" (tiling/edge artefacts)
PROB_MIN_VISIBLE = 0.05
MODEL_INFO = {
    "mdd": {"name": "marinedebrisdetector (UNet++)", "url": "https://github.com/MarcCoru/marinedebrisdetector",
            "license": "MIT"},
    "lgbm": {"name": "LightGBM (наша, MARIDA)", "note": "обучена на ACOLITE rhorc; на L2A — сдвиг домена"},
}
COUNTRY = {"honduras": "Гондурас", "haiti": "Гаити", "durban": "ЮАР", "manila": "Филиппины",
           "santo_domingo": "Доминиканская Республика", "danang": "Вьетнам", "da_nang": "Вьетнам",
           "bali": "Индонезия", "scotland": "Великобритания", "jakarta": "Индонезия", "accra": "Гана",
           "lagos": "Нигерия", "tiber": "Италия"}
NAME_RU = {"honduras": "Гондурасский залив", "haiti": "Гаити", "durban": "Дурбан", "manila": "Манильский залив",
           "santo_domingo": "Санто-Доминго", "danang": "Дананг", "da_nang": "Дананг", "bali": "Бали",
           "scotland": "Шотландия", "jakarta": "Джакартский залив", "accra": "Аккра", "lagos": "Лагос",
           "tiber": "Устье Тибра"}
SOURCES = [
    {"name": "Copernicus Sentinel-2 L2A (содержит модифицированные данные Copernicus Sentinel)",
     "url": "https://dataspace.copernicus.eu", "license": "Copernicus open licence (free, full and open)"},
    {"name": "Earth Search (Element 84), коллекция sentinel-2-l2a на AWS", "url": "https://earth-search.aws.element84.com/v1",
     "license": "данные Copernicus Sentinel; каталог — открытый доступ"},
    {"name": "marinedebrisdetector (Rußwurm et al., 2023)", "url": "https://github.com/MarcCoru/marinedebrisdetector",
     "license": "MIT"},
    {"name": "MARIDA: Marine Debris Archive (Kikaki et al., 2022)", "url": "https://zenodo.org/records/5151941",
     "license": "CC BY 4.0"},
    {"name": "H3 (Uber) — гексагональная сетка", "url": "https://h3geo.org", "license": "Apache-2.0"},
]
CLOUD_SCL = (3, 8, 9, 10)
RETRIES, RETRY_WAIT_S = 3, 30  # input scene may be mid-write by lane L6
THR_OVERRIDE: dict[str, float] = {}  # --thresholds mdd=0.3,lgbm=0.5  # cloud shadow, cloud medium/high, thin cirrus


def read_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(p: Path, obj, compact: bool = False):
    p.parent.mkdir(parents=True, exist_ok=True)
    text = dumps_compact(obj) if compact else json.dumps(obj, ensure_ascii=False, indent=1)
    p.write_text(text, encoding="utf-8")


def read_band(p: Path):
    with rasterio.open(p) as ds:
        return ds.read(1), ds.transform, ds.crs, ds.profile


# ---------------------------------------------------------------- display rasters (lon/lat grid)
def display_grid(bounds, src_shape):
    w, s, e, n = bounds
    lat = math.radians((s + n) / 2)
    aspect = ((e - w) * math.cos(lat)) / max(n - s, 1e-12)  # width / height in metres
    long_side = min(MAX_PX, max(src_shape))
    if aspect >= 1:
        W, H = long_side, max(1, round(long_side / aspect))
    else:
        W, H = max(1, round(long_side * aspect)), long_side
    return W, H, from_bounds(w, s, e, n, W, H)


def warp(src, src_transform, src_crs, dst_shape, dst_transform, resampling, dst_dtype=None):
    dst = np.zeros(dst_shape, dst_dtype or src.dtype)
    reproject(src, dst, src_transform=src_transform, src_crs=src_crs, dst_transform=dst_transform,
              dst_crs="EPSG:4326", resampling=resampling, src_nodata=None, dst_nodata=0)
    return dst


PAL_LOW = np.array([[0x2b, 0x6c, 0xb0], [0xb7, 0x94, 0xf4], [0xf6, 0xad, 0x55]], np.float32)  # CONTRACTS 03:35
PAL_ALPHA = (40.0, 170.0)
PAL_HI = (0xff, 0x6b, 0x4a, 230)


def prob_palette(p: np.ndarray, threshold: float) -> np.ndarray:
    """Fixed palette (docs/CONTRACTS.md, additions 03:35): P < 0.05 transparent; 0.05 <= P < threshold ramp
    #2b6cb0 (alpha 40) -> #b794f4 -> #f6ad55 (alpha 170); P >= threshold accent #ff6b4a (alpha 230)."""
    rgba = np.zeros(p.shape + (4,), np.uint8)
    low = (p >= PROB_MIN_VISIBLE) & (p < threshold)
    t = np.clip((p[low] - PROB_MIN_VISIBLE) / max(threshold - PROB_MIN_VISIBLE, 1e-6), 0, 1)
    seg = np.minimum((t * 2).astype(int), 1)
    u = (t * 2 - seg)[:, None]
    rgb = PAL_LOW[seg] * (1 - u) + PAL_LOW[seg + 1] * u
    a = PAL_ALPHA[0] + (PAL_ALPHA[1] - PAL_ALPHA[0]) * t
    rgba[low] = np.column_stack([rgb, a]).round().astype(np.uint8)
    rgba[p >= threshold] = PAL_HI
    return rgba


def save_thumb(rgba: np.ndarray, path: Path, size: int = THUMB_PX, max_bytes: int = THUMB_MAX_BYTES):
    """rgb preview, 256 px long side, JPEG <= 30 KB; transparent parts on the dark map background."""
    im = Image.fromarray(rgba, "RGBA")
    k = size / max(im.size)
    im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
    bg = Image.new("RGBA", im.size, (11, 19, 32, 255))
    im = Image.alpha_composite(bg, im).convert("RGB")
    for q in (85, 75, 65, 55, 45, 35):
        im.save(path, "JPEG", quality=q, optimize=True)
        if path.stat().st_size <= max_bytes:
            break


def rgb_display(scene_dir: Path, s_transform, s_crs, s_shape):
    """Return (rgba uint8 HxWx4, bounds [w,s,e,n], dst_transform) on a regular lon/lat grid.

    If L6's rgb.json says EPSG:4326 the PNG already is that grid: reuse its bounds (downscale to <= 2048).
    Otherwise rgb.png is assumed to cover the scene UTM grid (possibly downscaled) and is warped."""
    rj = read_json(scene_dir / "rgb.json") if (scene_dir / "rgb.json").exists() else {}
    im = Image.open(scene_dir / "rgb.png").convert("RGBA")
    if str(rj.get("crs", "")).upper() == "EPSG:4326" and rj.get("bounds"):
        bounds = [round(float(v), 6) for v in rj["bounds"]]
        if max(im.size) > MAX_PX:
            k = MAX_PX / max(im.size)
            im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
        return np.asarray(im), bounds, from_bounds(*bounds, im.width, im.height)
    bounds = [round(v, 6) for v in transform_bounds(s_crs, "EPSG:4326", *raster_bounds(s_transform, s_shape),
                                                    densify_pts=21)]
    W, H, d_transform = display_grid(bounds, s_shape)
    img = np.asarray(im)
    src_t = s_transform * s_transform.scale(s_shape[1] / img.shape[1], s_shape[0] / img.shape[0])
    out = np.zeros((H, W, 4), np.uint8)
    for c in range(4):
        out[..., c] = warp(img[..., c], src_t, s_crs, (H, W), d_transform,
                           Resampling.nearest if c == 3 else Resampling.bilinear)
    return out, bounds, d_transform


# ---------------------------------------------------------------- per scene
def discover(live: Path, regions, models):
    found = {}
    for sj in sorted(live.glob("*/*/scene.json")):
        d = sj.parent
        region, date = d.parent.name, d.name
        if regions and region not in regions:
            continue
        ms = sorted(p.stem[len("prob_"):] for p in d.glob("prob_*.tif"))
        if models:
            ms = [m for m in ms if m in models]
        if not ms:
            print(f"  skip {region}/{date}: no prob_<model>.tif")
            continue
        found.setdefault(region, []).append((date, d, ms))
    return found


def edge_zone(scene_dir: Path, shape, width: int) -> np.ndarray:
    """True within `width` px of the raster border or of nodata (SCL == 0)."""
    z = np.zeros(shape, bool)
    if width <= 0:
        return z
    z[:width], z[-width:], z[:, :width], z[:, -width:] = True, True, True, True
    if (scene_dir / "scl.tif").exists():
        scl, *_ = read_band(scene_dir / "scl.tif")
        if scl.shape == shape and (scl == 0).any():
            z |= ndimage.binary_dilation(scl == 0, structure=np.ones((3, 3), bool), iterations=width)
    return z


def cloud_fraction(scene_dir: Path, scene: dict, shape):
    if (scene_dir / "scl.tif").exists():
        scl, *_ = read_band(scene_dir / "scl.tif")
        if scl.shape == shape:
            valid = scl != 0
            if valid.any():
                return float(np.isin(scl[valid], CLOUD_SCL).mean())
    cc = scene.get("cloud_cover")
    if cc is None:
        return None
    return float(cc) / 100.0 if float(cc) > 1 else float(cc)


def process_scene(region: str, date: str, sdir: Path, models: list[str], out: Path, log) -> dict:
    t0 = time.time()
    scene = read_json(sdir / "scene.json")
    water, s_transform, s_crs, _ = read_band(sdir / "water_mask.tif")
    water = water == 1
    shape = water.shape
    edge = edge_zone(sdir, shape, EDGE_PX)
    water &= ~edge  # edge band is not observed: no detections there, not counted in observed_water_px
    if (sdir / "scl.tif").exists():  # ships/platforms: hole-filling in water_mask turns their SCL 4/5 pixels into water
        scl, *_ = read_band(sdir / "scl.tif")
        if scl.shape == shape:
            water &= ~np.isin(scl, (4, 5))
    cloud_px = np.zeros(shape, bool)
    if (sdir / "scl.tif").exists():
        scl, *_ = read_band(sdir / "scl.tif")
        if scl.shape == shape:
            cloud_px |= np.isin(scl, CLOUD_SCL)
    bands = None
    if (sdir / "bands.tif").exists():  # L28: B2, B3, B4, B8, B11 for the spectral cloud mask and the shadow test
        with rasterio.open(sdir / "bands.tif") as ds:
            names = list(ds.descriptions)
            want = ("B2", "B3", "B4", "B8", "B11")
            if ds.shape == shape and all(b in names for b in want):
                bands = {b: ds.read(names.index(b) + 1) for b in want}
    n_spec = 0
    if bands is not None:
        spec = spectral_cloud(bands["B2"], bands["B11"], water)
        n_spec = int(spec.sum())
        water &= ~spec
        cloud_px |= spec
    rgba, bounds, d_transform = rgb_display(sdir, s_transform, s_crs, shape)
    H, W = rgba.shape[:2]
    odir = out / region / date
    odir.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(odir / "rgb.png", optimize=True)
    save_thumb(rgba, odir / "thumb.jpg")
    cloud = cloud_fraction(sdir, scene, shape)
    write_json(odir / "rgb.json", {"bounds": bounds, "width": W, "height": H, "crs": str(s_crs),
                                   "image_crs": "EPSG:4326", "scene_id": scene.get("scene_id", ""),
                                   "cloud_frac": None if cloud is None else round(cloud, 4)})
    drift = None
    if (sdir / "drift.json").exists():
        shutil.copyfile(sdir / "drift.json", odir / "drift.json")
        drift = f"{region}/{date}/drift.json"
    cellr, cells = cell_raster(s_transform, s_crs, shape, H3_RES)
    res = {"date": date, "scene": scene, "bounds": bounds, "cloud": cloud, "drift": drift, "models": {}}
    masks: dict[str, tuple] = {}
    for m in models:
        prob, p_transform, p_crs, prof = read_band(sdir / f"prob_{m}.tif")
        if prob.shape != shape or p_transform != s_transform:
            log(f"  !! {region}/{date}/{m}: prob grid differs from water_mask grid — skipped")
            continue
        prob = prob.astype(np.uint8)
        pj = read_json(sdir / f"prob_{m}.json") if (sdir / f"prob_{m}.json").exists() else {}
        thr = float(THR_OVERRIDE.get(m, pj.get("threshold", 0.5)))
        mdir = odir / m
        mdir.mkdir(exist_ok=True)
        # prob.tif: model raster on scene grid, no nodata
        with rasterio.open(mdir / "prob.tif", "w", driver="GTiff", height=shape[0], width=shape[1], count=1,
                           dtype="uint8", crs=s_crs, transform=s_transform, compress="deflate", tiled=True,
                           blockxsize=256, blockysize=256) as dst:
            dst.write(prob, 1)
        # prob.png: observed water only, max-resampled so 1-2 px detections stay visible
        pd = warp(np.where(water, prob, 0).astype(np.uint8), s_transform, s_crs, (H, W), d_transform,
                  Resampling.max if max(shape) > max(W, H) else Resampling.nearest)
        Image.fromarray(prob_palette(pd.astype(np.float32) / 255.0, thr), "RGBA").save(mdir / "prob.png", optimize=True)
        # detections (detections.geojson is written after all models: `confirmed` needs the other model)
        labels, n, raw = clean_mask(prob, water, thr, min_px=2, buffer_px=1)
        n0 = n
        near = near_cloud_components(labels, n, cloud_px, CLOUD_BUFFER_PX)
        shadow = (shadow_components(labels, n, bands["B2"], bands["B3"], bands["B4"], bands["B8"], water)
                  if bands is not None else np.zeros(n, bool))
        guard = {"spectral_cloud_px": n_spec, "near_cloud": int(near.sum()), "shadow": int((shadow & ~near).sum()),
                 "near_cloud_px": int(np.isin(labels, np.flatnonzero(near) + 1).sum()) if near.any() else 0,
                 "shadow_px": int(np.isin(labels, np.flatnonzero(shadow & ~near) + 1).sum()) if (shadow & ~near).any() else 0}
        labels, n = drop_components(labels, n, near | shadow)
        if n0 != n:
            log(f"  {region}/{date}/{m}: cloud guard dropped {n0 - n} of {n0} components {guard}")
        recs = vectorize_detections(labels, n, prob, s_transform, s_crs, region, date, m)
        masks[m] = (labels, n, raw)
        # H3 index (flagged = pixels of kept components, i.e. after SPEC §5.4 post-processing)
        stats = h3_stats(cellr, cells, water, labels > 0, prob, [r["pixel"] for r in recs])
        # cells without any observed water (land / fully clouded / outside) are not written (map clutter)
        write_json(mdir / "h3.geojson", h3_feature_collection([s for s in stats if s["observed_water_px"] > 0],
                                                              date, m, thr), compact=True)
        res["models"][m] = {"threshold": thr, "stats": stats, "recs": recs,
                            "raw_flagged": int(raw.sum()), "clean_flagged": int((labels > 0).sum()),
                            "ts": timeseries_row(date, m, recs, stats, cloud), "meta": pj, "cloud_guard": guard}
    # L15: cross-model confirmation (rule D, reports/model_agreement.md) — only when >= 2 models on this date
    done = list(res["models"])
    for m in done:
        r = res["models"][m]
        if len(done) >= 2:
            labels, n, _ = masks[m]
            lab_of = label_of_records(labels, r["recs"])
            by = {}
            for o in done:
                if o == m:
                    continue
                conf = confirmed_components(labels, n, masks[o][2], CONFIRM_RADIUS_PX)
                for i, k in enumerate(lab_of):
                    if k > 0 and conf[k - 1] and i not in by:
                        by[i] = o
            per_cell: dict[str, int] = {}
            for i, rec in enumerate(r["recs"]):
                rec["props"]["confirmed"] = i in by
                rec["props"]["confirmed_by"] = by.get(i)
                if i in by:
                    k = int(cellr[rec["pixel"][0], rec["pixel"][1]])
                    if k > 0:
                        per_cell[cells[k - 1]] = per_cell.get(cells[k - 1], 0) + 1
            r["n_confirmed"], r["confirmed_cells"] = len(by), per_cell
        text, ginfo = fit_geojson_size(r["recs"], s_crs)
        (odir / m / "detections.geojson").write_text(text, encoding="utf-8")
        r["geo"] = ginfo
        log(f"  {region}/{date}/{m}: thr={r['threshold']} raw_flagged={r['raw_flagged']} kept={r['clean_flagged']} px, "
            f"{len(r['recs'])} det, confirmed={r.get('n_confirmed', '-')}, {len(r['stats'])} cells, "
            f"geojson tol={ginfo['tolerance_m']} dropped={ginfo['dropped']}")
    masks.clear()
    res["seconds"] = round(time.time() - t0, 2)
    log(f"  {region}/{date}: {shape[1]}x{shape[0]} px, {len(cells)} cells, {res['seconds']} s")
    return res


# ---------------------------------------------------------------- per region / manifest
def zoom_for(bounds):
    w, s, e, n = bounds
    ext = max((e - w) * math.cos(math.radians((s + n) / 2)), n - s)
    return int(min(14, max(7, round(11 + math.log2(0.23 / max(ext, 1e-6))))))


HAZE_B8 = 0.006  # median water B8 reflectance above this -> haze / glint (orchestrator 04:48)
HAZE_NOTE = "дымка/блик — находки могут быть завышены"


def scene_quality(sj: dict) -> dict:
    b8 = sj.get("water_b8_median")
    goh = bool(sj.get("glint_or_haze", False))
    haze = goh or (b8 is not None and float(b8) > HAZE_B8)
    note = ""
    if haze:
        note = HAZE_NOTE + (f" (медиана B8 воды {float(b8):.4f})" if b8 is not None else "")
    return {"glint_or_haze": goh, "haze": bool(haze), "note": note}


def _cyr(text) -> bool:
    return any("а" <= ch.lower() <= "я" or ch in "ёЁ" for ch in str(text or ""))


def build_region(region: str, scenes: list[dict], out: Path, model_meta: dict) -> dict:
    ts = []
    for sc in scenes:
        sc["quality"] = scene_quality(sc["scene"])
    by_model: dict[str, list[dict]] = {}
    for sc in scenes:
        for m, r in sc["models"].items():
            by_model.setdefault(m, []).append((sc, r))
    for m, lst in by_model.items():
        repeat: dict[str, int] = {}
        for _, r in lst:
            for s in r["stats"]:
                if s["flagged_water_px"] > 0:
                    repeat[s["h3"]] = repeat.get(s["h3"], 0) + 1
        for sc, r in lst:
            zones = rank_zones(r["stats"], repeat, n_dates=len(lst))
            if sc["quality"]["haze"]:
                for z in zones:
                    z["reason"] += "; " + HAZE_NOTE
            if "n_confirmed" in r:  # L15: confirmed detections (max-prob pixel) in the zone's H3 cell
                for z in zones:
                    z["n_confirmed"] = int(r["confirmed_cells"].get(z["h3"], 0))
            r["zones"] = zones
            write_json(out / region / sc["date"] / m / "zones.json",
                       {"region": region, "date": sc["date"], "model": m, "threshold": r["threshold"],
                        "quality": sc["quality"], "zones": zones})
            ts.append(r["ts"])
            mm = model_meta.setdefault(m, dict(MODEL_INFO.get(m, {"name": m})))
            mm.setdefault("threshold", r["threshold"])
            if m in THR_OVERRIDE:
                mm["threshold_source"] = "задан при сборке (--thresholds), не из чекпойнта"
            if abs(mm["threshold"] - r["threshold"]) > 1e-9:
                mm["threshold_note"] = "порог различается по сценам; точный порог — в h3.geojson/zones.json даты"
    write_json(out / region / "timeseries.json", sort_rows(ts))
    clear = [sc for sc in scenes if not sc["quality"]["haze"]]
    latest = (clear or scenes)[-1]  # default / summary date: latest date without haze, if any
    sj = scenes[-1]["scene"]
    all_b = np.array([sc["bounds"] for sc in scenes])
    bounds = [float(all_b[:, 0].min()), float(all_b[:, 1].min()), float(all_b[:, 2].max()), float(all_b[:, 3].max())]
    prim = "mdd" if "mdd" in latest["models"] else next(iter(latest["models"]), None)
    lt = latest["models"][prim]["ts"] if prim else {"mean_index": None, "n_detections": 0, "total_debris_area_m2": 0.0}
    rn = sj.get("region_name", "")
    return {"id": region,
            "name": rn if _cyr(rn) else (NAME_RU.get(region) or rn or region.replace("_", " ").title()),
            "name_en": sj.get("region_name_en") or ("" if _cyr(rn) else rn),
            "country": COUNTRY.get(region) or sj.get("country", ""),
            "tile": sj.get("tile", ""),
            "center": [round((bounds[0] + bounds[2]) / 2, 6), round((bounds[1] + bounds[3]) / 2, 6)],
            "bounds": [round(v, 6) for v in bounds], "zoom": zoom_for(bounds),
            "default_date": latest["date"],
            "summary": {"latest_date": latest["date"], "model": prim, "haze": latest["quality"]["haze"], "index_permille": lt["mean_index"],
                        "n_detections": lt["n_detections"], "total_debris_area_m2": lt["total_debris_area_m2"]},
            "dates": [{"date": sc["date"], "scene_id": sc["scene"].get("scene_id", ""),
                       "source": sc["scene"].get("source", "earth-search sentinel-2-l2a"),
                       "cloud_frac": None if sc["cloud"] is None else round(sc["cloud"], 4), "bounds": sc["bounds"],
                       "rgb": f"{region}/{sc['date']}/rgb.png", "thumb": f"{region}/{sc['date']}/thumb.jpg",
                       "models": sorted(sc["models"]), "drift": sc["drift"], "quality": sc["quality"],
                       **({"n_confirmed": {m: r["n_confirmed"] for m, r in sorted(sc["models"].items())}}
                          if all("n_confirmed" in r for r in sc["models"].values()) and len(sc["models"]) >= 2
                          else {})}
                      for sc in scenes]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live-dir", default="data/live")
    ap.add_argument("--out", default="service/data")
    ap.add_argument("--regions", default="")
    ap.add_argument("--models", default="")
    ap.add_argument("--kind", default="real", choices=["real", "demo", "fixture"])
    ap.add_argument("--thresholds", default="", help="override decision thresholds, e.g. mdd=0.3,lgbm=0.5 "
                                                     "(default: threshold from prob_<model>.json)")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for kv in filter(None, a.thresholds.split(",")):
        k, v = kv.split("=")
        THR_OVERRIDE[k.strip()] = float(v)
    t_all = time.time()
    live, out = Path(a.live_dir), Path(a.out)
    regions = [r for r in a.regions.split(",") if r]
    models = [m for m in a.models.split(",") if m]
    found = discover(live, regions, models)
    if not found:
        print(f"no scenes with prob_<model>.tif under {live}")
        return 1
    out.mkdir(parents=True, exist_ok=True)
    old = read_json(out / "manifest.json") if (out / "manifest.json").exists() else None
    model_meta: dict = {}
    man_regions, summary_rows, guard_rows = [], [], []
    for region, items in sorted(found.items()):
        rdir = out / region
        if rdir.exists() and rdir.resolve().parent == out.resolve():
            shutil.rmtree(rdir)
        print(f"[{region}] {len(items)} date(s)")
        scenes = []
        for date, sdir, ms in sorted(items):
            for attempt in range(RETRIES + 1):
                try:
                    scenes.append(process_scene(region, date, sdir, ms, out, print))
                    break
                except Exception as e:  # noqa: BLE001 — scene may be being (re)written by L6: wait and retry
                    shutil.rmtree(out / region / date, ignore_errors=True)
                    if attempt < RETRIES:
                        print(f"  .. {region}/{date}: {type(e).__name__}: {e}; retry in {RETRY_WAIT_S} s")
                        time.sleep(RETRY_WAIT_S)
                    else:
                        print(f"  !! {region}/{date}: skipped ({type(e).__name__}: {e})")
        scenes = [s for s in scenes if s["models"]]
        if not scenes:
            continue
        man_regions.append(build_region(region, scenes, out, model_meta))
        for sc in scenes:
            for m, r in sc["models"].items():
                z = r["zones"][0] if r["zones"] else None
                guard_rows.append((region, sc["date"], m, r.get("cloud_guard", {})))
                summary_rows.append((region, sc["date"], m, r["ts"]["n_detections"], r.get("n_confirmed", "-"),
                                     r["ts"]["total_debris_area_m2"],
                                     r["ts"]["mean_index"], (z["h3"], z["index"]) if z else None, sc["seconds"]))
    if old and regions:  # keep regions not rebuilt in this run
        keep = [r for r in old.get("regions", []) if r["id"] not in {x["id"] for x in man_regions}]
        man_regions = sorted(keep + man_regions, key=lambda r: r["id"])
        for k, v in old.get("models", {}).items():
            model_meta.setdefault(k, v)
    manifest = {
        "version": 1, "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": a.kind,
        "index": {"name": "доля наблюдаемой воды с признаками мусора", "unit": "‰", "h3_res": H3_RES,
                  "formula": "flagged_water_px / observed_water_px", "note": "индекс по снимку, не масса пластика",
                  "flagged": "пиксели prob ≥ порога на наблюдаемой воде после постобработки (компоненты ≥ 2 px, "
                             "не касаются облаков/суши в буфере 1 px, не ближе 5 px к облаку/тени, "
                             "не тень облака — L28)",
                  "min_observed_frac": 0.5},
        "models": model_meta, "regions": man_regions, "sources": SOURCES}
    write_json(out / "manifest.json", manifest)
    print("\nregion | date | model | n_det | n_confirmed | area_m2 | mean_index | top zone | s")
    for row in summary_rows:
        print(" | ".join(str(x) for x in row))
    tot: dict = {}
    for reg, date, m, g in guard_rows:
        for k, v in g.items():
            tot.setdefault(m, {}).setdefault(k, 0)
            tot[m][k] += v
    print(f"\ncloud guard (L28), totals per model over {len({r[:2] for r in guard_rows})} scenes: {tot}")
    print(f"\nbuilt {out} in {time.time() - t_all:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
