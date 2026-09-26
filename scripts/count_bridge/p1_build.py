"""L127 П1: build docs/research/pairs/p1_targets.csv (one row per campaign x date, CASES_REGISTRY columns +
«пиксели_детектора_на_месте_счёта») and docs/research/pairs/p1_pixels.csv (one row per target pixel: coverage, items per
pixel, 12 L2A bands, water background of the date, FDI / NDVI / B8 anomaly, detector P) from out/l127/<site>/<date>/pixels.json
(scripts/count_bridge/p1_targets.py).

  .venv/Scripts/python.exe scripts/count_bridge/p1_build.py

Item counts (published / field notes; nothing else is assumed):
  PLP2018  3 600 PET 1.5 L bottles on 10x10 m + 138 LDPE bags on 10x10 m (Topouzelis et al. 2019, JAG 79:175, 2.1)
  PLP2019  416 PET bottles per 5x5 m target at 100 % cover -> 16.64 /m2; bag count not published (Topouzelis 2020, RS 12:2013)
  PLP2021  wooden target 342 boards on a circle r = 14 m (615.8 m2) (L115 / Papageorgiou 2022); HDPE mesh = 1 object
  PLP2022  PVC 5x5 m sheet + 2 HDPE circles r = 3.5 m: single objects, coverage only
  Maathuis PET bottles 4 /m2 on 3x30 m (= 360) and 8 /m2 on 3x15 m (= 360) (Fieldwork_Notes.xlsx, 4TU 3d24e304);
           polyester sheets 30x3 / 30x2 / 30x1 / 30x0.5 m = coverage only. Items in a pixel = 360 x (pixel ∩ hull) / hull,
           hull = convex hull of the Topcon GNSS outline points.
  Themistocleous 2020: 1 500 bottles (0.5 and 1.5 L) on 3x10 m; location only from the paper figures (Fig. 8, 12).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out" / "l127"
DST = ROOT / "docs" / "research" / "pairs"
BANDS = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"]
THR = 0.63
BOTTLES_M2_2019 = 416 / 25.0
WOOD_BOARDS, WOOD_AREA = 342, np.pi * 14 ** 2
EXIF_2019 = {"20190418": "13:46", "20190503": "11:32", "20190518": "11:27", "20190528": "12:52", "20190607": "12:45"}
STATE_2021 = {"20210611": "floating", "20210621": "floating", "20210626": "floating", "20210701": "floating",
              "20210706": "floating", "20210711": "floating", "20210716": "floating", "20210721": "floating",
              "20210726": "floating", "20210731": "floating", "20210805": "floating", "20210810": "floating",
              "20210815": "submerged", "20210820": "submerged", "20210825": "part sub", "20210830": "part sub",
              "20210904": "mix floating", "20210909": "mix part sub", "20210914": "mix mostly sub",
              "20210919": "mix mostly sub", "20210924": "mostly sub", "20211004": "mostly sub"}
MA_N = {"PET 3x30 m, 4 /m2": 360, "PET 3x15 m, 8 /m2": 360}
THEMIS_PX = [(142, 129), (142, 130), (142, 131), (143, 129), (143, 130), (143, 131)]  # candidate (see report)

REG_COLS = ["id", "первоисточник_URL_DOI", "лицензия", "природный_искусственный", "море_река_берег", "lat_lon_контур",
            "дата_UTC", "платформа_GSD", "ссылка_на_кадр", "полевой_метод", "N", "материал_мин_размер", "площадь_A",
            "единицы", "полнота_разметки", "сцена_product_id_dt", "дрейф_радиус", "независимость_train_test",
            "визуальное_подтверждение", "класс", "можно_нельзя_обучать", "следующий_шаг",
            "пиксели_детектора_на_месте_счёта"]


def fdi_of(v):
    return v["B8"] - (v["B6"] + (v["B11"] - v["B6"]) * (842 - 665) / (1610 - 665) * 10)


def load(site):
    for f in sorted((OUT / site).glob("*/pixels.json")):
        yield json.loads(f.read_text(encoding="utf-8"))


def r4(x):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), 4)


def pixel_row(camp, d, p, **kw):
    bg = d.get("bg_median") or {}
    fbg = fdi_of(bg) if bg else None
    row = dict(pair_id=f"P1-{camp}-{d['date']}", campaign=camp, date=d["date"], scene_id=d["scene_id"],
               scene_datetime=d["scene_datetime"], row=p["row"], col=p["col"], x=r4(p.get("x")), y=r4(p.get("y")))
    row.update(kw)
    row.update({b: r4(p[b]) for b in BANDS})
    row.update(fdi=r4(p["fdi"]), ndvi=r4(p["ndvi"]))
    row.update({f"bg_{b}": r4(bg.get(b)) for b in BANDS})
    row.update(fdi_contrast=r4(p["fdi"] - fbg) if fbg is not None else None,
               b8_anom=r4(p["B8"] - bg["B8"]) if bg else None,
               p=r4(p["p"]), q=p["q"], det=int(p["p"] >= THR and p["q"] == 1),
               evaluable=int(bool(d["evaluable"])), sun_zenith=r4(d.get("sun_zenith")), water_b3=r4(d.get("water_b3")))
    return row


def main():
    pix, dates = [], []
    # ---------------- PLP
    for d in load("plp"):
        yr = d["date"][:4]
        camp = f"PLP{yr}"
        P = []
        for p in d["pixels"]:
            fp = p.get("frac_plastic")
            kw = dict(epsg=32635, surface="sea", target=p.get("target"), pixel_id=p.get("pixel"), material=p.get("material"),
                      frac_target=None, frac_plastic=r4(fp), frac_bottles=r4(p.get("frac_bottles")),
                      frac_bags=r4(p.get("frac_bags")), frac_wood=r4(p.get("frac_wood")),
                      plastic_m2=r4(fp * 100) if fp is not None else None, items_in_pixel=None, items_kind="",
                      items_basis="", note=p.get("note") or "")
            if yr == "2019":
                kw["frac_target"] = r4((p.get("frac_plastic") or 0) + (p.get("frac_reeds") or 0))
                kw["items_in_pixel"] = round(p["frac_bottles"] * 100 * BOTTLES_M2_2019, 1)
                kw["items_kind"] = "PET bottles"
                kw["items_basis"] = "drone bottle fraction x 100 m2 x 16.64 /m2 (416 per 25 m2); bags not counted (unpublished)"
            elif yr == "2018" and p.get("target") in ("bottles", "bags", "nets"):
                kw["frac_target"] = r4(fp)
                if p["target"] == "bottles":
                    kw.update(items_in_pixel=round(fp * 100 * 36.0, 1), items_kind="PET 1.5 L bottles",
                              items_basis="frame fraction x 100 m2 x 36 /m2 (3600 per 10x10 m); ortho registered to S2")
                elif p["target"] == "bags":
                    kw.update(items_in_pixel=round(fp * 100 * 1.38, 1), items_kind="LDPE bags",
                              items_basis="frame fraction x 138 bags per 100 m2; ortho registered to S2")
                else:
                    kw.update(items_basis="fishing net: coverage only (200 m2 net on 100 m2 frame)")
            elif yr == "2018":
                kw.update(target="PS box 20180607_082852 (5x5 px)", items_basis="per-pixel cover unknown: 3 targets 10x10 m "
                          "(3600 bottles; 138 bags; nets) inside ~625 m2 NASA PS box; location approximate")
            elif yr == "2021" and p.get("material") == "wood":
                kw["frac_target"] = r4(p.get("frac_wood"))
                kw["items_in_pixel"] = round(p["frac_wood"] * 100 * WOOD_BOARDS / WOOD_AREA, 1)
                kw["items_kind"] = "wooden boards (not plastic)"
                kw["items_basis"] = "342 boards x pixel share of r=14 m circle"
            else:
                kw["frac_target"] = r4(fp)
                kw["items_basis"] = "single object (HDPE mesh / sheet): coverage only"
            if yr == "2021":
                kw["note"] = (kw["note"] + " state=" + STATE_2021.get(d["date"], "")).strip()
            r = pixel_row(camp, d, p, **kw)
            pix.append(r)
            P.append(r)
        dates.append((camp, d, P))
    # ---------------- Maathuis
    for d in load("maathuis"):
        hull = {t["name"]: t["area_m2"] for t in d["targets"]}
        P = []
        for p in d["pixels"]:
            if not p["cover"]:
                continue
            items = 0.0
            kinds = []
            for name, c in p["cover"].items():
                if name in MA_N:
                    items += MA_N[name] * c["area_m2"] / hull[name]
                    kinds.append(name)
            fr_pet = sum(c["frac"] for n, c in p["cover"].items() if n in MA_N)
            kw = dict(epsg=32631, surface="land (riverbank)", target="; ".join(p["cover"]), pixel_id=f"{p['row']}_{p['col']}",
                      material="; ".join(sorted({c['material'] for c in p['cover'].values()})),
                      frac_target=r4(p["frac_target"]), frac_plastic=r4(p["frac_target"]), frac_bottles=r4(fr_pet),
                      frac_bags=None, frac_wood=None, plastic_m2=r4(p["frac_target"] * 100),
                      items_in_pixel=round(items, 1) if kinds else None, items_kind="PET bottles" if kinds else "",
                      items_basis="360 bottles x (pixel ∩ hull) / hull; frac = target footprint, not bottle area" if kinds
                      else "polyester sheet: coverage only", note="")
            r = pixel_row("Maathuis2026", d, p, **kw)
            pix.append(r)
            P.append(r)
        dates.append(("Maathuis2026", d, P))
    # ---------------- Themistocleous
    for d in load("themis"):
        M = {(p["row"], p["col"]): p for p in d["pixels"]}
        P = []
        for rc in THEMIS_PX:
            p = M[rc]
            kw = dict(epsg=32636, surface="sea", target="candidate (paper Fig. 8/12 location)", pixel_id=f"{rc[0]}_{rc[1]}",
                      material="PET bottles 0.5 + 1.5 L", frac_target=None, frac_plastic=None, frac_bottles=None,
                      frac_bags=None, frac_wood=None, plastic_m2=None, items_in_pixel=None, items_kind="",
                      items_basis="1500 bottles on 3x10 m (50 /m2) somewhere in the 6-px cluster; per-pixel split unknown",
                      note="water B4-B8 of the L2A = 0.0001 (dark-water clamp); low sun")
            r = pixel_row("Themistocleous2020", d, p, **kw)
            pix.append(r)
            P.append(r)
        dates.append(("Themistocleous2020", d, P))

    # ---------------- located by the NIR anomaly (no published coordinates): Fronkova 2024, PLP2020, PLP2024
    for site, camp in (("fronkova2024", "Fronkova2024"), ("plp2020", "PLP2020"), ("plp2024", "PLP2024")):
        for d in load(site):
            P = []
            for p in d["pixels"]:
                kw = dict(epsg=d["epsg"], surface="water", target=d["located_note"].split(";")[0],
                          pixel_id=f"{p['row']}_{p['col']}" + ("*" if p.get("centre") else ""), material="",
                          frac_target=None, frac_plastic=None, frac_bottles=None, frac_bags=None, frac_wood=None,
                          plastic_m2=None, items_in_pixel=None, items_kind="",
                          items_basis="3x3 px around the located anomaly (*); per-pixel cover unknown",
                          note=d["located_note"])
                r = pixel_row(camp, d, p, **kw)
                pix.append(r)
                P.append(r)
            dates.append((camp, d, P))

    ps_rows = maathuis_ps(rows_out := [])

    DST.mkdir(parents=True, exist_ok=True)
    if ps_rows:
        with open(DST / "p1_pixels_ps.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, list(ps_rows[0].keys()))
            w.writeheader()
            w.writerows(ps_rows)
    cols = list(pix[0].keys())
    with open(DST / "p1_pixels.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows(pix)
    rows = [date_row(c, d, P) for c, d, P in dates]
    rows = [r for r in rows if r]
    rows += rows_out
    with open(DST / "p1_targets.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, REG_COLS)
        w.writeheader()
        w.writerows(rows)
    summary(pix, rows)


# ---------------- Maathuis 2026: PlanetScope SuperDove clips (Scripts_and_input_data.zip, 4TU, CC BY 4.0), 3 m
MA_TOPCON = ROOT / "data/extra/count_ds/maathuis_riverbank/field/Field_campaign_data/Measurements/Topcon"
MA_PS = ROOT / "data/extra/count_ds/maathuis_riverbank/Scripts_and_input_data/Data/PlanetScope"
PS_BANDS = ["CoastalBlue", "Blue", "Green_i", "Green", "Yellow", "Red", "RedEdge", "NIR"]
# date -> targets, as in the authors' main.py (poly2 x2, poly1 x2, poly05 x2, bot4 x3, bot8 x3, no-target x2)
MA_PS_PLAN = {
    "20250306": [],
    "20250315": [("polyester 30x2 m", "mi1003", (100, 109), None), ("PET 3x30 m, 4 /m2", "mi1003", (110, 136), 360)],
    "20250316": [("polyester 30x2 m", "mi1003", (100, 109), None), ("PET 3x30 m, 4 /m2", "mi1003", (110, 136), 360)],
    "20250317": [("polyester 30x1 m", "mi1703", (100, 109), None), ("PET 3x30 m, 4 /m2", "mi1003", (110, 136), 360)],
    "20250319": [("polyester 30x1 m", "mi1703", (100, 109), None), ("PET 3x15 m, 8 /m2", "mi1703", (110, 120), 360)],
    "20250320": [("polyester 30x0.5 m", "mi1903", (100, 112), None), ("PET 3x15 m, 8 /m2", "mi1703", (110, 120), 360)],
    "20250321": [("polyester 30x0.5 m", "mi1903", (100, 112), None), ("PET 3x15 m, 8 /m2", "mi1703", (110, 120), 360)],
    "20250327": [],
}


def maathuis_ps(rows_out):
    import geopandas as gpd
    import rasterio
    from shapely.geometry import MultiPoint, box
    if not MA_PS.exists():
        return []
    out = []
    for dt, plan in MA_PS_PLAN.items():
        f = MA_PS / f"{dt[6:8]}{dt[4:6]}_PS.tif"
        if not f.exists():
            continue
        with rasterio.open(f) as ds:
            a = ds.read().astype(float) / 10000.0
            t = ds.transform
        polys = []
        for name, shp, (lo, hi), n in plan:
            g = gpd.read_file(MA_TOPCON / f"{shp}.shp").to_crs(epsg=32631)
            k = g.OBJNAME.astype(int)
            polys.append((name, n, MultiPoint(list(g[(k >= lo) & (k <= hi)].geometry)).convex_hull))
        H, W = a.shape[1:]
        cover = np.zeros((H, W))
        recs = []
        for r in range(H):
            for c in range(W):
                x0, y0 = t * (c, r)
                x1, y1 = t * (c + 1, r + 1)
                px = box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                cov = {nm: px.intersection(pg).area for nm, n, pg in polys}
                cov = {k_: v for k_, v in cov.items() if v > 0}
                if not cov:
                    continue
                cover[r, c] = sum(cov.values()) / px.area
                items = sum(n * cov[nm] / pg.area for nm, n, pg in polys if n and nm in cov)
                recs.append((r, c, cov, items, px.area))
        bgm = cover == 0
        bg = {b: float(np.median(a[i][bgm])) for i, b in enumerate(PS_BANDS)}
        for r, c, cov, items, pa in recs:
            v = {b: round(float(a[i, r, c]), 4) for i, b in enumerate(PS_BANDS)}
            ndvi = (v["NIR"] - v["Red"]) / (v["NIR"] + v["Red"]) if v["NIR"] + v["Red"] else None
            out.append(dict(pair_id=f"P1-Maathuis2026PS-{dt}", date=dt, image=f"{f.name} (PlanetScope SuperDove, 3 m, SR)",
                            row=r, col=c, target="; ".join(cov), frac_target=round(sum(cov.values()) / pa, 4),
                            items_in_pixel=round(items, 1) if items else None,
                            items_basis="360 bottles x (pixel ∩ hull) / hull" if items else "polyester: coverage only",
                            **v, ndvi=round(ndvi, 4) if ndvi is not None else None,
                            nir_anom=round(v["NIR"] - bg["NIR"], 4), **{f"bg_{b}": round(bg[b], 4) for b in PS_BANDS},
                            surface="land (riverbank)", detector="n/a (PlanetScope; S2 detector not applicable)"))
        P = [o for o in out if o["date"] == dt]
        pet = any(n for _, n, _ in polys)
        fr = [o["frac_target"] for o in P]
        it = [o["items_in_pixel"] for o in P if o["items_in_pixel"]]
        rows_out.append({
            "id": f"P1-Maathuis2026PS-{dt}",
            "первоисточник_URL_DOI": "Maathuis et al. 2026, Mar. Pollut. Bull. 119751; 4TU doi 10.4121/3d24e304-27d2-4980-8a48-2c9ef1973124 (Scripts_and_input_data.zip)",
            "лицензия": "CC-BY-4.0", "природный_искусственный": "искусственный",
            "море_река_берег": "берег реки (Недеррейн, Вагенинген, NL)", "lat_lon_контур": "Topcon GNSS (EPSG:28992), ≈ 51.9547N 5.653E",
            "дата_UTC": f"{dt[:4]}-{dt[4:6]}-{dt[6:]} (PlanetScope; время съёмки в файле не указано)",
            "платформа_GSD": "PlanetScope SuperDove 3 м (клип авторов)", "ссылка_на_кадр": f"Data/PlanetScope/{f.name}",
            "полевой_метод": "мишени на берегу, Fieldwork_Notes.xlsx; раскладка по датам — как в main.py авторов",
            "N": "360 бутылок PET" if pet else ("полотно полиэстера" if plan else "0 (контроль)"),
            "материал_мин_размер": "PET бутылки; полиэстер",
            "площадь_A": "; ".join(f"{nm} ({pg.area:.1f} м² по GNSS)" for nm, _, pg in polys) or "мишеней нет (контроль)",
            "единицы": (f"доля пикселя 3 м под мишенью max {max(fr):.2f}; " if fr else "") + (f"бутылок в пикселе 3 м max {max(it):.0f}" if it else ""),
            "полнота_разметки": "контур по GNSS; бутылки — плотность × площадь",
            "сцена_product_id_dt": f"PlanetScope {f.name}; id сцены в клипе не сохранён; мишени лежали весь день",
            "дрейф_радиус": "мишень закреплена", "независимость_train_test": "не в MARIDA; в обучении не использовалась",
            "визуальное_подтверждение": "фото и MAIA S2 в записи",
            "класс": "A*" if pet else "контроль",
            "можно_нельзя_обучать": "[нет | да | нет (суша, PlanetScope) | да] только спектр суши",
            "следующий_шаг": "только для П3/справки: снимок не S2",
            "пиксели_детектора_на_месте_счёта": (f"детектор S2 не применим (PlanetScope, суша); NIR-аномалия max "
                                                 f"{max(o['nir_anom'] for o in P):+.4f} к фону травы {bg['NIR']:.3f}" if P else "мишени нет (контроль)")})
    return out


def det_text(d, P):
    if not P:
        return "мишени нет (контроль)"
    ps = [r["p"] for r in P]
    k = sum(r["det"] for r in P)
    fc = [r["fdi_contrast"] for r in P if r["fdi_contrast"] is not None]
    ba = [r["b8_anom"] for r in P if r["b8_anom"] is not None]
    surf = P[0]["surface"]
    qs = sorted({r["q"] for r in P})
    ev = "да" if d["evaluable"] else "нет"
    fcs = f"{max(fc):.4f}" if fc else "— (нет фона воды)"
    bas = f"{max(ba):.4f}" if ba else "—"
    fr = [r["fdi"] for r in P]
    t = (f"P≥0.63 на годной воде: {k} из {len(P)} пикс.; P max {max(ps):.3f}; FDI max {max(fr):.4f}; FDI-контраст max "
         f"{fcs}; B8-аномалия max {bas}; оценивается: {ev} (зенит {d['sun_zenith']:.1f}°, B3 воды "
         f"{d['water_b3']}); коды качества {qs}")
    if surf.startswith("land"):
        t += "; мишень на суше — детектор (вода) не применим, P по всем пикселям"
    return t


def ps_same_day(dt):
    """PlanetScope SuperDove clips of the same date in the PLP.zip mirror (PLP2022 only; open, not used by the detector)."""
    f = sorted({x.name[:23] for x in (ROOT / "data/extra/plp/PLP/PLP2022/PlanetScope/files").glob(f"{dt}_*_SR_8b_harmonized_clip.tif")})
    return f"; PlanetScope того же дня (PLP.zip): {', '.join(f)}" if f else ""


def date_row(camp, d, P):
    dt = d["date"]
    iso = f"{dt[:4]}-{dt[4:6]}-{dt[6:]}"
    t = d["scene_datetime"][11:16]
    sid = d["scene_id"]
    base = dict(id=f"P1-{camp}-{dt}", дата_UTC=f"{iso}, S2 {t} UTC", природный_искусственный="искусственный",
                дрейф_радиус="мишень заякорена/закреплена", независимость_train_test="не в MARIDA; в обучении не использовалась",
                пиксели_детектора_на_месте_счёта=det_text(d, P))
    fr = [r["frac_target"] for r in P if r["frac_target"] is not None]
    items = [r["items_in_pixel"] for r in P if r["items_in_pixel"] is not None]
    cov = (f"доля пикселя под мишенью max {max(fr):.2f}; " if fr else "") + \
          (f"предметов в пикселе (оценка) max {max(items):.0f}" if items else "")
    if camp == "PLP2018":
        base.update(первоисточник_URL_DOI="Topouzelis, Papakonstantinou, Garaba 2019, JAG 79:175, doi 10.1016/j.jag.2019.03.011; ортофото — Zenodo 3752719",
                    лицензия="CC-BY-4.0 (ортофото)", море_река_берег="море (Лесбос, Цамакия)",
                    lat_lon_контур="≈ 39.108N 26.566E; 3 мишени × 4 пикселя — ортофото совмещено с S2 по 3 мишеням (сдвиг, corr 0.51; scripts/count_bridge/p1_plp2018_register.py)",
                    платформа_GSD="DJI S900 + Sony A5100, 100 м, 2.14 см", ссылка_на_кадр="PLP2019_dataset/UAV_photos/UAV_20180607.jpg (без привязки)",
                    полевой_метод="мишени известного состава (A*: число предметов известно)", N="3 600 бутылок PET 1.5 л + 138 пакетов LDPE (+ сети)",
                    материал_мин_размер="PET 1.5 л, LDPE", площадь_A="3 мишени по 100 м²", единицы="36 бут./м² внутри мишени",
                    полнота_разметки="полная (конструкция); доля на пиксель — по совмещённому ортофото",
                    сцена_product_id_dt=f"{sid}; мишени на воде 08:00–17:30 UTC (статья), пролёт внутри окна; дрон ≈ полдень (два полёта по 13.5 мин)",
                    визуальное_подтверждение="все 3 мишени видны в B8 S2 (аномалия до 0.05)", класс="A*",
                    можно_нельзя_обучать="[нет | да | нет | да] порог обнаружения", следующий_шаг="П3 и оценка зон: пиксель с ≈ 1 670 бутылками не сработал")
    elif camp == "PLP2019":
        ex = EXIF_2019.get(dt)
        s2h = int(t[:2]) + int(t[3:]) / 60
        dth = (int(ex[:2]) - 3 + int(ex[3:]) / 60 - s2h) if ex else None
        base.update(первоисточник_URL_DOI="Topouzelis et al. 2020, Remote Sens. 12:2013; Zenodo 3752719", лицензия="CC-BY-4.0",
                    море_река_берег="море (Лесбос)", lat_lon_контур="точки пикселей UTM 35N (Vector_Points)",
                    платформа_GSD="Phantom 4 Pro / Sony ILCE-5100, ≈ 3.4–5.4 см", ссылка_на_кадр=f"UAV_photos/UAV_{dt}.JPG",
                    полевой_метод="мишени 5×5 м + доля покрытия по дрону на пиксель S2 (A*)",
                    N="416 бутылок на мишень 5×5 м (16.64 /м²); пакеты — число не опубликовано",
                    материал_мин_размер="PET бутылки, LDPE пакеты, тростник", площадь_A=f"{len(P)} пикселей S2 = {len(P) * 100} м²",
                    единицы=cov, полнота_разметки="доля на пиксель по дрону",
                    сцена_product_id_dt=f"{sid}; Δt дрон−S2 ≈ {dth:+.1f} ч (EXIF {ex} местн., принято UTC+3)" if ex else sid,
                    визуальное_подтверждение="фото дрона той же даты", класс="A*",
                    можно_нельзя_обучать="[нет | да | нет | да] калибровка порога", следующий_шаг="П3: пиксели с N")
    elif camp in ("PLP2021", "PLP2022"):
        wood = [r for r in P if "wood" in str(r["material"])]
        st = STATE_2021.get(dt, "")
        if camp == "PLP2021":
            n = "HDPE-сетка 1 шт. (≈ 616 м²)" + ("; деревянная мишень 342 доски (не пластик)" if wood else "")
            src = "Papageorgiou et al. 2022, Remote Sens. 14:5997; Zenodo 7085112; координаты — PLP.zip (зеркало marinedebrisdetector)"
        else:
            n = "лист PVC 5×5 м + 2 круга HDPE r = 3.5 м (3 объекта)"
            src = "PLP2022 (PLP.zip, marinedebrisdetector; Zenodo 10046182)"
        base.update(первоисточник_URL_DOI=src, лицензия="CC-BY-4.0 (Zenodo); PLP.zip без указания",
                    море_река_берег="море (Лесбос)", lat_lon_контур="полигоны мишеней, labels_plp.csv (EPSG:32635)",
                    платформа_GSD="Phantom 4 RTK", ссылка_на_кадр="", полевой_метод="мишени известной площади (A* покрытие)",
                    N=n, материал_мин_размер="HDPE, дерево" if camp == "PLP2021" else "PVC, HDPE",
                    площадь_A=f"{len(P)} пикселей", единицы=cov, полнота_разметки=f"полигон; состояние: {st}" if st else "полигон",
                    сцена_product_id_dt=f"{sid}; Δt: дрон в день пролёта (время не опубликовано)" + ps_same_day(dt),
                    визуальное_подтверждение="ортофото дрона в записи Zenodo (не качалось)",
                    класс="A* (покрытие)",
                    можно_нельзя_обучать="[нет | да | нет | да] предел доли", следующий_шаг="П3: фон + доля")
    elif camp == "Maathuis2026":
        names = "; ".join(f"{t['name']} ({t['area_m2']} м² по GNSS)" for t in d["targets"]) or "мишеней нет (контроль)"
        pet = any(t["name"] in MA_N for t in d["targets"])
        base.update(первоисточник_URL_DOI="Maathuis, Rußwurm, Bochow, van Emmerik 2026, Mar. Pollut. Bull. 119751; 4TU doi 10.4121/3d24e304-27d2-4980-8a48-2c9ef1973124",
                    лицензия="CC-BY-4.0", море_река_берег="берег реки (Недеррейн, Вагенинген, NL)",
                    lat_lon_контур="Topcon GNSS (EPSG:28992), ≈ 51.9547N 5.653E", платформа_GSD="GNSS + ASD + MAIA S2 (с земли)",
                    ссылка_на_кадр="Field_campaign_data.zip (4TU)", полевой_метод="мишени на берегу, Fieldwork_Notes.xlsx",
                    N=("360 бутылок PET" if pet else ("полотно полиэстера" if d["targets"] else "0 (контроль)")),
                    материал_мин_размер="PET бутылки; полиэстер", площадь_A=names, единицы=cov,
                    полнота_разметки="контур по GNSS; бутылки — плотность × площадь",
                    сцена_product_id_dt=f"{sid}; облачн. сцены {d['cloud']:.0f} %; мишени лежали весь день (Δt = 0)",
                    визуальное_подтверждение="фото и MAIA S2 в записи", класс="A*" if pet else ("A* (покрытие)" if d["targets"] else "контроль"),
                    можно_нельзя_обучать="[нет | да | нет (суша) | да] только спектр суши",
                    следующий_шаг="суша: для морского детектора — только контроль")
    elif camp in ("Fronkova2024", "PLP2020", "PLP2024"):
        cp = [r for r in P if r["pixel_id"].endswith("*")]
        cp = cp[0] if cp else P[len(P) // 2]
        info = {"Fronkova2024": ("Fronkova et al. 2024, Remote Sens. 16:4405 (дрон-гиперспектр: Cefas Data Hub 10.14466/CefasDataHub.145)",
                                 "статья CC-BY; дрон OGL v3", "озеро (Whitlingham Great Broad, Норидж, UK)",
                                 "брезент PE синий 10×10 м (1 объект, 100 м²)", "05–19.08.2021 на воде; дрон 19.08"),
                "PLP2020": ("Kremezi et al. 2021 IEEE Access 9:61955; журнал plp.aegean.gr (пересказ поискового агента)",
                            "—", "море (Лесбос, Цамакия)", "диск HDPE-сетки Ø 7 м (≈ 38 м²) + клетка с россыпью (¼, счёта нет)",
                            "по журналу: развёртывание во время пролёта S2 (16.06); 06.07 — повтор"),
                "PLP2024": ("журнал plp.aegean.gr (пересказ поискового агента); данных нет", "—", "море (Лесбос, залив Геры)",
                            "полосы HDPE-сетки и досок 1.2–2.4 × 25–50 м", "по журналу")}[camp]
        base.update(первоисточник_URL_DOI=info[0], лицензия=info[1], море_река_берег=info[2],
                    lat_lon_контур=f"найдено по аномалии B8: {d.get('located_note', '')}", платформа_GSD="",
                    ссылка_на_кадр="", полевой_метод="мишень известной площади; координаты не опубликованы",
                    N=info[3], материал_мин_размер="", площадь_A=info[3], единицы="доля на пиксель неизвестна",
                    полнота_разметки="3×3 пикс. вокруг найденной аномалии",
                    сцена_product_id_dt=f"{sid}; {info[4]}",
                    визуальное_подтверждение=f"аномалия B8 центрального пикселя {cp['b8_anom']}; место совпадает с источником",
                    класс="A* (покрытие)", можно_нельзя_обучать="[нет | да | нет | да] предел доли",
                    следующий_шаг="координаты у авторов")
    elif camp == "Themistocleous2020":
        c = P[0]
        base.update(первоисточник_URL_DOI="Themistocleous et al. 2020, Remote Sens. 12:2648", лицензия="статья CC-BY; данных нет",
                    море_река_берег="море (Лимасол, у Старого порта)",
                    lat_lon_контур="кандидат ≈ 34.6696N 33.0455E (6 пикс. 36SWD; по рис. 8 и 12 статьи, координат в статье нет)",
                    платформа_GSD="Phantom 4 Pro + Phantom 2 (GoPro)", ссылка_на_кадр="",
                    полевой_метод="мишень из бутылок, дроны во время пролёта", N="1 500 бутылок (0.5 и 1.5 л)",
                    материал_мин_размер="PET 0.5 / 1.5 л", площадь_A="3×10 м = 30 м²", единицы="50 бут./м² внутри мишени",
                    полнота_разметки="конструкция мишени",
                    сцена_product_id_dt=f"{sid}; Δt ≈ 0 (дроны во время пролёта, по статье)",
                    визуальное_подтверждение="кластер B8 0.003–0.009 на фоне 0.0001 в месте рис. 8 — кандидат, не подтверждён",
                    класс="A*", можно_нельзя_обучать="[нет | да | нет | да] только порог; вода L2A обрезана (0.0001)",
                    следующий_шаг="запрос координат GPS-трекера у авторов (ERATOSTHENES)")
    return base


def summary(pix, rows):
    import collections
    print("dates:", len(rows), collections.Counter(r["класс"] for r in rows))
    # markdown table per date (for PAIRS.md / report): out/l127/p1_table.md
    by = collections.defaultdict(list)
    for r in pix:
        by[r["pair_id"]].append(r)
    L = ["| id | класс | N (что посчитано) | пикс. мишени | max доля | max предметов/пикс. | P max | P≥0.63 | FDI max | B8-аном. max | оценивается |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in rows:
        P = by.get(row["id"], [])
        fr = [x["frac_target"] for x in P if x["frac_target"] is not None]
        it = [x["items_in_pixel"] for x in P if x["items_in_pixel"] is not None]
        ba = [x["b8_anom"] for x in P if x["b8_anom"] is not None]
        f = lambda v, k=2: (f"{max(v):.{k}f}" if v else "—")  # noqa: E731
        ev = P[0]["evaluable"] if P else None
        L.append(f"| {row['id']} | {row['класс']} | {row['N']} | {len(P)} | {f(fr)} | {f(it, 0)} | "
                 f"{f([x['p'] for x in P], 3)} | {sum(x['det'] for x in P)} | {f([x['fdi'] for x in P], 3)} | {f(ba, 3)} | "
                 f"{'—' if ev is None else ('да' if ev else 'нет')} |")
    (OUT / "p1_table.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    # per campaign
    C = collections.OrderedDict()
    for row in rows:
        camp = row["id"].split("-")[1]
        c = C.setdefault(camp, dict(dates=[], cls=collections.Counter(), px=0, det=0, ev=0, items=[], pmax=0.0))
        P = by.get(row["id"], [])
        c["dates"].append(row["id"].split("-")[2])
        c["cls"][row["класс"]] += 1
        c["px"] += len(P)
        c["det"] += sum(x["det"] for x in P)
        c["ev"] += int(bool(P and P[0]["evaluable"]))
        c["items"] += [x["items_in_pixel"] for x in P if x["items_in_pixel"] is not None]
        c["pmax"] = max([c["pmax"]] + [x["p"] for x in P])
    L = ["| кампания | дат | класс | даты | пикс. мишеней | оценивается (дат) | P≥0.63 (пикс.) | P max | max предметов/пикс. |",
         "|---|---|---|---|---|---|---|---|---|"]
    for k, c in C.items():
        ds = c["dates"]
        L.append(f"| {k} | {len(ds)} | {', '.join(f'{a} {b}' for a, b in c['cls'].items())} | {ds[0]}–{ds[-1]} | {c['px']} | "
                 f"{c['ev']} | {c['det']} | {c['pmax']:.3f} | {max(c['items']) if c['items'] else '—'} |")
    (OUT / "p1_campaigns.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    trig = [r for r in pix if r["det"]]
    print("triggered pixels:", [(r["pair_id"], r["pixel_id"], r["frac_target"], r["items_in_pixel"], r["items_kind"], r["p"]) for r in trig])


if __name__ == "__main__":
    main()
