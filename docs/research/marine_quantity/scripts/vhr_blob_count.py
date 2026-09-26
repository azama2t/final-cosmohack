"""L117 P4: stage-2 of the two-stage scheme on a REAL free VHR image: naive per-object counter on open water,
and what happens to the count when the same water is seen at 0.9 / 3 / 10 m (block-averaged).

Image: Maxar Open Data (CC-BY-NC-4.0), GeoEye-1 visual (pansharpened RGB, 0.305 m), Derna, Libya, 2023-09-13 09:18 UTC
(2 days after the dam-break flood; turbid plume, flood debris expected). Imagery stays in data/extra (NOT git).
Window: offshore water only (checked visually on the overview), no land/surf/cloud.

Counter (no training, transparent): residual = RGB - gaussian background (sigma = 10 m); robust z per channel (MAD);
score = sqrt(sum z^2); blobs = 8-connected components with score > k and area >= 4 px (>= 2x2 px) and <= 400 m2.
Blobs are split into 'bright' (mean residual > 0 on all channels: foam/whitecap/glint-like) and 'coloured/dark'.
Output: results/vhr_blob_count.json (+ blob thumbnails grid for manual check in data/extra/marine_quantity/maxar/).
"""
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds
from scipy import ndimage as ndi

ROOT = Path(__file__).resolve().parents[4]
RES = ROOT / "docs/research/marine_quantity/results"
RAW = ROOT / "data/extra/marine_quantity/maxar"
URL = ("https://maxar-opendata.s3.amazonaws.com/events/Libya-Floods-Sept-2023/ard/34/120200213130/2023-09-13/"
       "10500100363D0900-visual.tif")
WIN = (22.625, 32.789, 22.650, 32.797)  # lon0, lat0, lon1, lat1 - offshore water


def read(url=URL, win=WIN, tag="derna_offshore"):
    cache = RAW / f"{tag}_window.npy"
    if cache.exists():
        d = np.load(cache, allow_pickle=True).item()
        return d["arr"], d["res"]
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        with rasterio.open(url) as ds:
            b = transform_bounds("EPSG:4326", ds.crs, *win)
            w = from_bounds(*b, transform=ds.transform).round_offsets().round_lengths()
            arr = ds.read(indexes=[1, 2, 3], window=w)
            res = ds.res[0]
    np.save(cache, {"arr": arr, "res": res})
    Image.fromarray(np.moveaxis(arr, 0, -1)[::4, ::4]).save(RAW / f"{tag}_window_q.png")
    return arr, res


