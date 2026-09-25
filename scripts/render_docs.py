r"""Fill README.md and reports/report.md from their templates with numbers from reports/final_numbers.json.

Usage (from repo root):
    .venv\Scripts\python.exe scripts\final_numbers.py      # 1) collect numbers
    .venv\Scripts\python.exe scripts\render_docs.py        # 2) README.md.tmpl -> README.md, reports\report.md.tmpl -> reports\report.md

Template syntax:
    {{l3_lgbm.val.f1_md}}          value by dotted path (list items by index: service.regions.0.name)
    {{l3_lgbm.val.f1_md|f3}}       filters: f1 f2 f3 (fixed decimals), pct (x100, 1 decimal, no % sign),
                                   int (thousands separated), km2, raw
    {{service.n_dates|pl:живой снимок/живых снимка/живых снимков}}
                                   number + noun agreed by Russian rules (1 / 2-4 / 5+, 11-14 -> 5+)
    {{derived.metrics_table}}      ready-made markdown blocks computed here (see derived())
Missing or null values render as an em dash "—" and are listed on stdout (exit code stays 0).
Only placeholders whose path starts with a known top-level key are replaced; any other {{...}} is kept.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASH = "—"
PAIRS = [("README.md.tmpl", "README.md"), ("reports/report.md.tmpl", "reports/report.md")]
PH = re.compile(r"\{\{\s*([A-Za-z_][\w.]*)\s*(?:\|\s*(\w+)(?::([^{}]*?))?\s*)?\}\}")


def plural_ru(n, one: str, few: str, many: str) -> str:
    """Russian noun form for integer n: 1 снимок, 2-4 снимка, 5-20 снимков, 21 снимок, 111 снимков."""
    n = abs(int(n))
    if n % 100 in (11, 12, 13, 14):
        return many
    if n % 10 == 1:
        return one
    if n % 10 in (2, 3, 4):
        return few
    return many


def fmt_pl(v, forms: str) -> str:
    """'64' + 'живой снимок/живых снимка/живых снимков' -> '64 живых снимка'."""
    parts = [s.strip() for s in (forms or "").split("/")]
    if v is None or len(parts) != 3:
        return fmt(v, None) + (f" {parts[-1]}" if parts and parts[-1] else "")
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return f"{v} {parts[2]}"
    num = f"{n:,}".replace(",", " ")
    return f"{num} {plural_ru(n, *parts)}"


def get_path(data, path: str):
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


def fmt(v, flt: str | None) -> str:
    if v is None or v == "" or v == []:
        return DASH
    if flt == "raw" or isinstance(v, str):
        return str(v)
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, (list, tuple)):
        return ", ".join(fmt(x, flt) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if flt in ("f1", "f2", "f3"):
        return f"{x:.{int(flt[1])}f}"
    if flt == "pct":
        return f"{100 * x:.1f}"
    if flt == "int":
        return f"{int(round(x)):,}".replace(",", " ")
    if flt == "km2":
        return f"{x:.2f}"
    if isinstance(v, int):
        return f"{v:,}".replace(",", " ") if abs(v) >= 10000 else str(v)
    return f"{x:.4g}"


def _f(d, key, flt="f3"):
    return fmt((d or {}).get(key), flt)


LRO_NAMES = {"honduras_gulf": "Гондурасский залив", "haiti_pap": "Порт-о-Пренс", "jakarta": "Джакарта",
             "bay_islands": "острова Ислас-де-ла-Баия"}


def _pm(mean, std, nd=4) -> str:
    if mean is None:
        return DASH
    return f"{float(mean):.{nd}f} ± {float(std):.{nd}f}" if std is not None else f"{float(mean):.{nd}f}"


def _ci(ci, nd=3) -> str:
    if not (isinstance(ci, list) and len(ci) == 2 and None not in ci):
        return DASH
    return f"{float(ci[0]):.{nd}f}…{float(ci[1]):.{nd}f}"


def _flat_numbers(d, prefix="", out=None, limit=16):
    """Numeric leaves of an arbitrary JSON (for reports/speed.json, whose layout is not fixed)."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(d, dict):
        for k, v in d.items():
            _flat_numbers(v, f"{prefix}.{k}" if prefix else str(k), out, limit)
    elif isinstance(d, (int, float)) and not isinstance(d, bool):
        out.append((prefix, d))
    elif isinstance(d, str) and prefix and len(d) <= 60 and len(out) < limit:
        out.append((prefix, d))
    return out


