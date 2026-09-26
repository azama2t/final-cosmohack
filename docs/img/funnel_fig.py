"""Воронка данных для деки и docs/PIPELINE.md: CSV организаторов → … → A/C, ветка ADIS, блок B/D.

Запуск:  .venv\\Scripts\\python.exe docs\\img\\funnel_fig.py
Выход:   docs/img/funnel.png, docs/img/funnel.json (числа, из которых нарисовано).

Ни одно число не вписано руками. Источники:
  - реестр пар 25.09: data/pairs/events.csv, candidates.csv, pair_quality.csv (стадии 318 → 68 → 29 → 12 → 0);
  - розыск §11 по событиям CSV: reports/search/{s1,s2,s3,s4}_candidates.csv (колонка level: A/B/C/D);
  - ветка ADIS: всего отрезков — data/extra/field/adis/Segments.csv (если скачан), иначе таблица в
    reports/extra_data/field.md; «со снимком ±1 сут» — строки ADIS в reports/extra_data/field_candidates.csv;
    дальше — reports/search/search_numbers.json → adis_pairs (177, A, оцениваемые, с предметами, срабатывания);
  - блок B/D: reports/search/search_numbers.json → labeled_data (PLP/FloatingObjects, Cózar 2024, суда, облака).
docs/img/funnel.json: ключи stages / quality_reject_reasons читает scripts/case/collect_search.py — не переименовывать.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_PNG = os.path.join(ROOT, "docs", "img", "funnel.png")
OUT_JSON = os.path.join(ROOT, "docs", "img", "funnel.json")
REP = os.path.join(ROOT, "reports")

SOURCES = ["S1_GPGP2018", "S2_SARGASSO_MSM41", "S3_SE_NORTH_SEA", "S4_BLACK_SEA_DOORS3"]
SRC_LABEL = {
    "S1_GPGP2018": "S1 GPGP (пластик)",
    "S2_SARGASSO_MSM41": "S2 Саргассово (пластик)",
    "S3_SE_NORTH_SEA": "S3 Северное море (весь мусор)",
    "S4_BLACK_SEA_DOORS3": "S4 Чёрное море (весь мусор)",
}
# категориальная палитра по умолчанию, слоты 1–4 в фиксированном порядке
SRC_COLOR = {
    "S1_GPGP2018": "#2a78d6",
    "S2_SARGASSO_MSM41": "#eb6834",
    "S3_SE_NORTH_SEA": "#1baf7a",
    "S4_BLACK_SEA_DOORS3": "#eda100",
}
ADIS_COLOR = "#4a3aa7"  # слот 7 (violet) — отдельная совокупность, не источник CSV
B_COLOR, D_COLOR = "#256abf", "#8a8983"
INK, INK2, SURF, RULE = "#0b0b0b", "#52514e", "#fcfcfb", "#d9d8d3"
LEVELS = ["A", "B", "C", "D"]


def _truthy(s: pd.Series) -> pd.Series:
    return s.astype(str).str.lower().isin(["true", "1", "yes"])


def _load_json(p: str) -> dict:
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ данные
def csv_stages() -> tuple[list[str], list[dict[str, int]], dict]:
    ev = pd.read_csv(os.path.join(ROOT, "data", "pairs", "events.csv"))
    cand = pd.read_csv(os.path.join(ROOT, "data", "pairs", "candidates.csv"), low_memory=False)
    pq = pd.read_csv(os.path.join(ROOT, "data", "pairs", "pair_quality.csv"))
    src_of = dict(zip(ev["event_id"], ev["source_id"]))

    def by_src(ids) -> dict[str, int]:
        c = Counter(src_of.get(i, "?") for i in set(ids))
        return {s: int(c.get(s, 0)) for s in SOURCES}

    has_scene = cand[cand["item_id"].notna() & _truthy(cand["point_inside_footprint"])]
    stages = [
        ("События в CSV организаторов", ev["event_id"]),
        ("Есть снимок S2/Landsat в ±5 сут (точка внутри контура сцены)", has_scene["event_id"]),
        ("Отбор по метаданным (|dt| ≤ 1 сут, облачность ≤ 60 %)", cand[_truthy(cand["accept_meta"])]["event_id"]),
        ("Прошли маски качества в полосе (облака, блик, покрытие)", pq[pq["decision"].astype(str) == "accept"]["event_id"]),
        ("Синхронны при типичном дрейфе (0.2 м/с, допуск 3 км)", cand[_truthy(cand["accept"])]["event_id"]),
    ]
    extra = {"quality_reject_reasons": {
        str(k): int(v) for k, v in pq[pq["decision"] != "accept"]["reason"].value_counts().items()}}
    return [s[0] for s in stages], [by_src(s[1]) for s in stages], extra


def csv_search_levels() -> dict:
    """Розыск §11 по событиям CSV: уровни A–D по reports/search/s{1..4}_candidates.csv."""
    tot = Counter()
    per = {}
    for k in ("s1", "s2", "s3", "s4"):
        p = os.path.join(REP, "search", f"{k}_candidates.csv")
        if not os.path.exists(p):
            continue
        df = pd.read_csv(p, low_memory=False)
        col = next((c for c in ("level", "evidence_level") if c in df.columns), None)
        if col is None:
            continue
        lv = df[col].astype(str).str.strip().str.upper().str[:1]
        c = Counter(x for x in lv if x in LEVELS)
        per[k.upper()] = {x: int(c.get(x, 0)) for x in LEVELS}
        tot.update(c)
    return {"total": {x: int(tot.get(x, 0)) for x in LEVELS}, "by_source": per}


def adis_total_segments() -> tuple[int | None, str]:
    raw = os.path.join(ROOT, "data", "extra", "field", "adis", "Segments.csv")
    if os.path.exists(raw):
        return int(len(pd.read_csv(raw, usecols=[0]))), "data/extra/field/adis/Segments.csv"
    md = os.path.join(REP, "extra_data", "field.md")
    if os.path.exists(md):
        txt = open(md, encoding="utf-8").read()
        m = re.search(r"\|\s*ADIS, отрезки 10 км\s*\|\s*([\d\s ]+)\(", txt)
        if m:
            return int(re.sub(r"\D", "", m.group(1))), "reports/extra_data/field.md (таблица §2)"
    return None, "нет"


def adis_stages(sn: dict) -> list[tuple[str, int | None]]:
    total, _ = adis_total_segments()
    fc = pd.read_csv(os.path.join(REP, "extra_data", "field_candidates.csv"), low_memory=False,
                     usecols=["source"])
    with_s2 = int(fc["source"].astype(str).str.startswith("ADIS").sum())
    ap = sn["adis_pairs"]
    return [
        ("Отрезки ADIS по 10 км (камера судна, счёт предметов)", total),
        ("Есть снимок Sentinel-2 L2A в ±1 сут", with_s2),
        (f"|dt| ≤ {ap['dt_max_h']:.0f} ч и облачность сцены ≤ {ap['cloud_max_pct']} %", ap["segments"]),
        ("Пары по месту и времени (уровень A: время, дрейф, площадь, годные пиксели)", ap["A"]),
        (f"…с единичными предметами ({ap['A_with_items_density_min']:.1f}–{ap['A_with_items_density_max']:.1f} шт./км²)",
         ap["A_with_items"]),
        ("…из них детектор оценивается (солнце, сигнал воды)", ap["A_with_items_eval"]),
        ("Срабатываний детектора в полосе (на всех с предметами), пикс.", ap["A_with_items_det_px"]),
        ("Калибровочные пары «снимок → шт./км²» (A с сигналом на снимке)", sn["quantity"]["calibration"]["pairs_A_with_S_pos"]),
    ]


def cozar_units() -> dict:
    """Единицы Cózar 2024 прямо по реестру: окна; съёмки = уникальные тайл+дата; продукты L1C = уникальные scene_id.
    Продуктов больше съёмок: у части тайл+дата в реестре два продукта L1C одной съёмки (разное время обработки)."""
    z = pd.read_csv(os.path.join(REP, "extra_data", "registry_cozar2024.csv.gz"), low_memory=False,
                    usecols=["scene_id", "tile", "date"])
    acq = z[["tile", "date"]].drop_duplicates()
    per = z.groupby(["tile", "date"])["scene_id"].nunique()
    return {
        "windows": int(len(z)),
        "acq_tile_date": int(len(acq)),
        "products_l1c_scene_id": int(z["scene_id"].nunique()),
        "acq_with_2plus_products": int((per > 1).sum()),
        "tiles": int(z["tile"].nunique()),
        "definition": "съёмка = уникальная пара тайл MGRS + дата (единица проверки утечек, как у PLP/FO/судов); "
                      "продукт L1C = уникальный scene_id; 4 472 в отчёте L98 — это продукты, не тайл+дата",
        "source": "reports/extra_data/registry_cozar2024.csv.gz",
    }


def bd_block(sn: dict, cz: dict) -> list[tuple[str, str, int, str]]:
    ld = sn["labeled_data"]
    assert cz["acq_tile_date"] == ld["cozar"]["acq"], (cz, ld["cozar"])
    return [
        ("B", "съёмок PLP + FloatingObjects (съёмка = тайл + дата)", ld["b_new_acq"],
         f"PLP {ld['plp']['acq_B']} (мишени) · FO {ld['floatingobjects']['acq_B']} (естественные)"),
        ("B", "окон Cózar 2024 (нити плавучего материала)", cz["windows"],
         f"{_fmt(cz['acq_tile_date'])} съёмок (тайл + дата; {_fmt(cz['products_l1c_scene_id'])} продуктов L1C), "
         f"{cz['tiles']} тайлов"),
        ("D", "рамок судов", ld["vessels"]["boxes"], f"{ld['vessels']['acq']} съёмок (тайл + дата)"),
        ("D", "сцен облаков и теней", ld["clouds"]["scenes"], "проверенные негативы"),
    ]


def detector_v2_check(sn: dict) -> dict:
    """Строка про детектор v2 — только из search_numbers.json → detector_v2 (зафиксированный снимок L102),
    не из живого reports/detector_v2/experiments.json. Проверка: пересчёт по experiments_snapshot.json
    тем же правилом, что в scripts/case/collect_search.py (lightgbm retrain, кроме r0; ACCEPT*)."""
    dv = sn.get("detector_v2") or {}
    snap_p = os.path.join(REP, "detector_v2", "experiments_snapshot.json")
    if os.path.exists(snap_p) and dv.get("available"):
        e = _load_json(snap_p)
        cands = [x for x in (e.get("experiments") or []) if x.get("kind") == "lightgbm retrain" and x.get("exp") != "r0"]
        acc = [x.get("exp") for x in cands
               if str((x.get("decision") or {}).get("decision", "")).startswith("ACCEPT")]
        assert (len(cands), len(acc)) == (dv.get("n_variants"), dv.get("n_accept")), (
            f"детектор v2: снимок {len(cands)}/{len(acc)} против search_numbers.json "
            f"{dv.get('n_variants')}/{dv.get('n_accept')} — пересобрать collect_search.py")
        dv = dict(dv, has_r0=any(x.get("exp") == "r0" for x in (e.get("experiments") or [])))
        if dv.get("snapshot_generated"):
            assert dv["snapshot_generated"] == e.get("generated"), "search_numbers.json собран по другому снимку"
    return {k: dv.get(k) for k in ("n_variants", "n_seeds", "has_r0", "n_accept", "accepted", "orchestrator_accepted", "current_model",
                                   "snapshot_generated", "source")}


# ------------------------------------------------------------------ рисунок
def _fmt(n) -> str:
    return "—" if n is None else f"{int(n):,}".replace(",", " ")


def _bar_rows(ax, rows, colors_fn, xmax, label_w, big=14):
    """rows: [(label, value, segments[(v,color)])]; линейная шкала, подпись слева, число справа."""
    n = len(rows)
    ys = list(range(n))[::-1]
    for y, (lab, val, segs) in zip(ys, rows):
        left = 0.0
        for v, col in segs:
            if v and v > 0:
                ax.barh(y, v, left=left, height=0.62, color=col, edgecolor=SURF, linewidth=2)
                left += v
        ax.text(-label_w * 0.03, y, lab, ha="right", va="center", fontsize=10, color=INK, wrap=True)
        ax.text(left + xmax * 0.015, y, _fmt(val), ha="left", va="center", fontsize=big, fontweight="bold",
                color=INK)
    ax.set_xlim(-label_w, xmax * 1.18)
    ax.set_ylim(-1.2, n - 0.3)
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)


def main() -> None:
    sn = _load_json(os.path.join(REP, "search", "search_numbers.json"))
    labels, counts, extra = csv_stages()
    srch = csv_search_levels()
    ad = adis_stages(sn)
    cz = cozar_units()
    bd = bd_block(sn, cz)
    adis_src = adis_total_segments()[1]

    # сверка с search_numbers.json (один источник чисел)
    fn = [s["total"] for s in sn["search"]["funnel"]]
    mine = [sum(c.values()) for c in counts]
    assert fn == mine, f"воронка CSV расходится с search_numbers.json: {mine} vs {fn}"
    assert srch["total"]["A"] == sn["search"]["csv_A"] and srch["total"]["C"] == sn["search"]["csv_C"], srch

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig = plt.figure(figsize=(17, 10.2), facecolor=SURF)
    gs = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.0], width_ratios=[1.35, 1], hspace=0.28, wspace=0.05,
                          left=0.01, right=0.99, top=0.87, bottom=0.07)
    ax1 = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[1, 0])
    ax3 = fig.add_subplot(gs[:, 1])
    for a in (ax1, ax2, ax3):
        a.set_facecolor(SURF)

    fig.text(0.01, 0.955, "Как мы искали пары «снимок ↔ полевое число»: воронка данных",
             fontsize=18, fontweight="bold", color=INK)
    fig.text(0.01, 0.92, "A — снимок и независимое полевое число связаны по времени, месту, площади и категории · "
             "B — подтверждённая разметка скопления · C — кандидат · D — отвергнутый / отрицательный пример",
             fontsize=10.5, color=INK2)

    # --- 1. CSV организаторов (линейная шкала), сегменты по источникам
    t = srch["total"]
    rows1 = [(lab, sum(c.values()), [(c[s], SRC_COLOR[s]) for s in SOURCES]) for lab, c in zip(labels, counts)]
    x1 = max(r[1] for r in rows1)
    _bar_rows(ax1, rows1, None, x1, label_w=x1 * 0.95)
    ax1.set_title("1. События CSV организаторов (318; длина полосы — линейная шкала)", loc="left",
                  fontsize=12.5, color=INK, fontweight="bold")
    ax1.text(-x1 * 0.95 * 0.03, -0.75, "Розыск снимков §11 по событиям CSV\n(±сутки, S2 / Landsat / S1, дрейф)",
             ha="right", va="center", fontsize=10, color=INK, fontweight="bold")
    ax1.text(x1 * 0.015, -0.75, f"A {t['A']}  ·  C {t['C']}  ·  D {t['D']}", ha="left", va="center",
             fontsize=14, fontweight="bold", color=INK)
    handles = [plt.Rectangle((0, 0), 1, 1, color=SRC_COLOR[s]) for s in SOURCES]
    ax1.legend(handles, [SRC_LABEL[s] for s in SOURCES], loc="lower right", frameon=False, fontsize=9.5, ncol=1,
               bbox_to_anchor=(1.0, 0.0))
    ax1.set_ylim(-1.3, len(rows1) - 0.3)

    # --- 2. ADIS (длина — логарифмическая шкала: 21 444 и 3 на одной картинке)
    import math
    vmax = max(v for _, v in ad if v)
    lg = lambda v: 0 if not v else math.log10(v) + 0.3  # noqa: E731
    rows2 = [(lab, v, [(lg(v), ADIS_COLOR)]) for lab, v in ad]
    _bar_rows(ax2, rows2, None, lg(vmax), label_w=lg(vmax) * 0.95)
    ax2.set_title("2. ADIS, The Ocean Cleanup — найден нами, в CSV нет (длина полосы — логарифмическая шкала)",
                  loc="left", fontsize=12.5, color=INK, fontweight="bold")
    ax2.text(0, -0.95, "0 срабатываний на единичных предметах согласуется с физикой (доля покрытия ~10⁻⁷), "
             "но не задаёт общий предел для всех скоплений: плотных полос среди пар ADIS нет.",
             ha="left", va="center", fontsize=9.5, color=INK2)

    # --- 3. блок B/D: плитки (единицы разные — не столбики)
    ax3.set_xlim(0, 1)
    ax3.set_ylim(0, 1)
    ax3.axis("off")
    ax3.text(0.04, 0.985, "3. Новые размеченные данные для детектора", fontsize=12.5, fontweight="bold",
             color=INK, va="top")
    ax3.text(0.04, 0.945, "единицы разные, поэтому плитки, а не столбики", fontsize=9.5, color=INK2, va="top")
    y = 0.87
    for lv, name, val, sub in bd:
        col = B_COLOR if lv == "B" else D_COLOR
        ax3.add_patch(plt.Rectangle((0.04, y - 0.155), 0.012, 0.15, color=col, transform=ax3.transAxes))
        ax3.text(0.08, y - 0.005, f"{lv}", fontsize=13, fontweight="bold", color=INK, va="top")
        ax3.text(0.15, y - 0.005, _fmt(val), fontsize=24, fontweight="bold", color=INK, va="top")
        ax3.text(0.15, y - 0.075, name, fontsize=11, color=INK, va="top")
        ax3.text(0.15, y - 0.112, sub, fontsize=9.5, color=INK2, va="top")
        y -= 0.19
    ld = sn["labeled_data"]
    dv = detector_v2_check(sn)
    ax3.text(0.04, y + 0.02,
             f"Утечек с MARIDA/MADOS/нашими сценами: {ld.get('leaks_total', '—')}. "
             f"Дообучение на B + D: принято вариантов {dv.get('n_accept', '—')} из {dv.get('n_variants', '—')}"
             f" (× {dv.get('n_seeds') or 3} seed{' + контроль r0' if dv.get('has_r0') else ''};"
             f" снимок {str(dv.get('snapshot_generated') or '—')[:16]})"
             f" — в сервисе {dv.get('current_model') or 'weights/lgbm'}.",
             fontsize=9.5, color=INK2, va="top", wrap=True)

    fig.text(0.01, 0.02,
             "Источник: docs/img/funnel_fig.py ← data/pairs/*, reports/search/*_candidates.csv, "
             f"reports/search/search_numbers.json, reports/extra_data/field_candidates.csv, {adis_src}",
             fontsize=8.5, color=INK2)
    fig.savefig(OUT_PNG, dpi=120, facecolor=SURF)

    out = {
        "stages": [{"label": lab, "total": sum(c.values()), "by_source": c} for lab, c in zip(labels, counts)],
        "csv_search_levels": srch,
        "level_A_pairs_csv": t["A"],
        "adis_stages": [{"label": lab, "total": v} for lab, v in ad],
        "adis_total_source": adis_src,
        "labeled_BD": [{"level": lv, "label": name, "total": val, "note": sub} for lv, name, val, sub in bd],
        "cozar_units": cz,
        "detector_v2": dv,
        **extra,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
