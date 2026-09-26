"""Jury-human timed tasks on http://localhost:8070 (headless Chromium).
Measures real UI latency per step, takes a screenshot at each task end state,
checks whether the answer is inside the viewport (no scroll).
Human time = sum(measured latency) + 2.5 s per click (KLM: point 1.1 + mental 1.35)
             + reading time (0.25 s/word of text that must be read to get the answer).
Usage: python tasks.py TAG WIDTH  -> TAG_WIDTH_tasks.json + img/TAG_WIDTH_tN.png
"""
import sys, time, os, json, re
from playwright.sync_api import sync_playwright

TAG = sys.argv[1]; W = int(sys.argv[2]); H = {1366: 768, 1920: 1080}[W]
URL = "http://localhost:8070"
D = os.path.dirname(os.path.abspath(__file__)); IMG = os.path.join(D, "img")
CLICK = 2.5

IN_VIEW_JS = """(re) => { const rx=new RegExp(re); const W=innerWidth,H=innerHeight; const hits=[];
 const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT); let n;
 while(n=walker.nextNode()){ const el=n.parentElement; if(!el) continue; const t=(el.innerText||'').trim(); if(!rx.test(n.textContent)&&!rx.test(t)) continue;
  const r=el.getBoundingClientRect(); const cs=getComputedStyle(el);
  if(cs.visibility==='hidden'||cs.display==='none'||r.width===0||r.height===0) continue;
  hits.push({t:t.slice(0,160), inView: r.bottom>0&&r.top<H&&r.right>0&&r.left<W, y:Math.round(r.top)}); }
 return hits.slice(0,8); }"""

res = {"tag": TAG, "width": W, "time": time.strftime("%H:%M"), "tasks": {}}

def shot(pg, name):
    p = os.path.join(IMG, f"{TAG}_{W}_{name}.png"); pg.screenshot(path=p); return os.path.relpath(p, D)

def find(pg, rx):
    try: return pg.evaluate(IN_VIEW_JS, rx)
    except Exception as e: return [{"err": str(e)}]

def fresh(pg):
    pg.goto(URL); pg.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch(e) {} }")
    pg.goto(URL + "/?mode=case"); pg.wait_for_selector("button.c-zi", timeout=45000); pg.wait_for_timeout(1500)

