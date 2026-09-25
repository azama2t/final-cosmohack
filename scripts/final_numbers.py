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
    """Candidate models that did not pass the rules: light (L23/L26), mid-size (L31), scene-relative features (L33)."""
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
    # L40: ranking as on the site (rankRegions in service/frontend/src/lib/data.ts): reliable regions by index desc,
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
    """L40: a region is unreliable if its latest scene is unreliable (as on the site)."""
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
        missing = [k for k, v in fn["artifacts"].items() if v is False]
        if missing:
            print(f"  not yet present: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
