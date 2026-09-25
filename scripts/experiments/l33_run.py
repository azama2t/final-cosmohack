"""Scene-relative features: scene-relative / wide-window extra features - val MARIDA (3 seeds) + leave-region-out (metric audit protocol).

    cd scripts/experiments && PYTHONPATH=../../src L16_THREADS=10 ../../.venv/Scripts/python.exe l33_run.py
Needs out/l33_cache (l33_feats.py). Writes weights_exp/l33/run.json (+ models of seed 0 for the timing script).
MARIDA test is never read.

Variants (extra columns appended to the 48 'win' features; recipe otherwise = final model):
  base      48                      control: val s0 must give 0.9226, LRO must give 0.854 (weights_exp/l16/lro.json)
  zs        48 + 15 scene z-scores  (x - water median of the scene) / water MAD
  zd        48 + 15 scene diffs     x - water median of the scene
  wide      48 + 5 wide windows     B8/FDI/NDVI dmed63, NDWI dmed31/63
  zs_wide   48 + 20
Val recipe (scripts/train_lgbm_mados.py, meta of weights/lgbm): MARIDA train caps (seed) + MADOS train+val minus
scenes NO / same place as MARIDA val, MADOS-only classes dropped, caps (seed+7919); threshold = F1-opt on val.
LRO recipe = scripts/experiments/l16_lro.py variant 'combined' (threshold from the other held-out regions).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

import l16_common as C
import l33_feats as F
from macroplastic.features.pixel import feature_names

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "weights_exp" / "l33"
OUT.mkdir(parents=True, exist_ok=True)
BASE = feature_names("win")
KEY_COLS = list(range(11)) + [BASE.index(k) for k in ("FDI", "FAI", "NDVI", "NDWI")]


def excluded_val_place():
    """= train_lgbm_mados.excluded_scenes(include_place_val=True) (final model config)."""
    out = set()
    for r in C.mados_overlap():
        if r["use_in_training"].startswith("NO"):
            out.add(r["mados_scene"])
        elif r["kind"] != "none" and r["marida_split"] == "val":
            out.add(r["mados_scene"])
    return out


def scene_stats():
    """scene -> (med, mad, n, mode) for every MARIDA (train+val) and MADOS (train+val pooled) scene."""
    cands = {}
    for kind in ("marida", "mados"):
        for split in ("train", "val"):
            for s, c in F.scene_stats_from_cache(F.build(kind, split)).items():
                cands.setdefault(s, []).append(c)
    cands = {s: np.concatenate(v, 1) for s, v in cands.items()}
    st = {s: F.water_stats(c) for s, c in cands.items()}
    ok = [v for v in st.values() if v[0] is not None]
    default = (np.median([v[0] for v in ok], 0).astype(np.float32), np.median([v[1] for v in ok], 0).astype(np.float32))
    for s, c in cands.items():
        if st[s][0] is None:
            st[s] = F.water_stats(c, default)
    return st, default


def load():
    X, y, conf, meta = C.marida_pixels()
    Xm_l, ym_l, scm_l, wm_l = [], [], [], []
    for split in ("train", "val"):
        z = np.load(C.L13_CACHE / f"mados_{split}_win.npz", allow_pickle=False)
        scenes = np.array([str(p).rsplit("_", 1)[0] for p in z["patches"]])
        Xm_l.append(z["X"]); ym_l.append(z["y"]); scm_l.append(scenes[z["pid"]])
        wm_l.append(F.build("mados", split)["wide"])
    Xm, ym, scm, wm = (np.concatenate(a) for a in (Xm_l, ym_l, scm_l, wm_l))
    wa = np.concatenate([F.build("marida", s)["wide"] for s in ("train", "val")])
    st, default = scene_stats()

    def rel(Xb, sc):
        zs = np.empty((len(Xb), 15), np.float32)
        zd = np.empty((len(Xb), 15), np.float32)
        for s in np.unique(sc):
            i = np.flatnonzero(sc == s)
            med, mad = st[str(s)][0], st[str(s)][1]
            zs[i], zd[i] = F.scene_features(Xb[i][:, KEY_COLS], med, mad)
        return zs, zd

    zs, zd = rel(X, meta["scene"])
    zsm, zdm = rel(Xm, scm)
    XA = np.concatenate([X, zs, zd, wa], 1)
    XM = np.concatenate([Xm, zsm, zdm, wm], 1)
    keep = ym <= 15
    info = {"scene_modes": {m: int(sum(1 for v in st.values() if v[3] == m)) for m in ("dark_half", "candidates", "default")},
            "scenes_default": sorted(s for s, v in st.items() if v[3] == "default"),
            "water_px_per_scene_median": int(np.median([v[2] for v in st.values()])),
            "default_med": [round(float(v), 5) for v in default[0]]}
    return XA, y, meta, XM[keep], ym[keep], scm[keep], info


NAMES = BASE + F.zs_names() + F.zd_names() + F.WIDE_NAMES
nb = len(BASE)
VARIANTS = {
    "base": list(range(nb)),
    "zs": list(range(nb)) + list(range(nb, nb + 15)),
    "zd": list(range(nb)) + list(range(nb + 15, nb + 30)),
    "wide": list(range(nb)) + list(range(nb + 30, nb + 35)),
    "zs_wide": list(range(nb)) + list(range(nb, nb + 15)) + list(range(nb + 30, nb + 35)),
    # implementation 2 (after run 1: zs_B1/zs_B2 were the top zs features = scene identity / atmosphere shortcut):
    # z-scores only of red-edge/NIR/SWIR bands and the indices (no blue/green/red), and only B8 + 4 indices
    "zs_nir": list(range(nb)) + [nb + F.ZS_KEYS.index(k) for k in
                                 ("B6", "B7", "B8", "B8A", "B11", "B12", "FDI", "FAI", "NDVI", "NDWI")],
    "zs_idx": list(range(nb)) + [nb + F.ZS_KEYS.index(k) for k in ("B8", "FDI", "FAI", "NDVI", "NDWI")],
}


def train(X, y, seed, fidx):
    import lightgbm as lgb

    label = (y == 1).astype(np.float32)
    w = np.where(label > 0, C.POS_WEIGHT, 1.0).astype(np.float32)
    params = dict(C.LGB_PARAMS, objective="binary", seed=seed, bagging_seed=seed, feature_fraction_seed=seed,
                  data_random_seed=seed, num_threads=C.THREADS, verbose=-1, deterministic=True, force_row_wise=True)
    ds = lgb.Dataset(X[:, fidx], label=label, weight=w, feature_name=[NAMES[i] for i in fidx], free_raw_data=True)
    return lgb.train(params, ds, num_boost_round=C.ROUNDS)


def predict(b, X, fidx):
    p = b.predict(X[:, fidx], num_threads=C.THREADS).astype(np.float32)
    p[np.isnan(X[:, :11]).any(1)] = 0.0
    return p


def run_val(XA, y, meta, XM, ym, scm, seeds, variants):
    tr = meta["split"] == "train"
    va = meta["split"] == "val"
    excl = excluded_val_place()
    km = ~np.isin(scm, sorted(excl))
    res = {}
    for seed in seeds:
        im = np.flatnonzero(tr)[C.sample_caps(y[tr], seed)]
        imd = np.flatnonzero(km)[C.sample_caps(ym[km], seed + 7919)]
        Xt = np.concatenate([XA[im], XM[imd]]); yt = np.concatenate([y[im], ym[imd]])
        for v in variants:
            b = train(Xt, yt, seed, VARIANTS[v])
            p = predict(b, XA[va], VARIANTS[v])
            t = y[va] == 1
            thr, f1 = C.best_thr(t, p)
            s = C.scores(*C.confusion(t, p >= thr))
            s063 = C.scores(*C.confusion(t, p >= 0.63))["f1"]
            res[f"{v}|s{seed}"] = {"variant": v, "seed": seed, "thr": thr, **C.rnd(s), "f1_at_0.63": round(s063, 4),
                                   "n_train": int(len(yt)), "n_train_md": int(np.sum(yt == 1))}
            if seed == seeds[0]:
                b.save_model(str(OUT / f"val_{v}_s{seed}.txt"))
                gain = b.feature_importance("gain")
                fn = [NAMES[i] for i in VARIANTS[v]]
                res[f"{v}|s{seed}"]["top_gain"] = [(n, round(float(g), 1)) for n, g in
                                                   sorted(zip(fn, gain), key=lambda q: -q[1])[:15]]
            print(f"[val {v} s{seed}] F1 {s['f1']:.4f} thr {thr:.2f} (@0.63 {s063:.4f})", flush=True)
    return res


def run_lro(XA, y, meta, XM, ym, scm, seeds, variants):
    mreg, mno = C.mados_scene_regions()
    regions = np.unique(meta["region"])
    md_by_region = {r: int(np.sum((meta["region"] == r) & (y == 1))) for r in regions}
    held = [r for r in sorted(md_by_region, key=lambda r: -md_by_region[r]) if md_by_region[r] >= 50]
    oof = {}
    for R in held:
        tr_m = meta["region"] != R
        ex_scenes = {s for s, rs in mreg.items() if R in rs} | mno
        tr_mados = ~np.isin(scm, sorted(ex_scenes))
        te = np.flatnonzero(~tr_m)
        for seed in seeds:
            im = np.flatnonzero(tr_m)[C.sample_caps(y[tr_m], seed)]
            imd = np.flatnonzero(tr_mados)[C.sample_caps(ym[tr_mados], seed + 7919)]
            Xt = np.concatenate([XA[im], XM[imd]]); yt = np.concatenate([y[im], ym[imd]])
            for v in variants:
                b = train(Xt, yt, seed, VARIANTS[v])
                arr = oof.setdefault((v, seed), np.full(len(y), np.nan, np.float32))
                arr[te] = predict(b, XA[te], VARIANTS[v])
        print(f"[lro {R}] done", flush=True)
    res = {}
    for (v, seed), p in oof.items():
        per = []
        for R in held:
            m = meta["region"] == R
            others = np.isin(meta["region"], [q for q in held if q != R])
            thr, _ = C.best_thr(y[others] == 1, p[others])
            t = y[m] == 1
            s = C.scores(*C.confusion(t, p[m] >= thr))
            o_thr, o_f1 = C.best_thr(t, p[m])
            per.append({"region": R, "n_md": int(t.sum()), "thr": thr, "f1": round(s["f1"], 4),
                        "iou": round(s["iou"], 4), "precision": round(s["precision"], 4),
                        "recall": round(s["recall"], 4),
                        "f1_at_0.63": round(C.scores(*C.confusion(t, p[m] >= 0.63))["f1"], 4),
                        "oracle_f1": round(o_f1, 4)})
        res[f"{v}|s{seed}"] = {"variant": v, "seed": seed, "per_region": per,
                               "mean_f1": round(float(np.mean([r["f1"] for r in per])), 4),
                               "mean_f1_at_0.63": round(float(np.mean([r["f1_at_0.63"] for r in per])), 4),
                               "mean_oracle_f1": round(float(np.mean([r["oracle_f1"] for r in per])), 4)}
        print(f"[lro {v} s{seed}] {res[f'{v}|s{seed}']['mean_f1']} {[r['f1'] for r in per]}", flush=True)
    np.savez_compressed(OUT / f"lro_oof_{'_'.join(sorted({v for v, _ in oof}))}.npz", **{f"{v}__s{s}": p for (v, s), p in oof.items()})
    return res, held


def summarize(val, lro, seeds, variants, held):
    out = {}
    base_l = np.array([lro[f"base|s{s}"]["mean_f1"] for s in seeds])
    for v in variants:
        vf = np.array([val[f"{v}|s{s}"]["f1"] for s in seeds])
        lf = np.array([lro[f"{v}|s{s}"]["mean_f1"] for s in seeds])
        d = lf - base_l
        per_reg = {R: round(float(np.mean([lro[f"{v}|s{s}"]["per_region"][i]["f1"] for s in seeds])), 4)
                   for i, R in enumerate(held)}
        per_reg_std = {R: round(float(np.std([lro[f"{v}|s{s}"]["per_region"][i]["f1"] for s in seeds], ddof=1)), 4)
                       for i, R in enumerate(held)}
        std_v = float(np.std(lf, ddof=1))
        std_d = float(np.std(d, ddof=1))
        need_lro = 0.854 + max(0.01, 2 * std_v, 2 * std_d)
        need_val = 0.9226 - 0.005
        out[v] = {"n_features": len(VARIANTS[v]),
                  "val_f1_by_seed": vf.round(4).tolist(), "val_mean": round(float(vf.mean()), 4),
                  "val_std": round(float(vf.std(ddof=1)), 4),
                  "val_f1_at_0.63_mean": round(float(np.mean([val[f"{v}|s{s}"]["f1_at_0.63"] for s in seeds])), 4),
                  "lro_by_seed": lf.round(4).tolist(), "lro_mean": round(float(lf.mean()), 4),
                  "lro_std": round(std_v, 4), "lro_delta_vs_base": round(float(d.mean()), 4),
                  "lro_delta_std": round(std_d, 4), "lro_per_region": per_reg, "lro_per_region_std": per_reg_std,
                  "lro_mean_at_0.63": round(float(np.mean([lro[f"{v}|s{s}"]["mean_f1_at_0.63"] for s in seeds])), 4),
                  "lro_mean_oracle": round(float(np.mean([lro[f"{v}|s{s}"]["mean_oracle_f1"] for s in seeds])), 4),
                  "accept_lro_needed": round(need_lro, 4), "accept_val_needed": round(need_val, 4),
                  "pass_lro": bool(lf.mean() >= need_lro), "pass_val": bool(vf.mean() >= need_val)}
        out[v]["accept"] = out[v]["pass_lro"] and out[v]["pass_val"] and v != "base"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="*", default=[0, 1, 2])
    ap.add_argument("--variants", nargs="*", default=list(VARIANTS))
    ap.add_argument("--out", default="run.json")
    a = ap.parse_args()
    t0 = time.time()
    XA, y, meta, XM, ym, scm, info = load()
    print("loaded", XA.shape, XM.shape, info["scene_modes"], f"{time.time() - t0:.0f}s", flush=True)
    val = run_val(XA, y, meta, XM, ym, scm, a.seeds, a.variants)
    lro, held = run_lro(XA, y, meta, XM, ym, scm, a.seeds, a.variants)
    summ = summarize(val, lro, a.seeds, a.variants, held)
    ref = json.loads((C.OUT / "lro.json").read_text(encoding="utf-8"))["summary"]["combined"]
    C.dump(OUT / a.out, {"summary": summ, "val": val, "lro": lro, "held_out": held, "scene_info": info,
                              "l16_reference_combined": ref, "names": NAMES,
                              "variants": {v: [NAMES[i] for i in VARIANTS[v]] for v in a.variants},
                              "seconds": round(time.time() - t0)})
    print(json.dumps(summ, indent=1), flush=True)


if __name__ == "__main__":
    main()
