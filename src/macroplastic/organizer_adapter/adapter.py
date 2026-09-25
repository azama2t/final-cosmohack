"""Organizer dataset adapter: config (YAML) -> internal format.

Internal format (what the rest of the pipeline expects, same as MARIDA via macroplastic.data.marida):
    image: (C, H, W) float32 reflectance ~0..1, NaN = nodata, channels named with OUR names
           (canonical S2: B1..B8, B8A, B9, B11, B12; order = config `bands.output`)
    mask : (H, W) uint8 in OUR class scheme (MARIDA ids: 1 = Marine Debris, 7 = Marine Water, ...;
           0 = unlabelled / ignore)

Config keys (see configs/adapter_example.yaml for the fully commented version):
    name, root
    layout:  image_glob | band_files, mask_glob | mask_from_image, id_regex, exclude_regex, split_lists
    bands:   source (file order names or 'descriptions'), rename, output, hwc (for npy)
    radiometry: scale, offset (scalar or per-band dict, reflectance = DN*scale + offset), clip
    nodata:  values, nan_is_nodata, zero_all_bands_is_nodata
    resolution: target (m, null = keep), resampling (bilinear|nearest|cubic|average)
    classes: map {organizer_value: our_value}, default, ignore_values, mask_band, rgb_palette
    output:  write_mask_binary (extra *_bin mask: 1 debris / 0 other / 255 ignore)
"""
from __future__ import annotations

import csv
import glob
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import numpy as np

_REPO = Path(__file__).resolve().parents[3]

CANON = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B10", "B11", "B12"]


def canon_band(name: str) -> str:
    """'B02' / 'b2' / 'B8a' / 'SR_B4' / 'B04_10m' -> canonical 'B2' / 'B8A' / 'B4'. Others returned upper-cased."""
    s = str(name).strip().upper()
    m = re.search(r"B0?(\d{1,2})(A?)", s)
    if m and (f"B{int(m.group(1))}{m.group(2)}" in CANON):
        return f"B{int(m.group(1))}{m.group(2)}"
    return s


@dataclass
class Sample:
    id: str
    image_paths: list  # one multi-band file, or one per band (band_files)
    mask_path: str | None
    split: str | None = None
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- config
def load_config(path_or_dict) -> dict:
    if isinstance(path_or_dict, dict):
        cfg = dict(path_or_dict)
        base = Path.cwd()
    else:
        import yaml

        p = Path(path_or_dict)
        cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
        base = _REPO
    root = Path(os.path.expandvars(str(cfg.get("root", "."))))
    if not root.is_absolute():
        root = base / root
    cfg["root"] = root
    cfg.setdefault("name", "organizer")
    cfg.setdefault("layout", {})
    cfg.setdefault("bands", {})
    cfg.setdefault("radiometry", {})
    cfg.setdefault("nodata", {})
    cfg.setdefault("resolution", {})
    cfg.setdefault("classes", {})
    cfg.setdefault("output", {})
    return cfg


def _rglob(root: Path, pattern: str) -> list[Path]:
    return sorted(Path(p) for p in glob.glob(str(root / pattern), recursive=True))


def _sid(path: Path, id_regex: str | None) -> str:
    s = str(path).replace("\\", "/")
    if id_regex:
        m = re.search(id_regex, s)
        if m:
            return m.group(1) if m.groups() else m.group(0)
    return path.stem


