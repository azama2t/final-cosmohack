"""Faster R-CNN (torchvision ResNet50-FPN, 2 classes: background + garbage) — the architecture of the published
FML weights (fasterrcnn_fml.pth, authors' Google Drive). Inference-only helpers used by the API and scripts."""
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
WEIGHTS_DIR = ROOT / "weights_exp" / "photo_count"


def build_model(num_classes=2, pretrained_backbone=False):
    import torchvision
    from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
    if pretrained_backbone:
        from torchvision.models.detection.faster_rcnn import FasterRCNN_ResNet50_FPN_Weights
        m = torchvision.models.detection.fasterrcnn_resnet50_fpn(weights=FasterRCNN_ResNet50_FPN_Weights.DEFAULT)
        m.roi_heads.box_predictor = FastRCNNPredictor(m.roi_heads.box_predictor.cls_score.in_features, num_classes)
    else:
        m = torchvision.models.detection.fasterrcnn_resnet50_fpn(weights=None, weights_backbone=None,
                                                                num_classes=num_classes)
    m.roi_heads.score_thresh = 0.05
    m.roi_heads.detections_per_img = 100
    return m


def load_model(weights_path, device="cpu"):
    import torch
    m = build_model()
    sd = torch.load(weights_path, map_location="cpu", weights_only=True)
    m.load_state_dict(sd)
    return m.to(device).eval()


def predict_images(model, images, device="cpu", batch=4):
    """images: list of PIL RGB. Returns list of (boxes xyxy float32 (N,4), scores float32 (N,))."""
    import torch
    from torchvision.transforms.functional import to_tensor
    out = []
    with torch.inference_mode():
        for k in range(0, len(images), batch):
            ts = [to_tensor(im).to(device) for im in images[k:k + batch]]
            for p in model(ts):
                keep = p["labels"] == 1
                out.append((p["boxes"][keep].float().cpu().numpy(), p["scores"][keep].float().cpu().numpy()))
    return out


SURVEYS = {  # survey type -> model card (weights_exp/photo_count/, outside git)
    "water_camera": "model_card.json",       # FML: camera at the water, first-person view
    "aerial": "model_card_aerial.json",      # Winans 2023: nadir aerial/drone imagery of shorelines, GSD known
}


def load_card(survey="water_camera"):
    p = WEIGHTS_DIR / SURVEYS[survey]
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


class Counter:
    """Lazy per-survey singletons used by the API: load the weights named in the model card once."""
    _inst = {}

    def __init__(self, card):
        import os
        import torch
        self.card = card
        want = os.environ.get("MACROPLASTIC_PHOTO_DEVICE") or card.get("device", "auto")  # auto | cpu | cuda
        self.device = "cuda" if (torch.cuda.is_available() and want != "cpu") else "cpu"
        self.model = load_model(WEIGHTS_DIR / card["weights_file"], self.device)
        self.threshold = float(card["threshold"])

    @classmethod
    def get(cls, survey="water_camera"):
        if survey not in SURVEYS:
            raise KeyError(survey)
        if survey not in cls._inst:
            card = load_card(survey)
            if card is None:
                raise FileNotFoundError(f"no model card for {survey} in {WEIGHTS_DIR}")
            cls._inst[survey] = cls(card)
        return cls._inst[survey]

    def count(self, image, threshold=None):
        thr = self.threshold if threshold is None else float(threshold)
        boxes, scores = predict_images(self.model, [image], self.device, batch=1)[0]
        keep = scores >= thr
        return boxes[keep], scores[keep], thr


BINS_CM = [0, 30, 60, 120, 1e9]  # size bins of the detection-probability correction (scripts/photo_count/eval_area.py)


def corrected_count(boxes, gsd_m, factors):
    """Sum over detections of precision/recall of its size bin (factors from VAL, model card)."""
    b = np.asarray(boxes, float).reshape(-1, 4)
    if not len(b) or not factors:
        return float(len(b))
    side_cm = np.maximum(b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]) * gsd_m * 100
    k = np.clip(np.digitize(side_cm, BINS_CM) - 1, 0, len(factors) - 1)
    return float(np.asarray(factors, float)[k].sum())


def density_per_km2(count, frame_area_m2):
    """Items per km^2 on the frame scale — only if the imaged water area is known (not a satellite estimate)."""
    if frame_area_m2 is None or not np.isfinite(frame_area_m2) or frame_area_m2 <= 0:
        return None
    return float(count) / (float(frame_area_m2) / 1e6)


def predict_tiled(model, images, device="cpu", grid=2, overlap=0.2, include_full=True, nms_iou=0.5):
    """Tiled inference without new packages (idea of SAHI, own code): full frame + grid x grid tiles with overlap,
    boxes shifted back to frame coordinates and merged by NMS. Returns list of (boxes, scores)."""
    import torch
    from torchvision.ops import nms
    out = []
    for im in images:
        W, H = im.size
        tw, th = W / (grid - (grid - 1) * overlap), H / (grid - (grid - 1) * overlap)
        crops, offs = [], []
        for gy in range(grid):
            for gx in range(grid):
                x0, y0 = int(round(gx * tw * (1 - overlap))), int(round(gy * th * (1 - overlap)))
                x1, y1 = min(W, int(round(x0 + tw))), min(H, int(round(y0 + th)))
                crops.append(im.crop((x0, y0, x1, y1)))
                offs.append((x0, y0))
        preds = predict_images(model, crops, device, batch=len(crops))
        bs, ss = [], []
        for (b, s), (x0, y0) in zip(preds, offs):
            if len(b):
                bs.append(b + np.array([x0, y0, x0, y0], dtype=b.dtype))
                ss.append(s)
        if include_full:
            b, s = predict_images(model, [im], device, batch=1)[0]
            bs.append(b)
            ss.append(s)
        if not bs:
            out.append((np.zeros((0, 4), np.float32), np.zeros(0, np.float32)))
            continue
        B = torch.as_tensor(np.concatenate(bs)).float()
        S = torch.as_tensor(np.concatenate(ss)).float()
        keep = nms(B, S, nms_iou).numpy()
        out.append((B.numpy()[keep], S.numpy()[keep]))
    return out
