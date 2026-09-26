"""QA agent 19, приёмка Матвея п.3: texts of «Цифры», an S2 / S1 field card, a satellite zone card, the QC panel.
usage: python reports/qa/cards_probe.py <base> <stamp> <out.json>"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

base, stamp, outp = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
img = Path(__file__).resolve().parent / "img" / f"cp_{stamp}"
img.mkdir(parents=True, exist_ok=True)
res = {}
with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    pg = b.new_page(viewport={"width": 1920, "height": 1080}, locale="ru-RU")
    errs = []
    pg.on("console", lambda m: errs.append(m.text[:200]) if m.type == "error" else None)

    def ready():
        pg.wait_for_function("() => window.__app && window.__app.ready && window.__app.szReady", timeout=120000)
        pg.wait_for_timeout(2500)

    pg.goto(base + "/", wait_until="domcontentloaded")
    ready()
    try:
        pg.get_by_role("button", name="Цифры").first.click()
        pg.wait_for_timeout(1500)
        res["digits"] = pg.locator("[data-testid=headline]").first.inner_text()[:2500] if pg.locator("[data-testid=headline]").count() else pg.locator("body").inner_text()[:2500]
        pg.screenshot(path=str(img / "digits.png"))
    except Exception as e:  # noqa: BLE001
        res["digits_error"] = repr(e)[:300]
    for sid in ("MPL-0234", "MPL-0551", "MPL-0085"):
        pg.goto(f"{base}/?sel=obs:{sid}", wait_until="domcontentloaded")
        try:
            pg.wait_for_selector("[data-testid=obs-card]", timeout=90000)
            pg.wait_for_timeout(1500)
            res[f"obs_{sid}"] = pg.locator("[data-testid=obs-card]").first.inner_text()[:2500]
            pg.screenshot(path=str(img / f"obs_{sid}.png"))
        except Exception as e:  # noqa: BLE001
            res[f"obs_{sid}_error"] = repr(e)[:300]
    for zid in ("SZ-demo-cozar-2021-03-11-016", "SZ-drift-honduras-2026-02-19-007"):
        pg.goto(f"{base}/?sel=zone:{zid}", wait_until="domcontentloaded")
        try:
            pg.wait_for_selector("[data-testid=scene-zone-card]", timeout=90000)
            pg.wait_for_timeout(2500)
            res[f"zone_{zid}"] = pg.locator("[data-testid=scene-zone-card]").first.inner_text()[:3000]
            pg.screenshot(path=str(img / f"zone_{zid}.png"))
        except Exception as e:  # noqa: BLE001
            res[f"zone_{zid}_error"] = repr(e)[:300]
    pg.goto(base + "/", wait_until="domcontentloaded")
    ready()
    try:
        pg.click("[data-testid=act-qc]")
        pg.wait_for_function("() => window.__app.metricsReady", timeout=60000)
        pg.wait_for_timeout(1500)
        res["qc"] = pg.locator("[data-testid=metrics-panel]").first.inner_text()[:4000] if pg.locator("[data-testid=metrics-panel]").count() else None
        pg.screenshot(path=str(img / "qc.png"))
    except Exception as e:  # noqa: BLE001
        res["qc_error"] = repr(e)[:300]
    res["console_errors"] = errs[:10]
    b.close()
Path(outp).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
for k, v in res.items():
    print("=====", k)
    print(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
