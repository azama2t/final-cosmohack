"""Бейзлайн б1 — плоская калибровка PLP (§34 п.2, configs/zone_estimate.yaml, L131).

N = n_пикселей_детектора в ROI × 561 [470; 670] предметов на пиксель; детектор weights/lgbm без гармонизации, P ≥ 0.63.
Числа 470/670 — пиксели мишеней PLP2019 18.04 и 18.05.2019 (это даты набора A1: утечка, б1 на A1 оптимистичен).
"""
import numpy as np

from lib import ITEMS_PER_PX_HI, ITEMS_PER_PX_LO, ITEMS_PER_PX_MID, detector_prob, detector_threshold

NAME = "b1_flat_plp"


def predict(window_bands, meta):
    p = detector_prob(window_bands)
    n = int(((p >= detector_threshold()) & meta["roi"]).sum())
    return n * ITEMS_PER_PX_MID, n * ITEMS_PER_PX_LO, n * ITEMS_PER_PX_HI
