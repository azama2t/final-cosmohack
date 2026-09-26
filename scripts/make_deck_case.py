r"""Материалы защиты кейса из reports/final_numbers.json (единственный источник чисел).

Пишет:
  reports/case_deck.pptx     слайды 16:9 (с блоком «Расследование данных» §11), заметки докладчика = текст речи слайда
  docs/SPEECH.md             речь по слайдам, тайминг по числу слов
  docs/DEMO.md               сценарий демо на карте кейса (2 мин) и план Б без сети
  docs/QA.md                 30 вопросов жюри с ответами и ссылками на доказательства
  reports/case_deck_img/     4 скриншота карты (уменьшенные копии из reports/screens/case_v2/iter8/)

Запуск из корня:
    .venv\Scripts\python.exe scripts\make_deck_case.py            # всё
    .venv\Scripts\python.exe scripts\make_deck_case.py --check    # только проверить, что все числа нашлись

Числа руками не пишутся: каждое берётся по пути из reports/final_numbers.json (блок case, порог — l3_lgbm), других
источников чисел нет. Каждый прочитанный путь и его значение записываются в USED — по нему tests/test_case_docs_numbers.py
сверяет деку, речь, демо и вопросы с final_numbers.json. Если хоть одно значение не найдено, скрипт завершается с кодом 1
и печатает список путей (пустых значений 0). Полная сборка документов — scripts/case/build_docs.py.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FN = ROOT / "reports" / "final_numbers.json"
SHOTS = ROOT / "reports" / "screens" / "case_v2" / "iter8"
IMG_DIR = ROOT / "reports" / "case_deck_img"
OUT_PPTX = ROOT / "reports" / "case_deck.pptx"
OUT_SPEECH = ROOT / "docs" / "SPEECH.md"
OUT_DEMO = ROOT / "docs" / "DEMO.md"
OUT_QA = ROOT / "docs" / "QA.md"

# скриншот-источник -> имя в reports/case_deck_img, обрезка (x0, y0, x1, y1) или None
IMAGES = {
    "overview": ("19_overview_1366.png", "01_overview.jpg", None),
    "zone": ("../../../selfcheck/jury_4/04_strip_HE460t03_1920.png", "02_zone_he460_t03.jpg", (340, 0, 1920, 1080)),
    "obs": ("05_obs_card.png", "03_observation_card.jpg", None),
    "pairfinder": ("25_pairfinder_s2.png", "04_pairfinder_transect.jpg", (760, 0, 1920, 1080)),
    # §11 «Расследование данных»: воронка и вырезки розыска (docs/img, собираются scripts/case/collect_search.py --sync)
    "funnel": ("../../../../docs/img/funnel.png", "05_funnel.jpg", None),
    "adis": ("../../../../docs/img/search_adis_pair.jpg", "06_adis_pair.jpg", None),
    "s4": ("../../../../docs/img/search_s4_t30.jpg", "07_s4_t30.jpg", None),
    "independent": ("../../../../docs/img/independent_adis_pairs.jpg", "08_independent_adis.jpg", None),
    "demo_card": ("../../../case_demo/1920_02_zone_card.png", "09_demo_zone_card.jpg", (336, 0, 1920, 1080)),
    "ladder": ("../../../../docs/img/units_ladder.png", "10_units_ladder.jpg", None),
}

# основная часть §32 Б: ключ -> (кандидаты от корня репо по приоритету, имя в reports/case_deck_img, обрезка)
# свежие скрины v2 (http://localhost:8070, 1920×1080) кладутся в presentation/img/; пока их нет — прежние скрины
IMAGES_MAIN = {
    "photo": (("presentation/img/photo.png", "reports/photo_count/ui_photo_v2.png"), "20_photo.jpg", None),
    "svc_earth": (("presentation/img/earth.png",), "21_svc_earth.jpg", None),
    "svc_card": (("presentation/img/card.png", "reports/case_demo/1920_02_zone_card.png"), "22_svc_card.jpg", None),
    "svc_studio": (("presentation/img/studio.png",), "25_svc_studio.jpg", None),
    "svc_photo": (("presentation/img/photo.png", "reports/photo_count/ui_photo_v2.png"), "23_svc_photo.jpg", None),
    "svc_export": (("presentation/img/export.png", "reports/case_demo/1920_07_export_menu.png"), "24_svc_export.jpg", None),
}

# основная часть деки и речи (SPEC-GAPS.md: ТЗ не задаёт время → основная речь ≤ 5:00, ≤ 14 слайдов); остальное — «Приложение»
MAIN_LIMIT_S = 300
MAIN_MAX_SLIDES = 14
APPENDIX_FROM_MAIN = ("Данные и отбор", "Эксперимент «снимок vs all_litter»", "Дополнительные функции")
# прежние подробные слайды, которые остаются в приложении (§32 Б: основная часть — main_slides)
APPENDIX_KEEP = ("Реестр пар и окно синхронизации", "Данные и отбор", "Детектор", "Концентрация",
                 "Эксперимент «снимок vs all_litter»", "Собственный вклад и ключевые решения", "Дополнительные функции")
OUT_PRES = ROOT / "presentation"

MISSING: list[str] = []
USED: dict[str, object] = {}  # путь в final_numbers.json -> значение, подставленное в тексты


# ----------------------------------------------------------------------------------------------- numbers
class Src:
    """Доступ к числам по точечному пути; ненайденное запоминается в MISSING."""

    def __init__(self, data: dict, name: str):
        self.d = data
        self.name = name

    def __call__(self, path: str):
        cur = self.d
        for part in path.split("."):
            if isinstance(cur, list):
                try:
                    cur = cur[int(part)]
                    continue
                except (ValueError, IndexError):
                    cur = None
                    break
            if not isinstance(cur, dict) or part not in cur:
                cur = None
                break
            cur = cur[part]
        if cur is None:
            MISSING.append(f"{self.name}:{path}")
            return None
        USED[path] = cur
        return cur


def num(x, d: int = 1) -> str:
    """Число с точкой и настоящим минусом; None -> «—» (и уже записано в MISSING)."""
    if x is None:
        return "—"
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, int) and d == 0:
        s = str(x)
    elif isinstance(x, (int, float)):
        s = f"{float(x):.{d}f}"
    else:
        return str(x)
    return s.replace("-", "−")


def sgn(x, d: int = 1) -> str:
    if x is None:
        return "—"
    s = num(x, d)
    return s if s.startswith("−") else "+" + s


def ci(pair, d: int = 1, signed: bool = False) -> str:
    if not pair or pair[0] is None or pair[1] is None:
        MISSING.append("ci")
        return "[—]"
    f = sgn if signed else num
    return f"[{f(pair[0], d)}; {f(pair[1], d)}]"


def pl(n, one: str, few: str, many: str) -> str:
    """Число со словом в нужной форме: 1 событие, 3 события, 63 события, 11 событий."""
    if n is None:
        return "— " + many
    m = int(n)
    w = one if m % 10 == 1 and m % 100 != 11 else few if 2 <= m % 10 <= 4 and not 12 <= m % 100 <= 14 else many
    return f"{m} {w}"


EV = ("событие", "события", "событий")
SEG = ("сегмент", "сегмента", "сегментов")
TAB = ("выходная таблица", "выходные таблицы", "выходных таблиц")
PX = ("пиксель", "пикселя", "пикселей")


def rng(a, b, d: int = 1) -> str:
    return f"{num(a, d)}–{num(b, d)}"


def pct(x) -> str:
    return num(x * 100 if x is not None and x <= 1.0 else x, 0) + " %"


def load() -> dict:
    """Все числа, которые идут в деку, речь, демо и вопросы."""
    N = Src(json.loads(FN.read_text(encoding="utf-8")), "final_numbers")
    c = "case."
    k: dict = {}
    # --- данные и отбор
    k["rows"] = N(c + "csv.rows"); k["fields"] = N(c + "csv.fields"); k["events"] = N(c + "csv.events")
    for s in ("S1_GPGP2018", "S2_SARGASSO_MSM41", "S3_SE_NORTH_SEA", "S4_BLACK_SEA_DOORS3"):
        k["ev_" + s[:2]] = N(c + f"csv.events_by_source.{s}")
    k["scope_all_litter"] = N(c + "csv.rows_by_scope.all_litter")
    k["scope_total_plastic"] = N(c + "csv.rows_by_scope.total_plastic")
    sel = c + "selection.S2."
    for key in ("accepted_rows", "rejected_rows", "rej_item_observation", "rej_plastic_category", "rej_all_litter",
                "rej_fisheries", "rej_aerial", "rej_other_source"):
        k["s2_" + key] = N(sel + key)
    k["s1_accepted_rows"] = N(c + "selection.S1.accepted_rows")
    t = c + "conc.S2.target."
    for key in ("n_events", "n_days", "date_min", "date_max", "c_median", "c_min", "c_max", "n_min", "n_max",
                "n_median", "a_min", "a_max", "n_zero"):
        k["t_" + key] = N(t + key)
    k["s1_c_median"] = N(c + "conc.S1.target.c_median")
    k["s1_n_events"] = N(c + "conc.S1.target.n_events")
    # --- пары
    p = c + "pairs."
    for key in ("events", "candidate_rows", "candidate_rows_with_scene", "events_accept_meta", "rows_accept_meta",
                "quality_accept", "quality_reject", "reject_glint", "reject_cloud", "reject_cloud_qa_suspect",
                "reject_coverage", "events_accept_drift", "max_dt_h_typical", "tolerance_buffer_km",
                "unknown_time_extra_h", "stage_metadata", "stage_drift", "accept_without_drift", "events_time_unknown"):
        k["p_" + key] = N(p + key)
    for sc in ("low", "typical", "high"):
        k[f"drift_{sc}_ms"] = N(p + f"drift.{sc}.current_ms")
        k[f"drift_{sc}_km"] = N(p + f"drift.{sc}.shift_median_km")
        k[f"drift_{sc}_buf3"] = N(p + f"drift.{sc}.events_buf3")
    k["glint_b11"] = N(p + "decision.glint_b11")
    for s in ("S1_GPGP2018", "S2_SARGASSO_MSM41", "S3_SE_NORTH_SEA", "S4_BLACK_SEA_DOORS3"):
        k["any1_" + s[:2]] = N(p + f"by_source.{s}.any_1")
        k["any5_" + s[:2]] = N(p + f"by_source.{s}.any_5")
        k["l1c60_" + s[:2]] = N(p + f"by_source.{s}.l_1_cloud60")
    # --- детектор
    dt = c + "detector."
    for m in ("lgbm", "rf_argmax", "fdi_ndvi_box", "fdi_threshold"):
        k[f"d_{m}_f1"] = N(dt + f"test.{m}.f1")
        k[f"d_{m}_p"] = N(dt + f"test.{m}.precision")
        k[f"d_{m}_r"] = N(dt + f"test.{m}.recall")
    k["d_lgbm_ci"] = N(dt + "test.lgbm.ci95_f1")
    k["d_lgbm_val_f1"] = N(dt + "val.lgbm.f1")
    k["d_fdi_ndvi_val_f1"] = N(dt + "val.fdi_ndvi_box.f1")
    k["d_n_scenes"] = N(dt + "test_n_scenes"); k["d_n_patches"] = N(dt + "test_n_patches"); k["d_md_px"] = N(dt + "test_md_px")
    k["d_delta"] = N(dt + "paired_vs_rf.delta_f1"); k["d_delta_ci"] = N(dt + "paired_vs_rf.ci95")
    k["d_better"] = N(dt + "paired_vs_rf.scenes_better"); k["d_worse"] = N(dt + "paired_vs_rf.scenes_worse")
    k["d_fp_total"] = N(dt + "fp_total"); k["d_fp_ship"] = N(dt + "fp_by_class.Ship.fp")
    k["d_fp_org"] = N(dt + "fp_by_class.Natural Organic Material.fp")
    k["d_fp_org_rate"] = N(dt + "fp_by_class.Natural Organic Material.rate_pct")
    k["d_fp_ship_org_pct"] = N(dt + "fp_ship_organic_pct")
    k["d_hard_bg_px"] = N(dt + "fp_hard_bg_px"); k["d_hard_bg_fp"] = N(dt + "fp_hard_bg")
    k["d_sarg_px"] = N(dt + "sargassum_px"); k["d_fdi_sarg_pct"] = N(dt + "fdi_ndvi_box_sargassum_pct")
    k["d_fn"] = N(dt + "test.lgbm.fn"); k["d_fn_top3"] = N(dt + "fn_top3")
    k["d_unlab_pct"] = N(dt + "test.lgbm.unlabelled_rate_pct")
    k["thr"] = N("l3_lgbm.threshold"); k["n_feat"] = N("l3_lgbm.n_features")
    # --- концентрация
    k["ctl_n"] = N(c + "control.n"); k["ctl_a"] = N(c + "control.area_km2"); k["ctl_v"] = N(c + "control.value")
    k["ctl_lo"] = N(c + "control.lower"); k["ctl_hi"] = N(c + "control.upper")
    k["ctl_model"] = N(c + "control.model_example"); k["ctl_err"] = N(c + "control.abs_error")
    k["cons_rows"] = N(c + "conc.consistency.rows"); k["cons_match"] = N(c + "conc.consistency.match")
    k["k_blocks"] = N(c + "conc.main_split.k_blocks"); k["buf_days"] = N(c + "conc.main_split.buffer_days")
    k["n_cand"] = N(c + "conc.protocol.n_candidates")
    k["frozen"] = N(c + "conc.final_test_frozen_text")
    for pr in ("S2", "S1"):
        b = c + f"conc.{pr}."
        k[f"{pr}_primary"] = N(b + "primary"); k[f"{pr}_n_dev"] = N(b + "n_dev"); k[f"{pr}_dev_days"] = N(b + "dev_days")
        k[f"{pr}_dev_main"] = N(b + "main.mae"); k[f"{pr}_dev_med"] = N(b + "median.mae")
        k[f"{pr}_dev_ci"] = N(b + "main.d_mae_ci95"); k[f"{pr}_dev_bonf"] = N(b + "main.d_mae_ci95_bonf")
        k[f"{pr}_dev_cov"] = N(b + "main.coverage90_pct")
        k[f"{pr}_test_days"] = N(b + "final_test.test_days")
        k[f"{pr}_min_dt"] = N(b + "final_test.min_dt_days"); k[f"{pr}_min_km"] = N(b + "final_test.min_dist_km")
        f = c + f"sections.field_test.{pr}."
        for key in ("n_test", "main_model", "main_mae", "median_mae", "d_mae", "d_mae_ci95", "main_coverage90_pct",
                    "median_coverage90_pct", "verdict", "field_estimate_model", "test_cruise_days", "main_rmse", "median_rmse"):
            k[f"{pr}_t_{key}"] = N(f + key)
    k["S2_k4"] = N(c + "conc.S2.sensitivity.k4.d_mae"); k["S2_k4_ci"] = N(c + "conc.S2.sensitivity.k4.ci95")
    k["S2_knn_dev"] = N(c + "conc.S2.knn5_log.mae")
    # схемы разбиения (утечка через соседей того же дня)
    for sch in N(c + "conc.S2.schemes") or []:
        k[f"S2_sch_{sch['split']}"] = sch
    for sch in N(c + "conc.S1.schemes") or []:
        k[f"S1_sch_{sch['split']}"] = sch
    for need in ("S2_sch_st", "S2_sch_route_buf1", "S1_sch_event", "S1_sch_route_buf1"):
        if need not in k:
            MISSING.append("final_numbers:" + need)
            k[need] = {}
    k["ft_when"] = N(c + "sections.field_test.when")
    k["ft_decision"] = N(c + "sections.field_test.decision_recorded_at")
    k["ft_rule"] = N(c + "sections.field_test.rule")
    k["ft_limitation"] = N(c + "sections.field_test.limitation")
    k["ft_not_before"] = N(c + "conc.final_test_not_before")
    # --- эксперимент
    e = c + "experiment."
    for key in ("n_pairs", "n_accept", "n_accept_s2", "n_groups", "n_black_sea_accept_zero_det", "field_min",
                "field_max", "n_needed_rho05", "n_needed_rho03", "n_s2_pairs", "n_landsat_pairs"):
        k["e_" + key] = N(e + key)
    k["e_fdi_rho"] = N(e + "fdi.rho"); k["e_fdi_ci"] = N(e + "fdi.ci95"); k["e_fdi_pperm"] = N(e + "fdi.p_perm")
    k["e_fdi_holm"] = N(e + "fdi.p_holm"); k["e_fdi_null"] = N(e + "fdi.null_median")
    k["e_ctx_rho"] = N(e + "fdi_context.rho"); k["e_res_rho"] = N(e + "fdi_strip_resid.rho")
    k["e_res_holm"] = N(e + "fdi_strip_resid.p_holm")
    k["e_pm_rho"] = N(e + "p_mean.rho"); k["e_pm_holm"] = N(e + "p_mean.p_holm")
    k["e_pow6"] = N(e + "power_min_rho.6"); k["e_pow11"] = N(e + "power_min_rho.11")
    # --- сервис и воспроизводимость
    k["zones"] = N(c + "run.export.zones"); k["exp_obs"] = N(c + "run.export.observations")
    k["exp_pairs"] = N(c + "run.export.pairs"); k["exp_bs24"] = N(c + "run.export.example_pairs_black_sea_24h")
    k["exp_detected"] = N(c + "run.export.example_zones_detected")
    k["sc_checks"] = N(c + "selfcheck.checks"); k["sc_ok"] = N(c + "selfcheck.ok"); k["sc_fail"] = N(c + "selfcheck.fail")
    k["sc_inv_ok"] = N(c + "selfcheck.invalid_ok"); k["sc_inv_total"] = N(c + "selfcheck.invalid_total")
    k["sc_p95"] = N(c + "selfcheck.p95_max_ms"); k["sc_file"] = N(c + "selfcheck.file")
    k["run_s"] = N(c + "run.total_s"); k["n_outputs"] = N(c + "run.n_outputs")
    k["fingerprint"] = N(c + "run.outputs_fingerprint_short")
    k["cc_total"] = N(c + "clean_clone.total_min"); k["cc_clone_s"] = N(c + "clean_clone.clone_s")
    k["cc_install"] = N(c + "clean_clone.install_route"); k["cc_map_s"] = N(c + "clean_clone.map_load_s")
    k["cc_file"] = N(c + "clean_clone.file")
    k["n_tests"] = N(c + "n_tests"); k["t_skipped"] = N(c + "tests.skipped"); k["t_failed"] = N(c + "tests.failed")
    k["t_seconds"] = N(c + "tests.seconds"); k["t_when"] = N(c + "tests.when"); k["n_test_funcs"] = N(c + "n_test_funcs")
    k["n_sections"] = len(N(c + "sections") or {})
    # --- детектор на снимках пар: текущий режим (без гармонизации), решение по MARIDA val, визуальная разметка
    dc = c + "detector_current."
    for key in ("n_crops", "n_obj", "n_in_strip", "n_ship", "n_other_single", "n_strips_with_obj", "harmonize"):
        k["dc_" + key] = N(dc + key)
    for key in ("f1_none", "f1_per_scene", "delta_per_scene", "delta_per_scene_ci95"):
        k["hv_" + key] = N(dc + "harmonize_val." + key)
    for key in ("n_labelled", "h_out_n", "h_out_k", "h_out_precision_pct", "h_out_ci95_pct", "kappa"):
        k["vr_" + key] = N(dc + "visual_review." + key)
    # --- прежний режим (с гармонизацией) — только как основание отказа
    r = c + "detector_review."
    for key in ("n_crops", "n_obj", "n_in_strip", "wo_he460_n_obj", "wo_he460_false_share_pct", "wo_he460_glint_pct",
                "wo_he460_cloud_pct", "glint_rejected_n_crops", "glint_rejected_n_in_strip", "glint_rejected_in_strip_false",
                "he460_n_obj", "he460_n_in_strip", "he460_n_out_strip", "no_harmonize_n_obj", "accept_n_in_strip"):
        k["dr_" + key] = N(r + key)
    # --- подбор снимка и геометрия трансект
    f = c + "pairfinder."
    k["pf_window_h"] = N(f + "window_h"); k["pf_naive"] = N(f + "naive"); k["pf_naive_drift"] = N(f + "naive_sync")
    k["pf_cond"] = N(f + "cond_date_only"); k["pf_cond_ok"] = N(f + "cond_quality_accept")
    k["pf_fc_n"] = N(f + "forecast_n"); k["pf_fc_hit"] = N(f + "forecast_hit")
    k["g_events"] = N(c + "geometry.n_events"); k["g_segments"] = N(c + "geometry.n_segments")
    k["g_multi"] = N(c + "geometry.n_multi")
    # --- §11 расследование данных (case.sections.search / adis_pairs / labeled_data / detector_v2 / baselines / quantity / oil)
    sr = c + "sections.search."
    k["sr_csv_A"] = N(sr + "csv_A"); k["sr_csv_C"] = N(sr + "csv_C"); k["sr_events"] = N(sr + "events_total")
    for s in ("S1", "S2", "S3", "S4", "ADIS"):
        for key in ("A", "C", "D", "rows"):
            k[f"sr_{s}_{key}"] = N(sr + f"by_source.{s}.{key}")
    k["sr_S2_noscene"] = N(sr + "by_source.S2.d_reasons.снимка нет")
    k["sr_S4_noscene"] = N(sr + "by_source.S4.d_reasons.снимка нет")
    k["sr_S3_cloud"] = N(sr + "by_source.S3.d_reasons.облака")
    k["sr_S3_foot"] = N(sr + "by_source.S3.d_reasons.маршрут вне снимка")
    ad = c + "sections.adis_pairs."
    for key in ("segments", "scenes", "A", "C", "D", "A_passes", "A_dt_le1h", "A_with_items", "A_with_items_det_px",
                "A_with_items_det_shifted", "A_with_items_density_min", "A_with_items_density_max", "A_ship_found", "A_zero",
                "A_eval", "A_not_eval", "A_with_items_eval", "A_with_items_eval_det_px", "A_zero_eval",
                "wind_split_ms", "dt_max_h", "coverage_frac_text", "largest_item_pct_px", "A_items_gt50", "A_survey_km2"):
        k["ad_" + key] = N(ad + key)
    for w in ("calm", "windy"):
        for key in ("pairs", "px", "strip_km2", "px_per_km2", "zone_obj", "zone_obj_per_km2"):
            k[f"ad_{w}_{key}"] = N(ad + f"fa_{w}.{key}")
    ld = c + "sections.labeled_data."
    for key in ("b_new_acq", "leaks_total", "dup_excluded", "content_match", "content_checked", "neg_sources_rejected"):
        k["ld_" + key] = N(ld + key)
    k["ld_plp"] = N(ld + "plp.acq_B"); k["ld_fo"] = N(ld + "floatingobjects.acq_B"); k["ld_fo_reg"] = N(ld + "floatingobjects.regions")
    k["ld_cz_win"] = N(ld + "cozar.windows"); k["ld_cz_acq"] = N(ld + "cozar.acq")
    k["ld_ves_boxes"] = N(ld + "vessels.boxes"); k["ld_ves_acq"] = N(ld + "vessels.acq")
    k["ld_ves_flag"] = N(ld + "vessels.boxes_flagged"); k["ld_ves_pct"] = N(ld + "vessels.boxes_flagged_pct")
    k["ld_cl_scenes"] = N(ld + "clouds.scenes"); k["ld_cl_px"] = N(ld + "clouds.px_cloud"); k["ld_cl_flag"] = N(ld + "clouds.cloud_px_flagged")
    k["ld_zs_ref"] = N(ld + "zero_shot.refined_B_recall_pct"); k["ld_zs_plp"] = N(ld + "zero_shot.plp_plastic_recall_pct")
    v2 = c + "sections.detector_v2."
    for key in ("n_variants", "n_seeds", "n_accept", "need_df1", "df1_min", "df1_max", "current_model"):
        k["v2_" + key] = N(v2 + key)
    for m in ("reference", "vessels_d", "mix"):
        for key in ("cozar_pct", "vessels_pct", "cozar_hit", "cozar_n"):
            k[f"v2_{m}_{key}"] = N(v2 + f"{m}.{key}")
    k["v2_mix_df1"] = N(v2 + "mix.df1")
    k["v2_note"] = N(v2 + "decision_note"); k["v2_n_control"] = N(v2 + "n_control")
    for key in ("n", "cozar_pct_min", "cozar_pct_max", "vessels_pct_min", "vessels_pct_max", "water_pct_min", "water_pct_max",
                "df1_min", "df1_max", "foam_fa_max", "foam_n"):
        k["nb_" + key] = N(v2 + "natural_b." + key)
    k["v2_ref_foam"] = N(v2 + "reference_foam_fa"); k["v2_vd_foam"] = N(v2 + "vessels_d.foam_fa")
    for key in ("cozar_ge3px_pct", "cozar_pixel_pct", "pairs_d_fa", "pairs_d_n", "pairs_d_acq_flagged", "pairs_d_acq_total"):
        k["v2_ref_" + key] = N(v2 + "reference." + key)
    ic = c + "sections.independent_check."
    for key in ("pairs.n", "routes_gt50", "routes_same_day_s2", "plp.rho_fdi"):
        k["ic_" + key.replace(".", "_")] = N(ic + key)
    sz = c + "sections.scene_zones."
    for key in ("n_zones", "n_scenes_eval", "n_scenes", "by_level_b", "by_unverified", "by_insufficient", "by_not_detected", "by_not_informative",
                "demo.tile", "demo.date", "demo.n_zones", "demo.n_zones_cozar", "demo.det_pixels", "demo.mados_votes",
                "example.zone_id", "example.n_cozar", "example.area_km2", "example.susp_m2", "example.n_px", "example.lwd_m2_km2",
                "example.water_pct", "example.prob_mean", "example.prob_max", "example.sha256_short",
                "field_nearby.distance_km", "field_nearby.date", "field_nearby.c", "field_nearby.lo", "field_nearby.hi",
                "zone_main_status",
                "export.ui", "export.csv", "export.geojson"):
        k["sz_" + key.replace(".", "_")] = N(sz + key)
    ql = c + "sections.quantity.levels."
    for key in ("pairs_place_time", "pairs_place_time_eval", "visible_signal", "visible_signal_of", "calibration_pairs",
                "calibration_needed_min", "calibration_needed_max"):
        k["ql_" + key] = N(ql + key)
    bl = c + "sections.baselines."
    for m in ("lgbm", "rf_argmax", "unet_argmax", "unet_prob"):
        k[f"bl_{m}"] = N(bl + f"test.{m}.f1")
    k["bl_unet_ci"] = N(bl + "test.unet_argmax.ci95"); k["bl_delta"] = N(bl + "test_lgbm_minus_unet")
    k["bl_delta_ci"] = N(bl + "test_lgbm_minus_unet_ci95"); k["bl_better"] = N(bl + "test_scenes_lgbm_better")
    k["bl_n_scenes"] = N(bl + "test_n_scenes"); k["bl_unet_val"] = N(bl + "val.unet_argmax.f1")
    k["bl_ship_unet"] = N(bl + "test_ship_fp_unet"); k["bl_ship_lgbm"] = N(bl + "test_ship_fp_lgbm")
    k["bl_pairs_unet"] = N(bl + "pairs_unet_obj"); k["bl_pairs_unet_strip"] = N(bl + "pairs_unet_in_strip")
    qn = c + "sections.quantity."
    for key in ("pooled_N", "pooled_A_km2", "pooled_C", "lo95", "hi95"):
        k["q_" + key] = N(qn + "field_S2." + key)
    k["q_live_n"] = N(qn + "coverage_live.n_scenes"); k["q_live_med"] = N(qn + "coverage_live.ppm_median")
    k["q_thr_ratio"] = N(qn + "coverage_live.thr_ratio_median")
    k["q_strips"] = N(qn + "pairs.strips"); k["q_strips_nz"] = N(qn + "pairs.strips_nonzero")
    k["q_p_nz"] = N(qn + "pairs.p_nonzero_upper95"); k["q_mult"] = N(qn + "pairs.survey_multiplier")
    for key in ("n_pairs_k_x2_min", "n_pairs_k_x2_max", "n_pairs_r05", "n_pairs_r03", "floor_factor", "median_factor", "pairs_A_with_S_pos"):
        k["q_" + key] = N(qn + "calibration." + key)
    k["q_sc_ratio"] = N(qn + "scenario.spread_ratio"); k["q_sc_reason"] = N(qn + "scenario.reason")
    k["q_an_text"] = N(qn + "analogy.text"); k["q_an_not"] = N(qn + "analogy.why_not_here")
    k["q_cc_rho"] = N(qn + "cell_calibration.v3_rho"); k["q_cc_cells"] = N(qn + "cell_calibration.v3_cells")
    k["q_cc_dates"] = N(qn + "cell_calibration.same_period_common_dates")
    k["q_quote"] = N(qn + "cozar2024_quote.text"); k["q_quote_ref"] = N(qn + "cozar2024_quote.ref")
    k["q_quote_short"] = N(qn + "cozar2024_quote.short"); k["q_quote_doi"] = N(qn + "cozar2024_quote.doi")
    k["ad_seg_total"] = N(c + "sections.adis_pairs.adis_segments_total")
    k["ad_seg_items"] = N(c + "sections.adis_pairs.adis_segments_with_items")
    k["ad_vhr"] = N(c + "sections.adis_pairs.adis_vhr_same_day")
    oi = c + "sections.oil."
    for key in ("val_f1", "val_osi_f1", "test_f1", "test_osi_f1", "test_f1_ci95", "test_precision", "test_recall"):
        k["oil_" + key] = N(oi + key)
    k["oil_w1_km2"] = N(oi + "wakashio.0.oil_km2"); k["oil_w2_km2"] = N(oi + "wakashio.1.oil_km2")
    k["oil_w1_date"] = N(oi + "wakashio.0.date"); k["oil_w2_date"] = N(oi + "wakashio.1.date")
    # --- основная часть деки §32 Б: поле с честным интервалом, ADIS, счётчик по фото, профили (материал · размер · единица)
    fs = qn + "field_S2."
    for key in ("boot_lo95", "boot_hi95", "event_lo95", "event_hi95", "event_n_test", "test_mean", "nb2_lo95", "nb2_hi95"):
        k["qf_" + key] = N(fs + key)
    k["qf_event_cov"] = N(fs + "event_cov_test")
    k["q_sd_day"] = N(qn + "variance_S2.sd_within_day")
    af = qn + "adis_forecast."
    for key in ("C", "lo", "hi", "n_segments", "profile", "test_n", "test_mae_median", "test_mae_b1", "verdict", "zero_pct"):
        k["af_" + key] = N(af + key)
    for key in ("C", "lo_typ", "hi_typ", "source"):
        k["afa_" + key] = N(af + "authors_calibrated." + key)
    for pr in ("S1", "S2", "S3", "S4"):
        k[f"pr_{pr}_size"] = N(qn + f"profiles.{pr}.size_class"); k[f"pr_{pr}_n"] = N(qn + f"profiles.{pr}.n")
    pc = c + "sections.photo_count."
    for key in ("n_images", "mae", "mae_ci95", "ap50", "exact_pct", "baseline_median_count", "license"):
        k["pc_" + key] = N(pc + "frame." + key)
    for key in ("dataset", "gsd_m", "frame_area_m2", "n_test_frames", "count_mae", "density_mae_km2", "baseline_density_mae_km2",
                "license"):
        k["pa_" + key] = N(pc + "area." + key)
    k["sz_demo_scene_id"] = N(sz + "demo.scene_id"); k["sz_demo_wind"] = N(sz + "demo.wind10m_ms")
    k["sz_demo_marida_same_tile"] = N(sz + "demo.marida_same_tile"); k["sz_demo_mados_verdict"] = N(sz + "demo.mados_verdict")
    k["mt_n_scenes"] = N(c + "sections.marida_test.n_scenes")
    k["S2_dev_median_c"] = N(c + "conc.S2.dev_median_c")
    return k


def adis_size(profile) -> str:
    """Размерный класс из строки профиля ADIS (например «> 10 см»)."""
    import re
    m = re.search(r">\s?\d+(?:[.,]\d+)?\s?см", str(profile or ""))
    return m.group(0).replace(">", "> ").replace(">  ", "> ") if m else "—"


def size_ru(s) -> str:
    """Размерный класс профиля из final_numbers (например «>2 cm (macro)») по-русски: «> 2 см»."""
    if not s:
        return "—"
    s = str(s).split(" (")[0].replace("cm", "см").replace("-", "–")
    return s.replace(">", "> ").replace(">  ", "> ")


def search_slides(k: dict) -> list[dict]:
    """§11 «Расследование данных»: 4 слайда основной части (воронка, пары ADIS, B/D, разбор ошибок на сложном фоне)
    и 3 слайда приложения (детектор v2 и бейзлайны, количество, нефть). Формулировки — INBOX §15."""
    S = []
    S.append(dict(
        section="Как мы искали данные",
        title=f"Пайплайн поиска данных: {num(k['sr_events'], 0)} событий CSV → пар уровня A {num(k['sr_csv_A'], 0)}; ADIS — {num(k['ad_A'], 0)} пар по месту и времени",
        bullets=[
            "Уровни: A — снимок и полевое число связаны по времени, месту, площади и категории; B — подтверждённая разметка скопления; C — кандидат; D — отвергнутый или отрицательный пример",
            "Детектор учим только на B + проверенных D; «снимок → шт./км²» — только на калибровочных парах; C меткой не становится",
            f"Пластик: снимка нет у {num(k['sr_S2_noscene'], 0)} из {num(k['sr_S2_rows'], 0)} событий Саргассова моря, у S1 датчики не видят предметы; Северное море — облака {num(k['sr_S3_cloud'], 0)}, маршрут вне снимка {num(k['sr_S3_foot'], 0)}; Чёрное море — C {num(k['sr_S4_C'], 0)} (время и площадь трансект не опубликованы)",
            f"Стоп-правило: нет A за пилот — поиск по дрейфу не расширяем. Нашли ADIS (The Ocean Cleanup): {num(k['ad_segments'], 0)} отрезков → A {num(k['ad_A'], 0)}, C {num(k['ad_C'], 0)}, D {num(k['ad_D'], 0)}",
        ],
        image="funnel",
        caption="Воронка «событие → снимок → маски → синхронность»; каждая попытка — docs/SEARCH_LOG.md, хронология — docs/PIPELINE.md",
        source="docs/PIPELINE.md; docs/SEARCH_LOG.md; reports/search/*_candidates.csv (final_numbers → case.sections.search)",
        speech=(f"Главное в работе — как мы искали данные. CSV — это подсчёты вдоль маршрутов, поэтому каждую находку мы помечаем уровнем: "
                f"A, B, C или D. У событий CSV пар уровня A — {num(k['sr_csv_A'], 0)}: для пластика снимков нет, в Северном море облака, "
                f"в Чёрном время трансект не опубликовано. Тогда мы нашли открытые данные ADIS: {num(k['ad_A'], 0)} пар по месту и времени."),
    ))
    S.append(dict(
        section="Пары по месту и времени (ADIS)",
        title=f"ADIS: {num(k['ad_A'], 0)} пар по месту и времени; на {num(k['ad_A_with_items'], 0)} отрезках с единичными предметами детектор их не увидел",
        bullets=[
            f"Камера судна, счёт предметов по размерам, время GPS; |dt| ≤ {num(k['ad_dt_max_h'], 0)} ч; ≈ {num(k['ad_A_passes'], 0)} независимых проходов, время у {num(k['ad_A_ship_found'], 0)} пар подтверждено самим судном на снимке",
            f"Детектор оценивается на {num(k['ad_A_eval'], 0)} из {num(k['ad_A'], 0)} (на {num(k['ad_A_not_eval'], 0)} — низкое солнце или слабый сигнал воды); из {num(k['ad_A_with_items'], 0)} отрезков с предметами ({num(k['ad_A_with_items_density_min'], 1)}–{num(k['ad_A_with_items_density_max'], 1)} шт./км²) оценивается {num(k['ad_A_with_items_eval'], 0)} — на всех {num(k['ad_A_with_items_eval_det_px'], 0)} пикселей",
            f"Согласуется с физикой: доля покрытия ≈ {k['ad_coverage_frac_text']}, предмет — {num(k['ad_largest_item_pct_px'], 1)} % пикселя; но общий предел для всех скоплений это не задаёт — плотные полосы эти пары не проверяют",
            f"Ложные при полевом нуле ({num(k['ad_A_zero_eval'], 0)} пар): ветер < {num(k['ad_wind_split_ms'], 0)} м/с — {num(k['ad_calm_px'], 0)} пикселей на {num(k['ad_calm_strip_km2'], 1)} км²; сильнее — {num(k['ad_windy_px'], 0)} на {num(k['ad_windy_strip_km2'], 1)} км², {num(k['ad_windy_zone_obj_per_km2'], 2)} объекта/км² в зоне (барашки)",
            f"Три уровня: по месту и времени {num(k['ql_pairs_place_time'], 0)} · видимый сигнал {num(k['ql_visible_signal'], 0)} из {num(k['ql_visible_signal_of'], 0)} · калибровочных «снимок → шт./км²» {num(k['ql_calibration_pairs'], 0)} (нужно {num(k['ql_calibration_needed_min'], 0)}–{num(k['ql_calibration_needed_max'], 0)})",
            f"Независимая проверка: {num(k['ic_pairs_n'], 0)} наглядных пар по предметам > 50 см (другая выборка, наши — > 5 см); S2 в тот же день — у {num(k['ic_routes_same_day_s2'], 0)} из {num(k['ic_routes_gt50'], 0)} маршрутов",
        ],
        image="independent",
        caption="Независимая проверка: пары ADIS ↔ S2 по предметам > 50 см — маршрут судна (жёлтый), поиск с учётом дрейфа (голубой); предметы на снимке не различимы",
        source="reports/search/adis_candidates.csv, reports/search/adis.md (final_numbers → case.sections.adis_pairs, quantity.levels)",
        speech=(f"Пар по месту и времени — {num(k['ad_A'], 0)}, но это не калибровочные пары: на {num(k['ad_A_with_items'], 0)} отрезках с "
                f"предметами детектор их не увидел, предмет меньше процента пикселя. Калибровочных пар «снимок — штуки» "
                f"{num(k['ql_calibration_pairs'], 0)}: мы проверили {num(k['sr_events'], 0)} событий CSV и {num(k['ad_segments'], 0)} синхронных "
                f"отрезков ADIS. У авторов крупнейшего набора полос тоже: «{k['q_quote_short']}» (Cózar и др., 2024, doi {k['q_quote_doi']})."),
    ))
    S.append(dict(
        section="Новые размеченные данные B/D",
        title=f"Новые данные: B — {num(k['ld_b_new_acq'], 0)} съёмок и {num(k['ld_cz_win'], 0)} нитей Cózar, D — {num(k['ld_ves_boxes'], 0)} судов и облака; с MARIDA совпадений {num(k['ld_leaks_total'], 0)}",
        bullets=[
            f"B: PLP {num(k['ld_plp'], 0)} съёмок (мишени), FloatingObjects {num(k['ld_fo'], 0)} съёмок ({num(k['ld_fo_reg'], 0)} регионов), Cózar 2024 — {num(k['ld_cz_win'], 0)} окон на {num(k['ld_cz_acq'], 0)} съёмках",
            f"D: суда у Финляндии — {num(k['ld_ves_boxes'], 0)} рамок, {num(k['ld_ves_acq'], 0)} съёмок; облака — {num(k['ld_cl_scenes'], 0)} сцен; отклонено источников {num(k['ld_neg_sources_rejected'], 0)} (у каждого причина)",
            f"Утечки: с MARIDA — {num(k['ld_leaks_total'], 0)} совпадений по тайлу и дате у всех наборов; с MADOS по пикселям — PLP и FO ({num(k['ld_content_match'], 0)} из {num(k['ld_content_checked'], 0)}); Cózar, суда, облака с MADOS не сверить — у MADOS нет геопривязки",
            f"Детектор без дообучения на L2A: нити Cózar {num(k['v2_reference_cozar_pct'], 0)} % (хотя бы один пиксель нити; ≥ 3 пикс. — {num(k['v2_ref_cozar_ge3px_pct'], 0)} %, по пикселям {num(k['v2_ref_cozar_pixel_pct'], 0)} %), Refined {num(k['ld_zs_ref'], 1)} % пикселей; ложные на судах {num(k['ld_ves_pct'], 1)} %; облака — {num(k['ld_cl_flag'], 0)} пикс.",
        ],
        table=[["Набор (уровень)", "Детектор"],
               [f"Cózar, нити (B), из {num(k['v2_reference_cozar_n'], 0)}", f"{num(k['v2_reference_cozar_hit'], 0)}"],
               ["PLP, пластик (B), % пикс.", num(k["ld_zs_plp"], 1)],
               ["Суда (D), ложные рамки", num(k["ld_ves_flag"], 0)],
               ["Облака (D), ложные пикс.", num(k["ld_cl_flag"], 0)]],
        source="reports/extra_data/registry*.csv, fp_current_detector.json (final_numbers → case.sections.labeled_data)",
        speech=(f"Для детектора мы собрали новые метки: скопления PLP, FloatingObjects и нити Cózar — B, суда и облака — D. "
                f"С MARIDA совпадений нет; с MADOS по пикселям сверены PLP и FloatingObjects. Детектор на них почти не видит нити и путает суда."),
    ))
    S.append(dict(
        section="Разбор ошибок на сложном фоне",
        title=f"Сложный фон: нити Cózar в обучении — полнота до {num(k['nb_cozar_pct_max'], 0)} %, но пена {num(k['nb_foam_fa_max'], 0)}/{num(k['nb_foam_n'], 0)} и до {num(k['nb_vessels_pct_max'], 0)} % судов становятся «мусором»",
        bullets=[
            f"Откат weights/lgbm (L2A, как в сервисе): нити Cózar {num(k['v2_reference_cozar_pct'], 0)} % (хотя бы один пиксель нити; по пикселям {num(k['v2_ref_cozar_pixel_pct'], 0)} %), ложные на судах {num(k['v2_reference_vessels_pct'], 0)} %; D пар {num(k['v2_ref_pairs_d_fa'], 0)} из {num(k['v2_ref_pairs_d_n'], 0)} объектов, все на {num(k['v2_ref_pairs_d_acq_flagged'], 0)} съёмке из {num(k['v2_ref_pairs_d_acq_total'], 0)}",
            f"+ естественные B (Cózar, линии FloatingObjects): нити {num(k['nb_cozar_pct_min'], 0)}–{num(k['nb_cozar_pct_max'], 0)} %, но F1 MARIDA val {num(k['nb_df1_max'], 3)}…{num(k['nb_df1_min'], 3)}, ложные на {num(k['nb_water_pct_min'], 0)}–{num(k['nb_water_pct_max'], 0)} % чистой воды и {num(k['nb_vessels_pct_min'], 0)}–{num(k['nb_vessels_pct_max'], 0)} % судов",
            f"+ D судов: ложные на судах {num(k['v2_reference_vessels_pct'], 0)} % → {num(k['v2_vessels_d_vessels_pct'], 0)} %, на пене {num(k['v2_vd_foam'], 0)}/{num(k['nb_foam_n'], 0)}, но нити {num(k['v2_reference_cozar_pct'], 0)} % → {num(k['v2_vessels_d_cozar_pct'], 0)} %",
            f"Суда, пена и нити спектрально близки: выигрыш на одном ломает другое. Итог: {k['v2_note']}",
        ],
        table=[["Вариант", "Нити · суда · пена"],
               ["Откат weights/lgbm (нить — ≥ 1 пикс.)", f"{num(k['v2_reference_cozar_pct'], 0)} % · {num(k['v2_reference_vessels_pct'], 0)} % · {num(k['v2_ref_foam'], 0)}/{num(k['nb_foam_n'], 0)}"],
               ["+ естественные B", f"{num(k['nb_cozar_pct_min'], 0)}–{num(k['nb_cozar_pct_max'], 0)} % · {num(k['nb_vessels_pct_min'], 0)}–{num(k['nb_vessels_pct_max'], 0)} % · {num(k['nb_foam_fa_max'], 0)}/{num(k['nb_foam_n'], 0)}"],
               ["+ D судов", f"{num(k['v2_vessels_d_cozar_pct'], 0)} % · {num(k['v2_vessels_d_vessels_pct'], 0)} % · {num(k['v2_vd_foam'], 0)}/{num(k['nb_foam_n'], 0)}"]],
        table_w0=0.46,
        source="reports/detector_v2/experiments.md (снимок experiments_snapshot.json), decision.json; configs/detector_v2_eval.yaml",
        speech=(f"Главная трудность — сложный фон. Если учить детектор на естественных нитях Cózar, полнота на них растёт до "
                f"{num(k['nb_cozar_pct_max'], 0)} процентов, но пена и суда тоже становятся мусором. Если добавить суда как отрицательные, "
                f"ложные на судах падают с {num(k['v2_reference_vessels_pct'], 0)} до {num(k['v2_vessels_d_vessels_pct'], 0)} процентов, "
                f"но теряются нити. Правило, записанное заранее, не прошёл ни один вариант — остаётся прежний детектор, а его ограничение "
                f"по судам мы называем прямо."),
    ))
    # ---------------- приложение
    S.append(dict(
        section="Детектор v2 и бейзлайны",
        title=f"Дообучение на B + D: {num(k['v2_n_variants'], 0)} вариантов с новыми данными + контрольный r0 — правило не прошёл ни один; U-Net MARIDA на test — F1 {num(k['bl_unet_argmax'], 3)}",
        bullets=[
            f"Правило принятия записано до экспериментов: ΔF1 val ≥ {num(k['v2_need_df1'], 2)} и не хуже на B и D; {num(k['v2_n_seeds'], 0)} seed; test не читался",
            f"ΔF1 val от {num(k['v2_df1_min'], 3)} до {num(k['v2_df1_max'], 3)} — {k['v2_note']}",
            f"U-Net MARIDA (официальные веса): LightGBM лучше на {num(k['bl_delta'], 3)} {ci(k['bl_delta_ci'], 3)}, в {num(k['bl_better'], 0)} из {num(k['bl_n_scenes'], 0)} сцен; на снимках пар U-Net — {num(k['bl_pairs_unet'], 0)} объектов «соль-перец», в полосах {num(k['bl_pairs_unet_strip'], 0)}",
        ],
        bars=dict(title=f"F1 Marine Debris, test MARIDA ({num(k['bl_n_scenes'], 0)} сцен)", maxv=1.0, fmt=3, items=[
            ("LightGBM (основной)", k["bl_lgbm"], True),
            ("RandomForest, протокол MARIDA", k["bl_rf_argmax"], False),
            ("U-Net MARIDA, веса авторов", k["bl_unet_argmax"], False),
            ("окно FDI × NDVI", k["d_fdi_ndvi_box_f1"], False)]),
        source="reports/detector_v2/experiments.md, decision.json, unet_test.json; configs/detector_v2_eval.yaml",
        speech=(f"Ответ на вопрос о дообучении: {k['v2_note']}. Официальный U-Net MARIDA на том же test — F1 "
                f"{num(k['bl_unet_argmax'], 3)}, наш LightGBM — {num(k['bl_lgbm'], 3)}."),
    ))
    S.append(dict(
        section="Количество: поле и снимок",
        title=f"Количество: пар по месту и времени {num(k['ql_pairs_place_time'], 0)}, видимый сигнал {num(k['ql_visible_signal'], 0)}, калибровочных пар {num(k['ql_calibration_pairs'], 0)} — шт./км² только по полю",
        bullets=[
            f"Поле (измерение): C = N/A, интервал Пуассона; S2 ΣN/ΣA = {num(k['q_pooled_N'], 0)} / {num(k['q_pooled_A_km2'], 2)} км² = {num(k['q_pooled_C'], 1)} [{rng(k['q_lo95'], k['q_hi95'])}] шт./км²; на карте — медиана профиля (отложенный test)",
            f"Снимок: площадь маски и доля покрытия подозрительного материала (LWD, м²/км²); медиана на {num(k['q_live_n'], 0)} живых сценах {num(k['q_live_med'], 1)}; это не мусор и не шт./км²",
            f"Калибровка ln C = ln k + b · ln S требует калибровочных пар (по месту и времени и с сигналом) — их {num(k['ql_calibration_pairs'], 0)}; нужно {num(k['q_n_pairs_k_x2_min'], 0)}–{num(k['q_n_pairs_k_x2_max'], 0)} для k в ×/÷2, для связи r = 0.5 — {num(k['q_n_pairs_r05'], 0)}",
            f"Штуки по снимку не показываем: {k['q_sc_reason']}; перевод «площадь / размер предмета» дал бы разброс в {num(k['q_sc_ratio'], 0)} раз",
            f"Аналогия: {k['q_an_text']}. Для мусора — {k['q_an_not']}: общих дат поле × спутник {num(k['q_cc_dates'], 0)}, климатологии ρ = {num(k['q_cc_rho'], 2)} на {num(k['q_cc_cells'], 0)} ячейках",
        ],
        image="ladder",
        caption="Лестница единиц: предмет на кадре → шт./м² кадра → шт./км² маршрута → спутниковая зона; у каждой ступени своя метрика",
        source="docs/QUANTITY.md; reports/quantity/*.json (final_numbers → case.sections.quantity)",
        speech=("Количество: штуки на квадратный километр — только по полю. Со снимка — площадь маски и доля покрытия; перевода в "
                "штуки нет, калибровочных пар ноль, калибровка по ячейкам дала отрицательный результат."),
    ))
    S.append(dict(
        section="Нефтяное пятно — эксперимент",
        title=f"Нефть: на MADOS F1 {num(k['oil_test_f1'], 3)} против OSI {num(k['oil_test_osi_f1'], 3)}, но разлив Wakashio не найден — слой выключен",
        bullets=[
            f"Отдельная голова на MADOS, сплит по сценам заморожен до обучения; val F1 {num(k['oil_val_f1'], 3)}, test один раз {num(k['oil_test_f1'], 3)} {ci(k['oil_test_f1_ci95'], 3)}",
            f"Контроль на реальном L2A: Wakashio {k['oil_w1_date']} — {num(k['oil_w1_km2'], 2)} км² мелких пятен (пятно в лагуне не найдено), {k['oil_w2_date']} — {num(k['oil_w2_km2'], 2)} км² ряби",
            "Честный отказ: это не детектор нефти на L2A; слой «эксперимент», выключен по умолчанию; единица — площадь, не объём и не масса",
        ],
        big=("выкл.", "слой «Нефтяное пятно» — эксперимент; для нефти обычно нужен радар Sentinel-1"),
        source="reports/oil/test.json, control.json; docs/OIL.md",
        speech=("Нефть — отдельный эксперимент. На разметке MADOS голова лучше простого индекса, но известный разлив Wakashio на "
                "реальном снимке она не нашла. Поэтому слой выключен и подписан как эксперимент."),
    ))
    return S


# ----------------------------------------------------------------------------------------------- основная часть (§32 Б)
def main_slides(k: dict) -> list[dict]:
    """Основная часть: 13 слайдов, крупные числа, минимум текста, у каждой картинки подпись-вывод.
    kind="main": layout left (числа слева, картинка/диаграмма/таблица справа) | full (картинка во всю ширину) |
    grid (до 3 картинок) | text (крупные тезисы). notes — «подробнее» в заметках докладчика (вслух не читается)."""
    S = []
    S.append(dict(
        kind="main", layout="left", section="Задача и целевая величина",
        title="Сколько плавающего пластика в море: шт./км² — по полю, место скопления — по снимку",
        kpis=[("шт./км²", "целевая единица: материал + размерный класс + единица — профили не смешиваем")],
        bullets=["Пользователь — эколог по мониторингу: карта находок → зона на снимке → полевое число рядом → выгрузка",
                 "Детектор (класс Marine Debris MARIDA) видит любой плавающий мусор, не только пластик"],
        table=[["Профиль", "Что считаем", "Событий"],
               ["S2 Саргассово, с судна", f"суммарный пластик {size_ru(k['pr_S2_size'])}", num(k["pr_S2_n"], 0)],
               ["S1 GPGP, трал", f"суммарный пластик {size_ru(k['pr_S1_size'])}", num(k["pr_S1_n"], 0)],
               ["S3 / S4, с судна", f"весь мусор {size_ru(k['pr_S3_size'])} / {size_ru(k['pr_S4_size'])} — не пластик",
                f"{num(k['pr_S3_n'], 0)} · {num(k['pr_S4_n'], 0)}"],
               ["ADIS, камера судна", f"плавающие предметы {adis_size(k['af_profile'])}", num(k["af_n_segments"], 0)]],
        table_w=(0.3, 0.52, 0.18),
        caption="Вывод: основная целевая величина — суммарный пластик S2, шт./км²; весь мусор S3/S4 — только для экспериментов",
        source="configs/case_selection.yaml; reports/quantity/*.json (final_numbers → case.sections.quantity.profiles)",
        speech=("Задача — сколько плавающего пластика в море, в штуках на квадратный километр. Пользователь — эколог: он видит "
                "находки на карте, открывает зону на снимке, сверяет с полем и выгружает результат. Целевая величина — это "
                "материал, размер и единица вместе; профили разных методов мы не смешиваем."),
        notes=(f"Основной профиль — S2: суммарный пластик {size_ru(k['pr_S2_size'])}, визуальный учёт с судна, {pl(k['pr_S2_n'], *EV)}. "
               f"S1 — трал {size_ru(k['pr_S1_size'])}, только полевая проверка. S3/S4 — весь мусор, не пластик. ADIS — камера судна, "
               f"{k['af_profile']}."),
    ))
    S.append(dict(
        kind="main", layout="left", section="Данные и воронка",
        title=(f"{num(k['p_events'], 0)} событий CSV → {num(k['p_events_accept_drift'], 0)} синхронных пар; "
               f"калибровочных пар «снимок → шт./км²» — {num(k['ql_calibration_pairs'], 0)}"),
        kpis=[(f"{num(k['p_events'], 0)} → {num(k['p_events_accept_meta'], 0)} → {num(k['p_quality_accept'], 0)} → "
               f"{num(k['p_events_accept_drift'], 0)}",
               "события CSV → по дате и облакам → по маскам качества → синхронно с учётом дрейфа"),
              (num(k["ad_A"], 0), f"пар по месту и времени ADIS (камера судна); видимый сигнал — {num(k['ql_visible_signal'], 0)} "
                                  f"из {num(k['ql_visible_signal_of'], 0)}"),
              (num(k["ql_calibration_pairs"], 0), f"калибровочных пар; для коэффициента в ×/÷2 нужно "
                                                  f"{num(k['q_n_pairs_k_x2_min'], 0)}–{num(k['q_n_pairs_k_x2_max'], 0)}")],
        bullets=[f"Cózar и др., 2024: «{k['q_quote_short']}»"],
        image="funnel",
        caption="Вывод: для пластика снимков в окне нет, а где снимок и счёт совпали — предметы меньше пикселя; калибровать не на чем",
        source="docs/img/funnel.png; reports/search/*.md; reports/case_pairs/summary.md (final_numbers → case.pairs, case.sections.search)",
        speech=(f"Мы искали пары «снимок плюс полевой счёт». Из {num(k['p_events'], 0)} событий CSV синхронных с учётом дрейфа — "
                f"{num(k['p_events_accept_drift'], 0)}. В открытых данных ADIS нашли {num(k['ad_A'], 0)} пар по месту и времени, но "
                f"предметы там меньше пикселя. Калибровочных пар — {num(k['ql_calibration_pairs'], 0)}; у авторов крупнейшего "
                f"набора полос, Cózar 2024, тоже только искусственные мишени."),
        notes=(f"Цитата полностью: «{k['q_quote']}» — {k['q_quote_ref']}, doi {k['q_quote_doi']}. На "
               f"{num(k['ad_A_with_items'], 0)} синхронных отрезках ADIS с единичными предметами детектор предметы не увидел; это "
               f"согласуется с физикой (доля покрытия ≈ {k['ad_coverage_frac_text']}), но не задаёт общий предел для всех скоплений."),
    ))
    S.append(dict(
        kind="main", layout="full", section="Решение: лестница единиц",
        title="Решение — лестница единиц: предмет на фото → плотность на кадре → шт./км² маршрута → зона на снимке",
        image="ladder",
        caption="Вывод: у каждой ступени своя проверка и своя метрика; между ступенями без калибровки не переходим",
        source="docs/QUANTITY.md; docs/img/units_ladder.png (final_numbers → case.sections.photo_count, quantity, scene_zones)",
        speech=("Поэтому решение — лестница единиц. На фото счётчик считает предметы. Если известна площадь кадра — это "
                "плотность. По маршруту судна — штуки на квадратный километр. Со спутника — место и площадь зоны. У каждой "
                "ступени своя проверка, и без калибровки мы между ними не прыгаем."),
        notes="Сквозной путь в сервисе: спутник находит зону → детальное фото над ней → счётчик → шт./км² кадра → сверка с полем.",
    ))
    S.append(dict(
        kind="main", layout="left", section="Детектор",
        title=f"Детектор скоплений по Sentinel-2: F1 {num(k['d_lgbm_f1'], 3)} на test MARIDA — лучше RandomForest, U-Net авторов и индексов",
        kpis=[(num(k["d_lgbm_f1"], 3), f"F1 на test MARIDA {ci(k['d_lgbm_ci'], 3)}, {num(k['mt_n_scenes'], 0)} сцен, test один раз"),
              (sgn(k["d_delta"], 3), f"к RandomForest по протоколу MARIDA {ci(k['d_delta_ci'], 3)}")],
        bullets=[f"Дообучение на новых B/D: {num(k['v2_n_variants'], 0)} вариантов — правило, записанное заранее, не прошёл ни один; остаётся weights/lgbm"],
        bars=dict(title=f"F1 Marine Debris, test MARIDA ({num(k['bl_n_scenes'], 0)} сцен)", maxv=1.0, fmt=3, items=[
            ("LightGBM (наш)", k["bl_lgbm"], True),
            ("RandomForest, протокол MARIDA", k["bl_rf_argmax"], False),
            ("U-Net MARIDA, веса авторов", k["bl_unet_argmax"], False),
            ("окно FDI × NDVI", k["d_fdi_ndvi_box_f1"], False)],
            note="Вывод: на том же test пиксельный LightGBM лучше всех бейзлайнов; пороги индексов с val не переносятся"),
        source="reports/case_detector/compare.md, metrics.json; reports/detector_v2/unet_test.json, decision.json",
        speech=(f"Первая ступень со спутника — детектор скоплений. Пиксельный LightGBM на test MARIDA даёт F1 {num(k['d_lgbm_f1'], 3)}: "
                f"RandomForest по протоколу авторов — {num(k['d_rf_argmax_f1'], 3)}, их U-Net — {num(k['bl_unet_argmax'], 3)}. "
                f"Порог выбран на val, test посчитан один раз. Класс — любой плавающий мусор, не только пластик."),
        notes=(f"Ошибки: из {num(k['d_fp_total'], 0)} ложных {num(k['d_fp_ship_org_pct'], 0)} % — суда и природная органика. "
               f"Дообучение: ΔF1 val от {num(k['v2_df1_min'], 3)} до {num(k['v2_df1_max'], 3)} — {k['v2_note']}."),
    ))
    S.append(dict(
        kind="main", layout="left", section="Разбор ошибок на сложном фоне",
        title="Сложный фон: нити, пена и суда спектрально близки — выигрыш на одном ломает другое",
        kpis=[(f"{num(k['nb_cozar_pct_max'], 0)} %", "полнота на нитях Cózar, если учить на естественных скоплениях B"),
              (f"{num(k['nb_foam_fa_max'], 0)}/{num(k['nb_foam_n'], 0)}", "ложных на пене у того же варианта")],
        table=[["Вариант", "Нити · суда · пена"],
               ["В сервисе: weights/lgbm", f"{num(k['v2_reference_cozar_pct'], 0)} % · {num(k['v2_reference_vessels_pct'], 0)} % · {num(k['v2_ref_foam'], 0)}/{num(k['nb_foam_n'], 0)}"],
               ["+ естественные B", f"{num(k['nb_cozar_pct_min'], 0)}–{num(k['nb_cozar_pct_max'], 0)} % · {num(k['nb_vessels_pct_min'], 0)}–{num(k['nb_vessels_pct_max'], 0)} % · {num(k['nb_foam_fa_max'], 0)}/{num(k['nb_foam_n'], 0)}"],
               ["+ суда как отрицательные D", f"{num(k['v2_vessels_d_cozar_pct'], 0)} % · {num(k['v2_vessels_d_vessels_pct'], 0)} % · {num(k['v2_vd_foam'], 0)}/{num(k['nb_foam_n'], 0)}"]],
        table_w=(0.5, 0.5),
        caption="Вывод: правило принятия не прошёл ни один вариант — оставили прежний детектор и назвали его слабость (суда)",
        source="reports/detector_v2/experiments.md, decision.json; configs/detector_v2_eval.yaml",
        speech=(f"Главная трудность — сложный фон. Если учить на естественных нитях, полнота растёт до {num(k['nb_cozar_pct_max'], 0)} "
                f"процентов, но пена и суда тоже становятся мусором. Добавим суда как отрицательные — теряются нити. Правило, "
                f"записанное заранее, не прошёл ни один вариант, поэтому в сервисе прежний детектор, а суда мы отсеиваем фильтрами."),
        notes=("Нить — хотя бы один пиксель детектора на окне нити каталога Cózar 2024; суда — рамки судов (D); пена — "
               "окна пены. Test MARIDA при подборе не читался."),
    ))
    S.append(dict(
        kind="main", layout="left", section="Концентрация по полю",
        title=f"Главный количественный результат: {num(k['q_pooled_C'], 1)} шт./км² [{rng(k['qf_boot_lo95'], k['qf_boot_hi95'])}] — пластик S2 по полю",
        kpis=[(num(k["q_pooled_C"], 1), f"шт./км², суммарный пластик {size_ru(k['pr_S2_size'])}: {num(k['q_pooled_N'], 0)} шт. / "
                                        f"{num(k['q_pooled_A_km2'], 2)} км²; 95 % по дням рейса [{rng(k['qf_boot_lo95'], k['qf_boot_hi95'])}]"),
              (num(k["af_C"], 2), f"шт./км², ADIS, предметы {adis_size(k['af_profile'])} [{rng(k['af_lo'], k['af_hi'], 2)}]; калибровка авторов по тралу — "
                                  f"{num(k['afa_C'], 2)}")],
        bullets=[f"На карте — медиана профиля {num(k['S2_dev_median_c'], 1)} шт./км² (типичное событие); "
                 f"{num(k['q_pooled_C'], 1)} — ΣN/ΣA всех событий; ошибка медианы на отложенном test — MAE {num(k['S2_t_median_mae'], 1)}"],
        table=[["Отложенный test", "Модель", "Медиана"],
               [f"S2, MAE ({num(k['S2_t_n_test'], 0)} событий)", num(k["S2_t_main_mae"], 1), num(k["S2_t_median_mae"], 1)],
               [f"S1, MAE ({num(k['S1_t_n_test'], 0)} событий)", num(k["S1_t_main_mae"], 1), num(k["S1_t_median_mae"], 1)],
               ["S2, покрытие 90 %-интервала", f"{num(k['S2_t_main_coverage90_pct'], 0)} %", f"{num(k['S2_t_median_coverage90_pct'], 0)} %"]],
        table_w=(0.5, 0.25, 0.25),
        caption="Вывод: модели не лучше медианы на отложенном test → на карте медиана профиля с честным интервалом",
        source="reports/case_conc/final_test.json; reports/quantity/field_intervals.md, adis_forecast.md",
        speech=(f"Главный количественный результат — по полю: C равно N на A. Для пластика S2 — {num(k['q_pooled_C'], 1)} штуки на "
                f"квадратный километр, 95 процентов по дням рейса — от {num(k['qf_boot_lo95'], 1)} до {num(k['qf_boot_hi95'], 1)}. "
                f"На отложенном test модели не лучше медианы — {num(k['S2_t_main_mae'], 1)} против {num(k['S2_t_median_mae'], 1)}, "
                f"поэтому на карте медиана."),
        notes=(f"Интервал Пуассона [{rng(k['q_lo95'], k['q_hi95'])}] — только ошибка счёта; разброс между днями (sd ln C ≈ "
               f"{num(k['q_sd_day'], 2)}) он не учитывает. Одно новое место: {rng(k['qf_event_lo95'], k['qf_event_hi95'])} шт./км². "
               f"ADIS: модели по регионам не лучше медианы ({k['af_verdict']}). Калибровка авторов ADIS — {k['afa_source']}, "
               f"типичный интервал отрезка {rng(k['afa_lo_typ'], k['afa_hi_typ'])}; наше число без поправки не заменяем."),
    ))
    S.append(dict(
        kind="main", layout="left", section="Счётчик предметов по фото",
        title=f"Счётчик по фото: ошибка {num(k['pc_mae'], 2)} шт./кадр на отложенных сессиях; при известной площади — шт./км² кадра",
        kpis=[(num(k["pc_mae"], 2), f"шт./кадр — ошибка числа, {num(k['pc_n_images'], 0)} фото FML {ci(k['pc_mae_ci95'], 2)}; "
                                    f"медиана без модели — {num(k['pc_baseline_median_count'], 2)}"),
              (num(k["pa_density_mae_km2"], 0), f"шт./км² — ошибка плотности на кадре {num(k['pa_frame_area_m2'], 0)} м² "
                                               f"(аэро, берег); медиана — {num(k['pa_baseline_density_mae_km2'], 0)}")],
        bullets=["Состав: один класс «мусор» — по материалам не делим («состав не определён»)"],
        image="photo",
        caption="Вывод: штуки считаем там, где предмет виден — на фото; шт./км² — только при известной площади кадра",
        source="reports/photo_count/eval_grouped.json, area_winans.json; docs/PHOTO_COUNT.md",
        speech=(f"Штуки видно только на детальном фото. Счётчик предметов ошибается на {num(k['pc_mae'], 2)} предмета на кадр на "
                f"отложенных сессиях съёмки, медиана без модели — {num(k['pc_baseline_median_count'], 2)}. Если площадь кадра известна, "
                f"как у аэросъёмки, получаем штуки на квадратный километр. Состав по материалам не выдаём."),
        notes=(f"FML (камера у воды, {k['pc_license']}): mAP@0,5 {num(k['pc_ap50'], 3)}, точное число у {num(k['pc_exact_pct'], 0)} % кадров. "
               f"Аэро: {k['pa_dataset']}, GSD {num(k['pa_gsd_m'], 2)} м, {num(k['pa_n_test_frames'], 0)} отложенных кадров, ошибка "
               f"числа {num(k['pa_count_mae'], 2)} шт./кадр ({k['pa_license']}). Это отдельный модуль, не спутник."),
    ))
    S.append(dict(
        kind="main", layout="left", section="Спутниковые зоны на отложенной сцене",
        title=(f"Отложенная сцена {k['sz_demo_tile']} ({k['sz_demo_date']}): {num(k['sz_demo_n_zones_cozar'], 0)} из "
               f"{num(k['sz_demo_n_zones'], 0)} зон совпали с нитями Cózar"),
        kpis=[(f"{num(k['sz_demo_n_zones_cozar'], 0)} / {num(k['sz_demo_n_zones'], 0)}",
               "зон детектора совпали с разметкой Cózar (уровень B); сцена не участвовала в обучении и подборе порога"),
              (f"{num(k['sz_by_level_b'], 0)} + {num(k['sz_by_unverified'], 0)}",
               f"находок детектора на {num(k['sz_n_scenes_eval'], 0)} сценах: {num(k['sz_by_level_b'], 0)} совпали с Cózar, "
               f"{num(k['sz_by_unverified'], 0)} требуют проверки; ещё {num(k['sz_by_insufficient'], 0)} — недостаточно данных или "
               f"ложные, {num(k['sz_by_not_detected'], 0)} — не обнаружено")],
        image="demo_card",
        caption=f"Вывод: снимок даёт место, площадь и статус зоны; шт./км² — «{k['sz_zone_main_status']}»",
        source="data/case/scene_zones; reports/case_demo/heldout_check.json, demo_path.json (final_numbers → case.sections.scene_zones)",
        speech=(f"Теперь спутник на сцене, которую модель не видела. На отложенной сцене {k['sz_demo_tile']} "
                f"{num(k['sz_demo_n_zones_cozar'], 0)} из {num(k['sz_demo_n_zones'], 0)} зон совпали с нитями, найденными людьми. "
                f"У зоны — площадь, доля покрытия и статус; суда, пена, блик и сильный ветер — «недостаточно данных». Штук по снимку не выдаём."),
        notes=(f"Сцена {k['sz_demo_scene_id']}: в MARIDA того же тайла нет, MADOS по содержимому — {k['sz_demo_mados_verdict']}; "
               f"ветер {num(k['sz_demo_wind'], 1)} м/с. Пример зоны {k['sz_example_zone_id']}: {num(k['sz_example_area_km2'], 2)} км², "
               f"вероятность {num(k['sz_example_prob_mean'], 2)}, модель sha256 {k['sz_example_sha256_short']}."),
    ))
    S.append(dict(
        kind="main", layout="grid", section="Сервис: путь эколога",
        title="Сервис: Земля с находками → точка → сцена и карточка зоны → студия → назад к следующей точке",
        images=[("svc_earth", "1. Обзор Земли: находки обработанных сцен сразу, без формы"),
                ("svc_card", "2. Точка → сцена и карточка: что найдено, когда, площадь, уверенность"),
                ("svc_studio", "3. «В студию» — работа с зоной; «Назад к карте» — к той же точке обзора")],
        caption="Вывод: путь эколога — Земля → точка → карточка → студия → назад к следующей находке",
        source="http://localhost:8070 (v2); docs/DEMO.md",
        speech=("Сервис. При открытии — Земля с реальными находками обработанных сцен. Клик по точке — сцена и карточка зоны: "
                "что найдено, когда, площадь, уверенность и откуда каждое число. Из карточки — в студию, кнопкой «Назад» — "
                "к той же точке обзора и к следующей находке."),
        notes="Показ вживую — docs/DEMO.md. Полевые шт./км² другого места не выдаются за плотность зоны: «не определено по этому снимку».",
    ))
    S.append(dict(
        kind="main", layout="grid", section="Сервис: фото и выгрузка",
        title=f"Счётчик по фото и выгрузка: карта = CSV = GeoJSON ({num(k['sz_export_ui'], 0)} = "
              f"{num(k['sz_export_csv'], 0)} = {num(k['sz_export_geojson'], 0)})",
        images=[("svc_photo", "«Фото»: рамки и число предметов, площадь кадра → шт./км²; состав не определён"),
                ("svc_export", "Выгрузка GeoJSON/CSV и повтор сохранённого запроса")],
        caption="Вывод: у каждого числа подписан источник — поле, фото, снимок или «нет данных»",
        source="reports/case_demo/*.png; reports/photo_count/ui_photo_v2.png; docs/CONTRACTS_V3.md",
        speech=("В карточке у каждого числа подписан источник: измерено в поле, посчитано по фото или по снимку. Режим «Фото» "
                "считает предметы на загруженном кадре. Выгрузка GeoJSON и CSV совпадает с картой, запрос можно сохранить и повторить."),
        notes=f"Выгрузка сверяется тестом: UI {num(k['sz_export_ui'], 0)}, CSV {num(k['sz_export_csv'], 0)}, GeoJSON {num(k['sz_export_geojson'], 0)} строк.",
    ))
    S.append(dict(
        kind="main", layout="text", section="Ограничения: что мы НЕ утверждаем",
        title="Что мы НЕ утверждаем",
        bullets=[f"шт./км² по снимку — не выдаём: калибровочных пар {num(k['ql_calibration_pairs'], 0)}; «концентрация по снимку не подтверждена»",
                 f"«Пластик» по снимку — нет: детектор видит любой плавающий мусор; суда — известная слабость ({num(k['v2_reference_vessels_pct'], 0)} % судов)",
                 f"Предел обнаружения не доказан: на {num(k['ad_A_with_items'], 0)} отрезках ADIS с единичными предметами детектор их не увидел — "
                 f"согласуется с физикой, но общий предел для всех скоплений не задаёт",
                 "Сильный ветер: «не обнаружено» → «недостаточно данных» (правило Cózar 2024)",
                 f"Поле: S2 — один рейс; интервал одного нового места широкий ({rng(k['qf_event_lo95'], k['qf_event_hi95'], 0)} шт./км²)",
                 "Счётчик по фото на реку и спутник без дообучения не переносится"],
        source="README.md «Ограничения»; reports/report.md; docs/QUANTITY.md",
        speech=("Что мы не утверждаем. Штук по снимку — нет, калибровочных пар ноль. Пластик по снимку — нет, детектор видит любой "
                "мусор и путает суда. Предел обнаружения не доказан. При сильном ветре — «недостаточно данных». Поле — один рейс, "
                "а счётчик по фото без дообучения на реку не переносится."),
        notes=(f"Test концентрации не абсолютно нетронутый: {k['ft_limitation']}. Порог ветра — 5 м/с из Methods Cózar 2024, "
               "не подбирался."),
    ))
    S.append(dict(
        kind="main", layout="row", section="Развитие",
        title="Развитие: спутник находит зону → детальный снимок над ней → счёт предметов → шт./км² → сверка с полем",
        kpis=[(f"{num(k['q_n_pairs_k_x2_min'], 0)}–{num(k['q_n_pairs_k_x2_max'], 0)}",
               "калибровочных пар «снимок → шт./км²» хватит для коэффициента в ×/÷2"),
              (num(k["q_n_pairs_r05"], 0), "пар — чтобы увидеть связь r = 0.5")],
        bullets=["Трансекты и пролёты дронов под пролёт Sentinel-2, время записано",
                 "Детальные снимки (дрон, VHR) над найденными зонами → счётчик → шт./км² кадра",
                 "Дообучить счётчик на реке и дронах; классы материалов — с матрицей ошибок",
                 "Больше рейсов и сезонов для поля; дрейф по течениям и ветру"],
        source="docs/QUANTITY.md; docs/research/marine_quantity/DECISION.md; reports/quantity/calibration.json",
        speech=(f"Развитие — замкнуть лестницу. Спутник находит зону, над ней делается детальный снимок с дрона, счётчик даёт "
                f"штуки на квадратный километр, их сверяем с полем. Для калибровки хватит {num(k['q_n_pairs_k_x2_min'], 0)}–"
                f"{num(k['q_n_pairs_k_x2_max'], 0)} таких пар."),
        notes="Проверено: доступа к VHR над положительными отрезками ADIS в тот же день нет; поэтому это следующий шаг, а не результат.",
    ))
    team = ROOT / "presentation" / "team.txt"
    team_line = team.read_text(encoding="utf-8").strip() if team.exists() else ""
    S.append(dict(
        kind="main", layout="row", section="Воспроизводимость, команда, репозиторий",
        title=f"Одна команда без сети — {num(k['run_s'], 1)} с; чистый клон до карты — {k['cc_total']} мин; числа из одного файла",
        kpis=[(f"{num(k['run_s'], 1)} с", "run.ps1 -Case all -Offline, CPU, без сети"),
              (f"{k['cc_total']} мин", "от git clone до карты на экране"),
              (f"{num(k['n_tests'], 0)} passed", f"тесты кейса; skipped {num(k['t_skipped'], 0)}, failed {num(k['t_failed'], 0)}")],
        bullets=["Репозиторий: github.com/azama2t/final-cosmohack",
                 f"Все числа — reports/final_numbers.json; отпечаток прогона {k['fingerprint']}",
                 *([f"Команда: {team_line}"] if team_line else []),
                 "Спасибо! Вопросы — в приложении и в presentation/qa.md"],
        source=f"{k['cc_file']}; reports/case_run/run_summary.json; README.md «Как пересчитать»",
        speech=(f"Всё воспроизводится одной командой без сети за {num(k['run_s'], 1)} секунды, чистый клон до карты — "
                f"{k['cc_total']} минуты, числа в README, отчёте и этой презентации сверяет тест. Спасибо, готовы к вопросам."),
        notes=f"Тесты кейса ({k['t_when']}): {num(k['n_tests'], 0)} passed. Приложение — подробные слайды для вопросов.",
    ))
    return S


# ----------------------------------------------------------------------------------------------- content
def slides(k: dict) -> list[dict]:
    """12 слайдов: заголовок = вывод; тело; справа картинка / число / диаграмма / таблица; речь и тайминг."""
    t2, t1 = "S2", "S1"
    S = []
    S.append(dict(
        time=("0:00", "0:20"), section="Кейс «Макропластик»",
        title="Плавающий мусор: детектор по снимку и шт./км² по полю — что доказано, а что нет",
        bullets=[
            f"Детектор по Sentinel-2 проверен на test MARIDA: F1 {num(k['d_lgbm_f1'], 3)} против RandomForest {num(k['d_rf_argmax_f1'], 3)}",
            f"Концентрация шт./км² — по полевым данным: C = N/A с интервалом Пуассона; на отложенном test модель не лучше медианы, поэтому на карте медиана",
            f"Снимок ↔ поле: {num(k['p_events'], 0)} событий → {num(k['p_events_accept_drift'], 0)} синхронных пар; перенос «снимок → шт./км²» не заявляем",
        ],
        big=(num(k["p_events_accept_drift"], 0), "подтверждённых пар «снимок ↔ полевое измерение» — и мы это показываем, а не прячем"),
        source=f"reports/final_numbers.json → case.sections ({k['n_sections']} разделов с источником и протоколом)",
        speech=(f"Мы решали кейс как два алгоритма, проверенных по отдельности. Детектор по снимку — F1 {num(k['d_lgbm_f1'], 3)} "
                f"на test MARIDA. Концентрация — по полевым данным. Синхронных пар «снимок — поле» {num(k['p_events_accept_drift'], 0)}, "
                f"поэтому переноса на снимок мы не заявляем, и карта это прямо говорит."),
    ))
    S.append(dict(
        time=("0:20", "0:40"), section="Задача и пользователь",
        title="Пользователь — специалист по мониторингу: акватория и дата → проверка зон → сравнение с полем → выгрузка",
        bullets=[
            "Выбирает акваторию и дату, видит полевые измерения, снимки-кандидаты и маску качества",
            "Смотрит, где детектор нашёл подозрительные пиксели, и почему пара «снимок ↔ поле» принята или отклонена",
            "Сравнивает с полевым измерением (шт./км² с интервалом), сохраняет запрос, выгружает GeoJSON/CSV",
            "Измерение, оценка по полевым данным и площадь маски — разные поля везде: в API, карточке, легенде, выгрузке",
        ],
        image="overview",
        caption=f"Карта кейса: {num(k['exp_obs'], 0)} наблюдений, {num(k['zones'], 0)} полос обследования со снимками-кандидатами",
        source="README.md §1, §8; docs/CONTRACTS_V3.md",
        speech=("Пользователь — специалист по мониторингу. Он выбирает акваторию и дату, проверяет находки детектора, "
                "сравнивает с полем и выгружает результат. Измерение, оценка и площадь маски у нас везде — разные поля."),
    ))
    S.append(dict(
        time=("0:40", "1:00"), section="Целевая величина",
        title="Целевая величина — суммарный плавающий пластик > 2 см, шт./км², визуальная полоса 10 м (S2, Саргассово море)",
        bullets=[
            f"{pl(k['t_n_events'], *EV)} за {num(k['t_n_days'], 0)} дней ({k['t_date_min']} – {k['t_date_max']}); у всех есть N и A → C = N/A проверяема",
            f"N {num(k['t_n_min'], 0)}–{num(k['t_n_max'], 0)} предметов, A {num(k['t_a_min'], 3)}–{num(k['t_a_max'], 3)} км²: масштаб контрольного примера {num(k['ctl_n'], 0)} / {num(k['ctl_a'], 2)} км²",
            f"Второй профиль — трал S1, 5–50 см, {pl(k['s1_n_events'], *EV)}: только полевая проверка, со S2 не смешиваем",
            f"all_litter (S3, S4) — весь мусор всех материалов, не пластик: только в эксперименте на снимках",
            "Детектор: класс Marine Debris MARIDA = любой плавающий мусор; пластик отдельно он не выделяет",
        ],
        big=(num(k["t_c_median"], 1), f"шт./км² — медиана C профиля S2 (диапазон {rng(k['t_c_min'], k['t_c_max'])})"),
        source="configs/case_selection.yaml; README.md §1; reports/report.md §1",
        speech=(f"Целевая величина — суммарный плавающий пластик больше двух сантиметров, штук на квадратный километр, "
                f"визуальная полоса с судна. Это {pl(k['t_n_events'], *EV)} Саргассова моря, у каждого есть N и A. "
                f"Весь мусор Северного и Чёрного морей — не пластик, он только для эксперимента."),
    ))
    S.append(dict(
        time=("1:00", "1:20"), section="Данные и отбор",
        title=f"Из {num(k['rows'], 0)} строк реестра в профиль S2 входят {num(k['s2_accepted_rows'], 0)}: у каждого отказа записана причина",
        bullets=[
            f"Реестр: {num(k['rows'], 0)} строк, {num(k['fields'], 0)} полей, {num(k['events'], 0)} событий из 4 источников (S1 {num(k['ev_S1'], 0)}, S2 {num(k['ev_S2'], 0)}, S3 {num(k['ev_S3'], 0)}, S4 {num(k['ev_S4'], 0)})",
            f"Принято: S2 — {num(k['s2_accepted_rows'], 0)}, S1 — {num(k['s1_accepted_rows'], 0)}; категории не суммируем (пусто ≠ 0)",
            "Поля-утечки (concentration_*, items_count, reported_* …) не бывают признаками: тесты портят их и проверяют, что прогноз не меняется",
        ],
        table=[["Причина отказа (профиль S2)", "Строк"],
               ["объектная строка — предмет, а не плотность", num(k["s2_rej_item_observation"], 0)],
               ["категория пластика, не суммарный пластик", num(k["s2_rej_plastic_category"], 0)],
               ["суммарный пластик другого профиля (трал S1)", num(k["s2_rej_other_source"], 0)],
               ["all_litter — весь мусор, не пластик", num(k["s2_rej_all_litter"], 0)],
               ["категория промыслового мусора", num(k["s2_rej_fisheries"], 0)],
               ["аэросъёмка S1 > 50 см", num(k["s2_rej_aerial"], 0)],
               ["итого отказов", num(k["s2_rejected_rows"], 0)]],
        source="src/macroplastic/case/selection.py; reports/case_splits/selection_*[_rejected].csv",
        speech=(f"Из {num(k['rows'], 0)} строк в основной профиль попадают {num(k['s2_accepted_rows'], 0)}. У каждого отказа есть причина: "
                f"объектные строки, категории, весь мусор. Поля-утечки в признаки не попадают, это проверяют тесты."),
    ))
    S.append(dict(
        time=("1:20", "1:45"), section="Реестр пар и окно синхронизации",
        title=f"{num(k['p_events'], 0)} событий → {num(k['p_events_accept_meta'], 0)} по метаданным → {num(k['p_quality_accept'], 0)} по маскам → {num(k['p_events_accept_drift'], 0)} синхронных с учётом дрейфа",
        bullets=[
            f"Для пластика снимков нет: S1 и S2 — открытый океан 2015 г., Sentinel-2A снимает с конца июня 2015 (S1 — 0 сцен в ±5 сут, S2 — {num(k['any5_S2'], 0)})",
            f"Дрейф: при типичных {num(k['drift_typical_ms'], 1)} м/с вода смещается на {num(k['drift_typical_km'], 1)} км (медиана) — допуск 3 км держит только |dt| ≤ {num(k['p_max_dt_h_typical'], 1)} ч",
            f"Маски в полосе: блик {num(k['p_reject_glint'], 0)}, облака {num(k['p_reject_cloud'], 0)} (у {num(k['p_reject_cloud_qa_suspect'], 0)} флаг QA Landsat сомнителен), покрытие {num(k['p_reject_coverage'], 0)}",
            f"Реестр: строка на каждое из {num(k['p_events'], 0)} событий, этап отказа и все причины",
        ],
        bars=dict(title="События, у которых остаётся пара", items=[
            ("все события", k["p_events"], False),
            ("|dt| ≤ 1 сут, облачность ≤ 60 %", k["p_events_accept_meta"], False),
            ("маски качества в полосе", k["p_quality_accept"], False),
            (f"дрейф ≤ допуска ({num(k['drift_typical_ms'], 1)} м/с)", k["p_events_accept_drift"], True)]),
        source="reports/case_pairs/summary.md, quality.md; data/case/run/registry_pairs.csv",
        speech=(f"Реестр пар — главный фильтр. Из {num(k['p_events'], 0)} событий по метаданным проходят {num(k['p_events_accept_meta'], 0)}, "
                f"маски в полосе — {num(k['p_quality_accept'], 0)}. Но вода за сутки уходит на десятки километров: пара синхронна, "
                f"только если между снимком и наблюдением не больше {num(k['p_max_dt_h_typical'], 1)} часа. Таких ноль. "
                f"Для пластика снимков нет вообще."),
    ))
    S.append(dict(
        time=("1:45", "2:10"), section="Детектор",
        title=f"Детектор: F1 {num(k['d_lgbm_f1'], 3)} на test MARIDA; на снимках пар — {num(k['dc_n_obj'], 0)} объектов, в полосах {num(k['dc_n_in_strip'], 0)}",
        bullets=[
            f"LightGBM, порог {num(k['thr'], 2)} с val; test MARIDA посчитан один раз: ΔF1 к RF {num(k['d_delta'], 3)} {ci(k['d_delta_ci'], 3)}",
            f"MARIDA test: на саргассуме и мутной воде ложных {num(k['d_hard_bg_fp'], 0)} (FDI × NDVI помечает {num(k['d_fdi_sarg_pct'], 0)} % саргассума); ошибки — суда {num(k['d_fp_ship'], 0)} и органика {num(k['d_fp_org'], 0)}",
            f"Снимки пар (L2A): гармонизация каналов выключена по MARIDA val — F1 {num(k['hv_f1_none'], 3)} без сдвига против {num(k['hv_f1_per_scene'], 3)} со сдвигом, Δ {num(k['hv_delta_per_scene'], 3)} {ci(k['hv_delta_per_scene_ci95'], 3)}",
            f"Сейчас: {num(k['dc_n_obj'], 0)} объектов на {num(k['dc_n_crops'], 0)} сценах, в полосах {num(k['dc_n_in_strip'], 0)}; все на HE460 т.03 вне полосы: {num(k['dc_n_other_single'], 0)} пена/барашки, {num(k['dc_n_ship'], 0)} вероятно судно (тип по правилам) — все ложные, скоплений нет (суда — известная слабость: {num(k['v2_reference_vessels_pct'], 0)} % судов)",
            f"Прежний режим (с гармонизацией) — основание отказа: {num(k['dr_n_obj'], 0)} объектов, вероятное скопление по виду у {num(k['vr_h_out_precision_pct'], 1)} % {ci(k['vr_h_out_ci95_pct'], 1)}; размечал ИИ-агент, один аннотатор, κ {num(k['vr_kappa'], 2)} — его же слепой повтор",
        ],
        bars=dict(title=f"F1 Marine Debris, test MARIDA ({num(k['d_n_scenes'], 0)} сцен)", maxv=1.0, fmt=3, items=[
            ("LightGBM (основной)", k["d_lgbm_f1"], True),
            ("RandomForest, протокол MARIDA", k["d_rf_argmax_f1"], False),
            ("окно FDI × NDVI", k["d_fdi_ndvi_box_f1"], False)]),
        source="reports/case_detector/compare.md; reports/case_pairs/visual_review.md; docs/DECISIONS.md (final_numbers → case.detector_current)",
        speech=(f"Детектор — пиксельный LightGBM. На test MARIDA F1 {num(k['d_lgbm_f1'], 3)}, RandomForest — {num(k['d_rf_argmax_f1'], 3)}, "
                f"индексы FDI — {num(k['d_fdi_ndvi_box_f1'], 3)}; саргассум и мутную воду он не путает. На снимках пар мы выключили "
                f"гармонизацию каналов — решение по MARIDA val, не по тесту. Сейчас {num(k['dc_n_obj'], 0)} объектов на {num(k['dc_n_crops'], 0)} "
                f"сценах и ни одного в полосах; вне полосы — одиночные пиксели пены и, вероятно, судно — все ложные, скоплений нет. Прежний режим давал {num(k['dr_n_obj'], 0)} "
                f"объектов, почти всё — фон моря."),
    ))
    S.append(dict(
        time=("2:10", "2:45"), section="Концентрация",
        title=f"C = N/A сошлась с опубликованной у {num(k['cons_match'], 0)} из {num(k['cons_rows'], 0)} строк; на отложенном test модель не лучше медианы — на карте медиана",
        bullets=[
            f"Контроль: {num(k['ctl_n'], 0)} / {num(k['ctl_a'], 2)} км² = {num(k['ctl_v'], 1)} шт./км², 95 % ДИ Пуассона {rng(k['ctl_lo'], k['ctl_hi'])}; C = N/A совпала с опубликованной у {num(k['cons_match'], 0)} из {num(k['cons_rows'], 0)} строк",
            f"Сплит: {num(k['k_blocks'], 0)} участков маршрута + буфер {num(k['buf_days'], 0)} сут; протокол и правило выбора записаны до CV",
            f"Dev CV S2: {k['S2_primary']} {num(k['S2_dev_main'], 1)} против медианы {num(k['S2_dev_med'], 1)}, ΔMAE {ci(k['S2_dev_ci'])} — слабый выигрыш (с Бонферрони {ci(k['S2_dev_bonf'])})",
            f"Отложенный test ({k['ft_when']}, один раз): S2 {num(k['S2_t_main_mae'], 1)} против {num(k['S2_t_median_mae'], 1)} ({k['S2_t_verdict']}); S1 {num(k['S1_t_main_mae'], 1)} против {num(k['S1_t_median_mae'], 1)} ({k['S1_t_verdict']})",
        ],
        table=[["Отложенный test: модель / медиана", "S2 · S1"],
               ["n событий", f"{num(k['S2_t_n_test'], 0)} · {num(k['S1_t_n_test'], 0)}"],
               ["MAE, шт./км²", f"{num(k['S2_t_main_mae'], 1)} / {num(k['S2_t_median_mae'], 1)} · {num(k['S1_t_main_mae'], 1)} / {num(k['S1_t_median_mae'], 1)}"],
               ["RMSE, шт./км²", f"{num(k['S2_t_main_rmse'], 1)} / {num(k['S2_t_median_rmse'], 1)} · {num(k['S1_t_main_rmse'], 1)} / {num(k['S1_t_median_rmse'], 1)}"],
               ["Покрытие 90 %-интервала", f"{num(k['S2_t_main_coverage90_pct'], 0)} / {num(k['S2_t_median_coverage90_pct'], 0)} % · {num(k['S1_t_main_coverage90_pct'], 0)} / {num(k['S1_t_median_coverage90_pct'], 0)} %"],
               ["На карте", "медиана профиля"]],
        table_w0=0.5,
        source="reports/case_conc/dev_cv.json, final_test.json; configs/case_selection.yaml: final_test",
        speech=(f"Концентрация: C равно N на A, интервал Пуассона, контрольный пример — {num(k['ctl_v'], 1)}. Модели сравниваем с "
                f"медианой на участках маршрута. На dev ridge выигрывает слабо, а на отложенном test, посчитанном один раз, — "
                f"{num(k['S2_t_main_mae'], 1)} против {num(k['S2_t_median_mae'], 1)} у медианы. Правило записано до открытия "
                f"test, поэтому на карте — медиана. Модель не спасаем."),
    ))
    S.append(dict(
        time=("2:45", "3:05"), section="Эксперимент «снимок vs all_litter»",
        title="Связь признаков снимка с полевой плотностью всего мусора не установлена: данных на это мало",
        bullets=[
            f"{num(k['e_n_accept_s2'], 0)} пар S2 прошли маски, независимых групп «район × день» — {num(k['e_n_groups'], 0)}; эталон — весь мусор, не пластик",
            f"FDI в полосе: ρ {num(k['e_fdi_rho'], 2)} {ci(k['e_fdi_ci'], 2)}, p Холма {num(k['e_fdi_holm'], 2)}; та же полоса в случайной воде сцены — {num(k['e_fdi_null'], 2)}",
            "Значит, связь на уровне сцены или дня, а не места; знак к тому же обратный ожидаемому",
            f"Мощность: при {num(k['e_n_groups'], 0)} группах видна только |ρ| ≥ {num(k['e_pow6'], 2)}; для ρ = 0.5 нужно ≈ {num(k['e_n_needed_rho05'], 0)} независимых пар",
        ],
        big=(num(k["e_n_needed_rho05"], 0), f"независимых синхронных пар нужно, чтобы увидеть ρ = 0.5 (есть {num(k['e_n_groups'], 0)} групп)"),
        source="reports/case_pairs/experiment.md, experiment.json",
        speech=(f"Связь снимка с полем мы всё же проверили. На {num(k['e_n_accept_s2'], 0)} парах FDI даёт ρ {num(k['e_fdi_rho'], 2)}, "
                f"но после поправки Холма p {num(k['e_fdi_holm'], 2)}, а случайная вода той же сцены даёт почти то же. "
                f"Вывод — данных мало: нужно около {num(k['e_n_needed_rho05'], 0)} независимых пар."),
    ))
    S.append(dict(
        time=("3:05", "3:25"), section="Карта",
        title=f"Карта: отложенная сцена Cózar {k['sz_demo_tile']} — {num(k['sz_demo_n_zones'], 0)} зон, {num(k['sz_demo_n_zones_cozar'], 0)} совпали с нитями Cózar; концентрация по снимку не подтверждена",
        bullets=[
            f"Сцена {k['sz_demo_tile']}, {k['sz_demo_date']} не участвовала в обучении, подборе порога и экспериментах детектора; всего в слое {num(k['sz_n_zones'], 0)} зон на {num(k['sz_n_scenes_eval'], 0)} оцениваемых сценах",
            f"Карточка зоны: измерено (площадь {num(k['sz_example_area_km2'], 2)} км², LWD, маска качества, версия модели) · вероятно (статус, признаки пены/блика/судна) · главный статус — «{k['sz_zone_main_status']}»",
            f"«Обнаружено» только при совпадении с нитью Cózar ({num(k['sz_by_level_b'], 0)}); признаки пены, блика, судна, берега → «недостаточно данных» ({num(k['sz_by_insufficient'], 0)})",
            f"Полосы кейса ({num(k['zones'], 0)}): связь с полем не подтверждена, концентрация по снимку недоступна; выгрузка = карта ({num(k['sz_export_ui'], 0)} = {num(k['sz_export_csv'], 0)} строк CSV)",
        ],
        image="demo_card",
        caption="Отложенная сцена Cózar: контуры зон детектора и карточка «Измерено / Вероятно»",
        source=f"data/case/scene_zones; reports/case_demo/demo_path.json (final_numbers → case.sections.scene_zones); {k['sc_file']}",
        speech=("Карта на сцене, которую модель не видела. Каждая зона — измерено по снимку, вероятность с признаками ложных и, "
                "статус «концентрация по снимку не подтверждена» — штук по снимку мы не показываем. Выгрузка равна карте."),
    ))
    st, rb = k["S2_sch_st"], k["S2_sch_route_buf1"]
    S.append(dict(
        time=("", ""), section="Собственный вклад и ключевые решения",
        title="Наш вклад — решения, каждое проверено экспериментом или записано до результата",
        bullets=[
            f"Реестр пар с допуском по дрейфу вместо «±1 сут»: {num(k['p_events_accept_meta'], 0)} пар по метаданным → {num(k['p_events_accept_drift'], 0)} синхронных; причины у каждого из {num(k['p_events'], 0)} событий",
            f"Сплит по участкам маршрута + буфер: на «связных компонентах» kNN выигрывал {num(st.get('d'), 1)} {ci(st.get('ci95'))}, на маршруте {num(rb.get('d'), 1)} {ci(rb.get('ci95'))} — утечка через соседей дня",
            f"Отложенный test один раз: правило «не лучше медианы → медиана» записано {k['ft_decision']}, test открыт {k['ft_when']} — на карте медиана",
            f"Отказ от гармонизации L2A по MARIDA val (F1 {num(k['hv_f1_none'], 3)} против {num(k['hv_f1_per_scene'], 3)}), test не использовался",
            f"Геометрия трансект по первоисточникам PANGAEA: {pl(k['g_events'], *EV)}, {num(k['g_multi'], 0)} прерванные — двумя сегментами; окно синхронизации |dt| ≤ {num(k['pf_window_h'], 2)} ч",
            f"Один источник чисел: final_numbers.json → README, отчёт, дека, речь, ответы; тест сверяет ({num(k['n_tests'], 0)} passed)",
            "Готовое, не наше: разметка MARIDA и MADOS, протокол RandomForest MARIDA, индекс FDI, каталоги STAC",
        ],
        source="reports/case_pairs/summary.md; reports/case_conc/metrics.json; configs/case_selection.yaml; docs/DECISIONS.md; reports/case_geometry/summary.md",
        speech=("Наш вклад — решения, которые защищают выводы от самообмана. Допуск по дрейфу в реестре пар, "
                "сплит по участкам маршрута, test один раз по заранее записанному "
                "правилу, отказ от гармонизации по val, геометрия трансект по первоисточникам и один источник чисел с тестом. "
                "Разметка MARIDA, протокол RandomForest и индекс FDI — готовые, мы с ними сравниваемся."),
    ))
    S.append(dict(
        time=("3:25", "3:40"), section="Дополнительные функции",
        title="«Подобрать снимок»: из правила дрейфа — инструмент планирования полевых работ под пролёт",
        bullets=[
            f"Окно синхронизации |dt| ≤ {num(k['pf_window_h'], 2)} ч; наивное окно ±1 сут даёт {num(k['pf_naive'], 0)} «пар», синхронных {num(k['pf_naive_drift'], 0)}",
            f"Чёрное море без времени трансект: {num(k['pf_cond'], 0)} событий получают окно UTC, у {num(k['pf_cond_ok'], 0)} полоса чистая по маскам",
            f"Прогноз пролётов на архиве совпал с реальной съёмкой в {num(k['pf_fc_hit'], 0)} из {num(k['pf_fc_n'], 0)} случаев (±5 мин)",
            f"Геометрия трансект по PANGAEA: {pl(k['g_events'], *EV)} S2/S3, {pl(k['g_segments'], *SEG)}; {num(k['g_multi'], 0)} прерванные трансекты — двумя сегментами",
        ],
        image="pairfinder",
        caption="Саргассово море, T18: прерванная трансекта (2 сегмента) и «Подбор снимка» — сцен в окне нет",
        source="docs/EXTRA_FEATURES.md; reports/case_pairfinder/evidence.md; reports/case_geometry/summary.md",
        speech=(f"Из правила дрейфа мы сделали инструмент «Подобрать снимок»: он говорит, когда выходить на трансекту. "
                f"Для Чёрного моря это окна UTC у {pl(k['pf_cond'], *EV)}. Геометрию трансект восстановили по PANGAEA."),
    ))
    S.append(dict(
        time=("3:40", "3:52"), section="Ограничения и развитие",
        title="Ограничения названы на карте и в отчёте; развитие — синхронные пары и больше рейсов",
        bullets=[
            "Перенос «снимок → шт./км²» не доказан: синхронных пар нет, для пластика снимков нет",
            f"Мало независимых данных: S2 — один рейс, {num(k['t_n_days'], 0)} дней; интервалы недопокрывают ({num(k['S2_dev_cov'], 0)} % и {num(k['S1_dev_cov'], 0)} % вместо 90 %)",
            "Test не абсолютно нетронутый: ранняя разведка бейзлайнов видела все события профиля (записано до открытия)",
            "Детектор на L2A пар по разметке не измерен; порог блика не калибровался на этих сценах",
            f"Дальше: трансекты под пролёт с записанным временем (≈ {num(k['e_n_needed_rho05'], 0)} пар), больше рейсов и сезонов, ERA5 для дрейфа, разметка скоплений на L2A",
        ],
        source="reports/report.md §8–9; README.md «Ограничения»",
        speech=("Ограничения называем сами: перенос на снимок не доказан, данных мало, интервалы недопокрывают. "
                "Развитие — трансекты под пролёт с записанным временем и больше рейсов."),
    ))
    S.append(dict(
        time=("3:52", "4:00"), section="Воспроизводимость",
        title=f"Одна команда, {num(k['run_s'], 1)} с без сети; чистый клон до карты — {k['cc_total']} мин, числа совпали",
        bullets=[
            "Одна команда: run.ps1 -Case all -Offline (CSV → отбор → пары → маски → детектор → метрики → выгрузка)",
            f"{pl(k['n_outputs'], *TAB)} с одинаковыми sha256 при повторе; отпечаток {k['fingerprint']}…",
            f"Чистый клон: клон {k['cc_clone_s']} с, установка и маршрут {k['cc_install']}, карта {k['cc_map_s']} с",
            f"Тесты кейса ({k['t_when']}): {num(k['n_tests'], 0)} passed, {num(k['t_skipped'], 0)} skipped, {num(k['t_failed'], 0)} failed; числа документов сверяет тест",
        ],
        big=(f"{k['cc_total']} мин", "от git clone до карты на экране, CPU"),
        source=f"{k['cc_file']}; reports/case_run/run_summary.json",
        speech=(f"Всё воспроизводится одной командой без сети; чистый клон до карты — {k['cc_total']} минуты, числа совпали. "
                f"Спасибо, готовы к вопросам."),
    ))
    # §32 Б: основная часть — main_slides (13 слайдов); прежние подробные слайды и «Расследование данных» §11 — приложение
    ss = search_slides(k)
    appendix = [s for s in ss if s["section"] != "Разбор ошибок на сложном фоне"] + [s for s in S if s["section"] in APPENDIX_KEEP]
    S = main_slides(k)
    # тайминг основной части по объёму речи (≈ 130 слов в минуту), минимум 12 с на слайд; регламент — SPEC-GAPS.md
    t0 = 0
    for s in S:
        dur = max(12, round(len(s["speech"].split()) * 60 / 130))
        s["time"] = (f"{t0 // 60}:{t0 % 60:02d}", f"{(t0 + dur) // 60}:{(t0 + dur) % 60:02d}")
        t0 += dur
    assert t0 <= MAIN_LIMIT_S, f"основная речь {t0} с > {MAIN_LIMIT_S} с — сократите текст"
    assert len(S) <= MAIN_MAX_SLIDES, f"основная часть {len(S)} слайдов > {MAIN_MAX_SLIDES}"
    for s in appendix:  # вслух не читается; заметки докладчика — ответ на вопрос
        s["appendix"] = True
        s["section"] = "Приложение · " + s["section"]
        s["time"] = ("прил.", "прил.")
    return S + appendix


# ----------------------------------------------------------------------------------------------- images
def prepare_images() -> dict:
    """Уменьшенные копии скриншотов в reports/case_deck_img (в git — только они)."""
    from PIL import Image

    IMG_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for key, (cands, dst, box) in IMAGES_MAIN.items():
        # основная часть §32 Б: первый найденный кандидат (свежий скрин presentation/img → прежний скрин); нет — слайд-заглушка
        s = next((ROOT / c for c in cands if (ROOT / c).exists()), None)
        d = IMG_DIR / dst
        if s is None:
            continue
        im = Image.open(s).convert("RGB")
        if box and im.width >= box[2]:
            im = im.crop(box)
        if im.width > 1920:
            im = im.resize((1920, round(im.height * 1920 / im.width)), Image.LANCZOS)
        im.save(d, "JPEG", quality=88, optimize=True)
        out[key] = d
    for key, (src, dst, box) in IMAGES.items():
        s, d = SHOTS / src, IMG_DIR / dst
        if s.exists():
            im = Image.open(s).convert("RGB")
            if box and im.width >= box[2]:
                im = im.crop(box)
            if im.width > 1600:
                im = im.resize((1600, round(im.height * 1600 / im.width)), Image.LANCZOS)
            im.save(d, "JPEG", quality=85, optimize=True)
        if d.exists():
            out[key] = d
        else:
            MISSING.append(f"image:{src}")
    return out


# ----------------------------------------------------------------------------------------------- pptx
BG = (0x10, 0x12, 0x16)
INK = (0xEE, 0xEF, 0xF1)
MUTED = (0x9A, 0xA1, 0xAC)
ACCENT = (0xD9, 0x91, 0x2E)
BASE = (0x5B, 0x63, 0x70)
PANEL = (0x1B, 0x1E, 0x24)


def build_pptx(S: list[dict], imgs: dict, out: Path, start: int = 1) -> None:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Emu, Inches, Pt

    rgb = lambda c: RGBColor(*c)  # noqa: E731
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]

    def text(slide, x, y, w, h, paras, size=16, color=INK, bold=False, anchor=MSO_ANCHOR.TOP, align=PP_ALIGN.LEFT,
             bullet=False, space=6):
        tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = anchor
        tf.margin_left = tf.margin_right = Inches(0.02)
        tf.margin_top = tf.margin_bottom = Inches(0.02)
        for i, ptxt in enumerate(paras if isinstance(paras, list) else [paras]):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            para.alignment = align
            para.space_after = Pt(space)
            r = para.add_run()
            r.text = ("• " if bullet else "") + ptxt
            r.font.size = Pt(size)
            r.font.bold = bold
            r.font.color.rgb = rgb(color)
            r.font.name = "Segoe UI"
        return tb

    def rect(slide, x, y, w, h, color):
        sh = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
        sh.fill.solid()
        sh.fill.fore_color.rgb = rgb(color)
        sh.line.fill.background()
        sh.shadow.inherit = False
        return sh

    def bars(slide, x, y, w, spec):
        text(slide, x, y, w, 0.4, spec["title"], size=14, color=MUTED)
        items = spec["items"]
        vmax = spec.get("maxv") or max(v for _, v, _ in items if v is not None) or 1
        fmt = spec.get("fmt", 0)
        label_w, val_w = w * 0.42, 0.9
        bar_w = w - label_w - val_w
        yy = y + 0.5
        for lab, v, hi in items:
            text(slide, x, yy, label_w - 0.1, 0.42, lab, size=13, color=INK, anchor=MSO_ANCHOR.MIDDLE)
            v0 = v or 0
            bw = max(bar_w * v0 / vmax, 0.04)
            rect(slide, x + label_w, yy + 0.07, bw, 0.3, ACCENT if hi else BASE)
            text(slide, x + label_w + bw + 0.08, yy, val_w, 0.42, num(v, fmt), size=14, bold=True,
                 color=INK, anchor=MSO_ANCHOR.MIDDLE)
            yy += 0.55
        if spec.get("note"):
            text(slide, x, yy + 0.05, w, 0.4, spec["note"], size=13, color=MUTED)

    def table(slide, x, y, w, rows, w0=0.78):
        n, m = len(rows), len(rows[0])
        shp = slide.shapes.add_table(n, m, Inches(x), Inches(y), Inches(w), Inches(0.36 * n))
        tbl = shp.table
        tbl.columns[0].width = Inches(w * w0)
        tbl.columns[1].width = Inches(w * (1 - w0))
        for i, row in enumerate(rows):
            for j, val in enumerate(row):
                cell = tbl.cell(i, j)
                cell.fill.solid()
                cell.fill.fore_color.rgb = rgb(PANEL if i else BASE)
                cell.margin_left = cell.margin_right = Inches(0.08)
                cell.margin_top = cell.margin_bottom = Inches(0.03)
                tf = cell.text_frame
                tf.paragraphs[0].text = ""
                r = tf.paragraphs[0].add_run()
                r.text = str(val)
                r.font.size = Pt(13)
                r.font.name = "Segoe UI"
                r.font.bold = (i == 0 or i == n - 1)
                r.font.color.rgb = rgb(INK)
                if j == 1:
                    tf.paragraphs[0].alignment = PP_ALIGN.RIGHT

    def table_n(slide, x, y, w, rows, widths):
        n, m = len(rows), len(rows[0])
        import math
        hs = [0.2 + 0.3 * max(math.ceil(len(str(v)) / max(1.0, w * widths[j] * 7.2 - 1)) for j, v in enumerate(row))
              for row in rows]
        shp = slide.shapes.add_table(n, m, Inches(x), Inches(y), Inches(w), Inches(sum(hs)))
        tbl = shp.table
        for i, hh in enumerate(hs):
            tbl.rows[i].height = Inches(hh)
        for j in range(m):
            tbl.columns[j].width = Inches(w * widths[j])
        for i, row in enumerate(rows):
            for j, val in enumerate(row):
                cell = tbl.cell(i, j)
                cell.fill.solid()
                cell.fill.fore_color.rgb = rgb(PANEL if i else BASE)
                cell.margin_left = cell.margin_right = Inches(0.08)
                cell.margin_top = cell.margin_bottom = Inches(0.04)
                tf = cell.text_frame
                tf.word_wrap = True
                tf.paragraphs[0].text = ""
                r = tf.paragraphs[0].add_run()
                r.text = str(val)
                r.font.size = Pt(15)
                r.font.name = "Segoe UI"
                r.font.bold = i == 0 or j > 0
                r.font.color.rgb = rgb(INK)
                if j > 0 and m > 2:
                    tf.paragraphs[0].alignment = PP_ALIGN.RIGHT if str(val)[:1].isdigit() else PP_ALIGN.LEFT
        return sum(hs)

    def picture(slide, key, x, y, w, hmax):
        """Картинка по ширине w, не выше hmax, по центру колонки; возвращает её высоту. Нет файла — рамка-заглушка."""
        from PIL import Image
        if key not in imgs:
            rect(slide, x, y, w, hmax, PANEL)
            text(slide, x + 0.3, y + hmax / 2 - 0.4, w - 0.6, 0.8,
                 "Скрин сервиса v2 (http://localhost:8070) — будет вставлен после финальной правки интерфейса",
                 size=16, color=MUTED, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
            return hmax
        p = imgs[key]
        with Image.open(p) as im:
            ar = im.height / im.width
        h = min(w * ar, hmax)
        wi = h / ar
        slide.shapes.add_picture(str(p), Inches(x + (w - wi) / 2), Inches(y), Inches(wi), Inches(h))
        return h

    def render_main(sl, s):
        text(sl, 0.5, 0.5, 12.3, 1.05, s["title"], size=28 if len(s["title"]) < 80 else 24, bold=True)
        lay = s["layout"]
        y0 = 1.75
        if lay == "left":
            lx, lw, rx_, rw_ = 0.5, 4.55, 5.35, 7.5
            y = y0
            kp = s.get("kpis", [])
            step = min(1.6, 4.3 / max(1, len(kp)))
            for val, lab in kp:
                big = 40 if len(val) <= 9 else 30 if len(val) <= 16 else 24
                text(sl, lx, y, lw, 0.72, val, size=big, bold=True, color=ACCENT)
                text(sl, lx, y + 0.72, lw, 0.7, lab, size=13, color=INK)
                y += step
            if s.get("bullets"):
                text(sl, lx, y, lw, 6.8 - y, s["bullets"], size=13, color=MUTED, bullet=True, space=6)
            if "image" in s:
                h = picture(sl, s["image"], rx_, y0, rw_, 4.3)
                text(sl, rx_, y0 + h + 0.1, rw_, 0.6, s["caption"], size=15, bold=True, color=INK)
            elif "bars" in s:
                bars(sl, rx_, y0, rw_, s["bars"])
            elif "table" in s:
                h = table_n(sl, rx_, y0, rw_, s["table"], s.get("table_w", (0.5, 0.5)))
                if s.get("caption"):
                    text(sl, rx_, y0 + h + 0.35, rw_, 0.8, s["caption"], size=15, bold=True, color=INK)
        elif lay == "full":
            h = picture(sl, s["image"], 0.5, y0 - 0.1, 12.3, 4.6)
            text(sl, 0.5, y0 + h + 0.05, 12.3, 0.5, s["caption"], size=16, bold=True, color=INK, align=PP_ALIGN.CENTER)
        elif lay == "grid":
            n = len(s["images"])
            gw = (12.3 - 0.3 * (n - 1)) / n
            hmax = 0
            for j, (key, cap) in enumerate(s["images"]):
                gx = 0.5 + j * (gw + 0.3)
                h = picture(sl, key, gx, y0, gw, 3.9)
                hmax = max(hmax, h)
                text(sl, gx, y0 + h + 0.08, gw, 0.7, cap, size=13, color=INK)
            text(sl, 0.5, 6.25, 12.3, 0.5, s["caption"], size=16, bold=True, color=INK, align=PP_ALIGN.CENTER)
        elif lay == "row":
            kp = s.get("kpis", [])
            n = max(1, len(kp))
            tw = (12.3 - 0.3 * (n - 1)) / n
            for j, (val, lab) in enumerate(kp):
                tx = 0.5 + j * (tw + 0.3)
                rect(sl, tx, y0, tw, 1.9, PANEL)
                text(sl, tx + 0.25, y0 + 0.15, tw - 0.5, 0.8, val, size=40 if len(val) <= 12 else 30, bold=True, color=ACCENT)
                text(sl, tx + 0.25, y0 + 1.0, tw - 0.5, 0.8, lab, size=14, color=INK)
            if s.get("bullets"):
                text(sl, 0.7, y0 + 2.3, 11.9, 6.8 - y0 - 2.3, s["bullets"], size=18, bullet=True, space=10)
        elif lay == "text":
            text(sl, 0.7, y0, 11.9, 5.0, s["bullets"], size=20, bullet=True, space=14)

    for i, s in enumerate(S, start):
        sl = prs.slides.add_slide(blank)
        sl.background.fill.solid()
        sl.background.fill.fore_color.rgb = rgb(BG)
        text(sl, 0.5, 0.22, 9, 0.3, f"{i:02d} · {s['section']}", size=12, color=ACCENT, bold=True)
        if s.get("kind") == "main":
            render_main(sl, s)
            text(sl, 0.5, 6.95, 11.5, 0.35, "Источник: " + s["source"], size=10, color=MUTED)
            text(sl, 12.2, 6.95, 0.7, 0.35, f"{s['time'][0]}", size=10, color=MUTED, align=PP_ALIGN.RIGHT)
            sl.notes_slide.notes_text_frame.text = (f"[{s['time'][0]}–{s['time'][1]}] " + s["speech"]
                                                    + ("\n\nЕсли спросят: " + s["notes"] if s.get("notes") else ""))
            continue
        text(sl, 0.5, 0.5, 12.3, 1.05, s["title"], size=26 if len(s["title"]) < 95 else 23, bold=True,
             anchor=MSO_ANCHOR.TOP)
        right = any(key in s for key in ("image", "big", "bars", "table"))
        body_w = 6.1 if right else 12.3
        fsize = 16 if sum(len(b) for b in s["bullets"]) < 520 else 15
        text(sl, 0.5, 1.75, body_w, 5.0, s["bullets"], size=fsize, bullet=True, space=10)
        rx, rw = 7.0, 5.85
        if "image" in s and s["image"] in imgs:
            from PIL import Image
            p = imgs[s["image"]]
            with Image.open(p) as im:
                ar = im.height / im.width
            h = min(rw * ar, 4.4)
            wimg = h / ar
            sl.shapes.add_picture(str(p), Inches(rx + (rw - wimg) / 2), Inches(1.8), Inches(wimg), Inches(h))
            text(sl, rx, 1.85 + h, rw, 0.6, s.get("caption", ""), size=12, color=MUTED)
        elif "bars" in s:
            bars(sl, rx, 1.85, rw, s["bars"])
        elif "table" in s:
            table(sl, rx, 1.85, rw, s["table"], s.get("table_w0", 0.78))
        if "big" in s:
            by = 1.85 if "image" not in s and "bars" not in s and "table" not in s else 5.0
            if "bars" in s:
                by = 4.9
            rect(sl, rx, by, rw, 1.7, PANEL)
            text(sl, rx + 0.3, by + 0.1, rw - 0.6, 0.9, s["big"][0], size=48, bold=True, color=ACCENT)
            text(sl, rx + 0.3, by + 1.0, rw - 0.6, 0.65, s["big"][1], size=13, color=INK)
        text(sl, 0.5, 6.95, 11.5, 0.35, "Источник: " + s["source"], size=10, color=MUTED)
        text(sl, 12.2, 6.95, 0.7, 0.35, f"{s['time'][0]}", size=10, color=MUTED, align=PP_ALIGN.RIGHT)
        sl.notes_slide.notes_text_frame.text = f"[{s['time'][0]}–{s['time'][1]}] " + s["speech"]
    prs.save(out)


# ----------------------------------------------------------------------------------------------- SPEECH.md
def speech_md(S: list[dict], k: dict) -> str:
    A = [s for s in S if s.get("appendix")]
    S = [s for s in S if not s.get("appendix")]
    L = [f"# Речь на защите кейса ({S[-1]['time'][1]})", "",
         "Генерируется `scripts/make_deck_case.py` из `reports/final_numbers.json`; руками не править. Тот же текст — "
         "в заметках докладчика `reports/case_deck.pptx` и `presentation/deck.pptx` (там же «Если спросят» — вслух не читается). Демо на карте — отдельно, `docs/DEMO.md` (2 мин); вопросы — `docs/QA.md`.",
         "",
         f"Темп ≈ 130 слов в минуту, всего ≈ {pl(sum(len(s['speech'].split()) for s in S), 'слово', 'слова', 'слов')}. Если отстаём больше чем на 15 с — слайды «Разбор ошибок на сложном фоне» и «Развитие» сокращаем до заголовка. "
         f"Регламент в ТЗ не задан — допущение «основная речь ≤ 5:00»; после основной части — {pl(len(A), 'слайд', 'слайда', 'слайдов')} «Приложения» для вопросов, вслух не читаются.", "",
         "| Слайд | Время | О чём |", "|---|---|---|"]
    for i, s in enumerate(S, 1):
        L.append(f"| {i} | {s['time'][0]}–{s['time'][1]} | {s['section']} |")
    L.append("")
    for i, s in enumerate(S, 1):
        words = len(s["speech"].split())
        L += [f"## {i}. {s['section']} ({s['time'][0]}–{s['time'][1]}, ≈ {pl(words, 'слово', 'слова', 'слов')})", "",
              f"**На слайде:** {s['title']}", "", s["speech"], ""]
    if A:
        L += ["## Приложение (вслух не читается, для вопросов)", ""]
        for j, s in enumerate(A, len(S) + 1):
            L += [f"- слайд {j}: **{s['section']}** — {s['title']}"]
        L.append("")
    L += ["## Числа наизусть", "",
          f"- F1 детектора на test MARIDA — **{num(k['d_lgbm_f1'], 3)}** {ci(k['d_lgbm_ci'], 3)}, RandomForest {num(k['d_rf_argmax_f1'], 3)}, FDI × NDVI {num(k['d_fdi_ndvi_box_f1'], 3)}.",
          f"- Пары: {num(k['p_events'], 0)} → {num(k['p_events_accept_meta'], 0)} → {num(k['p_quality_accept'], 0)} → **{num(k['p_events_accept_drift'], 0)}**; окно синхронизации ≈ {num(k['p_max_dt_h_typical'], 1)} ч.",
          f"- Контроль: {num(k['ctl_n'], 0)} / {num(k['ctl_a'], 2)} км² = **{num(k['ctl_v'], 1)}** шт./км² [{rng(k['ctl_lo'], k['ctl_hi'])}].",
          f"- Отложенный test S2: {num(k['S2_t_main_mae'], 1)} против медианы **{num(k['S2_t_median_mae'], 1)}**; S1: {num(k['S1_t_main_mae'], 1)} против {num(k['S1_t_median_mae'], 1)}.",
          f"- Эксперимент: {num(k['e_n_accept_s2'], 0)} пар, {num(k['e_n_groups'], 0)} групп; нужно ≈ {num(k['e_n_needed_rho05'], 0)} пар для ρ = 0.5.",
          f"- Розыск: у событий CSV пар A {num(k['sr_csv_A'], 0)}; ADIS — **{num(k['ad_A'], 0)}** пар по месту и времени; видимый сигнал {num(k['ql_visible_signal'], 0)}; калибровочных пар {num(k['ql_calibration_pairs'], 0)}.",
          f"- Новые метки: B {num(k['ld_b_new_acq'], 0)} съёмок + {num(k['ld_cz_win'], 0)} нитей Cózar, D {num(k['ld_ves_boxes'], 0)} судов; с MARIDA по тайлу и дате совпадений {num(k['ld_leaks_total'], 0)}, с MADOS по пикселям — только PLP/FO; детектор: {k['v2_note']}.",
          f"- U-Net MARIDA на test — {num(k['bl_unet_argmax'], 3)}; калибровке «снимок → шт./км²» нужно {num(k['q_n_pairs_k_x2_min'], 0)}–{num(k['q_n_pairs_k_x2_max'], 0)} калибровочных пар, есть {num(k['q_pairs_A_with_S_pos'], 0)}.",
          "",
          "## Формулировки, которых избегаем", "",
          "- all_litter (Северное и Чёрное моря) — «весь плавающий мусор», никогда «пластик».",
          "- Детектор находит «вероятное скопление плавающего мусора», а не «пластик».",
          "- На несинхронной паре — «подозрительные пиксели, связь с полем не подтверждена», а не «обнаружено».",
          "- Оценка по полевым данным — «медиана профиля, не по снимку».",
          "- Доля покрытия со снимка — «доля покрытия подозрительного материала», не «мусор» и не «шт./км²»; массу не называем.",
          "- Пары ADIS — «пары по месту и времени», не «калибровочные»; не говорим о доказанном пределе обнаружения: 0 на единичных предметах "
          "согласуется с физикой, но не задаёт общий предел для всех скоплений.",
          "- Штуки по снимку — не показываем: «концентрация по снимку не подтверждена», калибровочных пар нет.", ""]
    return "\n".join(L)


# ----------------------------------------------------------------------------------------------- DEMO.md
def _sz_block(k: dict) -> str:
    """Основной путь демо: отложенная сцена Cózar, слой «Спутниковые зоны». Числа — только case.sections.scene_zones."""
    return f"""<!-- scene_zones:begin -->
