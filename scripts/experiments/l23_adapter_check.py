"""L23: can a band-subset model (no RGB extras) run through the product LGBMPredictor today?

Trick: the product feature code needs all 11 bands, so missing bands are filled with 0.0 (not NaN - NaN would blank
every feature). The subset model only uses features whose inputs are present, so its output must equal the output on
the true 11-band chip. Checked on 20 MARIDA val chips (out/speed_chips); also adds "feature_level": "win" to the L23
subset meta.json files so LGBMPredictor can load them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from macroplastic.features.pixel import BANDS11  # noqa: E402
from macroplastic.io import read_raster  # noqa: E402
from macroplastic.models.lgbm_predict import LGBMPredictor  # noqa: E402

res = {}
for name in ("b_rgbnir_s0", "f_10m_swir_s0", "d_10m_20m_s0"):
    wd = ROOT / "weights_exp" / "l23" / name
    meta = json.loads((wd / "meta.json").read_text(encoding="utf-8"))
    meta["feature_level"] = "win"
    (wd / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    pred = LGBMPredictor(wd, num_threads=8)
    keep = set(meta["channels"])
    mx, nd = 0.0, 0
    for f in sorted((ROOT / "out" / "speed_chips").glob("*.tif"))[:20]:
        arr, _ = read_raster(f)
        full = pred.predict_proba(arr, BANDS11)
        z = arr.copy()
        for i, b in enumerate(BANDS11):
            if b not in keep:
                z[i] = 0.0
        sub = pred.predict_proba(z, BANDS11)
        mx = max(mx, float(np.abs(full - sub).max()))
        nd += int(((full >= pred.threshold) != (sub >= pred.threshold)).sum())
    res[name] = {"max_abs_prob_diff": mx, "mask_diff_px": nd, "chips": 20}
    print(name, res[name], flush=True)
(ROOT / "out" / "l23_runs" / "adapter_check.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
