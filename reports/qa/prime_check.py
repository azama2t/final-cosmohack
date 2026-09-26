"""L140 · §54 п.5: проверка PRIME MODE · синтетика (Python Playwright, headless).
.venv\\Scripts\\python.exe reports/qa/prime_check.py http://127.0.0.1:8094 [tag]
Скрины → reports/qa/img/s54/s54_5_prime_<размер>_<шаг>.png; сводка → stdout (JSON)."""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
IMG = ROOT / "reports" / "qa" / "img" / "s54"
IMG.mkdir(parents=True, exist_ok=True)
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8094"
SIZES = [("1366", 1366, 768, False), ("1920", 1920, 1080, False), ("iphone12", 390, 844, True), ("360", 360, 740, True)]

JS_LAYERS = """() => { const m = window.__caseMap; if (!m || !m.getStyle) return null;
  const L = m.getStyle().layers.filter(l => l.id.startsWith('c-') && l.type !== 'raster');
  return { vis: L.filter(l => m.getLayoutProperty(l.id, 'visibility') !== 'none').length, all: L.length,
           nasa: m.getLayer('c-nasa') ? (m.getLayoutProperty('c-nasa', 'visibility') || 'visible') : 'absent' }; }"""
JS_SYNTH = """() => document.querySelectorAll('.prime-panel, .prime-wm, .prime-badge, [data-testid=prime-scene-view]').length"""
JS_SMALL = """() => { const p = document.querySelector('.prime-panel'); if (!p) return null; let t = 0, b = 0;
  for (const e of p.querySelectorAll('*')) { const r = e.getBoundingClientRect(); if (!r.width) continue;
    const own = [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (own && parseFloat(getComputedStyle(e).fontSize) < 14) t++;
    if (e.tagName === 'BUTTON' && (r.height < 44 && r.width < 44)) b++; } return { small_text: t, small_btn: b }; }"""
JS_OVERLAP = """() => { const a = document.querySelector('[data-testid=prime-toggle]'); if (!a) return 'no toggle';
  const r = a.getBoundingClientRect(); const hits = [];
  for (const e of document.querySelectorAll('button, [role=button], a, .c-nasa-note, .c-legend')) { if (a.contains(e) || e.closest('.prime-float')) continue;
    const q = e.getBoundingClientRect(); if (!q.width || getComputedStyle(e).visibility === 'hidden') continue;
    if (q.left < r.right && q.right > r.left && q.top < r.bottom && q.bottom > r.top) hits.push((e.textContent || e.getAttribute('aria-label') || '').trim().slice(0, 30)); }
  return hits; }"""


def main():
    out = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        for tag, w, h, mob in SIZES:
            ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, is_mobile=mob, has_touch=mob)
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
            pg.on("console", lambda m: m.type == "error" and errs.append(m.text[:200]))
            pg.goto(URL + "/")
            pg.wait_for_selector("[data-testid=prime-toggle]", timeout=20000)
            pg.wait_for_timeout(3500)
            r = {"off_synth_elems": pg.evaluate(JS_SYNTH), "layers_off": pg.evaluate(JS_LAYERS),
                 "toggle_overlaps": pg.evaluate(JS_OVERLAP)}
            pg.screenshot(path=str(IMG / f"s54_5_prime_{tag}_1_off.png"))
            # NASA on (desktop: «Слои» → layer-nasa) before PRIME: independence check
            nasa_btn = pg.locator("[data-testid=layer-nasa]")
            if not mob:
                try:
                    pg.get_by_role("button", name="Слои").first.click(timeout=3000)
                    nasa_btn.first.click(timeout=3000)
                    pg.keyboard.press("Escape")
                    pg.wait_for_timeout(1500)
                except Exception as e:  # noqa: BLE001
                    r["nasa_click"] = str(e)[:120]
            pg.wait_for_timeout(1200)
            r["layers_nasa_before"] = pg.evaluate(JS_LAYERS)
            r["toggle_overlaps_nasa_on"] = pg.evaluate(JS_OVERLAP)
            pg.click("[data-testid=prime-toggle]")
            pg.wait_for_selector("[data-testid=prime-scene-view]", timeout=15000)
            pg.wait_for_timeout(1800)
            r["on_badge"] = pg.inner_text("[data-testid=prime-badge]")
            r["on_scenes"] = pg.locator("[data-testid=prime-scene]").count()
            r["on_metrics"] = pg.locator("[data-testid=prime-metrics] li").count()
            r["layers_on"] = pg.evaluate(JS_LAYERS)
            r["imgs_loaded"] = pg.evaluate("() => [...document.querySelectorAll('.prime-imgs img')].map(i => i.naturalWidth)")
            if mob:
                r["small"] = pg.evaluate(JS_SMALL)
            pg.screenshot(path=str(IMG / f"s54_5_prime_{tag}_2_on.png"))
            pg.locator("[data-testid=prime-scene]").nth(1).click()
            pg.wait_for_timeout(1200)
            r["scene2"] = pg.get_attribute("[data-testid=prime-scene-view]", "data-id")
            pg.screenshot(path=str(IMG / f"s54_5_prime_{tag}_3_scene2.png"))
            if mob:
                pg.click("[data-testid=prime-close]")
            else:
                pg.click("[data-testid=prime-toggle]")
            pg.wait_for_timeout(1500)
            r["after_off_synth_elems"] = pg.evaluate(JS_SYNTH)
            r["layers_after_off"] = pg.evaluate(JS_LAYERS)
            pg.screenshot(path=str(IMG / f"s54_5_prime_{tag}_4_off_again.png"))
            r["errors"] = errs
            out[tag] = r
            ctx.close()
        b.close()
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
