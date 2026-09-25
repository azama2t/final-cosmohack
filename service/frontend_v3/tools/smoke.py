"""v3 smoke (L94): failure modes and flows that the screenshots do not cover.

.venv\\Scripts\\python.exe service\\frontend_v3\\tools\\smoke.py --url http://127.0.0.1:8072 --out reports/screens/v3/smoke
Checks: (1) Esri tiles blocked → offline outline, no page errors; (2) API unreachable → «Сервис недоступен», map alive;
(3) saved query: save → listed → apply → delete; (4) ?q= link restores filters; (5) empty filter → message + reset;
(6) back restores camera; (7) reload keeps sites + work status.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import re
from playwright.sync_api import sync_playwright

ARGS = ["--use-angle=d3d11", "--ignore-gpu-blocklist"]


def page(b, w=1366, h=768):
    ctx = b.new_context(viewport={"width": w, "height": h})
    pg = ctx.new_page()
    errs: list[str] = []
    pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
    pg.on("console", lambda m: m.type == "error" and errs.append(m.text + " @ " + str((m.location or {}).get("url", ""))[:100]))
    return ctx, pg, errs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8072")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    U = a.url
    res: dict = {}
    with sync_playwright() as p:
        b = p.chromium.launch(args=ARGS)

        # (1) Esri blocked
        ctx, pg, errs = page(b)
        ctx.route(re.compile(r"arcgisonline.com"), lambda r: r.abort("connectionreset"))
        pg.goto(U + "/?fresh=1")
        pg.wait_for_function("window.__mapReady === true", timeout=20000)
        pg.wait_for_timeout(14000)
        style = pg.evaluate("window.__map.getStyle().name")
        pg.screenshot(path=str(out / "01_offline.png"))
        res["offline"] = {"style": style, "ok": style == "offline", "page_errors": [e for e in errs if e.startswith("pageerror")]}
        ctx.close()

        # (2) API unreachable (tiles still online)
        ctx, pg, errs = page(b)
        pg.goto(U + "/?fresh=1&api=http://127.0.0.1:9")
        pg.wait_for_timeout(5000)
        banner = pg.locator("[data-testid=api-error]").inner_text() if pg.locator("[data-testid=api-error]").count() else None
        alive = pg.evaluate("!!window.__map && window.__map.loaded !== undefined")
        pg.screenshot(path=str(out / "02_api_down.png"))
        res["api_down"] = {"banner": banner, "map_alive": alive, "ok": bool(banner) and alive, "page_errors": [e for e in errs if e.startswith("pageerror")]}
        ctx.close()

        # (3)-(7) normal flows
        ctx, pg, errs = page(b)
        pg.goto(U + "/?fresh=1")
        pg.evaluate("localStorage.clear()")
        pg.goto(U + "/?fresh=1")
        pg.wait_for_function("window.__mapReady === true", timeout=20000)
        pg.wait_for_timeout(1500)
        # (5) empty filter
        pg.click("[data-testid=nav-layers]")
        pg.fill("input[aria-label='с']", "2030-01-01")
        pg.wait_for_timeout(400)
        empty_msg = pg.locator(".layers .err").count() > 0
        pg.screenshot(path=str(out / "05_empty_filter.png"))
        pg.click("[data-testid=reset-filters]")
        pg.wait_for_timeout(300)
        res["empty_filter"] = {"message": empty_msg, "reset": pg.locator(".layers .err").count() == 0}
        res["empty_filter"]["ok"] = res["empty_filter"]["message"] and res["empty_filter"]["reset"]
        # (3) saved query
        pg.fill("input[aria-label='с']", "2016-01-01")
        pg.click("[data-testid=nav-export]")
        pg.fill(".export input.name", "smoke L94")
        pg.click("[data-testid=save-query]")
        pg.wait_for_timeout(800)
        listed = pg.locator(".q-row", has_text="smoke L94").count()
        pg.screenshot(path=str(out / "03_saved_query.png"))
        q = pg.evaluate("fetch('/api/v3/queries').then(r => r.json())")
        mine = [x for x in q["queries"] if x["name"] == "smoke L94"]
        # apply after a reset
        pg.click("[data-testid=nav-layers]")
        pg.click("[data-testid=reset-filters]")
        pg.click("[data-testid=nav-export]")
        pg.locator(".q-row", has_text="smoke L94").locator(".q-name").click()
        pg.wait_for_timeout(300)
        ui = pg.evaluate("JSON.parse(localStorage.getItem('mp3.ui') || '{}')")
        applied = (ui.get("filters") or {}).get("from") == "2016-01-01"
        for x in mine:
            pg.locator(".q-row", has_text="smoke L94").locator("button[title='Удалить']").first.click()
            pg.wait_for_timeout(500)
        left = [x for x in pg.evaluate("fetch('/api/v3/queries').then(r => r.json())")["queries"] if x["name"] == "smoke L94"]
        res["saved_query"] = {"listed": listed, "saved": len(mine), "applied": applied, "deleted": not left, "ok": listed >= 1 and applied and not left}
        # (4) ?q= link
        qobj = {"bbox": [7.0, 54.0, 9.0, 55.0], "date_from": "2014-04-01", "date_to": "2014-04-30", "statuses": [], "sources": ["S3_SE_NORTH_SEA"], "profiles": [], "layers": ["observations"], "scene_id": None}
        enc = base64.urlsafe_b64encode(json.dumps(qobj).encode()).decode().rstrip("=")
        pg.goto(U + "/?q=" + enc)
        pg.wait_for_function("window.__mapReady === true", timeout=20000)
        pg.wait_for_timeout(2500)
        ui = pg.evaluate("JSON.parse(localStorage.getItem('mp3.ui') || '{}')")
        cen = pg.evaluate("window.__map.getCenter().toArray()")
        f = ui.get("filters") or {}
        res["q_link"] = {"filters": f, "center": cen, "ok": f.get("from") == "2014-04-01" and f.get("sources") == ["S3_SE_NORTH_SEA"] and f.get("zones") is False and 7 < cen[0] < 9.5}
        pg.screenshot(path=str(out / "04_q_link.png"))
        # (6) back restores the camera + (7) reload keeps the site and its status
        pg.goto(U + "/?fresh=1")
        pg.wait_for_function("window.__mapReady === true", timeout=20000)
        pg.wait_for_timeout(1000)
        pos = pg.evaluate(
            """async () => { const r = await fetch('/api/v3/observations/MPL-0919'); const f = await r.json();
              const c = f.properties.track_center || f.geometry.coordinates; const m = window.__map; m.jumpTo({center: c, zoom: 9});
              await new Promise(res => m.once('idle', res)); const p = m.project(c); return [p.x, p.y, m.getZoom()]; }"""
        )
        pg.mouse.click(pos[0], pos[1])
        pg.wait_for_timeout(500)
        cam0 = pg.evaluate("[window.__map.getCenter().toArray(), window.__map.getZoom()]")
        pg.click("[data-testid=to-studio]")
        pg.wait_for_timeout(2500)
        pg.locator("[data-testid=work-status] button", has_text="В работе").click()
        pg.click("[data-testid=back]")
        pg.wait_for_timeout(1600)
        cam1 = pg.evaluate("[window.__map.getCenter().toArray(), window.__map.getZoom()]")
        card = pg.locator("[data-testid=cand-card]").count()
        res["back"] = {"cam_before": cam0, "cam_after": cam1, "card_restored": card, "ok": abs(cam0[1] - cam1[1]) < 0.05 and abs(cam0[0][0] - cam1[0][0]) < 0.01 and card == 1}
        pg.reload()
        pg.wait_for_function("window.__mapReady === true", timeout=20000)
        pg.wait_for_timeout(800)
        sites = pg.evaluate("JSON.parse(localStorage.getItem('mp3.sites') || '[]')")
        res["persist"] = {"sites": len(sites), "status": [s["status"] for s in sites], "ok": len(sites) == 1 and sites[0]["status"] == "work"}
        res["console_errors_normal_flow"] = errs
        ctx.close()
        b.close()
    res["all_ok"] = all(v.get("ok") for k, v in res.items() if isinstance(v, dict))
    (out / "smoke.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