def list_samples(cfg: dict) -> list[Sample]:
    L = cfg["layout"]
    root = cfg["root"]
    idre = L.get("id_regex")
    excl = re.compile(L["exclude_regex"]) if L.get("exclude_regex") else None
    samples: list[Sample] = []
    if L.get("band_files"):
        # one file per band: {our_or_source_band: "glob with {id}"}; ids come from the first band's glob
        bf: dict = L["band_files"]
        first_name, first_pat = next(iter(bf.items()))
        for p in _rglob(root, first_pat.replace("{id}", "*")):
            sid = _sid(p, idre)
            paths = []
            for bname, pat in bf.items():
                hits = _rglob(root, pat.replace("{id}", sid))
                if not hits:
                    paths = None
                    break
                paths.append(str(hits[0]))
            if paths:
                samples.append(Sample(sid, paths, None))
    else:
        for p in _rglob(root, L.get("image_glob", "**/*.tif")):
            if excl and excl.search(str(p).replace("\\", "/")):
                continue
            samples.append(Sample(_sid(p, idre), [str(p)], None))
    # masks
    if L.get("mask_from_image"):
        rx, rep = L["mask_from_image"]["regex"], L["mask_from_image"]["replace"]
        for s in samples:
            mp = re.sub(rx, rep, s.image_paths[0].replace("\\", "/"))
            s.mask_path = mp if Path(mp).is_file() else None
    elif L.get("mask_glob"):
        mid = L.get("mask_id_regex", idre)
        masks = {_sid(p, mid): str(p) for p in _rglob(root, L["mask_glob"])}
        for s in samples:
            s.mask_path = masks.get(s.id)
    # splits: {split: list file (ids per line) } or {split: regex on path}
    for split, spec in (L.get("split_lists") or {}).items():
        sp = Path(spec)
        if not sp.is_absolute():
            sp = _REPO / sp
        if sp.is_file():
            ids = {ln.strip() for ln in sp.read_text().splitlines() if ln.strip()}
            for s in samples:
                if s.id in ids or s.id.removeprefix("S2_") in ids:
                    s.split = split
        else:
            rx = re.compile(spec)
            for s in samples:
                if rx.search(s.image_paths[0].replace("\\", "/")):
                    s.split = split
    for split, rx in (L.get("split_regex") or {}).items():
        r = re.compile(rx)
        for s in samples:
            if s.split is None and r.search(s.image_paths[0].replace("\\", "/")):
                s.split = split
    if L.get("require_mask", False):
        samples = [s for s in samples if s.mask_path]
    return samples


# --------------------------------------------------------------------------- reading
_RES = {"nearest": 0, "bilinear": 1, "cubic": 2, "average": 5, "mode": 6}


def _read_raw(path: str, hwc: bool | None = None):
    """-> (arr (C,H,W), profile dict with transform/crs/descriptions/nodata or None)."""
    p = Path(path)
    if p.suffix.lower() == ".npy":
        a = np.load(p)
        if a.ndim == 2:
            a = a[None]
        elif hwc or (hwc is None and a.shape[-1] < a.shape[0]):
            a = np.moveaxis(a, -1, 0)
        return a, {"transform": None, "crs": None, "descriptions": None, "nodata": None,
                   "height": a.shape[1], "width": a.shape[2], "res": None}
    import rasterio

    with rasterio.open(p) as ds:
        a = ds.read()
        prof = {"transform": ds.transform, "crs": ds.crs, "descriptions": list(ds.descriptions),
                "nodata": ds.nodata, "height": ds.height, "width": ds.width,
                "res": (abs(ds.transform.a), abs(ds.transform.e)) if ds.transform else None,
                "bounds": tuple(ds.bounds)}
        if ds.crs is None and ds.transform.is_identity:
            prof["transform"] = None
    return a, prof


def _target_grid(prof: dict, target_res: float | None):
    """-> (transform, height, width) of the output grid (same bounds, new resolution)."""
    from rasterio.transform import from_origin

    if not target_res or prof.get("transform") is None:
        return prof.get("transform"), prof["height"], prof["width"]
    b = prof["bounds"]
    w = int(round((b[2] - b[0]) / target_res))
    h = int(round((b[3] - b[1]) / target_res))
    return from_origin(b[0], b[3], target_res, target_res), h, w


