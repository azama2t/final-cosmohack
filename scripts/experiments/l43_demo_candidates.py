"""L43: RGB crops (1.5 km) of MDD zones #1-3 on the latest reliable date for the top-N reliable regions.

Usage: PYTHONPATH=src python scripts/experiments/l43_demo_candidates.py [--top 6] [--data service/data]
Writes reports/figures/demo_candidates_<region>.png (3 tiles: raw RGB | the same with outlines) and
reports/tmp_l43/demo_candidates.json (zones, rating). Outlines: coral = counted detection, grey = artefact
(seam / wake / ship), yellow = the H3 cell of the zone. Stretch per tile (1-99.5 % of the crop).
Reliable rating = site rule (make_demo.date_reasons on the latest date), by summary index_permille.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import h3
import numpy as np
import rasterio
import rasterio.features
import rasterio.windows
from scipy import ndimage
from PIL import Image, ImageDraw
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from make_demo import date_reasons  # noqa: E402

HALF = 75  # 150 px = 1.5 km
UP = 3


def rj(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--data", default=str(ROOT / "service/data"))
    ap.add_argument("--zones", type=int, default=3)
    a = ap.parse_args()
    data = Path(a.data)
    man = rj(data / "manifest.json")
    ranked = sorted(man["regions"], key=lambda r: -((r.get("summary") or {}).get("index_permille") or 0.0))
    ok = [r for r in ranked if r["dates"] and not date_reasons(data, r["id"], r["dates"][-1])]
    out = []
    for r in ok[:a.top]:
        d = r["dates"][-1]
        date = d["date"]
        zf = rj(data / r["id"] / date / "mdd" / "zones.json")
        dets = rj(data / r["id"] / date / "mdd" / "detections.geojson")["features"]
        sdir = ROOT / "data/live" / r["id"] / date
        with rasterio.open(sdir / "bands.tif") as ds:
            names = list(ds.descriptions)
            crs, T = ds.crs, ds.transform
            H, W = ds.shape
            rgb_all = None
        tr = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        tiles = []
        zrows = []
        for z in zf["zones"][:a.zones]:
            x, y = tr.transform(z["lon"], z["lat"])
            c, rr = ~T * (x, y)
            c, rr = int(round(c)), int(round(rr))
            r0, c0 = min(max(rr - HALF, 0), H - 2 * HALF), min(max(c - HALF, 0), W - 2 * HALF)
            with rasterio.open(sdir / "bands.tif") as ds:
                win = rasterio.windows.Window(c0, r0, 2 * HALF, 2 * HALF)
                v = np.stack([ds.read(names.index(b) + 1, window=win) for b in ("B4", "B3", "B2")], -1).astype(np.float32)
            v = np.nan_to_num(v)
            lo, hi = np.percentile(v, 1), np.percentile(v, 99.5)
            im = np.clip((v - lo) / max(hi - lo, 1e-4) * 255, 0, 255).astype(np.uint8)
            raw = Image.fromarray(im).resize((2 * HALF * UP, 2 * HALF * UP), Image.NEAREST)
            ov = raw.copy()
            dr = ImageDraw.Draw(ov)

            def to_px(lon, lat):
                X, Y = tr.transform(lon, lat)
                cc, r_ = ~T * (X, Y)
                return ((cc - c0) * UP, (r_ - r0) * UP)
            cell = h3.cell_to_boundary(z["h3"])
            dr.polygon([to_px(lon, lat) for lat, lon in cell], outline=(255, 220, 0))
            n_art = 0
            wt = rasterio.windows.transform(win, T)
            arr = np.array(ov)
            for f in dets:
                g = f["geometry"]
                polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
                gu = {"type": "MultiPolygon", "coordinates": [[[list(tr.transform(x, y)) for x, y in ring] for ring in poly]
                                                              for poly in polys]}
                mk = rasterio.features.rasterize([(gu, 1)], out_shape=(2 * HALF, 2 * HALF), transform=wt,
                                                 fill=0, dtype="uint8").astype(bool)
                if not mk.any():
                    continue
                art = f["properties"].get("artifact")
                n_art += bool(art)
                big = np.kron(mk, np.ones((UP, UP), bool))
                edge = big & ~ndimage.binary_erosion(big)
                arr[edge] = (150, 150, 150) if art else (255, 90, 70)
            ov = Image.fromarray(arr)
            dr = ImageDraw.Draw(ov)
            dr.polygon([to_px(lon, lat) for lat, lon in cell], outline=(255, 220, 0))
            dr.text((6, 6), f"#{z['rank']} {z['h3']} {z['flagged_water_px']} px x{z.get('n_confirmed', 0)} conf", fill=(255, 255, 255))
            tiles.append((raw, ov))
            zrows.append(dict(rank=z["rank"], h3=z["h3"], px=z["flagged_water_px"], n_det=z["n_detections"],
                              n_confirmed=z.get("n_confirmed", 0), score=z["score"], repeat=z["repeat_dates"],
                              lon=z["lon"], lat=z["lat"], artefacts_in_crop=n_art))
        S = 2 * HALF * UP
        sheet = Image.new("RGB", (2 * S + 12, len(tiles) * (S + 8) + 24), (15, 20, 30))
        drs = ImageDraw.Draw(sheet)
        drs.text((6, 4), f"{r['id']} {date} MDD, zones #1-{len(tiles)}; 1.5 km; coral = counted, grey = artefact, "
                         f"yellow = H3 cell", fill=(230, 230, 230))
        for i, (raw, ov) in enumerate(tiles):
            sheet.paste(raw, (0, 24 + i * (S + 8)))
            sheet.paste(ov, (S + 12, 24 + i * (S + 8)))
        fp = ROOT / "reports/figures" / f"demo_candidates_{r['id']}.png"
        sheet.save(fp, optimize=True)
        out.append(dict(region=r["id"], name=r["name"], date=date, index=(r.get("summary") or {}).get("index_permille"),
                        drift=bool(d.get("drift")), zones=zrows, figure=str(fp.relative_to(ROOT))))
        print(fp, [z["h3"] for z in zrows])
    (ROOT / "reports/tmp_l43/demo_candidates.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
