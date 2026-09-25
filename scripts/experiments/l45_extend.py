"""L45: extend the main drift run (wdf 0.02, same seeds = drift.json path[0], same settings) beyond 72 h for pairs whose
second snapshot is later than 72 h. Forcing (HYCOM ESPC-D-V02 + NCEP GFS only; Open-Meteo/constant fallbacks are
REJECTED here) goes to data_cache/forcing_l45/; drift.json is not touched.

  .venv/Scripts/python.exe scripts/experiments/l45_extend.py --region santo_domingo --date 2026-02-18 --hours 121
  -> out/l45/ext/<region>_<date>.npz (lon, lat (n, hours+1), stranded) + .json (forcing sources, log)
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from macroplastic.drift.forcing import Window, get_forcing, parse_time  # noqa: E402
from macroplastic.drift.run import simulate  # noqa: E402

OUT = ROOT / "out" / "l45" / "ext"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--hours", type=int, required=True)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    sd = ROOT / "data" / "live" / a.region / a.date
    scene = json.loads((sd / "scene.json").read_text(encoding="utf-8"))
    d = json.loads((sd / "drift.json").read_text(encoding="utf-8"))
    start = parse_time(d["start_time"])
    seeds = np.array([p["path"][0][:2] for p in d["particles"]], dtype="float64")
    win = Window.around(scene["bounds_wgs84"], start, after_h=a.hours + 3)
    t = time.time()
    frc = get_forcing(a.region, a.date, win, ROOT / "data_cache" / "forcing_l45")
    cur_nc, cur_name = frc["currents"]
    wind_nc, wind_name = frc["wind"]
    meta = {"region": a.region, "date": a.date, "hours": a.hours, "currents": cur_name, "wind": wind_name,
            "forcing_log": frc["log"], "forcing_s": round(time.time() - t, 1),
            "wdf": d["forcing"]["wind_drift_factor"],
            "diffusivity": d["forcing"].get("horizontal_diffusivity_m2s", 5.0), "n": len(seeds)}
    if "HYCOM" not in cur_name or "NCEP" not in wind_name:
        meta["status"] = "rejected: forcing is not HYCOM+NCEP"
        (OUT / f"{a.region}_{a.date}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(meta, ensure_ascii=False))
        return 2
    t = time.time()
    sim = simulate(seeds, start, cur_nc, wind_nc, meta["wdf"], hours=a.hours,
                   horizontal_diffusivity=meta["diffusivity"])
    meta["sim_s"] = round(time.time() - t, 1)
    meta["status"] = "ok"
    np.savez_compressed(OUT / f"{a.region}_{a.date}.npz", lon=sim["lon"], lat=sim["lat"], stranded=sim["stranded"])
    # consistency with the published 72 h run: mean distance at hour 72 between the two runs
    lon72 = np.array([p["path"][72][0] for p in d["particles"]])
    lat72 = np.array([p["path"][72][1] for p in d["particles"]])
    dx = (sim["lon"][:, 72] - lon72) * 111.32 * math.cos(math.radians(float(np.mean(lat72))))
    dy = (sim["lat"][:, 72] - lat72) * 110.57
    meta["mean_km_vs_drift_json_at_72h"] = round(float(np.hypot(dx, dy).mean()), 2)
    (OUT / f"{a.region}_{a.date}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