def _s(x, unit=" с", nd=1) -> str:
    """Seconds with a decimal comma for prose tables."""
    if x is None:
        return DASH
    return f"{float(x):.{nd}f}".replace(".", ",") + unit


def speed_table(fn: dict) -> str:
    """Human-readable speed table from final_numbers speed.rows (+ light model from l23)."""
    sp = fn.get("speed") or {}
    if not sp.get("available"):
        return "Замер скорости инференса ещё не записан (`reports/speed.json`)."
    rows = sp.get("rows") or []
    if not rows:
        return str(sp.get("summary")) if sp.get("summary") else DASH
    chips = sp.get("chips")
    out = [f"`inference.py` на {fmt(chips, None)} чипах 256×256×11 (первые патчи val MARIDA), холодный старт — новый "
           "процесс от запуска до выхода, медиана 3 запусков:", "",
           "| Режим | Холодный старт | Разброс запусков | Тёплый режим, чипов/с | До оптимизации |",
           "|---|---|---|---|---|"]
    for r in rows:
        rng = (f"{_s(r.get('cold_s_min'), '', 2)}–{_s(r.get('cold_s_max'), ' с', 2)}"
               if r.get("cold_s_min") is not None else DASH)
        warm = DASH if r.get("warm_chips_per_s") is None else f"{float(r['warm_chips_per_s']):.1f}".replace(".", ",")
        out.append(f"| {r.get('mode')} | **{_s(r.get('cold_s'), ' с', 2)}** | {rng} | {warm} | {_s(r.get('before_cold_s'), ' с', 1)} |")
    lt = (fn.get("l23") or {}).get("light") or {}
    if lt.get("cold_s_300") is not None and lt.get("final_cold_s_300") is not None:
        thr = (fn.get("l23") or {}).get("threads")
        comb = ((fn.get("models") or {}).get("rows") or {}).get("combined") or {}
        out += ["", f"Лёгкая модель ({fmt(lt.get('n_features'), None)} признаков, {fmt(lt.get('n_trees'), None)} деревьев, "
                f"листьев в дереве до {fmt(lt.get('num_leaves'), None)}) замерена на отдельном стенде: пакет lightgbm, "
                f"CPU {fmt(thr, None)} потоков, машина под чужой нагрузкой. Секунды сравнимы только внутри этой таблицы:", "",
                "| Модель | F1 MD val (3 seed) | Холодный старт, 300 чипов | Тёплый режим, чипов/с |", "|---|---|---|---|",
                f"| итоговая ({fmt(lt.get('final_n_features'), None)} признаков, {fmt(lt.get('final_n_trees'), None)} деревьев) | "
                f"{_pm(comb.get('val_f1_mean'), comb.get('val_f1_std'), 3)} | "
                f"{_s(lt.get('final_cold_s_300'))} | {fmt(lt.get('final_warm_chips_per_s'), 'f1').replace('.', ',')} |",
                f"| лёгкая (отклонена: хуже переносится на новый район) | {_pm(lt.get('f1_mean'), lt.get('f1_sd'), 3)} | "
                f"{_s(lt.get('cold_s_300'))} | {fmt(lt.get('warm_chips_per_s'), 'f1').replace('.', ',')} |"]
    if sp.get("hardware"):
        out += ["", f"Железо: {sp['hardware']}."]
    return "\n".join(out)


BASELINE_ROWS = [
    ("fdi_threshold", "Порог FDI (одно правило)", "FDI", "подобран на val"),
    ("fdi_interval", "Интервал FDI (два порога)", "FDI", "подобраны на val"),
    ("single_index_threshold", "Порог лучшего из NDVI / FAI", None, "подобран на val"),
    ("fdi_ndvi_box", "FDI и NDVI в интервалах (4 порога)", "FDI, NDVI", "подобраны на val"),
    ("random_forest_marida", "RandomForest, параметры статьи MARIDA", "11 каналов + 8 индексов", "argmax, без подбора"),
    ("lgbm_no_windows", "LightGBM без оконных признаков", "11 каналов + 8 индексов", "подобран на val"),
]


