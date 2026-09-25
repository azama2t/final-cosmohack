"""L88: per-footprint detail for scenes found by s1_stac_search.py + control queries.

1) Control queries: the same catalogues/collections WITH a place/time where data surely exist (Hawaii, Oct 2016)
   -> proves that "0 items over the GPGP lines" is an absence of acquisitions, not a broken query.
2) Sentinel-1 WV: split the MultiPolygon into 20x20 km imagettes, approximate each imagette time from its
   along-track position, distance (km) from every mosaic centre; flag imagettes within 50 km and +-2 days.
3) Sentinel-3 OLCI (300 m): footprint covers the lines; context only (cloud %, time vs flight date).
Outputs: controls.csv, wv_imagettes.csv, s3_olci_context.csv
Run: .venv/Scripts/python.exe data/search/s1/s1_scene_detail.py
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd
from shapely.geometry import Point, shape

import s1_stac_search as S  # same folder; reuse the cached POST

OUT = Path(__file__).resolve().parent
ev = pd.read_csv(OUT / "events.csv")


def km_dist(poly, lat, lon) -> float:
    c = np.cos(np.deg2rad(lat))
    from shapely.affinity import scale, translate
    p = translate(poly, -lon, -lat)
    p = scale(p, xfact=111.32 * c, yfact=110.57, origin=(0, 0))
    return float(p.distance(Point(0, 0)))


def controls() -> pd.DataFrame:
    bbox = [-158.5, 19.0, -154.5, 22.5]  # Hawaii
    rows = []
    for cat, (base, colls) in S.STAC.items():
        for coll in colls:
            body = {"collections": [coll], "bbox": bbox, "datetime": "2016-10-01T00:00:00Z/2016-10-31T23:59:59Z",
                    "limit": 50}
            d = S.cached_post(base + "/search", body)
            rows.append({"catalog": cat, "collection": coll, "control_bbox": "Hawaii Oct-2016",
                         "n_items_control": len(d.get("features", [])), "error": d.get("error")})
    return pd.DataFrame(rows)


def wv() -> pd.DataFrame:
    rows = []
    for f in glob.glob(str(OUT / "cache" / "*.json")):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        for it in (d.get("features", []) if isinstance(d, dict) else []) or []:
            if "_WV_" not in it["id"]:
                continue
            p = it["properties"]
            t0, t1 = pd.Timestamp(p["start_datetime"]), pd.Timestamp(p.get("end_datetime") or p["start_datetime"])
            g = shape(it["geometry"])
            polys = list(g.geoms) if g.geom_type == "MultiPolygon" else [g]
            lats = np.array([q.centroid.y for q in polys])
            asc = p.get("sat:orbit_state") == "ascending"
            # along-track fraction by latitude (monotonic on this half-orbit segment; +-1 min approx)
            fr = (lats - lats.min()) / max(lats.max() - lats.min(), 1e-6)
            fr = fr if asc else 1 - fr
            for k, (q, f_) in enumerate(zip(polys, fr)):
                if not (27 < q.centroid.y < 37 and -150 < q.centroid.x < -128):
                    continue
                tk = t0 + (t1 - t0) * float(f_)
                ds = [(km_dist(q, r.lat, r.lon), r.event_id, r.date) for r in ev.itertuples()]
                dmin = min(ds)
                rows.append({"item_id": it["id"], "product": it["id"][:16], "imagette_idx": k,
                             "lat": round(q.centroid.y, 3), "lon": round(q.centroid.x, 3),
                             "t_approx_utc": tk.strftime("%Y-%m-%d %H:%M"), "nearest_event": dmin[1],
                             "dist_km": round(dmin[0], 1),
                             "dt_days": (tk.tz_convert(None).normalize() - pd.Timestamp(dmin[2])).days})
    df = pd.DataFrame(rows).drop_duplicates(["item_id", "imagette_idx"]).sort_values("dist_km")
    return df


def s3() -> pd.DataFrame:
    a = pd.read_csv(OUT / "scenes_all.csv")
    a = a[a.collection.str.contains("olci", na=False) & (a.n_events_inside.fillna(0) > 0)]
    return a[["catalog", "collection", "item_id", "datetime", "cloud", "n_events_inside", "dt_days_vs_nearest_event"]]


if __name__ == "__main__":
    c = controls(); c.to_csv(OUT / "controls.csv", index=False); print(c.to_string())
    w = wv(); w.to_csv(OUT / "wv_imagettes.csv", index=False); print(w.head(20).to_string())
    s = s3(); s.to_csv(OUT / "s3_olci_context.csv", index=False)
    print(s.groupby(s.datetime.str[:10]).size())
