"""L45: summary figure of reports/drift_check.json -> reports/figures/drift_check_summary.png (two panels, one axis each)."""
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
d = json.loads((ROOT / "reports" / "drift_check.json").read_text(encoding="utf-8"))
C = {"forecast": "#2a78d6", "baseline": "#eb6834", "shift": "#1baf7a"}
SURF, INK, INK2 = "#fcfcfb", "#0b0b0b", "#52514e"
labels = ["прогноз (OpenDrift)", "нулевой дрейф", "сдвиг назад\n(та же площадь)"]
keys = ["forecast", "baseline", "shift"]
fig, axes = plt.subplots(1, 2, figsize=(10, 4.2), dpi=120, facecolor=SURF)
n = d["n_pairs"]
vals = [d["k_hit"], d["k_hit_baseline"], d["k_hit_shift"]]
ax = axes[0]
bars = ax.bar(range(3), vals, color=[C[k] for k in keys], width=0.6)
for b, v in zip(bars, vals):
    ax.text(b.get_x() + b.get_width() / 2, v + 0.2, f"{v}/{n}", ha="center", va="bottom", color=INK, fontsize=10)
ax.set_ylim(0, n)
ax.set_title("Пары с ≥ 1 находкой снимка 2 в 90%-м контуре", fontsize=10, color=INK)
dh = [d["det_hits"], d["det_hits_baseline"], d["det_hits_shift"]]
ex = [d["expected_random_hits"], d["expected_random_hits_baseline"], None]
ax2 = axes[1]
bars = ax2.bar(range(3), dh, color=[C[k] for k in keys], width=0.6)
for i, (b, v) in enumerate(zip(bars, dh)):
    ax2.text(b.get_x() + b.get_width() / 2, v + 2, str(v), ha="center", va="bottom", color=INK, fontsize=10)
    if ex[i] is not None:
        ax2.plot([b.get_x(), b.get_x() + b.get_width()], [ex[i], ex[i]], color=INK, lw=2, ls="--")
ax2.plot([], [], color=INK, lw=2, ls="--", label="ожидание при случайном размещении\n(та же площадь контура)")
ax2.legend(fontsize=8, loc="upper left", frameon=False, labelcolor=INK2)
ax2.set_ylim(0, max(dh) * 1.3)
ax2.set_title(f"Находки снимка 2 в контуре (всего {d['n_det2_total']})", fontsize=10, color=INK)
for a in axes:
    a.set_facecolor(SURF)
    a.set_xticks(range(3), labels, fontsize=9, color=INK2)
    a.tick_params(axis="y", colors=INK2, labelsize=8)
    for s in ("top", "right"):
        a.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        a.spines[s].set_color("#c3c2b7")
    a.yaxis.grid(True, color="#e8e7e2", lw=0.8)
    a.set_axisbelow(True)
fig.suptitle(f"Проверка дрейфа следующим снимком: {n} пар, Δt 1–5 сут (MDD, критерий зафиксирован заранее)",
             fontsize=11, color=INK)
fig.tight_layout()
out = ROOT / "reports" / "figures" / "drift_check_summary.png"
fig.savefig(out, facecolor=SURF)
print(out)
