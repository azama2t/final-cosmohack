"""Wake rule: zones #1 before/after the rebuild + artefact totals (reads reports/tmp_l41/zones1_before.json, service/data)."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
data = ROOT / "service/data"
sys.stdout.reconfigure(encoding="utf-8")
rj = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
before = {(r[0], r[1], r[2]): r for r in rj(ROOT / "reports/tmp_l41/zones1_before.json")}
man = rj(data / "manifest.json")
tot = {}
changed = []
n = 0
for reg in man["regions"]:
    for d in reg["dates"]:
        for m in d["models"]:
            fc = rj(data / reg["id"] / d["date"] / m / "detections.geojson")
            t = tot.setdefault(m, {"n": 0, "wake": 0, "wake_px": 0, "seam": 0, "ship": 0, "art_px": 0, "px": 0, "conf": 0, "conf_art": 0})
            for f in fc["features"]:
                p = f["properties"]; t["n"] += 1; t["px"] += p.get("n_px", 0)
                a = p.get("artifact")
                if a:
                    t[a] += 1; t["art_px"] += p.get("n_px", 0)
                    if a == "wake": t["wake_px"] += p.get("n_px", 0)
                if p.get("confirmed"):
                    t["conf"] += 1; t["conf_art"] += bool(a)
            z = rj(data / reg["id"] / d["date"] / m / "zones.json")["zones"]
            z1 = z[0] if z else None
            b = before.get((reg["id"], d["date"], m))
            n += 1
            if (b[4] if b else None) != (z1["h3"] if z1 else None):
                changed.append((reg["id"], d["date"], m, d["date"] == reg.get("default_date"), b[4:7] if b else None,
                                (z1["h3"], z1["flagged_water_px"], z1["score"], z1["score_terms"]["agreement"]["value"], z1["n_detections"]) if z1 else None))
print("TOTALS", json.dumps(tot, ensure_ascii=False))
print(f"zone #1 changed in {len(changed)} of {n} (region, date, model)")
for c in changed:
    print(c)
print("\nDEFAULT DATES zone #1 (mdd/lgbm):")
for reg in man["regions"]:
    dd = reg.get("default_date")
    for m in ("mdd", "lgbm"):
        p = data / reg["id"] / dd / m / "zones.json"
        if p.exists():
            z = rj(p)["zones"][:1]
            b = before.get((reg["id"], dd, m))
            if z:
                print(reg["id"], dd, m, "before", b[4:7] if b else None, "after", z[0]["h3"], z[0]["flagged_water_px"], z[0]["score"], z[0]["why"])
print("\nRATING:")
for reg in man["regions"]:
    s = reg.get("summary") or {}
    print(reg["id"], {k: s.get(k) for k in ("index_permille", "n_detections", "haze", "glint_or_haze", "latest_date") if k in s})
