"""L33: scene-relative features (wrapper over macroplastic.features.pixel; pixel.py itself is not changed).

Two families of EXTRA features (added to the 48 'win' features, never replacing them):

  scene z-scores ("zs_<k>"): (x_k - median_water_scene_k) / MAD_water_scene_k for k in ZS_KEYS
      (11 bands + FDI, FAI, NDVI, NDWI). Open water of the scene is found by a spectral rule only (no labels):
        candidates = all bands finite & NDWI > 0 & B8 < 0.1 & B11 < 0.05
        water      = candidates with B8 <= median(B8 of candidates in the scene)   (the darker half: drops
                     debris, foam, algae, glint, turbid plumes that are still NDWI > 0)
      stats pooled over every patch of the scene (MARIDA: S2_<date>_<tile>; MADOS: Scene_k; product: the whole
      input tile). Fallback if < MIN_WATER px: candidates without the darker-half step; if still too few: the
      pooled training reference (DEFAULT_STATS, set at cache time) - recorded per scene.
  scene differences ("zd_<k>"): x_k - median_water_scene_k (no scaling) - same water pixels.
  wide windows ("<k>_dmed<w>"): value - local median in larger windows: B8/FDI/NDVI 63, NDWI 31/63
      (pixel._local_median, the same approximate subsampled median as the existing *_dmed15/31 features).

Caches (out/l33_cache/): per split, extra features of all labelled px in the SAME order as out/l3_cache (MARIDA)
and out/l13_cache (MADOS); checked against the cached B8 column. MARIDA test is never read.
"""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from macroplastic.features import pixel as PX  # noqa: E402

CACHE = ROOT / "out" / "l33_cache"
THREADS = int(os.environ.get("L33_THREADS", "10"))
ZS_KEYS = PX.BANDS11 + ["FDI", "FAI", "NDVI", "NDWI"]
WIDE = [("B8", 63), ("FDI", 63), ("NDVI", 63), ("NDWI", 31), ("NDWI", 63)]
WIDE_NAMES = [f"{k}_dmed{w}" for k, w in WIDE]
MIN_WATER = 500
FLOOR = np.array([1e-4] * 13 + [1e-3, 1e-3], np.float32)  # MAD floor: reflectance-like / ratio keys
WATER_SUB = 2  # stride of the water sample inside a patch


def key_stack(x: np.ndarray) -> np.ndarray:
    """(11,H,W) band stack (BANDS11 order) -> (15,H,W) ZS_KEYS values (same formulas as pixel.py)."""
    b = {n: x[i] for i, n in enumerate(PX.BANDS11)}
    ind = PX._indices(b, ["FDI", "FAI", "NDVI", "NDWI"])
    return np.concatenate([x, np.stack([ind[k] for k in ("FDI", "FAI", "NDVI", "NDWI")])], 0)


def water_candidates(x: np.ndarray, sub: int = WATER_SUB) -> np.ndarray:
    """(15, n) key values of water-candidate pixels on a stride-`sub` grid (no labels used)."""
    xs = x[:, ::sub, ::sub]
    k = key_stack(xs)
    fin = np.isfinite(k).all(0)
    with np.errstate(invalid="ignore"):
        m = fin & (k[14] > 0) & (xs[7] < 0.1) & (xs[9] < 0.05)
    return k[:, m]


def water_stats(cands: np.ndarray, default=None):
    """cands (15, n) pooled over a scene -> (median (15,), mad (15,), n_used, mode)."""
    n = cands.shape[1]
    if n >= 2 * MIN_WATER:
        thr = np.median(cands[7])
        w = cands[:, cands[7] <= thr]
        mode = "dark_half"
    elif n >= MIN_WATER:
        w, mode = cands, "candidates"
    else:
        if default is None:
            return None, None, n, "none"
        return default[0], default[1], n, "default"
    med = np.median(w, 1).astype(np.float32)
    mad = (1.4826 * np.median(np.abs(w - med[:, None]), 1)).astype(np.float32)
    return med, np.maximum(mad, FLOOR), int(w.shape[1]), mode


def wide_features(x: np.ndarray) -> np.ndarray:
    """(11,H,W) -> (len(WIDE),H,W) value - local median in wide windows; NaN where any band is NaN."""
    b = {n: x[i] for i, n in enumerate(PX.BANDS11)}
    ind = PX._indices(b, ["FDI", "NDVI", "NDWI"])
    src = {"B8": b["B8"], **ind}
    out = np.empty((len(WIDE),) + x.shape[1:], np.float32)
    fills = {}
    for j, (k, w) in enumerate(WIDE):
        if k not in fills:
            fills[k] = PX._nan_fill(src[k])
        out[j] = src[k] - PX._local_median(src[k], w, fills[k])
    nanpix = np.isnan(x).any(0)
    if nanpix.any():
        out[:, nanpix] = np.nan
    return out


