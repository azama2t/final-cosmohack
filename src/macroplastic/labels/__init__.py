"""Canonical label scheme (material / form / context / sensor) and the «состав по классам» rule — INBOX §30 п.4, L123.

data/labels_map.csv — mapping of source labels (FML, Winans 2023, TOCL RMS, UCWD, ADIS, MARIDA, MADOS, ...) to the
canonical scheme; docs/LABELS.md — description; composition.composition_for — gate for per-material counts in the
photo counter; targets.TARGETS — «целевая совокупность = материал + размерный класс + единица».
"""
from .composition import NOT_DETERMINED, RULE_TEXT, SATELLITE_TEXT, accepted_classes, composition_for  # noqa: F401
from .schema import (CONTEXTS, FORMS, MAP_CSV, MATERIALS, SENSORS, load_map, lookup, material_shares,  # noqa: F401
                     validate)
