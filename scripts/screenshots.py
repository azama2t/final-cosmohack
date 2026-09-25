"""UI screenshot scenario + performance probe for the macroplastic map (SPEC §6).

Usage (from repo root):
  .venv\\Scripts\\python.exe scripts\\screenshots.py --base-url http://127.0.0.1:5173 --out reports\\screens\\iter1 --start-dev
  .venv\\Scripts\\python.exe scripts\\screenshots.py --base-url http://127.0.0.1:8000 --out reports\\screens\\final
  .venv\\Scripts\\python.exe scripts\\screenshots.py --start-preview --out reports\\screens\\prod   (vite preview of service/static)
  add --video to record the demo tour into <out>/tour.webm

Writes 10 PNG frames:
  01_overview_1920 02_region 03_detection_card 04_prob_layer 05_h3_2d 06_h3_3d 07_zones 08_compare 09_drift 10_overview_1366
and appends a row to reports/ui_perf.md (load_ms, console errors, fps during drift animation and during flyTo).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FRONT = ROOT / "service" / "frontend"
PERF_MD = ROOT / "reports" / "ui_perf.md"
EXTERNAL_HOSTS = ("arcgisonline.com", "cartocdn.com", "openstreetmap", "carto.com")
GL_ARGS = {
    # default: software WebGL (works everywhere, same as the orchestrator review); fps is then CPU-bound
    "swiftshader": ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
    # real GPU (RTX 4070 via ANGLE/D3D11) — use for fps measurements
    "gpu": ["--use-angle=d3d11", "--ignore-gpu-blocklist", "--enable-gpu", "--enable-gpu-rasterization"],
}


# ---------------------------------------------------------------- helpers
def wait_http(url: str, timeout: float = 60) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status < 500:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def start_server(kind: str, port: int) -> subprocess.Popen:
    npx = "npx.cmd" if os.name == "nt" else "npx"
    cmd = [npx, "vite", "--port", str(port), "--strictPort"] if kind == "dev" else [npx, "vite", "preview", "--port", str(port), "--strictPort"]
    env = dict(os.environ)
    log = open(ROOT / "reports" / "screens" / f".{kind}_server.log", "w", encoding="utf-8")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(cmd, cwd=FRONT, stdout=log, stderr=subprocess.STDOUT, env=env, creationflags=flags)


def stop_server(p: subprocess.Popen | None):
    if not p:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
    else:
        p.terminate()


def measure_fps(page: Page, ms: int) -> float:
    """Counts requestAnimationFrame callbacks for `ms` milliseconds."""
    return page.evaluate(
        """(ms) => new Promise(res => { let n = 0; const t0 = performance.now();
            const f = (t) => { n++; if (t - t0 < ms) requestAnimationFrame(f); else res(Math.round(n * 1000 / (t - t0) * 10) / 10); };
            requestAnimationFrame(f); })""",
        ms,
    )


def fps_while_moving(page: Page, max_ms: int = 6000) -> float:
    """FPS from now until the main map stops moving (flyTo)."""
    return page.evaluate(
        """(maxMs) => new Promise(res => { let n = 0; const t0 = performance.now(); let started = false;
            const f = (t) => { n++; const moving = window.__app && window.__app.isMoving();
              if (moving) started = true;
              if ((started && !moving) || t - t0 > maxMs) res(Math.round(n * 1000 / (t - t0) * 10) / 10);
              else requestAnimationFrame(f); };
            requestAnimationFrame(f); })""",
        max_ms,
    )


def wait_idle(page: Page, extra_ms: int = 600, timeout: int = 12000):
    page.wait_for_function("window.__app && !window.__app.isMoving()", timeout=timeout)
    page.wait_for_timeout(extra_ms)


def click(page: Page, testid: str):
    page.locator(f"[data-testid='{testid}']").first.click()


def shot(page: Page, out: Path, name: str, log: list):
    p = out / f"{name}.png"
    page.screenshot(path=str(p))
    log.append(name)
    print(f"  [shot] {p.relative_to(ROOT)}")


class Console:
    def __init__(self):
        self.errors: list[str] = []
        self.external: list[str] = []
        self.warnings: list[str] = []

    def attach(self, page: Page):
        page.on("console", self._on_console)
        page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        page.on("requestfailed", self._on_failed)

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
        if 'ERR_ABORTED' in (req.failure or ''):  # tiles cancelled during flyTo — normal
            return
        if any(h in req.url for h in EXTERNAL_HOSTS):
            self.external.append(f"requestfailed {req.url[:160]}")


# ---------------------------------------------------------------- scenario
def run(args) -> dict:
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    base = args.base_url.rstrip("/")
    res: dict = {"base_url": base, "shots": [], "notes": [], "gl": args.gl}
    con = Console()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed, args=GL_ARGS[args.gl])
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        page = ctx.new_page()
        con.attach(page)

        # 01 overview ---------------------------------------------------------
        t0 = time.time()
        page.goto(base + "/", wait_until="domcontentloaded")
        page.wait_for_function("window.__mapReady === true", timeout=30000)
        res["load_ms"] = round(page.evaluate("window.__mapReadyAt || 0"))
        res["load_wall_ms"] = round((time.time() - t0) * 1000)
        res["gpu"] = page.evaluate(
            """(() => { try { const g = document.createElement('canvas').getContext('webgl2');
                 const e = g.getExtension('WEBGL_debug_renderer_info'); return g.getParameter(e.UNMASKED_RENDERER_WEBGL); } catch (e) { return String(e); } })()"""
        )
        try:
            page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:
            res["notes"].append("networkidle timeout on overview (external tiles?)")
        page.wait_for_timeout(1200)
        res["projection_default"] = page.evaluate("window.__app && window.__app.projection")
        shot(page, out, "01_overview_1920", res["shots"])

        manifest = page.evaluate("fetch('/data/manifest.json').then(r => r.json())")
        regions = sorted(
            manifest["regions"],
            key=lambda r: ((r.get("summary") or {}).get("n_detections") or 0, (r.get("summary") or {}).get("index_permille") or -1),
            reverse=True,
        )
        # same rule as the demo tour (bestRegion in lib/data.ts: clean freshest scene, detections, drift, index)
        best_id = page.evaluate("window.__app && window.__app.bestRegion")
        best = next((r for r in regions if r["id"] == best_id), regions[0])
        res["best_region"] = best["id"]
        if args.only_l27:  # quick iteration on the L27 frames only
            ctx.close()
            shoot_l27(browser, base, out, res, con, best["id"], regions)
            browser.close()
            res["console_errors"] = con.errors
            res["external_errors"] = sorted(set(con.external))[:20]
            res["console_warnings"] = con.warnings[:20]
            return res
        if args.only_l20:  # quick iteration on the L20 frames only
            ctx.close()
            shoot_l20(browser, base, out, res, con, best["id"], regions, args)
            browser.close()
            res["console_errors"] = con.errors
            res["external_errors"] = sorted(set(con.external))[:20]
            res["console_warnings"] = con.warnings[:20]
            return res
        # overview hover on a label collapsed to a dot (shows the full label)
        if args.extra:
            compact = page.locator(".region-marker.compact")
            res["overview_compact_labels"] = compact.count()
            # L27: on the globe a collapsed dot can sit under a neighbour's dot — hover the first reachable one
            idx = page.evaluate("""(() => [...document.querySelectorAll('.region-marker.compact')].findIndex(el => {
                const r = el.getBoundingClientRect(); const t = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                return t && el.contains(t); }))()""")
            if compact.count() and idx >= 0:
                compact.nth(idx).hover()
                page.wait_for_timeout(400)
                shot(page, out, "17_overview_hover_dot", res["shots"])
                page.mouse.move(5, 500)

        # 02 region: click + measure fps during flyTo -------------------------
        click(page, f"region-item-{best['id']}")
        res["fps_flyto"] = fps_while_moving(page, 7000)
        page.wait_for_function("window.__app && window.__app.sceneReady", timeout=15000)
        wait_idle(page, 1500)
        shot(page, out, "02_region", res["shots"])

        # 03 detection card: hover + click the largest spot -------------------
        pos = page.evaluate("window.__app.largestDetectionScreen()")
        if pos:
            page.mouse.move(pos["x"] - 30, pos["y"] - 30)
            page.mouse.move(pos["x"], pos["y"], steps=5)
            page.wait_for_timeout(400)
            page.mouse.click(pos["x"], pos["y"])
            try:
                page.wait_for_selector("[data-testid='detection-card']", timeout=5000)
            except Exception:
                res["notes"].append("detection card did not open by map click")
            page.wait_for_timeout(1200)
        else:
            res["notes"].append("no detections in best region")
        shot(page, out, "03_detection_card", res["shots"])
        if page.locator("[data-testid='detection-card-close']").count():
            click(page, "detection-card-close")
        page.mouse.move(5, 500)

        # 04 prob ------------------------------------------------------------
        click(page, "layer-toggle-prob")
        page.wait_for_timeout(1500)
        shot(page, out, "04_prob_layer", res["shots"])
        click(page, "layer-toggle-prob")

        # 05 h3 2D -----------------------------------------------------------
        click(page, "layer-toggle-h3")
        page.wait_for_function("window.__app && window.__app.h3Ready", timeout=15000)
        page.wait_for_timeout(1500)
        shot(page, out, "05_h3_2d", res["shots"])

        # 06 h3 3D -----------------------------------------------------------
        click(page, "layer-toggle-h3-3d")
        page.wait_for_timeout(600)
        wait_idle(page, 1500)
        shot(page, out, "06_h3_3d", res["shots"])
        click(page, "layer-toggle-h3-3d")
        page.wait_for_timeout(300)
        click(page, "layer-toggle-h3")
        wait_idle(page, 400)

        # 07 zones -----------------------------------------------------------
        click(page, "layer-toggle-zones")
        page.wait_for_timeout(1500)
        shot(page, out, "07_zones", res["shots"])
        click(page, "layer-toggle-zones")

        # 08 compare ---------------------------------------------------------
        click(page, "compare-button")
        try:
            page.wait_for_function("window.__compareReady_a && window.__compareReady_b", timeout=15000)
        except Exception:
            res["notes"].append("compare panes not ready in 15 s")
        page.wait_for_selector("[data-testid='compare-table']", timeout=10000)
        page.wait_for_timeout(1800)
        shot(page, out, "08_compare", res["shots"])
        click(page, "compare-close")
        page.wait_for_timeout(500)

        # 09 drift: needs a date with drift.json ------------------------------
        drift_ref = None
        for r in [best] + [x for x in regions if x is not best]:
            for d in reversed(r["dates"]):
                if d.get("drift"):
                    drift_ref = (r["id"], d["date"])
                    break
            if drift_ref:
                break
        if drift_ref:
            if drift_ref[0] != best["id"]:
                click(page, f"region-item-{drift_ref[0]}")
                wait_idle(page, 800)
            if page.evaluate("window.__app.date") != drift_ref[1]:
                click(page, f"date-dot-{drift_ref[1]}")
                page.wait_for_timeout(800)
            click(page, "layer-toggle-drift")
            page.wait_for_function("window.__app && window.__app.driftReady", timeout=15000)
            page.wait_for_timeout(500)
            wait_idle(page, 500)
            click(page, "drift-play")
            page.wait_for_timeout(300)
            res["fps_drift"] = measure_fps(page, 5000)
            click(page, "drift-play")  # pause
            page.evaluate("window.__app.setDriftHour(36)")
            page.wait_for_timeout(700)
            res["drift_region"] = drift_ref[0]
            res["drift_ensemble"] = page.evaluate("window.__app.hasEnsemble")
            shot(page, out, "09_drift", res["shots"])
            if args.extra and page.locator("[data-testid='drift-spread']").count():
                click(page, "drift-spread")  # ensemble cloud off
                page.wait_for_timeout(500)
                shot(page, out, "09b_drift_no_spread", res["shots"])
                click(page, "drift-spread")
        else:
            res["fps_drift"] = None
            res["notes"].append("no drift.json in manifest")
            shot(page, out, "09_drift", res["shots"])

        # URL state round-trip check
        res["url_state"] = page.url.replace(base, "")

        # extra: empty state (region with 0 detections on its latest date) ------
        if args.extra:
            if page.locator("[data-testid='drift-play']").count():
                click(page, "layer-toggle-drift")
            empty = [r for r in regions if not (r.get("summary") or {}).get("n_detections")]
            if empty:
                click(page, f"region-item-{empty[0]['id']}")
                page.wait_for_function("window.__app && window.__app.sceneReady", timeout=15000)
                wait_idle(page, 800)
                try:
                    page.wait_for_selector("[data-testid='empty-scene']", timeout=5000)
                except Exception:
                    res["notes"].append("empty-scene card not shown")
                page.wait_for_timeout(600)
                shot(page, out, "12_empty_state", res["shots"])
            # halo at mid zoom vs polygons at close zoom
            click(page, f"region-item-{best['id']}")
            page.wait_for_function("window.__app && window.__app.sceneReady", timeout=15000)
            wait_idle(page, 800)
            pos = page.evaluate("window.__app.largestDetectionScreen()")
            if pos:
                page.evaluate("window.__ctl && window.__ctl.map && window.__ctl.map.easeTo({center: window.__ctl.map.unproject([%d - window.__ctl.map.getContainer().getBoundingClientRect().left, %d - window.__ctl.map.getContainer().getBoundingClientRect().top]), zoom: 15, duration: 0})" % (pos["x"], pos["y"]))
                wait_idle(page, 1200)
                shot(page, out, "13_zoom15_polygons", res["shots"])
        ctx.close()

        # 10 overview 1366 ------------------------------------------------------
        ctx2 = browser.new_context(viewport={"width": 1366, "height": 768}, device_scale_factor=1)
        p2 = ctx2.new_page()
        con.attach(p2)
        p2.goto(base + "/", wait_until="domcontentloaded")
        p2.wait_for_function("window.__mapReady === true", timeout=30000)
        try:
            p2.wait_for_load_state("networkidle", timeout=6000)
        except Exception:
            pass
        p2.wait_for_timeout(1200)
        shot(p2, out, "10_overview_1366", res["shots"])
        if args.extra:
            click(p2, f"region-item-{best['id']}")
            p2.wait_for_function("window.__app && window.__app.sceneReady", timeout=15000)
            wait_idle(p2, 1200)
            shot(p2, out, "11_region_1366", res["shots"])
            click(p2, "layer-toggle-h3")
            p2.wait_for_function("window.__app && window.__app.h3Ready", timeout=15000)
            p2.wait_for_timeout(1200)
            shot(p2, out, "14_h3_1366", res["shots"])
            click(p2, "layer-toggle-h3")
            click(p2, "compare-button")
            try:
                p2.wait_for_function("window.__compareReady_a && window.__compareReady_b", timeout=15000)
            except Exception:
                res["notes"].append("compare 1366 not ready")
            p2.wait_for_timeout(1500)
            shot(p2, out, "15_compare_1366", res["shots"])
            click(p2, "compare-close")
            p2.wait_for_timeout(400)
            if drift_ref:
                if drift_ref[0] != best["id"]:
                    click(p2, f"region-item-{drift_ref[0]}")
                    wait_idle(p2, 800)
                click(p2, "layer-toggle-drift")
                p2.wait_for_function("window.__app && window.__app.driftReady", timeout=15000)
                wait_idle(p2, 800)
                p2.evaluate("window.__app.setDriftHour(48)")
                p2.wait_for_timeout(600)
                shot(p2, out, "16_drift_1366", res["shots"])
            # a date with the haze/glint flag (badge + yellow timeline ring)
            flagged = next(((r["id"], d["date"]) for r in regions for d in r["dates"]
                            if (d.get("quality") or {}).get("haze") or (d.get("quality") or {}).get("glint_or_haze")), None)
            if flagged:
                if p2.locator("[data-testid='drift-play']").count():
                    click(p2, "layer-toggle-drift")
                click(p2, "region-sort-name")
                click(p2, f"region-item-{flagged[0]}")
                wait_idle(p2, 600)
                click(p2, f"date-dot-{flagged[1]}")
                p2.wait_for_function("window.__app && window.__app.sceneReady", timeout=15000)
                wait_idle(p2, 900)
                res["quality_badge"] = p2.locator("[data-testid='quality-badge']").count()
                p2.evaluate("""(() => { const r = document.querySelector('.panel-right .panel-scroll'); if (r) r.scrollTop = 0;
                    const t = document.querySelector("[data-testid='timeline']"); if (t) t.scrollIntoView({block: 'center'}); })()""")
                p2.wait_for_timeout(300)
                shot(p2, out, "18_quality_flag_1366", res["shots"])
        ctx2.close()

        # drift without `ensemble` (older drift.json): strip the field on the fly ----------
        if args.extra and drift_ref:
            ctx4 = browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)

            def strip_ens(route):
                r = route.fetch()
                try:
                    d = r.json()
                    d.pop("ensemble", None)
                    route.fulfill(response=r, body=json.dumps(d), headers={**r.headers, "content-type": "application/json"})
                except Exception:
                    route.fulfill(response=r)

            ctx4.route("**/drift.json", strip_ens)
            p4 = ctx4.new_page()
            con.attach(p4)
            p4.goto(base + f"/?r={drift_ref[0]}&d={drift_ref[1]}", wait_until="domcontentloaded")
            p4.wait_for_function("window.__mapReady === true", timeout=30000)
            p4.wait_for_function("window.__app && window.__app.sceneReady", timeout=15000)
            click(p4, "layer-toggle-drift")
            p4.wait_for_function("window.__app && window.__app.driftReady", timeout=15000)
            wait_idle(p4, 800)
            p4.evaluate("window.__app.setDriftHour(36)")
            p4.wait_for_timeout(600)
            res["noens_spread_toggle"] = p4.locator("[data-testid='drift-spread']").count()
            shot(p4, out, "19_drift_without_ensemble", res["shots"])
            ctx4.close()

        # L20: zone card, place card, calendar, review tab ----------------------------
        if args.l20 or args.only_l20:
            shoot_l20(browser, base, out, res, con, best["id"], regions, args)

        # L27: globe, route, offline coastline, compare opened by a link from the overview ------
        if args.extra:
            shoot_l27(browser, base, out, res, con, best["id"], regions)

        # optional: record demo tour ---------------------------------------------
        if args.video:
            vdir = out / "_video"
            vdir.mkdir(exist_ok=True)
            ctx3 = browser.new_context(
                viewport={"width": 1920, "height": 1080}, record_video_dir=str(vdir), record_video_size={"width": 1920, "height": 1080}
            )
            p3 = ctx3.new_page()
            con.attach(p3)
            p3.goto(base + "/?tour=1", wait_until="domcontentloaded")
            p3.wait_for_function("window.__mapReady === true", timeout=30000)
            p3.wait_for_function("window.__tourRunning === true", timeout=15000)
            t_tour = time.time()
            p3.wait_for_function("window.__tourRunning === false", timeout=120000, polling=500)
            res["tour_s"] = round(time.time() - t_tour, 1)
            p3.wait_for_timeout(800)
            video = p3.video
            ctx3.close()
            if video:
                src = Path(video.path())
                shutil.move(str(src), str(out / "tour.webm"))
                res["video"] = str((out / "tour.webm").relative_to(ROOT))
            shutil.rmtree(vdir, ignore_errors=True)

        browser.close()

    res["console_errors"] = con.errors
    res["external_errors"] = sorted(set(con.external))[:20]
    res["console_warnings"] = con.warnings[:20]
    return res


def _img_ready(page: Page, testids: list[str], timeout: int = 15000) -> bool:
    """Waits until every <img data-testid=...> present on the page has loaded (or none is present)."""
    js = """(ids) => ids.every(id => { const i = document.querySelector(`[data-testid='${id}']`);
              return !i || i.tagName !== 'IMG' || (i.complete && i.naturalWidth > 0); })"""
    try:
        page.wait_for_function(js, arg=testids, timeout=timeout)
        return True
    except Exception:
        return False


def shoot_l20(browser, base: str, out: Path, res: dict, con: "Console", best_id: str, regions: list, args):
    """L20 frames: 21 zone card («почему первая»), 22 place card (+PDF), 23 observation calendar,
    24 review tab, 25 retrain result (only with --review-write), + 1366 variants."""
    info = res.setdefault("l20", {})
    try:
        with urllib.request.urlopen(base + "/openapi.json", timeout=5) as r:
            api = set(json.loads(r.read().decode("utf-8")).get("paths", {}).keys())
    except Exception:
        api = set()
    info["api"] = sorted(p for p in api if any(k in p for k in ("/api/zone", "/api/crop", "/api/place", "/api/calendar", "/api/review")))

    def open_region(page: Page, rid: str, extra: str = ""):
        page.goto(base + f"/?r={rid}{extra}", wait_until="domcontentloaded")
        page.wait_for_function("window.__mapReady === true", timeout=30000)
        page.wait_for_function("window.__app && window.__app.sceneReady", timeout=20000)
        wait_idle(page, 600)

    for vw, vh, sfx in ((1920, 1080, ""), (1366, 768, "_1366")):
        ctx = browser.new_context(viewport={"width": vw, "height": vh}, device_scale_factor=1)
        page = ctx.new_page()
        con.attach(page)
        open_region(page, best_id)

        # 21 zone card: click the first row of the zones table
        page.locator("[data-testid='zone-row-1']").first.click()
        page.wait_for_selector("[data-testid='zone-card']", timeout=8000)
        # the click scrolled the right panel down to the table: back to the top (verdict + calendar visible)
        page.evaluate("(() => { const r = document.querySelector('.panel-right .panel-scroll'); if (r) { r.scrollTop = 0; r.scrollLeft = 0; } })()")
        page.wait_for_timeout(300)
        wait_idle(page, 400)
        info[f"zone_crop_loaded{sfx}"] = _img_ready(page, ["zone-crop-img"])
        info[f"zone_crop_source{sfx}"] = "api" if page.locator("[data-testid='zone-crop-img']").count() else "canvas"
        info[f"zone_why{sfx}"] = page.locator("[data-testid='zone-why']").count()
        if sfx and page.locator("[data-testid='zone-why-eq']").count() == 0 and page.locator("[data-testid='zone-why-toggle']").count():
            click(page, "zone-why-toggle")  # collapsed by default on short screens
            page.wait_for_timeout(200)
        page.wait_for_timeout(500)
        shot(page, out, f"21_zone_card{sfx}", res["shots"])

        # 22 place card from the zone card
        click(page, "zone-open-place")
        page.wait_for_selector("[data-testid='place-table']", timeout=20000)
        page.wait_for_timeout(600)
        info[f"place_rows{sfx}"] = page.locator("[data-testid='place-table'] tbody tr").count()
        pdf = page.locator("[data-testid='place-pdf']")
        info[f"pdf_button{sfx}"] = pdf.count()
        shot(page, out, f"22_place_card{sfx}", res["shots"])
        if pdf.count() and not sfx:
            href = pdf.first.get_attribute("href")
            try:
                r = page.request.get(base + href, timeout=60000)
                body = r.body()
                info["pdf"] = {"status": r.status, "type": r.headers.get("content-type"), "bytes": len(body),
                               "magic": body[:5].decode("latin-1")}
            except Exception as e:  # noqa: BLE001
                info["pdf"] = {"error": str(e)[:200]}
        click(page, "place-card-close")
        page.wait_for_timeout(300)

        # place card from a click on an H3 cell (zone 1 cell centre, H3 layer on, zone markers off)
        if page.evaluate("window.__app.layers.zones"):
            click(page, "layer-toggle-zones")
        click(page, "layer-toggle-h3")
        page.wait_for_function("window.__app && window.__app.h3Ready", timeout=15000)
        page.wait_for_timeout(800)
        pos = page.evaluate("window.__app.zoneScreen(1)")
        if pos:
            page.mouse.move(pos["x"], pos["y"])
            page.wait_for_timeout(200)
            page.mouse.click(pos["x"], pos["y"])
            try:
                page.wait_for_selector("[data-testid='place-table']", timeout=15000)
                info[f"place_from_h3{sfx}"] = page.evaluate("window.__app.placeCard") == pos["h3"]
            except Exception:
                info[f"place_from_h3{sfx}"] = False
            if sfx:
                page.mouse.move(700, 20)  # over the header: clears the H3 hover tooltip
                page.wait_for_timeout(400)
                shot(page, out, "22b_place_from_h3_1366", res["shots"])
            if page.locator("[data-testid='place-card-close']").count():
                click(page, "place-card-close")
        click(page, "layer-toggle-h3")

        # 23 calendar: region with the most dates
        cal = max(regions, key=lambda r: (len(r["dates"]), r["id"] == best_id))
        open_region(page, cal["id"])
        page.wait_for_selector("[data-testid='obs-calendar'] .cal-grid", timeout=10000)
        page.evaluate("document.querySelector(\"[data-testid='obs-calendar']\").scrollIntoView({block: 'nearest'})")
        page.wait_for_timeout(400)
        info[f"calendar_region{sfx}"] = cal["id"]
        info[f"calendar_dots{sfx}"] = page.locator("[data-testid='obs-calendar'] button.cal-dot").count()
        shot(page, out, f"23_calendar{sfx}", res["shots"])
        if not sfx:
            cur = page.evaluate("window.__app.date")
            other = next((d["date"] for d in cal["dates"] if d["date"] != cur and page.locator(f"[data-testid='cal-dot-{d['date']}']:not([disabled])").count()), None)
            if other:
                click(page, f"cal-dot-{other}")
                page.wait_for_timeout(500)
                info["calendar_click_ok"] = page.evaluate("window.__app.date") == other

        # 24 review tab
        review = "/api/review/queue" in api
        info[f"review_tab{sfx}"] = page.locator("[data-testid='tab-review']").count()
        if review:
            if args.review_write and not sfx:
                # «ложное?» from the detection card of the best region → the item appears in the queue with user_flag
                open_region(page, best_id)
                pos = page.evaluate("window.__app.largestDetectionScreen()")
                if pos:
                    page.mouse.click(pos["x"], pos["y"])
                    page.wait_for_selector("[data-testid='flag-false']", timeout=8000)
                    click(page, "flag-false")
                    try:
                        page.wait_for_function("document.querySelector(\"[data-testid='flag-false']\").textContent.includes('в очереди')", timeout=8000)
                        info["flag_false_ok"] = True
                    except Exception:
                        info["flag_false_ok"] = False
                    page.wait_for_timeout(300)
                    shot(page, out, "24a_flag_false", res["shots"])
                    click(page, "detection-card-close")
            else:
                open_region(page, best_id)
            click(page, "tab-review")
            try:
                page.wait_for_selector("[data-testid='review-card']", timeout=20000)
            except Exception:
                info[f"review_card{sfx}"] = False
            info[f"review_imgs{sfx}"] = _img_ready(page, ["review-rgb", "review-false"], 30000)
            page.wait_for_timeout(500)
            info[f"review_queue{sfx}"] = page.evaluate("window.__review && window.__review.n")
            shot(page, out, f"24_review{sfx}", res["shots"])
            if args.review_write and not sfx:
                n0 = page.evaluate("window.__review.labels")
                for key in ("1", "2", "ArrowRight", "6", "1"):
                    page.keyboard.press(key)
                    page.wait_for_timeout(700)
                _img_ready(page, ["review-rgb", "review-false"], 20000)
                info["labels_added"] = page.evaluate("window.__review.labels") - n0
                click(page, "review-retrain")
                t0 = time.time()
                try:
                    page.wait_for_function(
                        "window.__review && window.__review.jobStatus && window.__review.jobStatus !== 'running'",
                        timeout=args.retrain_timeout * 1000, polling=2000)
                except Exception:
                    info["retrain_timeout"] = True
                info["retrain_s"] = round(time.time() - t0, 1)
                info["retrain_status"] = page.evaluate("window.__review.jobStatus")
                dec = page.locator("[data-testid='review-decision']")
                info["retrain_decision"] = dec.first.text_content() if dec.count() else None
                page.wait_for_timeout(400)
                shot(page, out, "25_review_retrain", res["shots"])
        ctx.close()

    # fallbacks: the same UI against a backend without the L19 endpoints (/openapi.json without paths)
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
    ctx.route("**/openapi.json", lambda route: route.fulfill(status=200, content_type="application/json", body='{"paths": {}}'))
    page = ctx.new_page()
    con.attach(page)
    open_region(page, best_id)
    info["noapi_review_tab"] = page.locator("[data-testid='tab-review']").count()
    page.locator("[data-testid='zone-row-1']").first.click()
    page.wait_for_selector("[data-testid='zone-card']", timeout=8000)
    page.evaluate("(() => { const r = document.querySelector('.panel-right .panel-scroll'); if (r) { r.scrollTop = 0; r.scrollLeft = 0; } })()")
    wait_idle(page, 800)
    info["noapi_zone_crop"] = "canvas" if page.locator("[data-testid='zone-crop-canvas']").count() else "img"
    info["noapi_zone_why"] = page.locator("[data-testid='zone-why-eq']").count()
    shot(page, out, "21c_zone_card_no_api", res["shots"])
    click(page, "zone-open-place")
    try:
        page.wait_for_selector("[data-testid='place-table']", timeout=30000)
        info["noapi_place_rows"] = page.locator("[data-testid='place-table'] tbody tr").count()
    except Exception:
        info["noapi_place_rows"] = 0
    info["noapi_pdf_button"] = page.locator("[data-testid='place-pdf']").count()
    info["noapi_calendar_dots"] = page.locator("[data-testid='obs-calendar'] button.cal-dot").count()
    page.wait_for_timeout(400)
    shot(page, out, "22c_place_card_no_api", res["shots"])
    ctx.close()


def _goto_scene(page: Page, url: str, res: dict, tries: int = 2):
    """L27: open a region URL and wait for the scene; one retry (reload) if it did not come up."""
    for i in range(tries):
        page.goto(url, wait_until="load")
        try:
            page.wait_for_function("window.__app && window.__app.sceneReady", timeout=20000)
            return
        except Exception:
            st = page.evaluate("JSON.stringify(window.__app ? {r: window.__app.region, d: window.__app.date, "
                               "ready: window.__app.sceneReady} : null) + ' mapReady=' + window.__mapReady")
            res["notes"].append(f"scene not ready ({url.split('?')[-1]}, try {i + 1}): {st}")
    raise TimeoutError("scene not ready: " + url)


def shoot_l27(browser, base: str, out: Path, res: dict, con: "Console", best_id: str, regions: list):
    """L27 frames: 30 globe (+ fps), 31 route + verdicts, 32 offline coastline, 33 compare by link."""
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
    page = ctx.new_page()
    con.attach(page)
    # 30 globe overview, rotation fps, fly to the best region on the globe
    page.goto(base + "/?pr=globe", wait_until="domcontentloaded")
    page.wait_for_function("window.__mapReady === true", timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=6000)
    except Exception:
        pass
    page.wait_for_timeout(1500)
    shot(page, out, "30_globe", res["shots"])
    res["globe_far_side_hint"] = page.locator("[data-testid='globe-hint']").inner_text() if page.locator(
        "[data-testid='globe-hint']").is_visible() else None
    page.evaluate("""(() => { const m = window.__ctl.map; const c = m.getCenter();
        m.easeTo({ center: [c.lng + 90, c.lat], duration: 3000, easing: (t) => t }); })()""")
    res["fps_globe_rotate"] = measure_fps(page, 2800)
    page.wait_for_timeout(600)
    page.evaluate("window.__ctl.map.stop()")
    click(page, f"region-item-{best_id}")
    res["fps_globe_flyto"] = fps_while_moving(page, 8000)
    page.wait_for_function("window.__app && window.__app.sceneReady", timeout=15000)
    wait_idle(page, 1500)
    shot(page, out, "30b_globe_region", res["shots"])
    # same flight on the flat map for comparison
    page.goto(base + "/?pr=map", wait_until="domcontentloaded")
    page.wait_for_function("window.__mapReady === true", timeout=30000)
    page.wait_for_timeout(800)
    click(page, f"region-item-{best_id}")
    res["fps_map_flyto"] = fps_while_moving(page, 8000)
    wait_idle(page, 600)

    # 31 route (Manila) + verdicts (Manila, Honduras: №1 unconfirmed → confirmed alternative)
    ids = {r["id"] for r in regions}
    rid = "manila" if "manila" in ids else best_id
    _goto_scene(page, base + f"/?r={rid}&pr=map", res)
    wait_idle(page, 1200)
    shot(page, out, "31a_verdict_" + rid, res["shots"])
    if page.locator("[data-testid='route-toggle']").count():
        click(page, "route-toggle")
        page.wait_for_timeout(600)
        wait_idle(page, 1200)
        page.evaluate("""(() => { const e = document.querySelector("[data-testid='route-info']");
            if (e) e.scrollIntoView({ block: 'center' }); })()""")
        page.wait_for_timeout(400)
        res["route"] = page.evaluate("window.__app.route")
        shot(page, out, "31_route", res["shots"])
    else:
        res["notes"].append("no zones → no route button")
    if "honduras" in ids:
        _goto_scene(page, base + "/?r=honduras&pr=map", res)
        wait_idle(page, 1200)
        res["verdict_honduras"] = page.locator("[data-testid='verdict-box']").inner_text() if page.locator(
            "[data-testid='verdict-box']").count() else None
        shot(page, out, "31b_verdict_honduras", res["shots"])

    # 33 compare opened by a link straight from the overview (no overview labels on top)
    other = next((r for r in regions if r["id"] != best_id and r.get("dates")), None)
    best = next(r for r in regions if r["id"] == best_id)
    if other:
        cmp = f"{best_id}:{best['dates'][-1]['date']},{other['id']}:{other['dates'][-1]['date']}"
        page.goto(base + f"/?cmp={cmp}", wait_until="domcontentloaded")
        try:
            page.wait_for_function("window.__compareReady_a && window.__compareReady_b", timeout=15000)
        except Exception:
            res["notes"].append("compare by link not ready")
        page.wait_for_timeout(1500)
        res["cmp_link_overview_labels_visible"] = page.evaluate(
            "[...document.querySelectorAll('.region-marker')].filter(e => e.offsetParent !== null).length")
        shot(page, out, "33_compare_link", res["shots"])
    ctx.close()

    # 32 offline: every non-local request is aborted; basemap «Без» with the local land outline
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)

    def block(route):
        u = route.request.url
        if u.startswith(("http://127.0.0.1", "http://localhost", "data:", "blob:")):
            return route.continue_()
        return route.abort("internetdisconnected")

    ctx.route("**/*", block)
    page = ctx.new_page()
    off = Console()
    off.attach(page)
    page.goto(base + "/?b=none&pr=map", wait_until="domcontentloaded")
    page.wait_for_function("window.__mapReady === true", timeout=30000)
    page.wait_for_timeout(1500)
    res["offline_land_layer"] = page.evaluate("!!(window.__ctl.map && window.__ctl.map.getLayer('land'))")
    shot(page, out, "32_offline_coast", res["shots"])
    page.goto(base + "/?b=none&pr=globe", wait_until="domcontentloaded")
    page.wait_for_function("window.__mapReady === true", timeout=30000)
    page.wait_for_timeout(1500)
    shot(page, out, "32b_offline_globe", res["shots"])
    res["offline_console_errors"] = off.errors
    ctx.close()


def append_perf(res: dict):
    PERF_MD.parent.mkdir(parents=True, exist_ok=True)
    if not PERF_MD.exists():
        PERF_MD.write_text(
            "# UI performance (Playwright, Chromium)\n\n"
            "Генерируется `scripts/screenshots.py`. load_ms — от начала навигации до `window.__mapReady` "
            "(карта загружена и отрисован первый слой deck.gl); fps — число кадров requestAnimationFrame в секунду "
            "(дрейф: 5 с анимации TripsLayer; flyTo: от клика по району до остановки камеры). "
            "console_errors — ошибки консоли без учёта недоступности внешних тайлов (отдельный столбец).\n\n"
            "| дата-время | base-url | load_ms | console_errors | fps_drift | fps_flyto | внешние ошибки | GPU / заметки |\n"
            "|---|---|---|---|---|---|---|---|\n",
            encoding="utf-8",
        )
    errs = res.get("console_errors") or []
    err_cell = str(len(errs)) + ("" if not errs else ": " + "; ".join(e.replace("|", "/")[:120] for e in errs[:3]))
    notes = [f"gl={res.get('gl')}", res.get("gpu", "")] + res.get("notes", [])
    if res.get("fps_globe_flyto") is not None:  # L27
        notes.insert(1, "globe: rotate {} fps, flyTo {} fps (map flyTo {}); default={}".format(
            res.get("fps_globe_rotate"), res.get("fps_globe_flyto"), res.get("fps_map_flyto"), res.get("projection_default")))
    row = "| {} | {} | {} | {} | {} | {} | {} | {} |\n".format(
        dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        res["base_url"],
        res.get("load_ms"),
        err_cell,
        res.get("fps_drift"),
        res.get("fps_flyto"),
        len(res.get("external_errors") or []),
        "; ".join(n for n in notes if n).replace("|", "/")[:200],
    )
    with PERF_MD.open("a", encoding="utf-8") as f:
        f.write(row)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=None, help="http://127.0.0.1:5173 (vite dev) or http://127.0.0.1:8000 (FastAPI)")
    ap.add_argument("--out", default="reports/screens/iter1")
    ap.add_argument("--start-dev", action="store_true", help="start `vite` dev server (serves /data from DATA_ROOT or service/demo_fixtures)")
    ap.add_argument("--start-preview", action="store_true", help="start `vite preview` of the production build in service/static")
    ap.add_argument("--video", action="store_true", help="record the demo tour to <out>/tour.webm")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--gl", choices=list(GL_ARGS), default="swiftshader", help="WebGL backend: swiftshader (default) or gpu")
    ap.add_argument("--extra", action="store_true", help="also shoot 11_region_1366")
    ap.add_argument("--no-perf", action="store_true", help="do not append to reports/ui_perf.md")
    ap.add_argument("--l20", action="store_true",
                    help="also shoot L20 frames 21-25 (zone card, place card, calendar, review tab)")
    ap.add_argument("--only-l20", action="store_true", help="only the L20 frames (fast iteration)")
    ap.add_argument("--only-l27", action="store_true", help="only the L27 frames 30-33 (globe, route, offline, compare link)")
    ap.add_argument("--review-write", action="store_true",
                    help="L20: allow POST labels / «ложное?» / retrain (writes the backend labels file!) "
                         "for frames 24b and 25; use with a backend started with MACROPLASTIC_LABELS=<scratch dir>")
    ap.add_argument("--retrain-timeout", type=int, default=900, help="seconds to wait for the retrain job (frame 25)")
    args = ap.parse_args()
    if not args.base_url:
        args.base_url = "http://127.0.0.1:4173" if args.start_preview else "http://127.0.0.1:5173"
    (ROOT / "reports" / "screens").mkdir(parents=True, exist_ok=True)

    server = None
    if args.start_dev or args.start_preview:
        port = int(args.base_url.rsplit(":", 1)[1].split("/")[0])
        server = start_server("dev" if args.start_dev else "preview", port)
        if not wait_http(args.base_url + "/data/manifest.json", 60):
            stop_server(server)
            sys.exit("server did not start: " + args.base_url)
    try:
        res = run(args)
    finally:
        stop_server(server)
    if not args.no_perf:
        append_perf(res)
    out = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out
    (out / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "shots"}, ensure_ascii=False, indent=1))
    ok = (args.only_l20 or args.only_l27 or len(res["shots"]) >= 10) and not res["console_errors"]
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