def scene_features(keys_px: np.ndarray, med: np.ndarray, mad: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """keys_px (n,15) -> zs (n,15), zd (n,15)."""
    d = keys_px - med[None, :]
    return (d / mad[None, :]).astype(np.float32), d.astype(np.float32)


def zs_names():
    return [f"zs_{k}" for k in ZS_KEYS]


def zd_names():
    return [f"zd_{k}" for k in ZS_KEYS]


# ------------------------------------------------------------------ product-side reference implementation
def tile_scene_stats(arr: np.ndarray, channel_names, sub: int = 4, default=None):
    """Whole input tile -> water stats (what the product would do once per tile, before the blocked pass)."""
    x = PX.select_bands(arr, channel_names)
    x[~np.isfinite(x)] = np.nan
    return water_stats(water_candidates(x, sub), default)


def tile_extra_block(xblk: np.ndarray, med, mad, wide: bool = True, zs: bool = True):
    """Extra features of one block (11,h,w) given the tile stats (zs first, then wide)."""
    parts = []
    if zs:
        k = key_stack(xblk)
        parts.append((k - med[:, None, None]) / mad[:, None, None])
    if wide:
        parts.append(wide_features(xblk))
    return np.concatenate(parts, 0).astype(np.float32)


# ------------------------------------------------------------------ cache builders
def _marida_patch(name):
    import l16_common as C

    img, cl, _, _, _ = C.read_patch(name)
    x = PX.select_bands(img, PX.BANDS11)
    x[~np.isfinite(x)] = np.nan
    m = cl > 0
    wf = wide_features(x)[:, m].T.copy()
    return wf, x[7][m].copy(), water_candidates(x)


def _mados_patch(name):
    from macroplastic.data import mados as MD

    img, cl, _, _ = MD.load_patch(name, extra=True)
    x = PX.select_bands(img, MD.BAND_NAMES)
    x[~np.isfinite(x)] = np.nan
    m = cl > 0
    wf = wide_features(x)[:, m].T.copy()
    return wf, x[7][m].copy(), water_candidates(x)


def build(kind: str, split: str):
    """kind marida|mados -> out/l33_cache/<kind>_<split>.npz: wide (n,5), per-patch scene id, scene water cands."""
    import l16_common as C

    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{kind}_{split}.npz"
    if path.is_file():
        return np.load(path, allow_pickle=False)
    t = time.time()
    if kind == "marida":
        names = C.split_names(split)
        z = np.load(C.L3_CACHE / f"{split}_win.npz")
        scenes = [C.parse(n)["scene"] for n in names]
        fn = _marida_patch
    else:
        z = np.load(C.L13_CACHE / f"mados_{split}_win.npz")
        names = [str(p) for p in z["patches"]]
        scenes = [p.rsplit("_", 1)[0] for p in names]
        fn = _mados_patch
    with ProcessPoolExecutor(max_workers=THREADS) as ex:
        res = list(ex.map(fn, names, chunksize=4))
    wide = np.concatenate([r[0] for r in res]).astype(np.float32)
    b8 = np.concatenate([r[1] for r in res])
    Xc = z["X"]
    assert len(wide) == len(Xc), (len(wide), len(Xc))
    ok = np.isclose(b8, Xc[:, 7], equal_nan=True, rtol=0, atol=1e-7)
    assert ok.all(), f"order mismatch {kind} {split}: {np.count_nonzero(~ok)}"
    # water candidates pooled per scene
    cand = {}
    for s, r in zip(scenes, res):
        cand.setdefault(s, []).append(r[2])
    sc_names = sorted(cand)
    sc_cands = [np.concatenate(cand[s], 1) for s in sc_names]
    lens = np.array([c.shape[1] for c in sc_cands])
    np.savez(path, wide=wide, patch_scene=np.array(scenes), scenes=np.array(sc_names), cand_len=lens,
             cands=np.concatenate(sc_cands, 1).astype(np.float32))
    print(f"[l33 cache] {kind} {split}: {len(names)} patches, {len(wide)} px, {len(sc_names)} scenes, "
          f"{time.time() - t:.0f}s", flush=True)
    return np.load(path, allow_pickle=False)


def scene_stats_from_cache(z):
    """dict scene -> (cands (15,n)); at most `cap` candidates per scene (regular stride, deterministic)."""
    cap = 200000
    out, o = {}, 0
    cands = z["cands"]  # load once
    for s, n in zip(z["scenes"], z["cand_len"]):
        step = max(1, int(np.ceil(n / cap)))
        out[str(s)] = cands[:, o:o + n:step].copy()
        o += n
    del cands
    return out


if __name__ == "__main__":
    for kind in ("marida", "mados"):
        for split in ("train", "val"):
            build(kind, split)
