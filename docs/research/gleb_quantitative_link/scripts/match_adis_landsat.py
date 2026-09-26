"""Independent Landsat C2 check for the six examined ADIS segments."""
import csv
import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'results/adis_pairs'
API='https://planetarycomputer.microsoft.com/api/stac/v1/search'

def main():
    pairs=json.loads((BASE/'pair_manifest.json').read_text());results=[]
    for pair in pairs:
        dt=datetime.fromisoformat(pair['field_datetime'])
        q={'collections':['landsat-c2-l2'],'intersects':{'type':'Point','coordinates':[pair['longitude'],pair['latitude']]},
           'datetime':f"{(dt-timedelta(days=5)).isoformat().replace('+00:00','Z')}/{(dt+timedelta(days=5)).isoformat().replace('+00:00','Z')}",
           'limit':100}
        try:
            r=requests.post(API,json=q,timeout=40);r.raise_for_status();features=r.json().get('features',[])
            for f in features:
                ac=datetime.fromisoformat(f['properties']['datetime'].replace('Z','+00:00'))
                results.append({'segment_id':pair['segment_id'],'scene_id':f['id'],'platform':f['properties'].get('platform'),
                   'field_datetime':pair['field_datetime'],'acquisition_datetime':ac.isoformat(),
                   'delta_hours':round((ac-dt).total_seconds()/3600,2),
                   'cloud_cover_percent':f['properties'].get('eo:cloud_cover'),
                   'stac_item':next((x['href'] for x in f.get('links',[]) if x.get('rel')=='self'),'')})
            print(pair['segment_id'],len(features),flush=True)
        except Exception as e:
            print('ERROR',pair['segment_id'],e,flush=True)
    if results:
        with (BASE/'landsat_matches.csv').open('w',newline='') as out:
            wr=csv.DictWriter(out,results[0]);wr.writeheader();wr.writerows(results)
    print('total',len(results))

if __name__=='__main__':main()
