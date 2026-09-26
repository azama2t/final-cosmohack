r"""Финальный скринкаст §65 А (L160 «КАСТ»): 14 шагов ТЗ + киллер-фичи, 1920×1080, вшитые русские субтитры.

Стиль — как на пожарном кейсе (SAMARKAND): титульная карточка, «плавный» курсор-точка, паузы 2–3 с на ключевых кадрах,
субтитры внизу крупным шрифтом с подложкой. Запись — Playwright record_video; субтитры — SRT, вшиваются ffmpeg (libass).
Только чтение: сервис не перезапускается, запросы не сохраняются, фикстур нет. Сломанный шаг пропускается и вырезается
из ролика (его отрезок не попадает в mp4), в отчёт — строка «пропущен».

    .venv\Scripts\python.exe presentation\make_screencast_final.py --base https://5-231-59-204.sslip.io:8443
    .venv\Scripts\python.exe presentation\make_screencast_final.py --dry      # без записи: скрины шагов в out\cast160\dry\

Выход: presentation/screencast_final.mp4 (H.264) + presentation/screencast_final.srt; временные файлы — out/cast160/ (удаляются).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WORK = ROOT / "out" / "cast160"
FFMPEG = Path(r"C:\ffmpeg\bin\ffmpeg.exe")
W, H = 1920, 1080
SCENE_TEXT = "Альборан"          # отложенная сцена Cózar 30SXE, 11.03.2021
DRIFT_TEXT = "Гондурас"          # снимки с бейджем «дрейф»

CARD = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>
html,body{height:100%;margin:0}body{display:flex;align-items:center;justify-content:center;background:#0b0f16;color:#eef2f7;
font:26px/1.5 "Segoe UI",system-ui,sans-serif}.c{text-align:center;max-width:1400px}.t{font-size:72px;font-weight:700;margin:0 0 10px}
.bar{width:110px;height:6px;border-radius:3px;background:#f08a3c;margin:26px auto}.s{font-size:38px;margin:0}.m{color:#9aa4b2;margin-top:22px;font-size:28px}
</style></head><body><div class="c"><p class="t">@T@</p><div class="bar"></div><p class="s">@S@</p><p class="m">@M@</p></div></body></html>"""

CURSOR_JS = """(() => { const add = () => { if (document.getElementById('__cur') || !document.body) return;
  const c = document.createElement('div'); c.id = '__cur';
  c.style.cssText = 'position:fixed;left:-50px;top:-50px;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;z-index:2147483647;' +
    'background:rgba(255,255,255,.95);border:2px solid #111;box-shadow:0 0 0 3px rgba(255,255,255,.45);pointer-events:none';
  document.body.appendChild(c);
  document.addEventListener('mousemove', (e) => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add(); })();"""


class Skip(Exception):
    pass


