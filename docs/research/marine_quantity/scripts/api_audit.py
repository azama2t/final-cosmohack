"""L117 P1: real calls to satellite / imagery APIs WITHOUT credentials (none exist in env).

For every service: one catalogue call (does search work anonymously?) and, where relevant,
one data/auth call (is download/processing gated?). Records HTTP status, latency, what came back.
Test point: ADIS segment 335694 (Gleb pair, 2023-03-24, S2 same day) + one US coastal point for NAIP/NOAA.
No keys are read or printed. Output: docs/research/marine_quantity/results/api_audit.json
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs/research/marine_quantity/results/api_audit.json"
RAW = ROOT / "data/extra/marine_quantity/api_raw"
RAW.mkdir(parents=True, exist_ok=True)

# ADIS 335694 route centre (from Gleb concrete_examples / selected_routes) - approx; refined below from csv
LON, LAT = None, None
DATE = "2023-03-24"
UA = {"User-Agent": "cosmohack-L117-research/1.0"}


def adis_point():
    import csv
    p = ROOT / "data/extra/field/adis/Segments.csv"
    with p.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["SegmentID"] == "335694":
                return float(r["Longitude"]), float(r["Latitude"]), r["mindate"]
    return 17.9, 40.5, None


def call(name, method, url, **kw):
    t0 = time.time()
    rec = {"name": name, "method": method, "url": url}
    try:
        r = requests.request(method, url, timeout=kw.pop("timeout", 30), headers={**UA, **kw.pop("headers", {})}, **kw)
        rec["status"] = r.status_code
        rec["ms"] = int((time.time() - t0) * 1000)
        ct = r.headers.get("content-type", "")
        rec["content_type"] = ct
        body = r.content[:400000]
        (RAW / f"{name}.bin").write_bytes(body)
        if "json" in ct or body[:1] in (b"{", b"["):
            try:
                rec["_json"] = r.json()
            except Exception:
                rec["snippet"] = body[:300].decode("utf-8", "replace")
        else:
            rec["snippet"] = body[:300].decode("utf-8", "replace")
    except Exception as e:
        rec["status"] = None
        rec["error"] = repr(e)[:300]
        rec["ms"] = int((time.time() - t0) * 1000)
    return rec


def summarize_stac(rec):
    j = rec.pop("_json", None)
    if not isinstance(j, dict):
        return rec
    feats = j.get("features")
    if feats is not None:
        rec["n_features"] = len(feats)
        rec["items"] = [
            {
                "id": f.get("id"),
                "datetime": f.get("properties", {}).get("datetime"),
                "gsd": f.get("properties", {}).get("gsd"),
                "cloud": f.get("properties", {}).get("eo:cloud_cover"),
                "platform": f.get("properties", {}).get("platform"),
            }
            for f in feats[:5]
        ]
    elif "collections" in j:
        rec["n_collections"] = len(j["collections"])
        rec["collection_ids"] = [c.get("id") for c in j["collections"]][:200]
    elif "links" in j:
        rec["child_links"] = [l.get("href") for l in j["links"] if l.get("rel") in ("child", "item")][:300]
        rec["n_child_links"] = len([l for l in j["links"] if l.get("rel") in ("child", "item")])
    else:
        rec["keys"] = list(j)[:30]
        rec["json_head"] = json.dumps(j)[:600]
    return rec


def main():
    lon, lat, md = adis_point()
    d0, d1 = f"{DATE}T00:00:00Z", f"{DATE}T23:59:59Z"
    pt = {"type": "Point", "coordinates": [lon, lat]}
    bbox = [lon - 0.05, lat - 0.05, lon + 0.05, lat + 0.05]
    stac_body = {"intersects": pt, "datetime": f"{d0}/{d1}", "limit": 10}
    # US coastal point (Tampa Bay area, FL) for NAIP / NOAA
    us_pt = {"type": "Point", "coordinates": [-82.63, 27.62]}
    res = []

    # --- free, anonymous STAC catalogues
    res.append(call("earthsearch_collections", "GET", "https://earth-search.aws.element84.com/v1/collections"))
    res.append(call("earthsearch_s2l2a_adis335694", "POST", "https://earth-search.aws.element84.com/v1/search",
                    json={**stac_body, "collections": ["sentinel-2-l2a"]}))
    res.append(call("earthsearch_s1grd_adis335694", "POST", "https://earth-search.aws.element84.com/v1/search",
                    json={**stac_body, "collections": ["sentinel-1-grd"],
                          "datetime": "2023-03-21T00:00:00Z/2023-03-27T23:59:59Z"}))
    res.append(call("pc_collections", "GET", "https://planetarycomputer.microsoft.com/api/stac/v1/collections"))
    res.append(call("pc_s2l2a_adis335694", "POST", "https://planetarycomputer.microsoft.com/api/stac/v1/search",
                    json={**stac_body, "collections": ["sentinel-2-l2a"]}))
    res.append(call("pc_s1rtc_adis335694", "POST", "https://planetarycomputer.microsoft.com/api/stac/v1/search",
                    json={**stac_body, "collections": ["sentinel-1-grd"],
                          "datetime": "2023-03-21T00:00:00Z/2023-03-27T23:59:59Z"}))
    res.append(call("pc_naip_us_coast", "POST", "https://planetarycomputer.microsoft.com/api/stac/v1/search",
                    json={"intersects": us_pt, "collections": ["naip"], "limit": 5}))
    res.append(call("pc_sas_token_s2", "GET", "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-2-l2a"))
    # Copernicus Data Space Ecosystem
    res.append(call("cdse_stac_collections", "GET", "https://catalogue.dataspace.copernicus.eu/stac/collections"))
    res.append(call("cdse_stac_s2_adis335694", "GET",
                    "https://catalogue.dataspace.copernicus.eu/stac/collections/SENTINEL-2/items",
                    params={"bbox": ",".join(map(str, bbox)), "datetime": f"{d0}/{d1}", "limit": 5}))
    res.append(call("cdse_odata_s2_adis335694", "GET", "https://catalogue.dataspace.copernicus.eu/odata/v1/Products",
                    params={"$filter": f"Collection/Name eq 'SENTINEL-2' and OData.CSC.Intersects(area=geography'SRID=4326;POINT({lon} {lat})') and ContentDate/Start gt {d0[:-1]}.000Z and ContentDate/Start lt {d1[:-1]}.000Z",
                            "$top": 5}))
    res.append(call("cdse_download_noauth", "GET", "https://zipper.dataspace.copernicus.eu/odata/v1/Products(00000000-0000-0000-0000-000000000000)/$value", allow_redirects=False))
    res.append(call("cdse_ccm_vhr_collections", "GET", "https://catalogue.dataspace.copernicus.eu/odata/v1/Products",
                    params={"$filter": f"Collection/Name eq 'CCM' and OData.CSC.Intersects(area=geography'SRID=4326;POINT({lon} {lat})')", "$top": 5}))
    # Sentinel Hub (commercial + CDSE instance)
    res.append(call("sentinelhub_catalog_noauth", "POST", "https://services.sentinel-hub.com/api/v1/catalog/1.0.0/search",
                    json={**stac_body, "collections": ["sentinel-2-l2a"]}))
    res.append(call("cdse_sentinelhub_catalog_noauth", "POST", "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search",
                    json={**stac_body, "collections": ["sentinel-2-l2a"]}))
    # Planet
    res.append(call("planet_data_quicksearch_noauth", "POST", "https://api.planet.com/data/v1/quick-search",
                    json={"item_types": ["PSScene", "SkySatCollect"], "filter": {"type": "AndFilter", "config": [
                        {"type": "GeometryFilter", "field_name": "geometry", "config": pt},
                        {"type": "DateRangeFilter", "field_name": "acquired", "config": {"gte": d0, "lte": d1}}]}}))
    res.append(call("planet_basemaps_noauth", "GET", "https://api.planet.com/basemaps/v1/mosaics"))
    # Google Earth Engine REST
    res.append(call("gee_rest_noauth", "GET", "https://earthengine.googleapis.com/v1/projects/earthengine-public/assets/COPERNICUS/S2_SR_HARMONIZED"))
    # NASA CMR (HLS 30 m, Landsat) - search anonymous, download needs Earthdata login
    res.append(call("nasa_cmr_hls_adis335694", "GET", "https://cmr.earthdata.nasa.gov/search/granules.json",
                    params={"short_name": "HLSS30", "point": f"{lon},{lat}", "temporal": f"{d0},{d1}", "page_size": 5}))
    res.append(call("nasa_lpdaac_download_noauth", "GET",
                    "https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-protected/HLSS30.020/", allow_redirects=False))
    # Free VHR / open data programmes
    res.append(call("maxar_opendata_catalog", "GET", "https://maxar-opendata.s3.amazonaws.com/events/catalog.json"))
    res.append(call("capella_opendata_catalog", "GET", "https://capella-open-data.s3.us-west-2.amazonaws.com/stac/catalog.json"))
    res.append(call("umbra_opendata_listing", "GET", "https://umbra-open-data-catalog.s3.amazonaws.com/?list-type=2&max-keys=20&delimiter=/"))
    res.append(call("satellogic_earthview_listing", "GET", "https://satellogic-earthview.s3.us-west-2.amazonaws.com/?list-type=2&max-keys=20&delimiter=/"))
    res.append(call("noaa_eri_listing", "GET", "https://noaa-eri-pds.s3.amazonaws.com/?list-type=2&max-keys=50&delimiter=/"))
    res.append(call("openaerialmap_meta_us_coast", "GET", "https://api.openaerialmap.org/meta",
                    params={"bbox": "-83.0,27.0,-82.0,28.5", "limit": 5}))
    # Commercial aggregators (catalog needs account)
    res.append(call("up42_catalog_noauth", "POST", "https://api.up42.com/catalog/hosts/oneatlas/stac/search", json=stac_body))
    res.append(call("skywatch_noauth", "GET", "https://api.skywatch.co/earthcache/archive/search"))

    for r in res:
        summarize_stac(r)
        r.pop("_json", None)
    meta = {"generated_utc": datetime.now(timezone.utc).isoformat(), "credentials_used": "none (no keys in env/.env)",
            "adis_335694_point": [lon, lat], "adis_335694_mindate": md, "date": DATE, "results": res}
    OUT.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in res:
        extra = r.get("n_features", r.get("n_collections", r.get("n_child_links", "")))
        print(f"{r['name']:40s} {r.get('status')} {r.get('ms')}ms {extra} {r.get('error','')[:80]}")


if __name__ == "__main__":
    main()
