"""Survey-priority zones ("приоритет обследования"): top <= 10 H3 cells.

score = base × agreement × date_penalty
    base         = flagged_water_px × mean_prob × (1 + 0.5 × (repeat_dates − 1))
    agreement    = 1 + AGREE_W × confirmed_share          (AGREE_W = 1; 1 … 2)
    date_penalty = 0.5 if the date is unreliable (haze / glint), else 1
where
    flagged_water_px - debris-like observed water pixels in the cell on this date (primary key); objects marked as
                       artefacts (seam / wake / ship, grid.artifacts) are NOT counted (artefact filter)
    mean_prob        - mean model probability over those pixels (confidence)
    repeat_dates     - on how many *reliable* (no haze/glint) dates of the region (same model) the cell had flagged
                       pixels; the current date always counts (persistence). artefact filter: hazy dates no longer add to it
    confirmed_share  - share of the cell's flagged pixels that belong to detections confirmed by the second model
                       (grid.confirm, 20 m); 0 on single-model dates
Only cells with a defined index (observed_frac >= 0.5) are ranked. This is a survey priority, not plastic mass.
Each zone carries `score_terms` {base, agreement, date_penalty, score} and `why` (formula with numbers, Russian).
"""
from __future__ import annotations

from . import PIXEL_AREA_M2

TOP_N = 10
REPEAT_BONUS = 0.5
AGREE_W = 1.0
HAZE_PENALTY = 0.5
FORMULA = ("score = flagged_water_px × mean_prob × (1 + 0.5 × (repeat_dates − 1)) × (1 + 1 × confirmed_share) "
           "× (0.5 при дымке/блике)")


def base_score(flagged_px: int, mean_prob: float | None, repeat_dates: int) -> float:
    return float(flagged_px) * float(mean_prob or 0.0) * (1.0 + REPEAT_BONUS * max(int(repeat_dates) - 1, 0))


def zone_score(flagged_px: int, mean_prob: float | None, repeat_dates: int, confirmed_share: float = 0.0,
               unreliable: bool = False) -> float:
    return (base_score(flagged_px, mean_prob, repeat_dates) * (1.0 + AGREE_W * float(confirmed_share or 0.0))
            * (HAZE_PENALTY if unreliable else 1.0))


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


def make_why(px: int, mp: float, rep: int, share: float, unreliable: bool) -> str:
    rep_f = 1.0 + REPEAT_BONUS * max(rep - 1, 0)
    agree = 1.0 + AGREE_W * share
    pen = HAZE_PENALTY if unreliable else 1.0
    s = f"{px} пикс. × {mp:.2f} × повтор {rep_f:.1f} × согласие {agree:.2f} × дата {pen:.1f} = " \
        f"{px * mp * rep_f * agree * pen:.1f}"
    notes = []
    if share > 0:
        notes.append(f"{share * 100:.0f} % пикселей подтверждены второй моделью")
    if unreliable:
        notes.append("дымка/блик на снимке — балл ×0.5")
    return s + (" (" + "; ".join(notes) + ")" if notes else "")


def rank_zones(stats: list[dict], repeat: dict[str, int] | None = None, n_dates: int = 1,
               top_n: int = TOP_N, pixel_area_m2: float = PIXEL_AREA_M2,
               confirmed_px: dict[str, int] | None = None, unreliable: bool = False) -> list[dict]:
    """stats: output of h3index.h3_stats for one date/model. repeat: h3 -> number of dates flagged.
    confirmed_px: h3 -> flagged pixels of confirmed detections in the cell. unreliable: haze/glint date."""
    repeat = repeat or {}
    confirmed_px = confirmed_px or {}
    cand = []
    for s in stats:
        if s["flagged_water_px"] <= 0 or s["share_permille"] is None:
            continue
        rep = max(int(repeat.get(s["h3"], 1)), 1)
        share = min(1.0, max(0.0, confirmed_px.get(s["h3"], 0) / s["flagged_water_px"]))
        cand.append((zone_score(s["flagged_water_px"], s["mean_prob"], rep, share, unreliable), s, rep, share))
    cand.sort(key=lambda t: (-t[0], -t[1]["share_permille"], t[1]["h3"]))
    zones = []
    for rank, (score, s, rep, share) in enumerate(cand[:top_n], start=1):
        area = s["flagged_water_px"] * pixel_area_m2
        mp = float(s["mean_prob"] or 0.0)
        base = base_score(s["flagged_water_px"], mp, rep)
        agree = 1.0 + AGREE_W * share
        pen = HAZE_PENALTY if unreliable else 1.0
        zones.append({"rank": rank, "h3": s["h3"], "index": s["share_permille"], "area_m2": round(area, 1),
                      "reason": make_reason(s["flagged_water_px"], area, s["n_detections"], rep, n_dates, s["mean_prob"]),
                      "lon": s["lon"], "lat": s["lat"], "repeat_dates": rep, "mean_prob": s["mean_prob"],
                      "flagged_water_px": s["flagged_water_px"], "observed_frac": s["observed_frac"],
                      "n_detections": s["n_detections"], "score": round(score, 3),
                      "confirmed_share": round(share, 4),
                      "score_terms": {
                          "base": {"value": round(base, 3),
                                   "label": "пиксели × уверенность × повторяемость (надёжные даты)"},
                          "agreement": {"value": round(agree, 4), "label": "множитель согласия моделей",
                                        "note": f"1 + {AGREE_W:g} × доля подтверждённых пикселей ({share:.2f})"},
                          "date_penalty": {"value": pen, "label": "штраф за ненадёжную дату",
                                           "note": "дымка/блик ×0.5" if unreliable else "дата надёжна"},
                          "score": round(score, 3)},
                      "why": make_why(s["flagged_water_px"], mp, rep, share, unreliable)})
    return zones
