"""L23: inference speed of the final model vs light models on 300 chips 256x256 (out/speed_chips = first 300
MARIDA *val* patches, prepared by scripts/measure_speed.py; test never used).

  PYTHONPATH=src .venv/Scripts/python.exe scripts/experiments/l23_speed.py bench --models weights/lgbm,weights_exp/l23/light_k20_t200_l31_s0 --repeats 3
  PYTHONPATH=src .venv/Scripts/python.exe scripts/experiments/l23_speed.py run --model DIR --out DIR [--warm]   (one process)

Pipeline per model (same harness for all, CPU only, THREADS <= 10): read GeoTIFF -> features (only the ones the model
uses, same code as features/pixel.py; checked bit-identical on 5 chips) -> one LightGBM predict over all rows ->
write *_prob.tif (uint8) + *_mask.tif, like inference.py. Cold = new process, wall from spawn to exit.
Warm = same process, second full pass (read+features+model+write). Also times the product inference.py (final model,
--device cpu --workers 10) as an anchor.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

T_START = time.perf_counter()
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ["CUDA_VISIBLE_DEVICES"] = ""
THREADS = int(os.environ.get("L23_THREADS", "10"))
CHIPS = ROOT / "out" / "speed_chips"


def compute_selected(x, feats):
    """x: (11,H,W) BANDS11 float32 with NaN. Only the requested 'win' features, same arithmetic as pixel.py."""
    import numpy as np

    from macroplastic.features import pixel as PX

    H, W = x.shape[1:]
    out = np.empty((len(feats), H, W), np.float32)
    b = {n: x[i] for i, n in enumerate(PX.BANDS11)}
    need_idx = {f.split("_")[0] for f in feats} & set(PX.INDICES)
    ind = PX._indices(b) if need_idx else {}
    src = {"B8": b["B8"], **ind}
    pos = {f: i for i, f in enumerate(feats)}
    for f, i in pos.items():
        if f in b:
            out[i] = b[f]
        elif f in ind:
            out[i] = ind[f]
    for k in PX.WIN_KEYS:
        for w in PX.WIN_SIZES:
            a, s = f"{k}_mean{w}", f"{k}_std{w}"
            if a in pos or s in pos:
                m, sd = PX._win_mean_std(src[k], w)
                if a in pos:
                    out[pos[a]] = m
                if s in pos:
                    out[pos[s]] = sd
    fills = {}
    for k, w in PX.CONTRAST:
        n = f"{k}_dmed{w}"
        if n in pos:
            if k not in fills:
                fills[k] = PX._nan_fill(src[k])
            out[pos[n]] = src[k] - PX._local_median(src[k], w, fills[k])
    nanpix = np.isnan(x).any(0)
    if nanpix.any():
        out[:, nanpix] = np.nan
    return out


def pipeline(model_dir: Path, out_dir: Path, files, booster, meta):
    import numpy as np
    from concurrent.futures import ThreadPoolExecutor

    from macroplastic.features.pixel import BANDS11, compute_features, feature_names, select_bands
    from macroplastic.io import read_raster, write_raster

    feats = meta["features"]
    full = feats == feature_names("win")
    thr = float(meta["threshold"])
    out_dir.mkdir(parents=True, exist_ok=True)
    tm = {"read_feat": 0.0, "model": 0.0, "write": 0.0}

    def load(f):
        arr, prof = read_raster(f)
        names = BANDS11 if arr.shape[0] == 11 else None
        x = select_bands(arr, names)
        x[~np.isfinite(x)] = np.nan
        F = compute_features(x, BANDS11, "win") if full else compute_selected(x, feats)
        bad = np.isnan(x).any(0)
        return F.reshape(len(feats), -1).T, bad, prof, x.shape[1:]

    t = time.perf_counter()
    with ThreadPoolExecutor(THREADS) as pool:
        res = list(pool.map(load, files))
    tm["read_feat"] = time.perf_counter() - t
    t = time.perf_counter()
    X = np.concatenate([r[0] for r in res])
    p = booster.predict(X, num_threads=THREADS).astype(np.float32)
    tm["model"] = time.perf_counter() - t
    t = time.perf_counter()

    def write(i):
        f = files[i]
        _, bad, prof, (h, w) = res[i]
        pr = p[i * h * w:(i + 1) * h * w].reshape(h, w).copy()
        pr[bad] = 0.0
        write_raster(out_dir / f"{f.stem}_prob.tif", np.clip(np.round(pr * 255), 0, 255).astype(np.uint8), prof,
                     dtype="uint8")
        write_raster(out_dir / f"{f.stem}_mask.tif", (pr >= thr).astype(np.uint8), prof, dtype="uint8")

    with ThreadPoolExecutor(THREADS) as pool:
        list(pool.map(write, range(len(files))))
    tm["write"] = time.perf_counter() - t
    return p, tm


def cmd_run(a):
    import lightgbm as lgb

    md = Path(a.model)
    meta = json.loads((md / "meta.json").read_text(encoding="utf-8"))
    booster = lgb.Booster(model_file=str(md / "model.txt"))
    files = sorted(CHIPS.glob("*.tif"))
    t = time.perf_counter()
    _, tm = pipeline(md, Path(a.out), files, booster, meta)
    rec = {"first_pass_s": round(time.perf_counter() - t, 3), "stages": {k: round(v, 3) for k, v in tm.items()},
           "import_load_s": round(t - T_START, 3)}
    if a.warm:
        t = time.perf_counter()
        _, tm2 = pipeline(md, Path(a.out), files, booster, meta)
        s = time.perf_counter() - t
        rec.update(warm_s=round(s, 3), warm_chips_per_s=round(len(files) / s, 1),
                   warm_stages={k: round(v, 3) for k, v in tm2.items()})
    print("L23JSON " + json.dumps(rec), flush=True)


def check_equal(n=5):
    import numpy as np

    from macroplastic.features.pixel import BANDS11, compute_features, feature_names, select_bands
    from macroplastic.io import read_raster

    win = feature_names("win")
    for f in sorted(CHIPS.glob("*.tif"))[:n]:
        arr, _ = read_raster(f)
        x = select_bands(arr, BANDS11)
        x[~np.isfinite(x)] = np.nan
        a = compute_features(x.copy(), BANDS11, "win")
        sub = win[::-1][:30]
        b = compute_selected(x.copy(), sub)
        assert np.array_equal(a[[win.index(s) for s in sub]], b, equal_nan=True), f.name
    return True


def cmd_bench(a):
    py = sys.executable
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), CUDA_VISIBLE_DEVICES="", L23_THREADS=str(THREADS))
    res = {"threads": THREADS, "chips": len(list(CHIPS.glob("*.tif"))), "features_bit_identical": check_equal(),
           "models": {}}
    for m in a.models.split(","):
        md = ROOT / m
        meta = json.loads((md / "meta.json").read_text(encoding="utf-8"))
        import lightgbm as lgb

        nt = lgb.Booster(model_file=str(md / "model.txt")).num_trees()
        runs = []
        for i in range(a.repeats + 1):  # repeats cold runs + 1 run with a warm second pass
            warm = i == a.repeats
            out = ROOT / "out" / "l23_speed_out" / md.name
            cmd = [py, str(Path(__file__)), "run", "--model", str(md), "--out", str(out)] + (["--warm"] if warm else [])
            t = time.perf_counter()
            r = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True)
            wall = time.perf_counter() - t
            line = [ln for ln in r.stdout.splitlines() if ln.startswith("L23JSON ")]
            rec = json.loads(line[-1][8:]) if line else {"err": r.stderr[-500:]}
            rec["wall_s"] = round(wall, 3)
            rec["kind"] = "warm" if warm else "cold"
            runs.append(rec)
            print(m, i, rec, flush=True)
        colds = sorted(r["wall_s"] for r in runs if r["kind"] == "cold")
        res["models"][m] = {"n_features": len(meta["features"]), "n_trees": nt,
                            "threshold": meta["threshold"], "cold_s_median": colds[len(colds) // 2],
                            "cold_s_runs": colds, "warm_chips_per_s": runs[-1].get("warm_chips_per_s"),
                            "runs": runs}
    if a.anchor:
        runs = []
        for i in range(a.repeats):
            out = ROOT / "out" / "l23_speed_out" / "inference_py"
            cmd = [py, str(ROOT / "inference.py"), "--data-dir", str(CHIPS), "--output", str(out), "--device", "cpu",
                   "--workers", str(THREADS)]
            t = time.perf_counter()
            r = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True)
            runs.append(round(time.perf_counter() - t, 3))
            print("inference.py", i, runs[-1], r.returncode, flush=True)
        res["anchor_inference_py_cpu_cold_s"] = sorted(runs)
    Path(a.out_json).write_text(json.dumps(res, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--model", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--warm", action="store_true")
    b = sub.add_parser("bench")
    b.add_argument("--models", required=True)
    b.add_argument("--repeats", type=int, default=3)
    b.add_argument("--anchor", action="store_true")
    b.add_argument("--out-json", default=str(ROOT / "out" / "l23_runs" / "speed.json"))
    a = ap.parse_args()
    {"run": cmd_run, "bench": cmd_bench}[a.cmd](a)


if __name__ == "__main__":
    main()
