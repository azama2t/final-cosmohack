"""Класс «Нефтяное пятно» (INBOX §14, L101) — отдельная экспериментальная бинарная голова «нефть».

Основной детектор мусора (weights/lgbm) не затрагивается. Здесь только общий код (тесты: tests/test_oil.py):
  * metrics:  P/R/F1/IoU по пикселям, счётчики по сценам, бутстреп-ДИ по сценам;
  * baseline: OSI = (B3 + B4) / B2 — Oil Spill Index (Rajendran et al. 2021, MethodsX 8:101327,
              «Sentinel-2 image transformation methods for mapping oil spill — Wakashio, Mauritius»);
  * spills:   маска нефти -> полигоны (GeoJSON, EPSG:4326) с площадью км² (площадь, не объём/масса).
Обучение/оценка/инференс — scripts/oil/train_oil.py; состав проверки — configs/oil_eval.yaml.
"""
from __future__ import annotations

from .metrics import binary_metrics, bootstrap_ci, scene_counts, pooled_from_counts  # noqa: F401
from .baseline import osi, osi_score  # noqa: F401
from .spills import (CLASS_ID, CLASS_LABEL, UNIT_NOTE, spill_features, component_mask,  # noqa: F401
                     feature_collection, csv_rows, CSV_FIELDS)
