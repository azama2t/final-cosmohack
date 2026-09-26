"""L140 mobile acceptance: iPhone 12 / Pixel 5 / 360x800 / 768 (touch) + desktop smoke 1366/1920.
Path: open -> scene -> zone -> card -> back -> export. Screens -> reports/qa/mobile/. Prints one line per device.
Usage: .venv\\Scripts\\python.exe reports/qa/mobile_check.py [base_url] [tag]"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8094"
TAG = sys.argv[2] if len(sys.argv) > 2 else "run"
OUT = Path(__file__).resolve().parents[2] / "reports" / "qa" / "mobile"
OUT.mkdir(parents=True, exist_ok=True)

MEASURE = """() => {
  const vw = document.documentElement.clientWidth;
  const hscroll = document.documentElement.scrollWidth > vw + 1 || document.body.scrollWidth > vw + 1;
  const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    if (!(r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < vw)) return false;
    const x = Math.min(vw - 1, Math.max(0, r.left + r.width / 2)), y = Math.min(innerHeight - 1, Math.max(0, r.top + Math.min(r.height / 2, 10)));
    const t = document.elementFromPoint(x, y); return !!t && (t === el || el.contains(t) || t.contains(el)); };
  const small = []; const tiny = [];
  for (const el of document.querySelectorAll('button, a.btn, [role=button], select, input:not([type=hidden]):not([type=file])')) {
    if (!vis(el) || el.closest('.maplibregl-ctrl-attrib, .maplibregl-ctrl, .maplibregl-marker')) continue;
    if (el.matches('input[type=checkbox], input[type=radio]') && el.closest('label') && el.closest('label').getBoundingClientRect().height >= 43.5) continue;
    const r = el.getBoundingClientRect();
    if (r.height < 43.5 || r.width < 43.5) small.push((el.dataset.testid || el.className || el.tagName) + ':' + Math.round(r.width) + 'x' + Math.round(r.height));
  }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n; const seen = new Set();
  while ((n = walker.nextNode())) {
    const el = n.parentElement; if (!el || seen.has(el) || !n.textContent.trim()) continue; seen.add(el);
    if (!vis(el) || el.closest('.maplibregl-ctrl-attrib, .maplibregl-marker, .maplibregl-popup, svg')) continue;
    const fs = parseFloat(getComputedStyle(el).fontSize);
    if (fs > 0 && fs < 13.9 && !el.closest('.attrib, .maplibregl-ctrl-scale')) tiny.push((el.dataset.testid || el.className || el.tagName) + ':' + fs);
  }
  const over = [];
  for (const el of document.querySelectorAll('.left-body, .right, .c-filters, .menu, .c-modal-b')) { if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX !== 'hidden') over.push((el.className || '').toString().slice(0, 30) + ':' + el.scrollWidth + '>' + el.clientWidth); }
  return { vw, hscroll, small: [...new Set(small)].slice(0, 12), nSmall: small.length, tiny: [...new Set(tiny)].slice(0, 12), nTiny: tiny.length, over };
}"""


def run(p, name, ctx_args, mobile=True):
    b = p.chromium.launch()
    ctx = b.new_context(**ctx_args)
    page = ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.text[:160]) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append("PAGEERR " + str(e)[:160]))
    res = {"device": name, "steps": [], "measures": {}}

    def shot(step):
        page.screenshot(path=str(OUT / f"{TAG}_{name}_{step}.png"), scale="css")

    def meas(step):
        res["measures"][step] = page.evaluate(MEASURE)

    def tap(sel):
        loc = page.locator(sel).first
        loc.wait_for(state="visible", timeout=15000)
        if mobile:
            loc.tap()
        else:
            loc.click()

    try:
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.wait_for_selector('[data-testid="case-app"]', timeout=30000)
        page.wait_for_selector('[data-testid="scene-item"]', timeout=30000)
        page.wait_for_timeout(2500)
        shot("01_open"); meas("01_open"); res["steps"].append("open")
        if mobile and page.locator('[data-testid="m-sheet-handle"]').count():
            tap('[data-testid="m-sheet-handle"]')
            page.wait_for_timeout(500)
            shot("02_sheet"); meas("02_sheet"); res["steps"].append("sheet")
        tap('[data-testid="scene-item"]')
        page.wait_for_selector('[data-testid="sz-item"]', timeout=20000)
        page.wait_for_timeout(1500)
        shot("03_scene"); meas("03_scene"); res["steps"].append("scene")
        tap('[data-testid="sz-item"]')
        page.wait_for_selector('[data-testid="scene-zone-card"]', timeout=20000)
        page.wait_for_timeout(1500)
        shot("04_card"); meas("04_card"); res["steps"].append("card")
        tabs = page.locator('[data-testid="scene-zone-card"] [role="tab"]')
        if tabs.count():  # §47 п.4: tabs in the sheet scroll sideways, every tab reachable
            res["tabs"] = tabs.count()
            res["tablist_scroll"] = page.evaluate("() => { const t = document.querySelector('[data-testid=\"scene-zone-card\"] [role=tablist]'); return t ? t.scrollWidth + '/' + t.clientWidth : null; }")
            tabs.last.scroll_into_view_if_needed()
            if mobile:
                tabs.last.tap()
            else:
                tabs.last.click()
            page.wait_for_timeout(600)
            shot("04b_tab_last"); meas("04b_tab_last"); res["steps"].append("tabs")
            tabs.first.scroll_into_view_if_needed()
            tabs.first.tap() if mobile else tabs.first.click()
            page.wait_for_timeout(300)
        if mobile:
            page.locator('[data-testid="scene-zone-card"]').evaluate("e => { const s = e.closest('.right') || e; s.scrollTop = s.scrollHeight; }")
            page.wait_for_timeout(300)
            shot("05_card_bottom"); meas("05_card_bottom")
        back = '[data-testid="m-card-back"]' if mobile and page.locator('[data-testid="m-card-back"]').count() else '[data-testid="sz-back"]'
        tap(back)
        page.wait_for_timeout(1200)
        vis = page.locator('[data-testid="scene-zone-card"]').count()
        res["card_after_back"] = vis
        shot("06_back"); meas("06_back"); res["steps"].append("back")
        tap('[data-testid="act-export"]')
        page.wait_for_selector('[data-testid="export-menu"]', timeout=10000)
        page.wait_for_timeout(400)
        shot("07_export"); meas("07_export"); res["steps"].append("export")
        page.keyboard.press("Escape")
        tap('[data-testid="act-export"]')
        page.wait_for_timeout(300)
        if mobile and page.locator('[data-testid="m-filters"]').count():
            tap('[data-testid="m-filters"]')
            page.wait_for_timeout(500)
            shot("08_filters"); meas("08_filters"); res["steps"].append("filters")
            tap('[data-testid="m-sheet-close"]')
            page.wait_for_timeout(300)
        tap('[data-testid="act-qc"]')
        page.wait_for_timeout(1500)
        shot("08b_qc"); meas("08b_qc"); res["steps"].append("qc")
        tap('[data-testid="qc-close"]')
        page.wait_for_timeout(300)
        if mobile and page.locator('[data-testid="m-legend"]').count():
            tap('[data-testid="m-legend"]')
            page.wait_for_timeout(400)
            shot("09_legend"); res["steps"].append("legend")
            tap('[data-testid="m-legend"]')
        if page.locator('[data-testid="act-more"]').count():  # §47 п.1 «Ещё ▾»
            tap('[data-testid="act-more"]')
            page.wait_for_selector('[data-testid="more-menu"]', timeout=5000)
            page.wait_for_timeout(300)
            shot("11_more"); meas("11_more"); res["steps"].append("more")
            tap('[data-testid="act-more"]')
            page.wait_for_timeout(300)
        if page.locator('[data-testid="timeline-open"]').count() or page.locator('[data-testid="timeline"]').count():  # §48 timeline
            res["timeline_default_open"] = page.locator('[data-testid="timeline"]').count() > 0
            if not res["timeline_default_open"]:
                tap('[data-testid="timeline-open"]')
                page.wait_for_selector('[data-testid="timeline"]', timeout=5000)
            page.wait_for_timeout(500)
            shot("12_timeline"); meas("12_timeline"); res["steps"].append("timeline")
            if mobile:
                tap('[data-testid="timeline-close"]')
                page.wait_for_timeout(300)
        if page.locator('[data-testid="demo-tour-btn"]').count():  # §47 п.7 «Демо ▶»
            tap('[data-testid="demo-tour-btn"]')
            page.wait_for_selector('[data-testid="demo-tour-card"]', timeout=15000)
            page.wait_for_timeout(2500)
            shot("13_demo"); meas("13_demo"); res["steps"].append("demo")
            tap('[data-testid="demo-tour-exit"]')
            page.wait_for_timeout(500)
    except Exception as e:  # noqa: BLE001
        res["fail"] = str(e).splitlines()[0][:200]
        shot("99_fail")
    try:  # «Фото»: sample → result with boxes fits the width; camera button present on mobile
        page.goto(BASE + "/?mode=photo", wait_until="domcontentloaded")
        page.wait_for_selector('[data-testid="photo-sample"]', timeout=20000)
        tap('[data-testid="photo-sample"]')
        page.wait_for_timeout(6000)
        shot("10_photo"); meas("10_photo"); res["steps"].append("photo")
        res["photo_cam_visible"] = page.locator('[data-testid="photo-camera"]').is_visible()
        res["photo_img_w"] = page.evaluate("() => { const i = document.querySelector('.ph-img'); return i ? Math.round(i.getBoundingClientRect().width) + '/' + document.documentElement.clientWidth : null; }")
    except Exception as e:  # noqa: BLE001
        res["photo_fail"] = str(e).splitlines()[0][:200]
        shot("99_photo_fail")
    res["console_errors"] = errs[:8]
    ctx.close()
    b.close()
    return res


def main():
    with sync_playwright() as p:
        devs = p.devices
        ip, px = dict(devs["iPhone 12"]), dict(devs["Pixel 5"])
        ip.pop("default_browser_type", None)
        px.pop("default_browser_type", None)
        cfgs = [
            ("iphone12", ip, True),
            ("pixel5", px, True),
            ("w360", dict(viewport={"width": 360, "height": 800}, device_scale_factor=2, is_mobile=True, has_touch=True), True),
            ("w768", dict(viewport={"width": 768, "height": 1024}, device_scale_factor=2, is_mobile=True, has_touch=True), True),
            ("d1366", dict(viewport={"width": 1366, "height": 768}), False),
            ("d1920", dict(viewport={"width": 1920, "height": 1080}), False),
        ]
        only = set(sys.argv[3].split(",")) if len(sys.argv) > 3 else None
        allres = []
        for name, args, mob in cfgs:
            if only and name not in only:
                continue
            r = run(p, name, args, mob)
            allres.append(r)
            ms = r["measures"]
            worst = {k: (v["hscroll"], v["nSmall"], v["nTiny"]) for k, v in ms.items()}
            print(name, "steps=" + ",".join(r["steps"]), "FAIL=" + r.get("fail", "-"), "errs=%d" % len(r["console_errors"]),
                  "back_card=%s" % r.get("card_after_back"), "photo=%s/%s/%s" % (r.get("photo_cam_visible"), r.get("photo_img_w"), r.get("photo_fail", "-")), "tabs=%s:%s tl_open=%s" % (r.get("tabs"), r.get("tablist_scroll"), r.get("timeline_default_open")), json.dumps(worst, ensure_ascii=False))
        (OUT / f"{TAG}_result.json").write_text(json.dumps(allres, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
