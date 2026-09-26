"""L140 · §58 п.3: проверка блока «NASA · ежедневный обзор» в левой колонке (Python Playwright, headless).
.venv\\Scripts\\python.exe reports/qa/nasa_block_check.py http://127.0.0.1:8094
Скрины → reports/qa/img/s58/s58_3_nasa_<размер>_<шаг>.png; сводка → stdout (JSON)."""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
IMG = ROOT / "reports" / "qa" / "img" / "s58"
IMG.mkdir(parents=True, exist_ok=True)
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8094"
SIZES = [("1366", 1366, 768, False), ("1920", 1920, 1080, False), ("390", 390, 844, True)]

JS = """() => { const m = window.__caseMap; const L = m && m.getLayer('c-nasa');
  const b = document.querySelector('[data-testid=nasa-block]');
  return { layer: !!L, minzoom: L ? L.minzoom : null, vis: L ? (m.getLayoutProperty('c-nasa','visibility') || 'visible') : null,
    zoom: m ? +m.getZoom().toFixed(2) : null, tiles: L ? m.getSource('c-nasa').tiles[0].slice(-60) : null,
    block: b ? (b.classList.contains('off') ? 'off' : 'on') : null, block_h: b ? Math.round(b.getBoundingClientRect().height) : 0,
    right_panel: !!document.querySelector('[data-testid=nasa-note]'),
    date: document.querySelector('[data-testid=nasa-date]')?.value || null,
    hint: !!document.querySelector('[data-testid=nasa-zoomhint]'),
    tl_nasa: document.querySelectorAll('[data-testid=timeline] [class*=nasa]').length,
    hscroll: document.documentElement.scrollWidth > innerWidth + 1 }; }"""


def main():
    out = {}
    with sync_playwright() as p:
        br = p.chromium.launch()
        for tag, w, h, mob in SIZES:
            ctx = br.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, is_mobile=mob, has_touch=mob)
            pg = ctx.new_page()
            errs, res404 = [], []
            pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
            pg.on("console", lambda m: m.type == "error" and (res404 if "Failed to load resource" in m.text else errs).append(m.text[:160]))
            pg.goto(URL + "/")
            pg.wait_for_timeout(4000)
            r = {"start": pg.evaluate(JS)}
            # NASA on: top button (desktop) / more menu (phone)
            try:
                pg.locator("[data-testid=nasa-toggle]").first.click(timeout=10000)
            except Exception:  # noqa: BLE001
                try:
                    pg.get_by_text("Ещё").first.click(timeout=3000)
                    pg.locator("[data-testid=more-nasa]").first.click(timeout=3000)
                except Exception as e:  # noqa: BLE001
                    r["toggle_err"] = str(e)[:120]
            pg.wait_for_timeout(4000)
            if mob:  # the left column is the bottom sheet on the phone: open it
                for sel in ["[data-testid=m-sheet-handle]", ".m-sheet-handle", "[data-testid=sheet-toggle]"]:
                    if pg.locator(sel).count():
                        pg.locator(sel).first.click()
                        pg.wait_for_timeout(800)
                        break
            r["globe"] = pg.evaluate(JS)
            pg.screenshot(path=str(IMG / f"s58_3_nasa_{tag}_1_globe.png"))
            # regional map: Mediterranean, zoom 5
            pg.evaluate("() => window.__caseMap.jumpTo({ center: [5, 40], zoom: 5 })")
            pg.wait_for_timeout(5000)
            r["regional"] = pg.evaluate(JS)
            pg.screenshot(path=str(IMG / f"s58_3_nasa_{tag}_2_regional.png"))
            if pg.locator("[data-testid=nasa-prev]").count():
                d0 = pg.evaluate(JS)["date"]
                pg.locator("[data-testid=nasa-prev]").first.click()
                pg.wait_for_timeout(600)
                mod = pg.locator("[data-testid^=nasa-layer-modis]")
                if mod.count():
                    mod.first.click()
                pg.wait_for_timeout(3500)
                r["prev_modis"] = pg.evaluate(JS)
                r["date_changed"] = r["prev_modis"]["date"] != d0
                pg.screenshot(path=str(IMG / f"s58_3_nasa_{tag}_3_prev_modis.png"))
                # open a Sentinel-2 scene: the NASA date must stay as it is (not linked)
                if not mob and pg.locator("[data-testid=scene-item]").count():
                    pg.locator("[data-testid=scene-item]").first.click()
                    pg.wait_for_timeout(3500)
                    r["after_scene"] = pg.evaluate(JS)
                    r["date_independent"] = r["after_scene"]["date"] == r["prev_modis"]["date"]
                    pg.screenshot(path=str(IMG / f"s58_3_nasa_{tag}_4_scene.png"))
                pg.locator("[data-testid=nasa-off]").first.click()
                pg.wait_for_timeout(1500)
                r["off"] = pg.evaluate(JS)
                pg.screenshot(path=str(IMG / f"s58_3_nasa_{tag}_5_off.png"))
            r["errors"] = errs
            r["failed_resources"] = len(res404)
            out[tag] = r
            ctx.close()
        br.close()
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