## Основной путь: отложенная сцена Cózar (спутниковые зоны, 1:40)

Сцена Sentinel-2 **{k['sz_demo_tile']}, {k['sz_demo_date']}** не участвовала ни в обучении и подборе порога детектора (MARIDA — по тайлу и
дате; MADOS — по содержимому, лучшее совпадение {num(k['sz_demo_mados_votes'], 0)} голоса, «нет совпадения»), ни в экспериментах детектора v2
(`reports/case_demo/heldout_scene.md`). Слой — `scripts/case/scene_zones.py` (данные в git, `data/case/scene_zones/`).
Проверить до выхода: слева «{num(k['sz_n_zones'], 0)} спутн. зон», вкладка «Зоны» — первая строка «Cózar, отложенная сцена {k['sz_demo_tile']}».
Прямая ссылка: `?sel=zone:{k['sz_example_zone_id']}`.

| Время | Действие | Что говорим | Что видно |
|---|---|---|---|
| 0:00–0:15 | Вкладка **«Зоны»** → первая строка | «Сцена, которую модель не видела. Снимок, маска качества, контуры зон детектора — всё с этой сцены.» | снимок, маска качества, контуры зон |
| 0:15–0:40 | Карточка, блок **«Измерено по снимку»** | «Площадь зоны {num(k['sz_example_area_km2'], 2)} км², подозрительные пиксели {num(k['sz_example_susp_m2'], 0)} м² ({num(k['sz_example_n_px'], 0)} пикс.), LWD {num(k['sz_example_lwd_m2_km2'], 0)} м² на км² пригодной воды — как у Cózar 2024. Вода в зоне {num(k['sz_example_water_pct'], 0)} %. Модель weights/lgbm, порог {num(k['thr'], 2)}, sha256 {k['sz_example_sha256_short']}.» | вырезка «снимок / пиксели детектора», «Измерено» |
| 0:40–0:55 | Блок **«Вероятно»** | «Вероятность детектора {num(k['sz_example_prob_mean'], 2)} / {num(k['sz_example_prob_max'], 2)}. Признаков пены, блика, судна, берега нет. Контур пересекает {num(k['sz_example_n_cozar'], 0)} нити каталога Cózar — их отметили люди по снимку. Поэтому «обнаружено»; без такой разметки зона — «срабатывание, не проверено».» | статус, признаки, «Каталог Cózar 2024» |
| 0:55–1:10 | Статус **«Концентрация»** | «Главный статус зоны — «{k['sz_zone_main_status']}». Перевод площади в штуки не показываем: калибровочных пар «снимок → шт./км²» у нас {num(k['ql_calibration_pairs'], 0)}; {num(k['ad_A'], 0)} пар ADIS — пары по месту и времени, не калибровочные.» | статус концентрации |
| 1:10–1:20 | Блок **«Поле рядом»** | «Ближайшее полевое измерение — ADIS за {num(k['sz_field_nearby_distance_km'], 0)} км, {k['sz_field_nearby_date']}: C = N/A = {num(k['sz_field_nearby_c'], 1)} [{num(k['sz_field_nearby_lo'], 1)}–{num(k['sz_field_nearby_hi'], 1)}] шт./км² (> 5 см). Измерение ≠ оценка: это другое время и место.» | таблица N / A / C |
| 1:20–1:30 | **Сложный случай:** «Как выглядит удача и ошибка» → «ложное срабатывание: судно / кильватер» | «Яркая точка со следом — судно. Статус «недостаточно данных». Суда — известная слабость: {num(k['v2_reference_vessels_pct'], 0)} % судов как мусор.» | вырезка судна, статус |
| 1:30–1:40 | Даты сцены → **«Выгрузка»** → «Спутниковые зоны» CSV; **«Запросы»** → сохранить → «сбросить» → запустить | «Выгрузка — те же поля и статусы; сохранённый запрос восстанавливает вид.» | зон на карте {num(k['sz_export_ui'], 0)} = строк CSV {num(k['sz_export_csv'], 0)} = объектов GeoJSON {num(k['sz_export_geojson'], 0)} |

