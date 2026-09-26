"""«Состав по классам материала» for the photo counter (INBOX §30 п.4, L123).

Rule (fixed 26.09 06:20, before any material model was evaluated):
  a canonical material class M is reported as a separate number ONLY if
    1) M is labelled in the training source: in data/labels_map.csv every source label of that dataset mapped to M
       has material_confidence = sure (the label names the material; «buoy -> plastic» is a guess, not a label), and
    2) the model card carries a confusion matrix «true material × predicted material» on HELD-OUT data that is
       independent of train/val by place or session (heldout_independent = true), and
    3) on that held-out data: >= MIN_TEST_ITEMS true items of M, precision_M >= MIN_PRECISION and
       recall_M >= MIN_RECALL (material agreement among matched items, IoU >= 0.5).
  Everything else goes to «состав не определён». If no class passes, the only output is
  «всего предметов, состав не определён». Satellite: never a form or a material of items (no «bottles» on S2).

Card format (optional block of a model card, weights_exp/photo_count/*.json):
  "material_eval": {"classes": ["plastic", ...], "confusion": [[...]] (rows = true, cols = predicted),
                    "heldout": "текст: что отложено", "heldout_independent": true, "source_dataset": "Winans2023",
                    "other_source": false}
"""
from __future__ import annotations

from .schema import MATERIAL_RU, MATERIALS, load_map

MIN_TEST_ITEMS = 30
MIN_PRECISION = 0.7
MIN_RECALL = 0.7
NOT_DETERMINED = "всего предметов, состав не определён"
RULE_TEXT = (f"число по классу материала показываем, только если класс размечен в обучающем наборе и на отложенных "
             f"данных (независимых по месту/сессии) есть матрица ошибок: ≥ {MIN_TEST_ITEMS} предметов класса, "
             f"точность ≥ {MIN_PRECISION:.1f} и полнота ≥ {MIN_RECALL:.1f}; иначе — «{NOT_DETERMINED}»")
SATELLITE_TEXT = ("спутник (Sentinel-2, 10 м): только «подозрительный плавающий материал» против фона "
                  "(пена, водоросли, кильватер, суда, облака); форма предмета и материал по снимку не определяются")

WHY_SINGLE_CLASS = {
    "water_camera": "модель обучена на одном классе «garbage» (FML): материал в разметке не указан",
    "aerial": "8 исходных классов Winans 2023 объединены в один «предмет»: матрицы ошибок по материалу нет",
}


def class_metrics(classes: list[str], confusion: list[list[int]]) -> dict:
    """Per class: n_true (row sum), n_pred (col sum), precision, recall."""
    k = len(classes)
    out = {}
    for i, c in enumerate(classes):
        tp = confusion[i][i]
        n_true = sum(confusion[i])
        n_pred = sum(confusion[j][i] for j in range(k))
        out[c] = {"n_true": int(n_true), "n_pred": int(n_pred),
                  "precision": tp / n_pred if n_pred else None, "recall": tp / n_true if n_true else None}
    return out


def labelled_materials(dataset: str, rows=None) -> set:
    """Materials explicitly labelled in a dataset: every item row with this material is material_confidence=sure."""
    rs = [r for r in (rows if rows is not None else load_map()) if r["dataset"] == dataset and r["role"] == "item"]
    mats = {r["material"] for r in rs}
    return {m for m in mats if all(r["material_confidence"] == "sure" for r in rs if r["material"] == m)}


def accepted_classes(material_eval: dict | None, rows=None) -> tuple[list[str], dict]:
    """-> (classes that pass the rule, {class: reason it fails}). Unknown is never a reported class."""
    if not material_eval:
        return [], {}
    src = material_eval.get("source_dataset")
    if not src:
        return [], {"*": "не указан обучающий набор (source_dataset) — нельзя проверить, размечен ли материал"}
    labelled = labelled_materials(src, rows)
    if not material_eval.get("heldout_independent"):
        return [], {"*": "матрица ошибок не на отложенных данных (независимых по месту/сессии)"}
    classes, conf = material_eval.get("classes") or [], material_eval.get("confusion") or []
    if len(conf) != len(classes) or any(len(r) != len(classes) for r in conf):
        return [], {"*": "матрица ошибок неполная"}
    ok, why = [], {}
    for c, m in class_metrics(classes, conf).items():
        if c not in MATERIALS or c == "unknown":
            why[c] = "не класс материала"
        elif c not in labelled:
            why[c] = f"материал не размечен в {src} (выведен из формы предмета)"
        elif m["n_true"] < MIN_TEST_ITEMS:
            why[c] = f"на отложенных данных {m['n_true']} предметов (< {MIN_TEST_ITEMS})"
        elif (m["precision"] or 0) < MIN_PRECISION or (m["recall"] or 0) < MIN_RECALL:
            why[c] = f"точность {m['precision'] or 0:.2f} / полнота {m['recall'] or 0:.2f} ниже порога"
        else:
            ok.append(c)
    return ok, why


def composition_for(survey: str, boxes=None, card: dict | None = None, box_materials: list | None = None,
                    sensor: str | None = None) -> dict:
    """Composition block for the API answer (called by service/routes_v3_photo.py, L109).

    boxes — counted boxes (any sequence; only its length is used for "total");
    box_materials — predicted canonical material per counted box (None/"unknown" if the box has none), same order;
    used only if the card passes the rule. Returns status "by_class" | "not_determined" | "not_applicable".
    """
    if boxes is not None and box_materials is not None and len(box_materials) != len(boxes):
        raise ValueError("box_materials и boxes разной длины")
    base = {"rule": RULE_TEXT, "unit": "штук на кадр"}
    if sensor == "satellite" or survey == "satellite":
        return {**base, "status": "not_applicable", "text": SATELLITE_TEXT, "classes": [], "box_materials": None}
    me = (card or {}).get("material_eval")
    ok, why = accepted_classes(me)
    n = len(boxes) if boxes is not None else (len(box_materials) if box_materials is not None else None)
    if not ok or box_materials is None:
        reason = WHY_SINGLE_CLASS.get(survey, "у модели нет матрицы ошибок по материалу на отложенных данных")
        if me and not ok:
            reason = "ни один класс материала не прошёл правило: " + "; ".join(f"{k}: {v}" for k, v in why.items())
        return {**base, "status": "not_determined", "text": NOT_DETERMINED, "reason": reason, "total": n,
                "classes": [], "box_materials": None}
    mets = class_metrics(me["classes"], me["confusion"])
    per = [m if m in ok else None for m in box_materials]
    classes = [{"material": c, "label": MATERIAL_RU[c], "count": sum(1 for m in per if m == c),
                "precision": round(mets[c]["precision"], 3), "recall": round(mets[c]["recall"], 3),
                "n_heldout": mets[c]["n_true"]} for c in ok]
    und = sum(1 for m in per if m is None)
    scope = me.get("heldout", "отложенные данные")
    if not me.get("other_source"):
        scope += "; на другом источнике не проверено"
    return {**base, "status": "by_class", "text": f"по классам материала (проверено: {scope})", "total": n,
            "classes": classes, "by_class": {**{c["label"]: c["count"] for c in classes}, "состав не определён": und},
            "undetermined": und, "undetermined_label": "состав не определён",
            "not_reported": why, "box_materials": per}
