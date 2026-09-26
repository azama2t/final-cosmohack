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
V2_PAIRS_D = REP / "detector_v2" / "pairs_d_scenes.json"          # снимок: на скольких съёмках срабатывания на D пар
AUDIT_COZAR = REP / "audit" / "cozar_check.json"                    # аудит L107: полнота нитей Cózar при разных порогах

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
    af0 = _load_json(REP / "quantity" / "adis_field.json") or {}
    vhr = _load_json(ROOT / "docs" / "research" / "marine_quantity" / "results" / "vhr_adis_summary.json") or {}
    out["adis_segments_total"] = af0.get("n_segments")
    out["adis_segments_with_items"] = vhr.get("positive_segments")
    out["adis_vhr_same_day"] = vhr.get("matches_rows")
    if out.get("coverage_frac"):
        m, e = f"{out['coverage_frac']:.1e}".split("e")
        sup = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")
        out["coverage_frac_text"] = f"{m}·10{str(int(e)).translate(sup)}"
    # «видимый сигнал на снимке»: пары A с оцениваемым детектором и предметами в поле, где в полосе есть срабатывания
    out["A_eval_items_with_signal"] = int((Ei.det_pixels.fillna(0) > 0).sum())
    out["verdict"] = (f"пары по месту и времени, не калибровочные: на {len(Ai)} синхронных отрезках ADIS с единичными предметами детектор "
                      f"предметы не увидел (оценивается на {len(Ei)} из {len(Ai)} — на всех 0); это согласуется с физикой (доля покрытия "
                      f"~10⁻⁷), но не задаёт общий предел для всех скоплений")
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
                       "мусор и ≠ шт./км²; связь между ними — только калибровка на парах A, которой нет; перевод "
                       "площади в штуки не показывается"}
    p = next((x for x in (fld.get("profiles") or []) if x.get("source_id") == "S2_SARGASSO_MSM41"), {})
    out["field_S2"] = {"n": _i(p.get("n_events")), "pooled_N": _i(p.get("pooled_N")), "pooled_A_km2": _r(p.get("pooled_A_km2"), 2),
                       "pooled_C": _r(p.get("pooled_C"), 1), "lo95": _r(p.get("pooled_lo95"), 1), "hi95": _r(p.get("pooled_hi95"), 1),
                       "lo95_label": "только ошибка счёта (Пуассон, Гарвуд)"}
    # §28 А (Г1-2): интервал «между событиями» — scripts/case/field_interval_check.py, правило docs/PIPELINE.md C10
    fi = _load_json(q / "field_intervals.json") or {}
    if fi:
        ma, se, md = fi.get("mean_all") or {}, fi.get("single_event") or {}, fi.get("mean_dev_to_test") or {}
        ev = se.get("dev_event_quantiles_2_5_97_5") or {}
        out["field_S2"].update({
            "boot_lo95": (ma.get("bootstrap_days95") or [None, None])[0], "boot_hi95": (ma.get("bootstrap_days95") or [None, None])[1],
            "boot_label": "с разбросом между днями рейса (кластерный бутстреп, 2 000 повторов)",
            "boot_width_ratio": ma.get("width_ratio_boot_vs_garwood"),
            "nb2_lo95": ((ma.get("nb2") or {}).get("ci95") or [None, None])[0], "nb2_hi95": ((ma.get("nb2") or {}).get("ci95") or [None, None])[1],
            "nb2_alpha": (ma.get("nb2") or {}).get("alpha"),
            "test_mean": md.get("test_C"), "dev_mean": md.get("dev_C"),
            "garwood_covers_test_mean": md.get("garwood_covers_test_mean"), "boot_covers_test_mean": md.get("bootstrap_covers_test_mean"),
            "event_lo95": (ev.get("interval") or [None, None])[0], "event_hi95": (ev.get("interval") or [None, None])[1],
            "event_cov_test": ev.get("coverage"), "event_n_test": se.get("n_test"),
            "garwood_event_cov_test": (se.get("garwood95_of_dev_mean") or {}).get("coverage"),
            "nb2_event_cov_test": (se.get("nb2_predictive95") or {}).get("coverage"),
            "intervals_source": "reports/quantity/field_intervals.{json,md}",
        })
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
    out["scenario"] = {"used": False, "spread_ratio": _i(one.get("ratio_max_min")),
                       "spread_orders_text": f"{math.log10(one['ratio_max_min']):.1f}".replace(".", ",") if one.get("ratio_max_min") else None,
                       "reason": "перевод площади маски в штуки не показываем — нет калибровочных пар «снимок → шт./км²»"}
    cc = _load_json(q / "cell_calibration.json") or {}
    if cc:
        v, g = cc.get("variants") or {}, (((cc.get("variants") or {}).get("V3_climatology") or {}).get("grid_0.25") or {})
        pr = g.get("primary") or {}
        out["cell_calibration"] = {
            "source": "reports/quantity/cell_calibration.{json,md}, configs/cell_calibration.yaml (проверка записана до сравнения)",
            "stop_rule_min_cells": cc.get("stop_rule_min_cells"),
            "same_period_common_dates": ((v.get("V1_same_period") or {}).get("cells_with_field_date_inside_cozar_dates")),
            "v2_cells_with_signal": (v.get("V2_detector_same_month") or {}).get("cell_months_with_S_gt_0"),
            "v3_cells": pr.get("n_cells"), "v3_rho": _r((pr.get("spearman") or {}).get("rho"), 2),
            "v3_rho_ci95_shipday": [_r(x, 2) for x in (pr.get("spearman_ci95_shipday_cluster") or [])] or None,
            "v3_rule_passed": (cc.get("verdict") or {}).get("V3_decision_rule_passed"),
            "verdict": "калибровки по ячейкам нет: в одном периоде общих дат поле × спутник 0; климатологии разных лет ранжируют районы "
                       "слабо (ρ ≈ география — удалённость от берега), правило принятия не пройдено"}
    af2 = _load_json(q / "adis_forecast.json") or {}
    if af2:
        fl = (af2.get("field") or {}).get("gt10cm") or {}
        te = ((((af2.get("schemes") or {}).get("region") or {}).get("profiles") or {}).get("gt10cm") or {}).get("test") or {}
        m0, b1 = (te.get("M0_median") or {}).get("metrics") or {}, (te.get("B1_band_season_median") or {}).get("metrics") or {}
        dec = (af2.get("decision") or {}).get("gt10cm") or {}
        out["adis_forecast"] = {
            "source": "reports/quantity/adis_forecast.{json,md}, configs/adis_forecast.yaml (правило записано до метрик)",
            "profile": "объекты детектора ADIS > 10 см, камера судна (7 % — животное/растение по классу модели)",
            "n_segments": fl.get("n_segments"), "sum_N": _i(fl.get("sum_N")), "sum_A_km2": _r(fl.get("sum_A_km2"), 0),
            "C": _r(fl.get("C_pooled"), 2), "lo": _r(fl.get("C_pooled_lo"), 2), "hi": _r(fl.get("C_pooled_hi"), 2),
            "zero_pct": _r(100 * (fl.get("share_zero") or 0), 0), "seg_median": _r(fl.get("C_seg_median"), 1),
            "split": "регионы 10° × 10° (отложено 20 групп), вторичная схема — по судам",
            "test_n": m0.get("n"), "test_mae_median": _r(m0.get("mae"), 2), "test_rmse_median": _r(m0.get("rmse"), 2),
            "test_cov90_median": _r(100 * (m0.get("coverage90") or 0), 0), "test_mae_b1": _r(b1.get("mae"), 2),
            "final": dec.get("final"), "verdict": dec.get("why"),
            "note_zero": "медиана отрезка 0 ≠ «чисто»: 0 означает, что камера на этом отрезке предметов не насчитала"}
        ex = af2.get("external_adis_calibrated") or {}
        g10 = (ex.get("profiles") or {}).get("gt10cm") or {}
        if g10:
            out["adis_forecast"]["authors_calibrated"] = {
                "C": _r(g10.get("calibrated_C"), 2), "lo_typ": _r(g10.get("calibrated_lo_typ"), 2), "hi_typ": _r(g10.get("calibrated_hi_typ"), 1),
                "ours_raw_C": _r(g10.get("ours_raw_C"), 2), "factor_file": _r(g10.get("cal_over_observed_median"), 1),
                "source": ex.get("source"),
                "label": "калибровка авторов ADIS (по тралу), не наша; наша — без поправок",
                "caveat": "коэффициент в файле ≈ 1,5 не совпадает с приведённым в тексте статьи; приложение статьи не сверено"}
    paper = ROOT / "data" / "extra" / "cozar2024" / "paper.txt"
    q_txt = "The current matching of satellite detections and field observations is limited to fake targets (artificial LWs), and reports of dense LW sightings"
    q_short = "The current matching of satellite detections and field observations is limited to fake targets"
    out["cozar2024_quote"] = {"text": q_txt, "short": q_short, "doi": "10.1038/s41467-024-48674-7", "ref": "Cózar et al. 2024, Nat Commun, раздел «A new scenario for research and management» (PMC11178853)",
                              "checked_in_text": (q_txt in paper.read_text(encoding="utf-8")) if paper.is_file() else None}
    out["analogy"] = {"text": "пальмы на гектар по Sentinel-2 с опорой на подсчёт по снимкам высокого разрешения (arXiv 2105.11207): "
                              "MAE ±7,3 пальмы/га",
                      "why_possible": "там объект неподвижен, однороден и даёт устойчивый сигнал в пикселе, а опорный счёт совпадает со снимком",
                      "why_not_here": "мусор дрейфует (часы между снимком и полем уже сдвигают поле), разнороден по размеру и материалу и "
                                      "занимает доли процента пикселя; поэтому калибровка по ячейкам требует независимой проверки — у нас "
                                      "она дала отрицательный результат"}
    fvd = sc.get("field_vs_detection") or {}
    f = fvd.get("factor_vs_field_median")
    out["detection_factor_vs_median"] = _r(f[0] if isinstance(f, list) else f, -3)
    out["adis_rate_ge20m2_per_km2"] = _r(af.get("rate_items_ge20m2_per_km2"), 4)
    # --- для docs/QUANTITY.md (шаблон templates/docs/QUANTITY.md.tmpl)
    names = {"S2_SARGASSO_MSM41": "S2", "S1_GPGP2018": "S1", "S3_SE_NORTH_SEA": "S3", "S4_BLACK_SEA_DOORS3": "S4"}
    for pr in fld.get("profiles") or []:
        key = names.get(pr.get("source_id"))
        if not key or (key == "S1" and "aerial" in str(pr.get("profile"))):
            continue
        out.setdefault("profiles", {})[key] = {
            "profile": pr.get("profile"), "size_class": pr.get("size_class"), "n": _i(pr.get("n_events")),
            "c_median": _r(pr.get("C_median"), 1), "c_p25": _r(pr.get("C_p25"), 1), "c_p75": _r(pr.get("C_p75"), 1),
            "pooled_N": _i(pr.get("pooled_N")), "pooled_A_km2": _r(pr.get("pooled_A_km2"), 2), "pooled_C": _r(pr.get("pooled_C"), 1),
            "lo95": _r(pr.get("pooled_lo95"), 1), "hi95": _r(pr.get("pooled_hi95"), 1), "has_N": bool(pr.get("n_with_N"))}
    v2 = fld.get("variance_S2") or {}
    vd = fld.get("variance_S2_within_day") or {}
    out["variance_S2"] = {"sd_total": _r(v2.get("sd_lnC_total"), 2), "sd_poisson": _r(v2.get("sd_poisson"), 2),
                          "poisson_share_pct": _r(100 * (v2.get("poisson_share") or 0), 0), "sd_within_day": _r(vd.get("sd_extra_within_day"), 2)}
    out["adis_field"] = {"segments": af.get("n_segments"), "area_km2": _r(af.get("area_km2"), 0), "zero_share_pct": _r(100 * (af.get("zero_share") or 0), 0),
                         "pooled_items_km2": _r(af.get("pooled_items_km2_raw"), 2), "items_ge20m2": (af.get("items_by_area") or {}).get("ge_20m2")}
    out["coverage_live"].update({"ppm_p90": _r(cur.get("ppm_p90"), 1), "pooled_ppm": _r(cur.get("pooled_ppm"), 1),
                                 "thr_median_min": _r(cur.get("range_ppm_median_min"), 2), "thr_median_max": _r(cur.get("range_ppm_median_max"), 1)})
    out["calibration"]["scenarios"] = [{"sd_sat": s.get("sd_satellite_ln"), "basis": s.get("basis"), "n_x2": s.get("n_factor_2"),
                                        "n_x1_5": s.get("n_factor_1_5")} for s in scen]
    out["calibration"]["n_pairs_r07"] = (cal.get("n_pairs_correlation") or {}).get("r0.7")
    sd = sc.get("data_item_sizes") or {}
    out["scenario"].update({"adis_items_median_m2": _r((sd.get("ADIS_Objects") or {}).get("median_m2"), 2),
                            "lebreton_items_median_m2": _r((sd.get("Lebreton2018_mosaic") or {}).get("median_m2"), 2),
                            "adis_min": _i((((sc.get("one_minimal_object_200m2") or {}).get("data_sizes_ADIS_Objects")) or {}).get("items_min")),
                            "adis_max": _i((((sc.get("one_minimal_object_200m2") or {}).get("data_sizes_ADIS_Objects")) or {}).get("items_max")),
                            "fill_min": (sc.get("fill_frac") or [None, None])[0], "fill_max": (sc.get("fill_frac") or [None, None])[-1]})
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
    dec = _load_json(REP / "detector_v2" / "decision.json") or {}  # слот: решение команды о новой модели
    out = {"available": bool(e), "source": "reports/detector_v2/experiments_snapshot.json (снимок experiments.json L92), "
                                           "reports/detector_v2/decision.json (решение команды), configs/detector_v2_eval.yaml",
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
    ctrl = [x for x in ex if x.get("kind") == "lightgbm retrain" and x.get("exp") == "r0"]
    out.update({"n_variants": len(cands), "n_control": len(ctrl), "n_seeds": max((x.get("n_seeds") or 0) for x in cands) if cands else None,
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

    def foam(x):
        s = str((x.get("pairs_D_by_class_none") or {}).get("foam") or "")
        return tuple(int(v) for v in s.split("/")) if "/" in s else (None, None)
    # «естественные B без D судов/облаков» (Cózar, линии FloatingObjects): компромисс полнота ↔ ложные на сложном фоне
    nat = [x for x in cands if (x.get("train_kw") or {}).get("extra_b_w")
           and any(s.startswith(("features_cozar", "features_folines")) for s in ((x.get("train_kw") or {}).get("extra_srcs") or []))
           and not any(s.startswith(("features_vessels", "features_clouds")) for s in ((x.get("train_kw") or {}).get("extra_srcs") or []))
           and not (x.get("train_kw") or {}).get("pairs_w")]
    if nat:
        cz = [100 * (src(x, "features_cozar2024_l2a", "B_recall").get("rate") or 0) for x in nat]
        vs = [100 * (src(x, "features_vessels_fi", "D_false_alarm").get("rate") or 0) for x in nat]
        wf = [100 * sum(x.get("water_flag_none") or [0]) / max(1, len(x.get("water_flag_none") or [0])) for x in nat]
        d1 = [(x.get("decision") or {}).get("dF1_vs_ref") for x in nat]
        fm = [foam(x) for x in nat]
        out["natural_b"] = {"variants": [x.get("exp") for x in nat], "n": len(nat),
                            "cozar_pct_min": _r(min(cz), 0), "cozar_pct_max": _r(max(cz), 0),
                            "vessels_pct_min": _r(min(vs), 0), "vessels_pct_max": _r(max(vs), 0),
                            "water_pct_min": _r(min(wf), 0), "water_pct_max": _r(max(wf), 0),
                            "df1_min": _r(min(d1), 3), "df1_max": _r(max(d1), 3),
                            "foam_fa_max": max(f[0] for f in fm if f[0] is not None), "foam_n": max(f[1] for f in fm if f[1] is not None)}
    pd_ = _load_json(V2_PAIRS_D) or {}
    if pd_:
        out["reference"].update({"pairs_d_acq_flagged": pd_.get("acq_flagged"), "pairs_d_acq_total": pd_.get("acq_total")})
    c146 = src(ref, "features_cozar2024", "B_recall")
    if c146:
        out["reference"].update({"cozar146_hit": c146.get("flagged"), "cozar146_n": c146.get("n_objects")})
    au = (_load_json(AUDIT_COZAR) or {}).get("recall_36") or {}
    if au:
        out["reference"].update({"cozar_ge3px_pct": _r(100 * (au.get("recall_ge_3px") or 0), 0),
                                 "cozar_pixel_pct": _r(100 * (au.get("pixel_rate") or 0), 0)})
    fr = foam(ref)
    out["reference_foam_fa"], out["reference_foam_n"] = fr
    vd = next((y for y in ex if y.get("exp") == "vesD_w03"), {})
    if vd:
        out["vessels_d"]["foam_fa"] = foam(vd)[0]
    out["decision_recorded"] = bool(dec)
    out["decided_at"] = dec.get("decided_at")
    if dec.get("accepted") is True:
        out.update({"orchestrator_accepted": True, "current_model": dec.get("model"),
                    "decision_note": f"вариант {dec.get('model')} принят оркестратором по заранее записанному правилу"})
    else:
        out.update({"orchestrator_accepted": False, "current_model": dec.get("model") or "weights/lgbm",
                    "decision_note": (f"правило принятия (записано до экспериментов) не прошёл ни один из {len(cands)} вариантов с новыми "
                                      f"данными (×{out['n_seeds']} seed)" + (" и контрольный r0" if ctrl else "") +
                                      " → остаётся weights/lgbm") if not acc else
                    "есть кандидаты по правилу; решение команды ещё не записано — в сервисе weights/lgbm"})
    return out


GLEB = ROOT / "docs" / "research" / "gleb_quantitative_link" / "results"


def collect_independent() -> dict:
    """Независимая проверка источников количества (член команды, без наших результатов): docs/research/gleb_quantitative_link."""
    import pandas as pd
    out = {"available": (GLEB / "REPORT.md").is_file(),
           "source": "docs/research/gleb_quantitative_link/results/{REPORT.md, adis_pairs/pair_summary.csv, adis_sentinel_matches.csv, "
                     "adis_open_ocean_audit.json, plp2019/summary.json}",
           "protocol": "независимая проверка: наглядные пары ADIS ↔ S2 по предметам > 50 см (у автора «A» — хорошая полевая метка, у нас A — "
                       "пара снимок + подсчёт; выборки разные: его пары > 50 см, наши > 5 см) и попытка «спектр пикселя → доля покрытия» на "
                       "PLP2019 с проверкой leave-one-date-out"}
    if not out["available"]:
        return out
    ps = _csv(GLEB / "adis_pairs" / "pair_summary.csv")
    if ps is not None:
        out["pairs"] = {"n": int(len(ps)), "items_gt50": int(ps.n_objects_gt50cm.sum()),
                        "dt_abs_min_h": _r(ps.delta_hours.abs().min(), 1), "dt_abs_max_h": _r(ps.delta_hours.abs().max(), 1),
                        "density_min": _r(ps.raw_density_items_km2.min(), 1), "density_max": _r(ps.raw_density_items_km2.max(), 1)}
    m = _csv(GLEB / "adis_sentinel_matches.csv")
    if m is not None:
        m = m.assign(same_day=pd.to_datetime(m.field_datetime, utc=True).dt.date == pd.to_datetime(m.acquisition_datetime, utc=True).dt.date)
        out["routes_gt50"] = int(m.SegmentID.nunique())
        out["routes_same_day_s2"] = int(m[m.same_day].SegmentID.nunique())
    au = _load_json(GLEB / "adis_open_ocean_audit.json") or []
    if au:
        segs = {}
        for x in au:
            segs.setdefault(x["segment_id"], [int(x.get("count_gt50cm") or 0), 0])
            segs[x["segment_id"]][1] += int(x.get("scenes_same_day") or 0)
        out["dense_routes"] = len(segs)
        out["dense_routes_items"] = sorted((v[0] for v in segs.values()), reverse=True)
        out["dense_routes_scenes_same_day"] = sum(v[1] for v in segs.values())
    plp = _load_json(GLEB / "plp2019" / "summary.json") or {}
    if plp:
        lodo = plp.get("leave_one_date_out_fdi") or []
        out["plp"] = {"n_pixels": plp.get("rows"), "n_dates": len(lodo),
                      "rho_fdi": _r((plp.get("pooled_spearman_fdi") or {}).get("rho"), 2),
                      "rho_nir": _r((plp.get("pooled_spearman_nir") or {}).get("rho"), 2),
                      "lodo_mae_min": _r(min(x["mae_percent_points"] for x in lodo), 1) if lodo else None,
                      "lodo_mae_max": _r(max(x["mae_percent_points"] for x in lodo), 1) if lodo else None,
                      "lodo_mean_mae_min": _r(min(x["mean_baseline_mae_percent_points"] for x in lodo), 1) if lodo else None,
                      "lodo_mean_mae_max": _r(max(x["mean_baseline_mae_percent_points"] for x in lodo), 1) if lodo else None,
                      "verdict": "перевод «спектр пикселя → доля покрытия пластиком» не подтверждён: связь слабая, знак меняется по датам, "
                                 "модель по FDI на отложенной дате не лучше предсказания средним"}
    return out


def _dmy(s):
    s = str(s or "")[:10]
    return f"{s[8:10]}.{s[5:7]}.{s[:4]}" if len(s) == 10 else None


def collect_scene_zones() -> dict:
    """Слой «Спутниковые зоны» и демо на отложенной сцене (data/case/scene_zones, reports/case_demo) — для DEMO.md, деки, README."""
    idx = _load_json(ROOT / "data" / "case" / "scene_zones" / "index.json") or {}
    out = {"available": bool(idx), "source": "data/case/scene_zones/{index.json, <сцена>/zones.geojson}, reports/case_demo/{demo_path.json, "
                                              "heldout_check.json, mados_content_check.json}",
           "protocol": "зоны = кластеры срабатываний weights/lgbm (без гармонизации, порог с MARIDA val) на оцениваемых сценах; «обнаружено» "
                       "только при пересечении с нитью каталога Cózar 2024 (уровень B), признаки пены/блика/судна/берега/мелководья → "
                       "«недостаточно данных»; демо-сцена не участвовала в обучении и подборе порога (MARIDA — тайл и дата, MADOS — по "
                       "содержимому) и в экспериментах детектора v2"}
    if not idx:
        return out
    sys.path.insert(0, str(ROOT / "scripts" / "case"))
    import demo_sz_md  # noqa: WPS433  (числа слоя считает тот же код, что и сервис-документация)
    k = demo_sz_md.numbers() or {}
    ex, ds, fn, sc, by = k.get("ex") or {}, k.get("demo_scene") or {}, k.get("fn") or {}, k.get("sc") or {}, k.get("by") or {}
    m, pr = ex.get("measured") or {}, ex.get("probable") or {}
    path = (k.get("path") or {}).get("1920") or {}
    mc = ((_load_json(REP / "case_demo" / "mados_content_check.json") or {}).get("results") or {})
    mdemo = next((v for key, v in mc.items() if "cozar_demo" in key), {})
    hc = _load_json(REP / "case_demo" / "heldout_check.json") or {}
    out.update({
        "n_zones": k.get("n_all"), "n_scenes_eval": k.get("n_scenes"), "n_scenes": k.get("n_scenes_total"),
        "by_level_b": by.get("level_B", 0), "by_unverified": by.get("unverified", 0),
        "by_insufficient": by.get("insufficient_data", 0), "by_not_detected": by.get("not_detected", 0),
        "by_not_informative": by.get("not_informative", 0),
        "demo": {"tile": ds.get("tile"), "date": _dmy(ds.get("date")), "scene_id": ds.get("scene_id"),
                 "n_zones": k.get("n_demo"), "n_zones_cozar": k.get("n_demo_b"), "det_pixels": ds.get("det_pixels"),
                 "water_km2": _r(ds.get("water_km2"), 1), "lwd_m2_km2": _r(ds.get("lwd_m2_km2"), 0),
                 "sun_zenith_deg": ds.get("sun_zenith_deg"), "wind10m_ms": ds.get("wind10m_ms"),
                 "marida_same_tile": (hc.get("MARIDA") or {}).get("same_tile"), "marida_acq": (hc.get("MARIDA") or {}).get("n_acq"),
                 "mados_votes": mdemo.get("votes"), "mados_verdict": mdemo.get("verdict")},
        "example": {"zone_id": ex.get("zone_id"), "n_cozar": ex.get("n_cozar_filaments"),
                    "area_km2": _r(m.get("zone_area_km2"), 2), "susp_m2": m.get("suspicious_area_m2"), "n_px": m.get("n_pixels"),
                    "lwd_m2_km2": _r(m.get("lwd_m2_km2"), 0), "water_pct": _r(100 * ((m.get("quality") or {}).get("valid_water_fraction") or 0), 0),
                    "prob_mean": _r(pr.get("prob_mean"), 2), "prob_max": _r(pr.get("prob_max"), 2),
                    "sha256_short": (m.get("model") or {}).get("sha256_short")},
        "field_nearby": {"distance_km": _r(fn.get("distance_km"), 0), "date": _dmy(fn.get("date")), "n": fn.get("n_items"),
                         "area_km2": _r(fn.get("area_km2"), 2), "c": _r(fn.get("c_items_km2"), 1), "lo": _r(fn.get("ci95_lo"), 1),
                         "hi": _r(fn.get("ci95_hi"), 1)},
        "zone_main_status": "концентрация по снимку не подтверждена",
        "no_count_reason": "перевод площади маски в штуки не показываем — нет калибровочных пар «снимок → шт./км²»",
        "export": {"ui": (path.get("export") or {}).get("ui_count"), "csv": (path.get("export") or {}).get("csv"),
                   "geojson": (path.get("export") or {}).get("geojson")},
    })
    return out


def collect_photo_count() -> dict:
    """Счётчик предметов по фото (отдельный модуль, не спутник): reports/photo_count/eval_grouped.json, area_winans.json."""
    pcd = REP / "photo_count"
    ev = _load_json(pcd / "eval_grouped.json") or {}
    ar = _load_json(pcd / "area_winans.json") or {}
    out = {"available": bool(ev), "source": "reports/photo_count/{eval_grouped,area_winans}.json",
           "protocol": "Faster R-CNN на FML (сплит по сессиям съёмки, порог — только по val, test один раз); шт./м² — только на кадрах "
                       "с известной площадью (Winans, GSD 2 см); это фото с воды/воздуха, не спутник"}
    te = ev.get("test") or {}
    if te:
        out["frame"] = {"n_images": te.get("n_images"), "mae": _r(te.get("count_mae"), 2),
                        "mae_ci95": [_r(x, 2) for x in ((te.get("ci95") or {}).get("count_mae") or [])] or None,
                        "ap50": _r(te.get("ap50"), 3), "exact_pct": _r(100 * (te.get("count_exact") or 0), 0),
                        "baseline_median_count": _r((ev.get("test_baselines") or {}).get("count_median_train"), 2),
                        "license": "FML — CC BY (SEANOE doi:10.17882/106148)"}
    if ar:
        raw, base = ar.get("test_raw") or {}, ar.get("test_baseline_median") or {}
        out["area"] = {"dataset": "Winans 2023 (аэро, берег/мелководье)", "gsd_m": ar.get("gsd_m"), "frame_area_m2": _r(ar.get("frame_area_m2"), 0),
                       "n_test_frames": ar.get("n_test_frames"), "count_mae": _r(raw.get("count_mae"), 2),
                       "density_mae_km2": _r(raw.get("density_mae_km2"), -1), "baseline_density_mae_km2": _r(base.get("density_mae_km2"), -1),
                       "true_mean_density_km2": _r(ar.get("true_mean_density_km2"), -2),
                       "license": "Winans 2023 — CC BY (Zenodo 8381113)"}
    return out


def quantity_levels(ad: dict, q: dict) -> dict:
    """§15: единая логика количества — три уровня связи «снимок ↔ полевое число» (не путать между собой)."""
    return {
        "pairs_place_time": ad.get("A"), "pairs_place_time_eval": ad.get("A_eval"),
        "pairs_place_time_label": "пара по месту и времени (уровень A): снимок и полевой счёт совпадают по времени, месту и площади",
        "visible_signal": ad.get("A_eval_items_with_signal"), "visible_signal_of": ad.get("A_with_items_eval"),
        "visible_signal_label": "видимый сигнал на снимке: в паре с предметами в поле и оцениваемым детектором есть срабатывания в полосе",
        "calibration_pairs": (q.get("calibration") or {}).get("pairs_A_with_S_pos"),
        "calibration_pairs_label": "калибровочная пара «снимок → шт./км²»: пара по месту и времени с ненулевым сигналом снимка",
        "calibration_needed_min": (q.get("calibration") or {}).get("n_pairs_k_x2_min"),
        "calibration_needed_max": (q.get("calibration") or {}).get("n_pairs_k_x2_max"),
    }


def collect() -> dict:
    """Все разделы расследования данных → для case.sections (final_numbers.py)."""
    ad, q = collect_adis(), collect_quantity()
    q["levels"] = quantity_levels(ad, q)
    res = {"search": collect_search(), "labeled_data": collect_labeled(), "adis_pairs": ad,
           "baselines": collect_baselines(), "quantity": q, "oil": collect_oil(),
           "detector_v2": collect_detector_v2(), "independent_check": collect_independent(), "scene_zones": collect_scene_zones(),
           "photo_count": collect_photo_count()}
    return _clean_ids(res)


_TASK_PAREN = re.compile(r"\s*\((?:L\d{2,3}(?:\s*[–,-]\s*L?\d{2,3})*(?:,\s*)?)+\)")
_TASK_WORD = re.compile(r"(?:,\s*)?\bL\d{2,3}\b:?\s?")


def _clean_ids(x):
    """Сдача без служебных меток задач (L86 … L117): только в строках-подписях, числа не трогаются."""
    if isinstance(x, dict):
        return {k: _clean_ids(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean_ids(v) for v in x]
    if isinstance(x, str) and re.search(r"\bL\d{2,3}\b", x):
        return re.sub(r"\s{2,}", " ", _TASK_WORD.sub("", _TASK_PAREN.sub("", x))).replace("( ", "(").replace(" )", ")").strip()
    return x


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


def sync_pairs_d() -> str:
    """out/detector_v2 (не в git): срабатывания отката на D-объектах пар → по съёмкам → reports/detector_v2/pairs_d_scenes.json."""
    import pandas as pd
    run, objs = ROOT / "out" / "detector_v2" / "runs" / "reference.json", ROOT / "out" / "detector_v2" / "pairs_objects.csv"
    if not (run.is_file() and objs.is_file()):
        return "нет out/detector_v2 — снимок D пар не менялся"
    flags = ((((_load_json(run) or {}).get("pairs") or {}).get("none") or {}).get("flags")) or {}
    o = pd.read_csv(objs)
    d = o[o.level == "D"]
    hit = [int(i) for i, v in flags.items() if v and int(i) in set(d.index)]
    res = {"source": "out/detector_v2/runs/reference.json (pairs.none.flags), out/detector_v2/pairs_objects.csv",
           "n_d": int(len(d)), "flagged": len(hit), "acq_total": int(d.acq.nunique()),
           "acq_flagged": int(d.loc[hit].acq.nunique()) if hit else 0, "acq_list": sorted(d.loc[hit].acq.unique().tolist()) if hit else []}
    V2_PAIRS_D.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return f"D пар: {res['flagged']} из {res['n_d']} объектов, съёмок {res['acq_flagged']} из {res['acq_total']}"


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
    "independent_adis_pairs.jpg": ["docs/research/gleb_quantitative_link/results/adis_pairs/335694/rgb_pair_verified.png",
                                   "docs/research/gleb_quantitative_link/results/adis_pairs/335765/rgb_pair_verified.png"],
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
        print("[collect_search]", sync_pairs_d())
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
