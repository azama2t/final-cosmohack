"""Single inference entry point: folder of Sentinel-2 GeoTIFFs -> probability and mask GeoTIFFs.

    python inference.py --data-dir DIR --output OUT_DIR [--device cpu|cuda|auto]
                        [--model lgbm|fdi_rule|mdd|unet] [--weights DIR] [--channels marida|s2_l2a_12|...]
                        [--threshold T] [--scale auto|1|0.0001] [--offset auto|0|-1000] [--strict]
                        [--profile] [--workers N] [--profile-json PATH] [--report errors.json]

For every *.tif under DIR (recursively; *_cl, *_conf, *_prob, *_mask are skipped) writes
    OUT_DIR/<name>_prob.tif   uint8 0..255 (= prob*255), CRS/transform of the input
    OUT_DIR/<name>_mask.tif   uint8 0/1 (prob >= threshold)
Input: reflectance 0..1 (MARIDA rhorc, or L2A already scaled). Channel order: --channels set from
configs/channels.yaml, else band descriptions, else by band count (11 -> marida, 12 -> s2_l2a_12).
Reflectance = (input + offset) * scale.
--scale auto multiplies by 1e-4 if the data look like DN (median > 2); for such DN files --offset auto
decides the L2A BOA_ADD_OFFSET (processing baseline >= 04.00: DN = 10000*rho + 1000) from the data:
median B12 DN of the dark pixels (B8 <= its 25th percentile, i.e. water) >= 500 -> offset -1000 (DN 0 =
nodata -> NaN), else 0; the rule and the measured median are logged per file. Manual mode:
--scale 1e-4 --offset -1000 (with an explicit --scale, --offset auto means 0).
1-band rasters (scl.tif, masks, prob_*.tif) are skipped with a warning (not an S2 image).
If the requested model cannot be loaded, 'fdi_rule' is used with a warning (--strict: exit 2).

Speed (reports/speed.md): for 'lgbm' files are read and featurized in a thread pool, the rows of
many chips go to the model in one call (CUDA forest kernel with --device cuda/auto, lib_lightgbm
on all CPU cores otherwise) and outputs are written in the pool. torch is never imported for
lgbm/fdi_rule. --profile prints stage times; --profile-json writes them as JSON.

Errors are isolated per file (lane L26): a file that cannot be read, has wrong/missing bands, or makes the
model fail is reported (stderr; --report FILE.json lists every failed/skipped file) and the remaining files
are still processed and written.
Exit codes: 0 ok; 1 some files failed (or data error: missing dir, no images, output not writable);
            2 the model could not be loaded at all (unknown model, load failure with --strict).
"""
from __future__ import annotations

import os
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
    ap.add_argument("--weights", default=None,
                    help="weights folder of the model (model.txt/model.pt + meta.json); default: weights/<model>")
    ap.add_argument("--channels", default=None, help="channel set from configs/channels.yaml (default: auto)")
    ap.add_argument("--threshold", type=float, default=None, help="mask threshold (default: model's, else 0.5)")
    ap.add_argument("--scale", default="auto", help="auto | factor applied to inputs (e.g. 0.0001 for DN)")
    ap.add_argument("--offset", default="auto",
                    help="auto | value added to inputs BEFORE --scale (e.g. -1000 for L2A DN of baseline >= 04.00); "
                         "auto: decided from dark-pixel B12 for DN files under --scale auto, else 0")
    ap.add_argument("--report", default=None, help="write a JSON summary of failed / skipped files here")
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


#: L2A processing baseline >= 04.00 (from 2022-01-25): DN = 10000 * rho + BOA_ADD_OFFSET(1000)
BOA_OFFSET_DN = 1000.0
#: detection threshold on the dark-pixel SWIR median (lane L31: 1000 -> 500). Water without the offset:
#: DN ~10..240 (rho_B12 0.001..0.024); with it: ~950+ (L2A water is often slightly negative, rho -0.005 ->
#: DN 950, which the old >= 1000 test missed). 500 DN = 0.05 reflectance of margin on both sides.
BOA_DETECT_DN = 500.0


