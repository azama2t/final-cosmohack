r"""Official MARIDA U-Net (Kikaki et al. 2022) as a baseline, on the SAME check as our LightGBM (scripts/case/detector_compare.py).

Weights: the authors' pretrained model (README of github.com/marine-debris/marine-debris.github.io, "pretrained Unet model on
MARIDA" -> https://pithos.okeanos.grnet.gr/public/lxh8hL4zvuSKds2BdVnMd2, MARIDA_models.zip, 90 776 944 B,
member MARIDA_models/unet/trained_models/44/model.pth) -> weights_exp/unet_marida/model_epoch44.pth. Nothing is trained here.
Model/preprocessing = official semantic_segmentation/unet/{unet.py, dataloader.py, evaluation.py}: UNet(11 bands -> 11 classes,
hidden 16), classes 12-15 merged into Marine Water, NaN -> band mean, (x - mean) / std with the official band stats,
softmax; the official prediction is argmax (class 0 = Marine Debris).

Settings (none chosen on test):
  unet_argmax  MD = argmax of 11 classes (official protocol, nothing tuned)
  unet_prob    MD = softmax P(MD) >= t, t chosen on MARIDA val (grid 0.02..0.98, F1 MD), as rf_prob in detector_compare
Metric = detector_compare.evaluate (same code): MD vs other labelled pixels (cl > 0) pooled over patches, NaN pixels -> not MD,
unlabelled separate; 95 % CI = scene bootstrap 2000 reps default_rng(0). For comparison the stored val masks of lgbm / rf
(data/case/detector_preds/val_preds.npz, produced by detector_compare.py) are scored by the same function, plus a paired
scene bootstrap of dF1 (lgbm - unet).

  # 1) val (CPU, ~1 min) -> reports/detector_v2/unet_baseline.{json,md}
  $env:CUDA_VISIBLE_DEVICES=""; .venv\Scripts\python.exe scripts\case\unet_baseline.py val
  # 2) MARIDA test: ONLY the orchestrator, ONCE, for the final baseline table (thresholds read from the val json)
  $env:CUDA_VISIBLE_DEVICES=""; .venv\Scripts\python.exe scripts\case\unet_baseline.py test --once
  # 3) 22 S2 crops of the case pairs, no harmonization, same quality masks / object rules as pair_quality.py (network: S2 COGs)
  $env:CUDA_VISIBLE_DEVICES=""; .venv\Scripts\python.exe scripts\case\unet_baseline.py pairs
Outputs: reports/detector_v2/unet_baseline.{json,md}, reports/detector_v2/unet_test.json (step 2), out/l99_unet_pairs/ (step 3).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["L16_THREADS"] = "8"
os.environ.setdefault("OMP_NUM_THREADS", "8")
import numpy as np  # noqa: E402
import torch  # noqa: E402
from torch import nn  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "case"))
sys.path.insert(0, str(ROOT / "scripts" / "experiments"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
import detector_compare as DC  # noqa: E402
import l16_common as C  # noqa: E402

torch.set_num_threads(8)
WDIR = ROOT / "weights_exp" / "unet_marida"
WFILE = WDIR / "model_epoch44.pth"
REP = ROOT / "reports" / "detector_v2"
OUT_VAL = REP / "unet_baseline.json"
OUT_TEST = REP / "unet_test.json"
SRC_URL = "https://pithos.okeanos.grnet.gr/public/lxh8hL4zvuSKds2BdVnMd2"
MODELS = ["unet_argmax", "unet_prob"]
REF = ["lgbm", "rf_argmax", "rf_prob"]

# official dataloader.py constants
BANDS_MEAN = np.array([0.05197577, 0.04783991, 0.04056812, 0.03163572, 0.02972606, 0.03457443,
                       0.03875053, 0.03436435, 0.0392113, 0.02358126, 0.01588816], np.float32)
BANDS_STD = np.array([0.04725893, 0.04743808, 0.04699043, 0.04967381, 0.04946782, 0.06458357,
                      0.07594915, 0.07120246, 0.08251058, 0.05111466, 0.03524419], np.float32)
MARIDA_BANDS = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]


# ------------------------------------------------------------ official U-Net (semantic_segmentation/unet/unet.py, verbatim layers)
def _dc(i, o):
    return [nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True),
            nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True)]


class Down(nn.Module):
    def __init__(self, i, o):
        super().__init__()
        self.maxpool_conv = nn.Sequential(nn.MaxPool2d(2), *_dc(i, o))

    def forward(self, x):
        return self.maxpool_conv(x)


class Up(nn.Module):
    def __init__(self, i, o):
        super().__init__()
        self.up = nn.Upsample(scale_factor=2, mode="bilinear", align_corners=True)
        self.conv = nn.Sequential(*_dc(i, o))

    def forward(self, x1, x2):
        return self.conv(torch.cat([x2, self.up(x1)], dim=1))


class UNet(nn.Module):
    def __init__(self, input_bands=11, output_classes=11, hidden_channels=16):
        super().__init__()
        h = hidden_channels
        self.inc = nn.Sequential(*_dc(input_bands, h))
        self.down1, self.down2, self.down3, self.down4 = Down(h, 2 * h), Down(2 * h, 4 * h), Down(4 * h, 8 * h), Down(8 * h, 8 * h)
        self.up1, self.up2, self.up3, self.up4 = Up(16 * h, 4 * h), Up(8 * h, 2 * h), Up(4 * h, h), Up(2 * h, h)
        self.outc = nn.Conv2d(h, output_classes, 1)

    def forward(self, x):
        x1 = self.inc(x); x2 = self.down1(x1); x3 = self.down2(x2); x4 = self.down3(x3); x5 = self.down4(x4)
        return self.outc(self.up4(self.up3(self.up2(self.up1(x5, x4), x3), x2), x1))


def load_model() -> UNet:
    m = UNet()
    m.load_state_dict(torch.load(WFILE, map_location="cpu"))
    return m.eval()


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@torch.no_grad()
def infer(model: UNet, img: np.ndarray, tile: int = 512, pad: int = 64):
    """img (11,H,W) reflectance (NaN allowed) -> (P(MD) float32 (H,W), argmax==MD bool (H,W)). Official preprocessing.
    Patches <= tile are run whole (MARIDA 256x256 = exactly evaluation.py); larger crops are tiled with overlap."""
    x = img.astype(np.float32).copy()
    nan = np.isnan(x)
    x[nan] = np.broadcast_to(BANDS_MEAN[:, None, None], x.shape)[nan]
    x = (x - BANDS_MEAN[:, None, None]) / BANDS_STD[:, None, None]
    _, H, W = x.shape

    def run(a):
        h, w = a.shape[1:]
        ph, pw = (-h) % 16, (-w) % 16
        if ph or pw:
            a = np.pad(a, ((0, 0), (0, ph), (0, pw)), mode="reflect")
        pr = torch.softmax(model(torch.from_numpy(np.ascontiguousarray(a))[None]), 1)[0, :, :h, :w].numpy()
        return pr[0], pr.argmax(0) == 0

    if H <= tile and W <= tile:
        return run(x)
    pmd = np.zeros((H, W), np.float32)
    arg = np.zeros((H, W), bool)
    for y0 in range(0, H, tile):
        for x0 in range(0, W, tile):
            ya, xa = max(0, y0 - pad), max(0, x0 - pad)
            yb, xb = min(H, y0 + tile + pad), min(W, x0 + tile + pad)
            p, a = run(x[:, ya:yb, xa:xb])
            y1, x1 = min(H, y0 + tile), min(W, x0 + tile)
            pmd[y0:y1, x0:x1] = p[y0 - ya:y1 - ya, x0 - xa:x1 - xa]
            arg[y0:y1, x0:x1] = a[y0 - ya:y1 - ya, x0 - xa:x1 - xa]
    return pmd, arg


# ------------------------------------------------------------ MARIDA split
def split_names(split: str) -> list[str]:
    return [ln.strip() for ln in (C.MARIDA / "splits" / f"{split}_X.txt").read_text().splitlines() if ln.strip()]


def run_split(model, split: str, thr: float | None):
    names = split_names(split)
    res, probs = [], []
    t0 = time.time()
    for name in names:
        img, cl, _conf, _, _ = C.read_patch(name)
        bad = np.isnan(img).any(0)
        pmd, arg = infer(model, img)
        pmd[bad] = 0.0
        probs.append(pmd)
        res.append({"name": name, "cl": cl, "bad": bad, "arg": arg & ~bad, "ncl": np.bincount(cl[~bad], minlength=16)[:16]})
    sec = time.time() - t0
    lab = np.concatenate([r["cl"][r["cl"] > 0] for r in res])
    if thr is None:  # choose on val only
        pl = np.concatenate([p[r["cl"] > 0] for p, r in zip(probs, res)])
        md = lab == 1
        grid = np.round(np.arange(0.02, 0.981, 0.01), 2)
        f1s = [(float(t), 2 * np.sum(md & (pl >= t)) / max(1, np.sum(md) + np.sum(pl >= t))) for t in grid]
        thr = max(f1s, key=lambda z: z[1])[0]
    for r, p in zip(res, probs):
        r["masks"] = {"unet_argmax": r.pop("arg"), "unet_prob": (p >= thr) & ~r["bad"]}
        r["pmd"] = p
    return names, res, thr, sec


def add_counts(res):
    for r in res:
        r["counts"] = {k: np.bincount(r["cl"][m], minlength=16)[:16] for k, m in r["masks"].items()}


def add_reference_masks(res, split: str):
    """Masks of lgbm / rf from detector_compare.py (same patches, same NaN rule) -> scored by the same evaluate()."""
    d = np.load(DC.PRED_DIR / f"{split}_preds.npz")
    idx = {n: i for i, n in enumerate(d["names"])}
    for r in res:
        i = idx[r["name"]]
        for m in REF:
            r["masks"][m] = np.unpackbits(d[f"mask_{m}"][i], axis=-1)[:, :256].astype(bool)


def paired_delta(res, names, a: str, b: str, n_boot: int = 2000):
    """F1(a) - F1(b), paired bootstrap over scenes (same resampling loop/seed as evaluate)."""
    scene = np.array([n.rsplit("_", 1)[0] for n in names])
    scenes = np.unique(scene)
    per = {m: {s: np.zeros(3) for s in scenes} for m in (a, b)}
    for r, s in zip(res, scene):
        lab = r["cl"] > 0
        y = r["cl"][lab] == 1
        for m in (a, b):
            per[m][s] += np.array(C.confusion(y, r["masks"][m][lab])[:3])
    f1 = lambda v: 2 * v[0] / max(1, 2 * v[0] + v[1] + v[2])  # noqa: E731
    point = f1(sum(per[a].values())) - f1(sum(per[b].values()))
    rng = np.random.default_rng(0)
    ds = []
    for _ in range(n_boot):
        pick = rng.choice(scenes, len(scenes), replace=True)
        ds.append(f1(sum(per[a][s] for s in pick)) - f1(sum(per[b][s] for s in pick)))
    ds = np.array(ds)
    better = sum(f1(per[a][s]) > f1(per[b][s]) for s in scenes if per[a][s][[0, 2]].sum() + per[b][s][[0, 2]].sum() > 0)
    worse = sum(f1(per[a][s]) < f1(per[b][s]) for s in scenes if per[a][s][[0, 2]].sum() + per[b][s][[0, 2]].sum() > 0)
    return {"a": a, "b": b, "delta_f1": round(float(point), 4),
            "ci95": [round(float(np.percentile(ds, 2.5)), 4), round(float(np.percentile(ds, 97.5)), 4)],
            "share_delta_le_0": round(float((ds <= 0).mean()), 4), "scenes_a_better": int(better), "scenes_a_worse": int(worse)}


def evaluate(res, names, models):
    DC.MODELS = models  # detector_compare.evaluate iterates over its module-level MODELS list
    return DC.evaluate(res, names)


def slim(m: dict) -> dict:
    keep = ("precision_md", "recall_md", "f1_md", "iou_md", "tp", "fp", "fn", "md_px", "ci95_f1", "ci95_precision",
            "ci95_recall", "ci95_iou", "unlabelled_px", "unlabelled_pred_md", "unlabelled_pred_md_rate", "per_class",
            "n_patches", "n_scenes")
    return {k: m[k] for k in keep}


def provenance() -> dict:
    return {"weights": str(WFILE.relative_to(ROOT)).replace("\\", "/"), "weights_sha256": sha256(WFILE),
            "source_url": SRC_URL, "source_archive": "MARIDA_models.zip (90 776 944 B, sha256 c17d677146ea6bdc3b938bf2539c85f73bd268d3940f8f5ada0a1d976a66c6e7)",
            "archive_member": "MARIDA_models/unet/trained_models/44/model.pth",
            "linked_from": "github.com/marine-debris/marine-debris.github.io README (Semantic Segmentation / Unet: 'download the pretrained Unet model on MARIDA')",
            "code": "official semantic_segmentation/unet/unet.py layers + dataloader.py band mean/std + evaluation.py preprocessing/argmax",
            "trained_by": "authors (Kikaki et al. 2022); epoch 44 = default --model_path of the official evaluation.py; not retrained here",
            "classes": "11 outputs: MD, Dense Sarg., Sparse Sarg., NatOM, Ship, Clouds, Marine Water(+Waves, Cloud Shadows, Wakes, Mixed), SLW, Foam, Turbid, Shallow"}


# ------------------------------------------------------------ commands
def cmd_val():
    t0 = time.time()
    model = load_model()
    names, res, thr, sec = run_split(model, "val", None)
    add_reference_masks(res, "val")
    add_counts(res)
    met = evaluate(res, names, MODELS + REF)
    ft = json.loads((ROOT / "reports" / "lgbm_final_test.json").read_text(encoding="utf-8"))["val_md"]
    repro = {"f1_here": met["lgbm"]["f1_md"], "f1_lgbm_final_test_val": ft["f1_md"], "ci_here": met["lgbm"]["ci95_f1"],
             "ci_final_test": ft["ci95"], "equal": abs(met["lgbm"]["f1_md"] - ft["f1_md"]) < 1e-12 and met["lgbm"]["ci95_f1"] == ft["ci95"]}
    out = {"when": datetime.now().isoformat(timespec="seconds"), "split": "val",
           "note": "MARIDA val only; test not read. unet_prob threshold chosen on this val -> its val F1 is optimistic (as rf_prob).",
           "provenance": provenance(),
           "settings": {"unet_argmax": {"setting": "argmax of 11 classes", "source": "official protocol, nothing tuned"},
                        "unet_prob": {"setting": f"P(MD) >= {thr}", "threshold": thr, "source": "grid 0.02..0.98 on MARIDA val (F1 MD)"},
                        "lgbm": {"setting": "P(MD) >= 0.63", "source": "weights/lgbm; masks from data/case/detector_preds/val_preds.npz"},
                        "rf_argmax": {"setting": "argmax", "source": "detector_compare.py, val_preds.npz"},
                        "rf_prob": {"setting": "P(MD) >= 0.36", "source": "detector_compare.py, val_preds.npz"}},
           "metric": "detector_compare.evaluate: MD vs other labelled pixels (cl>0), pooled; NaN pixels -> not MD; unlabelled separate",
           "ci": "scene bootstrap, 2000 reps, numpy default_rng(0), same loop as scripts/final_test.py",
           "val": {m: slim(met[m]) for m in MODELS + REF},
           "paired_val": {"lgbm_vs_unet_argmax": paired_delta(res, names, "lgbm", "unet_argmax"),
                          "lgbm_vs_unet_prob": paired_delta(res, names, "lgbm", "unet_prob"),
                          "unet_argmax_vs_rf_argmax": paired_delta(res, names, "unet_argmax", "rf_argmax")},
           "reproducibility_lgbm_val": repro,
           "timing": {"inference_s_val_328_patches_cpu8": round(sec, 1), "total_s": None, "device": "CPU, torch 8 threads",
                      "torch": torch.__version__},
           "test_command": "$env:CUDA_VISIBLE_DEVICES=\"\"; .venv\\Scripts\\python.exe scripts\\case\\unet_baseline.py test --once"}
    out["timing"]["total_s"] = round(time.time() - t0, 1)
    REP.mkdir(parents=True, exist_ok=True)
    OUT_VAL.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    for m in MODELS + REF:
        d = met[m]
        print(f"  {m:12s} P {d['precision_md']:.4f} R {d['recall_md']:.4f} F1 {d['f1_md']:.4f} IoU {d['iou_md']:.4f} "
              f"CI {d['ci95_f1']} unlab {d['unlabelled_pred_md_rate']:.5f}", flush=True)
    print("paired", json.dumps(out["paired_val"]), "\nrepro lgbm val:", repro["equal"], f"\n{out['timing']}", flush=True)


def cmd_test(once: bool):
    if not once:
        sys.exit("MARIDA test is evaluated once, by the orchestrator only: add --once")
    if OUT_TEST.exists():
        sys.exit(f"{OUT_TEST} exists: the test split is evaluated once. Delete it only if the run crashed.")
    v = json.loads(OUT_VAL.read_text(encoding="utf-8"))
    thr = float(v["settings"]["unet_prob"]["threshold"])
    if sha256(WFILE) != v["provenance"]["weights_sha256"]:
        sys.exit("weights differ from the ones evaluated on val")
    t0 = time.time()
    names, res, _, sec = run_split(load_model(), "test", thr)  # thr fixed from val
    add_reference_masks(res, "test")
    add_counts(res)
    met = evaluate(res, names, MODELS + REF)
    ref = json.loads((ROOT / "reports" / "case_detector" / "metrics.json").read_text(encoding="utf-8"))["test"]
    out = {"when": datetime.now().isoformat(timespec="seconds"), "split": "test",
           "note": "MARIDA test evaluated once; unet_prob threshold from val json; nothing selected on test",
           "provenance": provenance(), "threshold_unet_prob_from_val": thr,
           "test": {m: slim(met[m]) for m in MODELS + REF},
           "paired_test": {"lgbm_vs_unet_argmax": paired_delta(res, names, "lgbm", "unet_argmax"),
                           "lgbm_vs_unet_prob": paired_delta(res, names, "lgbm", "unet_prob")},
           "reference_equal_case_detector_metrics": {m: met[m]["f1_md"] == ref[m]["f1_md"] and met[m]["ci95_f1"] == ref[m]["ci95_f1"] for m in REF},
           "inference_s": round(sec, 1), "total_s": round(time.time() - t0, 1)}
    OUT_TEST.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    for m in MODELS + REF:
        d = met[m]
        print(f"  {m:12s} F1 {d['f1_md']:.4f} CI {d['ci95_f1']} P {d['precision_md']:.4f} R {d['recall_md']:.4f}", flush=True)
    print(json.dumps(out["paired_test"]), out["reference_equal_case_detector_metrics"], flush=True)


class _Pred:
    """pair_quality.process_s2 predictor interface: predict_proba(bands, band_names, water_mask) + threshold."""
    # (bands object, result): holding the reference keeps the array alive, so identity cannot be reused by a new crop
    # (an id()-keyed dict did exactly that: a freed crop's address was reused -> stale predictions; caught by a shape error)
    cache: list = [None, None]

    def __init__(self, model, mode: str, thr: float):
        self.model, self.mode, self.last_offset = model, mode, None
        self.threshold = 0.5 if mode == "argmax" else thr

    def predict_proba(self, bands, names, water_mask=None):
        if _Pred.cache[0] is not bands:
            img = np.stack([bands[list(names).index(b)] for b in MARIDA_BANDS])
            _Pred.cache[:] = [bands, infer(self.model, img)]
        pmd, arg = _Pred.cache[1]
        assert pmd.shape == bands.shape[1:]
        return arg.astype(np.float32) if self.mode == "argmax" else pmd


def cmd_pairs():
    import pandas as pd
    import pair_quality as PQ
    from macroplastic.live import stac

    cur = json.loads((ROOT / "reports" / "case_pairs" / "detector_current.json").read_text(encoding="utf-8"))
    dirs = [c["dir"] for c in cur["crops"]]
    v = json.loads(OUT_VAL.read_text(encoding="utf-8"))
    thr = float(v["settings"]["unet_prob"]["threshold"])
    cfg = PQ.load_cfg(ROOT / "configs" / "case_pairs.yaml")
    assert cfg["detector"]["harmonize"] == "none"
    best = pd.read_csv(PQ.PAIRS / "best_per_event.csv")
    samples = pd.read_csv(ROOT / "task" / "macroplastic_marine_samples.csv", low_memory=False)
    model = load_model()
    orig = stac.read_crop
    memo = {}

    def read_crop_memo(item, epsg, bounds, **kw):  # the two detector modes reuse one download
        k = (item.id, tuple(bounds))
        if k not in memo:
            memo.clear()
            memo[k] = orig(item, epsg, bounds, **kw)
        return memo[k]

    PQ.stac.read_crop = read_crop_memo
    outroot = ROOT / "out" / "l99_unet_pairs"
    rows = []
    for row in best.itertuples():
        if PQ.safe(row.event_id) not in dirs:
            continue
        g = PQ.event_geometry(samples, row.event_id, cfg)
        rec = {"dir": PQ.safe(row.event_id), "event_id": row.event_id}
        for mode in ("argmax", "prob"):
            t0 = time.time()
            try:
                r = PQ.process_s2(row, g, cfg, _Pred(model, mode, thr), outroot / mode / PQ.safe(row.event_id))
                d = r["detector"]
                rec[mode] = {"decision": r["decision"], "reason": r["reason"], "n_obj_crop": d["n_det_crop"],
                             "n_obj_in_strip": d["n_det"], "px_crop": d["det_px_crop"], "px_in_strip": d["det_px_strip"],
                             "n_raw": d["n_det_crop_raw"], "n_dropped_cloud_shadow": d["n_dropped_cloud_shadow"],
                             "prob_max_strip": d["prob_max"], "det_frac_water_crop": d["det_frac_water_crop"],
                             "size": [r["height"] if "height" in r else None, r.get("width")], "s": round(time.time() - t0, 1)}
            except Exception as e:  # noqa: BLE001
                rec[mode] = {"error": f"{type(e).__name__}: {e}"}
            print(rec["dir"], mode, rec[mode], flush=True)
        rows.append(rec)
        (outroot / "pairs.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    PQ.stac.read_crop = orig
    tot = {}
    for mode in ("argmax", "prob"):
        ok = [r[mode] for r in rows if "error" not in r[mode]]
        acc = [x for x in ok if x["decision"] == "accept"]
        tot[mode] = {"n_crops": len(ok), "n_errors": len(rows) - len(ok), "n_obj": sum(x["n_obj_crop"] for x in ok),
                     "n_in_strip": sum(x["n_obj_in_strip"] for x in ok), "n_crops_with_obj": sum(x["n_obj_crop"] > 0 for x in ok),
                     "n_strips_with_obj": sum(x["n_obj_in_strip"] > 0 for x in ok), "n_accept": len(acc),
                     "n_obj_accept": sum(x["n_obj_crop"] for x in acc), "n_in_strip_accept": sum(x["n_obj_in_strip"] for x in acc),
                     "px_crop": sum(x["px_crop"] for x in ok)}
    out = {"when": datetime.now().isoformat(timespec="seconds"), "threshold_prob": thr, "harmonize": "none",
           "protocol": "pair_quality.process_s2 with the U-Net as predictor (same crops, quality masks, water_ok, 8-connected objects, "
                       "near-cloud/shadow drops, strip raster); argmax mode = official prediction; prob mode = P(MD) >= val threshold",
           "lgbm_current": cur["totals"], "totals": tot, "crops": rows}
    (outroot / "pairs.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    v["pairs_case"] = {k: out[k] for k in ("when", "threshold_prob", "harmonize", "protocol", "lgbm_current", "totals")}
    v["pairs_case"]["per_crop"] = "out/l99_unet_pairs/pairs.json (masks/prob per crop in out/l99_unet_pairs/{argmax,prob}/<event>/)"
    OUT_VAL.write_text(json.dumps(v, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(json.dumps(tot, indent=1), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["val", "test", "pairs"])
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    {"val": cmd_val, "pairs": cmd_pairs}.get(a.cmd, lambda: cmd_test(a.once))()


if __name__ == "__main__":
    main()
