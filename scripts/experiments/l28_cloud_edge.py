"""L28 diagnostic: are detections near clouds / cloud shadows a cloud-edge artefact?

For every live scene and model: rebuild the kept components exactly as scripts/build_service_data.py did before L28
(observed water = water_mask & ~edge(4 px) & ~SCL 4/5; clean_mask(min_px=2, buffer_px=1)), then per component:
Euclidean distance (px) to the nearest cloud/shadow pixel (SCL 3, 8, 9, 10) and to land (non-water, non-cloud,
valid SCL), area, confirmation by the other model (radius 2 px), top-3 zone membership (service/data zones.json),
local background brightness (B2 / B4 in a 3..8 px ring of water around the component vs the scene water median).
Also simulates wider cloud buffers (EDT <= b px) and saves RGB crops of the 5 largest spots near clouds.

Usage: python scripts/experiments/l28_cloud_edge.py [--live data/live] [--data service/data] [--out reports]
Writes: <out>/cloud_edge_components.csv, <out>/figures/cloud_edge_<k>.png, prints markdown tables.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from macroplastic.grid.confirm import confirmed_components  # noqa: E402
from macroplastic.grid.h3index import cell_raster  # noqa: E402
from macroplastic.grid import H3_RES  # noqa: E402
from macroplastic.grid.vectorize import clean_mask  # noqa: E402

CLOUD_SCL = (3, 8, 9, 10)
EDGE_PX = 4
DISTS = (1, 2, 3, 5, 10, 15, 20)
BUFS = (1, 3, 5, 8, 10, 15, 20)
INF = 1e9


def rd(p):
    with rasterio.open(p) as ds:
        return ds.read(1), ds.transform, ds.crs


def edge_zone(scl, shape, width=EDGE_PX):
    z = np.zeros(shape, bool)
    z[:width], z[-width:], z[:, :width], z[:, -width:] = True, True, True, True
    if (scl == 0).any():
        z |= ndimage.binary_dilation(scl == 0, structure=np.ones((3, 3), bool), iterations=width)
    return z


def dist_to(mask):
    if not mask.any():
        return np.full(mask.shape, INF, np.float32)
    return ndimage.distance_transform_edt(~mask).astype(np.float32)


def scene_rows(sdir: Path, data: Path):
    region, date = sdir.parent.name, sdir.name
    wm, tr, crs = rd(sdir / "water_mask.tif")
    scl, *_ = rd(sdir / "scl.tif")
    shape = wm.shape
    water = (wm == 1) & ~edge_zone(scl, shape) & ~np.isin(scl, (4, 5))
    cloud = np.isin(scl, CLOUD_SCL)
    land = (wm != 1) & ~cloud & (scl != 0) & ~np.isin(scl, (4, 5))  # ships are not land
    dcl, dland = dist_to(cloud), dist_to(land)
    with rasterio.open(sdir / "bands.tif") as ds:
        b2, b4 = ds.read(2), ds.read(4)
    wmed_b2 = float(np.nanmedian(b2[water])) if water.any() else np.nan
    wmed_b4 = float(np.nanmedian(b4[water])) if water.any() else np.nan
    models = sorted(p.stem[5:] for p in sdir.glob("prob_*.tif"))
    probs, masks = {}, {}
    for m in models:
        prob, *_ = rd(sdir / f"prob_{m}.tif")
        pj = json.loads((sdir / f"prob_{m}.json").read_text(encoding="utf-8")) if (sdir / f"prob_{m}.json").exists() else {}
        thr = float(pj.get("threshold", 0.5))
        probs[m] = prob
        masks[m] = (*clean_mask(prob, water, thr, min_px=2, buffer_px=1), thr)
    cellr = cells = None
    rows = []
    for m in models:
        labels, n, raw, thr = masks[m]
        if n == 0:
            continue
        idx = np.arange(1, n + 1)
        npx = np.bincount(labels.ravel(), minlength=n + 1)[1:]
        mind_c = np.asarray(ndimage.minimum(dcl, labels, idx))
        mind_l = np.asarray(ndimage.minimum(dland, labels, idx))
        pos = ndimage.maximum_position(probs[m], labels, idx)
        conf = np.zeros(n, bool)
        for o in models:
            if o != m:
                conf |= confirmed_components(labels, n, masks[o][2], 2)
        # ring 3..8 px around each component (water only): local background brightness
        near = ndimage.binary_dilation(labels > 0, iterations=8)
        ring_all = near & ~ndimage.binary_dilation(labels > 0, iterations=3) & water
        ring_lab = ndimage.grey_dilation(labels, size=(17, 17)) * ring_all
        rb2 = np.asarray(ndimage.median(b2, ring_lab, idx)) if ring_lab.any() else np.full(n, np.nan)
        rb4 = np.asarray(ndimage.median(b4, ring_lab, idx)) if ring_lab.any() else np.full(n, np.nan)
        zfile = data / region / date / m / "zones.json"
        top3 = {}
        if zfile.exists():
            zs = json.loads(zfile.read_text(encoding="utf-8"))["zones"]
            top3 = {z["h3"]: z["rank"] for z in zs[:3]}
            if cellr is None:
                cellr, cells = cell_raster(tr, crs, shape, H3_RES)
        for k in range(n):
            r, c = pos[k]
            zr = 0
            if top3 and cellr is not None:
                ci = int(cellr[r, c])
                zr = top3.get(cells[ci - 1], 0) if ci > 0 else 0
            rows.append({"region": region, "date": date, "model": m, "k": k + 1, "n_px": int(npx[k]),
                         "d_cloud": float(min(mind_c[k], INF)), "d_land": float(min(mind_l[k], INF)),
                         "confirmed": bool(conf[k]), "zone_rank": zr, "row": int(r), "col": int(c),
                         "ring_b2_ratio": float(rb2[k] / wmed_b2) if np.isfinite(rb2[k]) and wmed_b2 > 0 else np.nan,
                         "ring_b4_ratio": float(rb4[k] / wmed_b4) if np.isfinite(rb4[k]) and wmed_b4 > 0 else np.nan,
                         "scene_cloud_frac": float(cloud[scl != 0].mean())})
    return rows


def frac_table(rows, title):
    out = [f"\n### {title}\n", "| модель | объектов | площадь, px | " +
           " | ".join(f"≤{d} px облако: объекты / площадь" for d in (5, 10, 20)) + " | " +
           " | ".join(f"≤{d} px суша: объекты / площадь" for d in (5, 10, 20)) + " |",
           "|---" * 9 + "|"]
    for m in sorted({r["model"] for r in rows}):
        rs = [r for r in rows if r["model"] == m]
        N, A = len(rs), sum(r["n_px"] for r in rs)
        if not N:
            continue
        cells = []
        for key in ("d_cloud", "d_land"):
            for d in (5, 10, 20):
                sel = [r for r in rs if r[key] <= d]
                cells.append(f"{100 * len(sel) / N:.1f} % / {100 * sum(r['n_px'] for r in sel) / max(A, 1):.1f} %")
        out.append(f"| {m} | {N} | {A} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def buffer_table(rows, title):
    out = [f"\n### {title}\n", "| модель | буфер облаков, px | уходит объектов | уходит площади | "
           "уходит уверенных | осталось объектов | осталось уверенных |", "|---" * 7 + "|"]
    for m in sorted({r["model"] for r in rows}):
        rs = [r for r in rows if r["model"] == m]
        N, A, C = len(rs), sum(r["n_px"] for r in rs), sum(r["confirmed"] for r in rs)
        for b in BUFS:
            gone = [r for r in rs if r["d_cloud"] <= b]
            gA, gC = sum(r["n_px"] for r in gone), sum(r["confirmed"] for r in gone)
            out.append(f"| {m} | {b} | {len(gone)} ({100 * len(gone) / max(N, 1):.1f} %) | "
                       f"{gA} px ({100 * gA / max(A, 1):.1f} %) | {gC} | {N - len(gone)} | {C - gC} |")
    return "\n".join(out)


def density_table(rows, scenes_info):
    """Detections per 10^6 observed-water px by distance band to clouds (is density elevated near clouds?)."""
    out = ["\n### Плотность находок по расстоянию до облаков (на 1 млн пикс. наблюдаемой воды в полосе)\n",
           "| модель | 2–5 px | 6–10 px | 11–20 px | 21–50 px | > 50 px |", "|---" * 6 + "|"]
    bands = [(1, 5), (5, 10), (10, 20), (20, 50), (50, INF * 10)]
    area = np.zeros(len(bands))
    for info in scenes_info:
        area += info["band_px"]
    for m in sorted({r["model"] for r in rows}):
        rs = [r for r in rows if r["model"] == m]
        vals = []
        for i, (lo, hi) in enumerate(bands):
            npx = sum(r["n_px"] for r in rs if lo < r["d_cloud"] <= hi)
            vals.append(f"{1e6 * npx / max(area[i], 1):.1f}")
        out.append(f"| {m} (flagged px / 1e6) | " + " | ".join(vals) + " |")
    out.append(f"| площадь полосы, млн px | " + " | ".join(f"{a / 1e6:.2f}" for a in area) + " |")
    return "\n".join(out)


def band_px(sdir):
    wm, *_ = rd(sdir / "water_mask.tif")
    scl, *_ = rd(sdir / "scl.tif")
    water = (wm == 1) & ~edge_zone(scl, wm.shape) & ~np.isin(scl, (4, 5))
    dcl = dist_to(np.isin(scl, CLOUD_SCL))
    d = dcl[water]
    return np.array([((d > lo) & (d <= hi)).sum() for lo, hi in [(1, 5), (5, 10), (10, 20), (20, 50), (50, INF * 10)]])


def crops(rows, live: Path, figdir: Path, k=5):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    near = sorted([r for r in rows if r["d_cloud"] <= 20], key=lambda r: -r["n_px"])
    seen, pick = set(), []
    for r in near:  # one crop per spot location (both models can flag the same spot)
        key = (r["region"], r["date"], r["row"] // 60, r["col"] // 60)
        if key in seen:
            continue
        seen.add(key)
        pick.append(r)
        if len(pick) == k:
            break
    figdir.mkdir(parents=True, exist_ok=True)
    for i, r in enumerate(pick, 1):
        sdir = live / r["region"] / r["date"]
        with rasterio.open(sdir / "bands.tif") as ds:
            H, W = ds.height, ds.width
            half = 90
            r0, c0 = max(0, r["row"] - half), max(0, r["col"] - half)
            r1, c1 = min(H, r["row"] + half), min(W, r["col"] + half)
            win = rasterio.windows.Window(c0, r0, c1 - c0, r1 - r0)
            rgb = np.stack([ds.read(b, window=win) for b in (4, 3, 2)], -1)
        scl, *_ = rd(sdir / "scl.tif")
        scl = scl[r0:r1, c0:c1]
        prob, *_ = rd(sdir / f"prob_{r['model']}.tif")
        pj = json.loads((sdir / f"prob_{r['model']}.json").read_text(encoding="utf-8"))
        pm = prob[r0:r1, c0:c1] >= np.ceil(float(pj.get("threshold", 0.5)) * 255 - 1e-6)
        rgb = np.nan_to_num(rgb)
        img = np.clip(rgb / 0.12, 0, 1) ** 0.8
        img2 = np.clip(rgb / 0.04, 0, 1) ** 0.8  # water-stretched
        fig, ax = plt.subplots(1, 3, figsize=(12, 4.3))
        for a, im, t in ((ax[0], img, "RGB (B4,B3,B2), 0–0.12"), (ax[1], img2, "RGB, растяжка воды 0–0.04")):
            a.imshow(im)
            a.contour(np.isin(scl, CLOUD_SCL), levels=[0.5], colors="cyan", linewidths=0.8)
            a.contour(pm, levels=[0.5], colors="red", linewidths=0.8)
            a.set_title(t, fontsize=9)
            a.axis("off")
        cmap = matplotlib.colors.ListedColormap(["black", "#555", "#333", "#444", "green", "#a0522d", "#1f4e99",
                                                 "#888", "#bbb", "#fff", "#8fd3ff", "#ffd"])
        ax[2].imshow(scl, cmap=cmap, vmin=0, vmax=11, interpolation="nearest")
        ax[2].contour(pm, levels=[0.5], colors="red", linewidths=0.8)
        ax[2].set_title("SCL (голубой=облако/тень контур; красный=P≥порога)", fontsize=8)
        ax[2].axis("off")
        fig.suptitle(f"{r['region']} {r['date']} {r['model']}: пятно {r['n_px']} px, до облака/тени "
                     f"{r['d_cloud']:.1f} px, фон B2 x{r['ring_b2_ratio']:.2f} к медиане воды, "
                     f"уверенная={r['confirmed']}, зона №{r['zone_rank'] or '-'}", fontsize=9)
        fig.tight_layout()
        p = figdir / f"cloud_edge_{i}.png"
        fig.savefig(p, dpi=90)
        plt.close(fig)
        print(f"  figure {p}: {r['region']} {r['date']} {r['model']} n_px={r['n_px']} d_cloud={r['d_cloud']:.1f}")
    return pick


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", default=str(ROOT / "data/live"))
    ap.add_argument("--data", default=str(ROOT / "service/data"))
    ap.add_argument("--out", default=str(ROOT / "reports"))
    ap.add_argument("--no-figs", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    live, data, out = Path(a.live), Path(a.data), Path(a.out)
    rows, info = [], []
    for sj in sorted(live.glob("*/*/scene.json")):
        sdir = sj.parent
        if not list(sdir.glob("prob_*.tif")):
            continue
        rr = scene_rows(sdir, data)
        rows += rr
        info.append({"band_px": band_px(sdir), "region": sdir.parent.name})
        print(f"{sdir.parent.name}/{sdir.name}: {len(rr)} components", flush=True)
    with open(out / "cloud_edge_components.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"\nscenes: {len(info)}")
    print(frac_table(rows, "Все сцены"))
    print(frac_table([r for r in rows if r["region"] == "honduras"], "Гондурас (все даты)"))
    print(frac_table([r for r in rows if r["zone_rank"] in (1, 2, 3)], "Находки в зонах №1–3 (все сцены)"))
    print(frac_table([r for r in rows if r["region"] == "honduras" and r["zone_rank"] in (1, 2, 3)],
                     "Гондурас, зоны №1–3"))
    print(density_table(rows, info))
    print(density_table([r for r in rows if r["region"] == "honduras"], [i for i in info if i["region"] == "honduras"]))
    print(buffer_table(rows, "Симуляция буфера облаков (все сцены)"))
    # brightness of local background near vs far from clouds
    for m in sorted({r["model"] for r in rows}):
        for lo, hi, t in ((0, 10, "≤10 px"), (10, 50, "11–50 px"), (50, INF * 10, ">50 px")):
            v = [r["ring_b2_ratio"] for r in rows if r["model"] == m and lo < r["d_cloud"] <= hi
                 and np.isfinite(r["ring_b2_ratio"])]
            v4 = [r["ring_b4_ratio"] for r in rows if r["model"] == m and lo < r["d_cloud"] <= hi
                  and np.isfinite(r["ring_b4_ratio"])]
            if v:
                print(f"ring B2/B4 ratio {m} {t}: n={len(v)} median B2 x{np.median(v):.2f}, B4 x{np.median(v4):.2f}, "
                      f"p90 B2 x{np.percentile(v, 90):.2f}")
    if not a.no_figs:
        crops(rows, live, out / "figures")


if __name__ == "__main__":
    main()
