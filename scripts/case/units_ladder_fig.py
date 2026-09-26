r"""«Лестница» единиц количества → docs/img/units_ladder.png. Числа — только из reports/final_numbers.json (case.sections).

    .venv\Scripts\python.exe scripts\case\units_ladder_fig.py

Ступени: предмет на кадре (фото, счётчик) → шт./м² кадра (известная площадь) → шт./км² маршрута (поле, C = N/A)
→ спутниковая зона (площадь, статус «концентрация по снимку не подтверждена»). У каждой — своя проверка и метрика.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "img" / "units_ladder.png"


def _f(x, nd=1):
    if x is None:
        return "—"
    return f"{x:,.{nd}f}".replace(",", " ").replace(".", ",")


def steps(fn: dict) -> list[dict]:
    s = fn["case"]["sections"]
    pc, q, sz = s.get("photo_count") or {}, s.get("quantity") or {}, s.get("scene_zones") or {}
    f2, af = q.get("field_S2") or {}, q.get("adis_forecast") or {}
    lv = q.get("levels") or {}
    fr, ar = pc.get("frame") or {}, pc.get("area") or {}
    ci = fr.get("mae_ci95") or [None, None]
    return [
        {"head": "1. Предмет на кадре", "unit": "шт. на кадр",
         "what": "фото с воды: счётчик находит и считает предметы",
         "check": f"отложенный test FML ({_f(fr.get('n_images'), 0)} кадров, по сессиям)",
         "metric": f"MAE {_f(fr.get('mae'), 2)} шт./кадр [{_f(ci[0], 2)}–{_f(ci[1], 2)}]"},
        {"head": "2. Плотность на кадре", "unit": "шт./м² → шт./км²",
         "what": "только если известна площадь кадра (GSD)",
         "check": f"аэро Winans, кадр {_f(ar.get('frame_area_m2'), 0)} м², {_f(ar.get('n_test_frames'), 0)} кадров",
         "metric": f"MAE {_f(ar.get('density_mae_km2'), 0)} против медианы {_f(ar.get('baseline_density_mae_km2'), 0)} шт./км²"},
        {"head": "3. Маршрут судна (поле)", "unit": "шт./км², C = N / A",
         "what": "главный количественный результат",
         "check": f"S2: {_f(f2.get('pooled_C'), 1)} [{_f(f2.get('boot_lo95'), 1)}–{_f(f2.get('boot_hi95'), 1)}]; ADIS > 10 см: {_f(af.get('C'), 2)}",
         "metric": "отложенный test: модель против медианы профиля"},
        {"head": "4. Спутниковая зона", "unit": "м² маски, LWD м²/км²",
         "what": f"{_f(sz.get('n_zones'), 0)} зон; {_f(sz.get('by_level_b'), 0)} совпали с нитями Cózar",
         "check": f"калибровочных пар «снимок → шт./км²»: {_f(lv.get('calibration_pairs'), 0)}",
         "metric": "концентрация по снимку не подтверждена"},
    ]


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    fn = json.loads((ROOT / "reports" / "final_numbers.json").read_text(encoding="utf-8"))
    st = steps(fn)
    fig, ax = plt.subplots(figsize=(17, 5.6), dpi=110)
    ax.set_xlim(0, 16.6)
    ax.set_ylim(0, 5.0)
    ax.axis("off")
    colors = ["#2b6cb0", "#2f855a", "#b7791f", "#718096"]
    w, gap = 3.55, 0.55
    for i, s in enumerate(st):
        x = 0.25 + i * (w + gap)
        ax.add_patch(FancyBboxPatch((x, 0.35), w, 4.2, boxstyle="round,pad=0.02,rounding_size=0.12",
                                    fc="white", ec=colors[i], lw=2.2))
        ax.add_patch(FancyBboxPatch((x, 3.75), w, 0.8, boxstyle="round,pad=0.02,rounding_size=0.12", fc=colors[i], ec=colors[i]))
        ax.text(x + w / 2, 4.15, s["head"], ha="center", va="center", color="white", fontsize=13, weight="bold")
        ax.text(x + w / 2, 3.35, s["unit"], ha="center", va="center", fontsize=12.5, weight="bold", color="#1a202c")
        for j, key in enumerate(("what", "check", "metric")):
            ax.text(x + 0.15, 2.85 - j * 0.85, textwrap.fill(s[key], 30), ha="left", va="top", fontsize=10, color="#2d3748",
                    weight="bold" if key == "metric" else "normal")
        if i < len(st) - 1:
            ax.add_patch(FancyArrowPatch((x + w + 0.03, 2.4), (x + w + gap - 0.03, 2.4), arrowstyle="-|>", mutation_scale=20,
                                         color="#4a5568", lw=1.8))
    ax.text(8.3, 0.0, "Каждая ступень — своя проверка и своя метрика; перевода между ступенями без калибровки нет. "
                    "Числа — reports/final_numbers.json.", ha="center", va="bottom", fontsize=10, color="#4a5568")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, bbox_inches="tight", facecolor="white")
    print(f"[units_ladder] -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