def _resample(arr: np.ndarray, src_prof: dict, dst_tf, h: int, w: int, method: str) -> np.ndarray:
    """(C,h0,w0) -> (C,h,w) on the destination grid (same CRS)."""
    if arr.shape[1:] == (h, w) and (src_prof.get("transform") is None or src_prof["transform"] == dst_tf):
        return arr
    if src_prof.get("transform") is None or dst_tf is None:
        # no georeference: plain zoom
        from scipy.ndimage import zoom

        order = 0 if method == "nearest" else 1
        return np.stack([zoom(b, (h / b.shape[0], w / b.shape[1]), order=order) for b in arr])
    from rasterio.warp import Resampling, reproject

    out = np.full((arr.shape[0], h, w), np.nan if arr.dtype.kind == "f" else 0, dtype=arr.dtype)
    for i in range(arr.shape[0]):
        reproject(arr[i], out[i], src_transform=src_prof["transform"], src_crs=src_prof["crs"],
                  dst_transform=dst_tf, dst_crs=src_prof["crs"], resampling=Resampling(_RES[method]),
                  src_nodata=(np.nan if arr.dtype.kind == "f" else None),
                  dst_nodata=(np.nan if arr.dtype.kind == "f" else None))
    return out


def _per_band(v, names: list[str], default: float) -> np.ndarray:
    if v is None:
        return np.full(len(names), default, dtype=np.float64)
    if isinstance(v, dict):
        return np.array([float(v.get(n, v.get(canon_band(n), v.get("default", default)))) for n in names])
    if isinstance(v, (list, tuple)):
        return np.array([float(x) for x in v], dtype=np.float64)
    return np.full(len(names), float(v), dtype=np.float64)


def load_image(cfg: dict, s: Sample) -> tuple[np.ndarray, list[str], dict]:
    """-> ((C,H,W) float32 reflectance in OUR band order, our band names, grid profile)."""
    B, R, N, RES = cfg["bands"], cfg["radiometry"], cfg["nodata"], cfg["resolution"]
    method = RES.get("resampling", "bilinear")
    raws = [_read_raw(p, B.get("hwc")) for p in s.image_paths]
    # source band names
    if cfg["layout"].get("band_files"):
        src_names = list(cfg["layout"]["band_files"].keys())
    else:
        src = B.get("source", "descriptions")
        if src == "descriptions":
            src_names = [d or f"band{i + 1}" for i, d in enumerate(raws[0][1]["descriptions"] or [None] * raws[0][0].shape[0])]
        else:
            src_names = list(src)
    ren = {str(k): str(v) for k, v in (B.get("rename") or {}).items()}
    names_our = [ren.get(n, canon_band(n)) for n in src_names]
    # reference grid = finest-resolution input (or first), resampled to target res
    ref = min(raws, key=lambda r: (r[1]["res"] or (1e9,))[0])[1]
    dst_tf, h, w = _target_grid(ref, RES.get("target"))
    bands = []
    for arr, prof in raws:
        arr = arr.astype(np.float32)
        nod = N.get("values")
        nod = [] if nod is None else (nod if isinstance(nod, list) else [nod])
        if prof.get("nodata") is not None and N.get("use_file_nodata", True):
            nod = nod + [prof["nodata"]]
        for v in nod:
            if v is not None and np.isfinite(v):
                arr[arr == v] = np.nan
        if N.get("zero_all_bands_is_nodata", False):
            arr[:, np.all(arr == 0, axis=0)] = np.nan
        bands.append(_resample(arr, prof, dst_tf, h, w, method))
    img = np.concatenate(bands, axis=0)
    if img.shape[0] != len(src_names):
        raise ValueError(f"{s.id}: {img.shape[0]} bands read but {len(src_names)} source names configured")
    scale = _per_band(R.get("scale"), src_names, 1.0).astype(np.float32)
    offset = _per_band(R.get("offset"), src_names, 0.0).astype(np.float32)
    img = img * scale[:, None, None] + offset[:, None, None]
    if R.get("clip") is not None:
        lo, hi = R["clip"]
        img = np.where(np.isnan(img), img, np.clip(img, lo, hi))
    out_names = B.get("output") or [n for n in names_our if n in CANON]
    idx = []
    for n in out_names:
        if n not in names_our:
            if B.get("missing", "error") == "nan":
                idx.append(None)
                continue
            raise KeyError(f"band {n} not in source bands {names_our}")
        idx.append(names_our.index(n))
    out = np.stack([img[i] if i is not None else np.full((h, w), np.nan, np.float32) for i in idx]).astype(np.float32)
    grid = {"transform": dst_tf, "crs": ref.get("crs"), "height": h, "width": w,
            "res": (RES.get("target") or (ref["res"][0] if ref.get("res") else None))}
    return out, list(out_names), grid


