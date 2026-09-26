"""Jury-human timed tasks (§31 п.3 as updated by §33/§33а п.6) on http://localhost:8070.
Measures UI latency per step (headless Chromium, own session), screenshots each end state,
checks whether the needed answer is inside the viewport without scrolling.
Human time (reported separately) = measured latency + 2.5 s per click (KLM point+mental) + reading.
Usage: python tasks33.py TAG WIDTH  -> TAG_WIDTH_tasks33.json + img/TAG_WIDTH_tN*.png
"""
import sys, time, os, json, csv, io
from playwright.sync_api import sync_playwright
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jh import Run, URL, D, IMG, ALLTXT, orange_markers

TAG = sys.argv[1]; W = int(sys.argv[2]); H = {1366: 768, 1920: 1080}[W]
R = Run(TAG, W)
res = {"tag": TAG, "width": W, "time": time.strftime("%H:%M:%S"), "tasks": {}}
CARD_SEL = "text=В студию"


def card_open(pg):
    return pg.evaluate("() => [...document.querySelectorAll('button')].some(b => /В студию/.test(b.innerText) && b.getBoundingClientRect().width > 0)")


def card_checks(pg):
    q = {
        "area": r"м²", "date": r"\d{2}\.\d{2}\.\d{4}.*UTC|UTC", "quality": r"облак|блик|ветер|маска качества",
        "status": r"обнаружено|статус|требует проверки", "confidence": r"вероятность детектора|уверенност",
        "count_undetermined_exact": r"Количество предметов по этому снимку не определено",
        "count_undetermined_any": r"не определено по этому снимку|шт\./км² по снимку не выдаём",
        "composition": r"Состав|не определён", "explore_btn": r"Исследовать дальше",
        "field_nearby_numbers": r"Поле рядом|C = N/A, шт\./км²", "nearest_field_line": r"Ближайшее полевое измерение",
        "demo_word": r"\(демо\)",
    }
    out = {}
    for k, rx in q.items():
        h = R.inview(pg, rx)
        out[k] = {"in_view": any(x.get("inView") for x in h), "present": bool(h), "first": (h[0]["t"][:120] if h else None)}
    out["thumb_in_view"] = pg.evaluate("() => [...document.querySelectorAll('img')].some(i => { const r=i.getBoundingClientRect(); return r.left>innerWidth*0.55 && r.top<innerHeight && r.bottom>0 && r.width>40 && i.complete && i.naturalWidth>0 && r.bottom<=innerHeight+5 })")
    out["thumb_partial"] = pg.evaluate("() => [...document.querySelectorAll('img')].filter(i => { const r=i.getBoundingClientRect(); return r.left>innerWidth*0.55 && r.width>40 }).map(i=>{const r=i.getBoundingClientRect(); return [Math.round(r.top), Math.round(r.bottom)]})")
    return out


