"""L115 (INBOX §17, agent 10 «мост к Sentinel-2»): PLP drone/target <-> S2 L2A of the same day, current detector.

  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/count_bridge/plp_bridge_s2.py            # all dates, resumable
  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/count_bridge/plp_bridge_s2.py --years 2019 --force

Per date (Tsamakia, Lesvos, tile 35SMD): Earth Search L2A of the same acquisition -> scripts/case/pair_quality.process_s2
(same quality masks + weights/lgbm, harmonize none, as all pairs) on a small point crop around the targets ->
per target: drone/target coverage (PLP2019: % per 10 m pixel from the UAV orthophoto, shapefiles of Zenodo 3752719;
PLP2021/22: fraction of the pixel under the target polygon, labels_plp.csv of L89), P(debris) and quality code at the
target pixels, FDI contrast, «detector evaluable» (src/macroplastic/case/illumination.py, studio rule L95).
PLP2018 (07.06.2018 orthophoto in the PLP2019 archive): no target coordinates -> scene-level only.
Out: out/l115/plp/<date>/ (pair_quality files + meta.json + targets.png), out/l115/plp_bridge_targets.csv,
out/l115/plp_bridge_dates.csv.
"""
from __future__ import annotations

import argparse
import glob
import json
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
import pair_quality as pq  # noqa: E402
from macroplastic.case import illumination as illum  # noqa: E402
from macroplastic.live import stac  # noqa: E402

OUT = ROOT / "out" / "l115" / "plp"
PLP = ROOT / "data" / "extra" / "plp"
EPSG = 32635
TILE = "35SMD"
_LAST = {}


def _capture(fn):
    def wrap(*a, **k):
        r = fn(*a, **k)
        _LAST["crop"] = r
        return r
    return wrap


pq.stac.read_crop = _capture(stac.read_crop)


def fdi(b):  # Biermann 2020: B8 - (B6 + (B11 - B6) * (842-665)/(1610-665) * 10)
    i = {k: stac.BANDS.index(k) for k in ("B4", "B6", "B8", "B11")}
    return b[i["B8"]] - (b[i["B6"]] + (b[i["B11"]] - b[i["B6"]]) * (842 - 665) / (1610 - 665) * 10)


def targets_2019() -> pd.DataFrame:
    rows = []
    for f in sorted(glob.glob(str(PLP / "PLP2019" / "PLP2019_dataset" / "Vector_Points" / "*" / "*.shp"))):
        import geopandas as gpd
        g = gpd.read_file(f)
        g.columns = [c if c == "geometry" else c.upper() for c in g.columns]
        date = Path(f).parent.name
        for r in g.itertuples():
            bags = float(getattr(r, "CP_BAGS", 0) or 0)
            bott = float(getattr(r, "CP_BOTTLES", 0) or 0)
            reeds = float(getattr(r, "CP_REEDS", 0) or 0)
            name = str(r.PIXEL_NAME)
            rows.append(dict(date=date, year=2019, target=name[0], pixel=name, x=float(r.POINT_X), y=float(r.POINT_Y),
                             frac_plastic=(bags + bott) / 100, frac_bags=bags / 100, frac_bottles=bott / 100,
                             frac_reeds=reeds / 100, material="bags+bottles" if bags and bott else
                             ("bags" if bags else ("bottles" if bott else ("reeds" if reeds else "sea")))))
    return pd.DataFrame(rows)


def targets_2021_22() -> pd.DataFrame:
    t = pd.read_csv(PLP / "labels_plp.csv")
    t = t[(t.kind == "plp_target") & t.year.isin([2021, 2022])]
    return pd.DataFrame(dict(date=t.date.astype(str), year=t.year, target=t.material + "#" + t.target_id.astype(str),
                             pixel=t.r.astype(str) + "_" + t.c.astype(str), x=t.x, y=t.y_utm,
                             frac_plastic=np.where(t.material == "wood", 0.0, t.frac),
                             frac_wood=np.where(t.material == "wood", t.frac, 0.0), material=t.material,
                             note=t.note.fillna("")))


