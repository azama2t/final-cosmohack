"""L31: evidence for the BOA-offset detection threshold (inference._boa_offset_rule, 1000 -> 500 DN).

    PYTHONPATH=src .venv/Scripts/python.exe scripts/experiments/l31_boa_evidence.py

For every file the rule's statistic (dark pixels = B8 <= its 25th percentile among valid px, median of B12) is
computed by inference._boa_offset_rule itself, twice:
  * no offset:   DN = rint(rho * 1e4)                 (negative rho -> DN <= 0 -> excluded by the rule, as in L2A)
  * with offset: DN = clip(rint(rho * 1e4) + 1000, 1)  (baseline >= 04.00; negative water stays in, DN < 1000)
Sources: 48 live L2A scenes data/live/*/*/bands.tif (reflectance, offset already removed by the harmonizer; they
keep negative values) and the 300 MARIDA *val* chips out/speed_chips (ACOLITE rhorc). MARIDA test is never read.
Writes weights_exp/l31/boa_evidence.json.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from macroplastic.io import channel_names, guess_channel_names, read_raster  # noqa: E402

spec = importlib.util.spec_from_file_location("inference_l31", ROOT / "inference.py")
INF = importlib.util.module_from_spec(spec)
spec.loader.exec_module(INF)


def stat(arr, names, offset):
    dn = np.rint(arr * 1e4).astype(np.float32)
    if offset:
        dn = np.maximum(dn + offset, 1.0)
    dn[~np.isfinite(arr)] = np.nan
    _, why = INF._boa_offset_rule(np, dn, names)
    return float(why.split("median DN=")[1].split()[0]) if "median DN=" in why else None


def summary(v):
    v = np.array([x for x in v if x is not None])
    return {"n": int(v.size), "min": float(v.min()), "p1": float(np.percentile(v, 1)), "p50": float(np.median(v)),
            "p99": float(np.percentile(v, 99)), "max": float(v.max())}


def decide(no_off, with_off, thr):
    return {"false_offset (no-offset file >= thr)": int(sum(x is not None and x >= thr for x in no_off)),
            "missed_offset (offset file < thr)": int(sum(x is not None and x < thr for x in with_off))}


def main():
    res = {}
    rows = []
    for f in sorted((ROOT / "data" / "live").glob("*/*/bands.tif")):
        arr, meta = read_raster(f)
        names = guess_channel_names(arr.shape[0], meta.get("descriptions") or ())
        rows.append({"file": str(f.relative_to(ROOT)), "no_offset": stat(arr, names, 0),
                     "with_offset": stat(arr, names, 1000), "b8_median": round(float(np.nanmedian(arr[names.index("B8")])), 4)})
    names = channel_names("marida")
    val = []
    for f in sorted((ROOT / "out" / "speed_chips").glob("*.tif")):
        arr = read_raster(f)[0]
        val.append({"file": f.name, "no_offset": stat(arr, names, 0), "with_offset": stat(arr, names, 1000),
                    "b8_median": round(float(np.nanmedian(arr[names.index("B8")])), 4)})
    for key, rs in (("live_l2a_48", rows), ("marida_val_300", val)):
        no, wi = [r["no_offset"] for r in rs], [r["with_offset"] for r in rs]
        res[key] = {"no_offset": summary(no), "with_offset": summary(wi),
                    "rule_1000": decide(no, wi, 1000), "rule_500": decide(no, wi, 500),
                    "no_offset_ge_500": [r for r in rs if r["no_offset"] is not None and r["no_offset"] >= 500],
                    "with_offset_lt_1000": [r for r in rs if r["with_offset"] is not None and r["with_offset"] < 1000],
                    "files": rs}
    (ROOT / "weights_exp" / "l31" / "boa_evidence.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "files"} for k, v in res.items()}, indent=1))


if __name__ == "__main__":
    main()
