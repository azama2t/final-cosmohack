"""L98 part B: Landsat re-check for S2_SARGASSO_MSM41 (Gutow et al. 2021, cruise MSM41, April 2015).

For every event with known time: STAC search +-24 h around the transect interval
in PC landsat-c2-l2, PC landsat-c2-l1, Earth Search landsat-c2-l2; intersects =
transect line (start->end) buffered 2 km. Controls: Bermuda point and wider
+-8 day windows. For hits: crop 6x6 km, RGB + FDI-like png, usable_frac from QA_PIXEL.

Outputs: data/search/s2/candidates.csv, data/search/s2/hits_all.csv,
data/search/s2/controls.csv, data/search/s2/crops/<event>/, cache in data/search/s2/cache/.
Run: CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/search/s2_sargasso_landsat.py
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

import numpy as np
import pandas as pd
import requests
from pyproj import Transformer
from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import transform

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "search" / "s2"
CACHE = OUT / "cache"
CROPS = OUT / "crops"
for p in (OUT, CACHE, CROPS):
    p.mkdir(parents=True, exist_ok=True)

ENDPOINTS = {
    "pc:landsat-c2-l2": ("https://planetarycomputer.microsoft.com/api/stac/v1/search", "landsat-c2-l2"),
    "pc:landsat-c2-l1": ("https://planetarycomputer.microsoft.com/api/stac/v1/search", "landsat-c2-l1"),
    "es:landsat-c2-l2": ("https://earth-search.aws.element84.com/v1/search", "landsat-c2-l2"),
}
BUF_M = 2000.0
WIN_H = 24.0


def load_events() -> pd.DataFrame:
    d = pd.read_csv(ROOT / "task" / "macroplastic_marine_samples.csv")
    d = d[d.source_id == "S2_SARGASSO_MSM41"]
    rows = []
    for ev, g in d.groupby("event_id", sort=False):
        r = g.iloc[0]
        t0 = pd.Timestamp(r.datetime_start_iso) if isinstance(r.datetime_start_iso, str) else pd.NaT
        t1 = pd.NaT
        if pd.notna(t0) and isinstance(r.time_end_utc, str):
            t1 = pd.Timestamp(f"{str(t0.date())}T{r.time_end_utc}Z")
            if t1 < t0:
                t1 += pd.Timedelta(days=1)
        tm = t0 + (t1 - t0) / 2 if pd.notna(t1) else t0
        rows.append(dict(event_id=ev, lat=r.latitude, lon=r.longitude, lat_start=r.lat_start,
                         lon_start=r.lon_start, lat_end=r.lat_end, lon_end=r.lon_end,
                         t_start=t0, t_end=t1 if pd.notna(t1) else t0, t_mid=tm,
                         length_km=r.transect_length_km, width_m=r.transect_width_m))
    return pd.DataFrame(rows)


def geom_for(ev) -> tuple:
    """Return (lonlat geometry of transect/point, lonlat buffered search polygon, local aeqd transformers)."""
    if all(pd.notna([ev.lat_start, ev.lon_start, ev.lat_end, ev.lon_end])):
        g = LineString([(ev.lon_start, ev.lat_start), (ev.lon_end, ev.lat_end)])
    else:
        g = Point(ev.lon, ev.lat)
    c = g.centroid
    crs = f"+proj=aeqd +lat_0={c.y} +lon_0={c.x} +datum=WGS84 +units=m"
    fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
    inv = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
    buf = transform(inv, transform(fwd, g).buffer(BUF_M))
    return g, buf


def stac_search(key: str, geom: dict, t0: datetime, t1: datetime) -> list[dict]:
    url, coll = ENDPOINTS[key]
    body = {"collections": [coll], "intersects": geom,
            "datetime": f"{t0.strftime('%Y-%m-%dT%H:%M:%SZ')}/{t1.strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "limit": 100}
    h = hashlib.sha1((key + json.dumps(body, sort_keys=True)).encode()).hexdigest()[:16]
    cf = CACHE / f"{key.replace(':', '_')}_{h}.json"
    if cf.exists():
        return json.loads(cf.read_text())["features"]
    feats, nxt, tries = [], body, 0
    while True:
        try:
            r = requests.post(url, json=nxt, timeout=60)
            r.raise_for_status()
        except Exception:
            tries += 1
            if tries > 8:
                raise
            time.sleep(min(5 * tries, 30))
            continue
        js = r.json()
        feats += js.get("features", [])
        link = next((l for l in js.get("links", []) if l.get("rel") == "next"), None)
        if not link or not js.get("features"):
            break
        nxt = link.get("body") or {**body, **({"token": link["href"].split("token=")[-1]} if "token=" in link.get("href", "") else {})}
        if nxt == body:
            break
    cf.write_text(json.dumps({"request": body, "url": url, "features": feats}))
    return feats


def hit_record(key, f, ev, g):
    p = f["properties"]
    ts = pd.Timestamp(p["datetime"])
    fp = shape(f["geometry"])
    return dict(event_id=ev.event_id, collection=key, scene_id=f["id"],
                sensor=p.get("platform", "") + "/" + ",".join(p.get("instruments", []) or []),
                scene_datetime=ts.isoformat(), dt_h=round((ts - ev.t_mid).total_seconds() / 3600, 2),
                cloud_cover=p.get("eo:cloud_cover"), point_inside=fp.contains(Point(ev.lon, ev.lat)),
                transect_inside=fp.contains(g), transect_intersects=fp.intersects(g),
                wrs=f"{p.get('landsat:wrs_path','')}/{p.get('landsat:wrs_row','')}")


def search_events(ev_df):
    jobs = []
    for _, ev in ev_df.iterrows():
        if pd.isna(ev.t_mid):
            continue
        g, buf = geom_for(ev)
        t0 = (ev.t_start - pd.Timedelta(hours=WIN_H)).to_pydatetime()
        t1 = (ev.t_end + pd.Timedelta(hours=WIN_H)).to_pydatetime()
        for key in ENDPOINTS:
            jobs.append((key, ev, g, mapping(buf), t0, t1))
    res = []
    def run(j):
        try:
            return j, stac_search(j[0], j[3], j[4], j[5])
        except Exception as e:  # recorded, reported, never silently counted as 0
            return j, e
    with ThreadPoolExecutor(3) as ex:
        outs = list(ex.map(run, jobs))
    n_q = {k: 0 for k in ENDPOINTS}
    errs = []
    for (key, ev, g, _, _, _), feats in outs:
        if isinstance(feats, Exception):
            errs.append((key, ev.event_id, repr(feats)[:200]))
            continue
        n_q[key] += 1
        for f in feats:
            res.append(hit_record(key, f, ev, g))
    if errs:
        print('QUERY ERRORS', len(errs)); [print(e) for e in errs]
        pd.DataFrame(errs, columns=['collection','event_id','error']).to_csv(OUT / 'query_errors.csv', index=False)
    return pd.DataFrame(res), n_q


def controls(ev_df):
    rows = []
    bermuda = mapping(Point(-64.8, 32.3).buffer(0.02))
    t0 = datetime(2015, 3, 31, tzinfo=timezone.utc)
    t1 = datetime(2015, 4, 28, tzinfo=timezone.utc)
    for key in ENDPOINTS:
        feats = stac_search(key, bermuda, t0, t1)
        rows.append(dict(control="Bermuda 32.3N -64.8W, 2015-03-31..2015-04-28", collection=key,
                         n_hits=len(feats), ids=";".join(sorted(f["id"] for f in feats))))
    # wider +-8 day windows for a sample of events (first, and every 10th)
    sample = ev_df.iloc[::10]
    for _, ev in sample.iterrows():
        g, buf = geom_for(ev)
        a = (ev.t_start - pd.Timedelta(days=8)).to_pydatetime()
        b = (ev.t_end + pd.Timedelta(days=8)).to_pydatetime()
        for key in ENDPOINTS:
            feats = stac_search(key, mapping(buf), a, b)
            rows.append(dict(control=f"{ev.event_id} +-8 d", collection=key, n_hits=len(feats),
                             ids=";".join(sorted(f["id"] for f in feats))))
    # whole cruise bbox, whole month
    lons = pd.concat([ev_df.lon_start, ev_df.lon_end]); lats = pd.concat([ev_df.lat_start, ev_df.lat_end])
    box = mapping(Point(0, 0).buffer(1).envelope)
    from shapely.geometry import box as sbox
    box = mapping(sbox(lons.min(), lats.min(), lons.max(), lats.max()))
    for key in ENDPOINTS:
        feats = stac_search(key, box, t0, t1)
        rows.append(dict(control=f"cruise bbox {lons.min():.2f},{lats.min():.2f},{lons.max():.2f},{lats.max():.2f}, 2015-03-31..04-28",
                         collection=key, n_hits=len(feats), ids=";".join(sorted(f["id"] for f in feats))))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    ev = load_events()
    ev.to_csv(OUT / "events.csv", index=False)
    print("events", len(ev), "with time", int(ev.t_mid.notna().sum()))
    hits, nq = search_events(ev)
    print("queries", nq)
    hits.to_csv(OUT / "hits_all.csv", index=False)
    print("hits", len(hits), hits.groupby("collection").size().to_dict() if len(hits) else {})
    ctl = controls(ev)
    ctl.to_csv(OUT / "controls.csv", index=False)
    print(ctl[["control", "collection", "n_hits"]].to_string())


# ---------------------------------------------------------------- stage 2: crops, QA, levels
def find_item(scene_id: str) -> dict | None:
    for cf in CACHE.glob("pc_landsat-c2-l2_*.json"):
        for f in json.loads(cf.read_text())["features"]:
            if f["id"] == scene_id:
                return f
    return None


def crop_and_qa(ev, scene_id: str) -> dict:
    import planetary_computer as pcm
    import rasterio
    from rasterio.features import rasterize
    from rasterio.windows import from_bounds
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    item = find_item(scene_id)
    a = item["assets"]
    href = {k: pcm.sign(a[k]["href"]) for k in ("red", "green", "blue", "nir08", "swir16", "qa_pixel")}
    g, _ = geom_for(ev)
    out = CROPS / ev.event_id.replace(":", "_")
    out.mkdir(parents=True, exist_ok=True)
    res = {}
    with rasterio.open(href["qa_pixel"]) as src:
        to_utm = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True).transform
        gu = transform(to_utm, g)
        mid = gu.interpolate(0.5, normalized=True)
        strip = gu.buffer(45.0)          # pixels touched by the 10 m strip (30 m grid)
        buf1k = gu.buffer(1000.0)
        minx, miny, maxx, maxy = buf1k.buffer(500).bounds
        win = from_bounds(minx, miny, maxx, maxy, src.transform).round_offsets().round_lengths()
        qa = src.read(1, window=win, boundless=True, fill_value=1)
        wt = src.window_transform(win)
    shp = qa.shape
    m_strip = rasterize([strip], out_shape=shp, transform=wt, all_touched=True).astype(bool)
    m_1k = rasterize([buf1k], out_shape=shp, transform=wt).astype(bool)
    fill = (qa & 1) > 0
    cloudy = (qa & 0b11110) > 0          # bits 1 dilated, 2 cirrus, 3 cloud, 4 shadow
    water = (qa & (1 << 7)) > 0
    usable = ~fill & ~cloudy & water
    res["n_strip_px"] = int(m_strip.sum())
    res["usable_frac"] = round(float(usable[m_strip].mean()), 4)
    res["usable_frac_1km"] = round(float(usable[m_1k].mean()), 4)
    res["cloud_frac_strip"] = round(float((cloudy & ~fill)[m_strip].mean()), 4)
    res["fill_frac_strip"] = round(float(fill[m_strip].mean()), 4)
    res["water_bit_frac_strip"] = round(float(water[m_strip].mean()), 4)
    # SR sanity over usable strip pixels (clear open ocean: blue SR ~0.03-0.06, red >0)
    for k in ("blue", "red", "nir08", "swir16"):
        with rasterio.open(href[k]) as s:
            arr = s.read(1, window=win, boundless=True, fill_value=0) * 2.75e-5 - 0.2
        sel = arr[m_strip & usable]
        res[f"sr_{k}_median_usable"] = round(float(np.median(sel)), 4) if sel.size else None
    for tag, bounds in (("6km", (mid.x - 3000, mid.y - 3000, mid.x + 3000, mid.y + 3000)),
                        ("transect", (minx, miny, maxx, maxy))):
        bands = {}
        for k in ("red", "green", "blue", "nir08", "swir16", "qa_pixel"):
            with rasterio.open(href[k]) as s:
                w = from_bounds(*bounds, s.transform).round_offsets().round_lengths()
                arr = s.read(1, window=w, boundless=True, fill_value=0)
                wt2 = s.window_transform(w)
            bands[k] = arr if k == "qa_pixel" else np.where(arr == 0, np.nan, arr * 2.75e-5 - 0.2)
        rgb = np.dstack([bands["red"], bands["green"], bands["blue"]])
        lo, hi = np.nanpercentile(rgb, 2), np.nanpercentile(rgb, 98)
        rgb8 = np.nan_to_num(np.clip((rgb - lo) / (hi - lo + 1e-9), 0, 1))
        r, n, s1 = bands["red"], bands["nir08"], bands["swir16"]
        fdi = n - (r + (s1 - r) * ((865 - 655) / (1609 - 655)) * 10)
        ext = [wt2.c, wt2.c + wt2.a * rgb.shape[1], wt2.f + wt2.e * rgb.shape[0], wt2.f]
        cld = ((bands["qa_pixel"] & 0b11110) > 0).astype(float)
        fig, ax = plt.subplots(1, 3, figsize=(15, 5.4) if tag == "6km" else (15, 4))
        ax[0].imshow(rgb8, extent=ext); ax[0].set_title(f"RGB (B4,B3,B2) {tag}")
        v = np.nanpercentile(np.abs(fdi), 98) if np.isfinite(fdi).any() else 0.01
        im = ax[1].imshow(fdi, extent=ext, cmap="RdBu_r", vmin=-v, vmax=v); ax[1].set_title("FDI-like (B5,B4,B6)")
        plt.colorbar(im, ax=ax[1], fraction=0.04)
        ax[2].imshow(cld, extent=ext, cmap="gray", vmin=0, vmax=1); ax[2].set_title("QA cloud/shadow/cirrus/dilated")
        for a_ in ax:
            x, y = gu.xy
            a_.plot(x, y, "y-", lw=1)
            a_.set_xlim(ext[0], ext[1]); a_.set_ylim(ext[2], ext[3])
        fig.suptitle(f"{ev.event_id} | {scene_id} | usable_frac strip={res['usable_frac']}")
        fig.tight_layout(); fig.savefig(out / f"{scene_id}_{tag}.png", dpi=90); plt.close(fig)
    return res


def nearest_misses(ev_df, ctl):
    """Scenes of the cruise-bbox month control vs events: events inside footprint (min |dt|), nearest event in time."""
    rows, feats = [], {}
    for cf in CACHE.glob("pc_landsat-c2-l2_*.json"):
        for f in json.loads(cf.read_text())["features"]:
            feats[f["id"]] = f
    bbox_ids = ctl[ctl.control.str.startswith("cruise bbox") & (ctl.collection == "pc:landsat-c2-l2")].ids.iloc[0].split(";")
    geoms = {e.event_id: geom_for(e)[0] for _, e in ev_df.iterrows()}
    for sid in bbox_ids:
        f = feats[sid]
        fp = shape(f["geometry"])
        ts = pd.Timestamp(f["properties"]["datetime"])
        best = None
        dts = []
        for _, e in ev_df.iterrows():
            dt = (ts - e.t_mid).total_seconds() / 3600
            dts.append((dt, e.event_id, fp.distance(geoms[e.event_id]) * 111))
            if fp.intersects(geoms[e.event_id]) and (best is None or abs(dt) < abs(best[1])):
                best = (e.event_id, dt)
        nt = min(dts, key=lambda x: abs(x[0]))
        rows.append(dict(scene_id=sid, scene_datetime=ts.isoformat(), cloud=f["properties"].get("eo:cloud_cover"),
                         event_in_footprint=best[0] if best else "",
                         dt_h_in_footprint=round(best[1], 1) if best else None,
                         nearest_time_event=nt[1], nearest_time_dt_h=round(nt[0], 1),
                         nearest_time_dist_km_approx=round(nt[2], 0)))
    return pd.DataFrame(rows)


def stage2():
    ev_df = pd.read_csv(OUT / "events.csv", parse_dates=["t_start", "t_end", "t_mid"])
    hits = pd.read_csv(OUT / "hits_all.csv")
    ctl = pd.read_csv(OUT / "controls.csv")
    nm = nearest_misses(ev_df, ctl)
    nm.to_csv(OUT / "bbox_month_scenes_vs_events.csv", index=False)
    print(nm.to_string())
    qa_rows, cand = [], []
    uniq = hits.drop_duplicates(["event_id", "scene_id"])
    for _, h in uniq.iterrows():
        ev = ev_df[ev_df.event_id == h.event_id].iloc[0]
        cols = ",".join(sorted(hits[(hits.event_id == h.event_id) & (hits.scene_id == h.scene_id)].collection))
        q = crop_and_qa(ev, h.scene_id)
        qa_rows.append({**h.to_dict(), **q, "found_in": cols})
        reasons = []
        if not h.transect_intersects:
            reasons.append("transect outside footprint")
        if abs(h.dt_h) > 24:
            reasons.append(f"|dt|={abs(h.dt_h):.1f} h > 24 h")
        if q["usable_frac"] < 0.3:
            reasons.append(f"usable_frac {q['usable_frac']:.2f} < 0.30 (QA cloud/shadow/cirrus in strip "
                           f"{q['cloud_frac_strip']:.2f}, scene cloud {h.cloud_cover}%)")
        level = "D" if reasons else "C"
        if level == "C":
            cav = []
            if q.get("sr_blue_median_usable") is not None and (q["sr_blue_median_usable"] < 0.01 or q["sr_red_median_usable"] < 0):
                cav.append(f"SR implausible for clear ocean (blue {q['sr_blue_median_usable']}, red {q['sr_red_median_usable']}) -> haze/over-correction")
            if "_T2" in h.scene_id:
                cav.append("Tier 2 scene")
            if h.cloud_cover and h.cloud_cover > 60:
                cav.append(f"scene cloud {h.cloud_cover}%")
            if cav:
                reasons.append("caveats: " + ", ".join(cav))
            src = pd.read_csv(ROOT / "task" / "macroplastic_marine_samples.csv")
            dens = src[(src.event_id == h.event_id) & (src.target_scope == "total_plastic")].concentration_items_km2.iloc[0]
            reasons.append(f"QA-clear water over transect usable_frac {q['usable_frac']:.2f}, dt {h.dt_h:+.1f} h; "
                           f"not A: 30 m pixel = 900 m2 vs {dens:.1f} items/km2 (>2 cm) -> {dens * 9e-4:.3f} items/pixel, "
                           "item area <<1% of pixel; ship strip 10 m < pixel; " f"{h.dt_h:+.0f} h drift")
        cand.append(dict(event_id=h.event_id, scene_id=h.scene_id, sensor=h.sensor, dt_h=h.dt_h,
                         usable_frac=q["usable_frac"], level=level,
                         reason="; ".join(reasons) + f" [found in {cols}]"))
    pd.DataFrame(qa_rows).to_csv(OUT / "hits_qa.csv", index=False)
    for _, ev in ev_df.iterrows():
        if ev.event_id not in set(uniq.event_id):
            cand.append(dict(event_id=ev.event_id, scene_id="", sensor="", dt_h="", usable_frac="", level="D",
                             reason="no Landsat scene ±24 h (control query OK)"))
    c = pd.DataFrame(cand, columns=["event_id", "scene_id", "sensor", "dt_h", "usable_frac", "level", "reason"])
    c.to_csv(OUT / "candidates.csv", index=False)
    print(c.level.value_counts().to_dict())
    print(pd.DataFrame(qa_rows).T.to_string())


if __name__ == "__main__" and os.environ.get("S2_STAGE2", "1") == "1":
    stage2()
