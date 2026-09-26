"""Fetch the FML dataset (SEANOE doi:10.17882/106148, CC BY 4.0) without downloading the whole archive.

The v2 archive is 4.5 GB (full_dataset + single_sets duplicates). We read the zip central directory over
HTTP Range requests and extract only the members we need (full_dataset/**), keeping data/extra/fml/ <= 3 GB.

  python scripts/photo_count/fetch.py --list            # print top-level structure + sizes
  python scripts/photo_count/fetch.py --prefix <p> ...   # extract members whose name starts with prefix
"""
import argparse
import io
import sys
import time
import urllib.request
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
URLS = {"v1": "https://www.seanoe.org/data/00950/106148/data/119295.zip",
        "v2": "https://www.seanoe.org/data/00950/106148/data/120969.zip"}
OUT = ROOT / "data" / "extra" / "fml"


class HttpRangeFile(io.RawIOBase):
    """Seekable read-only file over HTTP Range (enough for zipfile)."""

    def __init__(self, url):
        self.url, self.pos = url, 0
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as r:
            self.size = int(r.headers["Content-Length"])

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.size + off)
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        if n == 0 or self.pos >= self.size:
            return b""
        end = min(self.pos + n, self.size) - 1
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={self.pos}-{end}"})
        for attempt in range(8):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = r.read()
                break
            except Exception:  # noqa: BLE001 - network retry (SEANOE returns sporadic 500s)
                if attempt == 7:
                    raise
                time.sleep(1.5 * (attempt + 1))
        self.pos += len(data)
        return data

    def readinto(self, b):
        d = self.read(len(b))
        b[:len(d)] = d
        return len(d)


def open_remote(version):
    f = HttpRangeFile(URLS[version])
    return zipfile.ZipFile(io.BufferedReader(f, buffer_size=1 << 20)), f


def fetch_range(url, a, b, chunk=8 << 20):
    """Bytes [a, b) via several Range requests with retry/backoff (SEANOE drops long transfers)."""
    out = bytearray()
    pos = a
    while pos < b:
        e = min(pos + chunk, b) - 1
        for attempt in range(10):
            try:
                req = urllib.request.Request(url, headers={"Range": f"bytes={pos}-{e}"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = r.read()
                if len(data) != e - pos + 1:
                    raise IOError("short read")
                break
            except Exception:  # noqa: BLE001
                if attempt == 9:
                    raise
                time.sleep(2 * (attempt + 1))
        out += data
        pos = e + 1
    return bytes(out)


def extract_batch(url, batch, next_offset, dest):
    """batch: ZipInfo sorted by header_offset, contiguous in archive; next_offset: end bound."""
    import zlib
    a = batch[0].header_offset
    buf = fetch_range(url, a, next_offset)
    n = 0
    for info in batch:
        o = info.header_offset - a
        name_len = int.from_bytes(buf[o + 26:o + 28], "little")
        extra_len = int.from_bytes(buf[o + 28:o + 30], "little")
        s = o + 30 + name_len + extra_len
        raw = buf[s:s + info.compress_size]
        data = raw if info.compress_type == zipfile.ZIP_STORED else zlib.decompressobj(-15).decompress(raw)
        if len(data) != info.file_size:
            raise IOError(f"size mismatch {info.filename}")
        t = dest / info.filename
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_bytes(data)
        n += 1
    return n


PUB_WEIGHTS = ("https://drive.usercontent.google.com/download?id=1bNMgN5IVxik95jrWw-TyVqr65K6GziNP"
               "&export=download&confirm=t")
PUB_SHA256 = "86813d453c732a727844e8f9cfa9584de85bb6452861a6213991453afff2e0d4"


def fetch_weights():
    """fasterrcnn_fml.pth from the FML authors' Google Drive folder 1QTGO9rDWCKzvo5DlphT6a4IzraDRwJ4o (165.7 MB)."""
    import hashlib
    dst = ROOT / "weights_exp" / "photo_count" / "fasterrcnn_fml_published.pth"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        with urllib.request.urlopen(PUB_WEIGHTS, timeout=600) as r:
            dst.write_bytes(r.read())
    h = hashlib.sha256(dst.read_bytes()).hexdigest()
    print(dst, h, "OK" if h == PUB_SHA256 else "SHA256 MISMATCH")
    return 0 if h == PUB_SHA256 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v2", choices=list(URLS))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--depth", type=int, default=4)
    ap.add_argument("--prefix", nargs="*", default=[])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--weights", action="store_true",
                    help="download the authors' published Faster R-CNN weights into weights_exp/photo_count/")
    a = ap.parse_args()
    if a.weights:
        return fetch_weights()
    zf, _ = open_remote(a.version)
    infos = zf.infolist()
    if a.list:
        cnt, size = Counter(), Counter()
        for i in infos:
            key = "/".join(i.filename.split("/")[:a.depth])
            cnt[key] += 1
            size[key] += i.file_size
        for k in sorted(cnt):
            print(f"{cnt[k]:6d} {size[k] / 1e6:9.1f} MB  {k}")
        print(f"total {len(infos)} members, {sum(i.file_size for i in infos) / 1e9:.2f} GB")
        return 0
    sel = [i for i in infos if not i.is_dir() and any(i.filename.startswith(p) for p in a.prefix)]
    total = sum(i.file_size for i in sel)
    print(f"extracting {len(sel)} members, {total / 1e9:.2f} GB -> {OUT}", flush=True)
    if total > 3e9:
        print("refusing: > 3 GB", file=sys.stderr)
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    offsets = sorted(i.header_offset for i in infos) + [zf.start_dir]
    nxt = {o: offsets[k + 1] for k, o in enumerate(offsets[:-1])}
    todo = sorted((i for i in sel if not ((OUT / i.filename).exists()
                                          and (OUT / i.filename).stat().st_size == i.file_size)),
                  key=lambda i: i.header_offset)
    batches, cur = [], []
    for i in todo:  # contiguous runs, <= 64 MB each
        if cur and (nxt[cur[-1].header_offset] != i.header_offset
                    or nxt[i.header_offset] - cur[0].header_offset > (64 << 20)):
            batches.append(cur)
            cur = []
        cur.append(i)
    if cur:
        batches.append(cur)
    print(f"{len(todo)} to fetch in {len(batches)} batches", flush=True)
    done = 0
    with ThreadPoolExecutor(a.workers) as ex:
        for n in ex.map(lambda b: extract_batch(URLS[a.version], b, nxt[b[-1].header_offset], OUT), batches):
            done += n
            print(f"  {done}/{len(todo)}", flush=True)
    print("ok", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
