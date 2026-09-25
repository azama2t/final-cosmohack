"""L28: compare two service data builds (detections, area, confirmed per model; Honduras 2026-05-30 top zones)."""
import json
import sys
from pathlib import Path


def summary(root: Path):
    man = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    tot = {}
    for r in man["regions"]:
        for d in r["dates"]:
            for m in d["models"]:
                fc = json.loads((root / r["id"] / d["date"] / m / "detections.geojson").read_text(encoding="utf-8"))
                t = tot.setdefault(m, [0, 0.0, 0])
                t[0] += len(fc["features"])
                t[1] += sum(f["properties"]["area_m2"] for f in fc["features"])
                t[2] += sum(bool(f["properties"].get("confirmed")) for f in fc["features"])
    return tot


def zones(root: Path, region="honduras", date="2026-05-30", m="mdd"):
    z = json.loads((root / region / date / m / "zones.json").read_text(encoding="utf-8"))["zones"][:4]
    return [(x["rank"], x["h3"], x["flagged_water_px"], x.get("n_confirmed")) for x in z]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for p in sys.argv[1:]:
        print(p, {m: [v[0], round(v[1] / 1e4, 2), v[2]] for m, v in summary(Path(p)).items()}, "(n, ha, confirmed)")
        print("  honduras 2026-05-30 mdd top zones:", zones(Path(p)))
