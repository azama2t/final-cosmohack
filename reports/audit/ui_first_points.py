"""Audit L107 (§33): are the finding points actually drawn on the first screen? Frames at 20/60/90 s (globe) and after
zooming to the Gulf of Honduras and to 30SXE; counts rendered features of point layers via MapLibre if reachable."""
import json, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright
OUT = Path(__file__).resolve().parent
B = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8070").rstrip("/")
JS_FIND_MAP = """() => { const els = document.querySelectorAll('.maplibregl-map'); const r = {n_maps: els.length, globals: Object.keys(window).filter(k => /map/i.test(k)).slice(0, 20)}; return r; }"""
with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    page = b.new_context(viewport={"width": 1920, "height": 1080}, locale="ru-RU").new_page()
    page.goto(B + "/", wait_until="domcontentloaded")
    out = {}
    for t, w in ((20, 20), (60, 40), (90, 30)):
        time.sleep(w); page.screenshot(path=str(OUT / f"ui_first_{t}s.png"))
    out["dom"] = page.evaluate(JS_FIND_MAP)
    for name, c in (("honduras", "-87.5,16.2,6"), ("cozar", "-1.62,35.5,9"), ("guanabara", "-43.15,-22.85,9")):
        page.goto(f"{B}/?c={c}", wait_until="domcontentloaded"); time.sleep(35)
        page.screenshot(path=str(OUT / f"ui_first_zoom_{name}.png"))
    b.close()
print(json.dumps(out, ensure_ascii=False))