Всего в слое {num(k['sz_n_zones'], 0)} зон на {num(k['sz_n_scenes_eval'], 0)} оцениваемых сценах из {num(k['sz_n_scenes'], 0)} (на остальных низкое солнце или слабый
сигнал воды — детектор не оценивается): обнаружено с подтверждением уровня B — {num(k['sz_by_level_b'], 0)}, срабатываний без проверки —
{num(k['sz_by_unverified'], 0)}, недостаточно данных (пена/блик/судно/берег/мелководье) — {num(k['sz_by_insufficient'], 0)}, не обнаружено — {num(k['sz_by_not_detected'], 0)}, ноль не информативен (ветер ≥ 5 м/с) — {num(k['sz_by_not_informative'], 0)}.
На демо-сцене {num(k['sz_demo_n_zones'], 0)} зон ({num(k['sz_demo_det_pixels'], 0)} пикс. детектора), из них с нитью Cózar — {num(k['sz_demo_n_zones_cozar'], 0)}.
Живой проход без моков (окна 1920×1080 и 1366×768): `scripts/case/demo_path_v2.py` → `reports/case_demo/demo_path.json`, кадры `reports/case_demo/*.png`.

## Путь данных: снимок → маски качества → детекция → зона
1. `scripts/case/demo_scene.py select` — отбор и проверка отложенности (тайл, дата; MARIDA, MADOS, эксперименты детектора v2, PLP/FO, суда, пары, районы).
2. `scripts/case/demo_scene.py fetch --acq <тайл_дата>` — L2A той же съёмки (Earth Search) → `data/live/cozar_demo/<дата>/` (формат районов сервиса).
3. `scripts/case/demo_scene.py detect` — маска качества и детектор, как у пар (weights/lgbm, без гармонизации, порог с MARIDA val).
4. `scripts/case/scene_zones.py [--only demo]` — зоны (кластеры объектов), признаки ложных, «измерено / вероятно», статус концентрации, вырезки.
5. Сервис: `/api/v3/scene_zones`, `/api/v3/scene_zones/{{id}}`, `/api/v3/export?layer=scene_zones`; карта v2 — слой «Спутниковые зоны».

