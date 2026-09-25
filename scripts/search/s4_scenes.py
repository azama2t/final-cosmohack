"""S4: all scenes within +-1 day of each DOORS-3 transect, with dt against the reconstructed observation-time windows.

Input: data/search/s4/transect_times.csv (scripts/search/s4_track.py).
Collections (STAC, point intersects, date-1 .. date+1):
  earth-search sentinel-2-l2a / sentinel-2-l1c, CDSE sentinel-2-l2a / sentinel-2-l1c, planetary-computer landsat-c2-l2,
  CDSE sentinel-1-grd, CDSE sentinel-3-olci-1-efr-ntc (S1/S3 = context only).
Cache: data/search/s4/stac_cache/<coll>__<event>.json (rerun is offline). Output: data/search/s4/scenes.csv
dt_* in hours = scene_time - observation_time (positive = scene after the survey):
  dt_est (point estimate), dt_min_abs / dt_max_abs over the feasible time intervals.
  PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/search/s4_scenes.py
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
S = ROOT / "data/search/s4"
CACHE = S / "stac_cache"
ES = "https://earth-search.aws.element84.com/v1"
CDSE = "https://stac.dataspace.copernicus.eu/v1"
PC = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLS = [("earth-search", ES, "sentinel-2-l2a"), ("earth-search", ES, "sentinel-2-l1c"),
         ("cdse", CDSE, "sentinel-2-l2a"), ("cdse", CDSE, "sentinel-2-l1c"),
         ("planetary-computer", PC, "landsat-c2-l2"),
         ("cdse", CDSE, "sentinel-1-grd"), ("cdse", CDSE, "sentinel-3-olci-1-efr-ntc")]


def query(ep, url, coll, eid, lon, lat, d0, d1):
    f = CACHE / f"{ep}_{coll}__{eid.replace(':', '_')}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    from pystac_client import Client
    last = None
    for a in range(4):
        try:
            c = Client.open(url)
            items = list(c.search(collections=[coll], intersects=dict(type="Point", coordinates=[lon, lat]),
                                  datetime=f"{d0}T00:00:00Z/{d1}T23:59:59Z", max_items=200).items())
            out = []
            for it in items:
                p = it.properties
                out.append(dict(id=it.id, datetime=p.get("datetime") or p.get("start_datetime"),
                                cloud=p.get("eo:cloud_cover"), platform=p.get("platform"),
                                tile=p.get("s2:mgrs_tile") or p.get("grid:code") or
                                (f"{p.get('landsat:wrs_path')}/{p.get('landsat:wrs_row')}" if p.get("landsat:wrs_path") else None),
                                mode=p.get("sar:instrument_mode"), orbit=p.get("sat:relative_orbit"),
                                sun_elev=p.get("view:sun_elevation")))
            CACHE.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(out, indent=0), encoding="utf-8")
            return out
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (a + 1))
    return [dict(error=f"{type(last).__name__}: {last}"[:300])]


def dt_stats(t_scene: pd.Timestamp, r) -> tuple:
    est = (t_scene - pd.Timestamp(r.t_est)).total_seconds() / 3600
    iv = []
    for s in str(r.feasible_intervals_utc).split(";"):
        s = s.strip()
        if "-" in s:
            a, b = s.split("-")
            iv.append((pd.Timestamp(f"{r.date} {a}"), pd.Timestamp(f"{r.date} {b}")))
    if not iv:
        return round(est, 2), None, None
    mins, maxs = [], []
    for a, b in iv:
        if a <= t_scene <= b:
            mins.append(0.0)
        else:
            mins.append(min(abs((t_scene - a).total_seconds()), abs((t_scene - b).total_seconds())) / 3600)
        maxs.append(max(abs((t_scene - a).total_seconds()), abs((t_scene - b).total_seconds())) / 3600)
    return round(est, 2), round(min(mins), 2), round(max(maxs), 2)


def main():
    tt = pd.read_csv(S / "transect_times.csv")
    jobs = []
    for r in tt.itertuples():
        d = pd.Timestamp(r.date)
        d0, d1 = (d - pd.Timedelta(days=1)).date(), (d + pd.Timedelta(days=1)).date()
        for ep, url, coll in COLLS:
            jobs.append((r, ep, url, coll, d0, d1))
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda j: query(j[1], j[2], j[3], j[0].event_id, j[0].lon, j[0].lat, j[4], j[5]), jobs))
    rows = []
    for (r, ep, url, coll, d0, d1), items in zip(jobs, res):
        for it in items:
            if "error" in it:
                rows.append(dict(event_id=r.event_id, endpoint=ep, collection=coll, error=it["error"]))
                continue
            ts = pd.Timestamp(it["datetime"]).tz_convert(None) if pd.Timestamp(it["datetime"]).tzinfo else pd.Timestamp(it["datetime"])
            est, mn, mx = dt_stats(ts, r)
            rows.append(dict(event_id=r.event_id, transect=r.transect, obs_date=r.date, items_km2=r.items_km2,
                             endpoint=ep, collection=coll, item_id=it["id"], scene_datetime=ts, cloud=it.get("cloud"),
                             platform=it.get("platform"), tile=it.get("tile"), mode=it.get("mode"),
                             dt_est_h=est, dt_min_abs_h=mn, dt_max_abs_h=mx, error=""))
    o = pd.DataFrame(rows)
    o.to_csv(S / "scenes.csv", index=False)
    print(len(o), "rows;", o.groupby(["endpoint", "collection"]).size().to_dict())
    if "error" in o and (o.error.fillna("") != "").any():
        print("errors:", o[o.error.fillna("") != ""][["event_id", "collection", "error"]].head(10).to_string())


if __name__ == "__main__":
    main()
