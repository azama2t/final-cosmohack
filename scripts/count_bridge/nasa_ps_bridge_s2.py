"""L115: NASA IMPACT PlanetScope marine-debris boxes (Shah et al. 2021, CC BY-NC 4.0; L114 sample in
data/extra/count_ds/nasa_marine_debris_planet/) <-> Sentinel-2 L2A of the same day (|dt| <= 1.5 h, L114 s2_match.csv).

  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/count_bridge/nasa_ps_bridge_s2.py [--force]

Per PlanetScope scene: all 256x256 XYZ z16 tiles (their footprint = the area the annotators looked at) + boxes whose
`label` names this scene (some tile files also carry boxes of another date -> dropped). S2 L2A (fetch_live.pick_item
policy) -> scripts/case/pair_quality.process_s2 (quality masks + weights/lgbm, harmonize none) on a crop covering all
tiles -> per box: detector pixels (P >= thr on valid water) inside the box (+10 m) and within 300 m (drift during |dt|),
P max, FDI contrast; per scene: detector pixels in tile footprint far (> 300 m) from any box; illumination rule.
MARIDA overlap is flagged (16PCC 19.09.2018 = MARIDA val, 16PDC 24.10.2018 = MARIDA train, 16PDC 19.09.2018 = test).
Out: out/l115/nasa_ps/<scene>/ + out/l115/nasa_ps_boxes.csv + out/l115/nasa_ps_scenes.csv
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "case"))
sys.path.insert(0, str(ROOT / "scripts"))
import pair_quality as pq  # noqa: E402
from macroplastic.case import illumination as illum  # noqa: E402
from macroplastic.live import stac  # noqa: E402

DS = ROOT / "data" / "extra" / "count_ds" / "nasa_marine_debris_planet"
OUT = ROOT / "out" / "l115" / "nasa_ps"
MARIDA = {("16PCC", "2018-09-19"): "MARIDA val", ("16PDC", "2018-10-24"): "MARIDA train",
          ("16PDC", "2018-09-19"): "MARIDA test"}
_LAST = {}


def _capture(fn):
    def wrap(*a, **k):
        r = fn(*a, **k)
        _LAST["crop"] = r
        return r
    return wrap


pq.stac.read_crop = _capture(stac.read_crop)


def xyz_bounds(x, y, z):
    n = 2 ** z
    lon0, lon1 = x / n * 360 - 180, (x + 1) / n * 360 - 180
    lat0 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    lat1 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon0, lat0, lon1, lat1


def fdi(b):
    i = {k: stac.BANDS.index(k) for k in ("B6", "B8", "B11")}
    return b[i["B8"]] - (b[i["B6"]] + (b[i["B11"]] - b[i["B6"]]) * (842 - 665) / (1610 - 665) * 10)


def load_scene(ps):
    from shapely.geometry import box, shape
    tiles, boxes, dropped = [], [], 0
    for f in sorted(glob.glob(str(DS / "labels" / f"{ps}_*.geojson"))):
        x, y, z = map(int, Path(f).stem.split("_")[-1].split("-"))
        tiles.append(box(*xyz_bounds(x, y, z)))
        for ft in json.loads(Path(f).read_text(encoding="utf-8"))["features"]:
            if ps in str(ft["properties"].get("label", "")):
                boxes.append(shape(ft["geometry"]))
            else:
                dropped += 1
    # the same box can be listed in two overlapping tiles -> dedupe by rounded bounds
    uniq = {tuple(round(v, 6) for v in b.bounds): b for b in boxes}
    return tiles, list(uniq.values()), dropped, len(boxes)


def run_scene(r, cfg, pred, force):
    from pyproj import Transformer
    from shapely.ops import transform as stransform, unary_union
    from rasterio.features import rasterize
    from scipy import ndimage
    import rasterio
    outdir = OUT / r.ps_scene
    if (outdir / "meta.json").exists() and not force:
        return json.loads((outdir / "meta.json").read_text(encoding="utf-8"))
    tiles, boxes, dropped, n_raw = load_scene(r.ps_scene)
    fp = unary_union(tiles)
    lon, lat = fp.centroid.x, fp.centroid.y
    s2tile = r.best_s2_id.split("_")[1]
    date = r.best_s2_id.split("_")[2]
    d = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    from fetch_live import pick_item
    item = pick_item(d, s2tile, lon, lat)
    epsg = stac.item_epsg(item)
    to = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform
    fpu = stransform(to, fp)
    w, s_, e, n = fpu.bounds
    half = math.hypot(e - w, n - s_) / 2
    cfg["strip"]["point_buffer_m"] = half
    endpoint = "planetary-computer" if "planetary" in stac.source_of(item) else "earth-search"
    row = SimpleNamespace(endpoint=endpoint, collection="sentinel-2-l2a", item_id=item.id)
    g = dict(line=None, lines=None, lon=lon, lat=lat, width_m=None)
    res = pq.process_s2(row, g, cfg, pred, outdir)
    crop = _LAST["crop"]
    bands, tfm = crop["bands"], crop["transform"]
    with rasterio.open(outdir / "prob.tif") as ds:
        prob = ds.read(1) / 255.0
    with rasterio.open(outdir / "quality.tif") as ds:
        q = ds.read(1)
    H, W = q.shape
    water = q == pq.Q_WATER
    thr = float(pred.threshold)
    det = (prob >= thr) & water
    F = fdi(bands)
    fdi_bg = float(np.nanmedian(F[water])) if water.any() else np.nan
    fpm = rasterize([(fpu, 1)], out_shape=(H, W), transform=tfm, fill=0, dtype="uint8").astype(bool)
    bu = [stransform(to, b) for b in boxes]
    bm = rasterize([(b.buffer(10), 1) for b in bu], out_shape=(H, W), transform=tfm, all_touched=True, fill=0,
                   dtype="uint8").astype(bool) if bu else np.zeros((H, W), bool)
    near300 = ndimage.binary_dilation(bm, iterations=30) if bu else np.zeros((H, W), bool)
    rows = []
    for i, (b, bb) in enumerate(zip(boxes, bu)):
        m1 = rasterize([(bb.buffer(10), 1)], out_shape=(H, W), transform=tfm, all_touched=True, fill=0, dtype="uint8").astype(bool)
        m3 = ndimage.binary_dilation(m1, iterations=30)
        wv = m1 & water
        rows.append(dict(ps_scene=r.ps_scene, box=i, area_m2=round(bb.area, 1), lon=round(b.centroid.x, 6), lat=round(b.centroid.y, 6),
                         water_px=int(wv.sum()), q_codes=json.dumps(np.unique(q[m1]).tolist()),
                         p_max=round(float(prob[m1].max()), 4) if m1.any() else None,
                         det_in_box=int((det & m1).sum()), det_within_300m=int((det & m3).sum()),
                         fdi_contrast_max=round(float(np.nanmax(F[wv]) - fdi_bg), 4) if wv.any() else None))
    zen_b = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    xs, ys = zen_b.transform([w, e, w, e], [s_, s_, n, n])
    zen = illum.solar_zenith(res["scene_datetime"], [min(xs), min(ys), max(xs), max(ys)])
    b3 = illum.water_median(illum.rgb_png_b3(outdir / "rgb.png", 512), q, 1)
    ok, low, weak = illum.detector_evaluable(zen, b3)
    fw = fpm & water
    meta = dict(ps_scene=r.ps_scene, ps_time=r.ps_scene[:15], s2_scene=item.id, source=endpoint,
                processing_baseline=item.properties.get("s2:processing_baseline"), scene_datetime=res["scene_datetime"],
                dt_h=float(r.dt_h), cloud=res.get("scene_cloud_cover"), marida=MARIDA.get((s2tile, d), ""),
                n_tiles=len(tiles), footprint_km2=round(fpu.area / 1e6, 3), n_boxes=len(boxes), n_boxes_raw=n_raw,
                n_boxes_other_date_dropped=dropped, box_area_m2_median=round(float(np.median([b.area for b in bu])), 1) if bu else None,
                footprint_water_frac=round(float(fw.sum() / max(fpm.sum(), 1)), 3),
                boxes_with_det_in_box=int(sum(x["det_in_box"] > 0 for x in rows)),
                boxes_with_det_300m=int(sum(x["det_within_300m"] > 0 for x in rows)),
                boxes_on_valid_water=int(sum(x["water_px"] > 0 for x in rows)),
                det_px_footprint=int((det & fpm).sum()), det_px_footprint_far=int((det & fpm & ~near300).sum()),
                water_px_footprint=int(fw.sum()), sun_zenith=zen, water_b3=b3, evaluable=ok, low_sun=low, weak_signal=weak,
                decision=res["decision"], reason=res["reason"], quality=res["quality"], detector=res["detector"],
                threshold=thr, boxes=rows, created=time.strftime("%Y-%m-%dT%H:%M:%S"))
    (outdir / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--max-dt", type=float, default=1.5)
    a = ap.parse_args()
    cfg = pq.load_cfg(ROOT / "configs" / "case_pairs.yaml")
    cfg["strip"]["context_m"] = 500
    from macroplastic.models.lgbm_predict import load_predictor
    pred = load_predictor(ROOT / cfg["detector"]["weights"], harmonize=pq.harmonize_mode(cfg))
    m = pd.read_csv(DS / "s2_match.csv")
    m = m[m.best_s2_id.notna() & (m.dt_h.abs() <= a.max_dt)]
    S, B = [], []
    for r in m.itertuples():
        t0 = time.time()
        try:
            meta = run_scene(r, cfg, pred, a.force)
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"[error] {r.ps_scene}: {e}\n{traceback.format_exc()[-600:]}", flush=True)
            continue
        B += meta["boxes"]
        S.append({k: v for k, v in meta.items() if k not in ("boxes", "quality", "detector")})
        print(f"{r.ps_scene}: {meta['s2_scene']} boxes={meta['n_boxes']} det_in_box={meta['boxes_with_det_in_box']} "
              f"det300={meta['boxes_with_det_300m']} far={meta['det_px_footprint_far']} eval={meta['evaluable']} "
              f"{meta['marida']} {time.time() - t0:.0f} s", flush=True)
    pd.DataFrame(S).to_csv(ROOT / "out" / "l115" / "nasa_ps_scenes.csv", index=False)
    pd.DataFrame(B).to_csv(ROOT / "out" / "l115" / "nasa_ps_boxes.csv", index=False)


if __name__ == "__main__":
    main()
