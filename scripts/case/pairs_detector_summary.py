r"""Current result of the detector on the satellite pairs -> reports/case_pairs/detector_current.json.

Reads only the current pipeline outputs (data/pairs/quality/<pair>/meta.json written by scripts/case/pair_quality.py)
and the detector mode from configs/case_pairs.yaml. No network, no model run, a second.
Optional: typed counts from a rerun of scripts/case/pairs_detector_review.py on the same detections (--review JSON).

Usage (repo root):
    .venv\Scripts\python.exe scripts\case\pairs_detector_summary.py [--review out\l75c_review\detector_review.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "case_pairs" / "detector_current.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", default=str(ROOT / "out" / "l75c_review" / "detector_review.json"),
                    help="optional: detector_review.json of pairs_detector_review.py run on the current detections")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    cfg = yaml.safe_load((ROOT / "configs" / "case_pairs.yaml").read_text(encoding="utf-8")) or {}
    det_cfg = cfg.get("detector") or {}
    crops = []
    for p in sorted(glob.glob(str(ROOT / "data" / "pairs" / "quality" / "*" / "meta.json"))):
        m = json.loads(Path(p).read_text(encoding="utf-8"))
        d = m.get("detector")
        if not isinstance(d, dict):  # Landsat: masks only, no detector
            continue
        crops.append({"dir": Path(p).parent.name, "event_id": m.get("event_id"), "scene_id": m.get("scene_id"),
                      "decision": m.get("decision"), "reason": m.get("reason"),
                      "n_obj_crop": int(d.get("n_det_crop") or 0), "n_obj_in_strip": int(d.get("n_det") or 0),
                      "px_crop": int(d.get("det_px_crop") or 0), "px_in_strip": int(d.get("det_px_strip") or 0),
                      "prob_max": d.get("prob_max"), "harmonize_offset_applied": d.get("harmonize_offset") is not None})
    tot = {"n_crops": len(crops), "n_obj": sum(c["n_obj_crop"] for c in crops),
           "n_in_strip": sum(c["n_obj_in_strip"] for c in crops),
           "n_crops_with_obj": sum(1 for c in crops if c["n_obj_crop"] > 0),
           "n_strips_with_obj": sum(1 for c in crops if c["n_obj_in_strip"] > 0),
           "n_accept": sum(1 for c in crops if c["decision"] == "accept"),
           "n_obj_accept": sum(c["n_obj_crop"] for c in crops if c["decision"] == "accept"),
           "prob_max": max((c["prob_max"] or 0) for c in crops) if crops else None,
           "n_crops_harmonized": sum(1 for c in crops if c["harmonize_offset_applied"])}
    by_dir = {}
    for c in crops:
        if c["n_obj_crop"]:
            by_dir[c["dir"]] = {"n_obj": c["n_obj_crop"], "n_in_strip": c["n_obj_in_strip"], "decision": c["decision"]}
    review = None
    rp = Path(a.review)
    if rp.is_file():
        r = json.loads(rp.read_text(encoding="utf-8"))
        t = (r.get("summary") or {}).get("totals") or {}
        if t.get("n_obj") == tot["n_obj"] and t.get("n_in_strip") == tot["n_in_strip"]:  # same detections only
            review = {"source": str(rp.relative_to(ROOT)).replace("\\", "/") if rp.is_relative_to(ROOT) else str(rp),
                      "by_type": {k: (v or {}).get("n", 0) for k, v in (t.get("by_type") or {}).items()},
                      "note": "типы назначены правилами pairs_detector_review.py, без ручной разметки"}
    rep = {"created": dt.datetime.now().isoformat(timespec="seconds"),
           "source": "data/pairs/quality/*/meta.json (scripts/case/pair_quality.py)",
           "protocol": "текущий режим детектора на вырезках Sentinel-2 L2A пар: LightGBM weights/lgbm, порог P ≥ "
                       f"{det_cfg.get('threshold', 0.63)}, harmonize = {det_cfg.get('harmonize')!r}; объекты — 8-связные "
                       "компоненты на пригодной воде без объектов у облаков; «в полосе» — пересечение с растровой полосой обследования",
           "harmonize": det_cfg.get("harmonize"),
           "config_sha256": hashlib.sha256((ROOT / "configs" / "case_pairs.yaml").read_bytes()).hexdigest(),
           "totals": tot, "crops_with_objects": by_dir, "review_types": review, "crops": crops}
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(f"[pairs_detector_summary] -> {out}: {tot['n_crops']} crops, {tot['n_obj']} objects, {tot['n_in_strip']} in strips, "
          f"harmonize={det_cfg.get('harmonize')!r}, types={'yes' if review else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