## Эксперт повторяет расчёт без правки кода
- Другая сцена Cózar: строка из `reports/case_demo/heldout_candidates.csv` → `demo_scene.py fetch --acq <тайл_дата>` → `detect` → `scene_zones.py --only demo` → перезапустить сервис.
- Полоса пары кейса: `scripts/case/pair_quality.py --only S3:HE460_MarLitter_transect03 --force` → `/api/v3/zones/Z-S3_HE460_MarLitter_transect03`.
- Своя точка и дата: «Подобрать снимок» в карточке измерения (`scripts/case/pairfinder.py`), затем тот же путь.
- Любая зона открывается ссылкой `?sel=zone:<zone_id>`; числа — `curl http://127.0.0.1:8000/api/v3/scene_zones/<zone_id>`.
<!-- scene_zones:end -->"""


def demo_md(k: dict) -> str:
    return f"""# Демо на карте кейса: сценарий на 2 минуты и план Б

Генерируется `scripts/make_deck_case.py` из `reports/final_numbers.json`; руками не править.

**Подготовка (до выхода):** `powershell -ExecutionPolicy Bypass -File run.ps1 -Case all -Offline`, затем
`powershell -ExecutionPolicy Bypass -File run.ps1` → http://127.0.0.1:8000 (режим «Кейс» открывается по умолчанию).
Окно 1920×1080, браузер на весь экран. Проверить: слева «{num(k['exp_obs'], 0)} наблюдений · {num(k['zones'], 0)} полос»,
в легенде — «{num(k['zones'], 0)} обследованных участков со снимками-кандидатами; 0 подтверждённых пар; для пластика снимков нет».

