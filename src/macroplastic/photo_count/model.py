"""Faster R-CNN (torchvision ResNet50-FPN, 2 classes: background + garbage) — the architecture of the published
FML weights (fasterrcnn_fml.pth, authors' Google Drive). Inference-only helpers used by the API and scripts."""
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
WEIGHTS_DIR = ROOT / "weights_exp" / "photo_count"
CARD = WEIGHTS_DIR / "model_card.json"


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


def load_card():
    if CARD.exists():
        return json.loads(CARD.read_text(encoding="utf-8"))
    return None


class Counter:
    """Lazy singleton used by the API: loads the weights named in model_card.json once."""
    _inst = None

    def __init__(self, card):
        import os
        import torch
        self.card = card
        want = os.environ.get("MACROPLASTIC_PHOTO_DEVICE") or card.get("device", "auto")  # auto | cpu | cuda
        self.device = "cuda" if (torch.cuda.is_available() and want != "cpu") else "cpu"
        self.model = load_model(WEIGHTS_DIR / card["weights_file"], self.device)
        self.threshold = float(card["threshold"])

    @classmethod
    def get(cls):
        if cls._inst is None:
            card = load_card()
            if card is None:
                raise FileNotFoundError(f"no model card at {CARD}")
            cls._inst = cls(card)
        return cls._inst

    def count(self, image, threshold=None):
        thr = self.threshold if threshold is None else float(threshold)
        boxes, scores = predict_images(self.model, [image], self.device, batch=1)[0]
        keep = scores >= thr
        return boxes[keep], scores[keep], thr


def density_per_km2(count, frame_area_m2):
    """Items per km^2 on the frame scale — only if the imaged water area is known (not a satellite estimate)."""
    if frame_area_m2 is None or not np.isfinite(frame_area_m2) or frame_area_m2 <= 0:
        return None
    return float(count) / (float(frame_area_m2) / 1e6)
