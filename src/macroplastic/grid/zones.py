"""Survey-priority zones ("приоритет обследования"): top <= 10 H3 cells.

score = flagged_water_px * mean_prob * (1 + 0.5 * (repeat_dates - 1))
    flagged_water_px - debris-like observed water pixels in the cell on this date (primary key)
    mean_prob        - mean model probability over those pixels (confidence)
    repeat_dates     - on how many dates of the region (same model) the cell had flagged pixels (persistence)
Only cells with a defined index (observed_frac >= 0.5) are ranked. This is a survey priority, not plastic mass.
"""
from __future__ import annotations

from . import PIXEL_AREA_M2

TOP_N = 10
REPEAT_BONUS = 0.5


def zone_score(flagged_px: int, mean_prob: float | None, repeat_dates: int) -> float:
    return float(flagged_px) * float(mean_prob or 0.0) * (1.0 + REPEAT_BONUS * max(int(repeat_dates) - 1, 0))


def _plural_spot(n: int) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return "пятно"
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return "пятна"
    return "пятен"


def make_reason(flagged_px: int, area_m2: float, n_det: int, repeat_dates: int, n_dates: int,
                mean_prob: float | None) -> str:
    parts = [f"{flagged_px} пикс. с признаками мусора (~{area_m2:.0f} м²)"]
    if n_det:
        parts.append(f"{n_det} {_plural_spot(n_det)}")
    if n_dates > 1:
        parts.append(f"повторяется на {repeat_dates} из {n_dates} дат" if repeat_dates > 1 else "только на этой дате")
    if mean_prob is not None:
        parts.append(f"ср. уверенность {mean_prob:.2f}")
    return "; ".join(parts)


def rank_zones(stats: list[dict], repeat: dict[str, int] | None = None, n_dates: int = 1,
               top_n: int = TOP_N, pixel_area_m2: float = PIXEL_AREA_M2) -> list[dict]:
    """stats: output of h3index.h3_stats for one date/model. repeat: h3 -> number of dates flagged."""
    repeat = repeat or {}
    cand = []
    for s in stats:
        if s["flagged_water_px"] <= 0 or s["share_permille"] is None:
            continue
        rep = max(int(repeat.get(s["h3"], 1)), 1)
        cand.append((zone_score(s["flagged_water_px"], s["mean_prob"], rep), s, rep))
    cand.sort(key=lambda t: (-t[0], -t[1]["share_permille"], t[1]["h3"]))
    zones = []
    for rank, (score, s, rep) in enumerate(cand[:top_n], start=1):
        area = s["flagged_water_px"] * pixel_area_m2
        zones.append({"rank": rank, "h3": s["h3"], "index": s["share_permille"], "area_m2": round(area, 1),
                      "reason": make_reason(s["flagged_water_px"], area, s["n_detections"], rep, n_dates, s["mean_prob"]),
                      "lon": s["lon"], "lat": s["lat"], "repeat_dates": rep, "mean_prob": s["mean_prob"],
                      "flagged_water_px": s["flagged_water_px"], "observed_frac": s["observed_frac"],
                      "n_detections": s["n_detections"], "score": round(score, 3)})
    return zones
