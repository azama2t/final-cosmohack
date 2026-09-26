"""Минимальный читатель xlsx без openpyxl (в окружении его нет): каждый лист -> CSV.
  .venv/Scripts/python.exe -X utf8 scripts/search/qpairs/xlsx2csv.py <file.xlsx> <outdir>"""
import zipfile, re, csv, sys, pathlib, xml.etree.ElementTree as ET
NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
def col_idx(ref):
    s = re.match(r'([A-Z]+)', ref).group(1); n = 0
    for ch in s: n = n * 26 + ord(ch) - 64
    return n - 1
def convert(path, outdir):
    z = zipfile.ZipFile(path); outdir = pathlib.Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    ss = []
    if 'xl/sharedStrings.xml' in z.namelist():
        for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', NS):
            ss.append(''.join(t.text or '' for t in si.iter('{%s}t' % NS['m'])))
    wb = ET.fromstring(z.read('xl/workbook.xml'))
    rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    rmap = {r.get('Id'): r.get('Target') for r in rels}
    for sh in wb.find('m:sheets', NS):
        name = sh.get('name'); tgt = rmap[sh.get('{%s}id' % NS['r'])]
        tgt = tgt.lstrip('/'); tgt = tgt if tgt.startswith('xl/') else 'xl/' + tgt
        root = ET.fromstring(z.read(tgt)); rows = []
        for row in root.iter('{%s}row' % NS['m']):
            vals = {}
            for c in row.findall('m:c', NS):
                t = c.get('t'); v = c.find('m:v', NS)
                if t == 's' and v is not None: val = ss[int(v.text)]
                elif t == 'inlineStr': val = ''.join(x.text or '' for x in c.iter('{%s}t' % NS['m']))
                else: val = v.text if v is not None else ''
                vals[col_idx(c.get('r'))] = val
            if vals: rows.append([vals.get(i, '') for i in range(max(vals) + 1)])
        fn = outdir / (re.sub(r'[^\w-]+', '_', name) + '.csv')
        with open(fn, 'w', newline='', encoding='utf-8') as f: csv.writer(f).writerows(rows)
        print(name, len(rows), '->', fn)
if __name__ == '__main__':
    convert(sys.argv[1], sys.argv[2])