def _boa_offset_rule(np, arr, names) -> tuple[float, str]:
    """DN file: is the L2A BOA_ADD_OFFSET (+1000) inside the pixels? Same idea as live.stac.es_offset_in_pixels:
    open water is dark in SWIR (rho_B12 ~ 0.000-0.024 -> DN 0..240 without, ~950+ with the offset: L2A water can
    be slightly negative); threshold BOA_DETECT_DN = 500 sits in the gap. Dark pixels = B8 <= its 25th percentile (water), both bands finite and > 0.
    Returns (offset to add, evidence)."""
    from macroplastic.io import normalize_band

    norm = [normalize_band(n) for n in names]
    sw = next((b for b in ("B12", "B11") if b in norm), None)
    if sw is None:
        return 0.0, "no B12/B11 band -> offset 0"
    b = arr[norm.index(sw)]
    nir = arr[norm.index("B8")] if "B8" in norm else b
    step = max(1, int(np.sqrt(b.size / 250_000)))  # subsample big tiles
    b, nir = b[::step, ::step], nir[::step, ::step]
    ok = np.isfinite(b) & np.isfinite(nir) & (b > 0) & (nir > 0)
    if int(ok.sum()) < 16:
        return 0.0, f"< 16 valid {sw} pixels -> offset 0"
    bv, nv = b[ok], nir[ok]
    dark = bv[nv <= np.percentile(nv, 25)]
    med = float(np.median(dark))
    if med >= BOA_DETECT_DN:
        neg = " (water slightly below 0 after subtraction, usual for L2A)" if med < BOA_OFFSET_DN else ""
        return -BOA_OFFSET_DN, (f"dark-pixel {sw} median DN={med:.0f} >= {BOA_DETECT_DN:.0f} -> L2A BOA_ADD_OFFSET "
                                f"in pixels (baseline >= 04.00): subtracting {BOA_OFFSET_DN:.0f} DN{neg}")
    return 0.0, f"dark-pixel {sw} median DN={med:.0f} < {BOA_DETECT_DN:.0f} -> no BOA offset"


