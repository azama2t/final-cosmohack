"""Воронка «318 событий → … → A/B/C/D» для деки и docs/PIPELINE.md.

Запуск:  .venv\\Scripts\\python.exe docs\\img\\funnel_fig.py
Выход:   docs/img/funnel.png, docs/img/funnel.json (числа, из которых нарисовано).

Все числа читаются из реестров, ничего не вписано руками:
  - data/pairs/events.csv, data/pairs/candidates.csv, data/pairs/pair_quality.csv — реестр 25.09;
  - data/search/*/candidates.csv — розыск §11 (колонка level / evidence_level: A, B, C, D);
  - reports/extra_data/*.csv и data/extra/*.csv — размеченные наборы и полевые данные §11
    (учитываются только файлы с колонкой level / evidence_level).
Если файлов §11 ещё нет, правая панель честно пишет «пока нет строк».
"""
from __future__ import annotations

import glob
import json
import os
from collections import Counter, defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_PNG = os.path.join(ROOT, "docs", "img", "funnel.png")
OUT_JSON = os.path.join(ROOT, "docs", "img", "funnel.json")

SOURCES = ["S1_GPGP2018", "S2_SARGASSO_MSM41", "S3_SE_NORTH_SEA", "S4_BLACK_SEA_DOORS3"]
SRC_LABEL = {
    "S1_GPGP2018": "S1 GPGP (пластик)",
    "S2_SARGASSO_MSM41": "S2 Саргассово (пластик)",
    "S3_SE_NORTH_SEA": "S3 Северное море (весь мусор)",
    "S4_BLACK_SEA_DOORS3": "S4 Чёрное море (весь мусор)",
}
# категориальная палитра по умолчанию (слоты 1–4, фиксированный порядок)
SRC_COLOR = {
    "S1_GPGP2018": "#2a78d6",
    "S2_SARGASSO_MSM41": "#eb6834",
    "S3_SE_NORTH_SEA": "#1baf7a",
    "S4_BLACK_SEA_DOORS3": "#eda100",
}
LEVELS = ["A", "B", "C", "D"]
LEVEL_COLOR = {"A": "#0d366b", "B": "#256abf", "C": "#86b6ef", "D": "#b4b2ab"}
LEVEL_NAME = {
    "A": "A — снимок + полевое число",
    "B": "B — подтверждённая разметка",
    "C": "C — кандидат, ручная проверка",
    "D": "D — отвергнут / негатив",
}
INK, INK2, SURF, GRID = "#0b0b0b", "#52514e", "#fcfcfb", "#e4e3df"


def _truthy(s: pd.Series) -> pd.Series:
    return s.astype(str).str.lower().isin(["true", "1", "yes"])


def pair_stages() -> tuple[list[str], list[dict[str, int]], dict]:
    ev = pd.read_csv(os.path.join(ROOT, "data", "pairs", "events.csv"))
    cand = pd.read_csv(os.path.join(ROOT, "data", "pairs", "candidates.csv"), low_memory=False)
    pq = pd.read_csv(os.path.join(ROOT, "data", "pairs", "pair_quality.csv"))
    src_of = dict(zip(ev["event_id"], ev["source_id"]))

    def by_src(ids) -> dict[str, int]:
        c = Counter(src_of.get(i, "?") for i in set(ids))
        return {s: int(c.get(s, 0)) for s in SOURCES}

    has_scene = cand[cand["item_id"].notna() & _truthy(cand["point_inside_footprint"])]
    meta = cand[_truthy(cand["accept_meta"])]
    q_ok = pq[pq["decision"].astype(str) == "accept"]
    sync = cand[_truthy(cand["accept"])]
    stages = [
        ("События в CSV организаторов", ev["event_id"]),
        ("Есть снимок S2/Landsat в ±5 сут\n(точка внутри контура сцены)", has_scene["event_id"]),
        ("Отбор по метаданным\n(|dt| ≤ 1 сут, облачность ≤ 60 %)", meta["event_id"]),
        ("Прошли маски качества в полосе\n(облака, блик, покрытие)", q_ok["event_id"]),
        ("Синхронны при типичном дрейфе\n(0.2 м/с, допуск 3 км)", sync["event_id"]),
    ]
    labels = [s[0] for s in stages]
    counts = [by_src(s[1]) for s in stages]
    extra = {
        "quality_reject_reasons": {
            str(k): int(v) for k, v in pq[pq["decision"] != "accept"]["reason"].value_counts().items()
        }
    }
    return labels, counts, extra


def _level_col(df: pd.DataFrame) -> str | None:
    for c in ("level", "evidence_level", "evidence", "уровень"):
        if c in df.columns:
            return c
    return None


def s11_levels() -> tuple[dict[str, Counter], list[str]]:
    """Сводка A/B/C/D по файлам §11: {группа: Counter(level)}."""
    groups: dict[str, Counter] = defaultdict(Counter)
    used: list[str] = []
    pats = [
        ("data/search/*/candidates.csv", "розыск"),
        ("reports/extra_data/*.csv", "наборы/поле"),
        ("data/extra/*.csv", "наборы/поле"),
        ("data/extra/*/*.csv", "наборы/поле"),
    ]
    seen = set()
    for pat, kind in pats:
        for f in sorted(glob.glob(os.path.join(ROOT, pat))):
            if f in seen:
                continue
            seen.add(f)
            try:
                df = pd.read_csv(f, low_memory=False)
            except Exception:
                continue
            col = _level_col(df)
            if col is None or df.empty:
                continue
            lv = df[col].astype(str).str.strip().str.upper().str[:1]
            lv = lv[lv.isin(LEVELS)]
            if lv.empty:
                continue
            rel = os.path.relpath(f, ROOT).replace("\\", "/")
            if kind == "розыск":
                name = "розыск " + rel.split("/")[2].upper()
            else:
                name = os.path.splitext(os.path.basename(f))[0]
            groups[name].update(lv.tolist())
            used.append(rel)
    return groups, used


