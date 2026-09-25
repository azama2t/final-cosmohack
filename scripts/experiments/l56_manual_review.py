"""L56: crops for the manual review of top findings / zones (reports/manual_review.md).

Usage: python scripts/experiments/l56_manual_review.py [--data service/data] [--out reports/manual_review]
        [--targets honduras:2026-05-30,guanabara:2026-07-16,...] [--top-det 5] [--top-zones 3]

Per target region/date and model: top zones (zones.json rank) and top findings (as in the v2 list: priority = rank
of the zone the centre falls in, then area) -> <out>/<region>_<date>_<model>_{z|d}<n>.png = RGB | SWIR (B11/B8/B4)
crops 1.5 km around the object (service.place.render_crop, detection contours drawn; grey = artefact).
Prints a table (id, lon, lat, area, max_prob, confirmed, artifact) for the review notes.
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from service import core, place  # noqa: E402

TARGETS = "honduras:2026-05-30,guanabara:2026-07-16,mumbai:2026-01-01,lagos:2025-11-10"


def pair(st, rid, date, lon, lat, model, size_m, cell=None) -> Image.Image:
    ims = []
    for b in ("rgb", "swir"):
        try:
            ims.append(Image.open(io.BytesIO(place.render_crop(st, rid, date, lon, lat, size_m, True, b, None, 420,
                                                               "auto", cell))).convert("RGB"))
        except Exception as e:  # noqa: BLE001
            print(f"  crop {b} failed: {e}")
    w = sum(i.width for i in ims) + 6 * (len(ims) - 1)
    out = Image.new("RGB", (w, max(i.height for i in ims)), (20, 20, 20))
    x = 0
    for i in ims:
        out.paste(i, (x, 0))
        x += i.width + 6
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="service/data")
    ap.add_argument("--out", default="reports/manual_review")
    ap.add_argument("--targets", default=TARGETS)
    ap.add_argument("--top-det", type=int, default=5)
    ap.add_argument("--top-zones", type=int, default=3)
    ap.add_argument("--size-m", type=float, default=1500)
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    st = core.Store(ROOT / a.data)
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    h3 = place._h3()
    for t in a.targets.split(","):
        rid, date = t.split(":")
        d = st.date_entry(rid, date)
        for m in d.get("models") or []:
            zones = (st._optional(rid, date, m, "zones") or {}).get("zones") or []
            rank = {z["h3"]: z["rank"] for z in zones}
            feats = place._features(st._optional(rid, date, m, "detections"))
            real = [f for f in feats if not core.artifact_of(f["properties"])]

            def prio(f):
                lon, lat = place.det_lonlat(f)
                return rank.get(h3.latlng_to_cell(lat, lon, 8), 99)

            real.sort(key=lambda f: (prio(f), -f["properties"].get("area_m2", 0)))
            print(f"\n== {rid} {date} {m}: {len(feats)} det ({len(feats) - len(real)} artefacts), {len(zones)} zones")
            for z in zones[:a.top_zones]:
                name = f"{rid}_{date}_{m}_z{z['rank']}.png"
                pair(st, rid, date, z["lon"], z["lat"], m, a.size_m, z["h3"]).save(out / name)
                print(f"  zone #{z['rank']} {z['h3']} lon={z['lon']:.5f} lat={z['lat']:.5f} px={z.get('flagged_water_px')} "
                      f"ndet={z.get('n_detections')} nconf={z.get('n_confirmed')} -> {name}")
            for i, f in enumerate(real[:a.top_det], 1):
                p = f["properties"]
                lon, lat = place.det_lonlat(f)
                name = f"{rid}_{date}_{m}_d{i}.png"
                pair(st, rid, date, lon, lat, m, a.size_m).save(out / name)
                print(f"  det #{i} {p.get('id')} lon={lon:.5f} lat={lat:.5f} area={p.get('area_m2')} "
                      f"max_prob={p.get('max_prob')} conf={p.get('confirmed')} prio={prio(f)} -> {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
