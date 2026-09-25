#!/usr/bin/env python
"""org_to_map.py (L49) -- organiser chips + our predictions -> a map data root for `python -m service`.

    $env:CUDA_VISIBLE_DEVICES="-1"; $env:PYTHONPATH="src"
    .venv\\Scripts\\python.exe scripts\\tools\\org_to_map.py --chips <their GeoTIFF chips> `
        --pred lgbm=out\\pred_lgbm [--pred fdi_rule=out\\pred_fdi] [--config data\\ingest\\org\\adapter.yaml] `
        [--labels <their masks> --label-debris 3] --out out\\org_map
    .venv\\Scripts\\python.exe -m service --port 8090 --data-root out\\org_map

Pipeline:
 1. chips = every *.tif/*.tiff under --chips (files ending _cl/_conf/_prob/_mask skipped). Every chip MUST have
    a CRS and a geotransform; otherwise the script stops with exit 2 (no "gallery" mode -- a map needs coordinates).
 2. Each chip is read through the ingest adapter (organizer_adapter.load_image: bands.source order,
    radiometry scale/offset, nodata, resolution from --config adapter.yaml). Without --config: band order from
    band descriptions, else by band count (11 = MARIDA, 12 = S2 L2A without B10, 13 = all), scale 1e-4 if the
    data look like DN (median > 2), else 1.
 3. Grouping into scenes (--group): name = CRS + date + name prefix (chip index `_<n>` stripped; default),
    date = CRS + date, adjacency = CRS only. Every group is then split into spatially connected clusters
    (chip boxes closer than --max-gap-m) and clusters larger than --max-scene-px are cut into tiles, so a scene
    mosaic never gets huge when chips are spread over a whole S2 tile.
    Date: from the file name (YYYY-MM-DD, YYYYMMDD, D-M-YY as in MARIDA; --date-order dmy|mdy) or TIFF tags
    (TIFFTAG_DATETIME, DATE*, ACQUISITION*); if none -> --unknown-date (default 1900-01-01, flagged
    date_unknown in scene.json and in manifest regions[].dates[].date_unknown / regions[].date_unknown; the v2 UI
    then shows «дата неизвестна» -- the service needs YYYY-MM-DD).
    Region names are unique: equal group names get the tile, else the UTM zone, else `_g<k>` as a suffix.
 4. Per scene, a live-format folder <work>/<region>/<date>/ (reports/tasklog/06_live.md):
    bands.tif (adapter bands, float32 reflectance, descriptions = band names, NaN outside chips),
    water_mask.tif (NDWI = (B3-B8)/(B3+B8) > --ndwi, holes <= 100 px filled -- the organiser data have no SCL),
    scl.tif (PSEUDO-SCL, not from L2A: 0 outside chips, 6 water, 7 other, 8 bright-cloud rule B2>=0.06 &
    B11>=0.03, blobs >= 100 px), rgb.png (B4/B3/B2, percentile stretch, alpha 0 outside chips, scene UTM grid),
    rgb.json, prob_<model>.tif (uint8 P*255 on the mosaic grid, max over overlapping chips),
    prob_<model>.json (threshold: --threshold > weights/<model>/meta.json > 0.5), scene.json,
    [labels.tif + per-model pixel P/R/F1 in scene.json and the report if --labels].
 5. scripts/build_service_data.py (as a library) -> <out>/manifest.json (kind "organizer"), per-date rgb, prob,
    detections, H3, zones, timeseries; then scripts/validate_service_data.py-compatible.
    Summary: <work>/org_to_map_report.json.

Predictions (--pred NAME=DIR, repeatable; plain DIR = lgbm): matched to chips by file stem:
<stem>_prob.tif (inference.py), <stem>.png / <stem>.tif (predict_org.py --prob -> <out>_prob/), uint8 0..255 or
float 0..1. Pure 0/1 rasters are treated as binary masks (warning); all-zero rasters are «no signal» chips
(counted separately, normal for clean water). Ungeoreferenced predictions (PNG) take the
chip grid (shapes must match).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import time
import warnings
from collections import defaultdict
from datetime import date as _date
from pathlib import Path

if not os.environ.get("CUDA_VISIBLE_DEVICES"):
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
warnings.filterwarnings("ignore", message=".*(NotGeoreferenced|geotransform).*")

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from PIL import Image  # noqa: E402
from rasterio.crs import CRS  # noqa: E402
from rasterio.transform import Affine, array_bounds  # noqa: E402
from rasterio.warp import Resampling, reproject  # noqa: E402
from scipy import ndimage  # noqa: E402

SKIP_RE = re.compile(r"_(cl|conf|prob|mask)$", re.I)
ORDERS = {11: ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"],
          12: ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"],
          13: ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B10", "B11", "B12"]}
WATER_RULE = ("NDWI = (B3 - B8) / (B3 + B8) > {ndwi} on valid pixels, holes <= {hole} px inside water filled "
              "(floating objects/boats); no SCL in organiser data -- assumption")
SCL_RULE = ("pseudo-SCL (not L2A): 0 = outside chips / nodata, 6 = water_mask, 7 = other valid, "
            "8 = bright cloud rule B2 >= 0.06 & B11 >= 0.03 (blobs >= 100 px)")
MARKER = ".org_to_map"
ZERO_SIGNAL = "zero"  # read_prob: the raster is 0 everywhere (no signal), counted separately from warnings


class OrgMapError(Exception):
    """Clear user-facing error (exit 2)."""


def log(msg: str) -> None:
    print(f"[org_to_map] {msg}", flush=True)


# ---------------------------------------------------------------- names / dates
DATE_PATTERNS = [
    (re.compile(r"(?<!\d)((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})(?!\d)"), "ymd"),
    (re.compile(r"(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})(?:T\d{6})?(?!\d)"), "ymd"),
    (re.compile(r"(?<!\d)(\d{1,2})-(\d{1,2})-(\d{2})(?!\d)"), "short"),
]
TILE_RE = re.compile(r"(?<![0-9A-Za-z])T?(\d{2}[C-HJ-NP-X][A-HJ-NP-Z]{2})(?![0-9A-Za-z])")


def _mkdate(y: int, m: int, d: int) -> str | None:
    try:
        return _date(y, m, d).isoformat()
    except ValueError:
        return None


def date_from_name(stem: str, order: str = "dmy") -> str | None:
    for rx, kind in DATE_PATTERNS:
        for m in rx.finditer(stem):
            a, b, c = (int(g) for g in m.groups())
            if kind == "ymd":
                d = _mkdate(a, b, c)
            else:
                day, mon = (a, b) if order == "dmy" else (b, a)
                d = _mkdate(2000 + c, mon, day)
            if d and "2013-01-01" <= d <= "2035-12-31":
                return d
    return None


def date_from_tags(path: Path, order: str = "dmy") -> str | None:
    try:
        with rasterio.open(path) as ds:
            tags = dict(ds.tags())
    except Exception:  # noqa: BLE001
        return None
    for k, v in tags.items():
        if k.upper().startswith(("TIFFTAG_DATETIME", "DATE", "ACQUISITION", "SENSING")):
            s = str(v).replace(":", "-", 2) if re.match(r"^\d{4}:\d{2}:\d{2}", str(v)) else str(v)
            d = date_from_name(s, order)
            if d:
                return d
    return None


def tile_from_name(stem: str) -> str:
    m = TILE_RE.search(stem)
    return m.group(1) if m else ""


def name_prefix(stem: str) -> str:
    """Scene prefix of a chip name: trailing chip-index tokens (`_12`, `-3`, `_r2_c5`) are stripped, a date
    token is never stripped."""
    s = stem
    for _ in range(2):
        m = re.search(r"[_\-]([rcxy]?\d{1,5})$", s, re.I)
        if not m or date_from_name(m.group(1)) or len(s) == len(m.group(0)):
            break
        s = s[: m.start()]
    return s


def safe_id(name: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
    return s or "scene"


# ---------------------------------------------------------------- chips
def list_chips(chips_dir: Path) -> list[Path]:
    out = []
    for p in sorted(chips_dir.rglob("*")):
        if p.is_file() and p.suffix.lower() in (".tif", ".tiff") and not SKIP_RE.search(p.stem):
            out.append(p)
    return out


def chip_meta(p: Path) -> dict:
    with rasterio.open(p) as ds:
        tf, crs = ds.transform, ds.crs
        geo = crs is not None and tf is not None and tf != Affine.identity()
        return {"path": p, "stem": p.stem, "crs": crs.to_string() if crs else None, "transform": tf,
                "shape": (ds.height, ds.width), "count": ds.count, "descriptions": list(ds.descriptions),
                "georef": bool(geo),
                "bounds": tuple(ds.bounds) if geo else None}


def default_config(metas: list[dict], chips_dir: Path) -> tuple[dict, list[str]]:
    """Adapter config when no adapter.yaml is given. Returns (cfg, notes)."""
    from macroplastic.organizer_adapter.adapter import CANON, canon_band

    m0 = metas[0]
    notes = []
    descs = [canon_band(d) if d else "" for d in m0["descriptions"]]
    if all(d in CANON for d in descs):
        source, why = "descriptions", "порядок каналов: из описаний каналов GeoTIFF"
    elif m0["count"] in ORDERS:
        source, why = ORDERS[m0["count"]], f"порядок каналов: по числу каналов ({m0['count']}) -> {ORDERS[m0['count']]}"
    else:
        raise OrgMapError(f"{m0['path'].name}: {m0['count']} каналов без описаний -- порядок не угадать; "
                          "передайте --config adapter.yaml (python -m macroplastic.ingest)")
    notes.append(why + " (допущение, нет --config)")
    with rasterio.open(m0["path"]) as ds:
        a = ds.read(masked=False).astype(np.float32)
    med = float(np.nanmedian(a[np.isfinite(a) & (a != 0)])) if np.isfinite(a).any() else 0.0
    scale = 1e-4 if med > 2 else 1.0
    notes.append(f"радиометрия: медиана {med:.4g} -> scale {scale} (допущение, нет --config)")
    cfg = {"name": "org_to_map", "root": str(chips_dir), "layout": {"image_glob": "**/*.tif"},
           "bands": {"source": source, "rename": {}, "output": None, "missing": "nan"},
           "radiometry": {"scale": scale, "offset": 0.0, "clip": None},
           "nodata": {"values": [], "use_file_nodata": True}, "resolution": {"target": None}}
    return cfg, notes


def load_chip(cfg: dict, meta: dict):
    from macroplastic.organizer_adapter.adapter import Sample, load_image

    img, names, grid = load_image(cfg, Sample(id=meta["stem"], image_paths=[str(meta["path"])], mask_path=None))
    return img.astype(np.float32), list(names), grid


# ---------------------------------------------------------------- grouping
def cluster_boxes(boxes: np.ndarray, gap: float) -> list[list[int]]:
    """Single-linkage clusters of boxes (N,4: left,bottom,right,top) closer than `gap` (map units)."""
    n = len(boxes)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        b = boxes[i]
        near = ((boxes[:, 0] <= b[2] + gap) & (boxes[:, 2] >= b[0] - gap)
                & (boxes[:, 1] <= b[3] + gap) & (boxes[:, 3] >= b[1] - gap))
        for j in np.flatnonzero(near):
            if j > i:
                ri, rj = find(i), find(int(j))
                if ri != rj:
                    parent[rj] = ri
    out: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        out[find(i)].append(i)
    return list(out.values())


def split_large(idx: list[int], boxes: np.ndarray, cap_m: float) -> list[list[int]]:
    b = boxes[idx]
    if (b[:, 2].max() - b[:, 0].min()) <= cap_m and (b[:, 3].max() - b[:, 1].min()) <= cap_m:
        return [idx]
    x0, y1 = b[:, 0].min(), b[:, 3].max()
    cx, cy = (b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2
    key = np.floor((cx - x0) / cap_m).astype(int) * 100000 + np.floor((y1 - cy) / cap_m).astype(int)
    out: dict[int, list[int]] = defaultdict(list)
    for k, i in zip(key, idx):
        out[int(k)].append(i)
    return [out[k] for k in sorted(out)]


def group_chips(metas: list[dict], mode: str, gap_m: float, max_px: int, order: str, unknown_date: str) -> list[dict]:
    for m in metas:
        d, src = date_from_name(m["stem"], order), "name"
        if not d:
            d, src = date_from_tags(m["path"], order), "metadata"
        m["date"], m["date_source"] = (d, src) if d else (unknown_date, "unknown")
        m["tile"] = tile_from_name(m["stem"])
        m["prefix"] = name_prefix(m["stem"])
    keys: dict[tuple, list[int]] = defaultdict(list)
    for i, m in enumerate(metas):
        if mode == "name":
            k = (m["crs"], m["date"], m["prefix"])
        elif mode == "date":
            k = (m["crs"], m["date"])
        else:
            k = (m["crs"],)
        keys[k].append(i)
    boxes = np.array([m["bounds"] for m in metas], float)
    scenes = []
    for k, idx in sorted(keys.items(), key=lambda kv: str(kv[0])):
        res = min(abs(metas[i]["transform"].a) for i in idx)
        parts = []
        for cl in cluster_boxes(boxes[idx], gap_m):
            parts += split_large([idx[j] for j in cl], boxes, max_px * res)
        parts.sort(key=lambda p: (-boxes[p, 3].max(), boxes[p, 0].min()))
        for pi, p in enumerate(parts):
            ms = [metas[i] for i in p]
            dates = sorted({m["date"] for m in ms})
            date = max(dates, key=lambda d: sum(m["date"] == d for m in ms))
            if len(dates) > 1:
                log(f"ВНИМАНИЕ: в одной мозаике чипы {len(dates)} дат {dates} -> дата сцены {date} (большинство); "
                    "перекрытия заполняет первый чип. Для разных дат -- --group name|date")
            tiles = sorted({m["tile"] for m in ms if m["tile"]})
            if mode == "name":
                base = k[2]
            else:
                base = f"{tiles[0] if tiles else 'scene'}_{date}"
            name = base + (f"_p{pi + 1}" if len(parts) > 1 else "")
            scenes.append({"name": name, "crs": k[0], "date": date, "date_source": ms[0]["date_source"],
                           "tile": tiles[0] if tiles else "", "chips": ms})
    return unique_names(scenes)


def crs_label(crs: str | None) -> str:
    """EPSG:32616 -> utm16n, EPSG:32733 -> utm33s, else the EPSG code."""
    m = re.match(r"^EPSG:(32[67])(\d{2})$", crs or "")
    if m:
        return f"utm{int(m.group(2))}{'n' if m.group(1) == '326' else 's'}"
    m = re.match(r"^EPSG:(\d+)$", crs or "")
    return f"epsg{m.group(1)}" if m else "crs"


def unique_names(scenes: list[dict]) -> list[dict]:
    """Region ids AND display names unique: groups with the same name (anonymous chips `tile_*` of different CRS /
    dates -> both `tile_p1`) get a suffix -- the tile, else the UTM zone, else the group number `_g<k>`. The
    region id is the safe_id of the (now unique) name, so the map list never shows two identical names."""
    from collections import Counter

    for key in ("tile", "crs", None):
        cnt = Counter(s["name"] for s in scenes)
        dup = {n for n, c in cnt.items() if c > 1}
        if not dup:
            break
        for s in scenes:
            if s["name"] not in dup:
                continue
            if key == "tile" and s.get("tile") and s["tile"].lower() not in s["name"].lower():
                s["name"] = f"{s['name']}_{s['tile']}"
            elif key == "crs" and s.get("crs"):
                s["name"] = f"{s['name']}_{crs_label(s['crs'])}"
        if key is None:
            k: dict[str, int] = defaultdict(int)
            for s in scenes:
                if s["name"] in dup:
                    k[s["name"]] += 1
                    s["name"] = f"{s['name']}_g{k[s['name']]}"
    seen: dict[str, int] = {}
    for s in scenes:  # safety net: different names may still collide after safe_id
        rid = safe_id(s["name"])
        if rid in seen:
            seen[rid] += 1
            rid = f"{rid}_{seen[rid]}"
            s["name"] = rid
        else:
            seen[rid] = 0
        s["region"] = rid
    return scenes


# ---------------------------------------------------------------- mosaic
class Mosaic:
    def __init__(self, grids: list[dict]):
        res = min(abs(g["transform"].a) for g in grids)
        g0 = grids[0]["transform"]
        lefts = [g["transform"].c for g in grids]
        tops = [g["transform"].f for g in grids]
        rights = [g["transform"].c + g["width"] * g["transform"].a for g in grids]
        bottoms = [g["transform"].f + g["height"] * g["transform"].e for g in grids]
        # snap to the first chip's pixel lattice so aligned chips paste without resampling
        x0 = g0.c - math.ceil((g0.c - min(lefts)) / res - 1e-6) * res
        y1 = g0.f + math.ceil((max(tops) - g0.f) / res - 1e-6) * res
        self.res = res
        self.W = int(math.ceil((max(rights) - x0) / res - 1e-6))
        self.H = int(math.ceil((y1 - min(bottoms)) / res - 1e-6))
        self.transform = Affine(res, 0, x0, 0, -res, y1)

    def window(self, g: dict):
        t = g["transform"]
        if abs(t.a - self.res) > 1e-6 or abs(t.e + self.res) > 1e-6 or t.b or t.d:
            return None
        c, r = (t.c - self.transform.c) / self.res, (self.transform.f - t.f) / self.res
        if abs(c - round(c)) > 1e-3 or abs(r - round(r)) > 1e-3:
            return None
        return int(round(r)), int(round(c))

    def place(self, dst: np.ndarray, arr: np.ndarray, g: dict, crs, how: str) -> None:
        """arr (C,h,w) or (h,w) -> dst (same leading dims, H, W). how: 'fill' (NaN-fill, first wins) | 'max'."""
        a3 = arr if arr.ndim == 3 else arr[None]
        d3 = dst if dst.ndim == 3 else dst[None]
        w = self.window(g)
        if w is not None:
            r, c = w
            h, wd = a3.shape[1:]
            r0, c0, r1, c1 = max(r, 0), max(c, 0), min(r + h, self.H), min(c + wd, self.W)
            src = a3[:, r0 - r:r1 - r, c0 - c:c1 - c]
            tgt = d3[:, r0:r1, c0:c1]
        else:  # different lattice / resolution: resample (nearest) into a full-size buffer
            src = np.full((a3.shape[0], self.H, self.W), np.nan, np.float32)
            for k in range(a3.shape[0]):
                reproject(a3[k].astype(np.float32), src[k], src_transform=g["transform"], src_crs=crs,
                          dst_transform=self.transform, dst_crs=crs, resampling=Resampling.nearest,
                          src_nodata=np.nan, dst_nodata=np.nan)
            tgt = d3
        if how == "max":
            np.fmax(tgt, src, out=tgt)
        else:
            m = np.isnan(tgt) & ~np.isnan(src)
            tgt[m] = src[m]


# ---------------------------------------------------------------- predictions / labels
def index_dir(d: Path, strip: tuple[str, ...]) -> dict[str, Path]:
    idx: dict[str, Path] = {}
    for p in sorted(d.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (".tif", ".tiff", ".png"):
            continue
        s = p.stem
        low = s.lower()
        pri = 1
        for suf in strip:
            if low.endswith(suf):
                s, pri = s[: -len(suf)], 0
                break
        if s not in idx or pri == 0:
            idx[s] = p
    return idx


def pred_scale_255(files: list[Path]) -> bool:
    """True if any integer prediction raster of the model has a value > 1 -> the folder holds P*255 and a chip
    with values only in {0, 1} is a LOW probability (<= 1/255), not a binary mask (L52: 2 of 104 rehearsal chips
    were turned into P = 1 by the per-chip rule)."""
    for p in files:
        try:
            with rasterio.open(p) as ds:
                if not ds.dtypes[0].startswith("float") and float(ds.read(1).max()) > 1:
                    return True
        except Exception:  # noqa: BLE001
            continue
    return False


def read_prob(p: Path, g: dict, crs, scale255: bool = False) -> tuple[np.ndarray, str | None]:
    """-> (float32 0..255 on the chip grid, warning or None). scale255: the folder is P*255 (pred_scale_255)."""
    warn = None
    with rasterio.open(p) as ds:
        a = ds.read(1).astype(np.float32)
        pgeo = ds.crs is not None and ds.transform != Affine.identity()
        ptf, pcrs, dt = ds.transform, ds.crs, ds.dtypes[0]
    if a.shape != (g["height"], g["width"]):
        if not pgeo:
            raise OrgMapError(f"{p.name}: размер {a.shape} != чип {(g['height'], g['width'])} и нет геопривязки")
        dst = np.full((g["height"], g["width"]), np.nan, np.float32)
        reproject(a, dst, src_transform=ptf, src_crs=pcrs, dst_transform=g["transform"], dst_crs=crs,
                  resampling=Resampling.nearest, src_nodata=np.nan, dst_nodata=np.nan)
        a = dst
    fin = a[np.isfinite(a)]
    mx = float(fin.max()) if fin.size else 0.0
    uniq = np.unique(fin[:10000]) if fin.size else np.array([])
    if mx <= 0.0:  # all zeros (or empty): no signal on this chip -- not a binary mask
        return np.zeros_like(a), ZERO_SIGNAL
    if dt.startswith("float") and mx <= 1.0 + 1e-6:
        a = a * 255.0
    elif scale255 and not dt.startswith("float"):
        pass  # P*255 folder: 0/1 values are P <= 1/255
    elif set(np.unique(fin).tolist()) <= {0.0, 1.0}:
        a, warn = a * 255.0, "бинарная маска 0/1, а не вероятность"
    elif set(uniq.tolist()) <= {0.0, 255.0} and len(uniq) == 2:
        warn = "бинарная маска 0/255, а не вероятность"
    return np.clip(a, 0, 255), warn


def model_threshold(name: str, cli: dict[str, float]) -> tuple[float, str]:
    if name in cli:
        return cli[name], "--threshold"
    mj = ROOT / "weights" / name / "meta.json"
    if mj.is_file():
        try:
            return float(json.loads(mj.read_text(encoding="utf-8"))["threshold"]), f"weights/{name}/meta.json"
        except Exception:  # noqa: BLE001
            pass
    return 0.5, "по умолчанию 0.5"


# ---------------------------------------------------------------- scene writing
def water_rule(bands: dict[str, np.ndarray], valid: np.ndarray, ndwi_thr: float, max_hole: int):
    b3, b8 = bands["B3"], bands["B8"]
    with np.errstate(invalid="ignore", divide="ignore"):
        ndwi = (b3 - b8) / (b3 + b8)
    w0 = valid & np.isfinite(ndwi) & (ndwi > ndwi_thr)
    holes = ndimage.binary_fill_holes(w0) & ~w0 & valid
    if holes.any():
        lab, n = ndimage.label(holes)
        small = np.bincount(lab.ravel(), minlength=n + 1) <= max_hole
        small[0] = False
        w0 |= small[lab]
    return w0


def bright_cloud(bands, valid):
    b2, b11 = np.nan_to_num(bands["B2"]), np.nan_to_num(bands.get("B11", bands["B2"]))
    c = valid & (b2 >= 0.06) & (b11 >= 0.03)
    if not c.any():
        return c
    c = ndimage.binary_opening(c, iterations=1)
    lab, n = ndimage.label(c)
    keep = np.bincount(lab.ravel(), minlength=n + 1) >= 100
    keep[0] = False
    return keep[lab]


def rgb_png(bands, valid) -> np.ndarray:
    rgb = np.stack([bands["B4"], bands["B3"], bands["B2"]], -1)
    v = rgb[valid]
    v = v[np.isfinite(v).all(1)]
    lo, hi = (np.percentile(v, 1), np.percentile(v, 99.5)) if v.size else (0.0, 0.3)
    x = np.clip((np.nan_to_num(rgb) - lo) / max(hi - lo, 1e-6), 0, 1) ** (1 / 1.4)
    out = np.zeros(rgb.shape[:2] + (4,), np.uint8)
    out[..., :3] = np.round(x * 255).astype(np.uint8)
    out[..., 3] = np.where(valid, 255, 0)
    return out


def write_tif(p: Path, arr: np.ndarray, tf, crs, dtype, descs=None, nodata=None):
    a3 = arr if arr.ndim == 3 else arr[None]
    with rasterio.open(p, "w", driver="GTiff", height=a3.shape[1], width=a3.shape[2], count=a3.shape[0],
                       dtype=dtype, crs=crs, transform=tf, compress="deflate", tiled=True, blockxsize=256,
                       blockysize=256, nodata=nodata) as ds:
        ds.write(a3.astype(dtype))
        if descs:
            ds.descriptions = tuple(descs)


def label_values(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x.strip()]


def build_scene(sc: dict, cfg: dict, preds: dict[str, dict[str, Path]], thr: dict[str, tuple], labels_idx,
                a, work: Path) -> dict:
    crs = CRS.from_string(sc["crs"])
    loaded = []
    for m in sc["chips"]:
        img, names, grid = load_chip(cfg, m)
        grid = dict(grid, transform=grid["transform"] or m["transform"], height=img.shape[1], width=img.shape[2])
        loaded.append((m, img, names, grid))
    names = loaded[0][2]
    for m, _, nm, _ in loaded:
        if nm != names:
            raise OrgMapError(f"{m['path'].name}: каналы {nm} != {names} у других чипов сцены")
    need = [b for b in ("B2", "B3", "B4", "B8") if b not in names]
    if need:
        raise OrgMapError(f"нет каналов {need} (есть {names}) -- без них не посчитать water_mask/rgb; "
                          "проверьте bands.source в adapter.yaml")
    mos = Mosaic([g for *_, g in loaded])
    H, W = mos.H, mos.W
    stack = np.full((len(names), H, W), np.nan, np.float32)
    for m, img, _, g in loaded:
        mos.place(stack, img, g, crs, "fill")
    bands = {n: stack[i] for i, n in enumerate(names)}
    valid = np.isfinite(stack[[names.index(b) for b in ("B2", "B3", "B4", "B8")]]).all(0)
    water = water_rule(bands, valid, a.ndwi, a.max_hole_px)
    cloud = bright_cloud(bands, valid)
    scl = np.zeros((H, W), np.uint8)
    scl[valid] = 7
    scl[water] = 6
    scl[cloud] = 8
    sdir = work / sc["region"] / sc["date"]
    sdir.mkdir(parents=True, exist_ok=True)
    write_tif(sdir / "bands.tif", stack, mos.transform, crs, "float32", names)
    write_tif(sdir / "water_mask.tif", water.astype(np.uint8), mos.transform, crs, "uint8")
    write_tif(sdir / "scl.tif", scl, mos.transform, crs, "uint8")
    Image.fromarray(rgb_png(bands, valid), "RGBA").save(sdir / "rgb.png", optimize=True)
    bounds_utm = [float(v) for v in array_bounds(H, W, mos.transform)]  # west, south, east, north
    json.dump({"crs": sc["crs"], "bounds": bounds_utm, "width": W, "height": H,
               "note": "rgb.png on the scene UTM grid (warped by build_service_data)"},
              open(sdir / "rgb.json", "w", encoding="utf-8"), indent=1)
    lab_mos = None
    if labels_idx is not None:
        lab_mos = np.full((H, W), np.nan, np.float32)
        n_lab = 0
        for m, img, _, g in loaded:
            lp = labels_idx.get(m["stem"])
            if lp is None:
                continue
            with rasterio.open(lp) as ds:
                la = ds.read(1).astype(np.float32)
            if la.shape == (g["height"], g["width"]):
                mos.place(lab_mos, la, g, crs, "fill")
                n_lab += 1
        write_tif(sdir / "labels.tif", np.nan_to_num(lab_mos, nan=255).astype(np.uint8), mos.transform, crs,
                  "uint8", nodata=255)
    wv = water & ~cloud
    info = {"scene_id": sc["name"], "date": sc["date"], "date_source": sc["date_source"],
            "date_unknown": sc["date_source"] == "unknown",
            "region_name": f"{sc['name']} — данные организаторов" + (" (дата неизвестна)"
                                                                       if sc["date_source"] == "unknown" else ""),
            "region_name_en": sc["name"], "tile": sc["tile"], "source": "organizer dataset",
            "chips_dir": str(a.chips), "n_chips": len(loaded), "chips": [m["stem"] for m, *_ in loaded],
            "crs": sc["crs"], "transform": list(mos.transform)[:6], "bounds_utm": bounds_utm,
            "shape": [H, W], "valid_frac": round(float(valid.mean()), 4),
            "water_frac": round(float(water.sum() / max(valid.sum(), 1)), 4),
            "crop_cloud_frac": round(float(cloud.sum() / max(valid.sum(), 1)), 4),
            "water_median_b8": round(float(np.nanmedian(bands["B8"][wv])), 5) if wv.any() else None,
            "water_median_b11": (round(float(np.nanmedian(bands["B11"][wv])), 5)
                                 if wv.any() and "B11" in bands else None),
            "haze_check": "off: water_b8_median/glint_or_haze are not written -- the live haze threshold (L2A) "
                          "is not calibrated for organiser radiometry",
            "water_mask_rule": WATER_RULE.format(ndwi=a.ndwi, hole=a.max_hole_px), "scl_rule": SCL_RULE,
            "bands": names, "models": {}}
    for mname, idx in preds.items():
        prob = np.full((H, W), np.nan, np.float32)
        n_ok, missing, warns, zero = 0, [], set(), []
        for m, img, _, g in loaded:
            pp = idx.get(m["stem"])
            if pp is None:
                missing.append(m["stem"])
                continue
            pa, w = read_prob(pp, g, crs, a.pred_scale255.get(mname, False))
            if w == ZERO_SIGNAL:
                zero.append(m["stem"])
            elif w:
                warns.add(w)
            mos.place(prob, pa, g, crs, "max")
            n_ok += 1
        if n_ok == 0:
            log(f"  {sc['region']}: нет предсказаний {mname} ни для одного чипа -- модель пропущена")
            continue
        p8 = np.round(np.nan_to_num(prob, nan=0.0)).astype(np.uint8)
        t, tsrc = thr[mname]
        write_tif(sdir / f"prob_{mname}.tif", p8, mos.transform, crs, "uint8")
        pj = {"model": mname, "threshold": t, "threshold_source": tsrc, "source_dir": str(a.pred_dirs[mname]),
              "n_chips": n_ok, "missing_chips": missing, "warnings": sorted(warns), "zero_signal_chips": zero,
              "n_above_threshold_water": int(((p8 >= round(t * 255)) & water).sum())}
        if lab_mos is not None:
            deb = np.isin(lab_mos, label_values(a.label_debris))
            lab_ok = np.isfinite(lab_mos) & ~np.isin(lab_mos, label_values(a.label_ignore)) & valid
            pos = (p8 >= math.ceil(t * 255 - 1e-9)) & valid
            tp = int((pos & deb & lab_ok).sum())
            fp = int((pos & ~deb & lab_ok).sum())
            fn = int((~pos & deb & lab_ok).sum())
            pj["labels"] = {"debris_px": int((deb & lab_ok).sum()), "tp": tp, "fp": fp, "fn": fn,
                            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
                            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
                            "f1": round(2 * tp / (2 * tp + fp + fn), 4) if tp + fp + fn else None,
                            "note": "pixel-level on labelled pixels (ignore values excluded), before map "
                                    "post-processing"}
        json.dump(pj, open(sdir / f"prob_{mname}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        info["models"][mname] = {k: pj[k] for k in ("threshold", "n_chips", "n_above_threshold_water")}
        if "labels" in pj:
            info["models"][mname]["labels"] = pj["labels"]
        if missing:
            log(f"  {sc['region']}/{mname}: нет файла предсказания для {len(missing)} чипов, например {missing[:3]}")
        if zero:
            log(f"  {sc['region']}/{mname}: {len(zero)} из {n_ok} чипов без сигнала (вероятность 0 во всех пикселях) "
                "-- это нормально для чистой воды")
        if warns:
            log(f"  {sc['region']}/{mname}: {sorted(warns)}")
    json.dump(info, open(sdir / "scene.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"  {sc['region']}/{sc['date']}: {len(loaded)} чипов -> {W}x{H} px, вода {info['water_frac']:.2f}, "
        f"модели {list(info['models'])}")
    return info


def _safe_rmtree(d: Path, allow_manifest_kind: str | None = None) -> None:
    if not d.exists():
        return
    ok = (d / MARKER).exists() or not any(d.iterdir())
    mp = d / "manifest.json"
    if not ok and allow_manifest_kind and mp.exists():
        try:
            ok = json.loads(mp.read_text(encoding="utf-8")).get("kind") == allow_manifest_kind
        except Exception:  # noqa: BLE001
            ok = False
    if not ok:
        raise OrgMapError(f"{d} уже существует и создан не org_to_map -- укажите пустую папку")
    shutil.rmtree(d)


def parse_kv(items: list[str], default_key: str = "lgbm") -> dict[str, str]:
    out = {}
    for it in items or []:
        if "=" in it:
            k, v = it.split("=", 1)
        else:
            k, v = default_key, it
        out[k.strip()] = v.strip()
    return out


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chips", required=True, help="folder with the organiser GeoTIFF chips (recursive)")
    ap.add_argument("--pred", action="append", default=[], help="NAME=DIR with prob rasters (repeatable); "
                                                                "plain DIR = lgbm")
    ap.add_argument("--threshold", action="append", default=[], help="NAME=T (default: weights/NAME/meta.json, 0.5)")
    ap.add_argument("--config", default=None, help="adapter.yaml from `python -m macroplastic.ingest` "
                                                   "(bands/scale/offset); default: guess (see doc)")
    ap.add_argument("--labels", default=None, help="optional folder with their masks (matched by stem, "
                                                   "suffix _cl/_mask/_label stripped)")
    ap.add_argument("--label-debris", default="1", help="label value(s) of debris, comma-separated")
    ap.add_argument("--label-ignore", default="0", help="label value(s) excluded from the pixel metrics")
    ap.add_argument("--out", default=str(ROOT / "out" / "org_map"), help="map data root (manifest.json)")
    ap.add_argument("--work", default=None, help="intermediate live-format folder (default <out>_live)")
    ap.add_argument("--group", choices=["name", "date", "adjacency"], default="name",
                    help="scene rule: name = CRS+date+name prefix (default), date = CRS+date, adjacency = CRS")
    ap.add_argument("--max-gap-m", type=float, default=20000.0, help="chips closer than this (m) are one mosaic "
                    "(scattered chips of one S2 tile; the mosaic side is still capped by --max-scene-px)")
    ap.add_argument("--max-scene-px", type=int, default=4096, help="max mosaic side (px), larger clusters are cut")
    ap.add_argument("--date-order", choices=["dmy", "mdy"], default="dmy", help="for D-M-YY names (MARIDA: dmy)")
    ap.add_argument("--unknown-date", default="1900-01-01", help="date used when none is found (flagged)")
    ap.add_argument("--ndwi", type=float, default=0.0, help="water: NDWI > this")
    ap.add_argument("--max-hole-px", type=int, default=100, help="non-water holes inside water <= this are water")
    ap.add_argument("--demo-region", default="", help="manifest demo region (default: most detections)")
    ap.add_argument("--no-build", action="store_true", help="only write the live-format folders")
    a = ap.parse_args(argv)
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    t0 = time.time()
    a.chips = Path(a.chips)
    out = Path(a.out)
    work = Path(a.work) if a.work else Path(str(out) + "_live")
    if not a.chips.is_dir():
        raise OrgMapError(f"нет папки чипов {a.chips}")
    chips = list_chips(a.chips)
    if not chips:
        raise OrgMapError(f"в {a.chips} нет *.tif (файлы *_cl/_conf/_prob/_mask пропускаются)")
    metas = [chip_meta(p) for p in chips]
    nogeo = [m["path"].name for m in metas if not m["georef"]]
    if nogeo:
        raise OrgMapError(
            f"{len(nogeo)} из {len(metas)} чипов без геопривязки (нет CRS или геотрансформа), например "
            f"{nogeo[:3]}. Карта строится только по GeoTIFF с координатами; режима «галерея» нет. Варианты: "
            "попросить у организаторов GeoTIFF/координаты чипов или показывать их находки без карты "
            "(predict_org.py + score.py).")
    if a.config:
        from macroplastic.organizer_adapter import load_config

        cfg, notes = load_config(a.config), [f"каналы/радиометрия: {a.config}"]
    else:
        cfg, notes = default_config(metas, a.chips)
    for n in notes:
        log(n)
    pred_dirs = {k: Path(v) for k, v in parse_kv(a.pred).items()}
    for k, d in pred_dirs.items():
        if not d.is_dir():
            raise OrgMapError(f"--pred {k}={d}: нет такой папки")
        if not re.match(r"^[a-z0-9_]+$", k):
            raise OrgMapError(f"имя модели '{k}' -- только a-z0-9_")
    if not pred_dirs:
        raise OrgMapError("нужен хотя бы один --pred NAME=DIR (predict_org.py --prob или inference.py)")
    a.pred_dirs = pred_dirs
    preds = {k: index_dir(d, ("_prob",)) for k, d in pred_dirs.items()}
    for k, idx in preds.items():
        hit = sum(m["stem"] in idx for m in metas)
        log(f"предсказания {k}: {hit}/{len(metas)} чипов найдено в {pred_dirs[k]}")
        if hit == 0:
            raise OrgMapError(f"--pred {k}: ни один файл не совпал по имени с чипами (ожидается <stem>_prob.tif, "
                              f"<stem>.png или <stem>.tif; пример чипа: {metas[0]['stem']})")
    a.pred_scale255 = {k: pred_scale_255([idx[m["stem"]] for m in metas if m["stem"] in idx]) for k, idx in preds.items()}
    thr = {k: model_threshold(k, {kk: float(v) for kk, v in parse_kv(a.threshold).items()}) for k in preds}
    labels_idx = None
    if a.labels:
        labels_idx = index_dir(Path(a.labels), ("_cl", "_mask", "_label", "_labels", "_gt"))
        log(f"разметка: {sum(m['stem'] in labels_idx for m in metas)}/{len(metas)} чипов")
    scenes = group_chips(metas, a.group, a.max_gap_m, a.max_scene_px, a.date_order, a.unknown_date)
    log(f"{len(metas)} чипов -> {len(scenes)} сцен (--group {a.group}, разрыв {a.max_gap_m:g} м, "
        f"<= {a.max_scene_px} px)")
    _safe_rmtree(work)
    work.mkdir(parents=True)
    (work / MARKER).write_text("created by scripts/tools/org_to_map.py\n", encoding="utf-8")
    infos = [build_scene(sc, cfg, preds, thr, labels_idx, a, work) for sc in scenes]
    t_scenes = time.time() - t0
    rep = {"chips_dir": str(a.chips), "n_chips": len(metas), "n_scenes": len(scenes), "group": a.group,
           "config_notes": notes, "thresholds": {k: {"threshold": v[0], "source": v[1]} for k, v in thr.items()},
           "work": str(work), "out": str(out),
           "scenes": [{"region": sc["region"], "date": sc["date"], "date_source": sc["date_source"],
                       "n_chips": len(sc["chips"]), "shape": i["shape"], "water_frac": i["water_frac"],
                       "models": i["models"]} for sc, i in zip(scenes, infos)],
           "seconds_scenes": round(t_scenes, 1)}
    if not a.no_build:
        import build_service_data as bsd

        _safe_rmtree(out, allow_manifest_kind="organizer")
        t1 = time.time()
        rc = bsd.main(["--live-dir", str(work), "--out", str(out), "--kind", "organizer"])
        if rc:
            raise OrgMapError(f"build_service_data вернул {rc}")
        (out / MARKER).write_text("created by scripts/tools/org_to_map.py\n", encoding="utf-8")
        mp = out / "manifest.json"
        man = json.loads(mp.read_text(encoding="utf-8"))
        man["sources"] = [{"name": "Данные организаторов хакатона (чипы GeoTIFF)", "url": "",
                           "license": "по условиям организаторов"}] + [
            s for s in man["sources"] if "Earth Search" not in s["name"]]
        man["organizer"] = {"chips_dir": str(a.chips), "n_chips": len(metas), "group": a.group,
                            "water_mask_rule": WATER_RULE.format(ndwi=a.ndwi, hole=a.max_hole_px),
                            "scl_rule": SCL_RULE, "config": notes, "haze_check": "off"}
        regs = man.get("regions", [])
        unknown = {(sc["region"], sc["date"]) for sc in scenes if sc["date_source"] == "unknown"}
        for r in regs:  # L52: the UI shows «дата неизвестна» instead of the placeholder date (1900-01-01)
            for d in r["dates"]:
                if (r["id"], d["date"]) in unknown:
                    d["date_unknown"] = True
            if r["dates"] and all(d.get("date_unknown") for d in r["dates"]):
                r["date_unknown"] = True
        for k, (t, src) in thr.items():  # threshold given on the CLI = a model of the organiser run, not weights/<k>
            if src == "--threshold" and k in man.get("models", {}):
                mm = man["models"][k]
                if k == "lgbm":
                    mm["name"] = "LightGBM"
                mm["threshold"] = t
                mm["threshold_source"] = "--threshold (org_to_map)"
                mm["note"] = (f"предсказания из {pred_dirs[k]}; порог задан при сборке карты (--threshold {k}={t}), "
                              "обычно из meta.json модели, обученной на данных организаторов")
        # region summary = the FIRST --pred model (build_service_data picks mdd or the alphabetically first one)
        prim = next(iter(pred_dirs))
        for r in regs:
            ts = json.loads((out / r["id"] / "timeseries.json").read_text(encoding="utf-8"))
            row = next((x for x in ts if x["model"] == prim and x["date"] == r["summary"]["latest_date"]), None)
            if row:
                r["summary"].update(model=prim, index_permille=row["mean_index"], n_detections=row["n_detections"],
                                    total_debris_area_m2=row["total_debris_area_m2"])
            for d in r["dates"]:  # the UI opens dates[].models[0]
                d["models"] = sorted(d["models"], key=lambda k: k != prim)
        man["models"] = {k: man["models"][k] for k in sorted(man["models"], key=lambda k: k != prim)}
        if "mdd" not in man["models"]:
            man["sources"] = [x for x in man["sources"] if "marinedebrisdetector" not in x["name"]]
        demo =a.demo_region or (max(regs, key=lambda r: r["summary"].get("n_detections") or 0)["id"] if regs else "")
        if demo:
            man["demo"] = bsd.demo_entry(regs, demo, "", "данные организаторов: сцена с наибольшим числом находок"
                                         if not a.demo_region else "")
        mp.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
        rep["seconds_build"] = round(time.time() - t1, 1)
        rep["regions"] = [{"id": r["id"], "n_detections": r["summary"].get("n_detections"),
                           "index_permille": r["summary"].get("index_permille")} for r in regs]
    rep["seconds_total"] = round(time.time() - t0, 1)
    (work / "org_to_map_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"готово за {rep['seconds_total']} s: {len(scenes)} сцен -> {out if not a.no_build else work}")
    if not a.no_build:
        log(f"карта: .venv\\Scripts\\python.exe -m service --port 8090 --data-root {out}")
    return 0


def main(argv=None) -> int:
    try:
        return run(argv)
    except OrgMapError as e:
        print(f"[org_to_map] ОШИБКА: {e}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
