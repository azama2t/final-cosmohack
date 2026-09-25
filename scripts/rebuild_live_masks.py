"""Recompute water_mask.tif + water stats in scene.json for existing data/live scenes (after a mask-rule change).

  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/rebuild_live_masks.py
Then re-run scripts/run_mdd.py --all --force (GPU queue) and scripts/run_lgbm_live.py --all --force to refresh counts.
"""
import json
import sys
from pathlib import Path

import rasterio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from macroplastic.live import stac  # noqa: E402

RULE = ("1 = SCL 6, or SCL 2/7 with NDWI(B3,B8)>0; + enclosed non-water holes <=100 px (not cloud/shadow/nodata) "
        "filled as water; then 2 px buffer next to any non-water and 5 px next to cloud/shadow (SCL 3,8,9,10) removed; 0 = land/cloud 8-10/shadow 3/"
        "snow/saturated/nodata/buffer")

for sj in sorted((ROOT / "data" / "live").glob("*/*/scene.json")):
    d = sj.parent
    with rasterio.open(d / "bands.tif") as s:
        bands, tr, crs = s.read(), s.transform, s.crs
    scl = rasterio.open(d / "scl.tif").read(1)
    water = stac.water_mask(bands, scl)
    stac._write_tif(d / "water_mask.tif", water, tr, crs, "uint8", descriptions=["water"])
    st = stac.scene_stats(bands, scl, water)
    st.update(stac.water_quality(bands, water))
    meta = json.loads(sj.read_text(encoding="utf-8"))
    meta.update(water_frac=st["water_frac"], water_b8_median=st["water_b8_median"],
                water_b11_median=st["water_b11_median"], glint_or_haze=st["glint_or_haze"], water_mask_rule=RULE)
    sj.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(d.parent.name, d.name, st["water_frac"], st["water_b11_median"], st["glint_or_haze"], flush=True)
