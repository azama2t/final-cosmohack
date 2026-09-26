"""L111: раздел docs/DEMO.md «Демо на отложенной сцене» — числа только из data/case/scene_zones (scripts/case/scene_zones.py)
и reports/case_demo/demo_path.json (живой проход scripts/case/demo_path_v2.py). Вызывается из scripts/make_deck_case.py (demo_md)
и отдельно: .venv\\Scripts\\python.exe scripts\\case\\demo_sz_md.py --write (заменяет блок между маркерами в docs/DEMO.md).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SZ = ROOT / "data" / "case" / "scene_zones"
BEGIN, END = "<!-- L111:scene_zones:begin -->", "<!-- L111:scene_zones:end -->"


def _n(v, d=0):
    if v is None:
        return "—"
    s = f"{v:,.{d}f}".replace(",", " ").replace(".", ",")
    return s


def numbers() -> dict | None:
    idx_p = SZ / "index.json"
    if not idx_p.is_file():
        return None
    idx = json.loads(idx_p.read_text(encoding="utf-8"))
    zs = []
    for s in idx["scenes"]:
        p = SZ / s["key"] / "zones.geojson"
        if s.get("evaluable") and p.is_file():
            zs += [f["properties"] for f in json.loads(p.read_text(encoding="utf-8"))["features"]]
    demo = [z for z in zs if z["scene_kind"] == "demo"]
    b = [z for z in zs if z.get("verification") == "level_B_cozar"]
    b.sort(key=lambda z: (-z["n_cozar_filaments"], -z["measured"]["n_pixels"]))
    ex = b[0] if b else None
    by = {}
    for z in zs:
        k = "unverified" if (z["detection_status"] == "detected" and z.get("verification") != "level_B_cozar") else z["detection_status"]
        if z["detection_status"] == "detected" and z.get("verification") == "level_B_cozar":
            k = "level_B"
        by[k] = by.get(k, 0) + 1
    ds = next((s for s in idx["scenes"] if s["kind"] == "demo"), {})
    fn = (ex or {}).get("field_nearby", {}).get("items", [None])[0] or {}
    path = {}
    pp = ROOT / "reports" / "case_demo" / "demo_path.json"
    if pp.is_file():
        path = json.loads(pp.read_text(encoding="utf-8"))
    return {"n_all": len(zs), "n_scenes": sum(1 for s in idx["scenes"] if s.get("evaluable")),
            "n_scenes_total": len(idx["scenes"]), "by": by, "n_demo": len(demo),
            "n_demo_b": sum(1 for z in demo if z.get("verification") == "level_B_cozar"), "ex": ex, "demo_scene": ds,
            "fn": fn, "path": path}


def md() -> str:
    k = numbers()
    if not k or not k["ex"]:
        return f"{BEGIN}\n_Слой спутниковых зон не построен: `scripts/case/scene_zones.py`._\n{END}"
    ex, m, ds, fn, by = k["ex"], k["ex"]["measured"], k["demo_scene"], k["fn"], k["by"]
    try:  # покрытие пикселя сценария PLP — из configs/zone_estimate.yaml тем же кодом, что сервис
        sys.path.insert(0, str(ROOT / "src"))
        from macroplastic.case.zone_estimate import calibration, load_config
        cal = calibration(load_config())
        cov = f"{cal['coverage_pct_lo']}–{cal['coverage_pct_hi']} %"
    except Exception:  # noqa: BLE001
        cov = "по мишеням PLP"
    ex = {**ex, "title": ex["title"].replace("Демо Cózar 2024 (отложенная сцена)", "Cózar, отложенная сцена 30SXE")}  # = API title
    pr = ex["probable"]
    rr = ((k["path"].get("1920") or {}).get("rerun") or {})
    exp = ((k["path"].get("1920") or {}).get("export") or {})
    return f"""{BEGIN}
## Основной путь: отложенная сцена Cózar (спутниковые зоны, 1:40)

Сцена Sentinel-2 **30SXE, {ds.get('date', '—')}** не участвовала ни в обучении и подборе порога детектора (MARIDA, MADOS — по тайлу, дате и по
пикселям), ни в экспериментах L92 (`reports/case_demo/heldout_scene.md`). Слой — `scripts/case/scene_zones.py` (данные в git, `data/case/scene_zones/`).
Проверить до выхода: слева «{k['n_all']} спутн. зон», вкладка «Зоны» — первая строка «{ex['title']}». Прямая ссылка: `?sel=zone:{ex['zone_id']}`.

