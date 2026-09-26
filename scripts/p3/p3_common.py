"""П3 (L129): общие части — признаки окна 15 × 15 (обучение на синтетике и применение к реальным сценам одним кодом).

Вход признаков: окна (n, 11, 15, 15) отражения S2 L2A в порядке BANDS11 (MARIDA: B1 B2 B3 B4 B5 B6 B7 B8 B8A B11 B12).
Признаки центрального пикселя — только относительные (контраст к медиане окна), чтобы уровень фона (L2A/ACOLITE,
разные сцены) не был подсказкой. Список — configs/p3_synth.yaml model.features.
"""
from __future__ import annotations

import numpy as np

BANDS11 = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
BI = {b: i for i, b in enumerate(BANDS11)}
LIVE12 = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"]
IDX12_TO_11 = [LIVE12.index(b) for b in BANDS11]
W = 15
C = W // 2
L4, L8, L11 = 664.8, 832.9, 1612.05
# нативное разрешение каналов (м) — для рендера PSF
NATIVE = {"B1": 60, "B2": 10, "B3": 10, "B4": 10, "B5": 20, "B6": 20, "B7": 20, "B8": 10, "B8A": 20, "B11": 20, "B12": 20}
GROUP = {10: [BI[b] for b in BANDS11 if NATIVE[b] == 10], 20: [BI[b] for b in BANDS11 if NATIVE[b] == 20],
         60: [BI[b] for b in BANDS11 if NATIVE[b] == 60]}


def fdi(x, axis=1):
    """FDI (Biermann 2020, как в src/macroplastic/features/pixel.py). x: (..., 11 по axis, ...)."""
    b6 = np.take(x, BI["B6"], axis)
    b8 = np.take(x, BI["B8"], axis)
    b11 = np.take(x, BI["B11"], axis)
    return b8 - (b6 + (b11 - b6) * (L8 - L4) / (L11 - L4) * 10)


def ndvi(b8, b4):
    with np.errstate(invalid="ignore", divide="ignore"):
        return (b8 - b4) / (b8 + b4 + 1e-6)


FEATURE_NAMES = ([f"d_{b}" for b in BANDS11] + [f"d3_{b}" for b in BANDS11] +
                 ["max3_dB8", "dFDI", "d3_FDI", "max3_dFDI", "FDI_c", "NDVI_c", "dNDVI",
                  "mad_B4", "mad_B8", "mad_B11", "mad_FDI", "z_B8", "z_FDI", "z3_FDI", "med_B3", "med_B8"])


def features(win: np.ndarray) -> np.ndarray:
    """win: (n, 11, 15, 15) float32 -> (n, F) float32."""
    win = np.asarray(win, np.float32)
    n = win.shape[0]
    flat = win.reshape(n, 11, -1)
    med = np.nanmedian(flat, axis=2)                                   # (n, 11)
    d = win - med[:, :, None, None]
    dc = d[:, :, C, C]
    d3 = np.nanmean(d[:, :, C - 1:C + 2, C - 1:C + 2].reshape(n, 11, -1), axis=2)
    max3_b8 = np.nanmax(d[:, BI["B8"], C - 1:C + 2, C - 1:C + 2].reshape(n, -1), axis=1)
    F = fdi(win, 1)                                                     # (n, 15, 15)
    Fmed = np.nanmedian(F.reshape(n, -1), axis=1)
    dF = F - Fmed[:, None, None]
    dFc = dF[:, C, C]
    d3F = np.nanmean(dF[:, C - 1:C + 2, C - 1:C + 2].reshape(n, -1), axis=1)
    max3F = np.nanmax(dF[:, C - 1:C + 2, C - 1:C + 2].reshape(n, -1), axis=1)
    Fc = F[:, C, C]
    nd_c = ndvi(win[:, BI["B8"], C, C], win[:, BI["B4"], C, C])
    nd_m = ndvi(med[:, BI["B8"]], med[:, BI["B4"]])

    def mad(a):  # a: (n, m)
        m = np.nanmedian(a, axis=1)
        return 1.4826 * np.nanmedian(np.abs(a - m[:, None]), axis=1) + 1e-5

    mb4 = mad(flat[:, BI["B4"]])
    mb8 = mad(flat[:, BI["B8"]])
    mb11 = mad(flat[:, BI["B11"]])
    mF = mad(F.reshape(n, -1))
    X = np.concatenate([dc, d3, np.stack([max3_b8, dFc, d3F, max3F, Fc, nd_c, nd_c - nd_m, mb4, mb8, mb11, mF,
                                          dc[:, BI["B8"]] / mb8, dFc / mF, d3F / mF,
                                          med[:, BI["B3"]], med[:, BI["B8"]]], 1)], 1)
    return X.astype(np.float32)


def windows_at(bands11: np.ndarray, rows, cols) -> np.ndarray:
    """bands11 (11, H, W) -> окна (n, 11, 15, 15) с центром в (row, col); вне кадра — NaN."""
    H, Wd = bands11.shape[1:]
    pad = np.pad(bands11, ((0, 0), (C, C), (C, C)), constant_values=np.nan)
    rows = np.asarray(rows) + C
    cols = np.asarray(cols) + C
    out = np.empty((len(rows), 11, W, W), np.float32)
    for k, (r, c) in enumerate(zip(rows, cols)):
        out[k] = pad[:, r - C:r + C + 1, c - C:c + C + 1]
    return out
