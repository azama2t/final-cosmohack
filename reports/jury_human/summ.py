import json, sys
for f in sys.argv[1:]:
    r = json.load(open(f, encoding="utf-8"))["res"]
    print("=====", f)
    for k, v in r["tasks"].items():
        v = dict(v)
        for drop in ["card_head"]:
            if drop in v: v[drop] = v[drop][:25]
        print("--", k, json.dumps(v, ensure_ascii=False)[:2500])
    print("errors", r.get("errors")[:10])