def timed_click(pg, sel, wait_sel=None, wait_ms=0, timeout=30000):
    t0 = time.time(); pg.click(sel, timeout=10000)
    if wait_sel: pg.wait_for_selector(wait_sel, timeout=timeout)
    if wait_ms: pg.wait_for_timeout(wait_ms)
    return round(time.time() - t0, 2)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(viewport={"width": W, "height": H}, locale="ru-RU", accept_downloads=True)
    pg = ctx.new_page()
    errs = []
    pg.on("response", lambda r: errs.append(f"{r.status} {r.url}") if r.status >= 400 else None)
    pg.on("pageerror", lambda e: errs.append(f"pageerror {e}"))

    # T1 understand service
    t0 = time.time(); pg.goto(URL); pg.wait_for_selector("button.c-zi", timeout=45000)
    t_panel = round(time.time() - t0, 2)
    try: pg.wait_for_load_state("networkidle", timeout=20000)
    except Exception: pass
    t_idle = round(time.time() - t0, 2); pg.wait_for_timeout(1000)
    title = pg.evaluate("() => document.title")
    res["tasks"]["t1"] = {"load_panel_s": t_panel, "load_idle_s": t_idle, "title": title, "shot": shot(pg, "t1"),
        "has_kpi_items_km2_in_view": [h for h in find(pg, r"шт\./км²") if h.get("inView")][:5],
        "has_glavnoe": find(pg, "Главное")}

    # T2 concentration + interval (natural path: tab Измерения -> first item)
    fresh(pg)
    lat = []
    try:
        lat.append(timed_click(pg, "button.tab:has-text('Измерения')", wait_ms=300))
        s_list = shot(pg, "t2a_list")
        t0 = time.time(); pg.locator("button.c-zi, .c-zi").first.click()
        pg.wait_for_selector("text=Измерение · полевое наблюдение", timeout=15000); lat.append(round(time.time()-t0, 2))
        pg.wait_for_timeout(800)
        card = pg.evaluate("() => { const el=[...document.querySelectorAll('*')].find(e=>/Измерение · полевое наблюдение/.test(e.textContent)&&e.children.length<40&&e.getBoundingClientRect().left>W_LEFT); return el? el.innerText.slice(0,1500):'' }".replace("W_LEFT", str(W//2)))
        res["tasks"]["t2"] = {"latencies_s": lat, "clicks": 2, "shot_list": s_list, "shot": shot(pg, "t2b_card"),
            "interval_hits": find(pg, r"интервал|ДИ|\[\d"), "card_text": card}
    except Exception as e:
        res["tasks"]["t2"] = {"error": str(e), "latencies_s": lat, "shot": shot(pg, "t2_err")}

    # T3 measured vs estimate: read the card from T2 + zone card
    res["tasks"]["t3"] = {"labels_in_view": find(pg, r"Измерено|Вероятно|Оценка по полевым|измерение|модельн|сценари|Исследовательская")}

    # T4 satellite zone: tab Зоны default -> first zone
    fresh(pg); lat = []
    try:
        t0 = time.time(); pg.locator("button.c-zi").first.click()
        pg.wait_for_selector("text=Спутниковая зона", timeout=15000); lat.append(round(time.time()-t0, 2)); pg.wait_for_timeout(1200)
        res["tasks"]["t4"] = {"latencies_s": lat, "clicks": 1, "shot": shot(pg, "t4_zone"),
            "status_hits": find(pg, r"обнаружено|не подтвержден|концентрация|недоступна|не проверено")}
    except Exception as e:
        res["tasks"]["t4"] = {"error": str(e), "shot": shot(pg, "t4_err")}

    # T5 photo count: Фото -> (example preloaded?) else Пример 1
    fresh(pg); lat = []
    try:
        lat.append(timed_click(pg, "button:has-text('Фото')", wait_sel="text=Счётчик предметов", timeout=20000))
        pg.wait_for_timeout(1500); s0 = shot(pg, "t5a_open")
        pre = pg.evaluate("() => document.body.innerText.match(/(\\d+)\\s*предмет/)")
        clicks = 1
        if not pre or True:
            t0 = time.time(); pg.click("text=Пример 1"); clicks += 1
            ok = True
            try:
                pg.wait_for_function("() => /Неизвестные параметры|ошибка|Ошибка/.test(document.body.innerText) || (document.body.innerText.match(/\\n(\\d+)\\s*\\n?\\s*предмет/))", timeout=60000)
            except Exception:
                ok = False
            lat.append(round(time.time()-t0, 2)); pg.wait_for_timeout(1000)
        body = pg.evaluate("() => document.body.innerText")
        m = re.search(r"Неизвестные параметры[^\n]*|Ошибка[^\n]*", body)
        res["tasks"]["t5"] = {"latencies_s": lat, "clicks": clicks, "shot_open": s0, "shot": shot(pg, "t5b_result"),
            "preloaded_count_match": pre, "error_text": m.group(0) if m else None,
            "count_hits": find(pg, r"предмет|шт\./кадр|шт\./м²")}
    except Exception as e:
        res["tasks"]["t5"] = {"error": str(e), "shot": shot(pg, "t5_err")}

    # T6 export
    fresh(pg); lat = []
    try:
        lat.append(timed_click(pg, "button:has-text('Выгрузка')", wait_ms=300))
        s0 = shot(pg, "t6a_menu")
        t0 = time.time()
        with pg.expect_download(timeout=20000) as dl:
            pg.locator("text=CSV").first.click()
        d = dl.value; lat.append(round(time.time()-t0, 2))
        path = os.path.join(IMG, f"{TAG}_{W}_{d.suggested_filename}"); d.save_as(path)
        res["tasks"]["t6"] = {"latencies_s": lat, "clicks": 2, "shot": s0, "file": d.suggested_filename, "bytes": os.path.getsize(path)}
        os.remove(path)
    except Exception as e:
        res["tasks"]["t6"] = {"error": str(e), "shot": shot(pg, "t6_err")}

    # T7 metrics: tab Метрики
    fresh(pg); lat = []
    try:
        lat.append(timed_click(pg, "button.tab:has-text('Метрики')", wait_ms=500))
        res["tasks"]["t7"] = {"latencies_s": lat, "clicks": 1, "shot": shot(pg, "t7_metrics"),
            "f1_hits": find(pg, r"^F1"), "mae_hits": find(pg, r"MAE"), "median_hits": find(pg, r"медиан")}
    except Exception as e:
        res["tasks"]["t7"] = {"error": str(e), "shot": shot(pg, "t7_err")}

    res["http_errors"] = errs[:30]
    with open(os.path.join(D, f"{TAG}_{W}_tasks.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    b.close()
print("ok")

