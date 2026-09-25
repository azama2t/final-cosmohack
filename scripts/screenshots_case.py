"""L66: case-mode (API v3) screenshots + smoke checks for the v2 frontend (service/static_v2, default mode «Кейс»).

Usage (repo root; own service instance, never :8000):
  .venv\\Scripts\\python.exe -m service --port 8070 --data-root service\\data
  .venv\\Scripts\\python.exe scripts\\screenshots_case.py --base-url http://127.0.0.1:8070 --out reports\\screens\\case_v2\\iter1 --offline
--offline aborts every request that is not to --base-url (external tiles fail → offline basemap; the local API works).

Frames (1920×1080 unless noted): 01_overview 02_source 03_zone_detected 04_zone_na_info 05_obs_card 06_pairs_drawer
07_pair_on_map 08_metrics 09_export_menu 10_queries 11_query_restored 12_quality_mask 13_empty 14_date_error
15_layers_menu 16_service_down 17_mock 18_live_mode 19_overview_1366 20_zone_1366 21_pairs_1366 22_obs_list 23_url_restore (+17b_mock_zone_concentration).
result.json: console errors (external tile hosts separate), fps idle / pan, counts UI vs export (T5 consistency),
words on the first screen, prefs persistence, steps ok/failed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_HOSTS = ("arcgisonline.com", "cartocdn.com", "openstreetmap", "carto.com", "doi.org")
GL_ARGS = {
    "swiftshader": ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
    "gpu": ["--use-angle=d3d11", "--ignore-gpu-blocklist", "--enable-gpu", "--enable-gpu-rasterization"],
}

REC_START = """() => { const r = { on: true, d: [], last: performance.now() }; window.__rec = r;
    const f = (t) => { if (!r.on) return; r.d.push(t - r.last); r.last = t; requestAnimationFrame(f); }; requestAnimationFrame(f); }"""
REC_STOP = """() => { const r = window.__rec; if (!r) return null; r.on = false; const d = r.d.slice(1); if (!d.length) return null;
    const sum = d.reduce((a, b) => a + b, 0); const s = [...d].sort((a, b) => a - b);
    return { fps: Math.round(d.length * 1000 / sum * 10) / 10, frames: d.length, long50: d.filter(x => x > 50).length,
             p95_ms: Math.round(s[Math.floor(s.length * 0.95)] * 10) / 10 }; }"""

WORDS_JS = r"""() => {
  const skip = '[data-testid=map], .maplibregl-marker, [data-testid=attribution], .maplibregl-ctrl, .info-btn, option';
  let words = 0; const texts = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
  while ((n = w.nextNode())) {
    const t = n.textContent.trim(); if (!t) continue;
    const el = n.parentElement; if (!el || el.closest(skip)) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    const rg = document.createRange(); rg.selectNodeContents(n); const r = rg.getBoundingClientRect();
    if (r.width < 1 || r.height < 1 || r.bottom < 0 || r.top > innerHeight) continue;
    let a = el.parentElement, clipped = false;
    while (a && a !== document.body) { const c2 = getComputedStyle(a);
      if (/(auto|scroll|hidden)/.test(c2.overflowY)) { const ar = a.getBoundingClientRect(); if (r.top >= ar.bottom || r.bottom <= ar.top) { clipped = true; break; } }
      a = a.parentElement; }
    if (clipped) continue;
    const k = t.split(/\s+/).filter(x => /\p{L}/u.test(x)).length; words += k; if (k) texts.push(t.slice(0, 50));
  }
  return { words, texts };
}"""

# rough WCAG contrast of the text actually rendered (foreground colour vs the nearest opaque background)
CONTRAST_JS = r"""() => {
  const parse = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(',').map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const bgOf = (el) => { let e = el; while (e) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0.8) return c; e = e.parentElement; } return { r: 7, g: 8, b: 10, a: 1 }; };
  const bad = []; let n = 0;
  for (const el of document.querySelectorAll('.left *, .right *, .legend *, .c-drawer *, .menu *, .actions *')) {
    if (!el.childNodes.length || ![...el.childNodes].some(x => x.nodeType === 3 && x.textContent.trim())) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    const r = el.getBoundingClientRect(); if (r.width < 1 || r.bottom < 0 || r.top > innerHeight) continue;
    const fg = parse(cs.color); if (!fg) continue; const bg = bgOf(el);
    const op = parseFloat(cs.opacity) || 1; const fa = fg.a * op;
    const mix = { r: fg.r * fa + bg.r * (1 - fa), g: fg.g * fa + bg.g * (1 - fa), b: fg.b * fa + bg.b * (1 - fa) };
    const L1 = lum(mix), L2 = lum(bg); const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05); n++;
    if (ratio < 4.5) bad.push({ text: el.textContent.trim().slice(0, 40), ratio: Math.round(ratio * 100) / 100, cls: el.className && String(el.className).slice(0, 40) });
  }
  return { checked: n, below_4_5: bad.slice(0, 25), n_below: bad.length };
}"""


class Console:
    def __init__(self):
        self.errors: list[str] = []
        self.external: list[str] = []

    def attach(self, page: Page):
        page.on("console", self._on)
        page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))

    def _on(self, m):
        if m.type != "error":
            return
        loc = (m.location or {}).get("url", "")
        t = m.text
        if loc and "Failed to load resource" in t:
            t = f"{t} [{loc[:160]}]"
        if any(h in t or h in loc for h in EXTERNAL_HOSTS):
            self.external.append(t[:200])
        else:
            self.errors.append(t[:300])


def tid(page: Page, t: str):
    return page.locator(f"[data-testid='{t}']").first


def wait_ready(page: Page, timeout=30000):
    page.wait_for_function("window.__app && window.__app.ready", timeout=timeout)
    wait_idle(page)


def wait_idle(page: Page, extra=700, timeout=15000):
    page.wait_for_function("window.__app && !window.__app.isMoving()", timeout=timeout)
    page.wait_for_timeout(extra)


def run(args) -> dict:
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    base = args.base_url.rstrip("/")
    res: dict = {"base_url": base, "offline": args.offline, "steps": {}, "shots": [], "fps": {}, "t": time.strftime("%H:%M")}
    con = Console()

    def api(path: str):
        with urllib.request.urlopen(base + path, timeout=30) as r:
            return json.loads(r.read())

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed, args=GL_ARGS[args.gl])

        def new_page(w=1920, h=1080, block_api=False, console=None):
            ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, accept_downloads=True)

            def router(route):
                u = route.request.url
                if block_api and "/api/v3/" in u:
                    return route.abort()
                if args.offline and not (u.startswith(base) or u.startswith("data:") or u.startswith("blob:")):
                    return route.abort()
                return route.continue_()

            ctx.route("**/*", router)
            pg = ctx.new_page()
            (console or con).attach(pg)
            return pg

        def shot(page: Page, name: str):
            p = out / f"{name}.png"
            page.screenshot(path=str(p))
            res["shots"].append(p.name)

        def step(name: str, fn):
            try:
                fn()
                res["steps"][name] = "ok"
            except Exception as e:  # noqa: BLE001
                res["steps"][name] = f"FAILED: {str(e)[:300]}"
                print(f"[step] {name}: {e}", file=sys.stderr)

        page = new_page()
        t0 = time.time()
        page.goto(base + "/", wait_until="domcontentloaded")

        def s_overview():
            wait_ready(page)
            res["ready_s"] = round(time.time() - t0, 1)
            page.wait_for_timeout(1500)
            shot(page, "01_overview")
            res["first_screen"] = page.evaluate("({basemap: window.__app.basemap, projection: window.__app.projection, offline: window.__app.offline, counts: window.__app.counts})")
            res["words_first_screen"] = page.evaluate(WORDS_JS)
            res["contrast_first"] = page.evaluate(CONTRAST_JS)
            page.evaluate(REC_START)
            page.wait_for_timeout(2000)
            res["fps"]["idle"] = page.evaluate(REC_STOP)
            box = tid(page, "map").bounding_box()
            cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            page.evaluate(REC_START)
            page.mouse.move(cx, cy)
            page.mouse.down()
            for i in range(1, 80):
                page.mouse.move(cx + 3 * i, cy + i)
                page.wait_for_timeout(16)
            for i in range(80, 0, -1):
                page.mouse.move(cx + 3 * i, cy + i)
                page.wait_for_timeout(16)
            page.mouse.up()
            res["fps"]["pan_globe"] = page.evaluate(REC_STOP)

        step("overview", s_overview)

        def s_source():
            tid(page, "f-source").select_option("S3_SE_NORTH_SEA")
            page.wait_for_function("window.__app.q.source === 'S3_SE_NORTH_SEA' && window.__app.ready && window.__app.counts.zones !== null")
            wait_idle(page, 2500)
            shot(page, "02_source")
            page.evaluate(REC_START)
            box = tid(page, "map").bounding_box()
            cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            page.mouse.move(cx, cy)
            page.mouse.down()
            for i in range(1, 70):
                page.mouse.move(cx + 4 * i, cy + 2 * i)
                page.wait_for_timeout(16)
            for i in range(70, 0, -1):
                page.mouse.move(cx + 4 * i, cy + 2 * i)
                page.wait_for_timeout(16)
            page.mouse.up()
            res["fps"]["pan_region"] = page.evaluate(REC_STOP)
            # consistency UI ↔ export (T5)
            res["consistency_s3"] = page.evaluate(
                """async () => { const a = window.__app; const out = {ui: a.counts};
                for (const l of ['zones','observations']) { const r = await fetch(a.exportUrl(l, 'geojson')); const j = await r.json(); out[l + '_export_geojson'] = j.features.length;
                  const c = await (await fetch(a.exportUrl(l, 'csv'))).text(); out[l + '_export_csv_rows'] = c.trim().split(/\\r?\\n/).length - 1; }
                return out; }"""
            )

        step("source", s_source)

        def s_zone():
            page.evaluate("window.__app.selectZone('Z-S3_HE460_MarLitter_transect03')")
            page.wait_for_selector("[data-testid='zone-card']")
            page.wait_for_selector("[data-testid='zone-pairs'], [data-testid='zone-card'] .c-err", timeout=15000)
            wait_idle(page, 1500)
            shot(page, "03_zone_detected")
            res["zone_card"] = page.evaluate("""() => ({link: document.querySelector('[data-testid=zone-link-title]')?.textContent,
              why: document.querySelector('[data-testid=zone-link-why]')?.textContent, px: document.querySelector('[data-testid=zone-suspicious]')?.textContent,
              det: document.querySelector('[data-testid=zone-det-status]')?.textContent,
              conc: document.querySelector('[data-testid=zone-conc-status]')?.textContent,
              na: !!document.querySelector('[data-testid=zone-conc-na]'), area: document.querySelector('[data-testid=zone-area]')?.textContent,
              fe: document.querySelector('[data-testid=zone-field-estimate]')?.textContent})""")

        step("zone_detected", s_zone)

        def s_zone_na():
            page.evaluate("window.__app.selectZone('Z-S3_HE419_MarLitter_transect29')")
            page.wait_for_function("document.querySelector('[data-testid=card-title]')?.textContent.includes('29')")
            wait_idle(page, 1800)
            tid(page, "zone-conc-why").click()
            page.wait_for_timeout(300)
            shot(page, "04_zone_na_info")
            page.keyboard.press("Escape")

        step("zone_na", s_zone_na)

        def s_quality():
            # quality mask of the same scene on the map (zone 29 lies on a scene with quality.png)
            page.wait_for_timeout(300)
            shot(page, "12_quality_mask")

        step("quality_mask", s_quality)

        def s_obs():
            tid(page, "zone-field-obs").click()
            page.wait_for_selector("[data-testid='obs-card'] [data-testid='obs-pairs']", timeout=15000)
            page.wait_for_function("document.querySelector('[data-testid=obs-license]')?.textContent.length > 12", timeout=15000)
            wait_idle(page, 1200)
            shot(page, "05_obs_card")
            res["obs_card"] = page.evaluate("""() => ({profile: document.querySelector('[data-testid=obs-profile]')?.textContent,
              license: document.querySelector('[data-testid=obs-license]')?.textContent, pairs: document.querySelectorAll('[data-testid=card-pair-row]').length})""")

        step("obs_card", s_obs)

        def s_pairs():
            tid(page, "act-pairs").click()
            page.wait_for_function("window.__app.pairsReady", timeout=30000)
            page.wait_for_timeout(500)
            shot(page, "06_pairs_drawer")
            rows = page.locator("[data-testid='pair-row']")
            res["pairs_rows_rendered"] = rows.count()
            rows.nth(0).click()
            page.wait_for_function("!!window.__app.activePair")
            wait_idle(page, 1500)
            shot(page, "07_pair_on_map")
            res["pairs_consistency"] = page.evaluate(
                """async () => { const a = window.__app; const j = await (await fetch(a.exportUrl('pairs','geojson'))).json(); return {ui: a.counts.pairs, export: j.features.length}; }"""
            )
            tid(page, "pairs-close").click()

        step("pairs", s_pairs)

        def s_metrics():
            tid(page, "card-close").click()
            tid(page, "tab-metrics").click()
            page.wait_for_function("window.__app.metricsReady", timeout=15000)
            page.wait_for_timeout(400)
            shot(page, "08_metrics")
            api_m = api("/api/v3/metrics")
            ui = page.evaluate("document.querySelector('[data-testid=metrics-panel]').innerText")
            t = api_m["detector"]["main"].get("test_f1")
            res["metrics_ui_has_test_f1"] = (f"{t:.3f}".replace(".", ",") in ui) if t is not None else None
            tid(page, "tab-zones").click()

        step("metrics", s_metrics)

        def s_export():
            tid(page, "act-export").click()
            page.wait_for_selector("[data-testid='export-menu']")
            shot(page, "09_export_menu")
            with page.expect_download(timeout=15000) as d:
                tid(page, "export-zones-csv").click()
            dl = d.value
            res["download_zones_csv"] = dl.suggested_filename
            page.keyboard.press("Escape")

        step("export", s_export)

        def s_queries():
            tid(page, "f-det-not_detected").click()
            page.wait_for_function("window.__app.q.det.length === 1 && window.__app.ready")
            wait_idle(page, 600)
            res["counts_s3_not_detected"] = page.evaluate("window.__app.counts")
            tid(page, "act-queries").click()
            tid(page, "q-name").fill("L66 проверка · Северное море, не обнаружено")
            tid(page, "q-save").click()
            page.wait_for_selector("[data-testid='toast']")
            page.wait_for_timeout(600)
            tid(page, "act-queries").click() if not page.locator("[data-testid='query-menu']").count() else None
            page.wait_for_selector("[data-testid='q-item']")
            shot(page, "10_queries")
            page.keyboard.press("Escape")

        step("queries_save", s_queries)

        def s_restore():
            p2 = new_page()
            p2.goto(base + "/", wait_until="domcontentloaded")
            wait_ready(p2)
            p2.locator("[data-testid='act-queries']").click()
            items = p2.locator("[data-testid='q-item']")
            n = items.count()
            idx = None
            for i in range(n):
                if "L66 проверка" in items.nth(i).inner_text():
                    idx = i
            assert idx is not None, "saved query not in list"
            items.nth(idx).locator("[data-testid='q-run']").click()
            p2.wait_for_function("window.__app.q.source === 'S3_SE_NORTH_SEA' && window.__app.q.det.length === 1 && window.__app.ready")
            wait_idle(p2, 2500)
            shot(p2, "11_query_restored")
            res["query_restored"] = p2.evaluate("({q: window.__app.q, counts: window.__app.counts, toast: document.querySelector('[data-testid=toast]')?.textContent})")
            # clean up: delete the test query
            p2.locator("[data-testid='act-queries']").click()
            items = p2.locator("[data-testid='q-item']")
            for i in range(items.count() - 1, -1, -1):
                if "L66 проверка" in items.nth(i).inner_text():
                    items.nth(i).locator("[data-testid='q-del']").click()
                    p2.wait_for_timeout(400)
            p2.context.close()

        step("query_restore", s_restore)

        def s_empty():
            tid(page, "f-reset").click()
            page.wait_for_function("window.__app.q.det.length === 0 && window.__app.ready")
            tid(page, "f-source").select_option("S4_BLACK_SEA_DOORS3")
            tid(page, "f-from").fill("2016-01-01")
            tid(page, "f-to").fill("2016-12-31")
            page.wait_for_selector("[data-testid='empty-result']", timeout=15000)
            wait_idle(page, 1500)
            shot(page, "13_empty")

        step("empty", s_empty)

        def s_date_err():
            tid(page, "f-from").fill("2016-12-31")
            tid(page, "f-to").fill("2016-01-01")
            page.wait_for_selector("[data-testid='date-error']", timeout=5000)
            page.wait_for_timeout(300)
            shot(page, "14_date_error")
            tid(page, "f-reset").click()
            page.wait_for_function("window.__app.q.source === null && window.__app.ready")

        step("date_error", s_date_err)

        def s_layers():
            tid(page, "layers-menu").click()
            page.wait_for_selector("[data-testid='layers-panel']")
            shot(page, "15_layers_menu")
            # prefs: projection → mercator persists after reload
            tid(page, "proj-mercator").click()
            page.wait_for_timeout(500)
            page.reload(wait_until="domcontentloaded")
            wait_ready(page)
            res["prefs_after_reload"] = page.evaluate("({projection: window.__app.projection, basemap: window.__app.basemap})")
            tid(page, "layers-menu").click()
            tid(page, "proj-globe").click()
            page.keyboard.press("Escape")

        step("layers_prefs", s_layers)

        def s_down():
            con_down = Console()
            p3 = new_page(block_api=True, console=con_down)
            p3.goto(base + "/", wait_until="domcontentloaded")
            p3.wait_for_selector("[data-testid='service-down']", timeout=15000)
            shot(p3, "16_service_down")
            res["service_down_page_errors_expected"] = len(con_down.errors)
            p3.context.close()

        step("service_down", s_down)

        def s_mock():
            p4 = new_page()
            p4.goto(base + "/?mock=1", wait_until="domcontentloaded")
            wait_ready(p4)
            p4.wait_for_selector("[data-testid='mock-banner']")
            shot(p4, "17_mock")
            # the «concentration present» path of the card (only demo data has a satellite concentration)
            p4.evaluate("window.__app.selectZone('Z-DEMO-1')")
            p4.wait_for_selector("[data-testid='zone-conc-value']", timeout=10000)
            wait_idle(p4, 1500)
            shot(p4, "17b_mock_zone_concentration")
            p4.context.close()

        step("mock", s_mock)

        def s_live():
            p5 = new_page()
            p5.goto(base + "/?mode=live", wait_until="domcontentloaded")
            p5.wait_for_function("window.__app && window.__app.version === 'v2' && window.__app.sceneReady", timeout=40000)
            p5.wait_for_timeout(2500)
            shot(p5, "18_live_mode")
            res["live_url_keeps_mode"] = "mode=live" in p5.url
            p5.context.close()

        step("live_mode", s_live)

        def s_small():
            p6 = new_page(1366, 768)
            p6.goto(base + "/", wait_until="domcontentloaded")
            wait_ready(p6)
            p6.wait_for_timeout(1200)
            shot(p6, "19_overview_1366")
            res["contrast_1366"] = p6.evaluate(CONTRAST_JS)
            p6.locator("[data-testid='f-source']").select_option("S4_BLACK_SEA_DOORS3")
            p6.wait_for_function("window.__app.q.source === 'S4_BLACK_SEA_DOORS3' && window.__app.ready")
            wait_idle(p6, 1500)
            ids = p6.evaluate("window.__app.zoneIds()")
            if ids:
                p6.evaluate(f"window.__app.selectZone('{ids[0]}')")
                p6.wait_for_selector("[data-testid='zone-card']")
                wait_idle(p6, 1800)
            shot(p6, "20_zone_1366")
            p6.locator("[data-testid='act-pairs']").click()
            p6.wait_for_function("window.__app.pairsReady", timeout=30000)
            p6.wait_for_timeout(500)
            shot(p6, "21_pairs_1366")
            res["overflow_1366"] = p6.evaluate("({docW: document.documentElement.scrollWidth, docH: document.documentElement.scrollHeight, w: innerWidth, h: innerHeight})")
            p6.context.close()

        step("small", s_small)

        def s_obslist():
            p7 = new_page()
            p7.goto(base + "/", wait_until="domcontentloaded")
            wait_ready(p7)
            p7.locator("[data-testid='f-source']").select_option("S2_SARGASSO_MSM41")
            p7.wait_for_function("window.__app.q.source === 'S2_SARGASSO_MSM41' && window.__app.ready")
            p7.locator("[data-testid='f-profile']").select_option("S2_visual_GT2")
            p7.wait_for_function("window.__app.q.profile === 'S2_visual_GT2' && window.__app.ready")
            p7.locator("[data-testid='tab-obs']").click()
            p7.wait_for_selector("[data-testid='obs-item']")
            p7.locator("[data-testid='obs-item']").first.click()
            p7.wait_for_selector("[data-testid='obs-card'] [data-testid='obs-pairs']", timeout=15000)
            wait_idle(p7, 2000)
            shot(p7, "22_obs_list")
            if p7.locator("[data-testid='obs-research-estimate'] summary").count():
                p7.locator("[data-testid='obs-research-estimate'] summary").click()
                p7.wait_for_timeout(300)
                shot(p7, "22b_research_estimate")
            res["obs_field_estimate"] = p7.evaluate("document.querySelector('[data-testid=obs-field-estimate]')?.textContent")
            res["obs_list_first"] = p7.evaluate("document.querySelector('[data-testid=obs-item]')?.innerText")
            p7.context.close()

        step("obs_list", s_obslist)

        def s_url():
            # a shared link (?q=…&sel=…) restores filters, selection and the card without the backend's saved queries
            p8 = new_page()
            p8.goto(base + "/?q=eyJzb3VyY2UiOiJTM19TRV9OT1JUSF9TRUEiLCJmcm9tIjpudWxsLCJ0byI6bnVsbCwicHJvZmlsZSI6bnVsbCwic2NvcGUiOm51bGwsImRldCI6W10sImNvbmMiOltdLCJsYXllcnMiOnsib2JzIjp0cnVlLCJ6b25lcyI6dHJ1ZSwic2NlbmVzIjp0cnVlLCJxdWFsaXR5Ijp0cnVlfX0&sel=zone:Z-S3_HE460_MarLitter_transect03", wait_until="domcontentloaded")
            wait_ready(p8)
            p8.wait_for_selector("[data-testid='zone-card']", timeout=15000)
            wait_idle(p8, 2500)
            shot(p8, "23_url_restore")
            res["url_restore"] = p8.evaluate("({q: window.__app.q, counts: window.__app.counts, sel: window.__app.sel})")
            p8.context.close()

        step("url_restore", s_url)

        def s_geom():
            # L66 it.8: interrupted transect (MultiLineString, 2 segments, gap not joined), reconstructed end, pairfinder
            p9 = new_page()
            p9.goto(base + "/", wait_until="domcontentloaded")
            wait_ready(p9)
            p9.locator("[data-testid='f-source']").select_option("S2_SARGASSO_MSM41")
            p9.wait_for_function("window.__app.q.source === 'S2_SARGASSO_MSM41' && window.__app.ready")
            wait_idle(p9, 1500)
            p9.evaluate("window.__app.selectObs('MPL-0257')")
            p9.wait_for_selector("[data-testid='obs-geometry']", timeout=15000)
            wait_idle(p9, 2000)
            shot(p9, "24_multiline_transect")
            res["geom_multiline"] = p9.evaluate("document.querySelector('[data-testid=obs-geometry]')?.textContent")
            p9.locator("[data-testid='pairfinder-run']").click()
            p9.wait_for_selector("[data-testid='pairfinder-window']", timeout=60000)
            p9.wait_for_timeout(500)
            shot(p9, "25_pairfinder_s2")
            p9.locator("[data-testid='f-source']").select_option("S3_SE_NORTH_SEA")
            p9.wait_for_function("window.__app.q.source === 'S3_SE_NORTH_SEA' && window.__app.ready")
            wait_idle(p9, 1500)
            p9.evaluate("window.__app.selectObs('MPL-0779')")
            p9.wait_for_selector("[data-testid='obs-geometry']", timeout=15000)
            wait_idle(p9, 2000)
            res["geom_reconstructed"] = p9.evaluate("document.querySelector('[data-testid=obs-geometry]')?.textContent")
            p9.locator("[data-testid='pairfinder-run']").click()
            p9.wait_for_selector("[data-testid='pairfinder-window']", timeout=60000)
            p9.wait_for_timeout(500)
            shot(p9, "26_reconstructed_pairfinder")
            res["pairfinder_rows"] = p9.locator("[data-testid='pairfinder-row']").count()
            p9.context.close()

        step("geometry_pairfinder", s_geom)

        def s_qr():
            # a strip whose scene was rejected by the quality masks: its pixels are grey, «вероятно ложные»
            p10 = new_page()
            p10.goto(base + "/", wait_until="domcontentloaded")
            wait_ready(p10)
            p10.locator("[data-testid='f-source']").select_option("S4_BLACK_SEA_DOORS3")
            p10.wait_for_function("window.__app.q.source === 'S4_BLACK_SEA_DOORS3' && window.__app.ready")
            p10.evaluate("window.__app.selectZone('Z-S4_DOORS3_T14')")
            p10.wait_for_selector("[data-testid='zone-suspicious']", timeout=15000)
            wait_idle(p10, 2500)
            shot(p10, "27_quality_rejected_pixels")
            res["qr_text"] = p10.evaluate("document.querySelector('[data-testid=zone-suspicious]')?.textContent")
            tid(p10, "tab-metrics").click()
            p10.wait_for_function("window.__app.metricsReady", timeout=15000)
            p10.wait_for_timeout(400)
            res["metrics_det_cols"] = p10.evaluate("[...document.querySelectorAll('[data-testid=metrics-panel] .sec:first-child th')].map(x => x.textContent).filter(Boolean)")
            p10.context.close()

        step("quality_rejected", s_qr)
        browser.close()

    # never leave test queries in the shared saved-queries file
    try:
        for q in api("/api/v3/queries").get("queries", []):
            if str(q.get("name", "")).startswith("L66 проверка"):
                urllib.request.urlopen(urllib.request.Request(f"{base}/api/v3/queries/{q['query_id']}", method="DELETE"), timeout=10)
    except Exception as e:  # noqa: BLE001
        res["cleanup_error"] = str(e)[:200]
    res["console_errors"] = con.errors
    res["external_errors_n"] = len(con.external)
    res["smoke_ok"] = all(v == "ok" for v in res["steps"].values()) and not con.errors
    (out / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8070")
    ap.add_argument("--out", default="reports/screens/case_v2/iter1")
    ap.add_argument("--gl", choices=list(GL_ARGS), default="gpu")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    res = run(args)
    print(json.dumps({k: res.get(k) for k in ("smoke_ok", "steps", "fps", "first_screen", "words_first_screen", "consistency_s3", "pairs_consistency",
                                             "zone_card", "obs_card", "query_restored", "prefs_after_reload", "console_errors", "external_errors_n",
                                             "contrast_first", "overflow_1366", "metrics_ui_has_test_f1", "live_url_keeps_mode", "ready_s")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
