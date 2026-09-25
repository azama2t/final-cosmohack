"""marinedebrisdetector (Russwurm et al. 2023, MIT) inference on live L2A crops.

Model definition is re-implemented here (no pytorch_lightning needed): the checkpoint state_dict keys are
`model.<smp.UnetPlusPlus | UNet>` + buffer `threshold`. Default checkpoint = the one MDD itself loads via torch hub
(checkpoints.py CHECKPOINTS["unet++1"]): models/mdd/unet++1/epoch=54-val_loss=0.50-auroc=0.987.ckpt, threshold 0.0639.

Input: 12 bands B1..B8,B8A,B9,B11,B12 as reflectance 0..1. MDD's ScenePredictor reads L2A DN and multiplies by 1e-4
(pre-baseline-04.00 DN, i.e. no offset), so the model's input = harmonized reflectance = our bands.tif as is.
NaN -> 0 (MDD pads with zeros). Tiled inference with overlap, centre crop is kept; fp32; no TTA.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[3]
# Display / flagging threshold for live L2A scenes (the checkpoint threshold 0.0639 is kept as threshold_checkpoint).
# Chosen from live scenes only, not MARIDA: on calm clean-water scenes (Honduras 2025-01-30/02-19, Haiti 2021-01-03,
# Durban 2026-05-04) P>=0.0639 flags 0.15-3 permille of the water, mostly wave texture, while P>=0.5 flags 0-225 px;
# on the known debris event Durban 2019-04-24 (paper case) 2717 px remain at 0.5. See reports/tasklog/06_live.md.
DISPLAY_THRESHOLD = 0.5
DEFAULT_CKPT = ROOT / "models" / "mdd" / "unet++1" / "epoch=54-val_loss=0.50-auroc=0.987.ckpt"


# MDD's own UNet class is loaded from the cloned repo (data_cache/marinedebrisdetector/.../unet.py).
def _load_unet_class():
    import importlib.util
    p = ROOT / "data_cache" / "marinedebrisdetector" / "marinedebrisdetector" / "model" / "unet.py"
    spec = importlib.util.spec_from_file_location("mdd_unet", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.UNet


def load_model(ckpt: str | Path = DEFAULT_CKPT, device: str = "cpu"):
    ck = torch.load(str(ckpt), map_location="cpu", weights_only=False)
    args = ck["hyper_parameters"]["args"]
    sd = ck["state_dict"]
    thr = float(sd["threshold"])
    inch = 4 if getattr(args, "hr_only", False) else 12
    if args.model == "unet++":
        import segmentation_models_pytorch as smp
        net = smp.UnetPlusPlus(in_channels=inch, classes=1, encoder_weights=None)
    elif args.model == "unet":
        net = _load_unet_class()(n_channels=inch, n_classes=1, bilinear=False)
    else:
        raise NotImplementedError(args.model)
    msd = {k[len("model."):]: v for k, v in sd.items() if k.startswith("model.")}
    missing, unexpected = net.load_state_dict(msd, strict=False)
    if missing or unexpected:
        raise RuntimeError(f"state_dict mismatch: missing={missing[:5]} unexpected={unexpected[:5]}")
    net.eval().to(device)
    return net, thr, dict(model=args.model, ckpt=str(Path(ckpt).relative_to(ROOT)) if str(ckpt).startswith(str(ROOT))
                          else str(ckpt), epoch=int(ck.get("epoch", -1)))


@torch.no_grad()
def predict(net, x: np.ndarray, device: str = "cpu", tile: int = 480, margin: int = 64, batch: int = 4):
    """x: (12,H,W) reflectance. Returns (H,W) float32 probability. Tiles of `tile` px, each read with `margin` px
    context on every side (reflect-free zero padding at scene borders like MDD), only the centre is written."""
    x = np.nan_to_num(x.astype(np.float32), nan=0.0)
    C, H, W = x.shape
    out = np.zeros((H, W), np.float32)
    S = tile + 2 * margin  # 608, divisible by 32 for smp encoders
    S = int(np.ceil(S / 32) * 32)
    pad = np.pad(x, ((0, 0), (margin, S), (margin, S)))
    coords = [(r, c) for r in range(0, H, tile) for c in range(0, W, tile)]
    for i in range(0, len(coords), batch):
        cs = coords[i:i + batch]
        xb = torch.from_numpy(np.stack([pad[:, r:r + S, c:c + S] for r, c in cs])).to(device)
        p = torch.sigmoid(net(xb))[:, 0].float().cpu().numpy()
        for (r, c), pi in zip(cs, p):
            h, w = min(tile, H - r), min(tile, W - c)
            out[r:r + h, c:c + w] = pi[margin:margin + h, margin:margin + w]
    return out


def run_scene(scene_dir: str | Path, ckpt=DEFAULT_CKPT, device: str | None = None, out_name: str = "prob_mdd"):
    import json
    import rasterio
    scene_dir = Path(scene_dir)
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(0)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    with rasterio.open(scene_dir / "bands.tif") as src:
        x = src.read()
        prof = src.profile
        names = list(src.descriptions)
    want = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"]
    if names != want:
        x = x[[names.index(b) for b in want]]
    t0 = time.time()
    net, thr, info = load_model(ckpt, device)
    t1 = time.time()
    prob = predict(net, x, device)
    if device.startswith("cuda"):
        torch.cuda.synchronize()
    t2 = time.time()
    u8 = np.clip(np.round(prob * 255), 0, 255).astype(np.uint8)
    prof = dict(driver="GTiff", width=prof["width"], height=prof["height"], count=1, dtype="uint8",
                crs=prof["crs"], transform=prof["transform"], compress="deflate", tiled=True,
                blockxsize=256, blockysize=256)
    with rasterio.open(scene_dir / f"{out_name}.tif", "w", **prof) as dst:
        dst.write(u8[None])
        dst.set_band_description(1, "P(marine debris)*255")
    water = None
    if (scene_dir / "water_mask.tif").exists():
        with rasterio.open(scene_dir / "water_mask.tif") as src:
            water = src.read(1).astype(bool)
    above = prob >= DISPLAY_THRESHOLD
    above_ck = prob >= thr
    meta = dict(model=f"marinedebrisdetector {info['model']} seed1 (Russwurm et al. 2023)",
                threshold=DISPLAY_THRESHOLD, threshold_checkpoint=thr,
                threshold_note="threshold = display/flag threshold for live L2A (high confidence); "
                               "threshold_checkpoint = MDD validation threshold stored in the checkpoint",
                n_above_checkpoint_threshold_water=int((above_ck & water).sum()) if water is not None else None,
                ckpt=info["ckpt"], epoch=info["epoch"], device=device, runtime_s=round(t2 - t1, 2),
                load_s=round(t1 - t0, 2), height=int(prob.shape[0]), width=int(prob.shape[1]),
                n_above_threshold=int(above.sum()),
                n_above_threshold_water=int((above & water).sum()) if water is not None else None,
                encoding="uint8 = round(P*255)", tta=False, precision="fp32",
                input="bands.tif reflectance (= L2A DN*1e-4 pre-04.00 convention), NaN->0",
                tiling="480 px tiles + 64 px context margin, centre kept")
    (scene_dir / f"{out_name}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return prob, meta