class Cast:
    def __init__(self, pg, dry: bool, pace: float):
        self.pg, self.dry, self.pace = pg, dry, pace
        self.t0 = time.perf_counter()
        self.subs: list[list] = []        # [start, end, text]
        self.cuts: list[tuple] = []       # вырезаемые отрезки (сломанные шаги)
        self.done: list[str] = []
        self.skipped: list[str] = []
        self.n = 0

    def now(self) -> float:
        return time.perf_counter() - self.t0

    def wait(self, sec: float):
        self.pg.wait_for_timeout(int(sec * self.pace * 1000))

    def say(self, text: str, hold: float = 2.5):
        """Субтитр держится до следующего; hold — пауза на кадре (2–3 с)."""
        t = self.now()
        if self.subs and self.subs[-1][1] is None:
            self.subs[-1][1] = t
        self.subs.append([t, None, text])
        self.wait(hold)

    def hush(self):
        if self.subs and self.subs[-1][1] is None:
            self.subs[-1][1] = self.now()

    def shot(self, name: str):
        if self.dry:
            self.n += 1
            self.pg.screenshot(path=str(WORK / "dry" / f"{self.n:02d}_{name}.png"))

    def loc(self, tid: str, text: str | None = None):
        sel = f"[data-testid={tid}]:visible"
        l = self.pg.locator(sel, has_text=text) if text else self.pg.locator(sel)
        return l

    def has(self, tid: str, text: str | None = None) -> bool:
        return self.loc(tid, text).count() > 0

    def need(self, tid: str, timeout: float = 20000, text: str | None = None):
        l = self.loc(tid, text).first
        try:
            l.wait_for(state="visible", timeout=timeout)
        except Exception as e:  # noqa: BLE001
            raise Skip(f"нет [{tid}]") from e
        return l

    def glide_to(self, l, click: bool = True, steps: int = 22):
        l.scroll_into_view_if_needed(timeout=10000)
        b = l.bounding_box()
        if not b:
            raise Skip("элемент без рамки")
        x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
        self.pg.mouse.move(x, y, steps=steps)
        self.wait(0.45)
        if click:
            self.pg.mouse.click(x, y)

    def click(self, tid: str, text: str | None = None, timeout: float = 20000):
        self.glide_to(self.need(tid, timeout, text))

    def step(self, name: str, fn):
        t_start = self.now()
        n_subs = len(self.subs)
        try:
            fn()
            self.hush()
            self.done.append(name)
            print(f"  ok   {name}  ({self.now() - t_start:.1f} с)")
        except Exception as e:  # noqa: BLE001
            msg = str(e).splitlines()[0][:160]
            print(f"  SKIP {name}: {msg}")
            self.skipped.append(f"{name}: {msg}")
            del self.subs[n_subs:]
            if self.subs and self.subs[-1][1] is None:
                self.subs[-1][1] = t_start
            self.cuts.append((t_start, self.now()))
            self.shot("FAIL_" + re.sub(r"\W+", "_", name)[:30])
            try:
                self.reset()
            except Exception:  # noqa: BLE001
                pass
            self.cuts[-1] = (t_start, self.now())

    def esc(self, n: int = 1):
        for _ in range(n):
            self.pg.keyboard.press("Escape")
            self.pg.wait_for_timeout(250)

    def reset(self):
        """Вернуться на обзор Земли без перезагрузки (перезагрузку — только как крайнюю меру)."""
        self.esc(2)
        for bk in ("studio-back", "drones-close", "prime-close", "dynamics-close", "fresh-panel-close", "field-close",
                   "sz-back", "scene-back"):
            if self.has(bk):
                try:
                    self.loc(bk).first.click(timeout=3000)
                    self.pg.wait_for_timeout(500)
                except Exception:  # noqa: BLE001
                    pass
        if self.has("act-earth"):
            self.loc("act-earth").first.click(timeout=3000)
            self.pg.wait_for_timeout(1200)
        try:
            if self.pg.eval_on_selector("[data-testid=f-source]", "e => e.selectedIndex") != 0:
                self.pg.locator("[data-testid=f-source]").select_option(index=0)
                self.pg.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001
            pass

    def region(self, text: str):
        sel = self.need("f-source")
        self.glide_to(sel, click=False)
        opt = self.pg.evaluate("(t) => [...document.querySelector('[data-testid=f-source]').options].find(o => o.text.includes(t))?.value", text)
        if opt is None:
            raise Skip("нет района " + text)
        sel.select_option(opt)
        self.wait(1.5)


