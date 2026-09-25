"""L86 — zoomed chips (RGB, FDI, SWIR false colour, P(debris)) of every detector component in scanned drift zones and strips.

  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/search/s3/s3_chips.py

Components = prob >= threshold on valid water (quality code 1) in zones/*/prob.tif (the same rule as pair_quality before
its component filters; components removed there by cloud/shadow filters are flagged kept_by_pair_quality=False by count only).
Output: data/search/s3/chips/<zone>/det_XX.png + detections.csv (lon, lat, px, prob_max, fdi_max, distance to the transect).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from scipy import ndimage
from shapely.geometry import LineString, Point

sys.path.insert(0, str(Path(__file__).resolve().parent))
import s3_pairs as sp  # noqa: E402

pq, ROOT, OUT, stac = sp.pq, sp.ROOT, sp.OUT, sp.stac
HALF = 400  # m, chip half size


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    tr_all = pd.read_csv(ROOT / "data" / "case" / "geometry" / "transects.csv")
    rows = []
    for zd in sorted((OUT / "zones").glob("*")):
        meta = json.loads((zd / "meta.json").read_text(encoding="utf-8"))
        thr = meta["detector"]["threshold"]
        with rasterio.open(zd / "prob.tif") as s:
            prob = s.read(1) / 255.0; T = s.transform; epsg = s.crs.to_epsg()
        with rasterio.open(zd / "quality.tif") as s:
            qa = s.read(1)
        det = (prob >= thr) & (qa == pq.Q_WATER)
        lab, n = ndimage.label(det, structure=np.ones((3, 3), bool))
        item = pq.get_item("planetary-computer", "sentinel-2-l2a", meta["scene_id"])
        to_ll = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
        to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        seg = tr_all[tr_all.event_id == meta["event_id"]]
        line = LineString([to_utm.transform(r.lon_start, r.lat_start) for r in seg.itertuples()] +
                          [to_utm.transform(seg.iloc[-1].lon_end, seg.iloc[-1].lat_end)])
        cd = OUT / "chips" / zd.name
        cd.mkdir(parents=True, exist_ok=True)
        # one read of the whole zone crop (same bounds as the zone scan), chips are cut locally
        full = stac.read_crop(item, epsg, meta["bounds_utm"], workers=4)["bands"]
        R = int(HALF / 10)
        for k, sl in enumerate(ndimage.find_objects(lab), 1):
            comp = lab[sl] == k
            yy, xx = np.nonzero(comp)
            r0, c0 = yy.mean() + sl[0].start, xx.mean() + sl[1].start
            x, y = T * (c0 + 0.5, r0 + 0.5)
            lon, lat = to_ll.transform(x, y)
            ri, ci = int(r0), int(c0)
            bb = full[:, max(ri - R, 0):ri + R, max(ci - R, 0):ci + R]
            fdi = sp.fdi_s2(bb)
            rgb = np.clip(np.nan_to_num(bb[[3, 2, 1]]).transpose(1, 2, 0) / 0.12, 0, 1) ** (1 / 1.8)
            sw = np.dstack([sp.stretch(bb[10], 0, 0.1), sp.stretch(bb[7], 0, 0.1), sp.stretch(bb[3], 0, 0.1)])
            h = bb.shape[1] // 2
            pv = prob[max(int(r0) - 40, 0):int(r0) + 40, max(int(c0) - 40, 0):int(c0) + 40]
            fig, ax = plt.subplots(1, 4, figsize=(14, 3.8), dpi=70)
            for a, im, t in zip(ax, [rgb, sw, fdi, pv], ["RGB (x1.3 stretch)", "SWIR/NIR/red", "FDI", "P(debris)"]):
                a.imshow(im, cmap=None if im.ndim == 3 else "viridis", vmin=None if im.ndim == 3 else 0,
                         vmax=None if im.ndim == 3 else (0.05 if t == "FDI" else 1))
                a.plot([im.shape[1] / 2], [im.shape[0] / 2], "r+", ms=14); a.set_title(t); a.axis("off")
            pm = float(prob[sl][comp].max())
            dist = line.distance(Point(x, y)) / 1000
            fig.suptitle(f"{meta['event_id']} det {k}: {lat:.4f}N {lon:.4f}E, {comp.sum()} px, P max {pm:.2f}, "
                         f"{dist:.1f} km from track", fontsize=10)
            fig.tight_layout(); fig.savefig(cd / f"det_{k:02d}.png"); plt.close(fig)
            rows.append(dict(zone=zd.name, event_id=meta["event_id"], scene_id=meta["scene_id"], det=k, lon=round(lon, 5),
                             lat=round(lat, 5), px=int(comp.sum()), prob_max=round(pm, 3),
                             fdi_center=round(float(np.nanmax(fdi[h - 2:h + 3, h - 2:h + 3])), 4),
                             fdi_chip_water_p50=round(float(np.nanmedian(fdi)), 4),
                             b8_center=round(float(np.nanmax(bb[7][h - 2:h + 3, h - 2:h + 3])), 4),
                             b8_chip_p50=round(float(np.nanmedian(bb[7])), 4),
                             dist_track_km=round(dist, 2), chip=f"chips/{zd.name}/det_{k:02d}.png"))
            print(rows[-1], flush=True)
    pd.DataFrame(rows).to_csv(OUT / "chips" / "detections.csv", index=False)


if __name__ == "__main__":
    main()
