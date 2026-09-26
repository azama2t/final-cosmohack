r"""Раздел «Расследование данных» (§11) для reports/final_numbers.json → case.sections.

Собирает числа только из файлов в git (офлайн):
  reports/search/{s1,s2,s3,s4}_candidates.csv, reports/search/adis_candidates.csv (копия data/search/adis/candidates.csv),
  reports/search/adis.md (доля покрытия предметами — нужен Objects.csv ADIS, которого нет в git),
  reports/extra_data/{registry.csv, registry_negatives.csv, registry_cozar2024.csv.gz, field_candidates.csv,
                      content_overlap.csv, current_detector_zero_shot.csv, fp_current_detector.json},
  reports/detector_v2/{unet_baseline.json, unet_test.json, experiments.json[, decision.json]},
  reports/quantity/*.json, reports/oil/{val_runs.json, test.json, control.json}, docs/img/funnel.json.

Разделы (ключи case.sections): search, labeled_data, adis_pairs, baselines, quantity, oil, detector_v2.
У каждого раздела — source и protocol. Нет файла → раздел с available: false (документы пишут «—»).

    .venv\Scripts\python.exe scripts\case\collect_search.py            # числа -> reports/search/search_numbers.json
    .venv\Scripts\python.exe scripts\case\collect_search.py --sync     # + копия ADIS, снимок detector_v2, вырезки в docs/img/

Импортируется из scripts/final_numbers.py (collect()) и вызывается из scripts/case/run_all.py (шаг 5b_search_numbers).
Уровни доказательности — docs/INDEX.md: A (снимок ↔ независимое полевое число), B (подтверждённая разметка),
C (кандидат), D (отвергнутый/отрицательный). Здесь ничего не калибруется и масса не считается.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REP = ROOT / "reports"
OUT_JSON = REP / "search" / "search_numbers.json"
ADIS_SRC = ROOT / "data" / "search" / "adis" / "candidates.csv"
ADIS_CSV = REP / "search" / "adis_candidates.csv"
IMG = ROOT / "docs" / "img"
V2_LIVE = REP / "detector_v2" / "experiments.json"
V2_SNAPSHOT = REP / "detector_v2" / "experiments_snapshot.json"

SOURCES = {"S1": "S1_GPGP2018", "S2": "S2_SARGASSO_MSM41", "S3": "S3_SE_NORTH_SEA", "S4": "S4_BLACK_SEA_DOORS3"}
SEARCH_NAMES = {"S1": "S1 Тихий океан, аэросъёмка 2016 (L88)", "S2": "S2 Саргассово море, MSM41 (L98)",
                "S3": "S3 Северное море, HE419/HE460 (L86)", "S4": "S4 Чёрное море, DOORS 2024 (L87)",
                "ADIS": "ADIS, The Ocean Cleanup — найден нами, не в CSV (L91, L100)"}
# причины уровня D → короткие категории (первое совпадение по порядку)
REASON_CATS = [
    ("пена/барашки", r"whitecap|foam"),
    ("маршрут вне снимка", r"outside the scene footprint|footprint"),
    ("снимка нет", r"no landsat scene|no optical scene|no scene|0 scenes"),
    ("датчик не видит предметы", r"ocn product|sensor unsuitable|300 m pixel|sar imagette|context only"),
    ("дрейф больше допуска", r"^drift|drift out of|drifted"),
]
PIX_CATS = {"glint": "блик", "cloud": "облака", "insufficient_coverage": "край снимка / покрытие"}


def _r(x, nd=4):
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return round(f, nd)


def _i(x):
    try:
        return int(x) if x is not None and not (isinstance(x, float) and math.isnan(x)) else None
    except (TypeError, ValueError):
        return None


def _load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _csv(p: Path, **kw):
    import pandas as pd
    if not p.is_file():
        return None
    return pd.read_csv(p, low_memory=False, **kw)


def _rel(p: Path) -> str:
    return str(p.relative_to(ROOT)).replace("\\", "/")


def reason_category(text) -> str:
    t = str(text or "").lower()
    for name, pat in REASON_CATS:
        if re.search(pat, t):
            return name
    m = re.search(r"(glint|cloud|insufficient_coverage)", t)
    if m:
        return PIX_CATS[m.group(1)]
    return "прочее"


def _levels(df) -> dict:
    vc = df["level"].value_counts().to_dict() if df is not None and "level" in df else {}
    return {lv: int(vc.get(lv, 0)) for lv in ("A", "B", "C", "D")}


def _d_reasons(df, col) -> dict:
    if df is None or col not in df:
        return {}
    d = df[df["level"] == "D"]
    vc = d[col].map(reason_category).value_counts()
    return {k: int(v) for k, v in vc.items()}


def _ci_wilson(k, n, z=1.96):
    if not n:
        return [None, None]
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, c - h), 3), round(min(1.0, c + h), 3)]


# ------------------------------------------------------------------------------------------------ search
def collect_search() -> dict:
    fun = _load_json(ROOT / "docs" / "img" / "funnel.json") or {}
    stages = fun.get("stages") or []
    ev_by = (stages[0].get("by_source") if stages else None) or {}
    out = {"available": bool(stages), "source": "reports/search/{s1,s2,s3,s4}_candidates.csv, reports/search/adis_candidates.csv, "
           "docs/img/funnel.json",
           "protocol": "розыск снимков под события CSV организаторов (L86–L88, L98) и под отрезки ADIS (L100); уровень "
                       "доказательности A–D у каждой строки; стоп-правило §11: нет A или убедительного C за пилот — поиск "
                       "по дрейфу не расширяем; дрейф — область поиска, не доказательство",
           "events_total": stages[0].get("total") if stages else None,
           "funnel": [{"label": s.get("label"), "total": s.get("total")} for s in stages],
           "funnel_quality_reject": fun.get("quality_reject_reasons") or {}, "by_source": {}}
    reason_col = {"S1": "reason", "S2": "reason", "S3": "reason", "S4": "why"}
    scene_col = {"S1": "scene_id", "S2": "scene_id", "S3": "scene_id", "S4": "best_scene"}
    tot = {"A": 0, "B": 0, "C": 0, "D": 0}
    for key, src in SOURCES.items():
        df = _csv(REP / "search" / f"{key.lower()}_candidates.csv")
        if df is None:
            out["by_source"][key] = {"available": False}
            continue
        lv = _levels(df)
        for k in tot:
            tot[k] += lv[k]
        sc = df[scene_col[key]].dropna() if scene_col[key] in df else []
        rec = {"available": True, "name": SEARCH_NAMES[key], "file": f"reports/search/{key.lower()}_candidates.csv",
               "events_in_csv": ev_by.get(src), "rows": int(len(df)), "events_checked": int(df["event_id"].nunique()),
               "scenes": int(len(set(sc))), **lv, "d_reasons": _d_reasons(df, reason_col[key])}
        if key == "S3" and "kind" in df:
            rec["d_by_kind"] = {k: int(v) for k, v in df[df.level == "D"]["kind"].value_counts().items()}
        if key == "S4" and "n_det_strip" in df:
            c = df[df.level == "C"]
            rec["c_det_in_strip"] = int(c["n_det_strip"].fillna(0).sum())
            rec["items_km2_max"] = _r(df["items_km2"].max(), 1)
        out["by_source"][key] = rec
    a = _csv(ADIS_CSV)
    if a is not None:
        lv = _levels(a)
        for k in tot:
            tot[k] += lv[k]
        out["by_source"]["ADIS"] = {"available": True, "name": SEARCH_NAMES["ADIS"], "file": _rel(ADIS_CSV),
                                    "events_in_csv": 0, "rows": int(len(a)), "events_checked": int(a["event_id"].nunique()),
                                    "scenes": int(a["scene_id"].nunique()), **lv, "d_reasons": _d_reasons(a, "reason")}
    else:
        out["by_source"]["ADIS"] = {"available": False}
    out["totals"] = tot
    out["csv_A"] = sum((out["by_source"].get(k) or {}).get("A", 0) or 0 for k in SOURCES)
    out["csv_C"] = sum((out["by_source"].get(k) or {}).get("C", 0) or 0 for k in SOURCES)
    return out


# ------------------------------------------------------------------------------------------------ labeled data
def collect_labeled() -> dict:
    out = {"source": "reports/extra_data/registry.csv (L89), registry_negatives.csv (L90), registry_cozar2024.csv.gz (L98), "
                     "content_overlap.csv, current_detector_zero_shot.csv, fp_current_detector.json",
           "protocol": "B — подтверждённая разметка скопления (без числа предметов), D — проверенный отрицательный пример; "
                       "пиксели — S2 L2A той же съёмки (тот же ридер, что у live-сцен); утечка = та же съёмка (тайл+дата) или те же "
                       "пиксели (LBP) с MARIDA/MADOS/нашими сценами; текущий детектор weights/lgbm без дообучения, порог с MARIDA val"}
    r = _csv(REP / "extra_data" / "registry.csv")
    if r is not None:
        r = r.assign(fam=r.source.astype(str).str.startswith("PLP").map({True: "PLP", False: "FO"}),
                     acq=r.tile.astype(str) + "_" + r.date.astype(str))
        excl = r.overlap_ours.astype(str).str.startswith("EXCLUDE")
        ok = r[~excl]
        b = ok[ok.level == "B"]
        out["plp"] = {"acq_B": int(b[b.fam == "PLP"].acq.nunique()), "kind": "искусственные мишени (Лесбос)",
                      "license": "CC-BY-4.0 / не указана (зеркало MIT marinedebrisdetector)"}
        out["floatingobjects"] = {"acq_B": int(b[b.fam == "FO"].acq.nunique()),
                                  "regions": int(b[b.fam == "FO"].scene_id.astype(str).str.split("_").str[0].nunique()),
                                  "kind": "естественные скопления (FloatingObjects + Refined)",
                                  "license": "Apache-2.0 / MIT"}
        out["b_new_acq"] = out["plp"]["acq_B"] + out["floatingobjects"]["acq_B"]
        out["acq_with_D"] = int(ok[ok.level == "D"].acq.nunique())
        out["dup_excluded"] = int(r[excl].acq.nunique())
        out["registry_rows"] = int(len(r))
    z = _csv(REP / "extra_data" / "registry_cozar2024.csv.gz")
    if z is not None:
        out["cozar"] = {"windows": int(len(z)), "acq": int((z.tile.astype(str) + "_" + z.date.astype(str)).nunique()),
                        "tiles": int(z.tile.nunique()), "px": int(z.n_pixels_fil.sum()), "km2": _r(z.area_m2.sum() / 1e6, 2),
                        "date_min": str(z.date.min()), "date_max": str(z.date.max()),
                        "same_acq_marida_ours": int(z.marida_same_scene.notna().sum() + z.ours_same_scene.notna().sum()),
                        "license": "CC BY 4.0", "kind": "нити плавучего материала Средиземного моря (Cózar et al. 2024)"}
    n = _csv(REP / "extra_data" / "registry_negatives.csv")
    if n is not None:
        v = n[n.source.astype(str).str.startswith("finland")]
        c = n[n.source.astype(str).str.startswith("cloud_mask")]
        px = c.groupby("klass").n_px.sum().to_dict()
        out["vessels"] = {"acq": int((v.tile.astype(str) + v.date.astype(str)).nunique()), "boxes": int(len(v)),
                          "license": "CC-BY-4.0"}
        out["clouds"] = {"scenes": int(c.scene.nunique()), "px_cloud": _i(px.get("облако")), "px_shadow": _i(px.get("тень облака")),
                         "px_water": _i(px.get("чистая вода (фон)")), "license": "CC-BY-4.0"}
        out["neg_sources_checked"] = int(n.source.nunique())
        out["neg_sources_rejected"] = int(n.status.astype(str).str.startswith("отклон").sum())
        out["neg_leaks"] = int((n.leak.astype(str) == "да").sum())
    fp = _load_json(REP / "extra_data" / "fp_current_detector.json") or {}
    if fp:
        hull = ((fp.get("sets") or {}).get("vessels_fi") or {}).get("ship_hull (B8_dmed15>0.01)") or {}
        cl = ((fp.get("sets") or {}).get("clouds_cmc") or {}).get("cloud") or {}
        bl = ((fp.get("box_level") or {}).get("vessels_fi")) or {}
        out.setdefault("vessels", {}).update({"hull_px": _i(hull.get("n")), "boxes_flagged": _i(bl.get("flagged")),
                                              "boxes_flagged_pct": _r(100 * (bl.get("frac") or 0), 1)})
        out.setdefault("clouds", {}).update({"cloud_px_flagged": _i(cl.get("fp"))})
        out["threshold"] = fp.get("threshold")
    co = _csv(REP / "extra_data" / "content_overlap.csv")
    if co is not None:
        ctl = co.image.astype(str).str.contains("data/live")
        out["content_checked"] = int((~ctl).sum())
        out["content_match"] = int(((co.verdict.astype(str).str.startswith("MATCH")) & ~ctl).sum())
        out["content_control_votes"] = _i(co[ctl].votes.max()) if ctl.any() else None
    zs = _csv(REP / "extra_data" / "current_detector_zero_shot.csv")
    if zs is not None:
        al = zs[zs.region == "ALL"].set_index("kind")

        def g(kind):
            return (_r(100 * al.loc[kind, "share_p_ge_thr"], 1), _i(al.loc[kind, "n"])) if kind in al.index else (None, None)
        out["zero_shot"] = {"refined_B_recall_pct": g("refined_pos")[0], "refined_B_px": g("refined_pos")[1],
                            "fo_line_recall_pct": g("fo_line")[0], "plp_plastic_recall_pct": g("plp_plastic_frac>=0.5")[0],
                            "refined_D_px": g("refined_neg")[1], "refined_D_flagged_pct": g("refined_neg")[0]}
    out["leaks_total"] = (out.get("neg_leaks") or 0) + (out.get("content_match") or 0) + \
        ((out.get("cozar") or {}).get("same_acq_marida_ours") or 0)
    out["available"] = r is not None or n is not None or z is not None
    return out


# ------------------------------------------------------------------------------------------------ ADIS pairs
def _adis_md_numbers() -> dict:
    p = REP / "search" / "adis.md"
    if not p.is_file():
        return {}
    t = p.read_text(encoding="utf-8")
    res = {}
    m = re.search(r"\|dt\| ≤ ([\d.]+) ч и облачность сцены ≤ (\d+) %", t)
    if m:
        res["dt_max_h"], res["cloud_max_pct"] = float(m.group(1)), int(m.group(2))
    m = re.search(r"площадь предметов сверху в A-отрезках ([\d.]+) м² на ([\d.]+) км² → доля покрытия ([\d.eE+-]+);", t)
    if m:
        res["items_area_m2"], res["coverage_frac"] = float(m.group(1)), float(m.group(3))
    m = re.search(r"крупнейший предмет ([\d.]+) м² = ([\d.]+) % пикселя", t)
    if m:
        res["largest_item_m2"], res["largest_item_pct_px"] = float(m.group(1)), float(m.group(2))
    return res


def collect_adis(wind_split: float = 7.0) -> dict:
    import pandas as pd
    a = _csv(ADIS_CSV)
    out = {"available": a is not None, "source": f"{_rel(ADIS_CSV)} (копия data/search/adis/candidates.csv, L100), reports/search/adis.md",
           "protocol": "отрезки ADIS 10 км (камера судна, счёт >5/>10/>50 см, площадь обследования) × Sentinel-2 L2A; "
                       "A = |dt| и дрейф в допуске, пиксели полосы годны по маскам качества, площадь и размерные классы из ADIS; "
                       "детектор weights/lgbm без гармонизации, те же маски и код, что у пар CSV (pair_quality.process_s2); "
                       "TP/FP по пикселям не утверждаются: пара A даёт только «на снимке N срабатываний при полевых M шт./км²»"}
    if a is None:
        return out
    for k in (">5cm", ">10cm", ">50cm"):
        a[k] = a.field_count_by_size.astype(str).str.extract(re.escape(k) + r":(\d+)")[0].astype(float).fillna(0)
    A = a[a.level == "A"]
    Ai = A[A[">5cm"] > 0]
    Z = A[A[">5cm"] == 0]
    mid = pd.to_datetime(A.seg_mid_utc, format="mixed", utc=True).dt.round("20min")
    out.update({"segments": int(len(a)), "scenes": int(a.scene_id.nunique()), **_levels(a),
                "d_reasons": _d_reasons(a, "reason"),
                "A_passes": int(A.assign(_m=mid).groupby(["ship", "_m"]).ngroups), "A_dt_le1h": int((A.dt_h.abs() <= 1).sum()),
                "A_ships": int(A.ship.nunique()),
                "A_survey_km2": _r(A.survey_area_km2.sum(), 1), "A_strip_usable_km2": _r(A.strip_usable_km2.sum(), 1),
                "A_items_gt5": int(A[">5cm"].sum()), "A_items_gt50": int(A[">50cm"].sum()),
                "A_with_items": int(len(Ai)),
                "A_with_items_det_px": int(Ai.det_pixels.fillna(0).sum()),
                "A_with_items_det_shifted": int(Ai.n_det_shifted.fillna(0).sum()),
                "A_with_items_density_min": _r(Ai.dhat_5cm_km2.min(), 1), "A_with_items_density_max": _r(Ai.dhat_5cm_km2.max(), 1),
                "A_ship_found": int((A.ship_found == True).sum()),  # noqa: E712
                "A_ship_along_median_m": _r(A[A.ship_found == True].ship_along_m.abs().median(), 0),  # noqa: E712
                "A_zero": int(len(Z)), "wind_split_ms": wind_split})
    # правило студии (L95, src/macroplastic/case/illumination.py): низкое солнце или слабый сигнал воды -> детектор не оценивается;
    # такие пары A остаются связью «полевое число ↔ снимок», но в «0 при N» и в оценку ложных тревог не входят
    ev = a["detector_evaluable"].astype(str).str.lower().eq("true") if "detector_evaluable" in a else pd.Series(True, index=a.index)
    E = A[ev.loc[A.index]]
    Ei = E[E[">5cm"] > 0]
    Z = E[E[">5cm"] == 0]
    out.update({"evaluable_rule": "детектор не оценивается при зените Солнца ≥ 58° или медиане B3 воды < 0.003 (правило студии)",
                "A_eval": int(len(E)), "A_not_eval": int(len(A) - len(E)), "A_with_items_eval": int(len(Ei)),
                "A_with_items_eval_det_px": int(Ei.det_pixels.fillna(0).sum()),
                "A_with_items_eval_det_shifted": int(Ei.n_det_shifted.fillna(0).sum()),
                "A_with_items_eval_density_min": _r(Ei.dhat_5cm_km2.min(), 1), "A_with_items_eval_density_max": _r(Ei.dhat_5cm_km2.max(), 1),
                "A_eval_survey_km2": _r(E.survey_area_km2.sum(), 1), "A_eval_items_gt5": int(E[">5cm"].sum()),
                "A_zero_eval": int(len(Z)), "segments_not_eval": int((~ev).sum()),
                "scenes_not_eval": int(a[~ev].scene_id.nunique())})
    for name, m in (("calm", Z.wind_ms < wind_split), ("windy", Z.wind_ms >= wind_split)):
        d = Z[m]
        su, zu = float(d.strip_usable_km2.fillna(0).sum()), float(d.zone_usable_km2.fillna(0).sum())
        px = float(d.det_px_strip_excl_ship.fillna(0).sum())
        zn = float(d.n_det_zone_excl_ship.fillna(0).sum())
        out[f"fa_{name}"] = {"pairs": int(len(d)), "strip_km2": _r(su, 1), "px": int(px), "px_per_km2": _r(px / su, 1) if su else None,
                             "zone_km2": _r(zu, 0), "zone_obj": int(zn), "zone_obj_per_km2": _r(zn / zu, 2) if zu else None}
    out.update(_adis_md_numbers())
    if out.get("coverage_frac"):
        m, e = f"{out['coverage_frac']:.1e}".split("e")
        sup = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")
        out["coverage_frac_text"] = f"{m}·10{str(int(e)).translate(sup)}"
    out["verdict"] = ("пары A есть, но все вида «0 на снимке при ≈ 0–4 шт./км²» — это предел обнаружения Sentinel-2 для "
                      "предметов 5 см – 5 м в открытом море, калибровку «снимок → шт./км²» на них построить нельзя")
    return out


# ------------------------------------------------------------------------------------------------ baselines
def collect_baselines() -> dict:
    t = _load_json(REP / "detector_v2" / "unet_test.json") or {}
    v = _load_json(REP / "detector_v2" / "unet_baseline.json") or {}
    out = {"available": bool(t or v), "source": "reports/detector_v2/unet_test.json, unet_baseline.json (L99)",
           "protocol": "официальные веса U-Net MARIDA (Kikaki et al. 2022, эпоха 44), код и предобработка авторов, не переобучали; "
                       "та же функция оценки, что у LightGBM (detector_compare.evaluate); порог U-Net — только по val; "
                       "test MARIDA посчитан один раз; ДИ и парная разность — бутстреп по сценам"}
    tt = t.get("test") or {}

    def row(d):
        d = d or {}
        return {"f1": _r(d.get("f1_md"), 3), "ci95": [_r(x, 3) for x in (d.get("ci95_f1") or [])] or None,
                "precision": _r(d.get("precision_md"), 3), "recall": _r(d.get("recall_md"), 3)}
    if tt:
        out["test"] = {k: row(tt.get(k)) for k in ("lgbm", "rf_argmax", "unet_argmax", "unet_prob")}
        pt = (t.get("paired_test") or {}).get("lgbm_vs_unet_argmax") or {}
        out["test_lgbm_minus_unet"] = _r(pt.get("delta_f1"), 3)
        out["test_lgbm_minus_unet_ci95"] = [_r(x, 3) for x in (pt.get("ci95") or [])] or None
        out["test_scenes_lgbm_better"] = pt.get("scenes_a_better")
        out["test_n_scenes"] = (tt.get("lgbm") or {}).get("n_scenes")
        out["test_ship_fp_unet"] = (((tt.get("unet_argmax") or {}).get("per_class") or {}).get("Ship") or {}).get("pred_md")
        out["test_ship_fp_lgbm"] = (((tt.get("lgbm") or {}).get("per_class") or {}).get("Ship") or {}).get("pred_md")
    vv = v.get("val") or {}
    if vv:
        out["val"] = {k: row(vv.get(k)) for k in ("lgbm", "rf_argmax", "unet_argmax")}
    pc = v.get("pairs_case") or {}
    if pc:
        out["pairs_unet_obj"] = ((pc.get("totals") or {}).get("argmax") or {}).get("n_obj")
        out["pairs_unet_in_strip"] = ((pc.get("totals") or {}).get("argmax") or {}).get("n_in_strip")
        out["pairs_lgbm_obj"] = (pc.get("lgbm_current") or {}).get("n_obj")
        out["pairs_crops"] = (pc.get("lgbm_current") or {}).get("n_crops")
    return out


# ------------------------------------------------------------------------------------------------ quantity
def collect_quantity() -> dict:
    q = REP / "quantity"
    fld, cal, cl, cp, sc, af = (_load_json(q / f"{n}.json") or {} for n in
                                ("field", "calibration", "coverage_live", "coverage_pairs", "scenario", "adis_field"))
    out = {"available": bool(fld or cal), "source": "reports/quantity/{field,calibration,coverage_live,coverage_pairs,scenario,"
                                                     "adis_field}.json (L93), docs/QUANTITY.md",
           "protocol": "раздельно: полевая плотность C = N/A (шт./км², интервал Пуассона) и спутниковая доля покрытия "
                       "подозрительного материала (м² маски на км² пригодной воды, как LWD у Cózar et al. 2024); доля покрытия ≠ "
                       "мусор и ≠ шт./км²; связь между ними — только калибровка на парах A, которой нет; сценарий "
                       "«площадь / размер предмета» — помеченный исследовательский диапазон, на карту не идёт"}
    p = next((x for x in (fld.get("profiles") or []) if x.get("source_id") == "S2_SARGASSO_MSM41"), {})
    out["field_S2"] = {"n": _i(p.get("n_events")), "pooled_N": _i(p.get("pooled_N")), "pooled_A_km2": _r(p.get("pooled_A_km2"), 2),
                       "pooled_C": _r(p.get("pooled_C"), 1), "lo95": _r(p.get("pooled_lo95"), 1), "hi95": _r(p.get("pooled_hi95"), 1)}
    cur = cl.get("current") or {}
    out["coverage_live"] = {"n_scenes": cl.get("n_scenes"), "nonzero": cur.get("scenes_nonzero"),
                            "ppm_median": _r(cur.get("ppm_median"), 1), "ppm_max": _r(cur.get("ppm_max"), 0),
                            "thr_ratio_median": _r((cl.get("threshold_ratio_max_over_min") or {}).get("median"), 0)}
    ds = cal.get("detect_share") or {}
    out["pairs"] = {"strips": ds.get("strips_accept_with_detector"), "strips_nonzero": ds.get("strips_accept_nonzero"),
                    "p_nonzero_upper95": _r(ds.get("p_nonzero_upper95"), 2),
                    "survey_multiplier": _r(1 / ds["p_nonzero_upper95"], 1) if ds.get("p_nonzero_upper95") else None}
    scen = cal.get("scenarios") or []
    n2 = [s.get("n_factor_2") for s in scen if s.get("n_factor_2") is not None]
    fl = cal.get("floor_no_satellite") or {}
    out["calibration"] = {"model": "ln C = ln k + b · ln S", "n_pairs_k_x2_min": min(n2) if n2 else None,
                          "n_pairs_k_x2_max": max(n2) if n2 else None,
                          "n_pairs_r05": (cal.get("n_pairs_correlation") or {}).get("r0.5"),
                          "n_pairs_r03": (cal.get("n_pairs_correlation") or {}).get("r0.3"),
                          "floor_factor": _r(fl.get("best_pi95_factor"), 1), "median_factor": _r(fl.get("median_baseline_pi95_factor"), 1),
                          "pairs_A_with_S_pos": 0}
    one = ((sc.get("one_minimal_object_200m2") or {}).get("size_class_bounds_S2")) or {}
    out["scenario"] = {"label": sc.get("label"), "mask_m2": _r(one.get("area_m2"), 0), "items_min": _i(one.get("items_min")),
                       "items_max": _i(one.get("items_max"))}
    fvd = sc.get("field_vs_detection") or {}
    f = fvd.get("factor_vs_field_median")
    out["detection_factor_vs_median"] = _r(f[0] if isinstance(f, list) else f, -3)
    out["adis_rate_ge20m2_per_km2"] = _r(af.get("rate_items_ge20m2_per_km2"), 4)
    return out


# ------------------------------------------------------------------------------------------------ oil
def collect_oil() -> dict:
    v = _load_json(REP / "oil" / "val_runs.json") or {}
    t = _load_json(REP / "oil" / "test.json") or {}
    c = _load_json(REP / "oil" / "control.json") or []
    out = {"available": bool(t), "experimental": True, "enabled_by_default": False,
           "source": "reports/oil/{val_runs,test,control}.json (L101), configs/oil_eval.yaml, docs/OIL.md",
           "protocol": "MADOS класс Oil Spill, сплит по сценам заморожен до обучения (без съёмок MARIDA val/test); отдельная "
                       "голова LightGBM, основной детектор не тронут; выбор только по val, test открыт один раз; бейзлайн — порог "
                       "OSI = (B3+B4)/B2; положительный контроль на S2 L2A — разлив Wakashio 2020; единица — площадь, км², "
                       "не объём и не масса"}
    model = t.get("model")
    vm = ((v.get(model) or {}).get("val") or {}) if model else {}
    vo = ((v.get("baseline_osi") or {}).get("val") or {})
    tl = ((t.get("lgbm") or {}).get("test") or {})
    to = ((t.get("baseline_osi") or {}).get("test") or {})

    def f1(d):
        return _r((d.get("pooled") or {}).get("f1"), 3)

    def ci(d):
        return [_r(x, 3) for x in ((d.get("ci95") or {}).get("f1") or [])] or None
    out.update({"model": model, "val_f1": f1(vm), "val_f1_ci95": ci(vm), "val_osi_f1": f1(vo),
                "test_f1": f1(tl), "test_f1_ci95": ci(tl), "test_precision": _r((tl.get("pooled") or {}).get("precision"), 2),
                "test_recall": _r((tl.get("pooled") or {}).get("recall"), 2), "test_iou": _r((tl.get("pooled") or {}).get("iou"), 2),
                "test_osi_f1": f1(to), "val_oil_scenes": vm.get("n_oil_scenes"), "test_oil_scenes": tl.get("n_oil_scenes")})
    ctl = {str(x.get("key", "")).split(".")[-1]: x for x in c if isinstance(x, dict)}
    if ctl:
        ks = sorted(ctl)
        out["wakashio"] = [{"date": k, "oil_km2": _r(ctl[k].get("oil_km2"), 2), "n_spills": ctl[k].get("n_spills")} for k in ks]
    out["verdict"] = ("на разметке MADOS голова лучше порога OSI, но на реальных L2A ложные срабатывания (края облаков, дымка, "
                      "рябь), а известный разлив Wakashio не найден — слой только «эксперимент», выключен по умолчанию")
    return out


# ------------------------------------------------------------------------------------------------ detector v2 (slot)
def collect_detector_v2() -> dict:
    # зафиксированный снимок (копирует `--sync`), а не живой файл L92: иначе числа меняются при каждой записи экспериментов
    e = _load_json(V2_SNAPSHOT) or {}
    dec = _load_json(REP / "detector_v2" / "decision.json") or {}  # слот: решение оркестратора о новой модели
    out = {"available": bool(e), "source": "reports/detector_v2/experiments_snapshot.json (снимок experiments.json L92), "
                                           "reports/detector_v2/decision.json (решение оркестратора), configs/detector_v2_eval.yaml",
           "snapshot_generated": e.get("generated"),
           "protocol": "проверка зафиксирована до экспериментов (sha256 в experiments.json): MARIDA val + 3-fold по снимкам для "
                       "новых B/D; 3 seed; правило принятия записано заранее (ΔF1 val ≥ max(0.01, 2σ) и не хуже на D/B); "
                       "test MARIDA не читался; порог каждой модели — по val"}
    if not e:
        out["current_model"] = "weights/lgbm"
        return out
    ex = e.get("experiments") or []
    cands = [x for x in ex if x.get("kind") == "lightgbm retrain" and x.get("exp") != "r0"]
    decs = [(x.get("decision") or {}) for x in cands]
    acc = [x.get("exp") for x, d in zip(cands, decs) if str(d.get("decision", "")).startswith("ACCEPT")]
    dfs = [d.get("dF1_vs_ref") for d in decs if d.get("dF1_vs_ref") is not None]
    out.update({"n_variants": len(cands), "n_seeds": max((x.get("n_seeds") or 0) for x in cands) if cands else None,
                "n_accept": len(acc), "accepted": acc, "need_df1": (decs[0].get("need") if decs else None),
                "df1_min": _r(min(dfs), 3) if dfs else None, "df1_max": _r(max(dfs), 3) if dfs else None,
                "eval_sha256_short": (e.get("eval_yaml_sha256") or "")[:12] or None})
    ref = next((x for x in ex if x.get("exp") == "reference"), {})

    def src(x, key, what):
        return ((((x.get("extra") or [{}])[0].get("none") or {}).get("by_src") or {}).get(f"{key}:{what}")) or {}
    for name, x in (("reference", ref), ("vessels_d", next((y for y in ex if y.get("exp") == "vesD_w03"), {})),
                    ("mix", next((y for y in ex if y.get("exp") == "l2a_mix"), {}))):
        if not x:
            continue
        cz, vs = src(x, "features_cozar2024_l2a", "B_recall"), src(x, "features_vessels_fi", "D_false_alarm")
        pdn = (x.get("pairs_D_none") or [{}])[0]
        out[name] = {"cozar_hit": cz.get("flagged"), "cozar_n": cz.get("n_objects"), "cozar_pct": _r(100 * (cz.get("rate") or 0), 0),
                     "vessels_fa": vs.get("flagged"), "vessels_n": vs.get("n_objects"), "vessels_pct": _r(100 * (vs.get("rate") or 0), 0),
                     "pairs_d_fa": pdn.get("flagged"), "pairs_d_n": pdn.get("n"),
                     "df1": _r(((x.get("decision") or {}).get("dF1_vs_ref")), 3)}
    val = (ref.get("val_per_seed") or [{}])[0] if ref.get("val_per_seed") else {}
    out["reference_val_f1"] = _r(val.get("f1"), 3)
    if dec.get("accepted") is True:
        out.update({"orchestrator_accepted": True, "current_model": dec.get("model"), "decision_note": dec.get("note")})
    else:
        out.update({"orchestrator_accepted": False, "current_model": "weights/lgbm",
                    "decision_note": "ни один вариант не прошёл заранее записанное правило — в сервисе остаётся откат weights/lgbm"
                    if not acc else "есть кандидаты по правилу; решение оркестратора ещё не записано — в сервисе weights/lgbm"})
    return out


def collect() -> dict:
    """Все разделы расследования данных → для case.sections (final_numbers.py)."""
    return {"search": collect_search(), "labeled_data": collect_labeled(), "adis_pairs": collect_adis(),
            "baselines": collect_baselines(), "quantity": collect_quantity(), "oil": collect_oil(),
            "detector_v2": collect_detector_v2()}


# ------------------------------------------------------------------------------------------------ sync (not offline-only)
def sync_v2_snapshot() -> str:
    """reports/detector_v2/experiments.json (живой файл L92) → experiments_snapshot.json (зафиксированный источник документов)."""
    if not V2_LIVE.is_file():
        return "нет reports/detector_v2/experiments.json — снимок не менялся"
    try:
        live = json.loads(V2_LIVE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001  (L92 может писать файл прямо сейчас)
        return f"experiments.json не читается ({type(e).__name__}) — снимок не менялся"
    if not isinstance(live, dict) or not live.get("experiments"):
        return "experiments.json без экспериментов — снимок не менялся"
    if V2_SNAPSHOT.is_file() and _load_json(V2_SNAPSHOT) == live:
        return "снимок detector_v2 актуален"
    V2_SNAPSHOT.write_text(json.dumps(live, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return f"снимок detector_v2 обновлён ({live.get('generated')}, экспериментов {len(live['experiments'])})"


def sync_adis() -> str:
    """data/search/adis/candidates.csv (не в git) → reports/search/adis_candidates.csv (в git)."""
    if not ADIS_SRC.is_file():
        return "нет data/search/adis/candidates.csv — оставлена копия в reports/search"
    if ADIS_CSV.is_file() and ADIS_CSV.read_bytes() == ADIS_SRC.read_bytes():
        return "копия ADIS актуальна"
    shutil.copyfile(ADIS_SRC, ADIS_CSV)
    return f"скопировано {_rel(ADIS_SRC)} → {_rel(ADIS_CSV)}"


CROPS = {  # лучшие вырезки розыска → уменьшенные копии в docs/img (в git)
    "search_adis_pair.jpg": ["data/search/adis/best/02_335573_A_panel.png"],
    "search_s4_t30.jpg": ["data/search/s4/crops/S4_DOORS3_T30/S2B_36TUN_20240617_0_L2A/rgb.png",
                          "data/search/s4/crops/S4_DOORS3_T30/S2B_36TUN_20240617_0_L2A/swir.png",
                          "data/search/s4/crops/S4_DOORS3_T30/S2B_36TUN_20240617_0_L2A/mask.png"],
    "search_s3_he460.jpg": ["data/search/s3/pairs/S3_HE460_MarLitter_transect03__S2A_MSIL2A_20160411T105022_R051_T32ULF_20210211T031140/panel.png"],
}


def sync_images(width: int = 1600) -> list[str]:
    from PIL import Image
    done = []
    for dst, srcs in CROPS.items():
        ps = [ROOT / s for s in srcs]
        if not all(p.is_file() for p in ps):
            continue
        ims = [Image.open(p).convert("RGB") for p in ps]
        if len(ims) > 1:
            h = min(im.height for im in ims)
            ims = [im.resize((round(im.width * h / im.height), h)) for im in ims]
            canvas = Image.new("RGB", (sum(im.width for im in ims) + 8 * (len(ims) - 1), h), (16, 18, 22))
            x = 0
            for im in ims:
                canvas.paste(im, (x, 0))
                x += im.width + 8
            im = canvas
        else:
            im = ims[0]
        if im.width > width:
            im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        im.save(IMG / dst, "JPEG", quality=82, optimize=True)
        done.append(dst)
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Раздел «Расследование данных» → reports/search/search_numbers.json")
    ap.add_argument("--sync", action="store_true", help="скопировать ADIS из data/search и сделать вырезки в docs/img")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    if a.sync:
        print("[collect_search]", sync_adis())
        print("[collect_search]", sync_v2_snapshot())
        print("[collect_search] вырезки:", ", ".join(sync_images()) or "исходников нет (data/search не в git)")
    res = collect()
    OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    s, ad = res["search"], res["adis_pairs"]
    print(f"[collect_search] -> {_rel(OUT_JSON)}: поиск A/B/C/D {s.get('totals')}; ADIS A {ad.get('A')} "
          f"(с предметами {ad.get('A_with_items')}, пикселей {ad.get('A_with_items_det_px')}); B новых съёмок "
          f"{res['labeled_data'].get('b_new_acq')}; U-Net test F1 {((res['baselines'].get('test') or {}).get('unet_argmax') or {}).get('f1')}; "
          f"детектор v2 принято {res['detector_v2'].get('n_accept')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
