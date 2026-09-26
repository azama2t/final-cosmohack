"""L140 · §60 А1: PRIME вкл → выбрать демо-точку на карте → PRIME выкл → камера и реальное состояние как до PRIME.
.venv\\Scripts\\python.exe reports/qa/prime_off_check.py [URL]   (по умолчанию http://127.0.0.1:8094)
Два сценария на 1366/1920/390: «globe» (стартовый глобус) и «scene» (сначала открыт реальный снимок S2).
Скрины → reports/qa/img/s60/a1_<размер>_<сценарий>_{0_before,1_prime_sel,2_after}.png; сводка → stdout (JSON)."""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
IMG = ROOT / "reports" / "qa" / "img" / "s60"
IMG.mkdir(parents=True, exist_ok=True)
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8094"
SIZES = [("1366", 1366, 768, False), ("1920", 1920, 1080, False), ("390", 390, 844, True)]

SNAP = """() => { const m = window.__caseMap; if (!m) return null;
  const c = m.getCenter(), p = m.getPadding(), cv = m.getCanvas(), pc = m.project(c);
  const ls = (m.getStyle().layers || []).map(l => l.id);
  return { lng: +c.lng.toFixed(4), lat: +c.lat.toFixed(4), zoom: +m.getZoom().toFixed(3), bearing: +m.getBearing().toFixed(2), pitch: +m.getPitch().toFixed(2),
    padding: [p.top, p.right, p.bottom, p.left].map(Math.round),
    center_px_off: [Math.round(pc.x - cv.clientWidth / 2), Math.round(pc.y - cv.clientHeight / 2)],
    scene_layers: ls.filter(i => /^c-scene|^c-sz|^c-zone/.test(i)).length,
    scene_src: ls.filter(i => /^c-scene-(rgb|q)|^cs-/.test(i)).sort().join(','),
    step: (document.querySelector('.steps .on, [aria-current=step]')?.textContent || '').trim().slice(0, 30),
    right: (document.querySelector('.right h2, .right h3, [data-testid=scene-title]')?.textContent || '').trim().slice(0, 50),
    prime_layers: ls.filter(i => i.startsWith('prime-')).length,
    prime_panel: !!document.querySelector('[data-testid=prime-panel]'),
    prime_card: !!document.querySelector('[data-testid=prime-csv-card]'),
    body_prime: document.body.classList.contains('prime-on'),
    hscroll: document.documentElement.scrollWidth > innerWidth + 1 }; }"""

# screen position of one demo point that is inside the visible canvas (not under the panel)
PT = """() => { const m = window.__caseMap; if (!m || !m.getLayer('prime-csv-pt')) return null;
  const cv = m.getCanvas().getBoundingClientRect(); const pan = document.querySelector('.prime-wrap')?.getBoundingClientRect();
  const fs = m.queryRenderedFeatures({ layers: ['prime-csv-pt'] });
  for (const f of fs) { const q = m.project(f.geometry.coordinates); const x = cv.left + q.x, y = cv.top + q.y;
    if (x < cv.left + 20 || x > cv.right - 20 || y < cv.top + 20 || y > cv.bottom - 20) continue;
    if (pan && x > pan.left - 10 && x < pan.right + 10 && y > pan.top - 10 && y < pan.bottom + 10) continue;
    const el = document.elementFromPoint(x, y); if (!el || el.tagName !== 'CANVAS') continue;
    return { x, y, id: f.properties.id }; }
  return null; }"""


def close(a, b):
    """camera identical up to rounding"""
    return (abs(a["lng"] - b["lng"]) < 0.01 and abs(a["lat"] - b["lat"]) < 0.01 and abs(a["zoom"] - b["zoom"]) < 0.01
            and a["padding"] == b["padding"] and abs(a["bearing"] - b["bearing"]) < 0.1 and abs(a["pitch"] - b["pitch"]) < 0.1)


def main():
    out = {}
    with sync_playwright() as p:
        br = p.chromium.launch()
        for tag, w, h, mob in SIZES:
            for scen in ("globe", "scene"):
                ctx = br.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, is_mobile=mob, has_touch=mob)
                pg = ctx.new_page()
                errs, res404 = [], []
                pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
                pg.on("console", lambda m: m.type == "error" and (res404 if "Failed to load resource" in m.text else errs).append(m.text[:160]))
                pg.goto(URL + "/")
                pg.wait_for_timeout(4500)
                r = {}
                if scen == "scene":
                    try:  # the archive list loads slowly («Загрузка снимков…»)
                        pg.wait_for_selector("[data-testid=scene-item]", state="attached", timeout=40000)
                    except Exception:
                        pass
                    r["scene_key"] = pg.evaluate("() => { const b = document.querySelector('[data-testid=scene-item]'); if (!b) return null; b.click(); return b.dataset.scene; }")
                    pg.wait_for_timeout(4000)
                r["before"] = before = pg.evaluate(SNAP)
                pg.screenshot(path=str(IMG / f"a1_{tag}_{scen}_0_before.png"))
                pg.evaluate("() => (document.querySelector('[data-testid=prime-toggle-inline]') || document.querySelector('[data-testid=prime-toggle]')).click()")
                pg.wait_for_timeout(3500)  # demo index + fitBounds of the demo points
                pt = pg.evaluate(PT)
                r["demo_point"] = pt
                if pt:
                    pg.mouse.click(pt["x"], pt["y"])
                else:  # no free point on screen → the list row (same flyToScene with padding)
                    pg.evaluate("() => document.querySelector('[data-testid=prime-csv-item]')?.click()")
                pg.wait_for_timeout(2000)
                r["prime_sel"] = pg.evaluate(SNAP)
                pg.screenshot(path=str(IMG / f"a1_{tag}_{scen}_1_prime_sel.png"))
                # the bug path: PRIME off right after picking a demo point
                pg.evaluate("() => (document.querySelector('[data-testid=prime-close]') || document.querySelector('[data-testid^=prime-toggle]')).click()")
                pg.wait_for_timeout(2500)
                r["after"] = after = pg.evaluate(SNAP)
                pg.screenshot(path=str(IMG / f"a1_{tag}_{scen}_2_after.png"))
                r["ok"] = {
                    "demo_selected": bool(r["prime_sel"] and r["prime_sel"]["prime_card"]),
                    "camera_restored": bool(before and after and close(before, after)),
                    "padding_zero": bool(after and after["padding"] == [0, 0, 0, 0]),
                    "globe_centered": bool(after and max(map(abs, after["center_px_off"])) <= 2),
                    "demo_cleared": bool(after and not after["prime_panel"] and not after["prime_card"] and after["prime_layers"] == 0 and not after["body_prime"]),
                    "real_state_kept": bool(before and after and after["scene_layers"] == before["scene_layers"]
                                            and after["scene_src"] == before["scene_src"] and after["right"] == before["right"]
                                            and after["step"] == before["step"]),
                }
                r["errors"] = errs
                r["failed_resources"] = len(res404)
                out[f"{tag}_{scen}"] = r
                ctx.close()
        br.close()
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
