"""L16: collect weights_exp/l16/{recheck,paired,lro,harm_val}.json -> reports/l16_metric_audit.json (+ prints tables).

  .venv/Scripts/python.exe scripts/experiments/l16_summarize.py
"""
from __future__ import annotations

import json

import numpy as np

import l16_common as C


def load(n):
    return json.loads((C.OUT / n).read_text(encoding="utf-8"))


def main():
    rc, pr, lro, hv = load("recheck.json"), load("paired.json"), load("lro.json"), load("harm_val.json")
    runs = lro["runs"]
    held = lro["held_out"]
    seeds = sorted({r["seed"] for r in runs.values()})

    def by(v, key):
        return [runs[f"{v}|s{s}"][key] for s in seeds if f"{v}|s{s}" in runs]

    base = "combined"
    helps = []
    for v in ["combined", "marida_only", "combined_aug", "combined_harmonize", "ens_combined_3seeds",
              "ens_combined_plus_marida", "combined_min", "combined_nowide"]:
        f = by(v, "mean_f1")
        row = {"variant": v, "n_seeds": len(f), "lro_mean_f1": round(float(np.mean(f)), 4),
               "lro_std_seeds": round(float(np.std(f, ddof=1)), 4) if len(f) > 1 else None,
               "lro_mean_iou": round(float(np.mean(by(v, "mean_iou"))), 4),
               "lro_mean_f1_at_0.63": round(float(np.mean(by(v, "mean_f1_at_0.63"))), 4),
               "lro_mean_oracle_f1": round(float(np.mean(by(v, "mean_oracle_f1"))), 4),
               "per_region_f1_mean": {R: round(float(np.mean([runs[f"{v}|s{s}"]["per_region"][i]["f1"] for s in seeds
                                                              if f"{v}|s{s}" in runs])), 4) for i, R in enumerate(held)}}
        if v != base:
            if len(f) == len(seeds):
                d = np.array(f) - np.array(by(base, "mean_f1"))
                sd = float(np.std(d, ddof=1))
            else:  # 3-seed ensemble vs the 3 single-seed runs
                d = np.array([f[0] - x for x in by(base, "mean_f1")])
                sd = float(np.std(by(base, "mean_f1"), ddof=1))
            gate = max(0.01, 2 * sd)
            row.update({"delta_vs_final_recipe": round(float(d.mean()), 4), "delta_std": round(sd, 4),
                        "gate": round(gate, 4), "decision": "помогает" if d.mean() >= gate else
                        ("хуже" if d.mean() <= -gate else "не помогает")})
        helps.append(row)
    for h in helps:
        if h["variant"] == "combined_harmonize":
            h["in_distribution_marida_val"] = {"as_is_f1": hv["as_is"]["f1_at_0.63"],
                                               "harmonized_f1_at_0.63": hv["harmonized"]["f1_at_0.63"],
                                               "harmonized_best_f1": hv["harmonized"]["best_f1"]}

    per_region = []
    for i, R in enumerate(held):
        rr = [runs[f"combined|s{s}"]["per_region"][i] for s in seeds]
        rm = [runs[f"marida_only|s{s}"]["per_region"][i] for s in seeds]
        row = {"region": R, "n_md_px": rr[0]["n_md"],
               "lro_f1": round(float(np.mean([r["f1"] for r in rr])), 4),
               "lro_f1_std": round(float(np.std([r["f1"] for r in rr], ddof=1)), 4),
               "lro_iou": round(float(np.mean([r["iou"] for r in rr])), 4),
               "lro_precision": round(float(np.mean([r["precision"] for r in rr])), 4),
               "lro_recall": round(float(np.mean([r["recall"] for r in rr])), 4),
               "thr_from_other_regions": [r["thr_from_other_regions"] for r in rr],
               "lro_f1_at_0.63": round(float(np.mean([r["f1_at_0.63"] for r in rr])), 4),
               "lro_oracle_f1": round(float(np.mean([r["oracle_f1"] for r in rr])), 4),
               "lro_f1_marida_only": round(float(np.mean([r["f1"] for r in rm])), 4),
               "lro_f1_marida_only_at_0.63": round(float(np.mean([r["f1_at_0.63"] for r in rm])), 4),
               "mados_scenes_excluded": lro["fold_info"][R]["mados_scenes_excluded_for_region"]}
        if "val_only" in rr[0]:
            row["val_px_only"] = {"n_md": rr[0]["val_only"]["n_md"],
                                  "lro_f1": round(float(np.mean([r["val_only"]["lro_f1"] for r in rr])), 4),
                                  "in_dist_final_f1": rr[0]["val_only"]["in_dist_final_f1"]}
        per_region.append(row)

    fm = rc["models"]["final"]
    bm = rc["models"]["backup_marida_only"]
    out = {
        "recheck": {
            "final": {"recount": fm["recount_full_patch"], "meta": fm["meta_val_md"], "match_3dp": fm["match_meta_3dp"],
                      "threshold": fm["meta_threshold"], "best_thr_on_val": fm["best_thr_on_val"],
                      "f1_at_0.5": fm["f1_at_0.5"], "thr_split_half": fm["thr_split_half"],
                      "scene_bootstrap_ci95": rc["same_place"]["final"]["all"]["ci95"], "border": fm["border"]},
            "backup_marida_only": {"recount": bm["recount_full_patch"], "meta": bm["meta_val_md"],
                                   "match_3dp": bm["match_meta_3dp"], "threshold": bm["meta_threshold"],
                                   "best_thr_on_val": bm["best_thr_on_val"], "thr_split_half": bm["thr_split_half"],
                                   "scene_bootstrap_ci95": rc["same_place"]["backup_marida_only"]["all"]["ci95"]},
            "mados_gain_paired_scene_bootstrap": {k: pr[k] for k in ("delta_point", "delta_ci95", "p_delta_le_0")},
            "nan_labelled_px": fm["nan"]["labelled_px_with_nan_band"],
            "splits": rc["splits"], "cache_val": rc["cache_val"],
            "full_patch_equals_cache": fm["max_abs_prob_diff_full_vs_cache"] == 0.0,
            "mados": rc["mados"], "border_train_vs_val": rc["border_train_vs_val"],
        },
        "same_place": {"patch_counts": rc["same_place_patch_counts"],
                       "final": {k: v for k, v in rc["same_place"]["final"].items()},
                       "backup_marida_only": {k: v for k, v in rc["same_place"]["backup_marida_only"].items()}},
        "lro": {"protocol": "MARIDA train+val by region (test untouched); final recipe (+MADOS minus scenes of the "
                            "held-out region); threshold = F1-optimal on OOF of the other held-out regions; "
                            f"seeds {seeds}; per-region numbers = mean over seeds",
                "per_region": per_region,
                "mean_f1": helps[0]["lro_mean_f1"], "mean_f1_std_seeds": helps[0]["lro_std_seeds"],
                "mean_iou": helps[0]["lro_mean_iou"],
                "mean_f1_marida_only": helps[1]["lro_mean_f1"], "mean_f1_at_0.63": helps[0]["lro_mean_f1_at_0.63"],
                "in_dist_f1": 0.9226, "in_dist_per_region_val": lro["in_dist"]["per_region_val"],
                "drop_vs_in_dist": round(0.9226 - helps[0]["lro_mean_f1"], 4),
                "cross_region_patch_overlaps": hv["cross_region_patch_overlaps"]},
        "helps": helps,
    }
    (C.ROOT / "reports" / "l16_metric_audit.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=float),
                                                             encoding="utf-8")
    for h in helps:
        print({k: h.get(k) for k in ("variant", "lro_mean_f1", "lro_std_seeds", "lro_mean_f1_at_0.63", "lro_mean_oracle_f1",
                                     "delta_vs_final_recipe", "gate", "decision", "per_region_f1_mean")})
    for r in per_region:
        print(r)


if __name__ == "__main__":
    main()
