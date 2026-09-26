"""«Целевая совокупность = материал + размерный класс + единица» for every number we publish (INBOX §30 п.4,
postanovka p.2: «Отдельный предмет, категория, общий мусор и суммарный пластик относятся к разным целевым величинам»).

Only definitions live here; the numbers themselves come from reports/final_numbers.json (column `numbers_key`).
    python -m macroplastic.labels.targets      # print the markdown table (for docs/LABELS.md, README, QUANTITY)
"""
from __future__ import annotations

import sys

TARGETS = [
    {"id": "field_S2", "what": "Поле CSV, профиль S2 (основной)",
     "scope": "суммарный пластик (total_plastic)", "material": "пластик, все полимеры",
     "size": "> 2 см, визуально с судна (полоса)", "unit": "шт./км², C = ΣN / ΣA",
     "numbers_key": "case.sections.quantity.field_S2 / profiles.S2",
     "not_this": "не общий мусор, не отдельная категория, не масса"},
    {"id": "field_S1", "what": "Поле CSV, профиль S1 (GPGP)",
     "scope": "суммарный пластик (total_plastic)", "material": "пластик",
     "size": "5–50 см, трал", "unit": "шт./км² (N в источнике нет — медиана событий)",
     "numbers_key": "case.sections.quantity.profiles.S1",
     "not_this": "с S2 не смешивается (другой метод и размерный класс)"},
    {"id": "field_S3_S4", "what": "Поле CSV, S3 (Сев. море) и S4 (Чёрное море) — только справка",
     "scope": "общий мусор (all_litter)", "material": "все материалы — НЕ пластик",
     "size": "> 2 см (S3), > 2,5 см (S4), визуально", "unit": "шт./км²",
     "numbers_key": "case.sections.quantity.profiles.S3 / S4",
     "not_this": "исключены из пластиковых профилей (configs/case_selection.yaml)"},
    {"id": "adis", "what": "ADIS (камера судна, The Ocean Cleanup) — полевой прогноз",
     "scope": "все объекты детектора авторов в отрезке 10 км",
     "material": "по классам детектора авторов: hard plastic 79,1 %, fibrous 12,5 %, buoy 1,3 % + animal 6,3 % и plant 0,8 % (объекты в отрезках, n = 22 184)",
     "size": "> 10 см (основной), > 50 см (чувствительность)", "unit": "шт./км², ΣN / ΣA по отрезкам",
     "numbers_key": "case.sections.quantity.adis_forecast",
     "not_this": "не чистый пластик: n_objects отрезка = все объекты, включая классы animal и plant (98,5 % отрезков)"},
    {"id": "photo_water", "what": "Счётчик по фото — камера у воды (FML)",
     "scope": "отдельные предметы одного класса «мусор» (garbage)", "material": "не определён (состав не размечен)",
     "size": "видимые на кадре 1920×1080; масштаба нет", "unit": "шт. на кадр; шт./км² — только при известной площади воды в кадре",
     "numbers_key": "weights_exp/photo_count/model_card.json test_grouped (docs/PHOTO_COUNT.md)",
     "not_this": "не шт./км² моря; не пластик отдельно"},
    {"id": "photo_aerial", "what": "Счётчик по фото — аэро/дрон, берег (Winans 2023)",
     "scope": "отдельные предметы, все 8 классов в одном «предмет»",
     "material": "смесь: пластик по форме (буи, сети, лини) 36 %, неопознанное 50 %, дерево 7 %, шины 4 %, металл 2,5 % (доли рамок набора)",
     "size": "рамки ≳ 40 см по большей стороне (1 % < 36 см), GSD 2 см", "unit": "шт. на 164 м² кадра берега (чип 12,8 × 12,8 м: песок + вода + растительность, не площадь пляжа)",
     "numbers_key": "weights_exp/photo_count/model_card_aerial.json test_spatial",
     "not_this": "не плавающий мусор (берег); не пластик; не шт./км² моря"},
    {"id": "scene_zones", "what": "Спутниковые зоны (Sentinel-2, наш детектор)",
     "scope": "скопление «подозрительного плавающего материала» (класс MARIDA Marine Debris против фона)",
     "material": "не определяется (пена, водоросли, суда исключены не полностью)",
     "size": "скопление, покрытие ≳ 25–30 % пикселя 10 м", "unit": "м² маски на км² пригодной воды (доля покрытия); шт./км² — не выдаём",
     "numbers_key": "case.sections.scene_zones",
     "not_this": "не штуки, не материал, не форма (никаких «бутылок» на S2); концентрация по снимку не подтверждена"},
]

COLS = [("what", "Что считаем"), ("scope", "Целевая совокупность"), ("material", "Материал"),
        ("size", "Размерный класс"), ("unit", "Единица"), ("not_this", "Чем НЕ является"), ("numbers_key", "Где число")]


def markdown() -> str:
    head = "| " + " | ".join(t for _, t in COLS) + " |\n|" + "---|" * len(COLS) + "\n"
    return head + "".join("| " + " | ".join(r[k] for k, _ in COLS) + " |\n" for r in TARGETS)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(markdown())
