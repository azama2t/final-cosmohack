r"""Графики и снимок чисел сервиса для научного отчёта (reports/report_final.md → reports/report.pdf).

    .venv\Scripts\python.exe scripts\case\report_figs.py            # снимок сервиса + все графики
    .venv\Scripts\python.exe scripts\case\report_figs.py --figs     # только графики (снимок не трогать)

1. Снимок чисел карточки и сервиса (без сети, без запуска сервиса: те же функции service/case_store.py, что отдают
   /api/v3/scene_zones, /api/v3/scene_zones/regions, /api/v3/defense_examples)
   → reports/report_extra/service_card.json (в git). Его читает scripts/case/collect_search.py (collect_report) →
   reports/final_numbers.json → case.sections.report.
2. Графики → reports/img/report/*.png|jpg (≤ 300 КБ). Числа графиков — только из reports/final_numbers.json и файлов,
   указанных в подписи каждого графика (все в git). CPU, matplotlib.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FN = ROOT / "reports" / "final_numbers.json"
SNAP = ROOT / "reports" / "report_extra" / "service_card.json"
IMG = ROOT / "reports" / "img" / "report"
MAX_BYTES = 300_000


# ------------------------------------------------------------------------------------------------ snapshot
def snapshot() -> dict:
    """Числа карточки зоны и сервиса из service/case_store.py (офлайн)."""
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
    sys.path.insert(0, str(ROOT))
    from service import case_store as cs  # noqa: PLC0415

    P = [f.get("properties", f) for f in cs.scene_zones_all()]
    finds = [p for p in P if p.get("is_find")]
    cfg = cs._s51_cfg()  # noqa: SLF001
    alerts = {lv: sum(1 for p in finds if p.get("alert_level") == lv) for lv in ("слабый", "средний", "высокий")}
    shore = sorted(p["shore_km"] for p in finds if p.get("shore_km") is not None)
    strand = sorted(p["stranded_pct_72h"] for p in finds if p.get("stranded_pct_72h") is not None)
    stats = cs.sz_scene_stats()
    demo_key = "demo-cozar-2021-03-11"
    regions = cs.sz_regions()
    dyn_rows = {}
    for r in regions:
        d = cs.region_dynamics(r["id"])
        if d:
            dyn_rows[r["id"]] = {"label": r.get("short") or r.get("label"), "n_dates": d.get("count"),
                                 "rows": [{"date": x.get("date"), "evaluable": x.get("evaluable"), "n_finds": x.get("n_finds"),
                                           "n_large": x.get("n_large"), "total_find_area_km2": x.get("total_find_area_km2"),
                                           "coverage_pct": x.get("coverage_pct"), "field_items_km2": x.get("field_items_km2")}
                                          for x in d.get("rows") or []]}
    multi = [k for k, v in dyn_rows.items() if sum(1 for x in v["rows"] if x["evaluable"]) >= 2]
    best = max(multi, key=lambda k: sum(1 for x in dyn_rows[k]["rows"] if x["evaluable"])) if multi else None
    tot_area = sum((r.get("integral") or {}).get("total_find_area_km2") or 0 for r in regions)
    fe_units = sorted({(p.get("field_estimate") or {}).get("unit") for p in finds if p.get("field_estimate")} - {None})
    n_fe = sum(1 for p in finds if (p.get("field_estimate") or {}).get("value") is not None)
    qbi = sorted({str(p.get("quantity_by_image")) for p in finds})
    de = cs.defense_examples()
    ex = [{"kind": e.get("kind"), "label": e.get("label"), "title": e.get("title"), "verdict": e.get("verdict"),
           "reference": e.get("reference"), "basis": e.get("basis"), "zone_id": e.get("zone_id"), "scene_key": e.get("scene_key"),
           "crop": ((e.get("image") or {}).get("crop_url") or (e.get("image") or {}).get("rgb_url"))}
          for e in de.get("examples") or []]
    demo = stats.get(demo_key) or {}
    out = {
        "source": "service/case_store.py: scene_zones_all, sz_scene_stats, sz_regions, region_dynamics, defense_examples "
                  "(те же функции, что у /api/v3/scene_zones*, /api/v3/defense_examples); пороги — configs/zone_estimate.yaml",
        "command": r".venv\Scripts\python.exe scripts\case\report_figs.py",
        "n_zones": len(P), "n_finds": len(finds),
        "large_km2": cfg.get("large_km2"), "large_axis_m": cfg.get("large_axis_m"),
        "n_large_finds": sum(1 for p in finds if p.get("is_large")),
        "alerts": alerts,
        "shore_n": len(shore), "shore_km_min": round(shore[0], 1) if shore else None,
        "shore_km_median": round(statistics.median(shore), 1) if shore else None, "shore_km_max": round(shore[-1], 1) if shore else None,
        "n_finds_with_drift": len(strand),
        "stranded_pct_median": round(statistics.median(strand), 1) if strand else None,
        "stranded_pct_max": round(strand[-1], 1) if strand else None,
        "n_finds_stranded_pos": sum(1 for v in strand if v > 0),
        "n_organic_finds": sum(1 for p in finds if p.get("likely_organic")),
        "n_field_estimate": n_fe, "field_estimate_units": fe_units, "quantity_by_image_values": qbi,
        "demo": {"key": demo_key, **{k: demo.get(k) for k in ("n_finds", "n_large", "large_area_km2", "total_find_area_km2",
                                                               "find_mask_area_km2", "valid_water_km2", "coverage_pct")}},
        "n_regions": len(regions), "n_regions_multi_date": len(multi), "total_find_area_km2_all": round(tot_area, 2),
        "dynamics_region": best, "dynamics": dyn_rows.get(best) if best else None,
        "defense_examples": ex, "n_defense_kinds": len(ex),
    }
    SNAP.parent.mkdir(parents=True, exist_ok=True)
    SNAP.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(f"[report_figs] {SNAP.relative_to(ROOT)}: зон {out['n_zones']}, находок {out['n_finds']}, крупных {out['n_large_finds']}, "
          f"алерты {alerts}, с дрейфом {out['n_finds_with_drift']}, примеров {len(ex)}")
    return out


# ------------------------------------------------------------------------------------------------ figures
def _plt():
    import matplotlib  # noqa: PLC0415
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.titlesize": 10, "axes.titleweight": "bold", "figure.dpi": 110})
    return plt


def _save(fig, name: str) -> None:
    IMG.mkdir(parents=True, exist_ok=True)
    p = IMG / name
    fig.savefig(p, dpi=130, bbox_inches="tight")
    import matplotlib.pyplot as plt  # noqa: PLC0415
    plt.close(fig)
    if p.stat().st_size > MAX_BYTES:  # подстраховка: пережать в палитру
        from PIL import Image  # noqa: PLC0415
        Image.open(p).convert("RGB").quantize(colors=128).save(p, optimize=True)
    print(f"[report_figs] {p.relative_to(ROOT)} {p.stat().st_size // 1024} КБ")


C_MAIN, C_BASE, C_GREY, C_BAD = "#1f6f8b", "#e07b39", "#9aa5ad", "#b33a3a"


def fig_exclusions(fn: dict) -> None:
    plt = _plt()
    s = fn["case"]["selection"]["S2"]
    items = [("объектная строка (не плотность)", s["rej_item_observation"]), ("категория пластика (не сумма)", s["rej_plastic_category"]),
             ("другой профиль / метод", s["rej_other_source"]), ("весь мусор (all_litter)", s["rej_all_litter"]),
             ("промысловый мусор", s["rej_fisheries"]), ("аэросъёмка > 50 см", s["rej_aerial"]),
             ("принято в профиль S2", s["accepted_rows"])]
    fig, ax = plt.subplots(figsize=(7.2, 2.8))
    ys = range(len(items))[::-1]
    for y, (lab, v) in zip(ys, items):
        ax.barh(y, v, color=C_MAIN if "принято" in lab else C_GREY)
        ax.text(v + 5, y, str(v), va="center")
    ax.set_yticks(list(ys), [x[0] for x in items])
    ax.set_xlabel("строк реестра организаторов")
    ax.set_title(f"Отбор строк: {fn['case']['csv']['rows']} строк → {s['accepted_rows']} событий профиля S2 (у каждого отказа — причина)")
    _save(fig, "fig01_selection.png")


def fig_detector(fn: dict) -> None:
    plt = _plt()
    t = fn["case"]["detector"]["test"]
    u = ((fn["case"]["sections"].get("baselines") or {}).get("test") or {}).get("unet_argmax") or {}
    rows = [("LightGBM (наш)", t["lgbm"]), ("RandomForest, протокол MARIDA", t["rf_argmax"]),
            ("U-Net MARIDA, веса авторов", {"f1": u.get("f1"), "ci95_f1": u.get("ci95_f1") or u.get("ci95")}),
            ("окно FDI × NDVI", t["fdi_ndvi_box"])]
    fig, ax = plt.subplots(figsize=(7.2, 2.4))
    for i, (lab, r) in enumerate(rows):
        f1, ci = r.get("f1"), r.get("ci95_f1") or [None, None]
        ax.barh(len(rows) - 1 - i, f1, color=C_MAIN if i == 0 else C_GREY)
        if ci and ci[0] is not None:
            ax.errorbar(f1, len(rows) - 1 - i, xerr=[[f1 - ci[0]], [ci[1] - f1]], color="k", capsize=3, lw=1)
        small = f1 < 0.1
        xt = ((ci[1] if ci and ci[1] is not None else f1) + 0.015) if small else f1 / 2
        ax.text(xt, len(rows) - 1 - i, f"{f1:.3f}", va="center", ha="left" if small else "center",
                color="k" if small else "w", fontweight="bold")
    ax.set_yticks(range(len(rows))[::-1], [r[0] for r in rows])
    ax.set_xlim(0, 1)
    ax.set_xlabel("F1 Marine Debris, отложенный test MARIDA (95 % ДИ — бутстреп по сценам)")
    ax.set_title(f"Детектор против бейзлайнов на одной выборке: {fn['case']['detector']['test_n_scenes']} сцен, test посчитан один раз")
    _save(fig, "fig02_detector_test.png")


def fig_organic(fn: dict) -> None:
    plt = _plt()
    org = (fn["case"]["sections"].get("report") or {}).get("organic") or {}
    cl = org.get("classes") or []
    if not cl:
        return
    fig, ax = plt.subplots(figsize=(7.2, 2.6))
    x = range(len(cl))
    w = 0.38
    ax.bar([i - w / 2 for i in x], [100 * c["det_md_share"] for c in cl], w, color=C_BAD, label="детектор назвал «мусором», % пикселей")
    ax.bar([i + w / 2 for i in x], [100 * c["flag_px_share"] for c in cl], w, color="#3a8f3a", label="флаг NDVI ≥ 0,20 и FAI > 0, % пикселей")
    for i, c in enumerate(cl):
        ax.text(i - w / 2, 100 * c["det_md_share"] + 2, f"{100 * c['det_md_share']:.1f}", ha="center", fontsize=8)
        ax.text(i + w / 2, 100 * c["flag_px_share"] + 2, f"{100 * c['flag_px_share']:.1f}", ha="center", fontsize=8)
    ax.set_xticks(list(x), [f"{c['name']}\n(n = {c['pixels']} пикс.)" for c in cl])
    ax.set_ylim(0, 128)
    ax.set_ylabel("%")
    ax.legend(loc="upper center", ncol=2, frameon=False, fontsize=8)
    ax.set_title("Органика: детектор бинарный; флаг «вероятно органика» — эксперимент на MARIDA val")
    _save(fig, "fig03_organic.png")


def fig_field(fn: dict) -> None:
    plt = _plt()
    ft = fn["case"]["sections"]["field_test"]
    q = fn["case"]["sections"]["quantity"]["field_S2"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.4, 2.7), gridspec_kw={"width_ratios": [1.1, 1]})
    labs, mm, md = [], [], []
    for k, name in (("S2", "S2 пластик > 2 см"), ("S1", "S1 трал 5–50 см")):
        t = ft[k]
        labs.append(f"{name}\n(n = {t['n_test']})")
        mm.append(t["main_mae"] / t["median_mae"])
        md.append(1.0)
    x = range(len(labs))
    a1.bar([i - 0.2 for i in x], md, 0.38, color=C_BASE, label="медиана профиля (на карте)")
    a1.bar([i + 0.2 for i in x], mm, 0.38, color=C_MAIN, label="основная модель")
    for i, k in enumerate(("S2", "S1")):
        a1.text(i - 0.2, 1.02, f"{ft[k]['median_mae']:.1f}", ha="center", fontsize=8)
        a1.text(i + 0.2, mm[i] + 0.02, f"{ft[k]['main_mae']:.1f}", ha="center", fontsize=8)
    a1.set_xticks(list(x), labs)
    a1.set_ylabel("MAE / MAE медианы")
    a1.set_ylim(0, 1.75)
    a1.legend(frameon=False, fontsize=7, loc="upper center", ncol=2)
    a1.set_title("Отложенный test (шт./км²)")
    iv = [("Пуассон (Гарвуд)", q["lo95"], q["hi95"]), ("кластерный бутстреп\nпо дням рейса", q["boot_lo95"], q["boot_hi95"]),
          ("одно новое место", q["event_lo95"], q["event_hi95"])]
    for i, (lab, lo, hi) in enumerate(iv):
        a2.plot([lo, hi], [2 - i, 2 - i], lw=6, color=[C_MAIN, C_BASE, C_GREY][i], solid_capstyle="butt")
        a2.text(hi * 1.08, 2 - i, f"{lo:.1f}–{hi:.1f}", va="center", fontsize=8)
    a2.axvline(q["pooled_C"], color="k", lw=1, ls="--")
    a2.text(q["pooled_C"], 2.45, f"ΣN/ΣA = {q['pooled_C']:.1f}", ha="center", fontsize=8)
    a2.set_xscale("log")
    a2.set_xlim(5, 600)
    a2.set_ylim(-0.5, 2.7)
    a2.set_yticks([2, 1, 0], [x[0] for x in iv])
    a2.set_xlabel("шт./км², 95 %")
    a2.set_title("S2: три интервала")
    fig.tight_layout()
    _save(fig, "fig04_field.png")


def fig_resolution() -> None:
    plt = _plt()
    d = json.loads((ROOT / "docs/research/marine_quantity/results/resolution_physics.json").read_text(encoding="utf-8"))
    t = d["table"]
    g = [r["gsd_m"] for r in t]
    fig, ax = plt.subplots(figsize=(7.2, 2.9))
    ax.plot(g, [100 * r["share_countable_2x2px"] for r in t], "o-", color=C_MAIN, label="предмет ≥ 2×2 пикс. (можно считать штуки)")
    ax.plot(g, [100 * r["share_ge_1px"] for r in t], "s-", color=C_BASE, label="предмет ≥ 1 пикс.")
    ax.plot(g, [100 * r["share_fill_ge_20pct"] for r in t], "^-", color=C_GREY, label="предмет заполняет ≥ 20 % пикселя")
    ax.axvline(10, color=C_BAD, ls="--", lw=1)
    ax.text(10.5, 60, "Sentinel-2\n10 м", color=C_BAD, fontsize=8)
    ax.set_xscale("log")
    ax.set_xlabel("размер пикселя (GSD), м")
    ax.set_ylabel(f"% из {d['size_distribution']['n_objects_total']:,} объектов ADIS".replace(",", " "))
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    ax.set_title("Физический предел: какая доля реальных предметов (ADIS, > 5 см) различима при данном пикселе")
    _save(fig, "fig05_resolution.png")


def fig_drift48(fn: dict) -> None:
    plt = _plt()
    d48 = (fn["case"]["sections"].get("report") or {}).get("drift48") or {}
    rows = d48.get("pairs") or []
    if not rows:
        return
    fig, ax = plt.subplots(figsize=(7.2, 2.7))
    for i, r in enumerate(rows):
        ax.plot([r["before_m"], r["after_m"]], [i, i], color=C_GREY, lw=1)
        ax.plot(r["before_m"], i, "o", color=C_BASE)
        ax.plot(r["after_m"], i, "o", color=C_MAIN)
    ax.plot([], [], "o", color=C_BASE, label="без дрейфа")
    ax.plot([], [], "o", color=C_MAIN, label="после сдвига дрейфом (лучшая гипотеза пояса)")
    ax.axvline(d48.get("threshold_m", 50), color=C_BAD, ls="--", lw=1)
    ax.text(d48.get("threshold_m", 50) * 1.05, len(rows) - 0.6, f"порог {d48.get('threshold_m', 50)} м", color=C_BAD, fontsize=8)
    ax.set_yticks(range(len(rows)), [f"ISPRA №{r['id']}" for r in rows])
    ax.set_xscale("log")
    ax.set_xlabel("расстояние «предмет полевого учёта → ближайший пиксель детектора», м")
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    ax.set_title(f"Пары ±48 ч со сдвигом дрейфом: порог прошли {d48.get('n_pass')} из {d48.get('n_pairs')}")
    _save(fig, "fig06_drift48.png")


def fig_dynamics(fn: dict) -> None:
    plt = _plt()
    sv = (fn["case"]["sections"].get("report") or {}).get("service") or {}
    dy = sv.get("dynamics") or {}
    rows = [r for r in dy.get("rows") or [] if r.get("evaluable")]
    if not rows:
        return
    fig, ax = plt.subplots(figsize=(7.2, 2.5))
    x = range(len(rows))
    ax.bar(x, [r["n_finds"] for r in rows], color=C_MAIN, label="находок")
    ax.set_ylabel("находок на снимке")
    ax.set_ylim(0, max(r["n_finds"] for r in rows) * 1.35)
    ax2 = ax.twinx()
    ax2.plot(x, [r["total_find_area_km2"] for r in rows], "o-", color=C_BASE, label="суммарная площадь контуров, км²")
    ax2.set_ylabel("км² (площадь, не число предметов)")
    ax2.set_ylim(0, max(r["total_find_area_km2"] for r in rows) * 1.35)
    ax.set_xticks(list(x), [r["date"] for r in rows], rotation=30, ha="right", fontsize=8)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, frameon=False, fontsize=8, loc="upper left")
    ax.set_title(f"Панель «Динамика»: {dy.get('label')} — {len(rows)} оцениваемых дат")
    _save(fig, "fig07_dynamics.png")


def fig_defense(fn: dict) -> None:
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415
    sv = (fn["case"]["sections"].get("report") or {}).get("service") or {}
    ex = sv.get("defense_examples") or []
    TW, TH = 380, 190
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except OSError:
        font = ImageFont.load_default()
    tiles = []
    for e in ex:
        url = e.get("crop") or ""
        rel = url.replace("/api/v3/scene_zones/scenes/", "")
        key, _, rest = rel.partition("/")
        p = ROOT / "data" / "case" / "scene_zones" / key / rest
        if not p.is_file():
            continue
        im = Image.open(p).convert("RGB")
        im.thumbnail((TW, TH))
        canvas = Image.new("RGB", (TW, TH + 70), "white")
        canvas.paste(im, ((TW - im.width) // 2, (TH - im.height) // 2))
        dr = ImageDraw.Draw(canvas)
        y = TH + 4
        for line in (f"{len(tiles) + 1}. {e.get('label')}", str(e.get('title')), f"вывод: {e.get('verdict')}"):
            while len(line) > 48:
                cut = line[:48].rfind(" ")
                cut = cut if cut > 10 else 48
                dr.text((4, y), line[:cut], fill="black", font=font)
                line, y = line[cut:].strip(), y + 16
            dr.text((4, y), line, fill="black", font=font)
            y += 16
        tiles.append(canvas)
    if not tiles:
        return
    cols = 3
    rows_n = (len(tiles) + cols - 1) // cols
    W = Image.new("RGB", (cols * TW + (cols - 1) * 8, rows_n * (TH + 70) + (rows_n - 1) * 8), "white")
    for i, t in enumerate(tiles):
        W.paste(t, ((i % cols) * (TW + 8), (i // cols) * (TH + 78)))
    IMG.mkdir(parents=True, exist_ok=True)
    p = IMG / "fig08_defense_examples.jpg"
    W.save(p, quality=82, optimize=True)
    print(f"[report_figs] {p.relative_to(ROOT)} {p.stat().st_size // 1024} КБ")


def fig_registry(fn: dict) -> None:
    plt = _plt()
    rl = fn["case"]["sections"].get("research_log") or {}
    bp = rl.get("by_prefix") or {}
    names = {"D": "детектор", "S": "синхронизация", "C": "концентрация (поле)", "E": "снимок ↔ поле", "P": "подготовка",
             "R": "розыск данных", "G": "независимая проверка", "Q": "количество"}
    ks = sorted(bp, key=lambda k: -bp[k])
    fig, ax = plt.subplots(figsize=(7.2, 2.6))
    ys = list(range(len(ks)))[::-1]
    ax.barh(ys, [bp[k] for k in ks], color=C_MAIN)
    for y, k in zip(ys, ks):
        ax.text(bp[k] + 0.3, y, str(bp[k]), va="center", fontsize=8)
    ax.set_yticks(ys, [f"{k} · {names.get(k, k)}" for k in ks], fontsize=8)
    ax.set_xlabel("строк журнала")
    ax.set_title(f"Журнал экспериментов docs/PIPELINE.md: {rl.get('n_experiments')} строк «проверка → число → решение»")
    _save(fig, "fig09_registry.png")


def figs() -> None:
    fn = json.loads(FN.read_text(encoding="utf-8"))
    for f in (fig_exclusions, fig_detector, fig_organic, fig_field, fig_drift48, fig_dynamics, fig_defense, fig_registry):
        try:
            f(fn)
        except Exception as e:  # noqa: BLE001  один график не должен ронять остальные
            print(f"[report_figs] {f.__name__}: {type(e).__name__}: {e}")
    fig_resolution()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="снимок чисел сервиса + графики научного отчёта")
    ap.add_argument("--figs", action="store_true", help="только графики")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if not a.figs:
        snapshot()
    figs()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
