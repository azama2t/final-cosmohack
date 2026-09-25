"""Refresh region_name (ru), region_name_en, country, marida_dates in data/live/*/*/scene.json from stac.REGIONS.
  python scripts/relabel_live_regions.py [--root data/live]"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from macroplastic.live import stac  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT / "data" / "live"))
    a = ap.parse_args()
    for sj in sorted(Path(a.root).glob("*/*/scene.json")):
        s = json.loads(sj.read_text(encoding="utf-8"))
        reg = stac.REGIONS.get(s["region"])
        if not reg:
            continue
        s.update(region_name=reg["region_name"], region_name_en=reg.get("region_name_en"), country=reg.get("country"),
                 marida_dates=reg.get("marida_dates"), marida_md_px_tile=reg.get("md_px"))
        sj.write_text(json.dumps(s, indent=1, ensure_ascii=False), encoding="utf-8")
        print(sj.parent.parent.name, sj.parent.name, s["region_name"])


if __name__ == "__main__":
    main()
