"""Jury-human: check provenance/dates/quantity/classes of satellite zones via the public API of :8070."""
import json, urllib.request, collections, sys, re
B = "http://localhost:8070/api/v3"
d = json.load(urllib.request.urlopen(B + "/scene_zones", timeout=60))
feats = d.get("features") if isinstance(d, dict) else d
print("top keys:", list(d.keys())[:20] if isinstance(d, dict) else "list")
print("n zones:", len(feats))
props = [x.get("properties", x) for x in feats]
print(json.dumps(props[0], ensure_ascii=False)[:3000])
keys = collections.Counter(k for p in props for k in p)
print("keys:", keys.most_common(100))
for k in keys:
    vals = collections.Counter(json.dumps(p.get(k), ensure_ascii=False)[:90] for p in props)
    if len(vals) <= 12:
        print("  ", k, vals.most_common(12))
# red flags: any number next to шт./км² or class names
bad = []
for p in props:
    s = json.dumps(p, ensure_ascii=False)
    for m in re.finditer(r"[^\"]{0,60}шт\./км²[^\"]{0,40}", s):
        bad.append((p.get("zone_id") or p.get("id"), m.group(0)))
    for w in ["бутыл", "bottle", "пакет", "сеть ", "PET"]:
        if w in s: bad.append((p.get("zone_id") or p.get("id"), "class:" + w))
print("шт./км² / class mentions:", collections.Counter(b[1] for b in bad).most_common(15))
json.dump({"n": len(feats), "keys": keys.most_common(), "flags": bad[:200]}, open(sys.argv[1] if len(sys.argv) > 1 else "api_check.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
