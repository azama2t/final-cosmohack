r"""B (positive, level B) pixels in the SERVICE domain (S2 L2A, no harmonisation) from the Cózar et al. 2024 litter-windrow
catalogue (Zenodo 11045944, CC BY 4.0; registry data/extra/registry_cozar2024.csv by L98) - for detector_v2 (L92).

Why: the pair crops give only negatives (D); without positives in L2A we cannot tell whether the detector, silent on
L2A water, would also miss real floating-matter filaments. Cózar filaments were found on L1C by the WASP index and every
filament was accepted by a team of 6 researchers (level B: floating matter = litter + natural, not plastic-specific).

  $env:PYTHONPATH="src"; .venv\Scripts\python.exe scripts\case\detector_v2_cozar.py sample   # random filaments, 1 per scene
  .venv\Scripts\python.exe scripts\case\detector_v2_cozar.py fetch     # pixel coords + L1C spectra (HTTP ranges) + L2A crop
  .venv\Scripts\python.exe scripts\case\detector_v2_cozar.py register  # global pixel->L2A mapping check (spectral correlation)
  .venv\Scripts\python.exe scripts\case\detector_v2_cozar.py features  # win features at filament pixels -> out/detector_v2/features_cozar2024_l2a.npz

Sample: registry rows with georef == stac_transform, not in a MARIDA/MADOS/our scene; SAMPLE_SCENES random S2 scenes
(rng 92), one random filament per scene (no size filter -> no detectability selection).
Registration: for each filament the 13-band L1C spectra of its pixels (from the NetCDF) are correlated with the L2A bands
at the mapped pixel positions over shifts of +-SEARCH px; the mapping rule (axis order, shift) is chosen GLOBALLY as the
mode of per-filament best shifts, and then applied to ALL sampled filaments (per-filament fits are not used to drop
filaments, so faint filaments are not selectively removed).
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""
# the first run hung >15 min on Planetary Computer blob reads without timeouts -> bounded HTTP waits
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "60")
os.environ.setdefault("GDAL_HTTP_CONNECTTIMEOUT", "20")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import requests  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "case"))

REG = ROOT / "data" / "extra" / "registry_cozar2024.csv"
TILES = ROOT / "data" / "extra" / "cozar2024" / "tile_origins.csv"
WORK = ROOT / "out" / "detector_v2"
CROPS = WORK / "cozar_crops"
URL = ("https://zenodo.org/api/records/11045944/files/"
       "WASP_LW_SENT2_MED_L1C_B_201506_202109_10m_6y_NRT_v1.0.nc/content")
NMAX = 2563
OFF = {"pixel_x": 1530968, "pixel_y": 75212092, "pixel_spec": 148893216}
SAMPLE_SCENES = 240
MARGIN = 48       # px around the filament bbox for window features (largest window 31 + subsampled median)
SEARCH = 120      # +- px crop margin kept for a wider search if the local one fails (file lat/lon vs pixel mapping differ by ~1 km)
REG_SEARCH = 10   # +- px of the registration search (pilot: r = 0.999 / 1.000 at shift 0 on the first 2 filaments)
L1C_BANDS = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B10", "B11", "B12"]
REG_BANDS = ["B2", "B3", "B4", "B8"]


def _range(session, a, n):
    for attempt in range(5):
        try:
            r = session.get(URL, headers={"Range": f"bytes={a}-{a + n - 1}"}, timeout=60)
            if r.status_code == 206 and len(r.content) == n:
                return r.content
        except requests.RequestException:
            pass
        time.sleep(2 * (attempt + 1))
    raise IOError(f"range {a}+{n} failed")


def read_pixels(session, i: int, n: int):
    """pixel_x, pixel_y (int16 LE) and pixel_spec (float32 LE, 13 bands) of filament i (first n pixels)."""
    x = np.frombuffer(_range(session, OFF["pixel_x"] + i * NMAX * 2, n * 2), "<i2").astype(np.int32)
    y = np.frombuffer(_range(session, OFF["pixel_y"] + i * NMAX * 2, n * 2), "<i2").astype(np.int32)
    s = np.frombuffer(_range(session, OFF["pixel_spec"] + i * NMAX * 13 * 4, n * 13 * 4), "<f4").reshape(n, 13)
    return x, y, s.astype(np.float32)


def cmd_sample():
    d = pd.read_csv(REG)
    ok = (d.level == "B") & (d.georef == "stac_transform") & d.marida_same_scene.isna() & d.mados_same_scene.isna() & d.ours_same_scene.isna()
    d = d[ok]
    rng = np.random.default_rng(92)
    scenes = sorted(d.scene_id.unique())
    pick = rng.choice(len(scenes), size=min(SAMPLE_SCENES, len(scenes)), replace=False)
    rows = []
    for k in sorted(pick):
        g = d[d.scene_id == scenes[k]]
        rows.append(g.iloc[int(rng.integers(len(g)))])
    s = pd.DataFrame(rows)
    s["acq"] = s["tile"] + "_" + s["date"].str.replace("-", "")
    WORK.mkdir(parents=True, exist_ok=True)
    s.to_csv(WORK / "cozar_sample.csv", index=False)
    print(f"sample: {len(s)} filaments from {len(s)} scenes (of {len(scenes)} eligible); n_px median {s.n_pixels_fil.median():.0f}")


def find_l2a(row):
    """L2A item of the SAME datatake: Earth Search first (s2:datatake_id), Planetary Computer as fallback (id match)."""
    from macroplastic.live import stac
    prod = row.scene_id  # S2A_MSIL1C_20160217T111122_N0201_R137_T29SQA_20160217T111843
    parts = prod.split("_")
    sat, dtake, orbit, tile = parts[0], parts[2], parts[4], parts[5][1:]
    day = f"{dtake[:4]}-{dtake[4:6]}-{dtake[6:8]}"
    why = []
    for src in ("earth-search", "planetary-computer"):
        items = None
        for attempt in range(3):
            try:
                cl = stac.open_client(src)
                items = list(cl.search(collections=["sentinel-2-l2a"], intersects=dict(type="Point", coordinates=[row.lon_pix, row.lat_pix]),
                                       datetime=day, max_items=50).items())
                break
            except Exception:  # noqa: BLE001
                time.sleep(3 * (attempt + 1))
        if items is None:
            why.append(f"{src}: search failed")
            continue
        if src == "earth-search":
            items = [it for it in items if stac.tile_of(it) == tile
                     and str(it.properties.get("s2:datatake_id", "")).startswith(f"G{sat}_{dtake}")]
        else:
            items = [it for it in items if stac.tile_of(it) == tile and f"_{dtake}_" in it.id and f"_{orbit}_" in it.id]
        if items:
            return sorted(items, key=lambda it: it.id)[-1], ""
        why.append(f"{src}: no L2A of the same datatake")
    return None, "; ".join(why)


def fetch_one(args):
    row, tiles = args
    from macroplastic.live import stac
    out = CROPS / f"{int(row.fil_idx):05d}.npz"
    if out.is_file():
        return int(row.fil_idx), "cached"
    s = requests.Session()
    n = int(row.n_pixels_fil)
    try:
        px, py, spec = read_pixels(s, int(row.fil_idx), n)
    except Exception as e:  # noqa: BLE001
        return int(row.fil_idx), f"netcdf read failed: {e}"
    item, why = find_l2a(row)
    if item is None:
        return int(row.fil_idx), why
    t = tiles.loc[row.tile]
    epsg = int(t.epsg)
    if stac.item_epsg(item) != epsg:
        return int(row.fil_idx), f"epsg mismatch {stac.item_epsg(item)} vs {epsg}"
    # pixel_x = image row, pixel_y = image column (L98, checked against the registry limits); shifts +-SEARCH are
    # searched in the registration step
    rlo, rhi = int(px.min()) - MARGIN - SEARCH, int(px.max()) + MARGIN + SEARCH + 1
    clo, chi = int(py.min()) - MARGIN - SEARCH, int(py.max()) + MARGIN + SEARCH + 1
    if (rhi - rlo) * (chi - clo) > 4_000_000:
        return int(row.fil_idx), f"window too large {(rhi - rlo)}x{(chi - clo)}"
    x0 = float(t.ul_x) + 10 * clo
    x1 = float(t.ul_x) + 10 * chi
    y1 = float(t.ul_y) - 10 * rlo
    y0 = float(t.ul_y) - 10 * rhi
    try:
        crop = stac.read_crop(item, epsg, [x0, y0, x1, y1], workers=6)
    except Exception as e:  # noqa: BLE001
        return int(row.fil_idx), f"read_crop failed: {type(e).__name__}"
    b = crop["bands"]
    enc = np.where(np.isfinite(b), np.clip(np.round(b * 10000) + 1000, 1, 65535), 0).astype(np.uint16)
    np.savez_compressed(out, bands=enc, scl=crop["scl"], px=px, py=py, spec=spec, rlo=rlo, clo=clo, item=item.id)
    return int(row.fil_idx), "ok"


def cmd_fetch(shard: int = 0, n_shards: int = 1):
    """Sequential per process (threads + GDAL/PC signing hung twice for >15 min); run n_shards processes in parallel."""
    s = pd.read_csv(WORK / "cozar_sample.csv")
    s = s.iloc[shard::n_shards]
    tiles = pd.read_csv(TILES).set_index("tile")
    CROPS.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    res = []
    for k, r in enumerate(s.itertuples()):
        fi, st = fetch_one((r, tiles))
        res.append((fi, st))
        print(f"[fetch {shard}/{n_shards}] {k + 1}/{len(s)} fil {fi}: {st} ({time.time() - t0:.0f}s)", flush=True)
        pd.DataFrame(res, columns=["fil_idx", "status"]).to_csv(WORK / f"cozar_fetch_status_{shard}.csv", index=False)
    ok = sum(st in ("ok", "cached") for _, st in res)
    print(f"[fetch {shard}/{n_shards}] {ok}/{len(res)} crops ({time.time() - t0:.0f}s)")


def fetched_ids():
    s = pd.read_csv(WORK / "cozar_sample.csv")
    have = {int(p.stem) for p in CROPS.glob("*.npz")}
    return [int(f) for f in s.fil_idx if int(f) in have]


def load_crop(fi):
    z = np.load(CROPS / f"{int(fi):05d}.npz", allow_pickle=False)
    enc = z["bands"].astype(np.float32)
    b = np.where(enc > 0, (enc - 1000) / 10000.0, np.nan).astype(np.float32)
    return b, z["scl"], z["px"], z["py"], z["spec"], int(z["rlo"]), int(z["clo"])


def _zs(v):
    v = v - np.nanmean(v, axis=0, keepdims=True)
    sd = np.nanstd(v, axis=0, keepdims=True)
    return v / np.where(sd > 0, sd, np.nan)


def register_one(fi):
    """Best (axis order, dy, dx) by correlation of L1C spectra (REG_BANDS) with L2A bands at shifted pixel positions."""
    from macroplastic.live import stac
    b, scl, px, py, spec, rlo, clo = load_crop(fi)
    H, W = b.shape[1:]
    li = [L1C_BANDS.index(k) for k in REG_BANDS]
    ai = [stac.BANDS.index(k) for k in REG_BANDS]
    ref = _zs(spec[:, li].astype(np.float64))  # (n, 4)
    best = {}
    for order, (rr, cc) in {"x_row": (px, py)}.items():
        r = rr - rlo
        c = cc - clo
        sh = np.arange(-REG_SEARCH, REG_SEARCH + 1)
        grid = np.full((len(sh), len(sh)), np.nan)
        for iy, dy in enumerate(sh):
            R = r + dy
            okr = (R >= 0) & (R < H)
            for ix, dx in enumerate(sh):
                C = c + dx
                ok = okr & (C >= 0) & (C < W)
                if ok.sum() < max(5, 0.8 * len(r)):
                    continue
                v = b[ai][:, R[ok], C[ok]].T.astype(np.float64)
                vz = _zs(v)
                m = np.isfinite(vz).all(1) & np.isfinite(ref[ok]).all(1)
                if m.sum() < 5:
                    continue
                grid[iy, ix] = float(np.nanmean(vz[m] * ref[ok][m]))
        if np.isfinite(grid).any():
            k = np.nanargmax(grid)
            iy, ix = divmod(int(k), grid.shape[1])
            g2 = grid.copy()
            g2[max(0, iy - 3):iy + 4, max(0, ix - 3):ix + 4] = np.nan
            best[order] = {"dy": int(sh[iy]), "dx": int(sh[ix]), "r": round(float(grid[iy, ix]), 3),
                           "r_second": round(float(np.nanmax(g2)), 3) if np.isfinite(g2).any() else None,
                           "r_at_0": round(float(grid[REG_SEARCH, REG_SEARCH]), 3) if np.isfinite(grid[REG_SEARCH, REG_SEARCH]) else None}
    return int(fi), best


def cmd_register():
    fis = fetched_ids()
    t0 = time.time()
    with ThreadPoolExecutor(8) as ex:
        res = dict(ex.map(register_one, fis))
    rows = []
    for fi, bo in res.items():
        for order, v in bo.items():
            rows.append(dict(fil_idx=fi, order=order, **v))
    df = pd.DataFrame(rows)
    df.to_csv(WORK / "cozar_register.csv", index=False)
    good = df[(df.r >= 0.6) & (df.r - df.r_second.fillna(0) >= 0.1)]
    mode = good.groupby(["order", "dy", "dx"]).size().sort_values(ascending=False)
    print(mode.head(10))
    top = mode.index[0] if len(mode) else None
    rule = {"order": top[0], "dy": int(top[1]), "dx": int(top[2]), "n_support": int(mode.iloc[0]),
            "n_confident": int(len(good)), "n_filaments": int(len(res)),
            "share_of_confident": round(float(mode.iloc[0] / max(len(good), 1)), 3)} if top else None
    (WORK / "cozar_register_rule.json").write_text(json.dumps(rule, indent=1), encoding="utf-8")
    print(f"rule {rule} ({time.time() - t0:.0f}s)")


def feat_one(args):
    fi, rule = args  # rule: global {'order','dy','dx'} or a per-filament override with the same keys
    from macroplastic.features.pixel import compute_features_blocked
    from macroplastic.live import stac
    from macroplastic.models.lgbm_predict import load_predictor
    b, scl, px, py, spec, rlo, clo = load_crop(fi)
    rr, cc = (px, py) if rule["order"] == "x_row" else (py, px)
    r = rr - rlo + rule["dy"]
    c = cc - clo + rule["dx"]
    H, W = b.shape[1:]
    ok = (r >= 0) & (r < H) & (c >= 0) & (c < W)
    r, c = r[ok], c[ok]
    sel = np.zeros((H, W), bool)
    sel[r, c] = True
    out = {}
    href = load_predictor(ROOT / "weights" / "lgbm", harmonize="water_median", num_threads=1)
    for mode in ("none", "water_median"):
        arr, names = (b, stac.BANDS) if mode == "none" else href._harmonized(b, stac.BANDS, scl == 6)
        rows = []
        for (y0, y1, x0, x1), f in compute_features_blocked(arr, names, "win"):
            m = sel[y0:y1, x0:x1]
            if m.any():
                rows.append(f[:, m].T.copy())
        out[mode] = np.concatenate(rows) if rows else np.zeros((0, 48), np.float32)
    scl_at = scl[sel]
    return fi, out, int(sel.sum()), {int(k): int(v) for k, v in zip(*np.unique(scl_at, return_counts=True))}


def cmd_features():
    from macroplastic.features.pixel import feature_names
    rule = json.loads((WORK / "cozar_register_rule.json").read_text(encoding="utf-8"))
    s = pd.read_csv(WORK / "cozar_sample.csv").set_index("fil_idx")
    reg = pd.read_csv(WORK / "cozar_register.csv").set_index("fil_idx")
    fis = fetched_ids()
    # Registration found 0/0 for all 2015-2017 products (N0201-N0205) but 1-3 px offsets for 30 % of 2018-2021 products
    # (the L2A available today is a later processing than the L1C used by Cózar). Per-filament shift if the match is
    # confident (r >= 0.6 and >= 0.1 above the best shift >3 px away), else the global rule. No filament is dropped.
    rules, n_own = {}, 0
    for fi in fis:
        if fi in reg.index:
            g = reg.loc[fi]
            if g.r >= 0.6 and g.r - (g.r_second if pd.notna(g.r_second) else 0) >= 0.1:
                rules[fi] = {"order": "x_row", "dy": int(g.dy), "dx": int(g.dx)}
                n_own += int(g.dy != rule["dy"] or g.dx != rule["dx"])
                continue
        rules[fi] = rule
    t0 = time.time()
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(feat_one, [(fi, rules[fi]) for fi in fis]))
    X, Xw, acq, obj, scl_hist = [], [], [], [], {}
    for fi, out, n, sh in res:
        if not n:
            continue
        X.append(out["none"]); Xw.append(out["water_median"])
        acq += [s.loc[fi, "acq"]] * n
        obj += [f"cozar:{fi}"] * n
        for k, v in sh.items():
            scl_hist[k] = scl_hist.get(k, 0) + v
    X = np.concatenate(X); Xw = np.concatenate(Xw)
    np.savez_compressed(WORK / "features_cozar2024_l2a.npz", X=X, X_wm=Xw, names=np.array(feature_names("win")),
                        level=np.array(["B"] * len(X)), acq=np.array(acq), obj=np.array(obj))
    info = {"n_filaments": len({o for o in obj}), "n_px": int(len(X)), "rule": rule,
            "n_per_filament_shift_nonzero": n_own, "n_global_rule": int(sum(rules[f] is rule for f in fis)),
            "scl_at_filament_px": scl_hist,
            "source": "Cózar et al. 2024 (Zenodo 11045944, CC BY 4.0), level B; L2A of the same datatake from Planetary Computer; "
                      "features = win, no harmonisation (X) and water_median (X_wm)"}
    (WORK / "features_cozar2024_l2a.json").write_text(json.dumps(info, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False), f"({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    if sys.argv[1] == "fetch":
        cmd_fetch(*(int(v) for v in sys.argv[2:4]))
    else:
        {"sample": cmd_sample, "register": cmd_register, "features": cmd_features}[sys.argv[1]]()
