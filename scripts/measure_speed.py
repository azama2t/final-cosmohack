"""Inference speed: cold start of `python inference.py` on 300 MARIDA chips (CPU and CUDA mode) + warm throughput.

    .venv/Scripts/python.exe scripts/measure_speed.py                  # current code, 3 cold runs per device
    .venv/Scripts/python.exe scripts/measure_speed.py --baseline       # + the committed (git HEAD) inference code
    .venv/Scripts/python.exe scripts/gpu_queue.py submit --name speed --wait -- .venv/Scripts/python.exe scripts/measure_speed.py --baseline

Chips: the first 300 patches of the MARIDA *val* split (splits/val_X.txt; test is never used), copied
to out/speed_chips/ (256x256, 11 bands, float32, the official MARIDA files unchanged).
Cold run = a new process `python inference.py --data-dir out/speed_chips --output out/speed_out_<dev>
--device <dev> --model lgbm`; wall time from process start to exit (the OS file cache is NOT flushed;
the output folder is deleted before each run). Stage times come from inference.py --profile-json.
Warm = same process, second call: end-to-end (read+features+model+write) and model-only on arrays
already in memory. --baseline builds out/speed_baseline/ from `git show HEAD:<file>` (read-only git)
and times it the same way; outputs of every run are compared to the first CPU run of the baseline
(or of the current code): max |prob_u8 diff| and differing mask pixels.

Writes reports/speed.json and out/speed_runs.json (all raw runs).
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CHIPS = ROOT / "out" / "speed_chips"
MARIDA = ROOT / "data" / "MARIDA"
BASE = ROOT / "out" / "speed_baseline"
BASELINE_FILES = ["inference.py", "src/macroplastic/models/lgbm_predict.py", "src/macroplastic/features/pixel.py",
                  "src/macroplastic/models/registry.py", "src/macroplastic/models/__init__.py"]


def prepare_chips(n: int) -> list[Path]:
    CHIPS.mkdir(parents=True, exist_ok=True)
    ids = [s.strip() for s in (MARIDA / "splits" / "val_X.txt").read_text().splitlines() if s.strip()][:n]
    out = []
    for pid in ids:
        scene = pid.rsplit("_", 1)[0]
        src = MARIDA / "patches" / f"S2_{scene}" / f"S2_{pid}.tif"
        dst = CHIPS / src.name
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dst)
        out.append(dst)
    extra = sorted(set(CHIPS.glob("*.tif")) - set(out))
    for p in extra:  # keep exactly n chips
        p.unlink()
    return out


def build_baseline() -> Path:
    """out/speed_baseline/ = src/, configs/, weights/lgbm from the working tree + git HEAD versions of the L17 files."""
    if BASE.exists():
        shutil.rmtree(BASE)
    shutil.copytree(ROOT / "src", BASE / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "configs", BASE / "configs")
    shutil.copytree(ROOT / "weights" / "lgbm", BASE / "weights" / "lgbm")
    for f in BASELINE_FILES:
        txt = subprocess.run(["git", "show", f"HEAD:{f}"], cwd=ROOT, capture_output=True, check=True).stdout
        (BASE / f).parent.mkdir(parents=True, exist_ok=True)
        (BASE / f).write_bytes(txt)
    return BASE / "inference.py"


def cold_run(script: Path, device: str, out_dir: Path, prof_json: Path | None) -> dict:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    cmd = [sys.executable, str(script), "--data-dir", str(CHIPS), "--output", str(out_dir), "--device", device,
           "--model", "lgbm"]
    if prof_json is not None:
        cmd += ["--profile-json", str(prof_json)]
    else:
        cmd += ["--profile"]
    t = time.perf_counter()
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    wall = time.perf_counter() - t
    rec = {"device": device, "wall_s": round(wall, 3), "exit": r.returncode}
    dev_line = [ln for ln in r.stderr.splitlines() if "model=" in ln]
    rec["log"] = dev_line[-1].split("inference: ", 1)[-1] if dev_line else r.stderr[-300:]
    if prof_json is not None and prof_json.exists():
        rec["profile"] = json.loads(prof_json.read_text(encoding="utf-8"))
    else:
        rec["profile_stdout"] = [ln for ln in r.stdout.splitlines() if "px >=" not in ln]
    return rec


def compare(ref: Path, new: Path) -> dict:
    import numpy as np

    from macroplastic.io import read_raster

    mx, md, n = 0, 0, 0
    for f in sorted(ref.glob("*_prob.tif")):
        a = read_raster(f, dtype=None)[0].astype(np.int16)
        b = read_raster(new / f.name, dtype=None)[0].astype(np.int16)
        mx = max(mx, int(np.abs(a - b).max()))
        m = f.name.replace("_prob", "_mask")
        md += int((read_raster(ref / m, dtype=None)[0] != read_raster(new / m, dtype=None)[0]).sum())
        n += 1
    return {"files": n, "max_abs_diff_u8": mx, "mask_diff_px": md}


def warm(devices: list[str]) -> dict:
    """Same process: end-to-end second call of inference.main and model-only throughput."""
    import numpy as np

    sys.path.insert(0, str(ROOT))
    import inference
    from macroplastic.features.pixel import BANDS11
    from macroplastic.io import list_images, read_raster
    from macroplastic.models.lgbm_predict import load_predictor

    files = list_images(CHIPS)
    arrs = [read_raster(f)[0] for f in files]
    res = {}
    for dev in devices:
        out = ROOT / "out" / f"speed_warm_{dev}"
        e2e = []
        for _ in range(2):
            if out.exists():
                shutil.rmtree(out)
            t = time.perf_counter()
            code = inference.main(["--data-dir", str(CHIPS), "--output", str(out), "--device", dev])
            e2e.append(time.perf_counter() - t)
            assert code == 0, code
        pred = load_predictor(device=dev)
        pred.predict_proba_many([(arrs[0], BANDS11)])  # warm-up
        t = time.perf_counter()
        probs = pred.predict_proba_many([(a, BANDS11) for a in arrs])
        model_s = time.perf_counter() - t
        assert len(probs) == len(arrs) and all(np.isfinite(p).all() for p in probs)
        res[dev] = {"backend": pred.device, "e2e_s": round(e2e[-1], 3), "e2e_chips_per_s": round(len(files) / e2e[-1], 1),
                    "model_only_s": round(model_s, 3), "model_only_chips_per_s": round(len(files) / model_s, 1),
                    "note": "model_only = features + model on arrays in memory, 1 thread for features"}
    return res


def hardware() -> str:
    cpu = platform.processor()
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "(Get-CimInstance Win32_Processor).Name; (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"],
                           capture_output=True, text=True, timeout=30)
        lines = [s.strip() for s in r.stdout.splitlines() if s.strip()]
        cpu = f"{lines[0]}, {round(int(lines[1]) / 2**30)} GB RAM"
    except Exception:
        pass
    from macroplastic.utils import available_cpus

    gpu = "no GPU"
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            gpu = r.stdout.strip().splitlines()[0].replace(", ", " (driver ") + ")"
    except Exception:
        pass
    return f"{cpu}, {available_cpus()} threads; {gpu}; {platform.system()} {platform.release()}, Python {platform.python_version()}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--chips", type=int, default=300)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--devices", default="cpu,cuda")
    ap.add_argument("--baseline", action="store_true", help="also time git HEAD inference code (1 run per device)")
    ap.add_argument("--baseline-repeats", type=int, default=1)
    ap.add_argument("--no-warm", action="store_true")
    ap.add_argument("--out-json", default=str(ROOT / "reports" / "speed.json"))
    a = ap.parse_args(argv)
    devices = [d for d in a.devices.split(",") if d]
    chips = prepare_chips(a.chips)
    print(f"{len(chips)} chips in {CHIPS}", flush=True)
    runs: dict = {"current": {}, "baseline": {}}
    ref_dir = None
    if a.baseline:
        script = build_baseline()
        for dev in devices:
            runs["baseline"][dev] = []
            for i in range(a.baseline_repeats):
                out = ROOT / "out" / f"speed_base_out_{dev}"
                rec = cold_run(script, dev, out, None)
                runs["baseline"][dev].append(rec)
                print(f"baseline {dev} run {i + 1}: {rec['wall_s']:.2f} s exit={rec['exit']}", flush=True)
                if ref_dir is None and rec["exit"] == 0:
                    ref_dir = ROOT / "out" / "speed_ref"
                    if ref_dir.exists():
                        shutil.rmtree(ref_dir)
                    shutil.copytree(out, ref_dir)
    for dev in devices:
        runs["current"][dev] = []
        for i in range(a.repeats):
            out = ROOT / "out" / f"speed_out_{dev}"
            rec = cold_run(ROOT / "inference.py", dev, out, ROOT / "out" / f"speed_prof_{dev}_{i}.json")
            if ref_dir is None and rec["exit"] == 0:
                ref_dir = ROOT / "out" / "speed_ref"
                if ref_dir.exists():
                    shutil.rmtree(ref_dir)
                shutil.copytree(out, ref_dir)
            if i == 0 and ref_dir is not None:
                rec["equivalence_vs_ref"] = compare(ref_dir, out)
            runs["current"][dev].append(rec)
            print(f"current {dev} run {i + 1}: {rec['wall_s']:.2f} s exit={rec['exit']} "
                  f"{rec.get('log', '')} {rec.get('equivalence_vs_ref', '')}", flush=True)
    if a.baseline:
        for dev in devices:
            rec = runs["baseline"][dev][0]
            if rec["exit"] == 0 and ref_dir is not None:
                rec["equivalence_vs_ref"] = compare(ref_dir, ROOT / "out" / f"speed_base_out_{dev}")
    warm_res = {} if a.no_warm else warm(devices)
    for dev, w in warm_res.items():
        print(f"warm {dev}: {w}", flush=True)

    def med(recs):
        return round(statistics.median(r["wall_s"] for r in recs), 2) if recs else None

    res = {
        "chips": len(chips),
        "cold_s": {dev: med(runs["current"][dev]) for dev in devices},
        "cold_s_max": {dev: round(max(r["wall_s"] for r in runs["current"][dev]), 2) for dev in devices},
        "cold_s_runs": {dev: [r["wall_s"] for r in runs["current"][dev]] for dev in devices},
        "backend": {dev: runs["current"][dev][0].get("profile", {}).get("device") for dev in devices},
        "warm_chips_per_s": max((w["e2e_chips_per_s"] for w in warm_res.values()), default=None),
        "warm": warm_res,
        "baseline_cold_s": {dev: med(runs["baseline"].get(dev, [])) for dev in devices} if a.baseline else None,
        "equivalence": {dev: runs["current"][dev][0].get("equivalence_vs_ref") for dev in devices},
        "hardware": hardware(),
        "command": "python inference.py --data-dir out/speed_chips --output out/speed_out --device {cpu|cuda}",
        "chip_source": "MARIDA val split, first 300 patches (splits/val_X.txt), 256x256x11 float32",
        "cold_definition": "new process, wall from start to exit, 3 runs, median; OS file cache not flushed",
    }
    Path(a.out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out_json).write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    (ROOT / "out" / "speed_runs.json").write_text(json.dumps({"summary": res, "runs": runs}, indent=1, ensure_ascii=False),
                                                  encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("chips", "cold_s", "warm_chips_per_s", "baseline_cold_s", "equivalence", "hardware")},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
