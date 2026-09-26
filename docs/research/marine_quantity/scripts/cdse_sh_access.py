"""L117 §27: what does the CDSE Sentinel Hub OAuth client (keys in .env, never printed) actually give us?

Steps (each writes a small JSON to results/, no secrets):
  collections  - Catalog API: list of collections visible to this client (+ BYOC/CCM probing)
  adis         - Catalog API search of every visible non-S1/S2/S3/S5P/DEM collection over ADIS positive segments
                 (same-day +-24 h) and over the demo scene 30SXE 2021-03-11 (any date + +-3 days)
  process      - Process API: S2 L2A B02/B03/B04/B08 FLOAT32 over a 5 x 5 km window of the demo scene,
                 compared pixel-by-pixel with the Earth Search COG of the same scene (consistency check)
Rules: keys only from .env; no paid orders; <= 50 MB per request; abort if C: free < 40 GB.
"""
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[4]
RES = ROOT / "docs/research/marine_quantity/results"
RAW = ROOT / "data/extra/marine_quantity/cdse_sh"
RAW.mkdir(parents=True, exist_ok=True)
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH = "https://sh.dataspace.copernicus.eu"
DEMO_BOUNDS = [-1.696015, 35.433893, -1.538426, 35.562232]
DEMO_DT = "2021-03-11T11:01:15Z"
_TOKEN = {}


def env():
    out = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def token():
    if _TOKEN.get("exp", 0) > time.time() + 60:
        return _TOKEN["tok"]
    e = env()
    r = requests.post(TOKEN_URL, data={"grant_type": "client_credentials", "client_id": e["CDSE_CLIENT_ID"],
                                       "client_secret": e["CDSE_CLIENT_SECRET"]}, timeout=60)
    if r.status_code != 200:
        raise SystemExit(f"token request failed: HTTP {r.status_code}")  # body not printed (may echo client id)
    j = r.json()
    _TOKEN.update(tok=j["access_token"], exp=time.time() + j.get("expires_in", 300))
    return _TOKEN["tok"]


def hdr():
    return {"Authorization": f"Bearer {token()}"}


def disk_ok():
    free = shutil.disk_usage("C:\\").free / 1e9
    if free < 40:
        raise SystemExit(f"C: free {free:.1f} GB < 40 GB - stop (§21/§27)")
    return free


def get(url, **kw):
    for att in range(5):
        r = requests.get(url, headers=hdr(), timeout=90, **kw)
        if r.status_code == 429:
            time.sleep(5 + 5 * att)
            continue
        return r
    return r


def post(url, **kw):
    for att in range(5):
        r = requests.post(url, headers=hdr(), timeout=120, **kw)
        if r.status_code == 429:
            time.sleep(5 + 5 * att)
            continue
        return r
    return r


