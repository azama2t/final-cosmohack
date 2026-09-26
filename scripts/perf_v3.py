r"""Frontend performance harness (L96, §11 direction 6): v2 baseline vs v3 iterations.

Usage (repo root; the service must already be running — this script only reads it):
  .venv\\Scripts\\python.exe scripts\\perf_v3.py --url http://127.0.0.1:8070/ --label v2_baseline
  .venv\\Scripts\\python.exe scripts\\perf_v3.py --url http://127.0.0.1:8072/ --label v3_iter1
Options: --sizes 1920x1080,1366x768  --repeats 2  --no-profile  --headed  --gl gpu|swiftshader

What is measured, per viewport (fresh browser context = cold cache for every viewport):
  first_show   — navigation start → WebGL context created (map visible) → first GL draw → first draw after the first
                 map tile arrived (resource timing, URL like /z/x/y) → map.loaded() && areTilesLoaded() (if a map handle
                 is exposed on window: __map, __caseMap, … or any object with flyTo/getCenter/isMoving).
  idle         — rAF fps for 3 s with no input (the vsync ceiling of this machine/browser).
  pan          — human-like mouse drag over the map (90 steps × 16 ms, and back), repeated --repeats times.
  wheel_zoom   — mouse-wheel zoom in and out at the map centre.
  flyto        — map.flyTo(...) there and back (needs a map handle; otherwise skipped).
  left_toggle  — open/close the left column (selectors LEFT_TOGGLE); v2 fallback: open a zone card and close it.
  views        — click through every view tab (role=tab / data-testid^=tab- / ^=view-) and back.
  For every scenario: fps, frames, frames > 50 ms (share), p95 / max frame time, and Event Timing (click → next paint).
Once (first viewport): CPU profile of pan + flyTo (CDP Profiler; top self-time functions and per-bundle-chunk time) and a
Chrome performance trace (devtools.timeline + gpu; top event types) → cpu_pan_flyto.cpuprofile, trace.json.gz.
Also: console errors / page errors / failed local requests; bundle = every JS/CSS response of the page, raw and gzip-9.
Output: reports/perf/<label>/result.json + report.md (+ profile files).
Compare all measured labels: .venv\Scripts\python.exe scripts\perf_v3.py --compare [label ...]  → reports/perf/compare.md
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_HOSTS = ("arcgisonline.com", "cartocdn.com", "openstreetmap", "carto.com", "openfreemap", "maptiler")
GL_ARGS = {
    "swiftshader": ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
    "gpu": ["--use-angle=d3d11", "--ignore-gpu-blocklist", "--enable-gpu", "--enable-gpu-rasterization"],
}
LEFT_TOGGLE = [
    "[data-testid=collapse]", "[data-testid=left-toggle]", "[data-testid=toggle-left]", "[data-testid=left-collapse]", "[data-testid=collapse-left]",
    "[data-testid=left-close]", "[data-testid=sidebar-toggle]", "[data-testid=panel-toggle]",
    "button[aria-label*='колонк' i]", "button[aria-label*='боков' i]", "button[aria-label*='панель' i]",
]
VIEW_TABS = "[role=tab], [data-testid^='tab-'], [data-testid^='view-'], [data-testid^='nav-']"

INIT_JS = r"""(() => {
  const T = window.__perfT = { glCtx: null, firstDraw: null, firstTileDraw: null, tileResp: null, nTiles: 0,
                               tilesIdle: null, mapName: null, events: [] };
  const tileRe = /\/\d+\/\d+\/\d+(\.(png|jpe?g|webp|pbf|mvt|pmtiles))?(\?|$)|\/tile/i;
  try { new PerformanceObserver(l => { for (const e of l.getEntries()) {
      if (tileRe.test(e.name) && !/\.(js|css|json|geojson)(\?|$)/.test(e.name)) { T.nTiles++;
        if (T.tileResp === null || e.responseEnd < T.tileResp) T.tileResp = e.responseEnd; } } })
    .observe({ type: 'resource', buffered: true }); } catch (e) {}
  try { new PerformanceObserver(l => { for (const e of l.getEntries()) T.events.push(
      { name: e.name, t: Math.round(e.startTime), dur: Math.round(e.duration),
        proc: Math.round((e.processingEnd || 0) - (e.processingStart || 0)) }); })
    .observe({ type: 'event', durationThreshold: 16, buffered: true }); } catch (e) {}
  const og = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (t, ...a) { const c = og.call(this, t, ...a);
    if (c && /webgl/.test(String(t)) && T.glCtx === null) T.glCtx = performance.now(); return c; };
  const saved = [];
  const restore = () => { for (const [P, fn, o] of saved) P.prototype[fn] = o; saved.length = 0; };
  for (const P of [window.WebGLRenderingContext, window.WebGL2RenderingContext]) { if (!P) continue;
    for (const fn of ['drawElements', 'drawArrays', 'drawElementsInstanced', 'drawArraysInstanced']) {
      const o = P.prototype[fn]; if (!o) continue; saved.push([P, fn, o]);
      P.prototype[fn] = function (...a) { const t = performance.now();
        if (T.firstDraw === null) T.firstDraw = t;
        if (T.firstTileDraw === null && T.tileResp !== null && t > T.tileResp) { T.firstTileDraw = t; setTimeout(restore, 0); }
        return o.apply(this, a); }; } }
  setTimeout(restore, 30000);
  const isMap = (m) => m && typeof m === 'object' && typeof m.flyTo === 'function' && typeof m.getCenter === 'function'
                       && typeof m.isMoving === 'function';
  window.__perfFindMap = () => {
    if (window.__perfMap) return T.mapName;
    for (const n of ['__map', '__caseMap', '__mapV3', '__v3map', '__maplibre', 'map']) {
      try { if (isMap(window[n])) { window.__perfMap = window[n]; T.mapName = n; return n; } } catch (e) {} }
    for (const k of Object.keys(window)) { try { if (isMap(window[k])) { window.__perfMap = window[k]; T.mapName = k; return k; } } catch (e) {} }
    return null; };
  const poll = setInterval(() => { try { window.__perfFindMap(); const m = window.__perfMap;
      if (m && m.loaded() && m.areTilesLoaded() && !m.isMoving()) { T.tilesIdle = performance.now(); clearInterval(poll); } } catch (e) {} }, 20);
  setTimeout(() => clearInterval(poll), 40000);
})();"""

REC_START = """() => { const r = { on: true, d: [], last: performance.now(), ev0: (window.__perfT ? window.__perfT.events.length : 0) };
    window.__rec = r; const f = (t) => { if (!r.on) return; r.d.push(t - r.last); r.last = t; requestAnimationFrame(f); };
    requestAnimationFrame(f); }"""
REC_STOP = """() => { const r = window.__rec; if (!r) return null; r.on = false; const d = r.d.slice(1);
    const ev = window.__perfT ? window.__perfT.events.slice(r.ev0) : [];
    if (!d.length) return { fps: null, frames: 0, events: ev };
    const sum = d.reduce((a, b) => a + b, 0); const s = [...d].sort((a, b) => a - b);
    return { fps: Math.round(d.length * 1000 / sum * 10) / 10, frames: d.length, dur_ms: Math.round(sum),
             long50: d.filter(x => x > 50).length, long50_share: Math.round(d.filter(x => x > 50).length / d.length * 1000) / 1000,
             long33: d.filter(x => x > 33.4).length, over17_share: Math.round(d.filter(x => x > 17.5).length / d.length * 1000) / 1000, p95_ms: Math.round(s[Math.floor(s.length * 0.95)] * 10) / 10,
             max_ms: Math.round(s[s.length - 1] * 10) / 10,
             long50_ms_total: Math.round(d.filter(x => x > 50).reduce((a, b) => a + b, 0)),
             events_max_ms: ev.length ? Math.max(...ev.map(e => e.dur)) : null, events: ev.slice(0, 20) }; }"""


def show_path(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


class Console:
    def __init__(self, host: str):
        self.host = host
        self.errors: list[str] = []
        self.external: list[str] = []
        self.warnings: list[str] = []
        self.failed_local: list[str] = []

    def attach(self, page: Page):
        page.on("console", self._on_console)
        page.on("pageerror", lambda e: self.errors.append(f"pageerror: {str(e)[:300]}"))
        page.on("requestfailed", self._on_failed)

    def _on_console(self, m):
        text = m.text
        if m.type == "error":
            loc = (m.location or {}).get("url", "")
            if loc and "Failed to load resource" in text:
                text = f"{text} [{loc[:160]}]"
            (self.external if any(h in text or h in loc for h in EXTERNAL_HOSTS) else self.errors).append(text[:300])
        elif m.type == "warning":
            self.warnings.append(text[:300])

    def _on_failed(self, req):
        if "ERR_ABORTED" in (req.failure or ""):
            return
        if self.host in req.url:
            self.failed_local.append(f"{req.failure} {req.url[:160]}")
        elif any(h in req.url for h in EXTERNAL_HOSTS):
            self.external.append(f"requestfailed {req.url[:160]}")


FIRST_SHOW_JS = """() => { const T = window.__perfT; const nav = performance.getEntriesByType('navigation')[0] || {};
                const p = {}; for (const e of performance.getEntriesByType('paint')) p[e.name] = Math.round(e.startTime);
                let lcp = null; try { const l = performance.getEntriesByType('largest-contentful-paint'); if (l.length) lcp = Math.round(l[l.length-1].startTime); } catch (e) {}
                const r = (x) => x === null || x === undefined ? null : Math.round(x);
                return { gl_context_ms: r(T.glCtx), first_gl_draw_ms: r(T.firstDraw), first_tile_response_ms: r(T.tileResp),
                         first_tile_draw_ms: r(T.firstTileDraw), tiles_idle_ms: r(T.tilesIdle), n_tile_requests: T.nTiles,
                         map_handle: window.__perfFindMap(), map_ready_at_ms: r(window.__mapReadyAt),
                         dom_content_loaded_ms: r(nav.domContentLoadedEventEnd), load_event_ms: r(nav.loadEventEnd),
                         fcp_ms: p['first-contentful-paint'] ?? null, lcp_ms: lcp }; }"""


def wait_first_show(page: Page, R: dict | None = None):
    try:
        page.wait_for_function("window.__perfT && window.__perfT.firstDraw !== null", timeout=30000)
    except Exception:
        if R is not None:
            R["first_show_error"] = "no WebGL draw within 30 s"
    try:
        page.wait_for_function("window.__perfT.tilesIdle !== null || window.__perfT.firstTileDraw !== null", timeout=30000)
        page.wait_for_function("!window.__perfMap || window.__perfT.tilesIdle !== null", timeout=20000)
    except Exception:
        if R is not None:
            R.setdefault("notes", []).append("tiles idle not reached within the wait")
    page.wait_for_timeout(1500)


def collect_first_show(page: Page) -> dict:
    T = page.evaluate(FIRST_SHOW_JS)
    fs = T.get("first_tile_draw_ms") or T.get("first_gl_draw_ms")
    T["first_show_s"] = round(fs / 1000, 2) if fs else None
    T["first_show_basis"] = "first GL draw after first tile" if T.get("first_tile_draw_ms") else "first GL draw (no tile requests seen)"
    return T


def extra_loads(browser, url: str, w: int, h: int, n: int, throttle: float) -> list[dict]:
    """n more cold loads (fresh context each) for the first-show median (external tiles make single loads noisy)."""
    outl = []
    for _ in range(n):
        ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
        ctx.add_init_script(INIT_JS)
        pg = ctx.new_page()
        if throttle > 1:
            ctx.new_cdp_session(pg).send("Emulation.setCPUThrottlingRate", {"rate": throttle})
        pg.goto(url, wait_until="load", timeout=60000)
        wait_first_show(pg)
        T = collect_first_show(pg)
        outl.append({k: T.get(k) for k in ("first_show_s", "gl_context_ms", "first_tile_draw_ms", "tiles_idle_ms", "fcp_ms")})
        ctx.close()
    return outl


def rec(page: Page, fn) -> dict | None:
    page.evaluate(REC_START)
    t0 = time.time()
    extra = fn() or {}
    r = page.evaluate(REC_STOP) or {}
    r["wall_s"] = round(time.time() - t0, 2)
    r.update(extra)
    return r


def map_box(page: Page) -> dict:
    """Largest visible canvas = the map (MapLibre / deck.gl overlay share the box)."""
    return page.evaluate("""() => { let best = null, ba = 0;
        for (const c of document.querySelectorAll('canvas')) { const r = c.getBoundingClientRect();
          const a = Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0)) * Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
          if (a > ba) { ba = a; best = r; } }
        if (!best) return null;
        const l = Math.max(best.left, 0), t = Math.max(best.top, 0), rr = Math.min(best.right, innerWidth), b = Math.min(best.bottom, innerHeight);
        return { x: l, y: t, w: rr - l, h: b - t, cx: (l + rr) / 2, cy: (t + b) / 2 }; }""")


def drag_pan(page: Page, cx: float, cy: float, dx: float, dy: float, steps: int = 90):
    page.mouse.move(cx, cy)
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(cx + dx * i / steps, cy + dy * i / steps)
        page.wait_for_timeout(16)
    for i in range(steps - 1, -1, -1):
        page.mouse.move(cx + dx * i / steps, cy + dy * i / steps)
        page.wait_for_timeout(16)
    page.mouse.up()
    page.wait_for_timeout(400)


def wait_map_still(page: Page, timeout: int = 15000):
    try:
        page.wait_for_function("!window.__perfMap || !window.__perfMap.isMoving()", timeout=timeout)
    except Exception:
        pass
    page.wait_for_timeout(300)


def fly(page: Page) -> dict:
    st = page.evaluate("""() => { const m = window.__perfMap; const c = m.getCenter();
        return { lon: c.lng, lat: c.lat, zoom: m.getZoom(), minZoom: m.getMinZoom(), maxZoom: m.getMaxZoom() }; }""")
    z2 = min(max(st["zoom"] + 3, 5), st["maxZoom"], 9)
    lon2 = st["lon"] + (25 if st["zoom"] < 3 else 4 / 2 ** max(st["zoom"] - 4, 0))
    lat2 = max(min(st["lat"] + 5 * (1 if st["lat"] < 30 else -1), 70), -60)
    t0 = time.time()
    for tgt in ({"center": [lon2, lat2], "zoom": z2}, {"center": [st["lon"], st["lat"]], "zoom": st["zoom"]}):
        page.evaluate("""(t) => new Promise(res => { const m = window.__perfMap; let done = false;
            const fin = () => { if (!done) { done = true; res(); } };
            m.once('moveend', fin); setTimeout(fin, 8000); m.flyTo({ center: t.center, zoom: t.zoom, duration: 2500, essential: true }); })""", tgt)
        page.wait_for_timeout(250)
    return {"from": st, "to": {"lon": round(lon2, 3), "lat": round(lat2, 3), "zoom": z2}, "fly_wall_s": round(time.time() - t0, 2)}


def wheel_zoom(page: Page, box: dict):
    page.mouse.move(box["cx"], box["cy"])
    for _ in range(8):
        page.mouse.wheel(0, -240)
        page.wait_for_timeout(60)
    page.wait_for_timeout(900)
    for _ in range(8):
        page.mouse.wheel(0, 240)
        page.wait_for_timeout(60)
    page.wait_for_timeout(900)


def visible(page: Page, sel: str):
    loc = page.locator(sel)
    for i in range(min(loc.count(), 30)):
        el = loc.nth(i)
        try:
            if el.is_visible():
                return el
        except Exception:
            pass
    return None


def left_toggle(page: Page) -> dict:
    for sel in LEFT_TOGGLE:
        el = visible(page, sel)
        if el is not None:
            def go():
                for _ in range(2):  # close, open, close, open
                    el.click()
                    page.wait_for_timeout(700)
                    el2 = visible(page, sel)
                    if el2 is None:
                        break
                    el2.click()
                    page.wait_for_timeout(700)
            r = rec(page, go)
            r["how"] = f"left column toggle {sel} ×2"
            return r
    # v2 case UI has no left-column collapse: the nearest analogue is opening / closing the right card
    item = visible(page, "[data-testid=zone-item]") or visible(page, "[data-testid=obs-item]")
    if item is not None:
        def go2():
            for _ in range(2):
                it = visible(page, "[data-testid=zone-item]") or visible(page, "[data-testid=obs-item]")
                it.click()
                page.wait_for_timeout(900)
                cl = visible(page, "[data-testid=card-close]")
                if cl is not None:
                    cl.click()
                else:
                    page.keyboard.press("Escape")
                page.wait_for_timeout(900)
        r = rec(page, go2)
        r["how"] = "no left-column toggle; fallback: open zone card from the list + close it ×2 (includes the map flyTo to the zone)"
        return r
    return {"skipped": "no left-column toggle and no list item found"}


def views(page: Page) -> dict:
    loc = page.locator(VIEW_TABS)
    tabs, seen = [], set()
    for i in range(min(loc.count(), 20)):
        el = loc.nth(i)
        try:
            if not el.is_visible():
                continue
            key = (el.get_attribute("data-testid") or "") + "|" + (el.inner_text() or "")[:30]
        except Exception:
            continue
        if key in seen:
            continue
        seen.add(key)
        tabs.append((key, el))
    if len(tabs) < 2:
        return {"skipped": f"found {len(tabs)} view tab(s) ({VIEW_TABS})"}
    per = []

    def go():
        for key, el in tabs + tabs[:1]:
            n0 = page.evaluate("window.__rec.d.length")
            t0 = time.time()
            try:
                el.click(timeout=3000)
            except Exception as e:
                per.append({"tab": key, "error": str(e)[:120]})
                continue
            page.wait_for_timeout(700)
            mx = page.evaluate("(n0) => { const d = window.__rec.d.slice(n0); return d.length ? Math.round(Math.max(...d)) : null; }", n0)
            per.append({"tab": key, "max_frame_ms": mx, "click_wall_ms": round((time.time() - t0) * 1000)})
    r = rec(page, go)
    r["tabs"] = per
    return r


PICK_JS = """() => { const m = window.__perfMap; if (!m) return null; const cv = m.getCanvas().getBoundingClientRect();
  const fs = m.queryRenderedFeatures(); const out = [];
  for (const f of fs) { const g = f.geometry; if (!g) continue; let c = null;
    if (g.type === 'Point') c = g.coordinates; else if (g.type === 'MultiPoint' || g.type === 'LineString') c = g.coordinates[0];
    else if (g.type === 'Polygon') c = g.coordinates[0][0];
    if (!c) continue; const pt = m.project(c); const x = cv.left + pt.x, y = cv.top + pt.y;
    if (x < cv.left + 420 || x > cv.right - 40 || y < cv.top + 40 || y > cv.bottom - 40) continue;
    const lid = f.layer && f.layer.id; out.push({ x, y, layer: lid, type: g.type }); if (out.length > 2000) break; }
  const rank = (a) => (/zone|scene|det|strip|band/.test(a.layer || '') ? 0 : 2) + (a.type === 'Point' ? 0 : 1);
  out.sort((a, b) => rank(a) - rank(b)); return out; }"""


def _studio_tabs(page: Page):
    return page.locator("[data-testid=studio] [role=tab]")


def _enter_studio(page: Page, c: dict) -> bool:
    page.mouse.click(c["x"], c["y"])
    page.wait_for_timeout(700)
    card = visible(page, "[data-testid=to-studio]")
    if card is None:
        return False
    card.click()
    try:
        page.wait_for_selector("[data-testid=studio]", timeout=10000)
    except Exception:
        return False
    page.wait_for_timeout(800)
    return True


def _studio_pick_scene(page: Page) -> int:
    if _studio_tabs(page).count() == 0:
        tl = page.locator("[data-testid=timeline] button:not([disabled])")
        if tl.count():
            tl.first.click(timeout=3000)
            page.wait_for_timeout(1500)
    return _studio_tabs(page).count()


def _studio_back(page: Page):
    b = visible(page, "[data-testid=back]")
    if b is not None:
        b.click()
        page.wait_for_timeout(1200)


ZONE_CENTERS_JS = """async () => { const m = window.__perfMap; const out = [];
  for (const [id, src] of Object.entries(m.getStyle().sources)) { if (src.type !== 'geojson' || !/zone|scene|strip/.test(id)) continue;
    try { const s = m.getSource(id); const d = s.getData ? await s.getData() : src.data;
      for (const f of (d && d.features) || []) { const g = f.geometry; if (!g) continue;
        const c = g.type === 'Point' ? g.coordinates : g.type === 'Polygon' ? g.coordinates[0][0] : g.type === 'MultiPolygon' ? g.coordinates[0][0][0] : null;
        if (c) out.push({ src: id, c }); if (out.length > 60) break; } } catch (e) {} }
  return out; }"""


def _try_candidates(page: Page, pool: list, tried: list):
    chosen = fallback = None
    for c in pool:
        if not _enter_studio(page, c):
            tried.append({"layer": c["layer"], "studio": False})
            page.keyboard.press("Escape")
            continue
        n = _studio_pick_scene(page)
        tried.append({"layer": c["layer"], "studio": True, "tabs": n})
        _studio_back(page)
        wait_map_still(page)
        if fallback is None:
            fallback = c
        if n > 0:
            chosen = c
            break
    return chosen, fallback


def _pool(page: Page, per: int = 2, cap: int = 8) -> list:
    cands = page.evaluate(PICK_JS) or []
    per_layer: dict = defaultdict(int)
    pool = []
    for c in cands:
        if per_layer[c["layer"]] < per:
            per_layer[c["layer"]] += 1
            pool.append(c)
    return pool[:cap]


def studio(page: Page) -> dict:
    """v3: click a map object → card → «В студию» → pick a scene on the timeline → cycle the studio views (role=tab) → «Назад».
    Candidates in the first view are tried (≤ 2 per map layer); if none has a scene image, the map jumps to zone / scene
    footprints (geojson sources named zone|scene|strip) and retries. The found object is re-entered and measured."""
    if not page.evaluate("!!window.__perfMap"):
        return {"skipped": "no map handle"}
    if page.locator("[data-testid=nav-studio], [data-testid=studio]").count() == 0:
        return {"skipped": "no studio in this UI (no data-testid=nav-studio)"}
    tried: list = []
    chosen, fallback = _try_candidates(page, _pool(page), tried)
    moved = None
    if chosen is None:  # nothing with a scene in the first view: fly to zone / scene footprints (image-backed strips) and retry
        for z in (page.evaluate(ZONE_CENTERS_JS) or [])[:4]:
            page.evaluate("(c) => window.__perfMap.jumpTo({ center: c, zoom: 7.5 })", z["c"])
            wait_map_still(page)
            page.wait_for_timeout(800)
            moved = z
            ch, fb = _try_candidates(page, _pool(page, per=3, cap=6), tried)
            fallback = fallback or fb
            if ch is not None:
                chosen = ch
                break
    c = chosen or fallback
    if c is None:
        return {"skipped": f"no map object opened a studio ({len(tried)} tried)", "tried": tried}
    info: dict = {"tried": tried, "layer": c["layer"], "with_scene": chosen is not None, "moved_to_zone": moved}

    def go():
        t0 = time.time()
        page.mouse.click(c["x"], c["y"])
        page.wait_for_timeout(700)
        card = visible(page, "[data-testid=to-studio]")
        if card is None:
            info["error"] = "card did not reopen"
            return
        t0 = time.time()
        card.click()
        try:
            page.wait_for_selector("[data-testid=studio]", timeout=10000)
            info["open_studio_ms"] = round((time.time() - t0) * 1000)
        except Exception:
            info["open_studio_ms"] = None
        page.wait_for_timeout(800)
        n0 = page.evaluate("window.__rec.d.length")
        _studio_pick_scene(page)
        info["scene_max_frame_ms"] = page.evaluate("(n0) => { const d = window.__rec.d.slice(n0); return d.length ? Math.round(Math.max(...d)) : null; }", n0)
        tabs = _studio_tabs(page)
        per = []
        for i in list(range(min(tabs.count(), 8))) + ([0] if tabs.count() > 1 else []):
            n0 = page.evaluate("window.__rec.d.length")
            tabs.nth(i).click()
            page.wait_for_timeout(900)
            per.append({"tab": (tabs.nth(i).inner_text() or "")[:20],
                        "max_frame_ms": page.evaluate("(n0) => { const d = window.__rec.d.slice(n0); return d.length ? Math.round(Math.max(...d)) : null; }", n0)})
        info["tabs"] = per
        _studio_back(page)
    r = rec(page, go)
    r.update(info)
    return r


def cpu_profile_summary(prof: dict, top: int = 25) -> dict:
    nodes = {n["id"]: n for n in prof["nodes"]}
    self_t: dict[int, float] = defaultdict(float)
    samples, deltas = prof.get("samples", []), prof.get("timeDeltas", [])
    for i, sid in enumerate(samples):
        dt = deltas[i + 1] if i + 1 < len(deltas) else 0
        self_t[sid] += dt / 1000.0
    by_fn: dict[tuple, float] = defaultdict(float)
    by_url: dict[str, float] = defaultdict(float)
    total = 0.0
    for nid, ms in self_t.items():
        cf = nodes[nid]["callFrame"]
        name = cf.get("functionName") or "(anonymous)"
        url = cf.get("url", "")
        short = url.rsplit("/", 1)[-1].split("?")[0] if url else ""
        by_fn[(name, short, cf.get("lineNumber", -1) + 1, cf.get("columnNumber", -1) + 1)] += ms
        by_url[short or name] += ms
        total += ms
    idle = sum(v for (n, *_), v in by_fn.items() if n in ("(idle)", "(program)"))
    fns = sorted(by_fn.items(), key=lambda kv: -kv[1])
    return {
        "total_ms": round(total), "idle_program_ms": round(idle),
        "top_self": [{"fn": k[0], "file": k[1], "line": k[2], "col": k[3], "ms": round(v, 1), "share_busy": round(v / max(total - idle, 1e-9), 3)}
                     for k, v in fns if k[0] not in ("(idle)", "(program)")][:top],
        "by_file": [{"file": k, "ms": round(v, 1)} for k, v in sorted(by_url.items(), key=lambda kv: -kv[1])[:15]],
    }


def trace_summary(path: Path, top: int = 20) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    evs = data["traceEvents"] if isinstance(data, dict) else data
    tnames: dict[tuple, str] = {}
    pnames: dict[int, str] = {}
    for e in evs:
        if e.get("ph") == "M" and e.get("name") == "thread_name":
            tnames[(e["pid"], e["tid"])] = e["args"]["name"]
        if e.get("ph") == "M" and e.get("name") == "process_name":
            pnames[e["pid"]] = e["args"]["name"]
    # the renderer main thread with the most events = the page
    cnt: dict[tuple, int] = defaultdict(int)
    for e in evs:
        if e.get("ph") == "X" and tnames.get((e.get("pid"), e.get("tid"))) == "CrRendererMain":
            cnt[(e["pid"], e["tid"])] += 1
    main = max(cnt, key=cnt.get) if cnt else None
    agg: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for e in evs:
        if e.get("ph") != "X" or "dur" not in e:
            continue
        key = (e.get("pid"), e.get("tid"))
        tn = tnames.get(key, "?")
        if key == main:
            grp = "renderer_main"
        elif pnames.get(e.get("pid"), "").startswith("GPU") and tn in ("CrGpuMain", "VizCompositorThread"):
            grp = f"gpu:{tn}"
        elif key[0] == (main or (None,))[0] and tn == "Compositor":
            grp = "renderer_compositor"
        else:
            continue
        agg[grp][e["name"]] += e["dur"] / 1000.0
    # top-level only would need nesting; totals include nested time (FunctionCall ⊃ v8.* etc.) — read as "where", not sums
    return {g: [{"event": k, "ms": round(v, 1)} for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:top]] for g, d in agg.items()}


def load_profile(browser, url: str, w: int, h: int, out: Path) -> dict:
    """CPU profile of a cold page load (navigation → map tiles idle or 8 s): where the first show is spent."""
    ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
    ctx.add_init_script(INIT_JS)
    page = ctx.new_page()
    cdp = ctx.new_cdp_session(page)
    cdp.send("Profiler.enable")
    cdp.send("Profiler.setSamplingInterval", {"interval": 250})
    cdp.send("Profiler.start")
    t0 = time.time()
    page.goto(url, wait_until="load", timeout=60000)
    try:
        page.wait_for_function("window.__perfT && (window.__perfT.tilesIdle !== null || window.__perfT.firstTileDraw !== null)", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(500)
    prof = cdp.send("Profiler.stop")["profile"]
    wall = round(time.time() - t0, 2)
    ctx.close()
    (out / "cpu_load.cpuprofile").write_text(json.dumps(prof), encoding="utf-8")
    return {"wall_s": wall, "cpu": cpu_profile_summary(prof, top=15)}


def _index_sha(url: str) -> str | None:
    import hashlib
    import urllib.request
    try:
        return hashlib.sha1(urllib.request.urlopen(url, timeout=10).read()).hexdigest()[:12]
    except Exception:
        return None


def run(args) -> dict:
    url = args.url if args.url.endswith("/") or "?" in args.url else args.url + "/"
    host = url.split("//", 1)[-1].split("/", 1)[0]
    out = Path(args.out) if args.out else ROOT / "reports" / "perf" / args.label
    if not out.is_absolute():
        out = ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    res: dict = {"url": url, "label": args.label, "gl": args.gl, "cpu_throttle": args.throttle, "headless": not args.headed,
                 "started": time.strftime("%Y-%m-%d %H:%M:%S"), "sizes": {}, "notes": []}
    con = Console(host)
    bundle: dict[str, dict] = {}

    def on_resp(r):
        u = r.url.split("?")[0]
        if host not in r.url or not re.search(r"\.(js|mjs|css)$", u) or u in bundle:
            return
        try:
            b = r.body()
        except Exception:
            return
        bundle[u] = {"bytes": len(b), "gzip": len(gzip.compress(b, 9)), "t": time.time()}

    sizes = [tuple(int(x) for x in s.lower().split("x")) for s in args.sizes.split(",")]
    with sync_playwright() as pw:
        kw = {"headless": not args.headed, "args": GL_ARGS[args.gl]}
        if not args.headed:
            kw["channel"] = "chromium"  # full Chromium in new headless mode (GPU-capable), not the headless shell
        try:
            browser = pw.chromium.launch(**kw)
        except Exception as e:
            res["notes"].append(f"channel=chromium failed ({str(e)[:80]}), fallback to default headless")
            kw.pop("channel", None)
            browser = pw.chromium.launch(**kw)
        res["browser_version"] = browser.version
        for si, (w, h) in enumerate(sizes):
            tag = f"{w}x{h}"
            print(f"[{args.label}] {tag}")
            R: dict = {}
            res["sizes"][tag] = R
            ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
            ctx.add_init_script(INIT_JS)
            page = ctx.new_page()
            if args.throttle > 1:
                ctx.new_cdp_session(page).send("Emulation.setCPUThrottlingRate", {"rate": args.throttle})
            con.attach(page)
            if si == 0:
                page.on("response", on_resp)
            n_err0 = len(con.errors)
            t_nav = time.time()
            page.goto(url, wait_until="load", timeout=60000)
            wait_first_show(page, R)
            T = collect_first_show(page)
            R["first_show"] = T
            R["map_style"] = page.evaluate("""() => { const m = window.__perfMap; if (!m) return null; try { const s = m.getStyle();
                const by = {}; for (const v of Object.values(s.sources)) by[v.type] = (by[v.type] || 0) + 1;
                return { projection: m.getProjection ? (m.getProjection() || {}).type || null : null, n_sources: Object.keys(s.sources).length,
                         sources_by_type: by, n_layers: s.layers.length, n_canvas: document.querySelectorAll('canvas').length,
                         dpr: devicePixelRatio, n_dom: document.getElementsByTagName('*').length }; } catch (e) { return { error: String(e) }; } }""")
            if si == 0:
                R["gpu"] = page.evaluate("""() => { const c = document.createElement('canvas'); const g = c.getContext('webgl2') || c.getContext('webgl');
                    if (!g) return null; const x = g.getExtension('WEBGL_debug_renderer_info');
                    return x ? g.getParameter(x.UNMASKED_RENDERER_WEBGL) : g.getParameter(g.RENDERER); }""")
            box = map_box(page)
            R["map_box"] = box
            page.screenshot(path=str(out / f"first_{tag}.png"))
            R["idle"] = rec(page, lambda: page.wait_for_timeout(3000))
            has_map = bool(T.get("map_handle"))
            if box:
                dx, dy = -min(450, box["w"] * 0.35), min(160, box["h"] * 0.2)
                R["pan"] = [rec(page, lambda: drag_pan(page, box["cx"], box["cy"], dx, dy)) for _ in range(args.repeats)]
                wait_map_still(page)
                R["wheel_zoom"] = rec(page, lambda: wheel_zoom(page, box))
                wait_map_still(page)
            else:
                R["pan"] = [{"skipped": "no map canvas"}]
            R["flyto"] = rec(page, lambda: fly(page)) if has_map else {"skipped": "no map handle on window (expose window.__map)"}
            wait_map_still(page)
            try:
                R["left_toggle"] = left_toggle(page)
            except Exception as e:
                R["left_toggle"] = {"error": str(e)[:200]}
            wait_map_still(page)
            try:
                R["views"] = views(page)
            except Exception as e:
                R["views"] = {"error": str(e)[:200]}
            wait_map_still(page)
            try:
                R["studio"] = studio(page)
            except Exception as e:
                R["studio"] = {"error": str(e)[:200]}
            wait_map_still(page)
            page.screenshot(path=str(out / f"end_{tag}.png"))
            R["console_errors_this_size"] = con.errors[n_err0:]
            if args.loads > 1:
                ex = extra_loads(browser, url, w, h, args.loads - 1, args.throttle)
                vals = sorted(v for v in [T.get("first_show_s")] + [e["first_show_s"] for e in ex] if v)
                R["first_show_loads"] = ex
                R["first_show_median_s"] = vals[len(vals) // 2] if vals else None
                R["first_show_all_s"] = vals

            if si == 0 and not args.no_profile and box:
                cdp = ctx.new_cdp_session(page)
                cdp.send("Profiler.enable")
                cdp.send("Profiler.setSamplingInterval", {"interval": 250})
                trace_path = out / "trace.json"
                browser.start_tracing(page=page, path=str(trace_path), screenshots=False,
                                      categories=["devtools.timeline", "disabled-by-default-devtools.timeline",
                                                  "disabled-by-default-devtools.timeline.frame", "v8.execute", "gpu", "toplevel"])
                cdp.send("Profiler.start")
                prof_scen = {}
                t0 = time.time()
                drag_pan(page, box["cx"], box["cy"], -min(450, box["w"] * 0.35), min(160, box["h"] * 0.2))
                prof_scen["pan_s"] = round(time.time() - t0, 2)
                wait_map_still(page)
                if has_map:
                    t0 = time.time()
                    fly(page)
                    prof_scen["flyto_s"] = round(time.time() - t0, 2)
                prof = cdp.send("Profiler.stop")["profile"]
                browser.stop_tracing()
                (out / "cpu_pan_flyto.cpuprofile").write_text(json.dumps(prof), encoding="utf-8")
                with open(trace_path, "rb") as fi, gzip.open(str(trace_path) + ".gz", "wb") as fo:
                    fo.write(fi.read())
                trace_path.unlink()
                res["profile"] = {"scenario": prof_scen, "cpu": cpu_profile_summary(prof),
                                  "trace": trace_summary(Path(str(trace_path) + ".gz"))}
            ctx.close()
            if si == 0 and not args.no_profile:
                res["load_profile"] = load_profile(browser, url, w, h, out)
        browser.close()

    js = {k: v for k, v in bundle.items()}
    res["bundle"] = {
        "files": len(js),
        "js_css_bytes": sum(v["bytes"] for v in js.values()),
        "js_css_gzip_bytes": sum(v["gzip"] for v in js.values()),
        "js_css_gzip_mb": round(sum(v["gzip"] for v in js.values()) / 1e6, 3),
        "largest": sorted([{"file": k.rsplit("/", 1)[-1], "bytes": v["bytes"], "gzip": v["gzip"]} for k, v in js.items()],
                          key=lambda d: -d["gzip"])[:10],
        "note": "every JS/CSS response of the first viewport session (initial + lazy chunks actually loaded), gzip -9 of the body",
    }
    entry = sorted(k.rsplit("/", 1)[-1] for k in js if re.match(r".*/index-[^/]+\.js$", k))
    res["build"] = {"entry_js": entry, "index_html_sha1": _index_sha(url)}
    res["console"] = {"errors": con.errors, "n_errors": len(con.errors), "failed_local": con.failed_local,
                      "external": con.external[:20], "n_external": len(con.external), "warnings": con.warnings[:20]}
    res["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (out / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "report.md").write_text(render_md(res), encoding="utf-8")
    print(f"  -> {show_path(out / 'result.json')}, {show_path(out / 'report.md')}")
    print("  " + one_line(res))
    return res


def _f(r: dict | None, k: str):
    if not r or k not in r or r[k] is None:
        return "—"
    return r[k]


def scen_rows(R: dict):
    rows = []
    for i, p in enumerate(R.get("pan") or []):
        rows.append((f"pan #{i + 1}", p))
    for k in ("wheel_zoom", "flyto", "left_toggle", "views", "studio", "idle"):
        rows.append((k, R.get(k)))
    return rows


def one_line(res: dict) -> str:
    s0 = next(iter(res["sizes"].values()), {})
    pans = [p.get("fps") for p in (s0.get("pan") or []) if p and p.get("fps")]
    pan = round(sum(pans) / len(pans), 1) if pans else None
    fl = (s0.get("flyto") or {}).get("fps")
    fs = s0.get("first_show_median_s") or (s0.get("first_show") or {}).get("first_show_s")
    return (f"fps pan {pan}, flyTo {fl}, first show {fs} s (1920x1080); console errors {res['console']['n_errors']}; "
            f"bundle gzip {res['bundle']['js_css_gzip_mb']} MB")


def render_md(res: dict) -> str:
    L = [f"# Perf — {res['label']} ({res['url']})", "",
         f"{res['started']} … {res['finished']}; Chromium {res.get('browser_version')}, gl={res['gl']}, headless={res['headless']}, CPU throttle ×{res.get('cpu_throttle')}",
         f"Сборка: {res.get('build')}", "", "**Сводка:** " + one_line(res), ""]
    for tag, R in res["sizes"].items():
        fs = R.get("first_show", {})
        L += [f"## {tag}", "",
              f"GPU: {R.get('gpu', '—')}" if R.get("gpu") else "",
              f"Первый показ: медиана **{R.get('first_show_median_s')} с** по {len(R.get('first_show_all_s') or [])} холодным загрузкам {R.get('first_show_all_s')}; "
              f"в первой загрузке {fs.get('first_show_s')} с ({fs.get('first_show_basis')}); WebGL-контекст {fs.get('gl_context_ms')} мс, "
              f"первый GL-draw {fs.get('first_gl_draw_ms')} мс, первый тайл пришёл {fs.get('first_tile_response_ms')} мс, "
              f"draw после тайла {fs.get('first_tile_draw_ms')} мс, тайлы загружены (map idle) {fs.get('tiles_idle_ms')} мс; "
              f"FCP {fs.get('fcp_ms')} мс, LCP {fs.get('lcp_ms')} мс, DCL {fs.get('dom_content_loaded_ms')} мс; "
              f"handle карты: {fs.get('map_handle')}; запросов тайлов {fs.get('n_tile_requests')}.", "",
              f"Стиль карты: {R.get('map_style')}", "",
              "| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |",
              "|---|---|---|---|---|---|---|---|---|"]
        for name, r in scen_rows(R):
            r = r or {}
            if "skipped" in r or "error" in r:
                L.append(f"| {name} | — | — | — | — | — | — | — | {r.get('skipped') or r.get('error')} |")
                continue
            note = r.get("how", "")
            if name == "studio" and not r.get("tabs"):
                note = f"открытие студии {r.get('open_studio_ms')} мс; снимка с видами не найдено (слой {r.get('layer')}, попыток {len(r.get('tried') or [])})"
            if name in ("views", "studio") and r.get("tabs"):
                note = "; ".join(f"{t['tab'].split('|')[0] or t['tab']}: {t.get('max_frame_ms')} мс" for t in r["tabs"])
                if name == "studio":
                    note = f"открытие студии {r.get('open_studio_ms')} мс, выбор снимка max кадр {r.get('scene_max_frame_ms')} мс; виды: " + note
            L.append(f"| {name} | {_f(r, 'fps')} | {_f(r, 'frames')} | {_f(r, 'long50')} ({_f(r, 'long50_share')}) | {_f(r, 'over17_share')} | {_f(r, 'p95_ms')} | "
                     f"{_f(r, 'max_ms')} | {_f(r, 'events_max_ms')} | {note} |")
        L.append("")
    pr = res.get("profile")
    if pr:
        L += ["## Профиль CPU (pan + flyTo, 1-й размер)", "",
              f"Сценарий: {pr['scenario']}; всего {pr['cpu']['total_ms']} мс, из них idle/program {pr['cpu']['idle_program_ms']} мс.", "",
              "| функция | файл:строка | self, мс | доля занятого |", "|---|---|---|---|"]
        for t in pr["cpu"]["top_self"][:15]:
            L.append(f"| `{t['fn']}` | {t['file']}:{t['line']} | {t['ms']} | {t['share_busy']} |")
        L += ["", "По файлам (self): " + ", ".join(f"{b['file']} {b['ms']}" for b in pr["cpu"]["by_file"][:8]), "",
              "Trace (включая вложенное время):"]
        for g, lst in pr["trace"].items():
            L.append(f"- {g}: " + ", ".join(f"{e['event']} {e['ms']}" for e in lst[:10]))
        L.append("")
    lp = res.get("load_profile")
    if lp:
        L += ["## Профиль CPU холодной загрузки (1-й размер)", "",
              f"{lp['wall_s']} с; всего {lp['cpu']['total_ms']} мс, idle/program {lp['cpu']['idle_program_ms']} мс.", "",
              "По файлам (self): " + ", ".join(f"{b['file']} {b['ms']}" for b in lp["cpu"]["by_file"][:10]), "",
              "Топ функций: " + ", ".join(f"`{t['fn']}` ({t['file']}:{t['line']}) {t['ms']}" for t in lp["cpu"]["top_self"][:8]), ""]
    b = res["bundle"]
    L += ["## Бандл", "", f"{b['files']} файлов JS/CSS, {b['js_css_bytes']} Б, gzip {b['js_css_gzip_bytes']} Б ({b['js_css_gzip_mb']} МБ).", "",
          "| файл | байт | gzip |", "|---|---|---|"]
    for f in b["largest"]:
        L.append(f"| {f['file']} | {f['bytes']} | {f['gzip']} |")
    c = res["console"]
    L += ["", "## Консоль", "", f"Ошибок: {c['n_errors']}; внешних (хосты подложки): {c['n_external']}; упавших локальных запросов: {len(c['failed_local'])}."]
    for e in c["errors"][:15]:
        L.append(f"- {e}")
    for e in c["failed_local"][:10]:
        L.append(f"- failed: {e}")
    if res["notes"]:
        L += ["", "Примечания: " + "; ".join(res["notes"])]
    return "\n".join(x for x in L if x is not None) + "\n"


FINAL_PAIR = ("v2_final", "v3_iter8_final")


def final_summary() -> list[str]:
    """Top block of compare.md: v2 vs v3 final (both viewports, CPU×1 and ×4), if those labels were measured."""
    rs = {}
    for lab in FINAL_PAIR:
        for suf in ("", "_cpu4"):
            f = ROOT / "reports" / "perf" / (lab + suf) / "result.json"
            if f.exists():
                rs[lab + suf] = json.loads(f.read_text(encoding="utf-8"))
    if len(rs) < 2:
        return []

    def pan(R):
        v = [p["fps"] for p in R.get("pan") or [] if p and p.get("fps")]
        return round(sum(v) / len(v)) if v else "—"

    def row(name, fn):
        cells = []
        for lab in FINAL_PAIR:
            for suf in ("", "_cpu4"):
                r = rs.get(lab + suf)
                cells.append(" / ".join(str(fn(R, r)) for R in r["sizes"].values()) if r else "—")
        return f"| {name} | " + " | ".join(cells) + " |"

    def ui_max(R, r):
        return round(max((R.get(k) or {}).get("max_ms") or 0 for k in ("left_toggle", "views", "studio")))
    L = ["# Perf: v2 против v3 — финальный замер", "",
         f"Метки: {', '.join(rs)}. Ячейка: 1920×1080 / 1366×768. Первый показ — медиана 3 холодных загрузок.", "",
         "| метрика | v2 | v2 CPU×4 | v3 iter8 | v3 iter8 CPU×4 |", "|---|---|---|---|---|",
         row("первый показ, с", lambda R, r: R.get("first_show_median_s")),
         row("pan, fps", lambda R, r: pan(R)),
         row("flyTo, fps", lambda R, r: round((R.get("flyto") or {}).get("fps") or 0)),
         row("flyTo, доля кадров > 17.5 мс", lambda R, r: (R.get("flyto") or {}).get("over17_share")),
         row("UI (колонка/карточка, разделы, студия): max кадр, мс", ui_max),
         row("источников карты / из них image", lambda R, r: f"{(R.get('map_style') or {}).get('n_sources')}/{((R.get('map_style') or {}).get('sources_by_type') or {}).get('image', 0)}"),
         row("бандл JS+CSS gzip, МБ", lambda R, r: r["bundle"]["js_css_gzip_mb"]),
         row("ошибки консоли (внешние хосты подложки)", lambda R, r: f"{r['console']['n_errors']} ({r['console']['n_external']})"),
         "", "Ниже — все замеры (история итераций).", ""]
    return L


def compare(labels: list[str]) -> str:
    """reports/perf/compare.md: one row per label × viewport (first show, fps / long-frame share per scenario, bundle, errors)."""
    L = ["# Perf — сравнение (scripts/perf_v3.py --compare)", "",
         "| метка | размер | CPU× | первый показ, с | pan fps (>17.5 мс) | flyTo fps (>17.5 мс) | колонка fps (max, мс) | виды fps (max, мс) | источников/image | бандл gzip, МБ | ошибок |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for lab in labels:
        f = ROOT / "reports" / "perf" / lab / "result.json"
        if not f.exists():
            continue
        r = json.loads(f.read_text(encoding="utf-8"))
        for tag, R in r["sizes"].items():
            pans = [p for p in (R.get("pan") or []) if p and p.get("fps")]
            pf = f"{round(sum(p['fps'] for p in pans) / len(pans), 1)} ({round(sum(p['over17_share'] for p in pans) / len(pans), 2)})" if pans and "over17_share" in pans[0] else "—"
            fl = R.get("flyto") or {}
            ms = R.get("map_style") or {}
            cell = lambda d: f"{d.get('fps')} ({d.get('max_ms')})" if d and d.get("fps") else "—"  # noqa: E731
            L.append(f"| {lab} | {tag} | {r.get('cpu_throttle', 1)} | {R.get('first_show_median_s') or (R.get('first_show') or {}).get('first_show_s')} | {pf} | "
                     f"{fl.get('fps', '—')} ({fl.get('over17_share', '—')}) | {cell(R.get('left_toggle'))} | {cell(R.get('views'))} | "
                     f"{ms.get('n_sources', '—')}/{(ms.get('sources_by_type') or {}).get('image', 0)} | {r['bundle']['js_css_gzip_mb']} | {r['console']['n_errors']} |")
    md = "\n".join(final_summary() + L) + "\n"
    (ROOT / "reports" / "perf" / "compare.md").write_text(md, encoding="utf-8")
    return md


def main():
    if "--compare" in sys.argv:
        labs = sys.argv[sys.argv.index("--compare") + 1:]
        if not labs:
            labs = sorted(p.name for p in (ROOT / "reports" / "perf").iterdir() if (p / "result.json").exists())
        print(compare(labs))
        return
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", default=None, help="default reports/perf/<label>")
    ap.add_argument("--sizes", default="1920x1080,1366x768")
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--gl", default="gpu", choices=list(GL_ARGS))
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--no-profile", action="store_true")
    ap.add_argument("--loads", type=int, default=3, help="cold loads per viewport for the first-show median")
    ap.add_argument("--throttle", type=float, default=1, help="CDP CPU throttling rate (4 = weak laptop proxy); measurements only, profiles unthrottled")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
