#!/usr/bin/env python
"""inspect_dataset.py -- "what is in this unknown remote-sensing dataset?" in minutes.

Usage (PowerShell, from repo root):
    .venv\\Scripts\\python.exe scripts\\tools\\inspect_dataset.py --data-dir data\\MARIDA --out reports\\tools\\marida_inspect

What it does
  * recursively finds rasters (tif/tiff/jp2/npy/png/jpg) and tables/labels (csv/json/geojson/shp/txt);
  * groups files by name template (digits -> {n}, dates -> {date}, MGRS tiles -> {tile}) and
    pairs groups that share the same sample id (image <-> mask <-> conf ...);
  * per raster (header, all files): bands, dtype, nodata, CRS, resolution, size, band descriptions;
  * per group (sampled files): per-band min/max/percentiles/NaN share/zero share + histograms;
  * mask guess (1 band, integer values, few unique) + class balance (pixels and files per value);
  * value-range hints (reflectance 0..1 vs DN 0..10000, L2A offset 1000, SCL band ...);
  * writes <out>/index.html (tables + histograms), <out>/summary.json, <out>/files.csv,
    <out>/pairs_guess.csv (image,mask for the most likely pairing; feed it to label_forensics.py).
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import datetime as _dt
import html
import json
import os
import random
import re
import sys
import time
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*NotGeoreferenced.*")

RASTER_EXT = {".tif", ".tiff", ".jp2", ".npy", ".png", ".jpg", ".jpeg", ".img", ".vrt"}
TABLE_EXT = {".csv", ".json", ".geojson", ".shp", ".gpkg", ".txt", ".parquet", ".xml", ".yaml", ".yml"}
MASK_NAME_RE = re.compile(r"(^|[_\-.])(mask|masks|label|labels|lbl|gt|cl|seg|annotation|annot|y|target)([_\-.]|$)", re.I)

# token classifiers used for name templates / sample ids
_DATE_RES = [
    re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:?\d{2}:?\d{2})?$"),
    re.compile(r"^(19|20)\d{6}(T\d{6})?$"),
    re.compile(r"^\d{1,2}-\d{1,2}-\d{2,4}$"),
]
_TILE_RE = re.compile(r"^T?\d{2}[C-X][A-Z]{2}$")
_NUM_RE = re.compile(r"^\d+$")
# short "letters + 1-2 digits" tokens are scene/region prefixes (r05, scene3, reg12) and must NOT split a
# dataset into one group per scene (L30 rehearsal: r01_{n} .. r16_{n} -> 16 groups, class values of 1 group
# only, pairs mixed between scenes). Sensor / band / product tokens stay literal (they DO define a group).
_SCENE_TOK_RE = re.compile(r"^([A-Za-z]{1,8})(\d{1,2})$")
_KEEP_LITERAL_RE = re.compile(r"^(S[123]|L[1-9]|LC0?\d|LT0?\d|LE0?\d|MSI|OLI|B\d{1,2}A?|band\d{1,2}|b\d{1,2}a?|"
                              r"v\d{1,2}|rgb\d?|x\d|h\d{1,2}|w\d{1,2}|p\d{1,2})$", re.I)


def _tok_kind(tok: str) -> str | None:
    if any(r.match(tok) for r in _DATE_RES):
        return "{date}"
    if _TILE_RE.match(tok):
        return "{tile}"
    if _NUM_RE.match(tok):
        return "{n}"
    return None


def name_template(stem: str) -> tuple[str, str]:
    """'S2_1-12-19_48MYU_0_cl' -> ('S2_{date}_{tile}_{n}_cl', '1-12-19_48MYU_0')."""
    toks = re.split(r"(_)", stem)
    tpl, ids = [], []
    for t in toks:
        if t == "_":
            tpl.append(t)
            continue
        k = _tok_kind(t)
        if k:
            tpl.append(k)
            ids.append(t)
        else:
            # mixed token like 'I1-I5' / 'B02' stays literal; embedded long digit runs -> {n}
            m = re.fullmatch(r"([A-Za-z]*)(\d{3,})", t)
            m2 = _SCENE_TOK_RE.fullmatch(t) if not m and not _KEEP_LITERAL_RE.match(t) else None
            if m:
                tpl.append(m.group(1) + "{n}")
                ids.append(m.group(2))
            elif m2:
                tpl.append(m2.group(1) + "{n}")
                ids.append(t)  # keep the letters: 'r05' stays distinguishable in the sample id
            else:
                tpl.append(t)
    return "".join(tpl), "_".join(ids)


def dir_template(rel_dir: str) -> str:
    parts = []
    for p in Path(rel_dir).parts:
        parts.append(name_template(p)[0])
    return "/".join(parts) if parts else "."


# --------------------------------------------------------------------------- raster reading
def read_header(path: Path) -> dict:
    ext = path.suffix.lower()
    info = {"path": str(path), "ext": ext, "size_bytes": path.stat().st_size}
    try:
        if ext == ".npy":
            a = np.load(path, mmap_mode="r")
            shp = a.shape
            info.update(dtype=str(a.dtype), shape=list(shp))
            if a.ndim == 2:
                info.update(count=1, height=shp[0], width=shp[1])
            elif a.ndim == 3:
                # guess channel axis = smallest dim
                ch_axis = int(np.argmin(shp))
                info.update(count=shp[ch_axis], channel_axis=ch_axis,
                            height=[s for i, s in enumerate(shp) if i != ch_axis][0],
                            width=[s for i, s in enumerate(shp) if i != ch_axis][1])
            else:
                info.update(count=None)
            info.update(crs=None, res=None, nodata=None, descriptions=None)
            return info
        import rasterio

        with rasterio.open(path) as ds:
            info.update(
                count=ds.count, dtype=ds.dtypes[0], dtypes=sorted(set(ds.dtypes)), nodata=ds.nodata,
                crs=(ds.crs.to_string() if ds.crs else None),
                res=[round(float(ds.res[0]), 6), round(float(ds.res[1]), 6)] if ds.transform else None,
                width=ds.width, height=ds.height,
                descriptions=[d for d in ds.descriptions] if any(ds.descriptions) else None,
                bounds=list(ds.bounds), driver=ds.driver,
                tags={k: v[:200] for k, v in list(ds.tags().items())[:30]},
                scales=list(ds.scales) if any(s != 1 for s in ds.scales) else None,
                offsets=list(ds.offsets) if any(o != 0 for o in ds.offsets) else None,
            )
            if info["crs"] and ds.crs.is_projected is False and ds.crs.is_geographic:
                info["res_units"] = "deg"
            try:
                if ds.crs:
                    from rasterio.warp import transform_bounds

                    info["bounds_wgs84"] = [round(v, 6) for v in transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)]
            except Exception:
                pass
    except Exception as e:  # unreadable file
        info["error"] = f"{type(e).__name__}: {e}"
    return info


def read_array(path: Path, max_side: int | None = 256, full: bool = False) -> np.ndarray | None:
    """(C,H,W) array; decimated (nearest) to <= max_side unless full."""
    ext = path.suffix.lower()
    if ext == ".npy":
        a = np.load(path, mmap_mode="r")
        if a.ndim == 2:
            a = a[None]
        elif a.ndim == 3:
            ch = int(np.argmin(a.shape))
            a = np.moveaxis(a, ch, 0)
        else:
            return None
        if not full and max_side:
            s = max(1, int(np.ceil(max(a.shape[1:]) / max_side)))
            a = a[:, ::s, ::s]
        return np.asarray(a)
    import rasterio

    with rasterio.open(path) as ds:
        if full or not max_side or max(ds.height, ds.width) <= max_side:
            return ds.read()
        s = max(ds.height, ds.width) / max_side
        shape = (ds.count, max(1, int(ds.height / s)), max(1, int(ds.width / s)))
        from rasterio.enums import Resampling

        return ds.read(out_shape=shape, resampling=Resampling.nearest)


# --------------------------------------------------------------------------- tables
def inspect_table(path: Path) -> dict:
    ext = path.suffix.lower()
    out = {"path": str(path), "ext": ext, "size_bytes": path.stat().st_size}
    try:
        if ext in (".csv", ".parquet"):
            import pandas as pd

            df = pd.read_csv(path, nrows=200000) if ext == ".csv" else pd.read_parquet(path)
            out.update(rows=int(len(df)), cols=list(map(str, df.columns)))
            colinfo = []
            for c in df.columns[:60]:
                s = df[c]
                ci = {"col": str(c), "dtype": str(s.dtype), "missing_frac": round(float(s.isna().mean()), 4),
                      "nunique": int(s.nunique())}
                if s.dtype.kind in "if" and s.notna().any():
                    ci.update(min=float(s.min()), max=float(s.max()), mean=float(s.mean()))
                else:
                    ci["top"] = [str(v) for v in s.dropna().astype(str).value_counts().head(5).index]
                colinfo.append(ci)
            out["columns"] = colinfo
            out["head"] = df.head(5).astype(str).values.tolist()
        elif ext in (".json", ".geojson"):
            if path.stat().st_size > 300e6:
                out["note"] = "too big, skipped"
                return out
            d = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            out["top_type"] = type(d).__name__
            if isinstance(d, dict):
                out["keys"] = list(d.keys())[:40]
                if d.get("type") == "FeatureCollection":
                    feats = d.get("features", [])
                    out["n_features"] = len(feats)
                    out["geometry_types"] = dict(collections.Counter((f.get("geometry") or {}).get("type") for f in feats))
                    props = collections.Counter()
                    for f in feats[:2000]:
                        props.update((f.get("properties") or {}).keys())
                    out["property_keys"] = dict(props.most_common(40))
                    out["crs"] = d.get("crs")
                else:
                    # e.g. labels_mapping: name -> vector
                    vals = list(d.values())[:3]
                    out["sample_items"] = {k: str(v)[:200] for k, v in list(d.items())[:3]}
                    out["n_items"] = len(d)
                    out["value_types"] = [type(v).__name__ for v in vals]
            elif isinstance(d, list):
                out["n_items"] = len(d)
                out["sample_items"] = [str(v)[:200] for v in d[:3]]
        elif ext == ".txt":
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            out.update(lines=len(lines), head=lines[:5])
        elif ext in (".shp", ".gpkg"):
            try:
                import geopandas as gpd

                g = gpd.read_file(path, rows=5000)
                out.update(rows=int(len(g)), cols=list(map(str, g.columns)), crs=str(g.crs),
                           geometry_types=dict(collections.Counter(g.geom_type.astype(str))),
                           total_bounds=[float(v) for v in g.total_bounds])
            except Exception as e:
                out["error"] = f"geopandas: {e}"
        elif ext in (".xml", ".yaml", ".yml"):
            out["head"] = path.read_text(encoding="utf-8", errors="replace")[:800]
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


# --------------------------------------------------------------------------- core
def _hist_png(bands_samples: list[np.ndarray], names: list[str], title: str, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n = len(bands_samples)
    cols = min(4, n)
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(3.2 * cols, 2.2 * rows), squeeze=False)
    for i, ax in enumerate(axes.ravel()):
        if i >= n:
            ax.axis("off")
            continue
        v = bands_samples[i]
        v = v[np.isfinite(v)]
        ax.set_facecolor("#fcfcfb")
        if v.size:
            lo, hi = np.percentile(v, [0.5, 99.5])
            if hi <= lo:
                hi = lo + 1
            ax.hist(np.clip(v, lo, hi), bins=60, color="#2a78d6", edgecolor="#fcfcfb", linewidth=0.3)
        ax.set_title(str(names[i]), fontsize=9, color="#0b0b0b")
        ax.tick_params(labelsize=7, colors="#52514e")
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.set_yticks([])
    fig.suptitle(title, fontsize=10, color="#0b0b0b")
    fig.tight_layout()
    fig.savefig(path, dpi=80)
    plt.close(fig)


def _bar_png(labels: list[str], values: list[float], title: str, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    h = max(1.6, 0.28 * len(labels) + 0.8)
    fig, ax = plt.subplots(figsize=(6.4, h))
    y = np.arange(len(labels))
    ax.barh(y, values, color="#2a78d6", height=0.7)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8, color="#0b0b0b")
    ax.invert_yaxis()
    if max(values or [1]) / max(1, min([v for v in values if v > 0] or [1])) > 100:
        ax.set_xscale("log")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(axis="x", labelsize=7, colors="#52514e")
    for yi, v in zip(y, values):
        ax.text(v, yi, f" {int(v):,}", va="center", fontsize=7, color="#52514e")
    ax.set_title(title, fontsize=10, color="#0b0b0b")
    fig.tight_layout()
    fig.savefig(path, dpi=80)
    plt.close(fig)


def value_hints(stats: list[dict], dtype: str, descs: list | None) -> list[str]:
    hints = []
    if not stats:
        return hints
    p99 = [s.get("p99") for s in stats if s.get("p99") is not None]
    p1 = [s.get("p1") for s in stats if s.get("p1") is not None]
    mx = max(p99) if p99 else None
    mn = min(p1) if p1 else None
    if mx is None:
        return hints
    if dtype.startswith("float") and mx <= 2.0:
        hints.append("float values ~0..1 -> looks like reflectance already (scale=1).")
    elif mx > 100 and mx < 20000 and dtype in ("uint16", "int16", "float32", "int32"):
        hints.append("values in hundreds..thousands -> looks like S2 DN (reflectance = DN * 1e-4 [+ offset]).")
        if mn is not None and mn >= 900:
            hints.append("1st percentile >= ~1000 -> maybe L2A processing baseline >= 04.00 with BOA_ADD_OFFSET=-1000 "
                         "NOT applied yet: reflectance = (DN - 1000) * 1e-4. Check water: should be ~0.00-0.05.")
    if dtype == "uint8" and mx <= 255:
        hints.append("uint8 -> could be a quick-look / RGB PNG, or scaled data; reflectance not recoverable without scale.")
    if descs:
        up = [str(d).upper() for d in descs if d]
        if "SCL" in up:
            hints.append("band 'SCL' present -> Sen2Cor scene classification (L2A); use it as cloud/water mask, not a feature.")
        if any(d in up for d in ("VV", "VH")):
            hints.append("VV/VH bands -> Sentinel-1 SAR (dB or linear?) check range.")
    return hints


def is_mask_like(hdr: dict, arr: np.ndarray | None, name: str) -> tuple[bool, str]:
    if arr is None:
        return False, "unreadable"
    name_hit = bool(MASK_NAME_RE.search(Path(name).stem))
    if arr.shape[0] != 1:
        return False, f"{arr.shape[0]} bands"
    v = arr[0]
    fin = v[np.isfinite(v)] if v.dtype.kind == "f" else v.ravel()
    if fin.size == 0:
        return False, "empty"
    if v.dtype.kind == "f" and not np.all(np.equal(np.mod(fin[:20000], 1), 0)):
        return False, "non-integer float"
    u = np.unique(fin[: 200000])
    if len(u) <= 32:
        return True, f"{len(u)} unique integer values" + (" + name hint" if name_hit else "")
    return False, f"{len(u)}+ unique values"


_MASK_SUFFIX_RE = re.compile(r"([_\-.]?(mask|masks|label|labels|lbl|gt|cl|seg|segmentation|annotation|annot|"
                             r"target|class|classes|y))$", re.I)
_IMG_SUFFIX_RE = re.compile(r"([_\-.]?(img|image|images|s2|sentinel2|data|x|rgb|input))$", re.I)


def stem_key(stem: str, rx: re.Pattern) -> str:
    """Name key without the mask/image suffix word ('r05_001_mask' -> 'r05_001'); scene prefix is KEPT."""
    s = stem
    for _ in range(2):
        s2 = rx.sub("", s)
        if s2 == s or not s2:
            break
        s = s2
    return s.lower()


def pair_within(img_hs: list[dict], mask_hs: list[dict], ig: str, mg: str) -> list[tuple]:
    """Image <-> mask pairs that never cross scenes.

    1) full name key (stem without mask/image suffix; scene prefix kept) + the closest folder;
    2) only for masks left over: shared sample id, but ONLY if the id is unique on both sides
       (ids like '001' repeated in every scene are never used -- L30: 8 of 111 pairs were r04 <-> r12)."""
    def parent(h):
        return str(Path(h["rel"]).parent).replace("\\", "/")

    def sim(a, b):
        pa, pb = parent(a).split("/"), parent(b).split("/")
        n = 0
        for x, y in zip(pa, pb):
            if x != y:
                break
            n += 1
        return n

    by_key = collections.defaultdict(list)
    for h in img_hs:
        by_key[stem_key(Path(h["rel"]).stem, _IMG_SUFFIX_RE)].append(h)
        by_key[Path(h["rel"]).stem.lower()].append(h)
    out, used, left = [], set(), []
    for m in mask_hs:
        k = stem_key(Path(m["rel"]).stem, _MASK_SUFFIX_RE)
        cands = {id(h): h for h in by_key.get(k, []) + by_key.get(Path(m["rel"]).stem.lower(), [])}
        cands = sorted(cands.values(), key=lambda h: -sim(h, m))
        if cands and cands[0]["path"] not in used and (len(cands) == 1 or sim(cands[1], m) < sim(cands[0], m)):
            used.add(cands[0]["path"])
            out.append((k, cands[0]["path"], m["path"], ig, mg))
        else:
            left.append(m)
    if left:
        cnt_i = collections.Counter(h["sample_id"] for h in img_hs)
        cnt_m = collections.Counter(h["sample_id"] for h in mask_hs)
        by_id = {h["sample_id"]: h for h in img_hs if cnt_i[h["sample_id"]] == 1}
        for m in left:
            h = by_id.get(m["sample_id"])
            if h is not None and cnt_m[m["sample_id"]] == 1 and h["path"] not in used:
                used.add(h["path"])
                out.append((m["sample_id"], h["path"], m["path"], ig, mg))
    return out


def _pick(items: list, k: int, rng: random.Random) -> list:
    if len(items) <= k:
        return list(items)
    return rng.sample(items, k)


def run(data_dir: Path, out: Path, max_files: int = 20000, sample_per_group: int = 40,
        max_mask_files: int = 4000, threads: int = 8, seed: int = 0) -> dict:
    t0 = time.time()
    data_dir = Path(data_dir).resolve()
    out = Path(out)
    (out / "img").mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)

    rasters, tables, other = [], [], collections.Counter()
    for dp, dn, fn in os.walk(data_dir):
        dn.sort()
        for f in sorted(fn):
            p = Path(dp) / f
            ext = p.suffix.lower()
            if ext in RASTER_EXT:
                rasters.append(p)
            elif ext in TABLE_EXT:
                tables.append(p)
            else:
                other[ext or "<none>"] += 1
    n_rasters_total = len(rasters)
    if len(rasters) > max_files:
        rasters = sorted(_pick(rasters, max_files, rng))

    # ---- headers (all, threaded)
    with cf.ThreadPoolExecutor(threads) as ex:
        headers = list(ex.map(read_header, rasters))
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for h in headers:
        p = Path(h["path"])
        rel = p.relative_to(data_dir)
        tpl, sid = name_template(p.stem)
        h["rel"] = str(rel).replace("\\", "/")
        h["sample_id"] = sid or p.stem
        h["group"] = f"{dir_template(str(rel.parent))}/{tpl}{p.suffix.lower()}"
        groups[h["group"]].append(h)

    # ---- per group: consistency + sampled stats + mask detection
    group_rows = []
    for gname, hs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        ok = [h for h in hs if "error" not in h]
        g = {"group": gname, "n_files": len(hs), "n_errors": len(hs) - len(ok)}
        for key in ("count", "dtype", "crs", "nodata", "driver"):
            g[key] = dict(collections.Counter(str(h.get(key)) for h in ok).most_common(8))
        g["res"] = dict(collections.Counter(str(h.get("res")) for h in ok).most_common(8))
        g["size_hw"] = dict(collections.Counter(f"{h.get('height')}x{h.get('width')}" for h in ok).most_common(8))
        descs = next((h.get("descriptions") for h in ok if h.get("descriptions")), None)
        g["descriptions"] = descs
        g["example"] = hs[0]["rel"]
        g["bytes_total"] = int(sum(h["size_bytes"] for h in hs))
        bb = [h["bounds_wgs84"] for h in ok if h.get("bounds_wgs84")]
        if bb:
            a = np.array(bb)
            g["extent_wgs84"] = [float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 2].max()), float(a[:, 3].max())]
        # sampled pixel stats
        samp = _pick(ok, sample_per_group, rng)
        arrs = []
        for h in samp:
            try:
                arrs.append((h, read_array(Path(h["path"]), max_side=256)))
            except Exception as e:
                h["read_error"] = str(e)
        arrs = [(h, a) for h, a in arrs if a is not None]
        mask_votes = [is_mask_like(h, a, h["path"]) for h, a in arrs]
        g["mask_like"] = bool(mask_votes) and sum(m for m, _ in mask_votes) >= 0.8 * len(mask_votes)
        g["mask_reason"] = mask_votes[0][1] if mask_votes else ""
        g["name_says_mask"] = bool(MASK_NAME_RE.search(Path(hs[0]["path"]).stem))
        nb = max((a.shape[0] for _, a in arrs), default=0)
        band_stats, band_samples = [], []
        for b in range(nb):
            vals = []
            nan_n = tot = zero_n = nod_n = 0
            for h, a in arrs:
                if b >= a.shape[0]:
                    continue
                v = a[b].astype(np.float64).ravel()
                tot += v.size
                nan_n += int(np.isnan(v).sum())
                zero_n += int((v == 0).sum())
                if h.get("nodata") is not None and np.isfinite(h["nodata"]):
                    nod_n += int((v == h["nodata"]).sum())
                    v = v[v != h["nodata"]]
                v = v[np.isfinite(v)]
                if v.size > 4000:
                    v = v[np.random.default_rng(seed).choice(v.size, 4000, replace=False)]
                vals.append(v)
            v = np.concatenate(vals) if vals else np.array([])
            name = descs[b] if descs and b < len(descs) and descs[b] else f"band{b + 1}"
            st = {"band": b + 1, "name": name, "nan_frac": round(nan_n / max(tot, 1), 5),
                  "zero_frac": round(zero_n / max(tot, 1), 5), "nodata_frac": round(nod_n / max(tot, 1), 5)}
            if v.size:
                q = np.percentile(v, [1, 5, 50, 95, 99])
                st.update(min=float(v.min()), p1=float(q[0]), p5=float(q[1]), p50=float(q[2]), p95=float(q[3]),
                          p99=float(q[4]), max=float(v.max()), mean=float(v.mean()), std=float(v.std()))
            band_stats.append(st)
            band_samples.append(v)
        g["band_stats"] = band_stats
        g["n_sampled"] = len(arrs)
        dt = next(iter(g["dtype"]), "")
        g["hints"] = [] if g["mask_like"] else value_hints(band_stats, dt, descs)
        slug = re.sub(r"[^A-Za-z0-9]+", "_", gname).strip("_")[:80] or "root"
        g["slug"] = slug
        if band_samples and not g["mask_like"]:
            try:
                _hist_png(band_samples, [s["name"] for s in band_stats], f"{gname} (n={len(arrs)} files sampled)",
                          out / "img" / f"hist_{slug}.png")
                g["hist_png"] = f"img/hist_{slug}.png"
            except Exception as e:
                g["hist_error"] = str(e)
        # class balance for mask-like groups (full-res read, many files)
        if g["mask_like"]:
            mfiles = _pick(ok, max_mask_files, rng)

            def _count(h):
                a = read_array(Path(h["path"]), full=True)[0]
                if a.dtype.kind == "f":
                    a = np.where(np.isfinite(a), a, -1).astype(np.int64)
                u, c = np.unique(a, return_counts=True)
                return dict(zip(u.tolist(), c.tolist()))

            with cf.ThreadPoolExecutor(threads) as ex:
                counts = list(ex.map(_count, mfiles))
            px, files = collections.Counter(), collections.Counter()
            for c in counts:
                px.update(c)
                files.update(c.keys())
            tot = sum(px.values())
            g["class_balance"] = [
                {"value": int(k), "pixels": int(px[k]), "pixel_frac": round(px[k] / max(tot, 1), 6),
                 "files_with": int(files[k]), "files_frac": round(files[k] / max(len(counts), 1), 4)}
                for k in sorted(px)
            ]
            g["n_mask_files_counted"] = len(counts)
            try:
                _bar_png([str(r["value"]) for r in g["class_balance"]], [r["pixels"] for r in g["class_balance"]],
                         f"pixels per value: {gname}", out / "img" / f"classes_{slug}.png")
                g["classes_png"] = f"img/classes_{slug}.png"
            except Exception as e:
                g["classes_error"] = str(e)
        group_rows.append(g)

    # ---- pairing of groups by sample id
    ids_by_group = {g: {h["sample_id"] for h in hs} for g, hs in groups.items()}
    pairs = []
    gl = list(groups)
    for i in range(len(gl)):
        for j in range(i + 1, len(gl)):
            a, b = ids_by_group[gl[i]], ids_by_group[gl[j]]
            inter = len(a & b)
            if inter:
                pairs.append({"group_a": gl[i], "group_b": gl[j], "shared_ids": inter,
                              "frac_a": round(inter / len(a), 4), "frac_b": round(inter / len(b), 4)})
    pairs.sort(key=lambda r: -r["shared_ids"])
    # families: connected components of groups sharing >=50% ids
    fam_parent = {g: g for g in groups}

    def find(x):
        while fam_parent[x] != x:
            fam_parent[x] = fam_parent[fam_parent[x]]
            x = fam_parent[x]
        return x

    for p in pairs:
        if min(p["frac_a"], p["frac_b"]) >= 0.9:  # same sample ids on both sides
            fam_parent[find(p["group_a"])] = find(p["group_b"])
    families = collections.defaultdict(list)
    for g in groups:
        families[find(g)].append(g)
    # merge families whose groups only differ by dir template (MARIDA: patches/S2_{date}_{tile}/...)
    gmeta = {g["group"]: g for g in group_rows}
    pairs_guess = []
    fam_rows = []
    for root, members in families.items():
        masks = [m for m in members if gmeta[m]["mask_like"]]
        imgs = [m for m in members if not gmeta[m]["mask_like"]]
        # prefer mask whose name looks like a mask and has most values; image with most bands
        masks.sort(key=lambda m: (not gmeta[m]["name_says_mask"], -len(gmeta[m].get("class_balance", []))))
        def _prefix(a, b):
            n = 0
            for x, y in zip(a.split("/")[:-1], b.split("/")[:-1]):
                if x != y:
                    break
                n += 1
            return n

        m0 = masks[0] if masks else None
        imgs.sort(key=lambda m: (-(_prefix(m, m0) if m0 else 0),
                                 -max((int(k) for k in gmeta[m]["count"] if k not in ("None",)), default=0), m))
        fam_rows.append({"members": members, "images": imgs, "masks": masks})
        if masks and imgs:
            pairs_guess += pair_within(groups[imgs[0]], groups[masks[0]], imgs[0], masks[0])

    # ---- tables
    table_info = [inspect_table(p) for p in tables[:200]]
    for t in table_info:
        t["rel"] = str(Path(t["path"]).relative_to(data_dir)).replace("\\", "/")

    summary = {
        "data_dir": str(data_dir), "generated": _dt.datetime.now().isoformat(timespec="seconds"),
        "n_rasters": n_rasters_total, "n_rasters_scanned": len(rasters), "n_tables": len(tables),
        "other_ext": dict(other), "groups": group_rows, "group_pairs": pairs[:200],
        "families": fam_rows, "n_pairs_guess": len(pairs_guess), "tables": table_info,
        "seconds": round(time.time() - t0, 1),
    }
    # merge-by-family totals (e.g. MARIDA: many per-scene dirs collapse into one template)
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    import csv

    with open(out / "files.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        cols = ["rel", "group", "sample_id", "count", "dtype", "nodata", "crs", "res", "height", "width", "size_bytes",
                "bounds_wgs84", "error"]
        w.writerow(cols)
        for h in headers:
            w.writerow([h.get(c) for c in cols])
    with open(out / "pairs_guess.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "image", "mask", "image_group", "mask_group", "group"])
        # group = scene prefix (id without trailing numbers) -> label_forensics splits folds by scene
        grp = [re.sub(r"([_\-]\d+)+$", "", str(p[0])) or str(p[0]) for p in pairs_guess]
        if len(set(grp)) < 2:
            grp = [str(p[0]) for p in pairs_guess]
        w.writerows([list(p) + [g] for p, g in zip(pairs_guess, grp)])
    write_html(summary, out / "index.html")
    summary["seconds"] = round(time.time() - t0, 1)
    return summary


# --------------------------------------------------------------------------- html
def _t(rows: list[dict], cols: list[str] | None = None) -> str:
    if not rows:
        return "<p><i>none</i></p>"
    cols = cols or list(rows[0].keys())
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in cols)
    body = []
    for r in rows:
        tds = []
        for c in cols:
            v = r.get(c, "")
            if isinstance(v, float):
                v = f"{v:.4g}"
            elif isinstance(v, (dict, list)):
                v = json.dumps(v, default=str)
            tds.append(f"<td>{html.escape(str(v))}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    return f"<table><tr>{head}</tr>{''.join(body)}</table>"


def write_html(s: dict, path: Path) -> None:
    css = """body{font-family:Segoe UI,Arial,sans-serif;background:#fcfcfb;color:#0b0b0b;margin:24px;max-width:1400px}
table{border-collapse:collapse;font-size:12px;margin:6px 0 14px}td,th{border:1px solid #ddd;padding:3px 6px;text-align:left;vertical-align:top}
th{background:#f0efec}h2{border-bottom:2px solid #2a78d6;padding-bottom:3px}.muted{color:#52514e}.hint{background:#fff6e0;padding:4px 8px;margin:3px 0}
code{background:#f0efec;padding:1px 4px}"""
    parts = [f"<html><head><meta charset='utf-8'><title>inspect: {html.escape(s['data_dir'])}</title><style>{css}</style></head><body>",
             f"<h1>Dataset inspection</h1><p class='muted'>{html.escape(s['data_dir'])} &middot; {s['generated']} &middot; "
             f"{s['n_rasters']} rasters ({s['n_rasters_scanned']} scanned), {s['n_tables']} tables/lists, "
             f"other: {html.escape(json.dumps(s['other_ext']))} &middot; {s['seconds']} s</p>"]
    parts.append("<h2>Groups (name templates)</h2>")
    parts.append(_t([{k: g.get(k) for k in ("group", "n_files", "count", "dtype", "nodata", "crs", "res", "size_hw",
                                             "mask_like", "mask_reason", "bytes_total", "example")} for g in s["groups"]]))
    parts.append("<h2>Families (groups sharing sample ids) &rarr; suggested image / mask</h2>")
    parts.append(_t(s["families"]))
    parts.append(f"<p>pairs_guess.csv: {s['n_pairs_guess']} image-mask pairs.</p>")
    parts.append("<h2>Group pairs by shared sample id</h2>" + _t(s["group_pairs"][:50]))
    for g in s["groups"]:
        parts.append(f"<h2>{html.escape(g['group'])}</h2><p class='muted'>{g['n_files']} files, sampled {g['n_sampled']}; "
                     f"descriptions: {html.escape(str(g['descriptions']))}; extent WGS84: {g.get('extent_wgs84')}</p>")
        for hnt in g.get("hints", []):
            parts.append(f"<div class='hint'>{html.escape(hnt)}</div>")
        parts.append(_t(g["band_stats"], ["band", "name", "min", "p1", "p5", "p50", "p95", "p99", "max", "mean",
                                           "std", "nan_frac", "zero_frac", "nodata_frac"]))
        if g.get("hist_png"):
            parts.append(f"<img src='{g['hist_png']}' style='max-width:100%'>")
        if g.get("class_balance"):
            parts.append(f"<h3>Class balance ({g['n_mask_files_counted']} mask files)</h3>")
            parts.append(_t(g["class_balance"]))
            if g.get("classes_png"):
                parts.append(f"<img src='{g['classes_png']}'>")
    parts.append("<h2>Tables / lists / labels</h2>")
    for t in s["tables"]:
        parts.append(f"<h3>{html.escape(t['rel'])}</h3>")
        if t.get("columns"):
            parts.append(f"<p>{t.get('rows')} rows</p>" + _t(t["columns"]))
        else:
            parts.append("<pre>" + html.escape(json.dumps({k: v for k, v in t.items() if k not in ('path',)},
                                                           indent=1, default=str)[:3000]) + "</pre>")
    parts.append("</body></html>")
    path.write_text("\n".join(parts), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-files", type=int, default=20000, help="max rasters for header scan (random sample above)")
    ap.add_argument("--sample-per-group", type=int, default=40, help="files per group for pixel statistics")
    ap.add_argument("--max-mask-files", type=int, default=4000, help="mask files per group for class balance")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    s = run(Path(a.data_dir), Path(a.out), a.max_files, a.sample_per_group, a.max_mask_files, a.threads, a.seed)
    print(f"[inspect] {s['n_rasters']} rasters, {len(s['groups'])} groups, {s['n_tables']} tables, "
          f"{s['n_pairs_guess']} image-mask pairs guessed, {s['seconds']} s -> {Path(a.out) / 'index.html'}")
    for g in s["groups"][:15]:
        print(f"  {g['n_files']:6d}  {g['group']}  bands={list(g['count'])} dtype={list(g['dtype'])} "
              f"res={list(g['res'])[:2]} mask={g['mask_like']}")
    for fam in s["families"]:
        if fam["masks"] and fam["images"]:
            print(f"  pair: image={fam['images'][0]}  mask={fam['masks'][0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
