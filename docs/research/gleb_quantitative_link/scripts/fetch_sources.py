"""Fetch primary quantitative marine-litter sources with provenance and checksums."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
SOURCES = {
    "adis/Readme.txt": "https://data.4tu.nl/file/ddede7f5-aca5-42ae-b851-e0bbb9a2c4c2/c81c3caf-e396-4f6d-af29-5676813ac2f2",
    "adis/Objects.csv": "https://data.4tu.nl/file/ddede7f5-aca5-42ae-b851-e0bbb9a2c4c2/a8313b0e-920b-4396-9145-bdccc3dc468c",
    "adis/Segments.csv": "https://data.4tu.nl/file/ddede7f5-aca5-42ae-b851-e0bbb9a2c4c2/1228295f-a835-4eac-81a4-df16c1ca239c",
    "adis/Segments.gpkg": "https://data.4tu.nl/file/ddede7f5-aca5-42ae-b851-e0bbb9a2c4c2/9cf7387a-4f8f-4e1b-9d25-5edea9e7c100",
    "adis/Object_snippets.zip": "https://data.4tu.nl/file/ddede7f5-aca5-42ae-b851-e0bbb9a2c4c2/f8afb7d2-91bf-4377-b425-5be6ad4bba52",
    "ruiz/Ruiz_2020_article.pdf": "https://www.frontiersin.org/journals/marine-science/articles/10.3389/fmars.2020.00308/pdf",
    "ruiz/Table_1.docx": "https://www.frontiersin.org/api/v4/articles/536888/file/Table_1.DOCX/536888_supplementary-materials_tables_1_docx/1",
    "plp2019/PLP2019_dataset.rar": "https://zenodo.org/api/records/3752719/files/PLP2019_dataset.rar/content",
    "fml/FML_full_dataset.zip": "https://www.seanoe.org/data/00950/106148/data/119295.zip",
}


def main() -> None:
    manifest = []
    for name, url in SOURCES.items():
        path = RAW / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            print("fetch", name, flush=True)
            with requests.get(url, stream=True, timeout=(30, 120)) as response:
                response.raise_for_status()
                temporary = path.with_suffix(path.suffix + ".part")
                with temporary.open("wb") as out:
                    for chunk in response.iter_content(1024 * 1024):
                        if chunk:
                            out.write(chunk)
                temporary.replace(path)
        digestor = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                digestor.update(chunk)
        digest = digestor.hexdigest()
        manifest.append({"file": str(path.relative_to(ROOT)), "url": url, "bytes": path.stat().st_size, "sha256": digest})
        print(name, path.stat().st_size, flush=True)
    (ROOT / "data" / "source_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
