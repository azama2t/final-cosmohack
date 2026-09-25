"""L86 — S3 (North Sea, Gutow 2018) scene inventory in +-1 day around each transect, all open STAC catalogs.

  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/search/s3/s3_inventory.py

One STAC search per (catalog, collection, cruise) over the cruise bbox (+0.6 deg drift margin) and period (+-1 day),
cached as JSON in data/search/s3/cache/; matched to events locally (footprint vs transect line).
Output: data/search/s3/inventory.csv (one row per event x item with |dt| <= 24 h).
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
from shapely.geometry import LineString, shape

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data" / "search" / "s3"
CACHE = OUT / "cache"
sys.path.insert(0, str(ROOT / "src"))

CATALOGS = {
    "earth-search": "https://earth-search.aws.element84.com/v1/search",
    "planetary-computer": "https://planetarycomputer.microsoft.com/api/stac/v1/search",
    "cdse": "https://stac.dataspace.copernicus.eu/v1/search",
    "usgs": "https://landsatlook.usgs.gov/stac-server/search",
}
# (catalog, collection, role): role optical = candidate for pixel check; context = weather/sea state only
COLLS = [
    ("earth-search", "sentinel-2-l2a", "optical"), ("earth-search", "sentinel-2-l1c", "optical"),
    ("earth-search", "sentinel-2-c1-l2a", "optical"), ("earth-search", "landsat-c2-l2", "optical"),
    ("planetary-computer", "sentinel-2-l2a", "optical"), ("planetary-computer", "landsat-c2-l2", "optical"),
    ("cdse", "sentinel-2-l1c", "optical"), ("cdse", "sentinel-2-l2a", "optical"),
    ("usgs", "landsat-c2l1", "optical"), ("usgs", "landsat-c2l2-sr", "optical"),
    ("planetary-computer", "sentinel-1-grd", "context"), ("cdse", "sentinel-1-grd", "context"),
    ("cdse", "sentinel-3-olci-1-efr-ntc", "context"), ("cdse", "sentinel-3-olci-1-err-ntc", "context"),
    ("planetary-computer", "modis-09GA-061", "context"),
]
CRUISES = {  # bbox from data/case/geometry/transects.csv + 0.6 deg; period = first start - 1 d .. last end + 1 d
    "HE419": None, "HE460": None,
}


def post(url: str, body: dict, retries: int = 4) -> dict:
    last = None
    for a in range(retries):
        try:
            r = requests.post(url, json=body, timeout=90)
            if r.status_code == 200:
                return r.json()
            last = RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            if r.status_code in (400, 404):
                break
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(3 * (a + 1))
    raise last


def search_all(cat: str, coll: str, bbox, t0: str, t1: str) -> list[dict]:
    body = {"collections": [coll], "bbox": bbox, "datetime": f"{t0}/{t1}", "limit": 100}
    key = hashlib.sha1(json.dumps([cat, body], sort_keys=True).encode()).hexdigest()
    cf = CACHE / f"{key}.json"
    if cf.exists():
        return json.loads(cf.read_text(encoding="utf-8"))
    feats, url, b, pages = [], CATALOGS[cat], body, 0
    while True:
        j = post(url, b)
        feats += [dict(id=f["id"], geometry=f.get("geometry"), properties=f.get("properties", {}),
                       assets=sorted((f.get("assets") or {}).keys())) for f in j.get("features", [])]
        nxt = [l for l in j.get("links", []) if l.get("rel") == "next"]
        pages += 1
        if not nxt or pages > 30:
            break
        n = nxt[0]
        if n.get("method", "GET").upper() == "POST":
            url, b = n["href"], (n.get("body") or b) if not n.get("merge") else {**b, **(n.get("body") or {})}
        else:
            r = requests.get(n["href"], timeout=90); r.raise_for_status(); j2 = r.json()
            feats += [dict(id=f["id"], geometry=f.get("geometry"), properties=f.get("properties", {}),
                           assets=sorted((f.get("assets") or {}).keys())) for f in j2.get("features", [])]
            nx2 = [l for l in j2.get("links", []) if l.get("rel") == "next"]
            if not nx2:
                break
            url, b = nx2[0]["href"], None
            # GET pagination: loop by GET
            while url:
                r = requests.get(url, timeout=90); r.raise_for_status(); j3 = r.json()
                feats += [dict(id=f["id"], geometry=f.get("geometry"), properties=f.get("properties", {}),
                               assets=sorted((f.get("assets") or {}).keys())) for f in j3.get("features", [])]
                nx3 = [l for l in j3.get("links", []) if l.get("rel") == "next"]
                url = nx3[0]["href"] if nx3 else None
            break
    cf.write_text(json.dumps(feats), encoding="utf-8")
    return feats


def pdt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def main():
    tr = pd.read_csv(ROOT / "data" / "case" / "geometry" / "transects.csv")
    tr = tr[tr.source_id == "S3_SE_NORTH_SEA"].copy()
    tr["cruise"] = tr.event_id.str.extract(r"S3:(HE\d+)_")[0]
    rows, log = [], []
    for cr, g in tr.groupby("cruise"):
        lo = min(g.lon_start.min(), g.lon_end.min()) - 0.6; hi = max(g.lon_start.max(), g.lon_end.max()) + 0.6
        la = min(g.lat_start.min(), g.lat_end.min()) - 0.6; ha = max(g.lat_start.max(), g.lat_end.max()) + 0.6
        t0 = (pd.to_datetime(g.time_start_utc).min() - pd.Timedelta(days=1, hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        t1 = (pd.to_datetime(g.time_end_utc).max() + pd.Timedelta(days=1, hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        bbox = [round(lo, 3), round(la, 3), round(hi, 3), round(ha, 3)]
        for cat, coll, role in COLLS:
            try:
                feats = search_all(cat, coll, bbox, t0, t1)
                err = ""
            except Exception as e:  # noqa: BLE001
                feats, err = [], f"{type(e).__name__}: {str(e)[:200]}"
            log.append(dict(cruise=cr, catalog=cat, collection=coll, role=role, bbox=bbox, t0=t0, t1=t1,
                            n_items=len(feats), error=err))
            print(cr, cat, coll, len(feats), err, flush=True)
            for f in feats:
                p = f["properties"]
                dt_s = p.get("datetime") or p.get("start_datetime")
                if not dt_s or not f.get("geometry"):
                    continue
                t_sc = pdt(dt_s)
                fp = shape(f["geometry"])
                for e in g.itertuples():
                    ts, te = pdt(e.time_start_utc), pdt(e.time_end_utc)
                    mid = ts + (te - ts) / 2
                    # dt to the nearest moment of the transect effort (0 if the scene is during the effort)
                    if t_sc < ts:
                        dt = (t_sc - ts).total_seconds() / 3600
                    elif t_sc > te:
                        dt = (t_sc - te).total_seconds() / 3600
                    else:
                        dt = 0.0
                    if abs(dt) > 24:
                        continue
                    line = LineString([(e.lon_start, e.lat_start), (e.lon_end, e.lat_end)])
                    inside = line.intersection(fp).length / max(line.length, 1e-9) if line.length > 0 else float(fp.contains(line.centroid))
                    near = fp.distance(line)  # degrees; 0 if touches
                    if near > 0.6:
                        continue
                    rows.append(dict(event_id=e.event_id, cruise=cr, catalog=cat, collection=coll, role=role,
                                     scene_id=f["id"], scene_datetime=dt_s, dt_h=round(dt, 2),
                                     dt_mid_h=round((t_sc - mid).total_seconds() / 3600, 2),
                                     line_in_footprint=round(inside, 3), dist_deg=round(near, 3),
                                     cloud_cover=p.get("eo:cloud_cover"),
                                     platform=p.get("platform"), instrument=",".join(p.get("instruments", []) or []),
                                     mode=p.get("sar:instrument_mode"), n_assets=len(f.get("assets", []))))
    inv = pd.DataFrame(rows).sort_values(["event_id", "role", "dt_h"], key=lambda s: s.abs() if s.name == "dt_h" else s)
    inv.to_csv(OUT / "inventory.csv", index=False)
    pd.DataFrame(log).to_csv(OUT / "inventory_queries.csv", index=False)
    print(len(inv), "rows")


if __name__ == "__main__":
    main()
