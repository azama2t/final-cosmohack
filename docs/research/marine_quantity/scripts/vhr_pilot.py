"""L117 §30 p.3: pilot search of HIGH-DETAIL archives (priority <= 0.5 m) over the 20 best field observations.

PRE-REGISTERED (written before any search result was seen, 26.09 ~06:30):
  Targets (20):
    - 10 organizers' CSV events (task/macroplastic_marine_samples.csv x data/pairs/events.csv): per source the densest
      events by max concentration_items_km2 among samples with items > 0; quota S1 GPGP 3, S2 Sargasso 2, S3 North Sea 2,
      S4 Black Sea 3; time_known events first, then density.
    - 10 ADIS segments (data/extra/field/adis/Segments.csv) with n_objects>5cm >= 5, highest dhat_5cm, at most one per
      (ship, UTC date).
  Search box: event point +- 0.3 deg; window: UTC +- 2 days (48 h).
  Drift plausibility of a scene: distance(event point, footprint) <= 0.2 m/s * |dt| + 2 km
    (0.2 m/s = 17 km/day, generous: currents + windage; INBOX §30 cites 0.1 m/s = 8.6 km/day as typical).
  Scene is USABLE for a same-object check only if: drift-plausible AND gsd <= 0.5 m (optical) AND footprint covers
    >= 10 % of the drift circle's WATER area AND cloud <= 30 % (when reported). Anything else is listed with its reason.
  A usable scene is still NOT ground truth of the same patch (drift); it would only be a candidate for manual review.
Sources (all anonymous metadata; nothing is downloaded):
  CDSE CCM STAC ccm-optical + ccm-sar (WV-2/3, GeoEye-1, Pleiades/Neo, SPOT, ... ; data need CCM eligibility: 403 for us, §27)
  Maxar Open Data (55 events, STAC static), OpenAerialMap (/meta), NAIP (Planetary Computer),
  Capella and Umbra open SAR (static STAC by date).
  Sentinel Hub CDSE with our key exposes only S1/S2/S3/S5P/Landsat (§27) - no <= 0.5 m source there, not queried.
Output: results/vhr_pilot_targets.csv, results/vhr_pilot_scenes.csv, results/vhr_pilot_summary.json
"""
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from shapely.geometry import Point, box, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[4]
RES = ROOT / "docs/research/marine_quantity/results"
BOX = 0.3
WIN_H = 48
V_DRIFT = 0.2  # m/s
GSHHS = Path.home() / ".local/share/cartopy/shapefiles/gshhs/i/GSHHS_i_L1.shp"
S = requests.Session()


def jget(url, params=None, tries=4):
    for a in range(tries):
        try:
            r = S.get(url, params=params, timeout=90)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (403, 404):
                return None
        except Exception:
            pass
        time.sleep(2 + 3 * a)
    return None