def run(base: str, dry: bool, pace: float) -> dict:
    from playwright.sync_api import sync_playwright

    (WORK / "raw").mkdir(parents=True, exist_ok=True)
    if dry:
        shutil.rmtree(WORK / "dry", ignore_errors=True)
        (WORK / "dry").mkdir(parents=True)
    errs: list[str] = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        kw = dict(viewport={"width": W, "height": H}, locale="ru-RU", ignore_https_errors=True, accept_downloads=False)
        if not dry:
            kw.update(record_video_dir=str(WORK / "raw"), record_video_size={"width": W, "height": H})
        ctx = b.new_context(**kw)
        ctx.add_init_script(CURSOR_JS)
        pg = ctx.new_page()
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        c = Cast(pg, dry, pace)

        # 0. титул
        pg.set_content(CARD.replace("@T@", "Плавающий мусор: снимки и поле")
                       .replace("@S@", "Демонстрация сервиса · 14 шагов")
                       .replace("@M@", "Sentinel-2 · полевые измерения · дроны и фото · 1920×1080, запись с работающего сайта"))
        c.say("Сервис: где на снимках Sentinel-2 плавающий материал — и что об этом говорят поле, дроны и фото", 3.5)
        pg.goto(base + "/", wait_until="networkidle", timeout=120000)
        pg.wait_for_selector("[data-testid=scene-item]", timeout=90000)
        pg.evaluate("() => { try { localStorage.removeItem('mp.case.filtersOpen') } catch (e) {} }")
        pg.wait_for_timeout(2500)

        def s1():
            c.say("1. Земля: точки — снимки и находки. Четыре независимых источника включаются отдельно", 2.5)
            c.shot("earth")
            for tid in ("src-s2-hist", "src-s2-fresh", "src-field", "src-drones"):
                c.click(tid); c.wait(1.0)
            c.say("Архив Sentinel-2 · свежие Sentinel-2 (авто) · полевые CSV · дроны и фото. Цвет — источник, не доказательство", 2.5)
            c.shot("sources_toggled")
            for tid in ("src-field", "src-drones", "src-s2-fresh", "src-s2-hist"):
                c.click(tid); c.wait(0.6)
            c.wait(1.5)
        c.step("1 Земля и источники", s1)

        def s2():
            c.region(SCENE_TEXT)
            c.say("2. Район и даты: выбираем Альборан — список снимков сужается до района", 3.0)
            c.glide_to(c.need("f-from"), click=False)
            c.say("Даты снимка задают окно поиска; здесь один снимок — 11.03.2021", 2.5)
            c.shot("region")
        c.step("2 Район и даты", s2)

        def s3():
            c.click("scene-item", SCENE_TEXT)
            c.need("sz-item", 40000)
            c.wait(1.5)
            c.say("3. Снимок Sentinel-2 с нумерованными зонами-находками детектора", 3.0)
            c.shot("scene")
        c.step("3 Снимок с зонами", s3)

        def s4():
            it = c.need("sz-item")
            c.glide_to(it)
            c.need("scene-zone-card", 30000)
            c.wait(1.5)
            c.say("4. Зона → карточка: что это, статус и «Оценка количества»", 3.0)
            c.shot("card")
            q = c.loc("sz-quantity")
            if q.count():
                c.glide_to(q.first, click=False)
                c.say("По снимку число предметов не определяется: зона — это площадь, а не число штук", 3.0)
                c.say("Количество — только из поля (независимая оценка) или по фото", 2.5)
                c.shot("quantity")
        c.step("4 Зона и карточка", s4)

        def s5():
            tab = c.loc("card-tab-quality")
            if tab.count():
                c.glide_to(tab.first)
            else:
                c.click("act-qc")
            c.wait(1.2)
            c.say("5. Качество: по каким признакам решение и насколько ему верить", 3.0)
            c.shot("quality")
        c.step("5 Качество", s5)

        def s6():
            main = c.loc("card-tab-main")
            if main.count():
                c.glide_to(main.first); c.wait(0.6)
            c.click("sz-studio")
            c.need("studio-card", 30000)
            c.wait(1.5)
            c.say("6. Студия: снимок зоны крупно — цветной кадр и маска детектора", 3.0)
            c.shot("studio")
            c.click("studio-back")
            c.wait(1.0)
            if not (c.has("scene-zone-card") or c.has("sz-item")):
                raise Skip("после «Назад» нет карточки/зон")
            c.say("Назад — к той же карточке", 2.0)
        c.step("6 Студия и назад", s6)

        def s7():
            c.click("act-export")
            c.need("export-menu", 15000)
            c.wait(0.8)
            c.say("7. Выгрузка: CSV и GeoJSON — зоны, снимки, поле; открываются в QGIS и Excel", 3.0)
            c.shot("export")
            c.esc(); pg.mouse.click(W - 300, 600); c.wait(0.5)
            c.reset()
        c.step("7 Выгрузка CSV/GeoJSON", s7)

        def s8():
            c.click("realtime-folder")
            c.wait(1.5)
            c.say("8. Реальное время: свежие снимки Sentinel-2 сервис сам скачал и обработал нашей моделью", 3.0)
            c.shot("realtime")
            fun = c.loc("realtime-funnel")
            if fun.count():
                c.glide_to(fun.first, click=False)
                c.say("Воронка: найдено снимков → обработано → с находками. Находки не проверены человеком", 3.0)
            z = c.loc("realtime-zone")
            if z.count():
                c.glide_to(z.first); c.wait(2.0)
                c.say("Свежая находка на карте — кандидат для проверки, а не подтверждённый мусор", 3.0)
                c.shot("realtime_zone")
            c.reset()
        c.step("8 Реальное время (свежий S2)", s8)

        def s9():
            if c.has("drones-open"):
                c.click("drones-open")
            else:
                c.click("act-more"); c.wait(0.5); c.click("more-drones")
            c.need("drones-panel", 20000)
            c.wait(1.0)
            c.say("9. Дроны и фото: отдельный источник — снимки с высоты десятков метров, не спутник", 3.0)
            cards = c.loc("drones-setcard")
            if cards.count():
                c.glide_to(cards.first); c.wait(1.5)
            th = c.need("drones-thumb", 20000)
            c.glide_to(th)
            c.need("drones-frame", 20000)
            c.wait(1.5)
            c.say("Кадр с рамками: наш счётчик по фото против разметки набора", 3.0)
            c.shot("drone_frame")
            c.say("Итог по всем кадрам: совпало 274 из 444 рамок разметки, ложных 257 (IoU ≥ 0,5). Дрон ≠ спутник", 3.5)
            c.reset()
        c.step("9 Дроны и фото", s9)

        def s10():
            c.region(DRIFT_TEXT)
            c.click("scene-item", DRIFT_TEXT)
            c.need("sz-item", 40000); c.wait(1.0)
            c.glide_to(c.need("sz-item"))
            c.need("scene-zone-card", 30000); c.wait(1.0)
            btn = c.loc("sz-drift-top")
            if not btn.count():
                btn = c.loc("drift-map-btn")
            if not btn.count():
                btn = c.loc("sz-drift-btn")
            if not btn.count():
                raise Skip("нет кнопки дрейфа")
            c.glide_to(btn.first)
            c.wait(2.0)
            c.say("10. Дрейф ▶: куда могло отнести пятно от даты снимка — модельный прогноз по ветру и течению", 3.0)
            c.wait(2.5)
            c.shot("drift")
            c.say("Это оценка коридора, а не наблюдение", 2.5)
        c.step("10 Дрейф", s10)

        def s11():
            for bk in ("card-close", "sz-back"):
                if c.has(bk) and not c.has("alert-filter"):
                    c.click(bk); c.wait(1.0)
            af = c.loc("alert-filter")
            if af.count():
                c.glide_to(af.first, click=False)
                c.say("11. Алерты: крупные скопления и близость к берегу — фильтр зон снимка", 3.0)
                for tid in ("alert-f-large", "alert-f-shore", "alert-f-all"):
                    if c.has(tid):
                        c.click(tid); c.wait(1.2)
                c.shot("alerts")
            c.reset()
            c.region(DRIFT_TEXT)
            c.click("dynamics-open")
            c.need("dynamics-panel", 20000)
            try:
                pg.wait_for_selector("[data-testid=dynamics-table]:visible, [data-testid=dynamics-row]:visible", timeout=40000)
            except Exception as e:  # noqa: BLE001
                raise Skip("«Динамика» не загрузилась") from e
            c.wait(1.0)
            c.say(("«Динамика»" if af.count() else "11. «Динамика»") + ": находки района по датам снимков — рост и спад во времени", 3.0)
            c.shot("dynamics")
            c.say("Сравниваем площади зон по датам, не число предметов", 2.5)
            c.reset()
        c.step("11 Алерты и динамика", s11)

        def s12():
            c.click("nasa-toggle")
            c.wait(3.0)
            c.say("12. NASA: ежедневный обзорный слой (MODIS/VIIRS) — для контекста погоды и облаков", 3.0)
            c.shot("nasa")
            c.say("Это обзор, не детекция: на нём мусор не ищется", 2.5)
            if c.has("nasa-off"):
                c.click("nasa-off")
            else:
                c.click("nasa-toggle")
            c.wait(1.0)
        c.step("12 NASA обзор", s12)

        def s13():
            if c.has("prime-toggle-inline"):
                c.click("prime-toggle-inline")
            else:
                c.click("prime-toggle")
            c.need("prime-panel", 20000); c.wait(2.0)
            c.say("13. PRIME MODE — по просьбе жюри: демонстрационные данные, плашка «ДЕМО» всегда видна", 3.0)
            c.shot("prime")
            it = c.loc("prime-csv-item")
            if it.count():
                c.glide_to(it.first); c.wait(2.0)
            c.say("Демо-режим показывает формат результата, это не результаты по реальным снимкам", 3.0)
            c.shot("prime_item")
            if c.has("prime-close"):
                c.click("prime-close")
            elif c.has("prime-toggle-inline"):
                c.click("prime-toggle-inline")
            c.wait(1.0)
        c.step("13 PRIME демо", s13)

        def s14():
            c.click("src-field"); c.wait(0.6)
            for tid in ("src-s2-hist", "src-s2-fresh"):
                c.click(tid); c.wait(0.6)
            c.wait(2.0)
            c.say("14. Полевые измерения (CSV организаторов): только точки поля — независимая проверка на воде", 3.0)
            c.shot("field")
            c.say("Число предметов на км² считают люди в поле — это и есть оценка количества", 3.0)
            for tid in ("src-s2-hist", "src-s2-fresh", "src-field"):
                c.click(tid); c.wait(0.5)
            c.reset()
        c.step("14 Полевые измерения", s14)


        c.say("Итог: зона — площадь, не штуки · дрон ≠ спутник · NASA — обзор · PRIME — демо", 3.5)
        c.hush()
        c.shot("end")
        video = pg.video
        total = c.now()
        ctx.close(); b.close()
        raw = Path(video.path()) if video else None
    return dict(raw=raw, subs=c.subs, cuts=c.cuts, done=c.done, skipped=c.skipped, total=total, errs=errs)


