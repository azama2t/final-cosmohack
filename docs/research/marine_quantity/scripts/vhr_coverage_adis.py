"""L117 P3: is there a VHR / 3 m optical image over ADIS routes WITH counted items on the same day?

Source of VHR metadata: Copernicus Data Space Ecosystem STAC, collection `ccm-optical`
(Copernicus Contributing Missions: WorldView-2/3, GeoEye-1, Pleiades 1A/1B, Pleiades Neo, SPOT, PlanetScope, ...).
Metadata search is anonymous (checked in api_audit.py); DATA download requires CDSE login + CCM eligibility.

Unit of query: (UTC date, 0.5 deg cell) groups of ADIS segments with n_objects>5cm > 0 (2 018 groups).
Match rule: item footprint contains >= 1 positive segment centre AND |start_datetime - segment time| <= 24 h.
Output (metadata only): results/vhr_adis_matches.csv, results/vhr_adis_summary.json
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests
from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parents[4]
RES = ROOT / "docs/research/marine_quantity/results"
RAW = ROOT / "data/extra/marine_quantity/ccm_raw"
RAW.mkdir(parents=True, exist_ok=True)
URL = "https://catalogue.dataspace.copernicus.eu/stac/collections/ccm-optical/items"


def load():
    s = pd.read_csv(ROOT / "data/extra/field/adis/Segments.csv")
    s["t"] = pd.to_datetime(s["timestamp"], format="mixed", utc=True, errors="coerce")
    s["t"] = s["t"].fillna(pd.to_datetime(s["mindate"], utc=True))
    s = s[s["n_objects>5cm"] > 0].copy()
    s["date"] = s["t"].dt.date
    s["clat"] = (s.Latitude * 2).round() / 2
    s["clon"] = (s.Longitude * 2).round() / 2
    return s


def query(key):
    date, clat, clon = key
    f = RAW / f"{date}_{clat}_{clon}.json"
    if f.exists():
        return key, json.loads(f.read_text(encoding="utf-8"))
    d0 = (pd.Timestamp(date) - pd.Timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    d1 = (pd.Timestamp(date) + pd.Timedelta(days=1)).strftime("%Y-%m-%dT23:59:59Z")
    bbox = f"{clon-0.3},{clat-0.3},{clon+0.3},{clat+0.3}"
    feats, url, params = [], URL, {"bbox": bbox, "datetime": f"{d0}/{d1}", "limit": 100}
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, timeout=60)
            if r.status_code == 200:
                j = r.json()
                feats = j.get("features", [])
                break
            time.sleep(2 + 3 * attempt)
        except Exception:
            time.sleep(2 + 3 * attempt)
    else:
        return key, None
    slim = [{"id": x["id"], "geometry": x["geometry"],
             "p": {k: x["properties"].get(k) for k in ("gsd", "platform", "start_datetime", "end_datetime",
                                                       "eo:cloud_cover", "product:type", "constellation")}}
            for x in feats]
    f.write_text(json.dumps(slim), encoding="utf-8")
    return key, slim


def main():
    s = load()
    groups = s.groupby(["date", "clat", "clon"])
    keys = list(groups.groups.keys())
    print("groups", len(keys), flush=True)
    rows, failed = [], 0
    with ThreadPoolExecutor(3) as ex:
        for i, (key, feats) in enumerate(ex.map(query, keys)):
            if i % 200 == 0:
                print(i, flush=True)
            if feats is None:
                failed += 1
                continue
            g = groups.get_group(key)
            for it in feats:
                try:
                    geom = shape(it["geometry"])
                except Exception:
                    continue
                ts = pd.to_datetime(it["p"].get("start_datetime"), utc=True, errors="coerce")
                for _, seg in g.iterrows():
                    if not geom.contains(Point(seg.Longitude, seg.Latitude)):
                        continue
                    dt_h = (ts - seg.t).total_seconds() / 3600 if pd.notna(ts) else None
                    if dt_h is None or abs(dt_h) > 24:
                        continue
                    rows.append({"segment_id": seg.SegmentID, "ship": seg.Ship, "seg_time_utc": seg.t.isoformat(),
                                 "lat": seg.Latitude, "lon": seg.Longitude, "n_gt5cm": seg["n_objects>5cm"],
                                 "n_gt50cm": seg["n_objects>50cm"], "area_km2": seg.area_scanned_km2,
                                 "item_id": it["id"], "platform": it["p"].get("platform"), "gsd_m": it["p"].get("gsd"),
                                 "product_type": it["p"].get("product:type"), "img_start_utc": it["p"].get("start_datetime"),
                                 "dt_h": round(dt_h, 3), "cloud": it["p"].get("eo:cloud_cover")})
    m = pd.DataFrame(rows)
    m.to_csv(RES / "vhr_adis_matches.csv", index=False)
    summ = {"groups_queried": len(keys), "groups_failed": failed, "positive_segments": int(len(s)),
            "matches_rows": int(len(m))}
    if len(m):
        for lab, sub in {"all_24h": m, "le_3h": m[m.dt_h.abs() <= 3], "le_1h": m[m.dt_h.abs() <= 1]}.items():
            summ[lab] = {"segments": int(sub.segment_id.nunique()), "items": int(sub.item_id.nunique()),
                         "by_platform_segments": sub.groupby("platform").segment_id.nunique().to_dict(),
                         "segments_gsd_le_1m": int(sub[sub.gsd_m <= 1].segment_id.nunique()),
                         "segments_gsd_le_3m": int(sub[sub.gsd_m <= 3].segment_id.nunique()),
                         "sum_n_gt5cm_on_gsd_le_1m": int(sub[sub.gsd_m <= 1].drop_duplicates("segment_id").n_gt5cm.sum())}
    (RES / "vhr_adis_summary.json").write_text(json.dumps(summ, indent=1, default=str), encoding="utf-8")
    print(json.dumps(summ, indent=1, default=str))


if __name__ == "__main__":
    main()
