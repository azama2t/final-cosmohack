"""L117 P4a: list Maxar Open Data (CC-BY-NC-4.0) acquisitions for a given event; footprint, date, GSD.
Usage: python maxar_list.py Libya-Floods-Sept-2023 [lon lat]
Writes data/extra/marine_quantity/maxar/<event>_items.json (metadata only)."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/extra/marine_quantity/maxar"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "https://maxar-opendata.s3.amazonaws.com/events/"


def get(u):
    return requests.get(u, timeout=60).json()


def main():
    ev = sys.argv[1]
    pt = Point(float(sys.argv[2]), float(sys.argv[3])) if len(sys.argv) > 3 else None
    c = get(BASE + ev + "/collection.json")
    acq = [BASE + ev + "/" + l["href"][2:] for l in c["links"] if l["rel"] == "child"]
    items = []

    def acq_items(u):
        a = get(u)
        root = u.rsplit("/", 1)[0] + "/"
        res = []
        for l in a["links"]:
            if l["rel"] != "item":
                continue
            href = l["href"]
            iu = root + href[2:] if href.startswith("./") else (href if href.startswith("http") else root + href)
            res.append(iu)
        return a.get("id"), res

    with ThreadPoolExecutor(6) as ex:
        acqs = list(ex.map(acq_items, acq))
    urls = [(aid, u) for aid, us in acqs for u in us]
    print("acquisitions", len(acqs), "tiles", len(urls), flush=True)

    def item(t):
        aid, u = t
        try:
            it = get(u)
        except Exception as e:
            return None
        p = it["properties"]
        g = shape(it["geometry"])
        return {"acq": aid, "url": u, "id": it["id"], "datetime": p.get("datetime"), "platform": p.get("platform"),
                "gsd": p.get("gsd"), "cloud": p.get("eo:cloud_cover"), "off_nadir": p.get("view:off_nadir"),
                "bounds": g.bounds, "contains_pt": bool(pt and g.contains(pt)),
                "assets": {k: v.get("href") for k, v in it.get("assets", {}).items()}}

    with ThreadPoolExecutor(8) as ex:
        items = [x for x in ex.map(item, urls) if x]
    (OUT / f"{ev}_items.json").write_text(json.dumps(items, indent=1), encoding="utf-8")
    for x in items:
        if pt is None or x["contains_pt"]:
            print(x["acq"], x["id"], x["datetime"], x["platform"], x["gsd"], x["cloud"], [round(b, 3) for b in x["bounds"]], list(x["assets"])[:6])


if __name__ == "__main__":
    main()
