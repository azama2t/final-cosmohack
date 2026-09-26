"""L127 (INBOX §34 п.1, П1 «ВСЕ МИШЕНИ», A*): every date with a KNOWN number / coverage of artificial items and a
Sentinel-2 L2A of the same date -> target pixels with full spectra, detector P, FDI / NIR, coverage and items per pixel.

Continues L115 (scripts/count_bridge/plp_bridge_s2.py, same detector / masks / illumination rule / L2A choice):
  - PLP 2018/2019/2021/2022 (Lesvos, 35SMD): the L115 per-date routine is re-run into out/l127/plp/<date>/ and the crop
    is kept, so that every target pixel gets all 12 bands (L115 kept only P, FDI, B8);
  - Maathuis 2026 (Nederrijn riverbank, NL, 4TU 3d24e304): targets from the Topcon GNSS points + Fieldwork_Notes.xlsx
    (polyester sheets 30x3 / 30x2 / 30x1 / 30x0.5 m; PET bottles 3x30 m at 4 /m2 and 3x15 m at 8 /m2), 8 S2 dates
    05-24.03.2025 incl. 2 no-target controls; pixel coverage = exact polygon / pixel intersection;
  - Themistocleous 2020 (Limassol, 15.12.2018): no coordinates published -> scene search only (crop around the Old Port).

Detector: weights/lgbm, threshold from meta (0.63), harmonize none (configs/case_pairs.yaml), masks pair_quality.
L2A choice: scripts/fetch_live.pick_item (ES original _0 before 2022-01-25, else Planetary Computer).

  set CUDA_VISIBLE_DEVICES=-1
  .venv/Scripts/python.exe scripts/count_bridge/p1_targets.py plp [--dates 20190418 ...]
  .venv/Scripts/python.exe scripts/count_bridge/p1_targets.py maathuis
  .venv/Scripts/python.exe scripts/count_bridge/p1_targets.py themis
Out: out/l127/<site>/<date>/{meta.json,pixels.json,...}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "count_bridge"))
sys.path.insert(0, str(ROOT / "scripts"))
import plp_bridge_s2 as pb  # noqa: E402  (patches pq.stac.read_crop to keep the last crop in pb._LAST)

pq, stac, illum = pb.pq, pb.stac, pb.illum
OUT = ROOT / "out" / "l127"
BANDS = stac.BANDS


def load_pred(cfg):
    from macroplastic.models.lgbm_predict import load_predictor
    return load_predictor(ROOT / cfg["detector"]["weights"], harmonize=pq.harmonize_mode(cfg))


def base_cfg(point_buffer=300, context=1200):
    cfg = pq.load_cfg(ROOT / "configs" / "case_pairs.yaml")
    cfg["strip"]["point_buffer_m"] = point_buffer
    cfg["strip"]["context_m"] = context
    return cfg


def indices(v):
    """v: dict band -> reflectance. FDI (Biermann 2020), NDVI, B8 (NIR)."""
    fdi = v["B8"] - (v["B6"] + (v["B11"] - v["B6"]) * (842 - 665) / (1610 - 665) * 10)
    ndvi = (v["B8"] - v["B4"]) / (v["B8"] + v["B4"]) if (v["B8"] + v["B4"]) else np.nan
    return fdi, ndvi


def bg_spectrum(bands, q, excl):
    m = (q == pq.Q_WATER) & ~excl
    if m.sum() < 50:
        return None, None
    med = {b: float(np.nanmedian(bands[i][m])) for i, b in enumerate(BANDS)}
    sd = {b: float(np.nanstd(bands[i][m])) for i, b in enumerate(BANDS)}
    return med, sd


def px_record(bands, prob, q, F, r, c):
    v = {b: float(bands[i][r, c]) for i, b in enumerate(BANDS)}
    fdi, ndvi = indices(v)
    return dict(v, p=float(prob[r, c]), q=int(q[r, c]), fdi=float(fdi), ndvi=float(ndvi))


# ------------------------------------------------------------------------------------------------ PLP
def run_plp(dates, force=False):
    cfg = base_cfg()
    pred = load_pred(cfg)
    thr = float(pred.threshold)
    pb.OUT = OUT / "plp"
    T = pd.concat([pb.targets_2019(), pb.targets_2021_22()], ignore_index=True)
    all_dates = ["20180607"] + sorted(set(T.date))
    for d in (dates or all_dates):
        od = pb.OUT / d
        if (od / "pixels.json").exists() and not force:
            print(d, "cached", flush=True)
            continue
        t0 = time.time()
        pb._LAST.clear()
        try:
            m = pb.run_date(d, T[T.date == d], cfg, pred, force=True)
        except Exception as e:  # noqa: BLE001
            print("[error]", d, repr(e)[:300], flush=True)
            continue
        if m.get("status") != "ok" or "crop" not in pb._LAST:
            print(d, m.get("status"), flush=True)
            continue
        crop = pb._LAST["crop"]
        bands, tfm = crop["bands"], crop["transform"]
        import rasterio
        with rasterio.open(od / "prob.tif") as ds:
            prob = ds.read(1) / 255.0
        with rasterio.open(od / "quality.tif") as ds:
            q = ds.read(1)
        F = pb.fdi(bands)
        tmask = np.zeros(q.shape, bool)
        pix = []
        for t in m["targets"]:
            if not t["inside"]:
                continue
            r, c = t["row"], t["col"]
            tmask[r, c] = True
            rec = px_record(bands, prob, q, F, r, c)
            rec.update({k: t.get(k) for k in ("date", "year", "target", "pixel", "x", "y", "frac_plastic", "frac_bags",
                                               "frac_bottles", "frac_reeds", "frac_wood", "material", "note")},
                       row=r, col=c)
            pix.append(rec)
        reg = OUT / "plp2018_targets_px.json"  # scripts/count_bridge/p1_plp2018_register.py (orthophoto -> S2)
        if d == "20180607" and reg.exists():
            R = json.loads(reg.read_text(encoding="utf-8"))
            per_m2 = {"bottles": 36.0, "bags": 1.38, "nets": None}
            mat = {"bottles": "PET 1.5 L bottles", "bags": "LDPE bags (blue)", "nets": "nylon fishing net (yellow) on white net"}
            for name in ("bottles", "nets", "bags"):
                for o in R[name]:
                    r, c = o["row"], o["col"]
                    tmask[r, c] = True
                    rec = px_record(bands, prob, q, F, r, c)
                    X, Y = tfm * (c + 0.5, r + 0.5)
                    fb = o["frac"] if name == "bottles" else 0.0
                    fg = o["frac"] if name == "bags" else 0.0
                    rec.update(date=d, year=2018, target=name, pixel=f"{name}_{r}_{c}", x=X, y=Y, frac_plastic=o["frac"],
                               frac_bottles=fb, frac_bags=fg, frac_reeds=0.0, material=mat[name],
                               note=f"registered orthophoto (corr {R['registration']['corr']})", row=r, col=c)
                    pix.append(rec)
        elif d == "20180607":  # no target points: NASA PS box of this date (26.565782 E, 39.108337 N, 625 m2) -> 5x5 px
            from pyproj import Transformer
            x0, y0 = Transformer.from_crs("EPSG:4326", f"EPSG:{pb.EPSG}", always_xy=True).transform(26.565782, 39.108337)
            c0, r0 = ~tfm * (x0, y0)
            r0, c0 = int(np.floor(r0)), int(np.floor(c0))
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    r, c = r0 + dr, c0 + dc
                    tmask[r, c] = True
                    rec = px_record(bands, prob, q, F, r, c)
                    X, Y = tfm * (c + 0.5, r + 0.5)
                    rec.update(date=d, year=2018, target="PSbox", pixel=f"{dr}_{dc}", x=X, y=Y, frac_plastic=None,
                               material="unknown (PS box, 3 targets 10x10 m)", row=r, col=c)
                    pix.append(rec)
        med, sd = bg_spectrum(bands, q, pb._dilate(tmask, 3))
        out = dict(date=d, scene_id=m["scene_id"], scene_datetime=m["scene_datetime"], threshold=thr,
                   evaluable=m["evaluable"], sun_zenith=m["sun_zenith"], water_b3=m["water_b3"], bg_median=med, bg_std=sd,
                   det_px_water_crop=m["det_px_water"], water_px_crop=m["water_px"], near=m.get("near_targets"),
                   baseline=m.get("processing_baseline"), source=m.get("source"), pixels=pix)
        (od / "pixels.json").write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
        print(f"{d}: {m['scene_id']} px={len(pix)} eval={m['evaluable']} {time.time() - t0:.0f}s", flush=True)


# ------------------------------------------------------------------------------------------------ Maathuis 2026
MA_DIR = ROOT / "data" / "extra" / "count_ds" / "maathuis_riverbank" / "field" / "Field_campaign_data" / "Measurements" / "Topcon"
# date -> list of (target, shapefile, point range, material, items per m2 or None); Fieldwork_Notes.xlsx + main.py
MA_PLAN = {
    "20250305": [],
    "20250307": [("polyester 30x3 m", "mi0703", (100, 107), "polyester", None)],
    "20250310": [("polyester 30x2 m", "mi1003", (100, 109), "polyester", None),
                 ("PET 3x30 m, 4 /m2", "mi1003", (110, 136), "PET bottles", 4.0)],
    "20250312": [("polyester 30x2 m", "mi1003", (100, 109), "polyester", None),
                 ("PET 3x30 m, 4 /m2", "mi1003", (110, 136), "PET bottles", 4.0)],
    "20250314": [("polyester 30x2 m", "mi1003", (100, 109), "polyester", None),
                 ("PET 3x30 m, 4 /m2", "mi1003", (110, 136), "PET bottles", 4.0)],
    "20250315": [("polyester 30x2 m", "mi1003", (100, 109), "polyester", None),  # not used by the authors
                 ("PET 3x30 m, 4 /m2", "mi1003", (110, 136), "PET bottles", 4.0)],
    "20250317": [("polyester 30x1 m", "mi1703", (100, 109), "polyester", None),
                 ("PET 3x30 m, 4 /m2", "mi1003", (110, 136), "PET bottles", 4.0)],
    "20250320": [("polyester 30x0.5 m", "mi1903", (100, 112), "polyester", None),
                 ("PET 3x15 m, 8 /m2", "mi1703", (110, 120), "PET bottles", 8.0)],
    "20250324": [],
}
MA_LONLAT = (5.6530, 51.9547)


def ma_polygons(epsg):
    import geopandas as gpd
    from shapely.geometry import MultiPoint
    out = {}
    for d, tl in MA_PLAN.items():
        polys = []
        for name, shp, (a, b), mat, dens in tl:
            g = gpd.read_file(MA_DIR / f"{shp}.shp").to_crs(epsg=epsg)
            g["n"] = g.OBJNAME.astype(int)
            pts = g[(g.n >= a) & (g.n <= b)].geometry
            polys.append((name, mat, dens, MultiPoint(list(pts)).convex_hull))
        out[d] = polys
    return out


def run_generic(site, date, lon, lat, tile, polys, cfg, pred, force=False, radius_m=60):
    """polys: list of (name, material, items_per_m2, shapely polygon in the item EPSG) -> pixels with coverage."""
    from shapely.geometry import box
    import rasterio
    od = OUT / site / date
    if (od / "pixels.json").exists() and not force:
        print(site, date, "cached", flush=True)
        return
    from fetch_live import pick_item
    d = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    item = pick_item(d, tile, lon, lat)
    endpoint = "planetary-computer" if "planetary" in str(stac.source_of(item)).lower() else "earth-search"
    row = SimpleNamespace(endpoint=endpoint, collection="sentinel-2-l2a", item_id=item.id)
    g = dict(line=None, lines=None, lon=lon, lat=lat, width_m=None)
    pb._LAST.clear()
    res = pq.process_s2(row, g, cfg, pred, od)
    crop = pb._LAST["crop"]
    bands, tfm = crop["bands"], crop["transform"]
    with rasterio.open(od / "prob.tif") as ds:
        prob = ds.read(1) / 255.0
    with rasterio.open(od / "quality.tif") as ds:
        q = ds.read(1)
    F = pb.fdi(bands)
    from pyproj import Transformer
    tr = Transformer.from_crs(f"EPSG:{res['epsg']}", "EPSG:4326", always_xy=True)
    w, s_, e, n = res["bounds_utm"]
    xs, ys = tr.transform([w, e, w, e], [s_, s_, n, n])
    zen = illum.solar_zenith(res["scene_datetime"], [min(xs), min(ys), max(xs), max(ys)])
    b3 = illum.water_median(illum.rgb_png_b3(od / "rgb.png", 512), q, 1)
    ok, low, weak = illum.detector_evaluable(zen, b3)
    H, W = q.shape
    pix, tmask = [], np.zeros((H, W), bool)
    xc, yc = Transformer.from_crs("EPSG:4326", f"EPSG:{res['epsg']}", always_xy=True).transform(lon, lat)
    c0, r0 = ~tfm * (xc, yc)
    r0, c0 = int(r0), int(c0)
    k = int(radius_m // 10)
    for r in range(max(r0 - k, 0), min(r0 + k + 1, H)):
        for c in range(max(c0 - k, 0), min(c0 + k + 1, W)):
            X0, Y0 = tfm * (c, r)
            X1, Y1 = tfm * (c + 1, r + 1)
            pxb = box(min(X0, X1), min(Y0, Y1), max(X0, X1), max(Y0, Y1))
            cov = {}
            for name, mat, dens, poly in polys:
                a = pxb.intersection(poly).area
                if a > 0:
                    cov[name] = dict(material=mat, area_m2=round(a, 2), frac=round(a / 100.0, 4),
                                     items=round(a * dens, 1) if dens else None, items_per_m2=dens)
            rec = px_record(bands, prob, q, F, r, c)
            rec.update(date=date, row=r, col=c, x=(X0 + X1) / 2, y=(Y0 + Y1) / 2, cover=cov,
                       frac_target=round(sum(v["frac"] for v in cov.values()), 4),
                       items=round(sum(v["items"] or 0 for v in cov.values()), 1) if cov else 0.0)
            if cov:
                tmask[r, c] = True
            pix.append(rec)
    med, sd = bg_spectrum(bands, q, pb._dilate(tmask, 3))
    thr = float(pred.threshold)
    out = dict(site=site, date=date, scene_id=item.id, scene_datetime=res["scene_datetime"], source=endpoint,
               baseline=item.properties.get("s2:processing_baseline"), cloud=res.get("scene_cloud_cover"),
               epsg=res["epsg"], bounds_utm=res["bounds_utm"], threshold=thr, sun_zenith=zen, water_b3=b3, evaluable=ok,
               low_sun=low, weak_signal=weak, decision=res["decision"], reason=res["reason"], detector=res["detector"],
               bg_median=med, bg_std=sd, targets=[dict(name=n_, material=m_, items_per_m2=d_, area_m2=round(p_.area, 1))
                                                  for n_, m_, d_, p_ in polys],
               pixels=pix, created=time.strftime("%Y-%m-%dT%H:%M:%S"))
    (od / "pixels.json").write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
    tp = [p for p in pix if p["cover"]]
    print(f"{site} {date}: {item.id} target px={len(tp)} Pmax={max([p['p'] for p in tp], default=None)} "
          f"eval={ok} q={sorted({p['q'] for p in tp})}", flush=True)


def run_maathuis(force=False):
    cfg = base_cfg(point_buffer=150, context=600)
    pred = load_pred(cfg)
    polys = ma_polygons(32631)
    for d in MA_PLAN:
        try:
            run_generic("maathuis", d, *MA_LONLAT, "31UFT", polys[d], cfg, pred, force)
        except Exception as e:  # noqa: BLE001
            import traceback
            print("[error]", d, repr(e)[:300], traceback.format_exc()[-600:], flush=True)


# ------------------------------------------------------------------------------------------------ Themistocleous 2020
def run_themis(force=False, tile="36SWD"):
    """15.12.2018, «200 m south of the Limassol shoreline» south of the Old Port; no coordinates -> scene-level search:
    all water pixels 100-600 m from land in a 4x3 km crop, ranked by P and FDI contrast."""
    cfg = base_cfg(point_buffer=800, context=400)
    pred = load_pred(cfg)
    run_generic("themis", "20181215", 33.0445, 34.6712, tile, [], cfg, pred, force, radius_m=400)


# ------------------------------------------------------------------------------------------------ scan (no coordinates)
SCAN = {  # site -> (lon, lat, dates, radius_m); target positions are NOT published -> located by the NIR anomaly
    "fronkova2024": (1.3456, 52.6205, ["20210806", "20210809", "20210811", "20210814", "20210816", "20210819",
                                       ], 700),   # Whitlingham Great Broad, tarp 10x10 m, 05-19.08.2021
    "plp2020": (26.5659, 39.1084, ["20200616", "20200701", "20200706"], 400),     # Tsamakia, 2 discs d = 7 m
    "plp2024": (26.5246, 39.0393, ["20240531", "20240620", "20240625", "20240705"], 500),  # Gera, strips 1.2-2.4 x 25-50 m
}


def run_scan(site, dates=None, force=False):
    """Crop around the site; keep the whole crop (bands, P, quality) for manual target location."""
    import rasterio
    lon, lat, dd, rad = SCAN[site]
    cfg = base_cfg(point_buffer=rad, context=300)
    pred = load_pred(cfg)
    from fetch_live import pick_item
    for date in (dates or dd):
        od = OUT / site / date
        if (od / "crop.npz").exists() and not force:
            print(site, date, "cached", flush=True)
            continue
        d = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        src = "planetary-computer" if d >= "2022-01-25" else "earth-search"
        its = stac.search(src, lon, lat, f"{d}/{d}")
        if not its:
            print(site, date, "no S2 item", flush=True)
            continue
        tile = stac.tile_of(its[0])
        try:
            item = pick_item(d, tile, lon, lat)
            endpoint = "planetary-computer" if "planetary" in str(stac.source_of(item)).lower() else "earth-search"
            row = SimpleNamespace(endpoint=endpoint, collection="sentinel-2-l2a", item_id=item.id)
            pb._LAST.clear()
            res = pq.process_s2(row, dict(line=None, lines=None, lon=lon, lat=lat, width_m=None), cfg, pred, od)
        except Exception as e:  # noqa: BLE001
            print("[error]", site, date, repr(e)[:300], flush=True)
            continue
        crop = pb._LAST["crop"]
        with rasterio.open(od / "prob.tif") as ds:
            prob = ds.read(1) / 255.0
        with rasterio.open(od / "quality.tif") as ds:
            q = ds.read(1)
        from pyproj import Transformer
        tr = Transformer.from_crs(f"EPSG:{res['epsg']}", "EPSG:4326", always_xy=True)
        w, s_, e, n = res["bounds_utm"]
        xs, ys = tr.transform([w, e, w, e], [s_, s_, n, n])
        zen = illum.solar_zenith(res["scene_datetime"], [min(xs), min(ys), max(xs), max(ys)])
        b3 = illum.water_median(illum.rgb_png_b3(od / "rgb.png", 512), q, 1)
        ok, low, weak = illum.detector_evaluable(zen, b3)
        np.savez_compressed(od / "crop.npz", bands=crop["bands"], scl=crop["scl"], prob=prob, q=q,
                            transform=np.array(crop["transform"])[:6])
        meta = dict(site=site, date=date, scene_id=item.id, scene_datetime=res["scene_datetime"], source=endpoint,
                    baseline=item.properties.get("s2:processing_baseline"), cloud=res.get("scene_cloud_cover"),
                    epsg=res["epsg"], bounds_utm=res["bounds_utm"], sun_zenith=zen, water_b3=b3, evaluable=ok,
                    decision=res["decision"], reason=res["reason"], detector=res["detector"], quality=res["quality"])
        (od / "scan.json").write_text(json.dumps(meta, indent=1, default=float), encoding="utf-8")
        print(f"{site} {date}: {item.id} cloud={res.get('scene_cloud_cover')} eval={ok} q_water={res['quality']['valid_water_frac']} "
              f"det_crop={res['detector']['n_det_crop']}", flush=True)


# targets located on the scan crop (no published coordinates): (site, date) -> (lon, lat of the located anomaly, note)
LOCATED = {
    ("fronkova2024", "20210811"): (1.34567, 52.62234, "tarp 10x10 m blue PE; paper Fig. 1A box ≈ 52.6224N 1.3456E; "
                                                      "S2 B8 anomaly +0.021 at this pixel (10 m from the map box)"),
    ("plp2020", "20200616"): (26.56622, 39.10813, "HDPE mesh disc d = 7 m (+ cage 1/4 full of litter, not seen); only "
                                                  "B8 anomaly at the PLP site (+0.018), same place as PLP2018/19 targets"),
    ("plp2024", "20240705"): (26.52446, 39.03903, "HDPE mesh 2.4x50 m (from 27.06) + wood strips 1.2-2.4x25 m (log); "
                                                  "N-S elongated B8 anomaly +0.06..+0.07 over 4 px at the PLP2021/22 site"),
    ("plp2024", "20240625"): (26.52446, 39.03903, "HDPE mesh + wood strips 1.2x25 and 2.4x25 m (log, before the 27.06 "
                                                  "extension); B8 anomaly +0.06..+0.07 over 3 px at the PLP2021/22 site; "
                                                  "pixel glint B11 > 0.03 in the strip"),
    ("plp2020", "20200706"): (26.56633, 39.10822, "repeat deployment (log); B8 anomaly +0.008, 1 px; S2 use not stated in log"),
}


def run_located(force=False):
    from pyproj import Transformer
    for (site, date), (lon, lat, note) in LOCATED.items():
        od = OUT / site / date
        if not (od / "crop.npz").exists():
            print(site, date, "no crop", flush=True)
            continue
        z = np.load(od / "crop.npz")
        m = json.loads((od / "scan.json").read_text(encoding="utf-8"))
        b, q, prob, t = z["bands"], z["q"], z["prob"], z["transform"]
        x, y = Transformer.from_crs("EPSG:4326", f"EPSG:{m['epsg']}", always_xy=True).transform(lon, lat)
        c0, r0 = int((x - t[2]) // 10), int((t[5] - y) // 10)
        F = pb.fdi(b)
        pix = []
        tm = np.zeros(q.shape, bool)
        for r in range(r0 - 1, r0 + 2):
            for c in range(c0 - 1, c0 + 2):
                rec = px_record(b, prob, q, F, r, c)
                rec.update(date=date, row=r, col=c, x=t[2] + (c + 0.5) * 10, y=t[5] - (r + 0.5) * 10, cover={},
                           frac_target=None, items=None, centre=(r == r0 and c == c0))
                tm[r, c] = True
                pix.append(rec)
        med, sd = bg_spectrum(b, q, pb._dilate(tm, 3))
        out = dict(m, pixels=pix, bg_median=med, bg_std=sd, threshold=0.63, located_note=note,
                   targets=[dict(name=note.split(";")[0])])
        (od / "pixels.json").write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
        cp = [p for p in pix if p["centre"]][0]
        print(site, date, "centre px", r0, c0, "q", cp["q"], "P", round(cp["p"], 3), "B8", round(cp["B8"], 4), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["plp", "maathuis", "themis", "scan", "located"])
    ap.add_argument("--site")
    ap.add_argument("--dates", nargs="*")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--tile")
    a = ap.parse_args()
    if a.what == "plp":
        run_plp(a.dates, a.force)
    elif a.what == "located":
        run_located(a.force)
    elif a.what == "scan":
        run_scan(a.site, a.dates, a.force)
    elif a.what == "maathuis":
        run_maathuis(a.force)
    else:
        run_themis(a.force, a.tile or "36SWD")


if __name__ == "__main__":
    main()
