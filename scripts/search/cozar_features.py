"""Pixel features (level win, 48, same code as scripts/train_lgbm.py) for Cózar 2024 litter windrows (level B).

For selected S2 scenes (tile + date) of data/extra/registry_cozar2024.csv the L2A item of the same acquisition is read
from Earth Search (src/macroplastic/live/stac.py read_crop: STAC scale/offset, no harmonisation, like the service), per
filament a crop = filament bbox + 64 px margin (> MAX_HALO 32) on the exact tile 10 m grid, features
compute_features(..., 'win'). Positive pixels = the catalogue's own filament pixels (pixel_x = row, pixel_y = col,
read by HTTP range from the Zenodo NetCDF). Background pixels (unlabelled, NOT verified D) are sampled from clear water
(SCL 6) of the same crops >= 10 px away from any filament pixel.

Georeference check per filament: L1C TOA B8 of the catalogue vs L2A B8 read at shifted positions (dy, dx in -3..3);
the best shift must be (0, 0) — reported in data/extra/cozar2024/features_check.csv.

  .venv/Scripts/python.exe scripts/search/cozar_features.py --n-scenes 12
Output: data/extra/features_cozar2024.npz  keys X (N,48) float32, names, level ('B'), acq (<tile>_<YYYYMMDD>),
        obj (fil_idx), src, X_bg / acq_bg / obj_bg (background, level 'U' = unlabelled, not for training as D).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "search"))
from macroplastic.features.pixel import compute_features, feature_names  # noqa: E402
from macroplastic.live import stac  # noqa: E402
from cozar_remote import open_remote, read_filament  # noqa: E402

D = ROOT / "data" / "extra" / "cozar2024"
M = 64
BG_PER_FIL = 400


def pick_scenes(reg: pd.DataFrame, n: int) -> pd.DataFrame:
    r = reg[(reg.georef == "stac_transform") & (reg.date >= "2018-06-01")]
    r = r[(r.marida_same_scene.isna() | (r.marida_same_scene == "")) & ~r.marida_same_tile]
    sc = r.groupby(["scene_id", "tile", "date"]).agg(n_fil=("fil_idx", "size"), px=("n_pixels_fil", "sum"),
                                                     lat=("lat_pix", "mean"), lon=("lon_pix", "mean")).reset_index()
    sc = sc.sort_values("px", ascending=False)
    out, per_tile, cells = [], {}, set()
    for s in sc.itertuples():
        cell = (round(s.lat), round(s.lon))  # spread over the basin: <= 1 scene per 1-degree cell, <= 1 per tile
        if per_tile.get(s.tile, 0) >= 1 or cell in cells:
            continue
        out.append(s)
        per_tile[s.tile] = 1
        cells.add(cell)
        if len(out) >= n:
            break
    return pd.DataFrame(out)


def find_l2a(tile, date, lon, lat):
    for src in ("earth-search", "planetary-computer"):
        try:
            its = stac.search(src, lon, lat, f"{date}/{date}", tile=tile)
        except Exception as e:  # noqa
            its = []
        if its:
            return its[0]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-scenes", type=int, default=12)
    ap.add_argument("--max-fil", type=int, default=12, help="max filaments per scene (largest first)")
    ap.add_argument("--max-side", type=int, default=900, help="max crop side (px) of a filament group")
    a = ap.parse_args()
    reg = pd.read_csv(ROOT / "data" / "extra" / "registry_cozar2024.csv", low_memory=False)
    reg["marida_same_scene"] = reg["marida_same_scene"].fillna("")
    scenes = pick_scenes(reg, a.n_scenes)
    print(scenes.to_string())
    rf, f = open_remote()
    names = feature_names("win")
    Xs, acq, obj, src_ = [], [], [], []
    Xb, acqb, objb = [], [], []
    checks = []
    rng = np.random.default_rng(0)
    from scipy import ndimage
    CACHE = D / "feat_cache"
    CACHE.mkdir(exist_ok=True)
    for s in scenes.itertuples():
        t0 = time.time()
        g = reg[reg.scene_id == s.scene_id].sort_values("n_pixels_fil", ascending=False).head(a.max_fil)
        # group filaments into crops of <= a.max_side px per side (one COG read + one feature pass per group)
        groups = []
        for fl in g.itertuples():
            for gr in groups:
                r0 = min(gr["r0"], fl.row_lo); r1 = max(gr["r1"], fl.row_hi); c0 = min(gr["c0"], fl.col_lo); c1 = max(gr["c1"], fl.col_hi)
                if r1 - r0 <= a.max_side and c1 - c0 <= a.max_side:
                    gr.update(r0=r0, r1=r1, c0=c0, c1=c1); gr["fils"].append(fl); break
            else:
                groups.append(dict(r0=fl.row_lo, r1=fl.row_hi, c0=fl.col_lo, c1=fl.col_hi, fils=[fl]))
        item = None
        for gi, gr in enumerate(groups):
            cf = CACHE / f"{s.scene_id}_{gi}_{a.max_fil}_{a.max_side}.npz"
            if not cf.exists():
                if item is None:
                    item = find_l2a(s.tile, s.date, s.lon, s.lat)
                    if item is None:
                        checks.append(dict(scene_id=s.scene_id, status="no L2A item")); break
                    epsg = stac.item_epsg(item)
                fl0 = gr["fils"][0]
                r0, c0 = int(gr["r0"]) - M, int(gr["c0"]) - M
                r1, c1 = int(gr["r1"]) + M + 1, int(gr["c1"]) + M + 1
                bounds = (fl0.ul_x + c0 * 10, fl0.ul_y - r1 * 10, fl0.ul_x + c1 * 10, fl0.ul_y - r0 * 10)
                try:
                    crop = stac.read_crop(item, epsg, bounds, workers=4)
                except Exception as e:  # noqa
                    checks.append(dict(scene_id=s.scene_id, group=gi, status=f"read error {type(e).__name__}")); continue
                bands, scl = crop["bands"], crop["scl"]
                H, W = scl.shape
                feats = compute_features(bands, stac.BANDS, "win")  # (48,H,W)
                fdi = feats[names.index("FDI")]
                allmask = np.zeros((H, W), bool)
                per = []
                for fl in gr["fils"]:
                    px, py, sp = read_filament(f, int(fl.fil_idx), int(fl.n_pixels_fil))
                    rr, cc = px.astype(int) - r0, py.astype(int) - c0
                    ok = (rr >= 0) & (rr < H) & (cc >= 0) & (cc < W)
                    per.append((fl, rr[ok], cc[ok], sp[ok], int(ok.sum())))
                    allmask[rr[ok], cc[ok]] = True
                far = ~ndimage.binary_dilation(allmask, iterations=10)
                water = (scl == 6) & far & np.isfinite(feats).all(0)
                gX, gobj, gXb, gobjb, gchk = [], [], [], [], []
                for fl, rr, cc, sp, nin in per:
                    cor = {}
                    for dy in range(-3, 4):
                        for dx in range(-3, 4):
                            r2, c2 = np.clip(rr + dy, 0, H - 1), np.clip(cc + dx, 0, W - 1)
                            v = bands[7][r2, c2]
                            m = np.isfinite(v)
                            if m.sum() > 5 and np.std(v[m]) > 0 and np.std(sp[m, 7]) > 0:
                                cor[(dy, dx)] = float(np.corrcoef(sp[m, 7], v[m])[0, 1])
                    best = max(cor, key=cor.get) if cor else None
                    X = feats[:, rr, cc].T
                    good = np.isfinite(X).all(1)
                    gX.append(X[good]); gobj += [str(fl.fil_idx)] * int(good.sum())
                    scl_on = np.bincount(scl[rr, cc], minlength=12)
                    gchk.append(dict(scene_id=s.scene_id, l2a_item=item.id, group=gi, fil_idx=int(fl.fil_idx), n_px=int(fl.n_pixels_fil),
                                     n_px_in_crop=nin, n_px_feat=int(good.sum()), best_shift=str(best),
                                     r_at_0=round(cor.get((0, 0), np.nan), 3), r_best=round(cor[best], 3) if best else None,
                                     fdi_fil_median=float(np.nanmedian(fdi[rr, cc])),
                                     fdi_water_median=float(np.nanmedian(fdi[water])) if water.any() else None,
                                     b8_fil_median=float(np.nanmedian(bands[7][rr, cc])),
                                     b8_water_median=float(np.nanmedian(bands[7][water])) if water.any() else None,
                                     scl_water_frac_on_fil=round(float(scl_on[6]) / max(1, len(rr)), 3),
                                     scl_cloud_frac_on_fil=round(float(scl_on[[3, 8, 9, 10]].sum()) / max(1, len(rr)), 3),
                                     lat_pix=fl.lat_pix, lon_pix=fl.lon_pix, status="ok"))
                wr, wc = np.nonzero(water)
                if len(wr):
                    k = rng.choice(len(wr), min(BG_PER_FIL * len(per), len(wr)), replace=False)
                    gXb.append(feats[:, wr[k], wc[k]].T); gobjb += [f"grp{gi}"] * len(k)
                np.savez_compressed(cf, X=np.concatenate(gX).astype(np.float32), obj=np.array(gobj),
                                    Xb=(np.concatenate(gXb) if gXb else np.zeros((0, 48))).astype(np.float32),
                                    objb=np.array(gobjb), checks=np.array(json.dumps(gchk)))
                print(f"  group {gi}: {len(per)} fil, crop {H}x{W}, {time.time() - t0:.0f}s", flush=True)
            z = np.load(cf, allow_pickle=False)
            key = f"{s.tile}_{s.date.replace('-', '')}"
            Xs.append(z["X"]); obj += list(z["obj"]); acq += [key] * len(z["X"]); src_ += ["cozar2024"] * len(z["X"])
            Xb.append(z["Xb"]); objb += [f"{s.scene_id}:{o}" for o in z["objb"]]; acqb += [key] * len(z["Xb"])
            checks += json.loads(str(z["checks"]))
        print(f"scene {s.scene_id}: {len(groups)} groups {time.time() - t0:.0f}s  MB from Zenodo {rf.fetched_bytes / 1e6:.1f}", flush=True)
    pd.DataFrame(checks).to_csv(D / "features_check.csv", index=False)
    X = np.concatenate(Xs).astype(np.float32)
    np.savez_compressed(ROOT / "data" / "extra" / "features_cozar2024.npz", X=X, names=np.array(names),
                        level=np.array(["B"] * len(X)), acq=np.array(acq), obj=np.array(obj), src=np.array(src_),
                        X_bg=np.concatenate(Xb).astype(np.float32) if Xb else np.zeros((0, 48), np.float32),
                        acq_bg=np.array(acqb), obj_bg=np.array(objb), level_bg=np.array(["U"] * len(acqb)),
                        note=np.array("B = Cozar 2024 filament pixels (L2A Earth Search, no harmonisation); "
                                      "X_bg = unlabelled clear water (SCL 6) >=10 px from filaments, NOT verified D"))
    print("B pixels", len(X), "filaments", len(set(obj)), "scenes", len(set(acq)), "bg", len(acqb))


if __name__ == "__main__":
    main()