def find_item(lon, lat, date):
    """Same source policy as the live contour (scripts/fetch_live.pick_item): PC for >= 2022-01-25, else Earth Search
    item of the ORIGINAL processing (baseline < 04, _0) -- the _1/_3 reprocessings clamp dark water at DN=1."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from fetch_live import pick_item
    d = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    for a in range(4):
        try:
            return pick_item(d, TILE, lon, lat)
        except IndexError:
            return None
        except Exception as e:  # noqa: BLE001
            print("search retry", date, e, flush=True)
            time.sleep(3 * (a + 1))
    return None


def run_date(date, tg, cfg, pred, force=False):
    from pyproj import Transformer
    import rasterio
    outdir = OUT / date
    if (outdir / "meta.json").exists() and not force:
        return json.loads((outdir / "meta.json").read_text(encoding="utf-8"))
    tr = Transformer.from_crs(f"EPSG:{EPSG}", "EPSG:4326", always_xy=True)
    if len(tg):
        cx, cy = float(tg.x.mean()), float(tg.y.mean())
    else:  # PLP2018: centre of the PLP2019 target area (same beach)
        cx, cy = 462520.0, 4328900.0
    lon, lat = tr.transform(cx, cy)
    item = find_item(lon, lat, date)
    meta = dict(date=date, lon=lon, lat=lat, n_target_px=int(len(tg)))
    if item is None:
        meta.update(status="no_item")
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "meta.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")
        return meta
    endpoint = stac.source_of(item) if hasattr(stac, "source_of") else "earth-search"
    endpoint = "planetary-computer" if "planetary" in str(endpoint).lower() else "earth-search"
    row = SimpleNamespace(endpoint=endpoint, collection="sentinel-2-l2a", item_id=item.id)
    g = dict(line=None, lines=None, lon=lon, lat=lat, width_m=None)
    res = pq.process_s2(row, g, cfg, pred, outdir)
    crop = _LAST["crop"]
    bands, tfm = crop["bands"], crop["transform"]
    with rasterio.open(outdir / "prob.tif") as ds:
        prob = ds.read(1) / 255.0
    with rasterio.open(outdir / "quality.tif") as ds:
        q = ds.read(1)
    F = fdi(bands)
    water = q == pq.Q_WATER
    # illumination rule (same inputs as studio / ADIS): zenith at crop centre + scene time, B3 of water from rgb.png
    w, s_, e, n = res["bounds_utm"]
    xs, ys = tr.transform([w, e, w, e], [s_, s_, n, n])
    zen = illum.solar_zenith(res["scene_datetime"], [min(xs), min(ys), max(xs), max(ys)])
    b3 = illum.water_median(illum.rgb_png_b3(outdir / "rgb.png", 512), q, 1)
    ok, low, weak = illum.detector_evaluable(zen, b3)
    thr = float(pred.threshold)
    inv = ~tfm
    rows = []
    tmask = np.zeros(q.shape, bool)
    for r in tg.itertuples():
        c, rr = inv * (r.x, r.y)
        c, rr = int(np.floor(c)), int(np.floor(rr))
        inside = 0 <= rr < q.shape[0] and 0 <= c < q.shape[1]
        if inside:
            tmask[rr, c] = True
        rows.append(dict(r._asdict(), row=rr, col=c, inside=inside,
                         p=float(prob[rr, c]) if inside else None, q=int(q[rr, c]) if inside else None,
                         fdi=float(F[rr, c]) if inside else None,
                         b8=float(bands[7][rr, c]) if inside else None))
    bg = water & ~_dilate(tmask, 3)
    fdi_bg = float(np.nanmedian(F[bg])) if bg.sum() > 50 else None
    fdi_bg_p99 = float(np.nanpercentile(F[bg], 99)) if bg.sum() > 50 else None
    for d in rows:
        d["fdi_contrast"] = None if d["fdi"] is None or fdi_bg is None else d["fdi"] - fdi_bg
        d["det"] = bool(d["p"] is not None and d["p"] >= thr and d["q"] == pq.Q_WATER)
        d["det_anyq"] = bool(d["p"] is not None and d["p"] >= thr)
    shift = _best_shift(rows, bands, water)
    _save_targets_png(outdir, bands, F, tmask, prob, thr, date)
    meta.update(status="ok", scene_id=res["scene_id"], scene_datetime=res["scene_datetime"], tile=res["tile"],
                cloud=res.get("scene_cloud_cover"), decision=res["decision"], reason=res["reason"],
                quality=res["quality"], detector=res["detector"], threshold=thr, sun_zenith=zen, water_b3=b3,
                evaluable=ok, low_sun=low, weak_signal=weak, fdi_bg_median=fdi_bg, fdi_bg_p99=fdi_bg_p99,
                water_px=int(water.sum()), det_px_water=int(((prob >= thr) & water).sum()),
                shift_best=shift, near_targets=_near(prob, bands, F, water, tmask, thr, tfm, cx, cy),
                processing_baseline=item.properties.get("s2:processing_baseline"), source=endpoint,
                targets=rows, bounds_utm=res["bounds_utm"], created=time.strftime("%Y-%m-%dT%H:%M:%S"))
    (outdir / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return meta


def _best_shift(rows, bands, water, k=2):
    """Sensitivity only: shift (dr, dc) within +-k px maximising corr(drone plastic fraction, B8 - water median).
    Primary numbers are at the nominal pixel (no shift)."""
    b8 = bands[7]
    med = float(np.nanmedian(b8[water])) if water.any() else 0.0
    rr = np.array([r["row"] for r in rows]); cc = np.array([r["col"] for r in rows])
    fr = np.array([r["frac_plastic"] for r in rows], float)
    if len(rows) < 4 or fr.std() == 0:
        return None
    best = None
    for dr in range(-k, k + 1):
        for dc in range(-k, k + 1):
            y, x = rr + dr, cc + dc
            if (y < 0).any() or (x < 0).any() or (y >= b8.shape[0]).any() or (x >= b8.shape[1]).any():
                continue
            v = b8[y, x] - med
            if not np.isfinite(v).all() or v.std() == 0:
                continue
            c = float(np.corrcoef(fr, v)[0, 1])
            if best is None or c > best[2]:
                best = (dr, dc, round(c, 3))
    c0 = None
    v0 = b8[rr, cc] - med
    if np.isfinite(v0).all() and v0.std() > 0:
        c0 = round(float(np.corrcoef(fr, v0)[0, 1]), 3)
    return dict(dr=best[0], dc=best[1], r=best[2], r_nominal=c0, n=len(rows)) if best else None


def _near(prob, bands, F, water, tmask, thr, tfm, cx, cy, radius_m=400):
    """Scene-level check around the target site (radius 400 m): max P, pixels >= thr, top B8 anomaly in water."""
    H, W = prob.shape
    yy, xx = np.mgrid[0:H, 0:W]
    X, Y = tfm * (xx + 0.5, yy + 0.5)
    near = ((X - cx) ** 2 + (Y - cy) ** 2 <= radius_m ** 2) & water
    if not near.any():
        return None
    b8 = bands[7]
    med = float(np.nanmedian(b8[water]))
    an = np.where(near, b8 - med, -np.inf)
    top = np.argsort(an.ravel())[::-1][:5]
    return dict(n_water=int(near.sum()), p_max=round(float(prob[near].max()), 4), n_ge_thr=int((prob[near] >= thr).sum()),
                fdi_max_contrast=round(float(np.nanmax(F[near]) - np.nanmedian(F[water])), 4),
                top_b8_anomaly=[dict(row=int(k // W), col=int(k % W), b8_anom=round(float(an.ravel()[k]), 4),
                                     p=round(float(prob.ravel()[k]), 4), on_target=bool(tmask.ravel()[k])) for k in top])


def _dilate(m, k):
    from scipy import ndimage
    return ndimage.binary_dilation(m, iterations=k)


def _save_targets_png(outdir, bands, F, tmask, prob, thr, date):
    from PIL import Image
    ys, xs = np.where(tmask) if tmask.any() else (np.array([bands.shape[1] // 2]), np.array([bands.shape[2] // 2]))
    cy, cx = int(ys.mean()), int(xs.mean())
    h = 40
    sl = (slice(max(cy - h, 0), cy + h), slice(max(cx - h, 0), cx + h))
    rgb = np.stack([bands[3][sl], bands[2][sl], bands[1][sl]], -1)
    rgb = (np.clip(rgb / 0.16, 0, 1) ** (1 / 1.8) * 255).astype(np.uint8)
    f = F[sl]
    fn = (np.clip((f + 0.01) / 0.04, 0, 1) * 255).astype(np.uint8)
    fimg = np.stack([fn] * 3, -1)
    p = (np.clip(prob[sl], 0, 1) * 255).astype(np.uint8)
    pimg = np.stack([p, p // 3, p // 3], -1)
    pimg[prob[sl] >= thr] = (255, 255, 0)
    t = tmask[sl]
    for im in (rgb, fimg, pimg):
        edge = t & ~_erode(t)
        im[edge] = (255, 0, 255)
    im = np.concatenate([rgb, np.full((rgb.shape[0], 2, 3), 255, np.uint8), fimg,
                         np.full((rgb.shape[0], 2, 3), 255, np.uint8), pimg], 1)
    Image.fromarray(im).resize((im.shape[1] * 5, im.shape[0] * 5), Image.NEAREST).save(outdir / "targets.png")


def _erode(m):
    from scipy import ndimage
    return ndimage.binary_erosion(m)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs="*", type=int, default=[2018, 2019, 2021, 2022])
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    cfg = pq.load_cfg(ROOT / "configs" / "case_pairs.yaml")
    cfg["strip"]["point_buffer_m"] = 300
    cfg["strip"]["context_m"] = 1200
    from macroplastic.models.lgbm_predict import load_predictor
    pred = load_predictor(ROOT / cfg["detector"]["weights"], harmonize=pq.harmonize_mode(cfg))
    T = pd.concat([targets_2019(), targets_2021_22()], ignore_index=True)
    dates = sorted(set(T[T.year.isin(a.years)].date))
    if 2018 in a.years:
        dates = ["20180607"] + dates
    trows, drows = [], []
    for d in dates:
        t0 = time.time()
        try:
            m = run_date(d, T[T.date == d], cfg, pred, a.force)
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"[error] {d}: {e}\n{traceback.format_exc()[-800:]}", flush=True)
            continue
        for r in m.get("targets", []):
            trows.append(dict(r, scene_id=m.get("scene_id"), evaluable=m.get("evaluable"), fdi_bg_p99=m.get("fdi_bg_p99")))
        det = m.get("detector") or {}
        drows.append(dict(date=d, status=m.get("status"), scene_id=m.get("scene_id"), scene_datetime=m.get("scene_datetime"),
                          cloud=m.get("cloud"), decision=m.get("decision"), reason=m.get("reason"),
                          sun_zenith=m.get("sun_zenith"), water_b3=m.get("water_b3"), evaluable=m.get("evaluable"),
                          n_target_px=m.get("n_target_px"), n_det_crop=det.get("n_det_crop"),
                          det_px_water=m.get("det_px_water"), water_px=m.get("water_px"),
                          fdi_bg_median=m.get("fdi_bg_median"), fdi_bg_p99=m.get("fdi_bg_p99"),
                          source=m.get("source"), baseline=m.get("processing_baseline"),
                          shift=json.dumps(m.get("shift_best")), near=json.dumps(m.get("near_targets"))))
        print(f"{d}: {m.get('status')} {m.get('scene_id')} eval={m.get('evaluable')} n_det_crop={det.get('n_det_crop')} "
              f"{time.time() - t0:.0f} s", flush=True)
    pd.DataFrame(trows).to_csv(ROOT / "out" / "l115" / "plp_bridge_targets.csv", index=False)
    pd.DataFrame(drows).to_csv(ROOT / "out" / "l115" / "plp_bridge_dates.csv", index=False)


if __name__ == "__main__":
    main()
