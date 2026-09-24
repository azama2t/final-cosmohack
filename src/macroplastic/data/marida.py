"""MARIDA loader (Kikaki et al. 2022, CC BY 4.0).

Layout (as unpacked from MARIDA.zip):
    <root>/patches/S2_<d-m-yy>_<TILE>/S2_<d-m-yy>_<TILE>_<i>.tif      11 bands, float32 rhorc
    <root>/patches/S2_<d-m-yy>_<TILE>/S2_<d-m-yy>_<TILE>_<i>_cl.tif   class 0..15 (0 = unlabelled)
    <root>/patches/S2_<d-m-yy>_<TILE>/S2_<d-m-yy>_<TILE>_<i>_conf.tif confidence 1..3 (0 = none)
    <root>/splits/{train,val,test}_X.txt   names without "S2_" and ".tif", e.g. "1-12-19_48MYU_0"
    <root>/labels_mapping.txt              JSON: patch -> 15-length multi-hot

Root is resolved from (in order): explicit arg, env MARIDA_ROOT, <repo>/data/MARIDA
(searching one level deeper for a nested "patches" folder).
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path
from typing import Iterator

import numpy as np

BAND_NAMES = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
# central wavelengths (nm), S2A, same order as BAND_NAMES
BAND_WAVELENGTHS = [442.7, 492.4, 559.8, 664.6, 704.1, 740.5, 782.8, 832.8, 864.7, 1613.7, 2202.4]

CLASS_NAMES = {
    0: "Unlabelled",
    1: "Marine Debris",
    2: "Dense Sargassum",
    3: "Sparse Sargassum",
    4: "Natural Organic Material",
    5: "Ship",
    6: "Clouds",
    7: "Marine Water",
    8: "Sediment-Laden Water",
    9: "Foam",
    10: "Turbid Water",
    11: "Shallow Water",
    12: "Waves",
    13: "Cloud Shadows",
    14: "Wakes",
    15: "Mixed Water",
}
CONF_NAMES = {0: "None", 1: "High", 2: "Moderate", 3: "Low"}
SPLITS = ("train", "val", "test")

_REPO = Path(__file__).resolve().parents[3]
_SCENE_RE = re.compile(r"^(?:S2_)?(\d{1,2})-(\d{1,2})-(\d{2})_([0-9]{2}[A-Z]{3})(?:_(\d+))?(?:\.tif)?$")


def resolve_root(root: str | os.PathLike | None = None) -> Path:
    cands = []
    if root:
        cands.append(Path(root))
    if os.environ.get("MARIDA_ROOT"):
        cands.append(Path(os.environ["MARIDA_ROOT"]))
    cands.append(_REPO / "data" / "MARIDA")
    for c in cands:
        if (c / "patches").is_dir():
            return c
        if c.is_dir():
            for sub in c.iterdir():
                if (sub / "patches").is_dir():
                    return sub
    raise FileNotFoundError(f"MARIDA root with 'patches/' not found in {cands}")


def parse_scene(name: str) -> dict:
    """'S2_1-12-19_48MYU_0' / '1-12-19_48MYU_0' / 'S2_1-12-19_48MYU' -> dict(scene, date, tile, idx)."""
    base = Path(str(name)).name
    base = re.sub(r"_(cl|conf)\.tif$", ".tif", base)
    m = _SCENE_RE.match(base)
    if not m:
        raise ValueError(f"cannot parse MARIDA name: {name}")
    d, mo, yy, tile, idx = m.groups()
    date = _dt.date(2000 + int(yy), int(mo), int(d))
    return {
        "scene": f"S2_{d}-{mo}-{yy}_{tile}",
        "date": date.isoformat(),
        "tile": tile,
        "idx": int(idx) if idx is not None else None,
    }


def _patch_path(name: str, root: Path) -> Path:
    base = Path(str(name)).name
    if base.endswith(".tif"):
        base = base[:-4]
    if not base.startswith("S2_"):
        base = "S2_" + base
    scene = parse_scene(base)["scene"]
    return root / "patches" / scene / f"{base}.tif"


def list_patches(split: str | None = None, root=None) -> list[Path]:
    """Paths to image .tif of a split ('train'|'val'|'test') or all patches (split=None/'all')."""
    r = resolve_root(root)
    if split in (None, "all"):
        out = []
        for d in sorted((r / "patches").iterdir()):
            if d.is_dir():
                out += sorted(p for p in d.glob("S2_*.tif") if not p.stem.endswith(("_cl", "_conf")))
        return out
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS} or 'all'")
    names = [ln.strip() for ln in (r / "splits" / f"{split}_X.txt").read_text().splitlines() if ln.strip()]
    return [_patch_path(n, r) for n in names]


def split_of(root=None) -> dict[str, str]:
    """patch stem (S2_..._i) -> split."""
    out = {}
    for s in SPLITS:
        for p in list_patches(s, root):
            out[p.stem] = s
    return out


def load_patch(name, root=None):
    """Return (img (11,H,W) float32, cl (H,W) uint8, conf (H,W) uint8, profile).

    `name` may be a path to the image tif, a stem 'S2_..._i' or a split entry '..._i'.
    NaN in img is preserved (caller decides how to fill).
    """
    import rasterio

    p = Path(name)
    if not p.is_file():
        p = _patch_path(str(name), resolve_root(root))
    with rasterio.open(p) as ds:
        img = ds.read().astype(np.float32)
        profile = ds.profile.copy()
        profile["bounds"] = tuple(ds.bounds)
    stem = p.with_suffix("")
    with rasterio.open(f"{stem}_cl.tif") as ds:
        cl = ds.read(1).astype(np.uint8)
    with rasterio.open(f"{stem}_conf.tif") as ds:
        conf = ds.read(1).astype(np.uint8)
    return img, cl, conf, profile


def merge_water(cl: np.ndarray, enabled: bool = True) -> np.ndarray:
    """Authors' baseline: Waves(12), Cloud Shadows(13), Wakes(14), Mixed Water(15) -> Marine Water(7)."""
    if not enabled:
        return cl
    out = cl.copy()
    out[np.isin(out, (12, 13, 14, 15))] = 7
    return out


def iter_split(split: str | None = "train", root=None, merge: bool = False) -> Iterator[tuple]:
    """Yield (path, img, cl, conf, profile) for every patch of a split."""
    for p in list_patches(split, root):
        img, cl, conf, prof = load_patch(p)
        yield p, img, (merge_water(cl) if merge else cl), conf, prof