## Порядок показа (как на слайдах «Сервис»): Земля → точка → карточка → студия → назад → другая точка

1. **Обзор Земли** (открывается сразу, без формы): точки — находки детектора на уже обработанных сценах, у точки — дата
   снимка и статус. «Это накопленные результаты обработки архива, а не съёмка всей Земли сегодня.»
2. **Точка → сцена и карточка зоны** (отложенная сцена {k['sz_demo_tile']}, {k['sz_demo_date']}): что найдено, когда и где снято,
   площадь и LWD, уверенность; количество — «{k['sz_zone_main_status']}», состав — «не определён». У каждого числа подписан источник.
3. **«В студию»** — снимок, маска качества, объекты детектора этой зоны; **«Назад к карте»** — та же точка и масштаб обзора.
4. **Другая точка** — например, находка «требует проверки» или «недостаточно данных» (судно, пена, ветер): почему статус такой.
5. **Числа — на своих панелях за один клик:** поле ({num(k['q_pooled_C'], 1)} шт./км² [{rng(k['qf_boot_lo95'], k['qf_boot_hi95'])}],
   медиана на карте), режим **«Фото»** (ошибка {num(k['pc_mae'], 2)} шт./кадр), вкладка **«Метрики»** (F1 {num(k['d_lgbm_f1'], 3)}).
