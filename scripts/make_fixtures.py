"""Generate valid fake service data files (docs/CONTRACTS.md) in seconds.

Usage: python scripts/make_fixtures.py [--out service/demo_fixtures] [--seed 0]
"""
import argparse
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import h3
except ImportError:  # fixtures still work without h3: fake hex ids on a lat/lon grid
    h3 = None

REGIONS = [
    {"id": "honduras", "name": "Гондурасский залив", "country": "Гондурас", "tile": "16PCC",
     "center": [-88.25, 16.05], "dates": ["2025-09-14", "2025-10-19", "2026-03-02"]},
    {"id": "durban", "name": "Дурбан", "country": "ЮАР", "tile": "36JUN",
     "center": [31.08, -29.88], "dates": ["2025-11-07", "2026-04-21"]},
]
MODELS = {
    "mdd": {"name": "marinedebrisdetector (UNet++)", "threshold": 0.5,
            "url": "https://github.com/MarcCoru/marinedebrisdetector", "license": "MIT"},
    "lgbm": {"name": "LightGBM (наша, MARIDA)", "threshold": 0.5,
             "note": "обучена на ACOLITE rhorc; на L2A — сдвиг домена"},
}
HALF_DEG = 0.11  # ~25 km box
SIZE = 512


def bounds_of(center):
    lon, lat = center
    dlon = HALF_DEG / max(math.cos(math.radians(lat)), 0.2)
    return [round(lon - dlon, 6), round(lat - HALF_DEG, 6), round(lon + dlon, 6), round(lat + HALF_DEG, 6)]


def px_to_lonlat(b, x, y, w, h):
    return [b[0] + (b[2] - b[0]) * x / w, b[3] - (b[3] - b[1]) * y / h]


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def make_rgb(rng):
    base = np.zeros((SIZE, SIZE, 3), np.float32)
    base[..., 0], base[..., 1], base[..., 2] = 12, 38, 62
    noise = rng.normal(0, 4, (SIZE, SIZE, 1))
    img = np.clip(base + noise, 0, 255).astype(np.uint8)
    img[:40, :90] = (90, 80, 60)  # a bit of "land"
    return img


def make_prob(rng, n_blobs):
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    prob = np.zeros((SIZE, SIZE), np.float32)
    blobs = []
    for _ in range(n_blobs):
        cx, cy = rng.uniform(60, SIZE - 60), rng.uniform(60, SIZE - 60)
        sx, sy = rng.uniform(3, 14), rng.uniform(2, 6)
        amp = rng.uniform(0.55, 0.98)
        prob = np.maximum(prob, amp * np.exp(-(((xx - cx) / sx) ** 2 + ((yy - cy) / sy) ** 2)))
        blobs.append((cx, cy, sx, sy, amp))
    return prob, blobs


def prob_png(prob):
    rgba = np.zeros((SIZE, SIZE, 4), np.uint8)
    rgba[..., 0] = 255
    rgba[..., 1] = (80 + 150 * prob).astype(np.uint8)
    rgba[..., 2] = 40
    rgba[..., 3] = np.where(prob < 0.05, 0, (60 + 195 * prob)).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def ellipse(b, cx, cy, sx, sy):
    ring = [px_to_lonlat(b, cx + sx * math.cos(t), cy + sy * math.sin(t), SIZE, SIZE)
            for t in np.linspace(0, 2 * math.pi, 13)]
    ring[-1] = ring[0]
    return [[[round(p[0], 6), round(p[1], 6)] for p in ring]]


def hex_cells(b):
    if h3 is not None:
        poly = h3.LatLngPoly([(b[1], b[0]), (b[1], b[2]), (b[3], b[2]), (b[3], b[0])])
        cells = list(h3.polygon_to_cells(poly, 8))
        return [(c, [[lng, lat] for lat, lng in h3.cell_to_boundary(c)], h3.cell_to_latlng(c)[::-1]) for c in cells]
    out, step = [], 0.01
    for i, lat in enumerate(np.arange(b[1], b[3], step)):
        for j, lon in enumerate(np.arange(b[0], b[2], step)):
            ring = [[lon + step / 2 * math.cos(a), lat + step / 2 * math.sin(a)] for a in np.linspace(0, 2 * math.pi, 7)[:-1]]
            out.append((f"fake8{i:03d}{j:03d}", ring, (lon, lat)))
    return out


