"""Shared helpers for jury-human Playwright runs (no side effects on import)."""
import os, time
import numpy as np
from PIL import Image

URL = "http://localhost:8070"
D = os.path.dirname(os.path.abspath(__file__)); IMG = os.path.join(D, "img")
os.makedirs(IMG, exist_ok=True)

VIS = """() => { const out=[]; const W=innerWidth,H=innerHeight;
 const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT); let n;
 while(n=walker.nextNode()){ const t=n.textContent.trim(); if(!t) continue; const el=n.parentElement; if(!el) continue;
  const r=el.getBoundingClientRect(); const cs=getComputedStyle(el);
  if(cs.visibility==='hidden'||cs.display==='none'||r.width===0||r.height===0) continue;
  if(r.bottom>0&&r.top<H&&r.right>0&&r.left<W) out.push(Math.round(r.left)+','+Math.round(r.top)+' '+t.slice(0,240)); }
 return out; }"""
ALLTXT = """() => { const out=[];
 const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT); let n;
 while(n=walker.nextNode()){ const t=n.textContent.trim(); if(!t) continue; const el=n.parentElement; if(!el) continue;
  const r=el.getBoundingClientRect(); const cs=getComputedStyle(el);
  if(cs.visibility==='hidden'||cs.display==='none'||r.width===0||r.height===0) continue;
  const iv = r.bottom>0&&r.top<innerHeight&&r.right>0&&r.left<innerWidth;
  out.push((iv?'V ':'- ')+Math.round(r.left)+','+Math.round(r.top)+' '+t.slice(0,240)); }
 return out; }"""
CL = """() => Array.from(document.querySelectorAll('a,button,[role=tab],[role=button],select,input,summary')).filter(e=>{const r=e.getBoundingClientRect(); return r.width>0&&r.bottom>0&&r.top<innerHeight}).map(e=>{const r=e.getBoundingClientRect(); return e.tagName+' '+Math.round(r.left)+','+Math.round(r.top)+' '+(e.innerText||e.value||e.title||e.getAttribute('aria-label')||'').trim().replace(/\\s+/g,' ').slice(0,80)})"""
INVIEW = """(re) => { const rx=new RegExp(re,'i'); const W=innerWidth,H=innerHeight; const hits=[];
 const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT); let n;
 while(n=walker.nextNode()){ if(!rx.test(n.textContent)) continue; const el=n.parentElement; if(!el) continue;
  const r=el.getBoundingClientRect(); const cs=getComputedStyle(el);
  if(cs.visibility==='hidden'||cs.display==='none'||r.width===0||r.height===0) continue;
  hits.push({t:(el.innerText||'').trim().slice(0,200), inView: r.bottom>0&&r.top<H&&r.right>0&&r.left<W, x:Math.round(r.left), y:Math.round(r.top)}); }
 return hits.slice(0,12); }"""


def orange_markers(path, pg=None, x0=320):
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    m = (r > 200) & (g > 100) & (g < 210) & (b < 140) & (r - b > 90)
    m[:, :x0] = False
    ys, xs = np.nonzero(m)
    pts = []
    for x, y in zip(xs[::3], ys[::3]):
        for c in pts:
            if abs(c[0] - x) < 20 and abs(c[1] - y) < 20: c[2] += 1; break
        else: pts.append([x, y, 1])
    pts = [(int(x), int(y), n) for x, y, n in pts if n > 6]
    if pg is not None:
        pts = [q for q in pts if pg.evaluate("([x,y]) => (document.elementFromPoint(x,y)||{}).tagName", [q[0], q[1]]) == "CANVAS"]
    return pts


class Run:
    def __init__(self, tag, w):
        self.tag, self.w = tag, w; self.dumps = {}

    def shot(self, pg, name, dump=True):
        p = os.path.join(IMG, f"{self.tag}_{self.w}_{name}.png"); pg.screenshot(path=p)
        if dump:
            self.dumps[name] = {"text": pg.evaluate(VIS), "click": pg.evaluate(CL)}
        return os.path.relpath(p, os.path.dirname(D)).replace("\\", "/")

    def inview(self, pg, rx):
        try: return pg.evaluate(INVIEW, rx)
        except Exception as e: return [{"err": str(e)}]

    def wait_markers(self, pg, name, tries=40):
        t0 = time.time()
        for _ in range(tries):
            pg.wait_for_timeout(400)
            ph = os.path.join(IMG, f"{self.tag}_{self.w}_{name}.png"); pg.screenshot(path=ph)
            mk = orange_markers(ph, pg)
            if mk: return mk, round(time.time() - t0, 2)
        return [], round(time.time() - t0, 2)
