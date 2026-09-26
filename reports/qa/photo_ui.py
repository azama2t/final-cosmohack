"""QA agent 19: «Фото» (?mode=photo) on 1366/1920 — example preloaded with boxes and N, all sample switches count,
0 console / own-HTTP errors. usage: python reports/qa/photo_ui.py <base> <stamp> <out.json>"""
import json
import sys

from playwright.sync_api import sync_playwright

base, stamp, outp = sys.argv[1], sys.argv[2], sys.argv[3]
res = {}
with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    for W, H in ((1366, 768), (1920, 1080)):
        pg = b.new_page(viewport={"width": W, "height": H}, locale="ru-RU")
        errs, bad = [], []
        pg.on("console", lambda m: errs.append(m.text[:200]) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append("pageerror " + str(e)[:200]))
        pg.on("response", lambda r: bad.append(f"{r.status} {r.url[:120]}") if r.url.startswith(base) and r.status >= 400 else None)
        r = {}
        try:
            pg.goto(base + "/?mode=photo", wait_until="domcontentloaded")
            pg.wait_for_selector("[data-testid=photo-count]", timeout=120000)
            pg.wait_for_timeout(2000)
            r["first_count"] = pg.locator("[data-testid=photo-count]").first.inner_text()[:150]
            r["boxes"] = pg.locator("[data-testid=photo-boxes]").count()
            pg.screenshot(path=f"reports/qa/img/{stamp}_{W}_photo.png")
            sm = pg.locator("[data-testid=photo-sample]")
            r["n_samples"] = sm.count()
            r["samples"] = []
            for i in range(sm.count()):
                sm.nth(i).click()
                pg.wait_for_timeout(500)
                pg.wait_for_function("() => !document.querySelector('[data-testid=photo-count]') || !/считаю|загруж/i.test(document.querySelector('[data-testid=photo-count]').innerText)", timeout=120000)
                pg.wait_for_timeout(1500)
                dens = pg.locator("[data-testid=photo-density]")
                er = pg.locator("[data-testid=photo-error]")
                r["samples"].append({"i": i, "label": sm.nth(i).inner_text()[:60],
                                     "count": pg.locator("[data-testid=photo-count]").first.inner_text()[:120],
                                     "density": dens.first.inner_text()[:160] if dens.count() else None,
                                     "error": er.first.inner_text()[:200] if er.count() else None})
            pg.screenshot(path=f"reports/qa/img/{stamp}_{W}_photo_last.png")
        except Exception as e:  # noqa: BLE001
            r["error"] = repr(e)[:400]
        r["console_errors"], r["http_errors"] = errs[:10], bad[:10]
        res[W] = r
        pg.close()
    b.close()
open(outp, "w", encoding="utf-8").write(json.dumps(res, ensure_ascii=False, indent=1))
print(json.dumps(res, ensure_ascii=False, indent=1)[:4000])