def block(arr, f):
    if f == 1:
        return arr.astype(np.float32)
    c, h, w = arr.shape
    h2, w2 = h // f * f, w // f * f
    a = arr[:, :h2, :w2].astype(np.float32).reshape(c, h2 // f, f, w2 // f, f)
    return a.mean(axis=(2, 4))


def count(img, gsd, k):
    bg_sigma_px = max(1.5, 10.0 / gsd)
    res = np.stack([c - ndi.gaussian_filter(c, bg_sigma_px) for c in img])
    z = []
    for c in res:
        med = np.median(c)
        mad = np.median(np.abs(c - med)) * 1.4826 + 1e-6
        z.append((c - med) / mad)
    z = np.stack(z)
    score = np.sqrt((z ** 2).sum(0))
    out = {}
    for kk in k:
        lab, n = ndi.label(score > kk, structure=np.ones((3, 3)))
        if n == 0:
            out[kk] = {"n_blobs": 0}
            continue
        idx = np.arange(1, n + 1)
        area_px = ndi.sum(np.ones_like(score), lab, idx)
        mres = np.stack([ndi.mean(r, lab, idx) for r in res])
        keep = (area_px >= 4) & (area_px * gsd * gsd <= 400)
        bright = keep & (mres > 0).all(0)
        com = ndi.center_of_mass(np.ones_like(score), lab, idx)
        out[kk] = {"n_blobs": int(keep.sum()), "n_bright": int(bright.sum()), "n_coloured_or_dark": int((keep & ~bright).sum()),
                   "median_area_m2": float(np.median(area_px[keep] * gsd * gsd)) if keep.any() else None,
                   "_centres": [tuple(map(float, com[i])) for i in np.where(keep)[0]],
                   "_bright": [bool(b) for b in bright[keep]]}
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=URL)
    ap.add_argument("--urlfile")
    ap.add_argument("--win", type=float, nargs=4, default=WIN)
    ap.add_argument("--tag", default="derna_offshore")
    ap.add_argument("--note", default="Maxar Open Data GeoEye-1 visual 0.305 m, Derna 2023-09-13, CC-BY-NC-4.0")
    a = ap.parse_args()
    url = open(a.urlfile).read().strip() if a.urlfile else a.url
    arr, res = read(url, tuple(a.win), a.tag)
    c, h, w = arr.shape
    area_km2 = h * w * res * res / 1e6
    print("window px", h, w, "gsd", res, "area_km2", round(area_km2, 3), flush=True)
    ks = [4, 6, 8]
    table = []
    for f in (1, 3, 10, 33):
        img = block(arr, f)
        gsd = res * f
        r = count(img, gsd, ks)
        for kk in ks:
            rr = r[kk]
            table.append({"gsd_m": round(gsd, 3), "k": kk, "n_blobs": rr["n_blobs"], "n_bright": rr.get("n_bright"),
                          "n_coloured_or_dark": rr.get("n_coloured_or_dark"), "median_area_m2": rr.get("median_area_m2"),
                          "blobs_per_km2": rr["n_blobs"] / area_km2})
        if f == 1:
            native = r
        print(table[-3:], flush=True)
    # thumbnails for manual check: 48 random coloured/dark + 16 bright at k=6, 0.3 m
    rng = np.random.default_rng(0)
    cen = np.array(native[6]["_centres"]) if native[6]["n_blobs"] else np.zeros((0, 2))
    br = np.array(native[6]["_bright"]) if native[6]["n_blobs"] else np.zeros(0, bool)
    sel_c = rng.choice(np.where(~br)[0], size=min(48, (~br).sum()), replace=False) if (~br).any() else []
    sel_b = rng.choice(np.where(br)[0], size=min(16, br.sum()), replace=False) if br.any() else []
    tiles = []
    rgb = np.moveaxis(arr, 0, -1)
    for i in list(sel_c) + list(sel_b):
        y, x = map(int, cen[i])
        y0, x0 = max(0, y - 20), max(0, x - 20)
        t = rgb[y0:y0 + 40, x0:x0 + 40]
        t = np.pad(t, ((0, 40 - t.shape[0]), (0, 40 - t.shape[1]), (0, 0)))
        t = np.array(Image.fromarray(t).resize((120, 120), Image.NEAREST))
        tiles.append(t)
    if tiles:
        n = len(tiles)
        cols = 8
        rows = int(np.ceil(n / cols))
        grid = np.full((rows * 124, cols * 124, 3), 255, np.uint8)
        for j, t in enumerate(tiles):
            r0, c0 = divmod(j, cols)
            grid[r0 * 124:r0 * 124 + 120, c0 * 124:c0 * 124 + 120] = t
        Image.fromarray(grid).save(RAW / f"{a.tag}_blob_thumbs_k6.png")
    out = {"image": url.split("?")[0], "note": a.note, "imagery_in_git": False, "window_lonlat": list(a.win),
           "native_gsd_m": res, "area_km2": area_km2, "table": table,
           "thumbs": {"coloured_or_dark_first": len(sel_c), "bright_last": len(sel_b), "grid": f"data/extra/marine_quantity/maxar/{a.tag}_blob_thumbs_k6.png",
                      "tile_side_m": round(40 * res, 1)},
           "reference_densities": "see results/resolution_physics.json (ADIS)"}
    (RES / f"vhr_blob_count_{a.tag}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