def build(out: Path, seed: int):
    rng = np.random.default_rng(seed)
    random.seed(seed)
    manifest_regions = []
    for reg in REGIONS:
        b = bounds_of(reg["center"])
        dates_meta, ts = [], []
        for di, date in enumerate(reg["dates"]):
            ddir = out / reg["id"] / date
            ddir.mkdir(parents=True, exist_ok=True)
            Image.fromarray(make_rgb(rng)).save(ddir / "rgb.png")
            cloud = round(float(rng.uniform(0, 0.25)), 3)
            write_json(ddir / "rgb.json", {"bounds": b, "width": SIZE, "height": SIZE, "crs": "EPSG:32616",
                                           "scene_id": f"S2B_{reg['tile']}_{date.replace('-', '')}_0_L2A", "cloud_frac": cloud})
            for model, mm in MODELS.items():
                mdir = ddir / model
                mdir.mkdir(exist_ok=True)
                prob, blobs = make_prob(rng, int(rng.integers(4, 12)))
                prob_png(prob).save(mdir / "prob.png")
                Image.fromarray((prob * 255).astype(np.uint8)).save(mdir / "prob.tif")  # fixture: no CRS
                feats = []
                for k, (cx, cy, sx, sy, amp) in enumerate(blobs):
                    area = math.pi * sx * sy * 100.0 * 0.5
                    feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": ellipse(b, cx, cy, sx * .8, sy * .8)},
                                  "properties": {"id": f"{reg['id']}_{date}_{model}_{k}", "region": reg["id"], "date": date,
                                                 "area_m2": round(area, 1), "mean_prob": round(amp * 0.7, 3),
                                                 "max_prob": round(amp, 3), "model": model}})
                write_json(mdir / "detections.geojson", {"type": "FeatureCollection", "features": feats})
                cells, hfeats = hex_cells(b), []
                for cid, ring, (clon, clat) in cells:
                    obs_frac = float(np.clip(rng.normal(0.85, 0.2), 0, 1))
                    observed = int(7000 * obs_frac)
                    near = [f for f in feats if abs(f["geometry"]["coordinates"][0][0][0] - clon) < 0.012
                            and abs(f["geometry"]["coordinates"][0][0][1] - clat) < 0.012]
                    flagged = int(sum(f["properties"]["area_m2"] / 100 for f in near)) + int(rng.poisson(0.3))
                    share = round(1000 * flagged / observed, 3) if obs_frac >= 0.5 and observed else None
                    ring = ring + [ring[0]]
                    hfeats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                                   "properties": {"h3": cid, "res": 8, "date": date, "flagged_water_px": flagged,
                                                  "observed_water_px": observed, "observed_frac": round(obs_frac, 3),
                                                  "share_permille": share, "n_detections": len(near),
                                                  "threshold": mm["threshold"], "model": model}})
                write_json(mdir / "h3.geojson", {"type": "FeatureCollection", "features": hfeats})
                top = sorted((f for f in hfeats if f["properties"]["share_permille"]),
                             key=lambda f: -f["properties"]["flagged_water_px"])[:8]
                zones = []
                for r, f in enumerate(top, 1):
                    ring = f["geometry"]["coordinates"][0]
                    zones.append({"rank": r, "h3": f["properties"]["h3"], "index": f["properties"]["share_permille"],
                                  "area_m2": f["properties"]["flagged_water_px"] * 100.0,
                                  "reason": f"{f['properties']['n_detections']} пятен, повторяется на {1 + (r % 3)} датах",
                                  "lon": round(sum(p[0] for p in ring[:-1]) / (len(ring) - 1), 6),
                                  "lat": round(sum(p[1] for p in ring[:-1]) / (len(ring) - 1), 6),
                                  "repeat_dates": 1 + (r % 3), "mean_prob": round(float(rng.uniform(0.5, 0.9)), 3)})
                write_json(mdir / "zones.json", {"region": reg["id"], "date": date, "model": model,
                                                 "threshold": mm["threshold"], "zones": zones})
                valid = [f["properties"]["share_permille"] for f in hfeats if f["properties"]["share_permille"] is not None]
                ts.append({"date": date, "model": model,
                           "total_debris_area_m2": round(sum(f["properties"]["area_m2"] for f in feats), 1),
                           "mean_index": round(float(np.mean(valid)) if valid else 0.0, 4),
                           "n_detections": len(feats), "cloud_frac": cloud})
            drift = None
            if di == len(reg["dates"]) - 1:
                parts = []
                for pid in range(60):
                    lon, lat = px_to_lonlat(b, rng.uniform(150, 350), rng.uniform(150, 350), SIZE, SIZE)
                    path, u, v = [], rng.normal(0.004, 0.001), rng.normal(0.002, 0.001)
                    for t in range(73):
                        path.append([round(lon, 6), round(lat, 6), t])
                        lon += u + rng.normal(0, 0.0008)
                        lat += v + rng.normal(0, 0.0008)
                    parts.append({"id": pid, "path": path})
                write_json(ddir / "drift.json", {"region": reg["id"], "date": date, "start_time": f"{date}T10:00:00Z",
                                                 "hours": list(range(73)), "particles": parts,
                                                 "forcing": {"currents": "fixture", "wind": "fixture", "wind_drift_factor": 0.02,
                                                             "model": "OpenDrift OceanDrift"},
                                                 "note": "демонстрационный прогноз, без валидации (фикстура)"})
                drift = f"{reg['id']}/{date}/drift.json"
            dates_meta.append({"date": date, "scene_id": f"S2B_{reg['tile']}_{date.replace('-', '')}_0_L2A",
                               "source": "fixture", "cloud_frac": cloud, "bounds": b,
                               "rgb": f"{reg['id']}/{date}/rgb.png", "models": list(MODELS), "drift": drift})
        ts.sort(key=lambda r: (r["date"], r["model"]))
        write_json(out / reg["id"] / "timeseries.json", ts)
        last = [r for r in ts if r["date"] == reg["dates"][-1] and r["model"] == "mdd"][0]
        manifest_regions.append({"id": reg["id"], "name": reg["name"], "country": reg["country"], "tile": reg["tile"],
                                 "center": reg["center"], "bounds": b, "zoom": 11,
                                 "summary": {"latest_date": reg["dates"][-1], "index_permille": last["mean_index"],
                                             "n_detections": last["n_detections"],
                                             "total_debris_area_m2": last["total_debris_area_m2"]},
                                 "dates": dates_meta})
    write_json(out / "manifest.json", {
        "version": 1, "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": "fixture",
        "index": {"name": "доля наблюдаемой воды с признаками мусора", "unit": "‰", "h3_res": 8,
                  "formula": "flagged_water_px / observed_water_px", "note": "индекс по снимку, не масса пластика"},
        "models": MODELS, "regions": manifest_regions,
        "sources": [{"name": "ФИКСТУРА — синтетические данные", "url": "", "license": "—"}]})
    print(f"fixtures written to {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="service/demo_fixtures")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    build(Path(a.out), a.seed)
