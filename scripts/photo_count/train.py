"""Train Faster R-CNN (COCO init) on FML with a split grouped by acquisition set (no near-duplicate frames
across train/val/test). Run on GPU only via scripts/gpu_queue.py.

Why: the official FML split is random over video frames — ~90 % of val/test frames have a train frame
<= 2 s apart (reports/photo_count/leakage.json), so the official test is optimistic. The grouped split holds out
whole sets (camera configuration / session) and set17 (the only set shot on other days: 2024-10-04, 2024-11-18).

Split rule (fixed before any training): test_g = set17 + sets drawn with seed 0 until >= 20 % of images;
val_g = next drawn sets until >= 15 %; the rest is train_g. Epoch chosen by val_g AP50; threshold by val_g count MAE.

  python scripts/photo_count/train.py --split grouped --epochs 6 --max-minutes 55
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from macroplastic.photo_count import data as D  # noqa: E402
from macroplastic.photo_count import metrics as M  # noqa: E402

SETS = ROOT / "data" / "extra" / "fml" / "single_sets_index.json"
SPLIT_G = ROOT / "data" / "extra" / "fml" / "split_grouped.json"
PRETRAINED = ROOT / "data" / "extra" / "fml" / "pretrained" / "fasterrcnn_resnet50_fpn_coco-258fb6c6.pth"
OUTW = ROOT / "weights_exp" / "photo_count"


def official_location():
    loc = {}
    for s in ("train", "val", "test"):
        for f in D.split_files(s):
            loc[f.stem] = (f, D.label_path(f, s))
    return loc


def grouped_split(seed=0, test_frac=0.20, val_frac=0.15):
    sets = json.loads(SETS.read_text())
    n = sum(len(v) for v in sets.values())
    names = sorted(sets, key=lambda k: int(k[3:]))
    rng = np.random.default_rng(seed)
    order = [names[i] for i in rng.permutation(len(names)) if names[i] != "set17"]
    test, val = ["set17"], []
    k = 0
    while sum(len(sets[s]) for s in test) < test_frac * n:
        test.append(order[k]); k += 1
    while sum(len(sets[s]) for s in val) < val_frac * n:
        val.append(order[k]); k += 1
    train = order[k:]
    split = {"rule": "test = set17 + seeded sets to >=20%; val = next seeded sets to >=15%; seed 0",
             "sets": {"train": sorted(train, key=lambda s: int(s[3:])), "val": sorted(val, key=lambda s: int(s[3:])),
                      "test": sorted(test, key=lambda s: int(s[3:]))},
             "images": {sp: sorted(x for s in ss for x in sets[s]) for sp, ss in
                        (("train", train), ("val", val), ("test", test))}}
    SPLIT_G.write_text(json.dumps(split), encoding="utf-8")
    return split


def items_for(split_name, which):
    loc = official_location()
    if split_name == "official":
        return [loc[f.stem] for f in D.split_files(which)]
    sp = json.loads(SPLIT_G.read_text()) if SPLIT_G.exists() else grouped_split()
    return [loc[s] for s in sp["images"][which]]


class FMLDataset:
    def __init__(self, items, train):
        self.items, self.train = items, train

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        import torch
        from PIL import Image
        from torchvision.transforms.functional import to_tensor
        img_p, lab_p = self.items[i]
        im = Image.open(img_p).convert("RGB")
        w, h = im.size
        b = D.yolo_boxes(lab_p, w, h)
        b[:, [0, 2]] = b[:, [0, 2]].clip(0, w)
        b[:, [1, 3]] = b[:, [1, 3]].clip(0, h)
        keep = (b[:, 2] - b[:, 0] > 1) & (b[:, 3] - b[:, 1] > 1)
        b = b[keep]
        t = to_tensor(im)
        if self.train and np.random.rand() < 0.5:
            t = t.flip(-1)
            b = b.copy()
            b[:, [0, 2]] = w - b[:, [2, 0]]
        tgt = {"boxes": torch.as_tensor(b, dtype=torch.float32).reshape(-1, 4),
               "labels": torch.ones(len(b), dtype=torch.int64)}
        return t, tgt


def collate(batch):
    return tuple(zip(*batch))


def evaluate(model, loader, dev, gts):
    import torch
    model.eval()
    preds = []
    with torch.inference_mode():
        for ims, _ in loader:
            for p in model([x.to(dev) for x in ims]):
                k = p["labels"] == 1
                preds.append((p["boxes"][k].float().cpu().numpy(), p["scores"][k].float().cpu().numpy()))
    ap = M.ap_at_iou(preds, gts)
    tc = np.array([len(g) for g in gts])
    best = min(((M.count_metrics(M.counts_at(preds, t), tc)["mae"], float(t)) for t in np.arange(0.05, 0.96, 0.05)))
    model.train()
    return ap, best, preds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="grouped", choices=["grouped", "official"])
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--lr", type=float, default=0.01)
    ap.add_argument("--max-minutes", type=float, default=55)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    import torch
    from torch.utils.data import DataLoader
    from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
    from macroplastic.photo_count.model import build_model
    torch.manual_seed(a.seed)
    np.random.seed(a.seed)
    tag = a.tag or f"frcnn_{a.split}"
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    if a.split == "grouped" and not SPLIT_G.exists():
        grouped_split()
    tr, va = items_for(a.split, "train"), items_for(a.split, "val")
    print(f"{tag}: train {len(tr)} val {len(va)} on {dev}", flush=True)
    model = build_model(num_classes=91)
    model.load_state_dict(torch.load(PRETRAINED, map_location="cpu", weights_only=True))
    model.roi_heads.box_predictor = FastRCNNPredictor(model.roi_heads.box_predictor.cls_score.in_features, 2)
    model.to(dev).train()
    dl = DataLoader(FMLDataset(tr, True), batch_size=a.batch, shuffle=True, num_workers=a.workers,
                    collate_fn=collate, persistent_workers=a.workers > 0, drop_last=True)
    dv = DataLoader(FMLDataset(va, False), batch_size=4, shuffle=False, num_workers=0, collate_fn=collate)  # extra spawned workers hit WinError 1455 (page file)
    gts_v = [D.yolo_boxes(l) for _, l in va]
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.SGD(params, lr=a.lr, momentum=0.9, weight_decay=1e-4)
    total = a.epochs * len(dl)
    warm = min(300, len(dl))
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda it: (it + 1) / warm if it < warm else 0.5 * (1 + math.cos(math.pi * (it - warm) / max(total - warm, 1))))
    scaler = torch.amp.GradScaler("cuda", enabled=dev == "cuda")
    OUTW.mkdir(parents=True, exist_ok=True)
    hist, best = [], None
    t0 = time.time()
    it = 0
    for ep in range(a.epochs):
        run = 0.0
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
            it += 1
            run += loss.item()
            if k % 100 == 0:
                print(f"  ep{ep} it{k}/{len(dl)} loss {loss.item():.3f} {(time.time() - t0) / 60:.1f} min", flush=True)
            if (time.time() - t0) / 60 > a.max_minutes:
                break
        apv, (mae, thr), _ = evaluate(model, dv, dev, gts_v)
        row = {"epoch": ep + 1, "train_loss": run / max(k + 1, 1), "val_ap50": apv, "val_mae": mae, "val_thr": thr,
               "minutes": (time.time() - t0) / 60}
        hist.append(row)
        print(json.dumps(row), flush=True)
        if best is None or apv > best["val_ap50"]:
            best = row
            torch.save(model.state_dict(), OUTW / f"{tag}_best.pth")
        if (time.time() - t0) / 60 > a.max_minutes:
            print("time budget reached", flush=True)
            break
    (OUTW / f"{tag}_history.json").write_text(json.dumps({"tag": tag, "split": a.split, "args": vars(a),
                                                          "history": hist, "best": best}, indent=1), encoding="utf-8")
    print(f"best epoch {best['epoch']} val AP50 {best['val_ap50']:.3f}", flush=True)


if __name__ == "__main__":
    main()
