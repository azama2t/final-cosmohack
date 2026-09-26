"""Audit L107: re-check В12 (ships / sand-bank edges labelled «обнаружено · требует проверки») and В13 (stale satellite-scenario
wording in docs). Service :8096 must run the fixed code. Output -> reports/audit/recheck_v12_v13.json."""
import json, re, sys, urllib.request, collections
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
B = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8096"
d = json.load(urllib.request.urlopen(f"{B}/api/v3/scene_zones?limit=5000", timeout=120))
P = {f["properties"]["zone_id"]: f["properties"] for f in d["features"]}
V12 = {"ship": ["SZ-live-guanabara-2026-07-16-010", "SZ-live-karachi-2026-02-14-005", "SZ-live-karachi-2026-02-14-006",
                "SZ-live-karachi-2026-01-15-006"],
       "shoal": ["SZ-drift-guanabara-2025-09-09-007", "SZ-live-guanabara-2025-06-26-004", "SZ-live-guanabara-2025-06-26-005",
                 "SZ-live-guanabara-2026-07-16-005"],
       "unsure": ["SZ-live-manila-2026-04-01-010"]}
out = {"n_zones": len(P), "v12": {}, "v12_fail": [], "v13": {}}
for g, zs in V12.items():
    for z in zs:
        p = P.get(z)
        if p is None:
            out["v12"][z] = "absent"; continue
        out["v12"][z] = {"group": g, "status": p["status"], "flags": p.get("flags"), "label": p.get("detection_label"),
                         "scenario": p.get("scenario"), "conc": p.get("concentration_status")}
        if g != "unsure" and p["status"] == "detected":
            out["v12_fail"].append(z)
        if p.get("scenario") or p.get("concentration_status") == "research_estimate":
            out["v12_fail"].append(z + " (quantity shown)")
c = collections.Counter((p["scene_kind"], p["status"]) for p in P.values())
out["by_kind_status"] = {f"{k[0]}|{k[1]}": v for k, v in sorted(c.items())}
PAT = re.compile(r"диапазон шт\./км² у спутниковой зоны|сценарий шт\./км² — только по формулировке|Спутниковый сценарий шт\./км²|lo 1e4, typical_lo")
for f in ("docs/PIPELINE.md", "docs/CRITERIA_CHECK.md", "docs/CONTRACTS_V3.md", "README.md", "docs/QUANTITY.md", "docs/DEMO.md",
          "docs/SPEECH.md", "docs/QA.md", "reports/report.md"):
    fp = ROOT / f
    if not fp.exists():
        continue
    for i, line in enumerate(fp.read_text(encoding="utf-8").split("\n"), 1):
        m = PAT.search(line)
        if m:
            s = max(0, m.start() - 80)
            out["v13"][f"{f}:{i}"] = {"text": line[s:m.end() + 160], "marked_cancelled": bool(re.search("отмен", line))}
print(json.dumps(out, ensure_ascii=False, indent=1))
(ROOT / "reports/audit/recheck_v12_v13.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
