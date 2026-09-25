"""L23: collect out/l23_runs/*.json -> reports/l23_channels_speed.json (+ prints markdown tables)."""
from __future__ import annotations

import json
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "out" / "l23_runs"
LIGHT = "light_k20_t200_l31"

LABEL = {
    "a_rgb_strict": "(a) RGB, only the project features computable from RGB (B2,B3,B4,SI)",
    "a_rgb": "(a) RGB + RGB windows (BRI/NDGR/NDBG mean/std/contrast)",
    "b_rgbnir": "(b)=(c) RGB+NIR = all 10 m bands",
    "b_rgbnir_x": "(b)=(c) RGB+NIR + RGB windows",
    "f_10m_swir": "(extra) 10 m + B11,B12 (no FDI: needs B6)",
    "d_10m_20m": "(d) 10 m + 20 m (no B1)",
    "e_full": "(e) all 11 bands = final recipe",
}


def load(prefix):
    recs = []
    for f in RUNS.glob(f"{prefix}*.json"):
        if f.name.startswith(("channels_s", "light_s", "speed")):
            continue
        recs.append(json.loads(f.read_text(encoding="utf-8")))
    return recs


def agg(recs):
    by = {}
    for r in recs:
        by.setdefault(r["name"], {})[r["seed"]] = r
    return by


