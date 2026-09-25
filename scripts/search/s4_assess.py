"""S4: evidence level per transect (A/B/C/D, docs/INDEX.md) from time windows, scenes, crops and drift zones.

Inputs: data/search/s4/{transect_times,scenes,crops_summary,drift_zones}.csv. Output: data/search/s4/assessment.csv.
PLAUSIBLE: narrower windows from the cruise logic (hand-checked, reasons in the dict; the hard window stays in
transect_times.csv). dt = scene - observation (h).
  PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/search/s4_assess.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
S = ROOT / "data/search/s4"

PLAUSIBLE = {
    "T1": ("08:40-14:25", "after station BS066 (06:32-08:18, 6.8 km W), before AOT 15:01 at 29.733E (T1 west of it)"),
    "T2": ("15:25-17:25", "AOT 15:01 at 29.733E lies between T1 and T2 mid-points; ship heading east -> T2 after it"),
    "T18": ("01:50-05:00", "AOT 05:29 at 41.167E lies between T18 (41.069E) and T19 (41.248E); eastbound -> T18 before it"),
    "T19": ("05:50-08:00", "after AOT 05:29 (eastbound); upper bound = assumption (no fix until 06.06 07:12)"),
    "T30": ("08:35-11:15", "west of station BS088 (06:17-07:23, 31.80E), westbound to BS089 (14:37, 30.69E); order T30<T31<T32"),
    "T31": ("09:35-12:15", "same leg BS088 -> BS089"),
    "T32": ("10:50-13:30", "same leg BS088 -> BS089"),
}


def dt_range(date, iv, t_scene):
    a, b = iv.split("-")
    ta, tb = pd.Timestamp(f"{date} {a}"), pd.Timestamp(f"{date} {b}")
    return round((t_scene - tb).total_seconds() / 3600, 2), round((t_scene - ta).total_seconds() / 3600, 2)


def main():
    tt = pd.read_csv(S / "transect_times.csv")
    sc = pd.read_csv(S / "scenes.csv", parse_dates=["scene_datetime"])
    cr = pd.read_csv(S / "crops_summary.csv", parse_dates=["scene_datetime"]) if (S / "crops_summary.csv").exists() else pd.DataFrame()
    dz = pd.read_csv(S / "drift_zones.csv") if (S / "drift_zones.csv").exists() else pd.DataFrame()
    rows = []
    for r in tt.itertuples():
        s = sc[(sc.event_id == r.event_id) & sc.item_id.notna()]
        opt = s[s.collection.isin(["sentinel-2-l2a", "landsat-c2-l2"]) & (s.endpoint != "cdse")]
        same = opt[opt.scene_datetime.dt.normalize() == pd.Timestamp(r.date)]
        c = cr[cr.event_id == r.event_id] if len(cr) else pd.DataFrame()
        best = None
        if len(same):
            best = same.sort_values("dt_min_abs_h").iloc[0]
        pl = PLAUSIBLE.get(r.transect)
        dtp = dt_range(r.date, pl[0], best.scene_datetime.tz_localize(None) if best is not None and best.scene_datetime.tzinfo else
                       (best.scene_datetime if best is not None else None)) if (pl and best is not None) else (None, None)
        acc = c[c.decision == "accept"] if len(c) else c
        ndet = int(acc.n_det_strip.fillna(0).sum()) if len(acc) else None
        nzone = int(acc.zone_high_det_px.fillna(0).sum()) if len(acc) else None
        reasons = []
        if best is None:
            level, why = "D", "no optical scene on the survey day (nearest >= 15 h; drift over >=15 h at 0.2-0.5 m/s = 11-27+ km)"
        elif len(c) and not len(acc):
            level, why = "D", "same-day scene rejected by quality mask: " + ",".join(sorted(set(c.reason.fillna(""))))
        elif best.collection == "landsat-c2-l2" and not len(c):
            level, why = "C", "same-day Landsat only (QA mask, no detector)"
        else:
            level = "C"
            why = ("same-day S2, pixels usable; NOT A: transect length/width/area and start-end times not published "
                   "(mid-point + date only), item size 2.5 cm-? << 10 m pixel; detector 0 in strip")
        rows.append(dict(event_id=r.event_id, transect=r.transect, date=r.date, items_km2=r.items_km2,
                         hard_window_utc=r.feasible_intervals_utc, hard_window_h=r.window_h,
                         plausible_window_utc=pl[0] if pl else "", plausible_reason=pl[1] if pl else "",
                         best_scene=None if best is None else best.item_id,
                         best_scene_time=None if best is None else str(best.scene_datetime),
                         dt_min_abs_h=None if best is None else best.dt_min_abs_h,
                         dt_max_abs_h=None if best is None else best.dt_max_abs_h,
                         dt_plausible_h=f"{dtp[0]}..{dtp[1]}" if dtp[0] is not None else "",
                         n_same_day_scenes=len(same), quality=";".join(f"{x.tile}:{x.decision}{('(' + x.reason + ')') if isinstance(x.reason, str) and x.reason else ''}" for x in c.itertuples()) if len(c) else "",
                         n_det_strip=ndet, det_px_drift_zone_high=nzone,
                         drift_p95_km=(dz[dz.event_id == r.event_id].dist_p95_km.max() if len(dz) and (dz.event_id == r.event_id).any() else None),
                         level=level, why=why))
    o = pd.DataFrame(rows)
    o.to_csv(S / "assessment.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 60)
    print(o[["transect", "items_km2", "hard_window_utc", "plausible_window_utc", "best_scene_time", "dt_min_abs_h", "dt_plausible_h",
             "quality", "n_det_strip", "det_px_drift_zone_high", "drift_p95_km", "level"]].to_string())
    print(o.level.value_counts().to_dict())


if __name__ == "__main__":
    main()
