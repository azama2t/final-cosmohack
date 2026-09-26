r"""L140 · §63 п.1: блок NASA в левой колонке — в потоке, без наложений на счётчик «N снимков · M находок» и на список.
.venv\Scripts\python.exe reports/qa/nasa_flow_check.py [URL]  → reports/qa/img/s63/nasa_flow_<размер>_<шаг>.png + JSON."""
import json, sys
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[2]
IMG = ROOT / "reports" / "qa" / "img" / "s63"; IMG.mkdir(parents=True, exist_ok=True)
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8094"
GEO = """() => { const r = (e) => { if (!e) return null; const q = e.getBoundingClientRect(); return [Math.round(q.left), Math.round(q.top), Math.round(q.right), Math.round(q.bottom)]; };
  const b = document.querySelector('[data-testid=nasa-block]'), s = b && b.querySelector('summary');
  const cnt = document.querySelector('[data-testid=count-scenes]')?.parentElement, lb = document.querySelector('[data-testid=zone-list]');
  const ov = (a, c) => !!(a && c && a[0] < c[2] && a[2] > c[0] && a[1] < c[3] && a[3] > c[1]);
  const row = b && b.querySelector('.c-nasa-row'); const kids = row ? [...row.children].map(r) : [];
  const col = document.querySelector('aside.left, .left');
  const spill = b ? b.scrollHeight - b.clientHeight : 0;
  return { block: r(b), summary: r(s), counter: r(cnt), list: r(lb), column: r(col),
    pos: b && getComputedStyle(b).position, sum_pos: s && getComputedStyle(s).position,
    overlap_counter: ov(r(b), r(cnt)), overlap_list: ov(r(b), r(lb)), summary_inside: !!(s && b && r(s)[1] >= r(b)[1] && r(s)[3] <= r(b)[3]),
    spill, one_row: kids.length === 2 && Math.abs(kids[0][1] - kids[1][1]) < 4, inside_col: !!(b && col && r(b)[2] <= r(col)[2]),
    why_text: !!document.querySelector('[data-testid=nasa-caption]'), hscroll: document.documentElement.scrollWidth > innerWidth + 1 }; }"""
out = {}
with sync_playwright() as p:
    br = p.chromium.launch()
    for tag, w, h, mob in [("1366", 1366, 768, False), ("1920", 1920, 1080, False)]:
        ctx = br.new_context(viewport={"width": w, "height": h}, device_scale_factor=1); pg = ctx.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and errs.append(m.text[:160]))
        pg.goto(URL + "/"); pg.wait_for_timeout(5000)
        pg.evaluate("() => [...document.querySelectorAll('button')].find(b => /^NASA/.test(b.innerText.trim()))?.click()")
        pg.wait_for_timeout(2500)
        r = {"on": pg.evaluate(GEO)}; pg.screenshot(path=str(IMG / f"nasa_flow_{tag}_1_on.png"))
        pg.click("[data-testid=nasa-why]"); pg.wait_for_timeout(400)
        r["why"] = pg.evaluate(GEO); pg.screenshot(path=str(IMG / f"nasa_flow_{tag}_2_why.png"))
        pg.click("[data-testid=nasa-why]"); pg.click("[data-testid=nasa-next]") if pg.is_enabled("[data-testid=nasa-next]") else pg.click("[data-testid=nasa-prev]")
        pg.click("[data-testid=nasa-layer-modis] >> nth=0"); pg.wait_for_timeout(600)
        r["after_ctrl"] = pg.evaluate(GEO)
        pg.click("[data-testid=nasa-off]"); pg.wait_for_timeout(600)
        r["off"] = pg.evaluate(GEO); pg.screenshot(path=str(IMG / f"nasa_flow_{tag}_3_off.png"))
        r["errors"] = errs; out[tag] = r; ctx.close()
    br.close()
print(json.dumps(out, ensure_ascii=False, indent=1))