def main():
    ch = agg([r for r in load("") if r.get("name") in LABEL])
    full0 = ch["e_full"][0]["val_md"]["f1"]
    fullm = st.mean(v["val_md"]["f1"] for v in ch["e_full"].values())
    subsets = []
    print("| subset | channels | n feat | F1 s0 | IoU s0 | thr s0 | F1 mean(3 seeds) +- sd | delta vs (e) s0 | delta vs (e) mean3 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for n in LABEL:
        if n not in ch:
            continue
        s = ch[n]
        r0 = s[0]
        f1s = [s[k]["val_md"]["f1"] for k in sorted(s)]
        m = st.mean(f1s)
        sd = st.stdev(f1s) if len(f1s) > 1 else None
        rec = {"name": n, "label": LABEL[n], "channels": r0["channels"], "rgb_extra_features": r0["rgb_extra"],
               "n_features": r0["n_features"], "f1": r0["val_md"]["f1"], "iou": r0["val_md"]["iou"],
               "precision": r0["val_md"]["precision"], "recall": r0["val_md"]["recall"], "threshold": r0["threshold"],
               "delta_vs_full": round(r0["val_md"]["f1"] - full0, 4), "seeds": sorted(s),
               "f1_seeds": f1s, "iou_seeds": [s[k]["val_md"]["iou"] for k in sorted(s)],
               "f1_mean": round(m, 4), "f1_sd": None if sd is None else round(sd, 4),
               "delta_vs_full_mean": round(m - fullm, 4) if len(f1s) > 1 else None,
               "weights": f"weights_exp/l23/{n}_s0"}
        subsets.append(rec)
        ms = f"{m:.4f} +- {sd:.4f}" if sd is not None else "-"
        dm = f"{m - fullm:+.4f}" if sd is not None else "-"
        print(f"| {LABEL[n]} | {','.join(r0['channels'])} | {r0['n_features']} | {r0['val_md']['f1']:.4f} | "
              f"{r0['val_md']['iou']:.4f} | {r0['threshold']} | {ms} | {r0['val_md']['f1'] - full0:+.4f} | {dm} |")

    lt = agg([r for r in load("light_") if r.get("name", "").startswith("light_")])
    print("\n| light variant | top-k | trees | leaves | lr | F1 s0 | IoU s0 | F1 mean(seeds) | size MB |")
    print("|---|---|---|---|---|---|---|---|---|")
    for n, s in sorted(lt.items(), key=lambda t: (-(t[1][0]["n_features"]), -t[1][0]["n_trees"])):
        r0 = s[0]
        f1s = [s[k]["val_md"]["f1"] for k in sorted(s)]
        ms = f"{st.mean(f1s):.4f} +- {st.stdev(f1s):.4f} (n={len(f1s)})" if len(f1s) > 1 else "-"
        print(f"| {n} | {r0['top_k'] or 48} | {r0['n_trees']} | {r0['num_leaves']} | {r0['learning_rate']} | "
              f"{r0['val_md']['f1']:.4f} | {r0['val_md']['iou']:.4f} | {ms} | {r0.get('model_size_mb', '')} |")

    sp = json.loads((RUNS / "speed.json").read_text(encoding="utf-8"))
    L = lt[LIGHT]
    fin = sp["models"]["weights/lgbm"]
    lsp = sp["models"][f"weights_exp/l23/{LIGHT}_s0"]
    f1s = [L[k]["val_md"]["f1"] for k in sorted(L)]
    light = {"name": LIGHT, "f1": L[0]["val_md"]["f1"], "iou": L[0]["val_md"]["iou"], "threshold": L[0]["threshold"],
             "f1_seeds": f1s, "f1_mean": round(st.mean(f1s), 4), "delta_vs_full": round(L[0]["val_md"]["f1"] - full0, 4),
             "cold_s_300": lsp["cold_s_median"], "warm_chips_per_s": lsp["warm_chips_per_s"],
             "n_features": lsp["n_features"], "n_trees": lsp["n_trees"], "num_leaves": L[0]["num_leaves"],
             "features": json.loads((ROOT / "weights_exp" / "l23" / f"{LIGHT}_s0" / "meta.json").read_text(
                 encoding="utf-8"))["features"],
             "weights": f"weights_exp/l23/{LIGHT}_s0",
             "final_same_harness": {"f1": full0, "cold_s_300": fin["cold_s_median"],
                                    "warm_chips_per_s": fin["warm_chips_per_s"], "n_features": fin["n_features"],
                                    "n_trees": fin["n_trees"]}}
    others = {m: {k: v[k] for k in ("n_features", "n_trees", "cold_s_median", "cold_s_runs", "warm_chips_per_s")}
              for m, v in sp["models"].items()}
    print("\n| model | n feat | trees | cold 300 chips, s (median) | warm chips/s |")
    print("|---|---|---|---|---|")
    for m, v in others.items():
        print(f"| {m} | {v['n_features']} | {v['n_trees']} | {v['cold_s_median']} ({v['cold_s_runs']}) | {v['warm_chips_per_s']} |")
    print("anchor inference.py cpu cold:", sp.get("anchor_inference_py_cpu_cold_s"))
    out = {"subsets": subsets, "light": light, "speed_all_models": others,
           "speed_anchor_inference_py_cpu_cold_s": sp.get("anchor_inference_py_cpu_cold_s"),
           "speed_setup": {"threads": sp["threads"], "chips": sp["chips"],
                           "harness": "scripts/experiments/l23_speed.py (read+features+LightGBM CPU+write, new process)",
                           "features_bit_identical": sp["features_bit_identical"],
                           "note": "CPU shared with other lanes during the measurement; compare models within this run; cold_s_300 = worse of 2 cold runs"},
           "speed_product_predictor_warm": json.loads((RUNS / "product_speed.json").read_text(encoding="utf-8")),
           "adapter_zero_fill_check": json.loads((RUNS / "adapter_check.json").read_text(encoding="utf-8")),
           "val": "MARIDA val, all labelled px pooled, threshold chosen on val (grid 0.02..0.98 step 0.01); test never read",
           "recipe": "weights/lgbm/meta.json config (MARIDA train + MADOS train+val, excl. same-place-val scenes)"}
    (ROOT / "reports" / "l23_channels_speed.json").write_text(json.dumps(out, indent=1, ensure_ascii=False),
                                                               encoding="utf-8")


if __name__ == "__main__":
    main()
