"""Offline demo check: open the map with ALL external requests blocked.

Every request whose host is not 127.0.0.1 / localhost (or a data:/blob: URL) is aborted via
Playwright `route`, i.e. the browser behaves as if the internet were gone while the local
FastAPI service still answers. For each basemap mode (default «Тёмная», «Без», «Спутник») the
script opens the site, picks the best region (window.__app.bestRegion — same rule as the
demo tour), switches on the layers (snapshot, spots, probability, H3, zones, drift), opens
the comparison and records:
  * whether the map becomes ready (window.__mapReady) and how long it takes (__mapReadyAt);
  * which basemap the UI ends up in (auto-fallback to «Без» + toast);
  * per step: ready flags, share of "non-background" pixels in the map area (empty-map detector);
  * console errors / page errors, blocked external requests (by host).
An online baseline (nothing blocked) is run first for comparison unless --no-baseline.

Usage (service must already run on :8000):
  .venv\\Scripts\\python.exe scripts\\offline_check.py
  .venv\\Scripts\\python.exe scripts\\offline_check.py --base-url http://127.0.0.1:8000 --out reports\\screens\\offline --md reports\\offline_check.md

Writes PNG frames to --out, a JSON dump (<out>/offline_check.json) and a Markdown report (--md).
Exit code 0 if the map loaded and the region scene became ready in every offline mode, else 1.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
GL_ARGS = {
    "swiftshader": ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
    "gpu": ["--use-angle=d3d11", "--ignore-gpu-blocklist", "--enable-gpu", "--enable-gpu-rasterization"],
}
# modes: (name, url query, block external?)
MODES = [
    ("online_dark", "", False),
    ("offline_dark", "", True),
    ("offline_none", "?b=none", True),
    ("offline_satellite", "?b=satellite", True),
]
BG_RGB = (0x07, 0x11, 0x1F)  # OCEAN_BG of offlineStyle()


def is_local(url: str) -> bool:
    if url.startswith(("data:", "blob:", "about:")):
        return True
    return (urlparse(url).hostname or "") in LOCAL_HOSTS


def content_share(png: bytes, box: dict | None) -> float | None:
    """Share of pixels in the map box that differ noticeably from the plain ocean background."""
    try:
        import numpy as np
        from PIL import Image
    except Exception:
        return None
    im = Image.open(io.BytesIO(png)).convert("RGB")
    if box:
        im = im.crop((int(box["x"]), int(box["y"]), int(box["x"] + box["width"]), int(box["y"] + box["height"])))
    a = np.asarray(im).astype(int)
    d = np.abs(a - np.array(BG_RGB)).sum(axis=2)
    return round(float((d > 30).mean()), 3)


class Run:
    def __init__(self, name: str, block: bool):
        self.name = name
        self.block = block
        self.blocked: Counter = Counter()
        self.local_failed: list[str] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.steps: list[dict] = []
        self.info: dict = {}

    def route(self, route):
        url = route.request.url
        if self.block and not is_local(url):
            self.blocked[urlparse(url).hostname or url[:40]] += 1
            return route.abort("internetdisconnected")
        return route.continue_()

    def attach(self, page: Page):
        page.on("console", self._console)
        page.on("pageerror", lambda e: self.errors.append(f"pageerror: {str(e)[:300]}"))
        page.on("requestfailed", self._failed)

    def _console(self, m):
        if m.type == "error":
            loc = (m.location or {}).get("url", "")
            self.errors.append((m.text + (f" [{loc[:140]}]" if loc else ""))[:300])
        elif m.type == "warning":
            self.warnings.append(m.text[:300])

    def _failed(self, req):
        if is_local(req.url) and "ERR_ABORTED" not in (req.failure or ""):
            self.local_failed.append(f"{req.url[:160]} {req.failure}")


def app(page: Page, expr: str, default=None):
    try:
        return page.evaluate(f"(() => {{ try {{ return {expr}; }} catch (e) {{ return null; }} }})()")
    except Exception:
        return default


def wait_fn(page: Page, js: str, timeout: int) -> bool:
    try:
        page.wait_for_function(js, timeout=timeout)
        return True
    except Exception:
        return False


def current_basemap(page: Page) -> str | None:
    return app(page, "(document.querySelector(\"[data-testid^='basemap-'].on\") || {}).dataset?.testid?.slice(8) || null")


def toast(page: Page) -> str | None:
    return app(page, "(document.querySelector('.toast') || {}).textContent || null")


def map_box(page: Page) -> dict | None:
    return app(page, "(() => { const c = window.__ctl && window.__ctl.map && window.__ctl.map.getContainer(); if (!c) return null; const r = c.getBoundingClientRect(); return {x: r.left, y: r.top, width: r.width, height: r.height}; })()")


def step(page: Page, run: Run, out: Path, name: str, ok: bool, note: str = ""):
    png = page.screenshot()
    fname = f"{run.name}__{name}.png"
    (out / fname).write_bytes(png)
    share = content_share(png, map_box(page))
    rec = {"step": name, "ok": ok, "content_share": share, "basemap": current_basemap(page), "frame": fname}
    if note:
        rec["note"] = note
    run.steps.append(rec)
    print(f"  [{run.name}] {name}: ok={ok} content={share} basemap={rec['basemap']} {note}")


def ensure_layer(page: Page, key: str, testid: str, on: bool = True) -> bool:
    cur = app(page, f"!!window.__app.layers['{key}']")
    loc = page.locator(f"[data-testid='layer-toggle-{testid}']")
    if not loc.count():
        return False
    if loc.first.is_disabled():
        return False
    if bool(cur) != on:
        loc.first.click()
    return True


def bundle_id(base: str) -> str:
    """Entry-chunk name from index.html: changes when service/static is rebuilt."""
    import re
    import urllib.request
    try:
        html = urllib.request.urlopen(base + "/", timeout=5).read().decode("utf-8", "replace")
        m = re.search(r"assets/(index-[^\"]+\.js)", html)
        return m.group(1) if m else "?"
    except Exception as e:
        return f"error: {e}"


def scenario(browser, base: str, out: Path, name: str, query: str, block: bool, gl: str) -> Run:
    run = Run(name, block)
    run.info["bundle_start"] = bundle_id(base)
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
    ctx.route("**/*", run.route)
    page = ctx.new_page()
    run.attach(page)
    t0 = time.time()
    try:
        page.goto(base + "/" + query, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        run.info["goto_error"] = str(e)[:200]
    ready = wait_fn(page, "window.__mapReady === true", 30000)
    run.info["map_ready"] = ready
    run.info["map_ready_ms"] = round(app(page, "window.__mapReadyAt || 0") or 0) if ready else None
    run.info["map_ready_wall_ms"] = round((time.time() - t0) * 1000)
    page.wait_for_timeout(2500)  # let basemap fallback / toasts happen
    run.info["toast_after_load"] = toast(page)
    run.info["fonts_inter_loaded"] = app(page, "document.fonts.check('600 14px Inter') && [...document.fonts].some(f => f.family.includes('Inter') && f.status === 'loaded')")
    step(page, run, out, "01_overview", ready)
    if not ready:
        ctx.close()
        return run

    best = app(page, "window.__app.bestRegion")
    run.info["best_region"] = best
    # 02 region -----------------------------------------------------------------------------
    loc = page.locator(f"[data-testid='region-item-{best}']")
    if loc.count():
        loc.first.click()
    ok = wait_fn(page, "window.__app && window.__app.sceneReady", 20000)
    wait_fn(page, "window.__app && !window.__app.isMoving()", 12000)
    page.wait_for_timeout(1500)
    run.info["scene_ready"] = ok
    run.info["n_detections"] = app(page, "window.__app.nDetections")
    run.info["date"] = app(page, "window.__app.date")
    run.info["rgb_on"] = app(page, "!!window.__app.layers.rgb")
    step(page, run, out, "02_region_rgb_spots", ok, f"detections={run.info['n_detections']}")
    # 03 probability ------------------------------------------------------------------------
    ok = ensure_layer(page, "prob", "prob")
    page.wait_for_timeout(2000)
    step(page, run, out, "03_prob", ok)
    ensure_layer(page, "prob", "prob", False)
    # 04 H3 ---------------------------------------------------------------------------------
    ok = ensure_layer(page, "h3", "h3") and wait_fn(page, "window.__app && window.__app.h3Ready", 15000)
    page.wait_for_timeout(1500)
    step(page, run, out, "04_h3", ok)
    ensure_layer(page, "h3", "h3", False)
    # 05 zones ------------------------------------------------------------------------------
    ok = ensure_layer(page, "zones", "zones")
    page.wait_for_timeout(1500)
    run.info["zone_markers"] = app(page, "document.querySelectorAll('.zone-marker, [data-testid^=\"zone-marker\"]').length")
    step(page, run, out, "05_zones", ok)
    ensure_layer(page, "zones", "zones", False)
    # 06 drift (best region date if it has drift, otherwise first date with drift) -----------
    has_drift = page.locator("[data-testid='layer-toggle-drift']").count() and not page.locator("[data-testid='layer-toggle-drift']").first.is_disabled()
    if not has_drift:
        manifest = app(page, "null")
        try:
            manifest = page.evaluate("fetch('/data/manifest.json').then(r => r.json())")
        except Exception:
            manifest = None
        ref = None
        for r in (manifest or {}).get("regions", []):
            for d in reversed(r.get("dates", [])):
                if d.get("drift"):
                    ref = (r["id"], d["date"])
                    break
            if ref:
                break
        if ref:
            if ref[0] != best:
                page.locator(f"[data-testid='region-item-{ref[0]}']").first.click()
                wait_fn(page, "window.__app && window.__app.sceneReady", 20000)
                wait_fn(page, "window.__app && !window.__app.isMoving()", 12000)
            if page.locator(f"[data-testid='date-dot-{ref[1]}']").count():
                page.locator(f"[data-testid='date-dot-{ref[1]}']").first.click()
                page.wait_for_timeout(1000)
            run.info["drift_ref"] = ref
    ok = ensure_layer(page, "drift", "drift") and wait_fn(page, "window.__app && window.__app.driftReady", 15000)
    if ok:
        wait_fn(page, "window.__app && !window.__app.isMoving()", 12000)
        page.evaluate("window.__app.setDriftHour(36)")
        page.wait_for_timeout(1000)
    step(page, run, out, "06_drift", ok)
    ensure_layer(page, "drift", "drift", False)
    # 07 compare ----------------------------------------------------------------------------
    ok = False
    if page.locator("[data-testid='compare-button']").count():
        page.locator("[data-testid='compare-button']").first.click()
        ok = wait_fn(page, "window.__compareReady_a && window.__compareReady_b", 20000)
        page.wait_for_timeout(2000)
    step(page, run, out, "07_compare", ok)
    if page.locator("[data-testid='compare-close']").count():
        page.locator("[data-testid='compare-close']").first.click()
        page.wait_for_timeout(500)
    run.info["final_basemap"] = current_basemap(page)
    run.info["toast_end"] = toast(page)
    run.info["bundle_end"] = bundle_id(base)
    if run.info["bundle_end"] != run.info["bundle_start"]:
        run.info["WARNING"] = "service/static was rebuilt during this run: lazy chunks may 404 (stale tab)"
    ctx.close()
    return run


CDN_HOSTS = ("fonts.googleapis", "fonts.gstatic", "unpkg.com", "jsdelivr", "cdnjs", "gstatic.com", "raw.githubusercontent",
             "arcgisonline.com", "cartocdn.com", "openstreetmap", "mapbox.com", "demotiles.maplibre.org")


def static_audit() -> list[str]:
    """Grep of the built bundle (service/static) for CDN / external URLs and local fonts."""
    import re
    st = ROOT / "service" / "static"
    L = []
    if not (st / "index.html").exists():
        return ["service/static/index.html отсутствует (фронт не собран или пересобирается)"]
    html = (st / "index.html").read_text(encoding="utf-8", errors="replace")
    ext = [u for u in re.findall(r"(?:src|href)=\"(https?://[^\"]+)", html)]
    L.append(f"index.html: внешних src/href — {len(ext)}" + (f": {ext}" if ext else ""))
    fonts = sorted(x.name for x in (st / "assets").glob("*.woff2"))
    L.append(f"шрифты в бандле (woff2): {len(fonts)}; Inter: {sum('inter' in f for f in fonts)} (через @fontsource, локально)")
    hits: dict = {}
    for f in list((st / "assets").glob("*.js")) + list((st / "assets").glob("*.css")):
        t = f.read_text(encoding="utf-8", errors="replace")
        for h in CDN_HOSTS:
            if h in t:
                hits.setdefault(h, set()).add(f.name)
    for h, fs in sorted(hits.items()):
        L.append(f"`{h}` встречается в {', '.join(sorted(fs))}")
    return L


def write_md(md: Path, runs: list[Run], base: str, gl: str, out: Path):
    L = [
        "# Офлайн-проверка карты",
        "",
        f"Сгенерировано `scripts/offline_check.py` · {dt.datetime.now():%Y-%m-%d %H:%M} · {base} · WebGL: {gl} · окно 1920×1080.",
        "Офлайн = Playwright `route`: все запросы не к 127.0.0.1/localhost обрываются (`internetdisconnected`).",
        f"Кадры: `{out.relative_to(ROOT).as_posix()}/<режим>__<шаг>.png`. «Доля содержимого» = доля пикселей карты, отличных от однотонного фона #07111f (0 → пустая карта).",
        "",
        "## Сводка",
        "",
        "| Режим | Карта готова | __mapReady, мс | Итоговая подложка | Тост | Сцена района | Пятен | Ошибок консоли | Заблокировано внешних запросов |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in runs:
        i = r.info
        L.append(
            f"| {r.name} | {'да' if i.get('map_ready') else '**нет**'} | {i.get('map_ready_ms')} | {i.get('final_basemap')} | "
            f"{(i.get('toast_after_load') or '—')} | {'да' if i.get('scene_ready') else '**нет**'} | {i.get('n_detections')} | "
            f"{len(r.errors)} | {sum(r.blocked.values())} ({', '.join(f'{h}×{n}' for h, n in r.blocked.most_common(4)) or '—'}) |"
        )
    L += ["", "## Бандл: шрифты и внешние URL (статический grep `service/static`)", ""]
    L += [f"- {x}" for x in static_audit()]
    L += ["- Реально запрошенные внешние хосты — только в колонке «Заблокировано» выше (остальные строки из grep — мёртвый код библиотек: loaders.gl CDN для воркеров, draco-декодер; в рантайме не запрашиваются)."]
    L += ["", "## Шаги", ""]
    for r in runs:
        L += [f"### {r.name}", "", "| Шаг | ok | Доля содержимого | Подложка | Кадр | Прим. |", "|---|---|---|---|---|---|"]
        for s in r.steps:
            L.append(f"| {s['step']} | {'да' if s['ok'] else '**нет**'} | {s['content_share']} | {s['basemap']} | `{s['frame']}` | {s.get('note', '')} |")
        L.append("")
        extra = {k: v for k, v in r.info.items() if k not in ("map_ready", "map_ready_ms", "final_basemap", "toast_after_load", "scene_ready", "n_detections")}
        L.append("Прочее: " + ", ".join(f"`{k}`={v}" for k, v in extra.items()))
        L.append("")
        if r.errors:
            L.append("Ошибки консоли (первые 10):")
            L += [f"- `{e}`" for e in r.errors[:10]]
            L.append("")
        if r.local_failed:
            L.append("Упавшие ЛОКАЛЬНЫЕ запросы:")
            L += [f"- `{e}`" for e in r.local_failed[:10]]
            L.append("")
    md.write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--out", default="reports/screens/offline")
    ap.add_argument("--md", default="reports/offline_check.md")
    ap.add_argument("--gl", choices=list(GL_ARGS), default="swiftshader")
    ap.add_argument("--no-baseline", action="store_true", help="skip the online baseline run")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--retries", type=int, default=3, help="re-run a mode if service/static was rebuilt during it")
    args = ap.parse_args()
    out = Path(args.out)
    out = out if out.is_absolute() else ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    md = Path(args.md)
    md = md if md.is_absolute() else ROOT / md
    base = args.base_url.rstrip("/")
    runs: list[Run] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed, args=GL_ARGS[args.gl])
        for name, q, block in MODES:
            if args.no_baseline and not block:
                continue
            print(f"== {name}")
            r = scenario(browser, base, out, name, q, block, args.gl)
            for attempt in range(args.retries):
                if r.info.get("bundle_start") == r.info.get("bundle_end") and not str(r.info.get("bundle_start", "")).startswith(("?", "error")):
                    break
                print(f"   bundle changed / missing during {name} (someone rebuilt service/static) - retry {attempt + 1}")
                time.sleep(20)
                r = scenario(browser, base, out, name, q, block, args.gl)
                r.info["retried"] = attempt + 1
            runs.append(r)
        browser.close()
    dump = [
        {"name": r.name, "block": r.block, "info": r.info, "steps": r.steps, "errors": r.errors,
         "warnings": r.warnings[:20], "blocked": dict(r.blocked), "local_failed": r.local_failed}
        for r in runs
    ]
    (out / "offline_check.json").write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")
    write_md(md, runs, base, args.gl, out)
    print(f"report: {md}")
    bad = [r.name for r in runs if r.block and not (r.info.get("map_ready") and r.info.get("scene_ready"))]
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