def main() -> None:
    labels, counts, extra = pair_stages()
    groups, used = s11_levels()

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    fig = plt.figure(figsize=(16, 8.4), facecolor=SURF)
    gs = fig.add_gridspec(1, 2, width_ratios=[1.75, 1], wspace=0.08, left=0.02, right=0.985, top=0.86, bottom=0.12)
    ax = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[0, 1])
    for a in (ax, ax2):
        a.set_facecolor(SURF)
        for sp in a.spines.values():
            sp.set_visible(False)

    fig.text(0.02, 0.945, "Воронка данных: от 318 полевых событий до пар «снимок → шт./км²»",
             fontsize=17, fontweight="bold", color=INK)
    fig.text(0.02, 0.905, "Слева — реестр пар 25.09 (все 318 событий по S2/Landsat). "
             "Справа — уровни доказательности находок расследования 26.09 (§11).",
             fontsize=11.5, color=INK2)

    # ---- левая панель: горизонтальная воронка, сегменты по источникам
    n = len(labels)
    total0 = sum(counts[0].values())
    ys = list(range(n))[::-1]
    label_x = -0.02 * total0
    for y, lab, cnt in zip(ys, labels, counts):
        tot = sum(cnt.values())
        left = 0.0
        for s in SOURCES:
            v = cnt[s]
            if v <= 0:
                continue
            ax.barh(y, v, left=left, height=0.62, color=SRC_COLOR[s], edgecolor=SURF, linewidth=2)
            left += v
        ax.text(label_x, y, lab, ha="right", va="center", fontsize=10.5, color=INK)
        vx = max(tot, 0) + 0.012 * total0
        ax.text(vx, y, f"{tot}", ha="left", va="center", fontsize=15, fontweight="bold", color=INK)
    ax.set_xlim(-0.62 * total0, total0 * 1.1)
    ax.set_ylim(-1.4, n - 0.4)
    ax.set_yticks([])
    ax.set_xticks([])
    # итог-строка A
    ax.text(label_x, -1.05, "Пары уровня A (снимок + полевое число)", ha="right", va="center",
            fontsize=10.5, color=INK, fontweight="bold")
    nA = sum(g.get("A", 0) for g in groups.values())
    ax.text(0.012 * total0, -1.05, f"{nA}", ha="left", va="center", fontsize=15, fontweight="bold", color=INK)
    handles = [plt.Rectangle((0, 0), 1, 1, color=SRC_COLOR[s]) for s in SOURCES]
    ax.legend(handles, [SRC_LABEL[s] for s in SOURCES], loc="lower right", frameon=False, fontsize=10,
              bbox_to_anchor=(1.0, -0.12), ncol=2)

    # ---- правая панель: A/B/C/D по группам §11
    ax2.set_title("Расследование §11: находки по уровням", loc="left", fontsize=13, color=INK, pad=10)
    if groups:
        names = sorted(groups)
        yy = list(range(len(names)))[::-1]
        mx = max(sum(groups[k].values()) for k in names)
        for y, k in zip(yy, names):
            left = 0
            for lv in LEVELS:
                v = groups[k].get(lv, 0)
                if v <= 0:
                    continue
                ax2.barh(y, v, left=left, height=0.6, color=LEVEL_COLOR[lv], edgecolor=SURF, linewidth=2)
                left += v
            parts = " · ".join(f"{lv} {groups[k].get(lv, 0)}" for lv in LEVELS)
            ax2.text(0, y + 0.42, f"{k}", ha="left", va="bottom", fontsize=10, color=INK)
            ax2.text(left + mx * 0.02, y, parts, ha="left", va="center", fontsize=9.5, color=INK2)
        ax2.set_xlim(0, mx * 1.6)
        ax2.set_ylim(-0.8, len(names) - 0.1)
        h2 = [plt.Rectangle((0, 0), 1, 1, color=LEVEL_COLOR[lv]) for lv in LEVELS]
        ax2.legend(h2, [LEVEL_NAME[lv] for lv in LEVELS], loc="upper left", frameon=False, fontsize=9.5,
                   bbox_to_anchor=(0.0, -0.02), ncol=2)
    else:
        ax2.text(0.02, 0.6, "Пока нет строк с уровнем A–D:\nисполнители розыска и наборов\nещё не выложили реестры.",
                 transform=ax2.transAxes, fontsize=11.5, color=INK2, va="top")
    ax2.set_xticks([])
    ax2.set_yticks([])

    fig.text(0.02, 0.03,
             "A — снимок и независимое полевое число связаны по времени, месту, площади и категории; "
             "B — подтверждённая разметка скопления (без числа); C — кандидат; D — отвергнут/негатив. "
             "Источник: docs/img/funnel_fig.py",
             fontsize=9, color=INK2)
    fig.savefig(OUT_PNG, dpi=130, facecolor=SURF)

    out = {
        "stages": [
            {"label": lab.replace("\n", " "), "total": sum(c.values()), "by_source": c}
            for lab, c in zip(labels, counts)
        ],
        "level_A_pairs": nA,
        "s11_levels": {k: {lv: int(v.get(lv, 0)) for lv in LEVELS} for k, v in sorted(groups.items())},
        "s11_files": used,
        **extra,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
