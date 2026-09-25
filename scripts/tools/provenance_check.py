#!/usr/bin/env python
"""provenance_check.py -- do the organizer's files overlap with OUR training data (or with each other)?

Usage (PowerShell, repo root):
    .venv\\Scripts\\python.exe scripts\\tools\\provenance_check.py --ours data\\MARIDA\\patches --theirs D:\\org\\train --out reports\\tools\\prov_org
    # MARIDA self-check (split lists as inputs):
    .venv\\Scripts\\python.exe scripts\\tools\\provenance_check.py --ours data\\MARIDA\\splits\\train_X.txt --theirs data\\MARIDA\\splits\\val_X.txt --list-root data\\MARIDA\\patches --out reports\\tools\\marida_provenance

Inputs for --ours / --theirs (several allowed): a folder (recursive rasters), a glob, or a .txt list of names/stems
(resolved against --list-root). Masks are skipped by default (--exclude).

Checks (each pair ours x theirs; with --within also theirs x theirs):
  1. scene key: MGRS tile + acquisition date parsed from path names and GeoTIFF tags (S2_1-12-19_48MYU, T16PCC_20190412,
     S2A_MSIL2A_20190412T..._T16PCC...); also tile-only and date-only matches;
  2. geography: bbox in WGS84 intersect (IoU and share of the smaller box);
  3. identical files: SHA-1 of bytes;
  4. content: perceptual hash (DCT 8x8 of a 32x32 grey thumbnail; Hamming distance) and Pearson correlation of 16x16
     z-scored thumbnails, max over the 8 flips/rotations (catches re-exported / re-scaled / flipped copies).
Output: <out>/pairs.csv (every flagged pair), <out>/theirs_files.csv (per organizer file flags), <out>/provenance.md.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import csv
import datetime as _dt
import glob
import hashlib
import json
import os
import re
import sys
import time
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
RASTER_EXT = {".tif", ".tiff", ".jp2", ".png", ".npy", ".jpg", ".jpeg"}
DEFAULT_EXCLUDE = r"(_cl|_conf|mask|label|_gt|_lbl)[^/\\]*$"

TILE_RE = re.compile(r"(?<![A-Za-z0-9])T?(\d{2}[C-HJ-NP-X][A-HJ-NP-Z]{2})(?![A-Za-z0-9])")
DATE_RES = [
    (re.compile(r"(?<!\d)((?:19|20)\d{2})-(\d{2})-(\d{2})(?!\d)"), "ymd"),
    (re.compile(r"(?<!\d)((?:20)[12]\d)(\d{2})(\d{2})(?:T\d{6})?(?!\d)"), "ymd"),
    (re.compile(r"(?<![\d-])(\d{1,2})-(\d{1,2})-(\d{2})(?![\d-])"), "dmy2"),  # MARIDA: S2_1-12-19_48MYU
]


def parse_tile_date(text: str) -> tuple[set, set]:
    tiles, dates = set(), set()
    for m in TILE_RE.finditer(text):
        tiles.add(m.group(1))
    for rx, kind in DATE_RES:
        for m in rx.finditer(text):
            try:
                if kind == "ymd":
                    d = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                else:
                    d = _dt.date(2000 + int(m.group(3)), int(m.group(2)), int(m.group(1)))
                if 2013 <= d.year <= 2035:
                    dates.add(d.isoformat())
            except ValueError:
                pass
    return tiles, dates


# --------------------------------------------------------------------------- collect
def collect(spec: str, list_root: str | None, include: str | None, exclude: str | None) -> list[Path]:
    p = Path(spec)
    inc = re.compile(include) if include else None
    exc = re.compile(exclude, re.I) if exclude else None
    if p.is_file() and p.suffix.lower() == ".txt":
        root = Path(list_root) if list_root else p.parent.parent
        index = {}
        for f in _walk(root):
            index.setdefault(f.stem, f)
            index.setdefault(f.stem.removeprefix("S2_"), f)
        files = []
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            s = Path(ln.strip()).stem if ln.strip() else ""
            if s and s in index:
                files.append(index[s])
            elif s and ("S2_" + s) in index:
                files.append(index["S2_" + s])
    elif p.is_dir():
        files = _walk(p)
    else:
        files = sorted(Path(x) for x in glob.glob(spec, recursive=True))
    out = []
    for f in files:
        s = str(f).replace("\\", "/")
        if f.suffix.lower() not in RASTER_EXT:
            continue
        if inc and not inc.search(s):
            continue
        if exc and exc.search(s):
            continue
        out.append(f)
    return out


def _walk(root: Path) -> list[Path]:
    out = []
    for dp, dn, fn in os.walk(root):
        dn.sort()
        for f in sorted(fn):
            if Path(f).suffix.lower() in RASTER_EXT:
                out.append(Path(dp) / f)
    return out


# --------------------------------------------------------------------------- per file
def _dct_phash(g32: np.ndarray) -> int:
    from scipy.fft import dctn

    d = dctn(g32, norm="ortho")[:8, :8].ravel()[1:]
    bits = d > np.median(d)
    return int("".join("1" if b else "0" for b in bits), 2)


def file_info(path: Path, thumb: int = 16, hash_bytes: bool = True) -> dict:
    info = {"path": str(path), "size": path.stat().st_size}
    text = str(path).replace("\\", "/")
    try:
        if path.suffix.lower() == ".npy":
            a = np.load(path).astype(np.float32)
            a = a[None] if a.ndim == 2 else (np.moveaxis(a, -1, 0) if a.shape[-1] < a.shape[0] else a)
            from scipy.ndimage import zoom

            arr = np.stack([zoom(b, (32 / b.shape[0], 32 / b.shape[1]), order=1) for b in a])
        else:
            import rasterio
            from rasterio.enums import Resampling

            with rasterio.open(path) as ds:
                tags = ds.tags()
                text += " " + " ".join(f"{k}={v}" for k, v in tags.items())
                info["crs"] = ds.crs.to_string() if ds.crs else ""
                info["shape"] = f"{ds.count}x{ds.height}x{ds.width}"
                if ds.crs:
                    from rasterio.warp import transform_bounds

                    info["bbox"] = list(transform_bounds(ds.crs, "EPSG:4326", *ds.bounds))
                arr = ds.read(out_shape=(ds.count, 32, 32), resampling=Resampling.average).astype(np.float32)
                if ds.nodata is not None and np.isfinite(ds.nodata):
                    arr[arr == ds.nodata] = np.nan
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {e}"
        return info
    tiles, dates = parse_tile_date(text)
    info["tiles"] = sorted(tiles)
    info["dates"] = sorted(dates)
    # grey thumbnail: mean of per-band robust-normalised bands (invariant to scale/offset per band)
    bands = []
    for b in arr:
        fin = b[np.isfinite(b)]
        if fin.size < 16 or np.nanstd(b) == 0:
            continue
        lo, hi = np.percentile(fin, [2, 98])
        bands.append((np.nan_to_num(b, nan=np.median(fin)) - lo) / (hi - lo + 1e-12))
    if bands:
        g = np.mean(bands, 0)
        info["phash"] = _dct_phash(g)
        t = g.reshape(thumb, 32 // thumb, thumb, 32 // thumb).mean((1, 3))
        info["thumb"] = t
    if hash_bytes:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        info["sha1"] = h.hexdigest()
    return info


# --------------------------------------------------------------------------- compare
def _dihedral(t: np.ndarray) -> list[np.ndarray]:
    out = []
    for k in range(4):
        r = np.rot90(t, k)
        out += [r, np.fliplr(r)]
    return out


def _z(v: np.ndarray) -> np.ndarray:
    v = v - v.mean(1, keepdims=True)
    return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-12)


def compare(A: list[dict], B: list[dict], same: bool, a) -> tuple[list[dict], dict]:
    """Return flagged pairs and per-B flags."""
    pairs = collections.defaultdict(dict)
    # 1. scene keys
    sk_a = collections.defaultdict(list)
    tile_a = collections.defaultdict(list)
    date_a = collections.defaultdict(list)
    for i, x in enumerate(A):
        for t in x.get("tiles", []):
            tile_a[t].append(i)
            for d in x.get("dates", []):
                sk_a[(t, d)].append(i)
        for d in x.get("dates", []):
            date_a[d].append(i)
    flags = [collections.Counter() for _ in B]
    for j, y in enumerate(B):
        for t in y.get("tiles", []):
            for d in y.get("dates", []):
                for i in sk_a.get((t, d), []):
                    if same and i >= j:
                        continue
                    pairs[(i, j)]["scene"] = f"{t}_{d}"
                    flags[j]["scene"] += 1
            if tile_a.get(t):
                flags[j]["tile"] += len([i for i in tile_a[t] if not (same and i == j)])
        for d in y.get("dates", []):
            if date_a.get(d):
                flags[j]["date"] += len([i for i in date_a[d] if not (same and i == j)])
    # 2. bbox
    ia = [i for i, x in enumerate(A) if x.get("bbox")]
    jb = [j for j, y in enumerate(B) if y.get("bbox")]
    if ia and jb:
        ba = np.array([A[i]["bbox"] for i in ia])
        bb = np.array([B[j]["bbox"] for j in jb])
        area_a = (ba[:, 2] - ba[:, 0]) * (ba[:, 3] - ba[:, 1])
        area_b = (bb[:, 2] - bb[:, 0]) * (bb[:, 3] - bb[:, 1])
        for s in range(0, len(ia), 500):
            x0 = np.maximum(ba[s:s + 500, None, 0], bb[None, :, 0])
            y0 = np.maximum(ba[s:s + 500, None, 1], bb[None, :, 1])
            x1 = np.minimum(ba[s:s + 500, None, 2], bb[None, :, 2])
            y1 = np.minimum(ba[s:s + 500, None, 3], bb[None, :, 3])
            inter = np.clip(x1 - x0, 0, None) * np.clip(y1 - y0, 0, None)
            union = area_a[s:s + 500, None] + area_b[None, :] - inter
            iou = inter / np.maximum(union, 1e-18)
            frac = inter / np.maximum(np.minimum(area_a[s:s + 500, None], area_b[None, :]), 1e-18)
            for r, c in zip(*np.nonzero(frac > a.min_overlap)):
                i, j = ia[s + r], jb[c]
                if same and i >= j:
                    continue
                pairs[(i, j)]["bbox_iou"] = round(float(iou[r, c]), 4)
                pairs[(i, j)]["bbox_frac_smaller"] = round(float(frac[r, c]), 4)
                flags[j]["bbox"] += 1
    # 3. sha1
    sha_a = collections.defaultdict(list)
    for i, x in enumerate(A):
        if x.get("sha1"):
            sha_a[x["sha1"]].append(i)
    for j, y in enumerate(B):
        for i in sha_a.get(y.get("sha1"), []):
            if same and i >= j:
                continue
            pairs[(i, j)]["identical_file"] = True
            flags[j]["identical"] += 1
    # 4. content: correlation (max over dihedral) + phash
    ta = [i for i, x in enumerate(A) if "thumb" in x]
    tb = [j for j, y in enumerate(B) if "thumb" in y]
    if ta and tb:
        VA = _z(np.stack([A[i]["thumb"].ravel() for i in ta]))
        best = np.full((len(ta), len(tb)), -1.0, dtype=np.float32)
        for k in range(8):
            VB = _z(np.stack([_dihedral(B[j]["thumb"])[k].ravel() for j in tb]))
            best = np.maximum(best, (VA @ VB.T).astype(np.float32))
        pa = np.array([A[i].get("phash", 0) for i in ta], dtype=np.uint64)
        pb = np.array([B[j].get("phash", 0) for j in tb], dtype=np.uint64)
        for r, c in zip(*np.nonzero(best >= a.corr_thr)):
            i, j = ta[r], tb[c]
            if same and i >= j:
                continue
            pairs[(i, j)]["thumb_corr"] = round(float(best[r, c]), 4)
            flags[j]["content"] += 1
        # phash hamming (identity orientation)
        for s in range(0, len(pa), 500):
            x = np.bitwise_xor(pa[s:s + 500, None], pb[None, :])
            ham = np.zeros(x.shape, dtype=np.int32)
            for bit in range(63):
                ham += ((x >> np.uint64(bit)) & np.uint64(1)).astype(np.int32)
            for r, c in zip(*np.nonzero(ham <= a.phash_max)):
                i, j = ta[s + r], tb[c]
                if same and i >= j:
                    continue
                pairs[(i, j)]["phash_hamming"] = int(ham[r, c])
                flags[j]["phash"] += 1
        stats = {"thumb_corr_p99": float(np.percentile(best, 99)), "thumb_corr_max": float(best.max())}
    else:
        stats = {}
    rows = []
    for (i, j), d in pairs.items():
        rows.append({"ours": A[i]["path"], "theirs": B[j]["path"], **d})
    return rows, {"flags": flags, **stats}


# --------------------------------------------------------------------------- main
def run(a) -> dict:
    t0 = time.time()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    exclude = None if a.no_exclude else (a.exclude or DEFAULT_EXCLUDE)
    ours_files = [f for s in a.ours for f in collect(s, a.list_root, a.include, exclude)]
    theirs_files = [f for s in a.theirs for f in collect(s, a.list_root, a.include, exclude)] if a.theirs else []
    if a.max_files:
        ours_files, theirs_files = ours_files[:a.max_files], theirs_files[:a.max_files]
    with cf.ThreadPoolExecutor(a.threads) as ex:
        A = list(ex.map(lambda p: file_info(p, hash_bytes=not a.no_sha1), ours_files))
        B = list(ex.map(lambda p: file_info(p, hash_bytes=not a.no_sha1), theirs_files))
    print(f"[provenance] ours {len(A)} files, theirs {len(B)} files, read in {time.time() - t0:.0f} s")
    rows, meta = compare(A, B, same=False, a=a)
    within_rows, within_meta = ([], {})
    if a.within and B:
        within_rows, within_meta = compare(B, B, same=True, a=a)
    # outputs
    keys = ["ours", "theirs", "scene", "bbox_iou", "bbox_frac_smaller", "identical_file", "thumb_corr", "phash_hamming"]
    with open(out / "pairs.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["kind"] + keys)
        w.writeheader()
        for r in rows:
            w.writerow({"kind": "ours-theirs", **r})
        for r in within_rows:
            w.writerow({"kind": "theirs-theirs", **r})
    with open(out / "theirs_files.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["theirs", "tiles", "dates", "bbox", "n_scene", "n_tile", "n_date", "n_bbox", "n_identical",
                    "n_content", "n_phash"])
        for y, fl in zip(B, meta["flags"]):
            w.writerow([y["path"], ";".join(y.get("tiles", [])), ";".join(y.get("dates", [])),
                        json.dumps([round(v, 5) for v in y["bbox"]]) if y.get("bbox") else "",
                        fl["scene"], fl["tile"], fl["date"], fl["bbox"], fl["identical"], fl["content"], fl["phash"]])
    summ = summarize(A, B, rows, meta, within_rows, a, time.time() - t0)
    (out / "provenance.md").write_text(summ["md"], encoding="utf-8")
    (out / "summary.json").write_text(json.dumps({k: v for k, v in summ.items() if k != "md"}, indent=1), encoding="utf-8")
    return summ


def summarize(A, B, rows, meta, within_rows, a, secs) -> dict:
    fl = meta["flags"]
    n = len(B)

    def cnt(k):
        return sum(1 for f in fl if f[k] > 0)

    scenes_a = collections.Counter((t, d) for x in A for t in x.get("tiles", []) for d in x.get("dates", []))
    scenes_b = collections.Counter((t, d) for x in B for t in x.get("tiles", []) for d in x.get("dates", []))
    shared = sorted(set(scenes_a) & set(scenes_b))
    tiles_shared = sorted({t for t, _ in scenes_a} & {t for t, _ in scenes_b})
    s = {
        "ours_files": len(A), "theirs_files": n,
        "theirs_with_scene_match": cnt("scene"), "theirs_with_tile_match": cnt("tile"),
        "theirs_with_date_match": cnt("date"), "theirs_with_bbox_overlap": cnt("bbox"),
        "theirs_identical_files": cnt("identical"), "theirs_content_match": cnt("content"),
        "theirs_phash_match": cnt("phash"),
        "scenes_ours": len(scenes_a), "scenes_theirs": len(scenes_b), "scenes_shared": len(shared),
        "tiles_shared": tiles_shared, "shared_scenes": [f"{t}_{d}" for t, d in shared],
        "theirs_without_georef": sum(1 for y in B if not y.get("bbox")),
        "theirs_without_tile_date": sum(1 for y in B if not (y.get("tiles") and y.get("dates"))),
        "within_pairs": len(within_rows),
        "within_identical": sum(1 for r in within_rows if r.get("identical_file")),
        "within_bbox": sum(1 for r in within_rows if "bbox_iou" in r),
        "within_content": sum(1 for r in within_rows if "thumb_corr" in r),
        "thumb_corr_p99": meta.get("thumb_corr_p99"), "thumb_corr_max": meta.get("thumb_corr_max"),
        "seconds": round(secs, 1),
    }
    pct = lambda k: f"{s[k]} ({s[k] / max(n, 1):.0%})"  # noqa: E731
    L = [f"# Provenance check — {a.title or ''}", "",
         f"ours: {', '.join(a.ours)} → {len(A)} файлов; theirs: {', '.join(a.theirs or [])} → {n} файлов; {s['seconds']} с.", "",
         "| проверка | файлов theirs с совпадением |", "|---|---|",
         f"| сцена (MGRS-тайл + дата) | {pct('theirs_with_scene_match')} |",
         f"| только тайл | {pct('theirs_with_tile_match')} |",
         f"| только дата | {pct('theirs_with_date_match')} |",
         f"| пересечение bbox WGS84 (> {a.min_overlap:.0%} меньшего) | {pct('theirs_with_bbox_overlap')} |",
         f"| идентичный файл (SHA-1) | {pct('theirs_identical_files')} |",
         f"| содержимое: корреляция превью ≥ {a.corr_thr} (с поворотами/отражениями) | {pct('theirs_content_match')} |",
         f"| перцептивный хеш: Хэмминг ≤ {a.phash_max} | {pct('theirs_phash_match')} |", "",
         f"Сцен (тайл+дата): ours {s['scenes_ours']}, theirs {s['scenes_theirs']}, общих **{s['scenes_shared']}**. "
         f"Общие тайлы: {', '.join(tiles_shared) or '—'}.",
         f"theirs без геопривязки: {s['theirs_without_georef']}; без тайла/даты в имени/тегах: {s['theirs_without_tile_date']}.",
         f"Корреляция превью ours×theirs: p99 {s['thumb_corr_p99']}, max {s['thumb_corr_max']}.", ""]
    if shared:
        L += ["## Общие сцены (тайл_дата: файлов ours / theirs)", ""]
        L += [f"- {t}_{d}: {scenes_a[(t, d)]} / {scenes_b[(t, d)]}" for t, d in shared[:200]]
        L.append("")
    if a.within:
        L += ["## Внутри theirs (дубликаты/перекрытия)", "",
              f"пар: {s['within_pairs']}; идентичных файлов {s['within_identical']}; пересечений bbox {s['within_bbox']}; "
              f"совпадений содержимого {s['within_content']}.", ""]
    L += ["## Что делать", "",
          "- Сцены/тайлы, общие с нашими обучающими данными: держать в ОДНОМ сплите; для отчётной метрики на данных "
          "организатора — модель, обученная без этих сцен (или честно оговорить).",
          "- Совпадения содержимого/хеша внутри theirs → дубликаты: при своём train/val сплите группировать по ним.",
          "- Детали пар: pairs.csv; флаги по каждому файлу организатора: theirs_files.csv.", ""]
    s["md"] = "\n".join(L)
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ours", nargs="+", required=True, help="folders / globs / .txt lists of our training data")
    ap.add_argument("--theirs", nargs="+", help="organizer folders / globs / .txt lists")
    ap.add_argument("--list-root", help="where to resolve names from .txt lists (default: <list>/../..)")
    ap.add_argument("--include", help="regex: keep only matching paths")
    ap.add_argument("--exclude", help=f"regex: skip matching paths (default {DEFAULT_EXCLUDE!r})")
    ap.add_argument("--no-exclude", action="store_true", help="do not skip mask-like files")
    ap.add_argument("--within", action="store_true", help="also check theirs x theirs (duplicates / overlapping chips)")
    ap.add_argument("--min-overlap", type=float, default=0.05, help="bbox intersection / smaller area to flag")
    ap.add_argument("--corr-thr", type=float, default=0.97)
    ap.add_argument("--phash-max", type=int, default=4)
    ap.add_argument("--no-sha1", action="store_true")
    ap.add_argument("--max-files", type=int, default=0)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--title")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if not sys.stdout.isatty():
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    s = run(a)
    print(s["md"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
