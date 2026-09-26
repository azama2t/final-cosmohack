"""Audit L107: re-check K2 after the L111 fix. Fetches /api/v3/scene_zones from the auditor service (:8096, restart it on
the fixed code first) and reports, for the 12 zones flagged in K2 plus every zone: status, concentration_status,
scenario.shown, flags, training-set mark. PASS for K2 = none of the 10 ship/shore zones shows a scenario or
research_estimate, and no zone outside Cózar-B evidence shows a scenario."""
import json, sys, urllib.request, collections
B = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8096"
d = json.load(urllib.request.urlopen(f"{B}/api/v3/scene_zones?limit=5000", timeout=120))
P = {f["properties"]["zone_id"]: f["properties"] for f in d["features"]}
SHIP = ["SZ-drift-guanabara-2025-06-28-007", "SZ-drift-guanabara-2025-09-09-014", "SZ-drift-guanabara-2025-09-09-015",
        "SZ-live-guanabara-2025-06-26-007", "SZ-live-guanabara-2025-09-04-020", "SZ-live-guanabara-2026-07-16-011",
        "SZ-live-manila-2019-05-13-010"]
SHORE = ["SZ-live-durban-2025-09-26-002", "SZ-live-durban-2025-09-26-004", "SZ-live-guanabara-2025-09-04-013"]
OTHER = ["SZ-drift-guanabara-2025-09-09-001", "SZ-live-mumbai-2025-01-31-004", "SZ-live-haiti-2021-01-03-001"]
out = {"n_zones": len(P), "zones": {}, "fail": []}
for z in SHIP + SHORE + OTHER:
    p = P.get(z)
    if p is None:
        out["zones"][z] = "absent (zone ids may have been renumbered)"
        continue
    r = {k: p.get(k) for k in ("status", "concentration_status", "flags", "detection_label")}
    r["scenario_shown"] = (p.get("scenario") or {}).get("shown")
    r["training"] = {k: v for k, v in p.items() if "train" in k.lower() or "marida" in k.lower()}
    out["zones"][z] = r
    if z in SHIP + SHORE and (r["scenario_shown"] or r["concentration_status"] == "research_estimate"):
        out["fail"].append(z)
c = collections.Counter()
for p in P.values():
    shown = (p.get("scenario") or {}).get("shown")
    cz = p.get("n_cozar_filaments") or 0
    c[(p["scene_kind"], p["status"], bool(shown), cz > 0)] += 1
    if shown and cz == 0:
        out["fail"].append(p["zone_id"] + " (scenario without Cózar-B)")
out["by_kind_status_scenario_cozar"] = {"|".join(map(str, k)): v for k, v in sorted(c.items())}
print(json.dumps(out, ensure_ascii=False, indent=1))
json.dump(out, open("reports/audit/recheck_k2.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
