"""L14: threshold calibration and MDD vs our LightGBM agreement (analysis only, nothing is modified).

Parts
  scenes : every live scene data/live/<region>/<date>/ with prob_mdd.tif + prob_lgbm.tif + water_mask.tif.
           Over water (water_mask == 1): share above a threshold grid for each model, IoU/Dice of binary masks,
           share of MDD detections confirmed by LGBM within r = 1, 2 px (and vice versa) with a chance level
           (area of the dilated partner mask / water), object-level confirmation (8-connected components),
           Spearman of the probabilities (random water subsample), candidate "confident detection" rules.
           There are NO labels on live scenes: every number here is agreement, not accuracy.
  val    : MARIDA **val** only (official split; test is never read). Our model weights/lgbm_live on the cached
           L3 val features (out/l3_cache/val_win.npz, all labelled px): reliability diagram, ECE, Brier,
           F1/P/R vs threshold, scene bootstrap of the F1 curve. Optional MDD on val patches (--mdd-val) as a
           reference that is NOT independent (MDD was trained on MARIDA); CPU, B9 substituted by B8A.
  figs   : agreement maps (reports/figures/agreement_<region>_<date>.png) + reports/figures/calibration_lgbm_val.png

Probabilities in prob_*.tif are uint8 = round(P*255); a pixel is "above t" if u8/255 >= t (differences to the
float counts in prob_*.json are reported per scene).

    set CUDA_VISIBLE_DEVICES=
    set PYTHONPATH=src
    .venv\\Scripts\\python.exe scripts\\model_agreement.py --mdd-val
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LIVE = ROOT / "data" / "live"
REPORTS = ROOT / "reports"
FIGS = REPORTS / "figures"

MDD_GRID = [0.0639, 0.3, 0.5, 0.7]
LGBM_GRID = [0.3, 0.5, 0.64, 0.8, 0.9]
MDD_DISPLAY, MDD_CK = 0.5, 0.06387047469615936
RADII = [1, 2]
# pairs (mdd_thr, lgbm_thr) for proximity / object confirmation
PAIRS = [(0.5, 0.64), (0.0639, 0.64), (0.5, 0.5), (0.3, 0.5)]
SPEARMAN_N = 400_000

# categorical slots 1..3 of the reference palette (validated all-pairs, light mode)
C_MDD, C_LGBM, C_BOTH = "#2a78d6", "#eb6834", "#1baf7a"


# ----------------------------------------------------------------------------------------------- helpers
def disk(r: int) -> np.ndarray:
    y, x = np.mgrid[-r:r + 1, -r:r + 1]
    return (x * x + y * y) <= r * r


def dilate(m: np.ndarray, r: int) -> np.ndarray:
    from scipy import ndimage
    if r <= 0:
        return m
    return ndimage.binary_dilation(m, structure=disk(r))


def label8(m: np.ndarray):
    from scipy import ndimage
    return ndimage.label(m, structure=np.ones((3, 3), bool))


def read1(p: Path) -> np.ndarray:
    import rasterio
    with rasterio.open(p) as src:
        return src.read(1)


def list_scenes() -> list[Path]:
    out = []
    for d in sorted(LIVE.glob("*/*")):
        if all((d / f).is_file() for f in ("prob_mdd.tif", "prob_lgbm.tif", "water_mask.tif", "prob_mdd.json",
                                             "prob_lgbm.json")):
            out.append(d)
    return out


def above(u8: np.ndarray, t: float) -> np.ndarray:
    # u8/255 >= t  <=>  u8 >= ceil(t*255) (with float tolerance)
    return u8 >= int(np.ceil(t * 255 - 1e-6))


def iou_dice(a: np.ndarray, b: np.ndarray) -> tuple[float | None, float | None]:
    inter = int((a & b).sum())
    na, nb = int(a.sum()), int(b.sum())
    union = na + nb - inter
    if union == 0:
        return None, None
    return inter / union, 2 * inter / (na + nb)


def spearman(x: np.ndarray, y: np.ndarray) -> float | None:
    from scipy.stats import spearmanr
    if len(x) < 10 or x.std() == 0 or y.std() == 0:
        return None
    return float(spearmanr(x, y).statistic)


def r4(v):
    return None if v is None else round(float(v), 4)


# ----------------------------------------------------------------------------------------------- scenes
def analyse_scene(d: Path, rng: np.random.Generator) -> dict:
    t0 = time.time()
    water = read1(d / "water_mask.tif").astype(bool)
    um = read1(d / "prob_mdd.tif")
    ul = read1(d / "prob_lgbm.tif")
    jm = json.loads((d / "prob_mdd.json").read_text(encoding="utf-8"))
    jl = json.loads((d / "prob_lgbm.json").read_text(encoding="utf-8"))
    sj = json.loads((d / "scene.json").read_text(encoding="utf-8")) if (d / "scene.json").is_file() else {}
    thr_l = float(jl["threshold"])
    nw = int(water.sum())
    um = np.where(water, um, 0)
    ul = np.where(water, ul, 0)
    res = dict(region=d.parent.name, date=d.name, scene_id=sj.get("scene_id"), n_water=nw,
               water_frac=sj.get("water_frac"), water_b11_median=sj.get("water_b11_median"),
               glint_or_haze=sj.get("glint_or_haze"), lgbm_threshold=thr_l,
               mdd_threshold=float(jm["threshold"]))
    if nw == 0:
        res["error"] = "no water"
        return res
    # --- share above threshold
    res["mdd_above"] = {str(t): int(above(um, t).sum()) for t in MDD_GRID}
    res["lgbm_above"] = {str(t): int(above(ul, t).sum()) for t in LGBM_GRID}
    res["mdd_permille"] = {k: round(1000 * v / nw, 4) for k, v in res["mdd_above"].items()}
    res["lgbm_permille"] = {k: round(1000 * v / nw, 4) for k, v in res["lgbm_above"].items()}
    res["json_check"] = dict(
        mdd_0_5_json=jm.get("n_above_threshold_water"), mdd_0_5_u8=res["mdd_above"]["0.5"],
        mdd_ck_json=jm.get("n_above_checkpoint_threshold_water"), mdd_ck_u8=res["mdd_above"]["0.0639"],
        lgbm_json=jl.get("n_above_threshold_water"), lgbm_u8=int(above(ul, thr_l).sum()))
    # --- IoU / Dice for every threshold combination
    masks_m = {t: above(um, t) for t in MDD_GRID}
    masks_l = {t: above(ul, t) for t in LGBM_GRID}
    res["iou"], res["dice"] = {}, {}
    for tm, a in masks_m.items():
        for tl, b in masks_l.items():
            i, dc = iou_dice(a, b)
            res["iou"][f"mdd{tm}|lgbm{tl}"] = r4(i)
            res["dice"][f"mdd{tm}|lgbm{tl}"] = r4(dc)
    # --- proximity confirmation (pixels and objects) + chance level
    res["proximity"] = {}
    for tm, tl in PAIRS:
        a, b = masks_m[tm], masks_l[tl]
        na, nb = int(a.sum()), int(b.sum())
        la, ka = label8(a)
        lb, kb = label8(b)
        entry = dict(n_mdd_px=na, n_lgbm_px=nb, n_mdd_obj=int(ka), n_lgbm_obj=int(kb))
        for r in [0] + RADII:
            db, da = dilate(b, r) & water, dilate(a, r) & water
            e = {}
            e["mdd_px_confirmed"] = r4((a & db).sum() / na) if na else None
            e["lgbm_px_confirmed"] = r4((b & da).sum() / nb) if nb else None
            e["chance_mdd_px"] = r4(db.sum() / nw)  # P(random water px falls in dilated LGBM mask)
            e["chance_lgbm_px"] = r4(da.sum() / nw)
            if ka:
                hit = np.unique(la[a & db])
                e["mdd_obj_confirmed"] = r4(len(hit[hit > 0]) / ka)
                e["n_mdd_obj_confirmed"] = int(len(hit[hit > 0]))
            if kb:
                hit = np.unique(lb[b & da])
                e["lgbm_obj_confirmed"] = r4(len(hit[hit > 0]) / kb)
                e["n_lgbm_obj_confirmed"] = int(len(hit[hit > 0]))
            entry[f"r{r}"] = e
        res["proximity"][f"mdd{tm}|lgbm{tl}"] = entry
    # --- Spearman over water (random subsample) and over "active" px (either model P >= 0.1)
    widx = np.flatnonzero(water.ravel())
    sel = widx if len(widx) <= SPEARMAN_N else rng.choice(widx, SPEARMAN_N, replace=False)
    xm, xl = um.ravel()[sel].astype(np.float32), ul.ravel()[sel].astype(np.float32)
    res["spearman_water"] = r4(spearman(xm, xl))
    act = water & ((um >= 26) | (ul >= 26))
    aidx = np.flatnonzero(act.ravel())
    if len(aidx) > SPEARMAN_N:
        aidx = rng.choice(aidx, SPEARMAN_N, replace=False)
    res["n_active_p0_1"] = int(act.sum())
    res["spearman_active_p0_1"] = r4(spearman(um.ravel()[aidx].astype(np.float32), ul.ravel()[aidx].astype(np.float32)))
    # --- quantile match: LGBM threshold that flags as many water px as MDD@0.5 (and MDD thr matching LGBM@thr)
    n05 = res["mdd_above"]["0.5"]
    if n05 > 0:
        hl = np.bincount(ul[water], minlength=256)[::-1].cumsum()[::-1]  # hl[k] = #px with u8 >= k
        k = int(np.searchsorted(-hl, -n05, side="right")) - 1
        res["lgbm_thr_matching_mdd05_count"] = round(max(k, 0) / 255, 4)
    nl = int(above(ul, thr_l).sum())
    if nl > 0:
        hm = np.bincount(um[water], minlength=256)[::-1].cumsum()[::-1]
        k = int(np.searchsorted(-hm, -nl, side="right")) - 1
        res["mdd_thr_matching_lgbm_count"] = round(max(k, 0) / 255, 4)
    # --- candidate "confident detection" rules (proposals, not applied)
    m05, mck, lt = masks_m[0.5], masks_m[0.0639], masks_l[0.64]
    lt2 = dilate(lt, 2)
    rules = {}
    strict = m05 & lt
    rules["A_strict_pixel_and"] = strict
    rules["B_mdd05_and_lgbm_within2"] = m05 & lt2
    rules["C_mddck_and_lgbm_within2"] = mck & lt2
    # D: union of both models' objects that are confirmed by the other within 2 px (object-level consensus)
    la, ka = label8(m05)
    lb, kb = label8(lt)
    okA = np.zeros(ka + 1, bool)
    okA[np.unique(la[m05 & lt2])] = True
    okA[0] = False
    okB = np.zeros(kb + 1, bool)
    okB[np.unique(lb[lt & dilate(m05, 2)])] = True
    okB[0] = False
    rules["D_objects_confirmed_either_way"] = okA[la] | okB[lb]
    res["rules"] = {}
    for k, m in rules.items():
        _, n_obj = label8(m)
        res["rules"][k] = dict(n_px=int(m.sum()), permille=round(1000 * m.sum() / nw, 4), n_obj=int(n_obj))
    res["rules"]["ref_mdd05"] = dict(n_px=int(m05.sum()), n_obj=int(ka))
    res["rules"]["ref_lgbm_thr"] = dict(n_px=int(lt.sum()), n_obj=int(kb))
    res["seconds"] = round(time.time() - t0, 1)
    return res


def run_scenes(only: list[str] | None) -> list[dict]:
    rng = np.random.default_rng(0)
    out = []
    for d in list_scenes():
        tag = f"{d.parent.name}/{d.name}"
        if only and tag not in only and d.parent.name not in only:
            continue
        r = analyse_scene(d, rng)
        p = r.get("proximity", {}).get("mdd0.5|lgbm0.64", {}).get("r2", {})
        print(f"{tag}: water {r['n_water']}, MDD@0.5 {r.get('mdd_above', {}).get('0.5')}, "
              f"LGBM@{r['lgbm_threshold']} {r.get('json_check', {}).get('lgbm_u8')}, "
              f"conf r2 {p.get('mdd_px_confirmed')}/{p.get('lgbm_px_confirmed')}, rho {r.get('spearman_water')} "
              f"({r.get('seconds')} s)", flush=True)
        out.append(r)
    return out


# ----------------------------------------------------------------------------------------------- MARIDA val
def reliability(p: np.ndarray, y: np.ndarray, edges: np.ndarray):
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, len(edges) - 2)
    rows = []
    ece = 0.0
    for b in range(len(edges) - 1):
        m = idx == b
        n = int(m.sum())
        if n == 0:
            rows.append(dict(lo=float(edges[b]), hi=float(edges[b + 1]), n=0))
            continue
        conf, acc = float(p[m].mean()), float(y[m].mean())
        ece += n / len(p) * abs(conf - acc)
        rows.append(dict(lo=round(float(edges[b]), 4), hi=round(float(edges[b + 1]), 4), n=n,
                         mean_p=round(conf, 5), frac_md=round(acc, 5), n_md=int(y[m].sum())))
    return rows, ece


def prf(p, y, t):
    pr = p >= t
    tp = int((pr & y).sum())
    fp = int((pr & ~y).sum())
    fn = int((~pr & y).sum())
    P = tp / (tp + fp) if tp + fp else 0.0
    R = tp / (tp + fn) if tp + fn else 0.0
    F = 2 * P * R / (P + R) if P + R else 0.0
    return P, R, F, tp, fp, fn


def run_val(mdd_val: bool) -> dict:
    import lightgbm as lgb
    from macroplastic.data.marida import list_patches, parse_scene

    wd = ROOT / "weights" / "lgbm_live"
    meta = json.loads((wd / "meta.json").read_text(encoding="utf-8"))
    z = np.load(ROOT / "out" / "l3_cache" / "val_win.npz", allow_pickle=False)
    names = list(z["names"])
    fidx = [names.index(f) for f in meta["features"]]
    X = np.ascontiguousarray(z["X"][:, fidx])
    y = z["y"] == 1
    pid = z["pid"]
    booster = lgb.Booster(model_file=str(wd / "model.txt"))
    p = booster.predict(X, num_threads=16).astype(np.float64)
    thr = float(meta["threshold"])
    P, R, F, tp, fp, fn = prf(p, y, thr)
    out = dict(weights="weights/lgbm_live", split="MARIDA val (official), all labelled px, MD vs rest",
               n_px=int(len(y)), n_md=int(y.sum()), prevalence=round(float(y.mean()), 5), threshold=thr,
               check_meta_f1=meta["val_md"]["f1"], f1_at_threshold=round(F, 4),
               precision_at_threshold=round(P, 4), recall_at_threshold=round(R, 4), tp=tp, fp=fp, fn=fn)
    # calibration
    rows10, ece10 = reliability(p, y, np.linspace(0, 1, 11))
    rows15, ece15 = reliability(p, y, np.linspace(0, 1, 16))
    # conditional ECE: only px with p >= 0.05 (the region that matters for the UI)
    m = p >= 0.05
    rowsc, ecec = reliability(p[m], y[m], np.linspace(0.05, 1, 20))
    qe = np.unique(np.quantile(p[m], np.linspace(0, 1, 13)))
    qe[0], qe[-1] = 0.05, 1.0 + 1e-9
    rowsq, eceq = reliability(p[m], y[m], qe)
    out["calibration"] = dict(
        ece_p_ge_0_05_equal_count_12bins=round(eceq, 4), bins_p_ge_0_05_equal_count=rowsq,
        ece_10bins=round(ece10, 5), ece_15bins=round(ece15, 5), brier=round(float(((p - y) ** 2).mean()), 6),
        ece_p_ge_0_05=round(ecec, 4), n_p_ge_0_05=int(m.sum()), bins10=rows10, bins_p_ge_0_05=rowsc,
        mean_p=round(float(p.mean()), 5), note="ECE over all labelled px is dominated by the huge p~0 bin; "
                                              "ece_p_ge_0_05 = ECE restricted to px with p >= 0.05")
    # F1 curve
    ts = np.round(np.arange(0.02, 0.99, 0.01), 2)
    curve = [(float(t),) + tuple(round(v, 4) for v in prf(p, y, t)[:3]) for t in ts]
    best = max(curve, key=lambda c: c[3])
    plateau = [c[0] for c in curve if c[3] >= best[3] - 0.005]
    out["f1_curve"] = [dict(t=c[0], precision=c[1], recall=c[2], f1=c[3]) for c in curve]
    out["f1_best"] = dict(t=best[0], f1=best[3], plateau_within_0_005=[min(plateau), max(plateau)])
    # scene bootstrap (12 val scenes)
    patches = list_patches("val")
    scene_of_pid = np.array([parse_scene(pp.stem)["scene"] if "scene" in parse_scene(pp.stem)
                             else "_".join(pp.stem.split("_")[:-1]) for pp in patches])
    sc = scene_of_pid[pid]
    uniq = np.unique(sc)
    rng = np.random.default_rng(0)
    tsb = np.round(np.arange(0.1, 0.96, 0.02), 2)
    # precompute per-scene tp/fp/fn per threshold
    per = np.zeros((len(uniq), len(tsb), 3))
    for i, s in enumerate(uniq):
        mm = sc == s
        ps, ys = p[mm], y[mm]
        for j, t in enumerate(tsb):
            pr = ps >= t
            per[i, j] = ((pr & ys).sum(), (pr & ~ys).sum(), (~pr & ys).sum())
    B = 1000
    f1s = np.zeros((B, len(tsb)))
    for b in range(B):
        k = rng.integers(0, len(uniq), len(uniq))
        s = per[k].sum(0)
        f1s[b] = 2 * s[:, 0] / np.maximum(2 * s[:, 0] + s[:, 1] + s[:, 2], 1)
    argm = tsb[f1s.argmax(1)]
    j64 = int(np.argmin(np.abs(tsb - 0.64)))
    j50 = int(np.argmin(np.abs(tsb - 0.5)))
    out["bootstrap_scenes"] = dict(
        n_scenes=int(len(uniq)), B=B,
        f1_at_0_64=dict(mean=round(float(f1s[:, j64].mean()), 4), p2_5=round(float(np.percentile(f1s[:, j64], 2.5)), 4),
                        p97_5=round(float(np.percentile(f1s[:, j64], 97.5)), 4)),
        f1_at_0_50=dict(mean=round(float(f1s[:, j50].mean()), 4), p2_5=round(float(np.percentile(f1s[:, j50], 2.5)), 4),
                        p97_5=round(float(np.percentile(f1s[:, j50], 97.5)), 4)),
        diff_f1_0_64_minus_0_50=dict(mean=round(float((f1s[:, j64] - f1s[:, j50]).mean()), 4),
                                     p2_5=round(float(np.percentile(f1s[:, j64] - f1s[:, j50], 2.5)), 4),
                                     p97_5=round(float(np.percentile(f1s[:, j64] - f1s[:, j50], 97.5)), 4)),
        argmax_threshold=dict(p5=float(np.percentile(argm, 5)), median=float(np.median(argm)),
                              p95=float(np.percentile(argm, 95))),
        f1_curve_mean=[dict(t=float(t), mean=round(float(f1s[:, j].mean()), 4),
                            p2_5=round(float(np.percentile(f1s[:, j], 2.5)), 4),
                            p97_5=round(float(np.percentile(f1s[:, j], 97.5)), 4)) for j, t in enumerate(tsb)])
    out["note"] = ("threshold 0.64 was itself selected on this val -> the F1 curve is in-sample for the choice; "
                   "val labels are a sparse hand-labelled subset (MD prevalence 0.5 %), not a random sample of sea px")
    out["_p"] = p  # removed before json
    out["_y"] = y
    if mdd_val:
        out["mdd_reference"] = run_mdd_val(patches)
    return out


def run_mdd_val(patches) -> dict:
    """MDD on MARIDA val patches, CPU. NOT independent (MDD trained on MARIDA train, same scenes/regions family);
    input domain differs from MDD training (MARIDA patches are ACOLITE rhorc, MDD used its own L2A scenes);
    B9 missing in MARIDA -> filled with B8A."""
    import torch
    from macroplastic.data.marida import load_patch
    from macroplastic.live.mdd import load_model, predict

    torch.set_num_threads(8)
    net, thr_ck, info = load_model(device="cpu")
    ps, ys = [], []
    t0 = time.time()
    for pp in patches:
        img, cl, conf, _ = load_patch(pp)
        m = cl > 0
        if not m.any():
            continue
        x12 = np.concatenate([img[:9], img[8:9], img[9:]], 0)  # B1..B8,B8A, B9:=B8A, B11,B12
        pr = predict(net, x12, device="cpu", tile=256, margin=32, batch=1)
        ps.append(pr[m])
        ys.append(cl[m] == 1)
    p = np.concatenate(ps).astype(np.float64)
    y = np.concatenate(ys)
    res = dict(note="REFERENCE ONLY, NOT INDEPENDENT: MDD was trained on MARIDA (train) and its checkpoint "
                    "threshold was tuned on its own validation; input here = MARIDA ACOLITE patches, B9 := B8A",
               ckpt=info["ckpt"], seconds=round(time.time() - t0, 1), n_px=int(len(y)), n_md=int(y.sum()))
    for t in (thr_ck, 0.3, 0.5, 0.7):
        P, R, F, tp, fp, fn = prf(p, y, t)
        res[f"t{round(t, 4)}"] = dict(precision=round(P, 4), recall=round(R, 4), f1=round(F, 4), tp=tp, fp=fp, fn=fn)
    rows10, ece10 = reliability(p, y, np.linspace(0, 1, 11))
    res["ece_10bins"] = round(ece10, 5)
    m = p >= 0.05
    rowsc, ecec = reliability(p[m], y[m], np.linspace(0.05, 1, 20))
    res["ece_p_ge_0_05"] = round(ecec, 4)
    res["bins_p_ge_0_05"] = rowsc
    qe = np.unique(np.quantile(p[m], np.linspace(0, 1, 13)))
    qe[0], qe[-1] = 0.05, 1.0 + 1e-9
    rowsq, eceq = reliability(p[m], y[m], qe)
    res["ece_p_ge_0_05_equal_count_12bins"] = round(eceq, 4)
    res["bins_p_ge_0_05_equal_count"] = rowsq
    ts = np.round(np.arange(0.02, 0.99, 0.01), 2)
    res["f1_curve"] = [dict(t=float(t), f1=round(prf(p, y, t)[2], 4)) for t in ts]
    return res


# ----------------------------------------------------------------------------------------------- figures
def fig_calibration(val: dict, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10.5, 4.3), dpi=110)
    ink, mute = "#0b0b0b", "#52514e"
    a1.plot([0, 1], [0, 1], color="#b8b7b2", lw=1, ls="--", label="идеальная калибровка")
    rows = [r for r in val["calibration"]["bins_p_ge_0_05_equal_count"] if r["n"] > 0]
    x = np.array([r["mean_p"] for r in rows])
    yv = np.array([r["frac_md"] for r in rows])
    se = np.array([np.sqrt(max(v * (1 - v), 1e-4) / r["n"]) for v, r in zip(yv, rows)])
    a1.errorbar(x, yv, yerr=1.96 * se, color=C_LGBM, lw=2, elinewidth=1, capsize=0, zorder=2)
    a1.scatter(x, yv, s=36, color=C_LGBM, edgecolor="white", linewidth=1.5, zorder=3,
               label=f"LightGBM (lgbm_live), 12 корзин по ~{rows[0]['n']} px, ±95 %")
    if "mdd_reference" in val:
        rm = [r for r in val["mdd_reference"].get("bins_p_ge_0_05_equal_count", val["mdd_reference"]["bins_p_ge_0_05"])
              if r["n"] > 0]
        a1.plot([r["mean_p"] for r in rm], [r["frac_md"] for r in rm], color=mute, lw=1.5, ls=":",
                marker="o", ms=4, label="MDD (справочно, не независимо)")
    a1.set_xlim(0, 1)
    a1.set_ylim(0, 1)
    a1.set_xlabel("средняя предсказанная P(MD) в корзине", color=ink)
    a1.set_ylabel("доля MD среди размеченных пикселей", color=ink)
    c = val["calibration"]
    a1.set_title(f"Надёжность, MARIDA val (p ≥ 0.05; n={c['n_p_ge_0_05']})\n"
                 f"ECE(p≥0.05, равные корзины) {c['ece_p_ge_0_05_equal_count_12bins']:.3f} · ECE(все px) {c['ece_10bins']:.4f}",
                 fontsize=10, color=ink)
    a1.legend(frameon=False, fontsize=8, loc="upper left")
    t = [r["t"] for r in val["f1_curve"]]
    a2.plot(t, [r["f1"] for r in val["f1_curve"]], color=C_LGBM, lw=2, label="F1 LightGBM")
    a2.plot(t, [r["precision"] for r in val["f1_curve"]], color=C_MDD, lw=1.5, label="точность (precision)")
    a2.plot(t, [r["recall"] for r in val["f1_curve"]], color=C_BOTH, lw=1.5, label="полнота (recall)")
    bs = val["bootstrap_scenes"]["f1_curve_mean"]
    a2.fill_between([r["t"] for r in bs], [r["p2_5"] for r in bs], [r["p97_5"] for r in bs], color=C_LGBM,
                    alpha=0.12, lw=0, label="F1: 95 % бутстрэп по 12 сценам")
    if "mdd_reference" in val:
        fm = max(r["f1"] for r in val["mdd_reference"]["f1_curve"])
        a2.plot([], [], color=mute, lw=1.5, ls=":",
                label=f"F1 MDD ≤ {fm:.2f} — ниже оси (справочно, не независимо)")
    a2.axvline(val["threshold"], color=ink, lw=1, ls="--")
    a2.text(val["threshold"] + 0.01, 0.62, f"порог UI {val['threshold']}", fontsize=8, color=ink)
    a2.set_xlim(0, 1)
    a2.set_ylim(0.6, 1.0)
    a2.set_xlabel("порог P(MD)", color=ink)
    a2.set_title(f"F1(порог), MARIDA val; max F1 {val['f1_best']['f1']} при {val['f1_best']['t']}\n"
                 f"плато (F1 ≥ max−0.005): {val['f1_best']['plateau_within_0_005'][0]}–"
                 f"{val['f1_best']['plateau_within_0_005'][1]}", fontsize=10, color=ink)
    a2.legend(frameon=False, fontsize=8, loc="lower left")
    for a in (a1, a2):
        a.grid(color="#e6e5e0", lw=0.6)
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
        a.tick_params(colors=mute, labelsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def fig_agreement(d: Path, path: Path, win: int = 700, r: int = 2):
    """RGB crop (window of `win` px around the densest MDD@0.5 area) with MDD-only / LGBM-only / both."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    import rasterio
    from scipy import ndimage

    water = read1(d / "water_mask.tif").astype(bool)
    um = read1(d / "prob_mdd.tif")
    ul = read1(d / "prob_lgbm.tif")
    thr_l = json.loads((d / "prob_lgbm.json").read_text(encoding="utf-8"))["threshold"]
    a = above(um, MDD_DISPLAY) & water
    b = above(ul, thr_l) & water
    H, W = a.shape
    cons = (a & dilate(b, r)) | (b & dilate(a, r))
    focus = cons if cons.sum() >= 20 else (a | b)
    dens = ndimage.uniform_filter(focus.astype(np.float32), size=win // 2)
    cy, cx = np.unravel_index(np.argmax(dens), dens.shape)
    y0 = int(np.clip(cy - win // 2, 0, max(H - win, 0)))
    x0 = int(np.clip(cx - win // 2, 0, max(W - win, 0)))
    sl = (slice(y0, y0 + win), slice(x0, x0 + win))
    with rasterio.open(d / "bands.tif") as src:
        names = list(src.descriptions)
        rgb = np.stack([src.read(names.index(bn) + 1, window=((y0, y0 + win), (x0, x0 + win))) for bn in ("B4", "B3", "B2")], -1)
    rgb = np.nan_to_num(rgb.astype(np.float32))
    lo, hi = np.percentile(rgb[water[sl]] if water[sl].any() else rgb, [1, 99.5], axis=0) if rgb.ndim == 3 else (0, 1)
    rgb = np.clip((rgb - lo) / np.maximum(hi - lo, 1e-6), 0, 1) ** 0.8
    rgb = 0.55 * rgb  # darken so markers stand out
    aa, bb = a[sl], b[sl]
    both_a = aa & dilate(bb, r)
    both_b = bb & dilate(aa, r)
    only_a = aa & ~both_a
    only_b = bb & ~both_b
    both = both_a | both_b
    img = rgb.copy()

    def paint(m, hexc):
        c = np.array([int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5)])
        mm = ndimage.binary_dilation(m, structure=disk(1))  # 1 px halo for visibility
        img[mm] = c

    paint(only_b, C_LGBM)
    paint(only_a, C_MDD)
    paint(both, C_BOTH)
    fig, ax = plt.subplots(figsize=(5.6, 5.9), dpi=100)
    ax.imshow(img, interpolation="nearest")
    ax.set_xticks([])
    ax.set_yticks([])
    km = win * 10 / 1000
    ax.set_title(f"{d.parent.name} {d.name}: окно {km:.0f}×{km:.0f} км\n"
                 f"MDD P≥{MDD_DISPLAY} vs LightGBM P≥{thr_l}; «оба» = в пределах {r} px (20 м)", fontsize=9)
    leg = [Patch(color=C_MDD, label=f"только MDD ({int(only_a.sum())} px)"),
           Patch(color=C_LGBM, label=f"только LightGBM ({int(only_b.sum())} px)"),
           Patch(color=C_BOTH, label=f"обе модели ({int(both.sum())} px)")]
    ax.legend(handles=leg, loc="lower center", bbox_to_anchor=(0.5, -0.13), ncol=3, frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)
    # shrink if needed (limit 400 KB): re-save as palettized PNG
    if path.stat().st_size > 400_000:
        # palettize, but keep the three marker colours exact (plain quantize() merges rare saturated colours)
        from PIL import Image
        im = Image.open(path).convert("RGB")
        base = im.quantize(colors=240, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        pal = base.getpalette()[:240 * 3]
        for hexc in (C_MDD, C_LGBM, C_BOTH, "#000000", "#ffffff"):
            pal += [int(hexc[i:i + 2], 16) for i in (1, 3, 5)]
        pal += [0] * (768 - len(pal))
        pimg = Image.new("P", (1, 1))
        pimg.putpalette(pal)
        im.quantize(palette=pimg, dither=Image.Dither.NONE).save(path, optimize=True)
    return dict(path=str(path.relative_to(ROOT)), window=[y0, x0, win], kb=round(path.stat().st_size / 1024, 1))


# ----------------------------------------------------------------------------------------------- summary
def summarize(res: dict) -> str:
    """Markdown table per scene + pooled numbers (printed with --table; pasted into reports/model_agreement.md)."""
    L = ["| сцена | вода, Мpx | B11 воды | MDD ‰ при 0.064 / 0.3 / 0.5 / 0.7 | LGBM ‰ при 0.3 / 0.5 / 0.64 / 0.8 / 0.9 "
         "| IoU (0.5 / 0.64) | MDD→LGBM r≤2 px (случайно) | LGBM→MDD r≤2 px (случайно) | LGBM→MDD@0.064 r≤2 "
         "| объекты MDD подтв. | ρ Спирмена | консенсус D: px (объекты) |",
         "|" + "---|" * 12]
    tot = dict(m=0, l=0, b=0, d=0, dobj=0, mobj=0, lobj=0, sc_d=0, n=0, mconf=0, lconf=0, lck=0)
    rhos = []
    for s in res["scenes"]:
        if "error" in s:
            continue
        pr = s["proximity"]["mdd0.5|lgbm0.64"]
        p2 = pr["r2"]
        pc = s["proximity"]["mdd0.0639|lgbm0.64"]["r2"]
        R = s["rules"]
        f = lambda v: "–" if v is None else f"{v:.2f}"
        mm = " / ".join(f"{v:.3g}" for v in s["mdd_permille"].values())
        ll = " / ".join(f"{v:.3g}" for v in s["lgbm_permille"].values())
        mo = f"{p2.get('n_mdd_obj_confirmed', 0)}/{pr['n_mdd_obj']}"
        flag = " ⚠блик" if s.get("glint_or_haze") else ""
        L.append(f"| {s['region']} {s['date']}{flag} | {s['n_water'] / 1e6:.2f} | {s.get('water_b11_median')} | {mm} | {ll} "
                 f"| {f(s['iou']['mdd0.5|lgbm0.64'])} | {f(p2['mdd_px_confirmed'])} ({p2['chance_mdd_px']:.4f}) "
                 f"| {f(p2['lgbm_px_confirmed'])} ({p2['chance_lgbm_px']:.4f}) | {f(pc['lgbm_px_confirmed'])} "
                 f"({pc['chance_lgbm_px']:.3f}) | {mo} | {s['spearman_water']:.2f} "
                 f"| {R['D_objects_confirmed_either_way']['n_px']} ({R['D_objects_confirmed_either_way']['n_obj']}) |")
        tot["n"] += 1
        tot["m"] += R["ref_mdd05"]["n_px"]
        tot["l"] += R["ref_lgbm_thr"]["n_px"]
        tot["mobj"] += R["ref_mdd05"]["n_obj"]
        tot["lobj"] += R["ref_lgbm_thr"]["n_obj"]
        tot["b"] += R["B_mdd05_and_lgbm_within2"]["n_px"]
        tot["d"] += R["D_objects_confirmed_either_way"]["n_px"]
        tot["dobj"] += R["D_objects_confirmed_either_way"]["n_obj"]
        tot["sc_d"] += R["D_objects_confirmed_either_way"]["n_obj"] > 0
        tot["mconf"] += int(round((p2["mdd_px_confirmed"] or 0) * pr["n_mdd_px"]))
        tot["lconf"] += int(round((p2["lgbm_px_confirmed"] or 0) * pr["n_lgbm_px"]))
        tot["lck"] += int(round((pc["lgbm_px_confirmed"] or 0) * pr["n_lgbm_px"]))
        rhos.append(s["spearman_water"])
    L.append("")
    L.append(f"Итого по {tot['n']} сценам: MDD@0.5 {tot['m']} px ({tot['mobj']} объектов), LGBM@0.64 {tot['l']} px "
             f"({tot['lobj']} объектов); MDD-пикселей с LGBM в радиусе 2 px: {tot['mconf']} "
             f"({tot['mconf'] / max(tot['m'], 1):.1%}); LGBM-пикселей с MDD@0.5 в радиусе 2 px: {tot['lconf']} "
             f"({tot['lconf'] / max(tot['l'], 1):.1%}), с MDD@0.064: {tot['lck']} ({tot['lck'] / max(tot['l'], 1):.1%}); "
             f"правило D: {tot['d']} px, {tot['dobj']} объектов, сцен хотя бы с одним объектом: {tot['sc_d']}; "
             f"ρ Спирмена по воде: медиана {np.median(rhos):.3f}, диапазон {min(rhos):.3f}…{max(rhos):.3f}.")
    res["pooled"] = tot | dict(spearman_median=round(float(np.median(rhos)), 4), spearman_min=min(rhos),
                               spearman_max=max(rhos))
    return "\n".join(L)


# ----------------------------------------------------------------------------------------------- main
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parts", default="scenes,val,figs", help="comma list of scenes,val,mddval,figs")
    ap.add_argument("--only", nargs="*", help="region or region/date filter for scenes")
    ap.add_argument("--mdd-val", action="store_true", help="also MDD on MARIDA val (reference, not independent)")
    ap.add_argument("--fig-scenes", nargs="*", default=["durban/2019-04-24", "honduras/2020-08-29"])
    ap.add_argument("--out", default=str(REPORTS / "model_agreement.json"))
    ap.add_argument("--table", action="store_true", help="print the markdown table per scene + pooled numbers")
    a = ap.parse_args()
    parts = set(a.parts.split(","))
    outp = Path(a.out)
    res = json.loads(outp.read_text(encoding="utf-8")) if outp.is_file() else {}
    res["generated"] = time.strftime("%Y-%m-%d %H:%M")
    res["thresholds"] = dict(mdd_grid=MDD_GRID, lgbm_grid=LGBM_GRID, mdd_display=MDD_DISPLAY, mdd_checkpoint=MDD_CK,
                             pairs=PAIRS, radii_px=RADII, pixel_rule="u8/255 >= t, water_mask == 1")
    if "scenes" in parts:
        res["scenes"] = run_scenes(a.only)
    if "val" in parts:
        v = run_val(a.mdd_val)
        p, y = v.pop("_p"), v.pop("_y")
        if not a.mdd_val and "val" in res and "mdd_reference" in res["val"]:
            v["mdd_reference"] = res["val"]["mdd_reference"]
        res["val"] = v
        print(f"val: F1@{v['threshold']} {v['f1_at_threshold']} (meta {v['check_meta_f1']}), "
              f"best {v['f1_best']}, ECE10 {v['calibration']['ece_10bins']}, "
              f"ECE(p>=0.05) {v['calibration']['ece_p_ge_0_05']}", flush=True)
    if "mddval" in parts:  # MDD reference on MARIDA val only (slow on CPU: ~3 s/patch), adds to an existing val block
        from macroplastic.data.marida import list_patches
        res.setdefault("val", {})["mdd_reference"] = run_mdd_val(list_patches("val"))
        print("mdd val:", {k: v for k, v in res["val"]["mdd_reference"].items() if k.startswith("t")}, flush=True)
    if "figs" in parts:
        FIGS.mkdir(parents=True, exist_ok=True)
        res["figures"] = []
        if "val" in res:
            fig_calibration(res["val"], FIGS / "calibration_lgbm_val.png")
            res["figures"].append("reports/figures/calibration_lgbm_val.png")
        for tag in a.fig_scenes:
            d = LIVE / tag
            if d.is_dir():
                info = fig_agreement(d, FIGS / f"agreement_{tag.replace('/', '_')}.png")
                res["figures"].append(info)
                print("fig", info, flush=True)
    if "scenes" in res and (a.table or "scenes" in parts):
        tab = summarize(res)
        if a.table:
            print(tab)
    outp.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", outp)


if __name__ == "__main__":
    main()
