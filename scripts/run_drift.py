"""L8: 72 h demonstration drift forecast from MDD detections of fresh (2025-2026) live scenes.

Usage (from repo root, PYTHONPATH=src):
  .venv\\Scripts\\python.exe scripts\\run_drift.py --region durban --date 2026-05-04
  .venv\\Scripts\\python.exe scripts\\run_drift.py --all-fresh            # every data/live/<region>/<date>, year >= 2025
Options: --particles 300 --wdf 0.02 --no-ensemble --refresh (re-download forcing) --skip-existing
Writes data/live/<region>/<date>/drift.json, forcing cache data_cache/forcing/<region>_<date>_{currents,wind}.nc,
preview reports/drift_<region>_<date>.png; prints one JSON line per scene and a summary table.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

from macroplastic.drift.run import run_scene  # noqa: E402


def fresh_scenes(root: Path, min_year: int = 2025) -> list[tuple[str, str]]:
    out = []
    for sc in sorted((root / "data" / "live").glob("*/*/scene.json")):
        date = sc.parent.name
        if date[:4].isdigit() and int(date[:4]) >= min_year:
            out.append((sc.parent.parent.name, date))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region")
    ap.add_argument("--date")
    ap.add_argument("--all-fresh", action="store_true")
    ap.add_argument("--particles", type=int, default=300)
    ap.add_argument("--wdf", type=float, default=0.02)
    ap.add_argument("--no-ensemble", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--skip-existing", action="store_true")
    ap.add_argument("--png-dir", default=str(ROOT / "reports"))
    ap.add_argument("--log", default=None, help="append JSON lines with per-scene results to this file")
    a = ap.parse_args(argv)
    try:  # Windows consoles default to cp1251/cp866; the JSON lines contain degree signs etc.
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if a.all_fresh:
        scenes = fresh_scenes(ROOT)
    elif a.region and a.date:
        scenes = [(a.region, a.date)]
    else:
        ap.error("--region and --date, or --all-fresh")
    rows = []
    for region, date in scenes:
        if a.skip_existing and (ROOT / "data" / "live" / region / date / "drift.json").exists():
            print(json.dumps({"region": region, "date": date, "status": "exists"}), flush=True)
            continue
        t = time.time()
        try:
            r = run_scene(ROOT, region, date, n_particles=a.particles, wind_drift_factor=a.wdf,
                          ensemble_factors=() if a.no_ensemble else (0.01, 0.03), refresh=a.refresh,
                          png_dir=Path(a.png_dir))
        except Exception as e:  # keep going for the other scenes
            logging.exception("scene %s %s failed", region, date)
            r = {"region": region, "date": date, "status": f"error: {e!r}"}
        r["runtime_s"] = round(time.time() - t, 1)
        rows.append(r)
        line = json.dumps(r, ensure_ascii=False)
        print(line, flush=True)
        if a.log:
            with open(a.log, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    print("\nregion | date | particles | currents | wind | stranded % | mean path km | mean displ. km | status")
    for r in rows:
        print(f"{r['region']} | {r['date']} | {r.get('n_particles', '-')} | {str(r.get('currents', '-'))[:30]} | "
              f"{str(r.get('wind', '-'))[:30]} | {r.get('stranded_pct', '-')} | {r.get('mean_path_km', '-')} | "
              f"{r.get('mean_displacement_km', '-')} | {r['status']}")
    return 0 if any(r["status"] == "ok" for r in rows) or not rows else 1


if __name__ == "__main__":
    sys.exit(main())
