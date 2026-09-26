"""Extract the original PLP2019 RAR without modifying the source archive."""
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
archive = ROOT / "data/raw/plp2019/PLP2019_dataset.rar"
destination = ROOT / "data/raw/plp2019/extracted"
marker = destination / "PLP2019_dataset/S2_satellite_images_nc"

if not marker.is_dir() or not any(marker.glob("*.nc")):
    destination.mkdir(parents=True, exist_ok=True)
    extractor = shutil.which("bsdtar")
    if extractor is None:
        raise RuntimeError("bsdtar is required to extract the original PLP2019 RAR")
    subprocess.run([extractor, "-xf", str(archive), "-C", str(destination)], check=True)
print("PLP2019 NetCDF files:", len(list(marker.glob("*.nc"))))
