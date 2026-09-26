"""Audit L107: what the v2 first screen shows now (frames at 10/40 s, window.__app keys, visible text, API calls)."""
import json, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright
OUT = Path(__file__).resolve().parent
B = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8096/"
TAG = sys.argv[2] if len(sys.argv) > 2 else "probe"
with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    page = b.new_context(viewport={"width": 1920, "height": 1080}, locale="ru-RU").new_page()
    errs, reqs = [], []
    page.on("console", lambda m: errs.append(m.text[:200]) if m.type == "error" else None)
    page.on("response", lambda r: reqs.append(f"{r.status} {r.url.split('/api/')[1][:90]}") if "/api/" in r.url else None)
    page.goto(B, wait_until="domcontentloaded")
    for t in (10, 40):
        time.sleep(10 if t == 10 else 30)
        page.screenshot(path=str(OUT / f"ui_{TAG}_{t}s.png"))
    app = page.evaluate("() => { const a = window.__app; if (!a) return null; const o = {}; for (const k of Object.keys(a)) { const v = a[k]; o[k] = (typeof v === 'function') ? 'fn' : v; } return JSON.parse(JSON.stringify(o, (k, v) => (typeof v === 'object' && v && Object.keys(v).length > 40) ? '[big]' : v)); }")
    res = {"url": page.url, "app": app, "text": page.inner_text("body")[:2500], "console_errors": errs[:10], "api": reqs[:60]}
    b.close()
(OUT / f"ui_{TAG}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: v for k, v in res.items() if k != "text"}, ensure_ascii=False)[:3000]); print(res["text"][:1500])
