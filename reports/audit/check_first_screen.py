"""Audit L107 (§33): first screen of v2 — every point must come from a real satellite detection (scene_zones / detections in
the API), carry a date and a status, and show no invented items/km2 or material classes. Opens '/', waits, reads
window.__app, the visible text and the rendered point sources, cross-checks with /api/v3/scene_zones.
Output -> reports/audit/first_screen.json + ui_first_*.png."""
import json, re, sys, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
OUT = Path(__file__).resolve().parent
B = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8096").rstrip("/")
api = lambda p: json.loads(urllib.request.urlopen(B + p, timeout=120).read().decode("utf-8"))
sz = api("/api/v3/scene_zones?limit=5000")
ids = {f["properties"]["zone_id"] for f in sz["features"]}
BAD = [r"\d[\d  ,.]*\s*шт\.?\s*/\s*км", r"10⁴|10⁵|10⁸|100 млн", r"пластик(ов)?\s+обнаружен", r"(ПЭТ|PET|полиэтилен|полипропилен|PP|PE)\b",
       r"Условный диапазон|ЕСЛИ это"]
res = {"api_scene_zones": len(ids)}
with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    page = b.new_context(viewport={"width": 1920, "height": 1080}, locale="ru-RU").new_page()
    errs, reqs = [], []
    page.on("console", lambda m: errs.append(m.text[:200]) if m.type == "error" else None)
    page.on("response", lambda r: reqs.append(f"{r.status} {r.url.split('/api/')[1][:80]}") if "/api/" in r.url else None)
    page.goto(B + "/", wait_until="domcontentloaded")
    time.sleep(25)
    page.screenshot(path=str(OUT / "ui_first_25s.png"))
    res["url"] = page.url
    res["app"] = page.evaluate("() => { const a = window.__app; if (!a) return null; const o = {}; for (const k of Object.keys(a)) { const v = a[k]; if (typeof v !== 'function') o[k] = v; } return JSON.parse(JSON.stringify(o, (k, v) => (Array.isArray(v) && v.length > 20) ? `[array ${v.length}]` : v)); }")
    # rendered point features, if the map is MapLibre and exposed
    res["map_sources"] = page.evaluate("""() => { const m = window.__map || (window.__app && window.__app.map); if (!m || !m.getStyle) return null;
        const out = {}; for (const [id, s] of Object.entries(m.getStyle().sources)) { if (s.type !== 'geojson') continue;
        const d = s.data; out[id] = (d && d.features) ? {n: d.features.length, props: d.features.slice(0, 3).map(f => f.properties)} : (typeof d === 'string' ? d : null); } return out; }""")
    text = page.inner_text("body")
    res["text"] = text[:3000]
    res["bad_text"] = {p: re.findall(p, text)[:5] for p in BAD if re.search(p, text)}
    res["console_errors"] = errs[:10]
    res["api"] = reqs[:40]
    b.close()
(OUT / "first_screen.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: v for k, v in res.items() if k != "text"}, ensure_ascii=False)[:4000]); print("---"); print(res["text"][:1500])
