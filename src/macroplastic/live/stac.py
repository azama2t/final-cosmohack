"""STAC search + harmonized Sentinel-2 L2A crops for live scenes.

Scale/offset rule (SPEC §2): reflectance = DN * scale + offset, where scale/offset come ONLY from the
asset's STAC `raster:bands` (Earth Search v1). Earth Search README ("Gain/Offset in Items after Jan 25, 2022"):
"If the `offset` is anything other than 0, then it should be applied after the scale factor". No date-based
"-1000 DN" rule is used anywhere. Planetary Computer items carry no `raster:bands` offset; for them the
BOA_ADD_OFFSET / QUANTIFICATION_VALUE are read from the product metadata XML of the same item.

Output of one scene: see write_scene() and docs/CONTRACTS.md.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

for _k, _v in {
    "AWS_NO_SIGN_REQUEST": "YES",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff,.jp2,.xml",
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "2",
    "VSI_CACHE": "TRUE",
}.items():
    os.environ.setdefault(_k, _v)

import rasterio  # noqa: E402
from rasterio.crs import CRS  # noqa: E402
from rasterio.enums import Resampling  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from rasterio.warp import reproject, transform_bounds, calculate_default_transform  # noqa: E402
from rasterio.windows import from_bounds  # noqa: E402

EARTH_SEARCH = "https://earth-search.aws.element84.com/v1"
PLANETARY = "https://planetarycomputer.microsoft.com/api/stac/v1"

BANDS = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"]
ES_ASSET = {"B1": "coastal", "B2": "blue", "B3": "green", "B4": "red", "B5": "rededge1", "B6": "rededge2",
            "B7": "rededge3", "B8": "nir", "B8A": "nir08", "B9": "nir09", "B11": "swir16", "B12": "swir22",
            "SCL": "scl"}
PC_ASSET = {b: ("B8A" if b == "B8A" else f"B{int(b[1:]):02d}") for b in BANDS} | {"SCL": "SCL"}

# Regions: centre = median of MARIDA Marine-Debris pixels on that tile (computed from data/MARIDA.zip _cl.tif,
# see out/l6/marida_md_scenes.csv). md_px = MD pixels of MARIDA on the tile (all dates).
REGIONS = {
    "honduras": dict(region_name="Гондурасский залив (Омоа – Пуэрто-Кортес – устье Мотагуа)",
                     region_name_en="Gulf of Honduras (Omoa / Puerto Cortes / Motagua mouth)", country="Гондурас / Гватемала",
                     tile="16PCC", center=(-88.22, 15.80), md_px=1496,  # between MD median (15.90) and Motagua/Omoa coast
                     marida_dates=["2016-09-04", "2020-09-18", "2020-09-23", "2019-01-27", "2016-11-03"]),
    "haiti": dict(region_name="Залив Порт-о-Пренс (Гаити)", region_name_en="Port-au-Prince Bay, Haiti", country="Гаити",
                  tile="18QYF", center=(-72.55, 18.72), md_px=1112,
                  marida_dates=["2020-12-29", "2020-03-14", "2020-09-15", "2020-03-19", "2021-01-03"]),
    "durban": dict(region_name="Дурбан", region_name_en="Durban, South Africa", country="ЮАР",
                   tile="36JUN", center=(31.09, -29.83), md_px=46, marida_dates=["2019-04-24"]),
    "jakarta": dict(region_name="Джакартский залив", region_name_en="Jakarta Bay, Indonesia", country="Индонезия",
                    tile="48MXU", center=(106.50, -5.89), md_px=232,  # 48MXU 208 + 48MYU 24
                    marida_dates=["2018-12-06", "2019-05-25"]),
    # Live scenes additions. centre = middle of the MARIDA MD bbox of the tile (reports/marida_regions.md), nudged to water.
    "manila": dict(region_name="Манильский залив", region_name_en="Manila Bay, Philippines", country="Филиппины",
                   tile="51PTS", center=(120.82, 14.60), md_px=38, marida_dates=["2016-07-17", "2019-05-18"]),
    "danang": dict(region_name="Дананг (устье Тхубон, Хойан)", region_name_en="Da Nang / Hoi An, Vietnam",
                   country="Вьетнам", tile="48PZC", center=(108.50, 15.84), md_px=24,
                   marida_dates=["2018-01-18", "2018-11-14", "2019-11-24"]),
    "bali": dict(region_name="Бали (пролив Бадунг)", region_name_en="Bali (Badung Strait), Indonesia", country="Индонезия",
                 tile="50LLR", center=(115.35, -8.83), md_px=41, marida_dates=["2018-03-04"]),
    "scotland": dict(region_name="Шотландия (Ферт-оф-Форт)", region_name_en="Firth of Forth, Scotland", country="Великобритания",
                     tile="30VWH", center=(-2.46, 56.18), md_px=27, marida_dates=["2018-04-20"]),
    "santo_domingo": dict(region_name="Санто-Доминго (устье Осамы)", region_name_en="Santo Domingo (Ozama mouth)",
                          country="Доминиканская Республика", tile="19QDA", center=(-69.82, 18.40), md_px=0,
                          marida_dates=["2019-01-11"]),
    # reserve regions without MARIDA labels (fresh scenes only); boxes at large river mouths / ports
    "tiber": dict(region_name="Средиземное море: устье Тибра (Рим, Остия)", region_name_en="Tiber mouth, Rome (Mediterranean)",
                  country="Италия", tile="33TTG", center=(12.22, 41.72), md_px=0, marida_dates=[]),
    "accra": dict(region_name="Аккра (лагуна Корле, порт Тема)", region_name_en="Accra (Korle Lagoon), Ghana", country="Гана",
                  tile="30NZM", center=(-0.14, 5.50), md_px=0, marida_dates=[]),
    "lagos": dict(region_name="Лагос (вход в лагуну, порт Апапа)", region_name_en="Lagos harbour entrance, Nigeria",
                  country="Нигерия", tile="31NEG", center=(3.40, 6.30), md_px=0, marida_dates=[]),
    # Live scenes: river-plastic "hotspots" outside MARIDA (fresh 2025-2026 scenes only); centre +-12.5 km checked inside the tile
    "mumbai": dict(region_name="Мумбаи (устье Ульхаса, крики Васаи и Малад)", region_name_en="Mumbai (Ulhas / Vasai Creek), India",
                   country="Индия", tile="43QBB", center=(72.78, 19.20), md_px=0, marida_dates=[]),
    "karachi": dict(region_name="Карачи (порт, устье Лиари)", region_name_en="Karachi harbour (Lyari mouth), Pakistan",
                    country="Пакистан", tile="42RTN", center=(66.95, 24.80), md_px=0, marida_dates=[]),
    "nile": dict(region_name="Дельта Нила (устье Розетты)", region_name_en="Nile Delta (Rosetta mouth), Egypt",
                 country="Египет", tile="36RTV", center=(30.38, 31.485), md_px=0, marida_dates=[]),
    "mekong": dict(region_name="Дельта Меконга (устья Тьеу и Дай)", region_name_en="Mekong Delta (Cua Tieu / Cua Dai), Vietnam",
                   country="Вьетнам", tile="48PXS", center=(106.80, 10.15), md_px=0, marida_dates=[]),
    "guanabara": dict(region_name="Залив Гуанабара (Рио-де-Жанейро)", region_name_en="Guanabara Bay, Rio de Janeiro",
                      country="Бразилия", tile="23KPQ", center=(-43.15, -22.82), md_px=0, marida_dates=[]),
    "ganges": dict(region_name="Устье Хугли (дельта Ганга, остров Сагар)", region_name_en="Hooghly mouth (Ganges Delta, Sagar Island)",
                   country="Индия", tile="45QXD", center=(88.10, 21.58), md_px=0, marida_dates=[]),
}

# SCL classes (Sen2Cor): 0 nodata, 1 saturated/defective, 2 dark area, 3 cloud shadow, 4 vegetation,
# 5 not vegetated, 6 water, 7 unclassified, 8 cloud medium, 9 cloud high, 10 thin cirrus, 11 snow.
SCL_BAD = (0, 1, 3, 8, 9, 10, 11)
SCL_CLOUD = (8, 9, 10)


def open_client(source: str):
    from pystac_client import Client
    if source == "planetary-computer":
        import planetary_computer
        return Client.open(PLANETARY, modifier=planetary_computer.sign_inplace)
    return Client.open(EARTH_SEARCH)


def search(source: str, lon: float, lat: float, datetime: str, max_cloud: float = 100, tile: str | None = None,
           max_items: int = 500):
    """Items intersecting the point, optionally restricted to one MGRS tile, sorted by tile cloud cover."""
    client = open_client(source)
    coll = "sentinel-2-l2a"
    items = list(client.search(collections=[coll], intersects=dict(type="Point", coordinates=[lon, lat]),
                               datetime=datetime, query={"eo:cloud_cover": {"lt": max_cloud}},
                               max_items=max_items).items())
    if tile:
        items = [it for it in items if tile_of(it) == tile]
    # keep the latest reprocessing per (tile, date): Earth Search ids end with _0/_1/..
    best = {}
    for it in items:
        key = (tile_of(it), it.properties["datetime"][:10])
        if key not in best or it.id > best[key].id:
            best[key] = it
    return sorted(best.values(), key=lambda i: i.properties.get("eo:cloud_cover", 100))


def tile_of(item) -> str:
    p = item.properties
    if "grid:code" in p:
        return p["grid:code"].replace("MGRS-", "")
    if "s2:mgrs_tile" in p:
        return p["s2:mgrs_tile"]
    m = re.search(r"_(\d\d[A-Z]{3})_", item.id)
    return m.group(1) if m else ""


def source_of(item) -> str:
    return "planetary-computer" if "planetarycomputer" in (item.get_self_href() or "") or \
        any("blob.core.windows.net" in a.href for a in item.assets.values()) else "earth-search"


def item_epsg(item) -> int:
    p = item.properties
    for k in ("proj:epsg",):
        if k in p and p[k]:
            return int(p[k])
    if "proj:code" in p:
        return int(str(p["proj:code"]).split(":")[-1])
    a = item.assets[ES_ASSET["B4"]] if "red" in item.assets else item.assets["B04"]
    if "proj:epsg" in a.extra_fields:
        return int(a.extra_fields["proj:epsg"])
    return int(str(a.extra_fields["proj:code"]).split(":")[-1])


def crop_bounds(item, lon: float, lat: float, size_m: float = 25_000, snap: float = 60.0):
    """Square UTM box (tile CRS) of size_m around (lon, lat), snapped to the 60 m grid of the tile."""
    from pyproj import Transformer
    epsg = item_epsg(item)
    x, y = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform(lon, lat)
    h = size_m / 2
    b = [math.floor((x - h) / snap) * snap, math.floor((y - h) / snap) * snap,
         math.ceil((x + h) / snap) * snap, math.ceil((y + h) / snap) * snap]
    return epsg, b


def _scale_offset_es(asset):
    rb = asset.extra_fields.get("raster:bands") or [{}]
    return float(rb[0].get("scale", 1.0)), float(rb[0].get("offset", 0.0))


_PC_XML_CACHE: dict = {}


def _pc_offsets(item):
    """PC: BOA_ADD_OFFSET per band id + QUANTIFICATION_VALUE from the product metadata XML (metadata, not date)."""
    if item.id in _PC_XML_CACHE:
        return _PC_XML_CACHE[item.id]
    import requests
    href = item.assets["product-metadata"].href
    txt = requests.get(href, timeout=60).text
    q = re.search(r"<BOA_QUANTIFICATION_VALUE[^>]*>([\d.]+)<", txt)
    quant = float(q.group(1)) if q else 10000.0
    offs = {int(m.group(1)): float(m.group(2)) for m in
            re.finditer(r'<BOA_ADD_OFFSET band_id="(\d+)">(-?[\d.]+)<', txt)}
    _PC_XML_CACHE[item.id] = (quant, offs)
    return quant, offs


_PC_BAND_ID = {"B1": 0, "B2": 1, "B3": 2, "B4": 3, "B5": 4, "B6": 5, "B7": 6, "B8": 7, "B8A": 8, "B9": 9,
               "B10": 10, "B11": 11, "B12": 12}


def es_offset_in_pixels(item, dn_b12: np.ndarray, scl: np.ndarray):
    """Earth Search only. Is the BOA_ADD_OFFSET (+1000 DN) still inside the COG pixels?

    Metadata is inconsistent (measured 25.09.2026 on tile 16PCC, 31 items 2021-10..2026-06): every item of baseline
    >= 04.00 has raster:bands offset=-0.1; 30 have earthsearch:boa_offset_applied=True, one (S2C_16PCC_20250219_0,
    sentinel2-to-stac 2025.03.06) has False -- yet in ALL of them open-water B12 DN is 10..240, i.e. the offset has
    already been removed (with the offset inside, water DN would be >= ~1000, because DN<1000 = negative reflectance).
    Rule: offset 0 in raster:bands -> nothing to apply; otherwise decide by the data (median B12 DN of SCL-6 water
    >= 1000 -> offset is in the pixels -> apply raster:bands offset); if < 500 water px, fall back to the flag.
    Returns (in_pixels: bool, evidence: str)."""
    _, o = _scale_offset_es(item.assets[ES_ASSET["B12"]])
    flag = item.properties.get("earthsearch:boa_offset_applied")
    if o == 0:
        return False, "raster:bands offset 0 (pre-04.00 item)"
    w = (scl == 6) & (dn_b12 > 0)
    if w.sum() >= 500:
        med = float(np.median(dn_b12[w]))
        return med >= 1000, (f"raster:bands offset {o}; boa_offset_applied={flag}; SCL-6 water B12 median DN={med:.0f} "
                             f"-> offset {'still in pixels, applied' if med >= 1000 else 'already removed, not applied'}")
    return (flag is not True), f"raster:bands offset {o}; <500 water px, fall back to boa_offset_applied={flag}"


def band_scale_offset(item, band: str, offset_in_pixels: bool | None = None):
    """(scale, offset, note) so that reflectance = DN*scale + offset.
    Earth Search: scale from raster:bands; raster:bands offset applied only if offset_in_pixels (see
    es_offset_in_pixels); if not given, the item flag earthsearch:boa_offset_applied decides (True -> not applied)."""
    if source_of(item) == "earth-search":
        s, o = _scale_offset_es(item.assets[ES_ASSET[band]])
        if o == 0:
            return s, 0.0, "raster:bands"
        if offset_in_pixels is None:
            offset_in_pixels = item.properties.get("earthsearch:boa_offset_applied") is not True
        if not offset_in_pixels:
            return s, 0.0, "raster:bands scale; raster:bands offset NOT applied (already removed from COG pixels)"
        return s, o, "raster:bands scale+offset"
    quant, offs = _pc_offsets(item)
    return 1.0 / quant, offs.get(_PC_BAND_ID[band], 0.0) / quant, "product-metadata BOA_ADD_OFFSET"


def _read_asset(href, epsg, bounds, res=10.0, resampling=Resampling.nearest):
    w, s, e, n = bounds
    W, H = int(round((e - w) / res)), int(round((n - s) / res))
    with rasterio.open(href) as src:
        if src.crs.to_epsg() != epsg:
            raise ValueError(f"asset CRS {src.crs} != {epsg}")
        win = from_bounds(w, s, e, n, transform=src.transform)
        arr = src.read(1, window=win, out_shape=(H, W), resampling=resampling, boundless=True, fill_value=0)
    return arr


def read_crop(item, epsg: int, bounds, res: float = 10.0, workers: int = 13, resampling: str = "nearest"):
    """Read 12 bands + SCL for a UTM box. Returns dict with bands (12,H,W) float32 reflectance (NaN nodata)."""
    src = source_of(item)
    amap = ES_ASSET if src == "earth-search" else PC_ASSET
    keys = BANDS + ["SCL"]
    hrefs = {k: item.assets[amap[k]].href for k in keys}

    def job(k):
        for attempt in range(4):
            try:
                rs = Resampling.nearest if k == "SCL" else Resampling[resampling]
                return k, _read_asset(hrefs[k], epsg, bounds, res, rs)
            except Exception:  # transient HTTP errors
                if attempt == 3:
                    raise
                time.sleep(3 * (attempt + 1))

    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        raw = dict(ex.map(job, keys))
    scl = raw["SCL"].astype(np.uint8)
    bands, so = [], {}
    in_pix, evidence = (es_offset_in_pixels(item, raw["B12"], scl) if src == "earth-search"
                        else (True, "PC: offset from product metadata"))
    for b in BANDS:
        dn = raw[b]
        s, o, how = band_scale_offset(item, b, in_pix)
        so[b] = (s, o)
        r = dn.astype(np.float32) * np.float32(s) + np.float32(o)
        r[dn == 0] = np.nan  # DN 0 = nodata
        bands.append(r)
    bands = np.stack(bands).astype(np.float32)
    w, s_, e, n = bounds
    transform = from_origin(w, n, res, res)
    note = (f"reflectance = DN*scale + offset per asset from STAC metadata ({how}); offset check: {evidence}; "
            f"scale/offset used: {json.dumps({k: [round(v[0], 6), round(v[1], 6)] for k, v in so.items()})}; "
            f"no date-based -1000 DN rule; no clipping (values as computed); DN==0 -> NaN; 20/60 m bands resampled to 10 m "
            f"by {resampling} (SCL always nearest)")
    return dict(bands=bands, scl=scl, transform=transform, crs=CRS.from_epsg(epsg), width=bands.shape[2],
                height=bands.shape[1], scale_offset=so, scale_offset_note=note, offset_in_pixels=bool(in_pix), read_s=round(time.time() - t0, 1))


def water_mask(bands, scl, max_fill_px: int = 100, buffer_px: int = 2, cloud_buffer_px: int = 5):
    """1 = observed water. Base: SCL==6, or SCL in {2 dark, 7 unclassified} with NDWI(B3,B8) > 0.
    Then small non-water islands (<= max_fill_px, e.g. floating debris/sargassum classified by Sen2Cor as
    vegetation/bare soil) fully enclosed by water and not cloud/shadow/nodata are filled as water.
    Finally water within buffer_px of any non-water and within cloud_buffer_px of cloud/shadow (SCL 3,8,9,10) is removed."""
    from scipy import ndimage
    g, nir = bands[2], bands[7]
    with np.errstate(invalid="ignore", divide="ignore"):
        ndwi = (g - nir) / (g + nir)
    valid = np.isfinite(bands).all(0)
    bad = np.isin(scl, SCL_BAD) | ~valid
    base = (scl == 6) | (np.isin(scl, (2, 7)) & (ndwi > 0))
    base &= ~bad
    filled = ndimage.binary_fill_holes(base)
    holes = filled & ~base & ~bad
    if holes.any():
        lab, n = ndimage.label(holes)
        sizes = ndimage.sum(np.ones_like(lab), lab, index=np.arange(1, n + 1))
        small = np.zeros(n + 1, bool)
        small[1:] = sizes <= max_fill_px
        base |= small[lab]
    st = np.ones((3, 3), bool)
    if buffer_px > 0:
        base &= ~ndimage.binary_dilation(~base, structure=st, iterations=buffer_px)
    cloudy = np.isin(scl, (3, 8, 9, 10))
    if cloud_buffer_px > 0 and cloudy.any():  # cloud edges are a classic false-positive source (Durban 2019-04-24)
        base &= ~ndimage.binary_dilation(cloudy, structure=st, iterations=cloud_buffer_px)
    return base.astype(np.uint8)


def water_quality(bands, water):
    """Scene-level sun-glint / haze proxy: median B11 (SWIR 1.6 um) over observed water. Water absorbs SWIR, so on
    clean scenes it is ~0.000-0.005; > 0.01 means glint/haze/over-correction (MDD false positives on wave texture,
    see reports/report.md, live scenes)."""
    w = water.astype(bool)
    if w.sum() < 100:
        return dict(water_b8_median=None, water_b11_median=None, glint_or_haze=None)
    b8 = float(np.nanmedian(bands[7][w]))
    b11 = float(np.nanmedian(bands[10][w]))
    return dict(water_b8_median=round(b8, 4), water_b11_median=round(b11, 4), glint_or_haze=bool(b11 > 0.01))


def scene_stats(bands, scl, water):
    valid = np.isfinite(bands).all(0) & (scl != 0)
    nv = max(int(valid.sum()), 1)
    return dict(valid_frac=round(float(valid.mean()), 4),
                crop_cloud_frac=round(float(np.isin(scl, SCL_CLOUD)[valid].sum() / nv), 4),
                crop_shadow_frac=round(float((scl == 3)[valid].sum() / nv), 4),
                water_frac=round(float(water.sum() / water.size), 4),
                scl6_frac=round(float((scl == 6).sum() / scl.size), 4))


def _write_tif(path, arr, transform, crs, dtype, nodata=None, descriptions=None):
    arr = arr if arr.ndim == 3 else arr[None]
    prof = dict(driver="GTiff", width=arr.shape[2], height=arr.shape[1], count=arr.shape[0], dtype=dtype,
                crs=crs, transform=transform, compress="deflate", tiled=True, blockxsize=256, blockysize=256)
    if nodata is not None:
        prof["nodata"] = nodata
    if dtype == "float32":
        prof["predictor"] = 3
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr.astype(dtype))
        if descriptions:
            for i, d in enumerate(descriptions, 1):
                dst.set_band_description(i, d)


def write_rgb(outdir: Path, bands, transform, crs, max_side: int = 2048):
    """True colour (B4,B3,B2) warped to EPSG:4326 so that rgb.json bounds are exact; alpha=0 outside/nodata.
    Stretch for water: reflectance 0..0.16 -> 0..1 with gamma 1/1.8 (land/clouds saturate)."""
    from PIL import Image
    rgb = bands[[3, 2, 1]]
    H, W = rgb.shape[1:]
    w, s, e, n = rasterio.transform.array_bounds(H, W, transform)
    dst_crs = CRS.from_epsg(4326)
    dt, dw, dh = calculate_default_transform(crs, dst_crs, W, H, w, s, e, n)
    k = max(dw, dh) / max_side
    if k > 1:
        dt = dt * rasterio.Affine.scale(k)
        dw, dh = int(math.ceil(dw / k)), int(math.ceil(dh / k))
    out = np.full((3, dh, dw), np.nan, np.float32)
    for i in range(3):
        reproject(rgb[i], out[i], src_transform=transform, src_crs=crs, dst_transform=dt, dst_crs=dst_crs,
                  resampling=Resampling.average, src_nodata=np.nan, dst_nodata=np.nan)
    alpha = np.isfinite(out).all(0)
    v = np.clip(np.nan_to_num(out) / 0.16, 0, 1) ** (1 / 1.8)
    img = np.dstack([(v * 255).astype(np.uint8).transpose(1, 2, 0), (alpha * 255).astype(np.uint8)])
    Image.fromarray(img, "RGBA").save(outdir / "rgb.png", optimize=True)
    b = rasterio.transform.array_bounds(dh, dw, dt)
    meta = dict(bounds=[b[0], b[1], b[2], b[3]], width=dw, height=dh, crs="EPSG:4326",
                note="true colour B4,B3,B2 warped to EPSG:4326 (bounds exact); stretch 0..0.16 reflectance, "
                     "gamma 1/1.8; alpha 0 = nodata/outside crop")
    (outdir / "rgb.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def write_scene(outdir: Path, region: str, item, crop: dict, extra: dict | None = None):
    outdir.mkdir(parents=True, exist_ok=True)
    bands, scl, tr, crs = crop["bands"], crop["scl"], crop["transform"], crop["crs"]
    water = water_mask(bands, scl)
    stats = scene_stats(bands, scl, water)
    stats.update(water_quality(bands, water))
    _write_tif(outdir / "bands.tif", bands, tr, crs, "float32", nodata=float("nan"), descriptions=BANDS)
    _write_tif(outdir / "scl.tif", scl, tr, crs, "uint8", nodata=0, descriptions=["SCL"])
    _write_tif(outdir / "water_mask.tif", water, tr, crs, "uint8", descriptions=["water"])
    rgbmeta = write_rgb(outdir, bands, tr, crs)
    b = rasterio.transform.array_bounds(crop["height"], crop["width"], tr)
    bw = transform_bounds(crs, "EPSG:4326", b[0], b[1], b[2], b[3])
    p = item.properties
    reg = REGIONS.get(region, {})
    meta = dict(region=region, region_name=reg.get("region_name", region), region_name_en=reg.get("region_name_en"),
                country=reg.get("country"), date=p["datetime"][:10],
                datetime=p["datetime"], scene_id=item.id, tile=tile_of(item),
                cloud_cover=p.get("eo:cloud_cover"), crop_cloud_frac=stats["crop_cloud_frac"],
                crop_shadow_frac=stats["crop_shadow_frac"], water_frac=stats["water_frac"],
                valid_frac=stats["valid_frac"], water_b8_median=stats["water_b8_median"],
                water_b11_median=stats["water_b11_median"], glint_or_haze=stats["glint_or_haze"],
                source=source_of(item), crs=crs.to_string(),
                transform=list(tr)[:6], width=crop["width"], height=crop["height"],
                bounds_utm=[b[0], b[1], b[2], b[3]], bounds_wgs84=[round(x, 6) for x in bw],
                scale_offset_note=crop["scale_offset_note"], scale_offset=crop["scale_offset"],
                processing_baseline=p.get("s2:processing_baseline"),
                boa_offset_applied_property=p.get("earthsearch:boa_offset_applied"),
                band_names=BANDS,
                water_mask_rule="1 = SCL 6, or SCL 2/7 with NDWI(B3,B8)>0; + enclosed non-water holes <=100 px "
                                "(not cloud/shadow/nodata) filled as water; then 2 px buffer next to any non-water and 5 px "
                                "next to cloud/shadow removed; 0 = land/cloud 8-10/shadow 3/snow/saturated/nodata/buffer",
                transform_order="affine a,b,c,d,e,f (rasterio): x = a*col + b*row + c; y = d*col + e*row + f",
                read_s=crop.get("read_s"), marida_md_px_tile=reg.get("md_px"),
                marida_dates=reg.get("marida_dates"), created=time.strftime("%Y-%m-%dT%H:%M:%S"))
    if extra:
        meta.update(extra)
    (outdir / "scene.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta
