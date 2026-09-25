"""UI v2 smoke scenario + screenshots + performance probe.

Usage (from repo root; the service must serve the v2 build, the default UI):
  .venv\\Scripts\\python.exe scripts\\screenshots_v2.py --base-url http://127.0.0.1:8000 --out reports\\screens\\v2_iter1 --gl gpu
  demo set:  .venv\\Scripts\\python.exe -m service --port 8120 --data-root service\\demo
             .venv\\Scripts\\python.exe scripts\\screenshots_v2.py --base-url http://127.0.0.1:8120 --out reports\\screens\\v2_demo
  --out may lie anywhere (also outside the repository).
  add --video to record the demo tour (?tour=1) into <out>/tour.webm; --offline blocks every non-local request;
  --only a,b  shoots only the listed frame groups (globe,region,det,layers,h3,zone,place,drift,flow,check,compare,
              calendar,review,basemap,small,tour)

Frames (1920×1080 unless noted): 01_globe 02_region 03_detection 04_layers_menu 05_prob 06_h3_2d 07_h3_3d 08_zone_why
09_place 10_drift 11_particles 12_check 13_compare 14_calendar 15_review 16_incidents 17_satellite_offline
18_overview_1366 19_region_1366 20_detection_1366 21_zone_1366.
result.json: console errors (external tile hosts separate), fps idle / flyTo / drift / particles, bundle size (gzip).
L55 additions: frame-time probes (`perf`: fps, frames > 50 ms, p95 frame time) while panning the globe / the region by
mouse drag and during flyTo; `words_first_screen` (visible words outside the map: prose vs data rows); `prefs` (default
basemap = satellite + globe and stable for 10 s; the user's basemap / projection / model survive a reload; offline:
the fallback is temporary and the chosen basemap is kept). Frame groups: perf, prefs (run by default).
Steps whose feature has no data in the served set (no drift forecast, no current/wind fields, no drift-check pairs,
no zones / H3) are recorded as «пропущено (нет данных)» (`skipped` in result.json), not as failures.
Smoke passes (smoke_ok) when: no step failed, 0 console errors. fps ≥ 50 (with --gl gpu) is checked by eye.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "service" / "static_v2"
EXTERNAL_HOSTS = ("arcgisonline.com", "cartocdn.com", "openstreetmap", "carto.com")
GL_ARGS = {
    "swiftshader": ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
    "gpu": ["--use-angle=d3d11", "--ignore-gpu-blocklist", "--enable-gpu", "--enable-gpu-rasterization"],
}


def measure_fps(page: Page, ms: int) -> float:
    return page.evaluate(
        """(ms) => new Promise(res => { let n = 0; const t0 = performance.now();
            const f = (t) => { n++; if (t - t0 < ms) requestAnimationFrame(f); else res(Math.round(n * 1000 / (t - t0) * 10) / 10); };
            requestAnimationFrame(f); })""",
        ms,
    )


def fps_while_moving(page: Page, max_ms: int = 6000) -> float:
    return page.evaluate(
        """(maxMs) => new Promise(res => { let n = 0; const t0 = performance.now(); let started = false;
            const f = (t) => { n++; const moving = window.__app && window.__app.isMoving();
              if (moving) started = true;
              if ((started && !moving) || t - t0 > maxMs) res(Math.round(n * 1000 / (t - t0) * 10) / 10);
              else requestAnimationFrame(f); };
            requestAnimationFrame(f); })""",
        max_ms,
    )


REC_START = """() => { const r = { on: true, d: [], last: performance.now() }; window.__rec = r;
    const f = (t) => { if (!r.on) return; r.d.push(t - r.last); r.last = t; requestAnimationFrame(f); }; requestAnimationFrame(f); }"""
REC_STOP = """() => { const r = window.__rec; if (!r) return null; r.on = false; const d = r.d.slice(1); if (!d.length) return null;
    const sum = d.reduce((a, b) => a + b, 0); const s = [...d].sort((a, b) => a - b);
    return { fps: Math.round(d.length * 1000 / sum * 10) / 10, frames: d.length, long50: d.filter(x => x > 50).length,
             long33: d.filter(x => x > 33.4).length, p95_ms: Math.round(s[Math.floor(s.length * 0.95)] * 10) / 10,
             max_ms: Math.round(s[s.length - 1] * 10) / 10 }; }"""


def rec_start(page: Page):
    page.evaluate(REC_START)


def rec_stop(page: Page) -> dict | None:
    return page.evaluate(REC_STOP)


def drag_pan(page: Page, cx: float, cy: float, dx: float, dy: float, steps: int = 90, back: bool = True):
    """A human-like drag: ~16 ms per step (and back), measured by the caller with rec_start / rec_stop."""
    page.mouse.move(cx, cy)
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(cx + dx * i / steps, cy + dy * i / steps)
        page.wait_for_timeout(16)
    if back:
        for i in range(steps - 1, -1, -1):
            page.mouse.move(cx + dx * i / steps, cy + dy * i / steps)
            page.wait_for_timeout(16)
    page.mouse.up()


WORDS_JS = r"""() => {
  const skip = '[data-testid=map], .maplibregl-marker, [data-testid=attribution], .maplibregl-ctrl, .info-btn';
  const dataSel = '.reg-item, .li, .feed-item, .dates, table, .rp-title';
  const legendSel = '[data-testid=legend]';
  let prose = 0, data = 0, legend = 0, nums = 0; const texts = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = w.nextNode())) {
    const t = n.textContent.trim(); if (!t) continue;
    const el = n.parentElement; if (!el || el.closest(skip)) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) continue;
    const rg = document.createRange(); rg.selectNodeContents(n); const r = rg.getBoundingClientRect();
    if (r.width < 1 || r.height < 1 || r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) continue;
    // clipped by a scrolling ancestor?
    let a = el.parentElement, clipped = false;
    while (a && a !== document.body) { const cs2 = getComputedStyle(a);
      if (/(auto|scroll|hidden)/.test(cs2.overflowY)) { const ar = a.getBoundingClientRect(); if (r.top >= ar.bottom || r.bottom <= ar.top) { clipped = true; break; } }
      a = a.parentElement; }
    if (clipped) continue;
    const toks = t.split(/\s+/);
    const words = toks.filter(x => /\p{L}/u.test(x)).length;   // words = tokens with letters; numbers are data
    nums += toks.filter(x => !/\p{L}/u.test(x) && /\p{N}/u.test(x)).length;
    if (!words) continue;
    if (el.closest(legendSel)) legend += words;
    else if (el.closest(dataSel)) data += words; else { prose += words; texts.push(t.slice(0, 60)); }
  }
  return { prose, legend_on_map: legend, data_rows: data, numbers: nums, total_words: prose + data + legend, prose_texts: texts };
}"""


def wait_idle(page: Page, extra_ms: int = 600, timeout: int = 15000):
    page.wait_for_function("window.__app && !window.__app.isMoving()", timeout=timeout)
    page.wait_for_timeout(extra_ms)


def click(page: Page, testid: str, timeout: int = 5000):
    page.locator(f"[data-testid='{testid}']").first.click(timeout=timeout)


def exists(page: Page, testid: str) -> bool:
    return page.locator(f"[data-testid='{testid}']").count() > 0


class Skip(Exception):
    """The served data set has no data for this feature: the step is «пропущено (нет данных)», not a failure."""


def need(page: Page, testid: str, reason: str):
    """Skip the step if the control is absent or disabled (the UI disables controls whose data is missing)."""
    loc = page.locator(f"[data-testid='{testid}']")
    if loc.count() == 0 or loc.first.is_disabled():
        raise Skip(reason)


def show_path(p: Path) -> str:
    """Path for the log: relative to the repo root when inside it, else as is (no ValueError for --out elsewhere)."""
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


class Console:
    def __init__(self):
        self.errors: list[str] = []
        self.external: list[str] = []
        self.warnings: list[str] = []
        self.nonlocal_requests: set[str] = set()

    def attach(self, page: Page, base: str):
        page.on("console", self._on_console)
        page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        page.on("requestfailed", self._on_failed)
        host = base.split("//", 1)[-1].split("/", 1)[0]

        def on_req(req):
            u = req.url
            if not (u.startswith("data:") or u.startswith("blob:") or host in u):
                self.nonlocal_requests.add(u.split("?")[0][:120])

        page.on("request", on_req)

    def _on_console(self, m):
        text = m.text
        if m.type == "error":
            loc = (m.location or {}).get("url", "")
            if loc and "Failed to load resource" in text:
                text = f"{text} [{loc[:160]}]"
            if any(h in text or h in loc for h in EXTERNAL_HOSTS):
                self.external.append(text[:300])
            else:
                self.errors.append(text[:300])
        elif m.type == "warning":
            self.warnings.append(text[:300])

    def _on_failed(self, req):
        if "ERR_ABORTED" in (req.failure or ""):
            return
        if any(h in req.url for h in EXTERNAL_HOSTS):
            self.external.append(f"requestfailed {req.url[:160]}")


def bundle_size() -> dict:
    tot, gz = 0, 0
    for p in (STATIC / "assets").glob("*"):
        if p.suffix in (".js", ".css"):
            b = p.read_bytes()
            tot += len(b)
            gz += len(gzip.compress(b, 9))
    return {"js_css_bytes": tot, "js_css_gzip_bytes": gz, "js_css_gzip_mb": round(gz / 1e6, 2)}


def run(args) -> dict:
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    base = args.base_url.rstrip("/")
    only = set(args.only.split(",")) if args.only else None
    want = lambda g: only is None or g in only  # noqa: E731
    res: dict = {"base_url": base, "shots": [], "steps": {}, "notes": [], "gl": args.gl, "fps": {}}
    con = Console()

    def shot(page: Page, name: str):
        p = out / f"{name}.png"
        page.screenshot(path=str(p))
        res["shots"].append(name)
        print(f"  [shot] {show_path(p)}")

    def step(name: str, fn):
        t0 = time.time()
        try:
            fn()
            res["steps"][name] = {"ok": True, "s": round(time.time() - t0, 1)}
        except Skip as e:
            res["steps"][name] = {"ok": True, "skipped": f"пропущено (нет данных): {e}"}
            print(f"  [skip] {name}: пропущено (нет данных): {e}")
        except Exception as e:  # keep going: the report shows which step failed
            res["steps"][name] = {"ok": False, "error": str(e)[:300]}
            print(f"  [FAIL] {name}: {str(e)[:200]}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed, args=GL_ARGS[args.gl])

        def new_page(w=1920, h=1080, video=False):
            kw = {"viewport": {"width": w, "height": h}, "device_scale_factor": 1}
            if video:
                kw["record_video_dir"] = str(out / "_video")
                kw["record_video_size"] = {"width": w, "height": h}
            ctx = browser.new_context(**kw)
            if args.offline:
                ctx.route("**/*", lambda route: route.continue_() if (base in route.request.url or route.request.url.startswith("data:") or route.request.url.startswith("blob:")) else route.abort())
            pg = ctx.new_page()
            con.attach(pg, base)
            return ctx, pg

        ctx, page = new_page()
        page.goto(base + "/", wait_until="domcontentloaded")
        page.wait_for_function("window.__mapReady === true", timeout=30000)
        res["load_ms"] = round(page.evaluate("window.__mapReadyAt || 0"))
        res["gpu"] = page.evaluate(
            """(() => { try { const g = document.createElement('canvas').getContext('webgl2');
                 const e = g.getExtension('WEBGL_debug_renderer_info'); return g.getParameter(e.UNMASKED_RENDERER_WEBGL); } catch (e) { return String(e); } })()"""
        )
        page.wait_for_function("window.__app && window.__app.nFeed > 0", timeout=20000)
        # first screen = the demo region: where / when / what the model marked / what to click
        res["fps"]["first_flyin"] = fps_while_moving(page, 6000)
        page.wait_for_function("window.__app && window.__app.sceneReady", timeout=20000)
        wait_idle(page, 1500)
        res["first_screen"] = page.evaluate("({region: window.__app.region, date: window.__app.date, right: window.__app.rightMode, basemap: window.__app.basemap, projection: window.__app.projection, offline: window.__app.offline})")
        res["words_first_screen"] = page.evaluate(WORDS_JS)
        shot(page, "00_first_screen")
        res["projection"] = page.evaluate("window.__app.projection")
        res["local_tiles"] = page.evaluate("window.__app.localTiles")
        res["feed"] = {"n": page.evaluate("window.__app.nFeed"), "source": page.evaluate("window.__app.feedSource")}
        best = page.evaluate("(window.__app && window.__app.bestRegion) || null")
        res["best_region"] = best

        if want("globe"):
            def s_globe():
                rec_start(page)
                click(page, "world")
                page.wait_for_timeout(600)
                res["fps"]["flyto_region_to_globe"] = fps_while_moving(page, 6000)
                res.setdefault("perf", {})["flyto_region_to_globe"] = rec_stop(page)
                wait_idle(page, 1200)
                res["fps"]["idle_globe"] = measure_fps(page, 1500)
                res["words_globe"] = page.evaluate(WORDS_JS)
                shot(page, "01_globe")
                if want("perf") or only is None:
                    box = page.locator("[data-testid='main']").bounding_box()
                    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                    rec_start(page)
                    drag_pan(page, cx, cy, 420, 120)
                    res["perf"]["pan_globe"] = rec_stop(page)
                    res["fps"]["pan_globe"] = res["perf"]["pan_globe"]["fps"] if res["perf"]["pan_globe"] else None
                    wait_idle(page, 600)
            step("globe", s_globe)

        def feed_idx():
            return page.evaluate(
                f"""(() => {{ const els = [...document.querySelectorAll('[data-testid^=feed-item-]')];
                    return els.findIndex(e => e.dataset.region === {json.dumps(best)} && e.dataset.kind === 'new'); }})()"""
            )

        def open_region():
            click(page, "tab-regions")
            if page.evaluate("window.__app.region") == best:
                click(page, "world")
                page.wait_for_timeout(2600)
            rec_start(page)
            click(page, f"region-{best}")
            res["fps"]["flyto_globe_to_region"] = fps_while_moving(page, 9000)
            res.setdefault("perf", {})["flyto_globe_to_region"] = rec_stop(page)
            page.wait_for_function("window.__app && window.__app.sceneReady", timeout=20000)
            click(page, "tab-feed")
            wait_idle(page, 1500)

        if want("region") or want("det") or only is None:
            def s_region():
                open_region()
                shot(page, "02_region")
                box = page.locator("[data-testid='main']").bounding_box()
                cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2 + 60
                rec_start(page)
                drag_pan(page, cx, cy, 360, 140)
                res.setdefault("perf", {})["pan_region"] = rec_stop(page)
                res["fps"]["pan_region"] = res["perf"]["pan_region"]["fps"] if res["perf"]["pan_region"] else None
                wait_idle(page, 800)
            step("region", s_region)

        if want("det"):
            def s_det():
                pos = page.evaluate("window.__app.largestDetectionScreen()")
                if not pos:
                    raise RuntimeError("no detections on screen")
                page.mouse.move(pos["x"], pos["y"], steps=4)
                page.wait_for_timeout(300)
                page.mouse.click(pos["x"], pos["y"])
                page.wait_for_selector("[data-testid='detection-card']", timeout=5000)
                wait_idle(page, 1800)
                shot(page, "03_detection")
                click(page, "panel-back")
                page.mouse.move(900, 20)
                page.wait_for_timeout(400)
            step("detection", s_det)

        if want("feed"):
            def s_feed():
                idx = feed_idx()
                if idx is None or idx < 0:
                    raise RuntimeError("no feed item of the demo region")
                page.locator(f"[data-testid='feed-item-{idx}']").scroll_into_view_if_needed()
                page.locator(f"[data-testid='feed-item-{idx}']").click()
                page.wait_for_function("!!window.__app.detCard", timeout=15000)
                wait_idle(page, 1800)
                shot(page, "03b_feed_to_finding")
                click(page, "panel-back")
                page.mouse.move(900, 20)
                page.wait_for_timeout(400)
            step("feed", s_feed)

        if want("layers"):
            def s_layers():
                click(page, "layers-menu")
                page.wait_for_timeout(350)
                shot(page, "04_layers_menu")
                click(page, "layer-prob")
                page.wait_for_timeout(250)
                click(page, "layers-menu")
                page.wait_for_timeout(1500)
                shot(page, "05_prob")
                page.evaluate("window.__layersMenu && window.__layersMenu(true)")
                page.wait_for_timeout(200)
                click(page, "layer-prob")
                click(page, "layers-menu")
            step("layers", s_layers)

        if want("h3"):
            def s_h3():
                page.evaluate("window.__layersMenu(true)")
                page.wait_for_timeout(200)
                if not exists(page, "layer-h3") or page.locator("[data-testid='layer-h3']").first.is_disabled():
                    page.evaluate("window.__layersMenu(false)")
                    raise Skip("нет индекса H3 для этого снимка")
                click(page, "layer-h3")
                click(page, "layers-menu")
                page.wait_for_function("window.__app.h3Ready", timeout=15000)
                page.wait_for_timeout(1200)
                shot(page, "06_h3_2d")
                page.evaluate("window.__layersMenu(true)")
                click(page, "layer-h3_3d")
                click(page, "layers-menu")
                page.wait_for_timeout(2200)
                res["fps"]["h3_3d_idle"] = measure_fps(page, 1500)
                shot(page, "07_h3_3d")
                page.evaluate("window.__layersMenu(true)")
                click(page, "layer-h3")
                click(page, "layers-menu")
                wait_idle(page, 900)
            step("h3", s_h3)

        if want("zone"):
            def s_zone():
                need(page, "act-zones", "нет зон обследования на этом снимке")
                click(page, "act-zones")
                page.wait_for_timeout(600)
                shot(page, "07b_zones_list")
                click(page, "zone-row-1")
                page.wait_for_selector("[data-testid='zone-why']", timeout=8000)
                wait_idle(page, 1800)
                shot(page, "08_zone_why")
            step("zone", s_zone)

        if want("place"):
            def s_place():
                if not exists(page, "zone-open-place"):
                    need(page, "act-zones", "нет зон обследования на этом снимке")
                    click(page, "act-zones")
                    click(page, "zone-row-1")
                    page.wait_for_timeout(1200)
                click(page, "zone-open-place")
                page.wait_for_selector("[data-testid='place-card']", timeout=8000)
                page.wait_for_timeout(2500)
                shot(page, "09_place")
                click(page, "panel-back")
                page.wait_for_timeout(300)
                if exists(page, "panel-back"):
                    click(page, "panel-back")
                page.wait_for_timeout(400)
            step("place", s_place)

        if want("drift"):
            def s_drift():
                need(page, "act-drift", "нет прогноза дрейфа")
                click(page, "act-drift")
                page.wait_for_function("window.__app.driftReady", timeout=15000)
                wait_idle(page, 1000)  # the drift view starts playing by itself
                res["fps"]["drift"] = measure_fps(page, 3000)
                page.wait_for_timeout(1500)
                shot(page, "10_drift")
            step("drift", s_drift)

        if want("flow"):
            def s_flow():
                need(page, "act-drift", "нет прогноза дрейфа")
                if page.evaluate("window.__app.view") != "drift":
                    click(page, "act-drift")
                    page.wait_for_timeout(800)
                kind = next((k for k in ("drift-wind", "drift-currents")
                             if exists(page, k) and not page.locator(f"[data-testid='{k}']").first.is_disabled()), None)
                if kind is None:
                    raise Skip("нет полей течений и ветра для этой даты")
                click(page, kind)
                page.wait_for_function("window.__app.flowFields > 0", timeout=15000)
                page.wait_for_timeout(1500)
                res["fps"]["particles_and_drift"] = measure_fps(page, 3000)
                shot(page, "11a_drift_particles")
                page.evaluate("window.__app.setDriftHour(0)")
                page.evaluate("window.__layersMenu(true)")
                click(page, "layer-rgb")  # scene image off → particles over the basemap
                click(page, "layers-menu")
                page.wait_for_timeout(2500)
                res["fps"]["particles"] = measure_fps(page, 3000)
                res["flow_fps_internal"] = page.evaluate("window.__app.flowFps")
                shot(page, "11_particles")
                page.evaluate("window.__layersMenu(true)")
                click(page, "layer-rgb")
                click(page, "layers-menu")
                click(page, "act-findings")
                page.wait_for_timeout(500)
            step("flow", s_flow)

        if want("check"):
            def s_check():
                need(page, "act-drift", "нет прогноза дрейфа")
                click(page, "act-drift")
                page.wait_for_timeout(800)
                need(page, "check-open", "нет проверки прогноза дрейфа")
                click(page, "check-open")
                page.wait_for_selector("[data-testid='check-pairs'], [data-testid='check-none']", timeout=10000)
                if exists(page, "check-none"):
                    shot(page, "12_check_none")
                    click(page, "panel-close")
                    page.wait_for_timeout(500)
                    raise Skip("в этом наборе данных пар для проверки нет")
                idx = page.evaluate("""(() => { const els = [...document.querySelectorAll('[data-testid^=check-pair-]')];
                    return Math.max(0, els.findIndex(e => /попал|да,/.test(e.textContent))); })()""")
                click(page, f"check-pair-{idx}")
                page.wait_for_function("window.__app.check && window.__app.check.loaded", timeout=15000)
                wait_idle(page, 2000)
                shot(page, "12_check")
                res["check_verdict"] = page.locator("[data-testid='check-verdict']").inner_text() if exists(page, "check-verdict") else None
                click(page, "panel-close")
                page.wait_for_timeout(500)
            step("check", s_check)

        if want("compare"):
            def s_compare():
                if not page.evaluate("window.__app.region"):
                    open_region()
                click(page, "compare-open")
                page.wait_for_selector("[data-testid='compare-view']", timeout=8000)
                page.wait_for_timeout(4000)
                shot(page, "13_compare")
                click(page, "compare-close")
                page.wait_for_timeout(500)
            step("compare", s_compare)

        if want("calendar"):
            def s_cal():
                if not page.evaluate("window.__app.region"):
                    open_region()
                click(page, "act-history")
                page.wait_for_timeout(600)
                page.locator("[data-testid='obs-calendar']").scroll_into_view_if_needed()
                page.wait_for_timeout(800)
                shot(page, "14_calendar")
            step("calendar", s_cal)

        if want("review"):
            def s_review():
                click(page, "act-review")
                page.wait_for_selector("[data-testid='review-view']", timeout=8000)
                page.wait_for_timeout(3000)
                shot(page, "15_review")
                if exists(page, "review-tab-incidents"):
                    click(page, "review-tab-incidents")
                    page.wait_for_timeout(2500)
                    shot(page, "16_incidents")
                click(page, "review-close")
                page.wait_for_timeout(500)
            step("review", s_review)

        if want("basemap"):
            def s_base():
                page.evaluate("window.__layersMenu(true)")
                click(page, "basemap-dark")
                click(page, "layers-menu")
                page.wait_for_timeout(3000)
                shot(page, "17_dark_by_user")
                page.evaluate("window.__layersMenu(true)")
                click(page, "basemap-satellite")
                click(page, "layers-menu")
                page.wait_for_timeout(2500)
                shot(page, "17b_satellite")
            step("basemap", s_base)

        res["app_state_end"] = page.evaluate("JSON.parse(JSON.stringify({region: window.__app.region, date: window.__app.date, layers: window.__app.layers}))")
        ctx.close()

        if want("small"):
            def s_small():
                c2, p2 = new_page(1366, 768)
                p2.goto(base + "/", wait_until="domcontentloaded")
                p2.wait_for_function("window.__mapReady === true && window.__app.nFeed > 0 && window.__app.sceneReady", timeout=30000)
                wait_idle(p2, 2500)
                p2.screenshot(path=str(out / "19_region_1366.png"))
                res["shots"].append("19_region_1366")
                pos = p2.evaluate("window.__app.largestDetectionScreen()")
                if pos:
                    p2.mouse.click(pos["x"], pos["y"])
                    p2.wait_for_selector("[data-testid='detection-card']", timeout=5000)
                    wait_idle(p2, 1500)
                    p2.screenshot(path=str(out / "20_detection_1366.png"))
                    res["shots"].append("20_detection_1366")
                    p2.locator("[data-testid='panel-back']").click()
                p2.goto(base + "/?world=1", wait_until="domcontentloaded")
                p2.wait_for_function("window.__mapReady === true && window.__app.nFeed > 0", timeout=30000)
                p2.wait_for_timeout(2000)
                p2.screenshot(path=str(out / "18_overview_1366.png"))
                res["shots"].append("18_overview_1366")
                p2.locator(f"[data-testid='region-{best}']").click()
                p2.wait_for_function("window.__app && window.__app.sceneReady", timeout=20000)
                wait_idle(p2, 1500)
                p2.locator("[data-testid='act-zones']").click()
                p2.wait_for_timeout(500)
                p2.locator("[data-testid='zone-row-1']").click()
                p2.wait_for_selector("[data-testid='zone-why']", timeout=8000)
                wait_idle(p2, 1500)
                p2.screenshot(path=str(out / "21_zone_1366.png"))
                res["shots"].append("21_zone_1366")
                if p2.locator("[data-testid='act-drift']").count():
                    p2.locator("[data-testid='act-drift']").click()
                    p2.wait_for_function("window.__app.driftReady", timeout=15000)
                    wait_idle(p2, 2500)
                    p2.screenshot(path=str(out / "22_drift_1366.png"))
                    res["shots"].append("22_drift_1366")
                    p2.locator("[data-testid='act-findings']").click()
                    p2.wait_for_timeout(600)
                if p2.locator("[data-testid='act-review']").count():
                    p2.locator("[data-testid='act-review']").click()
                    p2.wait_for_selector("[data-testid='review-view']", timeout=8000)
                    p2.wait_for_timeout(2500)
                    p2.screenshot(path=str(out / "23_review_1366.png"))
                    res["shots"].append("23_review_1366")
                    p2.locator("[data-testid='review-close']").click()
                    p2.wait_for_timeout(400)
                # layout sanity: panels do not overlap, nothing wider than the viewport
                res["layout_1366"] = p2.evaluate("""(() => {
                    const r = (s) => { const e = document.querySelector(s); return e ? e.getBoundingClientRect() : null; };
                    const L = r('[data-panel=left]'), R = r('[data-panel=right]'), T = r('[data-testid=toolbar]');
                    return { left_right: L && R ? L.right <= R.left : null, toolbar_inside: T ? (T.left >= (L ? L.right : 0) && T.right <= (R ? R.left : innerWidth)) : null,
                             hscroll: document.documentElement.scrollWidth > innerWidth }; })()""")
                c2.close()
            step("small", s_small)

        if want("prefs"):
            def s_prefs():
                c4, p4 = new_page(1920, 1080)
                st = lambda: p4.evaluate("({basemap: window.__app.basemap, projection: window.__app.projection, model: window.__app.model, offline: window.__app.offline, prob: window.__app.layers.prob})")  # noqa: E731
                p4.goto(base + "/", wait_until="domcontentloaded")
                p4.wait_for_function("window.__mapReady === true && window.__app.sceneReady", timeout=30000)
                d0 = st()
                p4.wait_for_timeout(10000)
                d1 = st()
                pr = {"default": d0, "after_10s": d1,
                      "default_ok": d0["basemap"] == "satellite" and d0["projection"] == "globe",
                      "stable_10s": d1["basemap"] == d0["basemap"] and d1["projection"] == d0["projection"]}
                if args.offline:
                    pr["offline_fallback_on"] = bool(d1["offline"])
                    pr["choice_kept_offline"] = d1["basemap"] == "satellite"
                else:
                    pr["offline_flag_online"] = bool(d1["offline"])
                # user's choice → reload (no query string) → restored
                p4.evaluate("window.__layersMenu(true)")
                p4.locator("[data-testid='basemap-dark']").click()
                p4.locator("[data-testid='proj-mercator']").click()
                other = p4.evaluate("(() => { const b = [...document.querySelectorAll('[data-testid^=model-]')].find(e => !e.disabled && !e.querySelector('.radio.on')); return b ? b.dataset.testid.slice(6) : null; })()")
                if other:
                    p4.locator(f"[data-testid='model-{other}']").click()
                p4.evaluate("window.__layersMenu(true)")
                p4.locator("[data-testid='layer-prob']").click()
                p4.wait_for_timeout(800)
                chosen = st()
                p4.goto(base + "/", wait_until="domcontentloaded")
                p4.wait_for_function("window.__mapReady === true && window.__app.sceneReady", timeout=30000)
                p4.wait_for_timeout(1500)
                after = st()
                pr["chosen"] = chosen
                pr["after_reload"] = after
                pr["restored_after_reload"] = all(after[k] == chosen[k] for k in ("basemap", "projection", "model", "prob"))
                p4.screenshot(path=str(out / "24_prefs_after_reload.png"))
                res["shots"].append("24_prefs_after_reload")
                # region change keeps the choice
                reg2 = p4.evaluate("(() => { const els = [...document.querySelectorAll('[data-testid^=region-]')].filter(e => !e.classList.contains('on')); return els.length ? els[0].dataset.testid.slice(7) : null; })()")
                if reg2:
                    p4.locator(f"[data-testid='region-{reg2}']").click()
                    p4.wait_for_timeout(3500)
                    a2 = st()
                    pr["after_region_change"] = a2
                    pr["kept_after_region_change"] = a2["basemap"] == chosen["basemap"] and a2["projection"] == chosen["projection"]
                # restore defaults for the next runs of this profile (fresh context anyway)
                p4.evaluate("localStorage.clear()")
                c4.close()
                res["prefs"] = pr
                if not (pr["default_ok"] and pr["stable_10s"] and pr["restored_after_reload"]):
                    raise RuntimeError(f"prefs check failed: {json.dumps(pr, ensure_ascii=False)[:280]}")
            step("prefs", s_prefs)

        if args.video or want("tour") and only is not None:
            def s_tour():
                c3, p3 = new_page(1920, 1080, video=True)
                p3.goto(base + "/?tour=1", wait_until="domcontentloaded")
                p3.wait_for_function("window.__tourRunning === true", timeout=30000)
                t0 = time.time()
                last, n, steps = None, 0, []
                while time.time() - t0 < 180:
                    running = p3.evaluate("window.__tourRunning === true")
                    cap = p3.evaluate("(document.querySelector('[data-testid=tour-caption] .tc-text') || {}).textContent || null")
                    if cap and cap != last:
                        last = cap
                        n += 1
                        steps.append({"t": round(time.time() - t0, 1), "text": cap[:120]})
                        p3.wait_for_timeout(2600)
                        p3.screenshot(path=str(out / f"tour_{n:02d}.png"))
                        res["shots"].append(f"tour_{n:02d}")
                    if not running:
                        break
                    p3.wait_for_timeout(250)
                res["tour_seconds"] = round(time.time() - t0, 1)
                res["tour_steps"] = steps
                vid = p3.video.path() if p3.video else None
                c3.close()
                if vid:
                    dst = out / "tour.webm"
                    Path(vid).replace(dst)
                    res["tour_video"] = show_path(dst)
            step("tour", s_tour)

        browser.close()

    res["console_errors"] = con.errors
    res["external_errors"] = sorted(set(con.external))[:20]
    res["console_warnings"] = con.warnings[:20]
    res["nonlocal_requests"] = sorted(con.nonlocal_requests)[:30]
    res["bundle"] = bundle_size()
    perf = res.get("perf") or {}
    res["perf_summary"] = {
        "min_fps_pan_fly": min([v["fps"] for v in perf.values() if v] or [None]) if perf else None,
        "long50_total": sum(v["long50"] for v in perf.values() if v) if perf else None,
        "frames_total": sum(v["frames"] for v in perf.values() if v) if perf else None,
    }
    fps_vals = [v for v in res["fps"].values() if isinstance(v, (int, float))]
    res["fps_min"] = min(fps_vals) if fps_vals else None
    res["skipped"] = {k: s["skipped"] for k, s in res["steps"].items() if s.get("skipped")}
    res["smoke_ok"] = all(s.get("ok") for s in res["steps"].values()) and not con.errors
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8070")
    ap.add_argument("--out", default="reports/screens/v2_iter1")
    ap.add_argument("--gl", choices=list(GL_ARGS), default="gpu")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--video", action="store_true", help="record the demo tour (?tour=1) into <out>/tour.webm")
    ap.add_argument("--offline", action="store_true", help="abort every request that is not to --base-url")
    ap.add_argument("--only", default="", help="comma list of frame groups")
    args = ap.parse_args()
    res = run(args)
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    (out / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: res.get(k) for k in ("smoke_ok", "skipped", "fps", "fps_min", "perf", "perf_summary", "words_first_screen", "prefs", "first_screen", "console_errors", "external_errors", "bundle", "steps", "layout_1366", "tour_seconds")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
