"""UNet predictor: smp UNet (resnet34) for Marine Debris, trained on MARIDA by scripts/train_unet.py.

    from macroplastic.models.unet import load_predictor
    pred = load_predictor("weights_exp/unet/bin_r34_s0")        # dir with model.pt + meta.json
    prob = pred.predict_proba(arr, channel_names)   # arr (C,H,W) float32 reflectance 0..1 -> (H,W) float32 P(MD)
    mask = prob >= pred.threshold

Input: the 11 MARIDA bands are picked by name (extra bands such as B9 are ignored) plus the 8 indices of
macroplastic.indices (FDI, FAI, NDVI, NDWI, NDMI, SI, BSI, NRD) = 19 channels. Each channel is clipped to
the train [p0.1, p99.9] range and standardised with train mean/std (stored in meta.json); NaN -> 0 after
standardisation. Pixels with any NaN/inf band -> probability 0 (same as lgbm_predict).
Any tile size: the image is reflect-padded to >= tile, cut into tiles of `tile` px with `overlap` px overlap
and blended with a separable ramp weight. fp32, no TTA. Device: 'auto' (cuda if usable) | 'cpu' | 'cuda'.
Domain note: trained on ACOLITE rhorc (L1C); on Sen2Cor L2A the probability is not calibrated.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Sequence

import numpy as np

from ...features.pixel import BANDS11, select_bands
from ...indices import INDEX_NAMES, stack_indices

_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_WEIGHTS = _ROOT / "weights" / "unet"
INPUT_NAMES = list(BANDS11) + list(INDEX_NAMES)  # 19 input channels


def raw_inputs(arr: np.ndarray, channel_names: Sequence[str]) -> tuple[np.ndarray, np.ndarray]:
    """(C,H,W) reflectance -> ((19,H,W) float32 un-normalised bands+indices, (H,W) bool valid)."""
    b = select_bands(np.asarray(arr), channel_names)
    valid = np.isfinite(b).all(0)
    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        idx = stack_indices(b, BANDS11)
    x = np.concatenate([b, idx], 0).astype(np.float32)
    return x, valid


def normalize(x: np.ndarray, norm: dict) -> np.ndarray:
    """Clip to [lo, hi], (x - mean) / std, NaN/inf -> 0. x (19,...) float32; works in place on a copy."""
    lo = np.asarray(norm["lo"], np.float32).reshape(-1, *([1] * (x.ndim - 1)))
    hi = np.asarray(norm["hi"], np.float32).reshape(lo.shape)
    mu = np.asarray(norm["mean"], np.float32).reshape(lo.shape)
    sd = np.asarray(norm["std"], np.float32).reshape(lo.shape)
    out = np.clip(x, lo, hi)
    out = (out - mu) / sd
    out[~np.isfinite(out)] = 0.0
    return out.astype(np.float32)


def build_model(encoder: str = "resnet34", in_channels: int = len(INPUT_NAMES), classes: int = 2,
                encoder_weights: str | None = None, arch: str = "Unet"):
    import segmentation_models_pytorch as smp

    cls = getattr(smp, arch)
    return cls(encoder_name=encoder, encoder_weights=encoder_weights, in_channels=in_channels, classes=classes)


def _ramp(n: int, overlap: int) -> np.ndarray:
    w = np.ones(n, np.float32)
    if overlap > 0:
        r = (np.arange(overlap, dtype=np.float32) + 0.5) / overlap
        w[:overlap] = r
        w[-overlap:] = np.minimum(w[-overlap:], r[::-1])
    return w


def _starts(n: int, tile: int, stride: int) -> list[int]:
    if n <= tile:
        return [0]
    s = list(range(0, n - tile + 1, stride))
    if s[-1] != n - tile:
        s.append(n - tile)
    return s


class UNetPredictor:
    def __init__(self, weights_dir: str | os.PathLike = DEFAULT_WEIGHTS, device: str | None = "auto",
                 tile: int = 256, overlap: int = 64, batch: int = 8):
        import torch

        from ...utils import resolve_device

        wd = Path(weights_dir)
        if wd.is_file():
            wd = wd.parent
        self.meta = json.loads((wd / "meta.json").read_text(encoding="utf-8"))
        self.name = "unet"
        self.threshold = float(self.meta["threshold"])
        self.norm = self.meta["norm"]
        self.md_index = int(self.meta.get("md_index", 1))
        self.tile, self.overlap, self.batch = int(tile), int(overlap), int(batch)
        self.device = resolve_device(device or "auto")
        m = self.meta["model"]
        self.net = build_model(m["encoder"], len(self.meta["inputs"]), int(m["classes"]), None, m.get("arch", "Unet"))
        sd = torch.load(wd / "model.pt", map_location="cpu", weights_only=True)
        self.net.load_state_dict({k: v.float() for k, v in sd.items()})
        self.net.to(self.device).eval()

    def _forward(self, tiles: np.ndarray) -> np.ndarray:
        """(N,19,t,t) normalised -> (N,t,t) P(MD)."""
        import torch

        with torch.no_grad():
            x = torch.from_numpy(tiles).to(self.device, torch.float32)
            p = torch.softmax(self.net(x), 1)[:, self.md_index]
            return p.cpu().numpy().astype(np.float32)

    def predict_proba(self, arr: np.ndarray, channel_names: Sequence[str]) -> np.ndarray:
        arr = np.asarray(arr)
        if arr.ndim != 3:
            raise ValueError(f"expected (C,H,W), got {arr.shape}")
        x, valid = raw_inputs(arr, channel_names)
        x = normalize(x, self.norm)
        H, W = valid.shape
        t = self.tile
        # pad to a multiple of 32 and at least one tile
        Hp, Wp = max(t, -(-H // 32) * 32), max(t, -(-W // 32) * 32)
        if (Hp, Wp) != (H, W):
            x = np.pad(x, ((0, 0), (0, Hp - H), (0, Wp - W)), mode="reflect" if min(H, W) > 1 else "edge")
        stride = t - self.overlap
        ys, xs = _starts(Hp, t, stride), _starts(Wp, t, stride)
        single = len(ys) == 1 and len(xs) == 1
        wy, wx = _ramp(t, 0 if single else self.overlap), _ramp(t, 0 if single else self.overlap)
        wt = np.outer(wy, wx).astype(np.float32)
        acc = np.zeros((Hp, Wp), np.float32)
        wsum = np.zeros((Hp, Wp), np.float32)
        coords = [(y, x0) for y in ys for x0 in xs]
        for i in range(0, len(coords), self.batch):
            cc = coords[i:i + self.batch]
            tiles = np.stack([x[:, y:y + t, x0:x0 + t] for y, x0 in cc])
            p = self._forward(np.ascontiguousarray(tiles))
            for (y, x0), pk in zip(cc, p):
                acc[y:y + t, x0:x0 + t] += pk * wt
                wsum[y:y + t, x0:x0 + t] += wt
        prob = (acc / np.maximum(wsum, 1e-6))[:H, :W]
        prob[~valid] = 0.0
        return prob.astype(np.float32)


def load_predictor(weights_dir: str | os.PathLike | None = None, weights: str | os.PathLike | None = None,
                   device: str | None = "auto", **kw) -> UNetPredictor:
    """Registry loader. `weights` (dir or model.pt) is an alias of `weights_dir`."""
    wd = weights_dir or weights or DEFAULT_WEIGHTS
    return UNetPredictor(wd, device=device, **{k: v for k, v in kw.items() if k in ("tile", "overlap", "batch")})


try:  # register in the model registry if available
    from ..registry import register as _register

    _register("unet", load_predictor)
except Exception:  # pragma: no cover
    pass
