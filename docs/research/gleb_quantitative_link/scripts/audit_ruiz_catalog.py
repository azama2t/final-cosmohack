"""Check satellite coverage of Ruiz (2020) study region, without inventing tow dates."""
from pathlib import Path
import json
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/ruiz_catalog_audit.json'
BBOX=[-1.80,43.25,-1.30,43.65] # SE Bay of Biscay, Saint-Jean-de-Luz coast
INTERVAL='2018-05-01T00:00:00Z/2018-09-30T23:59:59Z'

def search(url,collection):
    response=requests.post(url,json={'collections':[collection],'bbox':BBOX,'datetime':INTERVAL,'limit':100},timeout=40)
    response.raise_for_status();d=response.json()
    return {'api':url,'collection':collection,'matched':d.get('numberMatched',d.get('context',{}).get('matched')),
            'returned':len(d.get('features',[])),
            'sample_scenes':[{'id':x['id'],'datetime':x['properties'].get('datetime'),'cloud':x['properties'].get('eo:cloud_cover')} for x in d.get('features',[])[:10]]}

def main():
    results=[]
    for url,collection in [('https://earth-search.aws.element84.com/v1/search','sentinel-2-l2a'),
                           ('https://planetarycomputer.microsoft.com/api/stac/v1/search','landsat-c2-l2')]:
        try:results.append(search(url,collection))
        except Exception as exc:results.append({'api':url,'collection':collection,'error':str(exc)})
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps({'study_area_bbox':BBOX,'season_interval':INTERVAL,
        'warning':'Ruiz public supplement does not expose tow-level coordinates and dates; these are regional catalog hits, not matched observations.',
        'results':results},indent=2))
    print(OUT.read_text()[:2500])

if __name__=='__main__':main()
