"""Fetch a harmonized S2 L2A crop for a region -> data/live/<region>/<date>/ (bands.tif, scl.tif, water_mask.tif,
rgb.png/json, scene.json). Screens candidates by cloud fraction of the CROP (SCL 8/9/10, 20 m) and ranks OK ones by water NIR (glint).

  python scripts/fetch_live.py --region honduras --datetime 2025-01-01/2026-09-25 --max-crop-cloud 0.10
  python scripts/fetch_live.py --region durban --date 2019-04-24 --bounds-utm 305880 6690330 344310 6721550
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from macroplastic.live import stac  # noqa: E402


def crop_cloud(item, epsg, bounds):
    """Cheap 20 m screening: valid, cloud, shadow, SCL-water fractions and median B8A of SCL-water (glint proxy)."""
    src = stac.source_of(item)
    amap = stac.ES_ASSET if src == "earth-search" else stac.PC_ASSET
    scl = stac._read_asset(item.assets[amap["SCL"]].href, epsg, bounds, res=20.0)
    valid = scl > 0
    nv = max(int(valid.sum()), 1)
    w = scl == 6
    glint = float("nan")
    if w.sum() > 100:
        dn = stac._read_asset(item.assets[amap["B8A"]].href, epsg, bounds, res=20.0).astype(np.float64)
        inpix = None
        if src == "earth-search":
            b12 = stac._read_asset(item.assets[amap["B12"]].href, epsg, bounds, res=20.0)
            inpix, _ = stac.es_offset_in_pixels(item, b12, scl)
        s, o, _ = stac.band_scale_offset(item, "B8A", inpix)
        glint = float(np.median(dn[w] * s + o))
    return float(valid.mean()), float(np.isin(scl, stac.SCL_CLOUD)[valid].sum() / nv), \
        float((scl == 3)[valid].sum() / nv), float(w.mean()), glint


def pick_item(date: str, tile: str, lon: float, lat: float):
    """Source policy (25.09, after measuring the DN=1 clamp):
    - date >= 2022-01-25 (baseline >= 04.00): Planetary Computer item (raw DN with +1000 offset, offset from the product
      metadata XML) -- Earth Search COGs of these baselines have the offset removed AND dark water clamped at DN=1
      (e.g. Haiti 2025-01-27: 97 % of SCL-6 water in B4 at DN=1; PC: B4 median -0.0011, not clamped);
    - earlier dates: Earth Search item of the ORIGINAL processing (baseline < 04, suffix _0), not the _1 reprocessing
      (05.00, clamped the same way). Fallback: whatever Earth Search has."""
    if date >= "2022-01-25":
        try:
            its = stac.search("planetary-computer", lon, lat, f"{date}/{date}", max_cloud=100, tile=tile)
            if its:
                return its[0]
        except Exception as e:  # PC unreachable -> Earth Search
            print("PC failed:", repr(e))
    from pystac_client import Client
    c = Client.open(stac.EARTH_SEARCH)
    its = [it for it in c.search(collections=["sentinel-2-l2a"], intersects=dict(type="Point", coordinates=[lon, lat]),
                                 datetime=f"{date}/{date}").items() if stac.tile_of(it) == tile]
    orig = [it for it in its if str(it.properties.get("s2:processing_baseline", "99")) < "04"]
    return sorted(orig or its, key=lambda i: i.id)[0]


def refetch_existing(root: Path):
    for sj in sorted(root.glob("*/*/scene.json")):
        old = json.loads(sj.read_text(encoding="utf-8"))
        reg = stac.REGIONS.get(old["region"], {})
        lon, lat = reg.get("center", (None, None))
        if lon is None:
            b = old["bounds_wgs84"]
            lon, lat = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        it = pick_item(old["date"], old["tile"], lon, lat)
        epsg = int(old["crs"].split(":")[-1])
        if stac.item_epsg(it) != epsg:
            print("CRS mismatch, skip", sj, it.id)
            continue
        crop = stac.read_crop(it, epsg, old["bounds_utm"])
        keep = {k: old[k] for k in ("water_b8a_median_20m", "selection") if k in old}
        keep["replaced_scene_id"] = old["scene_id"] if old["scene_id"] != it.id else None
        meta = stac.write_scene(sj.parent, old["region"], it, crop, extra=keep)
        print(f"rebuilt {sj.parent.parent.name}/{sj.parent.name} {meta['scene_id']} ({meta['source']}, "
              f"baseline {meta['processing_baseline']}) water_frac={meta['water_frac']} b11={meta['water_b11_median']}",
              flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region")
    ap.add_argument("--datetime", default="2025-01-01/2026-09-25")
    ap.add_argument("--date", help="exact date YYYY-MM-DD (overrides --datetime)")
    ap.add_argument("--source", default="earth-search", choices=["earth-search", "planetary-computer"])
    ap.add_argument("--tile", help="MGRS tile (default: region tile)")
    ap.add_argument("--size-km", type=float, default=25.0)
    ap.add_argument("--bounds-utm", type=float, nargs=4, help="explicit crop box in tile CRS (w s e n)")
    ap.add_argument("--max-tile-cloud", type=float, default=40)
    ap.add_argument("--max-crop-cloud", type=float, default=0.10)
    ap.add_argument("--min-valid", type=float, default=0.95)
    ap.add_argument("--max-candidates", type=int, default=12)
    ap.add_argument("--n", type=int, default=1, help="number of scenes to write")
    ap.add_argument("--exclude-dates", nargs="*", default=[])
    ap.add_argument("--out", default=str(ROOT / "data" / "live"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rank", default="glint", choices=["glint", "cloud"],
                    help="order of OK candidates: glint = lowest median B8A over SCL water (less sun glint/haze)")
    ap.add_argument("--source-policy", default="auto", choices=["auto", "screening-source"],
                    help="auto: write pixels from PC for baseline>=04 dates and from original ES _0 before 2022")
    ap.add_argument("--refetch-existing", action="store_true",
                    help="rebuild every data/live/*/*/ from its scene.json (same item id and UTM bounds)")
    a = ap.parse_args()
    if a.refetch_existing:
        return refetch_existing(Path(a.out))

    reg = stac.REGIONS[a.region]
    lon, lat = reg["center"]
    dt = f"{a.date}/{a.date}" if a.date else a.datetime
    items = stac.search(a.source, lon, lat, dt, max_cloud=a.max_tile_cloud if not a.date else 100,
                        tile=a.tile or reg["tile"])
    items = [it for it in items if it.properties["datetime"][:10] not in a.exclude_dates]
    print(f"{len(items)} candidate items ({a.source}, {dt}, tile {a.tile or reg['tile']})", flush=True)
    ok_items = []
    for it in items[: a.max_candidates]:
        if a.bounds_utm:
            epsg, bounds = stac.item_epsg(it), list(a.bounds_utm)
        else:
            epsg, bounds = stac.crop_bounds(it, lon, lat, a.size_km * 1000)
        vf, cf, sf, wf, gl = crop_cloud(it, epsg, bounds)
        ok = vf >= a.min_valid and cf <= a.max_crop_cloud
        print(f"  {it.id} tile_cc={it.properties.get('eo:cloud_cover'):.2f} crop valid={vf:.3f} cloud={cf:.3f} "
              f"shadow={sf:.3f} scl6={wf:.3f} water_B8A={gl:.4f} -> {'OK' if ok else 'skip'}", flush=True)
        if ok:
            ok_items.append((gl if a.rank == "glint" else cf, it, epsg, bounds, gl))
    ok_items.sort(key=lambda t: (np.nan_to_num(t[0], nan=9.0)))
    written = 0
    for _, it, epsg, bounds, gl in ok_items[: a.n] if not a.dry_run else []:
        if a.source_policy == "auto":  # screening on Earth Search, pixels from the unclamped source (see pick_item)
            it2 = pick_item(it.properties["datetime"][:10], stac.tile_of(it), lon, lat)
            if stac.item_epsg(it2) == epsg:
                it = it2
        crop = stac.read_crop(it, epsg, bounds)
        outdir = Path(a.out) / a.region / it.properties["datetime"][:10]
        meta = stac.write_scene(outdir, a.region, it, crop, extra=dict(
            water_b8a_median_20m=round(gl, 5),
            selection=f"{a.datetime if not a.date else a.date}; crop cloud<={a.max_crop_cloud}, valid>={a.min_valid}; "
                      f"ranked by {a.rank} among {len(ok_items)} OK of {min(len(items), a.max_candidates)} screened"))
        print(json.dumps({k: meta[k] for k in ("scene_id", "date", "crop_cloud_frac", "water_frac", "width",
                                                "height", "read_s")}), flush=True)
        print(f"WROTE {outdir}", flush=True)
        written += 1
    if written == 0 and not a.dry_run:
        print("NO SCENE WRITTEN")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
