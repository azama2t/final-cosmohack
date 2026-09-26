"""Add original ADIS route geometries and audit local Sentinel-2 quality."""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from pyproj import Transformer
from shapely.geometry import mapping
from shapely.ops import transform
from shapely import wkb

ROOT=Path(__file__).resolve().parents[1]
PAIR=ROOT/'results/adis_pairs'

def gpkg_geometry(blob):
    code=(blob[3]>>1)&7
    offset=8+{0:0,1:32,2:48,3:48,4:64}[code]
    return wkb.loads(blob[offset:])

def main():
    manifest=json.loads((PAIR/'pair_manifest.json').read_text())
    drift_path=PAIR/'drift_estimates.json'
    drifts={d['segment_id']:d for d in json.loads(drift_path.read_text())} if drift_path.exists() else {}
    conn=sqlite3.connect(ROOT/'data/raw/adis/Segments.gpkg')
    objects=list(csv.DictReader((ROOT/'data/raw/adis/Objects.csv').open(newline='')))
    features=[]
    for row in manifest:
        sid=row['segment_id']
        blob=conn.execute('select geom from merged where SegmentID=?',(int(sid),)).fetchone()[0]
        geom=gpkg_geometry(blob)
        features.append({'type':'Feature','geometry':mapping(geom),'properties':{'SegmentID':sid,
                         'count_gt50cm':row['n_objects_gt50cm'],'area_scanned_km2':row['area_scanned_km2'],
                         'field_datetime':row['field_datetime']}})
        with rasterio.open(ROOT/row['raster']) as ds:
            arr=ds.read();to_crs=Transformer.from_crs('EPSG:4326',ds.crs,always_xy=True)
            geom_m=transform(to_crs.transform,geom)
            track=geom_m.line_merge() if hasattr(geom_m,'line_merge') else geom_m
            # Sample all route vertices rather than only its centroid.
            coords=[]
            for part in geom_m.geoms:
                coords.extend(list(part.coords))
            pix=np.array([ds.index(x,y) for x,y in coords])
            inside=(pix[:,0]>=0)&(pix[:,0]<ds.height)&(pix[:,1]>=0)&(pix[:,1]<ds.width)
            good=pix[inside]
            scl=arr[4,good[:,0],good[:,1]] if len(good) else np.array([])
            row['route_vertices']=len(coords)
            row['route_inside_crop_fraction']=round(float(inside.mean()),4)
            row['route_water_scl6_fraction']=round(float(np.mean(scl==6)),4) if len(scl) else 0
            row['route_cloud_shadow_scl_fraction']=round(float(np.mean(np.isin(scl,[3,8,9,10]))),4) if len(scl) else 0
            valid=arr[0]>0; rgb=np.stack([arr[2],arr[1],arr[0]],axis=-1).astype(float)
            for c in range(3):
                lo,hi=np.percentile(rgb[:,:,c][valid],[2,98]);rgb[:,:,c]=np.clip((rgb[:,:,c]-lo)/(hi-lo if hi>lo else 1),0,1)
            rgb[~valid]=0
            fig,ax=plt.subplots(figsize=(8,8));ax.imshow(rgb)
            inv=~ds.transform
            for part in geom_m.geoms:
                pts=np.array([inv*(x,y) for x,y in part.coords]);ax.plot(pts[:,0],pts[:,1],color='yellow',lw=2)
            item_rows=[o for o in objects if o['SegmentID']==sid]
            for obj in item_rows:
                x,y=to_crs.transform(float(obj['longitude']),float(obj['latitude']));col,rr=inv*(x,y)
                ax.scatter(col,rr,s=25,c='magenta',edgecolor='black',linewidth=.3)
            if sid in drifts:
                d=drifts[sid]
                x,y=to_crs.transform(d['estimated_drift_longitude'],d['estimated_drift_latitude']);col,rr=inv*(x,y)
                ax.add_patch(plt.Circle((col,rr),d['heuristic_search_radius_km']*100,color='cyan',fill=False,ls='--',lw=1.5))
                ax.scatter(col,rr,marker='x',s=80,c='cyan')
                row['drift_model_displacement_km']=d['model_displacement_km']
                row['drift_search_radius_km']=d['heuristic_search_radius_km']
                row['drift_current_coverage_hours']=d['current_coverage_hours']
            ax.set(xlim=(0,ds.width),ylim=(ds.height,0))
            ax.set_title(f"ADIS {sid}: {row['n_objects_gt50cm']} objects >50cm / {row['area_scanned_km2']:.3f} km²\n"
                         f"S2 {row['scene_id']} | Δ {row['delta_hours']:+.2f} h\n"
                         f"yellow: ship route | magenta: camera | cyan: drift search | SCL water {row['route_water_scl6_fraction']:.0%}",fontsize=10)
            fig.tight_layout();out=PAIR/sid/'rgb_pair_verified.png';fig.savefig(out,dpi=140);plt.close(fig)
            row['verified_preview']=str(out.relative_to(ROOT))
        print(sid,row['route_inside_crop_fraction'],row['route_water_scl6_fraction'])
    (PAIR/'selected_routes.geojson').write_text(json.dumps({'type':'FeatureCollection','features':features}))
    (PAIR/'pair_manifest.json').write_text(json.dumps(manifest,indent=2))
    with (PAIR/'pair_summary.csv').open('w',newline='') as out:
        keys=[k for k in manifest[0] if k not in {'snippets','assets'}]
        wr=csv.DictWriter(out,keys);wr.writeheader();wr.writerows([{k:r[k] for k in keys} for r in manifest])

if __name__=='__main__':main()
