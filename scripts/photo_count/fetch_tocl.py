"""Sample of The Ocean Cleanup River Monitoring System labelled dataset (4TU 10.4121/fad0aa03-37b4-4a3c-8263-8ed60a03b393,
CC BY-NC 4.0 — non-commercial): up to N validation images per location, read from the 2.4 GB zip over HTTP Range
(no full download). Annotations (COCO) are already local (L112: data/extra/count_ds/tocl_rms/).

  python scripts/photo_count/fetch_tocl.py --per-location 10
"""
import argparse
import collections
import io
import json
import sys
import urllib.request
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "photo_count"))
import fetch as F  # noqa: E402

URL = "https://data.4tu.nl/file/fad0aa03-37b4-4a3c-8263-8ed60a03b393/4f22c1dc-b6f4-4dca-984b-5983dc41cacd"
BASE = ROOT / "data" / "extra" / "count_ds" / "tocl_rms" / "Supplementary materials C - label dataset"
PREFIX = "Supplementary materials C - label dataset/"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-location", type=int, default=10)
    a = ap.parse_args()
    with urllib.request.urlopen(urllib.request.Request(URL, method="HEAD"), timeout=60) as r:
        url = r.geturl()
    val = json.loads((BASE / "val" / "annotations" / "val_annotations.json").read_text(encoding="utf-8"))
    by_loc = collections.defaultdict(list)
    for im in sorted(val["images"], key=lambda x: x["file_name"]):
        by_loc[im["location"]].append(im["file_name"])
    want = set()
    for loc, names in by_loc.items():
        step = max(1, len(names) // a.per_location)
        want |= set(names[::step][:a.per_location])
    f = F.HttpRangeFile(url)
    zf = zipfile.ZipFile(io.BufferedReader(f, buffer_size=1 << 20))
    infos = {i.filename: i for i in zf.infolist()}
    offs = sorted(i.header_offset for i in infos.values()) + [zf.start_dir]
    nxt = {o: offs[k + 1] for k, o in enumerate(offs[:-1])}
    n = 0
    for name in sorted(want):
        key = PREFIX + "val/images/" + name
        info = infos.get(key)
        if info is None:
            continue
        t = BASE / "val" / "images" / name
        if t.exists() and t.stat().st_size == info.file_size:
            continue
        buf = F.fetch_range(url, info.header_offset, nxt[info.header_offset])
        nl = int.from_bytes(buf[26:28], "little")
        el = int.from_bytes(buf[28:30], "little")
        raw = buf[30 + nl + el:30 + nl + el + info.compress_size]
        data = raw if info.compress_type == zipfile.ZIP_STORED else zlib.decompressobj(-15).decompress(raw)
        t.write_bytes(data)
        n += 1
    print(f"locations {len(by_loc)}, wanted {len(want)}, fetched {n}")


if __name__ == "__main__":
    main()
