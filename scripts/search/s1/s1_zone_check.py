"""L88: which found scenes fall into the drift search zone at THEIR acquisition time.
For each footprint (S1 WV imagette, S3 OLCI frame) with |t_scene - flight date| <= 2 d: particles of that flight
(seed time uniform over the UTC date) are taken at t_scene (only those with |t_scene - t_seed| <= 48 h),
share inside the footprint = "how much of the plausible debris cloud the scene could have seen".
This is a SEARCH statistic, never an identity match.
Outputs: zone_scene_hits.csv, s1_search_map.png
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from shapely import contains_xy
from shapely.geometry import shape

OUT = Path(__file__).resolve().parent
P = np.load(OUT / "drift_particles.npz")
LA, LO, steps, eid = P["lat"], P["lon"], P["steps_h"], P["event_id"]
tseed = pd.to_datetime(P["t_seed"]).values
ev = pd.read_csv(OUT / "events.csv")
date_of = dict(zip(ev.event_id, ev.date))
fdate = np.array([date_of[e] for e in eid])


def footprints():
    wv = pd.read_csv(OUT / "wv_imagettes.csv")
    keep = {}
    for f in glob.glob(str(OUT / "cache" / "*.json")):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        for it in (d.get("features", []) if isinstance(d, dict) else []) or []:
            iid = it["id"]
            if "_WV_" in iid and iid in set(wv.item_id):
                g = shape(it["geometry"])
                for r in wv[wv.item_id == iid].itertuples():
                    keep[(iid, r.imagette_idx)] = ("S1 WV " + r.product[:3] + r.product[10:13], g.geoms[r.imagette_idx],
                                                   pd.Timestamp(r.t_approx_utc))
            elif "OL_1_EFR" in iid or "OL_2_WFR" in iid:
                p = it["properties"]
                keep[(iid, 0)] = ("S3 OLCI " + iid[4:12], shape(it["geometry"]), pd.Timestamp(p["datetime"]).tz_convert(None))
    return keep


def main() -> None:
    rows = []
    fps = footprints()
    for (iid, k), (kind, poly, t) in fps.items():
        for fd in sorted(ev.date.unique()):
            dtd = (t - pd.Timestamp(fd)).total_seconds() / 86400
            if dtd < -2 or dtd > 3:
                continue
            m = fdate == fd
            rel = ((t.to_datetime64() - tseed[m]) / np.timedelta64(1, "h")).round().astype(int)
            ok = np.abs(rel) <= 48
            if not ok.any():
                continue
            idx = np.searchsorted(steps, rel[ok])
            la, lo = LA[m][ok, idx], LO[m][ok, idx]
            inside = contains_xy(poly, lo, la)
            if inside.sum() == 0 and poly.distance(shape({"type": "Point", "coordinates": [float(np.median(lo)), float(np.median(la))]})) > 3:
                continue
            rows.append({"item_id": iid, "imagette_idx": k, "kind": kind, "t_scene_utc": t.strftime("%Y-%m-%d %H:%M"),
                         "flight_date": fd, "dt_h_vs_flight_noon": round(dtd * 24 - 12, 1),
                         "n_particles_valid": int(ok.sum()), "share_in_footprint": round(float(inside.mean()), 4),
                         "footprint_km2": round(poly.area * 111 * 111 * np.cos(np.deg2rad(poly.centroid.y)), 0)})
    df = pd.DataFrame(rows).sort_values(["kind", "share_in_footprint"], ascending=[True, False])
    df.to_csv(OUT / "zone_scene_hits.csv", index=False)
    print(df[df.share_in_footprint > 0].to_string())
    # map
    fig, ax = plt.subplots(figsize=(11, 6.2))
    zones = json.loads((OUT / "drift_zones.geojson").read_text())["features"]
    col = {"2016-10-02": "tab:red", "2016-10-06": "tab:orange"}
    for z in zones:
        if str(z["properties"]["horizon_h"]).startswith("envelope"):
            g = shape(z["geometry"]); x, y = g.exterior.xy
            ax.fill(x, y, alpha=0.15, color=col[z["properties"]["flight_date"]],
                    label=f"зона розыска ±48 ч, полёт {z['properties']['flight_date']}")
    for fd, g in ev.groupby("date"):
        ax.plot(g.lon, g.lat, "-o", ms=3, color=col[fd], label=f"центры мозаик {fd}")
    hit = df[df.share_in_footprint > 0]
    lab = False
    for (iid, k), (kind, poly, t) in fps.items():
        if not kind.startswith("S1"):
            continue
        dtd = min(abs((t - pd.Timestamp(fd)).days) for fd in col)
        if dtd > 2:
            continue
        x, y = poly.exterior.xy
        is_hit = ((hit.item_id == iid) & (hit.imagette_idx == k)).any()
        ax.plot(x, y, color="tab:blue" if is_hit else "0.6", lw=1.5 if is_hit else 0.6,
                label=None if lab or not is_hit else "S1 WV 20×20 км, частицы внутри")
        if is_hit:
            lab = True
            ax.annotate(t.strftime("%d.%m %H:%M"), (poly.centroid.x, poly.centroid.y), fontsize=7, color="tab:blue")
    ax.set_xlim(-146, -132.5); ax.set_ylim(28.5, 35.5); ax.set_aspect(1 / np.cos(np.deg2rad(32)))
    ax.set_xlabel("долгота"); ax.set_ylabel("широта"); ax.legend(fontsize=7, loc="lower right")
    ax.set_title("S1 GPGP аэросъёмка: зона розыска (HYCOM GOFS 3.1 + ERA5, ±48 ч) и снимки ±2 сут.\n"
                 "S2 / Landsat / S1 IW — 0 сцен; серые квадраты — S1 WV вне облака частиц", fontsize=9)
    fig.tight_layout(); fig.savefig(OUT / "s1_search_map.png", dpi=130)


if __name__ == "__main__":
    main()
