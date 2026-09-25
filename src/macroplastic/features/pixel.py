"""Pixel features for Marine Debris detection (lane L3).

Input: reflectance array (C, H, W) float32 ~0..1 + list of channel names (canonical
B1..B8, B8A, B9, B11, B12). Only the 11 MARIDA bands are used; extra bands (B9 of L2A)
are ignored. Output: (F, H, W) float32 feature stack; NaN where input is NaN.

Feature levels:
  "min" - 11 bands + 8 spectral indices (SPEC section 2).
  "win" - "min" + mean/std in windows 3/7/15 for B8, FDI, NDVI, NDWI
          + contrast with local "water" background (value - local median, windows 15/31).

Indices (SPEC section 2, signed off):
  FDI  = B8 - [B6 + (B11 - B6) * (l8 - l4) / (l11 - l4) * 10], l4=664.8, l8=832.9, l11=1612.05
         (Biermann 2020 paper formula; the MARIDA code uses l6=740 instead of l4 - we follow the paper)
  FAI  = B8 - [B4 + (B11 - B4) * (833 - 665) / (1614 - 665)]
  NDVI = (B8 - B4) / (B8 + B4)
  NDWI = (B3 - B8) / (B3 + B8)
  NDMI = (B8 - B11) / (B8 + B11)
  SI   = cbrt((1 - B2)(1 - B3)(1 - B4))
  BSI  = ((B11 + B4) - (B8 + B2)) / ((B11 + B4) + (B8 + B2))
  NRD  = B8 - B4

Local median ("water background") is approximated on a subsampled grid (stride f) with a
median filter of the scaled window, then nearest-upsampled - exact enough for a background
estimate and fast on 2500x2500 tiles. The same function is used in training and inference.
"""
from __future__ import annotations

from typing import Iterator, Sequence

import numpy as np
from scipy import ndimage

BANDS11 = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
INDICES = ["FDI", "FAI", "NDVI", "NDWI", "NDMI", "SI", "BSI", "NRD"]
WIN_KEYS = ["B8", "FDI", "NDVI", "NDWI"]
WIN_SIZES = [3, 7, 15]
# (key, window) for contrast = value - local median
CONTRAST = [("B8", 15), ("B8", 31), ("FDI", 15), ("FDI", 31), ("NDVI", 31)]
MAX_HALO = 32  # >= largest window radius (31 // 2) with margin for the subsampled median

L4, L8, L11 = 664.8, 832.9, 1612.05


def _canon(name: str) -> str:
    n = str(name).strip().upper()
    if n.startswith("B") and n[1:].isdigit():
        return "B" + str(int(n[1:]))  # B01 -> B1
    if n in ("B8A", "B08A"):
        return "B8A"
    return n


def select_bands(arr: np.ndarray, channel_names: Sequence[str]) -> np.ndarray:
    """Return (11, H, W) float32 in BANDS11 order; raises if a band is missing."""
    idx = {_canon(n): i for i, n in enumerate(channel_names)}
    missing = [b for b in BANDS11 if b not in idx]
    if missing:
        raise ValueError(f"missing bands {missing}; got {list(channel_names)}")
    if arr.shape[0] != len(channel_names):
        raise ValueError(f"arr has {arr.shape[0]} channels, names has {len(channel_names)}")
    return np.stack([np.asarray(arr[idx[b]], dtype=np.float32) for b in BANDS11], 0)


def feature_names(level: str = "win") -> list[str]:
    names = list(BANDS11) + list(INDICES)
    if level == "min":
        return names
    if level != "win":
        raise ValueError(level)
    for k in WIN_KEYS:
        for w in WIN_SIZES:
            names += [f"{k}_mean{w}", f"{k}_std{w}"]
    for k, w in CONTRAST:
        names.append(f"{k}_dmed{w}")
    return names


def _ratio(a, b):
    den = a + b
    out = np.full(a.shape, np.nan, dtype=np.float32)
    np.divide(a - b, den, out=out, where=np.abs(den) > 1e-6)
    out[np.isnan(a) | np.isnan(b)] = np.nan
    return out


def _indices(b: dict) -> dict:
    B2, B3, B4, B6, B8, B11 = b["B2"], b["B3"], b["B4"], b["B6"], b["B8"], b["B11"]
    out = {}
    out["FDI"] = B8 - (B6 + (B11 - B6) * ((L8 - L4) / (L11 - L4)) * 10.0)
    out["FAI"] = B8 - (B4 + (B11 - B4) * ((833.0 - 665.0) / (1614.0 - 665.0)))
    out["NDVI"] = _ratio(B8, B4)
    out["NDWI"] = _ratio(B3, B8)
    out["NDMI"] = _ratio(B8, B11)
    out["SI"] = np.cbrt((1.0 - B2) * (1.0 - B3) * (1.0 - B4))
    num = (B11 + B4) - (B8 + B2)
    den = (B11 + B4) + (B8 + B2)
    bsi = np.full(B2.shape, np.nan, dtype=np.float32)
    np.divide(num, den, out=bsi, where=np.abs(den) > 1e-6)
    bsi[np.isnan(num)] = np.nan
    out["BSI"] = bsi
    out["NRD"] = B8 - B4
    return {k: v.astype(np.float32, copy=False) for k, v in out.items()}


