"""L88: weather/cloud CONTEXT for S1 GPGP flight lines from MODIS Terra/Aqua true colour (NASA GIBS WMS, no login).
MODIS 250 m-1 km cannot see >50 cm debris; this only answers "was the sea visible / cloudy around the flight days".
Per image: share of line points (10 km disc) that are cloudy (bright & low-saturation) or no-data (swath gap).
Outputs: quicklooks/modis_<sat>_<date>.jpg (with lines drawn), modis_context.csv
Run: .venv/Scripts/python.exe data/search/s1/s1_modis_context.py
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent
QL = OUT / "quicklooks"; QL.mkdir(exist_ok=True)
CACHE = OUT / "cache"
LAT0, LAT1, LON0, LON1 = 28.5, 35.5, -146.0, -132.5
W, H = 1350, 700  # 0.01 deg / px
WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
LAYERS = {"terra": "MODIS_Terra_CorrectedReflectance_TrueColor", "aqua": "MODIS_Aqua_CorrectedReflectance_TrueColor"}


def fetch(layer: str, date: str) -> Image.Image:
    f = CACHE / f"gibs_{layer}_{date}.jpg"
    if not f.exists():
        p = {"SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.3.0", "LAYERS": layer, "STYLES": "",
             "CRS": "EPSG:4326", "BBOX": f"{LAT0},{LON0},{LAT1},{LON1}", "WIDTH": W, "HEIGHT": H,
             "FORMAT": "image/jpeg", "TIME": date}
        import time
        for k in range(6):  # GIBS resets TLS connections now and then: retry with back-off
            try:
                r = requests.get(WMS, params=p, timeout=120); r.raise_for_status(); break
            except requests.RequestException as e:
                print("gibs retry", layer, date, k, str(e)[:80]); time.sleep(5 * (k + 1))
        else:
            raise RuntimeError(f"GIBS failed {layer} {date}")
        if not r.headers.get("content-type", "").startswith("image"):
            raise RuntimeError(r.text[:300])
        f.write_bytes(r.content)
    return Image.open(f).convert("RGB")


def px(lat, lon):
    return (lon - LON0) / (LON1 - LON0) * W, (LAT1 - lat) / (LAT1 - LAT0) * H


def main() -> None:
    ev = pd.read_csv(OUT / "events.csv")
    rows = []
    for d in pd.date_range("2016-09-30", "2016-10-08"):
        ds = d.strftime("%Y-%m-%d")
        for sat, layer in LAYERS.items():
            im = fetch(layer, ds)
            a = np.asarray(im).astype(float) / 255
            br, sat_ = a.mean(2), a.max(2) - a.min(2)
            cloud = (br > 0.45) & (sat_ < 0.12)
            nod = a.max(2) < 0.02
            dr = ImageDraw.Draw(im)
            for fd, g in ev.groupby("date"):
                pts = [px(r.lat, r.lon) for r in g.itertuples()]
                dr.line(pts, fill=(255, 0, 0) if fd == "2016-10-02" else (255, 200, 0), width=3)
                cl, nd = [], []
                for r in g.itertuples():
                    x, y = px(r.lat, r.lon)
                    yy, xx = np.ogrid[:H, :W]
                    m = (xx - x) ** 2 + (yy - y) ** 2 <= 10 ** 2  # ~10 px ~ 10 km
                    nd.append(nod[m].mean() > 0.5)
                    cl.append(cloud[m & ~nod].mean() if (m & ~nod).any() else np.nan)
                rows.append({"image_date": ds, "sat": sat, "flight_date": fd, "dt_days": (d - pd.Timestamp(fd)).days,
                             "n_points": len(g), "share_nodata": round(float(np.mean(nd)), 2),
                             "cloud_frac_mean": round(float(np.nanmean(cl)), 2) if not np.all(np.isnan(cl)) else None})
            dr.text((8, 8), f"MODIS {sat} {ds} (NASA GIBS) | red: flight 02.10, yellow: flight 06.10", fill=(255, 255, 255))
            im.save(QL / f"modis_{sat}_{ds}.jpg", quality=80)
    df = pd.DataFrame(rows); df.to_csv(OUT / "modis_context.csv", index=False)
    print(df[df.dt_days.abs() <= 2].to_string())


if __name__ == "__main__":
    main()