6. **«Выгрузка»** GeoJSON/CSV и **«Запросы»** → сохранить → повторить.

Подробный сценарий с репликами — ниже.

{_sz_block(k)}

## Запасной путь: поле и полосы пар (2:00)

| Время | Действие | Что говорим | Что видно |
|---|---|---|---|
| 0:00–0:15 | «Акватория» → **Саргассово море, MSM41**; даты не трогаем | «Основной профиль — суммарный пластик > 2 см, визуальная полоса 10 м, {pl(k['t_n_events'], *EV)}.» | точки измерений на трансектах |
| 0:15–0:35 | Клик по измерению с плотностью (вкладка «Измерения» → запись «весь пластик») | «Это **измерение**: C = N/A, рядом 95 % интервал Пуассона. Если ниже есть блок «Оценка модели по полевым данным» — это исследовательский прогноз кросс-валидации без своего участка маршрута, не измерение. Итоговая оценка по полю — **медиана профиля**: на отложенном test модель не лучше медианы ({num(k['S2_t_main_mae'], 1)} против {num(k['S2_t_median_mae'], 1)}).» | карточка: значение, интервал Пуассона, профиль, источник |
| 0:35–0:55 | «Акватория» → **Юго-восток Северного моря** → полоса **HE460 · трансекта 03** | «Это полоса обследования на снимке-кандидате. Статус — **связь не подтверждена**, причина — дрейф больше допуска 3 км. Концентрация по снимку недоступна.» | карточка полосы, два статуса |
| 0:55–1:10 | В той же карточке — строка «подозрительных пикселей в полосе нет» и три миниатюры: снимок, маска качества, маска детектора | «Детектор в полосе ничего не нашёл. Сейчас он работает без гармонизации каналов — так решили по MARIDA val, не по тесту: на всех {num(k['dc_n_crops'], 0)} снимках пар {num(k['dc_n_obj'], 0)} объектов, в полосах {num(k['dc_n_in_strip'], 0)}; вне этой полосы — {num(k['dc_n_obj'], 0)} одиночных пикселей: {num(k['dc_n_other_single'], 0)} пена/барашки, {num(k['dc_n_ship'], 0)} вероятно судно (тип по правилам) — все ложные, скоплений нет. Класс детектора — любой плавающий мусор, не пластик.» | «подозрительных пикселей в полосе нет», вероятность детектора ср./макс. |
| 1:10–1:25 | «Реестр пар» → фильтр «Отклонённые» | «У каждого из {num(k['p_events'], 0)} событий — статус и все причины: сцены нет, облака, блик, дрейф. Принятых — {num(k['p_events_accept_drift'], 0)}.» | таблица: Δt, дрейф/допуск, причина |
| 1:25–1:40 | Карточка измерения → **«Подобрать снимок»** | «Окно синхронизации ≈ {num(k['p_max_dt_h_typical'], 1)} ч при 0,2 м/с. Функция отвечает, какой снимок годится и когда выходить на трансекту.» | окно синхронизации, кандидаты, решение |
| 1:40–1:50 | Вкладка **«Метрики»** | «F1 детектора {num(k['d_lgbm_f1'], 3)} на test MARIDA; отложенный test концентрации — модель против медианы.» | таблицы детектора и test |
| 1:50–2:00 | **«Выгрузка»** → GeoJSON; **«Запросы»** → сохранить, затем запустить сохранённый | «Выгрузка — те же поля и статусы, что на карте; сохранённый запрос перезапускается одной кнопкой.» | скачанный файл, список запросов |

Если жюри просит показать Чёрное море: «Акватория» → **Чёрное море, DOORS** → полоса **T1**: пара отклонена по дрейфу
(время трансект в данных не записано, окно — по суткам), пикселей в полосе нет, полевое значение — весь мусор, не пластик.

## План Б

1. **Нет интернета.** Данные кейса и API работают без сети, пропадает только подложка (Esri/OSM). Говорим: «подложка из
   интернета, данные локальные» и продолжаем по тому же сценарию.
2. **Сервис не стартует.** `.venv\\Scripts\\python.exe -m service --port 8000`; порт занят — `run.ps1 -Port 8080`.
3. **Карта не грузится совсем.** Показываем скриншоты `reports/case_deck_img/` (обзор, полоса с подозрительными
   пикселями, карточка измерения, «Подобрать снимок») и слайды «Карта» и «Собственный вклад» `reports/case_deck.pptx` тем же текстом.
4. **Нужны числа без интерфейса.** `http://127.0.0.1:8000/api/v3/metrics` и `/api/v3/meta` (итог в легенде) или
   `reports/final_numbers.json`, блок `case.sections`.
