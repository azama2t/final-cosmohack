"""PLASTIC MONITOR (ESA Discovery, Deltares, Citarum 2021-2022): raw numbers found in open sources ->
docs/research/pairs/plastic_monitor_raw.csv. Inputs (all open, downloaded < 2 MB):
  data/extra/plastic_monitor/C4000134420_ESR.pdf (+ esr_img/ extracted figures) - nebula.esa.int
  data/extra/plastic_monitor/plastic_monitor/{Layers,S2}/ - THREDDS deltaresdata.openearth.eu/thredds/catalog/plastic_monitor
  data/extra/plastic_monitor/github/ - github.com/Deltares-research/plastic-monitor (small files)
  out/l127/pm/fig5_join.json - out/l127/pm/fig5_join.py (Summary.xlsx matchup pins x digitised ESR Figure 5)
"""
import csv, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
J = json.loads((ROOT / "out/l127/pm/fig5_join.json").read_text(encoding="utf-8"))
COLS = ["id", "дата", "что_посчитано", "N", "N_органика", "единица", "площадь_пробы_м2", "lon", "lat", "S2_product_id", "FDI_S2_авторов",
        "NDVI_S2_авторов", "источник_файл", "как_получено", "надёжность", "примечание"]
rows = []
for o in J:
    n = o["n_plastic_digitised"]
    c = o["candidates"]
    rows.append({
        "id": f"PM-matchup-{o['date']}-p{o['point']}", "дата": f"{o['date'][:4]}-{o['date'][4:6]}-{o['date'][6:]}",
        "что_посчитано": "пластиковые предметы в пробе 1 м² (пул пластика, 10 повторов на пятне скопления)",
        "N": n if n is not None else ("; ".join(str(x) for x in c) + " (неоднозначно)" if c else ""),
        "N_органика": o["n_organic_digitised"] if o["n_organic_digitised"] is not None else "; ".join(str(x) for x in o["organic_candidates"]),
        "единица": "шт. на пробу 1 м²", "площадь_пробы_м2": 1, "lon": o["lon"], "lat": o["lat"],
        "S2_product_id": o["product"], "FDI_S2_авторов": o["fdi"], "NDVI_S2_авторов": o["ndvi"],
        "источник_файл": "THREDDS plastic_monitor/S2/Summary.xlsx (лист DataME: пин, lon/lat, FDI, NDVI) + ESR рис. 5 (N)",
        "как_получено": "N оцифрован с рис. 5 ESR (панели «Plastic item vs FDI» и «vs NDVI»); точка сопоставлена по совпадению FDI И NDVI из Summary.xlsx",
        "надёжность": ("N ±3 шт. (радиус точки на рисунке); дата/координаты/FDI — из файла авторов" if n is not None else
                       ("несколько кандидатов N" if c else "N не найден на рис. 5 (проба вне рисунка или FDI/NDVI не совпали)")),
        "примечание": ("FDI авторов по L1C (TOA)" if "MSIL1C" in o["product"] else "FDI авторов по L2A") +
                      "; время отбора в файлах нет; проба 1 м² ≠ пиксель S2 100–400 м²"})
rows += [
    {"id": "PM-total-2021-2022", "дата": "2021-10 … 2022-08", "что_посчитано": "все собранные плавающие предметы, 4 участка",
     "N_органика": "", "N": 57957, "единица": "шт. (сумма за проект)", "площадь_пробы_м2": "", "lon": "", "lat": "", "S2_product_id": "",
     "FDI_S2_авторов": "", "NDVI_S2_авторов": "", "источник_файл": "C4000134420_ESR.pdf, рис. 2 (подпись)",
     "как_получено": "текст отчёта", "надёжность": "число из отчёта; по дням/пробам не разбито",
     "примечание": "доли: пластик 74 %, органика 11 %, дерево 6 %, резина 4 %, остаток 4 %, ткань 1 % (рис. 2); помесячные доли — рис. 3"},
    {"id": "PM-frame-20220822", "дата": "2022-08-22 и 2022-08-27", "что_посчитано": "контролируемый опыт: рамка 10×10 м (бамбук, чёрная ткань) с речным пластиком, участок 3",
     "N_органика": "", "N": "", "единица": "", "площадь_пробы_м2": 100, "lon": "", "lat": "", "S2_product_id": "S2 L2A 22.08.2022 (id не указан)",
     "FDI_S2_авторов": "22.08: ≈ −0.004 и −0.010; 27.08: ≈ 0.02–0.035 (рис. 6, оцифровка на глаз)", "NDVI_S2_авторов": "22.08: ≈ −0.05 и 0.10; 27.08: ≈ 0.17–0.22",
     "источник_файл": "C4000134420_ESR.pdf, рис. 6 и 7; слой THREDDS Layers/sampling_points (10 точек)",
     "как_получено": "текст и рисунки отчёта", "надёжность": "число предметов в рамке не опубликовано",
     "примечание": "авторы: при 100 % пластика в рамке — ≤ 25 % покрытия пикселя 20 м; FDI рамки ~0 (22.08) — сигнал слабый"},
    {"id": "PM-camera-20211021", "дата": "2021-10-21", "что_посчитано": "поток плавающих предметов через зону кадра камеры с моста (участок 2)",
     "N_органика": "", "N": "", "единица": "шт./с (поток), площадь по геопривязке", "площадь_пробы_м2": "", "lon": 107.50586, "lat": -6.93268,
     "S2_product_id": "S2A_MSIL2A_20211021T025751_N0301_R032_T48MYT_20211021T062735 (02:57:51 UTC = 09:57:51 WIB)",
     "FDI_S2_авторов": "", "NDVI_S2_авторов": "",
     "источник_файл": "github Deltares-research/plastic-monitor: DebrisDetection/20211021/TLC00009_00890_02300_small.avi (20 МБ), calibrations/*/point_bridge_20211021_UTM32749.csv (GPS 09:51–10:07 WIB)",
     "как_получено": "видео и калибровка открыты; счёт — прогоном debrisDetection.py (нужен OpenCV, в среде нет)",
     "надёжность": "таблицы счёта в открытом доступе нет; видео ≈ в час пролёта S2",
     "примечание": "поток ≠ шт./площадь; мост в 3.3 км ниже по течению от участка проб; ширина реки ≈ десятки м → смешанные пиксели"},
    {"id": "PM-sampling-points", "дата": "2022 (слой без даты)", "что_посчитано": "10 точек отбора проб (две линии по 5 точек поперёк пятна)",
     "N_органика": "", "N": "", "единица": "", "площадь_пробы_м2": 1, "lon": "107.4757–107.4778", "lat": "−6.9152 … −6.9186", "S2_product_id": "",
     "FDI_S2_авторов": "", "NDVI_S2_авторов": "", "источник_файл": "THREDDS plastic_monitor/Layers/sampling_points.shp",
     "как_получено": "атрибуты слоя: Lon, Lat, name 1–10", "надёжность": "без счёта", "примечание": "совпадают с пинами 1–10 Summary.xlsx 19.04.2022"},
]
out = ROOT / "docs/research/pairs/plastic_monitor_raw.csv"
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, COLS); w.writeheader(); w.writerows(rows)
print(len(rows), "rows ->", out)
