"""Content check new scenes (PLP, FloatingObjects crops) vs MADOS and MARIDA (all splits) without tile/date.

MADOS GeoTIFFs have no georeference and anonymous scene names, so tile+date matching is impossible
(reports/l13_mados.md). We reuse the radiometry-robust 48-bit local binary pattern (B8, B4; 5x5) from
scripts/train_lgbm_mados.py: index MADOS + MARIDA pixels (stride 2), every pixel of a new image votes for
(reference image, dy, dx). The same acquisition gives hundreds..thousands of votes at one offset; random hits <= 7.

  .venv/Scripts/python.exe scripts/extra_data/mados_content_check.py  -> reports/extra_data/content_overlap.csv
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
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

import rasterio  # noqa: E402

from train_lgbm_mados import lbp_key, _mados_b4b8  # noqa: E402
from macroplastic.data import mados as MD  # noqa: E402

OUT = ROOT / "reports" / "extra_data" / "content_overlap.csv"
IDX = ROOT / "out" / "l89_ref_lbp_index.npz"
MIN_VOTES = 8
# the same acquisition gives 100s-1000s of votes (control: live durban 2019-04-24 vs MARIDA 36JUN = 7 579);
# unrelated images 1-2 (PLP). "second" = next best (image, offset) - often another patch of the same scene.
MATCH_VOTES = 30
TILE = 4096  # new images are processed in tiles (FO crops up to 2048 px)


def _ref_keys(args):
    i, kind, name = args
    if kind == "MADOS":
        b4, b8 = _mados_b4b8(name)
    else:
        with rasterio.open(name) as ds:
            a = ds.read([4, 8]).astype(np.float32)
        b4, b8 = a[0], a[1]
    key, ok = lbp_key(b4, b8)
    ok[1::2, :] = False
    ok[:, 1::2] = False
    ok &= key != 0  # flat water (all-zero pattern) carries no information
    yy, xx = np.nonzero(ok)
    return key[ok], np.full(len(yy), i, np.int32), (yy * 256 + xx).astype(np.int32)


def build_index():
    refs = [("MADOS", n) for n in MD.list_patches("all")]
    refs += [("MARIDA", str(p)) for p in sorted((ROOT / "data/MARIDA/patches").glob("*/S2_*.tif"))
             if not p.stem.endswith(("_cl", "_conf"))]
    if IDX.is_file():
        z = np.load(IDX)
        if len(z["names"]) == len(refs):
            return z["keys"], z["pid"], z["pix"], [str(s) for s in z["names"]], [str(s) for s in z["kinds"]]
    with ProcessPoolExecutor(max_workers=12) as ex:
        res = list(ex.map(_ref_keys, [(i, k, n) for i, (k, n) in enumerate(refs)], chunksize=16))
    keys = np.concatenate([r[0] for r in res])
    pid = np.concatenate([r[1] for r in res])
    pix = np.concatenate([r[2] for r in res])
    o = np.argsort(keys, kind="stable")
    names = [n for _, n in refs]
    kinds = [k for k, _ in refs]
    np.savez(IDX, keys=keys[o], pid=pid[o], pix=pix[o], names=np.array(names), kinds=np.array(kinds))
    return keys[o], pid[o], pix[o], names, kinds


_I = None


def _init():
    global _I
    z = np.load(IDX)
    _I = (z["keys"], z["pid"], z["pix"])


def _match(path):
    keys, pid, pix = _I
    with rasterio.open(path) as ds:
        a = ds.read([4, 8]).astype(np.float32)
        if ds.dtypes[0] == "uint16":
            a[a == 0] = np.nan
            a /= 10000.0
        elif np.nanmax(a) > 2:
            a /= 10000.0
    key, ok = lbp_key(a[0], a[1])
    ok &= key != 0
    yy, xx = np.nonzero(ok)
    k = key[ok]
    j = np.minimum(np.searchsorted(keys, k), len(keys) - 1)
    hit = keys[j] == k
    if not hit.any():
        return path, 0, None, 0, 0
    q = pid[j[hit]].astype(np.int64)
    mp = pix[j[hit]]
    dy = (mp // 256 - yy[hit]).astype(np.int64)
    dx = (mp % 256 - xx[hit]).astype(np.int64)
    code = (q * 8192 + (dy + 4096)) * 8192 + (dx + 4096)
    u, c = np.unique(code, return_counts=True)
    b = int(np.argmax(c))
    second = int(np.sort(c)[-2]) if len(c) > 1 else 0
    return path, int(ok.sum()), int(u[b] // (8192 * 8192)), int(c[b]), second


def main():
    t0 = time.time()
    keys, pid, pix, names, kinds = build_index()
    print(f"index: {len(keys)} keys from {len(names)} reference images, {time.time() - t0:.0f}s", flush=True)
    new = sorted(str(p) for p in (ROOT / "data/extra/plp/PLP").glob("PLP20*/Sentinel-2/*.tif"))
    new += sorted(str(p) for p in (ROOT / "data/extra/floatingobjects/crops").glob("*/*.tif") if ".part" not in p.name)
    # positive control: our live scene = same acquisition as MARIDA S2_24-4-19_36JUN (Sen2Cor L2A vs ACOLITE)
    new.append(str(ROOT / "data/live/durban/2019-04-24/bands.tif"))
    with ProcessPoolExecutor(max_workers=8, initializer=_init) as ex:
        res = list(ex.map(_match, new, chunksize=1))
    import pandas as pd
    rows = []
    for path, nvalid, q, votes, second in res:
        rows.append(dict(image=os.path.relpath(path, ROOT).replace("\\", "/"), valid_keys=nvalid,
                         best_ref=names[q] if q is not None else "", best_ref_kind=kinds[q] if q is not None else "",
                         votes=votes, second_votes=second,
                         verdict="MATCH (same pixels)" if votes >= MATCH_VOTES else ("weak" if votes >= MIN_VOTES else "no match")))
    df = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print(df.verdict.value_counts().to_string())
    print(df.sort_values("votes", ascending=False).head(8).to_string())


if __name__ == "__main__":
    main()
