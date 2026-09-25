"""L47: vulnerable objects and possible sources from OpenStreetMap (Overpass) for every region in the manifest.

For the bbox of each region (+/- 10 km) one Overpass query fetches aquaculture/farms, beaches, ports/marinas,
protected areas, outfalls/sewage pipelines/wastewater plants, rivers/canals and the coastline. River/canal mouths are
derived: a terminal node of a waterway (last node of a river, either end of a canal) that no other waterway shares and
that lies <= 1.5 km from the OSM coastline (or anywhere, if the bbox has no coastline). Output: simplified GeoJSON
service/context/<region>.geojson, properties: kind, kind_ru, name, osm_id, source "© OpenStreetMap contributors (ODbL)".

Polite use of Overpass: one request at a time, a pause between requests, a User-Agent, raw answers cached in
data_cache/osm/<region>.json (re-used unless --refresh).

    .venv/Scripts/python.exe scripts/fetch_osm_context.py [--regions honduras,bali] [--refresh] [--pause 8]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import requests
from shapely.geometry import LineString, Point, Polygon, box, mapping, shape
from shapely.ops import polygonize, unary_union

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data_cache" / "osm"
OUT = ROOT / "service" / "context"
MANIFEST = ROOT / "service" / "data" / "manifest.json"
ENDPOINTS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
UA = "cosmohack-macroplastic/0.1 (Sentinel-2 floating debris research demo; contact: github azama2t)"
SOURCE = "© OpenStreetMap contributors (ODbL)"
PAD_KM = 10.0
MOUTH_COAST_M = 1500.0
SIMPLIFY_DEG = 0.0002   # ~20 m
KIND_RU = {"aquaculture": "аквакультура / ферма", "beach": "пляж", "port": "порт", "marina": "марина",
           "protected_area": "охраняемая зона", "river_mouth": "устье реки / канала",
           "outfall": "выпуск / трубопровод стоков", "wastewater_plant": "очистные сооружения"}

QUERY = """[out:json][timeout:180][bbox:{s},{w},{n},{e}];
(
  nwr["landuse"="aquaculture"]; nwr["aquaculture"]; nwr["seamark:type"="marine_farm"];
  nwr["natural"="beach"];
  nwr["harbour"]; nwr["landuse"="port"]; nwr["industrial"="port"]; nwr["leisure"="marina"];
  nwr["seamark:type"="harbour"];
  nwr["boundary"="protected_area"]; nwr["leisure"="nature_reserve"]; nwr["boundary"="national_park"];
  nwr["man_made"="outfall"]; nwr["man_made"="wastewater_plant"];
  way["man_made"="pipeline"]["substance"~"sewage|wastewater|waste_water"];
  way["waterway"~"^(river|canal)$"];
  way["natural"="coastline"];
);
out body geom qt;
"""


def padded_bbox(b, km=PAD_KM):
    w, s, e, n = b
    lat0 = (s + n) / 2
    dlat = km / 110.574
    dlon = km / (111.320 * math.cos(math.radians(lat0)))
    return [w - dlon, s - dlat, e + dlon, n + dlat]


def fetch(rid: str, bb, refresh: bool, pause: float) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    fp = CACHE / f"{rid}.json"
    if fp.is_file() and not refresh:
        return json.loads(fp.read_text(encoding="utf-8"))
    w, s, e, n = bb
    q = QUERY.format(s=f"{s:.5f}", w=f"{w:.5f}", n=f"{n:.5f}", e=f"{e:.5f}")
    last = None
    for attempt in range(6):
        url = ENDPOINTS[attempt % len(ENDPOINTS)]
        try:
            r = requests.post(url, data={"data": q}, headers={"User-Agent": UA}, timeout=240)
            if r.status_code == 200:
                js = r.json()
                js["_query"] = {"bbox": bb, "endpoint": url, "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                fp.write_text(json.dumps(js), encoding="utf-8")
                time.sleep(pause)
                return js
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as ex:  # network hiccup -> wait and retry
            last = repr(ex)
        print(f"  [{rid}] attempt {attempt + 1} failed: {last}", flush=True)
        time.sleep(pause * (attempt + 2))
    raise RuntimeError(f"{rid}: overpass failed: {last}")


def classify(t: dict):
    if t.get("landuse") == "aquaculture" or "aquaculture" in t or t.get("seamark:type") == "marine_farm":
        return "aquaculture"
    if t.get("man_made") == "outfall" or t.get("man_made") == "pipeline":
        return "outfall"
    if t.get("man_made") == "wastewater_plant":
        return "wastewater_plant"
    if t.get("leisure") == "marina":
        return "marina"
    if "harbour" in t or t.get("landuse") == "port" or t.get("industrial") == "port" or t.get("seamark:type") == "harbour":
        if t.get("harbour") == "no":
            return None
        return "port"
    if t.get("natural") == "beach":
        return "beach"
    if t.get("boundary") in ("protected_area", "national_park") or t.get("leisure") == "nature_reserve":
        return "protected_area"
    return None


def line_of(el):
    g = el.get("geometry") or []
    pts = [(p["lon"], p["lat"]) for p in g if p]
    return pts


def geom_of(el):
    typ = el["type"]
    if typ == "node":
        return Point(el["lon"], el["lat"])
    if typ == "way":
        pts = line_of(el)
        if len(pts) < 2:
            return None
        closed = len(pts) >= 4 and pts[0] == pts[-1]
        t = el.get("tags") or {}
        if closed and t.get("man_made") != "pipeline" and "waterway" not in t:
            p = Polygon(pts)
            return p if p.is_valid else p.buffer(0)
        return LineString(pts)
    if typ == "relation":
        lines, outers_inner = [], []
        for m in el.get("members") or []:
            if m.get("type") != "way" or not m.get("geometry"):
                continue
            pts = [(p["lon"], p["lat"]) for p in m["geometry"] if p]
            if len(pts) >= 2:
                (lines if m.get("role") != "inner" else outers_inner).append(LineString(pts))
        if not lines:
            return None
        polys = list(polygonize(unary_union(lines)))
        if not polys:
            return unary_union(lines)
        g = unary_union(polys)
        if outers_inner:
            holes = list(polygonize(unary_union(outers_inner)))
            if holes:
                g = g.difference(unary_union(holes))
        return g if g.is_valid else g.buffer(0)
    return None


def m_per_deg(lat):
    return 111320.0 * math.cos(math.radians(lat)), 110574.0


def mouths(elements, bb):
    rivers = [e for e in elements if e["type"] == "way" and (e.get("tags") or {}).get("waterway") in ("river", "canal")]
    coast = [LineString(line_of(e)) for e in elements if e["type"] == "way"
             and (e.get("tags") or {}).get("natural") == "coastline" and len(line_of(e)) >= 2]
    coast_u = unary_union(coast) if coast else None
    node_use: dict[int, int] = {}
    for e in rivers:
        for nid in set(e.get("nodes") or []):
            node_use[nid] = node_use.get(nid, 0) + 1
    lat0 = (bb[1] + bb[3]) / 2
    kx, ky = m_per_deg(lat0)
    out, seen = [], []
    for e in rivers:
        nodes, pts = e.get("nodes") or [], line_of(e)
        if len(nodes) < 2 or len(pts) != len(nodes):
            continue
        t = e.get("tags") or {}
        ends = [-1] if t.get("waterway") == "river" else [0, -1]
        for i in ends:
            if node_use.get(nodes[i], 0) > 1:
                continue
            lon, lat = pts[i]
            d = None
            if coast_u is not None:
                p = Point(lon, lat)
                q = coast_u.interpolate(coast_u.project(p))
                d = math.hypot((q.x - lon) * kx, (q.y - lat) * ky)
                if d > MOUTH_COAST_M:
                    continue
            if any(math.hypot((lon - a) * kx, (lat - b) * ky) < 500 for a, b in seen):
                continue
            seen.append((lon, lat))
            out.append({"geometry": Point(lon, lat), "kind": "river_mouth", "name": t.get("name") or t.get("name:en"),
                        "osm_id": f"way/{e['id']}", "extra": {"waterway": t.get("waterway"),
                                                               "coast_dist_m": None if d is None else round(d)}})
    return out


def build(rid: str, js: dict, bb) -> dict:
    clip = box(*bb)
    feats = []
    for el in js.get("elements", []):
        t = el.get("tags") or {}
        kind = classify(t)
        if not kind:
            continue
        try:
            g = geom_of(el)
        except Exception:
            g = None
        if g is None or g.is_empty:
            continue
        g = g.intersection(clip) if not isinstance(g, Point) else g
        if g.is_empty:
            continue
        if not isinstance(g, Point):
            g = g.simplify(SIMPLIFY_DEG, preserve_topology=True)
            if g.is_empty:
                continue
        name = t.get("name") or t.get("name:en") or t.get("seamark:name")
        feats.append({"geometry": g, "kind": kind, "name": name, "osm_id": f"{el['type']}/{el['id']}", "extra": {}})
    feats += mouths(js.get("elements", []), bb)
    fc = []
    for i, f in enumerate(feats):
        gj = json.loads(json.dumps(mapping(f["geometry"])), parse_float=lambda x: round(float(x), 5))
        props = {"id": f"{rid}_osm_{i}", "kind": f["kind"], "kind_ru": KIND_RU[f["kind"]], "name": f["name"],
                 "osm_id": f["osm_id"], "source": SOURCE}
        props.update(f["extra"])
        fc.append({"type": "Feature", "geometry": gj, "properties": props})
    return {"type": "FeatureCollection", "region": rid, "bbox": [round(x, 5) for x in bb],
            "source": SOURCE, "fetched": (js.get("_query") or {}).get("fetched"),
            "note": "объекты OpenStreetMap (bbox района ± 10 км); устья — концевые узлы рек/каналов ≤ 1,5 км от береговой линии OSM",
            "features": fc}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--regions", default="")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--pause", type=float, default=8.0)
    a = ap.parse_args(argv)
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    want = {x for x in a.regions.split(",") if x}
    OUT.mkdir(parents=True, exist_ok=True)
    total = 0
    failed: list[str] = []
    for reg in man["regions"]:
        rid = reg["id"]
        if want and rid not in want:
            continue
        bb = padded_bbox(reg["bounds"])
        t0 = time.time()
        try:
            js = fetch(rid, bb, a.refresh, a.pause)
        except RuntimeError as ex:  # keep going; re-run later picks up only the missing regions from cache
            print(f"{rid}: SKIPPED ({ex})"[:300], flush=True)
            failed.append(rid)
            continue
        fc = build(rid, js, bb)
        fp = OUT / f"{rid}.geojson"
        fp.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        cnt = {}
        for f in fc["features"]:
            cnt[f["properties"]["kind"]] = cnt.get(f["properties"]["kind"], 0) + 1
        sz = fp.stat().st_size
        total += sz
        print(f"{rid}: {len(fc['features'])} objects {cnt} {sz / 1024:.0f} KB ({time.time() - t0:.1f} s)", flush=True)
    print(f"total {total / 1024 / 1024:.2f} MB; failed: {failed or 'none'}")


if __name__ == "__main__":
    sys.exit(main())