def _run(a) -> int:
    import threading

    prof = _Prof()
    # rasterio (GDAL) takes ~0.5 s to import: do it in the background while the model loads
    threading.Thread(target=lambda: __import__("rasterio"), daemon=True).start()
    import numpy as np

    from macroplastic.io import channel_names, guess_channel_names, list_images, normalize_band, prob_to_uint8,         read_raster, win_path, write_raster
    from macroplastic.io import is_dir as io_is_dir
    from macroplastic.models import ModelError, get_predictor
    from macroplastic.utils import available_cpus, get_logger

    log = get_logger("inference")
    data_dir, out_dir = Path(a.data_dir), Path(a.output)
    if not io_is_dir(data_dir):
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
    offset = None  # None = auto (only for DN files under --scale auto)
    if str(a.offset).lower() != "auto":
        try:
            offset = float(a.offset)
        except ValueError:
            _err(f"--offset must be 'auto' or a number, got {a.offset!r}")
            return EXIT_DATA
    elif scale is not None:
        offset = 0.0  # manual mode: no guessing
    try:  # fail early (one message) if the output folder cannot be created
        os.makedirs(win_path(out_dir), exist_ok=True)
    except OSError as e:
        _err(f"cannot write outputs to {out_dir}: {type(e).__name__}: {e}")
        return EXIT_DATA
    prof.mark("imports")

    if a.model in NO_TORCH_MODELS:
        device = a.device  # lgbm resolves cuda/auto itself (NVRTC kernel, no torch); fdi_rule ignores it
    else:
        from macroplastic.utils import resolve_device

        device = resolve_device(a.device)
    try:
        wkw = {"weights": a.weights} if a.weights else {}  # default: the loader's weights/<model>
        predictor = get_predictor(a.model, fallback=not a.strict, device=device, **wkw)
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
    required = [normalize_band(b) for b in (getattr(predictor, "required_bands", None) or ())]
    n_workers = a.workers if a.workers and a.workers > 0 else max(2, min(available_cpus(), 8))
    batch_rows = 1_000_000 if run_device == "cuda" else 2_000_000
    failed: list[dict] = []   # {"file", "stage", "error"}
    skipped: list[dict] = []  # {"file", "reason"}
    rules: list[dict] = []    # DN scale/offset decisions
    n_by_count = [0]          # files whose band order was assumed by band count
    lock = threading.Lock()

    def fail(f, stage: str, msg: str) -> None:
        _err(msg)
        with lock:
            failed.append({"file": str(f), "stage": stage, "error": msg})

    def load(f):
        """Read + channel check + scale (+ features for the batched path). Runs in a worker thread.
        Never raises: problems come back as {'f', 'err', 'stage'} (or {'f', 'skip'})."""
        try:
            return _load(f)
        except Exception as e:  # noqa: BLE001
            return {"f": f, "stage": "prepare", "err": f"{f.name}: {type(e).__name__}: {e}"}

    def _load(f):
        t = time.perf_counter()
        try:
            arr, rprof = read_raster(f)
        except Exception as e:  # noqa: BLE001
            return {"f": f, "stage": "read", "err": f"cannot read {f}: {type(e).__name__}: {e}"}
        if arr.shape[0] == 1 and not fixed_names:
            return {"f": f, "skip": "1 band: not a multispectral S2 image (mask / SCL / probability raster)"}
        desc = rprof.get("descriptions") or ()
        names = fixed_names or guess_channel_names(arr.shape[0], desc)
        if names is None or len(names) != arr.shape[0]:
            return {"f": f, "stage": "channels",
                    "err": f"{f.name}: {arr.shape[0]} bands, cannot map to channels "
                           f"({'set ' + a.channels + ' has ' + str(len(fixed_names)) if fixed_names else 'use --channels'})"}
        if not fixed_names and len([d for d in desc if d]) != arr.shape[0]:
            with lock:
                n_by_count[0] += 1
        if required:
            have = {normalize_band(n) for n in names}
            miss = [b for b in required if b not in have]
            if miss:
                return {"f": f, "stage": "channels",
                        "err": f"{f.name}: missing bands {miss} required by model {used!r}; got {list(names)}"}
        s, off = scale, offset
        if s is None:
            s = 1e-4 if _median_finite_gt2(np, arr) else 1.0
            if s != 1.0:
                if off is None:
                    off, why = _boa_offset_rule(np, arr, names)
                else:
                    why = f"--offset {off:g}"
                log.warning("%s looks like DN (median > 2) -> multiplying by 1e-4; %s", f.name, why)
                with lock:
                    rules.append({"file": str(f), "scale": s, "offset": off, "rule": why})
        if off is None:
            off = 0.0
        if off != 0.0:
            arr = arr.astype(np.float32, copy=True)
            if offset is None:  # auto-detected L2A offset: DN 0 is NO_DATA, not rho = -0.1
                arr[arr == 0] = np.nan
            arr += np.float32(off)
        if s != 1.0:
            arr = arr * np.float32(s)
        t1 = time.perf_counter()
        prof.add("read", t1 - t)
        item = {"f": f, "prof": rprof, "arr": arr, "names": names, "rows": None}
        if batched and predictor.is_small(arr):
            try:
                item["rows"] = predictor.prepare_rows(arr, names)
            except Exception as e:  # noqa: BLE001
                return {"f": f, "stage": "features",
                        "err": f"model {used!r} failed on {f.name}: {type(e).__name__}: {e}"}
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

    t_pred = 0.0
    writes: list[tuple] = []
    pend: list[dict] = []
    pend_rows = 0

    def emit(item, prob) -> None:
        """Checks + schedules the write of one result (a bad result fails only this file)."""
        f = item["f"]
        shape = item["rows"]["shape"] if item.get("rows") is not None else item["arr"].shape[1:]
        prob = np.asarray(prob, dtype=np.float32)
        if prob.shape != tuple(shape):
            fail(f, "predict", f"model {used!r} returned shape {prob.shape} on {f.name}, expected {tuple(shape)}")
            return
        prob = np.nan_to_num(prob, nan=0.0)
        if a.profile:
            print(f"[profile] {f.name}: {int((prob >= thr).sum())} px >= {thr:.2f}")
        writes.append((f, pool.submit(write, f, item["prof"], prob)))

    def flush() -> None:
        nonlocal pend, pend_rows, t_pred
        if not pend:
            return
        t = time.perf_counter()
        try:
            probs = predictor.predict_rows_many([it["rows"] for it in pend])
        except Exception as e:  # noqa: BLE001 - retry file by file: only the culprit fails
            log.warning("batch predict failed (%s: %s) -> retrying %d files one by one", type(e).__name__, e, len(pend))
            probs = []
            for it in pend:
                try:
                    probs.append(predictor.predict_rows_many([it["rows"]])[0])
                except Exception as e1:  # noqa: BLE001
                    fail(it["f"], "predict", f"model {used!r} failed on {it['f'].name}: {type(e1).__name__}: {e1}")
                    probs.append(None)
        t_pred += time.perf_counter() - t
        for it, p in zip(pend, probs):
            if p is not None:
                emit(it, p)
        pend, pend_rows = [], 0

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
            if "skip" in item:
                log.warning("skipped %s: %s", item["f"].name, item["skip"])
                skipped.append({"file": str(item["f"]), "reason": item["skip"]})
                continue
            if "err" in item:
                fail(item["f"], item["stage"], item["err"])
                continue
            if item["rows"] is not None:
                pend.append(item)
                pend_rows += len(item["rows"]["X"])
                if pend_rows >= batch_rows:
                    flush()
                continue
            t = time.perf_counter()
            try:
                prob = predictor.predict_proba(item["arr"], item["names"])
            except Exception as e:  # noqa: BLE001
                fail(item["f"], "predict", f"model {used!r} failed on {item['f'].name}: {type(e).__name__}: {e}")
                continue
            finally:
                t_pred += time.perf_counter() - t
            emit(item, prob)
        flush()
        prof.mark("pipeline")
        for f, w in writes:
            try:
                w.result()
            except Exception as e:  # noqa: BLE001
                fail(f, "write", f"cannot write outputs of {f.name} to {out_dir}: {type(e).__name__}: {e}")
    prof.mark("write_tail")
    prof.busy["predict"] = t_pred
    if n_by_count[0]:
        log.info("band order: %d file(s) without S2 band descriptions -> order assumed by band count "
                 "(11 -> marida, 12 -> s2_l2a_12); use --channels to set it explicitly", n_by_count[0])
    n_bad = len(failed)
    n_ok = len(files) - n_bad - len(skipped)
    code = EXIT_DATA if (n_bad or n_ok == 0) else EXIT_OK
    total = time.perf_counter() - _T_START
    if a.profile:
        busy = ", ".join(f"{k} {v:.2f}" for k, v in prof.busy.items())
        print(f"[profile] stages wall: " + ", ".join(f"{k} {v:.2f}" for k, v in prof.wall.items())
              + f" s; thread-seconds: {busy} (workers={n_workers}, device={run_device})")
        print(f"[profile] total {total:.2f} s for {len(files)} files")
    import json

    if a.profile_json:
        rec = {"files": len(files), "failed": n_bad, "skipped": len(skipped), "model": used, "device": run_device,
               "workers": n_workers, "total_s": round(total, 4),
               "wall_s": {k: round(v, 4) for k, v in prof.wall.items()},
               "thread_s": {k: round(v, 4) for k, v in prof.busy.items()}}
        Path(a.profile_json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.profile_json).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    if a.report:
        rep = {"data_dir": str(data_dir), "output": str(out_dir), "model": used, "device": run_device,
               "files": len(files), "ok": n_ok, "failed": sorted(failed, key=lambda r: r["file"]),
               "skipped": sorted(skipped, key=lambda r: r["file"]),
               "input_rules": sorted(rules, key=lambda r: r["file"]), "exit_code": code}
        try:
            os.makedirs(os.path.dirname(win_path(a.report)), exist_ok=True)
            with open(win_path(a.report), "w", encoding="utf-8") as fh:
                json.dump(rep, fh, ensure_ascii=False, indent=1)
        except OSError as e:
            _err(f"cannot write report {a.report}: {type(e).__name__}: {e}")
    if failed:
        names_bad = [Path(r["file"]).name for r in sorted(failed, key=lambda r: r["file"])]
        _err(f"{n_bad} of {len(files)} file(s) failed: " + ", ".join(names_bad[:20]) + (" ..." if n_bad > 20 else ""))
    if n_ok == 0 and not failed:
        _err(f"no processable image in {data_dir} ({len(skipped)} file(s) skipped)")
    log.info("done: %d ok, %d failed, %d skipped -> %s", n_ok, n_bad, len(skipped), out_dir)
    return code


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
