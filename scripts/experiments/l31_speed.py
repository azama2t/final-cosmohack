"""L31: cold-start speed of inference.py, final model vs mid-size k20_t400_l63, one interleaved series.

    .venv/Scripts/python.exe scripts/gpu_queue.py submit --name l31_speed --wait -- \
        .venv/Scripts/python.exe scripts/experiments/l31_speed.py [--repeats 3]

Chips: out/speed_chips (first 300 MARIDA val patches, measure_speed.prepare_chips; test never read).
CPU with N threads = the process affinity mask limited to the first N logical CPUs (like `start /affinity`, as in
docs/CRITERIA.md); child processes inherit it, inference.py sees N CPUs (utils.available_cpus) -> lib_lightgbm
uses N threads and the pool min(N, 8) workers (default --workers). GPU = --device cuda, all CPUs.
Order per repeat: for every config the two models back to back, the order of the pair alternates between
repeats. Wall = process start to exit (OS file cache not flushed). Outputs are compared: every run vs the
first run of the same model (must be identical; CPU vs CUDA too).
Writes weights_exp/l31/speed.json.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import measure_speed as MS  # noqa: E402

MODELS = {"final": ROOT / "weights" / "lgbm", "k20_t400_l63": ROOT / "weights_exp" / "l31" / "k20_t400_l63_s0"}


def set_affinity(n: int | None) -> None:
    k = ctypes.windll.kernel32
    k.GetCurrentProcess.restype = ctypes.c_void_p
    k.GetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    h = k.GetCurrentProcess()
    proc, system = ctypes.c_size_t(), ctypes.c_size_t()
    k.GetProcessAffinityMask(h, ctypes.byref(proc), ctypes.byref(system))
    mask = system.value if n is None else ((1 << n) - 1) & system.value
    if not k.SetProcessAffinityMask(h, ctypes.c_size_t(mask)):
        raise OSError("SetProcessAffinityMask failed")


def run(model: str, device: str, out: Path, prof: Path) -> dict:
    if out.exists():
        shutil.rmtree(out)
    cmd = [sys.executable, str(ROOT / "inference.py"), "--data-dir", str(MS.CHIPS), "--output", str(out),
           "--device", device, "--model", "lgbm", "--weights", str(MODELS[model]), "--profile-json", str(prof)]
    t = time.perf_counter()
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    wall = time.perf_counter() - t
    rec = {"model": model, "device": device, "wall_s": round(wall, 3), "exit": r.returncode}
    if prof.exists():
        p = json.loads(prof.read_text(encoding="utf-8"))
        rec["profile"] = {k: p.get(k) for k in ("device", "workers", "total_s", "wall_s", "thread_s") if k in p}
    if r.returncode != 0:
        rec["stderr"] = r.stderr[-800:]
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--configs", default="cpu:4,cpu:8,cpu:20,cuda:20")
    a = ap.parse_args()
    chips = MS.prepare_chips(300)
    configs = [(c.split(":")[0], int(c.split(":")[1])) for c in a.configs.split(",")]
    ref: dict[str, Path] = {}
    runs = []
    work = ROOT / "out" / "l31_speed"
    work.mkdir(parents=True, exist_ok=True)
    for rep in range(a.repeats):
        for dev, n in configs:
            order = list(MODELS) if rep % 2 == 0 else list(MODELS)[::-1]
            for model in order:
                set_affinity(None if dev == "cuda" else n)
                out = work / f"out_{model}_{dev}{n}"
                rec = run(model, dev, out, work / f"prof_{model}_{dev}{n}_{rep}.json")
                set_affinity(None)
                rec.update(threads=n, repeat=rep)
                if rec["exit"] == 0:
                    if model not in ref:
                        ref[model] = work / f"ref_{model}"
                        if ref[model].exists():
                            shutil.rmtree(ref[model])
                        shutil.copytree(out, ref[model])
                        rec["ref_from"] = f"{dev}{n}"
                    elif rep == 0:
                        rec["equivalence_vs_first"] = MS.compare(ref[model], out)
                runs.append(rec)
                print(f"rep{rep} {dev}{n:>3} {model:>13}: {rec['wall_s']:6.2f} s exit={rec['exit']} "
                      f"backend={rec.get('profile', {}).get('device')} {rec.get('equivalence_vs_first', '')}",
                      flush=True)
    summ = {}
    for model in MODELS:
        summ[model] = {}
        for dev, n in configs:
            w = [r["wall_s"] for r in runs if r["model"] == model and r["device"] == dev and r["threads"] == n
                 and r["exit"] == 0]
            key = "gpu" if dev == "cuda" else f"cpu{n}"
            summ[model][key] = {"median_s": round(statistics.median(w), 2) if w else None, "runs": w}
    ratio = {k: round(summ["final"][k]["median_s"] / summ["k20_t400_l63"][k]["median_s"], 3)
             for k in summ["final"] if summ["final"][k]["median_s"] and summ["k20_t400_l63"][k]["median_s"]}
    res = {"chips": len(chips), "summary": summ, "speedup_final_over_mid": ratio, "hardware": MS.hardware(),
           "method": __doc__.split("\n\n")[1:3], "runs": runs}
    (ROOT / "weights_exp" / "l31" / "speed.json").write_text(json.dumps(res, indent=1, ensure_ascii=False),
                                                              encoding="utf-8")
    print(json.dumps({"summary": {m: {k: v["median_s"] for k, v in s.items()} for m, s in summ.items()},
                      "speedup": ratio}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
