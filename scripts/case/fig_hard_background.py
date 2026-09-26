"""Рисунок для слайда «Удачные и ошибочные обнаружения на сложном фоне» (критерии О3, О5): docs/img/hard_background.jpg.

Панели берутся из уже сохранённых вырезок test MARIDA (reports/case_detector/examples/*.png, detector_compare.py),
подписи — из reports/case_detector/fp_by_class.csv (те же числа, что README §4). Ничего не пересчитывается.

    .venv\\Scripts\\python.exe scripts\\case\\fig_hard_background.py
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from PIL import Image  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EX = ROOT / "reports" / "case_detector" / "examples"
OUT = ROOT / "docs" / "img" / "hard_background.jpg"
# panel boxes of the example figures (detector_compare.py layout, 1760×429): RGB, MARIDA label, LightGBM, RandomForest
BOX = {"RGB (B4, B3, B2)": (42, 129, 323, 411), "Разметка MARIDA": (390, 129, 672, 411),
       "LightGBM (основной)": (739, 129, 1021, 411), "RandomForest (бейзлайн)": (1088, 129, 1370, 411)}


def main():
    fp = pd.read_csv(ROOT / "reports" / "case_detector" / "fp_by_class.csv").set_index("class")

    def r(c):
        return int(fp.loc[c, "lgbm_pred_md"]), int(fp.loc[c, "n_px_test"])
    md, sa, sd, fo, sh, org = (r(c) for c in ("Marine Debris", "Sparse Sargassum", "Dense Sargassum", "Foam", "Ship",
                                              "Natural Organic Material"))
    sarg_box = int(fp.loc["Sparse Sargassum", "fdi_ndvi_box_pred_md"] + fp.loc["Dense Sargassum", "fdi_ndvi_box_pred_md"])
    n_sarg = sa[1] + sd[1]
    rows = [
        ("01_success_1.png", f"Мусор найден: полнота {md[0] / md[1]:.0%} ({md[0]} из {md[1]} пикс. test)"),
        ("03_fp_sargassum_rf_argmax.png", f"Саргассум: LightGBM — {sa[0] + sd[0]} ложных из {n_sarg} пикс.,"
                                          f"\nокно FDI×NDVI — {sarg_box / n_sarg:.0%}"),
        ("04_fp_foam_lgbm.png", f"Пена: {fo[0]} ложное из {fo[1]} пикс."),
        ("05_fp_ship_lgbm.png", f"Суда: {sh[0]} ложных из {sh[1]} пикс. ({sh[0] / sh[1]:.1%}),\nглавный источник ложных вместе с органикой"),
        ("08_fp_natural_organic_lgbm.png", f"Природная органика: {org[0]} из {org[1]} пикс. ({org[0] / org[1]:.0%}),\n"
                                           "спектрально похожа на мусор"),
        ("09_miss_lgbm.png", "Пропуск по разметке: полоса размечена точками,\nдетектор отмечает её целиком (пурпурный)"),
    ]
    cols = ["RGB (B4, B3, B2)", "Разметка MARIDA", "LightGBM (основной)"]
    fig = plt.figure(figsize=(16, 6.4), dpi=110)
    outer = fig.add_gridspec(2, 3, left=0.01, right=0.99, top=0.87, bottom=0.09, wspace=0.06, hspace=0.32)
    for k, (fn, title) in enumerate(rows):
        im = Image.open(EX / fn).convert("RGB")
        inner = outer[k // 3, k % 3].subgridspec(1, 3, wspace=0.03)
        for j, name in enumerate(cols):
            ax = fig.add_subplot(inner[0, j])
            ax.imshow(im.crop(BOX[name]))
            ax.set_xticks([]); ax.set_yticks([])
            ax.set_xlabel(name, fontsize=8.5, labelpad=2)
            if j == 1:
                ax.set_title(title, fontsize=10, fontweight="bold", pad=6, wrap=True)
    fig.suptitle("Удачные и ошибочные обнаружения на сложном фоне (test MARIDA, основной детектор)", fontsize=14, y=0.985)
    fig.text(0.01, 0.012, "15 сцен test, порог 0.63 выбран на val. На LightGBM: зелёный — найдено верно, красный — ложное на "
             "размеченном фоне, синий — пропуск, пурпурный — отмечено на неразмеченных пикселях.\n"
             "Вырезка 64×64 пикс. = 640 м. "
             "Источник: reports/case_detector/examples, fp_by_class.csv", fontsize=8.5, ha="left")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, format="jpg", pil_kwargs={"quality": 88})
    print(OUT, Image.open(OUT).size)


if __name__ == "__main__":
    main()
