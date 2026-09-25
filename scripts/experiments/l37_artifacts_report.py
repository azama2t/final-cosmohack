"""Artefact filter: before/after tables and RGB figures for the linear-artefact filter (reports/artifacts.md).

Usage: python scripts/experiments/l37_artifacts_report.py --before reports/tmp_l37/zones1_before.json
       [--survey reports/tmp_l37/survey.csv] [--data service/data] [--live data/live]
Before = zones.json top-1 of the previous build (saved before the rebuild); after = current service/data.
Figures: reports/figures/artifacts_<region>.png - RGB crop 2.4 x 2.4 km around the old zone #1 (left: all detections
in red = before; right: after - artefacts in white (seam) / yellow (wake) / magenta (ship), kept detections in red,
H3 cell of the old zone #1 dashed white, new zone #1 cell solid green if inside the crop).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import h3
import numpy as np
import rasterio
from PIL import Image, ImageDraw, ImageFont
from pyproj import Transformer
from rasterio import features
from rasterio.windows import Window
from shapely.geometry import shape
from shapely.ops import transform as stf

ROOT = Path(__file__).resolve().parents[2]
COL = {None: (255, 70, 60), "seam": (255, 255, 255), "wake": (255, 220, 0), "ship": (255, 0, 255)}


def rj(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def fig(region, date, model, old_h3, new_h3, data: Path, live: Path, out: Path, half=120, scale=3):
    b = rasterio.open(live / region / date / "bands.tif")
    names = list(b.descriptions)
    lat, lon = h3.cell_to_latlng(old_h3)
    to = Transformer.from_crs(4326, b.crs, always_xy=True)
    x, y = to.transform(lon, lat)
    r, c = b.index(x, y)
    win = Window(c - half, r - half, 2 * half, 2 * half)
    v = np.stack([b.read(names.index(k) + 1, window=win, boundless=True, fill_value=0) for k in ("B4", "B3", "B2")], -1)
    v = np.nan_to_num(v)
    lo, hi = np.percentile(v, 1), np.percentile(v, 99.5)
    img = np.clip((v - lo) / max(hi - lo, 1e-4) * 255, 0, 255).astype(np.uint8)
    wt = b.window_transform(win)
    fc = rj(data / region / date / model / "detections.geojson")
    shapes = {}
    for f in fc["features"]:
        g = stf(to.transform, shape(f["geometry"]))
        shapes.setdefault(f["properties"].get("artifact"), []).append(g)
    before, after = img.copy(), img.copy()
    for a, gs in shapes.items():
        m = features.rasterize([(g, 1) for g in gs], out_shape=img.shape[:2], transform=wt, fill=0, dtype="uint8") > 0
        if not m.any():
            continue
        from scipy import ndimage
        e = m & ~ndimage.binary_erosion(m)
        before[e] = COL[None]
        after[e] = COL[a]
    W = 2 * half * scale
    im = Image.new("RGB", (2 * W + 10, W + 28), (15, 20, 30))
    for i, arr in enumerate((before, after)):
        im.paste(Image.fromarray(arr).resize((W, W), Image.NEAREST), (i * (W + 10), 28))
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("arial.ttf", 13)
    except OSError:
        font = None

    def cell_px(cell):
        pts = []
        for la, lo_ in h3.cell_to_boundary(cell):
            xx, yy = to.transform(lo_, la)
            rr, cc = rasterio.transform.rowcol(wt, xx, yy)
            pts.append((cc * scale, rr * scale + 28))
        return pts
    for i in range(2):
        off = i * (W + 10)
        pts = [(px + off, py) for px, py in cell_px(old_h3)]
        d.polygon(pts, outline=(200, 200, 200))
        if i == 1 and new_h3 and new_h3 != old_h3:
            p2 = [(px + off, py) for px, py in cell_px(new_h3)]
            if all(off <= px <= off + W and 28 <= py <= W + 28 for px, py in p2):
                d.polygon(p2, outline=(60, 255, 90))
    d.text((6, 6), f"{region} {date} {model}: до (все пятна красным)", fill=(255, 255, 255), font=font)
    d.text((W + 16, 6), "после: шов - белый, кильватер - жёлтый, судно - пурпурный, остальные - красный; серый шестиугольник - старая зона №1, зелёный - новая",
           fill=(255, 255, 255), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, optimize=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", default="reports/tmp_l37/zones1_before.json")
    ap.add_argument("--survey", default="reports/tmp_l37/survey.csv")
    ap.add_argument("--data", default="service/data")
    ap.add_argument("--live", default="data/live")
    ap.add_argument("--regions", default="guanabara,manila,mumbai,honduras")
    ap.add_argument("--out", default="reports/figures")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    data, live = ROOT / a.data, ROOT / a.live
    before = {(r[0], r[1], r[2]): r for r in rj(ROOT / a.before)}
    man = rj(data / "manifest.json")
    regs = {r["id"]: r for r in man["regions"]}
    # per-region totals after
    print("## Итог по всем сценам (после)")
    tot = {}
    for reg in man["regions"]:
        for d in reg["dates"]:
            for m in d["models"]:
                fc = rj(data / reg["id"] / d["date"] / m / "detections.geojson")
                t = tot.setdefault(m, {"n": 0, "px": 0, "seam": [0, 0], "wake": [0, 0], "ship": [0, 0], "conf": 0,
                                       "conf_art": 0})
                for f in fc["features"]:
                    p = f["properties"]
                    t["n"] += 1
                    t["px"] += p.get("n_px", 0)
                    art = p.get("artifact")
                    if art:
                        t[art][0] += 1
                        t[art][1] += p.get("n_px", 0)
                    if p.get("confirmed"):
                        t["conf"] += 1
                        t["conf_art"] += bool(art)
    for m, t in tot.items():
        na = sum(t[k][0] for k in ("seam", "wake", "ship"))
        pa = sum(t[k][1] for k in ("seam", "wake", "ship"))
        print(f"- {m}: объектов {t['n']}, помечено {na} ({100 * na / max(t['n'], 1):.1f} %), пикселей {pa} из {t['px']} "
              f"({100 * pa / max(t['px'], 1):.1f} %); seam {t['seam']}, wake {t['wake']}, ship {t['ship']} "
              f"([объектов, px]); подтверждённых второй моделью {t['conf']}, из них помечено {t['conf_art']}")
    print("\n## Зоны №1: до → после (все даты, выбранные районы)")
    print("| район | дата | модель | дымка | №1 до (px, балл) | №1 после (px, балл, согласие) | сменилась |")
    print("|---|---|---|---|---|---|---|")
    changed = total = 0
    for reg in man["regions"]:
        for d in reg["dates"]:
            for m in d["models"]:
                z = rj(data / reg["id"] / d["date"] / m / "zones.json")
                z1 = z["zones"][0] if z["zones"] else None
                b = before.get((reg["id"], d["date"], m))
                total += 1
                ch = (b[4] if b else None) != (z1["h3"] if z1 else None)
                changed += ch
                if reg["id"] in a.regions.split(","):
                    print(f"| {reg['id']} | {d['date']} | {m} | {'да' if z['quality']['haze'] else ''} | "
                          f"{b[4] + f' ({b[5]}, {b[6]:.1f})' if b else '—'} | "
                          f"{z1['h3'] + f' ({z1[chr(102) + 'lagged_water_px']}, {z1['score']:.1f}, ×{z1['score_terms']['agreement']['value']:.2f})' if z1 else '—'} | "
                          f"{'да' if ch else ''} |")
    print(f"\nЗона №1 сменилась на {changed} из {total} пар (район, дата, модель) по всем 18 районам.")
    for rid in a.regions.split(","):
        reg = regs.get(rid)
        if not reg:
            continue
        dd = reg["default_date"]
        m = "mdd"
        b = before.get((rid, dd, m))
        z = rj(data / rid / dd / m / "zones.json")["zones"]
        if not b:
            continue
        fig(rid, dd, m, b[4], z[0]["h3"] if z else None, data, live, ROOT / a.out / f"artifacts_{rid}.png")
        print(f"figure: {a.out}/artifacts_{rid}.png ({dd}, old #1 {b[4]}, new #1 {z[0]['h3'] if z else None})")


if __name__ == "__main__":
    main()
