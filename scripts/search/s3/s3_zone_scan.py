"""L86 — scan the drift search zone (drift/zones.geojson) of every USABLE S2 pair (pairs/*/meta.json decision=accept):
quality masks + LightGBM detector over the whole zone instead of the transect strip.

  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/search/s3/s3_zone_scan.py

Reuses pair_quality.process_s2 with the strip polygon replaced by the zone polygon (monkeypatch, pair_quality.py unchanged).
Output: data/search/s3/zones/<event>__<scene>/{rgb.png, quality.png, mask.png, quality.tif, prob.tif, meta.json}.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform as shp_transform

sys.path.insert(0, str(Path(__file__).resolve().parent))
import s3_pairs as sp  # noqa: E402  (sets CUDA_VISIBLE_DEVICES="", imports pair_quality)

pq, ROOT, OUT = sp.pq, sp.ROOT, sp.OUT


def main():
    cfg = pq.load_cfg(ROOT / "configs" / "case_pairs.yaml")
    from macroplastic.models.lgbm_predict import load_predictor
    pred = load_predictor(ROOT / cfg["detector"]["weights"], harmonize=pq.harmonize_mode(cfg))
    zones = json.loads((OUT / "drift" / "zones.geojson").read_text(encoding="utf-8"))["features"]
    orig = pq.strip_polygon
    for m in sorted((OUT / "pairs").glob("*/meta.json")):
        meta = json.loads(m.read_text(encoding="utf-8"))
        if meta.get("decision") != "accept" or not str(meta.get("source", "")).endswith("sentinel-2-l2a"):
            continue
        t = pd.Timestamp(meta["scene_datetime"])
        z = [f for f in zones if f["properties"]["event_id"] == meta["event_id"]
             and abs((pd.Timestamp(f["properties"]["scene_datetime"]) - t).total_seconds()) < 60]
        if not z:
            continue
        zone_ll = shape(z[0]["geometry"])
        od = OUT / "zones" / m.parent.name
        if (od / "meta.json").exists():
            continue

        def zone_poly(g, epsg, c, _z=zone_ll):
            tr = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
            return shp_transform(lambda x, y, zz=None: tr.transform(x, y), _z), "drift search zone (s3_drift.py, particles + tide + 0.5 km)"

        pq.strip_polygon = zone_poly
        try:
            row = pd.Series(dict(endpoint="planetary-computer", collection="sentinel-2-l2a", item_id=meta["scene_id"]))
            res = pq.process_s2(row, {}, cfg, pred, od)
        finally:
            pq.strip_polygon = orig
        sp.CAP.pop("crop", None)
        res.update(event_id=meta["event_id"], zone=z[0]["properties"])
        (od / "meta.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        q, d = res["quality"], res["detector"]
        print(meta["event_id"], res["scene_id"], res["decision"], res["reason"], "zone km2", q["strip_area_km2"],
              "water", q["valid_water_frac"], "n_det zone", d["n_det"], "pmax", d["prob_max"], flush=True)


if __name__ == "__main__":
    main()
