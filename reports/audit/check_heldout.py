"""Audit: held-out demo candidates vs same-date Cozar scenes used in L92/L98 (footprint overlap of adjacent tiles)."""
import json
from pathlib import Path
import pandas as pd
from pyproj import Transformer
from shapely.geometry import box, Point
from shapely.ops import transform

ROOT = Path(__file__).resolve().parents[2]
reg = pd.read_csv(ROOT / "data/extra/registry_cozar2024.csv")
reg["acq"] = reg.tile + "_" + reg.date.str.replace("-", "")
sc = reg.groupby("acq").agg(epsg=("epsg", "first"), ulx=("ul_x", "first"), uly=("ul_y", "first")).reset_index()
used = json.loads((ROOT / "reports/audit/cozar_used_in_experiments.json").read_text())
cand = pd.read_csv(ROOT / "reports/case_demo/heldout_candidates.csv")


def fp(acq):
    r = sc[sc.acq == acq].iloc[0]
    t = Transformer.from_crs(int(r.epsg), 4326, always_xy=True).transform
    return transform(t, box(r.ulx, r.uly - 109800, r.ulx + 109800, r.uly))


out = []
for _, c in cand.iterrows():
    p = Point(c.lon, c.lat)
    fc = fp(c.acq)
    for setname, lst in used.items():
        for u in lst:
            if u.split("_")[1] != c.acq.split("_")[1] or u == c.acq:
                continue
            if u not in set(sc.acq):
                continue
            fu = fp(u)
            inter = fc.intersection(fu)
            out.append({"cand": c.acq, "set": setname, "used_same_date": u,
                        "footprints_overlap_km2_approx": round(inter.area * 111 * 111 * 0.8, 1),
                        "cand_cluster_inside_used_tile": bool(fu.contains(p))})
df = pd.DataFrame(out)
print(df.to_string() if len(df) else "no same-date used scenes")
df.to_csv(ROOT / "reports/audit/heldout_same_date.csv", index=False)
