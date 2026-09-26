"""Jury-human exploration: first screen, visible text, clickable elements.
Usage: python explore.py TAG [url]
Writes img/TAG_{w}_first.png and TAG_{w}_text.txt / TAG_{w}_clickables.txt
"""
import sys, time, json, os
from playwright.sync_api import sync_playwright

TAG = sys.argv[1] if len(sys.argv) > 1 else "x"
URL = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8070"
OUT = os.path.join(os.path.dirname(__file__), "img")
os.makedirs(OUT, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    for w, h in [(1366, 768), (1920, 1080)]:
        ctx = b.new_context(viewport={"width": w, "height": h}, locale="ru-RU")
        pg = ctx.new_page()
        errs = []
        pg.on("console", lambda m: errs.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        pg.on("response", lambda r: errs.append(f"HTTP {r.status} {r.url}") if r.status >= 400 else None)
        t0 = time.time()
        pg.goto(URL, wait_until="domcontentloaded")
        t_dom = time.time() - t0
        try:
            pg.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        t_idle = time.time() - t0
        pg.wait_for_timeout(1500)
        pg.screenshot(path=os.path.join(OUT, f"{TAG}_{w}_first.png"))
        pg.screenshot(path=os.path.join(OUT, f"{TAG}_{w}_full.png"), full_page=True)
        # visible text within viewport
        vis = pg.evaluate("""() => {
          const out=[]; const W=innerWidth,H=innerHeight;
          const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
          let n; while(n=walker.nextNode()){
            const t=n.textContent.trim(); if(!t) continue;
            const el=n.parentElement; if(!el) continue;
            const r=el.getBoundingClientRect(); const cs=getComputedStyle(el);
            if(cs.visibility==='hidden'||cs.display==='none'||r.width===0||r.height===0) continue;
            const inView = r.bottom>0 && r.top<H && r.right>0 && r.left<W;
            out.push([inView?'V':'-', Math.round(r.left), Math.round(r.top), Math.round(parseFloat(cs.fontSize)), t.slice(0,200)]);
          } return out; }""")
        with open(os.path.join(os.path.dirname(__file__), f"{TAG}_{w}_text.txt"), "w", encoding="utf-8") as f:
            f.write(f"dom {t_dom:.2f}s idle {t_idle:.2f}s\n")
            for row in vis:
                f.write("\t".join(map(str, row)) + "\n")
            f.write("\n--- errors ---\n" + "\n".join(errs))
        cl = pg.evaluate("""() => Array.from(document.querySelectorAll('a,button,[role=tab],[role=button],select,input,summary,[onclick]')).map(e=>{
            const r=e.getBoundingClientRect();
            return [e.tagName, e.id||'', (e.className||'').toString().slice(0,40), Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height), (e.innerText||e.value||e.title||e.getAttribute('aria-label')||e.href||'').trim().replace(/\\s+/g,' ').slice(0,100)]})""")
        with open(os.path.join(os.path.dirname(__file__), f"{TAG}_{w}_clickables.txt"), "w", encoding="utf-8") as f:
            for row in cl:
                f.write("\t".join(map(str, row)) + "\n")
        print(w, "dom", round(t_dom, 2), "idle", round(t_idle, 2), "errs", len(errs))
        ctx.close()
    b.close()
