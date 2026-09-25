"""MADOS loader (Kikaki, Kakogeorgiou et al. 2024, ISPRS; Zenodo 10664073, CC BY 4.0).

Layout (as unpacked):
    <root>/Scene_<k>/10/Scene_<k>_L2R_rhorc_<wl>_<crop>.tif   10 m bands, 240x240: 492, 559|560, 665, 833
    <root>/Scene_<k>/20/Scene_<k>_L2R_rhorc_<wl>_<crop>.tif   20 m bands, 120x120: 704, 739|740, 780|783, 864|865,
                                                              1610|1614, 2186|2202
    <root>/Scene_<k>/60/Scene_<k>_L2R_rhorc_<wl>_<crop>.tif   60 m band, 40x40: 442|443
    <root>/Scene_<k>/10/Scene_<k>_L2R_{cl,conf,rep}_<crop>.tif  labels at 10 m (also copies at 20 m)
    <root>/splits/{train,val,test}_X.txt                     entries "Scene_<k>_<crop>"
Wavelength suffixes differ between S2A (443, 560, 740, 783, 865, 1614, 2202) and S2B (442, 559, 739, 780, 864,
1610, 2186); sorting the 11 rhorc files by wavelength gives exactly the MARIDA order
B1, B2, B3, B4, B5, B6, B7, B8, B8A, B11, B12 in both cases.
Up-sampling to 10 m is nearest (pixel repetition x2 / x6), identical to rasterio Resampling.nearest used by the
authors' utils/dataset.py and stack_patches.py. Files are NOT georeferenced (identity transform, no tags), so the
tile/date of a scene is unknown from the files themselves (see scripts/train_lgbm_mados.py overlap).

Classes (utils/assets.py of the MADOS repo; 0 = unlabelled) -> MARIDA scheme (macroplastic.data.marida.CLASS_NAMES):
    MADOS 1 Marine Debris            -> 1 Marine Debris
    MADOS 2 Dense Sargassum          -> 2 Dense Sargassum
    MADOS 3 Sparse Floating Algae    -> 3 Sparse Sargassum
    MADOS 4 Natural Organic Material -> 4 Natural Organic Material
    MADOS 5 Ship                     -> 5 Ship
    MADOS 7 Marine Water             -> 7 Marine Water
    MADOS 8 Sediment-Laden Water     -> 8 Sediment-Laden Water
    MADOS 9 Foam                     -> 9 Foam
    MADOS 10 Turbid Water            -> 10 Turbid Water
    MADOS 11 Shallow Water           -> 11 Shallow Water
    MADOS 12 Waves & Wakes           -> 12 Waves (MARIDA baseline merges 12..15 into 7 anyway)
    MADOS 6 Oil Spill / 13 Oil Platform / 14 Jellyfish / 15 Sea snot: no MARIDA class ->
        extended codes 16 / 17 / 18 / 19 (extra=True, default; negatives for a binary MD model)
        or 0 = unlabelled (extra=False, strict MARIDA scheme 0..15).
Confidence: 1 High, 2 Moderate, 3 Low (same as MARIDA), 0 = none.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import numpy as np

BAND_NAMES = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
MADOS_CLASSES = {
    1: "Marine Debris", 2: "Dense Sargassum", 3: "Sparse Floating Algae", 4: "Natural Organic Material",
    5: "Ship", 6: "Oil Spill", 7: "Marine Water", 8: "Sediment-Laden Water", 9: "Foam", 10: "Turbid Water",
    11: "Shallow Water", 12: "Waves & Wakes", 13: "Oil Platform", 14: "Jellyfish", 15: "Sea snot",
}
EXTRA_NAMES = {16: "Oil Spill (MADOS)", 17: "Oil Platform (MADOS)", 18: "Jellyfish (MADOS)", 19: "Sea snot (MADOS)"}
# MADOS code -> MARIDA code (index = MADOS code 0..15)
LUT_EXTRA = np.array([0, 1, 2, 3, 4, 5, 16, 7, 8, 9, 10, 11, 12, 17, 18, 19], np.uint8)
LUT_STRICT = np.array([0, 1, 2, 3, 4, 5, 0, 7, 8, 9, 10, 11, 12, 0, 0, 0], np.uint8)
SPLITS = ("train", "val", "test")

_REPO = Path(__file__).resolve().parents[3]
_RHORC_RE = re.compile(r"_L2R_rhorc_(\d+)_(\d+)\.tif$")


def resolve_root(root: str | os.PathLike | None = None) -> Path:
    cands = [Path(root)] if root else []
    if os.environ.get("MADOS_ROOT"):
        cands.append(Path(os.environ["MADOS_ROOT"]))
    cands.append(_REPO / "data" / "MADOS")
    for c in cands:
        if c.is_dir() and any(c.glob("Scene_*")):
            return c
        if c.is_dir():
            for sub in c.iterdir():
                if sub.is_dir() and any(sub.glob("Scene_*")):
                    return sub
    raise FileNotFoundError(f"MADOS root with 'Scene_*' not found in {cands}")


def map_classes(cl_mados: np.ndarray, extra: bool = True) -> np.ndarray:
    """MADOS class codes 0..15 -> MARIDA codes (0..15, plus 16..19 for MADOS-only classes if extra)."""
    lut = LUT_EXTRA if extra else LUT_STRICT
    c = np.asarray(cl_mados)
    c = np.where((c >= 0) & (c <= 15), c, 0).astype(np.intp)
    return lut[c]


def parse_patch(name: str) -> tuple[str, int]:
    """'Scene_12_3' -> ('Scene_12', 3)."""
    m = re.match(r"^(Scene_\d+)_(\d+)$", str(name).strip())
    if not m:
        raise ValueError(f"cannot parse MADOS patch name: {name}")
    return m.group(1), int(m.group(2))


def list_scenes(root=None) -> list[str]:
    r = resolve_root(root)
    return sorted((p.name for p in r.glob("Scene_*") if p.is_dir()), key=lambda s: int(s.split("_")[1]))


def list_patches(split: str | None = None, root=None) -> list[str]:
    """Patch names 'Scene_<k>_<crop>' of a MADOS split, or all patches (split None/'all')."""
    r = resolve_root(root)
    if split in (None, "all"):
        out = []
        for s in list_scenes(r):
            crops = sorted(int(_c.stem.split("_cl_")[-1]) for _c in (r / s / "10").glob(f"{s}_L2R_cl_*.tif"))
            out += [f"{s}_{c}" for c in crops]
        return out
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS} or 'all'")
    return [ln.strip() for ln in (r / "splits" / f"{split}_X.txt").read_text().splitlines() if ln.strip()]


def split_of(root=None) -> dict[str, str]:
    out = {}
    for s in SPLITS:
        for p in list_patches(s, root):
            out[p] = s
    return out


def band_files(name: str, root=None) -> list[tuple[int, int, Path]]:
    """[(wavelength_nm, resolution_m, path)] of the 11 rhorc bands, sorted by wavelength (= MARIDA band order)."""
    r = resolve_root(root)
    scene, crop = parse_patch(name)
    out = []
    for res in (10, 20, 60):
        for f in (r / scene / str(res)).glob(f"{scene}_L2R_rhorc_*_{crop}.tif"):
            m = _RHORC_RE.search(f.name)
            if m and int(m.group(2)) == crop:
                out.append((int(m.group(1)), res, f))
    out.sort(key=lambda t: t[0])
    if len(out) != 11:
        raise ValueError(f"{name}: expected 11 rhorc bands, found {len(out)}: {[w for w, _, _ in out]}")
    return out


def _read(path: Path) -> np.ndarray:
    import warnings

    import rasterio

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # NotGeoreferencedWarning
        with rasterio.open(path) as ds:
            return ds.read(1)


def upsample_nearest(a: np.ndarray, factor: int) -> np.ndarray:
    return a if factor == 1 else np.repeat(np.repeat(a, factor, 0), factor, 1)


def load_patch(name: str, root=None, extra: bool = True):
    """Return (img (11,240,240) float32 in MARIDA band order, cl (H,W) uint8 MARIDA scheme, conf (H,W) uint8, info).

    Non-finite reflectance -> NaN (same convention as MARIDA). info = {'wavelengths', 'sensor' ('S2A'|'S2B'),
    'cl_mados' (raw MADOS codes)}.
    """
    r = resolve_root(root)
    scene, crop = parse_patch(name)
    bands = band_files(name, r)
    img = []
    for wl, res, f in bands:
        a = _read(f).astype(np.float32)
        img.append(upsample_nearest(a, res // 10))
    shapes = {x.shape for x in img}
    if len(shapes) != 1:
        raise ValueError(f"{name}: band shapes differ after up-sampling: {shapes}")
    img = np.stack(img, 0)
    img[~np.isfinite(img)] = np.nan
    cl_raw = _read(r / scene / "10" / f"{scene}_L2R_cl_{crop}.tif").astype(np.uint8)
    conf = _read(r / scene / "10" / f"{scene}_L2R_conf_{crop}.tif").astype(np.uint8)
    wls = [w for w, _, _ in bands]
    info = {"wavelengths": wls, "sensor": "S2A" if 443 in wls else "S2B", "cl_mados": cl_raw}
    return img, map_classes(cl_raw, extra), conf, info
