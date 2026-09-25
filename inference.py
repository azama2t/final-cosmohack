"""Single inference entry point: folder of Sentinel-2 GeoTIFFs -> probability and mask GeoTIFFs.

    python inference.py --data-dir DIR --output OUT_DIR [--device cpu|cuda|auto]
                        [--model lgbm|fdi_rule|mdd|unet] [--channels marida|s2_l2a_12|...]
                        [--threshold T] [--scale auto|1|0.0001] [--strict] [--profile]
                        [--workers N] [--profile-json PATH]

For every *.tif under DIR (recursively; *_cl, *_conf, *_prob, *_mask are skipped) writes
    OUT_DIR/<name>_prob.tif   uint8 0..255 (= prob*255), CRS/transform of the input
    OUT_DIR/<name>_mask.tif   uint8 0/1 (prob >= threshold)
Input: reflectance 0..1 (MARIDA rhorc, or L2A already scaled). Channel order: --channels set from
configs/channels.yaml, else band descriptions, else by band count (11 -> marida, 12 -> s2_l2a_12).
--scale auto multiplies by 1e-4 if the data look like DN (median > 2).
If the requested model cannot be loaded, 'fdi_rule' is used with a warning (--strict: exit 2).

Speed (reports/speed.md): for 'lgbm' files are read and featurized in a thread pool, the rows of
many chips go to the model in one call (CUDA forest kernel with --device cuda/auto, lib_lightgbm
on all CPU cores otherwise) and outputs are written in the pool. torch is never imported for
lgbm/fdi_rule. --profile prints stage times; --profile-json writes them as JSON.

Exit codes: 0 ok; 1 data error (missing dir, no images, unreadable file, wrong bands);
            2 model error (unknown model, load/predict failure).
"""
from __future__ import annotations

import sys
import time

_T_START = time.perf_counter()

from pathlib import Path  # noqa: E402

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "src"))

import argparse  # noqa: E402

EXIT_OK, EXIT_DATA, EXIT_MODEL = 0, 1, 2
#: models that never need torch: the device is handled by the predictor itself (lgbm) or ignored
NO_TORCH_MODELS = ("lgbm", "fdi_rule")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Marine debris inference: GeoTIFFs -> <name>_prob.tif, <name>_mask.tif")
    ap.add_argument("--data-dir", required=True, help="folder with input GeoTIFFs (searched recursively)")
    ap.add_argument("--output", required=True, help="output folder")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    ap.add_argument("--model", default="lgbm", help="predictor name: lgbm | fdi_rule | mdd | unet (default lgbm)")
    ap.add_argument("--channels", default=None, help="channel set from configs/channels.yaml (default: auto)")
    ap.add_argument("--threshold", type=float, default=None, help="mask threshold (default: model's, else 0.5)")
    ap.add_argument("--scale", default="auto", help="auto | factor applied to inputs (e.g. 0.0001 for DN)")
    ap.add_argument("--strict", action="store_true", help="exit 2 instead of falling back to fdi_rule")
    ap.add_argument("--profile", action="store_true", help="print stage times and total")
    ap.add_argument("--workers", type=int, default=0, help="reader/feature/writer threads (0 = auto)")
    ap.add_argument("--profile-json", default=None, help="write stage times to this JSON file")
    return ap.parse_args(argv)


def _err(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)


class _Prof:
    """Stage timers: wall marks of the main thread + summed thread seconds per stage."""

    def __init__(self):
        import threading

        self.lock = threading.Lock()
        self.wall: dict[str, float] = {}
        self.busy: dict[str, float] = {}
        self.t = _T_START

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        self.wall[name] = self.wall.get(name, 0.0) + now - self.t
        self.t = now

    def add(self, name: str, dt: float) -> None:
        with self.lock:
            self.busy[name] = self.busy.get(name, 0.0) + dt