def baselines_table(fn: dict) -> str:
    """Ladder: simple rules -> RF -> LightGBM -> + windows -> + MADOS (final). All on val MARIDA, same metric."""
    bl = (fn.get("baselines") or {}).get("rows") or {}
    if not bl:
        return "Бейзлайны ещё не посчитаны (`scripts/baselines.py`)."
    out = ["| Модель / правило | Признаки | Порог | F1 MD val | IoU MD | Precision | Recall | 95 % CI F1 по сценам | F1, порог с train |",
           "|---|---|---|---|---|---|---|---|---|"]
    for key, name, feats, thr in BASELINE_ROWS:
        r = bl.get(key)
        if not r:
            continue
        if key == "single_index_threshold" and r.get("index"):
            name = f"Порог {r['index']} (лучший из NDVI / FAI)"
            feats = r["index"]
        f1 = fmt(r.get("f1_md"), "f3")
        if r.get("f1_std") is not None and r.get("n_seeds"):
            m = r.get("f1_mean") if r.get("f1_mean") is not None else r.get("f1_md")
            f1 = f"{_pm(m, r.get('f1_std'), 3)} ({r['n_seeds']} seed)"
        out.append(f"| {name} | {feats} | {thr} | {f1} | {fmt(r.get('iou_md'), 'f3')} | {fmt(r.get('precision'), 'f3')} | "
                   f"{fmt(r.get('recall'), 'f3')} | {_ci(r.get('scene_ci95_f1'))} | {fmt(r.get('f1_md_threshold_from_train'), 'f3')} |")
    mo = (fn.get("models") or {}).get("rows") or {}
    mar, comb = mo.get("marida_only") or {}, mo.get("combined") or {}
    au = fn.get("metric_audit") or {}
    l3 = fn.get("l3_lgbm") or {}
    v = l3.get("val") or {}
    nf = (f"{l3['n_base_features']} + {l3['n_window_features']} оконных (3–31 px)"
          if l3.get("n_base_features") and l3.get("n_window_features") else "с оконными признаками")
    bk = au.get("marida_only") or {}
    if mar.get("val_f1_mean") is not None:
        out.append(f"| LightGBM + оконные признаки, только MARIDA | {nf} | подобран на val | "
                   f"{_pm(mar.get('val_f1_mean'), mar.get('val_f1_std'), 3)} (3 seed) | {fmt(mar.get('val_iou_mean'), 'f3')} | "
                   f"{fmt(bk.get('precision'), 'f3')} | {fmt(bk.get('recall'), 'f3')} | {_ci(bk.get('scene_bootstrap_ci95'))} | {DASH} |")
    if comb.get("val_f1_mean") is not None:
        out.append(f"| **LightGBM + окна, MARIDA + MADOS (итоговая)** | {nf} | подобран на val | "
                   f"**{_pm(comb.get('val_f1_mean'), comb.get('val_f1_std'), 3)}** (3 seed) | {fmt(v.get('iou_md'), 'f3')} | "
                   f"{fmt(v.get('precision_md'), 'f3')} | {fmt(v.get('recall_md'), 'f3')} | {_ci(au.get('scene_bootstrap_ci95'))} | {DASH} |")
    return "\n".join(out)


L23_LABELS = {"a_rgb_strict": "RGB, только общие признаки", "a_rgb": "RGB + оконные признаки RGB",
              "b_rgbnir": "10 м (RGB + NIR)", "b_rgbnir_x": "10 м + оконные признаки RGB",
              "f_10m_swir": "10 м + B11, B12", "d_10m_20m": "10 м + 20 м (без B1)", "e_full": "все 11 каналов (итоговая)"}