# ---------------------------------------------------------------- targets
def targets():
    m = pd.read_csv(ROOT / "task/macroplastic_marine_samples.csv", low_memory=False)
    ev = pd.read_csv(ROOT / "data/pairs/events.csv")
    # DEVIATION (recorded 06:40, before any real result was non-zero): "items > 0" excluded all S4 Black Sea events
    # (they report concentration only, items_count empty). Operationalised as concentration_items_km2 > 0 instead.
    m = m[(m.concentration_items_km2.fillna(0) > 0) & m.event_id.notna()]
    g = m.groupby("event_id").agg(dens=("concentration_items_km2", "max"), items=("items_count", "sum"),
                                  area=("sampled_area_km2", "sum")).reset_index()
    g = g.merge(ev[["event_id", "source_id", "lat", "lon", "obs_datetime", "time_known"]], on="event_id")
    quota = {"S1_GPGP2018": 3, "S2_SARGASSO_MSM41": 2, "S3_SE_NORTH_SEA": 2, "S4_BLACK_SEA_DOORS3": 3}
    rows = []
    for src, k in quota.items():
        sub = g[g.source_id == src].sort_values(["time_known", "dens"], ascending=[False, False]).head(k)
        for _, r in sub.iterrows():
            rows.append({"target_id": r.event_id, "kind": "csv_event", "source": src, "lat": r.lat, "lon": r.lon,
                         "t_utc": pd.Timestamp(r.obs_datetime).tz_convert("UTC"), "time_known": bool(r.time_known),
                         "density_items_km2": float(r.dens), "items": float(r["items"]), "area_km2": float(r.area)})
    s = pd.read_csv(ROOT / "data/extra/field/adis/Segments.csv")
    s["t"] = pd.to_datetime(s["timestamp"], format="mixed", utc=True, errors="coerce")
    s = s[(s["n_objects>5cm"] >= 5) & s.t.notna() & s.dhat_5cm.notna()].sort_values("dhat_5cm", ascending=False)
    s["day"] = s.t.dt.date
    s = s.drop_duplicates(["Ship", "day"]).head(10)
    for _, r in s.iterrows():
        rows.append({"target_id": f"ADIS:{int(r.SegmentID)}", "kind": "adis_segment", "source": f"ADIS {r.Ship}",
                     "lat": r.Latitude, "lon": r.Longitude, "t_utc": r.t, "time_known": True,
                     "density_items_km2": float(r.dhat_5cm), "items": float(r["n_objects>5cm"]),
                     "area_km2": float(r.area_scanned_km2)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- geometry helpers
def km_per_deg(lat):
    return 111.32 * math.cos(math.radians(lat)), 110.57


def circle(lat, lon, r_km, n=64):
    kx, ky = km_per_deg(lat)
    a = np.linspace(0, 2 * np.pi, n)
    from shapely.geometry import Polygon
    return Polygon(np.c_[lon + r_km * np.cos(a) / kx, lat + r_km * np.sin(a) / ky])


def dist_km(lat, lon, geom):
    if geom.contains(Point(lon, lat)):
        return 0.0
    kx, ky = km_per_deg(lat)
    from shapely.ops import nearest_points
    p = nearest_points(Point(lon, lat), geom)[1]
    return math.hypot((p.x - lon) * kx, (p.y - lat) * ky)


def area_km2(geom, lat):
    kx, ky = km_per_deg(lat)
    return geom.area * kx * ky


@lru_cache(maxsize=64)
def land(lat_r, lon_r):
    """GSHHS L1 land within 1 deg of the (rounded) point, or empty."""
    try:
        import geopandas as gpd
        g = gpd.read_file(GSHHS, bbox=(lon_r - 1, lat_r - 1, lon_r + 1, lat_r + 1))
        return unary_union(g.geometry.values) if len(g) else None
    except Exception:
        return "unavailable"


def water(geom, lat, lon):
    L = land(round(lat), round(lon))
    if L is None:
        return geom
    if isinstance(L, str):
        return geom
    return geom.difference(L)


# ---------------------------------------------------------------- sources (return list of dicts)
def src_ccm(t):
    out = []
    bb = f"{t.lon-BOX},{t.lat-BOX},{t.lon+BOX},{t.lat+BOX}"
    d0 = (t.t_utc - pd.Timedelta(hours=WIN_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    d1 = (t.t_utc + pd.Timedelta(hours=WIN_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for col in ("ccm-optical", "ccm-sar"):
        j = jget(f"https://catalogue.dataspace.copernicus.eu/stac/collections/{col}/items",
                 {"bbox": bb, "datetime": f"{d0}/{d1}", "limit": 200})
        if j is None:
            out.append({"source": f"CDSE {col}", "error": "query failed"})
            continue
        for f in j.get("features", []):
            p = f["properties"]
            out.append({"source": f"CDSE {col}", "scene_id": f["id"], "platform": p.get("platform"),
                        "datetime": p.get("start_datetime") or p.get("datetime"), "gsd_m": p.get("gsd"),
                        "cloud_pct": p.get("eo:cloud_cover"), "off_nadir_deg": p.get("view:off_nadir") or p.get("view:incidence_angle"),
                        "sun_elev_deg": p.get("view:sun_elevation"), "geometry": f["geometry"], "sensor": "SAR" if col == "ccm-sar" else "optical"})
    return out


_MAXAR = {}


def maxar_collections():
    if _MAXAR:
        return _MAXAR
    c = jget("https://maxar-opendata.s3.amazonaws.com/events/catalog.json")
    for l in c["links"]:
        if l["rel"] != "child":
            continue
        u = "https://maxar-opendata.s3.amazonaws.com/events/" + l["href"].lstrip("./")
        col = jget(u)
        if not col:
            continue
        ext = col.get("extent", {})
        _MAXAR[u] = {"id": col.get("id"), "bboxes": ext.get("spatial", {}).get("bbox", []),
                     "interval": (ext.get("temporal", {}).get("interval") or [[None, None]])[0]}
    return _MAXAR


def src_maxar(t):
    out = []
    q = box(t.lon - BOX, t.lat - BOX, t.lon + BOX, t.lat + BOX)
    for u, c in maxar_collections().items():
        a, b = c["interval"]
        if a and b and (pd.Timestamp(b) < t.t_utc - pd.Timedelta(hours=WIN_H) or pd.Timestamp(a) > t.t_utc + pd.Timedelta(hours=WIN_H)):
            continue
        if not any(box(*bb[:4]).intersects(q) for bb in c["bboxes"]):
            continue
        out.append({"source": "Maxar Open Data", "scene_id": c["id"], "platform": "event collection",
                    "datetime": f"{a}/{b}", "note": "collection overlaps box+window; items to be listed"})
    return out


def src_oam(t):
    d0 = (t.t_utc - pd.Timedelta(hours=WIN_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    d1 = (t.t_utc + pd.Timedelta(hours=WIN_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    j = jget("https://api.openaerialmap.org/meta", {"bbox": f"{t.lon-BOX},{t.lat-BOX},{t.lon+BOX},{t.lat+BOX}",
                                                    "acquisition_from": d0, "acquisition_to": d1, "limit": 100})
    if j is None:
        return [{"source": "OpenAerialMap", "error": "query failed"}]
    return [{"source": "OpenAerialMap", "scene_id": r.get("_id"), "platform": r.get("platform"),
             "datetime": r.get("acquisition_start"), "gsd_m": r.get("gsd"), "geometry": r.get("geojson"), "sensor": "optical"}
            for r in j.get("results", [])]


def src_naip(t):
    body = {"collections": ["naip"], "bbox": [t.lon - BOX, t.lat - BOX, t.lon + BOX, t.lat + BOX],
            "datetime": f"{(t.t_utc - pd.Timedelta(hours=WIN_H)).isoformat()}/{(t.t_utc + pd.Timedelta(hours=WIN_H)).isoformat()}".replace("+00:00", "Z"),
            "limit": 100}
    try:
        r = S.post("https://planetarycomputer.microsoft.com/api/stac/v1/search", json=body, timeout=90)
        j = r.json() if r.status_code == 200 else None
    except Exception:
        j = None
    if j is None:
        return [{"source": "NAIP (PC)", "error": "query failed"}]
    return [{"source": "NAIP (PC)", "scene_id": f["id"], "platform": "aerial", "datetime": f["properties"].get("datetime"),
             "gsd_m": f["properties"].get("gsd"), "geometry": f["geometry"], "sensor": "optical"} for f in j.get("features", [])]


def _walk_items(url, depth=3):
    j = jget(url)
    if not j:
        return []
    if j.get("type") == "Feature":
        return [j]
    base = url.rsplit("/", 1)[0] + "/"
    out = []
    for l in j.get("links", []):
        if l["rel"] in ("item", "child") and depth > 0:
            out += _walk_items(base + l["href"].lstrip("./"), depth - 1)
    return out


@lru_cache(maxsize=512)
def sar_day(provider, day):
    y, m = day[:4], day[:7]
    if provider == "Capella":
        u = (f"https://capella-open-data.s3.us-west-2.amazonaws.com/stac/capella-open-data-by-datetime/capella-open-data-{y}/"
             f"capella-open-data-{m}/capella-open-data-{day}/catalog.json")
    else:
        u = f"https://umbra-open-data-catalog.s3.amazonaws.com/stac/{y}/{m}/{day}/catalog.json"
    return tuple(json.dumps(x) for x in _walk_items(u))


def src_sar(t):
    out = []
    q = box(t.lon - BOX, t.lat - BOX, t.lon + BOX, t.lat + BOX)
    days = pd.date_range((t.t_utc - pd.Timedelta(hours=WIN_H)).normalize(), (t.t_utc + pd.Timedelta(hours=WIN_H)).normalize(), freq="D")
    for prov, years in (("Capella", range(2020, 2027)), ("Umbra", (2024, 2025))):
        for d in days:
            if d.year not in years:
                continue
            for s in sar_day(prov, d.strftime("%Y-%m-%d")):
                f = json.loads(s)
                if f.get("geometry") and shape(f["geometry"]).intersects(q):
                    p = f["properties"]
                    out.append({"source": f"{prov} open SAR", "scene_id": f["id"], "platform": p.get("platform") or prov,
                                "datetime": p.get("datetime") or p.get("start_datetime"),
                                "gsd_m": p.get("gsd") or p.get("sar:resolution_range"), "geometry": f["geometry"], "sensor": "SAR"})
    return out


# ---------------------------------------------------------------- evaluation
def evaluate(t, sc):
    row = {k: sc.get(k) for k in ("source", "scene_id", "platform", "datetime", "gsd_m", "cloud_pct", "off_nadir_deg",
                                   "sun_elev_deg", "sensor", "note", "error")}
    row.update(target_id=t.target_id)
    if sc.get("error") or not sc.get("geometry"):
        row["usable"] = False
        row["reason"] = sc.get("error") or sc.get("note") or "no geometry"
        return row
    g = shape(sc["geometry"])
    dt_h = (pd.Timestamp(str(sc["datetime"]).split("/")[0]).tz_convert("UTC") if pd.Timestamp(str(sc["datetime"]).split("/")[0]).tzinfo
            else pd.Timestamp(str(sc["datetime"]).split("/")[0]).tz_localize("UTC")) - t.t_utc
    dt_h = dt_h.total_seconds() / 3600
    d = dist_km(t.lat, t.lon, g)
    r_km = V_DRIFT * abs(dt_h) * 3.6 + 2.0
    circ = circle(t.lat, t.lon, r_km)
    wc = water(circ, t.lat, t.lon)
    cov = area_km2(g.intersection(wc), t.lat) / max(area_km2(wc, t.lat), 1e-9)
    row.update(dt_h=round(dt_h, 2), dist_km=round(d, 2), drift_radius_km=round(r_km, 1), drift_ok=d <= r_km,
               water_cover_frac=round(cov, 3), water_frac_of_circle=round(area_km2(wc, t.lat) / area_km2(circ, t.lat), 3))
    reasons = []
    if not row["drift_ok"]:
        reasons.append("outside drift radius")
    if sc.get("sensor") == "SAR":
        reasons.append("SAR (not optical; items not countable)")
    if (sc.get("gsd_m") or 99) > 0.5:
        reasons.append(f"gsd {sc.get('gsd_m')} m > 0.5")
    if cov < 0.10:
        reasons.append("covers < 10 % of drift-circle water")
    if sc.get("cloud_pct") is not None and sc["cloud_pct"] > 30:
        reasons.append("cloud > 30 %")
    row["usable"] = not reasons
    row["reason"] = "; ".join(reasons) or "candidate for manual review (not GT of the same patch)"
    return row


CONTROLS = [  # known-positive (lat, lon, UTC) per source - the search must find them, otherwise zeros mean nothing
    ("CTRL:ccm_corsica_pleiades", 42.87, 9.55, "2023-07-20T10:24:00Z"),
    ("CTRL:maxar_derna", 32.765, 22.65, "2023-09-13T09:18:00Z"),
    ("CTRL:naip_tampa", 27.575, -82.66, "2023-01-06T16:00:00Z"),
    ("CTRL:capella_2023-07-03", 2.656, -77.617, "2023-07-03T17:44:00Z"),
    ("CTRL:umbra_2024-09-15", 69.78, -163.04, "2024-09-15T00:08:00Z"),
    ("CTRL:oam_2023-04-05", 51.78, 3.8603, "2023-04-05T16:00:00Z"),
]


def controls():
    T = pd.DataFrame([{"target_id": i, "kind": "control", "source": "control", "lat": la, "lon": lo,
                       "t_utc": pd.Timestamp(t), "time_known": True, "density_items_km2": None, "items": None, "area_km2": None}
                      for i, la, lo, t in CONTROLS])
    maxar_collections()
    out = {}
    for t in T.itertuples():
        res = {}
        for fn in (src_ccm, src_maxar, src_oam, src_naip, src_sar):
            try:
                sc = fn(t)
            except Exception as e:
                sc = [{"error": f"{type(e).__name__}: {e}"[:200]}]
            res[fn.__name__] = {"n": sum(1 for s in sc if s.get("scene_id")), "errors": [s["error"] for s in sc if s.get("error")],
                                "example": next((f"{s.get('platform')} {s.get('datetime')} gsd={s.get('gsd_m')}" for s in sc if s.get("scene_id")), None)}
        out[t.target_id] = res
        print(t.target_id, {k: (v["n"], v["errors"][:1]) for k, v in res.items()}, flush=True)
    (RES / "vhr_pilot_controls.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")


def main():
    import sys
    if "--control" in sys.argv:
        return controls()
    T = targets()
    T.to_csv(RES / "vhr_pilot_targets.csv", index=False)
    print(T[["target_id", "source", "t_utc", "density_items_km2"]].to_string())
    maxar_collections()
    rows, per = [], []

    def one(t):
        scenes = []
        for fn in (src_ccm, src_maxar, src_oam, src_naip, src_sar):
            try:
                scenes += fn(t)
            except Exception as e:  # recorded, not hidden
                scenes.append({"source": fn.__name__, "error": f"{type(e).__name__}: {e}"[:200]})
        return t, scenes

    with ThreadPoolExecutor(4) as ex:
        for t, scenes in ex.map(one, [t for t in T.itertuples()]):
            ev = [evaluate(t, s) for s in scenes]
            rows += ev
            ok = [e for e in ev if not e.get("error") and e.get("scene_id")]
            per.append({"target_id": t.target_id, "n_scenes_any": len(ok),
                        "n_optical_le_0p5m": sum(1 for e in ok if e.get("sensor") == "optical" and (e.get("gsd_m") or 99) <= 0.5),
                        "n_drift_ok": sum(1 for e in ok if e.get("drift_ok")), "n_usable": sum(1 for e in ev if e.get("usable")),
                        "errors": [e["source"] + ": " + str(e["error"]) for e in ev if e.get("error")]})
            print(per[-1], flush=True)
    sc = pd.DataFrame(rows)
    sc.to_csv(RES / "vhr_pilot_scenes.csv", index=False)
    summ = {"n_targets": len(T), "by_kind": T.kind.value_counts().to_dict(), "window_h": WIN_H, "box_deg": BOX, "v_drift_ms": V_DRIFT,
            "targets_with_any_scene": sum(p["n_scenes_any"] > 0 for p in per),
            "targets_with_optical_le_0p5m": sum(p["n_optical_le_0p5m"] > 0 for p in per),
            "targets_with_usable_scene": sum(p["n_usable"] > 0 for p in per),
            "maxar_collections_checked": len(_MAXAR), "per_target": per}
    (RES / "vhr_pilot_summary.json").write_text(json.dumps(summ, indent=1, default=str), encoding="utf-8")
    print({k: v for k, v in summ.items() if k != "per_target"})


if __name__ == "__main__":
    main()