def _median_finite_gt2(np, arr) -> bool:
    """median(arr[isfinite(arr)]) > 2 (DN check). Fast exact pre-check: the median can exceed 2 only
    if at least n//2 finite values exceed 2; reflectance chips never get to the full median."""
    fin = np.isfinite(arr)
    n = int(np.count_nonzero(fin))
    if n == 0:
        return False
    if int(np.count_nonzero((arr > 2.0) & fin)) < n // 2:
        return False
    return float(np.median(arr[fin])) > 2.0


def _run(a) -> int:
    import threading

    prof = _Prof()
    # rasterio (GDAL) takes ~0.5 s to import: do it in the background while the model loads
    threading.Thread(target=lambda: __import__("rasterio"), daemon=True).start()
    import numpy as np

    from macroplastic.io import channel_names, guess_channel_names, list_images, prob_to_uint8, read_raster, write_raster
    from macroplastic.models import ModelError, get_predictor
    from macroplastic.utils import available_cpus, get_logger

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
    prof.mark("imports")

    if a.model in NO_TORCH_MODELS:
        device = a.device  # lgbm resolves cuda/auto itself (NVRTC kernel, no torch); fdi_rule ignores it
    else:
        from macroplastic.utils import resolve_device

        device = resolve_device(a.device)
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
    run_device = getattr(predictor, "device", None) or (device if device in ("cpu", "cuda") else "cpu")
    if a.device == "cuda" and run_device != "cuda":
        log.warning("CUDA is not available -> running on CPU")
    thr = a.threshold if a.threshold is not None else float(getattr(predictor, "threshold", 0.5) or 0.5)
    log.info("model=%s device=%s threshold=%.3f files=%d", used, run_device, thr, len(files))
    prof.mark("model_load")
    if a.profile:
        print(f"[profile] model load {prof.wall['model_load']:.2f} s (imports {prof.wall['imports']:.2f} s)")

    batched = all(hasattr(predictor, m) for m in ("prepare_rows", "predict_rows_many", "is_small"))
    n_workers = a.workers if a.workers and a.workers > 0 else max(2, min(available_cpus(), 8))
    batch_rows = 1_000_000 if run_device == "cuda" else 2_000_000

    def load(f):
        """Read + channel check + scale (+ features for the batched path). Runs in a worker thread."""
        t = time.perf_counter()
        try:
            arr, rprof = read_raster(f)
        except Exception as e:  # noqa: BLE001
            return {"f": f, "err": f"cannot read {f}: {type(e).__name__}: {e}", "code": EXIT_DATA}
        names = fixed_names or guess_channel_names(arr.shape[0], rprof.get("descriptions") or ())
        if names is None or len(names) != arr.shape[0]:
            return {"f": f, "code": EXIT_DATA,
                    "err": f"{f.name}: {arr.shape[0]} bands, cannot map to channels "
                           f"({'set ' + a.channels + ' has ' + str(len(fixed_names)) if fixed_names else 'use --channels'})"}
        s = scale
        if s is None:
            s = 1e-4 if _median_finite_gt2(np, arr) else 1.0
            if s != 1.0:
                log.warning("%s looks like DN (median > 2) -> multiplying by 1e-4", f.name)
        if s != 1.0:
            arr = arr * np.float32(s)
        t1 = time.perf_counter()
        prof.add("read", t1 - t)
        item = {"f": f, "prof": rprof, "arr": arr, "names": names, "rows": None}
        if batched and predictor.is_small(arr):
            try:
                item["rows"] = predictor.prepare_rows(arr, names)
            except Exception as e:  # noqa: BLE001
                return {"f": f, "code": EXIT_MODEL, "err": f"model {used!r} failed on {f.name}: {type(e).__name__}: {e}"}
            item["arr"] = None
            prof.add("features", time.perf_counter() - t1)
        return item

    def write(f, rprof, prob):
        t = time.perf_counter()
        stem = f.stem
        write_raster(out_dir / f"{stem}_prob.tif", prob_to_uint8(prob), rprof, dtype="uint8")
        write_raster(out_dir / f"{stem}_mask.tif", (prob >= thr).astype(np.uint8), rprof, dtype="uint8")
        prof.add("write", time.perf_counter() - t)
        return f

    from concurrent.futures import ThreadPoolExecutor

    n_bad = 0
    t_pred = 0.0
    writes = []
    pend: list[dict] = []
    pend_rows = 0

    def emit(item, prob) -> int | None:
        """Checks + schedules the write of one result. Returns an exit code on fatal error."""
        f = item["f"]
        shape = item["rows"]["shape"] if item.get("rows") is not None else item["arr"].shape[1:]
        prob = np.asarray(prob, dtype=np.float32)
        if prob.shape != tuple(shape):
            _err(f"model {used!r} returned shape {prob.shape}, expected {tuple(shape)}")
            return EXIT_MODEL
        prob = np.nan_to_num(prob, nan=0.0)
        if a.profile:
            print(f"[profile] {f.name}: {int((prob >= thr).sum())} px >= {thr:.2f}")
        writes.append(pool.submit(write, f, item["prof"], prob))
        return None

    def flush() -> int | None:
        nonlocal pend, pend_rows, t_pred
        if not pend:
            return None
        t = time.perf_counter()
        try:
            probs = predictor.predict_rows_many([it["rows"] for it in pend])
        except Exception as e:  # noqa: BLE001
            _err(f"model {used!r} failed on {pend[0]['f'].name}..{pend[-1]['f'].name}: {type(e).__name__}: {e}")
            return EXIT_MODEL
        t_pred += time.perf_counter() - t
        for it, p in zip(pend, probs):
            code = emit(it, p)
            if code is not None:
                return code
        pend, pend_rows = [], 0
        return None

    with ThreadPoolExecutor(n_workers) as pool:
        # bounded look-ahead: many small chips in flight, but only a couple of large tiles (memory)
        window = max(2 * n_workers, 64)
        futs = [pool.submit(load, f) for f in files[:2]]
        nxt = len(futs)
        for i in range(len(files)):
            item = futs[i].result()
            futs[i] = None
            if item.get("arr") is not None and item["arr"].shape[-1] * item["arr"].shape[-2] > 1024 * 1024:
                window = 2
            while nxt < len(files) and nxt - i <= window:
                futs.append(pool.submit(load, files[nxt]))
                nxt += 1
            if "err" in item:
                _err(item["err"])
                if item["code"] == EXIT_MODEL:
                    return EXIT_MODEL
                n_bad += 1
                continue
            if item["rows"] is not None:
                pend.append(item)
                pend_rows += len(item["rows"]["X"])
                if pend_rows >= batch_rows:
                    code = flush()
                    if code is not None:
                        return code
                continue
            t = time.perf_counter()
            try:
                prob = predictor.predict_proba(item["arr"], item["names"])
            except Exception as e:  # noqa: BLE001
                _err(f"model {used!r} failed on {item['f'].name}: {type(e).__name__}: {e}")
                return EXIT_MODEL
            t_pred += time.perf_counter() - t
            code = emit(item, prob)
            if code is not None:
                return code
        code = flush()
        if code is not None:
            return code
        prof.mark("pipeline")
        for w in writes:
            try:
                w.result()
            except Exception as e:  # noqa: BLE001
                _err(f"cannot write outputs to {out_dir}: {type(e).__name__}: {e}")
                return EXIT_DATA
    prof.mark("write_tail")
    prof.busy["predict"] = t_pred
    total = time.perf_counter() - _T_START
    if a.profile:
        busy = ", ".join(f"{k} {v:.2f}" for k, v in prof.busy.items())
        print(f"[profile] stages wall: " + ", ".join(f"{k} {v:.2f}" for k, v in prof.wall.items())
              + f" s; thread-seconds: {busy} (workers={n_workers}, device={run_device})")
        print(f"[profile] total {total:.2f} s for {len(files)} files")
    if a.profile_json:
        import json

        rec = {"files": len(files), "failed": n_bad, "model": used, "device": run_device, "workers": n_workers,
               "total_s": round(total, 4), "wall_s": {k: round(v, 4) for k, v in prof.wall.items()},
               "thread_s": {k: round(v, 4) for k, v in prof.busy.items()}}
        Path(a.profile_json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.profile_json).write_text(json.dumps(rec, indent=1), encoding="utf-8")
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
