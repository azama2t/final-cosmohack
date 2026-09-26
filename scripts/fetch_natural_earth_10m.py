"""§51 п.6 bugfix (L142 found shore_km=0 for 19/77 finds — Natural Earth 1:110m collapsed real sea points into
land). Downloads Natural Earth 1:10m land polygons (~3 MB zip) into data_cache/natural_earth/ (gitignored, same
convention as data_cache/forcing — see service/routes_drift_check.py). Used by src/macroplastic/case/alerts.py.

Run:  .venv\\Scripts\\python.exe scripts\\fetch_natural_earth_10m.py
"""
from __future__ import annotations

import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data_cache" / "natural_earth"
URL = "https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_land.zip"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    zip_path = OUT / "ne_10m_land.zip"
    if not (OUT / "ne_10m_land.shp").is_file():
        print(f"скачиваю {URL} -> {zip_path}")
        urllib.request.urlretrieve(URL, zip_path)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(OUT)
        print(f"готово: {OUT}")
    else:
        print(f"уже есть: {OUT / 'ne_10m_land.shp'}")


if __name__ == "__main__":
    sys.exit(main())
