"""FML dataset access (data/extra/fml/fml_version2/full_dataset, YOLO txt labels, 1920x1080 jpg)."""
import re
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
FML = ROOT / "data" / "extra" / "fml" / "fml_version2" / "full_dataset"
_TS = re.compile(r"image_(\d{8})_(\d{6})_(\d{6})")


def split_files(split, root=FML):
    return sorted((root / "images" / split).glob("*.jpg"))


def yolo_boxes(label_path, w=1920, h=1080):
    """YOLO (cls cx cy bw bh, normalised) -> xyxy pixels (N,4)."""
    rows = []
    p = Path(label_path)
    if p.exists():
        for line in p.read_text().splitlines():
            parts = line.split()
            if len(parts) != 5:
                continue
            _, cx, cy, bw, bh = map(float, parts)
            rows.append([(cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h])
    return np.asarray(rows, dtype=float).reshape(-1, 4)


def label_path(img_path, split, root=FML):
    return root / "labels" / "yolo_format" / split / (Path(img_path).stem + ".txt")


def load_split(split, root=FML):
    files = split_files(split, root)
    return files, [yolo_boxes(label_path(f, split, root)) for f in files]


def frame_time(name):
    """FML file names carry the capture time: image_YYYYMMDD_HHMMSS_micro.jpg."""
    m = _TS.search(str(name))
    if not m:
        return None
    return datetime.strptime(m.group(1) + m.group(2) + m.group(3), "%Y%m%d%H%M%S%f")
