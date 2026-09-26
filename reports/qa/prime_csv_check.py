"""L140 · §55 п.2 в: проверка PRIME MODE по строкам CSV (Python Playwright, headless).
.venv\\Scripts\\python.exe reports/qa/prime_csv_check.py http://127.0.0.1:8094
Скрины → reports/qa/img/s55/s55_2_prime_<размер>_<шаг>.png; сводка → stdout (JSON)."""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
IMG = ROOT / "reports" / "qa" / "img" / "s55"
IMG.mkdir(parents=True, exist_ok=True)
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8094"
SIZES = [("1366", 1366, 768, False), ("1920", 1920, 1080, False), ("iphone12", 390, 844, True), ("360", 360, 740, True)]
ONLY = sys.argv[2].split(",") if len(sys.argv) > 2 else None

JS_STATE = """() => { const m = window.__caseMap; const dom = document.querySelectorAll(
  '.prime-panel, .prime-wm, .prime-badge, [data-testid=prime-csv-card], [data-testid=prime-scene-view]').length;
  if (!m || !m.getStyle) return { dom, map: null };
  const L = m.getStyle().layers;
  const real = L.filter(l => l.id.startsWith('c-') && l.type !== 'raster');
  const src = m.getSource('prime-csv');
  return { dom, prime_layers: L.filter(l => l.id.startsWith('prime-')).map(l => l.id), prime_src: !!src,
    prime_feats: src && src._data && src._data.features ? src._data.features.length : null,
    real_visible: real.filter(l => m.getLayoutProperty(l.id, 'visibility') !== 'none').length, real_all: real.length,
    nasa: m.getLayer('c-nasa') ? (m.getLayoutProperty('c-nasa', 'visibility') || 'visible') : 'absent',
    badge_visible: (() => { const b = document.querySelector('[data-testid=prime-badge]'); if (!b) return false;
      const r = b.getBoundingClientRect(); return r.width > 0 && r.bottom > 0 && r.top < innerHeight; })() }; }"""
JS_PT = """() => { const m = window.__caseMap; const f = m.queryRenderedFeatures({ layers: ['prime-csv-pt'] });
  if (!f.length) return null; const c = m.project(f[0].geometry.coordinates); const r = m.getContainer().getBoundingClientRect();
  // prefer a point not under the panel
  const pan = document.querySelector('.prime-wrap'); const pr = pan ? pan.getBoundingClientRect() : null;
  for (const g of f) { const p = m.project(g.geometry.coordinates); const x = r.left + p.x, y = r.top + p.y;
    if (x < r.left + 5 || x > r.right - 5 || y < r.top + 5 || y > r.bottom - 5) continue;
    if (pr && x >= pr.left && x <= pr.right && y >= pr.top && y <= pr.bottom) continue;
    const top = document.elementFromPoint(x, y); if (top && top.tagName !== 'CANVAS') continue;
    return { x, y, id: g.properties.id }; } return null; }"""
JS_SMALL = """() => { const p = document.querySelector('.prime-panel'); if (!p) return null; let t = 0, b = 0;
  for (const e of p.querySelectorAll('*')) { const r = e.getBoundingClientRect(); if (!r.width) continue;
    if (e.closest('svg')) continue;
    const own = [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (own && parseFloat(getComputedStyle(e).fontSize) < 14) t++;
    if (e.tagName === 'BUTTON' && (r.height < 44 && r.width < 44)) b++; } return { small_text: t, small_btn: b }; }"""


def shot(pg, tag, step):
    pg.screenshot(path=str(IMG / f"s55_2_prime_{tag}_{step}.png"))


def main():
    out = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        for tag, w, h, mob in SIZES:
            if ONLY and tag not in ONLY:
                continue
            ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, is_mobile=mob, has_touch=mob)
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
            pg.on("console", lambda m: m.type == "error" and errs.append(m.text[:200]))
            pg.goto(URL + "/")
            pg.wait_for_selector("[data-testid=prime-toggle]", state="attached", timeout=20000)
            pg.wait_for_timeout(3500)
            # a real selection before PRIME: first scene in the list (desktop) — must survive PRIME on/off
            if not mob:
                try:
                    pg.locator("[data-testid=scene-item]").first.click(timeout=4000)
                    pg.wait_for_timeout(3000)
                except Exception as e:  # noqa: BLE001
                    pass
            JS_CAM = "() => { const m = window.__caseMap; const c = m.getCenter(); return [location.search, +c.lng.toFixed(3), +c.lat.toFixed(3), +m.getZoom().toFixed(2), document.querySelector('[data-testid=scene-title]')?.textContent || null]; }"
            r = {"off": pg.evaluate(JS_STATE), "before": pg.evaluate(JS_CAM)}
            shot(pg, tag, "1_off")
            try:
                pg.locator("[data-testid=prime-toggle]").click(timeout=4000)
            except Exception:  # noqa: BLE001 — floating toggle hidden (phone card open) → programmatic via URL
                pg.goto(URL + "/?prime=1")
            pg.wait_for_selector("[data-testid=prime-csv-list], .prime-err", timeout=15000)
            pg.wait_for_timeout(2500)
            r["on"] = pg.evaluate(JS_STATE)
            r["on_small"] = pg.evaluate(JS_SMALL)
            shot(pg, tag, "2_on")
            # map click on a magenta point → card
            pt = pg.evaluate(JS_PT)
            r["map_point"] = pt
            if pt:
                pg.mouse.click(pt["x"], pt["y"])
                pg.wait_for_timeout(1200)
            card = pg.locator("[data-testid=prime-csv-card]")
            r["card_from_map"] = card.count() > 0 and card.first.get_attribute("data-id")
            if not card.count():
                pg.locator("[data-testid=prime-csv-item]").nth(3).click()
                pg.wait_for_timeout(1500)
                r["card_from_list"] = card.first.get_attribute("data-id") if card.count() else None
            pg.wait_for_timeout(1200)
            r["card_text"] = card.first.inner_text()[:400] if card.count() else None
            r["card_small"] = pg.evaluate(JS_SMALL)
            shot(pg, tag, "3_card")
            r["demo_val"] = pg.locator("[data-testid=prime-demo-val]").count()
            r["found_line"] = pg.locator("[data-testid=prime-csv-found]").count()
            if pg.locator("[data-testid=prime-csv-metrics-open]").count():
                pg.locator("[data-testid=prime-csv-metrics-open]").first.click()
                pg.wait_for_timeout(600)
                pg.locator("[data-testid=prime-csv-metrics]").first.scroll_into_view_if_needed()
                r["curve"] = pg.locator("[data-testid=prime-csv-curve]").count()
                shot(pg, tag, "4_experiment")
            pg.locator("[data-testid=prime-min]").click()
            pg.wait_for_timeout(800)
            r["badge_when_minimised"] = pg.evaluate(JS_STATE)["badge_visible"]
            shot(pg, tag, "5_minimised")
            pg.locator("[data-testid=prime-close]").click()
            pg.wait_for_timeout(1500)
            r["off_again"] = pg.evaluate(JS_STATE)
            r["after"] = pg.evaluate(JS_CAM)
            r["place_kept"] = r["after"] == r["before"]
            shot(pg, tag, "6_off_again")
            r["errors"] = errs
            out[tag] = r
            ctx.close()
        b.close()
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
