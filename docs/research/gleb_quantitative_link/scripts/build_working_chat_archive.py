"""Make a portable, upload-friendly research archive without multi-GB originals."""
from pathlib import Path
import zipfile


STUDY = Path(__file__).resolve().parents[1]
WORKSPACE = STUDY.parent
DEST = WORKSPACE / "quantitative_link_study_working_chat.zip"

FILES = [
    STUDY / "README.md",
    STUDY / "WORKING_CHAT_CONTEXT.md",
    STUDY / "requirements.txt",
    STUDY / "data/source_inventory.json",
    STUDY / "data/source_manifest.json",
    WORKSPACE / "data/macroplastic_marine_samples.csv",
    STUDY / "data/raw/adis/Readme.txt",
    STUDY / "data/raw/adis/Objects.csv",
    STUDY / "data/raw/adis/Segments.csv",
    STUDY / "data/raw/ruiz/Table_1.docx",
]

FOLDERS = [
    STUDY / "scripts",
    STUDY / "results",
    STUDY / "data/raw/plp2022_23",
    STUDY / "data/raw/weather",
    STUDY / "data/raw/plp2019/extracted/PLP2019_dataset/S2_satellite_images_nc",
    STUDY / "data/raw/plp2019/extracted/PLP2019_dataset/Vector_Points",
]


def main():
    files = set(FILES)
    for folder in FOLDERS:
        files.update(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    files.add(STUDY / "data/raw/plp2019/extracted/PLP2019_dataset/PLP2019 Sentinel-2 Image Catalogue.docx")
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        raise FileNotFoundError(missing)
    with zipfile.ZipFile(DEST, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(WORKSPACE))
    with zipfile.ZipFile(DEST) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"Corrupt ZIP member: {bad}")
        print(f"Archive: {DEST}\nFiles: {len(archive.infolist())}\nBytes: {DEST.stat().st_size}")


if __name__ == "__main__":
    main()
