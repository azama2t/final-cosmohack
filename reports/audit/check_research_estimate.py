"""Audit L107 (INBOX §34 п.2): research estimate of items/km2 for satellite zones (PLP calibration, L131).
Rule: N_zone = n_detector_pixels x [470; 670]; C = N_zone / zone_area_km2 (items/km2); interval from the range.
Checks on /api/v3/scene_zones (+ CSV/GeoJSON export, docs):
  C1 (critical) estimate present on a zone that is not a find (insufficient data / not detected / MARIDA-MADOS training image)
     or has a ship / foam / glint / wind / cloud / coast / shallow flag; C1b — API is_find disagrees with status/flags/training;
  C2 (critical) status of the estimate says «измерено»/measured, or concentration_status == measured;
  C3 (important) wording lacks «исследовательская оценка», «PLP», «искусственн…», «не проверена» (natural accumulations);
  C4 (important) numbers disagree with the formula (tolerance 3 %), or items_km2 outside [lo, hi];
  C5 (important) zone area missing / merged into the estimate;
  C6 (important) export CSV / GeoJSON differs from the API for the same zone, or lacks the wording;
  C7 note: finds without an estimate.
Field names follow L132's request (research_estimate {items_km2, lo, hi, n_items_lo, n_items_hi, label, note}); also accepts
value / interval [lo, hi]. Output -> reports/audit/research_estimate.json."""
import csv, io, json, re, sys, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
B = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8070").rstrip("/")
LO_PX, HI_PX = 470.0, 670.0
BAD_FLAGS = {"ship", "foam", "glint", "wind", "cloud", "coast", "shallow", "seam"}
WORDS = {"исследовательская оценка": r"исследовательская оценка", "PLP": r"PLP", "искусственные мишени": r"искусственн",
         "не проверена на природных": r"не провер"}
get = lambda p: json.loads(urllib.request.urlopen(B + p, timeout=180).read().decode("utf-8-sig"))

def est_of(p):
    for k in ("research_estimate", "estimate", "items_estimate"):
        if isinstance(p.get(k), dict):
            return k, p[k]
    return None, None

def nums(e):
    v = e.get("items_km2", e.get("value"))
    lo, hi = e.get("lo"), e.get("hi")
    if (lo is None or hi is None) and isinstance(e.get("interval"), (list, tuple)) and len(e["interval"]) == 2:
        lo, hi = e["interval"]
    return v, lo, hi

sz = get("/api/v3/scene_zones?limit=5000")
res = {"base": B, "n_zones": len(sz["features"]), "with_estimate": 0, "critical": [], "important": [], "notes": [], "sample": []}
by_id = {}
for f in sz["features"]:
    p = f["properties"]; zid = p["zone_id"]; by_id[zid] = p
    k, e = est_of(p)
    flags = set(p.get("flags") or []) | {s for s, v in ((p.get("probable") or {}).get("signs") or {}).items() if isinstance(v, dict) and v.get("flag")}
    rule_find = p.get("status") == "detected" and not (flags & BAD_FLAGS) and not p.get("training_scene")
    is_find = bool(p["is_find"]) if "is_find" in p else rule_find
    if "is_find" in p and bool(p["is_find"]) != rule_find:
        res["important"].append(f"C1b {zid}: is_find={p['is_find']} but status/flags/training say {rule_find} (flags {sorted(flags)}, training {p.get('training_scene')})")
    if e is None:
        if is_find:
            res["notes"].append(f"C7 {zid}: find without estimate")
        continue
    res["with_estimate"] += 1
    if not is_find:
        res["critical"].append(f"C1 {zid}: estimate on status={p.get('status')} flags={sorted(flags)}")
    blob = json.dumps(e, ensure_ascii=False)
    st_txt = " ".join(str(x) for x in (e.get("status"), e.get("status_id"), e.get("kind"), p.get("concentration_status"), p.get("status_label"),
                                        e.get("label"), e.get("label_short")) if x)
    if re.search(r"(?<!не )измерено|(?<!не )измерение|^measured$|measured", st_txt, re.I) or p.get("concentration_status") == "measured":
        res["critical"].append(f"C2 {zid}: «измерено»/measured in estimate or concentration_status")
    miss = [w for w, rx in WORDS.items() if not re.search(rx, blob, re.I)]
    if miss:
        res["important"].append(f"C3 {zid}: wording lacks {miss}")
    v, lo, hi = nums(e)
    m = p.get("measured") or {}
    npx, area = m.get("n_pixels"), m.get("zone_area_km2")
    if area is None:
        res["important"].append(f"C5 {zid}: zone area missing")
    if None in (v, lo, hi):
        res["important"].append(f"C4 {zid}: numbers missing ({v}, {lo}, {hi})")
    elif npx and area:
        elo, ehi = npx * LO_PX / area, npx * HI_PX / area
        bad = [n for n, a, b in (("lo", lo, elo), ("hi", hi, ehi)) if abs(a - b) > 0.03 * b]
        if bad or not (lo <= v <= hi):
            res["important"].append(f"C4 {zid}: api [{lo:.0f}, {v:.0f}, {hi:.0f}] vs formula [{elo:.0f}–{ehi:.0f}] (n_px {npx}, area {area} km²)")
    if len(res["sample"]) < 5:
        res["sample"].append({"zone_id": zid, "field": k, "estimate": e, "n_pixels": npx, "zone_area_km2": area, "status": p.get("status"),
                              "concentration_status": p.get("concentration_status")})
