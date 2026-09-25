"""L93: оценка количества раздельно — полевая шт./км², спутниковая доля покрытия (м²/км²), калибровка, сценарий.

Входы (только чтение): task/macroplastic_marine_samples.csv, data/case/splits/final_test_*.csv,
reports/case_conc/final_test.json, reports/case_conc/final_test_predictions.csv, data/pairs/pair_quality.csv,
data/pairs/quality/<event>/{meta.json, prob.tif, quality.tif}, service/data/<region>/<date>/lgbm/h3.geojson,
out/l93/live_cov.jsonl (scripts/case/quantity_live.py), weights/lgbm/meta.json.
Выход: reports/quantity/{field,coverage_pairs,coverage_live,calibration,scenario}.json + summary.md.

    .venv/Scripts/python.exe scripts/case/quantity_report.py
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402

from macroplastic.case import concentration as C  # noqa: E402
from macroplastic.case import coverage as V  # noqa: E402

OUT = ROOT / "reports" / "quantity"
SAMPLES = ROOT / "task" / "macroplastic_marine_samples.csv"
PAIR_THRESHOLDS = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.63, 0.70, 0.80, 0.88]
FILL = (0.2, 1.0)   # доля пикселя, занятая мусором: ~20 % — предел обнаружения по Cózar et al. 2024; 100 % — сплошь

# Исследовательские допущения о площади одного предмета (м², проекция на поверхность) по типу из item_observation.
# Первое совпадение по ключевым словам. Длины — оценки команды, НЕ измерения (в CSV размер есть только как класс > 2 см).
ITEM_GROUPS = [
    ("фрагмент", r"piece|fragment|snippet|chip", (4e-4, 1e-2), "2–10 см, квадрат"),
    ("тара", r"bottle|box|jerrycan|container|bucket|barrel|basket|beaker|\bcan\b|fish box|cup", (1e-2, 0.25),
     "10–50 см"),
    ("плёнка/пакет", r"bag|foil|packaging|balloon|belt|plate|sheet", (1e-2, 0.16), "10–40 см"),
    ("верёвка/леска", r"rope|line|net|thread|pipe", (1e-3, 2e-2), "1–2 см × 10–100 см"),
    ("пенопласт", r"styrofoam|foam|eps", (4e-4, 9e-2), "2–30 см"),
    ("прочий мелкий", r".*", (4e-4, 1e-2), "2–10 см (как фрагмент)"),
]
CLASS_BOUNDS = {"S2_visual_GT2": (0.02, 0.50), "S3_visual_GT2": (0.02, 0.50), "S4_visual_GT2_5": (0.025, 0.50),
                "S1_trawl_5_to_50": (0.05, 0.50)}   # верх 50 см — допущение (крупнее редки), м


def jdump(obj, name):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=float), encoding="utf-8")


def r3(x, k=3):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), k)


# ---------------------------------------------------------------- 1. полевая шт./км²
def field_section():
    d = pd.read_csv(SAMPLES, encoding="utf-8")
    ft = json.loads((ROOT / "reports/case_conc/final_test.json").read_text(encoding="utf-8"))["profiles"]
    pred = pd.read_csv(ROOT / "reports/case_conc/final_test_predictions.csv")
    t = d[d.record_type == "transect_density"]
    groups = [
        ("S2_visual_total_plastic", "основной", (t.measurement_profile == "S2_visual_GT2") & (t.target_scope == "total_plastic")),
        ("S1_trawl_total_plastic", "альтернативный", (t.measurement_profile == "S1_trawl_5_to_50") & (t.target_scope == "total_plastic")),
        ("S3_visual_all_litter", "справка: весь мусор, не пластик", (t.measurement_profile == "S3_visual_GT2") & (t.target_scope == "all_litter")),
        ("S4_visual_all_litter", "справка: весь мусор, не пластик", (t.measurement_profile == "S4_visual_GT2_5") & (t.target_scope == "all_litter")),
        ("S1_aerial_GT50_categories", "справка: >50 см, категории (не суммируются)", (t.measurement_profile == "S1_aerial_GT50") & (t.target_scope == "plastic_category")),
    ]
    rows = []
    for name, role, m in groups:
        g = t[m]
        c = pd.to_numeric(g.concentration_items_km2, errors="coerce")
        n = pd.to_numeric(g.density_numerator_items, errors="coerce")
        a = pd.to_numeric(g.sampled_area_km2, errors="coerce")
        row = {"profile": name, "role": role, "region": g.region.iloc[0], "source_id": g.source_id.iloc[0],
               "size_class": g.size_class.iloc[0], "method": g.sampling_method.iloc[0], "n_rows": int(len(g)),
               "n_events": int(g.event_id.nunique()), "n_with_N": int(n.notna().sum()),
               "C_median": r3(c.median()), "C_p25": r3(c.quantile(.25)), "C_p75": r3(c.quantile(.75)),
               "C_min": r3(c.min()), "C_max": r3(c.max()), "zeros": int((c == 0).sum()),
               "years": f"{str(g.date_utc.min())[:10]}..{str(g.date_utc.max())[:10]}"}
        if n.notna().all() and a.notna().all() and len(g) and "categories" not in name:
            pooled = C.concentration(n.sum(), a.sum())
            row.update(pooled_N=float(n.sum()), pooled_A_km2=r3(a.sum(), 4), pooled_C=r3(pooled.value),
                       pooled_lo95=r3(pooled.lower), pooled_hi95=r3(pooled.upper))
            ci = [C.concentration(ni, ai) for ni, ai in zip(n, a)]
            rel = [(r.upper - r.lower) / r.value for r in ci if r.value]
            row["event_rel_ci95_width_median"] = r3(np.median(rel)) if rel else None
            row["event_N_median"] = r3(n.median())
            row["event_A_km2_median"] = r3(a.median(), 4)
        elif "categories" in name:
            row["pooled_note"] = "категории не суммируются (отсутствие строки категории ≠ 0) — ΣN/ΣA не считается"
        else:
            row["pooled_note"] = "N нет в источнике (опубликованная оценка) — пуассоновский интервал не строится"
        if name in ft:
            p = ft[name]
            pm = pred[(pred.profile == name) & (pred.model == "median")]
            row["test"] = {"n_test": p["n_test"], "test_cruise_days": p["test_cruise_days"], "n_dev": p["n_dev"],
                           "map_value_dev_median": r3(pm.y_pred.iloc[0]), "map_interval90": [r3(pm.lo.iloc[0]), r3(pm.hi.iloc[0])],
                           "mae_median": r3(p["baseline_metrics"]["mae"]), "mae_main": r3(p["main"]["mae"]),
                           "main_model": p["main_model"], "d_mae": r3(p["main_vs_baseline"]["d_mae"]),
                           "d_mae_ci95": [r3(x) for x in p["main_vs_baseline"]["ci95"]],
                           "main_better_significant": p["main_better_significant"],
                           "coverage90_median": r3(p["baseline_metrics"]["coverage90"])}
        rows.append(row)
    # разложение изменчивости (S2: N есть)
    g = t[(t.measurement_profile == "S2_visual_GT2") & (t.target_scope == "total_plastic")].copy()
    g["C"] = pd.to_numeric(g.concentration_items_km2, errors="coerce")
    g["N"] = pd.to_numeric(g.density_numerator_items, errors="coerce")
    dec = V.log_var_decomposition(g.C, g.N)
    # внутри суток рейса (соседние трансекты, ~десятки км и часы): мелкомасштабная пятнистость
    g["day"] = g.date_utc.astype(str).str[:10]
    g = g[(g.C > 0) & (g.N > 0)]
    g["lc"] = np.log(g.C)
    within = g.groupby("day").filter(lambda x: len(x) >= 2)
    dev = within.lc - within.groupby("day").lc.transform("mean")
    k = within.groupby("day").ngroups
    var_within = float((dev ** 2).sum() / (len(within) - k))
    pois_within = float((1.0 / within.N).mean())
    dec_within = {"n_transects": int(len(within)), "n_days": int(k), "var_lnC_within_day": var_within,
                  "var_poisson": pois_within, "var_extra_within_day": max(var_within - pois_within, 0.0),
                  "sd_extra_within_day": math.sqrt(max(var_within - pois_within, 0.0)),
                  "note": "разброс ln C между трансектами одних суток рейса (0–десятки км, часы) минус пуассоновская часть; "
                          "оценка несовпадения следа трансекты и участка снимка при синхронной паре"}
    s1 = t[(t.measurement_profile == "S1_trawl_5_to_50") & (t.target_scope == "total_plastic")]
    c1 = pd.to_numeric(s1.concentration_items_km2, errors="coerce")
    s1_sd = float(np.std(np.log(c1[c1 > 0]), ddof=1))
    res = {"profiles": rows, "variance_S2": dec, "variance_S2_within_day": dec_within, "sd_lnC_S1_total": s1_sd,
           "formula": "C = N / A; интервал Гарвуда: N_lo = χ²(0.025; 2N)/2, N_hi = χ²(0.975; 2N+2)/2, делится на A; "
                      "по профилю C = ΣN/ΣA"}
    jdump(res, "field.json")
    return res


# ---------------------------------------------------------------- 2a. доля покрытия: сцены пар
def pairs_section():
    pq = pd.read_csv(ROOT / "data/pairs/pair_quality.csv")
    rows = []
    for _, r in pq.iterrows():
        qdir = ROOT / "data/pairs/quality" / r["dir"]
        meta = json.loads((qdir / "meta.json").read_text(encoding="utf-8"))
        row = {"event_id": r["event_id"], "decision": r["decision"], "reason": None if pd.isna(r["reason"]) else r["reason"],
               "scene_id": r["scene_id"], "dt_hours": r3(r["dt_hours"], 2), "time_known": meta.get("time_known"),
               "field_items_km2": r3(r["field_items_km2"]), "strip_area_km2": r3(r["strip_area_km2"], 4),
               "strip_valid_water_frac": r3(r["valid_water_frac"], 4), "strip_n_det": None if pd.isna(r["n_det"]) else int(r["n_det"]),
               "strip_det_area_m2": None if pd.isna(r["det_area_m2"]) else float(r["det_area_m2"]),
               "level": "C/D (не A): нет синхронности по дрейфу" + ("; время наблюдения неизвестно" if meta.get("time_known") is False else "")}
        sw = (r["strip_area_km2"] or 0) * (r["valid_water_frac"] if not pd.isna(r["valid_water_frac"]) else 0)
        row["strip_floor_ppm_1px"] = r3(V.detection_floor_ppm(sw, 1), 1) if sw > 0 else None
        if (qdir / "prob.tif").exists() and (qdir / "quality.tif").exists():
            with rasterio.open(qdir / "prob.tif") as ds:
                prob = ds.read(1)
            with rasterio.open(qdir / "quality.tif") as ds:
                q = ds.read(1)
            water = q == V.QUALITY_VALID_WATER
            cov = V.coverage_by_threshold(prob, water, PAIR_THRESHOLDS, min_px=1)
            row["crop_water_km2"] = r3(water.sum() * 1e-4, 3)
            row["crop_by_threshold"] = [{"threshold": c["threshold"], "px": c["detected_px"], "objects": c["n_objects"],
                                         "m2_per_km2": r3(c["m2_per_km2"], 2)} for c in cov]
            rg = V.coverage_range(cov)
            row["crop_ppm_range"] = [r3(rg["min"], 2), r3(rg["max"], 2)]
            row["crop_ppm_at_063"] = r3([c for c in cov if c["threshold"] == 0.63][0]["m2_per_km2"], 2)
            row["crop_floor_ppm_1px"] = r3(V.detection_floor_ppm(water.sum() * 1e-4, 1), 2) if water.any() else None
        rows.append(row)
    acc = [x for x in rows if x["decision"] == "accept"]
    with_prob = [x for x in rows if "crop_by_threshold" in x]
    summ = {"n_events": len(rows), "n_accept": len(acc), "n_with_prob": len(with_prob),
            "strip_detections_accept": sum((x["strip_n_det"] or 0) for x in acc),
            "crop_scenes_nonzero_at_063": sum(1 for x in with_prob if (x["crop_ppm_at_063"] or 0) > 0),
            "crop_scenes_nonzero_at_010": sum(1 for x in with_prob if x["crop_by_threshold"][0]["px"] > 0),
            "field_C_accept_range": [min(x["field_items_km2"] for x in acc), max(x["field_items_km2"] for x in acc)],
            "strip_floor_ppm_median": r3(np.median([x["strip_floor_ppm_1px"] for x in acc if x["strip_floor_ppm_1px"]]), 1)}
    res = {"summary": summ, "rows": rows, "thresholds": PAIR_THRESHOLDS,
           "note": "detector weights/lgbm без гармонизации (как reports/case_pairs); вода = quality.tif код 1; min_px 1; "
                   "полевая C и доля покрытия НЕ пара A (нет синхронности), рядом только для масштаба"}
    jdump(res, "coverage_pairs.json")
    return res


# ---------------------------------------------------------------- 2b. доля покрытия: сцены подготовки
def live_section():
    cache = ROOT / "out/l93/live_cov.jsonl"
    rows = [json.loads(x) for x in cache.read_text(encoding="utf-8").splitlines() if x.strip()] if cache.exists() else []
    out = []
    for r in rows:
        # карта сервиса: h3 flagged/observed после постобработки и фильтра артефактов (порог сцены)
        h3p = ROOT / "service/data" / r["region"] / r["date"] / "lgbm" / "h3.geojson"
        svc_map = None
        if h3p.exists():
            fc = json.loads(h3p.read_text(encoding="utf-8"))
            fl = sum(f["properties"]["flagged_water_px"] for f in fc["features"])
            ob = sum(f["properties"]["observed_water_px"] for f in fc["features"])
            svc_map = {"flagged_px": fl, "observed_px": ob, "m2_per_km2": fl / ob * 1e6 if ob else None}
        rec = {"scene": r["scene"], "water_km2": r3(r["water_km2"], 1), "floor_ppm_1obj": r3(r["floor_ppm_1obj"], 3),
               "service_map": {k: (r3(v, 3) if isinstance(v, float) else v) for k, v in svc_map.items()} if svc_map else None}
        for mode in ("current", "service"):
            m = r[mode]
            if not m.get("rows"):
                continue
            by = {x["threshold"]: x for x in m["rows"]}
            op = by.get(round(m["threshold"], 2)) or by[min(by, key=lambda t: abs(t - m["threshold"]))]
            vals = [x["m2_per_km2"] for x in m["rows"]]
            rec[mode] = {"threshold": m["threshold"], "ppm_at_threshold": r3(op["m2_per_km2"], 3),
                         "objects_at_threshold": op["n_objects"], "area_m2_at_threshold": op["detected_area_m2"],
                         "ppm_min": r3(min(vals), 3), "ppm_max": r3(max(vals), 3),
                         "objects_min": min(x["n_objects"] for x in m["rows"]),
                         "objects_max": max(x["n_objects"] for x in m["rows"])}
        out.append(rec)

    def agg(mode):
        v = [x[mode] for x in out if mode in x]
        if not v:
            return None
        at = np.array([x["ppm_at_threshold"] for x in v])
        return {"n_scenes": len(v), "scenes_nonzero": int((at > 0).sum()),
                "ppm_median": r3(float(np.median(at)), 3), "ppm_p90": r3(float(np.quantile(at, .9)), 3),
                "ppm_max": r3(float(at.max()), 3),
                "total_area_m2": float(sum(x["area_m2_at_threshold"] for x in v)),
                "total_water_km2": r3(sum(x["water_km2"] for x in out if mode in x), 1),
                "pooled_ppm": r3(sum(x["area_m2_at_threshold"] for x in v) / sum(x["water_km2"] for x in out if mode in x), 4),
                "range_ppm_median_min": r3(float(np.median([x["ppm_min"] for x in v])), 3),
                "range_ppm_median_max": r3(float(np.median([x["ppm_max"] for x in v])), 3),
                "scenes_nonzero_any_threshold": int(sum(1 for x in v if x["ppm_max"] > 0))}
    ratios = [x["current"]["ppm_max"] / x["current"]["ppm_min"] for x in out
              if "current" in x and x["current"]["ppm_min"] > 0]
    sm = [x["service_map"]["m2_per_km2"] for x in out if x.get("service_map") and x["service_map"]["m2_per_km2"] is not None]
    by_region = {}
    for x in out:
        reg = x["scene"].split("/")[0]
        b = by_region.setdefault(reg, {"n": 0, "cur": [], "map": [], "cur_lo": [], "cur_hi": []})
        b["n"] += 1
        b["cur"].append(x["current"]["ppm_at_threshold"])
        b["cur_lo"].append(x["current"]["ppm_min"])
        b["cur_hi"].append(x["current"]["ppm_max"])
        if x.get("service_map") and x["service_map"]["m2_per_km2"] is not None:
            b["map"].append(x["service_map"]["m2_per_km2"])
    by_region = {k: {"n_scenes": v["n"], "current_median": r3(float(np.median(v["cur"])), 2), "current_max": r3(max(v["cur"]), 2),
                     "current_threshold_range": [r3(min(v["cur_lo"]), 2), r3(max(v["cur_hi"]), 2)],
                     "map_median": r3(float(np.median(v["map"])), 2) if v["map"] else None}
                 for k, v in sorted(by_region.items())}
    res = {"n_scenes": len(out), "current": agg("current"), "service": agg("service"), "by_region": by_region,
           "service_map": {"n": len(sm), "ppm_median": r3(float(np.median(sm)), 3) if sm else None,
                           "ppm_max": r3(float(max(sm)), 3) if sm else None},
           "threshold_ratio_max_over_min": {"n": len(ratios), "median": r3(float(np.median(ratios)), 2) if ratios else None,
                                            "p90": r3(float(np.quantile(ratios, .9)), 2) if ratios else None},
           "rows": out,
           "note": "current = weights/lgbm без гармонизации; service = prob_lgbm.tif (lgbm_live + гармонизация water_median, "
                   "из них построена карта); service_map = h3 карты сервиса (после фильтра артефактов). Пороги 0.10–0.88 "
                   "(F1 MARIDA val ≥ max−0.01). Без фильтра судов/кильватера → current/service — верхняя оценка."}
    jdump(res, "coverage_live.json")
    return res


# ---------------------------------------------------------------- 1b. ADIS: доля площади предметов и предел обнаружения
def adis_section():
    seg_p = ROOT / "data/extra/field/adis/Segments.csv"
    obj_p = ROOT / "data/extra/field/adis/Objects.csv"
    if not (seg_p.exists() and obj_p.exists()):
        return None
    s = pd.read_csv(seg_p)
    o = pd.read_csv(obj_p, usecols=["SegmentID", "area"])
    n = s["n_objects>5cm"].astype(float)
    A = s["area_scanned_km2"].astype(float)
    s["obj_area_m2"] = s.SegmentID.map(o.groupby("SegmentID").area.sum()).fillna(0.0)
    s["ppm"] = s.obj_area_m2 / A          # м² предметов на км² обследованной полосы
    q = s.ppm.quantile([.5, .9, .99, .999]).to_dict()
    g = s[n > 0].copy()
    g["lc"], g["N"] = np.log(g.dhat_5cm.astype(float)), n[n > 0]
    g["day"] = g.Ship.astype(str) + "|" + g.mindate.astype(str).str[:10]
    w = g.groupby("day").filter(lambda x: len(x) >= 2)
    dev = w.lc - w.groupby("day").lc.transform("mean")
    k = w.day.nunique()
    var_w = float((dev ** 2).sum() / (len(w) - k))
    pois_w = float((1.0 / w.N).mean())
    tot_km2 = float(A.sum())
    big = {f"ge_{t}m2": int((o.area >= t).sum()) for t in (10, 20, 40)}
    rate20 = big["ge_20m2"] / tot_km2
    res = {"source": "ADIS, de Vries et al. 2026, 4TU 10.4121/ddede7f5-aca5-42ae-b851-e0bbb9a2c4c2 (CC BY 4.0), data/extra/field/adis",
           "n_segments": int(len(s)), "zero_share": r3(float((n == 0).mean())), "area_km2": r3(tot_km2, 1),
           "n_items_gt5cm": int(n.sum()), "pooled_items_km2_raw": r3(float(n.sum() / tot_km2)),
           "segment_ppm_quantiles": {f"p{int(k_ * 1000) / 10:g}": r3(v, 3) for k_, v in q.items()},
           "segment_ppm_max": r3(float(s.ppm.max()), 1),
           "sd_lnC_between_segments": r3(float(g.lc.std(ddof=1))), "sd_poisson": r3(math.sqrt(float((1 / g.N).mean()))),
           "within_day": {"n_segments": int(len(w)), "n_ship_days": int(k), "var_lnC": r3(var_w), "var_poisson": r3(pois_w),
                          "sd_extra": r3(math.sqrt(max(var_w - pois_w, 0.0)))},
           "items_by_area": big, "rate_items_ge20m2_per_km2": r3(rate20, 5),
           "expected_ge20m2_in_strip_0_8km2": r3(rate20 * 0.8, 5),
           "note": "ppm = Σ площади предметов сегмента / обследованная площадь (м²/км²), без поправки на пропуски; предмет ≥ 20 м² "
                   "в одиночку закрывает ≥ 20 % пикселя (порог обнаружения по Cózar 2024); остальные ≤ 11 % пикселя "
                   "и в пикселе на таких плотностях не складываются"}
    jdump(res, "adis_field.json")
    return res


# ---------------------------------------------------------------- 3. калибровка
def _detect_share(pairs):
    acc = [x for x in pairs["rows"] if x["decision"] == "accept" and x["strip_n_det"] is not None]
    k = sum(1 for x in acc if x["strip_n_det"] > 0)
    n = len(acc)
    from scipy.stats import beta
    hi = float(beta.ppf(0.975, k + 1, n - k)) if k < n else 1.0   # Клоппер–Пирсон, верхняя граница
    return {"strips_accept_nonzero": k, "strips_accept_with_detector": n, "p_nonzero_upper95": r3(hi, 3),
            "note": f"{k} из {n} принятых полос с S > 0: доля пар с ненулевой S ≤ {hi:.2f} (95 %, Клоппер–Пирсон) → "
                    f"полевых обследований нужно ≥ n / {hi:.2f}, т. е. в ≥ {1 / hi:.1f} раза больше, чем пар в таблице"}


def calibration_section(field, live, pairs, adis=None):
    dec, wd = field["variance_S2"], field["variance_S2_within_day"]
    sd_match = wd["sd_extra_within_day"]
    sd_pois = math.sqrt(dec["var_poisson"])
    thr_ratio = (live.get("threshold_ratio_max_over_min") or {}).get("median") or None
    sd_thr = math.log(thr_ratio) / (2 * 1.96) if thr_ratio and thr_ratio > 1 else None
    scen = []
    sats = [(0.3, "допущение"), (0.5, "допущение")]
    if sd_thr:
        sats.append((round(sd_thr, 3), "измерено: только разброс по порогу 0.10–0.88 на сценах"))
    sats.append((1.0, "допущение"))
    for sd_sat, basis in sats:
        s = math.sqrt(sd_match ** 2 + sd_pois ** 2 + sd_sat ** 2)
        scen.append({"sd_satellite_ln": sd_sat, "basis": basis, "sd_resid_ln": r3(s),
                     "best_pi95_factor": r3(math.exp(1.96 * s), 2),
                     "n_factor_1_5": V.n_pairs_for_factor(s, 1.5), "n_factor_2": V.n_pairs_for_factor(s, 2.0),
                     "n_factor_3": V.n_pairs_for_factor(s, 3.0),
                     "n_prediction_x2": V.n_pairs_for_prediction(s, 2.0),
                     "n_prediction_x3": V.n_pairs_for_prediction(s, 3.0),
                     "slope_se_n30": r3(V.slope_se(s, dec["sd_lnC_total"], 30)),
                     "slope_se_n100": r3(V.slope_se(s, dec["sd_lnC_total"], 100))})
    res = {
        "model": "ln C_field,i = ln k + b · ln S_i + ε_i,  S_i — доля покрытия (м²/км²) в том же следе и в то же время; "
                 "b = 1 — пропорциональная модель (C = k·S); ε = несовпадение следа (пятнистость) + пуассоновский шум + "
                 "ошибка спутниковой доли (порог, субпиксельная доля, пена/водоросли)",
        "assumptions": ["пара A: снимок и трансекта совпадают по времени (|dt| ≤ ~4 ч при 0.2 м/с и буфере 3 км), месту, "
                        "обследованной площади и совокупности (профиль: метод, размер, материал)",
                        "одна пара = один независимый участок (разные сутки/сцены); трансекты одних суток — не независимы",
                        "S > 0 (детектор видит скопление); пары с S = 0 дают только цензурированную информацию",
                        "ε логнормальный, не зависит от S"],
        "sd_components_ln": {"sd_lnC_between_events_S2": r3(dec["sd_lnC_total"]), "sd_poisson_S2": r3(sd_pois),
                             "sd_match_within_day_S2": r3(sd_match), "sd_threshold_from_scenes": r3(sd_thr) if sd_thr else None,
                             "threshold_ratio_median": thr_ratio},
        "scenarios": scen,
        "n_pairs_correlation": {"r0.3": V.n_pairs_for_correlation(0.3), "r0.5": V.n_pairs_for_correlation(0.5),
                                "r0.7": V.n_pairs_for_correlation(0.7)},
        "floor_no_satellite": {"sd_ln": r3(math.sqrt(sd_match ** 2 + sd_pois ** 2)),
                               "best_pi95_factor": r3(math.exp(1.96 * math.sqrt(sd_match ** 2 + sd_pois ** 2)), 2),
                               "median_baseline_pi95_factor": r3(math.exp(1.96 * dec["sd_lnC_total"]), 2),
                               "note": "даже при идеальном спутнике (σ_sat = 0) и бесконечном числе пар интервал одной "
                                       "оценки не уже ×/÷ этого множителя (пятнистость + пуассоновский шум поля)"},
        "detect_share": _detect_share(pairs),
        "adis_check": None if not adis else {
            "sd_poisson_10km_segments": adis["sd_poisson"], "sd_extra_within_day": adis["within_day"]["sd_extra"],
            "zero_share": adis["zero_share"],
            "note": "на отрезках 10 км (ADIS) избыточного разброса сверх Пуассона внутри суток нет, но счёт мал "
                    "(медиана 1–2 предмета) → σ Пуассона 0.75; 72 % отрезков — ноль (ln не определён) → нужна модель с нулями"},
        "have_now": {"pairs_A": 0, "pairs_accept_meta": 29, "pairs_quality_ok": 12, "synchronous_typical_drift": 0,
                     "detections_in_strips": 0},
        "formulas": {"factor": "n = ⌈(1.96·σ / ln f)²⌉ — 95 % ДИ множителя k в пределах ×/÷ f",
                     "prediction": "интервал предсказания одной пары 1.96·σ·√(1+1/n) ≤ ln f; при 1.96·σ ≥ ln f недостижим",
                     "correlation": "n = ((z_0.975 + z_0.8) / atanh r)² + 3",
                     "slope": "SE(b) = σ / (sd(ln S) · √(n−1)); sd(ln S) взят = sd ln C между событиями (b ≈ 1)"},
    }
    jdump(res, "calibration.json")
    return res


# ---------------------------------------------------------------- 4. исследовательский сценарий
def scenario_section(field, live):
    d = pd.read_csv(SAMPLES, encoding="utf-8")
    io = d[d.record_type == "item_observation"]
    comp = {}
    for src, g in io.groupby("source_id"):
        cnt = {}
        for typ, n in g.litter_item_type.str.lower().value_counts().items():
            grp = next(name for name, rx, _, _ in ITEM_GROUPS if re.search(rx, typ))
            cnt[grp] = cnt.get(grp, 0) + int(n)
        tot = sum(cnt.values())
        w = {k: v / tot for k, v in cnt.items()}
        amin = sum(w[k] * a[0] for k, _, a, _ in ITEM_GROUPS if k in w)
        amax = sum(w[k] * a[1] for k, _, a, _ in ITEM_GROUPS if k in w)
        comp[src] = {"n_items": tot, "counts": cnt, "mean_item_area_m2": [amin, amax]}
    groups = [{"group": k, "regex": rx, "item_area_m2": list(a), "basis": b} for k, rx, a, b in ITEM_GROUPS]
    a_s2 = comp["S2_SARGASSO_MSM41"]["mean_item_area_m2"]
    lo, hi = CLASS_BOUNDS["S2_visual_GT2"]
    bounds_area = (lo * lo, hi * hi)
    one_obj = {"composition_S2": V.items_from_area_research(200.0, FILL, tuple(a_s2)),
               "size_class_bounds_S2": V.items_from_area_research(200.0, FILL, bounds_area)}
    # в обратную сторону: полевая медиана → площадь мусора на км² → сравнение с пределом обнаружения
    s2 = next(p for p in field["profiles"] if p["profile"] == "S2_visual_total_plastic")
    cmed = s2["test"]["map_value_dev_median"]
    cmax = s2["C_max"]
    field_area = {"C_median_items_km2": cmed, "C_max_items_km2": cmax,
                  "litter_m2_per_km2_at_median": [cmed * a_s2[0], cmed * a_s2[1]],
                  "litter_m2_per_km2_at_max": [cmax * a_s2[0], cmax * a_s2[1]]}
    # локальная плотность, нужная, чтобы 1 пиксель (100 м²) был покрыт ≥ 20 %: 20 м² мусора на 100 м²
    need_local = [0.2 * 1e6 / a_s2[1], 0.2 * 1e6 / a_s2[0]]   # шт./км² внутри пикселя
    field_area["local_items_km2_needed_for_detection"] = need_local
    field_area["factor_vs_field_median"] = [need_local[0] / cmed, need_local[1] / cmed]
    field_area["factor_vs_field_max"] = [need_local[0] / cmax, need_local[1] / cmax]
    # сцены подготовки: доля покрытия → шт./км² (исследовательский диапазон)
    cur = (live or {}).get("current") or {}
    scene_items = None
    if cur.get("ppm_max") is not None:
        scene_items = {"ppm_median": cur["ppm_median"], "ppm_max": cur["ppm_max"],
                       "items_km2_at_ppm_max": V.items_from_area_research(cur["ppm_max"], FILL, tuple(a_s2)),
                       "note": "м²/км² × f / a = шт./км²; S2-состав предметов перенесён на чужие регионы — ещё одно допущение"}
    # размеры из данных (L91): ADIS Objects (камера с судна, > 5 см, смещено к крупным), Lebreton 2018 аэромозаики (> 50 см)
    data_sizes = {}
    adis = ROOT / "data/extra/field/adis/Objects.csv"
    if adis.exists():
        a = pd.read_csv(adis, usecols=["area"])["area"].astype(float)
        data_sizes["ADIS_Objects"] = {"n": int(len(a)), "mean_m2": r3(a.mean(), 4), "median_m2": r3(a.median(), 4),
                                      "p25_m2": r3(a.quantile(.25), 4), "p75_m2": r3(a.quantile(.75), 4),
                                      "source": "de Vries et al. 2026, 4TU 10.4121/ddede7f5-aca5-42ae-b851-e0bbb9a2c4c2 (data/extra/field/adis)"}
    leb = ROOT / "data/extra/field/csv/Lebreton2018_SamplingInformation-MosaicDebrisInfo.csv"
    if leb.exists():
        b = pd.read_csv(leb, dtype=str)["Topview area (m^2)"].str.replace(",", ".").astype(float).dropna()
        data_sizes["Lebreton2018_mosaic"] = {"n": int(len(b)), "mean_m2": r3(b.mean(), 4), "median_m2": r3(b.median(), 4),
                                             "p25_m2": r3(b.quantile(.25), 4), "p75_m2": r3(b.quantile(.75), 4),
                                             "source": "Lebreton et al. 2018, figshare 5873142 (data/extra/field/csv)"}
    for key, dsz in data_sizes.items():
        one_obj[f"data_sizes_{key}"] = V.items_from_area_research(200.0, FILL, (dsz["p25_m2"], dsz["p75_m2"]))
    cz = ROOT / "data/extra/registry_cozar2024.csv"
    windrow = None
    if cz.exists():
        w = pd.read_csv(cz, usecols=["area_m2"])["area_m2"].astype(float)
        windrow = {"n": int(len(w)), "median_m2": r3(w.median(), 1), "p90_m2": r3(w.quantile(.9), 1),
                   "sum_km2": r3(w.sum() / 1e6, 2), "source": "Cózar et al. 2024 каталог (Zenodo 11045944), data/extra/registry_cozar2024.csv"}
        wm = float(w.median())
        windrow["items_median_windrow_S2_composition"] = V.items_from_area_research(wm, FILL, tuple(a_s2))
        if "ADIS_Objects" in data_sizes:
            d_ = data_sizes["ADIS_Objects"]
            windrow["items_median_windrow_ADIS_sizes"] = V.items_from_area_research(wm, FILL, (d_["p25_m2"], d_["p75_m2"]))
    res = {"label": V.RESEARCH_LABEL, "fill_frac": list(FILL), "data_item_sizes": data_sizes, "cozar_windrows": windrow,
           "fill_basis": "Cózar et al. 2024: обнаружение при покрытии пикселя около 20 % (нижняя граница f); 1.0 — пиксель сплошь",
           "item_groups": groups, "composition": comp, "size_class_bounds_m": CLASS_BOUNDS,
           "one_minimal_object_200m2": one_obj, "field_vs_detection": field_area, "scenes": scene_items,
           "formula": "N = S_маски · f / ā;  N_min = S·f_min/ā_max, N_max = S·f_max/ā_min;  ā — средняя площадь предмета по составу"}
    jdump(res, "scenario.json")
    return res


def fmt(x, k=1):
    if x is None:
        return "—"
    if isinstance(x, (list, tuple)):
        return "–".join(fmt(v, k) for v in x)
    x = float(x)
    if x != 0 and (abs(x) >= 1e5 or abs(x) < 10 ** (-k)):
        return f"{x:.2g}"
    return f"{x:,.{k}f}".replace(",", " ")


def summary_md(field, pairs, live, cal, scen, adis=None):
    L = ["# L93 — оценка количества: раздельно поле и спутник (reports/quantity)", "",
         "Сгенерировано `scripts/case/quantity_report.py` (сцены — `scripts/case/quantity_live.py`). Пар уровня A: **0** — "
         "подсчёта «снимок → шт./км²» нет.", "",
         "## 1. Полевая концентрация шт./км² (C = N/A, интервал Гарвуда 95 %)", "",
         "| профиль | роль | регион | событий | ΣN / ΣA км² | C = ΣN/ΣA [95 %] | медиана событий [p25–p75] | мин–макс | карта: медиана dev [инт. 90 %] | test MAE медиана / модель |",
         "|---|---|---|---:|---|---|---|---|---|---|"]
    for p in field["profiles"]:
        pooled = (f"{fmt(p['pooled_C'])} [{fmt(p['pooled_lo95'])}–{fmt(p['pooled_hi95'])}]" if "pooled_C" in p else "N нет")
        na = f"{fmt(p.get('pooled_N'), 0)} / {fmt(p.get('pooled_A_km2'), 2)}" if "pooled_N" in p else "—"
        t = p.get("test")
        mp = f"{fmt(t['map_value_dev_median'])} [{fmt(t['map_interval90'])}]" if t else "—"
        te = f"{fmt(t['mae_median'])} / {fmt(t['mae_main'])} ({t['main_model']})" if t else "—"
        L.append(f"| {p['profile']} | {p['role']} | {p['region']} | {p['n_events']} | {na} | {pooled} | "
                 f"{fmt(p['C_median'])} [{fmt(p['C_p25'])}–{fmt(p['C_p75'])}] | {fmt(p['C_min'])}–{fmt(p['C_max'])} | {mp} | {te} |")
    dec, wd = field["variance_S2"], field["variance_S2_within_day"]
    L += ["", f"S2: sd ln C между событиями {fmt(dec['sd_lnC_total'], 2)}; пуассоновская часть sd {fmt(dec['sd_poisson'], 2)} "
          f"(доля дисперсии {fmt(dec['poisson_share'], 2)}); внутри суток рейса ({wd['n_transects']} трансект, {wd['n_days']} сут) "
          f"избыточный разброс sd {fmt(wd['sd_extra_within_day'], 2)}. S1: sd ln C {fmt(field['sd_lnC_S1_total'], 2)}.", "",
          ]
    if adis:
        L += ["", f"ADIS (L91, другие данные — камера с судна, > 5 см): {adis['n_segments']} отрезков по 10 км, {adis['area_km2']} км², "
              f"нулевых {fmt(adis['zero_share'], 2)}; площадь предметов на площадь полосы — медиана 0, p99 {adis['segment_ppm_quantiles'].get('p99')} "
              f"м²/км², макс {adis['segment_ppm_max']}. Предметов ≥ 20 м² (≥ 20 % пикселя в одиночку): {adis['items_by_area']['ge_20m2']} "
              f"на {adis['area_km2']} км² = {adis['rate_items_ge20m2_per_km2']} на км² → в полосе 0.8 км² ожидается {adis['expected_ge20m2_in_strip_0_8km2']}. "
              f"Разброс ln C внутри суток сверх Пуассона: sd {adis['within_day']['sd_extra']} (σ Пуассона {adis['sd_poisson']})."]
    L += ["", "## 2. Спутниковая доля покрытия, м²/км² пригодной воды (как LWD у Cózar et al. 2024)", ""]
    s = pairs["summary"]
    L += [f"Сцены пар (`data/pairs/quality`): событий {s['n_events']}, принято масками {s['n_accept']}, с картой вероятностей "
          f"{s['n_with_prob']}; срабатываний в полосах принятых — {s['strip_detections_accept']}; вырезок (≈7×7 км) с долей > 0 "
          f"при 0.63 — {s['crop_scenes_nonzero_at_063']}, при 0.10 — {s['crop_scenes_nonzero_at_010']}. "
          f"Предел обнаружения в полосе (1 пиксель) — медиана {fmt(s['strip_floor_ppm_median'])} м²/км²; полевая C "
          f"у принятых {fmt(s['field_C_accept_range'])} шт./км². Это не пары A (нет синхронности).", ""]
    if live.get("current"):
        c, sv, sm = live["current"], live["service"], live["service_map"]
        L += ["| режим | сцен | доля > 0 при рабочем пороге | медиана, м²/км² | p90 | макс | общая по всем сценам | диапазон по порогу 0.10–0.88 (медианы min–max) | сцен > 0 хоть при одном пороге |",
              "|---|---:|---:|---:|---:|---:|---:|---|---:|"]
        for name, a in (("текущий (weights/lgbm, без гармонизации, 0.63)", c), ("как на карте (lgbm_live + гармонизация, 0.64), без фильтра артефактов", sv)):
            if a:
                L.append(f"| {name} | {a['n_scenes']} | {a['scenes_nonzero']} | {fmt(a['ppm_median'], 2)} | {fmt(a['ppm_p90'], 2)} | "
                         f"{fmt(a['ppm_max'], 2)} | {fmt(a['pooled_ppm'], 3)} | {fmt(a['range_ppm_median_min'], 2)}–{fmt(a['range_ppm_median_max'], 2)} | {a['scenes_nonzero_any_threshold']} |")
        L += ["", f"Карта сервиса (h3, после фильтра артефактов): медиана {fmt(sm['ppm_median'], 2)}, макс {fmt(sm['ppm_max'], 2)} м²/км² "
              f"по {sm['n']} сценам. Разброс доли по порогу (макс/мин, где мин > 0): медиана ×{fmt(live['threshold_ratio_max_over_min']['median'], 2)}.", "",
              "По регионам (м²/км²; текущий режим при 0.63 — медиана / макс по датам; диапазон по порогу 0.10–0.88 — min–max по датам; карта — медиана):", "",
              "| регион | дат | текущий: медиана / макс | диапазон по порогу | карта сервиса |", "|---|---:|---|---|---:|"]
        for reg, b in live["by_region"].items():
            L.append(f"| {reg} | {b['n_scenes']} | {fmt(b['current_median'], 2)} / {fmt(b['current_max'], 2)} | "
                     f"{fmt(b['current_threshold_range'], 2)} | {fmt(b['map_median'], 2)} |")
        L += [""]
    L += ["## 3. Калибровка связи (нужно пар уровня A)", "", cal["model"], "",
          "Число пар — чтобы 95 % ДИ множителя k был в ×/÷ f; «предсказание» — чтобы интервал для ОДНОЙ новой сцены был в ×/÷ f.", "",
          "| σ спутника (ln) | основание | σ остатка (ln) | лучший 95 % интервал одной оценки | k в ×/÷1.5 | ×/÷2 | ×/÷3 | предсказание ×/÷2 | ×/÷3 | SE наклона n=30 / 100 |",
          "|---:|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in cal["scenarios"]:
        L.append(f"| {r['sd_satellite_ln']} | {r['basis']} | {r['sd_resid_ln']} | ×/÷{r['best_pi95_factor']} | {r['n_factor_1_5']} | {r['n_factor_2']} | {r['n_factor_3']} | "
                 f"{r['n_prediction_x2'] or 'недостижимо'} | {r['n_prediction_x3'] or 'недостижимо'} | {r['slope_se_n30']} / {r['slope_se_n100']} |")
    nc = cal["n_pairs_correlation"]
    fl, ds = cal["floor_no_satellite"], cal["detect_share"]
    L += ["", f"Обнаружить связь (α 0.05, мощность 0.8): r 0.3 — {nc['r0.3']} пар, r 0.5 — {nc['r0.5']}, r 0.7 — {nc['r0.7']}. "
          "Сейчас пар A — 0.",
          f"Нижний предел без спутника: σ {fl['sd_ln']} → интервал одной оценки не уже ×/÷{fl['best_pi95_factor']} "
          f"(у медианы профиля ×/÷{fl['median_baseline_pi95_factor']}). {ds['note']}.", "", "## 4. Исследовательский сценарий «площадь / размер предмета = штуки» (НЕ основное число)", ""]
    o = scen["one_minimal_object_200m2"]
    fv = scen["field_vs_detection"]
    L += [f"Один минимальный объект (2 пикселя = 200 м²), f {scen['fill_frac']}: по составу S2 — "
          f"{fmt(o['composition_S2']['items_min'], 0)}–{fmt(o['composition_S2']['items_max'], 0)} шт.; по границам класса 2–50 см — "
          f"{fmt(o['size_class_bounds_S2']['items_min'], 0)}–{fmt(o['size_class_bounds_S2']['items_max'], 0)} шт. "
          f"(разброс ×{fmt(o['size_class_bounds_S2']['ratio_max_min'], 0)})"
          + "".join(f"; по размерам {k.replace('data_sizes_', '')} (p25–p75 {fmt(v['item_area_m2'], 2)} м²) — {fmt(v['items_min'], 0)}–{fmt(v['items_max'], 0)} шт."
                    for k, v in o.items() if k.startswith("data_sizes_")) + ".",
          f"Полевая медиана {fmt(fv['C_median_items_km2'])} шт./км² = {fmt(fv['litter_m2_per_km2_at_median'], 3)} м² мусора на км²; "
          f"чтобы пиксель сработал (≥ 20 % покрытия), локально нужно {fmt(fv['local_items_km2_needed_for_detection'], 0)} шт./км² — "
          f"в {fmt(fv['factor_vs_field_median'], 0)} раз выше медианы поля (макс. поля — в {fmt(fv['factor_vs_field_max'], 0)} раз)."]
    cw = scen.get("cozar_windrows")
    if cw:
        a1, a2 = cw["items_median_windrow_S2_composition"], cw.get("items_median_windrow_ADIS_sizes")
        L.append(f"Медианное «окно» Cózar 2024 ({cw['n']} шт., медиана {fmt(cw['median_m2'], 0)} м² пикселей): по составу S2 — "
                 f"{fmt(a1['items_min'], 0)}–{fmt(a1['items_max'], 0)} шт." + (f"; по размерам ADIS (p25–p75 {fmt(a2['item_area_m2'], 2)} м²) — "
                 f"{fmt(a2['items_min'], 0)}–{fmt(a2['items_max'], 0)} шт." if a2 else "") + " Разброс на 2–3 порядка — поэтому это не число для карты.")
    L.append("")
    (OUT / "summary.md").write_text(("\n".join(L) + "\n").replace("шт..", "шт."), encoding="utf-8")


def main():
    field = field_section()
    pairs = pairs_section()
    live = live_section()
    adis = adis_section()
    cal = calibration_section(field, live, pairs, adis)
    scen = scenario_section(field, live)
    summary_md(field, pairs, live, cal, scen, adis)
    print((OUT / "summary.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
