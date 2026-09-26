"""Audit L107: live v2 checks on the auditor's service (default :8096). Frames -> reports/audit/ui_*.png, summary -> ui_walk.json.
A normal load; B all /api/v3 -> 500; C data endpoints -> 500 (meta ok); D network abort; E export + saved query via UI.
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "audit"
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8096/"
TAG = sys.argv[2] if len(sys.argv) > 2 else "p2"
res = {"base": BASE, "tag": TAG}


def text_of(page):
    try:
        return page.inner_text("body")[:4000]
    except Exception as e:  # noqa
        return f"ERR {e}"


def run_case(pw, name, route=None):
    b = pw.chromium.launch()
    ctx = b.new_context(viewport={"width": 1920, "height": 1080}, accept_downloads=True)
    page = ctx.new_page()
    cons, reqs = [], []
    page.on("console", lambda m: cons.append(f"{m.type}: {m.text}"[:300]) if m.type in ("error", "warning") else None)
    page.on("request", lambda r: reqs.append(r.url) if "/api/" in r.url else None)
    if route:
        page.route("**/api/v3/**", route)
    page.goto(BASE, wait_until="domcontentloaded")
    time.sleep(6)
    page.screenshot(path=str(OUT / f"ui_{TAG}_{name}.png"))
    t = text_of(page)
    r = {"console": cons[:15], "n_api_requests": len(reqs), "api_paths": sorted({u.split("?")[0].split("/api/")[1] for u in reqs})[:30],
         "service_down_banner": page.locator("[data-testid=service-down]").count(),
         "mock_banner": page.locator("[data-testid=mock-banner]").count(),
         "text_head": t[:1500]}
    return b, ctx, page, r


def main():
    with sync_playwright() as pw:
        b, ctx, page, r = run_case(pw, "A_normal")
        res["A_normal"] = r
        # E: export + saved query through the UI
        e = {}
        try:
            page.click("[data-testid=act-export]")
            time.sleep(0.5)
            page.screenshot(path=str(OUT / f"ui_{TAG}_E_export_menu.png"))
            for k in ("scene_zones", "zones", "observations"):
                for f in ("geojson", "csv"):
                    loc = page.locator(f"[data-testid=export-{k}-{f}]")
                    if not loc.count():
                        e[f"{k}.{f}"] = "no button"
                        continue
                    href = loc.get_attribute("href")
                    with page.expect_download(timeout=30000) as dl:
                        loc.click()
                    p = OUT / f"ui_{TAG}_export_{k}.{f}"
                    dl.value.save_as(str(p))
                    e[f"{k}.{f}"] = {"href": href, "bytes": p.stat().st_size}
                    page.click("[data-testid=act-export]") if not page.locator("[data-testid=export-menu]").count() else None
            page.keyboard.press("Escape")
            page.click("[data-testid=act-queries]")
            page.fill("[data-testid=q-name]", f"audit {TAG}")
            page.click("[data-testid=q-save]")
            time.sleep(1.5)
            page.screenshot(path=str(OUT / f"ui_{TAG}_E_query_saved.png"))
            if not page.locator("[data-testid=query-menu]").count():
                page.click("[data-testid=act-queries]")
            items = page.locator("[data-testid=q-item]")
            e["saved_items"] = items.count()
            before = text_of(page)
            page.locator("[data-testid=q-run]").last.click()
            time.sleep(3)
            page.screenshot(path=str(OUT / f"ui_{TAG}_E_query_run.png"))
            e["url_after_run"] = page.url
            e["text_after_run"] = text_of(page)[:800]
        except Exception as ex:  # noqa
            e["error"] = repr(ex)[:500]
            page.screenshot(path=str(OUT / f"ui_{TAG}_E_error.png"))
        res["E_export_query"] = e
        b.close()

        def r500(route):
            route.fulfill(status=500, content_type="application/json",
                          body='{"error":{"code":"INTERNAL","message":"audit: forced 500"}}')

        def r500_data(route):
            if "/api/v3/meta" in route.request.url:
                return route.continue_()
            return r500(route)

        for name, fn in (("B_all500", r500), ("C_data500", r500_data), ("D_abort", lambda rt: rt.abort())):
            b, ctx, page, r = run_case(pw, name, fn)
            res[name] = r
            b.close()
    (OUT / f"ui_walk_{TAG}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    for k, v in res.items():
        if isinstance(v, dict):
            print(k, {kk: vv for kk, vv in v.items() if kk not in ("text_head", "text_after_run")})


if __name__ == "__main__":
    main()
