"""M1 — «состав по материалу» for the aerial photo counter (Winans 2023), INBOX §30 п.4, L123.

Rule fixed in docs/LOG.md 26.09 06:30 BEFORE training (see composition.py for the generic gate):
  model: Faster R-CNN R50-FPN, the 8 source classes of Winans (+ background), init = L109 aerial counter
         (weights_exp/photo_count/frcnn_winans_best.pth); split = L109 spatial split (train 951 / val 239 / test 397);
         epoch by val mAP50 (mean over the 8 classes); test scored once.
  pipeline: boxes are counted by the L109 aerial counter (its threshold); material of a counted box = source class of
         the best-scoring M1 box with IoU >= 0.5 (score >= 0.05) -> material via data/labels_map.csv; none -> unknown.
  a material is shown only if explicitly labelled (Winans: organic = processed wood, metal), >= 30 test items,
         precision >= 0.7 and recall >= 0.7 among matched items, and its per-chip count MAE beats the baseline
         «counted boxes × train share of the material» (paired chip bootstrap, 1 000, CI95 of the difference < 0).

  GPU (queue):  python -m macroplastic.labels.material_winans train --epochs 12 --max-minutes 25
  CPU:          python -m macroplastic.labels.material_winans score
Outputs: weights_exp/labels/frcnn_winans_material_{best.pth,history.json}, out/labels/preds_m1_{val,test}.npz,
         reports/labels/material_winans.json, weights_exp/labels/model_card_material_aerial.json (material_eval block).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
WDIR = ROOT / "weights_exp" / "labels"
CACHE = ROOT / "out" / "labels"
REP = ROOT / "reports" / "labels"
COUNTER_W = ROOT / "weights_exp" / "photo_count" / "frcnn_winans_best.pth"
COUNTER_CARD = ROOT / "weights_exp" / "photo_count" / "model_card_aerial.json"
CLASSES = ["buoy", "line fragment", "metal", "net cloth", "processed wood", "tire", "unidentified object", "vessel"]
MATS = ["plastic", "organic", "metal", "other", "unknown"]
IOU = 0.5
MIN_SCORE = 0.05


def items(which):
    from macroplastic.photo_count import winans as W
    ch = W.load_all()
    sp, _ = W.spatial_split(ch)
    return [(ch[n]["path"], ch[n]["boxes"], [CLASSES.index(x) + 1 for x in ch[n]["labels"]])
            for n in sorted(ch) if sp[n] == which]


def material_of_class():
    from .schema import load_map
    m = {r["source_label"]: r["material"] for r in load_map() if r["dataset"] == "Winans2023"}
    return {i + 1: m[c] for i, c in enumerate(CLASSES)}


class DS:
    def __init__(self, its, train):
        self.its, self.train = its, train

    def __len__(self):
        return len(self.its)

    def __getitem__(self, i):
        import torch
        from PIL import Image
        from torchvision.transforms.functional import to_tensor
        p, b, lab = self.its[i]
        im = Image.open(p).convert("RGB")
        w, h = im.size
        b = np.asarray(b, float).reshape(-1, 4).copy()
        lab = np.asarray(lab, np.int64)
        b[:, [0, 2]] = b[:, [0, 2]].clip(0, w)
        b[:, [1, 3]] = b[:, [1, 3]].clip(0, h)
        k = (b[:, 2] - b[:, 0] > 1) & (b[:, 3] - b[:, 1] > 1)
        b, lab = b[k], lab[k]
        t = to_tensor(im)
        if self.train and np.random.rand() < 0.5:
            t = t.flip(-1)
            b[:, [0, 2]] = w - b[:, [2, 0]]
        if self.train and np.random.rand() < 0.5:
            t = t.flip(-2)
            b[:, [1, 3]] = h - b[:, [3, 1]]
        return t, {"boxes": torch.as_tensor(b, dtype=torch.float32).reshape(-1, 4),
                   "labels": torch.as_tensor(lab, dtype=torch.int64)}


def collate(batch):
    return tuple(zip(*batch))


def predict(model, its, dev, batch=4):
    """-> list of (boxes, scores, labels) for all classes, score >= 0.05."""
    import torch
    from PIL import Image
    from torchvision.transforms.functional import to_tensor
    out = []
    model.eval()
    with torch.inference_mode():
        for k in range(0, len(its), batch):
            ts = [to_tensor(Image.open(p).convert("RGB")).to(dev) for p, _, _ in its[k:k + batch]]
            for r in model(ts):
                out.append((r["boxes"].float().cpu().numpy(), r["scores"].float().cpu().numpy(),
                            r["labels"].cpu().numpy()))
    return out


def class_map50(preds, its):
    from macroplastic.photo_count import metrics as M
    aps = []
    for c in range(1, len(CLASSES) + 1):
        gts = [np.asarray(b, float).reshape(-1, 4)[np.asarray(lab) == c] for _, b, lab in its]
        if sum(len(g) for g in gts) == 0:
            continue
        pr = [(p[0][p[2] == c], p[1][p[2] == c]) for p in preds]
        aps.append(M.ap_at_iou(pr, gts))
    return float(np.mean(aps))


def train(a):
    import torch
    from torch.utils.data import DataLoader
    from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
    from macroplastic.photo_count.model import build_model
    torch.manual_seed(a.seed)
    np.random.seed(a.seed)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    tr, va, te = items("train"), items("val"), items("test")
    print(f"M1: train {len(tr)} val {len(va)} test {len(te)} on {dev}", flush=True)
    model = build_model(num_classes=2)
    model.load_state_dict(torch.load(COUNTER_W, map_location="cpu", weights_only=True))
    model.roi_heads.box_predictor = FastRCNNPredictor(model.roi_heads.box_predictor.cls_score.in_features,
                                                      len(CLASSES) + 1)
    model.to(dev).train()
    dl = DataLoader(DS(tr, True), batch_size=a.batch, shuffle=True, num_workers=a.workers, collate_fn=collate,
                    persistent_workers=a.workers > 0, drop_last=True)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.SGD(params, lr=a.lr, momentum=0.9, weight_decay=1e-4)
    total, warm = a.epochs * len(dl), min(200, len(dl))
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda it: (it + 1) / warm if it < warm else 0.5 * (1 + math.cos(math.pi * (it - warm) / max(total - warm, 1))))
    scaler = torch.amp.GradScaler("cuda", enabled=dev == "cuda")
    WDIR.mkdir(parents=True, exist_ok=True)
    best, hist, t0 = None, [], time.time()
    for ep in range(a.epochs):
        run, k = 0.0, 0
        model.train()
        for k, (ims, tg) in enumerate(dl):
            ims = [x.to(dev, non_blocking=True) for x in ims]
            tg = [{kk: v.to(dev) for kk, v in t.items()} for t in tg]
            with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=dev == "cuda"):
                loss = sum(model(ims, tg).values())
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            sched.step()
            run += loss.item()
            if (time.time() - t0) / 60 > a.max_minutes:
                break
        m = class_map50(predict(model, va, dev), va)
        row = {"epoch": ep + 1, "train_loss": run / max(k + 1, 1), "val_map50_8cls": m, "minutes": (time.time() - t0) / 60}
        hist.append(row)
        print(json.dumps(row), flush=True)
        if best is None or m > best["val_map50_8cls"]:
            best = row
            torch.save(model.state_dict(), WDIR / "frcnn_winans_material_best.pth")
        if (time.time() - t0) / 60 > a.max_minutes:
            print("time budget reached", flush=True)
            break
    (WDIR / "frcnn_winans_material_history.json").write_text(
        json.dumps({"args": vars(a), "history": hist, "best": best}, indent=1), encoding="utf-8")
    print(f"best epoch {best['epoch']} val mAP50(8) {best['val_map50_8cls']:.3f}", flush=True)
    infer(dev)


def infer(dev):
    import torch
    from macroplastic.photo_count.model import build_model, load_model
    m1 = build_model(num_classes=len(CLASSES) + 1)
    m1.load_state_dict(torch.load(WDIR / "frcnn_winans_material_best.pth", map_location="cpu", weights_only=True))
    m1.to(dev).eval()
    cnt = load_model(COUNTER_W, dev)
    CACHE.mkdir(parents=True, exist_ok=True)
    for sp in ("val", "test"):
        its = items(sp)
        pm = predict(m1, its, dev)
        pc = predict(cnt, its, dev)
        np.savez_compressed(CACHE / f"preds_m1_{sp}.npz", files=np.array([Path(p).name for p, _, _ in its]),
                            m_boxes=np.array([x[0] for x in pm], dtype=object),
                            m_scores=np.array([x[1] for x in pm], dtype=object),
                            m_labels=np.array([x[2] for x in pm], dtype=object),
                            c_boxes=np.array([x[0][x[2] == 1] for x in pc], dtype=object),
                            c_scores=np.array([x[1][x[2] == 1] for x in pc], dtype=object), allow_pickle=True)
        print(f"infer {sp}: {len(its)} chips", flush=True)


# ------------------------------------------------------------------ scoring (CPU)
def assign_materials(c_boxes, m_boxes, m_scores, m_labels, mat_of):
    """Material of each counted box: class of the best-scoring M1 box with IoU >= 0.5 (score >= 0.05), else unknown."""
    from macroplastic.photo_count.metrics import iou_matrix
    keep = m_scores >= MIN_SCORE
    mb, ms, ml = m_boxes[keep], m_scores[keep], m_labels[keep]
    if len(c_boxes) == 0:
        return [], []
    if len(mb) == 0:
        return ["unknown"] * len(c_boxes), [None] * len(c_boxes)
    iou = iou_matrix(c_boxes, mb)
    mats, srcs = [], []
    for i in range(len(c_boxes)):
        j = np.where(iou[i] >= IOU)[0]
        if len(j) == 0:
            mats.append("unknown")
            srcs.append(None)
            continue
        jb = j[np.argmax(ms[j])]
        mats.append(mat_of[int(ml[jb])])
        srcs.append(int(ml[jb]))
    return mats, srcs


def match_counted(c_boxes, c_scores, gt_boxes):
    """Greedy (score order) IoU >= 0.5 matching counted -> GT. Returns list of (counted_idx, gt_idx)."""
    from macroplastic.photo_count.metrics import iou_matrix
    if len(c_boxes) == 0 or len(gt_boxes) == 0:
        return []
    iou = iou_matrix(c_boxes, gt_boxes)
    used, pairs = set(), []
    for i in np.argsort(-c_scores, kind="stable"):
        cand = [(iou[i, g], g) for g in range(len(gt_boxes)) if g not in used and iou[i, g] >= IOU]
        if cand:
            g = max(cand)[1]
            used.add(g)
            pairs.append((int(i), int(g)))
    return pairs


def score_split(sp, thr, share, mat_of):
    z = np.load(CACHE / f"preds_m1_{sp}.npz", allow_pickle=True)
    its = items(sp)
    assert [Path(p).name for p, _, _ in its] == list(z["files"])
    conf = np.zeros((len(MATS), len(MATS)), int)
    src_conf = np.zeros((len(CLASSES), len(CLASSES) + 1), int)  # last column = no M1 box
    pred_cnt = {m: [] for m in MATS}
    true_cnt = {m: [] for m in MATS}
    base_cnt = {m: [] for m in MATS}
    for k, (_, gb, gl) in enumerate(its):
        cb, cs = z["c_boxes"][k].reshape(-1, 4), z["c_scores"][k]
        keep = cs >= thr
        cb, cs = cb[keep], cs[keep]
        mats, srcs = assign_materials(cb, z["m_boxes"][k].reshape(-1, 4), z["m_scores"][k], z["m_labels"][k], mat_of)
        gb = np.asarray(gb, float).reshape(-1, 4)
        for i, g in match_counted(cb, cs, gb):
            conf[MATS.index(mat_of[gl[g]]), MATS.index(mats[i])] += 1
            src_conf[gl[g] - 1, (srcs[i] - 1) if srcs[i] else len(CLASSES)] += 1
        for m in MATS:
            pred_cnt[m].append(sum(1 for x in mats if x == m))
            true_cnt[m].append(sum(1 for c in gl if mat_of[c] == m))
            base_cnt[m].append(len(cb) * share[m])
    return conf, src_conf, {m: np.array(v) for m, v in pred_cnt.items()}, \
        {m: np.array(v) for m, v in true_cnt.items()}, {m: np.array(v) for m, v in base_cnt.items()}


def paired_boot(a_err, b_err, b=1000, seed=0):
    rng = np.random.default_rng(seed)
    n = len(a_err)
    d = [np.mean(a_err[i] - b_err[i]) for i in (rng.integers(0, n, n) for _ in range(b))]
    return float(np.mean(a_err - b_err)), [float(np.quantile(d, 0.025)), float(np.quantile(d, 0.975))]


def score(a):
    from .composition import MIN_PRECISION, MIN_RECALL, MIN_TEST_ITEMS, accepted_classes, class_metrics
    card = json.loads(COUNTER_CARD.read_text(encoding="utf-8"))
    thr = float(card["threshold"])
    mat_of = material_of_class()
    tr = items("train")
    tot = {m: 0 for m in MATS}
    for _, _, gl in tr:
        for c in gl:
            tot[mat_of[c]] += 1
    n_tr = sum(tot.values())
    share = {m: tot[m] / n_tr for m in MATS}
    out = {"rule": "docs/LOG.md 26.09 06:30 (записано до обучения)", "counter_threshold": thr,
           "train_share": share, "splits": {}}
    hist = json.loads((WDIR / "frcnn_winans_material_history.json").read_text(encoding="utf-8"))
    out["training"] = hist["best"]
    for sp in ("val", "test"):
        conf, src_conf, pc, tc, bc = score_split(sp, thr, share, mat_of)
        met = class_metrics(MATS, conf.tolist())
        per = {}
        for m in MATS:
            e_mod, e_base = np.abs(pc[m] - tc[m]).astype(float), np.abs(bc[m] - tc[m])
            d, ci = paired_boot(e_mod, e_base)
            per[m] = {**{k: (round(v, 4) if isinstance(v, float) else v) for k, v in met[m].items()},
                      "mae_model": float(e_mod.mean()), "mae_baseline_share": float(e_base.mean()),
                      "mae_diff": d, "mae_diff_ci95": ci, "true_total": int(tc[m].sum()), "pred_total": int(pc[m].sum())}
        out["splits"][sp] = {"n_chips": len(tc["unknown"]), "confusion_material": conf.tolist(),
                             "confusion_source": src_conf.tolist(), "per_material": per}
    # decision on TEST (once)
    te = out["splits"]["test"]
    me = {"classes": MATS, "confusion": te["confusion_material"], "source_dataset": "Winans2023",
          "heldout": "отложенные участки берега Winans 2023 (397 чипов, пространственное разбиение L109)",
          "heldout_independent": True, "other_source": False}
    ok_gate, why = accepted_classes(me)
    final, reasons = [], dict(why)
    for m in ok_gate:
        if te["per_material"][m]["mae_diff_ci95"][1] < 0:
            final.append(m)
        else:
            reasons[m] = (f"MAE числа класса на чип {te['per_material'][m]['mae_model']:.3f} не лучше бейзлайна "
                          f"«доля train» {te['per_material'][m]['mae_baseline_share']:.3f} "
                          f"(ДИ95 разности {te['per_material'][m]['mae_diff_ci95']})")
    out["decision"] = {"accepted": final, "rejected": reasons,
                       "thresholds": {"min_items": MIN_TEST_ITEMS, "min_precision": MIN_PRECISION,
                                      "min_recall": MIN_RECALL}}
    REP.mkdir(parents=True, exist_ok=True)
    (REP / "material_winans.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    me["accepted"] = final
    me["rule_extra"] = "MAE числа класса на чип < бейзлайна «доля train» (парный бутстреп, ДИ95 < 0)"
    if final:
        me["classes_reportable"] = final
    card_out = {"version": "winans-material-m1-l123-2026-09-26", "weights_file": "frcnn_winans_material_best.pth",
                "classes_source": CLASSES, "material_of_class": {CLASSES[k - 1]: v for k, v in mat_of.items()},
                "counter_card": "weights_exp/photo_count/model_card_aerial.json", "material_eval": me}
    (WDIR / "model_card_material_aerial.json").write_text(json.dumps(card_out, ensure_ascii=False, indent=1),
                                                          encoding="utf-8")
    print(json.dumps(out["decision"], ensure_ascii=False, indent=1))
    for sp in ("val", "test"):
        print(sp, {m: {k: out["splits"][sp]["per_material"][m][k] for k in ("n_true", "precision", "recall",
                                                                            "mae_model", "mae_baseline_share")}
                   for m in MATS})


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--epochs", type=int, default=12)
    t.add_argument("--batch", type=int, default=4)
    t.add_argument("--lr", type=float, default=0.005)
    t.add_argument("--max-minutes", type=float, default=25)
    t.add_argument("--workers", type=int, default=2)
    t.add_argument("--seed", type=int, default=0)
    sub.add_parser("infer")
    sub.add_parser("score")
    a = ap.parse_args(argv)
    if a.cmd == "train":
        train(a)
    elif a.cmd == "infer":
        import torch
        infer("cuda" if torch.cuda.is_available() else "cpu")
    else:
        score(a)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))
    main()
