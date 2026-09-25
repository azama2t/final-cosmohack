"""Lane L4: train/evaluate an smp UNet for Marine Debris on MARIDA (val only; test is never read).

    # 1) CPU: cache train/val patches as .npy (once, ~1 min)
    set CUDA_VISIBLE_DEVICES= & .venv\\Scripts\\python.exe scripts\\train_unet.py cache
    # 2) GPU (only through the queue):
    .venv\\Scripts\\python.exe scripts\\gpu_queue.py submit --name unet_s0 --wait -- ^
        .venv\\Scripts\\python.exe scripts\\train_unet.py train --seed 0 [--exp bin_r34] [--set train.epochs=40]
    # 3) CPU: stack with L3 LightGBM on val (weighted mean / max, weight + threshold picked on val)
    .venv\\Scripts\\python.exe scripts\\train_unet.py stack --exp bin_r34 --seeds 0 1 2

Outputs: weights_exp/unet/<exp>_s<seed>/{model.pt, meta.json}, out/l4_unet/val_prob_<exp>_s<seed>.npy (float16,
(328,256,256)), out/l4_unet/run_<exp>_s<seed>.json, out/l4_unet/stack_<exp>.json.
Metric: binary MD F1/IoU pooled over every labelled val pixel (ignore = 0), as macroplastic.metrics.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from macroplastic.data.marida import list_patches, load_patch  # noqa: E402
from macroplastic.models.unet.model import INPUT_NAMES, build_model, normalize, raw_inputs  # noqa: E402
from macroplastic.utils import load_yaml, seed_everything  # noqa: E402

CACHE = ROOT / "out" / "l4_unet"
WEXP = ROOT / "weights_exp" / "unet"
IGN = 255


# ------------------------------------------------------------------ data
def build_cache(split: str) -> None:
    assert split in ("train", "val"), "test split is never used here"
    CACHE.mkdir(parents=True, exist_ok=True)
    paths = list_patches(split)
    X = np.zeros((len(paths), 11, 256, 256), np.float32)
    C = np.zeros((len(paths), 256, 256), np.uint8)
    F = np.zeros((len(paths), 256, 256), np.uint8)
    for i, p in enumerate(paths):
        img, cl, conf, _ = load_patch(p)
        X[i], C[i], F[i] = img, cl, conf
    np.save(CACHE / f"{split}_x.npy", X)
    np.save(CACHE / f"{split}_cl.npy", C)
    np.save(CACHE / f"{split}_conf.npy", F)
    (CACHE / f"{split}_names.txt").write_text("\n".join(p.stem for p in paths), encoding="utf-8")
    print(f"cached {split}: {X.shape}, MD px {(C == 1).sum()}")


def load_split(split: str):
    if not (CACHE / f"{split}_x.npy").exists():
        build_cache(split)
    X = np.load(CACHE / f"{split}_x.npy", mmap_mode="r")
    C = np.load(CACHE / f"{split}_cl.npy")
    names = (CACHE / f"{split}_names.txt").read_text(encoding="utf-8").split("\n")
    return X, C, names


def inputs_of(X) -> tuple[np.ndarray, np.ndarray]:
    """(N,11,H,W) -> raw (N,19,H,W) float32, valid (N,H,W)."""
    out = np.zeros((X.shape[0], len(INPUT_NAMES)) + X.shape[2:], np.float32)
    valid = np.zeros((X.shape[0],) + X.shape[2:], bool)
    for i in range(X.shape[0]):
        out[i], valid[i] = raw_inputs(np.asarray(X[i]), INPUT_NAMES[:11])
    return out, valid


def norm_stats(R: np.ndarray, valid: np.ndarray, cfg: dict, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    n, c = R.shape[:2]
    idx = np.flatnonzero(valid.ravel())
    idx = rng.choice(idx, size=min(len(idx), int(cfg["sample_px"])), replace=False)
    hw = valid.shape[1] * valid.shape[2]
    ni, pix = idx // hw, idx % hw
    lo, hi, mu, sd = [], [], [], []
    for k in range(c):
        v = R[:, k].reshape(n, -1)[ni, pix]
        v = v[np.isfinite(v)]
        a, b = np.percentile(v, [cfg["p_lo"], cfg["p_hi"]])
        v = np.clip(v, a, b)
        lo.append(float(a)), hi.append(float(b)), mu.append(float(v.mean())), sd.append(float(max(v.std(), 1e-6)))
    return {"names": INPUT_NAMES, "lo": lo, "hi": hi, "mean": mu, "std": sd}


def targets(C: np.ndarray, task: str = "binary") -> np.ndarray:
    """binary: MD -> 1, classes 2..15 -> 0; multiclass: MARIDA scheme (12..15 -> 7), class k -> k-1 (MD = 0,
    11 classes). Unlabelled (0) -> IGN."""
    t = np.full(C.shape, IGN, np.uint8)
    if task == "multiclass":
        from macroplastic.data.marida import merge_water

        M = merge_water(C)
        t[M > 0] = M[M > 0] - 1
        return t
    t[C >= 2] = 0
    t[C == 1] = 1
    return t


# ------------------------------------------------------------------ metrics
def f1_grid(prob: np.ndarray, gt_md: np.ndarray, grid) -> list[dict]:
    """prob/gt_md: 1-D over labelled pixels. Returns metrics per threshold."""
    out = []
    P = int(gt_md.sum())
    for t in grid:
        pr = prob >= t
        tp = int((pr & gt_md).sum())
        fp = int(pr.sum()) - tp
        fn = P - tp
        f1 = 2 * tp / max(2 * tp + fp + fn, 1)
        out.append({"threshold": round(float(t), 4), "f1": f1, "iou": tp / max(tp + fp + fn, 1),
                    "precision": tp / max(tp + fp, 1), "recall": tp / max(P, 1), "tp": tp, "fp": fp, "fn": fn})
    return out


def best_of(rows: list[dict]) -> dict:
    return max(rows, key=lambda r: (r["f1"], -abs(r["threshold"] - 0.5)))


def grid_of(cfg) -> np.ndarray:
    a, b, s = cfg["threshold_grid"]
    return np.round(np.arange(a, b + 1e-9, s), 4)


# ------------------------------------------------------------------ train
def set_key(cfg: dict, key: str, value: str) -> None:
    import yaml

    d = cfg
    ks = key.split(".")
    for k in ks[:-1]:
        d = d.setdefault(k, {})
    d[ks[-1]] = yaml.safe_load(value)


def d4(x, y):
    """Random dihedral transform per sample. x (B,C,H,W), y (B,H,W)."""
    import torch

    B = x.shape[0]
    ks = torch.randint(0, 4, (B,)).tolist()
    fl = torch.randint(0, 2, (B,)).tolist()
    xs, ys = [], []
    for i in range(B):
        xi, yi = x[i], y[i]
        if fl[i]:
            xi, yi = xi.flip(-1), yi.flip(-1)
        if ks[i]:
            xi, yi = torch.rot90(xi, ks[i], (-2, -1)), torch.rot90(yi, ks[i], (-2, -1))
        xs.append(xi), ys.append(yi)
    return torch.stack(xs), torch.stack(ys)


def predict_val(net, Xv, valid_v, bs=16, md=1):
    import torch

    net.eval()
    out = np.zeros(valid_v.shape, np.float32)
    with torch.no_grad():
        for i in range(0, Xv.shape[0], bs):
            p = torch.softmax(net(Xv[i:i + bs].float()), 1)[:, md]
            out[i:i + bs] = p.cpu().numpy()
    out[~valid_v] = 0.0
    net.train()
    return out


def train(cfg: dict) -> dict:
    import torch
    import torch.nn.functional as Fnn

    seed, exp = int(cfg["seed"]), cfg["exp"]
    seed_everything(seed)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    t0 = time.time()
    Xt, Ct, _ = load_split("train")
    Xv, Cv, vnames = load_split("val")
    Rt, valid_t = inputs_of(Xt)
    Rv, valid_v = inputs_of(Xv)
    norm = norm_stats(Rt, valid_t, cfg["norm"], seed=0)
    def to16(R):
        out = np.empty(R.shape, np.float16)
        for i in range(len(R)):
            out[i] = normalize(R[i], norm)
        return torch.from_numpy(out).to(dev)

    Nt = to16(Rt)
    del Rt
    Nv = to16(Rv)
    del Rv
    task = cfg.get("task", "binary")
    MDI = 0 if task == "multiclass" else 1  # channel of P(MD)
    if task == "multiclass":
        cfg["model"]["classes"] = 11
    Tt = torch.from_numpy(targets(Ct, task)).to(dev)
    lab_v = Cv > 0
    gt_v = Cv[lab_v] == 1
    print(f"data ready {time.time() - t0:.0f}s train {tuple(Nt.shape)} val {tuple(Nv.shape)} "
          f"MD train {(Ct == 1).sum()} val {gt_v.sum()} labelled val {lab_v.sum()}", flush=True)

    m = cfg["model"]
    net = build_model(m["encoder"], len(INPUT_NAMES), int(m["classes"]), m.get("encoder_weights"), m.get("arch", "Unet")).to(dev)
    tr = cfg["train"]
    ema_d = float(tr.get("ema") or 0.0)  # >0: evaluate/save an exponential moving average of the weights
    ema = None
    if ema_d > 0:
        ema = copy.deepcopy(net).eval()
        for q in ema.parameters():
            q.requires_grad_(False)
    opt = torch.optim.AdamW(net.parameters(), lr=float(tr["lr"]), weight_decay=float(tr["weight_decay"]))
    has_md = (Ct == 1).reshape(len(Ct), -1).any(1)
    base = np.concatenate([np.arange(len(Ct))] + [np.flatnonzero(has_md)] * (int(tr["md_patch_oversample"]) - 1))
    bs, E = int(tr["batch"]), int(tr["epochs"])
    steps_per = math.ceil(len(base) / bs)
    total, warm = E * steps_per, int(tr.get("warmup_epochs", 0)) * steps_per
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: (s + 1) / max(warm, 1) if s < warm else 0.5 * (1 + math.cos(math.pi * (s - warm) / max(total - warm, 1))))
    cew = torch.ones(int(m["classes"]), device=dev)
    cew[MDI] = float(cfg["loss"]["ce_md_weight"])
    dw = float(cfg["loss"]["dice_weight"])
    rng = np.random.default_rng(seed)
    grid = grid_of(cfg)
    curve = []
    t_train = time.time()
    stopped_early = False
    for ep in range(E):
        perm = rng.permutation(base)
        tot = 0.0
        for i in range(steps_per):
            ib = torch.from_numpy(perm[i * bs:(i + 1) * bs]).to(dev)
            x, y = Nt[ib].float(), Tt[ib].long()
            if tr.get("augment") == "d4":
                x, y = d4(x, y)
            logits = net(x)
            loss = Fnn.cross_entropy(logits, y, weight=cew, ignore_index=IGN)
            if dw > 0:
                lab = (y != IGN).float()
                p = torch.softmax(logits, 1)[:, MDI] * lab
                g = (y == MDI).float()
                dice = 1 - (2 * (p * g).sum() + 1) / (p.sum() + g.sum() + 1)
                loss = loss + dw * dice
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            if ema is not None:
                with torch.no_grad():
                    for qe, qn in zip(ema.state_dict().values(), net.state_dict().values()):
                        if qe.dtype.is_floating_point:
                            qe.mul_(ema_d).add_(qn.detach(), alpha=1 - ema_d)
                        else:
                            qe.copy_(qn)
            tot += loss.item()
        el = (time.time() - t_train) / 60
        msg = f"ep {ep + 1}/{E} loss {tot / steps_per:.4f} lr {sched.get_last_lr()[0]:.2e} {el:.1f} min"
        if (ep + 1) % int(tr["eval_every"]) == 0 or ep + 1 == E:
            pv = predict_val(ema if ema is not None else net, Nv, valid_v, md=MDI)
            b = best_of(f1_grid(pv[lab_v], gt_v, grid))
            curve.append({"epoch": ep + 1, "f1": round(b["f1"], 4), "thr": b["threshold"], "loss": round(tot / steps_per, 4)})
            msg += f" | val F1 {b['f1']:.4f} @ {b['threshold']}"
        print(msg, flush=True)
        if el > float(tr.get("max_minutes", 1e9)) and ep + 1 < E:
            print(f"time limit reached after epoch {ep + 1}", flush=True)
            stopped_early = True
            break
    train_min = (time.time() - t_train) / 60
    # final = last epoch
    if ema is not None:
        net = ema
    pv = predict_val(net, Nv, valid_v, md=MDI)
    rows = f1_grid(pv[lab_v], gt_v, grid)
    best = best_of(rows)
    at05 = min(rows, key=lambda r: abs(r["threshold"] - 0.5))
    # cross-check with the L1 metric on full maps
    from macroplastic.metrics import binary_scores

    chk = binary_scores([pv[i] >= best["threshold"] for i in range(len(pv))], [Cv[i] for i in range(len(Cv))])
    assert chk["tp"] == best["tp"] and chk["fp"] == best["fp"], (chk, best)
    # per-class false positives
    pred_md = pv >= best["threshold"]
    per_class = {int(k): {"n": int((Cv == k).sum()), "pred_md": int((pred_md & (Cv == k)).sum())} for k in range(1, 16)}

    tag = f"{exp}_s{seed}"
    wd = WEXP / tag
    wd.mkdir(parents=True, exist_ok=True)
    torch.save({k: v.detach().cpu().float() for k, v in net.state_dict().items()}, wd / "model.pt")
    CACHE.mkdir(parents=True, exist_ok=True)
    np.save(CACHE / f"val_prob_{tag}.npy", pv.astype(np.float16))
    meta = {"model": {"arch": m.get("arch", "Unet"), "encoder": m["encoder"], "classes": int(m["classes"])},
            "task": task, "md_index": MDI, "inputs": INPUT_NAMES, "norm": norm,
            "input": "reflectance 0..1 (trained on MARIDA ACOLITE rhorc)",
            "threshold": best["threshold"], "seed": seed, "exp": exp, "config": cfg,
            "val_md": best, "val_md_at_0.5": at05, "val_curve": curve, "val_per_class_pred_md": per_class,
            "train_minutes": round(train_min, 2), "epochs_done": len(curve) and curve[-1]["epoch"],
            "stopped_early": stopped_early, "device": dev,
            "torch": torch.__version__}
    (wd / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    (CACHE / f"run_{tag}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"FINAL {tag}: val F1 {best['f1']:.4f} IoU {best['iou']:.4f} P {best['precision']:.4f} R {best['recall']:.4f} "
          f"thr {best['threshold']} | @0.5 F1 {at05['f1']:.4f} | train {train_min:.1f} min | "
          f"size {(wd / 'model.pt').stat().st_size / 1e6:.1f} MB", flush=True)
    return meta


# ------------------------------------------------------------------ stack
def lgbm_val_prob() -> np.ndarray:
    f = CACHE / "val_prob_lgbm.npy"
    if f.exists():
        return np.load(f).astype(np.float32)
    from macroplastic.models.lgbm_predict import load_predictor

    Xv, _, _ = load_split("val")
    pred = load_predictor()
    out = np.stack([pred.predict_proba(np.asarray(Xv[i]), INPUT_NAMES[:11]) for i in range(len(Xv))]).astype(np.float32)
    np.save(f, out)
    return out


def stack(exp: str, seeds: list[int], cfg: dict) -> dict:
    _, Cv, _ = load_split("val")
    lab = Cv > 0
    gt = Cv[lab] == 1
    grid = grid_of(cfg)
    pl = lgbm_val_prob()[lab]
    base = best_of(f1_grid(pl, gt, grid))
    print(f"L3 lgbm on val: F1 {base['f1']:.4f} thr {base['threshold']} (expect 0.9078 @ 0.37)")
    ws = np.round(np.arange(0.0, 1.0001, 0.1), 2)
    res = {"lgbm": base, "seeds": {}}
    for s in seeds:
        pu = np.load(CACHE / f"val_prob_{exp}_s{s}.npy").astype(np.float32)[lab]
        r = {"unet": best_of(f1_grid(pu, gt, grid)), "mean_w": {}}
        for w in ws:
            b = best_of(f1_grid(w * pu + (1 - w) * pl, gt, grid))
            r["mean_w"][str(w)] = b
        r["max"] = best_of(f1_grid(np.maximum(pu, pl), gt, grid))
        r["geo"] = best_of(f1_grid(np.sqrt(pu * pl), gt, grid))
        bw = max(r["mean_w"], key=lambda k: r["mean_w"][k]["f1"])
        r["best_w"] = float(bw)
        res["seeds"][s] = r
        print(f"seed {s}: unet {r['unet']['f1']:.4f} | mean best w={bw}: {r['mean_w'][bw]['f1']:.4f} @ {r['mean_w'][bw]['threshold']}"
              f" | w=0.5 {r['mean_w']['0.5']['f1']:.4f} | max {r['max']['f1']:.4f} | geo {r['geo']['f1']:.4f}")
    # a single weight for all seeds: maximise the mean F1 over seeds (weight picked on val)
    meanf = {str(w): float(np.mean([res["seeds"][s]["mean_w"][str(w)]["f1"] for s in seeds])) for w in ws}
    wbest = max(meanf, key=meanf.get)
    res["mean_f1_by_w"] = meanf
    res["common_w"] = float(wbest)
    for key in ("max", "geo"):
        res[f"{key}_f1_mean"] = float(np.mean([res["seeds"][s][key]["f1"] for s in seeds]))
    f1s = [res["seeds"][s]["mean_w"][wbest]["f1"] for s in seeds]
    res["stack_common_w"] = {"w_unet": float(wbest), "f1_mean": float(np.mean(f1s)),
                             "f1_std": float(np.std(f1s, ddof=1)) if len(f1s) > 1 else 0.0, "f1_per_seed": f1s,
                             "iou_mean": float(np.mean([res["seeds"][s]["mean_w"][wbest]["iou"] for s in seeds])),
                             "thresholds": [res["seeds"][s]["mean_w"][wbest]["threshold"] for s in seeds]}
    print("mean F1 by w:", {k: round(v, 4) for k, v in meanf.items()})
    print("stack common w:", res["stack_common_w"])
    (CACHE / f"stack_{exp}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res


def evalpred(exp: str, seeds: list[int], cfg: dict, device: str = "cpu") -> dict:
    """Re-evaluate saved weights through the production predictor (fp32 inputs, tiling) on val."""
    from macroplastic.models.unet import load_predictor

    Xv, Cv, _ = load_split("val")
    lab = Cv > 0
    gt = Cv[lab] == 1
    grid = grid_of(cfg)
    res = {}
    for s in seeds:
        tag = f"{exp}_s{s}"
        pred = load_predictor(WEXP / tag, device=device)
        t0 = time.time()
        pv = np.stack([pred.predict_proba(np.asarray(Xv[i]), INPUT_NAMES[:11]) for i in range(len(Xv))])
        sec = (time.time() - t0) / len(Xv)
        rows = f1_grid(pv[lab], gt, grid)
        b = best_of(rows)
        at_saved = min(rows, key=lambda r: abs(r["threshold"] - pred.threshold))
        np.save(CACHE / f"val_prob32_{tag}.npy", pv.astype(np.float16))
        res[s] = {"best": b, "at_saved_threshold": at_saved, "sec_per_patch": round(sec, 3)}
        print(f"{tag} via predictor ({device}): best F1 {b['f1']:.4f} @ {b['threshold']} | at saved thr {pred.threshold}: "
              f"F1 {at_saved['f1']:.4f} | {sec:.3f} s/patch", flush=True)
    (CACHE / f"evalpred_{exp}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["cache", "train", "stack", "evalpred"])
    ap.add_argument("--device", default="cpu", help="evalpred only")
    ap.add_argument("--config", default=str(ROOT / "configs" / "unet.yaml"))
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--exp", default=None)
    ap.add_argument("--seeds", type=int, nargs="*", default=[0, 1, 2])
    ap.add_argument("--set", action="append", default=[], help="override, e.g. train.epochs=40")
    a = ap.parse_args()
    cfg = copy.deepcopy(load_yaml(a.config))
    for kv in a.set:
        k, v = kv.split("=", 1)
        set_key(cfg, k, v)
    if a.seed is not None:
        cfg["seed"] = a.seed
    if a.exp:
        cfg["exp"] = a.exp
    if a.mode == "cache":
        for s in ("train", "val"):
            build_cache(s)
    elif a.mode == "train":
        train(cfg)
    elif a.mode == "evalpred":
        evalpred(cfg["exp"], a.seeds, cfg, a.device)
    else:
        stack(cfg["exp"], a.seeds, cfg)


if __name__ == "__main__":
    main()
