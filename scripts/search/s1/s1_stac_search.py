"""L88 / §11 direction 1: re-search imagery for S1 GPGP aerial survey (02.10.2016, 06.10.2016).

Queries several STAC catalogues (CDSE, Earth Search, Planetary Computer, USGS LandsatLook)
and NASA CMR (MODIS/VIIRS, context only) over a generous bbox around both flight lines
with a +-4 day window. Every raw response is cached in data/search/s1/cache/.
Output: data/search/s1/scenes_all.csv (one row per item x catalogue), events.csv.
Run: .venv/Scripts/python.exe data/search/s1/s1_stac_search.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests
from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
CACHE = OUT / "cache"
CACHE.mkdir(exist_ok=True)

BBOX = [-146.0, 28.5, -132.5, 35.5]  # both flight lines + ~150-250 km drift/search buffer
T0, T1 = "2016-09-28T00:00:00Z", "2016-10-10T23:59:59Z"

STAC = {
    "cdse": ("https://stac.dataspace.copernicus.eu/v1", [
        "sentinel-2-l1c", "sentinel-2-l2a", "sentinel-2-night-time-acquisitions",
        "landsat-c2-l1-oli-tirs", "landsat-c2-l1-oli", "landsat-c2-l1-tirs",
        "sentinel-1-grd", "sentinel-1-slc", "sentinel-1-ocn", "sentinel-1-ocn-wv", "sentinel-1-slc-wv",
        "sentinel-3-olci-1-efr-ntc", "sentinel-3-olci-1-err-ntc", "sentinel-3-olci-2-wfr-ntc"]),
    "earth-search": ("https://earth-search.aws.element84.com/v1", [
        "sentinel-2-l1c", "sentinel-2-l2a", "landsat-c2-l2", "sentinel-1-grd"]),
    "planetary-computer": ("https://planetarycomputer.microsoft.com/api/stac/v1", [
        "landsat-c2-l1", "landsat-c2-l2", "sentinel-1-grd", "sentinel-2-l2a",
        "sentinel-3-olci-lfr-l2-netcdf", "sentinel-3-olci-wfr-l2-netcdf"]),
    "usgs": ("https://landsatlook.usgs.gov/stac-server", ["landsat-c2l1", "landsat-c2l2-sr"]),
}


def cached_post(url: str, body: dict) -> dict:
    key = hashlib.sha1((url + json.dumps(body, sort_keys=True)).encode()).hexdigest()
    f = CACHE / f"{key}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    r = None
    for _ in range(3):
        try:
            r = requests.post(url, json=body, timeout=120)
            if r.status_code == 429:  # CDSE WAF rate limit: back off, never cache
                import time
                time.sleep(20)
                r = None
                err = "429 rate limit"
                continue
            break
        except requests.RequestException as e:  # network reset: do not cache
            err = str(e)[:200]
    if r is None:
        return {"error": "network", "text": err, "features": []}
    if r.status_code >= 400:
        d = {"error": r.status_code, "text": r.text[:500], "features": []}
    else:
        d = r.json()
    f.write_text(json.dumps(d), encoding="utf-8")
    return d


def cached_get(url: str, params: dict) -> dict:
    key = hashlib.sha1((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()
    f = CACHE / f"{key}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    r = requests.get(url, params=params, timeout=120)
    d = r.json() if r.status_code < 400 else {"error": r.status_code, "text": r.text[:500]}
    f.write_text(json.dumps(d), encoding="utf-8")
    return d


def stac_search(cat: str, base: str, coll: str) -> list[dict]:
    body = {"collections": [coll], "bbox": BBOX, "datetime": f"{T0}/{T1}", "limit": 200}
    d = cached_post(base + "/search", body)
    if "error" in d:
        return [{"catalog": cat, "collection": coll, "error": f"{d['error']}: {d['text'][:200]}"}]
    rows = []
    for it in d.get("features", []):
        p = it.get("properties", {})
        rows.append({
            "catalog": cat, "collection": coll, "item_id": it["id"],
            "datetime": p.get("datetime") or p.get("start_datetime"),
            "platform": p.get("platform"), "mode": p.get("sar:instrument_mode"),
            "cloud": p.get("eo:cloud_cover"), "day_night": p.get("landsat:day_night") or p.get("sat:orbit_state"),
            "geometry": json.dumps(it.get("geometry")),
        })
    if not rows:
        rows.append({"catalog": cat, "collection": coll, "item_id": None, "n_returned": 0})
    return rows


def cmr(short: str) -> list[dict]:
    params = {"short_name": short, "bounding_box": ",".join(map(str, BBOX)),
              "temporal": "2016-09-30T00:00:00Z,2016-10-08T23:59:59Z", "page_size": 200}
    d = cached_get("https://cmr.earthdata.nasa.gov/search/granules.json", params)
    ents = d.get("feed", {}).get("entry", []) if isinstance(d, dict) else []
    rows = [{"catalog": "nasa-cmr", "collection": short, "item_id": e.get("producer_granule_id") or e.get("title"),
             "datetime": e.get("time_start"), "platform": short[:3], "day_night": e.get("day_night_flag"),
             "geometry": None} for e in ents]
    return rows or [{"catalog": "nasa-cmr", "collection": short, "item_id": None, "n_returned": 0}]


def events() -> pd.DataFrame:
    d = pd.read_csv(ROOT / "task/macroplastic_marine_samples.csv")
    a = d[d.source_id.eq("S1_GPGP2018") & d.sampling_method.str.contains("aerial|mosaic", case=False, na=False)]
    g = a.groupby("event_id").agg(lat=("latitude", "first"), lon=("longitude", "first"),
                                  date=("date_utc", "first"), area_km2=("sampled_area_km2", "first"),
                                  n_rows=("sample_id", "count"),
                                  total_items=("source_reported_total_items", "first")).reset_index()
    return g.sort_values(["date", "lon"])


def main() -> None:
    ev = events()
    ev.to_csv(OUT / "events.csv", index=False)
    jobs = [(c, b, coll) for c, (b, colls) in STAC.items() for coll in colls]
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda j: stac_search(*j), jobs))
        res += list(ex.map(cmr, ["MOD021KM", "MYD021KM", "VNP02MOD", "MOD09GA", "MYD09GA"]))
    rows = [r for rs in res for r in rs]
    df = pd.DataFrame(rows)
    # footprint vs events: nearest event distance (deg->km approx) and count of events inside
    pts = [(r.event_id, r.date, Point(r.lon, r.lat)) for r in ev.itertuples()]
    ins, near, near_ev, dtd = [], [], [], []
    for r in df.itertuples():
        g = getattr(r, "geometry", None)
        if not isinstance(g, str) or g in ("null", "None"):
            ins.append(None); near.append(None); near_ev.append(None); dtd.append(None); continue
        geom = shape(json.loads(g))
        dist = [(geom.distance(p) * 100.0, e, dte) for e, dte, p in pts]  # ~km at 33N (lon deg ~93 km)
        dmin = min(dist)
        ins.append(sum(1 for d_, _, _ in dist if d_ == 0))
        near.append(round(dmin[0], 1)); near_ev.append(dmin[1])
        dtd.append((pd.Timestamp(r.datetime).tz_convert(None).normalize() - pd.Timestamp(dmin[2])).days
                   if r.datetime else None)
    df["n_events_inside"] = ins
    df["nearest_event_km_approx"] = near
    df["nearest_event"] = near_ev
    df["dt_days_vs_nearest_event"] = dtd
    df.drop(columns=["geometry"]).to_csv(OUT / "scenes_all.csv", index=False)
    summ = df.groupby(["catalog", "collection"]).agg(
        n_items=("item_id", lambda s: s.notna().sum()),
        n_touching_line=("n_events_inside", lambda s: (s.fillna(0) > 0).sum()),
        min_km=("nearest_event_km_approx", "min")).reset_index()
    if "error" in df:
        errs = df.dropna(subset=["error"])[["catalog", "collection", "error"]]
        summ = summ.merge(errs, how="left", on=["catalog", "collection"])
    summ.to_csv(OUT / "scenes_summary.csv", index=False)
    print(summ.to_string())


if __name__ == "__main__":
    sys.exit(main())
