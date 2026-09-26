r"""L161 §65 п.Б — набор скринов сервиса с подписями-субтитрами: presentation/screens_final/.

Стиль — как в C:\Users\User\hack\SAMARKAND_artifacts\screenshots (только чтение, образец формата,
не числа): 14 шагов сценария (§65 п.А), честные подписи снизу (зона ≠ число штук; дрон ≠ спутник;
NASA — обзор; PRIME — демо), пара версий каждого кадра — с подписью (NN_имя.png) и без (NN_имя_clean.png),
плюс 2-3 мобильных кадра (390x844). Сайт не перезапускается, только чтение (сохранённые запросы за собой
удалить). PNG сжимаются pillow до <= 600 КБ.

СНИМАТЬ только после строк в docs/LOG.md «ЗАМОРОЗКА main <sha>» и «:8070 перезапущен на <sha>».

    .venv\Scripts\python.exe presentation\make_screens_final.py [--base https://5-231-59-204.sslip.io:8443] [--out presentation/screens_final]
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
MW, MH = 390, 844
FONTS = Path("C:/Windows/Fonts")
try:
    CAP_FONT = ImageFont.truetype(str(FONTS / "segoeui.ttf"), 22)
    CAP_FONT_M = ImageFont.truetype(str(FONTS / "segoeui.ttf"), 16)
except Exception:  # noqa: BLE001
    CAP_FONT = CAP_FONT_M = ImageFont.load_default()

DEMO_ZONE = "SZ-demo-cozar-2021-03-11-016"
SCENE_TEXT = "Альборан"
DRIFT_SCENE = "live-guanabara-2025-09-04"


def caption_band(png_bytes: bytes, text: str, mobile: bool = False) -> bytes:
    """Полупрозрачная подложка-субтитр внизу кадра (без изменения размера кадра)."""
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    w, h = im.size
    font = CAP_FONT_M if mobile else CAP_FONT
    band_h = 46 if mobile else 64
    overlay = Image.new("RGBA", (w, band_h), (12, 14, 18, 210))
    d = ImageDraw.Draw(overlay)
    d.rectangle([0, 0, w, 3], fill=(255, 140, 60, 255))
    pad = 14 if mobile else 24
    d.text((pad, band_h // 2), text, font=font, fill=(255, 255, 255, 255), anchor="lm")
    im.paste(overlay, (0, h - band_h), overlay)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def compress_to_limit(png_bytes: bytes, limit_kb: int = 600) -> bytes:
    if len(png_bytes) <= limit_kb * 1024:
        return png_bytes
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    for quality_scale in (0.9, 0.8, 0.7, 0.6, 0.5):
        w, h = im.size
        resized = im.resize((int(w * quality_scale), int(h * quality_scale)), Image.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format="PNG", optimize=True)
        if len(buf.getvalue()) <= limit_kb * 1024:
            return buf.getvalue()
    return buf.getvalue()  # лучшее, что получилось


# 14 шагов §65 п.А: (номер, имя_файла, подпись-субтитр, источник данных для README)
STEPS = [
    ("01", "earth_sources", "Земля и источники: 4 независимых переключателя данных (архив S2 / свежие S2 / поле / дроны)", "живой сервис :8070, стартовый список снимков"),
    ("02", "region_dates", "Выбор района и дат на карте и таймлайне", "живой сервис, таймлайн снимков"),
    ("03", "scene_zones", "Снимок Sentinel-2 с нумерованными зонами по кадру", "снимок Sentinel-2, зоны по модели"),
    ("04", "zone_card", "Карточка зоны: статус, «оценка количества» — по снимку не определено / по полю — независимая оценка / по фото", "карточка зоны (снимок + при наличии полевые/фото данные)"),
    ("05", "quality", "Проверка качества: примеры верных срабатываний, пропусков и ложных на сложном фоне", "метрики модели на разметке"),
    ("06", "studio", "Студия — детальный просмотр снимка зоны", "снимок Sentinel-2"),
    ("07", "export", "Выгрузка результатов в CSV / GeoJSON", "выгрузка по открытой зоне"),
    ("08", "realtime", "Реальное время: свежий Sentinel-2, обработан моделью автоматически — «не проверено человеком»", "воронка обработки свежих сцен (API /api/v3/fresh_s2)"),
    ("09", "drones", "Дроны и фото: кадр с рамками — наш счётчик отдельно от разметки (не одно и то же число)", "набор фото/видео с дрона, разметка + предсказание модели"),
    ("10", "drift", "Дрейф ▶ — модельный сценарий на дату снимка, не наблюдаемое перемещение", "прогноз дрейфа (модель течений), эксперимент"),
    ("11", "alerts", "Алерты, крупные скопления и «Динамика» по времени", "правило алертов (docs/ALERTS.md) + разметка/модель по времени"),
    ("12", "nasa", "NASA — ежедневный обзор снимков Земли (не детекция мусора)", "NASA GIBS (VIIRS/MODIS) через прокси сервиса"),
    ("13", "prime", "PRIME — демонстрационный режим по просьбе жюри (плашка «ДЕМО» видна)", "синтетические демо-данные, не реальные измерения"),
    ("14", "field", "Полевые измерения — данные организаторов (CSV, судно)", "полевые измерения организаторов"),
]

MOBILE_STEPS = [
    ("m1", "earth_sources_mobile", "Мобильный вид: Земля и источники"),
    ("m2", "zone_card_mobile", "Мобильный вид: карточка зоны"),
    ("m3", "drones_mobile", "Мобильный вид: дроны и фото"),
]


def home(pg, base):
    pg.goto(base + "/", wait_until="networkidle", timeout=90000)
    pg.wait_for_selector("[data-testid=scene-item]", timeout=60000)
    pg.wait_for_timeout(2500)


def has(pg, tid):
    return pg.locator(f"[data-testid={tid}]").count() > 0


def open_more(pg, item_tid):
    """Открывает меню «Ещё ▾», если элемент item_tid внутри него ещё не виден."""
    if pg.locator(f"[data-testid={item_tid}]:visible").count():
        return True
    if has(pg, "act-more"):
        pg.click("[data-testid=act-more]")
        pg.wait_for_timeout(400)
    return pg.locator(f"[data-testid={item_tid}]:visible").count() > 0


def raw_shot(pg) -> bytes:
    return pg.screenshot(type="png")


def run(a) -> int:
    from playwright.sync_api import sync_playwright

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    made: list[tuple[str, str, str]] = []  # (filename, caption, source)
    skipped: list[str] = []
    errs: list[str] = []

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": W, "height": H})
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

        def save(num, slug, caption, source):
            raw = raw_shot(pg)
            clean = compress_to_limit(raw)
            withcap = compress_to_limit(caption_band(raw, caption))
            (out / f"{num}_{slug}_clean.png").write_bytes(clean)
            (out / f"{num}_{slug}.png").write_bytes(withcap)
            made.append((f"{num}_{slug}.png", caption, source))

        def step(num, slug, caption, source, fn):
            try:
                fn()
                save(num, slug, caption, source)
            except Exception as e:  # noqa: BLE001
                skipped.append(f"{num}_{slug}: {e}"[:200])

        # 1) Земля и источники
        def s01():
            home(pg, a.base)
        step("01", "earth_sources", STEPS[0][2], STEPS[0][3], s01)

        # 2) район и даты (тот же экран, таймлайн снимков)
        def s02():
            home(pg, a.base)
            pg.wait_for_timeout(1500)
        step("02", "region_dates", STEPS[1][2], STEPS[1][3], s02)

        # 3) снимок Sentinel-2 с зонами
        def s03():
            home(pg, a.base)
            sc = pg.locator("[data-testid=scene-item]", has_text=SCENE_TEXT)
            (sc.first if sc.count() else pg.locator("[data-testid=scene-item]").first).click()
            pg.wait_for_selector("[data-testid=sz-item]", timeout=30000)
            pg.wait_for_timeout(4000)
        step("03", "scene_zones", STEPS[2][2], STEPS[2][3], s03)

        # 4) карточка зоны
        def s04():
            ok = pg.evaluate(
                f"() => !!(window.__app && window.__app.selectZone && (window.__app.selectZone('{DEMO_ZONE}'), true))"
            )
            if not ok:
                pg.locator("[data-testid=sz-item]").first.click()
            pg.wait_for_selector("[data-testid=scene-zone-card]", timeout=30000)
            pg.wait_for_timeout(4000)
        step("04", "zone_card", STEPS[3][2], STEPS[3][3], s04)

        # 5) качество
        def s05():
            if not has(pg, "act-qc"):
                raise RuntimeError("нет кнопки «Проверка качества»")
            pg.click("[data-testid=act-qc]")
            pg.wait_for_timeout(3000)
        step("05", "quality", STEPS[4][2], STEPS[4][3], s05)

        def s05_close():
            if has(pg, "qc-close"):
                pg.click("[data-testid=qc-close]")
            else:
                pg.keyboard.press("Escape")
            pg.wait_for_timeout(800)
        try:
            s05_close()
        except Exception:  # noqa: BLE001
            pass

        # 6) студия
        def s06():
            if not has(pg, "sz-studio"):
                raise RuntimeError("нет кнопки «В студию»")
            pg.click("[data-testid=sz-studio]")
            pg.wait_for_timeout(4000)
        step("06", "studio", STEPS[5][2], STEPS[5][3], s06)
        try:
            if has(pg, "studio-back"):
                pg.click("[data-testid=studio-back]")
                pg.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001
            pass

        # 7) выгрузка
        def s07():
            if not has(pg, "act-export"):
                raise RuntimeError("нет кнопки выгрузки")
            pg.click("[data-testid=act-export]")
            pg.wait_for_timeout(1200)
        step("07", "export", STEPS[6][2], STEPS[6][3], s07)
        try:
            pg.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            pass

        # 8) реальное время
        def s08():
            home(pg, a.base)
            rt = pg.get_by_text("Реальное время")
            if not rt.count():
                raise RuntimeError("нет папки «Реальное время»")
            rt.first.scroll_into_view_if_needed(timeout=8000)
            pg.wait_for_timeout(1500)
        step("08", "realtime", STEPS[7][2], STEPS[7][3], s08)

        # 9) дроны и фото
        def s09():
            home(pg, a.base)
            if has(pg, "drones-open"):
                pg.locator("[data-testid=drones-open]").first.click()
            elif open_more(pg, "more-drones"):
                pg.click("[data-testid=more-drones]")
            else:
                raise RuntimeError("нет входа в «Дроны и фото»")
            pg.wait_for_selector("[data-testid=drones-panel]", timeout=20000)
            pg.wait_for_timeout(1500)
            if has(pg, "drones-setcard"):
                cards = pg.locator("[data-testid=drones-setcard]")
                tun = pg.locator("[data-testid=drones-setcard]", has_text="TUN")
                (tun.first if tun.count() else cards.first).click()
                pg.wait_for_timeout(2000)
            if has(pg, "drones-thumb"):
                pg.locator("[data-testid=drones-thumb]").first.click()
                pg.wait_for_timeout(4000)
        step("09", "drones", STEPS[8][2], STEPS[8][3], s09)

        # 10) дрейф
        def s10():
            pg.goto(a.base + f"/?scene={a.drift_scene}", wait_until="networkidle", timeout=90000)
            pg.wait_for_selector("[data-testid=sz-item]", timeout=60000)
            pg.locator("[data-testid=sz-item]").first.click()
            pg.wait_for_selector("[data-testid=scene-zone-card]", timeout=30000)
            pg.wait_for_timeout(2500)
            if has(pg, "sz-drift-btn"):
                pg.locator("[data-testid=sz-drift-btn]").first.click()
                pg.wait_for_timeout(4000)
            elif has(pg, "drift-map-btn"):
                pg.locator("[data-testid=drift-map-btn]").first.click()
                pg.wait_for_timeout(6000)
            else:
                raise RuntimeError("нет кнопки дрейфа у первой зоны")
        step("10", "drift", STEPS[9][2], STEPS[9][3], s10)

        # 11) алерты / крупные скопления / динамика
        def s11():
            home(pg, a.base)
            dyn = pg.locator("[data-testid=dynamics-open]")
            if dyn.count() and dyn.first.is_enabled():
                dyn.first.click()
                pg.wait_for_timeout(3000)
            elif has(pg, "alert-card"):
                pg.locator("[data-testid=alert-card]").first.scroll_into_view_if_needed(timeout=8000)
                pg.wait_for_timeout(1500)
            else:
                raise RuntimeError("нет ни «Динамики», ни карточки алерта")
        step("11", "alerts", STEPS[10][2], STEPS[10][3], s11)

        # 12) NASA
        def s12():
            home(pg, a.base)
            if not has(pg, "nasa-toggle"):
                raise RuntimeError("нет кнопки NASA")
            pg.locator("[data-testid=nasa-toggle]").first.click()
            pg.wait_for_timeout(8000)
        step("12", "nasa", STEPS[11][2], STEPS[11][3], s12)
        try:
            if has(pg, "nasa-toggle"):
                pg.locator("[data-testid=nasa-toggle]").first.click()
        except Exception:  # noqa: BLE001
            pass

        # 13) PRIME
        def s13():
            home(pg, a.base)
            tg = pg.locator("[data-testid=prime-toggle-inline], [data-testid=prime-toggle]")
            if tg.count():
                tg.first.click()
            elif open_more(pg, "more-prime"):
                pg.click("[data-testid=more-prime]")
            else:
                raise RuntimeError("нет тумблера PRIME")
            pg.wait_for_selector("[data-testid=prime-panel]", timeout=20000)
            pg.wait_for_timeout(1500)
        step("13", "prime", STEPS[12][2], STEPS[12][3], s13)

        # 14) полевые измерения
        def s14():
            home(pg, a.base)
            if open_more(pg, "act-field"):
                pg.click("[data-testid=act-field]")
            elif has(pg, "src-field"):
                pg.click("[data-testid=src-field]")
            else:
                raise RuntimeError("нет включения полевых измерений")
            pg.wait_for_timeout(3000)
        step("14", "field", STEPS[13][2], STEPS[13][3], s14)

        # мобильный вид
        pgm = b.new_page(viewport={"width": MW, "height": MH})
        pgm.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

        def save_m(num, slug, caption, source):
            raw = pgm.screenshot(type="png")
            clean = compress_to_limit(raw)
            withcap = compress_to_limit(caption_band(raw, caption, mobile=True))
            (out / f"{num}_{slug}_clean.png").write_bytes(clean)
            (out / f"{num}_{slug}.png").write_bytes(withcap)
            made.append((f"{num}_{slug}.png", caption, source))

        try:
            home(pgm, a.base)
            save_m(MOBILE_STEPS[0][0], MOBILE_STEPS[0][1], MOBILE_STEPS[0][2], "живой сервис, мобильный вид")
        except Exception as e:  # noqa: BLE001
            skipped.append(f"{MOBILE_STEPS[0][0]}: {e}"[:200])
        try:
            home(pgm, a.base)
            item = pgm.locator("[data-testid=scene-item]").first
            item.scroll_into_view_if_needed(timeout=8000)
            item.click(force=True, timeout=15000)
            pgm.wait_for_selector("[data-testid=sz-item]", timeout=30000)
            pgm.wait_for_timeout(2000)
            zi = pgm.locator("[data-testid=sz-item]").first
            zi.scroll_into_view_if_needed(timeout=8000)
            zi.click(force=True, timeout=15000)
            pgm.wait_for_selector("[data-testid=scene-zone-card]", timeout=30000)
            pgm.wait_for_timeout(2500)
            save_m(MOBILE_STEPS[1][0], MOBILE_STEPS[1][1], MOBILE_STEPS[1][2], "снимок + карточка зоны, мобильный вид")
        except Exception as e:  # noqa: BLE001
            skipped.append(f"{MOBILE_STEPS[1][0]}: {e}"[:200])
        try:
            home(pgm, a.base)
            if pgm.locator("[data-testid=act-more]").count():
                pgm.click("[data-testid=act-more]")
                pgm.wait_for_timeout(400)
            if pgm.locator("[data-testid=drones-open]").count():
                pgm.locator("[data-testid=drones-open]").first.click()
            elif pgm.locator("[data-testid=more-drones]").count():
                pgm.click("[data-testid=more-drones]")
            else:
                raise RuntimeError("нет входа в дронов (моб.)")
            pgm.wait_for_selector("[data-testid=drones-panel]", timeout=20000)
            pgm.wait_for_timeout(1500)
            save_m(MOBILE_STEPS[2][0], MOBILE_STEPS[2][1], MOBILE_STEPS[2][2], "набор дронов, мобильный вид")
        except Exception as e:  # noqa: BLE001
            skipped.append(f"{MOBILE_STEPS[2][0]}: {e}"[:200])

        pgm.close()
        b.close()

    write_readme(out, made)
    print(f"скринов сделано: {len(made)}, пропущено: {len(skipped)}")
    for s in skipped:
        print("  пропуск:", s)
    print("ошибок консоли:", len(errs))
    return 0


def write_readme(out: Path, made: list[tuple[str, str, str]]) -> None:
    lines = [
        "# presentation/screens_final — набор скринов сервиса (L161, §65 п.Б)",
        "",
        "14 шагов сценария §65 п.А + мобильный вид. У каждого кадра есть версия с подписью-субтитром внизу",
        "(`NN_имя.png`) и без подписи (`NN_имя_clean.png`, для деки/README). Подписи честные: зона — это",
        "не число штук мусора, дрон — не спутник, NASA — ежедневный обзор Земли (не детекция), PRIME — демо-режим.",
        "",
        "| Файл | Что показано | Источник данных |",
        "|---|---|---|",
    ]
    for fname, caption, source in made:
        lines.append(f"| `{fname}` (+ `_clean`) | {caption} | {source} |")
    lines.append("")
    lines.append("Снято Playwright (Chromium) с живого сервиса, только чтение; сайт не перезапускался.")
    (out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8070")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "screens_final"))
    ap.add_argument("--drift-scene", dest="drift_scene", default=DRIFT_SCENE)
    a = ap.parse_args(argv)
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
