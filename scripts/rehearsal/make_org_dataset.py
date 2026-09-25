"""Rehearsal: build an "organizer-style" dataset from the MADOS *test* split only.

- Scenes: MADOS test split minus scenes that are the same acquisition as a MARIDA test scene
  (reports/mados_overlap_with_marida_test.csv, marida_split == test & kind == same_acquisition).
  MADOS test was never used in training (reports/l13_mados.md), so the dataset is "unseen" for weights/lgbm.
- Scene-level split into org train / org test (seeded, both sides must contain debris).
- Images: 11-band GeoTIFF uint16 DN = round(reflectance * 10000), clipped to 0..65535, NaN -> 0,
  shuffled band order, no band descriptions, no CRS/transform (MADOS has none anyway).
- Masks: single-band PNG uint8, organizer class codes: 0 background/unlabelled, 1 water, 2 other objects, 3 debris.
- Output: <out>/org_dataset.zip (train/images, train/masks, test/images, README.txt),
  <out>/org_raw/ (same tree unpacked, for convenience), <out>/hidden_test_masks/*.png ("private"),
  <out>/private_key.json (org id <-> MADOS patch, band order; NOT given to the "participant").

Usage:
    .venv\\Scripts\\python.exe scripts\\rehearsal\\make_org_dataset.py --out out\\rehearsal
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import sys
import warnings
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from macroplastic.data import mados  # noqa: E402

OUR_BANDS = mados.BAND_NAMES  # B1..B12 (MARIDA order)
ORG_ORDER = ["B8", "B4", "B3", "B2", "B11", "B12", "B5", "B6", "B7", "B8A", "B1"]
# MADOS raw code -> organizer code
ORG_LUT = np.zeros(16, np.uint8)
ORG_LUT[1] = 3  # Marine Debris
for c in (7, 8, 10, 11):  # water types
    ORG_LUT[c] = 1
for c in (2, 3, 4, 5, 6, 9, 12, 13, 14, 15):  # algae, NOM, ship, oil, foam, waves, platform, jellyfish, snot
    ORG_LUT[c] = 2

README = """Dataset: Sentinel-2 chips 240x240 px, 10 m, 11 bands, GeoTIFF uint16 (DN, reflectance x 10000).
Band order: B8, B4, B3, B2, B11, B12, B5, B6, B7, B8A, B1 (20 m / 60 m bands resampled to 10 m).
Mask classes (PNG, uint8): 0 - background, 1 - water, 2 - other floating objects, 3 - marine debris.
Metric: F1 score of class 3 (marine debris), computed over all pixels of all test images together.
Submission: one PNG mask (uint8, 240x240, same class codes) per test image, same file name as the image (.png), in one zip.
"""


def excluded_scenes(csv_path: Path) -> set[str]:
    out = set()
    with open(csv_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("marida_split") == "test" and r.get("kind") == "same_acquisition":
                out.add(r["mados_scene"])
    return out


def write_tif(path: Path, arr: np.ndarray) -> None:
    import rasterio

    c, h, w = arr.shape
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=c, dtype="uint16",
                           compress="deflate") as ds:
            ds.write(arr)


def write_png(path: Path, m: np.ndarray) -> None:
    from PIL import Image

    Image.fromarray(m.astype(np.uint8), mode="L").save(path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "out" / "rehearsal"))
    ap.add_argument("--overlap-csv", default=str(ROOT / "reports" / "mados_overlap_with_marida_test.csv"))
    ap.add_argument("--test-frac", type=float, default=0.4)
    ap.add_argument("--seed", type=int, default=30)
    args = ap.parse_args()

    out = Path(args.out)
    raw = out / "org_raw"
    hidden = out / "hidden_test_masks"
    for d in (raw, hidden):
        if d.exists():
            shutil.rmtree(d)
    for sub in ("train/images", "train/masks", "test/images"):
        (raw / sub).mkdir(parents=True, exist_ok=True)
    hidden.mkdir(parents=True, exist_ok=True)

    excl = excluded_scenes(Path(args.overlap_csv))
    patches = mados.list_patches("test")
    by_scene: dict[str, list[str]] = defaultdict(list)
    for p in patches:
        s, _ = mados.parse_patch(p)
        if s not in excl:
            by_scene[s].append(p)
    scenes = sorted(by_scene, key=lambda s: int(s.split("_")[1]))
    print(f"MADOS test patches {len(patches)}, excluded scenes (MARIDA test acq.) {sorted(excl)}; "
          f"kept {len(scenes)} scenes / {sum(len(v) for v in by_scene.values())} patches")

    # MD pixels per scene (raw codes) for a split with debris on both sides
    md = {}
    for s in scenes:
        n = 0
        for p in by_scene[s]:
            _, crop = mados.parse_patch(p)
            cl = mados._read(mados.resolve_root() / s / "10" / f"{s}_L2R_cl_{crop}.tif")
            n += int((cl == 1).sum())
        md[s] = n
    rng = random.Random(args.seed)
    for _ in range(1000):
        sh = scenes[:]
        rng.shuffle(sh)
        k = max(1, round(len(sh) * args.test_frac))
        test_s, train_s = sorted(sh[:k]), sorted(sh[k:])
        mt, mtr = sum(md[s] for s in test_s), sum(md[s] for s in train_s)
        if mt >= 0.25 * (mt + mtr) and mtr >= 0.4 * (mt + mtr):
            break
    print(f"org train scenes {len(train_s)} (MD px {mtr}), org test scenes {len(test_s)} (MD px {mt})")

    perm = [OUR_BANDS.index(b) for b in ORG_ORDER]
    scene_ids = {s: i for i, s in enumerate(rng.sample(scenes, len(scenes)))}  # anonymised scene numbers
    key = {"band_order": ORG_ORDER, "lut_mados_to_org": ORG_LUT.tolist(), "items": []}
    stats = defaultdict(lambda: np.zeros(4, np.int64))
    for split, ss in (("train", train_s), ("test", test_s)):
        for s in ss:
            for p in sorted(by_scene[s], key=lambda x: mados.parse_patch(x)[1]):
                img, _, _, info = mados.load_patch(p, extra=True)
                cl = ORG_LUT[np.clip(info["cl_mados"].astype(np.intp), 0, 15)]
                dn = np.nan_to_num(img[perm], nan=0.0) * 10000.0
                dn = np.clip(np.rint(dn), 0, 65535).astype(np.uint16)
                oid = f"r{scene_ids[s]:02d}_{mados.parse_patch(p)[1]:03d}"
                write_tif(raw / split / "images" / f"{oid}.tif", dn)
                mdir = raw / "train" / "masks" if split == "train" else hidden
                write_png(mdir / f"{oid}.png", cl)
                stats[split] += np.bincount(cl.ravel(), minlength=4)[:4]
                key["items"].append({"id": oid, "split": split, "mados": p, "sensor": info["sensor"]})
    (raw / "README.txt").write_text(README, encoding="utf-8")
    key["class_px"] = {k: v.tolist() for k, v in stats.items()}
    key["scenes"] = {"train": train_s, "test": test_s}
    (out / "private_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")

    zp = out / "org_dataset.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(raw.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(raw).as_posix())
    for k, v in stats.items():
        print(f"{k}: class px 0/1/2/3 = {v.tolist()}")
    print(f"wrote {zp} ({zp.stat().st_size / 1e6:.1f} MB), hidden masks {hidden}")


if __name__ == "__main__":
    main()