def l23_channels_table(fn: dict) -> str:
    l23 = fn.get("l23") or {}
    subs = l23.get("subsets") or []
    if not subs:
        return DASH
    out = ["| Каналы | Признаков | F1 MD val | Δ к итоговой |", "|---|---|---|---|"]
    for sub in subs:
        f1 = sub.get("f1_mean") if sub.get("f1_mean") is not None else sub.get("f1")
        f1s = (_pm(sub.get("f1_mean"), sub.get("f1_sd"), 3) + f" ({sub.get('n_seeds')} seed)"
               if sub.get("f1_sd") is not None else f"{fmt(f1, 'f3')} (1 seed)")
        d = sub.get("delta_vs_full_mean") if sub.get("delta_vs_full_mean") is not None else sub.get("delta_vs_full")
        ds = "0 (база)" if sub.get("name") == "e_full" else (DASH if d is None else f"{float(d):+.3f}")
        out.append(f"| {L23_LABELS.get(sub.get('name'), sub.get('label') or sub.get('name'))} | {fmt(sub.get('n_features'), None)} | {f1s} | {ds} |")
    lt = l23.get("light") or {}
    if lt:
        out.append(f"| все 11, лёгкая модель ({fmt(lt.get('n_features'), None)} признаков, {fmt(lt.get('n_trees'), None)} деревьев) | "
                   f"{fmt(lt.get('n_features'), None)} | {_pm(lt.get('f1_mean'), lt.get('f1_sd'), 3)} ({fmt(lt.get('n_seeds'), None)} seed) | "
                   f"{DASH} |")
    return "\n".join(out)


def _sx(x, nd=1):
    return DASH if x is None else f"{float(x):.{nd}f}".replace(".", ",")


def threads_table(fn: dict) -> str:
    """Cold start by CPU threads and GPU (final model; mid-size model for reference)."""
    st = fn.get("speed_threads") or {}
    fin, mid, sp = st.get("final") or {}, st.get("mid") or {}, st.get("speedup_mid") or {}
    if not fin:
        return DASH
    cols = [("cpu4", "CPU, 4 потока"), ("cpu8", "CPU, 8 потоков"), ("cpu20", "CPU, 20 потоков"), ("gpu", "GPU")]
    out = ["| Модель | " + " | ".join(c[1] for c in cols) + " |", "|---|" + "---|" * len(cols),
           "| **итоговая** (48 признаков, 400 деревьев) | " + " | ".join(f"**{_sx(fin.get(k), 2 if k == 'gpu' else 1)} с**" for k, _ in cols) + " |"]
    if mid:
        out.append("| средняя (20 признаков, 400 деревьев; отклонена) | " + " | ".join(f"{_sx(mid.get(k), 2 if k == 'gpu' else 1)} с" for k, _ in cols) + " |")
    if sp:
        out.append("| ускорение средней | " + " | ".join(f"×{_sx(sp.get(k), 2)}" for k, _ in cols) + " |")
    return "\n".join(out)