def load_mask(cfg: dict, s: Sample, grid: dict) -> np.ndarray | None:
    if not s.mask_path:
        return None
    C = cfg["classes"]
    arr, prof = _read_raw(s.mask_path)
    if C.get("rgb_palette"):  # color-coded PNG masks: {"r,g,b": organizer_value}
        pal = {tuple(int(x) for x in str(k).split(",")): int(v) for k, v in C["rgb_palette"].items()}
        m = np.full(arr.shape[1:], -1, np.int64)
        for rgb, v in pal.items():
            m[np.all(arr[:3] == np.array(rgb)[:, None, None], axis=0)] = v
        arr = m[None]
    band = int(C.get("mask_band", 1)) - 1
    m = arr[band]
    if m.dtype.kind == "f":
        m = np.where(np.isfinite(m), m, -1)
    m = np.rint(m).astype(np.int64)
    ign = set(int(v) for v in (C.get("ignore_values") or []))
    if prof.get("nodata") is not None and np.isfinite(prof["nodata"]):
        ign.add(int(prof["nodata"]))
    mp = {int(k): int(v) for k, v in (C.get("map") or {}).items()}
    default = C.get("default", "keep")
    if mp:
        out = np.full(m.shape, 0 if default == "keep" else int(default), np.int64)
        if default == "keep":
            out = m.copy()
        for k, v in mp.items():
            out[m == k] = v
    else:
        out = m.copy()
    for v in ign:
        out[m == v] = 0
    out[m < 0] = 0
    out = np.clip(out, 0, 255).astype(np.uint8)
    if out.shape != (grid["height"], grid["width"]):
        out = _resample(out[None], prof, grid["transform"], grid["height"], grid["width"], "nearest")[0]
    return out


def load_sample(cfg: dict, s: Sample):
    img, names, grid = load_image(cfg, s)
    mask = load_mask(cfg, s, grid)
    return img, mask, names, grid


# --------------------------------------------------------------------------- writing
def _safe_name(sid: str) -> str:
    n = re.sub(r"[^A-Za-z0-9_.\-]+", "_", sid)
    if n.split(".")[0].lower() in {"aux", "con", "nul", "prn", "com1", "lpt1"}:
        n = "_" + n
    return n


