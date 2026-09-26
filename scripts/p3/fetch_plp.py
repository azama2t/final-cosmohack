"""П3 (L129): S2 L2A вырезки того же дня над мишенями A* (PLP2019) с КАНАЛАМИ (L115 сохранил только P и маски).

  CUDA_VISIBLE_DEVICES=-1 .venv/Scripts/python.exe scripts/p3/fetch_plp.py [--force]

Источник сцены — то же правило, что у L115/живого контура (scripts/fetch_live.pick_item). Вырезка ±1200 м от центра
мишеней, границы привязаны к сетке 60 м (сетка тайла), 12 каналов + SCL, 10 м (20/60 м — nearest, как в продукте).
Мишени и P детектора (weights/lgbm, 0.63) — из out/l115/plp/<дата>/meta.json. Контроль: P детектора пересчитывается на
нашей вырезке и сверяется с L115 в тех же пикселях.
Выход: out/p3/plp/<дата>.npz (bands, scl, transform, prob), out/p3/plp/targets.csv.
Если есть docs/research/pairs/p1_pixels.csv (L127) — даты оттуда добавляются тем же путём (колонки date, x, y, epsg,
tile, items или bottles_fraction; см. load_p1()).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from macroplastic.live import stac  # noqa: E402

OUT = ROOT / "out" / "p3" / "plp"
L115 = ROOT / "out" / "l115" / "plp"
DATES_2019 = ["20190418", "20190503", "20190518", "20190528", "20190607"]
HALF = 1200.0
A_STAR_P1 = ["Maathuis2026"]  # A* с числом предметов на пиксель (docs/research/pairs/PAIRS.md: A* = PLP + Maathuis)


def targets_l115() -> pd.DataFrame:
    rows = []
    for d in DATES_2019:
        m = json.loads((L115 / d / "meta.json").read_text(encoding="utf-8"))
        for t in m["targets"]:
            rows.append(dict(date=d, campaign="PLP2019", epsg=32635, tile="35SMD", target=t["target"], pixel=t["pixel"],
                             x=t["x"], y=t["y"], frac_bottles=t["frac_bottles"], frac_bags=t["frac_bags"],
                             frac_reeds=t.get("frac_reeds", 0.0), frac_plastic=t["frac_plastic"], material=t["material"],
                             p_l115=t["p"], q_l115=t["q"], scene_l115=m["scene_id"]))
    return pd.DataFrame(rows)


def load_p1() -> pd.DataFrame | None:
    f = ROOT / "docs" / "research" / "pairs" / "p1_pixels.csv"
    if not f.exists():
        return None
    t = pd.read_csv(f)
    need = {"date", "x", "y"}
    if not need <= set(t.columns):
        print("p1_pixels.csv: нет колонок", need - set(t.columns))
        return None
    return t


def fetch(date, tile, epsg, cx, cy, force=False):
    from pyproj import Transformer
    from fetch_live import pick_item
    f = OUT / f"{date}.npz"
    if f.exists() and not force:
        return f
    lon, lat = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True).transform(cx, cy)
    d = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    item = None
    for a in range(4):
        try:
            item = pick_item(d, tile, lon, lat)
            break
        except Exception as e:  # noqa: BLE001
            print("search retry", date, e, flush=True)
            time.sleep(3 * (a + 1))
    if item is None:
        raise RuntimeError(f"no item {date}")
    w = np.floor((cx - HALF) / 60) * 60
    n = np.ceil((cy + HALF) / 60) * 60
    b = (w, n - 2 * HALF, w + 2 * HALF, n)
    crop = stac.read_crop(item, epsg, b)
    from macroplastic.models.lgbm_predict import load_predictor
    pred = load_predictor(ROOT / "weights" / "lgbm")
    prob = pred.predict_proba(crop["bands"], stac.BANDS)
    tf = crop["transform"]
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(f, bands=crop["bands"], scl=crop["scl"], prob=prob.astype(np.float32),
                        transform=np.array([tf.a, tf.b, tf.c, tf.d, tf.e, tf.f]), item_id=item.id, epsg=epsg,
                        baseline=str(item.properties.get("s2:processing_baseline")))
    print(date, item.id, crop["bands"].shape, f"{crop['read_s']} s", flush=True)
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    T = targets_l115()
    p1 = load_p1()
    if p1 is not None:
        print("p1_pixels.csv:", len(p1), "строк;", sorted(set(p1.date.astype(str))))
    rows = []
    for d, g in T.groupby("date"):
        f = fetch(d, g.tile.iloc[0], int(g.epsg.iloc[0]), g.x.mean(), g.y.mean(), a.force)
        z = np.load(f)
        tf = z["transform"]
        for r in g.itertuples():
            c = int(np.floor((r.x - tf[2]) / tf[0]))
            rr = int(np.floor((r.y - tf[5]) / tf[4]))
            rows.append(dict(r._asdict(), row=rr, col=c, p_ours=float(z["prob"][rr, c]), item_id=str(z["item_id"])))
    out = pd.DataFrame(rows).drop(columns=["Index"])
    out.to_csv(OUT / "targets.csv", index=False)
    # даты A* от L127 с числом предметов на пиксель (кроме PLP2019 — он уже выше): PLP2021 — уровень B (сетка/доски,
    # не A*), PLP2018 и Themistocleous — без числа на пиксель → в MAE не входят (configs/p3_synth.yaml check.test_dates)
    if p1 is not None:
        q = p1[(p1.campaign != "PLP2019") & p1.items_in_pixel.notna() & p1.campaign.isin(A_STAR_P1)].copy()
        q["date"] = q.date.astype(str)
        q["tile"] = q.scene_id.str.extract(r"_T(\d\d[A-Z]{3})_")[0].fillna(q.scene_id.str.extract(r"_(\d\d[A-Z]{3})_")[0])
        r2 = []
        for d, g in q.groupby("date"):
            f = fetch(d, g.tile.iloc[0], int(g.epsg.iloc[0]), g.x.mean(), g.y.mean(), a.force)
            z = np.load(f)
            tf = z["transform"]
            for r in g.itertuples():
                c = int(np.floor((r.x - tf[2]) / tf[0]))
                rr = int(np.floor((r.y - tf[5]) / tf[4]))
                r2.append(dict(date=d, campaign=r.campaign, epsg=r.epsg, tile=r.tile, target=r.target, pixel=r.pixel_id,
                               x=r.x, y=r.y, frac_bottles=np.nan, frac_bags=0.0, frac_reeds=0.0,
                               frac_plastic=r.frac_target, material=r.material, items=r.items_in_pixel,
                               p_l115=r.p, q_l115=r.q, surface=r.surface, scene_l115=r.scene_id, row=rr, col=c,
                               p_ours=float(z["prob"][rr, c]), item_id=str(z["item_id"])))
        o2 = pd.DataFrame(r2)
        o2.to_csv(OUT / "targets_p1.csv", index=False)
        print("p1 dates", sorted(set(o2.date)), "pixels", len(o2), "max |P ours - P L127|",
              round(float((o2.p_ours - o2.p_l115).abs().max()), 4))
    dp = (out.p_ours - out.p_l115).abs()
    print("P ours vs L115: max |dP|", round(float(dp.max()), 4), "; det ours", int((out.p_ours >= 0.63).sum()),
          "det L115", int((out.p_l115 >= 0.63).sum()))


if __name__ == "__main__":
    main()
