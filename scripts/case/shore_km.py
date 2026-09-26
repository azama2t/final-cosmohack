"""§51 п.6 reproducibility (orchestrator, 26.09 19:30): data_cache/natural_earth/ne_10m_land.shp is gitignored (like
data_cache/forcing) — a clean clone would silently fall back to the coarser 1:110m contour and get different
alert_level numbers. This script pre-computes shore_km/shore_km_reason for every satellite zone find with the 1:10m
contour and writes a small, git-tracked cache: data/case/scene_zones/shore_km.json. src/macroplastic/case/alerts.py
reads this file FIRST; the .shp is only needed to regenerate it.

Run (after scripts/fetch_natural_earth_10m.py):  .venv\\Scripts\\python.exe scripts\\case\\shore_km.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from service import case_store as cs  # noqa: E402
from src.macroplastic.case import alerts as A  # noqa: E402

OUT = ROOT / "data" / "case" / "scene_zones" / "shore_km.json"


def main() -> None:
    land, src = A._land()  # noqa: SLF001 - this script IS the regeneration path for that private cache
    if src != "Natural Earth 1:10m":
        print(f"ВНИМАНИЕ: считаю по «{src}», не по 1:10m — сначала scripts/fetch_natural_earth_10m.py", file=sys.stderr)
    feats = cs.scene_zones_all()
    out: dict[str, dict] = {}
    for f in feats:
        p = f["properties"]
        if not p.get("is_find"):
            continue
        lon, lat = cs._geom_center(f.get("geometry"))  # noqa: SLF001
        km, reason = A.shore_km(lon, lat)
        out[p["zone_id"]] = {"shore_km": km, "shore_km_reason": reason}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"source": src, "zones": out}, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")
    print(f"записано {len(out)} зон -> {OUT} (источник: {src})")


if __name__ == "__main__":
    main()
