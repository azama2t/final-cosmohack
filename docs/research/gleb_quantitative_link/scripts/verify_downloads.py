"""Inventory downloaded source files and validate derived multispectral crops."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import rasterio

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/'data/raw'
OUT=ROOT/'results'
SOURCES={
 'adis/Readme.txt':'https://data.4tu.nl/datasets/ddede7f5-aca5-42ae-b851-e0bbb9a2c4c2',
 'adis/Objects.csv':'https://data.4tu.nl/datasets/ddede7f5-aca5-42ae-b851-e0bbb9a2c4c2',
 'adis/Segments.csv':'https://data.4tu.nl/datasets/ddede7f5-aca5-42ae-b851-e0bbb9a2c4c2',
 'adis/Segments.gpkg':'https://data.4tu.nl/datasets/ddede7f5-aca5-42ae-b851-e0bbb9a2c4c2',
 'adis/Object_snippets.zip':'https://data.4tu.nl/datasets/ddede7f5-aca5-42ae-b851-e0bbb9a2c4c2',
 'ruiz/Ruiz_2020_article.pdf':'https://doi.org/10.3389/fmars.2020.00308',
 'ruiz/Table_1.docx':'https://doi.org/10.3389/fmars.2020.00308',
 'plp2019/PLP2019_dataset.rar':'https://doi.org/10.5281/zenodo.3752719',
 'plp2022_23/S2B_MSI_2022_07_21_09_09_36_T35SMD_L2W.nc':'https://doi.org/10.5281/zenodo.10046182',
 'plp2022_23/S2A_MSI_2023_06_21_09_09_35_T35SMD_L2W.nc':'https://doi.org/10.5281/zenodo.10046182',
 'plp2022_23/20220721_UAS.JPG':'https://doi.org/10.5281/zenodo.10046182',
 'plp2022_23/20230621_UAS_RGB.JPG':'https://doi.org/10.5281/zenodo.10046182',
 'fml/FML_full_dataset.zip':'https://doi.org/10.17882/106148',
}

def sha256(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def main():
    OUT.mkdir(exist_ok=True)
    inventory=[]
    for rel,url in SOURCES.items():
        p=RAW/rel
        if p.exists():inventory.append({'path':str(p.relative_to(ROOT)),'bytes':p.stat().st_size,'sha256':sha256(p),'source':url})
    (ROOT/'data/source_inventory.json').write_text(json.dumps(inventory,indent=2))
    crops=[]
    manifest=json.loads((OUT/'adis_pairs/pair_manifest.json').read_text())
    for item in manifest:
        p=ROOT/item['raster']
        with rasterio.open(p) as ds:
            arr=ds.read();valid=arr[0]>0
            crops.append({'segment_id':item['segment_id'],'scene_id':item['scene_id'],'path':item['raster'],
             'crs':str(ds.crs),'shape':[ds.count,ds.height,ds.width],
             'band_descriptions':list(ds.descriptions),'valid_fraction':round(float(valid.mean()),4),
             'red_valid_p2_p98':np.percentile(arr[2][valid],[2,98]).tolist(),
             'water_fraction':round(float(np.mean(arr[4][valid]==6)),4),
             'route_inside_crop_fraction':item['route_inside_crop_fraction'],
             'route_water_scl6_fraction':item['route_water_scl6_fraction'],
             'bytes':p.stat().st_size,
             'verified':bool(ds.crs and ds.count==5 and valid.sum()>1000 and item['route_inside_crop_fraction']>=.99)})
    (OUT/'download_verification.json').write_text(json.dumps({'source_files':len(inventory),'source_bytes':sum(i['bytes'] for i in inventory),
      'crops':crops,'crop_bytes':sum(i['bytes'] for i in crops)},indent=2))
    print('source files',len(inventory),'bytes',sum(i['bytes'] for i in inventory),'crops',len(crops),'verified',sum(i['verified'] for i in crops))

if __name__=='__main__':main()
