"""GeoTIFF I/O. Arrays are always (C, H, W); images are float32, masks uint8.

    from macroplastic.io import read_raster, write_raster, channel_names, list_images
    arr, profile = read_raster("x.tif")                         # (C,H,W) float32 + rasterio profile
    names = channel_names("marida")                             # from configs/channels.yaml
    write_raster("prob.tif", prob_u8, profile, dtype="uint8")   # (H,W) or (C,H,W)

All paths go through win_path(): Windows reserved names (aux, con, nul, prn, com1...) are only
reachable with the \\\\?\\ prefix.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Iterable

import numpy as np

from .utils import CONFIGS

CHANNELS_YAML = CONFIGS / "channels.yaml"

#: Stem suffixes of rasters that are not input images (MARIDA masks and our outputs).
AUX_SUFFIXES = ("_cl", "_conf", "_prob", "_mask")


def win_path(path) -> str:
    r"""On Windows: absolute normalized path with the ``\\?\`` prefix (works for aux/con/nul
    components and long paths). Elsewhere: str(path)."""
    s = os.fspath(path)
    if sys.platform != "win32":
        return s
    if s.startswith("\\\\?\\"):
        return s
    # not os.path.abspath(): it maps a trailing 'aux' component to the device '\\.\aux'
    s = os.path.normpath(os.path.join(os.getcwd(), s))
    if s.startswith("\\\\"):  # UNC share
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


def exists(path) -> bool:
    """os.path.exists through win_path()."""
    return os.path.exists(win_path(path))


def normalize_band(name: str) -> str:
    """'b08' / 'B08' / 'B8' -> 'B8'; 'b8a' -> 'B8A'; other strings are upper-cased."""
    s = str(name).strip().upper()
    m = re.fullmatch(r"B0*(\d{1,2})(A?)", s)
    if m:
        return f"B{int(m.group(1))}{m.group(2)}"
    return s


_CHANNEL_CACHE: dict[str, list[str]] | None = None


def channel_sets(path: str | os.PathLike | None = None) -> dict[str, list[str]]:
    """All channel sets from configs/channels.yaml: {set_name: [band names]}."""
    global _CHANNEL_CACHE
    if path is None and _CHANNEL_CACHE is not None:
        return _CHANNEL_CACHE
    from .utils import load_yaml

    data = load_yaml(path or CHANNELS_YAML)
    out = {k: [normalize_band(b) for b in v] for k, v in data.items()}
    if path is None:
        _CHANNEL_CACHE = out
    return out


def channel_names(set_name: str) -> list[str]:
    """Band names of a channel set: 'marida' (11), 's2_l2a_12' (12), 'mdd_input' (12)."""
    sets = channel_sets()
    if set_name not in sets:
        raise KeyError(f"unknown channel set {set_name!r}; known: {sorted(sets)}")
    return list(sets[set_name])


def guess_channel_names(n_bands: int, descriptions: Iterable[str | None] = ()) -> list[str] | None:
    """Band names for a file: from band descriptions if all look like S2 bands ('B8', 'B08', 'B8A'),
    else by count (11 -> marida, 12 -> s2_l2a_12). None if unknown."""
    desc = [d for d in (descriptions or ()) if d]
    if len(desc) == n_bands and n_bands > 0:
        norm = [normalize_band(d) for d in desc]
        if all(re.fullmatch(r"B\d{1,2}A?", d) for d in norm):
            return norm
    by_count = {11: "marida", 12: "s2_l2a_12"}
    return channel_names(by_count[n_bands]) if n_bands in by_count else None


def read_raster(path, dtype=np.float32, bands: Iterable[int] | None = None) -> tuple[np.ndarray, dict]:
    """Read a GeoTIFF -> ((C,H,W) array cast to `dtype` (None = keep), rasterio profile dict).

    `bands` are 1-based band indexes (rasterio convention); None = all. Nodata values stay as-is
    (MARIDA uses NaN). Band descriptions are in profile['descriptions'] (list of str|None).
    Raises FileNotFoundError if the file is missing.
    """
    import rasterio

    p = win_path(path)
    if not os.path.exists(p):
        raise FileNotFoundError(str(path))
    with rasterio.open(p) as src:
        arr = src.read(list(bands)) if bands is not None else src.read()
        profile = dict(src.profile)
        profile["descriptions"] = list(src.descriptions)
    if dtype is not None and arr.dtype != dtype:
        arr = arr.astype(dtype)
    return arr, profile


def read_mask(path) -> np.ndarray:
    """Single-band mask (e.g. MARIDA _cl.tif / _conf.tif) -> (H,W) uint8 (NaN -> 0)."""
    arr, _ = read_raster(path, dtype=None)
    a = arr[0]
    if a.dtype.kind == "f":
        a = np.nan_to_num(a, nan=0.0)
    return a.astype(np.uint8)


def output_profile(profile: dict, count: int, dtype: str, nodata=None) -> dict:
    """Profile for writing `count` bands of `dtype` with CRS/transform/size of `profile` (GTiff, deflate)."""
    out = {k: profile[k] for k in ("crs", "transform", "width", "height") if profile.get(k) is not None}
    if "width" not in out or "height" not in out:
        raise ValueError("profile must contain width and height")
    out.update(driver="GTiff", count=int(count), dtype=str(np.dtype(dtype)), compress="deflate")
    if nodata is not None:
        out["nodata"] = nodata
    if out["width"] % 16 == 0 and out["height"] % 16 == 0 and min(out["width"], out["height"]) >= 256:
        out.update(tiled=True, blockxsize=256, blockysize=256)
    return out


def write_raster(path, arr: np.ndarray, profile: dict, dtype: str = "float32", nodata=None,
                 descriptions: Iterable[str] | None = None) -> Path:
    """Write (H,W) or (C,H,W) `arr` as GeoTIFF with CRS/transform of `profile` (e.g. from read_raster).

    Array H,W must equal profile height,width. `dtype` e.g. 'uint8' / 'float32'. Parent dirs are
    created. Returns Path(path).
    """
    import rasterio

    a = np.asarray(arr)
    if a.ndim == 2:
        a = a[None]
    if a.ndim != 3:
        raise ValueError(f"expected (H,W) or (C,H,W), got shape {a.shape}")
    if (a.shape[1], a.shape[2]) != (profile.get("height"), profile.get("width")):
        raise ValueError(f"array {a.shape[1:]} does not match profile {profile.get('height')}x{profile.get('width')}")
    out = output_profile(profile, a.shape[0], dtype, nodata)
    p = win_path(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with rasterio.open(p, "w", **out) as dst:
        dst.write(a.astype(dtype, copy=False))
        if descriptions is not None:
            for i, d in enumerate(descriptions, start=1):
                dst.set_band_description(i, str(d))
    return Path(path)


def prob_to_uint8(prob: np.ndarray) -> np.ndarray:
    """Probability 0..1 (NaN -> 0) -> uint8 0..255 (= round(prob*255))."""
    p = np.nan_to_num(np.asarray(prob, dtype=np.float32), nan=0.0, posinf=1.0, neginf=0.0)
    return np.clip(np.rint(p * 255.0), 0, 255).astype(np.uint8)


def is_image_tif(path) -> bool:
    """True for *.tif/*.tiff whose stem does not end with _cl/_conf/_prob/_mask."""
    p = Path(path)
    return p.suffix.lower() in (".tif", ".tiff") and not p.stem.lower().endswith(AUX_SUFFIXES)


def list_images(data_dir, recursive: bool = True) -> list[Path]:
    """Sorted image GeoTIFFs under data_dir (_cl/_conf masks and our _prob/_mask outputs excluded)."""
    d = Path(data_dir)
    it = d.rglob("*") if recursive else d.glob("*")
    # lane L26: Path.is_file() is False for Windows reserved names (aux.tif, con.tif, nul.tif, com1.tif, ...)
    # -> such files were dropped silently; check through the \\?\ prefix instead
    return sorted(p for p in it if is_image_tif(p) and os.path.isfile(win_path(p)))


def is_dir(path) -> bool:
    """os.path.isdir through win_path() (works for reserved-name components such as 'aux')."""
    return os.path.isdir(win_path(path))


def bands_dict(arr: np.ndarray, names: list[str]) -> dict[str, np.ndarray]:
    """(C,H,W) + band names -> {normalized band name: (H,W) view}."""
    if arr.shape[0] != len(names):
        raise ValueError(f"array has {arr.shape[0]} bands but {len(names)} channel names given")
    return {normalize_band(n): arr[i] for i, n in enumerate(names)}


# ---------------------------------------------------------------- MARIDA layout helpers

def marida_patch_paths(marida_root, patch: str) -> dict[str, Path]:
    """Paths {'image','cl','conf'} of a MARIDA patch '11-6-18_16PCC_0' (S2_ prefix / .tif allowed)."""
    from .splits import parse_patch

    pid = parse_patch(patch)
    folder = Path(marida_root) / "patches" / pid.scene
    base = f"{pid.scene}_{pid.index}"
    return {"image": folder / f"{base}.tif", "cl": folder / f"{base}_cl.tif", "conf": folder / f"{base}_conf.tif"}


def read_marida_patch(marida_root, patch: str) -> dict:
    """{'image': (11,256,256) float32 rhorc (NaN possible), 'cl': (H,W) uint8 | None,
    'conf': (H,W) uint8 | None, 'profile': dict, 'channels': list[str]}."""
    paths = marida_patch_paths(marida_root, patch)
    img, prof = read_raster(paths["image"])
    out = {"image": img, "profile": prof, "channels": channel_names("marida")}
    for k in ("cl", "conf"):
        out[k] = read_mask(paths[k]) if exists(paths[k]) else None
    return out
