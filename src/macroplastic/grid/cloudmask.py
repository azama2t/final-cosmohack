"""Cloud / cloud-shadow guards for detections (cloud guards, reports/cloud_edge.md).

Two failure modes seen on live L2A scenes (29 scenes, 2123 components):
  * SCL misses part of the cloud: bright, SWIR-bright cloud pixels are labelled water (SCL 6). E.g. durban
    2019-04-24 (MDD, 942 px on a cloud body), honduras 2026-05-30 (1.1 % of water is such cloud).
    -> `spectral_cloud()`: water pixels with B2 >= 0.06 and B11 >= 0.03 (white in the visible AND bright in SWIR;
       clear/turbid water has B11 < 0.01), morphological opening, blobs >= 100 px. Treated as not observed.
  * Cloud shadows are not in SCL either; MDD flags dark shadow patches as debris (honduras 2026-05-30 zones
    No. 1-2, 685 + 230 px). Floating material raises NIR above the surrounding water; a shadow lowers everything.
    -> `shadow_components()`: component median B8 <= median B8 of its water ring (3..10 px) AND the ring's
       visible brightness (B2+B3+B4) < 0.8 x scene water median. Such components are dropped.
Plus a Euclidean buffer: components with any pixel within `CLOUD_BUFFER_PX` of a cloud/shadow pixel
(SCL 3, 8, 9, 10 or spectral cloud) are dropped (`near_cloud_components()`).
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

CLOUD_SCL = (3, 8, 9, 10)  # cloud shadow, cloud medium/high probability, thin cirrus
CLOUD_B2_MIN, CLOUD_B11_MIN, CLOUD_MIN_PX = 0.06, 0.03, 100
CLOUD_BUFFER_PX = 5
SHADOW_RING = (3, 10)
SHADOW_DARK_RATIO = 0.8


def spectral_cloud(b2: np.ndarray, b11: np.ndarray, water: np.ndarray, b2_min: float = CLOUD_B2_MIN,
                   b11_min: float = CLOUD_B11_MIN, min_px: int = CLOUD_MIN_PX) -> np.ndarray:
    """Cloud pixels that SCL left as water: bright in B2 and B11, opened (1 iteration), blobs >= min_px."""
    b2, b11 = np.nan_to_num(b2), np.nan_to_num(b11)
    c = (b2 >= b2_min) & (b11 >= b11_min) & np.asarray(water, bool)
    if not c.any():
        return c
    c = ndimage.binary_opening(c, iterations=1)
    lab, n = ndimage.label(c)
    if n == 0:
        return c
    keep = np.bincount(lab.ravel(), minlength=n + 1) >= int(min_px)
    keep[0] = False
    return keep[lab]


def near_cloud_components(labels: np.ndarray, n: int, cloud: np.ndarray,
                          buffer_px: float = CLOUD_BUFFER_PX) -> np.ndarray:
    """bool[n]: component k+1 has a pixel within `buffer_px` (Euclidean, pixel centres) of a cloud pixel."""
    if n == 0 or buffer_px <= 0 or not np.asarray(cloud).any():
        return np.zeros(n, bool)
    dist = ndimage.distance_transform_edt(~np.asarray(cloud, bool))
    near = (dist <= float(buffer_px)) & (labels > 0)
    return np.bincount(labels[near].ravel(), minlength=n + 1)[1:] > 0


def shadow_components(labels: np.ndarray, n: int, b2, b3, b4, b8, water: np.ndarray,
                      ring=SHADOW_RING, dark_ratio: float = SHADOW_DARK_RATIO) -> np.ndarray:
    """bool[n]: component k+1 looks like a cloud-shadow artefact (no NIR excess over its ring, ring darkened)."""
    if n == 0:
        return np.zeros(0, bool)
    water = np.asarray(water, bool)
    b8 = np.nan_to_num(b8)
    vis = np.nan_to_num(b2) + np.nan_to_num(b3) + np.nan_to_num(b4)
    if not water.any():
        return np.zeros(n, bool)
    scene_vis = float(np.median(vis[water]))
    r_in, r_out = ring
    inner = ndimage.grey_dilation(labels, size=(2 * r_in + 1,) * 2)
    outer = ndimage.grey_dilation(labels, size=(2 * r_out + 1,) * 2)
    ring_lab = np.where((inner == 0) & water, outer, 0)
    idx = np.arange(1, n + 1)
    has_ring = np.bincount(ring_lab.ravel(), minlength=n + 1)[1:] > 0
    s8 = np.asarray(ndimage.median(b8, labels, idx))
    r8 = np.asarray(ndimage.median(b8, ring_lab, idx)) if has_ring.any() else np.full(n, np.nan)
    rv = np.asarray(ndimage.median(vis, ring_lab, idx)) if has_ring.any() else np.full(n, np.nan)
    with np.errstate(invalid="ignore"):
        out = has_ring & (s8 <= r8) & (rv < dark_ratio * scene_vis)
    return np.nan_to_num(out, nan=0).astype(bool)


def drop_components(labels: np.ndarray, n: int, drop: np.ndarray) -> tuple[np.ndarray, int]:
    """Relabel `labels` without the components flagged in `drop` (bool[n])."""
    if n == 0 or not np.asarray(drop).any():
        return labels, n
    keep = np.concatenate([[False], ~np.asarray(drop, bool)])
    remap = np.zeros(n + 1, np.int32)
    remap[keep] = np.arange(1, int(keep.sum()) + 1, dtype=np.int32)
    return remap[labels], int(keep.sum())