| Время | Действие | Что говорим | Что видно |
|---|---|---|---|
| 0:00–0:15 | Вкладка **«Зоны»** → первая строка «{ex['title']}» | «Сцена, которую модель не видела. Снимок, маска качества, контуры зон детектора — всё с этой сцены.» | полёт к сцене, снимок и маска качества под зонами, контуры зон |
| 0:15–0:40 | Карточка, блок **«Измерено по снимку»** | «Площадь зоны {_n(m['zone_area_km2'], 2)} км², подозрительные пиксели {_n(m['suspicious_area_m2'])} м², LWD {_n(m['lwd_m2_km2'])} м² на км² пригодной воды — как у Cózar 2024. Маска качества: вода {_n(m['quality']['valid_water_fraction'] * 100)} %. Модель weights/lgbm, порог 0,63, sha256 {m['model']['sha256_short']}.» | вырезка «снимок / пиксели детектора», таблица «Измерено» |
| 0:40–0:55 | Блок **«Вероятно»** | «Вероятность детектора {_n(pr['prob_mean'], 2)} / {_n(pr['prob_max'], 2)}. Признаков пены, блика, судна, берега нет. Контур пересекает {ex['n_cozar_filaments']} нит(и) каталога Cózar — их отметили люди по снимку. Поэтому статус «обнаружено», «Подтверждение: совпадает с разметкой Cózar 2024 (B)»; без такой разметки — «Подтверждение: независимой разметки нет». Что это: плавающий материал (класс MARIDA Marine Debris; пластик не подтверждён).» | статус, «Подтверждение», «Что это / Исключено / Не проверяется» |
| 0:55–1:10 | Блок **«Количество»** | «Количество предметов по этому снимку не определено: перенос «снимок → штуки» не подтверждён, природных пар нет (ISPRA 604 — ни одного посчитанного предмета ближе 50 м к пикселю детектора). Под раскрывашкой — исследовательский сценарий по мишеням PLP: сколько было бы бутылок 1,5 л при покрытии {cov}; на природе не проверено, на оценщике хуже ответа «0» (docs/QUANTITY.md).» | строка «Количество …», свёрнутый «▸ Исследовательский сценарий (мишени PLP)» |
| 1:10–1:20 | Блок **«Поле рядом»** | «Ближайшее полевое измерение — ADIS за {_n(fn.get('distance_km'))} км, {fn.get('date', '—')}: C = N/A = {_n(fn.get('c_items_km2'), 1)} [{_n(fn.get('ci95_lo'), 1)}–{_n(fn.get('ci95_hi'), 1)}] шт./км² (> 5 см). Измерение ≠ оценка: это другое время и место.» | таблица N / A / C |
| 1:20–1:30 | **Сложный случай:** «Как выглядит удача и ошибка» → «ложное срабатывание: судно / кильватер» | «Детектор отметил яркую точку со следом — это судно. Статус «ложное срабатывание … недостаточно данных» — это не верное срабатывание. Суда — известная слабость: 26 % судов как мусор.» | вырезка судна, статус |
| 1:30–1:40 | Даты 11.03.2021–11.03.2021 → **«Выгрузка»** → «Спутниковые зоны» CSV; **«Запросы»** → сохранить → «сбросить» → запустить | «Выгрузка — те же поля и статусы; сохранённый запрос восстанавливает вид.» | зон на карте {exp.get('ui_count', '—')} = строк CSV {exp.get('csv', '—')} = объектов GeoJSON {exp.get('geojson', '—')}; «{rr.get('toast', '—')}» |

Всего в слое {k['n_all']} зон на {k['n_scenes']} оцениваемых сценах из {k['n_scenes_total']} (на остальных низкое солнце или слабый сигнал воды — детектор не оценивается):
обнаружено, подтверждение «совпадает с разметкой Cózar 2024 (B)» — {by.get('level_B', 0)}, обнаружено, «независимой разметки нет» — {by.get('unverified', 0)},
недостаточно данных или ложное (судно/пена/блик/облака/берег/мелководье/ветер > 5 м/с по правилу Cózar 2024) — {by.get('insufficient_data', 0)}, не обнаружено — {by.get('not_detected', 0)}.
Живой проход без моков (1920×1080 и 1366×768): `scripts/case/demo_path_v2.py` → `reports/case_demo/demo_path.json`, кадры `reports/case_demo/*.png`.

## Путь данных: снимок → маски качества → детекция → зона
1. `scripts/case/demo_scene.py select` — отбор и проверка отложенности (тайл, дата; MARIDA, MADOS, L92, L98, PLP/FO, суда, пары, районы).
2. `scripts/case/demo_scene.py fetch --acq 30SXE_20210311` — L2A той же съёмки (Earth Search) → `data/live/cozar_demo/<дата>/` (формат районов сервиса).
3. `scripts/case/demo_scene.py detect` — маска качества и детектор, как у пар (`studio_detector_current.run_scene`: weights/lgbm, без гармонизации, порог 0,63).
4. `scripts/case/scene_zones.py [--only demo]` — зоны (кластеры объектов), признаки ложных, «измерено / вероятно / сценарий», вырезки.
5. Сервис: `/api/v3/scene_zones`, `/api/v3/scene_zones/{{id}}`, `/api/v3/export?layer=scene_zones`; карта v2 — слой «Спутниковые зоны».

## Эксперт повторяет расчёт без правки кода
- Другая сцена Cózar: взять строку из `reports/case_demo/heldout_candidates.csv` → `demo_scene.py fetch --acq <тайл_дата>` → `detect` → `scene_zones.py --only demo` → перезапустить сервис.
- Полоса пары кейса: `scripts/case/pair_quality.py --only S3:HE460_MarLitter_transect03 --force` → `/api/v3/zones/Z-S3_HE460_MarLitter_transect03`.
- Своя точка и дата: «Подобрать снимок» в карточке измерения (`scripts/case/pairfinder.py`), затем тот же путь.
- Любая зона открывается ссылкой `?sel=zone:<zone_id>`; числа — `curl http://127.0.0.1:8000/api/v3/scene_zones/<zone_id>`.
{END}"""


def write():
    p = ROOT / "docs" / "DEMO.md"
    s = p.read_text(encoding="utf-8")
    block = md()
    if BEGIN in s and END in s:
        a, rest = s.split(BEGIN, 1)
        _, b = rest.split(END, 1)
        s = a + block + b
    else:
        s = s.replace("\n## Сценарий (2:00)", "\n" + block + "\n\n## Запасной путь: поле и полосы пар (2:00)", 1)
    p.write_bytes(s.encode("utf-8"))


if __name__ == "__main__":
    if "--write" in sys.argv:
        write()
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(md())
