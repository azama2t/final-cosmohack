"""Extract small PLP2022/23 reference files from the 3.8 GB Zenodo ZIP."""
from pathlib import Path
import json
from remotezip import RemoteZip

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/raw/plp2022_23"
URL = "https://zenodo.org/api/records/10046182/files/PLP2022_PLP2023_min_fraction_dataset.zip/content"

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows=[]
    with RemoteZip(URL) as z:
        wanted=[x for x in z.infolist() if x.filename.endswith(("_L2W.nc", "_UAS.JPG", "_UAS_RGB.JPG"))
                and ("/20220721/" in x.filename or "/20230621/" in x.filename)]
        for f in wanted:
            p=OUT/Path(f.filename).name
            if not p.exists():p.write_bytes(z.read(f.filename))
            rows.append({"member":f.filename,"path":str(p.relative_to(ROOT)),"bytes":p.stat().st_size,"source":URL})
            print(p.name,p.stat().st_size,flush=True)
    (OUT/"manifest.json").write_text(json.dumps(rows,indent=2))

if __name__ == '__main__':main()
