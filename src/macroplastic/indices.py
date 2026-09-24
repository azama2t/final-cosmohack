"""Spectral indices for floating debris (SPEC section 2). Inputs are reflectance (~0..1).

Every index function takes `b`: dict band name -> array ('B4', 'B8', 'B8A', 'B11'...; names are
normalized, so 'B04'/'b08' also work). For a (C,H,W) array use bands_of(arr, names) or
compute_indices(arr, names).

    from macroplastic.indices import compute_indices, INDEX_NAMES
    idx = compute_indices(arr, channel_names("marida"))   # {'FDI': (H,W), 'FAI': ..., ...}
    stack = stack_indices(arr, names)                       # (8,H,W) float32 in INDEX_NAMES order

Formulas (a / 0 -> NaN; NaN inputs propagate):
    FDI  = B8 - (B6 + (B11 - B6) * (l8 - l4) / (l11 - l4) * 10),  l4=664.8, l8=832.9, l11=1612.05 nm
           Biermann et al. 2020 (Sci. Rep. 10:5364). NOTE: the MARIDA code
           (semantic_segmentation/random_forest/engineering_patches.py, fdi()) uses l_redge = 740 (B6)
           instead of l4 = 665 in the ratio and l_nir=833, l_swir=1614. We use the paper formula;
           fdi(b, variant="marida") reproduces the MARIDA code.
    FAI  = B8 - (B4 + (B11 - B4) * (833 - 665) / (1614 - 665))         (MARIDA code, Hu 2009)
    NDVI = (B8 - B4) / (B8 + B4)
    NDWI = (B3 - B8) / (B3 + B8)
    NDMI = (B8 - B11) / (B8 + B11)
    SI   = cbrt((1 - B2) * (1 - B3) * (1 - B4))                          (shadow index, MARIDA code;
           MARIDA uses **(1/3) which gives NaN for a negative product, we use np.cbrt)
    BSI  = ((B11 + B4) - (B8 + B2)) / ((B11 + B4) + (B8 + B2))           (bare soil index, MARIDA code)
    NRD  = B8 - B4                                                       (MARIDA code)
In the MARIDA code the 1-based band 8 is B8 (842 nm) and band 10 is B11 (1600 nm); same here.
"""
from __future__ import annotations

import numpy as np

from .io import bands_dict, normalize_band

L4, L8, L11 = 664.8, 832.9, 1612.05          # Biermann 2020 (paper) wavelengths, nm
INDEX_NAMES = ("FDI", "FAI", "NDVI", "NDWI", "NDMI", "SI", "BSI", "NRD")


def _get(b: dict, name: str) -> np.ndarray:
    if name in b:
        return np.asarray(b[name], dtype=np.float32)
    for k, v in b.items():
        if normalize_band(k) == name:
            return np.asarray(v, dtype=np.float32)
    raise KeyError(f"band {name} is required (have {sorted(b)})")


def _nd(a: np.ndarray, c: np.ndarray) -> np.ndarray:
    """(a - c) / (a + c) with 0/0 -> NaN."""
    num = a - c
    den = a + c
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(den != 0, num / np.where(den != 0, den, 1), np.nan)
    return out.astype(np.float32)


def fdi(b: dict, variant: str = "paper") -> np.ndarray:
    """Floating Debris Index. variant='paper' (Biermann 2020, l4=664.8) or 'marida' (their code, 740)."""
    b6, b8, b11 = _get(b, "B6"), _get(b, "B8"), _get(b, "B11")
    if variant == "paper":
        ratio = (L8 - L4) / (L11 - L4)
    elif variant == "marida":
        ratio = (833.0 - 740.0) / (1614.0 - 740.0)
    else:
        raise ValueError("variant must be 'paper' or 'marida'")
    return (b8 - (b6 + (b11 - b6) * ratio * 10.0)).astype(np.float32)


def fai(b: dict) -> np.ndarray:
    """Floating Algae Index: B8 - (B4 + (B11 - B4) * (833 - 665) / (1614 - 665))."""
    b4, b8, b11 = _get(b, "B4"), _get(b, "B8"), _get(b, "B11")
    return (b8 - (b4 + (b11 - b4) * (833.0 - 665.0) / (1614.0 - 665.0))).astype(np.float32)


def ndvi(b: dict) -> np.ndarray:
    """(B8 - B4) / (B8 + B4)."""
    return _nd(_get(b, "B8"), _get(b, "B4"))


def ndwi(b: dict) -> np.ndarray:
    """(B3 - B8) / (B3 + B8)."""
    return _nd(_get(b, "B3"), _get(b, "B8"))


def ndmi(b: dict) -> np.ndarray:
    """(B8 - B11) / (B8 + B11)."""
    return _nd(_get(b, "B8"), _get(b, "B11"))


def si(b: dict) -> np.ndarray:
    """Shadow index cbrt((1-B2)(1-B3)(1-B4))."""
    return np.cbrt((1 - _get(b, "B2")) * (1 - _get(b, "B3")) * (1 - _get(b, "B4"))).astype(np.float32)


def bsi(b: dict) -> np.ndarray:
    """Bare soil index ((B11+B4) - (B8+B2)) / ((B11+B4) + (B8+B2))."""
    return _nd(_get(b, "B11") + _get(b, "B4"), _get(b, "B8") + _get(b, "B2"))


def nrd(b: dict) -> np.ndarray:
    """B8 - B4."""
    return (_get(b, "B8") - _get(b, "B4")).astype(np.float32)


INDEX_FUNCS = {"FDI": fdi, "FAI": fai, "NDVI": ndvi, "NDWI": ndwi, "NDMI": ndmi, "SI": si, "BSI": bsi, "NRD": nrd}


def bands_of(arr_or_dict, channel_names=None) -> dict:
    """dict passthrough, or (C,H,W) array + channel names -> band dict."""
    if isinstance(arr_or_dict, dict):
        return arr_or_dict
    if channel_names is None:
        raise ValueError("channel_names are required for an array input")
    return bands_dict(np.asarray(arr_or_dict), list(channel_names))


def compute_indices(arr_or_dict, channel_names=None, which=INDEX_NAMES) -> dict[str, np.ndarray]:
    """{index name: float32 array} for `which` (default all 8, INDEX_NAMES order)."""
    b = bands_of(arr_or_dict, channel_names)
    out = {}
    for name in which:
        if name not in INDEX_FUNCS:
            raise KeyError(f"unknown index {name!r}; known: {list(INDEX_FUNCS)}")
        out[name] = INDEX_FUNCS[name](b)
    return out


def stack_indices(arr_or_dict, channel_names=None, which=INDEX_NAMES) -> np.ndarray:
    """(len(which), H, W) float32 stack of compute_indices()."""
    d = compute_indices(arr_or_dict, channel_names, which)
    return np.stack([d[k] for k in which]).astype(np.float32)
