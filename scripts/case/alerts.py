"""§51 п.6 / п.7 / п.10 — offline check of the alert rule (docs/ALERTS.md, code in src/macroplastic/case/alerts.py).
Prints the distribution of importance_rank / alert_level over the real built scene_zones, so the thresholds in
ALERTS.md can be checked against real data before/after they are wired into the live API (service/case_store.py).

Run:  .venv\\Scripts\\python.exe scripts\\case\\alerts.py
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from service import case_store as cs  # noqa: E402


def main() -> None:
    feats = cs.scene_zones_all()
    finds = [f["properties"] for f in feats if f["properties"].get("is_find")]
    print(f"зон всего: {len(feats)}, находок: {len(finds)}")

    levels = collections.Counter(p["alert_level"] for p in finds)
    print("\nalert_level (находки):")
    for k in ("слабый", "средний", "высокий"):
        print(f"  {k}: {levels.get(k, 0)}")

    ranks = collections.Counter(p["importance_rank"] for p in finds)
    print("\nimportance_rank (находки), по возрастанию:")
    for k in sorted(ranks):
        print(f"  {k:>3}: {ranks[k]}")

    n_shore_ok = sum(1 for p in finds if p.get("shore_km") is not None)
    n_drift_ok = sum(1 for p in finds if p.get("stranded_pct_72h") is not None)
    print(f"\nshore_km посчитан: {n_shore_ok}/{len(finds)}")
    print(f"stranded_pct_72h есть (опубликован прогноз): {n_drift_ok}/{len(finds)}")

    reasons = collections.Counter(p.get("drift_reason") for p in finds if p.get("stranded_pct_72h") is None)
    print("\nпричины отсутствия прогноза дрейфа (топ-5):")
    for reason, n in reasons.most_common(5):
        print(f"  {n:>3}  {reason}")

    n_conf = sum(1 for p in finds if p.get("verification") == "level_B_cozar")
    print(f"\nподтверждённых находок (Cózar B): {n_conf}/{len(finds)} "
          f"— только они могут получить «высокий» (docs/ALERTS.md §4)")


if __name__ == "__main__":
    main()
