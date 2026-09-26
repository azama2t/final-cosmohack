"""Join Summary.xlsx matchup pins (date, point, lon/lat, FDI, NDVI of the S2 pixel) with ESR Figure 5 (plastic items per
1 m2 sample vs FDI and vs NDVI) by requiring the same N on both panels. Digitised N is approximate (dot radius ~3 items)."""
import re, json, numpy as np
from PIL import Image
from scipy import ndimage
D = 'data/extra/plastic_monitor/'
def mask(fn):
    a = np.asarray(Image.open(D + 'esr_img/' + fn).convert('RGB')).astype(int)
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    m = (B > 150) & (R < 130) & (G > 80) & (G < 150) & (B - R > 60)
    return ndimage.binary_opening(m, iterations=2)
mF = mask('fig5_plastic_fdi_zoom.png'); mN = mask('fig5_plastic_ndvi_zoom.png')
def gmask(fn):
    a = np.asarray(Image.open(D + 'esr_img/' + fn).convert('RGB')).astype(int)
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    return ndimage.binary_opening((G > 160) & (G - B > 60) & (G - R > 30), iterations=2)
oF = gmask('fig5_org_fdi_zoom.png'); oN = gmask('fig5_org_ndvi_zoom.png')
gF = lambda n, f: (int(round(220 + n * (1335 - 220) / 60)), int(round(619 - f / 0.05 * 80.4)))
gN = lambda n, v: (int(round(238 + n * (1320 - 238) / 60)), int(round(467 - v / 0.2 * 78.9)))
# calibrations (zoomed px): FDI panel x0=168, 350->1175; y 0->616, 0.05 per 75.1 px. NDVI panel x0=172, 350->1172; y 0->454, 0.2 per 81.9 px
fF = lambda n, f: (int(round(168 + n * (1175 - 168) / 350)), int(round(616 - f / 0.05 * 75.1)))
fN = lambda n, v: (int(round(172 + n * (1172 - 172) / 350)), int(round(454 - v / 0.2 * 81.9)))
def inside(m, xy):
    x, y = xy
    return 0 <= y < m.shape[0] and 0 <= x < m.shape[1] and m[y, x]
# matchup pins
L = open(D + 'summary_dump.txt', encoding='utf-8').read().splitlines()
prod, rows, coords = None, [], {}
for l in L:
    mm = re.search(r'# Product: \| B\d+=(\S+)', l)
    if mm: prod = mm.group(1); continue
    c = dict(re.findall(r'([A-Z]+)\d+=([^|]*?)\s*(?=\||$)', l))
    if 'G' in c and 'D' in c:
        try: coords[(prod, c['G'].strip())] = (float(c['D']), float(c['E']))
        except ValueError: pass
    for lab, f, v in [(c.get('W'), c.get('X'), c.get('Y')), (c.get('AN'), c.get('AO'), c.get('AP'))]:
        if lab and f and re.fullmatch(r'\d+', lab.strip()):
            try: rows.append((prod, lab.strip(), float(f), float(v)))
            except ValueError: pass
seen, out = set(), []
for prod, lab, f, v in rows:
    d = re.search(r'_(20\d{6})T', prod).group(1)
    if (d, lab) in seen: continue
    seen.add((d, lab))
    ns = [n / 2 for n in range(0, 701) if inside(mF, fF(n / 2, f)) and inside(mN, fN(n / 2, v))]
    groups = []
    for n in ns:
        if groups and n - groups[-1][-1] <= 1.0: groups[-1].append(n)
        else: groups.append([n])
    cands = [round((g[0] + g[-1]) / 2, 1) for g in groups]
    no = [k / 4 for k in range(0, 241) if inside(oF, gF(k / 4, f)) and inside(oN, gN(k / 4, v))]
    og = []
    for n in no:
        if og and n - og[-1][-1] <= 0.5: og[-1].append(n)
        else: og.append([n])
    ocands = [round((g[0] + g[-1]) / 2, 1) for g in og]
    lon, lat = coords.get((prod, lab), (None, None))
    out.append(dict(date=d, product=prod, point=lab, lon=lon, lat=lat, fdi=round(f, 4), ndvi=round(v, 4),
                    n_plastic_digitised=cands[0] if len(cands) == 1 else None, candidates=cands,
                    n_organic_digitised=ocands[0] if len(ocands) == 1 else None, organic_candidates=ocands))
json.dump(out, open('out/l127/pm/fig5_join.json', 'w'), indent=1)
for o in out: print(o['date'], o['point'], o['fdi'], o['ndvi'], o['candidates'], o['organic_candidates'])
print('matched uniquely:', sum(o['n_plastic_digitised'] is not None for o in out), 'of', len(out))
