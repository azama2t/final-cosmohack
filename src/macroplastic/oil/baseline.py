"""Простой порог из литературы: Oil Spill Index (OSI) для Sentinel-2.

OSI = (B3 + B4) / B2  — Rajendran S. et al. (2021) «Sentinel-2 image transformation methods for mapping oil spill —
A case study with Wakashio oil spill in the Indian Ocean, off Mauritius», MethodsX 8:101327
(doi:10.1016/j.mex.2021.101327); скрипт Sentinel Hub «OSI — Oil Spill Index».
Пятно у авторов выделяется визуально/порогом на сцене; у нас порог и знак (пятно ярче или темнее воды)
выбираются только на val (configs/oil_eval.yaml), затем замораживаются.
"""
from __future__ import annotations

import numpy as np

B2, B3, B4 = 1, 2, 3  # индексы в порядке BANDS11 (B1, B2, B3, B4, ...)


def osi(b2: np.ndarray, b3: np.ndarray, b4: np.ndarray) -> np.ndarray:
    b2 = np.asarray(b2, np.float32)
    out = np.full(b2.shape, np.nan, np.float32)
    np.divide(np.asarray(b3, np.float32) + np.asarray(b4, np.float32), b2, out=out, where=np.abs(b2) > 1e-6)
    return out


def osi_score(bands11: np.ndarray, sign: int = 1) -> np.ndarray:
    """Оценка «нефтяности» по OSI для строк (N, >=4) или стека (11, H, W) в порядке BANDS11.
    sign=+1: пятно — высокий OSI; sign=-1: пятно — низкий OSI. NaN -> -inf (никогда не нефть)."""
    a = np.asarray(bands11)
    if a.ndim == 2:  # rows
        v = osi(a[:, B2], a[:, B3], a[:, B4])
    else:
        v = osi(a[B2], a[B3], a[B4])
    v = v * float(sign)
    return np.where(np.isfinite(v), v, -np.inf).astype(np.float32)
