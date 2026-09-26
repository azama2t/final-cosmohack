"""Canonical label scheme (INBOX §30 п.4, L123): material / form / context / sensor + the original label next to it.

data/labels_map.csv is the single source of truth; this module loads and validates it. Rules checked here:
  * enums: material, form, context, sensor, role, origin, material_confidence;
  * material is filled only for role item|aggregation (a ship, a cloud or sea foam is not a material);
  * form only where the source labelled a form, and NEVER for sensor=satellite (no «bottles» on Sentinel-2);
  * sea foam (MARIDA/MADOS «Foam», background) is never a material; polystyrene («foam plastics») is plastic;
  * material_confidence=sure only when the source label names the material explicitly; a material inferred from the
    form (buoy -> plastic) is «unsure»;
  * a dataset must not be mapped «everything = plastic» unless every source label names plastic explicitly
    (Winans 2023: FORBIDDEN, §30 п.4).
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MAP_CSV = ROOT / "data" / "labels_map.csv"

MATERIALS = ("plastic", "organic", "metal", "other", "unknown")
MATERIAL_RU = {"plastic": "пластик", "organic": "органика (дерево, растения, животные)", "metal": "металл",
               "other": "другое (резина, стекло, ткань)", "unknown": "не определён"}
FORMS = ("bottle", "bag", "film", "packet", "container", "fragment", "hard_item", "foam_item", "net", "rope",
         "buoy", "tire", "wood", "vegetation", "animal", "clothing", "glass_item", "metal_item", "vessel")
CONTEXTS = ("floating_water", "river", "shore", "ship", "wake", "cloud", "sunglint")
SENSORS = ("satellite", "VHR", "aerial", "UAV", "ship", "bridge")
ROLES = ("item", "aggregation", "background", "negative")
ORIGINS = ("anthropogenic", "natural", "unknown")
CONFIDENCE = ("sure", "unsure")
COLUMNS = ("dataset", "dataset_ref", "sensor", "source_label_id", "source_label", "n", "n_unit", "n_detail",
           "role", "material", "material_confidence", "origin", "form", "context", "used_by", "note")


def load_map(path=None) -> list[dict]:
    p = Path(path) if path else MAP_CSV
    with p.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def validate(rows: list[dict]) -> list[str]:
    errs = []
    if rows and tuple(rows[0].keys()) != COLUMNS:
        errs.append(f"колонки {tuple(rows[0].keys())} != {COLUMNS}")
    seen = set()
    for i, r in enumerate(rows, 2):
        where = f"строка {i} ({r.get('dataset')} / {r.get('source_label')})"
        key = (r["dataset"], r["source_label"])
        if key in seen:
            errs.append(f"{where}: дубль")
        seen.add(key)
        if not r["source_label"]:
            errs.append(f"{where}: нет исходной метки")
        if r["sensor"] not in SENSORS:
            errs.append(f"{where}: sensor {r['sensor']!r}")
        if r["role"] not in ROLES:
            errs.append(f"{where}: role {r['role']!r}")
        if r["context"] not in CONTEXTS:
            errs.append(f"{where}: context {r['context']!r}")
        if r["origin"] not in ORIGINS:
            errs.append(f"{where}: origin {r['origin']!r}")
        if r["form"] and r["form"] not in FORMS:
            errs.append(f"{where}: form {r['form']!r}")
        if r["n"] and not r["n"].isdigit():
            errs.append(f"{where}: n {r['n']!r}")
        if r["role"] in ("item", "aggregation"):
            if r["material"] not in MATERIALS:
                errs.append(f"{where}: material {r['material']!r}")
            if r["material_confidence"] not in CONFIDENCE:
                errs.append(f"{where}: material_confidence {r['material_confidence']!r}")
        elif r["material"] or r["material_confidence"]:
            errs.append(f"{where}: у фона/ложного объекта не бывает материала")
        if r["sensor"] == "satellite" and r["form"]:
            errs.append(f"{where}: на спутнике форма предмета не определяется (никаких «бутылок» на S2)")
        if r["sensor"] == "satellite" and r["role"] == "item":
            errs.append(f"{where}: на спутнике нет отдельных предметов — только скопление (aggregation) или фон")
        lab = r["source_label"].lower()
        if lab.strip() == "foam" and r["material"]:
            errs.append(f"{where}: морская пена — фон, не материал (пенопласт ≠ пена)")
        if "foam plastic" in lab and r["material"] != "plastic":
            errs.append(f"{where}: пенопласт — материал plastic")
        if r["material_confidence"] == "sure" and r["material"] in ("plastic", "metal") and not _names_material(lab, r["material"]):
            errs.append(f"{where}: material {r['material']} выведен из формы — должен быть unsure")
    errs += _no_all_plastic(rows)
    return errs


_MATERIAL_WORDS = {"plastic": ("plastic", "pet bottle"), "metal": ("metal",)}


def _names_material(label: str, material: str) -> bool:
    lab = label + " "
    return any(w in lab for w in _MATERIAL_WORDS.get(material, ()))


def _no_all_plastic(rows) -> list[str]:
    errs = []
    by = {}
    for r in rows:
        if r["role"] == "item":
            by.setdefault(r["dataset"], []).append(r)
    for ds, rs in by.items():
        if len(rs) > 1 and all(r["material"] == "plastic" for r in rs) and \
                not all(_names_material(r["source_label"].lower(), "plastic") for r in rs):
            errs.append(f"{ds}: все метки сведены к «пластик» без явного указания материала в метке — запрещено")
    return errs


def lookup(dataset: str, source_label: str, rows=None) -> dict | None:
    for r in rows if rows is not None else load_map():
        if r["dataset"] == dataset and r["source_label"] == source_label:
            return r
    return None


def dataset_rows(dataset: str, rows=None) -> list[dict]:
    return [r for r in (rows if rows is not None else load_map()) if r["dataset"] == dataset]


def material_shares(dataset: str, rows=None) -> dict:
    """Share of labelled items per canonical material in a dataset (from the n column; units must be items)."""
    rs = [r for r in dataset_rows(dataset, rows) if r["role"] == "item" and r["n"]]
    tot = sum(int(r["n"]) for r in rs)
    out = {}
    for r in rs:
        out[r["material"]] = out.get(r["material"], 0) + int(r["n"])
    return {k: v / tot for k, v in sorted(out.items())} if tot else {}
