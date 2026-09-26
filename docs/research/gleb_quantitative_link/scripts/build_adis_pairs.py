"""Download compact georeferenced Sentinel-2 crops for close ADIS observations."""
from __future__ import annotations

import csv
import json
import math
import zipfile
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio
import requests
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.transform import from_origin, rowcol
from rasterio.vrt import WarpedVRT

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/adis_pairs"
RAW = ROOT / "data/raw/adis"
SELECT = ["335765", "335766", "335997", "335792", "335772", "335694"]
ASSETS = ["blue", "green", "red", "nir", "scl"]


def scene_for_segment(matches, sid):
    group = [x for x in matches if x["SegmentID"] == sid]
    return min(group, key=lambda x: (abs(float(x["delta_hours"])), float(x["cloud_cover_percent"])))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    matches = list(csv.DictReader((ROOT / "results/adis_sentinel_matches.csv").open(newline="")))
    segments = {x["SegmentID"]: x for x in csv.DictReader((RAW / "Segments.csv").open(newline=""))}
    objects = list(csv.DictReader((RAW / "Objects.csv").open(newline="")))
    results = []
    for sid in SELECT:
        m = scene_for_segment(matches, sid)
        s = segments[sid]
        scene = m["scene_id"]
        item_url = f"https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/{scene}"
        item = requests.get(item_url, timeout=40).json()
        assert item["id"] == scene
        assert item["properties"]["datetime"][:19] == m["acquisition_datetime"][:19]
        lon, lat = float(s["Longitude"]), float(s["Latitude"])
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="3", GDAL_HTTP_RETRY_DELAY="1"):
            with rasterio.open(item["assets"]["blue"]["href"]) as src:
                crs = src.crs
            x, y = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(lon, lat)
            t = from_origin(x - 6000, y + 6000, 10, 10)
            stack = np.zeros((5, 1200, 1200), dtype="uint16")
            for i, asset in enumerate(ASSETS):
                print(sid, scene, asset, flush=True)
                with rasterio.open(item["assets"][asset]["href"]) as src:
                    with WarpedVRT(src, crs=crs, transform=t, width=1200, height=1200,
                                   resampling=Resampling.nearest if asset == "scl" else Resampling.bilinear) as vrt:
                        stack[i] = vrt.read(1)
        folder = OUT / sid
        folder.mkdir(exist_ok=True)
        raster_path = folder / "sentinel2_crop_12km.tif"
        with rasterio.open(raster_path,"w",driver="GTiff",height=1200,width=1200,count=5,dtype="uint16",
                           crs=crs,transform=t,compress="deflate",tiled=True,blockxsize=256,blockysize=256,nodata=0) as dst:
            dst.write(stack)
            dst.descriptions = tuple(ASSETS)
            dst.update_tags(scene_id=scene, acquisition_datetime=m["acquisition_datetime"],
                            stac_item=item_url, adis_segment_id=sid)
        pointrow, pointcol = rowcol(t,x,y)
        assert 0 <= pointrow < 1200 and 0 <= pointcol < 1200
        valid = stack[0] > 0
        assert valid.sum() > 1000
        # SCL 6 = water; 3/8/9/10 = cloud/shadow/cirrus.
        point_scl = int(stack[4,pointrow,pointcol])
        rgb=np.stack([stack[2],stack[1],stack[0]],axis=-1).astype(float)
        for channel in range(3):
            vals=rgb[:,:,channel][valid]
            lo,hi=np.percentile(vals,[2,98])
            rgb[:,:,channel]=np.clip((rgb[:,:,channel]-lo)/(hi-lo if hi>lo else 1),0,1)
        rgb[~valid]=0
        fig,ax=plt.subplots(figsize=(8,8))
        ax.imshow(rgb);ax.plot(pointcol,pointrow,'c+',markersize=15,markeredgewidth=2)
        heading=math.radians(float(s["avgheading"]))
        half=float(s["distance"])/20 # metres / 10m/pixel /2
        ax.plot([pointcol-half*math.sin(heading),pointcol+half*math.sin(heading)],
                [pointrow+half*math.cos(heading),pointrow-half*math.cos(heading)],'y-',lw=1.5,label='approx. 10 km transect')
        ax.set(xlim=(0,1200),ylim=(1200,0));ax.legend(loc='lower right')
        ax.set_title(f"ADIS {sid}: {s['n_objects>50cm']} items >50 cm / {float(s['area_scanned_km2']):.3f} km²\n"
                     f"field {m['field_datetime'][:16]} | Sentinel-2 {m['acquisition_datetime'][:16]} UTC\n"
                     f"Δ {m['delta_hours']} h | tile cloud {m['cloud_cover_percent']}% | point SCL {point_scl}",fontsize=10)
        fig.tight_layout();fig.savefig(folder/"rgb_pair_preview.png",dpi=135);plt.close(fig)
        # Individual manually validated detections, rather than unreleased full frames.
        object_rows=[o for o in objects if o["SegmentID"]==sid]
        extracted=[]
        with zipfile.ZipFile(RAW/"Object_snippets.zip") as z:
            names=set(z.namelist())
            for o in object_rows[:3]:
                name=o["ID"]+".jpg"
                if name in names:
                    target=folder/"snippets"/name;target.parent.mkdir(exist_ok=True)
                    target.write_bytes(z.read(name));extracted.append(str(target.relative_to(ROOT)))
        results.append({"segment_id":sid,"scene_id":scene,"field_datetime":m["field_datetime"],
                        "acquisition_datetime":m["acquisition_datetime"],"delta_hours":float(m["delta_hours"]),
                        "latitude":lat,"longitude":lon,"n_objects_gt50cm":int(float(s["n_objects>50cm"])),
                        "area_scanned_km2":float(s["area_scanned_km2"]),
                        "raw_density_items_km2":float(s["n_objects>50cm"])/float(s["area_scanned_km2"]),
                        "calibrated_density_items_km2":float(s["dhat_50cm_calibrated"] or 0),
                        "scan_width_m":float(s["Scan_width"]),"distance_m":float(s["distance"]),
                        "cloud_cover_tile_percent":float(m["cloud_cover_percent"]),"point_scl":point_scl,
                        "valid_fraction":round(float(valid.mean()),4),"crs":str(crs),
                        "raster":str(raster_path.relative_to(ROOT)),"preview":str((folder/"rgb_pair_preview.png").relative_to(ROOT)),
                        "snippets":extracted,"stac_item":item_url,"assets":{a:item["assets"][a]["href"] for a in ASSETS}})
    (OUT/"pair_manifest.json").write_text(json.dumps(results,indent=2))
    with (OUT/"pair_summary.csv").open("w",newline="") as out:
        keys=[k for k in results[0] if k not in {"snippets","assets"}]
        wr=csv.DictWriter(out,keys);wr.writeheader();wr.writerows([{k:r[k] for k in keys} for r in results])
    print("built",len(results))


if __name__ == "__main__":
    main()