def keep_ranges(total: float, cuts: list[tuple]) -> list[tuple]:
    out, t = [], 0.0
    for a, b in sorted(cuts):
        if a > t:
            out.append((t, a))
        t = max(t, b)
    if t < total:
        out.append((t, total))
    return out


def remap(t: float, keep: list[tuple]) -> float | None:
    acc = 0.0
    for a, b in keep:
        if a <= t <= b:
            return acc + t - a
        acc += b - a
    return None


def srt_ts(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_srt(subs, keep, path: Path) -> int:
    lines, n = [], 0
    for s, e, text in subs:
        a, b = remap(s, keep), remap(e if e is not None else s + 3, keep)
        if a is None or b is None or b - a < 0.4:
            continue
        n += 1
        lines += [str(n), f"{srt_ts(a)} --> {srt_ts(b)}", text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return n


def encode(raw: Path, srt: Path, keep: list[tuple], mp4: Path, crf: int) -> None:
    sel = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keep)
    style = ("FontName=Segoe UI,FontSize=13,PrimaryColour=&H00FFFFFF,BackColour=&H99000000,OutlineColour=&H99000000,"
             "BorderStyle=3,Outline=6,Shadow=0,Bold=1,Alignment=2,MarginV=26,MarginL=60,MarginR=60,WrapStyle=0")
    shutil.copy(srt, WORK / "subs.srt")
    vf = f"select='{sel}',setpts=N/FRAME_RATE/TB,fps=25,subtitles=subs.srt:force_style='{style}'"
    subprocess.run([str(FFMPEG), "-y", "-i", str(raw), "-vf", vf, "-c:v", "libx264", "-crf", str(crf), "-preset", "veryfast",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(mp4)], check=True, cwd=str(WORK),
                   capture_output=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8070")
    ap.add_argument("--dry", action="store_true", help="без записи: только проверка шагов и скрины в out/cast160/dry")
    ap.add_argument("--pace", type=float, default=1.0)
    ap.add_argument("--crf", type=int, default=28)
    ap.add_argument("--keep-work", action="store_true")
    a = ap.parse_args(argv)
    r = run(a.base.rstrip("/"), a.dry, a.pace)
    print(f"шагов {len(r['done'])}/14, длительность сырой записи {r['total']:.0f} с")
    for s in r["skipped"]:
        print("  пропущен:", s)
    print("ошибок консоли:", len(r["errs"]))
    for e in r["errs"][:8]:
        print("   ", e[:180])
    if a.dry:
        return 0
    keep = keep_ranges(r["total"], r["cuts"])
    srt = HERE / "screencast_final.srt"
    mp4 = HERE / "screencast_final.mp4"
    n = write_srt(r["subs"], keep, srt)
    encode(r["raw"], srt, keep, mp4, a.crf)
    dur = sum(b - a for a, b in keep)
    meta = dict(duration_s=round(dur, 1), size_mb=round(mp4.stat().st_size / 1e6, 1), subs=n, done=r["done"],
                skipped=r["skipped"], base=a.base)
    (WORK / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
    if not a.keep_work:
        shutil.rmtree(WORK / "raw", ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