5. **Потерялись в интерфейсе.** «сбросить» под счётчиками слева, «Акватория» → «Все».
"""


# ----------------------------------------------------------------------------------------------- QA.md
def qa_items(k: dict) -> list[tuple[str, str, str]]:
    ft2 = k
    st, rb = k["S2_sch_st"], k["S2_sch_route_buf1"]
    s1e, s1r = k["S1_sch_event"], k["S1_sch_route_buf1"]
    Q = [
        # --- главное
        ("Почему вы не выдаёте концентрацию шт./км² по снимку?",
         f"Потому что перенос не доказан. Синхронных пар «снимок ↔ полевое измерение» при типичном дрейфе — "
         f"{num(k['p_events_accept_drift'], 0)} из {num(k['p_events'], 0)} событий; для профилей пластика сцен нет вообще; на "
         f"{num(k['e_n_accept_s2'], 0)} парах со всем мусором связь признаков снимка с плотностью не установлена. Выдать число "
         f"значило бы выдать калибровку, которой нет. Поэтому у всех {num(k['zones'], 0)} полос статус «концентрация недоступна», "
         f"поле `concentration` у зоны всегда `null`, а у {num(k['sz_n_zones'], 0)} спутниковых зон — «{k['sz_zone_main_status']}». "
         f"Калибровочных пар «снимок → шт./км²» {num(k['ql_calibration_pairs'], 0)}, и не только у нас: авторы крупнейшего каталога "
         f"полос пишут «{k['q_quote_short']}» (Cózar и др., 2024, doi {k['q_quote_doi']}).",
         "README.md «Главное», §6; reports/case_pairs/summary.md; reports/case_pairs/experiment.md; docs/QUANTITY.md"),
        ("Какой у вас главный количественный результат и насколько он точен?",
         f"Полевая концентрация C = N/A. Для суммарного пластика {size_ru(k['pr_S2_size'])} (S2): {num(k['q_pooled_N'], 0)} шт. на "
         f"{num(k['q_pooled_A_km2'], 2)} км² = {num(k['q_pooled_C'], 1)} шт./км². Интервал Пуассона [{rng(k['q_lo95'], k['q_hi95'])}] — "
         f"только ошибка счёта, он не накрывает среднее отложенного test ({num(k['qf_test_mean'], 1)}); поэтому правилом, записанным до "
         f"расчёта, выбран кластерный бутстреп по дням рейса: [{rng(k['qf_boot_lo95'], k['qf_boot_hi95'])}]. Для одного нового места "
         f"интервал {rng(k['qf_event_lo95'], k['qf_event_hi95'])} шт./км² накрывает {num(k['qf_event_cov'] * k['qf_event_n_test'], 0)} "
         f"из {num(k['qf_event_n_test'], 0)} событий test.",
         "reports/quantity/field_intervals.md; scripts/case/field_interval_check.py; docs/PIPELINE.md (правило C10)"),
        ("А ADIS — какая там концентрация и почему не калибровка авторов?",
         f"ADIS — {k['af_profile']}: ΣN/ΣA = {num(k['af_C'], 2)} [{rng(k['af_lo'], k['af_hi'], 2)}] шт./км² по "
         f"{num(k['af_n_segments'], 0)} отрезкам, {num(k['af_zero_pct'], 0)} % отрезков с нулём. Модели по регионам (отложено по "
         f"регионам и судам) не лучше медианы: {k['af_verdict']}. Калибровка авторов по тралу — {num(k['afa_C'], 2)} (типичный интервал "
         f"отрезка {rng(k['afa_lo_typ'], k['afa_hi_typ'])}; {k['afa_source']}) — показываем рядом как внешнюю, наше число без поправки не заменяем.",
         "reports/quantity/adis_forecast.md; configs/adis_forecast.yaml"),
        ("Как вы считаете штуки, если со спутника их не видно?",
         f"На детальном фото. Счётчик предметов (Faster R-CNN) на отложенных сессиях съёмки FML ({num(k['pc_n_images'], 0)} фото) "
         f"ошибается на {num(k['pc_mae'], 2)} {ci(k['pc_mae_ci95'], 2)} шт./кадр против {num(k['pc_baseline_median_count'], 2)} у медианы; "
         f"mAP@0,5 {num(k['pc_ap50'], 3)}. Если площадь кадра известна ({k['pa_dataset']}: GSD {num(k['pa_gsd_m'], 2)} м, кадр "
         f"{num(k['pa_frame_area_m2'], 0)} м²), число переводим в шт./км²: ошибка плотности {num(k['pa_density_mae_km2'], 0)} против "
         f"{num(k['pa_baseline_density_mae_km2'], 0)} у медианы на {num(k['pa_n_test_frames'], 0)} отложенных кадрах. Sentinel-2 так не считает.",
         "reports/photo_count/eval_grouped.json, area_winans.json; docs/PHOTO_COUNT.md"),
        ("Вы различаете бутылки, пакеты, пластик и дерево?",
         "Нет, пока не проверено. Счётчик FML обучен на одном классе «мусор»; классы аэросъёмки объединены в один. Разбивку по "
         "материалам показываем, только если класс размечен явно и есть матрица ошибок на отложенных данных; иначе в «Фото» и в "
         "карточке — «всего предметов, состав не определён». На снимке Sentinel-2 никаких «бутылок»: пиксель больше предмета.",
         "docs/LABELS.md; data/labels_map.csv; docs/PHOTO_COUNT.md"),
        ("Почему при сильном ветре зона «недостаточно данных», а не «не обнаружено»?",
         "При ветре сильнее 5 м/с мусор перемешивается в толще воды и полосы не формируются — так делают авторы каталога полос "
         "(Cózar и др., 2024, Methods: воду с ветром выше порога они исключают из площади). Порог взят из статьи, не подбирался. "
         "Поэтому «ноль» на такой сцене не информативен: статус «недостаточно данных», сцена не входит в площадь «обследовано»; "
         f"найденные зоны и уровень B не трогаем. На демо-сцене ветер {num(k['sz_demo_wind'], 1)} м/с.",
         "scripts/case/scene_zones.py; docs/CONTRACTS_V3.md (правило ветра); data/extra/cozar2024/paper.txt"),
        ("Чем доказано, что сцена демо не участвовала в обучении?",
         f"Сцена {k['sz_demo_scene_id']}: в MARIDA того же тайла нет; сверка с MADOS по содержимому — {k['sz_demo_mados_verdict']}; "
         f"в экспериментах дообучения и подборе порога она не использовалась. На ней {num(k['sz_demo_n_zones_cozar'], 0)} из "
         f"{num(k['sz_demo_n_zones'], 0)} зон детектора совпали с нитями каталога Cózar (уровень B).",
         "reports/case_demo/heldout_check.json, mados_content_check.json"),
        ("Какие лицензии у данных и моделей?",
         f"Разметка MARIDA и MADOS, FloatingObjects, каталог Cózar 2024 — открытые лицензии авторов; счётчик: {k['pc_license']}, "
         f"{k['pa_license']}. Речной набор TOCL — CC BY-NC (только исследовательская проверка, в продукт не включён). Полная таблица "
         "с атрибуцией — README «Источники и лицензии».",
         "README.md «Источники и лицензии»"),
        ("Модель концентрации не лучше медианы — зачем тогда модель и что это значит?",
         f"Это результат, а не провал процедуры. На dev CV S2 {k['S2_primary']} выигрывала слабо ({num(k['S2_dev_main'], 1)} против "
         f"{num(k['S2_dev_med'], 1)}, с Бонферрони ДИ {ci(k['S2_dev_bonf'])} уже содержит 0). На отложенном test — "
         f"{num(k['S2_t_main_mae'], 1)} против {num(k['S2_t_median_mae'], 1)}, ΔMAE {sgn(k['S2_t_d_mae'])} {ci(k['S2_t_d_mae_ci95'], 1, True)}; "
         f"у S1 {num(k['S1_t_main_mae'], 1)} против {num(k['S1_t_median_mae'], 1)}. Значит, внутри одного рейса координаты и сезон не "
         f"объясняют плотность лучше константы. По правилу, записанному до test, на карте — медиана профиля, подписанная "
         f"«оценка по полевым данным, не по снимку». Модель остаётся в отчёте как проверенная и отвергнутая.",
         "reports/case_conc/final_test.json; README.md §5; configs/case_selection.yaml: final_test.decision_before_opening"),
        ("Почему all_litter, если задача про пластик?",
         "all_litter — не целевая величина. Целевая — суммарный пластик S2. all_litter (весь плавающий мусор Северного и "
         "Чёрного морей) используется только в исследовательском эксперименте, потому что лишь у этих событий есть сцены "
         "в окне. Везде — в API, карточке, легенде, выгрузке — он подписан «весь мусор, не только пластик»; долю пластика "
         "из публикации на отдельное наблюдение не переносим. Компромисс осознанный: взять all_litter целью значило бы "
         "подменить задачу про пластик ради снимков, а синхронных пар у него всё равно нет. Мы выбрали правильную цель без "
         "спутниковой пары и честно показали, что перенос на снимок не проверен.",
         "README.md §1 «Что не является целью»; reports/report.md §1"),
        ("Почему синхронных пар ноль, если по метаданным их 29?",
         f"±1 сут — это совпадение даты, а не места. Вода за |dt| смещается: при типичных {num(k['drift_typical_ms'], 1)} м/с "
         f"медиана сдвига {num(k['drift_typical_km'], 1)} км при допуске «полуширина полосы + {num(k['p_tolerance_buffer_km'], 1)} км». "
         f"Синхронна пара с |dt| ≤ {num(k['p_max_dt_h_typical'], 1)} ч, таких нет. У всех событий S4 время трансекты не записано "
         f"(+{num(k['p_unknown_time_extra_h'], 0)} ч к |dt|). Даже при низком течении {num(k['drift_low_ms'], 2)} м/с из {num(k['p_events_accept_meta'], 0)} проходят только "
         f"{num(k['drift_low_buf3'], 0)} событий; без учёта дрейфа приняли бы {num(k['p_accept_without_drift'], 0)}.",
         "reports/case_pairs/summary.md «Допуск по дрейфу»; configs/case_pairs.yaml"),
        ("Почему для пластика нет снимков?",
         f"S1 (Тихий океан) и S2 (Саргассово море) — открытый океан 2015 г. Sentinel-2 открытый океан почти не снимает, а "
         f"Sentinel-2A начал съёмку в конце июня 2015, экспедиция MSM41 шла в апреле. По источникам: у S1 сцен в ±5 сут "
         f"{num(k['any5_S1'], 0)}, у S2 — {num(k['any5_S2'], 0)} (Landsat в ±1 сут; с облачностью сцены ≤ 60 % — {num(k['l1c60_S2'], 0)}).",
         "README.md §3; reports/case_pairs/summary.md"),
        # --- детектор
        ("Детектор ищет пластик или любой мусор?",
         f"Любой плавающий мусор: положительный класс — Marine Debris разметки MARIDA (пластик, дерево, ткань, часто вперемешку "
         f"с органикой). Поэтому маска подписана «вероятное скопление плавающего мусора». Отдельный класс «пластик» по снимку "
         f"10 м без спектральной разметки полимеров мы не заявляем.",
         "reports/case_detector/compare.md «Выборка, эталон, классы»"),
        ("Насколько детектор лучше простых индексов?",
         f"На одном и том же test MARIDA ({num(k['d_n_scenes'], 0)} сцен, {pl(k['d_md_px'], *PX)} мусора): LightGBM F1 "
         f"{num(k['d_lgbm_f1'], 3)} {ci(k['d_lgbm_ci'], 3)}, RandomForest по протоколу статьи {num(k['d_rf_argmax_f1'], 3)}, окно FDI × NDVI "
         f"{num(k['d_fdi_ndvi_box_f1'], 3)}. Разница с RF {num(k['d_delta'], 3)} {ci(k['d_delta_ci'], 3)} по парному бутстрепу сцен. Пороги "
         f"индексов с val на test не переносятся: FDI × NDVI падает с {num(k['d_fdi_ndvi_val_f1'], 3)} до {num(k['d_fdi_ndvi_box_f1'], 3)}.",
         "reports/case_detector/compare.md; reports/case_detector/metrics.json"),
        ("Где детектор ошибается?",
         f"Из {num(k['d_fp_total'], 0)} ложных срабатываний {num(k['d_fp_ship_org_pct'], 0)} % — суда ({num(k['d_fp_ship'], 0)}) и природная "
         f"органика ({num(k['d_fp_org'], 0)}, {num(k['d_fp_org_rate'], 1)} % пикселей класса). На саргассуме, мутной воде и воде со "
         f"взвесью ({num(k['d_hard_bg_px'], 0)} пикселей) — {num(k['d_hard_bg_fp'], 0)}. Пропуски: {num(k['d_fn_top3'], 0)} из "
         f"{num(k['d_fn'], 0)} в трёх сценах, где полосы мусора размечены отдельными точками. Вырезки — в examples/.",
         "reports/case_detector/compare.md «Ложные срабатывания по классам», examples/"),
        ("Что детектор показал на реальных сценах пар?",
         f"В текущем режиме (без гармонизации каналов) на {num(k['dc_n_crops'], 0)} вырезках Sentinel-2 L2A — {num(k['dc_n_obj'], 0)} объектов, "
         f"в полосах обследования {num(k['dc_n_in_strip'], 0)}. Все на одной сцене, HE460 т.03, вне полосы: {num(k['dc_n_other_single'], 0)} пена/барашки и "
         f"{num(k['dc_n_ship'], 0)} вероятно судно (тип по правилам) — все ложные, скоплений нет (суда — известная слабость: {num(k['v2_reference_vessels_pct'], 0)} % судов). В полосах принятых черноморских пар — 0, хотя "
         f"плотность всего мусора там {num(k['e_field_min'], 0)}–{num(k['e_field_max'], 0)} шт./км²: меньше одного предмета на пиксель 10 м. "
         f"Прежний режим с гармонизацией давал {num(k['dr_n_obj'], 0)} объектов, почти все — блик, облака и барашки; это основание "
         f"отказа от гармонизации (следующий вопрос).",
         "final_numbers.json → case.detector_current; reports/case_pairs/visual_review.md; docs/DECISIONS.md"),
        ("Детектор проверен на L2A? Ведь MARIDA — ACOLITE. Зачем выключили гармонизацию?",
         f"По разметке на L2A пар — нет, и F1 {num(k['d_lgbm_f1'], 3)} на снимки пар не переносится. Гармонизация (сдвиг каналов L2A к "
         f"медиане воды MARIDA) выключена, `harmonize: {k['dc_harmonize']}`. Выбирали по MARIDA val, test не трогали: F1 без сдвига "
         f"{num(k['hv_f1_none'], 3)}, со сдвигом на сцену {num(k['hv_f1_per_scene'], 3)}, Δ {num(k['hv_delta_per_scene'], 3)} "
         f"{ci(k['hv_delta_per_scene_ci95'], 3)}. На снимках пар прежний режим с гармонизацией давал {num(k['dr_n_obj'], 0)} объектов, "
         f"и по виду вероятным скоплением было {num(k['vr_h_out_precision_pct'], 1)} % {ci(k['vr_h_out_ci95_pct'], 1)} — это подтверждение, "
         f"а не критерий выбора. Полноту на L2A проверить нечем: полевых меток на уровне пикселя нет.",
         "docs/DECISIONS.md; reports/case_pairs/harmonize_val.md, visual_review.md; configs/case_pairs.yaml"),
        ("Кто размечал срабатывания детектора на снимках пар?",
         f"Один аннотатор — ИИ-агент, по вырезкам RGB и SWIR, без полевой проверки: предмет в сантиметры на снимке 10 м не виден. "
         f"Размечено {num(k['vr_n_labelled'], 0)} объектов, из выборки {num(k['vr_h_out_n'], 0)} вне полос вероятное скопление у "
         f"{num(k['vr_h_out_k'], 0)}. κ {num(k['vr_kappa'], 2)} — слепой повтор того же агента в отдельном запуске: это устойчивость "
         f"разметки, а не согласие двух людей. Поэтому разметка — оценка, а не критерий выбора режима.",
         "reports/case_pairs/visual_review.md «Протокол», «Ограничения»"),
        ("Что в решении ваше, а что готовое?",
         "Готовое: разметка MARIDA и MADOS, протокол RandomForest из статьи MARIDA, индекс FDI, каталоги STAC, полевой реестр "
         "организаторов. Наше: отбор записей с причинами, реестр пар с допуском по дрейфу и окно синхронизации, маски качества в "
         "полосе, LightGBM на MARIDA + MADOS с порогом по val, сплит по участкам маршрута и отложенный test с правилом до "
         "открытия, геометрия трансект по PANGAEA, карта с двумя статусами, «Подобрать снимок», один источник чисел с тестом.",
         "README.md §3–§8; docs/EXTRA_FEATURES.md; слайд «Собственный вклад»"),
        ("Нет ли утечки между MADOS и MARIDA в детекторе?",
         "Сцены MADOS, снятые в тот же день или в том же месте, что val MARIDA, и совпадающие со снимками test MARIDA, из "
         "обучения исключены. Порог и настройки — только по val, test посчитан один раз. Ограничение: официальные сплиты "
         "MARIDA не разделены по тайлам, это записано.",
         "README.md §7 «Проверка пересечений и утечек»; reports/report.md, раздел «Подготовка»"),
        # --- утечки и test
        ("Как вы исключили утечки при проверке концентрации?",
         f"Основной сплит — {num(k['k_blocks'], 0)} непрерывных участков маршрута, из train убраны записи ближе {num(k['buf_days'], 0)} сут к "
         f"test-участку; нет общих событий, дней рейса и соседних дней. Поля-утечки (concentration_*, items_count, reported_* …) "
         f"запрещены как признаки, тесты портят их и проверяют, что прогноз ни одной модели не меняется. Составы всех фолдов — в "
         f"reports/case_splits/.",
         "src/macroplastic/case/splits.py; tests/test_case_concentration.py, tests/test_case_conc_model.py"),
        ("Почему сплит по маршруту, а не случайный?",
         f"Слабые схемы завышают качество через соседей того же дня. Пример S2: на «связных компонентах» kNN даёт "
         f"{num(st.get('knn5_log_mae'), 1)} против медианы {num(st.get('median_mae'), 1)} (ΔMAE {num(st.get('d'), 1)} {ci(st.get('ci95'))}), "
         f"соседей в пределах суток {num(st.get('neighbors_1d_pct'), 0)} %; на участках маршрута с буфером — {num(rb.get('knn5_log_mae'), 1)} "
         f"против {num(rb.get('median_mae'), 1)}, {ci(rb.get('ci95'))} — незначимо. У S1 сплит по событиям даёт ΔMAE {num(s1e.get('d'), 1)} при {num(s1e.get('neighbors_1d_pct'), 0)} % "
         f"соседей того же дня, маршрут с буфером — {num(s1r.get('d'), 1)} {ci(s1r.get('ci95'))}. Мы нашли это до моделей и сменили основную схему.",
         "reports/case_conc/metrics.json, baseline.md; reports/report.md §4.2, §7"),
        ("Чем доказать, что отложенный test открыт один раз и после решения?",
         f"Состав test зафиксирован {k['frozen']} (sha256 в configs/case_selection.yaml). Решение — какие модели основные и что "
         f"делать, если они не лучше медианы, — записано {k['ft_decision']} и закоммичено в `eb35414`. Test посчитан {k['ft_when']}, "
         f"результат закоммичен в `93cf856` (время коммитов: `git log -1 --format=%ci eb35414`, то же для 93cf856). Скрипт final_test_conc.py отказывается считать повторно (есть final_test.json) и раньше срока — "
         f"это проверяют тесты test_final_test_script_refuses_*. Результат не в нашу пользу и не подогнан: модель проиграла медиане.",
         "git show eb35414; git show 93cf856; reports/case_conc/final_test.json; tests/test_case_conc_model.py"),
        ("Test действительно нетронутый?",
         f"Не абсолютно, и мы это записали до открытия: {k['ft_limitation']}. Модели и гиперпараметры выбирались только на dev.",
         "configs/case_selection.yaml: final_test.decision_before_opening.limitation"),
        ("Почему в test так мало событий?",
         f"S2 — один рейс: {pl(k['t_n_events'], *EV)}. Test — целый участок маршрута ({pl(k['S2_t_n_test'], *EV)}, "
         f"{num(k['S2_t_test_cruise_days'], 0)} дней рейса, {k['S2_test_days']}), отделённый буфером: минимум {num(k['S2_min_dt'], 2)} сут и "
         f"{num(k['S2_min_km'], 0)} км до dev. Меньше, но независимо. Поэтому ДИ широкие, и мы это пишем.",
         "README.md §5 «Отложенный test»"),
        # --- концентрация
        ("Как проверена формула C = N/A?",
         f"Контрольный пример постановки: {num(k['ctl_n'], 0)} / {num(k['ctl_a'], 2)} км² = {num(k['ctl_v'], 1)} шт./км², 95 % ДИ "
         f"Пуассона {rng(k['ctl_lo'], k['ctl_hi'])}; модель с прогнозом {num(k['ctl_model'], 0)} даёт ошибку {num(k['ctl_err'], 1)}. "
         f"На реестре C = N/A совпала с опубликованной (допуск 2 %) у {num(k['cons_match'], 0)} из {num(k['cons_rows'], 0)} строк. "
         f"Агрегация — ΣN/ΣA, N = 0 даёт «не обнаружено» с верхней границей.",
         "src/macroplastic/case/concentration.py; tests/test_case_concentration.py"),
        ("Почему MAE, а не MAPE?",
         "В реестре есть нули, MAPE на них не определена. Основная — MAE, дополнительно RMSE, медиана абсолютной ошибки и "
         "MAE в log1p.", "README.md §5"),
        ("Интервалы прогноза честные?",
         f"Они недопокрывают: фактическое покрытие 90 %-интервала на CV — {num(k['S2_dev_cov'], 0)} % (S2) и {num(k['S1_dev_cov'], 0)} % (S1). "
         f"Поэтому в карточке интервал подписан фактическим покрытием, а не номиналом. На test у медианы S2 покрытие "
         f"{num(k['S2_t_median_coverage90_pct'], 0)} %.",
         "README.md §5 «Интервалы прогноза»"),
        ("Почему основной профиль S2, а не S1, где событий больше?",
         f"У S1 ({pl(k['s1_n_events'], *EV)}) C — опубликованная оценка без числителя N, так что C = N/A и интервал Пуассона не "
         f"пересчитать; другой метод (трал) и размерный класс 5–50 см. У S2 все {pl(k['t_n_events'], *EV)} имеют N и A, один "
         f"метод и один размерный класс; поверхностный визуальный учёт ближе к тому, что в принципе видно со спутника.",
         "reports/report.md §1"),
        # --- эксперимент
        ("FDI дал ρ = −0.75 и p = 0.013 — почему вы не называете это связью?",
         f"Три причины. После поправки Холма p = {num(k['e_fdi_holm'], 2)}. Та же полоса, перенесённая в случайную воду той же сцены, "
         f"даёт медиану ρ {num(k['e_fdi_null'], 2)}, то есть связь — на уровне сцены или дня, а не места. Знак обратный: мусор должен "
         f"поднимать FDI. Остаток, специфичный для полосы, — ρ {num(k['e_res_rho'], 2)}, p Холма {num(k['e_res_holm'], 2)}.",
         "reports/case_pairs/experiment.md «Вывод»"),
        ("Сколько данных нужно, чтобы проверить перенос?",
         f"При {num(k['e_n_groups'], 0)} независимых группах обнаружима только |ρ| ≥ {num(k['e_pow6'], 2)}. Для ρ = 0.5 нужно ≈ "
         f"{num(k['e_n_needed_rho05'], 0)} независимых синхронных пар, для ρ = 0.3 — ≈ {num(k['e_n_needed_rho03'], 0)}. Плюс известное "
         f"время наблюдения и пары для профиля пластика.",
         "reports/case_pairs/experiment.md «Мощность»"),
        # --- сервис
        ("Что значит статус «связь не подтверждена»?",
         "Пара «снимок ↔ измерение» отклонена (дрейф, время неизвестно, маски). Тогда детекция получает «недостаточно данных» с "
         "этой причиной, а сырые пиксели детектора остаются отдельным слоем «подозрительные пиксели без полевого подтверждения». "
         f"«Обнаружено» возможно только на подтверждённой паре — таких сейчас {num(k['exp_detected'], 0)}.",
         "README.md §6 «Слои и статусы на карте»; docs/CONTRACTS_V3.md"),
        ("Совпадает ли то, что на карте, с выгрузкой?",
         f"Самопроверка сравнивает по id алгоритм, JSON, CSV и GeoJSON: {num(k['sc_ok'], 0)} из {num(k['sc_checks'], 0)} проверок ок, "
         f"расхождений {num(k['sc_fail'], 0)}; некорректные и пустые входы — {num(k['sc_inv_ok'], 0)} из {num(k['sc_inv_total'], 0)} с верным "
         f"кодом; p95 ответа ≤ {num(k['sc_p95'], 0)} мс. Сохранённый запрос при повторе даёт тот же результат.",
         f"scripts/case/consistency_check.py; {k['sc_file']}"),
        ("Как эксперт повторит расчёт без правки кода?",
         f"`run.ps1 -Case all -Offline` — {num(k['run_s'], 1)} с, {pl(k['n_outputs'], *TAB)} с одинаковыми sha256 при повторе. "
         f"Чистый клон до карты — {k['cc_total']} мин на CPU, числа совпали. Кэш STAC, маски, реестры и предсказания детектора — в git. "
         f"Каждая метрика пересчитывается отдельной командой (README §7).",
         f"{k['cc_file']}; reports/case_run/run_summary.json; README.md §2, §7"),
        ("Какие тесты есть?",
         f"Последний прогон тестов кейса ({k['t_when']}): {num(k['n_tests'], 0)} passed, {num(k['t_skipped'], 0)} skipped, "
         f"{num(k['t_failed'], 0)} failed за {num(k['t_seconds'], 0)} с ({num(k['n_test_funcs'], 0)} тестовых функций, часть "
         f"параметризована). Проверяют формулу и контрольные примеры, непересечение фолдов и test, утечки, однократность test, "
         f"API, согласованность выгрузки и то, что числа README, деки, речи и ответов совпадают с final_numbers.json.",
         "tests/test_case_*.py, tests/test_api_v3.py; reports/case_run/case_tests.json; scripts/case/build_docs.py"),
        # --- доп
        ("Что даёт «Подобрать снимок»?",
         f"Применяет правило дрейфа к новому наблюдению: окно |dt| ≤ {num(k['pf_window_h'], 2)} ч, решение о синхронности с причиной, "
         f"маска качества в полосе. На реестре: наивное окно даёт {num(k['pf_naive'], 0)} «пар» и {num(k['pf_naive_drift'], 0)} синхронных; "
         f"для {num(k['pf_cond'], 0)} событий Чёрного моря функция выдаёт окно UTC, у {num(k['pf_cond_ok'], 0)} полоса чистая. Прогноз "
         f"пролётов на архиве совпал в {num(k['pf_fc_hit'], 0)} из {num(k['pf_fc_n'], 0)} случаев. Скорость дрейфа — сценарная, это ограничение.",
         "docs/EXTRA_FEATURES.md §1; reports/case_pairfinder/evidence.md"),
        ("Зачем восстанавливать геометрию трансект?",
         f"В реестре у трансекты — точка. Для полосы на снимке нужна линия. По первоисточникам PANGAEA восстановлено "
         f"{pl(k['g_events'], *EV)} S2/S3 ({pl(k['g_segments'], *SEG)}), {num(k['g_multi'], 0)} прерванные трансекты S2 — "
         f"двумя сегментами. Эталоном площади для C остаётся площадь автора.",
         "reports/case_geometry/summary.md"),
        # --- ограничения и развитие
        ("Можно ли перенести решение на другие акватории?",
         f"Детектор — да, с оговорками (проверен на {num(k['d_n_scenes'], 0)} сценах test MARIDA разных районов, на L2A по разметке не измерен). Оценка "
         "концентрации по полевым данным — нет: она выдаётся только в области применимости профиля (bbox полевых записей ± 1°), "
         "вне её карта пишет, что оценки нет. Снимок → шт./км² — не доказано нигде.",
         "reports/report.md §8; README.md §6"),
        ("Что вы сделаете дальше в первую очередь?",
         f"Синхронные пары: трансекты под пролёт Sentinel-2 с записанным временем — ≈ {num(k['e_n_needed_rho05'], 0)} независимых пар "
         f"для ρ = 0.5; наблюдения пластика в прибрежных водах, которые снимает Sentinel-2. Затем больше рейсов и сезонов для "
         f"полевой модели, ERA5 для дрейфа вместо констант, разметка скоплений на L2A.",
         "reports/report.md §9"),
        ("Какая главная ошибка по ходу работы и что вы с ней сделали?",
         "Выигрыш kNN на схеме связных компонент оказался утечкой через соседей того же дня рейса — основную схему заменили "
         "до моделей. Ещё: спектральный тест облаков срабатывал на блике (добавлено правило блика), выбор лучшей из равных "
         "сцен зависел от порядка выдачи STAC (сделан детерминированным).",
         "reports/report.md §7 «Ошибки, найденные по ходу»"),
        # --- §11 расследование данных
        ("Как вы искали данные и как пришли к алгоритму?",
         f"Сначала прогнали все {num(k['sr_events'], 0)} событий CSV по архивам снимков и пометили каждую находку уровнем A–D. Пар A у "
         f"событий CSV — {num(k['sr_csv_A'], 0)}: для пластика снимков нет, в Северном море облака и маршрут вне кадра, в Чёрном время и "
         f"площадь трансект не опубликованы (C {num(k['sr_S4_C'], 0)}). По стоп-правилу поиск по дрейфу не расширяли, а искали другие "
         f"источники: полевые (ADIS — {num(k['ad_A'], 0)} пар по месту и времени) и размеченные (B/D) для детектора. Поэтому алгоритм — два раздельно "
         f"проверенных блока: детектор на разметке и полевая концентрация, без калибровки между ними.",
         "docs/PIPELINE.md; docs/SEARCH_LOG.md; README.md «Как мы искали данные»; final_numbers.json → case.sections.search"),
        ("У вас есть пары уровня A (ADIS) — почему тогда нет калибровки «снимок → шт./км²»?",
         f"Потому что это пары по месту и времени, а не калибровочные. Все {num(k['ad_A'], 0)} пар — вида «0 на снимке при N шт./км²»: на "
         f"{num(k['ad_A_with_items'], 0)} отрезках с единичными предметами ({num(k['ad_A_with_items_density_min'], 1)}–"
         f"{num(k['ad_A_with_items_density_max'], 1)} шт./км²) детектор предметы не увидел; оценивается на {num(k['ad_A_with_items_eval'], 0)} "
         f"из {num(k['ad_A_with_items'], 0)} — на всех {num(k['ad_A_with_items_eval_det_px'], 0)}. Калибровочная пара — пара по месту и времени с "
         f"ненулевым сигналом снимка; таких {num(k['ql_calibration_pairs'], 0)}, а нужно {num(k['ql_calibration_needed_min'], 0)}–"
         f"{num(k['ql_calibration_needed_max'], 0)}. Регрессию по нулям не построить.",
         "reports/search/adis.md; reports/search/adis_candidates.csv; docs/QUANTITY.md §0"),
        ("Значит, вы доказали предел обнаружения Sentinel-2?",
         f"Нет, и мы так не говорим. На {num(k['ad_A_with_items'], 0)} синхронных отрезках ADIS с единичными предметами детектор предметы не "
         f"увидел (детектор оценивается на {num(k['ad_A_with_items_eval'], 0)} из {num(k['ad_A_with_items'], 0)} отрезков — на всех "
         f"{num(k['ad_A_with_items_eval_det_px'], 0)}); это согласуется с физикой (доля покрытия ≈ {k['ad_coverage_frac_text']}, предмет — {num(k['ad_largest_item_pct_px'], 1)} % "
         f"пикселя), но не задаёт общий предел для всех скоплений: плотные полосы мусора эти пары не проверяют. Отдельно мы измерили фон "
         f"ложных тревог при полевом нуле: при ветре < {num(k['ad_wind_split_ms'], 0)} м/с — {num(k['ad_calm_px'], 0)} пикселей, при сильном — "
         f"{num(k['ad_windy_px'], 0)} на {num(k['ad_windy_strip_km2'], 1)} км² полосы (барашки).",
         "reports/search/adis.md; README.md «Как мы искали данные»"),
        ("Действительно ли пары ADIS синхронны?",
         f"Отбор |dt| ≤ {num(k['ad_dt_max_h'], 0)} ч и дрейф в допуске; у {num(k['ad_A_ship_found'], 0)} пар судно-съёмщик найдено на самом "
         f"снимке рядом с расчётной позицией — время подтверждено независимо. Независимых проходов ≈ {num(k['ad_A_passes'], 0)}: камеры "
         f"двух бортов дают два отрезка на один проход.",
         "reports/search/adis.md «Выводы»"),
        ("Почему нет калибровки «доля покрытия → шт./км²» и сколько пар для неё нужно?",
         f"Модель ln C = ln k + b · ln S требует калибровочных пар (по месту и времени и с ненулевым сигналом снимка), а их {num(k['q_pairs_A_with_S_pos'], 0)}. Для множителя k в "
         f"пределах ×/÷2 нужно {num(k['q_n_pairs_k_x2_min'], 0)}–{num(k['q_n_pairs_k_x2_max'], 0)} независимых пар с сигналом, для связи r = 0.5 "
         f"— {num(k['q_n_pairs_r05'], 0)}; доля полос с сигналом ≤ {num(k['q_p_nz'], 2)}, значит обследований нужно в {num(k['q_mult'], 1)} раза "
         f"больше. Даже идеальный спутник не сузит интервал одной оценки уже ×/÷{num(k['q_floor_factor'], 1)} — пятнистость и Пуассон.",
         "reports/quantity/calibration.json; docs/QUANTITY.md"),
        ("Что такое «доля покрытия» на снимке и почему это не концентрация?",
         f"Это м² маски детектора на км² пригодной воды — как у Cózar et al. 2024. Маска — «подозрительный материал», в ней пена, органика, "
         f"суда. Перевод в штуки зависит от допущений о размере предметов и дал бы разброс в {num(k['q_sc_ratio'], 0)} раз, поэтому мы его "
         f"не показываем: {k['q_sc_reason']}. Массу не оцениваем.",
         "reports/quantity/scenario.json; docs/QUANTITY.md §4"),
        ("Почему вы не откалибровали спутник по полю?",
         f"Потому что природных калибровочных пар «снимок → шт./км²» нет ни у нас ({num(k['ql_calibration_pairs'], 0)}), ни у авторов "
         f"крупнейшего каталога мусорных полос по Sentinel-2. Они пишут дословно: «{k['q_quote']}» ({k['q_quote_ref']}). У нас "
         f"{num(k['ad_A'], 0)} пар ADIS — пары по месту и времени с нулевым сигналом снимка (отобраны из {num(k['ad_seg_total'], 0)} "
         f"отрезков; с предметами — {num(k['ad_seg_items'], 0)}, снимков сверхвысокого разрешения того же дня над ними — {num(k['ad_vhr'], 0)}); "
         f"для калибровки нужно "
         f"{num(k['ql_calibration_needed_min'], 0)}–{num(k['ql_calibration_needed_max'], 0)} пар с сигналом.",
         "data/extra/cozar2024/paper.txt (проверено по тексту); docs/QUANTITY.md §0, §3"),
        ("Почему не откалибровать по ячейкам, как считают пальмы на гектар по Sentinel-2?",
         f"Аналогия уместна: {k['q_an_text']}. Но для мусора {k['q_an_not']}. Мы проверили: общих дат поле × спутник в одном периоде "
         f"{num(k['q_cc_dates'], 0)}; климатологии разных лет дают ρ = {num(k['q_cc_rho'], 2)} на {num(k['q_cc_cells'], 0)} ячейках, и это "
         f"почти целиком география (удалённость от берега). Правило принятия не пройдено, модели нет.",
         "reports/quantity/cell_calibration.md; docs/QUANTITY.md §5"),
        ("Откуда B и D и как проверены утечки?",
         f"B — открытые наборы с подтверждённой разметкой: PLP ({num(k['ld_plp'], 0)} съёмок), FloatingObjects ({num(k['ld_fo'], 0)}), нити "
         f"Cózar 2024 ({num(k['ld_cz_win'], 0)} окон). D — суда у Финляндии ({num(k['ld_ves_boxes'], 0)} рамок) и облака Cloud Mask Catalogue "
         f"({num(k['ld_cl_scenes'], 0)} сцен). Пиксели — L2A той же съёмки тем же кодом, что у сервиса. С MARIDA — {num(k['ld_leaks_total'], 0)} совпадений по тайлу "
         f"и дате для всех наборов. С MADOS по содержимому (пикселям, LBP) проверены PLP и FloatingObjects: {num(k['ld_content_match'], 0)} из "
         f"{num(k['ld_content_checked'], 0)}; для Cózar, судов и облаков сверка с MADOS невозможна — у сцен MADOS нет геопривязки. Демо-сцена "
         f"проверена по содержимому отдельно (reports/audit). Исключён {num(k['ld_dup_excluded'], 0)} дубль. Лицензии — в реестрах.",
         "reports/extra_data/registry.csv, registry_negatives.csv, registry_cozar2024.csv.gz, content_overlap.csv"),
        ("Почему дообучение на новых данных не приняли?",
         f"Правило записано до экспериментов: ΔF1 на MARIDA val ≥ {num(k['v2_need_df1'], 2)} и не хуже на B и D. Итог: {k['v2_note']} "
         f"(ΔF1 от {num(k['v2_df1_min'], 3)} до {num(k['v2_df1_max'], 3)}). "
         f"Суда и нити спектрально похожи: D судов снижает ложные с {num(k['v2_reference_vessels_pct'], 0)} % до {num(k['v2_vessels_d_vessels_pct'], 0)} %, "
         f"но полнота на нитях (хотя бы один пиксель нити) падает с {num(k['v2_reference_cozar_pct'], 0)} % до {num(k['v2_vessels_d_cozar_pct'], 0)} %. В сервисе — {k['v2_current_model']}.",
         "reports/detector_v2/experiments.md; configs/detector_v2_eval.yaml"),
        ("Почему официальный U-Net MARIDA хуже вашего LightGBM?",
         f"Веса и предобработка авторов, та же функция оценки: на test U-Net {num(k['bl_unet_argmax'], 3)} {ci(k['bl_unet_ci'], 3)} против "
         f"{num(k['bl_lgbm'], 3)}; LightGBM лучше в {num(k['bl_better'], 0)} из {num(k['bl_n_scenes'], 0)} сцен. U-Net больше путает суда "
         f"({num(k['bl_ship_unet'], 0)} пикселей против {num(k['bl_ship_lgbm'], 0)}) и на снимках пар даёт {num(k['bl_pairs_unet'], 0)} "
         f"объектов «соль-перец». В статье MARIDA U-Net тоже слабее RandomForest.",
         "reports/detector_v2/unet_test.json, unet_baseline.md"),
        ("Что эколог получит на новой акватории без полевых данных?",
         "Маску детектора «вероятное скопление плавающего мусора» с маской качества (облака, блик, суша) и площадь маски в м² и как "
         "долю покрытия подозрительного материала — это измерение по снимку, а не мусор и не шт./км². Инструмент «Подобрать снимок» "
         "подскажет окно пролёта для полевого обследования. Оценки шт./км² там нет: полевая модель работает только в области "
         "применимости профиля, а перевода «снимок → шт./км²» нет — калибровочных пар ноль. Карта так и пишет: «концентрация недоступна».",
         "README.md «Главный количественный результат», «Как мы искали данные»; docs/QUANTITY.md; docs/EXTRA_FEATURES.md"),
        ("Почему слой «Нефтяное пятно» выключен?",
         f"На MADOS голова лучше индекса OSI (test {num(k['oil_test_f1'], 3)} против {num(k['oil_test_osi_f1'], 3)}), но на реальном L2A "
         f"известный разлив Wakashio не найден: {num(k['oil_w1_km2'], 2)} км² мелких пятен в первый день и {num(k['oil_w2_km2'], 2)} км² ряби "
         f"во второй. Это честный отказ: слой — эксперимент, выключен по умолчанию; для нефти обычно нужен радар.",
         "docs/OIL.md; reports/oil/control.json"),
    ]
    return Q


def qa_md(k: dict) -> str:
    Q = qa_items(k)
    L = ["# Вопросы жюри и ответы", "",
         "Генерируется `scripts/make_deck_case.py` из `reports/final_numbers.json`; руками не править. У каждого ответа — "
         "где доказательство. Отвечаем коротко: сначала вывод, потом одно число, потом ссылка.", ""]
    for i, (q, a, ref) in enumerate(Q, 1):
        L += [f"## {i}. {q}", "", a, "", f"*Доказательство:* {ref}", ""]
    return "\n".join(L)


def preview(S: list[dict], imgs: dict) -> int:
    """PNG по слайдам: каждый слайд — отдельный pptx, LibreOffice конвертирует первый слайд в PNG."""
    import shutil

    so = shutil.which("soffice") or next((str(p) for p in (Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
                                                         Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"))
                                          if p.exists()), None)
    if not so:
        print("[deck_case] LibreOffice не найден: превью пропущено")
        return 0
    out = ROOT / "reports" / "case_deck_preview"
    work = out / "_work"
    work.mkdir(parents=True, exist_ok=True)
    made = 0
    for i in range(1, len(S) + 1):
        f = work / f"slide_{i:02d}.pptx"
        build_pptx(S[i - 1:i], imgs, f, start=i)
        cmd = [so, f"-env:UserInstallation=file:///{(work / 'lo_profile').as_posix()}", "--headless",
               "--convert-to", "png", "--outdir", str(out), str(f)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=180)
            made += (out / f"slide_{i:02d}.png").exists()
        except Exception as e:  # noqa: BLE001
            print(f"[deck_case] превью слайда {i}: {e}")
    shutil.rmtree(work, ignore_errors=True)
    print(f"[deck_case] превью: {made}/{len(S)} PNG -> {out.relative_to(ROOT)}")
    return made


def _soffice():
    import shutil
    return shutil.which("soffice") or next((str(p) for p in (Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
                                                            Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"))
                                             if p.exists()), None)


def to_pdf(pptx: Path) -> Path | None:
    """PDF деки через LibreOffice headless (отдельный профиль, чтобы не мешать открытому LibreOffice)."""
    so = _soffice()
    if not so:
        print("[deck_case] LibreOffice не найден: PDF пропущен")
        return None
    prof = ROOT / "out" / "lo_profile_deck"
    cmd = [so, f"-env:UserInstallation=file:///{prof.as_posix()}", "--headless", "--convert-to", "pdf",
           "--outdir", str(pptx.parent), str(pptx)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    pdf = pptx.with_suffix(".pdf")
    print(f"[deck_case] PDF: {pdf.relative_to(ROOT)} ({pdf.stat().st_size // 1024} КБ)")
    return pdf


# ----------------------------------------------------------------------------------------------- main
def generate() -> tuple[list[dict], dict, dict]:
    """Слайды, тексты md и числа — без записи файлов (для тестов). MISSING и USED заполняются заново."""
    MISSING.clear()
    USED.clear()
    k = load()
    S = slides(k)
    texts = {OUT_SPEECH: speech_md(S, k), OUT_DEMO: demo_md(k), OUT_QA: qa_md(k), OUT_PRES / "qa.md": qa_md(k)}
    return S, texts, k


def slide_strings(s: dict) -> list[str]:
    """Все строки, которые build_pptx кладёт на слайд (и в заметки) — для сверки с файлом деки."""
    out = [s["section"], s["title"], *s.get("bullets", []), "Источник: " + s["source"], s["speech"]]
    for val, lab in s.get("kpis", []):
        out += [val, lab]
    for _, cap in s.get("images", []):
        out.append(cap)
    if s.get("notes"):
        out.append(s["notes"])
    if "big" in s:
        out += list(s["big"])
    if s.get("caption"):
        out.append(s["caption"])
    if "table" in s:
        out += [str(c) for row in s["table"] for c in row]
    if "bars" in s:
        b = s["bars"]
        out += [b["title"], *(lab for lab, _, _ in b["items"]), *(num(v, b.get("fmt", 0)) for _, v, _ in b["items"])]
        if b.get("note"):
            out.append(b["note"])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="case_deck.pptx + SPEECH.md + DEMO.md + QA.md из final_numbers.json")
    ap.add_argument("--check", action="store_true", help="только проверить, что все значения нашлись")
    ap.add_argument("--out", default=str(OUT_PPTX))
    ap.add_argument("--preview", action="store_true",
                    help="PNG каждого слайда через LibreOffice -> reports/case_deck_preview/ (не для git)")
    ap.add_argument("--preview-main", action="store_true", help="превью только основной части")
    ap.add_argument("--pdf", action="store_true", help="presentation/deck.pdf через LibreOffice headless")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    S, texts, k = generate()
    if not a.check:
        imgs = prepare_images()
    if MISSING:
        print(f"[deck_case] не найдено значений: {len(MISSING)}")
        for m in MISSING:
            print("   ", m)
        return 1
    if a.check:
        print(f"[deck_case] все значения найдены; слайдов {len(S)}, вопросов {len(qa_items(k))}")
        return 0
    build_pptx(S, imgs, Path(a.out))
    OUT_PRES.mkdir(parents=True, exist_ok=True)
    build_pptx(S, imgs, OUT_PRES / "deck.pptx")
    for p, t in texts.items():
        p.write_text(t, encoding="utf-8")
    if a.pdf:
        to_pdf(OUT_PRES / "deck.pptx")
    if a.preview or a.preview_main:
        preview([s for s in S if not s.get("appendix")] if a.preview_main else S, imgs)
    print(f"[deck_case] {Path(a.out).relative_to(ROOT)}: {len(S)} слайдов; docs/SPEECH.md, docs/DEMO.md, "
          f"docs/QA.md ({len(qa_items(k))} вопросов); картинок {len(imgs)} в {IMG_DIR.relative_to(ROOT)}; пустых значений 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
