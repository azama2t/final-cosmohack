"""Cross-check PLP2019 dates and target location against independent STAC."""
from pathlib import Path
import json
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/plp2019/catalog_verification.json'
URL='https://planetarycomputer.microsoft.com/api/stac/v1/search'
DATES=['2019-04-18','2019-05-03','2019-05-18','2019-05-28','2019-06-07']
POINT={'type':'Point','coordinates':[26.5662,39.1082]}

def main():
    rows=[]
    for date in DATES:
        for collection in ['sentinel-2-l1c','sentinel-2-l2a']:
            q={'collections':[collection],'intersects':POINT,'datetime':date+'T00:00:00Z/'+date+'T23:59:59Z','limit':20}
            try:
                r=requests.post(URL,json=q,timeout=30);r.raise_for_status();features=r.json()['features']
                rows.append({'date':date,'collection':collection,'matches':len(features),
                             'scenes':[{'id':x['id'],'datetime':x['properties'].get('datetime'),
                                        'cloud':x['properties'].get('eo:cloud_cover')} for x in features]})
            except Exception as e:rows.append({'date':date,'collection':collection,'error':str(e)})
            print(date,collection,rows[-1].get('matches'),flush=True)
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(rows,indent=2))

if __name__=='__main__':main()
