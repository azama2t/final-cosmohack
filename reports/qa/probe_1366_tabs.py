"""QA agent 19: at 1366x768, are the left-panel tabs (Зоны/Метрики) reachable by a human (wheel scroll / keyboard)?"""
import json
import sys

from playwright.sync_api import sync_playwright

base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8093"
stamp = sys.argv[2] if len(sys.argv) > 2 else "probe"
out = {}
with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    for w, h in ((1366, 768), (1920, 1080)):
        page = b.new_page(viewport={"width": w, "height": h}, locale="ru-RU")
        page.goto(base + "/", wait_until="domcontentloaded")
        page.wait_for_function("() => window.__app && window.__app.ready && window.__app.szReady", timeout=120000)
        info = page.evaluate("""() => {
          const t = document.querySelector('[data-testid=tab-zones]');
          const r = t.getBoundingClientRect();
          let p = t.parentElement, chain = [];
          while (p && p !== document.body) { const cs = getComputedStyle(p);
            chain.push({tag: p.tagName, tid: p.dataset.testid || p.className.slice(0,40), oy: cs.overflowY, sh: p.scrollHeight, ch: p.clientHeight});
            p = p.parentElement; }
          return {top: r.top, bottom: r.bottom, vh: innerHeight, docScroll: document.scrollingElement.scrollHeight, chain: chain.slice(0,8)};
        }""")
        # human: wheel over the left panel
        page.mouse.move(150, 500)
        for _ in range(10):
            page.mouse.wheel(0, 400)
            page.wait_for_timeout(150)
        r2 = page.evaluate("() => { const r = document.querySelector('[data-testid=tab-zones]').getBoundingClientRect(); return {top: r.top, bottom: r.bottom}; }")
        page.screenshot(path=f"reports/qa/img/{stamp}_{w}_left_after_wheel.png")
        clicked = None
        try:
            page.click("[data-testid=tab-zones]", timeout=5000)
            clicked = True
        except Exception as e:  # noqa: BLE001
            clicked = repr(e)[:200]
        out[w] = {"before": info, "after_wheel": r2, "click_after_wheel": clicked}
        page.close()
    b.close()
print(json.dumps(out, ensure_ascii=False, indent=1))
