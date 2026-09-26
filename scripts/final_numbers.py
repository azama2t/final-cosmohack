r"""Collect every number shown in README / report / deck / UI into reports/final_numbers.json.

Usage (from repo root):
    .venv\Scripts\python.exe scripts\final_numbers.py [--out reports\final_numbers.json] [--quiet]

Sources (all optional; a missing source gives null, never an error):
  weights/lgbm/meta.json                 final LightGBM (MARIDA train + MADOS): val metrics, threshold, features, (test if present)
  reports/l13_mados.json                 seed noise of the final recipe and of the MARIDA-only recipe, acceptance rule
  reports/l3_*.json, reports/lgbm_*.json extra results (test run, seed noise) if written by training / final-test scripts
  reports/l4_unet*.json                  UNet and UNet+LightGBM stack (val, noise over seeds, decision)
  weights/lgbm_live/meta.json            LightGBM variant used as the map layer on live L2A scenes
  reports/l16_metric_audit.json          metric recheck, scene bootstrap, leave-region-out
  reports/model_agreement.json           agreement of the two models on live scenes
  reports/speed.json                     inference speed (cold start by device, warm throughput; optional cold_s_by_threads)
  reports/baselines.json                 simple baselines on val MARIDA (scripts/baselines.py)
  reports/l23_channels_speed.json        models on channel subsets and the light model
  data/live/<region>/<date>/drift.json   drift forecasts (0-72 h)
  reports/marida_scenes.csv, reports/eda/eda.md   data numbers (patches, scenes, MD pixels by split)
  data/live/<region>/<date>/scene.json   live Sentinel-2 L2A scenes
  service/data/manifest.json (else service/demo/manifest.json)   regions, dates, detections on the map
  reports/ui_perf.md                     last non-empty line (UI speed measurement)
  weights_exp/l31/speed.json | reports/l31_midsize.md       cold start by CPU threads, mid-size model (rejected)
  weights_exp/l26/*.json | reports/l31_midsize.md           LRO of the light models (rejected)
  reports/l33_scene_relative.json        scene-relative features (rejected)
  reports/robustness.md                  PASS / WARN / FAIL of the robustness checks
  reports/rehearsal2.md, reports/l35_asis_check.{md,json}   rehearsal of the first hour on an unfamiliar dataset
  reports/drift_check.json               drift forecast check on pairs of scenes (experiment; criterion fixed in advance)
  reports/context.json                   OSM objects by kind, demo drift clouds touching objects (no alerts)
  service/routes_incidents.py            incidents: same logic as /api/incidents/summary (total, excluded, reviewed)
  case (block "case", the hackathon case «макропластик, шт./км²»):
    task/macroplastic_marine_samples.csv, reports/case_run/run_summary.json   rows/events, selection, pairs registry, versions, sha256
    reports/case_pairs/summary.md, configs/case_pairs.yaml                  scenes by source/window, drift scenarios, mask thresholds
    reports/case_detector/{metrics.json, per_patch.csv, fn_by_scene.csv}    7 detectors on val/test MARIDA, FP by class, paired ΔF1
    reports/case_conc/{dev_cv.json, metrics.json}, configs/case_{selection,conc_model}.yaml, reports/case_splits/selection_*.csv
                                                                            targets, dev CV tables, split schemes, frozen test, final_test.json if present
    reports/case_pairs/experiment.json, reports/selfcheck/consistency_*.json  pairs experiment, API self-check
    src/macroplastic/case/concentration.py                                  control example 12 / 0.20 (same code as the service)

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
        with open(p, encoding="utf-8-sig") as f:
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


# ----------------------------------------------------------------------------------------------- LightGBM
def collect_l3() -> dict:
    res = {"available": False, "model": "LightGBM (пиксельная, MARIDA + MADOS)", "val": None, "val_at_0_5": None,
           "test": None, "noise": {"n_seeds": None, "f1_std": None, "f1_mean": None},
           "threshold": None, "n_features": None, "features": None, "n_train_px": None, "n_train_md": None,
           "model_size_mb": None, "train_seconds": None, "val_recall_by_conf": None,
           "val_fp_by_class": None, "training_data": None, "mados": None, "n_base_features": None,
           "n_window_features": None, "sources": []}
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
    if isinstance(meta, dict):
        cfg = meta.get("config") or {}
        res["training_data"] = {"combined": "MARIDA train + MADOS", "marida": "MARIDA train"}.get(
            cfg.get("data"), cfg.get("data") or "MARIDA train")
        ts = meta.get("train_sources") or {}
        mados = ts.get("mados") if isinstance(ts, dict) else None
        res["mados"] = None if not isinstance(mados, dict) else {
            "n_patches": mados.get("n_patches"), "n_scenes": mados.get("n_scenes"), "n_md_px": mados.get("n_md_px"),
            "n_train_px": mados.get("n_px"), "n_excluded_scenes": len(mados.get("excluded_scenes") or [])}
        res["n_base_features"] = sum(1 for f in (res["features"] or []) if "_" not in f) or None
        res["n_window_features"] = (res["n_features"] - res["n_base_features"]) if res["n_features"] and res["n_base_features"] else None
    l13 = _load_json(ROOT / "reports" / "l13_mados.json")
    if isinstance(l13, dict) and res["noise"]["f1_std"] is None:
        key = ((meta or {}).get("config") or {}).get("data") if isinstance(meta, dict) else None
        key = key or "combined"
        mean, std = (l13.get("val_f1") or {}).get(key), (l13.get("val_f1_std") or {}).get(key)
        if std is not None:
            res["noise"] = {"n_seeds": 3, "f1_std": _r(std), "f1_mean": _r(mean)}
            res["sources"].append("reports/l13_mados.json")
    return res


# ----------------------------------------------------------------------------------------------- models
def collect_models(l3: dict, l4: dict) -> dict:
    """Comparison table: MARIDA-only LightGBM, +MADOS (final), UNet, stack. val mean +- std over seeds."""
    l13 = _load_json(ROOT / "reports" / "l13_mados.json") or {}
    u = _load_json(ROOT / "reports" / "l4_unet.json") or {}
    f1, sd, iou = l13.get("val_f1") or {}, l13.get("val_f1_std") or {}, l13.get("val_iou") or {}
    stack = u.get("stack") if isinstance(u.get("stack"), dict) else {}
    m = re.search(r"stack\s+(\d+\.\d+)\s*\+-\s*(\d+\.\d+)", str(u.get("reason") or ""))
    stack_std = float(m.group(2)) if m else None
    m2 = re.search(r"Acceptance bar = .*?= (\d+\.\d+)", str(u.get("reason") or ""))
    unet_bar = float(m2.group(1)) if m2 else None
    un = l4.get("noise") or {}
    rejected = str(l4.get("decision") or "").lower().startswith("reject")
    final_ok = str(l13.get("decision") or "").upper().startswith("ACCEPT")
    rows = {
        "marida_only": {"name": "LightGBM, только MARIDA", "n_seeds": 3 if f1.get("marida_only") is not None else None,
                        "val_f1_mean": _r(f1.get("marida_only")), "val_f1_std": _r(sd.get("marida_only")),
                        "val_iou_mean": _r(iou.get("marida_only")),
                        "decision": "база для сравнения" if f1.get("marida_only") is not None else None},
        "combined": {"name": "LightGBM, MARIDA + MADOS", "n_seeds": 3 if f1.get("combined") is not None else None,
                     "val_f1_mean": _r(f1.get("combined")), "val_f1_std": _r(sd.get("combined")),
                     "val_iou_mean": _r(iou.get("combined")),
                     "decision": "принята, итоговая модель" if final_ok else None},
        "unet": {"name": "UNet (ResNet-34, EMA)", "n_seeds": un.get("n_seeds"), "val_f1_mean": un.get("f1_mean"),
                 "val_f1_std": un.get("f1_std"), "val_iou_mean": None,
                 "decision": "отклонена" if rejected else l4.get("decision")},
        "stack": {"name": "стек UNet + LightGBM (только MARIDA)", "n_seeds": 3 if stack else None,
                  "val_f1_mean": _r(stack.get("f1_md")), "val_f1_std": _r(stack_std),
                  "val_iou_mean": _r(stack.get("iou_md")),
                  "decision": "отклонён" if (stack and rejected) else None},
    }
    return {"rows": rows,
            "acceptance_rule": "прирост F1 MD на val не меньше max(0.01, 2 x std по seed)",
            "mados_acceptance_threshold": _r(l13.get("acceptance_threshold")),
            "unet_acceptance_threshold": _r(unet_bar),
            "mados_gain_bootstrap_ci95": (l13.get("paired_scene_bootstrap_combined_minus_marida_seed0") or {}).get("ci95"),
            "sources": [p for p in ("reports/l13_mados.json", "reports/l4_unet.json") if (ROOT / p).exists()]}


def collect_lgbm_live() -> dict:
    meta = _load_json(ROOT / "weights" / "lgbm_live" / "meta.json")
    if not isinstance(meta, dict):
        return {"available": False, "threshold": None, "val": None, "training_data": None}
    return {"available": True, "threshold": _r(meta.get("threshold")),
            "val": _md_block(meta.get("val_md"), meta.get("threshold")),
            "training_data": "MARIDA train + аугментация под L2A" if (meta.get("config") or {}).get("augment")
            else "MARIDA train"}


def collect_metric_audit() -> dict:
    a = _load_json(ROOT / "reports" / "l16_metric_audit.json")
    res = {"available": False, "recount_matches_meta": None, "scene_bootstrap_ci95": None, "threshold_optimism": None,
           "mados_gain": None, "mados_gain_ci95": None, "lro": None}
    if not isinstance(a, dict):
        return res
    res["available"] = True
    rc = a.get("recheck") or {}
    fin = rc.get("final") or {}
    res["recount_matches_meta"] = fin.get("match_3dp")
    ci = fin.get("scene_bootstrap_ci95")
    res["scene_bootstrap_ci95"] = [_r(x, 3) for x in ci] if isinstance(ci, list) else None
    res["threshold_optimism"] = _r((fin.get("thr_split_half") or {}).get("optimism_mean"))
    bk = rc.get("backup_marida_only") or {}
    bkr = bk.get("recount") or {}
    bci = bk.get("scene_bootstrap_ci95")
    res["marida_only"] = {"f1": _r(bkr.get("f1")), "iou": _r(bkr.get("iou")), "precision": _r(bkr.get("precision")),
                          "recall": _r(bkr.get("recall")), "threshold": _r(bk.get("threshold")),
                          "scene_bootstrap_ci95": [_r(x, 3) for x in bci] if isinstance(bci, list) else None} if bkr else None
    g = rc.get("mados_gain_paired_scene_bootstrap") or {}
    gci = g.get("delta_ci95")
    res["mados_gain"], res["mados_gain_ci95"] = _r(g.get("delta_point")), ([_r(x, 3) for x in gci] if isinstance(gci, list) else None)
    lro = a.get("lro")
    if isinstance(lro, dict):
        res["lro"] = {"mean_f1": _r(lro.get("mean_f1")), "mean_f1_std_seeds": _r(lro.get("mean_f1_std_seeds")),
                      "mean_iou": _r(lro.get("mean_iou")), "mean_f1_marida_only": _r(lro.get("mean_f1_marida_only")),
                      "in_dist_f1": _r(lro.get("in_dist_f1")), "drop_vs_in_dist": _r(lro.get("drop_vs_in_dist")),
                      "n_regions": len(lro.get("per_region") or []) or None,
                      "per_region": [{"region": r.get("region"), "n_md_px": r.get("n_md_px"), "f1": _r(r.get("lro_f1")),
                                      "f1_std": _r(r.get("lro_f1_std")), "f1_marida_only": _r(r.get("lro_f1_marida_only"))}
                                     for r in (lro.get("per_region") or []) if isinstance(r, dict)]}
    return res


def collect_agreement() -> dict:
    a = _load_json(ROOT / "reports" / "model_agreement.json")
    res = {"available": False, "n_scenes": None, "mdd_threshold": None, "lgbm_threshold": None, "radius_m": 20,
           "mdd_px": None, "lgbm_px": None, "mdd_px_confirmed": None, "lgbm_px_confirmed": None,
           "confirmed_objects": None, "confirmed_px": None, "scenes_with_confirmed": None, "spearman_median": None}
    if not isinstance(a, dict):
        return res
    p, t = a.get("pooled") or {}, a.get("thresholds") or {}
    res.update({"available": True, "n_scenes": p.get("n") or len(a.get("scenes") or []) or None,
                "mdd_threshold": _r(t.get("mdd_display")), "lgbm_threshold": _r((a.get("val") or {}).get("threshold")),
                "mdd_px": p.get("m"), "lgbm_px": p.get("l"), "mdd_px_confirmed": p.get("b"),
                "lgbm_px_confirmed": p.get("lconf"), "confirmed_objects": p.get("dobj"), "confirmed_px": p.get("d"),
                "scenes_with_confirmed": p.get("sc_d"), "spearman_median": _r(p.get("spearman_median"), 3)})
    return res


def collect_drift() -> dict:
    items = []
    for p in sorted(glob.glob(str(ROOT / "data" / "live" / "*" / "*" / "drift.json"))):
        d = _load_json(Path(p))
        if not isinstance(d, dict):
            continue
        st = d.get("stats") or {}
        hrs = d.get("hours") if isinstance(d.get("hours"), list) else []
        items.append({"region": d.get("region"), "date": d.get("date"), "hours": max(hrs) if hrs else None,
                      "n_particles": st.get("n_particles"),
                      "mean_displacement_km": _r(st.get("mean_displacement_km"), 2),
                      "stranded_pct": _r(st.get("stranded_pct"), 1),
                      "wind_drift_factors": sorted({"0.02", *(d.get("ensemble_stats") or {}).keys()})})
    return {"n_scenes": len(items) or None, "n_regions": len({i["region"] for i in items}) or None,
            "hours": max((i["hours"] or 0) for i in items) if items else None,
            "currents": "HYCOM ESPC-D-V02 1/12°" if items else None, "wind": "NCEP GFS 0.5°" if items else None,
            "model": "OpenDrift OceanDrift" if items else None, "scenes": items}


def collect_speed() -> dict:
    d = _load_json(ROOT / "reports" / "speed.json")
    if d is None:
        return {"available": False, "summary": None, "raw": None, "rows": None}
    summ = d.get("summary") if isinstance(d, dict) and isinstance(d.get("summary"), str) else None
    rows = []
    if isinstance(d, dict):
        chips = d.get("chips")
        cold, runs, base = d.get("cold_s") or {}, d.get("cold_s_runs") or {}, d.get("baseline_cold_s") or {}
        warm = d.get("warm") or {}
        hw = str(d.get("hardware") or "")
        gpu = re.search(r"NVIDIA ([^(;]+)", hw)
        cpu_thr = re.search(r"(\d+) threads", hw)
        labels = {"cuda": f"GPU ({gpu.group(1).strip()})" if gpu else "GPU (CUDA)",
                  "cpu": f"CPU, {cpu_thr.group(1)} потоков" if cpu_thr else "CPU"}
        for dev in ("cuda", "cpu"):
            if cold.get(dev) is None:
                continue
            rr = [x for x in (runs.get(dev) or []) if isinstance(x, (int, float))]
            rows.append({"mode": labels[dev], "device": dev, "threads": None, "cold_s": _r(cold.get(dev), 2),
                         "cold_s_min": _r(min(rr), 2) if rr else None, "cold_s_max": _r(max(rr), 2) if rr else None,
                         "n_runs": len(rr) or None,
                         "warm_chips_per_s": _r((warm.get(dev) or {}).get("e2e_chips_per_s"), 1),
                         "before_cold_s": _r(base.get(dev), 2)})
        # optional: cold start with a limited number of CPU threads, e.g. {"8": 37.3, "4": 74.7}
        for thr, v in sorted((d.get("cold_s_by_threads") or {}).items(), key=lambda kv: -int(kv[0])):
            rows.append({"mode": f"CPU, {thr} потоков", "device": "cpu", "threads": int(thr), "cold_s": _r(v, 2),
                         "cold_s_min": None, "cold_s_max": None, "n_runs": None, "warm_chips_per_s": None,
                         "before_cold_s": None})
        if summ is None and rows:
            summ = f"{chips} чипов 256×256, холодный старт: " + "; ".join(
                f"{r['mode']} {r['cold_s']:.1f} с" for r in rows if r["cold_s"] is not None)
    return {"available": True, "summary": summ, "raw": d, "rows": rows or None,
            "chips": d.get("chips") if isinstance(d, dict) else None,
            "hardware": d.get("hardware") if isinstance(d, dict) else None,
            "command": d.get("command") if isinstance(d, dict) else None}


def collect_baselines() -> dict:
    """reports/baselines.json (scripts/baselines.py): simple baselines on val MARIDA, same metric."""
    d = _load_json(ROOT / "reports" / "baselines.json")
    if not isinstance(d, dict):
        return {"available": False, "rows": None}
    rows = {}
    for k, v in d.items():
        if k.startswith("_") or not isinstance(v, dict):
            continue
        rows[k] = {kk: v.get(kk) for kk in ("f1_md", "iou_md", "precision", "recall", "threshold", "note", "f1_mean",
                                            "f1_std", "n_seeds", "scene_ci95_f1", "f1_md_threshold_from_train",
                                            "index", "bounds")}
    meta = d.get("_meta") or {}
    return {"available": True, "rows": rows, "split": meta.get("split"), "generated": meta.get("generated"),
            "scene_ci": meta.get("scene_ci"), "source": "reports/baselines.json"}


def collect_l23() -> dict:
    """reports/l23_channels_speed.json: models on channel subsets and the light model."""
    d = _load_json(ROOT / "reports" / "l23_channels_speed.json")
    if not isinstance(d, dict):
        return {"available": False, "subsets": None, "light": None}
    subs = []
    for s in d.get("subsets") or []:
        if not isinstance(s, dict):
            continue
        subs.append({"name": s.get("name"), "label": s.get("label"), "channels": s.get("channels"),
                     "n_features": s.get("n_features"), "f1": _r(s.get("f1")), "iou": _r(s.get("iou")),
                     "f1_mean": _r(s.get("f1_mean")), "f1_sd": _r(s.get("f1_sd")),
                     "n_seeds": len(s.get("seeds") or []) or None, "delta_vs_full": _r(s.get("delta_vs_full")),
                     "delta_vs_full_mean": _r(s.get("delta_vs_full_mean"))})
    lt = d.get("light") or {}
    fin = lt.get("final_same_harness") or {}
    seeds = [x for x in (lt.get("f1_seeds") or []) if isinstance(x, (int, float))]
    sd = None
    if len(seeds) > 1:
        m = sum(seeds) / len(seeds)
        sd = _r((sum((x - m) ** 2 for x in seeds) / (len(seeds) - 1)) ** 0.5)
    prod = d.get("speed_product_predictor_warm") or {}
    anchor = [x for x in (d.get("speed_anchor_inference_py_cpu_cold_s") or []) if isinstance(x, (int, float))]
    light = None if not lt else {
        "name": lt.get("name"), "n_features": lt.get("n_features"), "n_trees": lt.get("n_trees"),
        "num_leaves": lt.get("num_leaves"), "f1_s0": _r(lt.get("f1")), "iou_s0": _r(lt.get("iou")),
        "f1_mean": _r(lt.get("f1_mean")), "f1_sd": sd, "n_seeds": len(seeds) or None,
        "threshold": _r(lt.get("threshold")), "cold_s_300": _r(lt.get("cold_s_300"), 1),
        "warm_chips_per_s": _r(lt.get("warm_chips_per_s"), 1),
        "final_cold_s_300": _r(fin.get("cold_s_300"), 1), "final_warm_chips_per_s": _r(fin.get("warm_chips_per_s"), 1),
        "final_f1": _r(fin.get("f1")), "final_n_features": fin.get("n_features"), "final_n_trees": fin.get("n_trees"),
        "model_stage_s_product": _r((prod.get(lt.get("weights") or "") or {}).get("model_10threads_s"), 1),
        "final_model_stage_s_product": _r((prod.get("weights/lgbm") or {}).get("model_10threads_s"), 1),
        "product_cold_s_final": _r(max(anchor), 1) if anchor else None}
    return {"available": True, "subsets": subs, "light": light, "threads": (d.get("speed_setup") or {}).get("threads"),
            "chips": (d.get("speed_setup") or {}).get("chips"), "source": "reports/l23_channels_speed.json"}


def _read(rel: str) -> str:
    p = ROOT / rel
    try:
        return p.read_text(encoding="utf-8") if p.exists() else ""
    except Exception:  # noqa: BLE001
        return ""


def _fl(s):
    try:
        return float(str(s).replace(",", "."))
    except Exception:  # noqa: BLE001
        return None


def collect_speed_threads() -> dict:
    """Cold start of inference.py (300 chips) by CPU threads (affinity mask) and GPU, final vs mid-size model.
    Source: weights_exp/l31/speed.json (local); fallback: the table in reports/l31_midsize.md (in git)."""
    res = {"available": False, "chips": None, "final": None, "mid": None, "speedup_mid": None, "source": None,
           "method": "CPU N потоков = маска affinity процесса на первые N логических CPU; холодный старт, медиана 3 запусков"}
    d = _load_json(ROOT / "weights_exp" / "l31" / "speed.json")
    if isinstance(d, dict) and isinstance(d.get("summary"), dict):
        s = d["summary"]

        def pick(m):
            return {k: _r((v or {}).get("median_s"), 2) for k, v in (s.get(m) or {}).items()}
        res.update({"available": True, "chips": d.get("chips"), "final": pick("final"), "mid": pick("k20_t400_l63"),
                    "speedup_mid": {k: _r(v, 3) for k, v in (d.get("speedup_final_over_mid") or {}).items()},
                    "source": "weights_exp/l31/speed.json"})
        return res
    txt = _read("reports/l31_midsize.md")
    rows = {}
    for key, pat in (("final", r"^\|\s*итоговая\s*\|(.+)$"), ("mid", r"^\|\s*k20_t400_l63\s*\|(.+)$"),
                     ("speedup", r"^\|\s*ускорение\s*\|(.+)$")):
        for m in re.finditer(pat, txt, re.M):
            cells = [c.strip() for c in m.group(1).split("|") if c.strip()]
            if len(cells) == 4 and ("с" in cells[0] or key == "speedup"):
                vals = [_fl(re.search(r"(\d+(?:[.,]\d+)?)", c).group(1)) if re.search(r"\d", c) else None for c in cells]
                rows[key] = dict(zip(("cpu4", "cpu8", "cpu20", "gpu"), vals))
                break
    if rows.get("final"):
        res.update({"available": True, "chips": 300, "final": rows.get("final"), "mid": rows.get("mid"),
                    "speedup_mid": rows.get("speedup"), "source": "reports/l31_midsize.md"})
    return res


def collect_rejected(l23: dict, audit: dict) -> dict:
    """Candidate models that did not pass the rules: light band-subset models, mid-size model, scene-relative features."""
    final_lro = (audit.get("lro") or {}).get("mean_f1")
    out = {"final_lro": final_lro, "light": None, "mid": None, "zfeat": None}
    # light: val from l23, LRO from weights_exp/l26 (fallback: text of reports/l31_midsize.md)
    lt = (l23 or {}).get("light") or {}
    lro_light = None
    d = _load_json(ROOT / "weights_exp" / "l26" / "light_lro_alternatives.json")
    if isinstance(d, dict):
        lro_light = ((d.get("summary") or {}).get("light_k20_t200_l31") or {}).get("mean")
    if lro_light is None:
        m = re.search(r"k20_t200_l31\s+(0\.\d+)", _read("reports/l31_midsize.md"))
        lro_light = _fl(m.group(1)) if m else None
    if lt or lro_light is not None:
        out["light"] = {"name": lt.get("name") or "light_k20_t200_l31", "n_features": lt.get("n_features"),
                        "n_trees": lt.get("n_trees"), "num_leaves": lt.get("num_leaves"),
                        "val_f1_mean": lt.get("f1_mean"), "lro_mean": _r(lro_light, 3),
                        "lro_rule": _r(final_lro - 0.01, 4) if final_lro is not None else None,
                        "cold_s_300": lt.get("cold_s_300"), "final_cold_s_300": lt.get("final_cold_s_300"),
                        "decision": "отклонена: хуже переносится на новый район"}
    # mid-size: l31
    sp = collect_speed_threads()
    mid_val, mid_lro = None, None
    txt = _read("reports/l31_midsize.md")
    m = re.search(r"\|\s*k20_t400_l63\s*\|[^|]*\|[^|]*\|[^|]*\|\s*\*\*([\d.]+)\s*±\s*([\d.]+)\*\*[^|]*\|[^|]*\|\s*\*\*([\d.]+)\s*±", txt)
    if m:
        mid_val, mid_lro = _fl(m.group(1)), _fl(m.group(3))
    if d is not None and isinstance(d, dict):
        mid_lro = ((d.get("summary") or {}).get("light_k20_t400_l63") or {}).get("mean", mid_lro)
    if sp.get("available") or mid_val is not None:
        out["mid"] = {"name": "k20_t400_l63", "n_features": 20, "n_trees": 400, "num_leaves": 63,
                      "val_f1_mean": _r(mid_val, 4), "lro_mean": _r(mid_lro, 4),
                      "speedup_cpu8": _r((sp.get("speedup_mid") or {}).get("cpu8"), 2),
                      "speedup_gpu": _r((sp.get("speedup_mid") or {}).get("gpu"), 2),
                      "speedup_needed": 1.3,
                      "decision": "отклонена: почти не быстрее на CPU"}
    # scene-relative z-features: l33
    z = _load_json(ROOT / "reports" / "l33_scene_relative.json")
    if isinstance(z, dict) and isinstance(z.get("summary"), dict):
        s = z["summary"]
        base, zs = s.get("base") or {}, s.get("zs") or {}
        deltas_val = [(v.get("val_mean") - base.get("val_mean")) for k, v in s.items()
                      if k != "base" and isinstance(v, dict) and v.get("val_mean") is not None and base.get("val_mean") is not None]
        out["zfeat"] = {"n_variants": len(deltas_val) or None,
                        "zs_lro_delta": _r(zs.get("lro_delta_vs_base"), 3),
                        "zs_val_delta": _r((zs.get("val_mean") or 0) - (base.get("val_mean") or 0), 3) if zs else None,
                        "zs_lro_mean": _r(zs.get("lro_mean"), 4), "zs_val_mean": _r(zs.get("val_mean"), 4),
                        "val_delta_range": [_r(min(deltas_val), 3), _r(max(deltas_val), 3)] if deltas_val else None,
                        "decision": "отклонены: на знакомых районах качество падает"}
    return out


def collect_robustness() -> dict:
    txt = _read("reports/robustness.md")
    m = re.search(r"Итог:\s*(\d+)\s*провер\w*\s*—\s*PASS\s*(\d+),\s*WARN\s*(\d+),\s*FAIL\s*(\d+)", txt)
    if not m:
        return {"available": False}
    return {"available": True, "n_checks": int(m.group(1)), "pass": int(m.group(2)), "warn": int(m.group(3)),
            "fail": int(m.group(4)), "source": "reports/robustness.md"}


def collect_rehearsal() -> dict:
    """Dress rehearsal of the first hour on an unfamiliar dataset (27 MADOS test scenes packed like an organiser
    archive): reports/rehearsal2.md, reports/l35_asis_check.{md,json}."""
    t2, t35 = _read("reports/rehearsal2.md"), _read("reports/l35_asis_check.md")
    res = {"available": bool(t2), "first_submit_s": None, "first_submit_s_prev": None, "human_min": None,
           "private_asis": None, "private_trained": None, "private_trained_prev": None, "trained_submit_min": None,
           "labelled_no_seasnot_f1": None, "labelled_f1_asis": None, "best_all_px_f1": None, "best_all_px_thr": None,
           "unlabelled_pct": None, "n_test_chips": None, "n_scenes": None}
    m = re.search(r"первого валидного сабмита:\s*(\d+)\s*с\b.*?\((?:L30|репетиция 1):\s*(\d+)\s*мин\s*(\d+)\s*с\)", t2)
    if m:
        res["first_submit_s"] = int(m.group(1))
        res["first_submit_s_prev"] = int(m.group(2)) * 60 + int(m.group(3))
    m = re.search(r"человека по оценке\s*(\d+)[–-](\d+)\s*мин", t2)
    if m:
        res["human_min"] = [int(m.group(1)), int(m.group(2))]
    m = re.search(r"Как есть / после обучения:\s*([\d.]+)\s*→\s*([\d.]+)\*\*\s*\((?:L30|репетиция 1):\s*([\d.]+)\s*→\s*([\d.]+)\)", t2)
    if m:
        res["private_asis"], res["private_trained"] = _fl(m.group(1)), _fl(m.group(2))
        res["private_trained_prev"] = _fl(m.group(4))
    m = re.search(r"\*\*(\d+):(\d+)\*\*\s*\|\s*`predict_org", t2)
    if m:
        res["trained_submit_min"] = f"{m.group(1)}:{m.group(2)}"
    m = re.search(r"Это\s*(\d+)\s*сцен", t2)
    res["n_scenes"] = int(m.group(1)) if m else None
    m = re.search(r"без пикселей sea snot:\*\*[^=]*=[^=]*=\s*\*\*([\d.]+)\*\*", t35, re.I)
    if m:
        res["labelled_no_seasnot_f1"] = _fl(m.group(1))
    m = re.search(r"Не размечено\s*([\d.]+)\s*%", t35)
    res["unlabelled_pct"] = _fl(m.group(1)) if m else None
    j = _load_json(ROOT / "reports" / "l35_asis_check.json")
    if isinstance(j, dict):
        res["n_test_chips"] = j.get("n_chips")
        b = ((j.get("best") or {}).get("chain") or {}).get("all")
        if isinstance(b, list) and len(b) == 2:
            res["best_all_px_thr"], res["best_all_px_f1"] = b[0], _r((b[1] or {}).get("f1"), 3)
        lab = (((j.get("metrics") or {}).get("chain") or {}).get("lab") or {}).get(str(j.get("threshold_meta")))
        res["labelled_f1_asis"] = _r((lab or {}).get("f1"), 3)
    return res


def collect_test_status(l3: dict) -> dict:
    done = bool(l3.get("test"))
    return {"computed": done,
            "text": ("Test MARIDA посчитан один раз на итоговой модели, после этого модель не менялась." if done
                     else "Test MARIDA будет посчитан один раз на итоговой модели; до этого все решения принимались только по val.")}


# ----------------------------------------------------------------------------------------------- UNet
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


def collect_service(roots=None) -> dict:
    res = {"data_root": None, "kind": None, "n_regions": 0, "n_dates": 0, "n_detections_total": None,
           "models": None, "mdd_threshold": None, "lgbm_threshold_map": None, "n_drift": 0,
           "n_confirmed_total": None, "n_dates_with_confirmed": None, "n_confirmed_latest_total": None, "regions": []}
    for cand in (roots or (ROOT / "service" / "data", ROOT / "service" / "demo")):
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
    conf_tot, conf_dates, conf_latest, any_conf = 0, 0, 0, False
    for r in man.get("regions") or []:
        dates = r.get("dates") or []
        summ = r.get("summary") or {}
        res["n_dates"] += len(dates)
        res["n_drift"] += sum(1 for d in dates if d.get("drift"))
        if summ.get("n_detections") is not None:
            tot += int(summ["n_detections"])
            any_det = True
        model = summ.get("model") or "mdd"
        latest_conf = None
        for d in dates:
            nc = d.get("n_confirmed")
            if isinstance(nc, dict) and nc.get(model) is not None:
                any_conf = True
                conf_tot += int(nc[model])
                conf_dates += 1 if int(nc[model]) > 0 else 0
                if d.get("date") == summ.get("latest_date"):
                    latest_conf = int(nc[model])
        if latest_conf is not None:
            conf_latest += latest_conf
        res["regions"].append({
            "id": r.get("id"), "name": r.get("name"), "country": r.get("country"), "tile": r.get("tile"),
            "dates": [d.get("date") for d in dates], "latest_date": summ.get("latest_date"),
            "model": summ.get("model"), "index_permille": _r(summ.get("index_permille"), 4),
            "n_detections": summ.get("n_detections"),
            "total_debris_area_km2": _r((summ.get("total_debris_area_m2") or 0) / 1e6, 6)
            if summ.get("total_debris_area_m2") is not None else None,
            "n_dates": len(dates), "n_drift": sum(1 for d in dates if d.get("drift")),
            "n_confirmed_latest": latest_conf,
            "n_zones": _count_zones(res["data_root"], r.get("id"), summ.get("latest_date"), summ.get("model")),
            **_region_reliability(dates),
        })
    res["n_regions"] = len(res["regions"])
    # Consistency pass: ranking as on the site (rankRegions in service/frontend/src/lib/data.ts): reliable regions by index desc,
    # then unreliable ones by index desc
    by_idx = sorted(res["regions"], key=lambda x: -(x.get("index_permille") if x.get("index_permille") is not None else -1))
    res["ranking_reliable"] = [x["id"] for x in by_idx if x.get("reliable")]
    res["ranking_unreliable"] = [x["id"] for x in by_idx if not x.get("reliable")]
    res["n_reliable"] = len(res["ranking_reliable"])
    res["n_detections_total"] = tot if any_det else None
    if any_conf:
        res["n_confirmed_total"], res["n_dates_with_confirmed"] = conf_tot, conf_dates
        res["n_confirmed_latest_total"] = conf_latest
    return res


def _date_unreliable(d) -> str:
    """'' if the date is fine for ranking, else the reason. Same rule as isUnreliableDate / regionReliability in
    service/frontend/src/lib/data.ts: haze/glint flag or cloud > 50 %."""
    if not isinstance(d, dict):
        return "нет снимков"
    q = d.get("quality") or {}
    if q.get("haze") or q.get("glint_or_haze"):
        return "дымка/блик"
    if (d.get("cloud_frac") or 0) > 0.5:
        return "облачность > 50 %"
    return ""


def _region_reliability(dates) -> dict:
    """Consistency pass: a region is unreliable if its latest scene is unreliable (as on the site)."""
    ds = sorted((d for d in dates if isinstance(d, dict)), key=lambda d: d.get("date") or "")
    last = ds[-1] if ds else None
    why = _date_unreliable(last)
    return {"last_date": last.get("date") if last else None, "reliable": not why, "unreliable_reason": why or None}


def _count_zones(root, region, date, model):
    if not (root and region and date and model):
        return None
    z = _load_json(ROOT / root / region / date / model / "zones.json")
    if isinstance(z, dict) and isinstance(z.get("zones"), list):
        return len(z["zones"])
    return None


def collect_ui_perf() -> dict:
    """reports/ui_perf.md: the last measurement = the last non-empty line; numbers missing there are looked up
    in the line before it (a table row followed by its prose summary)."""
    p = ROOT / "reports" / "ui_perf.md"
    if not p.exists():
        return {"available": False, "last_line": None}
    lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    last = lines[-1] if lines else None
    res = {"available": True, "last_line": last, "load_s": None, "flyto_fps": None, "drift_fps": None,
           "globe_fps": None, "tour_s": None, "bundle_gzip_mb": None, "console_errors": None}
    num = r"(\d+(?:[.,]\d+)?)"
    pats = (("load_s", rf"load\s+{num}(?:\s*[–-]\s*{num})?\s*с"),
            ("flyto_fps", rf"(?:flyTo|облёт)\s+{num}(?:\s*[–-]\s*{num})?\s*fps"),
            ("drift_fps", rf"дрейф\s+{num}\s*fps"),
            ("globe_fps", rf"вращение\s+{num}\s*fps"),
            ("tour_s", rf"тур\s+{num}\s*с"),
            ("bundle_gzip_mb", rf"gzip\s+{num}\s*МБ"))
    for ln in reversed(lines[-2:]):
        for key, pat in pats:
            if res[key] is not None:
                continue
            m = re.search(pat, ln, re.I)
            if m:
                vals = [float(g.replace(",", ".")) for g in m.groups() if g]
                res[key] = vals[-1] if key == "load_s" else vals[0]  # worst load, lowest fps
        if res["console_errors"] is None:
            m = re.search(r"ошибок\s+консоли\s+(\d+)", ln, re.I)
            res["console_errors"] = int(m.group(1)) if m else None
    return res


def collect_drift_check() -> dict:
    """reports/drift_check.json: forecast vs 'zero drift' baseline on pairs of scenes. Experiment, not a product metric."""
    d = _load_json(ROOT / "reports" / "drift_check.json")
    if not isinstance(d, dict):
        return {"available": False}
    ph = d.get("posthoc") or {}
    return {"available": True, "label": "эксперимент",
            "n_pairs": d.get("n_pairs"), "n_pairs_with_det2": d.get("n_pairs_with_det2"),
            "k_hit": d.get("k_hit"), "k_hit_baseline": d.get("k_hit_baseline"), "k_hit_shift": d.get("k_hit_shift"),
            "hit_rate": _r(d.get("hit_rate"), 3), "baseline_hit_rate": _r(d.get("baseline_hit_rate"), 3),
            "shift_hit_rate": _r(d.get("shift_hit_rate"), 3),
            "pairs_forecast_better": d.get("pairs_forecast_better"), "pairs_baseline_better": d.get("pairs_baseline_better"),
            "n_pairs_verifiable": ph.get("n_pairs_verifiable"),
            "forecast_better": (d.get("k_hit") or 0) > (d.get("k_hit_baseline") or 0),
            "verdict": d.get("verdict"), "criterion": d.get("criterion")}


def collect_context() -> dict:
    """reports/context.json: OSM objects by kind and demo drift scenes touching objects. No hours / threats / sources."""
    d = _load_json(ROOT / "reports" / "context.json")
    if not isinstance(d, dict):
        return {"available": False}
    ob = d.get("objects_total") or {}
    return {"available": True, "objects_by_kind": ob, "objects_total": sum(v for v in ob.values() if isinstance(v, int)),
            "n_regions": len(d.get("regions") or {}) or None,
            "n_drift_scenes": d.get("n_drift_scenes"), "n_drift_scenes_touching": d.get("n_drift_scenes_touching"),
            "n_drift_objects": d.get("n_drift_objects"),
            "note": "объекты OSM (ODbL) для контекста; пересечение с демо-дрейфом без сроков и вероятностей, не предупреждение"}


def collect_incidents(root: Path | None = None) -> dict:
    """Incidents computed with the API code (service/routes_incidents.py, same as /api/incidents/summary)."""
    res = {"available": False, "data_root": None, "total": None, "by_kind": None, "excluded_artifacts": None,
           "reviewed": None, "confirmed_reviewed": None, "confirmed_share_of_reviewed": None}
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from service import core as score  # noqa: WPS433
        from service import routes_incidents as ri  # noqa: WPS433
        st = score.Store.open(root or next((c for c in (ROOT / "service" / "data", ROOT / "service" / "demo")
                                            if (c / "manifest.json").is_file()), None))
        if not st.has_data():
            return res
        sm = ri.summary(ri.state(st)["incidents"])
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"
        return res
    res.update({"available": True, "data_root": str(Path(st.root).relative_to(ROOT)).replace("\\", "/"),
                "total": sm.get("total"), "by_kind": sm.get("by_kind"), "by_status": sm.get("by_status"),
                "excluded_artifacts": (sm.get("by_status") or {}).get("excluded"),
                "reviewed": sm.get("reviewed"), "confirmed_reviewed": sm.get("confirmed_reviewed"),
                "confirmed_share_of_reviewed": sm.get("confirmed_share_of_reviewed")})
    return res


# ----------------------------------------------------------------------------------------------- case (macroplastic, шт./км²)
CASE_PROFILES = {"S2": "S2_visual_total_plastic", "S1": "S1_trawl_total_plastic"}
DET_MODELS = ("lgbm", "rf_argmax", "rf_prob", "fdi_ndvi_box", "fdi_interval", "fdi_threshold", "ndvi_threshold")


def _load_yaml(p: Path):
    try:
        import yaml  # noqa: WPS433
        with open(p, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:  # noqa: BLE001
        return None


def _ci2(x, nd=1):
    if not (isinstance(x, (list, tuple)) and len(x) == 2) or any(v is None or v != v for v in x):
        return None
    return [_r(x[0], nd), _r(x[1], nd)]


def _case_csv() -> dict:
    """task/macroplastic_marine_samples.csv: rows, events, events by source."""
    p = ROOT / "task" / "macroplastic_marine_samples.csv"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    ev = {}
    for r in rows:
        ev.setdefault(r.get("source_id"), set()).add(r.get("event_id"))
    return {"rows": len(rows), "fields": len(rows[0]) if rows else None,
            "events": len({r.get("event_id") for r in rows}),
            "events_by_source": {k: len(v) for k, v in sorted(ev.items())},
            "rows_by_scope": {k: sum(1 for r in rows if r.get("target_scope") == k)
                              for k in sorted({r.get("target_scope") for r in rows})}}


def _case_target(profile: str) -> dict | None:
    """Accepted rows of a profile (tracked copy reports/case_splits/selection_<profile>.csv)."""
    p = ROOT / "reports" / "case_splits" / f"selection_{profile}.csv"
    if not p.exists():
        p = ROOT / "data" / "case" / f"selection_{profile}.csv"
    if not p.exists():
        return None
    import statistics as st
    with open(p, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    y = [float(r["target"]) for r in rows if r.get("target") not in (None, "")]
    n = [float(r["density_numerator_items"]) for r in rows if r.get("density_numerator_items") not in (None, "")]
    a = [float(r["sampled_area_km2"]) for r in rows if r.get("sampled_area_km2") not in (None, "")]
    days = {r.get("date_utc") for r in rows}
    return {"n_rows": len(rows), "n_events": len({r.get("event_id") for r in rows}), "n_days": len(days),
            "date_min": min(days) if days else None, "date_max": max(days) if days else None,
            "c_median": _r(st.median(y), 1) if y else None, "c_mean": _r(st.mean(y), 1) if y else None,
            "c_min": _r(min(y), 1) if y else None, "c_max": _r(max(y), 1) if y else None,
            "n_zero": sum(1 for v in y if v == 0), "n_with_n": len(n), "n_with_a": len(a),
            "n_median": _r(st.median(n), 1) if n else None, "n_min": _r(min(n), 0) if n else None,
            "n_max": _r(max(n), 0) if n else None,
            "a_median": _r(st.median(a), 3) if a else None, "a_min": _r(min(a), 3) if a else None,
            "a_max": _r(max(a), 3) if a else None}


def _case_pairs_summary_md() -> dict:
    """reports/case_pairs/summary.md (tracked): events with a scene by source/window, drift scenarios."""
    txt = _read("reports/case_pairs/summary.md")
    out = {"by_source": {}, "drift": {}}
    cols = ("events", "s2_1", "s2_3", "s2_5", "l_1", "l_3", "l_5", "any_1", "any_3", "any_5", "s2_1_cloud60", "l_1_cloud60")
    for m in re.finditer(r"^\|\s*([A-Z0-9_ВСЕ]+)\s*\|((?:\s*\d+\s*\|){12})\s*$", txt, re.M):
        vals = [int(x) for x in re.findall(r"\d+", m.group(2))]
        out["by_source"][m.group(1).strip()] = dict(zip(cols, vals))
    for m in re.finditer(r"^\|\s*(low|typical|high)\s*\|\s*([\d.]+)\s*\|\s*(\d+) соб\. / \d+\s*\|\s*(\d+) соб\. / \d+\s*\|"
                         r"\s*(\d+) соб\. / \d+\s*\|\s*(\d+) соб\. / \d+\s*\|\s*([\d.]+) \(([\d.]+)–([\d.]+)\)", txt, re.M):
        out["drift"][m.group(1)] = {"current_ms": _fl(m.group(2)), "events_buf05": int(m.group(3)),
                                    "events_buf3": int(m.group(4)), "events_buf10": int(m.group(5)),
                                    "events_half_transect": int(m.group(6)), "shift_median_km": _fl(m.group(7)),
                                    "shift_p25_km": _fl(m.group(8)), "shift_p75_km": _fl(m.group(9))}
    m = re.search(r"Предельное \|dt\|[^≈]*≈\s*([\d.]+)\s*ч", txt)
    out["max_dt_h_typical"] = _fl(m.group(1)) if m else None
    m = re.search(r"событий без времени \(окно по суткам\):\s*(\d+)", txt)
    out["events_time_unknown"] = int(m.group(1)) if m else None
    reasons = {}
    for m in re.finditer(r"^\|\s*([a-z_]+(?:[<>]\d+\w*)?(?:\([^)]*\))?)\s*\|\s*(\d+)\s*\|\s*$", txt, re.M):
        reasons[m.group(1)] = int(m.group(2))
    out["reject_rows"] = reasons or None
    out["reject_dt_gt_1d"] = reasons.get("dt>1d")
    out["reject_cloud_cover_gt_60"] = reasons.get("cloud_cover>60")
    return out


def _paired_scene_bootstrap(per_patch: Path, a: str, b: str, reps=2000, seed=0):
    """ΔF1 (a − b) on MARIDA test, paired bootstrap over scenes (per_patch.csv: tp/fp/fn by patch)."""
    try:
        import numpy as np
        with open(per_patch, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        scenes = sorted({r["scene"] for r in rows})
        agg = {s: np.zeros(6) for s in scenes}
        for r in rows:
            agg[r["scene"]] += [float(r[f"{a}_tp"]), float(r[f"{a}_fp"]), float(r[f"{a}_fn"]),
                                float(r[f"{b}_tp"]), float(r[f"{b}_fp"]), float(r[f"{b}_fn"])]
        m = np.stack([agg[s] for s in scenes])

        def f1(v, o):
            tp, fp, fn = v[..., o], v[..., o + 1], v[..., o + 2]
            return 2 * tp / np.maximum(2 * tp + fp + fn, 1e-9)
        tot = m.sum(0)
        point = float(f1(tot, 0) - f1(tot, 3))
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, len(scenes), size=(reps, len(scenes)))
        s = m[idx].sum(1)
        d = f1(s, 0) - f1(s, 3)
        per_scene = f1(m, 0) - f1(m, 3)
        has_md = (m[:, 0] + m[:, 2]) > 0
        return {"delta_f1": _r(point, 3), "ci95": [_r(np.quantile(d, 0.025), 3), _r(np.quantile(d, 0.975), 3)],
                "p_le_0": _r(float((d <= 0).mean()), 4), "n_scenes": len(scenes), "reps": reps, "seed": seed,
                "scenes_better": int(((per_scene > 1e-12) & has_md).sum()),
                "scenes_worse": int(((per_scene < -1e-12) & has_md).sum())}
    except Exception:  # noqa: BLE001
        return None


def _case_detector() -> dict:
    d = _load_json(ROOT / "reports" / "case_detector" / "metrics.json")
    if not isinstance(d, dict):
        return {"available": False}
    out = {"available": True, "source": "reports/case_detector/metrics.json", "val": {}, "test": {},
           "reproducible_vs_final_test": (d.get("reproducibility_vs_lgbm_final_test") or {}).get("all_equal"),
           "seconds": _r(d.get("seconds"), 0)}
    for split in ("val", "test"):
        for k in DET_MODELS:
            m = (d.get(split) or {}).get(k)
            if not isinstance(m, dict):
                continue
            out[split][k] = {"precision": _r(m.get("precision_md"), 3), "recall": _r(m.get("recall_md"), 3),
                             "f1": _r(m.get("f1_md"), 3), "iou": _r(m.get("iou_md"), 3),
                             "ci95_f1": _ci2(m.get("ci95_f1"), 3), "tp": m.get("tp"), "fp": m.get("fp"), "fn": m.get("fn"),
                             "unlabelled_rate_pct": _r(100 * (m.get("unlabelled_pred_md_rate") or 0), 3),
                             "n_patches": m.get("n_patches"), "n_scenes": m.get("n_scenes"), "md_px": m.get("md_px")}
    for split in ("val", "test"):
        lg = out[split].get("lgbm") or {}
        out[f"{split}_n_patches"], out[f"{split}_n_scenes"], out[f"{split}_md_px"] = (
            lg.get("n_patches"), lg.get("n_scenes"), lg.get("md_px"))
    set_ = d.get("settings") or {}
    out["settings"] = {k: (v or {}).get("setting") for k, v in set_.items()}
    # FP of the main model on MARIDA test by background class
    pc = (((d.get("test") or {}).get("lgbm") or {}).get("per_class")) or {}
    fp = {k: {"n": v.get("n"), "fp": v.get("pred_md"), "rate_pct": _r(100 * (v.get("rate") or 0), 2)}
          for k, v in pc.items() if k not in ("Marine Debris", "Unlabelled") and isinstance(v, dict)}
    out["fp_by_class"] = fp
    out["fp_total"] = sum(v["fp"] or 0 for v in fp.values()) or None
    hard = ("Dense Sargassum", "Sparse Sargassum", "Turbid Water", "Sediment-Laden Water")
    out["fp_hard_bg_px"] = sum((fp.get(k) or {}).get("n") or 0 for k in hard) or None
    out["fp_hard_bg"] = sum((fp.get(k) or {}).get("fp") or 0 for k in hard)
    ship, org = (fp.get("Ship") or {}).get("fp") or 0, (fp.get("Natural Organic Material") or {}).get("fp") or 0
    out["fp_ship_organic_pct"] = _r(100 * (ship + org) / out["fp_total"], 0) if out["fp_total"] else None
    pcr = (((d.get("test") or {}).get("fdi_ndvi_box") or {}).get("per_class")) or {}
    out["fdi_ndvi_box_rate_pct"] = {k: _r(100 * (v.get("rate") or 0), 0) for k, v in pcr.items()
                                   if k in ("Dense Sargassum", "Sparse Sargassum", "Clouds", "Ship", "Foam")}
    sg = [pcr.get(k) or {} for k in ("Dense Sargassum", "Sparse Sargassum")]
    n_sg = sum(v.get("n") or 0 for v in sg)
    out["sargassum_px"] = n_sg or None
    out["fdi_ndvi_box_sargassum_pct"] = _r(100 * sum(v.get("pred_md") or 0 for v in sg) / n_sg, 0) if n_sg else None
    # FN concentrated in few scenes
    fns = []
    p = ROOT / "reports" / "case_detector" / "fn_by_scene.csv"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            fns = sorted(((r["scene"], int(float(r["lgbm_fn"]))) for r in csv.DictReader(f)), key=lambda x: -x[1])
    out["fn_top3"] = sum(x[1] for x in fns[:3]) if fns else None
    out["fn_top3_scenes"] = [x[0] for x in fns[:3]] or None
    out["paired_vs_rf"] = _paired_scene_bootstrap(ROOT / "reports" / "case_detector" / "per_patch.csv", "lgbm", "rf_argmax")
    return out


def _case_dev_row(table, model):
    for r in table or []:
        if r.get("model") == model:
            return {"mae": _r(r.get("mae"), 1), "rmse": _r(r.get("rmse"), 1), "median_ae": _r(r.get("median_ae"), 1),
                    "log1p_mae": _r(r.get("log1p_mae"), 3), "bias": _r(r.get("bias"), 1),
                    "coverage90_pct": _r(100 * r["coverage90"], 0) if r.get("coverage90") is not None else None,
                    "width_median": _r(r.get("width_median"), 0),
                    "d_mae": _r(r.get("d_mae"), 1), "d_mae_ci95": _ci2([r.get("d_mae_lo"), r.get("d_mae_hi")]),
                    "d_mae_ci95_bonf": _ci2([r.get("d_mae_bonf_lo"), r.get("d_mae_bonf_hi")])}
    return None


def _dt_ru(s):
    """'2026-09-26T12:00' -> '26.09.2026 12:00'."""
    try:
        return dt.datetime.fromisoformat(str(s)).strftime("%d.%m.%Y %H:%M")
    except Exception:  # noqa: BLE001
        return None


def _case_conc() -> dict:
    dev = _load_json(ROOT / "reports" / "case_conc" / "dev_cv.json") or {}
    met = _load_json(ROOT / "reports" / "case_conc" / "metrics.json") or {}
    sel = _load_yaml(ROOT / "configs" / "case_selection.yaml") or {}
    mdl = _load_yaml(ROOT / "configs" / "case_conc_model.yaml") or {}
    ft_cfg = (sel.get("final_test") or {})
    ft_res = _load_json(ROOT / (ft_cfg.get("result") or "reports/case_conc/final_test.json"))
    proto = (dev.get("protocol") or mdl.get("protocol") or {})
    out = {"available": bool(dev), "main_split": sel.get("main_split"),
           "protocol": {"k_blocks": (proto.get("cv") or {}).get("k_blocks"),
                        "buffer_days": (proto.get("cv") or {}).get("buffer_days"),
                        "n_candidates": len(proto.get("candidates") or []) or None,
                        "candidates": proto.get("candidates"), "baseline": proto.get("baseline"),
                        "acceptance_rule": proto.get("acceptance_rule"), "interval": proto.get("interval")},
           "final_test_done": isinstance(ft_res, dict),
           "final_test_not_before": ft_cfg.get("not_before"), "final_test_frozen_at": ft_cfg.get("frozen_at"),
           "final_test_frozen_text": _dt_ru(ft_cfg.get("frozen_at")),
           "consistency": met.get("consistency")}
    out["final_test_status"] = ("посчитан один раз" if out["final_test_done"] else
                                f"будет посчитан один раз в приёмке, не раньше {_dt_ru(ft_cfg.get('not_before')) or ft_cfg.get('not_before')}")
    for key, prof in CASE_PROFILES.items():
        p = (dev.get("profiles") or {}).get(prof) or {}
        table = p.get("table") or []
        prim = p.get("primary")
        sens = {}
        for s in p.get("sensitivity_k_blocks") or []:
            if s.get("model") == prim:
                sens[f"k{s.get('k_blocks')}"] = {"d_mae": _r(s.get("d_mae"), 1),
                                                  "ci95": _ci2([s.get("d_mae_lo"), s.get("d_mae_hi")])}
        ftp = (ft_cfg.get("profiles") or {}).get(prof) or {}
        m = met.get(prof) or {}
        boot = m.get("bootstrap_mae_diff_vs_median") or {}
        split = m.get("main_split") or "route_buf1"

        def ov(model):
            for r in m.get("overall") or []:
                if r.get("split") == split and r.get("model") == model:
                    return _r(r.get("mae"), 1)
            return None
        bk = boot.get(f"{split}:knn5_log")
        nb = m.get("knn_neighbors_within_1d") or {}
        schemes = []
        for sp in ("event", "daycell", "cruiseday", "st", "route", "route_buf1"):
            mae = {r.get("model"): r for r in (m.get("overall") or []) if r.get("split") == sp}
            if not mae:
                continue
            b = boot.get(f"{sp}:knn5_log")
            chk = (m.get("split_checks") or {}).get(sp) or []
            schemes.append({"split": sp, "median_mae": _r((mae.get("median") or {}).get("mae"), 1),
                            "knn5_log_mae": _r((mae.get("knn5_log") or {}).get("mae"), 1),
                            "d": _r(b[0], 1) if isinstance(b, list) and b else None,
                            "ci95": _ci2(b[1:]) if isinstance(b, list) and len(b) == 3 else None,
                            "neighbors_1d_pct": _r(100 * nb[sp], 0) if nb.get(sp) is not None else None,
                            "shared_events": sum(int(c.get("shared_events") or 0) for c in chk) if chk else None,
                            "shared_cruise_days": sum(int(c.get("shared_cruise_days") or 0) for c in chk) if chk else None})
        sel_m = ((mdl.get("selected") or {}).get(prof) or {})
        out[key] = {
            "profile": prof, "target": _case_target(prof),
            "n_dev": p.get("n_dev"), "dev_days": p.get("dev_cruise_days"),
            "dev_median_c": _r((p.get("dev_target") or {}).get("median"), 1),
            "primary": prim, "why": p.get("why"),
            "features": sel_m.get("features"), "interval_q": [_r(x, 2) for x in (p.get("q_app") or [])] or None,
            "median": _case_dev_row(table, "median"), "main": _case_dev_row(table, prim),
            "knn5_log": _case_dev_row(table, "knn5_log"), "ridge_log": _case_dev_row(table, "ridge_log"),
            "poisson_glm": _case_dev_row(table, "poisson_glm"), "geomean": _case_dev_row(table, "geomean"),
            "accepted_models": [r.get("model") for r in table if r.get("d_mae_hi") is not None
                                and r.get("d_mae_hi") == r.get("d_mae_hi") and r["d_mae_hi"] < 0] or None,
            "sensitivity": sens or None,
            "table": [{"model": r.get("model"), **(_case_dev_row(table, r.get("model")) or {})} for r in table] or None,
            "final_test": {"n_test": ftp.get("n_test"), "n_buffer": ftp.get("n_buffer"), "n_dev": ftp.get("n_dev"),
                           "test_days": ftp.get("test_days"), "min_dt_days": _r(ftp.get("min_dt_days"), 2),
                           "min_dist_km": _r(ftp.get("min_dist_km"), 0),
                           "test_sha256_short": (ftp.get("test_sha256") or "")[:12] or None,
                           "dev_sha256_short": (ftp.get("dev_sha256") or "")[:12] or None,
                           "result": ((ft_res or {}).get("profiles") or {}).get(prof) if isinstance(ft_res, dict) else None},
            "schemes": schemes or None,
            "all_events_route_buf1": {"median_mae": ov("median"), "knn5_log_mae": ov("knn5_log"), "knn5_mae": ov("knn5"),
                                      "d_knn5_log_ci95": _ci2(bk[1:]) if isinstance(bk, list) and len(bk) == 3 else None,
                                      "d_knn5_log": _r(bk[0], 1) if isinstance(bk, list) and bk else None,
                                      "n": m.get("rows")},
        }
    return out


def _case_experiment() -> dict:
    e = _load_json(ROOT / "reports" / "case_pairs" / "experiment.json")
    if not isinstance(e, dict):
        return {"available": False}
    res = e.get("results") or []
    main = res[0] if res else {}
    feats = main.get("features") or {}

    def fe(name):
        f = feats.get(name) or {}
        nl = f.get("null") or {}
        return {"n": f.get("n"), "rho": _r(f.get("spearman"), 2), "ci95": _ci2(f.get("spearman_ci95_groupboot"), 2),
                "p_perm": _r(f.get("p_perm_spearman"), 3), "p_holm": _r(f.get("p_holm"), 2),
                "null_median": _r(nl.get("null_median"), 2), "null_frac_ge": _r(nl.get("frac_null_abs_ge_obs"), 2)}
    pairs = e.get("pairs") or []
    return {"available": True, "source": "reports/case_pairs/experiment.json",
            "n_pairs": e.get("n_pairs_total"), "n_accept": e.get("n_accept"),
            "n_accept_s2": main.get("n"), "n_groups": main.get("n_groups"),
            "n_s2_pairs": sum(1 for p in pairs if p.get("has_detector")) or None,
            "n_landsat_pairs": sum(1 for p in pairs if not p.get("has_detector")) or None,
            "n_black_sea_accept_zero_det": sum(1 for p in pairs if p.get("status") == "accept" and p.get("has_detector")
                                               and str(p.get("region", "")).startswith("S4") and not p.get("s_frac_p_thr")),
            "field_min": _r(min((p["field_items_km2"] for p in pairs if p.get("status") == "accept"
                                 and p.get("field_items_km2") is not None), default=None), 0),
            "field_max": _r(max((p["field_items_km2"] for p in pairs if p.get("status") == "accept"
                                 and p.get("field_items_km2") is not None), default=None), 0),
            "fdi": fe("s_fdi_p95_anom"), "p_mean": fe("s_p_mean"), "fdi_context": fe("c_fdi_p95"),
            "fdi_strip_resid": fe("x_fdi_p95_anom"),
            "power_min_rho": {str(k): (v or {}).get("min_abs_rho_fisher") for k, v in (e.get("power") or {}).items()},
            "n_needed": e.get("n_needed"), "n_needed_rho05": (e.get("n_needed") or {}).get("0.5"),
            "n_needed_rho03": (e.get("n_needed") or {}).get("0.3"), "threshold_p": e.get("threshold_p")}


def _case_selfcheck() -> dict:
    latest = ROOT / "reports" / "selfcheck" / "consistency_latest.json"  # stable copy in git; dated files are ignored
    files = [str(latest)] if latest.is_file() else sorted(
        glob.glob(str(ROOT / "reports" / "selfcheck" / "consistency_2*.json")))
    if not files:
        return {"available": False}
    d = _load_json(Path(files[-1])) or {}
    def _rows(x):
        return x.get("rows") or [] if isinstance(x, dict) else (x if isinstance(x, list) else [])
    p95 = [r.get("p95_ms") for key in ("speed_testclient", "speed_live") for r in _rows(d.get(key))
           if isinstance(r, dict) and r.get("p95_ms") is not None and (r.get("target_ms") or 0) <= 300]
    inv = d.get("invalid_inputs") or []
    return {"available": True, "file": str(Path(files[-1]).relative_to(ROOT)).replace("\\", "/"), "when": d.get("when"),
            "checks": len(d.get("checks") or []) or None,
            **({"ok": 0, "fail": 0, "warn": 0} if d.get("stats") else {}), **(d.get("stats") or {}), "seconds": _r(d.get("seconds"), 0),
            "invalid_ok": sum(1 for x in inv if isinstance(x, dict) and x.get("ok")), "invalid_total": len(inv) or None,
            "p95_max_ms": _r(max(p95), 0) if p95 else None}


def _case_pairfinder() -> dict:
    """reports/case_pairfinder/evidence_numbers.json: «Подобрать снимок» на событиях реестра (офлайн, кэш STAC)."""
    d = _load_json(ROOT / "reports" / "case_pairfinder" / "evidence_numbers.json")
    if not isinstance(d, dict):
        return {"available": False}
    fc = d.get("forecast_hindcast") or {}
    return {"available": True, "source": "reports/case_pairfinder/evidence_numbers.json (разбор: evidence.md)",
            "window_h": d.get("max_abs_dt_h_typical_3km"), "naive": d.get("naive_pm1d_meta_accept"),
            "naive_sync": d.get("naive_pm1d_with_drift"), "cond_date_only": d.get("pairfinder_conditional_date_only"),
            "cond_quality_accept": (d.get("conditional_with_l61_quality") or {}).get("accept"),
            "sync_time_known": d.get("pairfinder_synchronous_time_known"),
            "forecast_n": fc.get("predictions_checked"), "forecast_hit": fc.get("hit_within_5min")}


def _case_geometry() -> dict:
    """data/case/geometry/transects.csv: геометрия трансект S2/S3 по PANGAEA (строка = сегмент)."""
    f = ROOT / "data" / "case" / "geometry" / "transects.csv"
    if not f.exists():
        return {"available": False}
    import csv as _csv
    with f.open(encoding="utf-8") as fh:
        rows = list(_csv.DictReader(fh))
    return {"available": True, "source": "data/case/geometry/transects.csv (разбор: reports/case_geometry/summary.md)",
            "n_events": len({r["event_id"] for r in rows}), "n_segments": len(rows),
            "n_multi": len({r["event_id"] for r in rows if int(float(r.get("n_segments") or 1)) > 1})}


def _case_tests() -> dict:
    """reports/case_run/case_tests.json: фактический прогон тестов кейса (пишет scripts/case/build_docs.py)."""
    d = _load_json(ROOT / "reports" / "case_run" / "case_tests.json")
    if not isinstance(d, dict) or d.get("passed") is None:
        return {"available": False}
    return {"available": True, "source": "reports/case_run/case_tests.json", **{k: d.get(k) for k in
            ("passed", "skipped", "failed", "seconds", "when", "command")}}


def _case_detector_review() -> dict:
    """reports/case_pairs/detector_review.json: the detector on the real L2A crops of the pairs (types of objects by rules,
    effect of the water_median harmonisation). Rules-based typing, no manual labels."""
    d = _load_json(ROOT / "reports" / "case_pairs" / "detector_review.json")
    if not isinstance(d, dict):
        return {"available": False}
    s = d.get("summary") or {}
    tot, grp = s.get("totals") or {}, s.get("by_group") or {}
    he, wo = s.get("he460_t03") or {}, s.get("without_he460_t03") or {}
    rg = grp.get("reject_glint") or {}
    crops = d.get("crops") or []

    def pct(x):
        return _r(100 * x, 1) if x is not None else None
    sh_wo = wo.get("share_n") or {}
    he_objs = (d.get("objects") or {}).get("S3_HE460_MarLitter_transect03") or []
    noh = [((c.get("no_harmonize") or {}).get("None") or {}) for c in crops]
    rg_in = {k: (v or {}).get("n_in", 0) for k, v in (rg.get("by_type") or {}).items()}
    false_types = ("ship", "wake", "seam", "cloud_edge", "cloud_small", "glint")
    t25 = next((c for c in crops if str(c.get("dir", "")).endswith("DOORS3_T25")), {})
    return {"available": True, "source": "reports/case_pairs/detector_review.json (разбор: reports/case_pairs/detector_review.md)",
            "protocol": "22 вырезки Sentinel-2 L2A пар; объекты пересобраны как в pair_quality.py; типы назначены правилами "
                        "(grid.artifacts, край облака, мелкое облако по B11, блик по B11, берег), без ручной разметки",
            "n_crops": tot.get("n_crops"), "n_obj": tot.get("n_obj"), "n_in_strip": tot.get("n_in_strip"),
            "false_share_pct": pct(tot.get("false_share_n")),
            "wo_he460_n_obj": wo.get("n_obj"), "wo_he460_false_share_pct": pct(wo.get("false_share_n")),
            "wo_he460_glint_pct": pct(sh_wo.get("glint")),
            "wo_he460_cloud_pct": pct((sh_wo.get("cloud_edge") or 0) + (sh_wo.get("cloud_small") or 0)),
            "accept_n_obj": (grp.get("accept") or {}).get("n_obj"), "accept_n_in_strip": (grp.get("accept") or {}).get("n_in_strip"),
            "glint_rejected_n_crops": rg.get("n_crops"), "glint_rejected_n_obj": rg.get("n_obj"),
            "glint_rejected_n_in_strip": rg.get("n_in_strip"),
            "glint_rejected_in_strip_false": sum(rg_in.get(k, 0) for k in false_types),
            "glint_rejected_false_share_pct": pct(rg.get("false_share_n")),
            "he460_n_obj": he.get("n_obj"), "he460_n_in_strip": he.get("n_in_strip"), "he460_n_out_strip": he.get("n_out_strip"),
            "he460_single_px": sum(1 for o in he_objs if o.get("n_px") == 1) or None,
            "he460_white_flat": ((he.get("other_spectrum") or {}).get("white_flat")),
            "he460_other": ((he.get("by_type") or {}).get("other_single") or {}).get("n", 0)
            + ((he.get("by_type") or {}).get("other_multi") or {}).get("n", 0),
            "no_harmonize_n_obj": sum(int(x.get("n_obj") or 0) for x in noh) if noh else None,
            "no_harmonize_n_in_strip": sum(int(x.get("n_in_strip") or 0) for x in noh) if noh else None,
            "t25_wake_lost_without_harmonize": bool(t25) and (((t25.get("no_harmonize") or {}).get("None") or {}).get("n_obj") == 0)
            and ((t25.get("no_harmonize") or {}).get("water_median") or {}).get("n_obj", 0) > 0,
            "marida_test_f1": None}


def _case_detector_current() -> dict:
    """Current detector on the pairs (reports/case_pairs/detector_current.json, scripts/case/pairs_detector_summary.py)
    + the MARIDA val choice of the harmonisation (reports/case_pairs/harmonize_val.json) + visual review of the former
    objects (reports/case_pairs/visual_review.json)."""
    d = _load_json(ROOT / "reports" / "case_pairs" / "detector_current.json")
    out = {"available": isinstance(d, dict)}
    if isinstance(d, dict):
        t = d.get("totals") or {}
        rt = (d.get("review_types") or {}).get("by_type") or {}
        out.update({"source": "reports/case_pairs/detector_current.json (из data/pairs/quality/*/meta.json)",
                    "protocol": d.get("protocol"), "harmonize": d.get("harmonize"),
                    "n_crops": t.get("n_crops"), "n_obj": t.get("n_obj"), "n_in_strip": t.get("n_in_strip"),
                    "n_crops_with_obj": t.get("n_crops_with_obj"), "n_strips_with_obj": t.get("n_strips_with_obj"),
                    "prob_max": _r(t.get("prob_max"), 2),
                    "crops_with_objects": ", ".join(k.replace("_MarLitter_", " ").replace("_", " ")
                                                    for k in (d.get("crops_with_objects") or {})) or "нет",
                    "n_ship": rt.get("ship"), "n_other_single": rt.get("other_single")})
    h = _load_json(ROOT / "reports" / "case_pairs" / "harmonize_val.json")
    if isinstance(h, dict):
        v = h.get("variants") or {}
        ps = (h.get("paired_bootstrap") or {}).get("per_scene_minus_none") or {}
        out["harmonize_val"] = {"source": "reports/case_pairs/harmonize_val.json",
                                "protocol": "MARIDA val (12 сцен), тот же LightGBM и порог; test не использовался",
                                "f1_none": _r((v.get("none") or {}).get("f1_md"), 3),
                                "f1_per_scene": _r((v.get("per_scene") or {}).get("f1_md"), 3),
                                "f1_per_patch": _r((v.get("per_patch") or {}).get("f1_md"), 3),
                                "delta_per_scene": _r(ps.get("delta_f1"), 3), "delta_per_scene_ci95": _ci2(ps.get("ci95"), 3)}
    vr = _load_json(ROOT / "reports" / "case_pairs" / "visual_review.json")
    if isinstance(vr, dict):
        ho, hi, nn = vr.get("H_out") or {}, vr.get("H_in_strip") or {}, vr.get("N_none") or {}
        out["visual_review"] = {"source": "reports/case_pairs/visual_review.json",
                                "protocol": vr.get("protocol"), "n_labelled": vr.get("n_labelled_objects"),
                                "h_out_n": ho.get("n"), "h_out_k": ho.get("k_accumulation"),
                                "h_out_precision_pct": _r(100 * (ho.get("precision_stratified") or 0), 1),
                                "h_out_ci95_pct": [_r(100 * x, 1) for x in (ho.get("ci95_stratified_bootstrap") or [])] or None,
                                "h_in_n": hi.get("n"), "h_in_k": hi.get("k_accumulation"),
                                "n_none_n": nn.get("n"), "n_none_k": nn.get("k_accumulation"),
                                "kappa": _r((vr.get("repeatability") or {}).get("kappa_7class"), 2),
                                "kappa_n": (vr.get("repeatability") or {}).get("n")}
    return out


def _case_clean_clone() -> dict:
    """Latest reports/selfcheck/clean_clone_*.md: a real `git clone` run by README (timings, tests, numbers)."""
    files = sorted(glob.glob(str(ROOT / "reports" / "selfcheck" / "clean_clone_*.md")))
    if not files:
        return {"available": False}
    txt = Path(files[-1]).read_text(encoding="utf-8")
    res = {"available": True, "file": str(Path(files[-1]).relative_to(ROOT)).replace("\\", "/")}

    def grab(key, pat, conv=str):
        m = re.search(pat, txt)
        res[key] = conv(m.group(1)) if m else None
    grab("total_min", r"до карты на экране прошло \*\*≈\s*([\d,]+)\s*мин")
    grab("clone_s", r"\|\s*1\s*\|[^|]*git clone[^|]*\|\s*(\d+)\s*с\s*\|")
    grab("install_route", r"\|\s*2\s*\|[^|]*run\.ps1[^|]*\|\s*([^|(]+?)\s*\(")
    grab("venv_gb", r"`\.venv` занимает ([\d,]+)\s*ГБ")
    grab("route_s", r"всего ([\d.]+) с \(в README")
    grab("map_load_s", r"загрузка ([\d,]+) с")
    grab("case_tests_s", r"\|\s*3\s*\|[^|]*pytest[^|]*\|\s*(\d+)\s*с\s*\|")
    grab("case_tests_passed", r"\|\s*3\s*\|[^|]*\|[^|]*\|\s*(\d+) passed", int)
    grab("case_tests_skipped", r"\|\s*3\s*\|[^|]*\|[^|]*\|\s*\d+ passed, (\d+) skipped", int)
    grab("full_tests_time", r"\|\s*3b\s*\|[^|]*\|\s*([^|]+?)\s*\|")
    grab("numpy_clone", r"В клоне стоит numpy ([\d.]+)")
    return res


def _case_sections(c: dict) -> dict:
    """The four evidence sections of the case, each number with its source and protocol (one source of numbers)."""
    det, cc, ex = c.get("detector") or {}, c.get("conc") or {}, c.get("experiment") or {}
    te = det.get("test") or {}
    sel = _load_yaml(ROOT / "configs" / "case_selection.yaml") or {}
    dec = ((sel.get("final_test") or {}).get("decision_before_opening")) or {}

    def ft(key):
        p = cc.get(key) or {}
        r = (p.get("final_test") or {}).get("result") or {}
        if not r:
            return {"computed": False, "n_test": (p.get("final_test") or {}).get("n_test")}
        mm, bm, d = r.get("main") or {}, r.get("baseline_metrics") or {}, r.get("main_vs_baseline") or {}
        sig = bool(r.get("main_better_significant"))
        return {"computed": True, "n_test": r.get("n_test"), "test_cruise_days": r.get("test_cruise_days"),
                "main_model": r.get("main_model"), "main_mae": _r(mm.get("mae"), 1), "main_rmse": _r(mm.get("rmse"), 1),
                "main_log1p_mae": _r(mm.get("log1p_mae"), 3),
                "main_coverage90_pct": _r(100 * mm["coverage90"], 0) if mm.get("coverage90") is not None else None,
                "median_mae": _r(bm.get("mae"), 1), "median_rmse": _r(bm.get("rmse"), 1),
                "median_log1p_mae": _r(bm.get("log1p_mae"), 3),
                "median_coverage90_pct": _r(100 * bm["coverage90"], 0) if bm.get("coverage90") is not None else None,
                "d_mae": _r(d.get("d_mae"), 1), "d_mae_ci95": _ci2(d.get("ci95"), 1),
                "main_better_significant": sig,
                "verdict": ("основная модель лучше медианы" if sig else
                            ("основная модель не лучше медианы" if (d.get("d_mae") or 0) > 0 else "разницы с медианой нет")),
                "field_estimate_model": r.get("main_model") if sig else "median"}
    ft_when = None
    ftj = _load_json(ROOT / "reports" / "case_conc" / "final_test.json")
    if isinstance(ftj, dict):
        ft_when = _dt_ru(ftj.get("when"))
    return {
        "marida_test": {
            "source": "reports/case_detector/metrics.json",
            "protocol": "официальный test MARIDA, один раз после заморозки; порог и настройки бейзлайнов — только по val; "
                        "класс Marine Debris против остальных размеченных классов, неразмеченные не считаются фоном; ДИ — бутстреп по сценам",
            "n_scenes": det.get("test_n_scenes"), "n_patches": det.get("test_n_patches"), "md_px": det.get("test_md_px"),
            "lgbm_f1": (te.get("lgbm") or {}).get("f1"), "lgbm_ci95": (te.get("lgbm") or {}).get("ci95_f1"),
            "rf_f1": (te.get("rf_argmax") or {}).get("f1"), "fdi_ndvi_f1": (te.get("fdi_ndvi_box") or {}).get("f1"),
            "delta_vs_rf": (det.get("paired_vs_rf") or {}).get("delta_f1"), "delta_vs_rf_ci95": (det.get("paired_vs_rf") or {}).get("ci95")},
        "field_dev": {
            "source": "reports/case_conc/dev_cv.json, configs/case_conc_model.yaml",
            "protocol": "кросс-валидация внутри dev по 5 участкам маршрута с буфером 1 сут (route_buf1); протокол и правило выбора записаны до CV; "
                        "ДИ разности MAE — бутстреп по дням рейса",
            "S2": {k: (cc.get("S2") or {}).get(k) for k in ("n_dev", "dev_days", "primary")} |
                  {"main_mae": ((cc.get("S2") or {}).get("main") or {}).get("mae"),
                   "median_mae": ((cc.get("S2") or {}).get("median") or {}).get("mae"),
                   "d_mae_ci95": ((cc.get("S2") or {}).get("main") or {}).get("d_mae_ci95")},
            "S1": {k: (cc.get("S1") or {}).get(k) for k in ("n_dev", "dev_days", "primary")} |
                  {"main_mae": ((cc.get("S1") or {}).get("main") or {}).get("mae"),
                   "median_mae": ((cc.get("S1") or {}).get("median") or {}).get("mae"),
                   "d_mae_ci95": ((cc.get("S1") or {}).get("main") or {}).get("d_mae_ci95")}},
        "field_test": {
            "source": "reports/case_conc/final_test.json, reports/case_conc/final_test_predictions.csv",
            "protocol": "отложенный участок маршрута (+ буфер 1 сут), состав зафиксирован до моделей (sha256 в configs/case_selection.yaml), "
                        "посчитан один раз; основная модель против медианы dev на тех же событиях; ДИ — бутстреп по дням рейса test",
            "computed": isinstance(ftj, dict), "when": ft_when,
            "decision_recorded_at": _dt_ru(dec.get("recorded_at")), "rule": dec.get("rule"), "limitation": dec.get("limitation"),
            "S2": ft("S2"), "S1": ft("S1")},
        "sat_experiment": {
            "source": "reports/case_pairs/experiment.json",
            "protocol": "пары, прошедшие маски качества; эталон — полевая плотность всего мусора (all_litter), не пластика; "
                        "ранговая связь Спирмена, бутстреп по группам «район × день», поправка Холма, нулевая модель — случайная вода той же сцены",
            "n_pairs": ex.get("n_accept_s2"), "n_groups": ex.get("n_groups"), "fdi_rho": (ex.get("fdi") or {}).get("rho"),
            "fdi_p_holm": (ex.get("fdi") or {}).get("p_holm"), "fdi_null_median": (ex.get("fdi") or {}).get("null_median"),
            "verdict": "связь не установлена"},
    }


def _case_search() -> dict:
    """Раздел «Расследование данных» (§11): scripts/case/collect_search.py, только файлы в git (офлайн)."""
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location("collect_search", ROOT / "scripts" / "case" / "collect_search.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m.collect()
    except Exception as e:  # noqa: BLE001
        print(f"[final_numbers] collect_search: {type(e).__name__}: {e}")
        return {"search": {"available": False, "error": f"{type(e).__name__}: {e}"}}


def _case_s54() -> dict:
    """INBOX §54 map layers: NASA GIBS overview (service/routes_nasa.py LAYERS), drone/aircraft/vessel frames
    (data/case/drones/index.json), PRIME synthetic scenes (data/case/prime/index.json). Counts only, no metrics."""
    out = {"source": "service/routes_nasa.py; data/case/drones/index.json; data/case/prime/index.json"}
    txt = _read("service/routes_nasa.py")
    daily = [int(m) for m in re.findall(r'"resolution_m":\s*(\d+),\s*"matrix_set":\s*"GoogleMapsCompatible_Level9"', txt)]
    out["nasa_res_m_min"], out["nasa_res_m_max"] = (min(daily), max(daily)) if daily else (None, None)
    dr = _load_json(ROOT / "data" / "case" / "drones" / "index.json") or {}
    sets = dr.get("sets") or []
    out["drones_n_sets"] = len(sets) or None
    out["drones_n_frames"] = sum(len(s.get("frames") or []) for s in sets) or None
    out["drones_n_sets_drone"] = sum((s.get("meta") or {}).get("sensor") == "drone" for s in sets) or None
    pr = _load_json(ROOT / "data" / "case" / "prime" / "index.json") or {}
    out["prime_n_scenes"] = len(pr.get("scenes") or []) or None
    return out


def collect_case() -> dict:
    """Case «макропластик, шт./км²»: selection, pairs, detector on MARIDA test, concentration (dev CV + frozen test),
    pairs experiment, run_all summary. Sources: reports/case_run/run_summary.json, reports/case_conc/*, configs/case_*.yaml,
    reports/case_detector/*, reports/case_pairs/*, reports/case_splits/*, reports/selfcheck/consistency_*.json."""
    rs = _load_json(ROOT / "reports" / "case_run" / "run_summary.json") or {}
    out = {"available": bool(rs), "csv": _case_csv()}
    sel = rs.get("selection") or {}
    out["selection"] = {"default_profile": sel.get("default_profile")}
    for key, prof in CASE_PROFILES.items():
        p = (sel.get("profiles") or {}).get(prof) or {}
        rs_ = p.get("reasons") or {}

        def rc(prefix):
            return next((v for k, v in rs_.items() if k.startswith(prefix)), None)
        out["selection"][key] = {"accepted_rows": p.get("accepted_rows"), "accepted_events": p.get("accepted_events"),
                                 "rejected_rows": p.get("rejected_rows"),
                                 "rej_item_observation": rc("item_observation"), "rej_plastic_category": rc("plastic_category"),
                                 "rej_all_litter": rc("all_litter"), "rej_fisheries": rc("fisheries_litter_category"),
                                 "rej_aerial": rc("S1_aerial_GT50"), "rej_other_source": rc("source_id_not_in_profile")}
    pr = rs.get("pairs") or {}
    qr = pr.get("quality_reject_reasons") or {}
    out["pairs"] = {"events": pr.get("events"), "candidate_rows": pr.get("candidate_rows"),
                    "candidate_rows_with_scene": pr.get("candidate_rows_with_scene"),
                    "rows_accept_meta": pr.get("rows_accept_meta"), "events_accept_meta": pr.get("events_accept_meta"),
                    "events_accept_drift": pr.get("events_accept_drift"),
                    "quality_accept": (pr.get("quality_decisions") or {}).get("accept"),
                    "quality_reject": (pr.get("quality_decisions") or {}).get("reject"),
                    "reject_glint": qr.get("glint"), "reject_cloud": sum(v for k, v in qr.items() if k.startswith("cloud")) or None,
                    "reject_cloud_qa_suspect": sum(v for k, v in qr.items() if k.startswith("cloud(")) or None,
                    "reject_coverage": qr.get("insufficient_coverage"),
                    "pairs_with_detections": pr.get("pairs_with_detections"),
                    "registry_reject": (pr.get("registry_status") or {}).get("reject"),
                    "registry_accept": (pr.get("registry_status") or {}).get("accept", 0),
                    "stage_metadata": (pr.get("registry_stage") or {}).get("metadata"),
                    "stage_drift": (pr.get("registry_stage") or {}).get("drift"),
                    "accept_without_drift": (pr.get("registry_status_without_drift") or {}).get("accept"),
                    **_case_pairs_summary_md()}
    cfg = _load_yaml(ROOT / "configs" / "case_pairs.yaml") or {}
    dr = cfg.get("drift") or {}
    out["pairs"]["tolerance_buffer_km"] = ((dr.get("tolerance") or {}).get("buffer_km"))
    out["pairs"]["unknown_time_extra_h"] = dr.get("unknown_time_extra_h")
    out["pairs"]["decision"] = cfg.get("decision")
    out["detector"] = _case_detector()
    out["conc"] = _case_conc()
    out["experiment"] = _case_experiment()
    out["selfcheck"] = _case_selfcheck()
    try:  # control example through the same code as the service (src/macroplastic/case/concentration.py)
        if str(ROOT / "src") not in sys.path:
            sys.path.insert(0, str(ROOT / "src"))
        from macroplastic.case.concentration import concentration as _conc  # noqa: WPS433
        r = _conc(12, 0.20)
        out["control"] = {"n": 12, "area_km2": 0.2, "value": _r(r.value, 1), "lower": _r(r.lower, 1),
                          "upper": _r(r.upper, 1), "unit": r.unit, "model_example": 75.0, "abs_error": _r(abs(75.0 - r.value), 1)}
    except Exception as e:  # noqa: BLE001
        out["control"] = {"error": f"{type(e).__name__}: {e}"}
    steps = rs.get("steps") or {}
    out["run"] = {"command": rs.get("command"), "finished": rs.get("finished"), "versions": rs.get("versions"),
                  "outputs_fingerprint_short": (rs.get("outputs_fingerprint") or "")[:16] or None,
                  "n_outputs": len(rs.get("outputs_sha256") or {}) or None,
                  "total_s": _r((steps.get("total_all") or {}).get("seconds"), 1),
                  "step_s": {k: (v or {}).get("seconds") for k, v in steps.items() if not k.startswith("total")},
                  "export": {Path(f.get("file", "")).stem: f.get("records") for f in (rs.get("export") or {}).get("files") or []},
                  "detector_recomputed": ((rs.get("detector") or {}).get("recomputed_from_preds") or {}).get("test")}
    out["sections"] = _case_sections(out)
    out["clean_clone"] = _case_clean_clone()
    out["detector_review"] = _case_detector_review()
    out["detector_review"]["marida_test_f1"] = (((out.get("detector") or {}).get("test") or {}).get("lgbm") or {}).get("f1")
    out["detector_review"]["mode"] = ("прежний режим с гармонизацией water_median (до решения об отказе от неё, docs/DECISIONS.md); "
                                      "основание для отказа от неё, не текущий результат")
    out["detector_current"] = _case_detector_current()
    dc = out["detector_current"]
    out["sections"]["sat_detector_current"] = {k: dc.get(k) for k in
                                               ("source", "protocol", "harmonize", "n_crops", "n_obj", "n_in_strip")}
    out["sections"]["sat_detector_review"] = {**{k: out["detector_review"].get(k) for k in
                                                 ("source", "protocol", "mode", "n_crops", "n_obj", "n_in_strip",
                                                  "wo_he460_false_share_pct", "no_harmonize_n_obj")},
                                              "visual_precision_pct": (dc.get("visual_review") or {}).get("h_out_precision_pct")}
    out["sections"].update(_case_search())  # §11: search, labeled_data, adis_pairs, baselines, quantity, oil, detector_v2
    out["sections"]["s54"] = _case_s54()
    out["splits_files"] = len(glob.glob(str(ROOT / "reports" / "case_splits" / "*.csv"))) or None
    tests = 0
    for p in glob.glob(str(ROOT / "tests" / "test_case_*.py")) + [str(ROOT / "tests" / "test_api_v3.py")]:
        tests += len(re.findall(r"^def test_", _read(str(Path(p).relative_to(ROOT))), re.M))
    out["pairfinder"] = _case_pairfinder()
    out["geometry"] = _case_geometry()
    out["tests"] = _case_tests()
    out["n_test_funcs"] = tests or None
    # число тестов кейса = фактический прогон (build_docs.py); без него — число функций test_ (не прогон)
    out["n_tests"] = out["tests"].get("passed") if out["tests"].get("available") else (tests or None)
    return out


def collect_artifacts() -> dict:
    def ex(rel):
        return (ROOT / rel).exists()
    return {
        "tools_doc": ex("docs/TOOLS.md"),
        "inspect_dataset": ex("scripts/tools/inspect_dataset.py"),
        "label_forensics": ex("scripts/tools/label_forensics.py"),
        "provenance_check": ex("scripts/tools/provenance_check.py"),
        "organizer_adapter": ex("src/macroplastic/organizer_adapter/__main__.py"),
        "adapter_wrapper": ex("scripts/tools/adapter.py"),
        "readme_images": sorted(Path(p).name for p in glob.glob(str(ROOT / "docs" / "img" / "*.*"))),
    }


# Sections whose sources are local (not in git: data/live, service/data, weights_exp). On a clean clone they are
# missing; then the section is taken from the previous reports/final_numbers.json (committed) and marked
# source_missing: true instead of being overwritten with empty values or with the demo set.
LOCAL_SOURCES = {
    "live": ("data/live/*/*/scene.json",),
    "drift": ("data/live/*/*/drift.json",),
    "service": ("service/data/manifest.json",),
    "incidents": ("service/data/manifest.json",),
    "speed_threads": ("weights_exp/l31/speed.json",),
    "rejected": ("weights_exp/l26/light_lro_alternatives.json",),
}


