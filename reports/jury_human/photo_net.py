"""Capture network for photo example click."""
import os, sys
from playwright.sync_api import sync_playwright
W = int(sys.argv[1]) if len(sys.argv) > 1 else 1366
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": W, "height": 768})
    reqs = []
    pg.on("request", lambda r: reqs.append(f"REQ {r.method} {r.url}") if "/api/" in r.url else None)
    def onresp(r):
        if "/api/" in r.url and "photo" in r.url:
            try: reqs.append(f"RESP {r.status} {r.url} {r.text()[:400]}")
            except Exception as e: reqs.append(f"RESP {r.status} {r.url} ?{e}")
    pg.on("response", onresp)
    pg.goto("http://localhost:8070"); pg.wait_for_load_state("networkidle")
    pg.click("button:has-text('Фото')"); pg.wait_for_timeout(1500)
    pg.click("text=Пример 1"); pg.wait_for_timeout(15000)
    print("\n".join(r for r in reqs if "photo" in r))
    b.close()
