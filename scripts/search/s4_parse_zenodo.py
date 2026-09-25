"""Dump all sheets of the DOORS-3 Zenodo xlsx files (no openpyxl) -> data/search/s4/src_csv/<file>__<sheet>.csv."""
import re, zipfile, csv
import xml.etree.ElementTree as ET
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data/search/s4/src"
OUT = ROOT / "data/search/s4/src_csv"
M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

def col(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1

def read_xlsx(path):
    z = zipfile.ZipFile(path)
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(M + "si"):
            ss.append("".join(t.text or "" for t in si.iter(M + "t")))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    out = {}
    for sh in ET.fromstring(z.read("xl/workbook.xml")).iter(M + "sheet"):
        tgt = rels[sh.get(R + "id")].lstrip("/")
        tgt = tgt if tgt.startswith("xl/") else "xl/" + tgt
        rows = []
        for r in ET.fromstring(z.read(tgt)).iter(M + "row"):
            row = {}
            for c in r.findall(M + "c"):
                v = c.find(M + "v"); t = c.get("t")
                if v is None:
                    is_ = c.find(M + "is")
                    val = "".join(x.text or "" for x in is_.iter(M + "t")) if is_ is not None else ""
                else:
                    val = ss[int(v.text)] if t == "s" else v.text
                row[col(c.get("r"))] = val
            rows.append([row.get(i, "") for i in range(max(row) + 1)] if row else [])
        out[sh.get("name")] = rows
    return out

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for p in sorted(SRC.glob("*.xlsx")):
        for name, rows in read_xlsx(p).items():
            w = max((len(r) for r in rows), default=0)
            f = OUT / f"{p.stem}__{re.sub(r'[^A-Za-z0-9_-]+', '_', name)}.csv"
            with open(f, "w", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerows([r + [""] * (w - len(r)) for r in rows])
            print(f.name, len(rows), "rows;", rows[0][:12] if rows else "")
