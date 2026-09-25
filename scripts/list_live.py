"""Print a table of live scenes in data/live/<region>/<date>/ (markdown).  python scripts/list_live.py [--json]"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rows():
    out = []
    for sj in sorted((ROOT / "data" / "live").glob("*/*/scene.json")):
        d = sj.parent
        s = json.loads(sj.read_text(encoding="utf-8"))
        r = dict(region=s["region"], date=s["date"], scene_id=s["scene_id"], tile=s["tile"],
                 tile_cc=s.get("cloud_cover"), crop_cloud=s.get("crop_cloud_frac"), water=s.get("water_frac"),
                 size=f"{s['width']}x{s['height']}", baseline=s.get("processing_baseline"))
        for m in ("mdd", "lgbm"):
            pj = d / f"prob_{m}.json"
            if pj.exists():
                p = json.loads(pj.read_text(encoding="utf-8"))
                r[f"{m}_px"] = p.get("n_above_threshold_water", p.get("n_above_threshold"))
                r[f"{m}_thr"] = round(p["threshold"], 4)
                r[f"{m}_s"] = p.get("runtime_s")
            else:
                r[f"{m}_px"] = r[f"{m}_thr"] = r[f"{m}_s"] = "-"
        out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rs = rows()
    if a.json:
        print(json.dumps(rs, indent=1))
        return
    if not rs:
        print("no scenes in data/live")
        return
    cols = list(rs[0])
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for r in rs:
        print("| " + " | ".join(str(r[c]) for c in cols) + " |")


if __name__ == "__main__":
    main()