def _previous_numbers(out: Path) -> dict | None:
    prev = _load_json(out)
    if isinstance(prev, dict):
        return prev
    try:  # no file on disk: the committed version
        import subprocess
        rel = str(out.resolve().relative_to(ROOT)).replace("\\", "/")
        r = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=ROOT, capture_output=True, timeout=30)
        return json.loads(r.stdout.decode("utf-8")) if r.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def _keep_sections_without_source(fn: dict, out: Path) -> list:
    missing = {k: pats for k, pats in LOCAL_SOURCES.items()
               if not any(glob.glob(str(ROOT / p)) for p in pats)}
    if not missing:
        return []
    prev = _previous_numbers(out) or {}
    kept = []
    for key, pats in missing.items():
        old = prev.get(key)
        if isinstance(old, dict) and old:
            old = dict(old)
            old["source_missing"] = True
            old["source_missing_note"] = (f"исходник не найден ({', '.join(pats)}; не в git) — раздел взят из прежнего "
                                          f"reports/final_numbers.json без пересчёта")
            fn[key] = old
            kept.append(key)
    if kept:
        print(f"[final_numbers] no local sources for {', '.join(kept)}: sections kept from the previous final_numbers.json "
              f"(source_missing: true)")
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Собрать reports/final_numbers.json из артефактов")
    ap.add_argument("--out", default=str(ROOT / "reports" / "final_numbers.json"))
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    try:  # a cp1251 console must not crash the summary print (the JSON is written before it)
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass

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
    fn["models"] = collect_models(fn["l3_lgbm"], fn["l4_unet"])
    fn["service_demo"] = collect_service((ROOT / "service" / "demo",))
    fn["lgbm_live"] = collect_lgbm_live()
    fn["metric_audit"] = collect_metric_audit()
    fn["agreement"] = collect_agreement()
    fn["drift"] = collect_drift()
    fn["speed"] = collect_speed()
    fn["test"] = collect_test_status(fn["l3_lgbm"])
    fn["baselines"] = collect_baselines()
    fn["l23"] = collect_l23()
    fn["speed_threads"] = collect_speed_threads()
    fn["rejected"] = collect_rejected(fn["l23"], fn["metric_audit"])
    fn["robustness"] = collect_robustness()
    fn["rehearsal"] = collect_rehearsal()
    fn["drift_check"] = collect_drift_check()
    fn["context"] = collect_context()
    fn["incidents"] = collect_incidents()
    fn["case"] = collect_case()
    out = Path(a.out)
    _keep_sections_without_source(fn, out)
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
        print("  models: " + "; ".join(f"{k}={r['val_f1_mean']}+-{r['val_f1_std']} ({r['decision']})"
                                       for k, r in fn["models"]["rows"].items()))
        print(f"  drift scenes={fn['drift']['n_scenes']} | agreement objects={fn['agreement']['confirmed_objects']} | "
              f"confirmed(total)={sv['n_confirmed_total']} | speed={fn['speed']['available']} | "
              f"audit={fn['metric_audit']['available']} | test computed={fn['test']['computed']}")
        bl = fn["baselines"]["rows"] or {}
        print("  baselines: " + "; ".join(f"{k}={r.get('f1_md')}" for k, r in bl.items()))
        lt = fn["l23"]["light"] or {}
        print(f"  l23: subsets={len(fn['l23']['subsets'] or [])} light F1={lt.get('f1_mean')} cold={lt.get('cold_s_300')}s "
              f"(final {lt.get('final_cold_s_300')}s)")
        print(f"  speed: {fn['speed']['summary']}")
        st = fn["speed_threads"]
        print(f"  speed by threads ({st.get('source')}): final={st.get('final')} mid={st.get('mid')}")
        rj = fn["rejected"]
        print(f"  rejected: light={rj.get('light')} | mid={rj.get('mid')} | zfeat={rj.get('zfeat')}")
        print(f"  robustness: {fn['robustness']} | rehearsal: {fn['rehearsal']}")
        dc, cx, ic = fn["drift_check"], fn["context"], fn["incidents"]
        print(f"  drift_check (эксперимент): {dc.get('k_hit')}/{dc.get('n_pairs')} vs baseline "
              f"{dc.get('k_hit_baseline')}/{dc.get('n_pairs')} | context: objects={cx.get('objects_total')} "
              f"drift scenes touching={cx.get('n_drift_scenes_touching')}/{cx.get('n_drift_scenes')} | incidents: "
              f"total={ic.get('total')} excluded={ic.get('excluded_artifacts')} reviewed={ic.get('reviewed')}")
        u = fn["ui_perf"]
        print(f"  ui: load={u.get('load_s')} flyto={u.get('flyto_fps')} globe={u.get('globe_fps')} "
              f"tour={u.get('tour_s')} gzip={u.get('bundle_gzip_mb')} err={u.get('console_errors')}")
        cs = fn["case"]
        cc, cd = cs.get("conc") or {}, (cs.get("detector") or {}).get("test") or {}
        print(f"  case: selection S2 {((cs.get('selection') or {}).get('S2') or {}).get('accepted_events')} | pairs meta "
              f"{(cs.get('pairs') or {}).get('events_accept_meta')} drift {(cs.get('pairs') or {}).get('events_accept_drift')} | "
              f"det test F1 {(cd.get('lgbm') or {}).get('f1')} vs RF {(cd.get('rf_argmax') or {}).get('f1')} | conc S2 "
              f"{(cc.get('S2') or {}).get('primary')} {((cc.get('S2') or {}).get('main') or {}).get('mae')} vs median "
              f"{((cc.get('S2') or {}).get('median') or {}).get('mae')} | final test done={cc.get('final_test_done')}")
        missing = [k for k, v in fn["artifacts"].items() if v is False]
        if missing:
            print(f"  not yet present: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