# export
try:
    rows = list(csv.DictReader(io.StringIO(urllib.request.urlopen(B + "/api/v3/export?layer=scene_zones&format=csv", timeout=180).read().decode("utf-8-sig"))))
    cols = list(rows[0].keys()) if rows else []
    ecols = [c for c in cols if re.search(r"research|estimate|items_km2", c) and not c.startswith("field_")]
    res["csv_estimate_cols"] = ecols
    if res["with_estimate"] and not ecols:
        res["important"].append("C6 CSV: no estimate columns")
    for r in rows:
        p = by_id.get(r.get("zone_id"))
        if not p: continue
        _, e = est_of(p)
        if e is None:
            if any((r.get(c) or "").strip() not in ("", "null", "None") and re.search("items_km2|lo|hi", c) for c in ecols):
                res["critical"].append(f"C6 CSV {r['zone_id']}: estimate in CSV but not in API")
            continue
        v, lo, hi = nums(e)
        vals = [float(r[c]) for c in ecols if re.fullmatch(r"-?[\d.]+(e[+-]?\d+)?", (r.get(c) or "").strip())]
        if v is not None and vals and not any(abs(x - v) <= max(1.0, 0.005 * v) for x in vals):
            res["important"].append(f"C6 CSV {r['zone_id']}: value {v} not among CSV numbers {vals[:4]}")
        if not any(re.search("PLP", r.get(c) or "") for c in cols):
            res["important"].append(f"C6 CSV {r['zone_id']}: no PLP wording in row")
            break
    gj = urllib.request.urlopen(B + "/api/v3/export?layer=scene_zones&format=geojson", timeout=180).read().decode("utf-8")
    g = json.loads(gj)
    diff = [f["properties"]["zone_id"] for f in g["features"] if json.dumps(est_of(f["properties"])[1], sort_keys=True) != json.dumps(est_of(by_id.get(f["properties"]["zone_id"], {}))[1], sort_keys=True)]
    if diff:
        res["important"].append(f"C6 GeoJSON: estimate differs from API for {len(diff)} zones, e.g. {diff[:3]}")
except Exception as ex:  # noqa
    res["notes"].append(f"export check failed: {ex!r}"[:300])
res["critical"] = sorted(set(res["critical"])); 
(ROOT / "reports/audit/research_estimate.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: (v if not isinstance(v, list) else (v[:8] + ([f"... {len(v)} total"] if len(v) > 8 else []))) for k, v in res.items()}, ensure_ascii=False, indent=1)[:5000])