def collections():
    cols, url = [], f"{SH}/api/v1/catalog/1.0.0/collections"
    while url:
        r = get(url)
        j = r.json()
        cols += [{"id": c["id"], "title": c.get("title"), "extent": c.get("extent", {}).get("temporal")} for c in j.get("collections", [])]
        nxt = [l["href"] for l in j.get("links", []) if l.get("rel") == "next"]
        url = nxt[0] if nxt and nxt[0] != url else None
    # BYOC collections owned by / shared with this account (CCM is served as BYOC in CDSE Sentinel Hub)
    byoc = get(f"{SH}/api/v1/byoc/collections")
    byoc_ids = []
    try:
        byoc_ids = [{"id": c.get("id"), "name": c.get("name")} for c in byoc.json().get("data", [])]
    except Exception:
        pass
    out = {"catalog_status": r.status_code, "n_collections": len(cols), "collections": cols,
           "byoc_status": byoc.status_code, "byoc": byoc_ids}
    (RES / "cdse_sh_collections.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("catalog", r.status_code, len(cols), [c["id"] for c in cols])
    print("byoc", byoc.status_code, byoc_ids[:20])
    return out


STAC = "https://catalogue.dataspace.copernicus.eu/stac/collections/ccm-optical/items"
ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
ZIP = "https://zipper.dataspace.copernicus.eu/odata/v1/Products({})/$value"


def odata_id(name_contains, extra=""):
    f = f"contains(Name,'{name_contains}'){extra}"
    r = requests.get(ODATA, params={"$filter": f, "$top": 5}, timeout=60)
    v = r.json().get("value", []) if r.status_code == 200 else []
    return [(x["Id"], x["Name"], x.get("ContentLength")) for x in v]


def range_probe(pid):
    """1 KB Range request - tells whether this token is accepted for download; nothing is saved."""
    r = requests.get(ZIP.format(pid), headers={**hdr(), "Range": "bytes=0-1023"}, timeout=60,
                     allow_redirects=True, stream=True)
    n = len(next(r.iter_content(2048), b"")) if r.status_code in (200, 206) else 0
    r.close()
    return {"status": r.status_code, "bytes_read": n}


def probe():
    out = {}
    b = DEMO_BOUNDS
    # (a) demo scene in Sentinel Hub Catalog (S2 L2A)
    body = {"bbox": b, "datetime": "2021-03-10T00:00:00Z/2021-03-12T23:59:59Z", "collections": ["sentinel-2-l2a"], "limit": 10}
    r = post(f"{SH}/api/v1/catalog/1.0.0/search", json=body)
    out["demo_s2l2a_catalog"] = {"status": r.status_code,
                                 "items": [(f["id"], f["properties"].get("datetime"), f["properties"].get("eo:cloud_cover"))
                                           for f in r.json().get("features", [])] if r.status_code == 200 else None}
    # (b) CCM optical over demo bounds (anonymous STAC; whole archive and +-3 days)
    bb = ",".join(map(str, b))
    allc = requests.get(STAC, params={"bbox": bb, "limit": 200}, timeout=90).json().get("features", [])
    out["demo_ccm_any_date"] = sorted({(f["properties"].get("platform") or "?", (f["properties"].get("start_datetime") or "")[:10],
                                        f["properties"].get("gsd")) for f in allc}, key=str)
    near = requests.get(STAC, params={"bbox": bb, "datetime": "2021-03-08T00:00:00Z/2021-03-14T23:59:59Z", "limit": 200},
                        timeout=90).json().get("features", [])
    out["demo_ccm_pm3days"] = len(near)
    # (c) download probes with this OAuth client token (Range 1 KB, not saved)
    s2 = odata_id("S2B_MSIL2A_20210311", " and contains(Name,'T30SXE')")
    out["odata_s2_demo_product"] = [(i, n) for i, n, _ in s2]
    out["download_probe_s2"] = range_probe(s2[0][0]) if s2 else None
    ccm_any = requests.get(STAC, params={"bbox": "9.4,42.8,9.7,42.95", "limit": 5}, timeout=90).json().get("features", [])
    if ccm_any:
        cid = ccm_any[0]["id"]
        cc = odata_id(cid.split(".")[0][:40])
        out["ccm_example"] = {"stac_id": cid, "platform": ccm_any[0]["properties"].get("platform"),
                              "odata": [(i, n) for i, n, _ in cc]}
        out["download_probe_ccm"] = range_probe(cc[0][0]) if cc else None
    (RES / "cdse_sh_probe.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(json.dumps(out, indent=1, default=str)[:3000])


EVAL = """//VERSION=3
function setup(){return {input:[{bands:["B02","B03","B04","B08","dataMask"],units:"REFLECTANCE"}],
 output:{bands:5,sampleType:"FLOAT32"}};}
function evaluatePixel(s){return [s.B02,s.B03,s.B04,s.B08,s.dataMask];}"""
ES_ITEM = "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/S2B_30SXE_20210311_0_L2A"


def process():
    import io
    import rasterio
    from rasterio.warp import transform
    from rasterio.windows import from_bounds
    lon, lat = (DEMO_BOUNDS[0] + DEMO_BOUNDS[2]) / 2, (DEMO_BOUNDS[1] + DEMO_BOUNDS[3]) / 2
    x, y = transform("EPSG:4326", "EPSG:32630", [lon], [lat])
    x0, y0 = round(x[0] / 10) * 10 - 2500, round(y[0] / 10) * 10 - 2500
    bb = [x0, y0, x0 + 5000, y0 + 5000]
    body = {"input": {"bounds": {"bbox": bb, "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/32630"}},
                      "data": [{"type": "sentinel-2-l2a", "dataFilter": {"timeRange": {"from": "2021-03-11T00:00:00Z", "to": "2021-03-11T23:59:59Z"},
                                                                          "mosaickingOrder": "leastCC"}}]},
            "output": {"width": 500, "height": 500, "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}]},
            "evalscript": EVAL}
    t0 = time.time()
    r = post(f"{SH}/api/v1/process", json=body)
    dt = time.time() - t0
    info = {"status": r.status_code, "seconds": round(dt, 1), "bytes": len(r.content),
            "pu_spent": r.headers.get("x-processingunits-spent"), "bbox_utm30n": bb}
    if r.status_code != 200:
        info["error"] = r.text[:300]
        (RES / "cdse_sh_process.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
        print(info)
        return
    with rasterio.open(io.BytesIO(r.content)) as ds:
        sh = ds.read()
    item = requests.get(ES_ITEM, timeout=60).json()
    info["earth_search_item"] = item["id"]
    info["earth_search_processing_baseline"] = item["properties"].get("s2:processing_baseline")
    es = []
    keys = {"B02": "blue", "B03": "green", "B04": "red", "B08": "nir"}
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", AWS_NO_SIGN_REQUEST="YES"):
        for b, k in keys.items():
            a = item["assets"][k]
            with rasterio.open(a["href"]) as ds:
                w = from_bounds(*bb, transform=ds.transform)
                v = ds.read(1, window=w, out_shape=(500, 500)).astype(np.float32)
            rb = (a.get("raster:bands") or [{}])[0]
            es.append(v * rb.get("scale", 1e-4) + rb.get("offset", 0.0))
    es = np.stack(es)
    valid = (sh[4] > 0) & (es[0] > -0.05)
    water = valid & (sh[3] < 0.03)
    per = {}
    for i, b in enumerate(keys):
        a, c = sh[i][valid], es[i][valid]
        aw, cw = sh[i][water], es[i][water]
        per[b] = {"pearson_r": float(np.corrcoef(a, c)[0, 1]), "median_diff_sh_minus_es": float(np.median(a - c)),
                  "mad_abs_diff": float(np.median(np.abs(a - c))),
                  "water_median_sh": float(np.median(aw)) if water.any() else None,
                  "water_median_es": float(np.median(cw)) if water.any() else None}
    info.update({"n_valid": int(valid.sum()), "n_water": int(water.sum()), "bands": per,
                 "note": "Sentinel Hub L2A uses the product it indexes (see demo_s2l2a_catalog: N0500 reprocessed 2023); "
                         "Earth Search item is the original processing (_0) our detector was trained on; nothing saved to disk"})
    (RES / "cdse_sh_process.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    print(json.dumps(info, indent=1))


if __name__ == "__main__":
    free = disk_ok()
    print("C: free GB", round(free, 1))
    step = sys.argv[1] if len(sys.argv) > 1 else "collections"
    if step == "collections":
        collections()
    elif step == "probe":
        probe()
    elif step == "process":
        process()
