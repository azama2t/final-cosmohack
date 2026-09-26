"""L117: read a COG (anonymous HTTPS) at an overview level or a full-res window; save PNG to data/extra (NOT git).
Usage:
  python cog_preview.py URL out.png --max 1500                       # whole tile, downsampled
  python cog_preview.py URL out.png --win lon0 lat0 lon1 lat1 [--max 3000]   # geographic window, full res (capped)
"""
import argparse
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--max", type=int, default=1500)
    ap.add_argument("--win", type=float, nargs=4)
    a = ap.parse_args()
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", AWS_NO_SIGN_REQUEST="YES"):
        with rasterio.open(a.url) as ds:
            print("crs", ds.crs, "size", ds.width, ds.height, "res", ds.res, "bands", ds.count, ds.dtypes[0])
            if a.win:
                b = transform_bounds("EPSG:4326", ds.crs, *a.win)
                w = from_bounds(*b, transform=ds.transform).round_offsets().round_lengths()
            else:
                w = rasterio.windows.Window(0, 0, ds.width, ds.height)
            scale = max(1.0, max(w.width, w.height) / a.max)
            oh, ow = int(w.height / scale), int(w.width / scale)
            arr = ds.read(indexes=list(range(1, min(3, ds.count) + 1)), window=w, out_shape=(min(3, ds.count), oh, ow),
                          resampling=Resampling.average, boundless=False)
            print("window", w, "out", arr.shape, "effective px m", ds.res[0] * scale)
    arr = np.moveaxis(arr, 0, -1)
    if arr.dtype != np.uint8:
        lo, hi = np.percentile(arr[arr > 0], [1, 99]) if (arr > 0).any() else (0, 1)
        arr = np.clip((arr - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr.squeeze()).save(a.out)
    print("saved", a.out)


if __name__ == "__main__":
    main()
