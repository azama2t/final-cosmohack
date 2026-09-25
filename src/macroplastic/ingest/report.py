"""Human report for an ingested dataset: <out>/report/index.html + summary.json (+ inspect/ details).

Re-uses scripts/tools/inspect_dataset.py (imported, not copied) for headers, per-group pixel statistics,
histograms, mask detection and class balance; adds: file tree summary, format candidates, auto config,
previews (RGB quick-look | mask after mapping, through the generated adapter config) and the
"Сомнения" section (what is unknown and why -- guesses are never presented as facts).
"""
from __future__ import annotations

import collections
import html
import json
import os
import time
from pathlib import Path

import numpy as np

from . import formats

LEVEL_ORDER = {"blocker": 0, "high": 1, "check": 2, "info": 3}
LEVEL_RU = {"blocker": "БЛОКЕР", "high": "важно", "check": "проверить", "info": "к сведению"}
CLASS_COLORS = {0: (40, 40, 40), 1: (230, 40, 40), 7: (40, 110, 220)}


def file_tree(root: Path, max_rows: int = 60) -> list[dict]:
    """Per directory: number of files by extension (directories with the same name template collapsed)."""
    ins = formats.load_inspect()
    agg = collections.defaultdict(lambda: {"n_dirs": set(), "ext": collections.Counter(), "bytes": 0, "example": ""})
    for dp, dn, fn in os.walk(root):
        dn.sort()
        rel = os.path.relpath(dp, root).replace("\\", "/")
        key = ins.dir_template(rel) if rel != "." else "."
        a = agg[key]
        a["n_dirs"].add(rel)
        for f in fn:
            if f == ".ingest_done":
                continue
            p = Path(dp) / f
            a["ext"][p.suffix.lower() or "<none>"] += 1
            a["bytes"] += p.stat().st_size
            if not a["example"]:
                a["example"] = (rel + "/" + f) if rel != "." else f
    rows = [{"folder (template)": k, "n_folders": len(v["n_dirs"]), "files by ext": dict(v["ext"]),
             "MB": round(v["bytes"] / 1e6, 2), "example": v["example"]} for k, v in sorted(agg.items())]
    return rows[:max_rows]


