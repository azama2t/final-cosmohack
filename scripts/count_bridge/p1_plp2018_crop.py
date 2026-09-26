"""L127: read the S2A_35SMD_20180607_0_L2A crop of the PLP2018 run (out/l127/plp/20180607/meta.json) -> out/l127/plp2018_crop.npz."""
import json, sys, numpy as np
sys.path.insert(0,'src'); sys.path.insert(0,'scripts/case')
from macroplastic.live import stac
import pair_quality as pq
m=json.load(open('out/l127/plp/20180607/meta.json',encoding='utf-8'))
it=pq.get_item('earth-search','sentinel-2-l2a',m['scene_id'])
crop=stac.read_crop(it,32635,m['bounds_utm'])
np.savez_compressed('out/l127/plp2018_crop.npz',bands=crop['bands'],scl=crop['scl'],transform=np.array(crop['transform'])[:6])
print(crop['bands'].shape, crop['transform'])
