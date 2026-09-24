"""EDA for MARIDA -> reports/eda/eda.md + 8 PNG, reports/marida_scenes.csv, reports/marida_regions.md.

Run (from repo root):
    set PYTHONPATH=src && .venv\\Scripts\\python.exe scripts\\eda_marida.py [--root data\\MARIDA]
CPU only, ~1-3 min.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from pyproj import Transformer  # noqa: E402
from scipy import ndimage  # noqa: E402

from macroplastic.data.marida import (  # noqa: E402
    BAND_NAMES,
    BAND_WAVELENGTHS,
    CLASS_NAMES,
    SPLITS,
    list_patches,
    load_patch,
    parse_scene,
    resolve_root,
    split_of,
)

EXPECTED = {"md_px": 3399, "md_conf": {1: 1625, 2: 1235, 3: 539}, "md_patches": 373,
            "patches": 1381, "scenes": 63, "splits": {"train": 694, "val": 328, "test": 359}}

# Human region names. Primary key: tile; the center coordinate is checked against the bbox (sanity).
# (name_ru, name_en, lon_min, lon_max, lat_min, lat_max)
TILE_REGIONS = {
    # names checked against patch-center coordinates (see reports/marida_scenes.csv center_lon/lat)
    "16PCC": ("Гондурасский залив (Омоа – Пуэрто-Кортес)", "Gulf of Honduras (Omoa - Puerto Cortes)"),
    "16PDC": ("Гондурасский залив (Омоа – Пуэрто-Кортес)", "Gulf of Honduras (Omoa - Puerto Cortes)"),
    "16PEC": ("Ислас-де-ла-Баия (Утила – Роатан)", "Bay Islands (Utila - Roatan), Honduras"),
    "16QED": ("Ислас-де-ла-Баия (Утила – Роатан)", "Bay Islands (Utila - Roatan), Honduras"),
    "18QWF": ("Гаити (запад п-ова Тибюрон)", "Haiti (western Tiburon peninsula)"),
    "18QYF": ("Гаити (Порт-о-Пренс, залив Гонав)", "Haiti (Port-au-Prince, Gulf of Gonave)"),
    "18QYG": ("Гаити (Порт-о-Пренс, залив Гонав)", "Haiti (Port-au-Prince, Gulf of Gonave)"),
    "19QDA": ("Санто-Доминго", "Santo Domingo"),
    "30VWH": ("Шотландия (Ферт-оф-Форт)", "Scotland (Firth of Forth)"),
    "36JUN": ("Дурбан", "Durban"),
    "48MXU": ("Джакарта (Джакартский залив)", "Jakarta Bay (Indonesia)"),
    "48MYU": ("Джакарта (Джакартский залив)", "Jakarta Bay (Indonesia)"),
    "48PZC": ("Дананг", "Da Nang"),
    "50LLR": ("Бали", "Bali"),
    "51PTS": ("Манила", "Manila"),
    "51RVQ": ("Шанхай (Восточно-Китайское море)", "Shanghai offshore (East China Sea)"),
    "52SDD": ("Пусан (Южная Корея)", "Busan (South Korea)"),
}
# coarse bboxes to name anything by coordinates (fallback + sanity check); first match wins
BBOX_REGIONS = [
    ("Ислас-де-ла-Баия (Утила – Роатан)", -87.0, -85.5, 15.7, 16.6),
    ("Гондурасский залив (Омоа – Пуэрто-Кортес)", -89.5, -87.0, 15.0, 18.5),
    ("Гаити (запад п-ова Тибюрон)", -74.6, -73.5, 17.8, 18.8),
    ("Гаити (Порт-о-Пренс, залив Гонав)", -73.5, -71.5, 18.0, 20.0),
    ("Санто-Доминго", -70.5, -68.5, 17.8, 19.0),
    ("Шотландия (Ферт-оф-Форт)", -8.0, 0.0, 54.5, 61.0),
    ("Дурбан", 29.5, 32.5, -31.0, -28.5),
    ("Джакарта (Джакартский залив)", 105.5, 108.0, -7.0, -5.0),
    ("Дананг", 107.5, 109.5, 15.0, 17.0),
    ("Бали", 114.0, 116.5, -9.5, -7.5),
    ("Манила", 119.5, 121.5, 13.5, 15.5),
    ("Шанхай (Восточно-Китайское море)", 120.5, 124.0, 29.0, 32.0),
    ("Пусан (Южная Корея)", 125.0, 130.0, 32.5, 36.0),
    ("Аккра", -1.0, 1.0, 4.5, 6.5),
]
EXCLUDED_SCENES = {"S2_23-9-20_16PCC": "по ТЗ: сцена Bay Islands 23.09.2020 исключена авторами MARIDA (плохая атмосферная коррекция); в архиве v1.0.0 присутствует (все патчи в train), в README/Zenodo подтверждения не найдено; тайл 16PCC географически — Омоа/Пуэрто-Кортес, не Ислас-де-ла-Баия"}

SPEC_CLASSES = [1, 2, 3, 9, 5, 7, 6]  # MD, Dense Sarg, Sparse Sarg, Foam, Ship, Marine Water, Clouds
SCATTER_CLASSES = {1: "Marine Debris", 7: "Marine Water", 9: "Foam", 2: "Dense Sargassum",
                   3: "Sparse Sargassum", 4: "Natural Organic Material"}
MAX_SAMPLES = 6000
RNG = np.random.default_rng(0)


def region_by_coords(lon, lat):
    for name, x0, x1, y0, y1 in BBOX_REGIONS:
        if x0 <= lon <= x1 and y0 <= lat <= y1:
            return name
    return ""


def fdi(img):
    """Biermann 2020 (formula from SPEC §2): B8 - [B6 + (B11-B6)*(l8-l4)/(l11-l4)*10]."""
    b6, b8, b11 = img[5], img[7], img[9]
    l4, l8, l11 = 664.8, 832.9, 1612.05
    return b8 - (b6 + (b11 - b6) * (l8 - l4) / (l11 - l4) * 10.0)


def ndvi(img):
    b4, b8 = img[3], img[7]
    return (b8 - b4) / (b8 + b4 + 1e-8)


def savefig(fig, path):
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    kb = path.stat().st_size / 1024
    if kb > 300:
        # re-render smaller is not possible after close; warn
        print(f"WARNING: {path.name} is {kb:.0f} KB > 300 KB")
    return kb


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None)
    ap.add_argument("--out", default=str(REPO / "reports"))
    args = ap.parse_args()
    t0 = time.time()
    root = resolve_root(args.root)
    out = Path(args.out)
    figdir = out / "eda"
    figdir.mkdir(parents=True, exist_ok=True)

    split_map = split_of(root)
    all_paths = list_patches("all", root)
    in_split = set(split_map)
    not_in_split = [p.stem for p in all_paths if p.stem not in in_split]
    missing_files = [s for s in split_map if not (root / "patches" / parse_scene(s)["scene"] / f"{s}.tif").is_file()]

    cls_px = {s: np.zeros(16, np.int64) for s in SPLITS + ("none",)}
    md_conf = {s: np.zeros(4, np.int64) for s in SPLITS + ("none",)}
    conf_all = np.zeros((16, 4), np.int64)
    nan_cnt = {s: np.zeros(11, np.int64) for s in SPLITS + ("none",)}
    tot_px = defaultdict(int)
    spec_sum = defaultdict(lambda: np.zeros(11))
    spec_sq = defaultdict(lambda: np.zeros(11))
    spec_n = defaultdict(int)
    samples = defaultdict(list)
    comp_sizes = []
    md_patch_count = defaultdict(int)
    scene_rows = {}
    crs_set = set()
    shapes = set()
    dtypes = set()
    tfm_cache = {}

    for i, p in enumerate(all_paths):
        img, cl, conf, prof = load_patch(p)
        stem = p.stem
        sp = split_map.get(stem, "none")
        info = parse_scene(stem)
        crs = prof["crs"]
        crs_set.add(str(crs))
        shapes.add(img.shape)
        dtypes.add(str(img.dtype))
        key = str(crs)
        if key not in tfm_cache:
            tfm_cache[key] = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        tr = tfm_cache[key]
        l, b, r, t = prof["bounds"]
        xs = [l, r, r, l, (l + r) / 2]
        ys = [b, b, t, t, (b + t) / 2]
        lons, lats = tr.transform(xs, ys)
        pb = (min(lons[:4]), min(lats[:4]), max(lons[:4]), max(lats[:4]))

        cnt = np.bincount(cl.ravel(), minlength=16)[:16]
        cls_px[sp] += cnt
        md = cl == 1
        mdc = np.bincount(conf[md].ravel(), minlength=4)[:4]
        md_conf[sp] += mdc
        for c in range(1, 16):
            m = cl == c
            if m.any():
                conf_all[c] += np.bincount(conf[m].ravel(), minlength=4)[:4]
        nan_cnt[sp] += np.isnan(img).reshape(11, -1).sum(1)
        tot_px[sp] += img.shape[1] * img.shape[2]

        for c in range(1, 16):
            m = cl == c
            if not m.any():
                continue
            v = img[:, m]
            ok = ~np.isnan(v).any(0)
            v = v[:, ok]
            spec_sum[c] += v.sum(1)
            spec_sq[c] += (v.astype(np.float64) ** 2).sum(1)
            spec_n[c] += v.shape[1]
            if c in SCATTER_CLASSES and v.shape[1]:
                k = min(v.shape[1], 200 if c != 1 else 10**6)
                idx = RNG.choice(v.shape[1], k, replace=False)
                samples[c].append(v[:, idx])
        if md.any():
            md_patch_count[sp] += 1
            lab, n = ndimage.label(md, structure=np.ones((3, 3)))
            comp_sizes += list(np.bincount(lab.ravel())[1:])

        sc = info["scene"]
        row = scene_rows.setdefault(sc, {
            "scene": sc, "date": info["date"], "tile": info["tile"], "crs": key,
            "n_patches": 0, "n_train": 0, "n_val": 0, "n_test": 0, "n_nosplit": 0,
            "md_px": 0, "md_px_high": 0, "md_px_moderate": 0, "md_px_low": 0, "md_px_noconf": 0,
            "n_md_patches": 0, "_all": [], "_md": [], "_cx": [], "_cy": [],
        })
        row["n_patches"] += 1
        row[{"train": "n_train", "val": "n_val", "test": "n_test", "none": "n_nosplit"}[sp]] += 1
        row["md_px"] += int(md.sum())
        row["md_px_noconf"] += int(mdc[0])
        row["md_px_high"] += int(mdc[1])
        row["md_px_moderate"] += int(mdc[2])
        row["md_px_low"] += int(mdc[3])
        row["_all"].append(pb)
        if md.any():
            row["n_md_patches"] += 1
            row["_md"].append(pb)
        if (i + 1) % 200 == 0:
            print(f"{i + 1}/{len(all_paths)} patches, {time.time() - t0:.0f}s", flush=True)

    # ---------------- scenes table ----------------
    def union(bbs):
        if not bbs:
            return (np.nan,) * 4
        a = np.array(bbs)
        return a[:, 0].min(), a[:, 1].min(), a[:, 2].max(), a[:, 3].max()

    rows = []
    for sc, r in scene_rows.items():
        ab = union(r.pop("_all"))
        mb = union(r.pop("_md"))
        r.pop("_cx"), r.pop("_cy")
        lon_c, lat_c = (ab[0] + ab[2]) / 2, (ab[1] + ab[3]) / 2
        r["center_lon"], r["center_lat"] = round(lon_c, 5), round(lat_c, 5)
        for k, v in zip(("bbox_all_lon_min", "bbox_all_lat_min", "bbox_all_lon_max", "bbox_all_lat_max"), ab):
            r[k] = round(float(v), 5)
        for k, v in zip(("bbox_md_lon_min", "bbox_md_lat_min", "bbox_md_lon_max", "bbox_md_lat_max"), mb):
            r[k] = round(float(v), 5) if not np.isnan(v) else ""
        ru, en = TILE_REGIONS.get(r["tile"], ("", ""))
        bycoord = region_by_coords(lon_c, lat_c)
        r["region"] = ru or bycoord or f"tile {r['tile']}"
        r["region_en"] = en
        r["region_by_coords"] = bycoord
        r["excluded"] = sc in EXCLUDED_SCENES
        r["excluded_note"] = EXCLUDED_SCENES.get(sc, "")
        rows.append(r)
    scenes = pd.DataFrame(rows).sort_values(["date", "tile"]).reset_index(drop=True)
    cols = ["scene", "date", "tile", "region", "region_en", "n_patches", "n_train", "n_val", "n_test",
            "n_nosplit", "n_md_patches", "md_px", "md_px_high", "md_px_moderate", "md_px_low", "md_px_noconf",
            "center_lon", "center_lat",
            "bbox_md_lon_min", "bbox_md_lat_min", "bbox_md_lon_max", "bbox_md_lat_max",
            "bbox_all_lon_min", "bbox_all_lat_min", "bbox_all_lon_max", "bbox_all_lat_max",
            "crs", "region_by_coords", "excluded", "excluded_note"]
    scenes = scenes[cols]
    scenes.to_csv(out / "marida_scenes.csv", index=False, encoding="utf-8")

    # ---------------- regions ----------------
    def agg(g):
        md = g[g["md_px"] > 0]
        return pd.Series({
            "tiles": ",".join(sorted(g["tile"].unique())),
            "n_scenes": len(g),
            "n_dates": g["date"].nunique(),
            "dates_with_md": ",".join(sorted(md["date"].unique())),
            "n_patches": int(g["n_patches"].sum()),
            "md_px": int(g["md_px"].sum()),
            "md_px_high": int(g["md_px_high"].sum()),
            "md_px_excl_excluded": int(g.loc[~g["excluded"], "md_px"].sum()),
            "bbox_md": (f"{pd.to_numeric(md['bbox_md_lon_min']).min():.4f},{pd.to_numeric(md['bbox_md_lat_min']).min():.4f},"
                        f"{pd.to_numeric(md['bbox_md_lon_max']).max():.4f},{pd.to_numeric(md['bbox_md_lat_max']).max():.4f}")
            if len(md) else "",
            "center": f"{g['center_lon'].mean():.4f},{g['center_lat'].mean():.4f}",
            "top_scene": (lambda t: f"{t['scene']} ({t['md_px']} px; bbox {t['bbox_md_lon_min']:.4f},{t['bbox_md_lat_min']:.4f},"
                          f"{t['bbox_md_lon_max']:.4f},{t['bbox_md_lat_max']:.4f})")(md.sort_values("md_px").iloc[-1])
            if len(md) else "",
        })

    regions = scenes.groupby("region").apply(agg, include_groups=False).sort_values("md_px", ascending=False)
    tiles = scenes.assign(_g=scenes["tile"]).groupby("_g").apply(agg, include_groups=False).sort_values("md_px", ascending=False)
    tiles["region"] = [TILE_REGIONS.get(t, ("",))[0] for t in tiles.index]

    # ---------------- figures ----------------
    figs = {}
    names16 = [CLASS_NAMES[c] for c in range(16)]
    # 1 class distribution by split
    fig, ax = plt.subplots(figsize=(10, 5))
    x = np.arange(1, 16)
    w = 0.27
    for j, s in enumerate(SPLITS):
        ax.bar(x + (j - 1) * w, cls_px[s][1:], w, label=s)
    ax.set_yscale("log")
    ax.set_xticks(x, names16[1:], rotation=60, ha="right", fontsize=8)
    ax.set_ylabel("pixels (log)")
    ax.set_title("MARIDA: labelled pixels per class and split")
    ax.legend()
    figs["01_class_pixels_by_split.png"] = savefig(fig, figdir / "01_class_pixels_by_split.png")
    # 2 MD by conf
    fig, ax = plt.subplots(figsize=(6, 4))
    bottom = np.zeros(3)
    for ci, cn in ((1, "High"), (2, "Moderate"), (3, "Low")):
        vals = np.array([md_conf[s][ci] for s in SPLITS])
        ax.bar(SPLITS, vals, bottom=bottom, label=cn)
        bottom += vals
    for k, s in enumerate(SPLITS):
        ax.text(k, bottom[k], str(int(bottom[k])), ha="center", va="bottom")
    ax.set_ylabel("Marine Debris pixels")
    ax.set_title("Marine Debris pixels by confidence and split")
    ax.legend()
    figs["02_md_by_conf.png"] = savefig(fig, figdir / "02_md_by_conf.png")
    # 3 MD by scene
    s3 = scenes[scenes["md_px"] > 0].sort_values("md_px", ascending=True)
    fig, ax = plt.subplots(figsize=(8, max(4, 0.2 * len(s3))))
    colors = ["#c0392b" if e else "#2e86c1" for e in s3["excluded"]]
    ax.barh(s3["scene"], s3["md_px"], color=colors)
    ax.tick_params(axis="y", labelsize=7)
    ax.set_xlabel("Marine Debris pixels (red = scene flagged excluded)")
    ax.set_title(f"Marine Debris pixels per scene ({len(s3)} of {len(scenes)} scenes have MD)")
    figs["03_md_by_scene.png"] = savefig(fig, figdir / "03_md_by_scene.png")
    # 4 spectra
    fig, ax = plt.subplots(figsize=(8, 5))
    spec_table = {}
    for c in SPEC_CLASSES:
        if spec_n[c] == 0:
            continue
        mu = spec_sum[c] / spec_n[c]
        sd = np.sqrt(np.maximum(spec_sq[c] / spec_n[c] - mu ** 2, 0))
        spec_table[c] = (mu, sd, spec_n[c])
        ax.plot(BAND_WAVELENGTHS, mu, marker="o", label=f"{CLASS_NAMES[c]} (n={spec_n[c]})")
    ax.set_xscale("log")
    ax.set_xticks(BAND_WAVELENGTHS, BAND_NAMES, fontsize=8)
    ax.set_xlabel("band (log wavelength)")
    ax.set_ylabel("mean rhorc reflectance")
    ax.set_title("Mean spectra by class (NaN pixels dropped)")
    ax.legend(fontsize=8)
    figs["04_spectra_by_class.png"] = savefig(fig, figdir / "04_spectra_by_class.png")
    # 5 FDI/NDVI scatter
    fig, ax = plt.subplots(figsize=(7, 6))
    scat_stats = {}
    for c, nm in SCATTER_CLASSES.items():
        if not samples[c]:
            continue
        v = np.concatenate(samples[c], 1)
        if v.shape[1] > MAX_SAMPLES:
            v = v[:, RNG.choice(v.shape[1], MAX_SAMPLES, replace=False)]
        f, n = fdi(v), ndvi(v)
        scat_stats[c] = (np.median(f), np.median(n), v.shape[1])
        ax.scatter(n, f, s=3 if c != 1 else 6, alpha=0.35 if c != 1 else 0.8, label=nm, rasterized=True,
                   zorder=3 if c == 1 else 2)
    ax.set_xlabel("NDVI")
    ax.set_ylabel("FDI")
    ax.set_title("FDI vs NDVI (subsample; MD = all pixels)")
    ax.legend(markerscale=4, fontsize=8)
    ax.grid(alpha=0.3)
    figs["05_fdi_ndvi_scatter.png"] = savefig(fig, figdir / "05_fdi_ndvi_scatter.png")
    # 6 world map (world + Caribbean zoom)
    reg_pts = scenes.groupby("region").agg(lon=("center_lon", "mean"), lat=("center_lat", "mean"),
                                           md=("md_px", "sum"), n=("scene", "count"))
    sz = 20 + 400 * np.sqrt(reg_pts["md"] / max(1, reg_pts["md"].max()))
    fig, (ax, az) = plt.subplots(1, 2, figsize=(14, 5), gridspec_kw={"width_ratios": [2.2, 1]})
    carib = (-90.5, -68.0, 14.5, 20.5)
    for a, lim in ((ax, (-180, 180, -60, 75)), (az, carib)):
        a.set_xlim(lim[0], lim[1])
        a.set_ylim(lim[2], lim[3])
        a.grid(alpha=0.3)
        a.scatter(reg_pts["lon"], reg_pts["lat"], s=sz, c="#c0392b", alpha=0.6, edgecolor="k")
        a.set_xlabel("lon")
        a.set_ylabel("lat")
    for k, (nm, r) in enumerate(reg_pts.iterrows()):
        inc = carib[0] <= r["lon"] <= carib[1] and carib[2] <= r["lat"] <= carib[3]
        a = az if inc else ax
        thr = 2.5 if inc else 15.0
        near_above = ((abs(reg_pts["lon"] - r["lon"]) < thr) & (abs(reg_pts["lat"] - r["lat"]) < thr)
                      & (reg_pts["lat"] > r["lat"])).any()
        a.annotate(f"{nm}\n{int(r['md'])} MD px, {int(r['n'])} sc", (r["lon"], r["lat"]), fontsize=7,
                   xytext=(8, -22 if near_above else 6), textcoords="offset points")
    ax.add_patch(plt.Rectangle((carib[0], carib[2]), carib[1] - carib[0], carib[3] - carib[2], fill=False, ls="--"))
    ax.set_title("MARIDA regions: scene centers (size ~ sqrt(MD px)); no basemap")
    az.set_title("Caribbean zoom")
    figs["06_world_map.png"] = savefig(fig, figdir / "06_world_map.png")
    # 7 NaN fraction
    fig, ax = plt.subplots(figsize=(8, 4))
    for j, s in enumerate(SPLITS):
        ax.bar(np.arange(11) + (j - 1) * w, 100 * nan_cnt[s] / max(1, tot_px[s]), w, label=s)
    ax.set_xticks(np.arange(11), BAND_NAMES)
    ax.set_ylabel("% NaN pixels")
    ax.set_title("NaN share per band and split")
    ax.legend()
    figs["07_nan_by_band.png"] = savefig(fig, figdir / "07_nan_by_band.png")
    # 8 component sizes
    cs = np.array(comp_sizes)
    fig, ax = plt.subplots(figsize=(7, 4))
    bins = np.arange(1, max(cs.max() if len(cs) else 2, 2) + 2) - 0.5
    ax.hist(cs, bins=bins if len(bins) < 80 else 60, color="#2e86c1")
    ax.set_yscale("log")
    ax.set_xlabel("component size, px (8-connected, 10 m px)")
    ax.set_ylabel("count (log)")
    ax.set_title(f"Marine Debris connected components (n={len(cs)})")
    figs["08_md_component_sizes.png"] = savefig(fig, figdir / "08_md_component_sizes.png")

    # ---------------- eda.md ----------------
    md_total = sum(int(cls_px[s][1]) for s in cls_px)
    md_conf_tot = sum(md_conf[s] for s in md_conf)
    n_md_patches = sum(md_patch_count.values())
    L = []
    L.append("# MARIDA — EDA\n")
    L.append(f"Сгенерировано `scripts/eda_marida.py` за {time.time() - t0:.0f} с. Корень данных: `{root}`.\n")
    L.append("## Состав\n")
    L.append(f"- Патчей на диске: **{len(all_paths)}** (ожидалось {EXPECTED['patches']}); сцен: **{len(scenes)}** (ожидалось {EXPECTED['scenes']}).")
    L.append(f"- Сплиты: " + ", ".join(f"{s} **{sum(v == s for v in split_map.values())}**" for s in SPLITS)
             + f" (ожидалось {EXPECTED['splits']}). Патчей вне сплитов: {len(not_in_split)}; строк сплита без файла: {len(missing_files)}.")
    L.append(f"- Формы: {sorted(shapes)}; dtype: {sorted(dtypes)}; CRS: {len(crs_set)} разных ({', '.join(sorted(crs_set))}).")
    L.append(f"- Каналы: {', '.join(BAND_NAMES)} (ACOLITE rhorc).\n")
    L.append("## Marine Debris\n")
    L.append(f"- Всего MD px: **{md_total}** (ожидалось {EXPECTED['md_px']}); по conf High/Moderate/Low: "
             f"**{md_conf_tot[1]} / {md_conf_tot[2]} / {md_conf_tot[3]}** (ожидалось 1625/1235/539); без conf: {md_conf_tot[0]}.")
    L.append(f"- Патчей с MD: **{n_md_patches}** (ожидалось {EXPECTED['md_patches']}); по сплитам: "
             + ", ".join(f"{s} {md_patch_count[s]}" for s in SPLITS) + ".")
    L.append("\n| split | MD px | High | Moderate | Low | патчей с MD |\n|---|---|---|---|---|---|")
    for s in SPLITS:
        L.append(f"| {s} | {cls_px[s][1]} | {md_conf[s][1]} | {md_conf[s][2]} | {md_conf[s][3]} | {md_patch_count[s]} |")
    if len(cs):
        L.append(f"\n- Компоненты MD (8-связность): {len(cs)}; медиана {np.median(cs):.0f} px, "
                 f"среднее {cs.mean():.1f}, макс {cs.max()}; одиночных пикселей {int((cs == 1).sum())} "
                 f"({100 * (cs == 1).mean():.0f} %); ≤ 4 px: {100 * (cs <= 4).mean():.0f} %.")
    L.append("\n## Пиксели по классам и сплитам\n")
    L.append("| id | класс | train | val | test | всего | High | Moderate | Low |\n|---|---|---|---|---|---|---|---|---|")
    for c in range(16):
        tot = sum(int(cls_px[s][c]) for s in cls_px)
        L.append(f"| {c} | {CLASS_NAMES[c]} | {cls_px['train'][c]} | {cls_px['val'][c]} | {cls_px['test'][c]} | {tot} | "
                 f"{conf_all[c][1] if c else ''} | {conf_all[c][2] if c else ''} | {conf_all[c][3] if c else ''} |")
    lab = sum(int(cls_px[s][1:].sum()) for s in cls_px)
    alls = sum(int(cls_px[s].sum()) for s in cls_px)
    L.append(f"\nРазмечено {lab} из {alls} px ({100 * lab / alls:.2f} %): разметка частичная — площадь по маскам не считать.\n")
    L.append("## NaN\n")
    L.append("| band | " + " | ".join(SPLITS) + " |\n|---|---|---|---|")
    for b in range(11):
        L.append(f"| {BAND_NAMES[b]} | " + " | ".join(f"{100 * nan_cnt[s][b] / max(1, tot_px[s]):.3f} %" for s in SPLITS) + " |")
    L.append("\n## Средние спектры (rhorc, без NaN)\n")
    L.append("| класс | n px | " + " | ".join(BAND_NAMES) + " |\n|---|---|" + "---|" * 11)
    for c, (mu, sd, n) in spec_table.items():
        L.append(f"| {CLASS_NAMES[c]} | {n} | " + " | ".join(f"{m:.4f}" for m in mu) + " |")
    L.append("\n## FDI / NDVI (медианы по выборке)\n")
    L.append("FDI по формуле ТЗ §2 (λ4 = 664.8, λ8 = 832.9, λ11 = 1612.05 нм).\n")
    L.append("| класс | n | median FDI | median NDVI |\n|---|---|---|---|")
    for c, (f, n, k) in scat_stats.items():
        L.append(f"| {CLASS_NAMES[c]} | {k} | {f:.4f} | {n:.3f} |")
    L.append("\n## Регионы (по MD px)\n")
    L.append("| регион | тайлы | сцен | MD px | MD px High | bbox MD (lon_min,lat_min,lon_max,lat_max) |\n|---|---|---|---|---|---|")
    for nm, r in regions.iterrows():
        L.append(f"| {nm} | {r['tiles']} | {r['n_scenes']} | {r['md_px']} | {r['md_px_high']} | {r['bbox_md']} |")
    L.append("\n## Исключённая сцена\n")
    for sc, note in EXCLUDED_SCENES.items():
        present = sc in set(scenes["scene"])
        if present:
            r = scenes[scenes["scene"] == sc].iloc[0]
            L.append(f"- `{sc}`: **присутствует в архиве**, патчей {r['n_patches']} (train {r['n_train']}, val {r['n_val']}, "
                     f"test {r['n_test']}), MD px {r['md_px']}. {note}. В `marida_scenes.csv` помечена `excluded=True`.")
        else:
            L.append(f"- `{sc}`: в архиве отсутствует. {note}.")
    L.append("\n## Рисунки\n")
    for k, kb in figs.items():
        L.append(f"- `reports/eda/{k}` ({kb:.0f} КБ)")
        L.append(f"  ![]({k})")
    L.append("\n## Сверка с ожиданиями\n")
    checks = [
        ("патчей", len(all_paths), EXPECTED["patches"]),
        ("сцен", len(scenes), EXPECTED["scenes"]),
        ("train", sum(v == "train" for v in split_map.values()), 694),
        ("val", sum(v == "val" for v in split_map.values()), 328),
        ("test", sum(v == "test" for v in split_map.values()), 359),
        ("MD px", md_total, 3399), ("MD High", int(md_conf_tot[1]), 1625),
        ("MD Moderate", int(md_conf_tot[2]), 1235), ("MD Low", int(md_conf_tot[3]), 539),
        ("патчей с MD", n_md_patches, 373),
    ]
    L.append("| величина | факт | ожидание | совпало |\n|---|---|---|---|")
    for nm, f, e in checks:
        L.append(f"| {nm} | {f} | {e} | {'да' if f == e else '**НЕТ**'} |")
    (figdir / "eda.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    # ---------------- regions md ----------------
    R = ["# MARIDA — регионы и тайлы\n",
         "Источник: `reports/marida_scenes.csv` (генерирует `scripts/eda_marida.py`). MD px — пиксели класса Marine Debris "
         "в частичной разметке MARIDA (не площадь и не масса пластика). bbox — WGS84 `lon_min,lat_min,lon_max,lat_max` "
         "по границам патчей, содержащих MD.\n",
         "## Ранжирование регионов для живого демо (по MD px)\n",
         "| # | регион | тайлы | сцен | дат | MD px | MD px High | MD px без исключённой сцены | bbox MD | центр (lon,lat) |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for k, (nm, r) in enumerate(regions.iterrows(), 1):
        R.append(f"| {k} | {nm} | {r['tiles']} | {r['n_scenes']} | {r['n_dates']} | {r['md_px']} | {r['md_px_high']} | "
                 f"{r['md_px_excl_excluded']} | {r['bbox_md']} | {r['center']} |")
    R.append("\n## Лучшая сцена региона (по MD px) и её bbox MD — кандидат окна для живого демо\n")
    R.append("| регион | сцена (MD px; bbox MD lon_min,lat_min,lon_max,lat_max) |\n|---|---|")
    for nm, r in regions.iterrows():
        if r["top_scene"]:
            R.append(f"| {nm} | {r['top_scene']} |")
    R.append("\n## По тайлам\n")
    R.append("| тайл | регион | сцен | дат | MD px | MD px High | bbox MD | даты с MD |\n|---|---|---|---|---|---|---|---|")
    for t, r in tiles.iterrows():
        R.append(f"| {t} | {r['region']} | {r['n_scenes']} | {r['n_dates']} | {r['md_px']} | {r['md_px_high']} | "
                 f"{r['bbox_md']} | {r['dates_with_md']} |")
    R.append("\n## Примечания\n")
    R.append("- Сцена `S2_23-9-20_16PCC` (Bay Islands, 23.09.2020) помечена `excluded` (по ТЗ: авторы исключили из-за атмосферной "
             "коррекции); для выбора региона демо смотрите колонку «без исключённой сцены».")
    R.append("- Для живого демо нужен ещё безоблачный свежий снимок L2A (дорожка L6) — ранжирование здесь только по MARIDA.")
    (out / "marida_regions.md").write_text("\n".join(R) + "\n", encoding="utf-8")

    print(f"done in {time.time() - t0:.0f}s: {len(all_paths)} patches, {len(scenes)} scenes, MD px {md_total}, "
          f"conf {md_conf_tot[1:].tolist()}, MD patches {n_md_patches}")
    print("regions:\n", regions[["tiles", "n_scenes", "md_px", "bbox_md"]].to_string())


if __name__ == "__main__":
    main()
