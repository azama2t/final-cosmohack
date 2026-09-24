r"""Collect every number shown in README / report / deck / UI into reports/final_numbers.json.

Usage (from repo root):
    .venv\Scripts\python.exe scripts\final_numbers.py [--out reports\final_numbers.json] [--quiet]

Sources (all optional; a missing source gives null, never an error):
  weights/lgbm/meta.json                 our LightGBM: val metrics, threshold, (test / seed noise if present)
  reports/l3_*.json, reports/lgbm_*.json extra L3 results (test run, seed noise) if written by training scripts
  reports/l4_unet*.json                  UNet results (val, noise over seeds, decision)
  reports/marida_scenes.csv, reports/eda/eda.md   data numbers (patches, scenes, MD pixels by split)
  data/live/<region>/<date>/scene.json   live Sentinel-2 L2A scenes
  service/data/manifest.json (else service/demo/manifest.json)   regions, dates, detections on the map
  reports/ui_perf.md                     last non-empty line (UI speed measurement)

The file is generated; do not edit it by hand.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_json(p: Path):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _r(x, nd=4):
    try:
        return None if x is None else round(float(x), nd)
    except Exception:
        return None


def _md_block(d: dict | None, threshold=None) -> dict | None:
    """Normalise a metrics dict ({f1, iou, precision, recall} or {f1_md, ...}) to our keys."""
    if not isinstance(d, dict):
        return None

    def g(*keys):
        for k in keys:
            if k in d and d[k] is not None:
                return d[k]
        return None

    out = {
        "f1_md": _r(g("f1_md", "f1", "F1")),
        "iou_md": _r(g("iou_md", "iou", "IoU")),
        "precision_md": _r(g("precision_md", "precision")),
        "recall_md": _r(g("recall_md", "recall")),
        "threshold": _r(g("threshold") if g("threshold") is not None else threshold),
        "n_md_px": g("n_md_px", "n_md") if g("n_md_px", "n_md") is not None else (
            (d.get("tp") or 0) + (d.get("fn") or 0) if "tp" in d else None),
    }
    if all(v is None for k, v in out.items() if k != "threshold"):
        return None
    return out


# ----------------------------------------------------------------------------------------------- L3
def collect_l3() -> dict:
    res = {"available": False, "model": "LightGBM (пиксельная, MARIDA)", "val": None, "val_at_0_5": None,
           "test": None, "noise": {"n_seeds": None, "f1_std": None, "f1_mean": None},
           "threshold": None, "n_features": None, "features": None, "n_train_px": None, "n_train_md": None,
           "model_size_mb": None, "train_seconds": None, "val_recall_by_conf": None,
           "val_fp_by_class": None, "sources": []}
    meta_p = ROOT / "weights" / "lgbm" / "meta.json"
    meta = _load_json(meta_p)
    if isinstance(meta, dict):
        res["available"] = True
        res["sources"].append(str(meta_p.relative_to(ROOT)).replace("\\", "/"))
        thr = meta.get("threshold")
        res["threshold"] = _r(thr)
        res["val"] = _md_block(meta.get("val_md"), thr)
        res["val_at_0_5"] = _md_block(meta.get("val_md_at_0.5"), 0.5)
        for k in ("test_md", "test", "test_md_final"):
            if isinstance(meta.get(k), dict):
                res["test"] = _md_block(meta[k], thr)
                break
        feats = meta.get("features")
        if isinstance(feats, list):
            res["n_features"] = len(feats)
            res["features"] = feats
        res["n_train_px"] = meta.get("n_train_px")
        res["n_train_md"] = meta.get("n_train_md")
        res["model_size_mb"] = _r(meta.get("model_size_mb"), 2)
        secs = meta.get("seconds")
        if isinstance(secs, dict):
            res["train_seconds"] = _r(secs.get("train"), 1)
        rbc = meta.get("val_recall_by_conf")
        if isinstance(rbc, dict):
            names = {"1": "high", "2": "moderate", "3": "low"}
            res["val_recall_by_conf"] = {names.get(str(k), str(k)): _r(v) for k, v in rbc.items()}
        pc = meta.get("val_per_class_pred_md")
        if isinstance(pc, dict):
            res["val_fp_by_class"] = {str(k): v for k, v in pc.items() if str(k) != "1"}
        for k in ("noise", "seed_noise", "val_noise"):
            if isinstance(meta.get(k), dict):
                n = meta[k]
                res["noise"] = {"n_seeds": n.get("n_seeds"), "f1_std": _r(n.get("f1_std", n.get("std"))),
                                "f1_mean": _r(n.get("f1_mean", n.get("mean")))}
    # extra result files written by training / final-test scripts
    for p in sorted(set(glob.glob(str(ROOT / "reports" / "l3_*.json")) + glob.glob(str(ROOT / "reports" / "lgbm_*.json")))):
        d = _load_json(Path(p))
        if not isinstance(d, dict):
            continue
        res["sources"].append(str(Path(p).relative_to(ROOT)).replace("\\", "/"))
        for k in ("test_md", "test"):
            if res["test"] is None and isinstance(d.get(k), dict):
                res["test"] = _md_block(d[k], res["threshold"])
        for k in ("noise", "seed_noise", "val_noise"):
            if res["noise"]["f1_std"] is None and isinstance(d.get(k), dict):
                n = d[k]
                res["noise"] = {"n_seeds": n.get("n_seeds"), "f1_std": _r(n.get("f1_std", n.get("std"))),
                                "f1_mean": _r(n.get("f1_mean", n.get("mean")))}
    return res


# ----------------------------------------------------------------------------------------------- L4
def collect_l4() -> dict:
    res = {"available": False, "val": None, "test": None, "noise": {"n_seeds": None, "f1_std": None, "f1_mean": None},
           "decision": None, "sources": []}
    for p in sorted(glob.glob(str(ROOT / "reports" / "l4_unet*.json"))):
        d = _load_json(Path(p))
        if not isinstance(d, dict):
            continue
        res["available"] = True
        res["sources"].append(str(Path(p).relative_to(ROOT)).replace("\\", "/"))
        for k in ("val_md", "val"):
            if res["val"] is None and isinstance(d.get(k), dict):
                res["val"] = _md_block(d[k], d.get("threshold"))
        for k in ("test_md", "test"):
            if res["test"] is None and isinstance(d.get(k), dict):
                res["test"] = _md_block(d[k], d.get("threshold"))
        for k in ("noise", "seed_noise", "val_noise"):
            if res["noise"]["f1_std"] is None and isinstance(d.get(k), dict):
                n = d[k]
                res["noise"] = {"n_seeds": n.get("n_seeds"), "f1_std": _r(n.get("f1_std", n.get("std"))),
                                "f1_mean": _r(n.get("f1_mean", n.get("mean")))}
        if res["decision"] is None and d.get("decision"):
            res["decision"] = str(d["decision"])
    if not res["available"]:
        md = sorted(glob.glob(str(ROOT / "reports" / "l4_unet*.md")))
        if md:
            res["sources"] = [str(Path(p).relative_to(ROOT)).replace("\\", "/") for p in md]
    return res


# ----------------------------------------------------------------------------------------------- data
def collect_data() -> dict:
    res = {"marida": None}
    p = ROOT / "reports" / "marida_scenes.csv"
    if p.exists():
        try:
            with open(p, encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

            def s(col):
                return int(sum(float(r.get(col) or 0) for r in rows))

            m = {
                "n_scenes": len(rows),
                "n_patches": s("n_patches"),
                "n_patches_train": s("n_train"), "n_patches_val": s("n_val"), "n_patches_test": s("n_test"),
                "n_md_patches": s("n_md_patches"),
                "md_px": s("md_px"), "md_px_high": s("md_px_high"), "md_px_moderate": s("md_px_moderate"),
                "md_px_low": s("md_px_low"),
                "n_tiles": len({r.get("tile") for r in rows}),
                "n_regions": len({r.get("region") for r in rows}),
                "date_min": min(r["date"] for r in rows), "date_max": max(r["date"] for r in rows),
                "md_px_train": None, "md_px_val": None, "md_px_test": None, "labelled_pct": None,
            }
            res["marida"] = m
        except Exception as e:  # noqa: BLE001
            print(f"[final_numbers] marida_scenes.csv: {e}", file=sys.stderr)
    eda = ROOT / "reports" / "eda" / "eda.md"
    if eda.exists() and res["marida"] is not None:
        txt = eda.read_text(encoding="utf-8")
        for split in ("train", "val", "test"):
            mm = re.search(rf"^\|\s*{split}\s*\|\s*(\d+)\s*\|", txt, re.M)
            if mm:
                res["marida"][f"md_px_{split}"] = int(mm.group(1))
        mm = re.search(r"Размечено\s+\d+\s+из\s+\d+\s+px\s+\(([\d.]+)\s*%\)", txt)
        if mm:
            res["marida"]["labelled_pct"] = float(mm.group(1))
        mm = re.search(r"Компоненты MD[^:]*:\s*(\d+);\s*медиана\s*([\d.]+)\s*px.*?одиночных пикселей\s*(\d+)\s*\((\d+)\s*%\)", txt)
        if mm:
            res["marida"].update({"md_components": int(mm.group(1)), "md_component_median_px": float(mm.group(2)),
                                  "md_single_px_components": int(mm.group(3)), "md_single_px_pct": float(mm.group(4))})
    return res


# ----------------------------------------------------------------------------------------------- live
def collect_live() -> dict:
    scenes = []
    for p in sorted(glob.glob(str(ROOT / "data" / "live" / "*" / "*" / "scene.json"))):
        d = _load_json(Path(p))
        if not isinstance(d, dict):
            continue
        folder = Path(p).parent
        models = sorted(q.stem.replace("prob_", "") for q in folder.glob("prob_*.tif"))
        scenes.append({"region": d.get("region"), "date": d.get("date"), "scene_id": d.get("scene_id"),
                       "tile": d.get("tile"), "source": d.get("source"),
                       "crop_cloud_frac": _r(d.get("crop_cloud_frac")), "water_frac": _r(d.get("water_frac")),
                       "size_px": [d.get("width"), d.get("height")], "models": models})
    fresh = [s for s in scenes if (s.get("date") or "") >= "2025-01-01"]
    return {"n_scenes": len(scenes), "n_regions": len({s["region"] for s in scenes}),
            "n_fresh_2025_2026": len(fresh), "scenes": scenes}


def collect_service() -> dict:
    res = {"data_root": None, "kind": None, "n_regions": 0, "n_dates": 0, "n_detections_total": None,
           "models": None, "mdd_threshold": None, "lgbm_threshold_map": None, "n_drift": 0, "regions": []}
    for cand in (ROOT / "service" / "data", ROOT / "service" / "demo"):
        man = _load_json(cand / "manifest.json")
        if isinstance(man, dict):
            res["data_root"] = str(cand.relative_to(ROOT)).replace("\\", "/")
            break
    else:
        return res
    res["kind"] = man.get("kind")
    models = man.get("models") or {}
    res["models"] = sorted(models)
    res["mdd_threshold"] = _r((models.get("mdd") or {}).get("threshold"))
    res["lgbm_threshold_map"] = _r((models.get("lgbm") or {}).get("threshold"))
    tot = 0
    any_det = False
    for r in man.get("regions") or []:
        dates = r.get("dates") or []
        summ = r.get("summary") or {}
        res["n_dates"] += len(dates)
        res["n_drift"] += sum(1 for d in dates if d.get("drift"))
        if summ.get("n_detections") is not None:
            tot += int(summ["n_detections"])
            any_det = True
        res["regions"].append({
            "id": r.get("id"), "name": r.get("name"), "country": r.get("country"), "tile": r.get("tile"),
            "dates": [d.get("date") for d in dates], "latest_date": summ.get("latest_date"),
            "model": summ.get("model"), "index_permille": _r(summ.get("index_permille"), 3),
            "n_detections": summ.get("n_detections"),
            "total_debris_area_km2": _r((summ.get("total_debris_area_m2") or 0) / 1e6, 3)
            if summ.get("total_debris_area_m2") is not None else None,
            "n_zones": _count_zones(res["data_root"], r.get("id"), summ.get("latest_date"), summ.get("model")),
        })
    res["n_regions"] = len(res["regions"])
    res["n_detections_total"] = tot if any_det else None
    return res


def _count_zones(root, region, date, model):
    if not (root and region and date and model):
        return None
    z = _load_json(ROOT / root / region / date / model / "zones.json")
    if isinstance(z, dict) and isinstance(z.get("zones"), list):
        return len(z["zones"])
    return None


def collect_ui_perf() -> dict:
    p = ROOT / "reports" / "ui_perf.md"
    if not p.exists():
        return {"available": False, "last_line": None}
    lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return {"available": True, "last_line": lines[-1] if lines else None}


def collect_artifacts() -> dict:
    def ex(rel):
        return (ROOT / rel).exists()
    return {
        "tools_doc": ex("docs/TOOLS.md"),
        "inspect_dataset": ex("scripts/tools/inspect_dataset.py"),
        "label_forensics": ex("scripts/tools/label_forensics.py"),
        "provenance_check": ex("scripts/tools/provenance_check.py"),
        "organizer_adapter": ex("src/macroplastic/organizer_adapter/__main__.py"),
        "screens_final": sorted(Path(p).name for p in glob.glob(str(ROOT / "reports" / "screens" / "final" / "*.png"))),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Собрать reports/final_numbers.json из артефактов")
    ap.add_argument("--out", default=str(ROOT / "reports" / "final_numbers.json"))
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    fn = {
        "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "note": "генерируется scripts/final_numbers.py; руками не править; null = ещё не посчитано",
        "l3_lgbm": collect_l3(),
        "l4_unet": collect_l4(),
        "data": collect_data(),
        "live": collect_live(),
        "service": collect_service(),
        "ui_perf": collect_ui_perf(),
        "artifacts": collect_artifacts(),
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(fn, f, ensure_ascii=False, indent=1)

    if not a.quiet:
        l3, l4, m, sv = fn["l3_lgbm"], fn["l4_unet"], fn["data"]["marida"] or {}, fn["service"]
        v, t = l3["val"] or {}, l3["test"] or {}
        print(f"[final_numbers] -> {out}")
        print(f"  L3 LightGBM: val F1 MD={v.get('f1_md')} IoU={v.get('iou_md')} thr={l3['threshold']} | "
              f"test F1={t.get('f1_md')} IoU={t.get('iou_md')} | noise std={l3['noise']['f1_std']}")
        print(f"  L4 UNet: available={l4['available']} val F1={(l4['val'] or {}).get('f1_md')} "
              f"decision={l4['decision']}")
        print(f"  MARIDA: scenes={m.get('n_scenes')} patches={m.get('n_patches')} MD px={m.get('md_px')} "
              f"(train/val/test {m.get('md_px_train')}/{m.get('md_px_val')}/{m.get('md_px_test')}) "
              f"labelled={m.get('labelled_pct')}%")
        print(f"  live scenes: {fn['live']['n_scenes']} in {fn['live']['n_regions']} regions "
              f"(fresh 2025-26: {fn['live']['n_fresh_2025_2026']})")
        print(f"  service: root={sv['data_root']} kind={sv['kind']} regions={sv['n_regions']} dates={sv['n_dates']} "
              f"detections={sv['n_detections_total']} models={sv['models']}")
        print(f"  ui_perf: {fn['ui_perf']['last_line']}")
        missing = [k for k, v in fn["artifacts"].items() if v is False]
        if missing:
            print(f"  not yet present: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
