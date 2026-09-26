"""QA agent 19: hit every /api/v3 route of a running service, record code/time/size/schema,
and compare API numbers with reports/final_numbers.json of the checked repo.

usage: python reports/qa/api_check.py --base http://127.0.0.1:8093 --repo <repo root> --out <json>
Only stdlib. Read-only against the service except a saved query that is created and deleted again.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROWS: list[dict] = []
CHECKS: list[dict] = []


def req(base, method, path, body=None, ctype=None, timeout=300):
    url = base + path
    data = body
    r = urllib.request.Request(url, data=data, method=method)
    if ctype:
        r.add_header("Content-Type", ctype)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            code, hdr = resp.status, dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw, code, hdr = e.read(), e.code, dict(e.headers)
    except Exception as e:  # noqa: BLE001
        raw, code, hdr = str(e).encode(), -1, {}
    ms = (time.perf_counter() - t0) * 1000
    return code, raw, hdr, ms


def rec(method, path, code, raw, hdr, ms, expect=200, schema_ok=True, note=""):
    ok = (code == expect) and schema_ok
    ROWS.append({"method": method, "path": path, "code": code, "expect": expect, "ms": round(ms, 1),
                 "bytes": len(raw), "ctype": hdr.get("content-type", hdr.get("Content-Type", "")),
                 "schema_ok": schema_ok, "ok": ok, "note": note})
    return ok


def js(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def get(base, path, expect=200, check=None, note=""):
    code, raw, hdr, ms = req(base, "GET", path)
    obj = js(raw) if "json" in (hdr.get("content-type", "") or hdr.get("Content-Type", "")) else None
    sok, n2 = True, note
    if check is not None and code == expect:
        try:
            r = check(obj if obj is not None else raw)
            if r is False:
                sok = False
            elif isinstance(r, str):
                n2 = (note + " " + r).strip()
        except Exception as e:  # noqa: BLE001
            sok, n2 = False, f"{note} schema: {type(e).__name__}: {e}"[:300]
    if code != expect and obj is not None and isinstance(obj, dict) and "error" in obj:
        n2 = (n2 + " err=" + json.dumps(obj["error"], ensure_ascii=False)[:200]).strip()
    rec("GET", path, code, raw, hdr, ms, expect, sok, n2)
    return code, obj, raw


def cmp(name, api, ref, tol=1e-6):
    ok = api is not None and ref is not None and (
        abs(float(api) - float(ref)) <= tol if isinstance(ref, (int, float)) and not isinstance(ref, bool)
        else api == ref)
    CHECKS.append({"name": name, "api": api, "final_numbers": ref, "ok": bool(ok)})
    return ok


def fc(o):
    assert o["type"] == "FeatureCollection", o.get("type")
    assert isinstance(o["features"], list)
    return f"n={len(o['features'])}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8093")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    base, repo = a.base.rstrip("/"), Path(a.repo)
    fn = json.loads((repo / "reports/final_numbers.json").read_text(encoding="utf-8"))
    sec = fn["case"]["sections"]

    get(base, "/health")
    code, oapi, _ = get(base, "/openapi.json", check=lambda o: f"paths={len(o['paths'])}")
    get(base, "/docs")
    get(base, "/")
    v3paths = sorted(p for p in (oapi or {}).get("paths", {}) if p.startswith("/api/v3"))

    _, meta, _ = get(base, "/api/v3/meta", check=lambda o: "summary" in json.dumps(o) or False)
    _, obs, _ = get(base, "/api/v3/observations", check=fc)
    if obs and obs.get("features"):
        sid = obs["features"][0].get("id") or obs["features"][0]["properties"].get("sample_id")
        get(base, f"/api/v3/observations/{sid}")
    get(base, "/api/v3/observations?source=S2_SARGASSO_MSM41&limit=5", check=fc)
    get(base, "/api/v3/observations?bbox=abc", expect=400)
    _, pairs, _ = get(base, "/api/v3/pairs")
    _, scenes, _ = get(base, "/api/v3/scenes")
    sc_list = (scenes or {}).get("scenes") or (scenes or {}).get("features") or []
    if sc_list:
        s0 = next((x for x in sc_list if x.get("preview_url") and x.get("mask_url")), sc_list[0])
        scid = s0.get("scene_id") or s0.get("id") or (s0.get("properties") or {}).get("scene_id")
        get(base, f"/api/v3/scenes/{scid}")
        for k in ("rgb.png", "quality.png", "mask.png"):
            get(base, f"/api/v3/scenes/{scid}/{k}", check=lambda r: r[:4] == b"\x89PNG" or False)
    _, zones, _ = get(base, "/api/v3/zones", check=fc)
    if zones and zones.get("features"):
        get(base, f"/api/v3/zones/{zones['features'][0]['id']}")
    _, metrics, _ = get(base, "/api/v3/metrics")

    # exports: every layer x format
    counts = {}
    for layer in ("observations", "pairs", "zones", "detections", "scene_zones"):
        for f in ("geojson", "csv"):
            def chk(r, f=f, layer=layer):
                if f == "csv":
                    txt = r.decode("utf-8-sig") if isinstance(r, bytes) else json.dumps(r)
                    n = sum(1 for _ in csv.reader(io.StringIO(txt))) - 1
                    counts[(layer, f)] = n
                    return f"rows={n}"
                o = r if isinstance(r, dict) else json.loads(r.decode("utf-8"))
                counts[(layer, f)] = len(o["features"])
                return fc(o)
            get(base, f"/api/v3/export?layer={layer}&format={f}", check=chk)
    get(base, "/api/v3/export?layer=nope", expect=400)
    for layer in ("observations", "pairs", "zones", "detections", "scene_zones"):
        if (layer, "csv") in counts and (layer, "geojson") in counts:
            cmp(f"export {layer}: csv rows == geojson features", counts[(layer, "csv")], counts[(layer, "geojson")])

    # saved queries round trip
    qname = f"qa19-{uuid.uuid4().hex[:6]}"
    body = json.dumps({"name": qname, "query": {"date_from": "2021-03-01", "date_to": "2021-03-31"}}).encode()
    code, raw, hdr, ms = req(base, "POST", "/api/v3/queries", body, "application/json")
    o = js(raw) or {}
    qid = (o.get("query_id") or o.get("id") or (o.get("query") or {}).get("id") if isinstance(o, dict) else None)
    rec("POST", "/api/v3/queries", code, raw, hdr, ms, 201, qid is not None, f"id={qid}")
    get(base, "/api/v3/queries")
    if qid:
        get(base, f"/api/v3/queries/{qid}")
        get(base, f"/api/v3/queries/{qid}/run")
        get(base, f"/api/v3/export?query_id={qid}&layer=scene_zones&format=csv")
        code, raw, hdr, ms = req(base, "DELETE", f"/api/v3/queries/{qid}")
        rec("DELETE", f"/api/v3/queries/{qid}", code, raw, hdr, ms, 204)

    # scene zones
    _, sz, _ = get(base, "/api/v3/scene_zones?limit=100000", check=fc)
    _, szs, _ = get(base, "/api/v3/scene_zones/scenes")
    zid = sec.get("scene_zones", {}).get("example", {}).get("zone_id")
    _, z1, _ = get(base, f"/api/v3/scene_zones/{zid}") if zid else (None, None, None)
    get(base, "/api/v3/scene_zones/NOPE", expect=404)
    if szs and szs.get("scenes"):
        s = next((x for x in szs["scenes"] if "demo" in x["scene_key"]), szs["scenes"][0])
        for nm in ("rgb.jpg", "quality.png"):
            get(base, f"/api/v3/scene_zones/scenes/{s['scene_key']}/{nm}")
        if zid:
            get(base, f"/api/v3/scene_zones/scenes/{s['scene_key']}/crops/{zid}.jpg")

    # photo
    _, pmeta, _ = get(base, "/api/v3/photo/meta")
    samples = sorted((repo / "service/frontend_v2/public/photo_samples").glob("*.jpg"))
    photo = {}
    for p in samples[:2]:
        for qs in ("", "?frame_area_m2=4"):
            img = p.read_bytes()
            bnd = "qa19" + uuid.uuid4().hex
            mp = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{p.name}\"\r\n"
                  f"Content-Type: image/jpeg\r\n\r\n").encode() + img + f"\r\n--{bnd}--\r\n".encode()
            code, raw, hdr, ms = req(base, "POST", f"/api/v3/photo/count{qs}", mp, f"multipart/form-data; boundary={bnd}")
            o = js(raw) or {}
            sok = code == 200 and isinstance(o.get("count"), int) and isinstance(o.get("boxes"), list)
            if qs and sok:
                sok = o.get("density") is not None
            note = f"{p.name} count={o.get('count')} density={(o.get('density') or {}).get('items_per_km2')}" if code == 200 \
                else f"{p.name} err={json.dumps(o.get('error'), ensure_ascii=False)[:200]}"
            rec("POST", f"/api/v3/photo/count{qs}", code, raw, hdr, ms, 200, sok, note)
            photo[p.name + qs] = o.get("count")
    code, raw, hdr, ms = req(base, "POST", "/api/v3/photo/count", b"", "image/jpeg")
    rec("POST", "/api/v3/photo/count (empty)", code, raw, hdr, ms, 400)
    code, raw, hdr, ms = req(base, "POST", "/api/v3/photo/count", b"notanimage", "image/jpeg")
    rec("POST", "/api/v3/photo/count (garbage)", code, raw, hdr, ms, 415)

    # oil
    get(base, "/api/v3/oil/meta")
    get(base, "/api/v3/oil/scenes")
    get(base, "/api/v3/oil/spills", check=fc)
    get(base, "/api/v3/oil/export?format=geojson", check=fc)
    get(base, "/api/v3/oil/export?format=csv")

    # studio
    _, st, _ = get(base, "/api/v3/studio/scenes")
    if st and st.get("scenes"):
        s = st["scenes"][0]
        get(base, f"/api/v3/studio/scenes/{s['id']}")
        for kind, v in (s.get("views") or {}).items():
            if isinstance(v, dict) and v.get("available") is False:
                continue
            get(base, f"/api/v3/studio/scenes/{s['id']}/view/{kind}.png", check=lambda r: r[:4] == b"\x89PNG" or False)

    # pairfinder
    body = json.dumps({"geometry": {"type": "Point", "coordinates": [30.93, 43.07]},
                       "datetime": "2024-06-02T09:00:00Z"}).encode()
    code, raw, hdr, ms = req(base, "POST", "/api/v3/pairfinder", body, "application/json")
    o = js(raw) or {}
    rec("POST", "/api/v3/pairfinder", code, raw, hdr, ms, 200, "candidates" in o,
        f"count={o.get('count')} n_sync={o.get('n_synchronous')}")
    code, raw, hdr, ms = req(base, "POST", "/api/v3/pairfinder", b"", "application/json")
    rec("POST", "/api/v3/pairfinder (empty)", code, raw, hdr, ms, 400)
    get(base, "/api/v3/nope", expect=404)

    # ---------------- numbers vs final_numbers
    mt = sec["marida_test"]
    def leaves(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                yield from leaves(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                yield from leaves(v, f"{path}[{i}]")
        elif isinstance(o, (int, float)) and not isinstance(o, bool):
            yield path, o

    def has_num(obj, target, nd):
        hits = [p for p, v in leaves(obj) if round(float(v), nd) == round(float(target), nd)]
        return hits[:3]

    def numcheck(name, obj, target, nd):
        hits = has_num(obj, target, nd) if obj is not None else []
        CHECKS.append({"name": name, "api": hits or None, "final_numbers": target, "ok": bool(hits)})

    mt = sec["marida_test"]
    numcheck("metrics: MARIDA test LightGBM F1", metrics, mt["lgbm_f1"], 3)
    numcheck("metrics: MARIDA test RF F1", metrics, mt["rf_f1"], 3)
    ft = sec["field_test"]
    for prof in ("S2", "S1"):
        for key in ("main_mae", "median_mae"):
            numcheck(f"metrics: field_test.{prof}.{key}", metrics, ft[prof][key], 1)
    szs_fn = sec.get("scene_zones", {})
    if sz:
        feats = sz["features"]
        cmp("scene_zones total", sz.get("total", len(feats)), szs_fn.get("n_zones"))
        st_counts, ver = {}, {}
        for f in feats:
            p = f["properties"]
            st_counts[p.get("status")] = st_counts.get(p.get("status"), 0) + 1
            if p.get("status") == "detected":
                v = p.get("verification")
                v = v.get("level") if isinstance(v, dict) else v
                ver[str(v)] = ver.get(str(v), 0) + 1
        CHECKS.append({"name": "scene_zones by status (API) vs final_numbers",
                       "api": {"status": st_counts, "detected_verification": ver},
                       "final_numbers": {k: szs_fn.get(k) for k in ("by_level_b", "by_unverified", "by_insufficient",
                                                                    "by_not_detected", "by_not_informative")},
                       "ok": st_counts.get("detected", 0) == szs_fn.get("by_level_b", 0) + szs_fn.get("by_unverified", 0)
                       and st_counts.get("not_detected", 0) == szs_fn.get("by_not_detected")
                       and st_counts.get("insufficient_data", 0) == szs_fn.get("by_insufficient")
                       and st_counts.get("not_informative", 0) == (szs_fn.get("by_not_informative") or 0)})
        n_cz = sum(1 for f in feats if (f["properties"].get("n_cozar_filaments") or 0) > 0)
        cmp("scene_zones with Cózar filament == by_level_b", n_cz, szs_fn.get("by_level_b"))
        demo = [f for f in feats if "demo" in (f["properties"].get("scene_key") or "")]
        cmp("demo scene zones", len(demo), szs_fn.get("demo", {}).get("n_zones"))
        cmp("demo scene zones with Cózar", sum(1 for f in demo if (f["properties"].get("n_cozar_filaments") or 0) > 0),
            szs_fn.get("demo", {}).get("n_zones_cozar"))
        if ("scene_zones", "csv") in counts:
            cmp("export scene_zones csv rows == n_zones", counts[("scene_zones", "csv")], szs_fn.get("n_zones"))
        qs = {json.dumps(f["properties"].get("quantity"), ensure_ascii=False)[:120] for f in feats}
        CHECKS.append({"name": "no scene zone has items/km2 scenario (quantity)", "api": sorted(qs)[:3],
                       "final_numbers": szs_fn.get("zone_main_status"),
                       "ok": not any("items_per_km2" in q or "шт./км²\": " in q for q in qs)})
    if z1:
        m = z1.get("properties", {}).get("measured", {})
        ex = szs_fn.get("example", {})
        for k_fn, k_api, nd in (("area_km2", "zone_area_km2", 2), ("lwd_m2_km2", "lwd_m2_km2", 0),
                                ("n_px", "n_pixels", 0), ("susp_m2", "suspicious_area_m2", 0)):
            api_v = m.get(k_api)
            cmp(f"example zone {k_fn}", None if api_v is None else round(float(api_v), nd), ex.get(k_fn))
        cmp("example zone model sha256_short", (m.get("model") or {}).get("sha256_short"), ex.get("sha256_short"))
    if meta:
        numcheck("meta: 29 strips", meta, 29, 0)
        numcheck("meta: 12 quality-passed", meta, 12, 0)

    out = {"base": base, "repo": str(repo), "when": time.strftime("%Y-%m-%d %H:%M:%S"),
           "v3_openapi_paths": v3paths, "rows": ROWS, "checks": CHECKS,
           "n_rows": len(ROWS), "n_fail": sum(1 for r in ROWS if not r["ok"]),
           "n_checks_fail": sum(1 for c in CHECKS if not c["ok"]), "photo": photo}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    covered = {r["path"].split("?")[0] for r in ROWS}
    print(f"rows {len(ROWS)} fail {out['n_fail']} | checks {len(CHECKS)} fail {out['n_checks_fail']}")
    for r in ROWS:
        if not r["ok"]:
            print("FAIL", r["method"], r["path"], r["code"], r["note"][:200])
    for c in CHECKS:
        if not c["ok"]:
            print("NUM-MISMATCH", c["name"], "api=", str(c["api"])[:150], "fn=", str(c["final_numbers"])[:150])
    slow = sorted(ROWS, key=lambda r: -r["ms"])[:5]
    print("slowest:", [(r["path"][:60], r["ms"]) for r in slow])
    print("openapi v3 paths:", len(v3paths))


if __name__ == "__main__":
    main()
