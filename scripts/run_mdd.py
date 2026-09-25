"""Run marinedebrisdetector on live scene folders -> prob_mdd.tif / prob_mdd.json.

GPU only via the queue:
  .venv/Scripts/python.exe scripts/gpu_queue.py submit --name mdd --wait -- .venv/Scripts/python.exe scripts/run_mdd.py data/live/honduras/2025-04-05
  scripts/run_mdd.py --all            # every data/live/*/*/ without prob_mdd.tif
  scripts/run_mdd.py --all --force    # recompute all
  --ckpt models/mdd/unet1/...ckpt --out-name prob_mdd_unet   (other checkpoints)
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from macroplastic.live import mdd  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenes", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--ckpt", default=str(mdd.DEFAULT_CKPT))
    ap.add_argument("--device", default=None)
    ap.add_argument("--out-name", default="prob_mdd")
    a = ap.parse_args()
    scenes = [Path(s) for s in a.scenes]
    if a.all:
        scenes += sorted(p.parent for p in (ROOT / "data" / "live").glob("*/*/bands.tif"))
    rc = 0
    for s in scenes:
        s = s if s.is_absolute() else ROOT / s
        if (s / f"{a.out_name}.tif").exists() and not a.force and a.all:
            continue
        try:
            prob, meta = mdd.run_scene(s, a.ckpt, a.device, a.out_name)
            print(f"{s}: {meta['height']}x{meta['width']} {meta['runtime_s']} s on {meta['device']}, "
                  f"thr={meta['threshold']:.4f}, above={meta['n_above_threshold']} "
                  f"(water {meta['n_above_threshold_water']})", flush=True)
        except Exception as e:  # keep going over other scenes
            print(f"FAILED {s}: {e!r}", flush=True)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
