"""L111: живой проход демо-пути v2 без моков (Playwright): карта → зона → карточка (измерено / вероятно / «концентрация по снимку не подтверждена») →
поле рядом → выгрузка → повтор сохранённого запроса. Кадры 1920×1080 и 1366×768 -> reports/case_demo/.

  .venv\\Scripts\\python.exe -m service --port 8094
  .venv\\Scripts\\python.exe scripts\\case\\demo_path_v2.py --base-url http://127.0.0.1:8094
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "case_demo"


def api(base, path):
    with urllib.request.urlopen(base + path, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def wait(page, js, t=60000):
    page.wait_for_function(js, timeout=t)


def run(base: str, size: tuple[int, int], tag: str, res: dict, pw):
    b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    ctx = b.new_context(viewport={"width": size[0], "height": size[1]}, locale="ru-RU", accept_downloads=True)
    ctx.add_init_script("try { localStorage.setItem('mp.case.filtersOpen', '1') } catch (e) {}")  # filters folded by default (§33а)
    page = ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    bad = []
    page.on("response", lambda r: bad.append(f"{r.status} {r.url}") if r.url.startswith(base) and r.status >= 400 else None)
    steps = {}

    def shot(name):
        page.wait_for_timeout(900)
        page.screenshot(path=str(OUT / f"{tag}_{name}.png"))

    page.goto(base + "/", wait_until="domcontentloaded")
    wait(page, "() => window.__app && window.__app.ready && window.__app.szReady", 120000)
    app = page.evaluate("() => ({mock: window.__app.mock, counts: window.__app.counts})")
    steps["open"] = {"mock": app["mock"], "counts": app["counts"]}
    assert app["mock"] is False
    shot("01_map")
    # map → zone (list of satellite zones; the held-out Cózar scene is first)
    page.click("[data-testid=tab-zones]")
    item = page.locator("[data-testid=sz-item]").first
    steps["first_zone_title"] = item.inner_text().split("\n")[0]
    item.click()
    wait(page, "() => window.__app.szDetailReady && document.querySelector('[data-testid=scene-zone-card]')")
    page.wait_for_timeout(2500)  # fly-to + scene overlay
    shot("02_zone_card")
    card = page.locator("[data-testid=scene-zone-card]")
    steps["card"] = {k: page.locator(f"[data-testid={k}]").inner_text() for k in
                     ("sz-status", "sz-plain-when", "sz-plain-quality", "sz-plain-area", "sz-plain-cover", "sz-plain-conf",
                      "sz-plain-qty", "sz-plain-comp")}
    page.click("[data-testid=sz-explore] summary")
    steps["card"]["explore"] = page.locator("[data-testid=sz-explore]").inner_text()
    page.locator("[data-testid=sz-explore]").scroll_into_view_if_needed()
    shot("03_quantity")
    steps["no_scenario_numbers"] = not any(x in page.locator("[data-testid=scene-zone-card]").inner_text()
                                           for x in ("10 000", "100 млн", "500 000", "Условный диапазон", "Сценарий"))
    steps["field"] = page.locator("[data-testid=sz-field-link]").inner_text() if page.locator("[data-testid=sz-field-link]").count() else None
    steps["no_far_field_numbers"] = page.locator("[data-testid=sz-field-row]").count() == 0
    shot("04_field")
    page.click("[data-testid=sz-more] > summary")
    # difficult case: false alarm (ship) — status «ложное срабатывание … недостаточно данных»
    page.locator("[data-testid=sz-examples]").scroll_into_view_if_needed()
    shot("05_examples")
    page.click("[data-testid=sz-example-false_alarm]")
    wait(page, "() => window.__app.szDetailReady && document.querySelector('[data-testid=sz-status]') && document.querySelector('[data-testid=sz-status]').innerText.startsWith('ложное')")
    page.wait_for_timeout(2500)
    steps["false_alarm"] = {"status": page.locator("[data-testid=sz-status]").inner_text(),
                            "quantity": page.locator("[data-testid=sz-plain-qty]").inner_text()}
    shot("06_false_alarm")
    # back to the demo zone and restrict the date to the demo scene (for the saved query)
    page.fill("[data-testid=f-from]", "2021-03-11")
    page.fill("[data-testid=f-to]", "2021-03-11")
    wait(page, "() => window.__app.ready && window.__app.q.to === '2021-03-11' && window.__app.szReady && window.__app.counts.szones !== null && window.__app.counts.szones < 286")
    n_sz = page.evaluate("() => window.__app.counts.szones")
    steps["filtered_szones"] = n_sz
    # export
    page.click("[data-testid=act-export]")
    shot("07_export_menu")
    exp = {}
    for fmt in ("csv", "geojson"):
        with page.expect_download() as dl:
            page.click(f"[data-testid=export-scene_zones-{fmt}]")
        p = OUT / f"{tag}_export_scene_zones.{fmt}"
        dl.value.save_as(str(p))
        body = p.read_text(encoding="utf-8-sig")
        exp[fmt] = len(list(csv.DictReader(io.StringIO(body)))) if fmt == "csv" else len(json.loads(body)["features"])
    steps["export"] = {"ui_count": n_sz, **exp, "equal": exp["csv"] == exp["geojson"] == n_sz}
    page.keyboard.press("Escape")
    page.mouse.click(100, 60)  # close the menu: the brand line of the left column (the map toolbar moved, §33)
    # save query → reset → run saved
    page.click("[data-testid=act-queries]")
    page.fill("[data-testid=q-name]", f"Демо Cózar {tag}")
    page.click("[data-testid=q-save]")
    page.wait_for_timeout(1200)
    page.keyboard.press("Escape")
    page.click("[data-testid=f-reset]")
    wait(page, "() => window.__app.ready && window.__app.q.from === null && window.__app.szReady && window.__app.counts.szones > 23")
    steps["after_reset_szones"] = page.evaluate("() => window.__app.counts.szones")
    page.click("[data-testid=act-queries]")
    row = page.locator("[data-testid=q-item]", has_text=f"Демо Cózar {tag}").first
    row.locator("[data-testid=q-run]").click()
    wait(page, "() => window.__app.ready && window.__app.q.from === '2021-03-11' && window.__app.szReady")
    page.wait_for_timeout(1500)
    steps["rerun"] = {"toast": page.locator("[data-testid=toast]").inner_text() if page.locator("[data-testid=toast]").count() else None,
                      "szones": page.evaluate("() => window.__app.counts.szones"), "q_from": page.evaluate("() => window.__app.q.from")}
    shot("08_query_rerun")
    # legend with model version
    steps["legend_model"] = page.locator("[data-testid=legend-model]").inner_text() if page.locator("[data-testid=legend-model]").count() else None
    steps["console_errors"] = [e for e in errs if "arcgisonline" not in e and "carto" not in e][:10]
    steps["http_errors"] = bad[:10]
    res[tag] = steps
    b.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8094")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    res = {"base": a.base_url, "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "api_scene_zones_total": api(a.base_url, "/api/v3/scene_zones?limit=1")["total"],
           "api_demo_day": api(a.base_url, "/api/v3/scene_zones?date_from=2021-03-11&date_to=2021-03-11")["total"]}
    with sync_playwright() as pw:
        for tag, size in (("1920", (1920, 1080)), ("1366", (1366, 768))):
            try:
                run(a.base_url, size, tag, res, pw)
            except Exception as e:  # noqa: BLE001
                res[tag] = {**res.get(tag, {}), "error": repr(e)[:500]}
                print(tag, "ERROR", e)
    (OUT / "demo_path.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    main()
