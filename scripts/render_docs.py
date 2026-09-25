r"""Fill README.md and reports/report.md from their templates with numbers from reports/final_numbers.json.

Usage (from repo root):
    .venv\Scripts\python.exe scripts\final_numbers.py      # 1) collect numbers
    .venv\Scripts\python.exe scripts\render_docs.py        # 2) README.md.tmpl -> README.md, reports\report.md.tmpl -> reports\report.md

Template syntax:
    {{l3_lgbm.val.f1_md}}          value by dotted path (list items by index: service.regions.0.name)
    {{l3_lgbm.val.f1_md|f3}}       filters: f1 f2 f3 (fixed decimals), pct (x100, 1 decimal, no % sign),
                                   int (thousands separated), km2, raw
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
PH = re.compile(r"\{\{\s*([A-Za-z_][\w.]*)\s*(?:\|\s*(\w+)\s*)?\}\}")


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


def derived(fn: dict) -> dict:
    l3, l4 = fn.get("l3_lgbm") or {}, fn.get("l4_unet") or {}
    test_done = bool(l3.get("test"))
    test_txt = (fn.get("test") or {}).get("text") or (
        "Test MARIDA посчитан один раз на итоговой модели, после этого модель не менялась." if test_done else
        "Test MARIDA будет посчитан один раз на итоговой модели; до этого все решения принимались только по val.")

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
    rr = ["| Район | Тайл | Дат | Последняя дата | Индекс, ‰ | Пятен | из них уверенных | Площадь пятен, га | Дрейф |",
          "|---|---|---|---|---|---|---|---|---|"]
    regs = sorted(sv.get("regions") or [], key=lambda r: -(r.get("index_permille") or 0))
    for r in regs:
        area = r.get("total_debris_area_km2")
        idx = r.get("index_permille")
        idx_s = DASH if idx is None else ("< 0.001" if (0 < idx < 0.001 or (idx == 0 and (r.get("n_detections") or 0) > 0)) else f"{idx:.3f}")
        rr.append(f"| {fmt(r.get('name'), None)} | {fmt(r.get('tile'), None)} | {fmt(r.get('n_dates'), 'int')} | "
                  f"{fmt(r.get('latest_date'), None)} | {idx_s} | {fmt(r.get('n_detections'), 'int')} | "
                  f"{fmt(r.get('n_confirmed_latest'), 'int')} | "
                  f"{DASH if area is None else f'{100 * area:.2f}'} | "
                  f"{'есть' if r.get('n_drift') else 'нет'} |")
    if len(rr) == 2:
        rr.append(f"| {DASH} | | | | | | | | |")
    regions_table = "\n".join(rr)

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

    # --- speed (reports/speed.json, any layout)
    sp = fn.get("speed") or {}
    if sp.get("available"):
        if sp.get("summary"):
            speed_block = str(sp["summary"])
        else:
            pairs = _flat_numbers(sp.get("raw"))
            speed_block = "\n".join(["| Замер | Значение |", "|---|---|"] + [f"| `{k}` | {fmt(v, None)} |" for k, v in pairs]) \
                if pairs else DASH
    else:
        speed_block = "Замер скорости инференса ещё не записан (`reports/speed.json`)."

    ui = fn.get("ui_perf") or {}
    parts = []
    if ui.get("load_s") is not None:
        parts.append(f"загрузка карты до {ui['load_s']:.2f} с".replace(".", ","))
    if ui.get("flyto_fps") is not None:
        parts.append(f"перелёт к району {ui['flyto_fps']:.0f} fps")
    if ui.get("drift_fps") is not None:
        parts.append(f"анимация дрейфа {ui['drift_fps']:.0f} fps")
    if ui.get("bundle_gzip_mb") is not None:
        parts.append(f"бандл JS+CSS {ui['bundle_gzip_mb']:.2f} МБ gzip".replace(".", ","))
    if ui.get("console_errors") is not None:
        parts.append(f"ошибок в консоли {ui['console_errors']}")
    ui_txt = "; ".join(parts) if parts else DASH

    return {"metrics_table": metrics_table, "models_table": models_table, "regions_table": regions_table,
            "live_table": "\n".join(lr), "l3_noise": noise_txt, "test_sentence": test_txt, "lro_text": lro_txt,
            "speed_block": speed_block, "ui_perf_text": ui_txt}


def render(text: str, ctx: dict, missing: list) -> str:
    roots = set(ctx) | {"l3_lgbm", "l4_unet", "data", "live", "service", "service_demo", "ui_perf", "artifacts", "models",
                         "lgbm_live", "metric_audit", "agreement", "drift", "speed", "test", "derived"}

    def sub(m):
        path, flt = m.group(1), m.group(2)
        if path.split(".")[0] not in roots:
            return m.group(0)
        v = get_path(ctx, path)
        if v is None:
            missing.append(path)
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
