"""S4 (DOORS cruise 3, R/V Mare Nigrum, June 2024): reconstruct observation-time windows for the 33 litter transects.

The litter file (Zenodo 15129753 = 15172377, same table) has date + transect mid-point only. Sister datasets of the same
cruise (Zenodo 15044582 AOT, 15120079 CHL/TSM, 15259958 AOP, 15778382 Rrs, 15044752 CTD) give timed ship positions (fixes).
Feasible time of transect Tk (mid-point) = daylight of its date (sun elevation > 0)
  AND reachability from every fix f:  |t - t_f| >= dist(Tk, f) / v_max
  AND order Tk < Tk+1 (numbering is chronological: consistent with the along-track order on every day):
      t_{k+1} - t_k >= dist(Tk, Tk+1) / v_max.
Point estimate: linear interpolation between two same-day fixes bracketing the transect along the track, else window centre.
Output: data/search/s4/fixes.csv, data/search/s4/transect_times.csv.
v_max = 11 kn (observed 9.4 kn between two AOT fixes on 03.06).

  PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/search/s4_track.py
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
S = ROOT / "data/search/s4"
C = S / "src_csv"
VMAX_KMH = 11 * 1.852
STEP_MIN = 5


def hav(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def sun_elev(ts: pd.Timestamp, lat, lon):
    d = ts.dayofyear + (ts.hour + ts.minute / 60) / 24
    g = 2 * math.pi / 365 * (d - 1)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g))
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g)
                    - 0.040849 * math.sin(2 * g))
    tst = ts.hour * 60 + ts.minute + eqt + 4 * lon
    ha = math.radians(tst / 4 - 180)
    la = math.radians(lat)
    return math.degrees(math.asin(math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)))


def xl_time(date, tm):
    date, tm = str(date).strip(), str(tm).strip()
    if re.fullmatch(r"\d{5}(\.0)?", date):
        d0 = pd.Timestamp("1899-12-30") + pd.Timedelta(days=float(date))
    else:
        d0 = pd.Timestamp(pd.to_datetime(date, dayfirst="/" in date).date())
    if re.fullmatch(r"0?\.\d+(E-\d+)?", tm):
        return d0 + pd.Timedelta(days=float(tm))
    return d0 + pd.to_timedelta(tm if tm.count(":") == 2 else tm + ":00")


def fixes() -> pd.DataFrame:
    rows = []
    a = pd.read_csv(C / "AOT_DOORS_cruise_3__Sheet1.csv")
    for r in a.itertuples(index=False):
        rows.append(("AOT Zenodo 15044582", "", xl_time(r[0], r[1]), r[2], r[3]))
    c = pd.read_csv(C / "CHL_TSM_SD_DOORS_cruise_3__Sheet1.csv")
    for r in c.itertuples(index=False):
        rows.append(("CHL Zenodo 15120079", r[0], pd.Timestamp(r[1]), r[2], r[3]))
    p = pd.read_csv(C / "AOP_DOORS_cruise_3__Sheet1.csv")
    for r in p.itertuples(index=False):
        if pd.notna(r[1]) and str(r[1]).strip():
            rows.append(("AOP Zenodo 15259958", r[0], xl_time(r[1], r[2]), r[3], r[4]))
    q = pd.read_csv(S / "src/DOORS_Jun2024_Rrs.csv", usecols=[0, 1, 2, 3, 4])
    for r in q.itertuples(index=False):
        rows.append(("Rrs Zenodo 15778382", r[0], xl_time(r[1], r[2]), r[3], r[4]))
    t = pd.read_csv(C / "CTD_Profiles_DOORS_cruise3__Sheet1.csv", usecols=[0, 1, 2, 3, 4], dtype=str)
    t = t.dropna(subset=["station"]).groupby("station", as_index=False).first()
    for r in t.itertuples(index=False):
        try:
            rows.append(("CTD Zenodo 15044752", r[0], xl_time(r[1], r[2]), float(r[3]), float(r[4])))
        except Exception:  # noqa: BLE001
            pass
    return pd.DataFrame(rows, columns=["source", "station", "time_utc", "lat", "lon"]).sort_values("time_utc").reset_index(drop=True)


# Manual exclusions (checked by hand against the same station in other files, see reports/search/s4.md):
#  CTD (15044752): station series MN256-* keep one nominal position; times contradict CHL/Rrs/AOT of the same day
#      (e.g. 07.06 09:49 at 41.820 N while CHL/Rrs put the ship at BS069 41.596 N 09:32-09:41, 25 km away) -> not used.
#  AOP BS087AA: 42.936 N vs 41.801 N in CHL/Rrs for the same station/time (typo in latitude).
#  AOP BS089: 02:28 (Excel day fraction 0.1028) vs 14:37 in CHL/Rrs -> 12-h clock error.
DROP = {("AOP Zenodo 15259958", "BS087AA"): "latitude typo (42.936 vs 41.801 in CHL/Rrs)",
        ("AOP Zenodo 15259958", "BS089"): "12-h clock error (02:28 vs 14:37 in CHL/Rrs)"}
DROP_SOURCES = {"CTD Zenodo 15044752": "nominal station positions; times contradict CHL/Rrs/AOT"}


def check_fixes(f: pd.DataFrame) -> pd.DataFrame:
    """Manual exclusions + drop fixes that need an impossible speed (> 1.5 v_max) to both time neighbours."""
    f = f.copy()
    f["drop_reason"] = [DROP.get((a, str(b)), DROP_SOURCES.get(a, "")) for a, b in zip(f.source, f.station)]
    f["drop"] = f.drop_reason != ""
    for _ in range(5):
        k = f[~f["drop"]].index.tolist()
        bad = []
        for i, j in enumerate(k):
            sp = []
            for n in ([k[i - 1]] if i else []) + ([k[i + 1]] if i + 1 < len(k) else []):
                dt = abs((f.time_utc[j] - f.time_utc[n]).total_seconds()) / 3600
                dd = hav(f.lat[j], f.lon[j], f.lat[n], f.lon[n])
                sp.append(dd / max(dt, 1 / 60) if dd > 2 else 0)
            if sp and min(sp) > 1.5 * VMAX_KMH:
                bad.append(j)
        if not bad:
            break
        f.loc[bad, "drop"] = True
        f.loc[bad, "drop_reason"] = "impossible speed to both neighbours"
    return f


def main():
    f = check_fixes(fixes())
    f.to_csv(S / "fixes.csv", index=False)
    use = f[~f["drop"]]
    z = pd.read_csv(C / "Floating_Marine_Litter_DOORs_cruise_3__Sheet1.csv").iloc[:, 1:]
    z.columns = ["transect", "date", "lat", "lon", "items_km2"]
    z["k"] = z.transect.str[1:].astype(int)
    swap = z.lat < z.lon - 5  # T33 lat/lon transposed in the source (lat 29.2) -> swap back (as in the organisers' CSV)
    z.loc[swap, ["lat", "lon"]] = z.loc[swap, ["lon", "lat"]].values
    z["date"] = pd.to_datetime(z.date, dayfirst=True)
    z = z.sort_values("k").reset_index(drop=True)
    grids = []
    for r in z.itertuples():
        g = pd.date_range(r.date, r.date + pd.Timedelta(hours=24) - pd.Timedelta(minutes=STEP_MIN), freq=f"{STEP_MIN}min")
        day = np.array([sun_elev(t, r.lat, r.lon) > 0 for t in g])
        ok = day.copy()
        for fx in use.itertuples():
            need = hav(r.lat, r.lon, fx.lat, fx.lon) / VMAX_KMH
            ok &= np.abs((g - fx.time_utc).total_seconds() / 3600) >= need - 1e-9
        grids.append([g, ok, day])
    for _ in range(3):  # order constraints, forward/backward
        for i in range(1, len(z)):
            d = hav(z.lat[i - 1], z.lon[i - 1], z.lat[i], z.lon[i]) / VMAX_KMH
            prev = grids[i - 1][0][grids[i - 1][1]]
            if len(prev):
                grids[i][1] &= grids[i][0] >= prev.min() + pd.Timedelta(hours=d)
        for i in range(len(z) - 2, -1, -1):
            d = hav(z.lat[i], z.lon[i], z.lat[i + 1], z.lon[i + 1]) / VMAX_KMH
            nxt = grids[i + 1][0][grids[i + 1][1]]
            if len(nxt):
                grids[i][1] &= grids[i][0] <= nxt.max() - pd.Timedelta(hours=d)
    out = []
    for r, (g, ok, day) in zip(z.itertuples(), grids):
        tt = g[ok]
        same = use[use.time_utc.dt.normalize() == r.date].reset_index(drop=True)
        near = same.assign(d=[hav(r.lat, r.lon, a, b) for a, b in zip(same.lat, same.lon)]).sort_values("d").head(3)
        est, how, best = None, "", None
        for j in range(len(same) - 1):
            a, b = same.iloc[j], same.iloc[j + 1]
            dab = hav(a.lat, a.lon, b.lat, b.lon)
            da, db = hav(a.lat, a.lon, r.lat, r.lon), hav(r.lat, r.lon, b.lat, b.lon)
            if dab > 3 and (da + db) <= 1.25 * dab:
                sc = (da + db) / dab
                if best is None or sc < best[0]:
                    best = (sc, a, b, da / (da + db))
        if best:
            sc, a, b, fr = best
            est = a.time_utc + (b.time_utc - a.time_utc) * fr
            how = (f"interp {a.source.split()[0]} {a.time_utc:%H:%M} -> {b.source.split()[0]} {b.time_utc:%H:%M} "
                   f"(detour {sc:.2f})")
        if len(tt):
            lo, hi = tt.min(), tt.max()
            br = np.where(np.diff(tt.values).astype("timedelta64[m]").astype(int) > STEP_MIN)[0]
            gaps = len(br)
            seg = np.split(np.arange(len(tt)), br + 1)
            intervals = "; ".join(f"{tt[x[0]]:%H:%M}-{tt[x[-1]]:%H:%M}" for x in seg)
            if est is None or not (lo <= est <= hi):
                if est is not None:
                    how += "; outside feasible window -> window centre"
                est = lo + (hi - lo) / 2
                how = how or "window centre"
            if not ok[g == est.floor(f"{STEP_MIN}min")].any():  # centre fell into a gap -> nearest feasible time
                est = tt[int(np.argmin(np.abs((tt - est).total_seconds())))]
                how += " (moved to nearest feasible time)"
        else:
            lo = hi = None
            gaps = 0
            intervals = ""
            how = "no feasible time (check fixes)"
        dl = g[day]
        out.append(dict(event_id=f"S4:DOORS3:{r.transect}", transect=r.transect, date=r.date.date(), lat=r.lat, lon=r.lon,
                        items_km2=r.items_km2, daylight_utc=f"{dl.min():%H:%M}-{dl.max():%H:%M}",
                        t_lo=lo, t_hi=hi, window_h=None if lo is None else round((hi - lo).total_seconds() / 3600, 2),
                        window_gaps=gaps, feasible_intervals_utc=intervals, t_est=est, t_est_method=how, n_fixes_same_day=len(same),
                        nearest_fixes="; ".join(f"{x.source.split()[0]} {x.station} {x.time_utc:%H:%M} {x.d:.1f} km"
                                                for x in near.itertuples())))
    o = pd.DataFrame(out)
    o.to_csv(S / "transect_times.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 80)
    print("dropped fixes:\n", f[f["drop"]].to_string())
    print(o[["transect", "date", "daylight_utc", "t_lo", "t_hi", "window_h", "feasible_intervals_utc", "t_est", "t_est_method"]].to_string())


if __name__ == "__main__":
    main()
