"""Jury-human probe for §33 path: Earth overview -> marker -> zone card -> back.
Usage: python probe33.py TAG WIDTH
Dumps texts/clickables after each step + screenshots to img/TAG_W_pN.png
"""
import sys, time, os, json
from playwright.sync_api import sync_playwright
from PIL import Image
import numpy as np

TAG = sys.argv[1]; W = int(sys.argv[2]); H = {1366: 768, 1920: 1080}[W]
URL = "http://localhost:8070"
D = os.path.dirname(os.path.abspath(__file__)); IMG = os.path.join(D, "img")
out = {"tag": TAG, "w": W, "steps": []}

VIS = """() => { const out=[]; const W=innerWidth,H=innerHeight;
 const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT); let n;
 while(n=walker.nextNode()){ const t=n.textContent.trim(); if(!t) continue; const el=n.parentElement; if(!el) continue;
  const r=el.getBoundingClientRect(); const cs=getComputedStyle(el);
  if(cs.visibility==='hidden'||cs.display==='none'||r.width===0||r.height===0) continue;
  if(r.bottom>0&&r.top<H&&r.right>0&&r.left<W) out.push(Math.round(r.left)+','+Math.round(r.top)+' '+t.slice(0,220)); }
 return out; }"""
CL = """() => Array.from(document.querySelectorAll('a,button,[role=tab],[role=button],select,input,summary')).filter(e=>{const r=e.getBoundingClientRect(); return r.width>0&&r.bottom>0&&r.top<innerHeight}).map(e=>{const r=e.getBoundingClientRect(); return e.tagName+' '+Math.round(r.left)+','+Math.round(r.top)+' '+(e.innerText||e.value||e.title||e.getAttribute('aria-label')||'').trim().replace(/\\s+/g,' ').slice(0,80)})"""


def snap(pg, name):
    p = os.path.join(IMG, f"{TAG}_{W}_{name}.png"); pg.screenshot(path=p)
    out["steps"].append({"name": name, "text": pg.evaluate(VIS), "click": pg.evaluate(CL)})
    return p


def orange_markers(path, x0):
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    m = (r > 200) & (g > 100) & (g < 170) & (b < 110)
    m[:, :x0] = False
    ys, xs = np.nonzero(m)
    if len(xs) == 0: return []
    # crude clustering
    pts = []
    for x, y in zip(xs, ys):
        for c in pts:
            if abs(c[0] - x) < 25 and abs(c[1] - y) < 25: c[2] += 1; break
        else: pts.append([x, y, 1])
    return [(int(x), int(y), n) for x, y, n in pts if n > 20]


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(viewport={"width": W, "height": H}, locale="ru-RU", accept_downloads=True)
    pg = ctx.new_page()
    t0 = time.time(); pg.goto(URL)
    marks = []
    for i in range(40):
        pg.wait_for_timeout(500)
        ph = os.path.join(IMG, f"{TAG}_{W}_p0.png"); pg.screenshot(path=ph)
        marks = [m for m in orange_markers(ph, 320) if pg.evaluate("([x,y]) => (document.elementFromPoint(x,y)||{}).tagName", [m[0], m[1]]) == "CANVAS"]
        if marks: break
    out["markers_visible_s"] = round(time.time() - t0, 2); out["markers"] = marks
    snap(pg, "p0")
    if marks:
        x, y, _ = max(marks, key=lambda m: m[2])
        pg.mouse.move(x, y); pg.wait_for_timeout(800); snap(pg, "p1_hover")
        t1 = time.time(); pg.mouse.click(x, y); pg.wait_for_timeout(2500)
        out["click_marker"] = [x, y]; snap(pg, "p2_after_click")
        pg.wait_for_timeout(3000); snap(pg, "p3_after_click5s")
    json.dump(out, open(os.path.join(D, f"{TAG}_{W}_probe33.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    b.close()
print("ok", out.get("markers_visible_s"), out.get("markers"))

