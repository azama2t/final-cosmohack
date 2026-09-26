"""L140: touch gestures on iPhone 12 emulation (CDP touch events) + lite globe + phone «back».
1) drag the sheet handle up → sheet grows; the map camera does not move;
2) one-finger drag on the map → camera moves, sheet state unchanged;
3) tap a zone number on the map → card; history.back() (phone back) → card closed, still on the site;
4) prefers-reduced-motion → html[data-mlite], stars hidden.
Usage: .venv\\Scripts\\python.exe reports/qa/mobile_gesture_check.py [base]"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8094"
OUT = Path(__file__).resolve().parents[2] / "reports" / "qa" / "mobile"


def drag(cdp, x, y0, y1, steps=12, x1=None):
    x1 = x if x1 is None else x1
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y0}]})
    for i in range(1, steps + 1):
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x + (x1 - x) * i / steps, "y": y0 + (y1 - y0) * i / steps}]})
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})


def main():
    out = {}
    errs = []
    with sync_playwright() as p:
        d = dict(p.devices["iPhone 12"])
        d.pop("default_browser_type", None)
        b = p.chromium.launch()
        ctx = b.new_context(**d)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errs.append(str(e)[:150]))
        page.on("console", lambda m: errs.append(m.text[:150]) if m.type == "error" else None)
        cdp = ctx.new_cdp_session(page)
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.wait_for_selector('[data-testid="m-sheet-handle"]', timeout=30000)
        page.wait_for_timeout(3000)
        cam = lambda: page.evaluate("() => location.search")  # noqa: E731  (CaseApp writes the camera into the URL)
        st = lambda: page.evaluate("() => document.documentElement.dataset.msheet")  # noqa: E731
        hb = page.locator('[data-testid="m-sheet-handle"]').bounding_box()
        c0 = cam() or "?c=-42.0000,30.0000,1.43"  # nothing written yet = the default camera
        drag(cdp, 195, hb["y"] + 30, hb["y"] - 420)
        page.wait_for_timeout(700)
        out["1_sheet_after_drag_up"] = st()
        out["1_camera_unchanged"] = cam() == c0
        out["1_cam"] = (c0[-60:], cam()[-60:])
        drag(cdp, 195, 250, 700)  # drag down (sheet is full: handle near the top)
        hb = page.locator('[data-testid="m-sheet-handle"]').bounding_box()
        drag(cdp, 195, hb["y"] + 30, 820)
        page.wait_for_timeout(700)
        out["1b_sheet_after_drag_down"] = st()
        s0, c0 = st(), cam()
        drag(cdp, 120, 300, 420, x1=260)
        page.wait_for_timeout(1500)
        out["2_map_pan_camera_moved"] = cam() != c0
        out["2_sheet_unchanged"] = st() == s0
        page.screenshot(path=str(OUT / "gest_iphone12_pan.png"), scale="css")
        # open a scene through the list, then tap a number marker on the map
        page.locator('[data-testid="m-sheet-handle"]').tap()
        page.wait_for_timeout(400)
        page.locator('[data-testid="scene-item"]').first.tap()
        page.wait_for_selector('[data-testid="zone-num"]', timeout=20000)
        page.wait_for_timeout(2500)
        mk = None
        for loc in page.locator('[data-testid="zone-num"]').all():
            bb = loc.bounding_box()
            if bb and 70 < bb["y"] < 300 and 10 < bb["x"] < 360:
                mk = bb
                break
        if mk:
            page.touchscreen.tap(mk["x"] + mk["width"] / 2, mk["y"] + mk["height"] / 2)
            page.wait_for_timeout(1500)
        out["3_card_from_map_tap"] = page.locator('[data-testid="scene-zone-card"]').count() > 0
        page.screenshot(path=str(OUT / "gest_iphone12_map_tap_card.png"), scale="css")
        page.go_back()
        page.wait_for_timeout(1200)
        out["3_back_closes_card"] = page.locator('[data-testid="scene-zone-card"]').count() == 0
        out["3_still_on_site"] = page.url.startswith(BASE)
        ctx.close()
        ctx = b.new_context(**d, reduced_motion="reduce")
        page = ctx.new_page()
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.wait_for_selector('[data-testid="case-app"]', timeout=30000)
        page.wait_for_timeout(2500)
        out["4_lite"] = page.evaluate("() => document.documentElement.dataset.mlite || null")
        out["4_stars_hidden"] = page.evaluate("() => { const s = document.querySelector('.stars'); return !s || getComputedStyle(s).display === 'none'; }")
        out["4_fps_probe"] = page.evaluate("() => document.documentElement.dataset.mfps || null")
        page.screenshot(path=str(OUT / "gest_iphone12_lite.png"), scale="css")
        ctx.close()
        b.close()
    out["errors"] = errs[:6]
    for k, v in out.items():
        print(k, v)


if __name__ == "__main__":
    main()
