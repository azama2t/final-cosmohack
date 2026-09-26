"""Check whether highest ADIS ocean counts have standard optical catalog coverage."""
import csv,json
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/adis_open_ocean_audit.json'
IDS={'158080','174202','31546'}
CATALOGS=[('earth-search','https://earth-search.aws.element84.com/v1/search','sentinel-2-l2a'),
          ('planetary-computer','https://planetarycomputer.microsoft.com/api/stac/v1/search','sentinel-2-l2a'),
          ('planetary-computer','https://planetarycomputer.microsoft.com/api/stac/v1/search','landsat-c2-l2')]

def main():
    seg={r['SegmentID']:r for r in csv.DictReader((ROOT/'data/raw/adis/Segments.csv').open(newline=''))}
    rows=[]
    for sid in sorted(IDS):
        s=seg[sid];d=s['mindate'][:10].replace('/','-')
        for name,url,collection in CATALOGS:
            q={'collections':[collection],'intersects':{'type':'Point','coordinates':[float(s['Longitude']),float(s['Latitude'])]},
               'datetime':d+'T00:00:00Z/'+d+'T23:59:59Z','limit':50}
            try:
                r=requests.post(url,json=q,timeout=30);r.raise_for_status();fs=r.json()['features']
                rows.append({'segment_id':sid,'count_gt50cm':s['n_objects>50cm'],'date':d,'catalog':name,'collection':collection,
                             'scenes_same_day':len(fs),'scene_ids':[f['id'] for f in fs]})
            except Exception as e:rows.append({'segment_id':sid,'catalog':name,'collection':collection,'error':str(e)})
    OUT.write_text(json.dumps(rows,indent=2));print('checks',len(rows),'scenes',sum(x.get('scenes_same_day',0) for x in rows))

if __name__=='__main__':main()
