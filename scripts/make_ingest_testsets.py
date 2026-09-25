"""Build 3 artificial "organizer" datasets to test `python -m macroplastic.ingest` end to end.

  (1) <out>/marida_dn_label.zip   MARIDA chips (scene sub-folders kept), bands as uint16 DN (x10000, no band names,
                                  no nodata tag), masks renamed *_cl.tif -> *_label.tif (_conf dropped)
  (2) <out>/fire_images_masks.tar.gz  fire-dataset S2 post chips (read-only source) cropped to 128x128:
                                  images/<id>.tif (10 bands uint16 with names incl. SCL) + masks/<id>.tif
                                  (severity 0..3, 255 nodata) + meta.csv (distractor table)
  (3) <out>/chips_csv/            32x32 float32 MARIDA chips (11 bands, no names) + labels.csv "chip,label"
                                  (1 = chip contains Marine Debris, 0 = only Marine Water)
  (4) <out>/evil.zip              zip-slip archive with '../evil.txt' (must be refused)

  .venv\\Scripts\\python.exe scripts\\make_ingest_testsets.py --out data\\ingest_testsets --n 30
Sources are only read. MARIDA: train+val splits only (test never touched).
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import random
import sys
import tarfile
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
FIRE_BS = Path(os.environ.get("FIRE_MONITORING_DIR", Path.home() / "hack" / "fire-monitoring")) / "data" / "train" / "bs"  # optional external dataset


def _marida_patches(n: int, seed: int, need_md: bool = True) -> list[Path]:
    from macroplastic.data.marida import list_patches, load_patch  # noqa: F401

    ps = list_patches("train") + list_patches("val")
    rng = random.Random(seed)
    rng.shuffle(ps)
    out, scenes = [], {}
    import rasterio

    for p in ps:
        sc = Path(p).parent.name
        if scenes.get(sc, 0) >= 3:
            continue
        with rasterio.open(str(p).replace(".tif", "_cl.tif")) as ds:
            cl = ds.read(1)
        if need_md and len(out) < n // 2 and not (cl == 1).any():
            continue
        out.append(Path(p))
        scenes[sc] = scenes.get(sc, 0) + 1
        if len(out) >= n:
            break
    return out


def _tif_bytes(arr, profile, descriptions=None) -> bytes:
    import rasterio
    from rasterio.io import MemoryFile

    with MemoryFile() as mf:
        with mf.open(**profile) as ds:
            ds.write(arr)
            if descriptions:
                ds.descriptions = tuple(descriptions)
        return mf.read()


def make_marida_zip(out: Path, n: int, seed: int) -> Path:
    import rasterio

    zp = out / "marida_dn_label.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in _marida_patches(n, seed):
            with rasterio.open(p) as ds:
                a = ds.read().astype(np.float32)
                prof = {"driver": "GTiff", "height": ds.height, "width": ds.width, "count": ds.count,
                        "dtype": "uint16", "crs": ds.crs, "transform": ds.transform, "compress": "deflate"}
            dn = np.where(np.isfinite(a), np.clip(np.round(a * 10000), 1, 65535), 0).astype(np.uint16)
            rel = f"marida_dn/{p.parent.name}/{p.stem}"
            zf.writestr(rel + ".tif", _tif_bytes(dn, prof))
            with rasterio.open(str(p).replace(".tif", "_cl.tif")) as ds:
                m = ds.read()
                mprof = {"driver": "GTiff", "height": ds.height, "width": ds.width, "count": 1, "dtype": "uint8",
                         "crs": ds.crs, "transform": ds.transform, "compress": "deflate"}
            zf.writestr(rel + "_label.tif", _tif_bytes(m.astype(np.uint8), mprof))
        zf.writestr("marida_dn/README.txt", "Sentinel-2 chips, DN, masks *_label.tif. Class ids see paper.\n")
    return zp


def make_fire_targz(out: Path, n: int, seed: int, crop: int = 128) -> Path:
    import rasterio
    from rasterio.windows import Window, transform as wtransform

    tp = out / "fire_images_masks.tar.gz"
    masks = sorted((FIRE_BS / "masks").glob("*_MASK.tif"))
    rng = random.Random(seed)
    rng.shuffle(masks)
    meta_rows = []
    meta = {}
    if (FIRE_BS / "meta.csv").is_file():
        with open(FIRE_BS / "meta.csv", encoding="utf-8") as f:
            meta = {r["chip_id"]: r for r in csv.DictReader(f)}
    with tarfile.open(tp, "w:gz") as tf:
        def add(name, data: bytes):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))

        k = 0
        for mp in masks:
            cid = mp.stem.replace("_MASK", "")
            ip = FIRE_BS / "sentinel2_post" / f"{cid}_Sentinel-2_post.tif"
            if not ip.is_file():
                continue
            with rasterio.open(mp) as ds:
                m = ds.read(1)
                mtf, mcrs = ds.transform, ds.crs
            H, W = m.shape
            best = None
            for y in range(0, H - crop + 1, crop // 2):
                for x in range(0, W - crop + 1, crop // 2):
                    w = m[y:y + crop, x:x + crop]
                    fr = np.mean((w > 0) & (w < 255))
                    bad = np.mean(w == 255)
                    sc = abs(fr - 0.3) + bad
                    if best is None or sc < best[0]:
                        best = (sc, y, x, fr)
            if best is None or best[3] < 0.02:
                continue
            _, y, x, _ = best
            win = Window(x, y, crop, crop)
            with rasterio.open(ip) as ds:
                a = ds.read(window=win)
                prof = {"driver": "GTiff", "height": crop, "width": crop, "count": ds.count, "dtype": ds.dtypes[0],
                        "crs": ds.crs, "transform": wtransform(win, ds.transform), "compress": "deflate"}
                descs = ds.descriptions
            add(f"fire_s2/images/{cid}.tif", _tif_bytes(a, prof, descs))
            mprof = {"driver": "GTiff", "height": crop, "width": crop, "count": 1, "dtype": "uint8", "crs": mcrs,
                     "transform": wtransform(win, mtf), "compress": "deflate", "nodata": 255}
            add(f"fire_s2/masks/{cid}.tif", _tif_bytes(m[y:y + crop, x:x + crop][None], mprof, ["severity"]))
            if cid in meta:
                meta_rows.append({k2: meta[cid][k2] for k2 in ("chip_id", "fire_event_id", "date_pre", "date_post",
                                                                "cloud_frac")})
            k += 1
            if k >= n:
                break
        if meta_rows:
            s = io.StringIO()
            w = csv.DictWriter(s, fieldnames=list(meta_rows[0]))
            w.writeheader()
            w.writerows(meta_rows)
            add("fire_s2/meta.csv", s.getvalue().encode("utf-8"))
    return tp


def make_chips_csv(out: Path, n: int, seed: int, size: int = 32) -> Path:
    import rasterio
    from rasterio.windows import Window, transform as wtransform

    d = out / "chips_csv"
    (d / "chips").mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    rows = []
    n_pos = n_neg = 0
    for p in _marida_patches(2 * max(n, 20), seed + 1):
        with rasterio.open(p) as ds:
            a = ds.read().astype(np.float32)
            tf, crs = ds.transform, ds.crs
        with rasterio.open(str(p).replace(".tif", "_cl.tif")) as ds:
            cl = ds.read(1)
        H, W = cl.shape
        h2 = size // 2
        for want in (1, 0):
            if want == 1:
                ys, xs = np.nonzero(cl == 1)
                if not len(ys) or n_pos >= n // 2:
                    continue
                i = rng.integers(len(ys))
                y0, x0 = int(np.clip(ys[i] - h2, 0, H - size)), int(np.clip(xs[i] - h2, 0, W - size))
            else:
                if n_neg >= n - n // 2:
                    continue
                ok = None
                ys, xs = np.nonzero(cl == 7)  # centred on labelled Marine Water, no debris inside
                for _ in range(40 if len(ys) else 0):
                    i = rng.integers(len(ys))
                    y0, x0 = int(np.clip(ys[i] - h2, 0, H - size)), int(np.clip(xs[i] - h2, 0, W - size))
                    w = cl[y0:y0 + size, x0:x0 + size]
                    if not (w == 1).any():
                        ok = (y0, x0)
                        break
                if ok is None:
                    continue
                y0, x0 = ok
            win = Window(x0, y0, size, size)
            chip = a[:, y0:y0 + size, x0:x0 + size]
            k = len(rows)
            fn = f"{p.stem}_{k:03d}.tif"
            prof = {"driver": "GTiff", "height": size, "width": size, "count": chip.shape[0], "dtype": "float32",
                    "crs": crs, "transform": wtransform(win, tf)}
            with rasterio.open(d / "chips" / fn, "w", **prof) as ds:
                ds.write(chip)
            rows.append({"chip": fn, "label": want})
            n_pos += want
            n_neg += 1 - want
        if n_pos + n_neg >= n:
            break
    with open(d / "labels.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["chip", "label"])
        w.writeheader()
        w.writerows(rows)
    return d


def make_evil_zip(out: Path) -> Path:
    zp = out / "evil.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("ok/readme.txt", "fine\n")
        zf.writestr("../evil.txt", "escaped!\n")
    return zp


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "data" / "ingest_testsets"))
    ap.add_argument("--n", type=int, default=30, help="chips per set (20-50 recommended)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--only", default="1,2,3,4", help="which sets: 1,2,3,4")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    only = set(a.only.split(","))
    if "1" in only:
        print("[testsets] (1)", make_marida_zip(out, a.n, a.seed))
    if "2" in only:
        if FIRE_BS.is_dir():
            print("[testsets] (2)", make_fire_targz(out, a.n, a.seed))
        else:
            print(f"[testsets] (2) skipped: {FIRE_BS} not found")
    if "3" in only:
        print("[testsets] (3)", make_chips_csv(out, a.n, a.seed))
    if "4" in only:
        print("[testsets] (4)", make_evil_zip(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