def _stretch(a: np.ndarray) -> np.ndarray:
    v = a[np.isfinite(a)]
    if v.size == 0:
        return np.zeros(a.shape, np.uint8)
    lo, hi = np.percentile(v, [2, 98])
    if hi <= lo:
        hi = lo + 1e-6
    return (np.clip((np.nan_to_num(a, nan=lo) - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8)


def _mask_rgb(m: np.ndarray) -> np.ndarray:
    out = np.zeros(m.shape + (3,), np.uint8)
    rng = np.random.default_rng(3)
    for v in np.unique(m):
        c = CLASS_COLORS.get(int(v), tuple(int(x) for x in rng.integers(60, 230, 3)))
        out[m == v] = c
    return out


def previews(cfg_text: str, out: Path, n: int = 6) -> tuple[list[dict], list[dict]]:
    """Load n samples through the generated config -> PNG pairs + post-conversion sanity stats."""
    import yaml
    from PIL import Image

    from macroplastic.organizer_adapter import list_samples, load_config, load_sample

    raw = yaml.safe_load(cfg_text)
    cfg = load_config(raw)
    rows, stats = [], []
    samples = list_samples(cfg)
    if raw.get("chip_labels"):
        import pandas as pd

        c = raw["chip_labels"]
        df = pd.read_csv(Path(cfg["root"]) / c["csv"])
        lab = {Path(str(i).replace("\\", "/")).stem: str(v) for i, v in zip(df[c["id_col"]], df[c["label_col"]])}
        samples = [s for s in samples if Path(s.image_paths[0]).stem in lab]
        # show both classes
        by = collections.defaultdict(list)
        for s in samples:
            by[lab[Path(s.image_paths[0]).stem]].append(s)
        pick = []
        for k in sorted(by):
            pick += by[k][: max(1, n // max(len(by), 1))]
        samples = pick[:n]
    else:
        lab = {}
        if len(samples) > n:
            idx = np.linspace(0, len(samples) - 1, n).round().astype(int)
            samples = [samples[i] for i in idx]
    (out / "img").mkdir(parents=True, exist_ok=True)
    for k, s in enumerate(samples):
        img, mask, names, grid = load_sample(cfg, s)
        rgbi = [names.index(b) for b in ("B4", "B3", "B2") if b in names]
        if len(rgbi) < 3:
            rgbi = list(range(min(3, img.shape[0]))) + [0] * (3 - min(3, img.shape[0]))
        rgb = np.stack([_stretch(img[i]) for i in rgbi], -1)
        tiles = [rgb]
        chip_lab = lab.get(Path(s.image_paths[0]).stem)
        if mask is not None:
            tiles.append(_mask_rgb(mask))
        h = max(t.shape[0] for t in tiles)
        tiles = [np.pad(t, ((0, h - t.shape[0]), (0, 4), (0, 0)), constant_values=255) for t in tiles]
        pic = np.concatenate(tiles, 1)
        scale = max(1, int(256 / max(pic.shape[0], 1)))
        im = Image.fromarray(pic).resize((pic.shape[1] * scale, pic.shape[0] * scale), Image.NEAREST)
        fn = f"img/preview_{k}.png"
        im.save(out / fn)
        valid = np.isfinite(img).all(0)
        st = {"id": s.id, "shape": list(img.shape), "nan_frac": round(float(1 - valid.mean()), 4)}
        for b in ("B2", "B4", "B8", "B11"):
            if b in names:
                v = img[names.index(b)][valid]
                st[f"{b}_p50"] = round(float(np.median(v)), 4) if v.size else None
        if mask is not None:
            u, c = np.unique(mask, return_counts=True)
            st["mask_values"] = {int(a): int(b) for a, b in zip(u, c)}
            if "B8" in names:
                v = img[names.index("B8")][(mask == 7) & valid]
                st["B8_p50_class7"] = round(float(np.median(v)), 4) if v.size else None
        stats.append(st)
        rows.append({"id": s.id, "png": fn, "image": s.image_paths[0], "mask": s.mask_path or "",
                     "chip_label": chip_lab})
    return rows, stats


def sanity_doubts(stats: list[dict], doubts: list) -> None:
    b8 = [s["B8_p50_class7"] for s in stats if s.get("B8_p50_class7") is not None]
    allb = [s.get("B4_p50") for s in stats if s.get("B4_p50") is not None]
    if b8 and np.median(b8) > 0.08:
        doubts.append(formats.doubt("проверка по фону", f"после конвертации медиана B8 на фоне (наш класс 7) = "
                                                     f"{np.median(b8):.3f} > 0.08: фон — не открытая вода "
                                                     "(суша/облака/мутная вода) ИЛИ неверные масштаб/смещение.",
                                    "для моря: B8 воды ≈ 0.00–0.03", "radiometry", "high"))
    if allb and (np.median(allb) < -0.02 or np.median(allb) > 1.2):
        doubts.append(formats.doubt("проверка масштаба", f"медиана B4 после конвертации {np.median(allb):.3f} — "
                                                      "вне 0..1: масштаб/смещение неверны.",
                                    where="radiometry", level="blocker"))


# --------------------------------------------------------------------------- html
def _t(rows, cols=None):
    ins = formats.load_inspect()
    return ins._t(rows, cols)


def write_html(s: dict, path: Path) -> None:
    css = """body{font-family:Segoe UI,Arial,sans-serif;background:#fcfcfb;color:#0b0b0b;margin:24px;max-width:1400px}
table{border-collapse:collapse;font-size:12px;margin:6px 0 14px}td,th{border:1px solid #ddd;padding:3px 6px;text-align:left;vertical-align:top}
th{background:#f0efec}h2{border-bottom:2px solid #2a78d6;padding-bottom:3px}.muted{color:#52514e}
.d{padding:6px 10px;margin:4px 0;border-left:4px solid #999;background:#f6f6f4}.d.blocker{border-color:#c0392b;background:#fdecea}
.d.high{border-color:#e67e22;background:#fff4e5}.d.check{border-color:#2a78d6}.d.info{border-color:#aaa}
pre{background:#f0efec;padding:8px;overflow:auto;font-size:12px}code{background:#f0efec;padding:1px 4px}
.ok{background:#e9f7ef;padding:6px 10px;border-left:4px solid #27ae60}"""
    e = html.escape
    best = s["candidates"][0] if s["candidates"] else {}
    P = [f"<html><head><meta charset='utf-8'><title>ingest: {e(s['name'])}</title><style>{css}</style></head><body>"]
    P.append(f"<h1>Датасет «{e(s['name'])}»: что внутри</h1>")
    u = s["unpack"]
    P.append(f"<p class='muted'>источник: {e(s['source'])} &middot; {u['kind']} &middot; {u['n_files']} файлов, "
             f"{u['n_bytes'] / 1e6:.1f} МБ &middot; корень данных: {e(u['root'])} &middot; {s['generated']} &middot; "
             f"{s['seconds']} с</p>")
    for n in u.get("notes", []):
        P.append(f"<p class='muted'>{e(n)}</p>")
    if u.get("nested"):
        P.append("<p>Вложенные архивы распакованы: " + e(json.dumps(u["nested"], ensure_ascii=False)) + "</p>")
    if u.get("skipped"):
        P.append("<div class='d blocker'>Вложенные архивы НЕ распакованы: " + e(json.dumps(u["skipped"],
                                                                                         ensure_ascii=False)) + "</div>")
    if s.get("docs"):
        P.append("<h2>Документация организаторов (README и т.п.) — ПРОЧИТАТЬ ПЕРВЫМ</h2>")
        for d in s["docs"]:
            P.append(f"<h3>{e(d['rel'])} <span class='muted'>({d['n_lines']} строк, показаны первые 60)</span></h3>"
                     f"<pre>{e(d['text'])}</pre>")
        h = s.get("hints") or {}
        P.append("<p><b>Извлечено из текста:</b> классы " + e(json.dumps(h.get("classes", {}), ensure_ascii=False))
                 + f"; целевой класс: <b>{e(str(h.get('target')))}</b>; каналы: {e(str(h.get('bands')))}; "
                 f"масштаб: {e(str(h.get('scale')))}; метрика: {e(str(h.get('metric_line')))}</p>")
    else:
        P.append("<div class='d high'>README/описания в архиве нет.</div>")
    if s.get("overrides"):
        P.append("<p><b>Флаги командной строки (приоритет над README и угадыванием):</b> "
                 + e(json.dumps(s["overrides"], ensure_ascii=False)) + "</p>")
    mv = (s.get("config_info") or {}).get("mask_values")
    if mv:
        P.append(f"<p><b>Значения масок по всем маскам:</b> проверено {mv['n_checked']} из {mv['n_total']}; пиксели "
                 + e(json.dumps(mv["values"])) + "; файлов со значением " + e(json.dumps(mv["files_with"])) + "</p>")
    P.append("<h2>Итог</h2>")
    if best:
        P.append(f"<div class='ok'><b>Вероятный формат: {e(best['format'])}</b> (оценка {best['score']}) — "
                 f"{e(best['evidence'])}.<br>Конфиг адаптера: <code>{e(s['config_path'])}</code> — проверить строки "
                 "«ПРОВЕРЬ ЭТО» и раздел «Сомнения», затем:<br><code>" + e(s["next_command"]) + "</code></div>")
    nb = sum(d["level"] == "blocker" for d in s["doubts"])
    P.append(f"<h2>Сомнения ({len(s['doubts'])}, блокеров {nb})</h2>")
    P.append("<p class='muted'>Что неизвестно и почему. Всё ниже — допущения, принятые в автоконфиге, а не факты. "
             "«где» — ключ YAML, который править.</p>")
    for d in sorted(s["doubts"], key=lambda d: LEVEL_ORDER.get(d["level"], 9)):
        P.append(f"<div class='d {e(d['level'])}'><b>[{LEVEL_RU.get(d['level'], d['level'])}] {e(d['topic'])}</b>: "
                 f"{e(d['text'])}" + (f"<br><i>варианты:</i> {e(d['options'])}" if d.get("options") else "")
                 + (f"<br><i>где:</i> <code>{e(d['where'])}</code>" if d.get("where") else "") + "</div>")
    P.append("<h2>Кандидаты форматов</h2>")
    P.append(_t([{k: c.get(k) for k in ("format", "score", "evidence")} for c in s["candidates"]]))
    P.append("<h2>Превью (через автоконфиг): RGB (B4,B3,B2, растяжка 2–98 %) | маска в нашей схеме</h2>")
    P.append("<p class='muted'>цвета маски: красный = 1 (мусор/цель), синий = 7 (фон/вода), тёмный = 0 "
             "(игнор/нет данных), прочее — случайные цвета.</p>")
    if s.get("preview_error"):
        P.append(f"<div class='d blocker'>превью не построено: {e(s['preview_error'])}</div>")
    for r in s.get("previews", []):
        lab = f" &middot; метка чипа: <b>{e(str(r['chip_label']))}</b>" if r.get("chip_label") is not None else ""
        P.append(f"<div style='display:inline-block;margin:6px;vertical-align:top'><img src='{r['png']}'><br>"
                 f"<span class='muted' style='font-size:11px'>{e(r['id'])}{lab}</span></div>")
    if s.get("preview_stats"):
        P.append("<h3>Значения после конвертации (медианы по валидным пикселям)</h3>" + _t(s["preview_stats"]))
    P.append("<h2>Дерево файлов (папки с одинаковым шаблоном имени свёрнуты)</h2>" + _t(s["tree"]))
    ins = s["inspect"]
    P.append(f"<h2>Растры: группы по шаблону имени ({ins['n_rasters']} файлов)</h2>")
    P.append(_t([{k: g.get(k) for k in ("group", "n_files", "count", "dtype", "nodata", "crs", "res", "size_hw",
                                         "descriptions", "mask_like", "mask_reason", "example")}
                 for g in ins["groups"]]))
    for g in ins["groups"]:
        P.append(f"<h3>{e(g['group'])}</h3><p class='muted'>{g['n_files']} файлов, статистика по {g['n_sampled']}; "
                 f"имена каналов: {e(str(g['descriptions']))}; охват WGS84: {g.get('extent_wgs84')}</p>")
        for h in g.get("hints", []):
            P.append(f"<div class='d check'>подсказка inspect: {e(h)}</div>")
        if g.get("class_balance"):
            P.append("<b>Маска: уникальные значения и баланс</b>" + _t(g["class_balance"]))
            if g.get("classes_png"):
                P.append(f"<img src='inspect/{g['classes_png']}'>")
        else:
            P.append(_t(g["band_stats"], ["band", "name", "min", "p1", "p5", "p50", "p95", "p99", "max", "mean",
                                          "nan_frac", "zero_frac", "nodata_frac"]))
            if g.get("hist_png"):
                P.append(f"<img src='inspect/{g['hist_png']}' style='max-width:100%'>")
    P.append("<h2>Таблицы CSV/JSON/списки</h2>")
    for t in ins["tables"]:
        P.append(f"<h3>{e(t['rel'])}</h3>")
        if t.get("columns"):
            P.append(f"<p>{t.get('rows')} строк</p>" + _t(t["columns"], ["col", "dtype", "missing_frac", "nunique",
                                                                         "min", "max", "top"]))
            if t.get("head"):
                P.append("<b>первые строки</b>" + _t([dict(zip(t["cols"], r)) for r in t["head"]]))
        else:
            P.append("<pre>" + e(json.dumps({k: v for k, v in t.items() if k != "path"}, indent=1,
                                            ensure_ascii=False, default=str)[:3000]) + "</pre>")
    P.append("<h2>Автоконфиг адаптера</h2><pre>" + e(s["config_text"]) + "</pre>")
    P.append("<p>Подробный отчёт inspect_dataset: <a href='inspect/index.html'>inspect/index.html</a>, "
             "пары снимок–маска: <code>inspect/pairs_guess.csv</code>.</p></body></html>")
    path.write_text("\n".join(P), encoding="utf-8")


def build(data_root: Path, out: Path, name: str, source: str, unpack: dict, force_format: str | None = None,
          sample_per_group: int = 40, max_files: int = 20000, config_path: Path | None = None,
          next_command: str = "", overrides: dict | None = None) -> dict:
    """Run inspect -> detect -> auto config -> previews -> report. Returns summary dict (also JSON on disk)."""
    t0 = time.time()
    rep = out / "report"
    rep.mkdir(parents=True, exist_ok=True)
    ins_mod = formats.load_inspect()
    ins = ins_mod.run(data_root, rep / "inspect", max_files=max_files, sample_per_group=sample_per_group)
    det = formats.detect(ins, rep / "inspect", data_root)
    doubts: list = []
    ov = {k: v for k, v in (overrides or {}).items() if v is not None}
    docs = formats.find_docs(data_root)
    img_counts = [int(k) for g in ins["groups"] if not g.get("mask_like") for k in g["count"] if k not in ("None",)]
    hints = formats.parse_hints(docs, max(img_counts) if img_counts else None)
    if not docs:
        doubts.append(formats.doubt("README", "в архиве нет README/*.txt/*.md с описанием — каналы, классы и метрику "
                                              "взять из ТЗ/чата организаторов и задать флагами --channels, "
                                              "--target-class, --scale.", level="high"))
    cands = det["candidates"]
    if force_format:
        forced = [c for c in cands if c["format"] == force_format]
        if not forced:
            raise SystemExit(f"--format {force_format}: not among candidates {[c['format'] for c in cands]}")
        cands = forced + [c for c in cands if c["format"] != force_format]
    if len(cands) > 1 and cands[0]["format"] != "images_only" and cands[1]["score"] > cands[0]["score"] - 0.1:
        doubts.append(formats.doubt("формат", f"два близких кандидата: {cands[0]['format']} ({cands[0]['score']}) и "
                                              f"{cands[1]['format']} ({cands[1]['score']}) — выбран первый.",
                                    f"--format {cands[1]['format']}", "командная строка", "high"))
    best = cands[0]
    if best["format"] == "images_only":
        doubts.append(formats.doubt("разметка", "не найдено ни масок, ни CSV с метками — обучение невозможно; "
                                                "похоже на тестовую часть.", level="blocker"))
    if det["n_masks"] and best["format"] in ("suffix", "dirs", "ids"):
        n_unp = det["n_images"] - len(best["pairs"])
        if n_unp > 0:
            doubts.append(formats.doubt("снимки без масок", f"{n_unp} из {det['n_images']} растров-«снимков» не "
                                                            "нашли маску (другие группы файлов или тест) — при "
                                                            "конвертации пропускаются (require_mask).",
                                        where="layout", level="info"))
        n_mu = det["n_masks"] - len(best["pairs"])
        if n_mu > 0:
            doubts.append(formats.doubt("маски без снимков", f"{n_mu} из {det['n_masks']} масок без пары.",
                                        where="layout", level="check"))
    splits_note = None
    try:
        cfg_text, info = formats.build_config(det, best, data_root, name, doubts, ov, hints)
    except Exception as e:  # never lose the report because of a config problem
        cfg_text, info = f"# config generation failed: {type(e).__name__}: {e}\n", {"error": str(e)}
        doubts.append(formats.doubt("автоконфиг", f"не удалось построить конфиг: {e}", level="blocker"))
    if not info.get("splits") and best["format"] != "images_only" and "error" not in info:
        splits_note = formats.doubt("сплит", "официального train/val по папкам нет — при обучении будет групповой "
                                             "сплит (сцена по имени, иначе по файлу). Если организаторы дадут "
                                             "список/колонку сплита — указать.",
                                    where="layout.split_regex / train --group-regex", level="check")
        doubts.append(splits_note)
    cfg_path = config_path or (out / "adapter.yaml")
    prev, pstats, perr = [], [], None
    if "error" not in info:
        try:
            prev, pstats = previews(cfg_text, rep)
            sanity_doubts(pstats, doubts)
        except Exception as e:
            perr = f"{type(e).__name__}: {e}"
            doubts.append(formats.doubt("конфиг не читается", f"адаптер не смог прочитать образцы с автоконфигом: "
                                                               f"{perr}", where="adapter.yaml", level="blocker"))
    blockers = [f"{d['topic']}: {d['text']}" for d in doubts if d["level"] == "blocker"]
    if blockers and "error" not in info:
        cfg_text += ("# --convert откажется работать, пока этот список не пуст: исправьте конфиг (или перезапустите\n"
                     "# ingest с --target-class/--channels/--scale) и удалите строки ниже.\n"
                     "ingest_blockers:\n" + "".join(f"  - {formats._q(b[:300])}\n" for b in blockers))
    summary = {
        "name": name, "docs": docs, "hints": hints, "overrides": ov, "source": source, "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "unpack": unpack, "data_root": str(data_root), "config_path": str(cfg_path), "config_text": cfg_text,
        "config_info": info, "candidates": [{k: v for k, v in c.items() if k not in ("pairs",)} | {
            "n_pairs": len(c.get("pairs", []))} for c in cands],
        "doubts": doubts, "previews": prev, "preview_stats": pstats, "preview_error": perr,
        "tree": file_tree(data_root), "n_images": det["n_images"], "n_masks": det["n_masks"],
        "chip_csv": det["chip_csv"],
        "inspect": {k: ins[k] for k in ("n_rasters", "n_tables", "groups", "tables", "families", "seconds")},
        "next_command": next_command,
    }
    summary["seconds"] = round(time.time() - t0, 1)
    (rep / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=str),
                                      encoding="utf-8")
    write_html(summary, rep / "index.html")
    return summary
