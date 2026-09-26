"""Audit L107: is a candidate S2 acquisition (full tile, L2A B04/B08 from Earth Search COGs) the same pixels as any
MADOS / MARIDA patch? Reuses the LBP index of scripts/extra_data/mados_content_check.py (out/l89_ref_lbp_index.npz,
MADOS all splits + MARIDA all splits) and its key function; reads the COG in 2048-px blocks over HTTP, nothing saved.
Same acquisition -> hundreds..thousands of votes at one (patch, dy, dx); unrelated -> <= ~7 (MATCH >= 30).

  .venv/Scripts/python.exe reports/audit/check_mados_content.py --control      # positive control (durban vs MARIDA)
  .venv/Scripts/python.exe reports/audit/check_mados_content.py S2B_30SXE_20210311_0_L2A [...]
"""
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "60")
os.environ.setdefault("GDAL_HTTP_CONNECTTIMEOUT", "20")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
import numpy as np
import rasterio
from rasterio.windows import Window

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from train_lgbm_mados import lbp_key  # noqa: E402

IDX = ROOT / "out" / "l89_ref_lbp_index.npz"
OUT = ROOT / "reports" / "audit" / "mados_content_demo.json"
BLOCK = 1024
MATCH = 30


def load_index():
    z = np.load(IDX)
    return z["keys"], z["pid"], z["pix"], [str(s) for s in z["names"]], [str(s) for s in z["kinds"]]


def votes(b4, b8, I):
    keys, pid, pix = I[:3]
    key, ok = lbp_key(b4, b8)
    ok &= key != 0
    yy, xx = np.nonzero(ok)
    k = key[ok]
    if not len(k):
        return 0, None, 0, 0
    j = np.minimum(np.searchsorted(keys, k), len(keys) - 1)
    hit = keys[j] == k
    if not hit.any():
        return int(ok.sum()), None, 0, 0
    q = pid[j[hit]].astype(np.int64)
    mp = pix[j[hit]]
    dy = (mp // 256 - yy[hit]).astype(np.int64)
    dx = (mp % 256 - xx[hit]).astype(np.int64)
    code = (q * 16384 + (dy + 8192)) * 16384 + (dx + 8192)
    u, c = np.unique(code, return_counts=True)
    b = int(np.argmax(c))
    return int(ok.sum()), int(u[b] // (16384 * 16384)), int(c[b]), int(np.sort(c)[-2]) if len(c) > 1 else 0


def scan_item(item, I):
    import urllib.request
    d = json.load(urllib.request.urlopen(
        f"https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/{item}", timeout=60))
    a = d["assets"]
    off = 0.0
    try:  # baseline >= 04.00: BOA_ADD_OFFSET -1000
        off = float(a["red"]["raster:bands"][0].get("offset", 0.0))
    except Exception:
        pass
    best = {"votes": 0}
    n_valid = 0
    with rasterio.open(a["red"]["href"]) as r4, rasterio.open(a["nir"]["href"]) as r8:
        H, W = r4.height, r4.width
        for y0 in range(0, H, BLOCK - 4):
            for x0 in range(0, W, BLOCK - 4):
                w = Window(x0, y0, min(BLOCK, W - x0), min(BLOCK, H - y0))
                b4 = r4.read(1, window=w).astype(np.float32)
                b8 = r8.read(1, window=w).astype(np.float32)
                nod = (b4 == 0) | (b8 == 0)
                b4 = b4 / 10000.0 + off
                b8 = b8 / 10000.0 + off
                b4[nod] = np.nan
                b8[nod] = np.nan
                if np.isnan(b8).all():
                    continue
                nv, q, v, second = votes(b4, b8, I)
                n_valid += nv
                if v > best["votes"]:
                    best = {"votes": v, "second": second, "ref": I[3][q], "ref_kind": I[4][q], "block": [int(y0), int(x0)]}
    best.update({"item": item, "valid_keys": n_valid, "baseline": d["properties"].get("s2:processing_baseline"),
                 "verdict": "MATCH (same pixels)" if best["votes"] >= MATCH else "no match"})
    return best


def main():
    t0 = time.time()
    I = load_index()
    print(f"index {len(I[0])} keys, {len(I[3])} refs, {time.time() - t0:.0f}s", flush=True)
    res = json.loads(OUT.read_text(encoding="utf-8")) if OUT.is_file() else {}
    if "--control" in sys.argv:
        with rasterio.open(ROOT / "data/live/durban/2019-04-24/bands.tif") as ds:
            a = ds.read([4, 8]).astype(np.float32)
        if np.nanmax(a) > 2:
            a /= 10000.0
        nv, q, v, s = votes(a[0], a[1], I)
        res["control_durban_2019-04-24"] = {"votes": v, "second": s, "ref": I[3][q] if q is not None else None,
                                            "expected": "MARIDA S2_24-4-19_36JUN, ~7 579 votes (L89)"}
        print(res["control_durban_2019-04-24"], flush=True)
    for item in [x for x in sys.argv[1:] if not x.startswith("--")]:
        t = time.time()
        res[item] = scan_item(item, I)
        print(res[item], f"{time.time() - t:.0f}s", flush=True)
        OUT.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    OUT.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