def convert(cfg: dict, out_dir: str | os.PathLike, limit: int | None = None, split: str | None = None,
            verbose: bool = True, workers: int = 8) -> Path:
    import rasterio
    from rasterio.warp import transform_bounds

    out = Path(out_dir) / cfg["name"]
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "masks").mkdir(parents=True, exist_ok=True)
    samples = list_samples(cfg)
    if split:
        samples = [s for s in samples if s.split == split]
    if limit:
        samples = samples[:limit]
    debris = int(cfg["output"].get("debris_class", 1))

    def _one(i_s):
        i, s = i_s
        img, mask, names, grid = load_sample(cfg, s)
        fn = _safe_name(s.id)
        ip = out / "images" / f"{fn}.tif"
        prof = {"driver": "GTiff", "height": img.shape[1], "width": img.shape[2], "count": img.shape[0],
                "dtype": "float32", "nodata": np.nan, "compress": "deflate", "predictor": 3,
                "tiled": img.shape[1] >= 256 and img.shape[2] >= 256}
        if grid["transform"] is not None:
            prof.update(transform=grid["transform"], crs=grid["crs"])
        with rasterio.open(ip, "w", **prof) as ds:
            ds.write(img)
            ds.descriptions = tuple(names)
            ds.update_tags(source=";".join(s.image_paths), adapter=cfg["name"])
        mp = ""
        n_lab = n_deb = 0
        if mask is not None:
            mp = out / "masks" / f"{fn}.tif"
            mprof = {k: v for k, v in prof.items() if k not in ("predictor",)}
            mprof.update(count=1, dtype="uint8", nodata=None)
            with rasterio.open(mp, "w", **mprof) as ds:
                ds.write(mask[None])
                ds.descriptions = ("class",)
            n_lab = int((mask > 0).sum())
            n_deb = int((mask == debris).sum())
        bw = ["", "", "", ""]
        if grid["transform"] is not None and grid["crs"] is not None:
            from rasterio.transform import array_bounds

            b = array_bounds(img.shape[1], img.shape[2], grid["transform"])
            bw = [round(v, 6) for v in transform_bounds(grid["crs"], "EPSG:4326", b[0], b[1], b[2], b[3])]
        if verbose and (i % 200 == 0 or i == len(samples) - 1):
            print(f"[adapter] {i + 1}/{len(samples)} {s.id}")
        return {"id": s.id, "split": s.split or "", "image": str(ip), "mask": str(mp),
                "height": img.shape[1], "width": img.shape[2], "bands": ",".join(names),
                "crs": grid["crs"].to_string() if grid["crs"] else "", "res": grid["res"],
                "west": bw[0], "south": bw[1], "east": bw[2], "north": bw[3],
                "nan_frac": round(float(np.isnan(img).any(0).mean()), 5),
                "n_labelled_px": n_lab, "n_debris_px": n_deb,
                "src_image": ";".join(s.image_paths), "src_mask": s.mask_path or ""}

    if workers and workers > 1:
        import concurrent.futures as cf

        with cf.ThreadPoolExecutor(workers) as ex:
            rows = list(ex.map(_one, enumerate(samples)))
    else:
        rows = [_one(x) for x in enumerate(samples)]
    with open(out / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["id"])
        w.writeheader()
        w.writerows(rows)
    return out / "manifest.csv"


def iter_samples(cfg: dict, split: str | None = None) -> Iterator[tuple]:
    """In-memory use without writing: yields (sample, img, mask, names, grid)."""
    for s in list_samples(cfg):
        if split and s.split != split:
            continue
        img, mask, names, grid = load_sample(cfg, s)
        yield s, img, mask, names, grid


def verify_against_marida(cfg: dict, n: int = 20, seed: int = 0) -> dict:
    """Compare adapter output with macroplastic.data.marida.load_patch for n random samples."""
    from macroplastic.data import marida

    samples = [s for s in list_samples(cfg)]
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(samples), size=min(n, len(samples)), replace=False)
    worst_img = 0.0
    mism_mask = 0
    for k in pick:
        s = samples[int(k)]
        img, mask, names, grid = load_sample(cfg, s)
        ref_img, ref_cl, _, _ = marida.load_patch(s.image_paths[0])
        assert names == marida.BAND_NAMES, (names, marida.BAND_NAMES)
        d = np.abs(np.nan_to_num(img, nan=-9) - np.nan_to_num(ref_img, nan=-9)).max()
        worst_img = max(worst_img, float(d))
        mism_mask += int((mask != ref_cl).sum())
    return {"n_checked": int(len(pick)), "max_abs_diff_image": worst_img, "mask_mismatch_px": mism_mask,
            "identical": worst_img == 0.0 and mism_mask == 0}
