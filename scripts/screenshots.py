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
        shot(page, out, "01_overview_1920", res["shots"])

        manifest = page.evaluate("fetch('/data/manifest.json').then(r => r.json())")
        regions = sorted(
            manifest["regions"],
            # same rule as the demo tour: most detections on the latest date, ties by index
            key=lambda r: ((r.get("summary") or {}).get("n_detections") or 0, (r.get("summary") or {}).get("index_permille") or -1),
            reverse=True,
        )
        best = regions[0]
        res["best_region"] = best["id"]

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
        for r in regions:
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
            shot(page, out, "09_drift", res["shots"])
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
        ctx2.close()

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
    ok = len(res["shots"]) >= 10 and not res["console_errors"]
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