def open_zone_via_map(pg, name):
    """Click the largest orange marker, re-detect after zoom, repeat until the zone card opens."""
    clicks, lat, path = 0, 0.0, []
    for step in range(4):
        mk, t = R.wait_markers(pg, f"{name}_s{step}", tries=15); lat += 0  # detection time not UI latency
        if not mk: break
        if step == 0: x, y, _ = max(mk, key=lambda m: m[2])
        else:  # nearest to the map centre
            cx, cy = (W + 330) / 2, H / 2; x, y, _ = min(mk, key=lambda m: (m[0] - cx) ** 2 + (m[1] - cy) ** 2)
        t0 = time.time(); pg.mouse.click(x, y); clicks += 1; path.append([x, y])
        try:
            pg.wait_for_function("() => [...document.querySelectorAll('button')].some(b => /В студию/.test(b.innerText) && b.getBoundingClientRect().width > 0)", timeout=5000)
            lat += round(time.time() - t0, 2); pg.wait_for_timeout(700); return clicks, round(lat, 2), path
        except Exception:
            pg.wait_for_timeout(1500); lat += round(time.time() - t0, 2)
    return clicks, round(lat, 2), path if card_open(pg) else None


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(viewport={"width": W, "height": H}, locale="ru-RU", accept_downloads=True)
    pg = ctx.new_page()
    errs = []
    pg.on("response", lambda r: errs.append(f"{r.status} {r.url}") if r.status >= 400 else None)
    pg.on("pageerror", lambda e: errs.append(f"pageerror {e}"))
    pg.on("console", lambda m: errs.append(f"console.error {m.text[:200]}") if m.type == "error" else None)

    # ---------- T1 understand service
    t0 = time.time(); pg.goto(URL)
    mk, _ = R.wait_markers(pg, "t1_wait", tries=60); t_mk = round(time.time() - t0, 2)
    try:
        pg.wait_for_function("() => /30SXE · зона/.test(document.body.innerText)", timeout=30000)
    except Exception: pass
    t_list = round(time.time() - t0, 2)
    res["tasks"]["t1"] = {"markers_visible_s": t_mk, "n_marker_blobs": len(mk), "zone_list_filled_s": t_list,
                          "shot": R.shot(pg, "t1_first"),
                          "one_liner_in_view": R.inview(pg, r"сервис|находит|ищет|показывает|детектор"),
                          "title_in_view": R.inview(pg, r"Плавающий мусор"),
                          "legend_in_view": R.inview(pg, r"находка"),
                          "filters_in_view": R.inview(pg, r"Акватория|Даты|Профиль")}

    # ---------- T2 zone via map (from overview)
    try:
        clicks, lat, path = open_zone_via_map(pg, "t2")
        res["tasks"]["t2_map"] = {"clicks": clicks, "ui_latency_s": lat, "path": path, "opened": card_open(pg),
                                  "shot": R.shot(pg, "t2_map_card"), "checks": card_checks(pg) if card_open(pg) else None,
                                  "card_head": [t for t in pg.evaluate(ALLTXT) if t.startswith("V ") and int(t[2:].split(",")[0]) > W * 0.6][:40]}
    except Exception as e:
        res["tasks"]["t2_map"] = {"error": str(e)[:300], "shot": R.shot(pg, "t2_map_err")}

    # ---------- T9 back to overview and choose another find (from T2 state)
    try:
        if card_open(pg):
            before = pg.evaluate("() => (document.querySelector('.maplibregl-canvas, canvas')||{}).toDataURL ? 1 : 0")
            t0 = time.time(); pg.click("text=← Назад", timeout=5000)
            pg.wait_for_timeout(1500); lat_back = round(time.time() - t0, 2)
            s_back = R.shot(pg, "t9a_after_back")
            still_card = card_open(pg)
            # second: explicit Earth overview
            t0 = time.time(); pg.click("text=Обзор Земли", timeout=5000); pg.wait_for_timeout(2500)
            lat_earth = round(time.time() - t0, 2); s_earth = R.shot(pg, "t9b_earth")
            clicks2, lat2, path2 = open_zone_via_map(pg, "t9c")
            res["tasks"]["t9"] = {"back_latency_s": lat_back, "card_after_back": still_card, "shot_back": s_back,
                                  "earth_latency_s": lat_earth, "shot_earth": s_earth,
                                  "another_find_clicks": clicks2, "another_find_latency_s": lat2, "another_opened": card_open(pg),
                                  "shot_another": R.shot(pg, "t9c_another"),
                                  "another_title": [t for t in pg.evaluate(ALLTXT) if t.startswith("V ") and int(t[2:].split(",")[0]) > W * 0.6][:4]}
        else:
            res["tasks"]["t9"] = {"error": "no card from t2"}
    except Exception as e:
        res["tasks"]["t9"] = {"error": str(e)[:300], "shot": R.shot(pg, "t9_err")}

    # ---------- T2b zone via left list (fresh)
    pg.goto(URL); pg.wait_for_timeout(500)
    try:
        pg.wait_for_function("() => /30SXE · зона/.test(document.body.innerText)", timeout=30000)
        item = pg.locator("text=/30SXE · зона/").first
        box = item.bounding_box(); in_view = bool(box and box["y"] + box["height"] <= H)
        t0 = time.time(); item.click(timeout=5000)
        pg.wait_for_function("() => [...document.querySelectorAll('button')].some(b => /В студию/.test(b.innerText) && b.getBoundingClientRect().width > 0)", timeout=10000)
        lat = round(time.time() - t0, 2); pg.wait_for_timeout(1500)
        res["tasks"]["t2_list"] = {"item_fully_in_view": in_view, "item_box": box, "ui_latency_s": lat, "clicks": 1,
                                   "shot": R.shot(pg, "t2_list_card"), "checks": card_checks(pg)}
        # T4 measured vs estimate labels in the card
        res["tasks"]["t4"] = {"labels_in_view": [h for h in R.inview(pg, r"измерено|оценка|нет данных|исследовательск|посчитано") if h.get("inView")],
                              "labels_all": R.inview(pg, r"измерено|оценка|нет данных|исследовательск|посчитано")}
        # scrollability of the card
        res["tasks"]["t2_list"]["card_scroll"] = pg.evaluate("""() => [...document.querySelectorAll('*')].filter(e=>{const r=e.getBoundingClientRect(); const cs=getComputedStyle(e); return r.left>innerWidth*0.55 && e.scrollHeight>e.clientHeight+20 && /(auto|scroll)/.test(cs.overflowY)}).map(e=>[e.className.toString().slice(0,40), e.scrollHeight, e.clientHeight])""")
    except Exception as e:
        res["tasks"]["t2_list"] = {"error": str(e)[:300], "shot": R.shot(pg, "t2_list_err")}

    # ---------- T3 field concentration via panel (fresh)
    pg.goto(URL); pg.wait_for_timeout(2500)
    try:
        t0 = time.time(); pg.click("text=/Цифры: поле/", timeout=8000); pg.wait_for_timeout(400); lat = round(time.time() - t0, 2)
        res["tasks"]["t3"] = {"clicks": 1, "ui_latency_s": lat, "shot": R.shot(pg, "t3_cifry"),
                              "numbers_in_view": [h for h in R.inview(pg, r"\[\d+[,.]?\d*\s*[–-]\s*\d") if h.get("inView")][:8],
                              "unit_in_view": [h for h in R.inview(pg, r"шт\./км²") if h.get("inView")][:3]}
        # field layer on the map: are field points visible at overview?
        pg.click("button:has-text('Слои')", timeout=5000); pg.wait_for_timeout(600)
        res["tasks"]["t3"]["layers_shot"] = R.shot(pg, "t3_layers")
        res["tasks"]["t3"]["layer_names"] = [h["t"] for h in R.inview(pg, r"Полев|измерен") if h.get("inView")][:6]
    except Exception as e:
        res["tasks"]["t3"] = {"error": str(e)[:300], "shot": R.shot(pg, "t3_err")}

    # ---------- T5 photo (fresh)
    pg.goto(URL); pg.wait_for_timeout(2500)
    try:
        t0 = time.time(); pg.click("button:has-text('Фото')", timeout=8000)
        pg.wait_for_function("() => /предмет\\S* на кадр/.test(document.body.innerText) && /\\n\\s*\\d+\\s*\\n/.test(document.body.innerText)", timeout=60000)
        lat = round(time.time() - t0, 2); pg.wait_for_timeout(800)
        res["tasks"]["t5"] = {"clicks": 1, "ui_latency_s": lat, "shot": R.shot(pg, "t5_photo"),
                              "count_in_view": [h for h in R.inview(pg, r"предмет\S* на кадр|шт\./кадр") if h.get("inView")][:4],
                              "switch": [h["t"] for h in R.inview(pg, r"FML|TOCL|Winans") if h.get("inView")][:6],
                              "classes": [h["t"] for h in R.inview(pg, r"Состав|класс") if h.get("inView")][:4]}
        # switch example -> TOCL
        try:
            t0 = time.time(); pg.click("text=Река · TOCL", timeout=5000); pg.wait_for_timeout(4000)
            res["tasks"]["t5"]["tocl_latency_s"] = round(time.time() - t0, 2); res["tasks"]["t5"]["tocl_shot"] = R.shot(pg, "t5_tocl")
            res["tasks"]["t5"]["tocl_density"] = [h["t"] for h in R.inview(pg, r"шт\./м²|шт\./км²") if h.get("inView")][:6]
        except Exception as e:
            res["tasks"]["t5"]["tocl_error"] = str(e)[:200]
    except Exception as e:
        res["tasks"]["t5"] = {"error": str(e)[:300], "shot": R.shot(pg, "t5_err")}

    # ---------- T6 export (fresh)
    pg.goto(URL); pg.wait_for_timeout(2500)
    try:
        t0 = time.time(); pg.click("button:has-text('Выгрузка')", timeout=8000); pg.wait_for_timeout(300)
        l1 = round(time.time() - t0, 2); s0 = R.shot(pg, "t6_menu")
        t0 = time.time()
        with pg.expect_download(timeout=30000) as dl:
            pg.locator("text=CSV").first.click()
        d = dl.value; l2 = round(time.time() - t0, 2)
        path = os.path.join(IMG, f"{TAG}_{W}_{d.suggested_filename}"); d.save_as(path)
        raw = open(path, encoding="utf-8-sig", errors="replace").read()
        rows = list(csv.DictReader(io.StringIO(raw)))
        cols = list(rows[0].keys()) if rows else []
        conc_cols = [c for c in cols if "conc" in c.lower() or "шт" in c]
        conc_vals = {c: sorted({r[c] for r in rows})[:6] for c in conc_cols}
        res["tasks"]["t6"] = {"clicks": 2, "ui_latency_s": [l1, l2], "shot": s0, "file": d.suggested_filename,
                              "bytes": os.path.getsize(path), "n_rows": len(rows), "columns": cols[:80], "conc_values": conc_vals}
        os.remove(path)
    except Exception as e:
        res["tasks"]["t6"] = {"error": str(e)[:300], "shot": R.shot(pg, "t6_err")}

    # ---------- T7 metrics (fresh): tab Метрики in the left panel
    pg.goto(URL); pg.wait_for_timeout(2500)
    try:
        t0 = time.time(); pg.click("text=Метрики", timeout=8000); pg.wait_for_timeout(500); lat = round(time.time() - t0, 2)
        res["tasks"]["t7"] = {"clicks": 1, "ui_latency_s": lat, "shot": R.shot(pg, "t7_metrics"),
                              "f1_in_view": [h for h in R.inview(pg, r"F1") if h.get("inView")][:4],
                              "f1_any": R.inview(pg, r"F1")[:4],
                              "conc_baseline_in_view": [h for h in R.inview(pg, r"медиан|MAE") if h.get("inView")][:4],
                              "conc_baseline_any": R.inview(pg, r"медиан|MAE")[:6]}
        # try to scroll the left panel like a human with the wheel
        pg.mouse.move(150, H - 60); pg.mouse.wheel(0, 600); pg.wait_for_timeout(600)
        res["tasks"]["t7"]["after_wheel_shot"] = R.shot(pg, "t7_metrics_wheel")
        res["tasks"]["t7"]["f1_in_view_after_wheel"] = [h for h in R.inview(pg, r"F1") if h.get("inView")][:4]
        res["tasks"]["t7"]["mae_in_view_after_wheel"] = [h for h in R.inview(pg, r"медиан|MAE") if h.get("inView")][:4]
    except Exception as e:
        res["tasks"]["t7"] = {"error": str(e)[:300], "shot": R.shot(pg, "t7_err")}

    res["errors"] = errs[:40]
    json.dump({"res": res, "dumps": R.dumps}, open(os.path.join(D, f"{TAG}_{W}_tasks33.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    b.close()
print(json.dumps(res, ensure_ascii=False)[:200])
