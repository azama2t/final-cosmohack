"""L117 P3b: sparsity control for P3 - for a random sample of 150 ADIS positive (date, 0.5 deg) groups,
how many CCM optical items exist over the group box at ANY date (whole archive)? Shows whether 0 same-day matches
is due to timing or to the CCM archive simply not covering open-ocean ADIS routes.
Output: results/vhr_ccm_any_date.json"""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parents[4]
RES = ROOT / "docs/research/marine_quantity/results"
URL = "https://catalogue.dataspace.copernicus.eu/stac/collections/ccm-optical/items"


def main():
    s = pd.read_csv(ROOT / "data/extra/field/adis/Segments.csv")
    s = s[s["n_objects>5cm"] > 0].copy()
    s["clat"] = (s.Latitude * 2).round() / 2
    s["clon"] = (s.Longitude * 2).round() / 2
    cells = s.groupby(["clat", "clon"]).agg(n=("SegmentID", "size"), lat=("Latitude", "mean"), lon=("Longitude", "mean")).reset_index()
    rng = np.random.default_rng(0)
    sub = cells.iloc[rng.choice(len(cells), size=min(150, len(cells)), replace=False)]

    def q(r):
        pt = Point(r.lon, r.lat)
        import time
        j = None
        for att in range(5):
            try:
                rr = requests.get(URL, params={"bbox": f"{r.lon-0.02},{r.lat-0.02},{r.lon+0.02},{r.lat+0.02}", "limit": 200}, timeout=90)
                if rr.status_code == 200 and "features" in rr.json():
                    j = rr.json()
                    break
            except Exception:
                pass
            time.sleep(2 + 3 * att)
        if j is None:
            return {"lat": r.lat, "lon": r.lon, "failed": True, "n_any": 0, "n_gsd_le_1m": 0, "n_bbox": 0, "platforms": []}
        feats = [f for f in j.get("features", []) if shape(f["geometry"]).contains(pt)]
        vhr = [f for f in feats if (f["properties"].get("gsd") or 99) <= 1.0]
        return {"lat": r.lat, "lon": r.lon, "failed": False, "n_bbox": len(j["features"]), "n_any": len(feats), "n_gsd_le_1m": len(vhr),
                "platforms": sorted({str(f["properties"].get("platform")) for f in feats})}

    with ThreadPoolExecutor(3) as ex:
        res = [x for x in ex.map(q, [r for _, r in sub.iterrows()]) if x]
    n_cells = len(cells)
    out = {"positive_cells_total_0p5deg": int(n_cells), "sampled": len(res),
           "cells_with_any_ccm_item_any_date": int(sum(r["n_any"] > 0 for r in res)),
           "cells_with_ccm_gsd_le_1m_any_date": int(sum(r["n_gsd_le_1m"] > 0 for r in res)),
           "failed_queries": int(sum(r["failed"] for r in res)), "cells_with_any_item_in_bbox": int(sum(r["n_bbox"] > 0 for r in res)), "median_items_per_cell": float(np.median([r["n_any"] for r in res])) if res else None,
           "rows": res}
    (RES / "vhr_ccm_any_date.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print({k: v for k, v in out.items() if k != "rows"})


if __name__ == "__main__":
    main()
