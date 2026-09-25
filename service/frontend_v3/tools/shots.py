"""v3 screenshots (L94): Playwright, 1920x1080 and 1366x768, console errors collected.

.venv\\Scripts\\python.exe service\\frontend_v3\\tools\\shots.py --url http://127.0.0.1:8072 --out reports/screens/v3/iter1
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OBS = "MPL-0779"  # North Sea 2016, S2 scene with RGB + quality + detector mask
OBS2 = "MPL-0919"  # Black Sea 2024


def run(url: str, out: Path, w: int, h: int, log: dict) -> None:
    tag = f"{w}"
    errs: list[str] = []
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--use-angle=d3d11", "--enable-gpu-rasterization", "--ignore-gpu-blocklist"])
        ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
        pg = ctx.new_page()
        pg.on("console", lambda m: m.type == "error" and errs.append(m.text + " @ " + str((m.location or {}).get("url", ""))[:120]))
        pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
        t0 = time.time()
        pg.goto(url + "/?fresh=1", wait_until="domcontentloaded")
        pg.evaluate("localStorage.clear()")
        pg.goto(url + "/?fresh=1", wait_until="domcontentloaded")
        pg.wait_for_function("window.__mapReady === true", timeout=20000)
        t_ready = time.time() - t0
        pg.wait_for_function("window.__map.getLayer('obs-d') && window.__map.queryRenderedFeatures({layers:['obs-d']}).length > 0", timeout=20000)
        marks = pg.evaluate("Object.fromEntries(performance.getEntriesByType('mark').map(m => [m.name, Math.round(m.startTime)]))")
        pg.wait_for_timeout(1500)
        pg.screenshot(path=str(out / f"{tag}_01_first.png"))
        words = pg.evaluate("document.body.innerText.split(/\\s+/).filter(Boolean).length")

        def click_obs(sid: str) -> None:
            pos = pg.evaluate(
                """async (sid) => {
                  const r = await fetch('/api/v3/observations/' + sid); const f = await r.json();
                  const c = f.properties.track_center || f.geometry.coordinates;
                  const m = window.__map; m.jumpTo({center: c, zoom: 10});
                  await new Promise(res => m.once('idle', res));
                  const p = m.project(c); return [p.x, p.y];
                }""",
                sid,
            )
            pg.mouse.click(pos[0], pos[1])
            pg.wait_for_timeout(600)

        click_obs(OBS)
        pg.screenshot(path=str(out / f"{tag}_02_pick.png"))
        pg.click("[data-testid=to-studio]")
        pg.wait_for_timeout(3500)
        pg.screenshot(path=str(out / f"{tag}_03_studio.png"))
        views = pg.locator("[data-testid=views] button")
        n_views = views.count()
        labels = [views.nth(i).inner_text() for i in range(n_views)]
        for i in range(n_views):
            if labels[i] != "Снимок":
                views.nth(i).click()
                pg.wait_for_timeout(1200)
                pg.screenshot(path=str(out / f"{tag}_04_view_{i}.png"))
        pg.click("[data-testid=back]")
        pg.wait_for_timeout(1800)
        pg.screenshot(path=str(out / f"{tag}_05_back.png"))
        pg.click("[data-testid=nav-layers]")
        pg.wait_for_timeout(300)
        pg.screenshot(path=str(out / f"{tag}_06_layers.png"))
        # lasso around a Black Sea transect
        pg.click("[data-testid=nav-sites]")
        pg.evaluate("""async () => { const m = window.__map; m.jumpTo({center: [36.9, 42.45], zoom: 7.5}); await new Promise(r => m.once('idle', r)); }""")
        pg.click("[data-testid=lasso]")
        cx, cy = (w + 360) // 2, h // 2
        pts = [(cx - 160, cy - 110), (cx + 170, cy - 120), (cx + 180, cy + 120), (cx - 150, cy + 130), (cx - 160, cy - 100)]
        pg.mouse.move(*pts[0])
        pg.mouse.down()
        for a, bb in zip(pts, pts[1:]):
            for k in range(1, 9):
                pg.mouse.move(a[0] + (bb[0] - a[0]) * k / 8, a[1] + (bb[1] - a[1]) * k / 8)
        pg.mouse.up()
        pg.wait_for_timeout(500)
        pg.screenshot(path=str(out / f"{tag}_07_lasso.png"))
        if pg.locator("[data-testid=to-studio]").count():
            pg.click("[data-testid=to-studio]")
            pg.wait_for_timeout(3500)
            pg.screenshot(path=str(out / f"{tag}_08_area_studio.png"))
        # a live scene (studio API) via its footprint: Firth of Forth, 26.03.2018
        pg.click("[data-testid=back]")
        pg.wait_for_timeout(800)
        pg.evaluate("""async () => { const m = window.__map; m.jumpTo({center: [-2.40, 56.13], zoom: 10}); await new Promise(r => m.once('idle', r)); }""")
        pg.mouse.click((w + 360) // 2, h // 2)
        pg.wait_for_timeout(600)
        pg.screenshot(path=str(out / f"{tag}_10_scene_pick.png"))
        if pg.locator("[data-testid=to-studio]").count():
            pg.click("[data-testid=to-studio]")
            pg.wait_for_timeout(4000)
            tabs = pg.locator("[data-testid=views] button")
            for i in range(tabs.count()):
                if tabs.nth(i).inner_text() == "Спектральный":
                    tabs.nth(i).click()
                    pg.wait_for_timeout(2500)
            pg.screenshot(path=str(out / f"{tag}_11_scene_studio.png"))
        pg.click("[data-testid=left-toggle]")
        pg.wait_for_timeout(500)
        pg.screenshot(path=str(out / f"{tag}_09_collapsed.png"))
        log[tag] = {"ready_s": round(t_ready, 2), "marks_ms": marks, "first_screen_words": words, "views": labels, "console_errors": errs}
        b.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8072")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    log: dict = {}
    for w, h in ((1920, 1080), (1366, 768)):
        run(a.url, out, w, h, log)
    (out / "shots.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(log, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
