"""Read members of a big remote ZIP over HTTP range requests (no full download).

Used for FloatingObjects (18.9 GB zip on the marinedebrisdetector S3 mirror): only the
central directory + the members we need (shapefiles, selected scenes) are fetched.

  .venv/Scripts/python.exe scripts/extra_data/remote_zip.py --url <zip url> --list out.csv
  .venv/Scripts/python.exe scripts/extra_data/remote_zip.py --url <zip url> --extract "shapefiles/*" --dest data/extra/floatingobjects
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
import io
import shutil
import sys
import time
import zipfile
from pathlib import Path

import requests


class HttpRangeFile(io.RawIOBase):
    """Seekable read-only file object backed by HTTP Range requests (with a small read-ahead cache)."""

    def __init__(self, url: str, block: int = 1 << 20, retries: int = 5):
        self.url, self.block, self.retries = url, block, retries
        self.s = requests.Session()
        r = self.s.head(url, timeout=60, allow_redirects=True)
        r.raise_for_status()
        self.size = int(r.headers["Content-Length"])
        self.pos = 0
        self._cache_start, self._cache = -1, b""
        self.bytes_fetched = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        if whence == 0:
            self.pos = off
        elif whence == 1:
            self.pos += off
        else:
            self.pos = self.size + off
        return self.pos

    def _get(self, a: int, b: int) -> bytes:  # inclusive range [a, b]
        for k in range(self.retries):
            try:
                r = self.s.get(self.url, headers={"Range": f"bytes={a}-{b}"}, timeout=120)
                r.raise_for_status()
                self.bytes_fetched += len(r.content)
                return r.content
            except Exception as e:  # noqa: BLE001
                if k == self.retries - 1:
                    raise
                print(f"range retry {k + 1}: {e}", file=sys.stderr)
                time.sleep(2 * (k + 1))
        return b""

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        n = max(0, min(n, self.size - self.pos))
        if n == 0:
            return b""
        cs, c = self._cache_start, self._cache
        if cs <= self.pos and self.pos + n <= cs + len(c):
            out = c[self.pos - cs:self.pos - cs + n]
        else:
            want = max(n, self.block)
            end = min(self.size, self.pos + want) - 1
            data = self._get(self.pos, end)
            self._cache_start, self._cache = self.pos, data
            out = data[:n]
        self.pos += len(out)
        return out

    def readinto(self, b):
        d = self.read(len(b))
        b[:len(d)] = d
        return len(d)


def open_remote_zip(url: str) -> tuple[zipfile.ZipFile, HttpRangeFile]:
    f = HttpRangeFile(url)
    return zipfile.ZipFile(f), f


def extract(zf: zipfile.ZipFile, fh: HttpRangeFile, names: list[str], dest: Path, strip: int = 0) -> list[Path]:
    """Stream-extract members (big block reads, skip existing files of the right size)."""
    out = []
    for n in names:
        info = zf.getinfo(n)
        parts = Path(n).parts[strip:]
        tgt = dest.joinpath(*parts)
        if tgt.is_file() and tgt.stat().st_size == info.file_size:
            out.append(tgt)
            continue
        tgt.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        fh.block = max(64 << 10, min(16 << 20, info.compress_size + 4096))  # no over-fetch on small members
        tmp = tgt.with_suffix(tgt.suffix + ".part")
        with zf.open(info) as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst, 8 << 20)
        tmp.replace(tgt)
        fh.block = 1 << 20
        print(f"{n}: {info.file_size / 1e6:.1f} MB in {time.time() - t0:.0f}s", flush=True)
        out.append(tgt)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--list", help="write member list csv")
    ap.add_argument("--extract", nargs="*", default=[], help="fnmatch patterns of members")
    ap.add_argument("--dest", default=".")
    ap.add_argument("--strip", type=int, default=0)
    a = ap.parse_args()
    zf, fh = open_remote_zip(a.url)
    infos = zf.infolist()
    print(f"{len(infos)} members, zip {fh.size / 1e9:.2f} GB, fetched {fh.bytes_fetched / 1e6:.1f} MB for directory")
    if a.list:
        Path(a.list).parent.mkdir(parents=True, exist_ok=True)
        with open(a.list, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["name", "file_size", "compress_size", "compress_type", "header_offset"])
            for i in infos:
                w.writerow([i.filename, i.file_size, i.compress_size, i.compress_type, i.header_offset])
    if a.extract:
        names = [i.filename for i in infos if not i.is_dir() and any(fnmatch.fnmatch(i.filename, p) for p in a.extract)]
        print(f"extracting {len(names)} members, {sum(zf.getinfo(n).file_size for n in names) / 1e6:.1f} MB")
        extract(zf, fh, names, Path(a.dest), a.strip)
    print(f"total fetched {fh.bytes_fetched / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
