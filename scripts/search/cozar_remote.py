"""Remote HTTP-range reader for the Cózar/Arias 2024 WASP litter-windrow NetCDF-4 (Zenodo 11045944).

The full file is 2.06 GB (> 1 GB budget), so we never download it: h5py reads only the byte ranges it needs
through a block-cached, Range-request file object. Every fetched block is cached on disk in
data/extra/cozar2024/blocks/ so re-runs cost no network.

  .venv/Scripts/python.exe scripts/search/cozar_remote.py meta      -> data/extra/cozar2024/filaments_meta.csv
  .venv/Scripts/python.exe scripts/search/cozar_remote.py layout    -> prints dataset layouts (chunks/offsets)
"""
from __future__ import annotations

import io
import sys
import threading
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[2]
URL = ("https://zenodo.org/api/records/11045944/files/"
       "WASP_LW_SENT2_MED_L1C_B_201506_202109_10m_6y_NRT_v1.0.nc/content")
SIZE = 2064602440
OUT = ROOT / "data" / "extra" / "cozar2024"
BLK = OUT / "blocks"
BLOCK = 1 << 20  # 1 MiB


class RangeFile(io.RawIOBase):
    def __init__(self, url=URL, size=SIZE, block=BLOCK):
        self.url, self.size, self.block, self.pos = url, size, block, 0
        self.s = requests.Session()
        self.mem: dict[int, bytes] = {}
        self.fetched_bytes = 0
        self.lock = threading.Lock()
        BLK.mkdir(parents=True, exist_ok=True)

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.size + off)
        return self.pos

    def _get(self, bi: int) -> bytes:
        if bi in self.mem:
            return self.mem[bi]
        p = BLK / f"{bi:06d}.bin"
        if p.exists():
            b = p.read_bytes()
        else:
            a = bi * self.block
            e = min(self.size, a + self.block) - 1
            for attempt in range(5):
                try:
                    r = self.s.get(self.url, headers={"Range": f"bytes={a}-{e}"}, timeout=120)
                    if r.status_code == 206 and len(r.content) == e - a + 1:
                        break
                except requests.RequestException:
                    pass
            else:
                raise IOError(f"range {a}-{e} failed")
            b = r.content
            p.write_bytes(b)
            self.fetched_bytes += len(b)
        if len(self.mem) > 256:
            self.mem.clear()
        self.mem[bi] = b
        return b

    def readinto(self, buf):
        n = len(buf)
        if self.pos >= self.size or n == 0:
            return 0
        n = min(n, self.size - self.pos)
        out = memoryview(buf)
        done = 0
        while done < n:
            bi, o = divmod(self.pos + done, self.block)
            b = self._get(bi)
            k = min(n - done, len(b) - o)
            out[done:done + k] = b[o:o + k]
            done += k
        self.pos += n
        return n


def open_remote():
    import h5py
    rf = RangeFile()
    return rf, h5py.File(io.BufferedReader(rf, buffer_size=BLOCK), "r")


def cmd_layout():
    rf, f = open_remote()
    for k, d in f.items():
        try:
            off = d.id.get_offset()
        except Exception:
            off = None
        print(k, d.shape, d.dtype, "chunks", d.chunks, "compression", d.compression, "offset", off)
    print("attrs", dict(f.attrs))
    print("fetched MB", rf.fetched_bytes / 1e6)


def cmd_meta():
    import pandas as pd
    rf, f = open_remote()
    prod = f["s2_product"][:]
    prod = ["".join(c.decode() if isinstance(c, bytes) else str(c) for c in row).strip() for row in prod]
    df = pd.DataFrame({
        "fil_idx": np.arange(len(prod)),
        "s2_product": prod,
        "dec_time": f["dec_time"][:],
        "x_centroid": f["x_centroid"][:],
        "y_centroid": f["y_centroid"][:],
        "lat_centroid": f["lat_centroid"][:],
        "lon_centroid": f["lon_centroid"][:],
        "n_pixels_fil": f["n_pixels_fil"][:],
    })
    lim = f["limits"][:]
    for i, c in enumerate(["x_lower", "y_lower", "x_upper", "y_upper"]):
        df[c] = lim[:, i]
    df.to_csv(OUT / "filaments_meta.csv", index=False)
    print(df.head().to_string())
    print(len(df), "fetched MB", rf.fetched_bytes / 1e6)


def read_filament(f, i: int, n: int):
    """pixel x, y (10 m image coords) and 13-band L1C TOA spectra for filament i (first n valid pixels)."""
    px = f["pixel_x"][i, :n]
    py = f["pixel_y"][i, :n]
    sp = f["pixel_spec"][i, :n, :]
    return px, py, sp


if __name__ == "__main__":
    {"layout": cmd_layout, "meta": cmd_meta}[sys.argv[1]]()
