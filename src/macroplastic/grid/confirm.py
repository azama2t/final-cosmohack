"""Cross-model confirmation of detections ("уверенная находка", rule D of reports/model_agreement.md).

A detection (kept 8-connected component of model A) is *confirmed* by model B when model B has at least one
pixel with P >= its own threshold on observed water within `radius_px` pixels (Euclidean disk, 2 px = 20 m at
10 m) of any pixel of the component. The partner mask is the raw thresholded mask (before component filtering),
as in reports/model_agreement.md. This is agreement between two models, not an on-site check.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

CONFIRM_RADIUS_PX = 2


def disk(radius_px: int) -> np.ndarray:
    r = int(radius_px)
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    return (yy * yy + xx * xx) <= r * r


def confirmed_components(labels: np.ndarray, n: int, partner_raw: np.ndarray,
                         radius_px: int = CONFIRM_RADIUS_PX) -> np.ndarray:
    """bool array of length n: component k+1 of `labels` touches the partner mask dilated by a disk of radius_px."""
    if n == 0:
        return np.zeros(0, bool)
    partner = np.asarray(partner_raw).astype(bool)
    if not partner.any():
        return np.zeros(n, bool)
    near = ndimage.binary_dilation(partner, structure=disk(radius_px)) if radius_px > 0 else partner
    hit = np.bincount(labels[near & (labels > 0)].ravel(), minlength=n + 1)[1:]
    return hit > 0


def label_of_records(labels: np.ndarray, records: list[dict]) -> list[int]:
    """Component label of each detection record (via its max-prob pixel, which lies inside the component)."""
    return [int(labels[r["pixel"][0], r["pixel"][1]]) for r in records]