def rejected_table(fn: dict) -> str:
    rj = fn.get("rejected") or {}
    lro = rj.get("final_lro")
    comb = ((fn.get("models") or {}).get("rows") or {}).get("combined") or {}
    out = ["| Кандидат | Зачем | F1 MD val (3 seed) | F1 на новом районе (LRO) | Скорость | Решение |", "|---|---|---|---|---|---|",
           f"| **итоговая LightGBM** | — | **{fmt(comb.get('val_f1_mean'), 'f3')}** | **{fmt(lro, 'f3')}** | "
           f"{_sx((fn.get('speed_threads') or {}).get('final', {}).get('cpu8'))} с на CPU 8 потоков | принята |"]
    lt = rj.get("light") or {}
    if lt:
        out.append(f"| лёгкая ({fmt(lt.get('n_features'), None)} признаков, {fmt(lt.get('n_trees'), None)} × {fmt(lt.get('num_leaves'), None)}) | "
                   f"быстрее на CPU | {fmt(lt.get('val_f1_mean'), 'f3')} | {fmt(lt.get('lro_mean'), 'f3')} (нужно ≥ {fmt(lt.get('lro_rule'), 'f3')}) | "
                   f"модель в ~3 раза быстрее | отклонена: хуже на новом районе |")
    md = rj.get("mid") or {}
    if md:
        out.append(f"| средняя ({fmt(md.get('n_features'), None)} признаков, {fmt(md.get('n_trees'), None)} × {fmt(md.get('num_leaves'), None)}) | "
                   f"быстрее на CPU без потери переноса | {fmt(md.get('val_f1_mean'), 'f3')} | {fmt(md.get('lro_mean'), 'f3')} | "
                   f"×{_sx(md.get('speedup_cpu8'), 2)} на CPU 8 потоков (нужно ×{_sx(md.get('speedup_needed'), 1)}) | отклонена: почти не быстрее |")
    z = rj.get("zfeat") or {}
    if z:
        rng = z.get("val_delta_range") or [None, None]
        out.append(f"| признаки относительно воды своей сцены ({fmt(z.get('n_variants'), None)} вариантов) | лучше перенос | "
                   f"{fmt(z.get('zs_val_mean'), 'f3')} ({z.get('zs_val_delta'):+.3f}; по вариантам {rng[0]:+.3f}…{rng[1]:+.3f}) | "
                   f"{fmt(z.get('zs_lro_mean'), 'f3')} ({z.get('zs_lro_delta'):+.3f}) | признаки +25 % времени | отклонены: падение на val больше допуска 0,005 |"
                   if z.get("zs_val_delta") is not None and rng[0] is not None else "| признаки относительно сцены | | | | | отклонены |")
    return "\n".join(out)


def _mmss(sec) -> str:
    if sec is None:
        return DASH
    m, x = divmod(int(sec), 60)
    return f"{m} мин {x:02d} с" if m else f"{x} с"


def rehearsal_text(fn: dict) -> str:
    r = fn.get("rehearsal") or {}
    if not r.get("available"):
        return DASH
    hm = r.get("human_min") or [None, None]
    return (f"Репетиция на «чужом» наборе: {fmt(r.get('n_scenes'), None)} сцен test-сплита MADOS, упакованных как архив "
            f"организаторов (uint16 DN, свои маски, 147 test-чипов, спрятанная разметка). Первый валидный сабмит — через "
            f"{fmt(r.get('first_submit_s'), None)} с машинного времени после распаковки (для человека по оценке "
            f"{fmt(hm[0], None)}–{fmt(hm[1], None)} мин; в первой репетиции — {_mmss(r.get('first_submit_s_prev'))}). "
            f"Private F1 «как есть» — {fmt(r.get('private_asis'), 'f3')}, после обучения на их train — "
            f"{fmt(r.get('private_trained'), 'f3')} (сабмит на T+{fmt(r.get('trained_submit_min'), None)}). Разбор «как есть»: "
            f"упаковка и адаптер вносят ±0,0003 F1; на размеченных пикселях без класса sea snot F1 "
            f"{fmt(r.get('labelled_no_seasnot_f1'), 'f3')}; низкий F1 даёт метрика по всем пикселям при "
            f"{_sx(r.get('unlabelled_pct'))} % неразмеченных (лучший порог ≈ {fmt(r.get('best_all_px_thr'), None)}, F1 "
            f"{fmt(r.get('best_all_px_f1'), 'f3')}) и то, что у них sea snot размечен как негатив.")


def robustness_text(fn: dict) -> str:
    r = fn.get("robustness") or {}
    if not r.get("available"):
        return DASH
    return (f"PASS {fmt(r.get('pass'), None)} из {fmt(r.get('n_checks'), None)}, WARN {fmt(r.get('warn'), None)}, "
            f"FAIL {fmt(r.get('fail'), None)}")


def _test_sentence(t: dict) -> str:
    f1, ci = t.get("f1_md"), t.get("ci95")
    if ci is None:  # scene-bootstrap CI lives in the one-time final-test report
        try:
            import json
            ci = json.loads((ROOT / "reports" / "lgbm_final_test.json").read_text(encoding="utf-8"))["test_md"]["ci95"]
        except (OSError, KeyError, ValueError):
            ci = None
    ci_txt = f", 95 % интервал по сценам {ci[0]:.3f}–{ci[1]:.3f}" if isinstance(ci, (list, tuple)) and len(ci) == 2 else ""
    num = f": F1 Marine Debris **{f1:.3f}**{ci_txt}" if isinstance(f1, (int, float)) else ""
    return f"Test MARIDA посчитан один раз на итоговой модели{num}; порог взят с val, после этого модель не менялась."


