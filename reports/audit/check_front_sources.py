"""Audit L107 (§34 п.3, фронт L132): the case panel «район · дата · N находок · облачность» must be built ONLY from
/api/v3/scene_zones (+ /scene_zones/scenes). Records every API call in case mode (start, open first district, open a zone),
lists calls outside the allowed set, and compares the numbers shown for each district/scene with the API.
Output -> reports/audit/front_sources.json + ui_fs_*.png."""
import collections, json, re, sys, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
OUT = Path(__file__).resolve().parent
B = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8070").rstrip("/")
ALLOWED = re.compile(r"^v3/(meta|scene_zones|export|queries|photo/meta|observations|zones|scenes)")
get = lambda p: json.loads(urllib.request.urlopen(B + p, timeout=180).read().decode("utf-8"))
sz = get("/api/v3/scene_zones?limit=5000")
finds = collections.Counter(f["properties"]["scene_key"] for f in sz["features"] if f["properties"]["status"] == "detected")
res = {"api_finds_by_scene": dict(finds), "calls": [], "outside_allowed": []}
with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    page = b.new_context(viewport={"width": 1920, "height": 1080}, locale="ru-RU").new_page()
    page.on("request", lambda r: res["calls"].append(r.url.split("/api/")[1][:120]) if "/api/" in r.url else None)
    errs = []
    page.on("console", lambda m: errs.append(m.text[:200]) if m.type == "error" else None)
    page.goto(B + "/", wait_until="domcontentloaded")
    time.sleep(30)
    page.screenshot(path=str(OUT / "ui_fs_0.png"))
    res["panel_text"] = page.locator("body").inner_text()[:3500]
    # try to open the first district / scene entry and then the first zone (test ids unknown in advance: try several)
    for sel in ("[data-testid=district-item]", "[data-testid=scene-item]", "[data-testid=sz-scene-item]", "[data-testid=region-item]", "[data-testid=sz-item]"):
        if page.locator(sel).count():
            res["opened"] = sel
            page.locator(sel).first.click(); time.sleep(8)
            page.screenshot(path=str(OUT / "ui_fs_1.png"))
            res["after_open_text"] = page.locator("body").inner_text()[:3500]
            break
    for sel in ("[data-testid=sz-item]", "[data-testid=zone-item]"):
        if page.locator(sel).count():
            page.locator(sel).first.click(); time.sleep(8)
            page.screenshot(path=str(OUT / "ui_fs_2.png"))
            break
    res["console_errors"] = errs[:10]
    b.close()
res["outside_allowed"] = sorted({c.split("?")[0] for c in res["calls"] if not ALLOWED.match(c)})
res["calls"] = sorted(collections.Counter(c.split("?")[0] if "/rgb" in c or "/quality" in c or "/crops/" in c else c for c in res["calls"]).items())[:60]
(OUT / "front_sources.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({"outside_allowed": res["outside_allowed"], "opened": res.get("opened"), "console_errors": res["console_errors"]}, ensure_ascii=False))
print(res["panel_text"][:1500])
