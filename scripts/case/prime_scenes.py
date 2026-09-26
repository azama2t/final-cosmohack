"""§54 п.5 (L140): PRIME MODE · синтетика — сцены «как работало бы при детальной съёмке».

  CUDA_VISIBLE_DEVICES=-1 .venv/Scripts/python.exe scripts/case/prime_scenes.py

Берёт готовые компоненты синтетики П3 (L129, scripts/p3/gen_synth.py, configs/p3_synth.yaml):
  * фон — реальная вода Sentinel-2 L2A наших сцен (out/p3/bg_blocks.npz + bg_blocks.csv: блоки SCL = 6, без находок);
  * размеры предметов — out/p3/sizes.npz (ADIS / Lebreton 2018 / Winans 2023 / бутылка 0.026 м²);
  * раскладки — те же, что в генераторе П3 (полоса, скопление, мишень-прямоугольник, равномерно) + контроль «без мусора».
Для каждой сцены 200 × 200 м: предметы кладутся по ячейкам 1 м (Пуассон(покрытие / размер), как в П3), рисуются
эллипсами своей площади на сетке 0.25 м («детальная съёмка»), рядом — та же сцена в пикселях 10 м (линейное смешение
воды и предметов по доле покрытия пикселя; спектр предметов — условный серо-белый, это иллюстрация, не модель сенсора).

Метрики только посчитанные: (1) П3 на синтетике — из reports/p3/p3_synth.json (как есть); (2) здесь же — счёт связных
областей на кадре 0.25 м против заложенного числа предметов (правда генератора), по всем сценам.
Выход: data/case/prime/index.json + data/case/prime/<id>_detail.png, <id>_s2.png. Всё — СИНТЕТИКА, не наблюдение.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from scipy import ndimage  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
P3 = ROOT / "out" / "p3"
OUT = ROOT / "data" / "case" / "prime"
BANDS11 = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
RGB = [BANDS11.index(b) for b in ("B4", "B3", "B2")]
SIZE_M = 200            # сторона сцены, м
S2 = 10                 # пиксель Sentinel-2, м
DET = 0.25              # «детальная съёмка», м/пиксель
K = int(SIZE_M / DET)   # 800 px
ITEM_RGB = np.array([0.22, 0.21, 0.20], np.float32)   # условная отражательная способность предметов (иллюстрация)
MAGENTA = (236, 64, 196)
REGION_RU = {"accra": "Аккра", "bali": "Бали", "danang": "Дананг", "durban": "Дурбан", "ganges": "Ганг",
             "guanabara": "залив Гуанабара", "haiti": "Гаити", "honduras": "Гондурас", "jakarta": "Джакарта",
             "karachi": "Карачи", "lagos": "Лагос", "manila": "Манильский залив", "mekong": "Меконг", "mumbai": "Мумбаи",
             "nile": "Нил", "santo_domingo": "Санто-Доминго", "scotland": "Шотландия", "tiber": "Тибр",
             "cozar_demo": "Альборан"}
SRC_RU = {"adis": "ADIS (буи/волокно/жёсткий пластик)", "lebreton": "Lebreton 2018 (мусорное пятно)",
          "winans": "Winans 2023 (аэросъёмка, Гавайи)", "bottle": "бутылка 1,5 л (0,026 м²)"}

SCENES = [  # id, название, раскладка, источник размеров, доля покрытия в ядре раскладки, параметры
    dict(id="p1_windrow", title="Полоса мусора вдоль фронта течения", layout="windrow", src="adis", fc=0.06,
         p=dict(th=0.5, w=8, ln=150)),
    dict(id="p2_cluster", title="Плотное скопление", layout="cluster", src="lebreton", fc=0.12, p=dict(s=15)),
    dict(id="p3_target", title="Мишень 10 × 5 м (как в опытах PLP)", layout="target_rect", src="bottle", fc=0.43,
         p=dict(h=5, w=10)),
    dict(id="p4_sparse", title="Редкий рассеянный мусор", layout="uniform", src="winans", fc=0.004, p={}),
    dict(id="p5_weak", title="Слабое пятно (не видно в 10 м)", layout="cluster", src="adis", fc=0.04, p=dict(s=10)),
    dict(id="p6_clean", title="Контроль: чистая вода", layout="zero", src="adis", fc=0.0, p={}),
]


def density(layout: str, p: dict, rng) -> np.ndarray:
    n = SIZE_M
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32) + 0.5
    cy, cx = n / 2 + rng.uniform(-10, 10, 2)
    if layout == "zero":
        return np.zeros((n, n), np.float32)
    if layout == "uniform":
        return np.ones((n, n), np.float32)
    if layout == "cluster":
        s = p["s"]
        return np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * s * s)).astype(np.float32)
    if layout == "windrow":
        th = p["th"]
        u = (xx - cx) * np.cos(th) + (yy - cy) * np.sin(th)
        v = -(xx - cx) * np.sin(th) + (yy - cy) * np.cos(th)
        return ((np.abs(v) < p["w"] / 2) & (np.abs(u) < p["ln"] / 2)).astype(np.float32)
    if layout == "target_rect":
        return ((np.abs(yy - cy) < p["h"] / 2) & (np.abs(xx - cx) < p["w"] / 2)).astype(np.float32)
    raise ValueError(layout)


def lonlat(scene_key: str, r: int, c: int):
    try:
        import rasterio
        from pyproj import Transformer
        with rasterio.open(ROOT / "data" / "live" / scene_key / "bands.tif") as ds:
            x, y = ds.transform * (c, r)
            t = Transformer.from_crs(ds.crs, "EPSG:4326", always_xy=True)
            lon, lat = t.transform(x, y)
            return round(lat, 5), round(lon, 5)
    except Exception:
        return None, None


def to_u8(rgb: np.ndarray) -> np.ndarray:
    return (np.clip(rgb / 0.12, 0, 1) ** (1 / 1.6) * 255).astype(np.uint8)


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(54)
    sizes = np.load(P3 / "sizes.npz")
    bgz = np.load(P3 / "bg_blocks.npz")
    blocks = bgz["blocks"]                        # (118, 11, 240, 240), NaN вне воды
    meta = pd.read_csv(P3 / "bg_blocks.csv")
    n_px = SIZE_M // S2                           # 20 пикселей S2
    scenes, cc_rows = [], []
    used = set()
    for sc in SCENES:
        # фон: блок реальной воды S2 с полностью конечным окном 20 × 20 пикселей
        for _ in range(500):
            b = int(rng.integers(len(blocks)))
            if b in used:
                continue
            r0, c0 = (int(v) for v in rng.integers(0, 240 - n_px, 2))
            win = blocks[b][:, r0:r0 + n_px, c0:c0 + n_px]
            if np.isfinite(win).all():
                used.add(b)
                break
        water = win[RGB].transpose(1, 2, 0).astype(np.float32)          # (20, 20, 3) отражение
        key = meta.scene[b]
        rr, cc = int(meta.r[b]) + r0 + n_px // 2, int(meta.c[b]) + c0 + n_px // 2
        lat, lon = lonlat(key, rr, cc)
        # предметы по ячейкам 1 м, как в генераторе П3
        D = density(sc["layout"], sc["p"], rng)
        cexp = D / max(D.max(), 1e-9) * sc["fc"]
        lib = sizes[sc["src"]]
        items = []
        ys, xs = np.nonzero(cexp > 0)
        for y, x in zip(ys, xs):
            a = float(lib[rng.integers(len(lib))])
            n = rng.poisson(cexp[y, x] / a)
            for _ in range(n):
                a_i = float(lib[rng.integers(len(lib))]) if sc["src"] != "bottle" else a
                items.append((x + rng.random(), y + rng.random(), a_i, rng.uniform(1, 3), rng.uniform(0, np.pi)))
        # «детальная съёмка» 0.25 м: маска предметов (суперсэмплинг ×2 для доли покрытия)
        ss = 2
        im = Image.new("L", (K * ss, K * ss), 0)
        dr = ImageDraw.Draw(im)
        scale = ss / DET
        area_px_min = 0
        for (x, y, a, asp, th) in items:
            ra = np.sqrt(a * asp / np.pi)
            rb = np.sqrt(a / (asp * np.pi))
            t = np.linspace(0, 2 * np.pi, 12, endpoint=False)
            px = x + ra * np.cos(t) * np.cos(th) - rb * np.sin(t) * np.sin(th)
            py = y + ra * np.cos(t) * np.sin(th) + rb * np.sin(t) * np.cos(th)
            pts = list(zip(px * scale, py * scale))
            dr.polygon(pts, fill=255)
            if max(ra, rb) * scale < 1:
                dr.point((x * scale, y * scale), fill=255)
                area_px_min += 1
        cov_ss = np.asarray(im, np.float32) / 255.0
        cov_det = cov_ss.reshape(K, ss, K, ss).mean((1, 3))                  # доля покрытия пикселя 0.25 м
        mask_det = cov_det >= 0.5
        n_cc = int(ndimage.label(cov_ss > 0.5, structure=np.ones((3, 3)))[1])  # связные области на сетке 0.125 м
        # фон детального кадра: вода S2, увеличенная билинейно (текстура волн не моделируется)
        wat_img = Image.fromarray(to_u8(water)).resize((K, K), Image.BILINEAR)
        det = np.asarray(wat_img, np.float32)
        det = det * (1 - cov_det[..., None]) + np.array(MAGENTA, np.float32) * cov_det[..., None]
        Image.fromarray(det.astype(np.uint8)).save(OUT / f"{sc['id']}_detail.png", optimize=True)
        # та же сцена в пикселях 10 м: линейное смешение по доле покрытия
        cov_s2 = cov_det.reshape(n_px, K // n_px, n_px, K // n_px).mean((1, 3))
        mix = water * (1 - cov_s2[..., None]) + ITEM_RGB * cov_s2[..., None]
        s2 = Image.fromarray(to_u8(mix)).resize((K, K), Image.NEAREST)
        Image.fromarray(np.asarray(s2)).save(OUT / f"{sc['id']}_s2.png", optimize=True)
        n_true = len(items)
        area_true = float(sum(i[2] for i in items))
        cc_rows.append((n_true, n_cc))
        region, date = key.split("/")
        scenes.append(dict(
            id=sc["id"], title=sc["title"], layout=sc["layout"], size_src=sc["src"], size_src_ru=SRC_RU[sc["src"]],
            size_m=SIZE_M, detail_m=DET, s2_m=S2,
            background=dict(source="Sentinel-2 L2A (реальная вода, без находок)", region=region, region_ru=REGION_RU.get(region, region), date=date,
                            lat=lat, lon=lon, note="фон увеличен из пикселей 10 м; волны и блики не моделируются"),
            truth=dict(n_items=n_true, items_area_m2=round(area_true, 1),
                       cover_pct_scene=round(100 * float(cov_det.mean()), 3),
                       max_cover_pct_s2_pixel=round(100 * float(cov_s2.max()), 1),
                       s2_pixels_cover_ge_20pct=int((cov_s2 >= 0.2).sum()),
                       s2_pixels_cover_ge_1pct=int((cov_s2 >= 0.01).sum()),
                       items_below_detail_px=area_px_min),
            count_detail=dict(n_components=n_cc, rel_err=(round((n_cc - n_true) / n_true, 3) if n_true else None)),
            img_detail=f"/api/prime/img/{sc['id']}_detail.png", img_s2=f"/api/prime/img/{sc['id']}_s2.png",
        ))
        print(sc["id"], key, "items", n_true, "cc", n_cc, f"{time.time() - t0:.0f} s", flush=True)
    p3 = json.loads((ROOT / "reports/p3/p3_synth.json").read_text(encoding="utf-8"))
    nz = [(t, c) for t, c in cc_rows if t > 0]
    cc_mae = float(np.mean([abs(t - c) for t, c in cc_rows]))
    cc_rel = float(np.median([abs(c - t) / t for t, c in nz])) if nz else None
    metrics = [
        dict(label="Счёт связных областей на кадре 0,25 м против заложенного числа (эти 6 сцен)",
             value=f"медиана отн. ошибки {cc_rel:.0%}, MAE {cc_mae:.0f} предм. на сцену", file="data/case/prime/index.json",
             note="слипшиеся предметы считаются одним — в плотных сценах счёт занижен; это простой счёт, не модель"),
        dict(label="П3: спектр S2 → число бутылок-эквивалентов, синтетический тест",
             value=f"MAE {p3['folds']['final']['synth_test_mae']} против {p3['folds']['final']['synth_test_mae_zero_pred']} у «всегда 0»",
             file="reports/p3/p3_synth.json", note="folds.final; окна 15 × 15 пикселей S2"),
        dict(label="П3: число предметов реальных размеров по спектру (синтетика)",
             value=f"медиана отн. ошибки {p3['synthetic_natural_count']['median_rel_err_nonzero']:.0%}",
             file="reports/p3/p3_synth.json", note="спектр несёт долю покрытия, а не размер предметов"),
        dict(label="П3 на реальных мишенях (проверка вне синтетики)",
             value=f"MAE {p3['primary']['mae_p3']} против {p3['primary']['mae_flat']} у плоской калибровки — модель НЕ принята",
             file="reports/p3/p3_synth.json", note="поэтому в основном продукте её нет"),
    ]
    index = dict(
        badge="СИНТЕТИКА — не наблюдение",
        about=("Сцены сгенерированы: фон — реальная вода Sentinel-2 наших районов, предметы — синтетические, размеры "
               "из полевых наборов (ADIS, Lebreton 2018, Winans 2023). Показывают, что было бы видно при детальной съёмке "
               "(0,25 м/пикс., дрон/аэро) и во что те же предметы превращаются в пикселе 10 м. Это не наблюдение и не "
               "прогноз для реального места."),
        generator="scripts/case/prime_scenes.py (компоненты П3: scripts/p3/gen_synth.py, configs/p3_synth.yaml)",
        created=time.strftime("%Y-%m-%d %H:%M"), seed=54, scenes=scenes, metrics=metrics,
        count_detail_rows=[dict(id=s["id"], n_true=t, n_components=c) for s, (t, c) in zip(scenes, cc_rows)],
    )
    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print("ok", len(scenes), f"{time.time() - t0:.0f} s")


if __name__ == "__main__":
    sys.exit(main())
