"""Single inference entry point: folder of Sentinel-2 GeoTIFFs -> probability and mask GeoTIFFs.

    python inference.py --data-dir DIR --output OUT_DIR [--device cpu|cuda|auto]
                        [--model lgbm|fdi_rule|mdd] [--channels marida|s2_l2a_12|...]
                        [--threshold T] [--scale auto|1|0.0001] [--strict] [--profile]

For every *.tif under DIR (recursively; *_cl, *_conf, *_prob, *_mask are skipped) writes
    OUT_DIR/<name>_prob.tif   uint8 0..255 (= prob*255), CRS/transform of the input
    OUT_DIR/<name>_mask.tif   uint8 0/1 (prob >= threshold)
Input: reflectance 0..1 (MARIDA rhorc, or L2A already scaled). Channel order: --channels set from
configs/channels.yaml, else band descriptions, else by band count (11 -> marida, 12 -> s2_l2a_12).
--scale auto multiplies by 1e-4 if the data look like DN (median > 2).
If the requested model cannot be loaded, 'fdi_rule' is used with a warning (--strict: exit 2).

Exit codes: 0 ok; 1 data error (missing dir, no images, unreadable file, wrong bands);
            2 model error (unknown model, load/predict failure).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "src"))

import argparse  # noqa: E402

EXIT_OK, EXIT_DATA, EXIT_MODEL = 0, 1, 2


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Marine debris inference: GeoTIFFs -> <name>_prob.tif, <name>_mask.tif")
    ap.add_argument("--data-dir", required=True, help="folder with input GeoTIFFs (searched recursively)")
    ap.add_argument("--output", required=True, help="output folder")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    ap.add_argument("--model", default="lgbm", help="predictor name: lgbm | fdi_rule | mdd (default lgbm)")
    ap.add_argument("--channels", default=None, help="channel set from configs/channels.yaml (default: auto)")
    ap.add_argument("--threshold", type=float, default=None, help="mask threshold (default: model's, else 0.5)")
    ap.add_argument("--scale", default="auto", help="auto | factor applied to inputs (e.g. 0.0001 for DN)")
    ap.add_argument("--strict", action="store_true", help="exit 2 instead of falling back to fdi_rule")
    ap.add_argument("--profile", action="store_true", help="print time per file and total")
    return ap.parse_args(argv)


def _err(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)


def _run(a) -> int:
    t0 = time.perf_counter()
    import numpy as np

    from macroplastic.io import channel_names, guess_channel_names, list_images, prob_to_uint8, read_raster, write_raster
    from macroplastic.models import ModelError, get_predictor
    from macroplastic.utils import get_logger, resolve_device

    log = get_logger("inference")
    data_dir, out_dir = Path(a.data_dir), Path(a.output)
    if not data_dir.is_dir():
        _err(f"data directory not found: {data_dir}")
        return EXIT_DATA
    files = list_images(data_dir)
    if not files:
        _err(f"no input *.tif in {data_dir} (files ending with _cl/_conf/_prob/_mask are skipped)")
        return EXIT_DATA
    fixed_names = None
    if a.channels:
        try:
            fixed_names = channel_names(a.channels)
        except KeyError as e:
            _err(str(e).strip("'\""))
            return EXIT_DATA
    scale = None
    if str(a.scale).lower() != "auto":
        try:
            scale = float(a.scale)
        except ValueError:
            _err(f"--scale must be 'auto' or a number, got {a.scale!r}")
            return EXIT_DATA

    device = resolve_device(a.device)
    if a.device == "cuda" and device != "cuda":
        log.warning("CUDA is not available -> running on CPU")
    t_load = time.perf_counter()
    try:
        predictor = get_predictor(a.model, fallback=not a.strict, device=device)
    except KeyError as e:
        _err(str(e).strip("'\""))
        return EXIT_MODEL
    except ModelError as e:
        _err(str(e))
        return EXIT_MODEL
    used = getattr(predictor, "name", a.model)
    if getattr(predictor, "fallback_from", None):
        log.warning("using %r instead of %r", used, a.model)
    thr = a.threshold if a.threshold is not None else float(getattr(predictor, "threshold", 0.5) or 0.5)
    log.info("model=%s device=%s threshold=%.3f files=%d", used, device, thr, len(files))
    if a.profile:
        print(f"[profile] model load {time.perf_counter() - t_load:.2f} s")

    n_bad = 0
    for f in files:
        t1 = time.perf_counter()
        try:
            arr, prof = read_raster(f)
        except Exception as e:  # noqa: BLE001
            _err(f"cannot read {f}: {type(e).__name__}: {e}")
            n_bad += 1
            continue
        names = fixed_names or guess_channel_names(arr.shape[0], prof.get("descriptions") or ())
        if names is None or len(names) != arr.shape[0]:
            _err(f"{f.name}: {arr.shape[0]} bands, cannot map to channels "
                 f"({'set ' + a.channels + ' has ' + str(len(fixed_names)) if fixed_names else 'use --channels'})")
            n_bad += 1
            continue
        s = scale
        if s is None:
            finite = arr[np.isfinite(arr)]
            s = 1e-4 if finite.size and float(np.median(finite)) > 2.0 else 1.0
            if s != 1.0:
                log.warning("%s looks like DN (median > 2) -> multiplying by 1e-4", f.name)
        if s != 1.0:
            arr = arr * np.float32(s)
        try:
            prob = np.asarray(predictor.predict_proba(arr, names), dtype=np.float32)
        except Exception as e:  # noqa: BLE001
            _err(f"model {used!r} failed on {f.name}: {type(e).__name__}: {e}")
            return EXIT_MODEL
        if prob.shape != arr.shape[1:]:
            _err(f"model {used!r} returned shape {prob.shape}, expected {arr.shape[1:]}")
            return EXIT_MODEL
        prob = np.nan_to_num(prob, nan=0.0)
        stem = f.stem
        try:
            write_raster(out_dir / f"{stem}_prob.tif", prob_to_uint8(prob), prof, dtype="uint8")
            write_raster(out_dir / f"{stem}_mask.tif", (prob >= thr).astype(np.uint8), prof, dtype="uint8")
        except Exception as e:  # noqa: BLE001
            _err(f"cannot write outputs for {f.name} to {out_dir}: {type(e).__name__}: {e}")
            return EXIT_DATA
        if a.profile:
            print(f"[profile] {f.name}: {time.perf_counter() - t1:.3f} s, "
                  f"{int((prob >= thr).sum())} px >= {thr:.2f}")
    if a.profile:
        print(f"[profile] total {time.perf_counter() - t0:.2f} s for {len(files)} files")
    log.info("done: %d ok, %d failed -> %s", len(files) - n_bad, n_bad, out_dir)
    return EXIT_DATA if n_bad else EXIT_OK


def main(argv=None) -> int:
    a = parse_args(argv)
    try:
        return _run(a)
    except KeyboardInterrupt:
        _err("interrupted")
        return EXIT_DATA
    except Exception as e:  # noqa: BLE001 - never show a traceback to the user
        _err(f"unexpected {type(e).__name__}: {e}")
        return EXIT_DATA


if __name__ == "__main__":
    sys.exit(main())
