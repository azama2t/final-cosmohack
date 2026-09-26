"""Join PLP2019 published target fractions to real ACOLITE Sentinel-2 pixels."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import shapefile
import xarray as xr
from pyproj import Transformer
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/raw/plp2019/extracted/PLP2019_dataset"
OUT = ROOT / "results/plp2019"


def band(ds, prefix, wavelength):
    key = min((k for k in ds.data_vars if k.startswith(prefix + "_")),
              key=lambda k: abs(int(k.split("_")[-1]) - wavelength))
    return ds[key].values, key


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    transformer = Transformer.from_crs("EPSG:32635", "EPSG:4326", always_xy=True)
    rows = []
    unmatched = []
    for shp in sorted((BASE / "Vector_Points").glob("*/*.shp")):
        date = shp.parent.name
        nc = next((BASE / "S2_satellite_images_nc").glob(f"*_{date}_*.nc"))
        with xr.open_dataset(nc, engine="h5netcdf") as ds:
            blue, blue_name = band(ds, "rhos", 492)
            green, green_name = band(ds, "rhos", 560)
            red, red_name = band(ds, "rhos", 665)
            nir, nir_name = band(ds, "rhos", 833)
            swir, swir_name = band(ds, "rhos", 1610)
            satellite_time = ds.attrs["isodate"]
            source_id = Path(str(ds.attrs.get("l1_file", ""))).stem
            sf = shapefile.Reader(str(shp), encoding="latin1", encodingErrors="replace")
            lon_grid, lat_grid = ds["lon"].values, ds["lat"].values
            for sr in sf.shapeRecords():
                attrs = sr.record.as_dict()
                x, y = sr.shape.points[0]
                lon, lat = transformer.transform(x, y)
                distance = (lon_grid - lon) ** 2 + (lat_grid - lat) ** 2
                iy, ix = np.unravel_index(np.nanargmin(distance), distance.shape)
                match_error_m=float(np.sqrt(distance[iy, ix])*111000)
                if match_error_m > 5:
                    unmatched.append({'date':date,'pixel_name':attrs['Pixel_name'],'utm_x':x,'utm_y':y,
                                      'nearest_distance_m':round(match_error_m,4),'reason':'labeled center outside published NetCDF grid'})
                    continue
                fraction = int(attrs.get("CP_Bags") or 0) + int(attrs.get("CP_Bottles") or 0)
                fdi = float(nir[iy, ix] - (red[iy, ix] + (swir[iy, ix] - red[iy, ix]) * (833 - 665) / (1610 - 665)))
                rows.append({
                    "date": date, "pixel_name": attrs["Pixel_name"],
                    "longitude": round(lon, 8), "latitude": round(lat, 8),
                    "utm_x": x, "utm_y": y, "sentinel_pixel_area_m2": 100,
                    "plastic_percent": fraction, "bags_percent": attrs.get("CP_Bags", 0),
                    "bottles_percent": attrs.get("CP_Bottles", 0), "sea_percent": attrs.get("CP_Sea", 0),
                    "reeds_percent": attrs.get("CP_Reeds", 0),
                    "rhos_blue": float(blue[iy, ix]), "rhos_green": float(green[iy, ix]),
                    "rhos_red": float(red[iy, ix]), "rhos_nir": float(nir[iy, ix]),
                    "rhos_swir": float(swir[iy, ix]),
                    "fdi": fdi,
                    "pixel_row": int(iy), "pixel_col": int(ix),
                    "coordinate_match_error_m": round(match_error_m, 4),
                    "satellite_time": satellite_time, "scene_product_name": source_id,
                    "source_nc": str(nc.relative_to(ROOT)),
                    "band_names": ";".join([blue_name,green_name,red_name,nir_name,swir_name]),
                })
    fields = list(rows[0])
    with (OUT / "labeled_pixels.csv").open("w", newline="") as file:
        writer=csv.DictWriter(file, fields);writer.writeheader();writer.writerows(rows)
    with (OUT / "unmatched_labels.csv").open("w", newline="") as file:
        writer=csv.DictWriter(file,unmatched[0].keys());writer.writeheader();writer.writerows(unmatched)
    stats={"rows":len(rows),"dates":{}}
    for date in sorted({r["date"] for r in rows}):
        subset=[r for r in rows if r["date"]==date]
        x=np.array([r["plastic_percent"] for r in subset]);y=np.array([r["rhos_nir"] for r in subset])
        rho,p=spearmanr(x,y) if len(set(x))>1 else (float('nan'),float('nan'))
        fdi=np.array([r["fdi"] for r in subset]);rho_fdi,p_fdi=spearmanr(x,fdi) if len(set(x))>1 else (float('nan'),float('nan'))
        stats["dates"][date]={"n":len(subset),"plastic_percent_min":int(x.min()),"plastic_percent_max":int(x.max()),"spearman_nir":float(rho),"p_value":float(p),
                              "spearman_fdi":float(rho_fdi),"fdi_p_value":float(p_fdi)}
    x=np.array([r["plastic_percent"] for r in rows]);y=np.array([r["rhos_nir"] for r in rows])
    rho,p=spearmanr(x,y);stats["pooled_spearman_nir"]={"rho":float(rho),"p_value":float(p)}
    fdi=np.array([r["fdi"] for r in rows]);rho,p=spearmanr(x,fdi);stats["pooled_spearman_fdi"]={"rho":float(rho),"p_value":float(p)}
    # Each acquisition is a separate scene: evaluate whether a one-band empirical
    # conversion learned on four dates transfers to the fifth date.
    holds=[]
    for date in sorted(stats["dates"]):
        train=[r for r in rows if r["date"]!=date];test=[r for r in rows if r["date"]==date]
        train_x=np.array([r["fdi"] for r in train]);train_y=np.array([r["plastic_percent"] for r in train])
        test_x=np.array([r["fdi"] for r in test]);test_y=np.array([r["plastic_percent"] for r in test])
        coef=np.linalg.lstsq(np.column_stack([np.ones(len(train_x)),train_x]),train_y,rcond=None)[0]
        predicted=np.clip(coef[0]+coef[1]*test_x,0,100)
        holds.append({"held_out_date":date,"n":len(test),"mae_percent_points":round(float(np.mean(abs(predicted-test_y))),2),
                      "mean_baseline_mae_percent_points":round(float(np.mean(abs(train_y.mean()-test_y))),2)})
    stats["leave_one_date_out_fdi"] = holds
    (OUT/"summary.json").write_text(json.dumps(stats,indent=2))
    fig,ax=plt.subplots(figsize=(8,5))
    for date in sorted(stats["dates"]):
        subset=[r for r in rows if r["date"]==date]
        ax.scatter([r["plastic_percent"] for r in subset],[r["rhos_nir"] for r in subset],label=date,s=38,alpha=.8)
    ax.set(xlabel="Plastic-covered fraction of 10 m pixel, %",ylabel="ACOLITE rhos NIR reflectance (833 nm)",title="PLP2019: measured target fraction vs Sentinel-2 signal")
    ax.legend(ncol=2,fontsize=8);ax.grid(alpha=.2);fig.tight_layout();fig.savefig(OUT/"plastic_fraction_vs_nir.png",dpi=170);plt.close(fig)
    print(json.dumps(stats,indent=2))


if __name__ == "__main__":
    main()
