"""Audit L107: independent checks of Cozar-related claims (read-only; writes only reports/audit/*).

1. Leakage: Cozar 2024 scenes (tile+date, tile, footprint) vs MARIDA train/val/test and MADOS (train/val/test, footprints).
2. "36 % of filaments": reproduce with weights/lgbm on out/detector_v2/extra_norm.npz, then re-estimate CI with
   clusters = tile+date, date (one pass over the Med), 1-degree cell, year; sensitivity to the "any pixel" rule.
3. Sets used by L92/L98 (to exclude from demo): tile+dates in features_cozar2024.npz and features_cozar2024_l2a.npz.
Output: reports/audit/cozar_check.json (+ printed summary).
"""
import json, re, sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "audit"
rng = np.random.default_rng(0)


def marida_scenes():
    sp = {}
    for s in ("train", "val", "test"):
        for ln in (ROOT / "data/MARIDA/splits" / f"{s}_X.txt").read_text().split():
            d, tile = ln.rsplit("_", 1)[0].split("_")
            sp.setdefault((tile, d), set()).add(s)
    rows = []
    for (tile, d), ss in sp.items():
        dd, mm, yy = d.split("-")
        rows.append({"tile": tile, "date": f"20{int(yy):02d}-{int(mm):02d}-{int(dd):02d}", "splits": ",".join(sorted(ss))})
    return pd.DataFrame(rows)


def mados_footprints():
    import rasterio
    from rasterio.warp import transform_bounds
    sp = {}
    for s in ("train", "val", "test"):
        for ln in (ROOT / "data/MADOS/splits" / f"{s}_X.txt").read_text().split():
            sc = "_".join(ln.split("_")[:2])
            sp.setdefault(sc, set()).add(s)
    rows = []
    for d in sorted((ROOT / "data/MADOS").glob("Scene_*")):
        fs = sorted((d / "10").glob("*.tif")) if (d / "10").is_dir() else sorted(d.rglob("*.tif"))
        if not fs:
            continue
        b = [1e9, 1e9, -1e9, -1e9]
        for f in fs:
            with rasterio.open(f) as r:
                if r.crs is None:
                    continue
                x0, y0, x1, y1 = transform_bounds(r.crs, "EPSG:4326", *r.bounds)
                b = [min(b[0], x0), min(b[1], y0), max(b[2], x1), max(b[3], y1)]
        rows.append({"scene": d.name, "lon0": b[0], "lat0": b[1], "lon1": b[2], "lat1": b[3],
                     "splits": ",".join(sorted(sp.get(d.name, {"none"})))})
    return pd.DataFrame(rows)


def cluster_boot(hit, cl, n=5000):
    hit = np.asarray(hit, float)
    u, inv = np.unique(cl, return_inverse=True)
    k = np.bincount(inv, weights=hit)
    m = np.bincount(inv)
    res = []
    for _ in range(n):
        i = rng.integers(0, len(u), len(u))
        res.append(k[i].sum() / m[i].sum())
    return {"n_clusters": int(len(u)), "rate": round(float(hit.mean()), 4),
            "ci95": [round(float(np.percentile(res, 2.5)), 4), round(float(np.percentile(res, 97.5)), 4)]}


