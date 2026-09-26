"""Approximate short-term ADIS-to-Sentinel drift using archived wind and model current."""
import json,math
from datetime import datetime,timedelta,timezone
from pathlib import Path
from pyproj import Geod
import requests

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'results/adis_pairs'
CACHE=ROOT/'data/raw/weather'
GEOD=Geod(ellps='WGS84')

def retrieve(base,params,path):
    if path.exists():return json.loads(path.read_text())
    r=requests.get(base,params=params,timeout=40);r.raise_for_status();data={'url':r.url,'response':r.json()}
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data));return data

def lookup(data,daytime,speed_key,direction_key):
    h=data['response'].get('hourly',{});time=daytime.strftime('%Y-%m-%dT%H:00')
    try:i=h['time'].index(time)
    except (KeyError,ValueError):return None
    v=h[speed_key][i];d=h[direction_key][i]
    return (float(v),float(d)) if v is not None and d is not None else None

def main():
    pairs=json.loads((BASE/'pair_manifest.json').read_text());result=[]
    for p in pairs:
        sid=p['segment_id'];field=datetime.fromisoformat(p['field_datetime']);sat=datetime.fromisoformat(p['acquisition_datetime'])
        d0=(min(field,sat)-timedelta(days=1)).date().isoformat();d1=(max(field,sat)+timedelta(days=1)).date().isoformat()
        common={'latitude':p['latitude'],'longitude':p['longitude'],'start_date':d0,'end_date':d1,'timezone':'GMT','wind_speed_unit':'ms'}
        wind=retrieve('https://archive-api.open-meteo.com/v1/archive',dict(common,hourly='wind_speed_10m,wind_direction_10m',cell_selection='sea'),CACHE/f'{sid}_wind.json')
        current=retrieve('https://marine-api.open-meteo.com/v1/marine',dict(common,hourly='ocean_current_velocity,ocean_current_direction'),CACHE/f'{sid}_current.json')
        assert wind['response'].get('hourly_units',{}).get('wind_speed_10m')=='m/s'
        assert current['response'].get('hourly_units',{}).get('ocean_current_velocity')=='m/s'
        sign=1 if sat>field else -1;start,end=sorted((field,sat));t=start;east=north=0.;wind_seconds=current_seconds=0.
        while t<end:
            t2=min(end,timedelta(minutes=15)+t);seconds=(t2-t).total_seconds()*sign
            w=lookup(wind,t,'wind_speed_10m','wind_direction_10m')
            c=lookup(current,t,'ocean_current_velocity','ocean_current_direction')
            if w:
                angle=math.radians((w[1]+180)%360);east+=.02*w[0]*math.sin(angle)*seconds;north+=.02*w[0]*math.cos(angle)*seconds;wind_seconds+=abs(seconds)
            if c:
                angle=math.radians(c[1]);east+=c[0]*math.sin(angle)*seconds;north+=c[0]*math.cos(angle)*seconds;current_seconds+=abs(seconds)
            t=t2
        distance=math.hypot(east,north);bearing=math.degrees(math.atan2(east,north))
        lon,lat,_=GEOD.fwd(p['longitude'],p['latitude'],bearing,distance)
        elapsed=abs((sat-field).total_seconds())/3600
        result.append({'segment_id':sid,'delta_hours':p['delta_hours'],'east_km':round(east/1000,3),'north_km':round(north/1000,3),
         'model_displacement_km':round(distance/1000,3),'estimated_drift_latitude':lat,'estimated_drift_longitude':lon,
         'heuristic_search_radius_km':round(.5+.25*elapsed,3),'wind_coverage_hours':round(wind_seconds/3600,3),
         'current_coverage_hours':round(current_seconds/3600,3),'wind_source':wind['url'],'current_source':current['url'],
         'caveat':'Eulerian model at fixed route centroid + 2% windage; heuristic radius, not measured plastic trajectory'})
        print(sid,result[-1]['model_displacement_km'],'km current hours',result[-1]['current_coverage_hours'],flush=True)
    (BASE/'drift_estimates.json').write_text(json.dumps(result,indent=2))

if __name__=='__main__':main()
