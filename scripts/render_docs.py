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


def derived(fn: dict) -> dict:
    l3, l4 = fn.get("l3_lgbm") or {}, fn.get("l4_unet") or {}
    rows = ["| Модель | Сплит | F1 Marine Debris | IoU Marine Debris | Precision | Recall | Порог |",
            "|---|---|---|---|---|---|---|"]
    for name, blk in (("LightGBM (наша)", l3), ("UNet (сравнение)", l4)):
        if name.startswith("UNet") and not blk.get("available"):
            continue
        for split, label in (("val", "val (выбор)"), ("test", "test (один раз)")):
            m = blk.get(split) or {}
            rows.append(f"| {name} | {label} | {_f(m, 'f1_md')} | {_f(m, 'iou_md')} | {_f(m, 'precision_md')} | "
                        f"{_f(m, 'recall_md')} | {_f(m, 'threshold', 'f2')} |")
    rows.append("| RF + индексы (статья MARIDA, Table 4) | test | 0.80 | 0.67 | | | |")
    metrics_table = "\n".join(rows)

    sv = fn.get("service") or {}
    rr = ["| Регион | Тайл | Даты | Модель | Индекс, ‰ | Находок | Площадь пятен, км² | Зон |",
          "|---|---|---|---|---|---|---|---|"]
    for r in sv.get("regions") or []:
        rr.append(f"| {fmt(r.get('name'), None)} | {fmt(r.get('tile'), None)} | {fmt(r.get('dates'), None)} | "
                  f"{fmt(r.get('model'), None)} | {fmt(r.get('index_permille'), 'f2')} | "
                  f"{fmt(r.get('n_detections'), 'int')} | {fmt(r.get('total_debris_area_km2'), 'km2')} | "
                  f"{fmt(r.get('n_zones'), 'int')} |")
    if len(rr) == 2:
        rr.append(f"| {DASH} | | | | | | | |")
    regions_table = "\n".join(rr)

    live = fn.get("live") or {}
    lr = ["| Регион | Дата | Сцена | Облачность вырезки | Доля воды | Модели |", "|---|---|---|---|---|---|"]
    for s in live.get("scenes") or []:
        lr.append(f"| {fmt(s.get('region'), None)} | {fmt(s.get('date'), None)} | {fmt(s.get('scene_id'), None)} | "
                  f"{fmt(s.get('crop_cloud_frac'), 'pct')} % | {fmt(s.get('water_frac'), 'pct')} % | "
                  f"{fmt(s.get('models'), None)} |")
    if len(lr) == 2:
        lr.append(f"| {DASH} | | | | | |")
    noise = l3.get("noise") or {}
    noise_txt = (f"{fmt(noise.get('f1_std'), 'f3')} (по {fmt(noise.get('n_seeds'), None)} seed)"
                 if noise.get("f1_std") is not None else DASH)
    return {"metrics_table": metrics_table, "regions_table": regions_table, "live_table": "\n".join(lr),
            "l3_noise": noise_txt}


def render(text: str, ctx: dict, missing: list) -> str:
    roots = set(ctx)

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
