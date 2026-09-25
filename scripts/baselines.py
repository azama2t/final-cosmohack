r"""Simple baselines on MARIDA val, same metric as the final model: F1/IoU of Marine Debris over the pool
of all labelled val pixels (ignore = 0), counted with src/macroplastic/metrics.py.

  (a) threshold on FDI                     - best threshold on val (optimistic) + threshold chosen on train (honest)
  (b) threshold on one index (NDVI / FAI / all 8 indices) - same protocol
  (c) RandomForest as in the MARIDA paper  - parameters from their code
      (semantic_segmentation/random_forest/random_forest.py: n_estimators=125, max_depth=20, gini,
      class_weight='balanced_subsample', random_state=5; train_eval.py: Mixed Water / Wakes / Cloud Shadows /
      Waves -> Marine Water, sample weight = 1 / confidence level), 11 bands + 8 indices, no windows /
      no GLCM texture; multiclass, MD = argmax (paper protocol) and P(MD) >= t (t on val).
      Trained on all labelled MARIDA train pixels. Seeds 5 (paper), 0, 1.
  (d) our LightGBM without window features - taken from out/l3_runs/bin_min_s{0,1,2}.json (not retrained).

Only the official train and val splits are read (cache out/l3_cache/{train,val}_win.npz, first 19 columns =
11 bands + 8 indices). The test split is never touched.

  .venv\Scripts\python.exe scripts\baselines.py [--threads 8] [--rf-seeds 5 0 1]
Output: reports/baselines.json  {name: {f1_md, iou_md, precision, recall, threshold, note, ...}}
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

from macroplastic.data.marida import list_patches  # noqa: E402
from macroplastic.metrics import binary_scores  # noqa: E402

CACHE = ROOT / "out" / "l3_cache"
OUT = ROOT / "reports" / "baselines.json"
BANDS = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
INDICES = ["FDI", "FAI", "NDVI", "NDWI", "NDMI", "SI", "BSI", "NRD"]
N_BOOT = 2000


def load(split: str):
    assert split in ("train", "val"), "test is never read"
    z = np.load(CACHE / f"{split}_win.npz", allow_pickle=False)
    names = [str(n) for n in z["names"]]
    assert names[:19] == BANDS + INDICES, names[:19]
    return z["X"][:, :19].astype(np.float32), z["y"], z["conf"], z["pid"], names[:19]


def fdi_marida(X, names):
    """FDI as in the MARIDA code (l_redge = 740 nm in the ratio), for reference."""
    b = {n: X[:, names.index(n)] for n in ("B6", "B8", "B11")}
    return b["B8"] - (b["B6"] + (b["B11"] - b["B6"]) * (833.0 - 740.0) / (1614.0 - 740.0) * 10.0)


def scores(pred, y) -> dict:
    """metrics.py on the pooled labelled pixels (y > 0 already; ignore=0 kept for safety)."""
    s = binary_scores(np.asarray(pred, bool), y.astype(np.int64))
    return {"f1_md": round(s["f1"], 4), "iou_md": round(s["iou"], 4), "precision": round(s["precision"], 4),
            "recall": round(s["recall"], 4), "tp": s["tp"], "fp": s["fp"], "fn": s["fn"]}


def sweep(x, is_md, n_q=2001):
    """Best F1 over thresholds (quantiles of x) and both directions (x >= t or x <= t). NaN -> not MD."""
    ok = np.isfinite(x)
    xs, md = x[ok], is_md[ok]
    n_md_total = int(is_md.sum())
    pos, neg = np.sort(xs[md]), np.sort(xs[~md])
    cand = np.unique(np.quantile(xs, np.linspace(0, 1, n_q)))
    best = None
    for direction in ("ge", "le"):
        if direction == "ge":
            tp = len(pos) - np.searchsorted(pos, cand, "left")
            fp = len(neg) - np.searchsorted(neg, cand, "left")
        else:
            tp = np.searchsorted(pos, cand, "right")
            fp = np.searchsorted(neg, cand, "right")
        fn = n_md_total - tp
        f1 = 2 * tp / np.maximum(2 * tp + fp + fn, 1)
        i = int(np.argmax(f1))
        if best is None or f1[i] > best[2]:
            best = (float(cand[i]), direction, float(f1[i]))
    return best


def apply_rule(x, t, direction):
    with np.errstate(invalid="ignore"):
        return (x >= t) if direction == "ge" else (x <= t)


def _inner_edges(x, nb):
    x = x[np.isfinite(x)]
    return np.unique(np.quantile(x, np.linspace(0, 1, nb + 1)[1:-1]))


def _intervals(n_bins):
    a, b = np.triu_indices(n_bins + 1, k=1)          # bins [a, b), 0 <= a < b <= n_bins
    return a, b


def _bounds(edges, a, b):
    lo = -np.inf if a == 0 else float(edges[a - 1])
    hi = np.inf if b == len(edges) + 1 else float(edges[b - 1])
    return lo, hi


def in_box(cols, bounds):
    """AND over columns of lo <= x < hi (NaN -> False)."""
    m = np.ones(len(cols[0]), bool)
    with np.errstate(invalid="ignore"):
        for x, (lo, hi) in zip(cols, bounds):
            m &= (x >= lo) & (x < hi) & np.isfinite(x)
    return m


def tune_interval(x, is_md, nb=400):
    """Best F1 of the rule lo <= x < hi (both thresholds free) on the tuning set."""
    e = _inner_edges(x, nb)
    ok = np.isfinite(x)
    k = np.searchsorted(e, x[ok], "right")
    n = len(e) + 1
    hp = np.concatenate([[0], np.cumsum(np.bincount(k[is_md[ok]], minlength=n))])
    hn = np.concatenate([[0], np.cumsum(np.bincount(k[~is_md[ok]], minlength=n))])
    a, b = _intervals(n)
    tp, fp = hp[b] - hp[a], hn[b] - hn[a]
    f1 = 2 * tp / np.maximum(tp + fp + int(is_md.sum()), 1)
    i = int(np.argmax(f1))
    return [_bounds(e, a[i], b[i])]


def tune_box2(x1, x2, is_md, nb=48):
    """Best F1 of lo1 <= x1 < hi1 AND lo2 <= x2 < hi2 (4 thresholds) via 2-D prefix sums."""
    e1, e2 = _inner_edges(x1, nb), _inner_edges(x2, nb)
    ok = np.isfinite(x1) & np.isfinite(x2)
    k1, k2 = np.searchsorted(e1, x1[ok], "right"), np.searchsorted(e2, x2[ok], "right")
    n1, n2 = len(e1) + 1, len(e2) + 1

    def pref(sel):
        h = np.zeros((n1, n2), np.int64)
        np.add.at(h, (k1[sel], k2[sel]), 1)
        s = np.zeros((n1 + 1, n2 + 1), np.int64)
        s[1:, 1:] = h.cumsum(0).cumsum(1)
        return s

    sp, sn = pref(is_md[ok]), pref(~is_md[ok])
    a1, b1 = _intervals(n1)
    a2, b2 = _intervals(n2)

    def box(s):
        return (s[np.ix_(b1, b2)] - s[np.ix_(a1, b2)] - s[np.ix_(b1, a2)] + s[np.ix_(a1, a2)])

    tp, fp = box(sp), box(sn)
    f1 = 2 * tp / np.maximum(tp + fp + int(is_md.sum()), 1)
    i, j = np.unravel_index(int(np.argmax(f1)), f1.shape)
    return [_bounds(e1, a1[i], b1[i]), _bounds(e2, a2[j], b2[j])]


def rule_baselines(Xtr, ytr, Xva, yva, names, sid, n_sc):
    """Interval rules: FDI in [lo, hi), and the box FDI x NDVI (FDI in [..) and NDVI in [..))."""
    def col(X, n):
        return X[:, names.index(n)]

    res = {}
    specs = {"FDI_interval": ["FDI"], "NDVI_interval": ["NDVI"], "FAI_interval": ["FAI"], "FDI_NDVI_box": ["FDI", "NDVI"]}
    for name, feats in specs.items():
        out = {}
        for tune_on, (X, y) in (("val_tuned", (Xva, yva)), ("train_tuned", (Xtr, ytr))):
            cols = [col(X, f) for f in feats]
            b = tune_interval(cols[0], y == 1) if len(feats) == 1 else tune_box2(cols[0], cols[1], y == 1)
            pred = in_box([col(Xva, f) for f in feats], b)
            out[tune_on] = {**scores(pred, yva), "bounds": {f: [round(lo, 5), round(hi, 5)] for f, (lo, hi) in zip(feats, b)}}
            if tune_on == "val_tuned":
                out[tune_on]["scene_ci95_f1"] = scene_boot_ci(pred, yva, sid, n_sc)
        res[name] = out
        print(f"[rule] {name:14s} val-tuned F1 {out['val_tuned']['f1_md']:.4f} {out['val_tuned']['bounds']} | "
              f"train-tuned F1 on val {out['train_tuned']['f1_md']:.4f}", flush=True)
    return res


def scene_ids(pid):
    scenes = [p.parent.name for p in list_patches("val")]
    names = sorted(set(scenes))
    lut = np.array([names.index(s) for s in scenes])
    return lut[pid], len(names)


def scene_boot_ci(pred, y, sid, n_sc, seed=0):
    """95 % CI of F1 MD by resampling val scenes (2000 reps)."""
    md, pred = y == 1, np.asarray(pred, bool)
    tp = np.bincount(sid, weights=(md & pred), minlength=n_sc)
    fp = np.bincount(sid, weights=(~md & pred), minlength=n_sc)
    fn = np.bincount(sid, weights=(md & ~pred), minlength=n_sc)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n_sc, size=(N_BOOT, n_sc))
    T, F, N = tp[idx].sum(1), fp[idx].sum(1), fn[idx].sum(1)
    f1 = np.where(2 * T + F + N > 0, 2 * T / np.maximum(2 * T + F + N, 1), np.nan)
    lo, hi = np.nanpercentile(f1, [2.5, 97.5])
    return [round(float(lo), 3), round(float(hi), 3)]


def index_baselines(Xtr, ytr, Xva, yva, names, sid, n_sc):
    cols = {n: (Xtr[:, names.index(n)], Xva[:, names.index(n)]) for n in INDICES + ["B8"]}
    cols["FDI_marida"] = (fdi_marida(Xtr, names), fdi_marida(Xva, names))
    res = {}
    for n, (xt, xv) in cols.items():
        t_val, d_val, _ = sweep(xv, yva == 1)
        t_tr, d_tr, _ = sweep(xt, ytr == 1)
        pv = apply_rule(xv, t_val, d_val)
        pt = apply_rule(xv, t_tr, d_tr)
        res[n] = {"val_tuned": {**scores(pv, yva), "threshold": round(t_val, 5), "direction": d_val,
                                "scene_ci95_f1": scene_boot_ci(pv, yva, sid, n_sc)},
                  "train_tuned": {**scores(pt, yva), "threshold": round(t_tr, 5), "direction": d_tr}}
        print(f"[index] {n:10s} val-tuned F1 {res[n]['val_tuned']['f1_md']:.4f} ({d_val} {t_val:.4f}) | "
              f"train-tuned F1 on val {res[n]['train_tuned']['f1_md']:.4f} ({d_tr} {t_tr:.4f})", flush=True)
    return res


def rf_baseline(Xtr, ytr, ctr, Xva, yva, sid, n_sc, seeds, threads):
    from sklearn.ensemble import RandomForestClassifier

    y = ytr.copy()
    y[np.isin(y, (12, 13, 14, 15))] = 7          # MARIDA code: Mixed Water, Wakes, Cloud Shadows, Waves -> Marine Water
    w = 1.0 / ctr.astype(np.float64)              # MARIDA code: weight = 1 / confidence level (1 High, 2 Moderate, 3 Low)
    runs = []
    grid = np.round(np.arange(0.02, 0.981, 0.01), 2)
    for s in seeds:
        t0 = time.time()
        rf = RandomForestClassifier(n_estimators=125, criterion="gini", max_depth=20, min_samples_leaf=1,
                                    min_impurity_decrease=0, oob_score=False, class_weight="balanced_subsample",
                                    random_state=int(s), n_jobs=threads)
        rf.fit(Xtr, y, sample_weight=w)
        t_fit = time.time() - t0
        proba = rf.predict_proba(Xva)
        classes = list(rf.classes_)
        p_md = proba[:, classes.index(1)]
        arg = np.asarray(rf.classes_)[proba.argmax(1)] == 1
        f1s = [(t, binary_scores(p_md >= t, yva.astype(np.int64))["f1"]) for t in grid]
        t_best = max(f1s, key=lambda z: z[1])[0]
        pt = p_md >= t_best
        r = {"seed": int(s), "fit_s": round(t_fit, 1), "argmax": {**scores(arg, yva), "scene_ci95_f1": scene_boot_ci(arg, yva, sid, n_sc)},
             "prob_val_tuned": {**scores(pt, yva), "threshold": float(t_best), "scene_ci95_f1": scene_boot_ci(pt, yva, sid, n_sc)}}
        runs.append(r)
        print(f"[rf] seed {s}: fit {t_fit:.0f}s | argmax F1 {r['argmax']['f1_md']:.4f} IoU {r['argmax']['iou_md']:.4f} | "
              f"P(MD)>={t_best:.2f} F1 {r['prob_val_tuned']['f1_md']:.4f}", flush=True)
        del rf
    return runs


def mean_sd(v):
    v = np.asarray(v, float)
    return round(float(v.mean()), 4), (round(float(v.std(ddof=1)), 4) if len(v) > 1 else None)


def lgbm_nowin():
    runs = []
    for s in (0, 1, 2):
        p = ROOT / "out" / "l3_runs" / f"bin_min_s{s}.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            runs.append({"seed": s, "threshold": d["threshold"], **{k: d["val_md"][k] for k in ("f1", "iou", "precision", "recall")}})
    return runs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--rf-seeds", type=int, nargs="+", default=[5, 0, 1])
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    t0 = time.time()
    Xtr, ytr, ctr, _, names = load("train")
    Xva, yva, _, pid, _ = load("val")
    sid, n_sc = scene_ids(pid)
    print(f"[data] train {len(ytr)} px ({int((ytr == 1).sum())} MD), val {len(yva)} px ({int((yva == 1).sum())} MD), "
          f"{n_sc} val scenes", flush=True)

    idx = index_baselines(Xtr, ytr, Xva, yva, names, sid, n_sc)
    rules = rule_baselines(Xtr, ytr, Xva, yva, names, sid, n_sc)
    rf_runs = rf_baseline(Xtr, ytr, ctr, Xva, yva, sid, n_sc, a.rf_seeds, a.threads)
    lg = lgbm_nowin()

    def pick(name, key="val_tuned"):
        return idx[name][key]

    best_single = max(("NDVI", "FAI"), key=lambda n: idx[n]["val_tuned"]["f1_md"])
    best_any = max(INDICES + ["B8", "FDI_marida"], key=lambda n: idx[n]["val_tuned"]["f1_md"])
    rf0 = rf_runs[0]
    rf_f1_m, rf_f1_sd = mean_sd([r["argmax"]["f1_md"] for r in rf_runs])
    rfp_f1_m, rfp_f1_sd = mean_sd([r["prob_val_tuned"]["f1_md"] for r in rf_runs])

    def row(d, note, **extra):
        return {"f1_md": d["f1_md"], "iou_md": d["iou_md"], "precision": d["precision"], "recall": d["recall"],
                "threshold": d.get("threshold"), "note": note, **extra}

    fdi = pick("FDI")
    out = {
        "fdi_threshold": row(fdi, f"MD = FDI {'>=' if fdi['direction'] == 'ge' else '<='} порог; порог подобран на val "
                                  f"(оптимистично). Порог, подобранный на train: F1 на val "
                                  f"{idx['FDI']['train_tuned']['f1_md']:.3f}",
                             direction=fdi["direction"], scene_ci95_f1=fdi["scene_ci95_f1"],
                             f1_md_threshold_from_train=idx["FDI"]["train_tuned"]["f1_md"]),
        "fdi_interval": row(rules["FDI_interval"]["val_tuned"],
                            "MD = нижний <= FDI < верхний (два порога, подобраны на val). Одиночный порог FDI не работает: "
                            "у Sediment-Laden Water и Sargassum FDI выше, чем у мусора. Пороги с train: F1 на val "
                            f"{rules['FDI_interval']['train_tuned']['f1_md']:.3f}",
                            threshold=None, bounds=rules["FDI_interval"]["val_tuned"]["bounds"],
                            scene_ci95_f1=rules["FDI_interval"]["val_tuned"]["scene_ci95_f1"],
                            f1_md_threshold_from_train=rules["FDI_interval"]["train_tuned"]["f1_md"]),
        "fdi_ndvi_box": row(rules["FDI_NDVI_box"]["val_tuned"],
                            "MD = FDI в интервале И NDVI в интервале (4 порога, подобраны на val; идея Biermann 2020: "
                            "FDI + NDVI). Пороги с train: F1 на val "
                            f"{rules['FDI_NDVI_box']['train_tuned']['f1_md']:.3f}",
                            threshold=None, bounds=rules["FDI_NDVI_box"]["val_tuned"]["bounds"],
                            scene_ci95_f1=rules["FDI_NDVI_box"]["val_tuned"]["scene_ci95_f1"],
                            f1_md_threshold_from_train=rules["FDI_NDVI_box"]["train_tuned"]["f1_md"]),
        "single_index_threshold": row(pick(best_single), f"лучший из NDVI/FAI: {best_single}; порог подобран на val. "
                                                         f"Порог с train: F1 {idx[best_single]['train_tuned']['f1_md']:.3f}",
                                      index=best_single, direction=pick(best_single)["direction"],
                                      scene_ci95_f1=pick(best_single)["scene_ci95_f1"],
                                      f1_md_threshold_from_train=idx[best_single]["train_tuned"]["f1_md"]),
        "best_any_index_threshold": row(pick(best_any), f"лучший из всех 8 индексов, B8 и FDI в варианте кода MARIDA: "
                                                        f"{best_any}; порог подобран на val",
                                        index=best_any, direction=pick(best_any)["direction"],
                                        scene_ci95_f1=pick(best_any)["scene_ci95_f1"],
                                        f1_md_threshold_from_train=idx[best_any]["train_tuned"]["f1_md"]),
        "random_forest_marida": row(rf0["argmax"], "RandomForest с параметрами кода MARIDA (125 деревьев, глубина 20, "
                                                   "balanced_subsample, вес 1/уверенность, 12–15 → Marine Water), "
                                                   "11 каналов + 8 индексов, без окон и GLCM; мультикласс, MD = argmax "
                                                   "(без подбора порога), обучен на всех размеченных пикселях MARIDA train; "
                                                   f"seed {rf0['seed']}",
                                    f1_mean=rf_f1_m, f1_std=rf_f1_sd, n_seeds=len(rf_runs),
                                    scene_ci95_f1=rf0["argmax"]["scene_ci95_f1"]),
        "random_forest_marida_prob_threshold": row(rf0["prob_val_tuned"], "тот же RF, MD = P(MD) >= порог, порог подобран на val",
                                                   f1_mean=rfp_f1_m, f1_std=rfp_f1_sd, n_seeds=len(rf_runs),
                                                   scene_ci95_f1=rf0["prob_val_tuned"]["scene_ci95_f1"]),
    }
    if lg:
        f_m, f_sd = mean_sd([r["f1"] for r in lg])
        i_m, _ = mean_sd([r["iou"] for r in lg])
        s0 = lg[0]
        out["lgbm_no_windows"] = {"f1_md": f_m, "iou_md": i_m, "precision": round(s0["precision"], 4),
                                  "recall": round(s0["recall"], 4), "threshold": s0["threshold"],
                                  "f1_std": f_sd, "n_seeds": len(lg), "f1_seeds": [r["f1"] for r in lg],
                                  "note": "наш LightGBM (400 деревьев) на 11 каналах + 8 индексах без окон, только MARIDA train; "
                                          "F1/IoU — среднее по 3 seed, P/R — seed 0; порог подобран на val "
                                          "(из журнала эксперимента H0, не переобучался)"}
    fin = ROOT / "weights" / "lgbm" / "meta.json"
    if fin.exists():
        m = json.loads(fin.read_text(encoding="utf-8"))
        v = m.get("val_md") or {}
        out["final_lgbm_reference"] = {"f1_md": v.get("f1"), "iou_md": v.get("iou"), "precision": v.get("precision"),
                                       "recall": v.get("recall"), "threshold": m.get("threshold"),
                                       "note": "итоговая модель (для сравнения): LightGBM + оконные признаки, MARIDA train + MADOS"}
    out["_meta"] = {"generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                    "split": "MARIDA val (official), all labelled pixels pooled, ignore=0; test never read",
                    "n_val_px": int(len(yva)), "n_val_md": int((yva == 1).sum()), "n_train_px": int(len(ytr)),
                    "metric_code": "src/macroplastic/metrics.py binary_scores",
                    "scene_ci": f"95 % bootstrap over {n_sc} val scenes, {N_BOOT} reps, threshold fixed",
                    "rf_params_source": "github.com/marine-debris/marine-debris.github.io semantic_segmentation/random_forest",
                    "all_indices": idx, "interval_rules": rules, "rf_runs": rf_runs, "lgbm_no_windows_runs": lg,
                    "seconds": round(time.time() - t0, 1)}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[baselines] -> {a.out} ({time.time() - t0:.0f}s)")
    for k, v in out.items():
        if not k.startswith("_"):
            print(f"  {k:38s} F1 {v['f1_md']} IoU {v['iou_md']} P {v['precision']} R {v['recall']} thr {v['threshold']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