def derived(fn: dict) -> dict:
    l3, l4 = fn.get("l3_lgbm") or {}, fn.get("l4_unet") or {}
    test_done = bool(l3.get("test"))
    test_txt = _test_sentence(l3["test"]) if test_done else ((fn.get("test") or {}).get("text") or (
        "Test MARIDA будет посчитан один раз на итоговой модели; до этого все решения принимались только по val."))

    # --- quality of the final model (val; test only when it exists)
    rows = ["| Модель | Сплит | F1 Marine Debris | IoU Marine Debris | Precision | Recall | Порог |",
            "|---|---|---|---|---|---|---|"]
    v = l3.get("val") or {}
    name = f"LightGBM, {l3.get('training_data') or 'MARIDA'} (итоговая)"
    rows.append(f"| {name} | val (выбор) | {_f(v, 'f1_md')} | {_f(v, 'iou_md')} | {_f(v, 'precision_md')} | "
                f"{_f(v, 'recall_md')} | {_f(v, 'threshold', 'f2')} |")
    if test_done:
        t = l3["test"]
        rows.append(f"| {name} | test (один раз) | {_f(t, 'f1_md')} | {_f(t, 'iou_md')} | {_f(t, 'precision_md')} | "
                    f"{_f(t, 'recall_md')} | {_f(t, 'threshold', 'f2')} |")
    else:
        rows.append(f"| {name} | test | будет посчитан один раз на итоговой модели | | | | |")
    rows.append("| RF + индексы (статья MARIDA, Table 4) | test | 0.80 | 0.67 | | | |")
    metrics_table = "\n".join(rows)

    # --- model comparison: val mean ± std over seeds and the decision
    mo = (fn.get("models") or {}).get("rows") or {}
    mt = ["| Модель | F1 Marine Debris, val (среднее ± std по seed) | Seed | Решение |", "|---|---|---|---|"]
    for key in ("marida_only", "combined", "unet", "stack"):
        r = mo.get(key)
        if not r or r.get("val_f1_mean") is None:
            continue
        nm = f"**{r.get('name')}**" if key == "combined" else r.get("name")
        mt.append(f"| {nm} | {_pm(r.get('val_f1_mean'), r.get('val_f1_std'))} | {fmt(r.get('n_seeds'), None)} | "
                  f"{fmt(r.get('decision'), None)} |")
    if len(mt) == 2:
        mt.append(f"| {DASH} | | | |")
    models_table = "\n".join(mt)

    # --- regions on the map (generated from the service manifest, never by hand)
    sv = fn.get("service") or {}
    # L40: order as on the site (rankRegions): reliable regions by index, then unreliable ones (latest scene with
    # haze/glint or clouds > 50 %) by index, marked in the column «Последний снимок».
    rr = ["| Район | Тайл | Дат | Последний снимок | Дата индекса | Индекс, ‰ | Пятен | из них уверенных | Площадь пятен, га | Дрейф |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    all_regs = sv.get("regions") or []
    by_id = {r.get("id"): r for r in all_regs}
    if sv.get("ranking_reliable") is not None:
        regs = [by_id[i] for i in (sv.get("ranking_reliable") or []) + (sv.get("ranking_unreliable") or []) if i in by_id]
    else:
        regs = sorted(all_regs, key=lambda r: -(r.get("index_permille") or 0))
    for r in regs:
        rel = r.get("reliable")
        last_s = (f"{fmt(r.get('last_date'), None)} · "
                  f"{'надёжен' if rel else ('**' + fmt(r.get('unreliable_reason'), None) + '**')}") if rel is not None else DASH
        area = r.get("total_debris_area_km2")
        idx = r.get("index_permille")
        idx_s = DASH if idx is None else ("< 0.001" if (0 < idx < 0.001 or (idx == 0 and (r.get("n_detections") or 0) > 0)) else f"{idx:.3f}")
        rr.append(f"| {fmt(r.get('name'), None)} | {fmt(r.get('tile'), None)} | {fmt(r.get('n_dates'), 'int')} | "
                  f"{last_s} | {fmt(r.get('latest_date'), None)} | {idx_s} | {fmt(r.get('n_detections'), 'int')} | "
                  f"{fmt(r.get('n_confirmed_latest'), 'int')} | "
                  f"{DASH if area is None else f'{100 * area:.2f}'} | "
                  f"{'есть' if r.get('n_drift') else 'нет'} |")
    if len(rr) == 2:
        rr.append(f"| {DASH} | | | | | | | | | |")
    regions_table = "\n".join(rr)

    # L40: leaders of the rating in prose — only reliable regions (as the site's rating)
    def _short(nm):
        nm = nm or DASH
        i = nm.find(" (")
        return nm[:i] if i > 0 else nm
    top = [by_id[i] for i in (sv.get("ranking_reliable") or [])[:3] if i in by_id]
    bad = [by_id[i] for i in (sv.get("ranking_unreliable") or []) if i in by_id]
    if top:
        lead = " → ".join(f"{_short(r.get('name'))} {fmt(r.get('index_permille'), 'f3')} ‰" for r in top)
        regions_leaders_short = lead
        regions_leaders = (f"Лидеры рейтинга по надёжным снимкам: {lead}. "
                           f"Надёжных районов {fmt(sv.get('n_reliable'), None)} из {fmt(sv.get('n_regions'), None)}; "
                           f"у {fmt_pl(len(bad), 'остального/остальных/остальных')} последний снимок с дымкой/бликом "
                           f"или облачностью > 50 %: они в конце таблицы, а на сайте — в блоке «ненадёжные снимки»")
        if bad and (bad[0].get("index_permille") or 0) > (top[0].get("index_permille") or 0):
            hi = [r for r in bad if (r.get("index_permille") or 0) > (top[0].get("index_permille") or 0)]
            regions_leaders += (". Индекс выше, чем у лидера, есть среди ненадёжных ("
                                + ", ".join(f"{_short(r.get('name'))} {fmt(r.get('index_permille'), 'f3')} ‰" for r in hi)
                                + "), но он посчитан по снимку с дымкой/бликом или по снимку старше последнего")
        regions_leaders += "."
    else:
        regions_leaders, regions_leaders_short = "", DASH

    live = fn.get("live") or {}
    lr = ["| Район | Дата | Сцена | Облачность вырезки | Доля воды | Модели |", "|---|---|---|---|---|---|"]
    for s in live.get("scenes") or []:
        lr.append(f"| {fmt(s.get('region'), None)} | {fmt(s.get('date'), None)} | {fmt(s.get('scene_id'), None)} | "
                  f"{fmt(s.get('crop_cloud_frac'), 'pct')} % | {fmt(s.get('water_frac'), 'pct')} % | "
                  f"{fmt(s.get('models'), None)} |")
    if len(lr) == 2:
        lr.append(f"| {DASH} | | | | | |")

    noise = l3.get("noise") or {}
    noise_txt = (f"{float(noise['f1_std']):.4f} (по {fmt(noise.get('n_seeds'), None)} seed, среднее "
                 f"{fmt(noise.get('f1_mean'), 'f3')})" if noise.get("f1_std") is not None else DASH)

    # --- honesty of the metric: leave-region-out, scene bootstrap
    au = fn.get("metric_audit") or {}
    lro = au.get("lro") or {}
    if au.get("available") and lro.get("mean_f1") is not None:
        per = ", ".join(f"{LRO_NAMES.get(p.get('region'), p.get('region'))} {fmt(p.get('f1'), 'f3')}" for p in lro.get("per_region") or [])
        lro_txt = (f"Проверка переноса на новый район (leave-region-out, {fmt(lro.get('n_regions'), None)} района MARIDA, "
                   f"3 seed): средний F1 MD {_pm(lro.get('mean_f1'), lro.get('mean_f1_std_seeds'), 3)} против "
                   f"{fmt(lro.get('in_dist_f1'), 'f3')} на официальном val (падение {fmt(lro.get('drop_vs_in_dist'), 'f3')}); "
                   f"по районам: {per}. Тот же протокол для модели только на MARIDA даёт "
                   f"{fmt(lro.get('mean_f1_marida_only'), 'f3')}: MADOS помогает именно переносу. "
                   f"95 % бутстрэп-интервал F1 на val по сценам: {_ci(au.get('scene_bootstrap_ci95'))}; "
                   f"прирост от MADOS в парном бутстрэпе по сценам {fmt(au.get('mados_gain'), 'f3')} "
                   f"(95 % интервал {_ci(au.get('mados_gain_ci95'))}).")
    else:
        lro_txt = DASH

    # --- speed (reports/speed.json via final_numbers speed.rows; human-readable table, never raw keys)
    speed_block = speed_table(fn)

    ui = fn.get("ui_perf") or {}
    parts = []
    if ui.get("load_s") is not None:
        parts.append(f"загрузка карты до {ui['load_s']:.2f} с".replace(".", ","))
    if ui.get("flyto_fps") is not None:
        parts.append(f"перелёт к району {ui['flyto_fps']:.0f} fps")
    if ui.get("globe_fps") is not None:
        parts.append(f"вращение глобуса {ui['globe_fps']:.0f} fps")
    if ui.get("drift_fps") is not None:
        parts.append(f"анимация дрейфа {ui['drift_fps']:.0f} fps")
    if ui.get("bundle_gzip_mb") is not None:
        parts.append(f"бандл JS+CSS {ui['bundle_gzip_mb']:.2f} МБ gzip".replace(".", ","))
    if ui.get("console_errors") is not None:
        parts.append(f"ошибок в консоли {ui['console_errors']}")
    ui_txt = "; ".join(parts) if parts else DASH

    return {"metrics_table": metrics_table, "models_table": models_table, "regions_table": regions_table, "regions_leaders": regions_leaders,
            "regions_leaders_short": regions_leaders_short,
            "live_table": "\n".join(lr), "l3_noise": noise_txt, "test_sentence": test_txt, "lro_text": lro_txt,
            "speed_block": speed_block, "ui_perf_text": ui_txt, "baselines_table": baselines_table(fn),
            "l23_channels_table": l23_channels_table(fn), "threads_table": threads_table(fn),
            "rejected_table": rejected_table(fn), "rehearsal_text": rehearsal_text(fn),
            "robustness_text": robustness_text(fn)}


def render(text: str, ctx: dict, missing: list) -> str:
    roots = set(ctx) | {"l3_lgbm", "l4_unet", "data", "live", "service", "service_demo", "ui_perf", "artifacts", "models",
                         "lgbm_live", "metric_audit", "agreement", "drift", "speed", "test", "derived", "baselines", "l23"}

    def sub(m):
        path, flt, arg = m.group(1), m.group(2), m.group(3)
        if path.split(".")[0] not in roots:
            return m.group(0)
        v = get_path(ctx, path)
        if v is None:
            missing.append(path)
        if flt == "pl":
            return fmt_pl(v, arg)
        return fmt(v, flt)

    return PH.sub(sub, text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="README.md / reports/report.md из шаблонов и final_numbers.json")
    ap.add_argument("--numbers", default=str(ROOT / "reports" / "final_numbers.json"))
    a = ap.parse_args(argv)
    p = Path(a.numbers)
    fn = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    if not fn:
        print(f"[render_docs] {p} not found: run scripts/final_numbers.py first; rendering with dashes")
    ctx = dict(fn)
    ctx["derived"] = derived(fn)
    for src, dst in PAIRS:
        sp, dp = ROOT / src, ROOT / dst
        if not sp.exists():
            print(f"[render_docs] skip: no template {src}")
            continue
        missing: list = []
        out = render(sp.read_text(encoding="utf-8"), ctx, missing)
        dp.write_text(out, encoding="utf-8", newline="\n")
        left = sorted(set(PH.findall(out)))
        print(f"[render_docs] {src} -> {dst}: {len(missing)} empty values"
              + (f" ({', '.join(sorted(set(missing)))})" if missing else "")
              + (f"; unreplaced placeholders: {left}" if left else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
