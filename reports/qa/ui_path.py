"""QA agent 19: Playwright walk of the docs/DEMO.md path on 1366x768 and 1920x1080 against a running service.
Each step is isolated (one failure does not stop the rest). Screens -> reports/qa/img/<stamp>_<vp>_<step>.png,
result JSON -> --out. Saved queries created by the walk are deleted again through the API.

usage: python reports/qa/ui_path.py --base http://127.0.0.1:8093 --stamp 0700 --out out/qa19/ui_0700.json
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import traceback
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

IMG = Path(__file__).resolve().parent / "img"
EXT = ("arcgisonline", "carto", "openstreetmap", "tile.", "basemaps", "esri")


def api(base, path, method="GET"):
    r = urllib.request.Request(base + path, method=method)
    with urllib.request.urlopen(r, timeout=60) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def run(base, size, vp, stamp, dl_dir: Path, pw):
    b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    ctx = b.new_context(viewport={"width": size[0], "height": size[1]}, locale="ru-RU", accept_downloads=True)
    page = ctx.new_page()
    errs, bad, ext_bad, t_load = [], [], [], {}
    page.on("console", lambda m: errs.append(m.text[:300]) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)[:300]))

    def on_resp(r):
        if r.status >= 400:
            (bad if r.url.startswith(base) else ext_bad).append(f"{r.status} {r.url[:160]}")
    page.on("response", on_resp)
    page.on("requestfailed", lambda r: (bad if r.url.startswith(base) else ext_bad).append(f"FAILED {r.url[:160]} {r.failure}"))
    steps: dict = {}
    shots = []

    def shot(name):
        page.wait_for_timeout(800)
        p = IMG / f"{stamp}_{vp}_{name}.png"
        page.screenshot(path=str(p))
        shots.append(p.name)

    def wait(js, t=60000):
        page.wait_for_function(js, timeout=t)

    def step(name, fn):
        t0 = time.perf_counter()
        try:
            out = fn()
            steps[name] = {"ok": True, "s": round(time.perf_counter() - t0, 1), **(out or {})}
        except Exception as e:  # noqa: BLE001
            steps[name] = {"ok": False, "s": round(time.perf_counter() - t0, 1), "error": repr(e)[:400],
                           "tb": traceback.format_exc()[-400:]}
            try:
                shot(f"ERR_{name}")
            except Exception:  # noqa: BLE001
                pass

    def s_open():
        t0 = time.perf_counter()
        page.goto(base + "/", wait_until="domcontentloaded")
        wait("() => window.__app && window.__app.ready && window.__app.szReady", 120000)
        t_load["map_ready_s"] = round(time.perf_counter() - t0, 1)
        app = page.evaluate("() => ({mock: window.__app.mock, counts: window.__app.counts})")
        shot("01_map")
        out = {"mock": app["mock"], "counts": app["counts"], **t_load}
        hl = page.locator("[data-testid=headline]")
        if hl.count():
            bb = hl.first.bounding_box()
            out["headline_text"] = hl.first.inner_text()[:800]
            out["headline_in_viewport"] = bool(bb and bb["y"] >= 0 and bb["y"] + bb["height"] <= size[1])
            out["headline_bbox"] = bb
        else:
            out["headline_text"] = None
        lg = page.locator("[data-testid=legend]")
        out["legend_text"] = lg.first.inner_text()[:600] if lg.count() else None
        assert app["mock"] is False, "mock data"
        return out

    def s_zone():
        page.click("[data-testid=tab-zones]")
        item = page.locator("[data-testid=sz-item]").first
        title = item.inner_text().split("\n")[0]
        item.click()
        wait("() => window.__app.szDetailReady && document.querySelector('[data-testid=scene-zone-card]')")
        page.wait_for_timeout(2500)
        shot("02_zone_card")
        card = {}
        for k in ("sz-status", "sz-area", "sz-px-area", "sz-lwd", "sz-quality", "sz-model", "sz-prob", "sz-next"):
            loc = page.locator(f"[data-testid={k}]")
            card[k] = loc.first.inner_text()[:300] if loc.count() else None
        return {"first_zone_title": title, "card": card}

    def s_quantity():
        page.click("[data-testid=sz-quantity-more] summary")
        q = page.locator("[data-testid=sz-quantity]").inner_text()
        page.locator("[data-testid=sz-quantity]").scroll_into_view_if_needed()
        shot("03_quantity")
        txt = page.locator("[data-testid=scene-zone-card]").inner_text()
        return {"quantity": q[:400], "no_scenario_numbers": not any(
            x in txt for x in ("10 000", "100 млн", "500 000", "Условный диапазон", "Сценарий"))}

    def s_field():
        page.locator("[data-testid=sz-field]").scroll_into_view_if_needed()
        f = page.locator("[data-testid=sz-field]").inner_text()
        shot("04_field")
        return {"field": f[:400]}

    def s_false_alarm():
        page.locator("[data-testid=sz-examples]").scroll_into_view_if_needed()
        shot("05_examples")
        page.click("[data-testid=sz-example-false_alarm]")
        wait("() => window.__app.szDetailReady && document.querySelector('[data-testid=sz-status]') && document.querySelector('[data-testid=sz-status]').innerText.startsWith('ложное')")
        page.wait_for_timeout(2000)
        shot("06_false_alarm")
        return {"status": page.locator("[data-testid=sz-status]").inner_text()[:200]}

    def s_export():
        page.fill("[data-testid=f-from]", "2021-03-11")
        page.fill("[data-testid=f-to]", "2021-03-11")
        wait("() => window.__app.ready && window.__app.q.to === '2021-03-11' && window.__app.szReady && window.__app.counts.szones !== null && window.__app.counts.szones < 286")
        n_sz = page.evaluate("() => window.__app.counts.szones")
        page.click("[data-testid=act-export]")
        shot("07_export_menu")
        exp = {}
        for fmt in ("csv", "geojson"):
            with page.expect_download() as dl:
                page.click(f"[data-testid=export-scene_zones-{fmt}]")
            p = dl_dir / f"{stamp}_{vp}_scene_zones.{fmt}"
            dl.value.save_as(str(p))
            body = p.read_text(encoding="utf-8-sig")
            exp[fmt] = len(list(csv.DictReader(io.StringIO(body)))) if fmt == "csv" else len(json.loads(body)["features"])
        page.keyboard.press("Escape")
        page.mouse.click(size[0] // 2, 40)
        return {"ui_count": n_sz, **exp, "equal": exp["csv"] == exp["geojson"] == n_sz}

    qname = f"QA19 {stamp} {vp}"

    def s_query():
        page.click("[data-testid=act-queries]")
        page.fill("[data-testid=q-name]", qname)
        page.click("[data-testid=q-save]")
        page.wait_for_timeout(1200)
        page.keyboard.press("Escape")
        page.click("[data-testid=f-reset]")
        wait("() => window.__app.ready && window.__app.q.from === null && window.__app.szReady && window.__app.counts.szones > 23")
        after = page.evaluate("() => window.__app.counts.szones")
        page.click("[data-testid=act-queries]")
        row = page.locator("[data-testid=q-item]", has_text=qname).first
        row.locator("[data-testid=q-run]").click()
        wait("() => window.__app.ready && window.__app.q.from === '2021-03-11' && window.__app.szReady")
        page.wait_for_timeout(1200)
        shot("08_query_rerun")
        lm = page.locator("[data-testid=legend-model]")
        return {"after_reset_szones": after, "rerun_szones": page.evaluate("() => window.__app.counts.szones"),
                "legend_model": lm.first.inner_text()[:200] if lm.count() else None}

    def s_metrics():
        page.goto(base + "/", wait_until="domcontentloaded")
        wait("() => window.__app && window.__app.ready", 120000)
        page.click("[data-testid=tab-metrics]")
        page.wait_for_timeout(1500)
        shot("09_metrics")
        mp = page.locator("[data-testid=metrics-panel]")
        return {"metrics_text": mp.first.inner_text()[:1200] if mp.count() else None}

    def s_photo():
        page.goto(base + "/?mode=photo", wait_until="domcontentloaded")
        page.wait_for_selector("[data-testid=photo-app]", timeout=60000)
        page.wait_for_selector("[data-testid=photo-count]", timeout=120000)
        page.wait_for_timeout(1500)
        shot("10_photo")
        out = {"count_text": page.locator("[data-testid=photo-count]").first.inner_text()[:200]}
        er = page.locator("[data-testid=photo-error]")
        out["photo_error"] = er.first.inner_text()[:300] if er.count() else None
        samples = page.locator("[data-testid=photo-sample]")
        out["n_samples"] = samples.count()
        res = []
        for i in range(min(samples.count(), 6)):
            samples.nth(i).click()
            page.wait_for_timeout(4000)
            res.append({"i": i, "count": page.locator("[data-testid=photo-count]").first.inner_text()[:120],
                        "info": (page.locator("[data-testid=photo-sample-info]").first.inner_text()[:160]
                                 if page.locator("[data-testid=photo-sample-info]").count() else None)})
        if res:
            shot("11_photo_last_sample")
        out["samples"] = res
        return out

    step("open", s_open)
    step("zone_card", s_zone)
    step("quantity", s_quantity)
    step("field", s_field)
    step("false_alarm", s_false_alarm)
    step("export", s_export)
    step("query", s_query)
    step("metrics", s_metrics)
    step("photo", s_photo)
    # clean the saved query created by the walk
    try:
        ql = api(base, "/api/v3/queries")
        for q in (ql or {}).get("queries", []):
            if q.get("name") == qname:
                api(base, f"/api/v3/queries/{q['query_id']}", "DELETE")
    except Exception as e:  # noqa: BLE001
        steps["cleanup_error"] = repr(e)[:200]
    b.close()
    return {"steps": steps, "console_errors": errs[:30], "n_console_errors": len(errs),
            "http_errors_own": bad[:30], "n_http_errors_own": len(bad), "http_errors_external": ext_bad[:10],
            "n_http_errors_external": len(ext_bad), "shots": shots}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8093")
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dl", default=None)
    a = ap.parse_args()
    IMG.mkdir(parents=True, exist_ok=True)
    dl_dir = Path(a.dl or Path(a.out).parent)
    dl_dir.mkdir(parents=True, exist_ok=True)
    res = {"base": a.base, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    with sync_playwright() as pw:
        for vp, size in (("1366", (1366, 768)), ("1920", (1920, 1080))):
            try:
                res[vp] = run(a.base, size, vp, a.stamp, dl_dir, pw)
            except Exception as e:  # noqa: BLE001
                res[vp] = {"fatal": repr(e)[:500]}
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    for vp in ("1366", "1920"):
        r = res.get(vp, {})
        if "fatal" in r:
            print(vp, "FATAL", r["fatal"])
            continue
        bad = [k for k, v in r["steps"].items() if isinstance(v, dict) and not v.get("ok")]
        print(vp, "steps failed:", bad, "| console errors:", r["n_console_errors"], "| own HTTP>=400:",
              r["n_http_errors_own"], "| external:", r["n_http_errors_external"])
        for k in bad:
            print("  ", k, r["steps"][k]["error"][:250])
        for e in r["console_errors"][:8]:
            print("   console:", e[:200])
        for e in r["http_errors_own"][:8]:
            print("   http:", e)


if __name__ == "__main__":
    main()
