"""Бейзлайн б3 — FDI-порог × площадь.

Пиксели ROI, где FDI (Biermann 2020) выше медианы FDI воды окна на 0.03 → площадь «плавающего» → N = n × 561 [470; 670]
(та же калибровка на пиксель, что у б1). Порог 0.03 выбран до прогона (порядок FDI-контраста сработавших мишеней PLP, L115).
"""
import numpy as np

from lib import ITEMS_PER_PX_HI, ITEMS_PER_PX_LO, ITEMS_PER_PX_MID, fdi, water_mask

NAME = "b3_fdi_area"
THR = 0.03


def predict(window_bands, meta):
    f = fdi(window_bands)
    w = water_mask(window_bands) & np.isfinite(f)
    ref = float(np.nanmedian(f[w])) if w.sum() >= 50 else float(np.nanmedian(f))
    n = int(((f - ref > THR) & meta["roi"] & np.isfinite(f)).sum())
    return n * ITEMS_PER_PX_MID, n * ITEMS_PER_PX_LO, n * ITEMS_PER_PX_HI