def _win_mean_std(x: np.ndarray, w: int, out_mean: np.ndarray | None = None, out_std: np.ndarray | None = None):
    """NaN-aware window mean/std (float64 sums, float32 result).

    Lane L17: same arithmetic as the first version (bit-identical output) with fewer temporaries -
    buffers are reused and the count filter of an all-valid chip is cached per (shape, w)."""
    valid = ~np.isnan(x)
    all_valid = bool(valid.all())
    xv = x.astype(np.float64)
    if not all_valid:
        xv[~valid] = 0.0
        cnt = ndimage.uniform_filter(valid.astype(np.float64), size=w, mode="reflect")
    else:
        cnt = _ones_count(x.shape, w)
    s1 = ndimage.uniform_filter(xv, size=w, mode="reflect")
    np.multiply(xv, xv, out=xv)
    s2 = ndimage.uniform_filter(xv, size=w, mode="reflect", output=xv)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.divide(s1, cnt, out=s1)
        var = np.divide(s2, cnt, out=s2)
        var -= mean * mean
    np.maximum(var, 0.0, out=var)
    bad = cnt < 1e-6
    if bad.any():
        mean[bad] = np.nan
        var[bad] = np.nan
    np.sqrt(var, out=var)
    if out_mean is None:
        return mean.astype(np.float32), var.astype(np.float32)
    out_mean[...] = mean
    out_std[...] = var
    return out_mean, out_std


_ONES_COUNT: dict = {}


def _ones_count(shape, w: int) -> np.ndarray:
    """uniform_filter of an all-ones float64 image (count of valid pixels / w^2), cached, read-only."""
    key = (tuple(shape), int(w))
    c = _ONES_COUNT.get(key)
    if c is None:
        c = ndimage.uniform_filter(np.ones(shape, np.float64), size=w, mode="reflect")
        c.setflags(write=False)
        if len(_ONES_COUNT) > 64:
            _ONES_COUNT.clear()
        _ONES_COUNT[key] = c
    return c


def _local_median(x: np.ndarray, w: int, fill=None) -> np.ndarray:
    """Approximate median in a w x w window: subsample with stride f, median filter, upsample.

    `fill` (NaN replacement, default nanmedian of x) can be passed to reuse it across windows."""
    H, W = x.shape
    f = max(1, int(round(w / 5)))
    k = max(3, int(round(w / f)) | 1)
    if fill is None:
        fill = _nan_fill(x)
    xs = x[f // 2::f, f // 2::f]
    xs = np.where(np.isnan(xs), fill, xs)
    med = ndimage.median_filter(xs, size=k, mode="reflect")
    iy = np.minimum(np.arange(H) // f, med.shape[0] - 1)
    ix = np.minimum(np.arange(W) // f, med.shape[1] - 1)
    return med[np.ix_(iy, ix)].astype(np.float32)


def _nan_fill(x: np.ndarray):
    return np.nanmedian(x) if np.isfinite(x).any() else 0.0


def compute_features(arr: np.ndarray, channel_names: Sequence[str], level: str = "win") -> np.ndarray:
    """(C,H,W) reflectance -> (F,H,W) float32 features (names: feature_names(level)).

    Features are written straight into the preallocated (F,H,W) output (lane L17: fewer copies;
    values identical to stacking the per-feature arrays)."""
    x = select_bands(arr, channel_names)
    fin = np.isfinite(x)
    if not fin.all():
        x[~fin] = np.nan
    if level not in ("win", "min"):
        raise ValueError(level)
    H, W = x.shape[1:]
    nb, ni = len(BANDS11), len(INDICES)
    F = nb + ni + (len(WIN_KEYS) * len(WIN_SIZES) * 2 + len(CONTRAST) if level == "win" else 0)
    out = np.empty((F, H, W), np.float32)
    out[:nb] = x
    b = {n: x[i] for i, n in enumerate(BANDS11)}
    ind = _indices(b)
    for j, k in enumerate(INDICES):
        out[nb + j] = ind[k]
    j = nb + ni
    if level == "win":
        src = {"B8": b["B8"], **ind}
        for k in WIN_KEYS:
            for w in WIN_SIZES:
                _win_mean_std(src[k], w, out[j], out[j + 1])
                j += 2
        fills: dict = {}
        for k, w in CONTRAST:
            if k not in fills:
                fills[k] = _nan_fill(src[k])
            np.subtract(src[k], _local_median(src[k], w, fills[k]), out=out[j])
            j += 1
    nanpix = np.isnan(x).any(0)
    if nanpix.any():
        out[:, nanpix] = np.nan
    return out


def iter_blocks(H: int, W: int, block: int = 512, halo: int = MAX_HALO):
    """Yield (y0, y1, x0, x1, hy0, hy1, hx0, hx1): core window and window with halo."""
    for y0 in range(0, H, block):
        y1 = min(H, y0 + block)
        for x0 in range(0, W, block):
            x1 = min(W, x0 + block)
            yield (y0, y1, x0, x1, max(0, y0 - halo), min(H, y1 + halo), max(0, x0 - halo), min(W, x1 + halo))


def compute_features_blocked(arr: np.ndarray, channel_names: Sequence[str], level: str = "win",
                             block: int = 512, halo: int = MAX_HALO) -> Iterator[tuple]:
    """Yield ((y0, y1, x0, x1), feats (F, y1-y0, x1-x0)) block by block (bounded memory)."""
    H, W = arr.shape[-2:]
    if H <= block and W <= block:
        yield (0, H, 0, W), compute_features(arr, channel_names, level)
        return
    for y0, y1, x0, x1, hy0, hy1, hx0, hx1 in iter_blocks(H, W, block, halo):
        f = compute_features(arr[:, hy0:hy1, hx0:hx1], channel_names, level)
        yield (y0, y1, x0, x1), f[:, y0 - hy0:y1 - hy0, x0 - hx0:x1 - hx0]
