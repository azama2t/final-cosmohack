"""L127: PLP2018 target pixels. Needs out/l127/plp2018_crop.npz (S2A_35SMD_20180607_0_L2A crop, written by
scripts/count_bridge/p1_plp2018_crop.py). Orthophoto thumbnail out/l115/UAV_20180607_thumb.jpg (from Zenodo 3752719 UAV_20180607.jpg).
Segments the 3 targets (bags blue / nets / bottles), assumes a north-up orthophoto at 2.14 cm (thumb 0.2269 m/px),
fits a 2-D translation (0.5 m steps) maximising corr(predicted cover, S2 B8 anomaly) -> out/l127/plp2018_targets_px.json."""
import json, numpy as np
from PIL import Image
from scipy import ndimage
S = 16963 / 1600 * 0.0214
a = np.asarray(Image.open('out/l115/UAV_20180607_thumb.jpg')).astype(float)
masks = {}
for name, (x0, y0, x1, y1) in {'bags': (1098, 562, 1162, 630), 'nets': (950, 780, 1050, 870), 'bottles': (820, 940, 910, 1030)}.items():
    sub = a[y0:y1, x0:x1]; g = sub.mean(2)
    m = (sub[..., 2] - sub[..., 0] > 60) & (sub[..., 2] > 150) if name == 'bags' else (g > np.median(g) + 25)
    lab, n = ndimage.label(m); sz = ndimage.sum(m, lab, range(1, n + 1)); mm = lab == (np.argmax(sz) + 1)
    mm = ndimage.binary_fill_holes(ndimage.binary_closing(mm, iterations=2))
    ys, xs = np.where(mm)
    masks[name] = np.c_[xs + x0, ys + y0].astype(float)
    print(name, 'area m2', round(len(xs) * S * S, 1))
d = np.load('out/l127/plp2018_crop.npz'); b = d['bands']; scl = d['scl']
x0g, y0g = 460980.0, 4330440.0
B8 = b[7]
# local background: median of water within 300 m
rr, cc = 156, 150
loc = B8[rr - 30:rr + 30, cc - 30:cc + 30]; ls = scl[rr - 30:rr + 30, cc - 30:cc + 30]
bg = float(np.median(loc[ls == 6]))
anom = B8 - bg
bx, by = masks['bottles'].mean(0)
# anchor guess: bottles at pixel centre (159.7, 147.6)
X0 = x0g + 146.0 * 10; Y0 = y0g - 159.5 * 10
best = None
for dx in np.arange(-15, 15.1, 0.5):
    for dy in np.arange(-15, 15.1, 0.5):
        cov = np.zeros_like(B8)
        for name, pts in masks.items():
            X = X0 + dx + (pts[:, 0] - bx) * S; Y = Y0 + dy - (pts[:, 1] - by) * S
            c = np.floor((X - x0g) / 10).astype(int); r = np.floor((y0g - Y) / 10).astype(int)
            np.add.at(cov, (r, c), S * S / 100)
        m = cov > 0
        win = (slice(147, 164), slice(143, 159))
        cw, aw = cov[win].ravel(), anom[win].ravel(); ok = aw < 0.06
        cc_ = np.corrcoef(cw[ok], aw[ok])[0, 1]
        if best is None or cc_ > best[0]:
            best = (cc_, dx, dy)
print('best corr', best)
cc_, dx, dy = best
out = {}
for name, pts in masks.items():
    X = X0 + dx + (pts[:, 0] - bx) * S; Y = Y0 + dy - (pts[:, 1] - by) * S
    c = np.floor((X - x0g) / 10).astype(int); r = np.floor((y0g - Y) / 10).astype(int)
    u, k = np.unique(np.c_[r, c], axis=0, return_counts=True)
    out[name] = [dict(row=int(i), col=int(j), frac=round(float(n * S * S / 100), 3)) for (i, j), n in zip(u, k)]
    out[name + '_centroid_utm'] = [float(X.mean()), float(Y.mean())]
    print(name, [(o['row'], o['col'], o['frac'], round(float(anom[o['row'], o['col']]), 4)) for o in out[name]])
out['registration'] = dict(corr=round(float(cc_), 3), dx=float(dx), dy=float(dy), scale_m_per_thumb_px=S, bg_b8=bg,
                           method='orthophoto thumb, north-up assumed, translation fitted to S2 B8 anomaly (3 targets)')
json.dump(out, open('out/l127/plp2018_targets_px.json', 'w'), indent=1)