def main():
    reg = pd.read_csv(ROOT / "data/extra/registry_cozar2024.csv")
    reg["key"] = reg.tile + "_" + reg.date.str.replace("-", "")
    res = {}
    # ---------------- 1. leakage
    mar = marida_scenes()
    mar["key"] = mar.tile + "_" + mar.date.str.replace("-", "")
    same_scene = reg[reg.key.isin(set(mar.key))]
    same_tile = reg[reg.tile.isin(set(mar.tile))]
    res["marida"] = {"n_scenes": len(mar), "cozar_same_tile_date": int(same_scene.key.nunique()),
                     "cozar_same_tile": sorted(set(same_tile.tile)),
                     "marida_tiles_in_med_like_box": sorted(set(mar.tile) & set(reg.tile))}
    mad = pd.DataFrame(columns=["lon0", "lat0", "lon1", "lat1", "scene", "splits"])  # MADOS GeoTIFFs carry no geotransform (checked)
    hits = []
    lon, lat = reg.lon_centroid.values, reg.lat_centroid.values
    for _, r in mad.iterrows():
        m = (lon >= r.lon0) & (lon <= r.lon1) & (lat >= r.lat0) & (lat <= r.lat1)
        if m.any():
            hits.append({"scene": r.scene, "splits": r.splits, "n_cozar_filaments_in_bbox": int(m.sum()),
                         "cozar_keys": sorted(set(reg.key[m]))[:20]})
    res["mados"] = {"n_scenes": len(mad), "scenes_whose_bbox_contains_cozar_filaments": hits,
                    "note": "MADOS GeoTIFFs: identity transform, anonymous Scene_N -> no tile/date/footprint; only content (LBP) check possible"}
    # ---------------- 3. sets used in experiments
    z98 = np.load(ROOT / "data/extra/features_cozar2024.npz", allow_pickle=False)
    z92 = np.load(ROOT / "out/detector_v2/features_cozar2024_l2a.npz", allow_pickle=False)
    used98, used92 = sorted(set(z98["acq"])), sorted(set(z92["acq"]))
    res["used_L98_tile_dates"] = used98
    res["used_L92_tile_dates_n"] = len(used92)
    (OUT / "cozar_used_in_experiments.json").write_text(json.dumps({"L98_features_cozar2024": used98,
                                                                    "L92_features_cozar2024_l2a": used92}, indent=1))
    # ---------------- 2. 36 % recompute
    import lightgbm as lgb
    meta = json.loads((ROOT / "weights/lgbm/meta.json").read_text(encoding="utf-8"))
    b = lgb.Booster(model_file=str(ROOT / "weights/lgbm/model.txt"))
    ex = np.load(ROOT / "out/detector_v2/extra_norm.npz", allow_pickle=False)
    names = list(meta["features"])
    m = ex["src"] == "features_cozar2024_l2a"
    X = ex["X"][m]
    s = b.predict(X[:, [names.index(n) for n in b.feature_name()]])
    thr = meta["threshold"]
    obj, acq = ex["obj"][m], ex["acq"][m]
    df = pd.DataFrame({"obj": obj, "acq": acq, "flag": s >= thr})
    g = df.groupby("obj").agg(acq=("acq", "first"), n_px=("flag", "size"), n_flag=("flag", "sum")).reset_index()
    g["fil_idx"] = g.obj.str.split(":").str[-1].astype(int)
    g = g.merge(reg[["fil_idx", "tile", "date", "rel_orbit", "lat_centroid", "lon_centroid", "n_pixels_fil"]], on="fil_idx", how="left")
    g["hit"] = g.n_flag > 0
    g["cell1"] = np.floor(g.lat_centroid).astype(int).astype(str) + "_" + np.floor(g.lon_centroid).astype(int).astype(str)
    g["year"] = g.date.str[:4]
    g["dt_orbit"] = g.date + "_R" + g.rel_orbit.astype(str)
    r2 = {"threshold": thr, "n_filaments": int(len(g)), "hits": int(g.hit.sum()),
          "n_tile_dates": int(g.acq.nunique()), "n_dates": int(g.date.nunique()), "n_date_orbits": int(g.dt_orbit.nunique()),
          "n_cells_1deg": int(g.cell1.nunique())}
    for cl in ("acq", "dt_orbit", "date", "cell1", "year"):
        r2[f"cluster_{cl}"] = cluster_boot(g.hit, g[cl].values)
    for k in (1, 2, 3, 5):
        r2[f"recall_ge_{k}px"] = round(float((g.n_flag >= k).mean()), 4)
    g["frac"] = g.n_flag / g.n_px
    r2["recall_frac_ge_10pct"] = round(float((g.frac >= 0.10).mean()), 4)
    r2["pixel_rate"] = round(float(df.flag.mean()), 4)
    r2["by_year"] = {y: [int(v.hit.sum()), int(len(v))] for y, v in g.groupby("year")}
    r2["hit_rate_by_filament_size_tercile"] = {str(k): [int(v.hit.sum()), int(len(v))]
                                               for k, v in g.groupby(pd.qcut(g.n_px, 3, labels=["small", "mid", "large"]))}
    res["recall_36"] = r2
    g.to_csv(OUT / "cozar_l92_per_filament.csv", index=False)
    (OUT / "cozar_check.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "used_L98_tile_dates"}, indent=1, ensure_ascii=False, default=str)[:6000])


if __name__ == "__main__":
    main()
