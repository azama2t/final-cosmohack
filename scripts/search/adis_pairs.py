"""L100 — §11 п.1: синхронные пары «отрезок ADIS (The Ocean Cleanup) ↔ Sentinel-2 L2A».

  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/search/adis_pairs.py               # всё, с докачкой (resumable)
  .venv/Scripts/python.exe scripts/search/adis_pairs.py --only 355772 356392 --force
  .venv/Scripts/python.exe scripts/search/adis_pairs.py --summary-only                          # csv + md из meta.json
  .venv/Scripts/python.exe scripts/search/adis_pairs.py --drift-only                            # только ветер/течения

Вход (только чтение): data/extra/field/adis_s2_match.csv (L91: отрезок ↔ лучший S2 L2A с облачностью сцены ≤ 30 %),
data/extra/field/adis/Objects.csv (предметы), configs/case_pairs.yaml (маски/детектор, harmonize: none), weights/lgbm.
Отбор: |dt| ≤ 3 ч (177 отрезков); порядок — сначала с предметами, затем по |dt|.

Геометрия полосы. Segments.gpkg (472 МБ) не качаем (GDAL /vsicurl/ здесь не резолвит хост) → полоса восстанавливается
прямой: центроид ± distance/2 по avgheading; скан — на борту Side от 0 до Scan_width (линия, смещённая на W/2, ± W/2).
Точность прямой проверяется по координатам камеры у предметов (Objects.csv): поперечное отклонение, м (geom_dev_m).
Из-за неточности прямой (сотни м при изгибе курса) кроме номинальной полосы считаем «зону» = трек ± (500 м + сдвиг дрейфа).

Маски качества и детектор — scripts/case/pair_quality.py:process_s2 (импорт, файл не меняется). Вокруг вызова:
 - get_item и read_crop кешируются (одна объединённая вырезка на группу отрезков одной сцены, дальше — срезы);
 - захватываются итоговая разметка компонент детектора (cloudmask.drop_components) и вероятность — для полосы со сдвигом,
   зоны, FDI/SWIR видов. Сам алгоритм масок/детектора не дублируется.
Дрейф за |dt|: течения HYCOM GOFS 3.1 (GLBy0.08/expt_93.0, NCSS, поверхность, без приливов), ветер ERA5 через Open-Meteo
archive API (НЕ CDS), windage 1/2/3 %; сдвиг = (u_cur + a*U10) * (t_scene - t_obs) для каждой точки отрезка.
Допуск по месту — как в configs/case_pairs.yaml drift.tolerance: полуширина полосы + 3 км (typical, a = 2 %);
отдельно строгий флаг in_strip_strict: поперечная к курсу составляющая сдвига ≤ W/2.
Выход: data/search/adis/{seg/<SegmentID>/(rgb,quality,mask,panel).png+meta.json, candidates.csv, best/, cache/},
reports/search/adis.md. Кеш сети — data/search/adis/cache/ (STAC item json, HYCOM csv, Open-Meteo json).
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import shutil
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

os.environ["CUDA_VISIBLE_DEVICES"] = ""
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import requests  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "case"))
import pair_quality as pq  # noqa: E402
from macroplastic.live import stac as st  # noqa: E402
from macroplastic.grid import cloudmask as cm  # noqa: E402
from macroplastic import indices  # noqa: E402
from macroplastic.case import illumination as illum  # noqa: E402  (same rule as the studio, service/routes_v3_studio.py)

FIELD = ROOT / "data" / "extra" / "field"
OUT = ROOT / "data" / "search" / "adis"
CACHE = OUT / "cache"
SEG = OUT / "seg"
BEST = OUT / "best"
REPORT = ROOT / "reports" / "search" / "adis.md"
ADIS_DOI = "10.4121/ddede7f5-aca5-42ae-b851-e0bbb9a2c4c2"
HYCOM_NCSS = "https://ncss.hycom.org/thredds/ncss/GLBy0.08/expt_93.0/uv3z"  # 2018-12-04 .. 2024-09-04
WINDAGE = {"low": 0.01, "typical": 0.02, "high": 0.03}
MAX_DT_H = 3.0
ZONE_M = 500.0          # зона вокруг трека: неточность прямой + дрейф сверху
SHIP_R_M = 500.0        # срабатывания ближе этого к позиции судна в момент съёмки -> «вероятно, само судно»
MAX_UNION_M = 22000.0   # сторона объединённой вырезки группы
KN = 0.514444

# ------------------------------------------------------------------------------------------ data


def load_segments() -> pd.DataFrame:
    s = pd.read_csv(FIELD / "adis_s2_match.csv")
    s = s[s.s2_clear_dt_h.abs() <= MAX_DT_H].copy()
    for c in ("mindate", "maxdate", "timestamp"):
        s[c] = pd.to_datetime(s[c], format="mixed", utc=True)
    s["t_scene"] = s.timestamp + pd.to_timedelta(s.s2_clear_dt_h * 3600, unit="s")  # уточняется по STAC item
    s["n5"] = s["n_objects>5cm"].astype(int)
    s["prio"] = np.where(s.n5 > 0, 0, 1)
    s["adt"] = s.s2_clear_dt_h.abs()
    return s.sort_values(["prio", "adt"]).reset_index(drop=True)


def load_objects() -> pd.DataFrame:
    o = pd.read_csv(FIELD / "adis" / "Objects.csv")
    return o


# ------------------------------------------------------------------------------------------ geometry

def _geod():
    from pyproj import Geod
    return Geod(ellps="WGS84")


def seg_geometry(r) -> dict:
    """Track line (ship) and scan-band centre line (offset W/2 to the camera side), lon/lat."""
    g = _geod()
    h, d, W = float(r.avgheading), float(r.distance), float(r.Scan_width)
    lon0, lat0, _ = g.fwd(r.Longitude, r.Latitude, h + 180, d / 2)
    lon1, lat1, _ = g.fwd(r.Longitude, r.Latitude, h, d / 2)
    side = 90.0 if str(r.Side).lower().startswith("star") else -90.0
    a0, b0, _ = g.fwd(lon0, lat0, h + side, W / 2)
    a1, b1, _ = g.fwd(lon1, lat1, h + side, W / 2)
    return dict(track=[(lon0, lat0), (lon1, lat1)], band=[(a0, b0), (a1, b1)], heading=h, side_az=(h + side) % 360, W=W)


def pq_geom(r, geo) -> dict:
    """g-dict for pair_quality.strip_polygon: band centre line, width = Scan_width -> band [0, W] from the track."""
    return dict(sample_ids=str(r.SegmentID), lat=float(r.Latitude), lon=float(r.Longitude), width_m=geo["W"],
                length_km=float(r.distance) / 1000, field_items_km2=None, field_sample_id=str(r.SegmentID),
                sampling_method="ADIS shipborne GoPro", line=None, lines=[geo["band"]],
                geometry_status="reconstructed straight line (centroid, avgheading, distance) offset W/2 to camera side",
                geometry_source="adis_segments_csv")


def seg_time_frac(r, t: pd.Timestamp) -> float:
    return (t - r.mindate).total_seconds() / max((r.maxdate - r.mindate).total_seconds(), 1.0)


# ------------------------------------------------------------------------------------------ drift (cached)

_S = requests.Session()


def _get(url: str, fp: Path, timeout=300, tries=4, check=None) -> str:
    if fp.exists():
        return fp.read_text(encoding="utf-8")
    last = None
    for a in range(tries):
        try:
            r = _S.get(url, timeout=timeout)
            if r.status_code == 200 and (check is None or check(r.text)):
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(r.text, encoding="utf-8")
                return r.text
            last = f"HTTP {r.status_code} {r.text[:200]}"
        except Exception as e:  # noqa: BLE001
            last = repr(e)
        time.sleep(5 * (a + 1))
    raise RuntimeError(f"{url[:120]}: {last}")


def hycom_uv(lat: float, lon: float, t0: pd.Timestamp, t1: pd.Timestamp) -> pd.DataFrame:
    lon360 = lon % 360
    a = (t0.floor("D") - pd.Timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")  # whole days -> shared cache
    b = (t1.ceil("D") + pd.Timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (f"{HYCOM_NCSS}?var=water_u&var=water_v&latitude={lat:.3f}&longitude={lon360:.3f}&time_start={a}&time_end={b}"
           f"&vertCoord=0&accept=csv")
    fp = CACHE / "hycom" / f"uv_{lat:.3f}_{lon360:.3f}_{a[:13]}_{b[:13]}.csv".replace(":", "")
    txt = _get(url, fp, timeout=400, check=lambda t: t.startswith("time"))
    from io import StringIO
    df = pd.read_csv(StringIO(txt))
    df.columns = ["time", "lat", "lon", "z", "u", "v"]
    df["time"] = pd.to_datetime(df.time, utc=True)
    # NCSS csv gives packed int16 (scale_factor 0.001, _FillValue -30000); detect packed values
    for c in ("u", "v"):
        x = df[c].astype(float)
        x[x <= -29999] = np.nan
        if np.nanmax(np.abs(x.to_numpy())) > 20:
            x = x * 0.001
        df[c] = x
    return df


def wind_uv(lat: float, lon: float, day: pd.Timestamp) -> pd.DataFrame:
    d0 = (day - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    d1 = (day + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    url = (f"https://archive-api.open-meteo.com/v1/archive?latitude={lat:.2f}&longitude={lon:.2f}&start_date={d0}"
           f"&end_date={d1}&hourly=wind_speed_10m,wind_direction_10m&wind_speed_unit=ms&models=era5&timezone=GMT")
    fp = CACHE / "wind" / f"era5_{lat:.2f}_{lon:.2f}_{d0}_{d1}.json"
    j = json.loads(_get(url, fp, timeout=90, check=lambda t: '"hourly"' in t))["hourly"]
    w = pd.DataFrame(j)
    w["time"] = pd.to_datetime(w.time, utc=True)
    rad = np.deg2rad(w.wind_direction_10m.astype(float))
    w["u"] = -w.wind_speed_10m * np.sin(rad)
    w["v"] = -w.wind_speed_10m * np.cos(rad)
    return w


def _interp(df: pd.DataFrame, t: pd.Timestamp, cols=("u", "v")):
    x = df.time.astype("int64").to_numpy() / 1e9
    tt = t.value / 1e9
    return tuple(float(np.interp(tt, x, df[c].to_numpy(float))) if np.isfinite(df[c]).any() else float("nan") for c in cols)


def drift_for(r, t_scene: pd.Timestamp) -> dict:
    """Shift vectors (east, north, m) of the scanned water from its observation time to t_scene, per windage scenario."""
    lat, lon = round(float(r.Latitude) * 5) / 5, round(float(r.Longitude) * 5) / 5  # 0.2 deg (HYCOM 0.08, ERA5 0.25)
    t0, t1 = min(r.mindate, t_scene), max(r.maxdate, t_scene)
    tm = t0 + (t1 - t0) / 2
    out = dict(drift_current_src="HYCOM GOFS 3.1 GLBy0.08/expt_93.0 surface (no tides)", drift_wind_src="ERA5 via Open-Meteo archive")
    try:
        cu = hycom_uv(lat, lon, t0, t1)
        uc, vc = _interp(cu.dropna(), tm) if cu.u.notna().any() else (float("nan"), float("nan"))
    except Exception as e:  # noqa: BLE001
        uc = vc = float("nan")
        out["drift_current_err"] = str(e)[:200]
    try:
        wd = wind_uv(lat, lon, tm.normalize())
        uw, vw = _interp(wd, tm)
    except Exception as e:  # noqa: BLE001
        uw = vw = float("nan")
        out["drift_wind_err"] = str(e)[:200]
    cur_ok = np.isfinite(uc)
    if not cur_ok:  # фоллбэк: typical из configs/case_pairs.yaml, направление неизвестно -> по ветру
        out["drift_current_src"] = "fallback 0.20 m/s (configs/case_pairs.yaml typical), direction = wind"
        sp = math.hypot(uw, vw) if np.isfinite(uw) else 1.0
        uc, vc = (0.2 * uw / sp, 0.2 * vw / sp) if np.isfinite(uw) and sp > 0 else (0.2, 0.0)
    if not np.isfinite(uw):
        uw = vw = 0.0
        out["drift_wind_src"] = "missing (0)"
    # dt per point: frac 0..1 along the segment, obs time t(f)
    fr = np.linspace(0, 1, 21)
    tobs = np.array([(r.mindate + (r.maxdate - r.mindate) * f).value / 1e9 for f in fr])
    dts = t_scene.value / 1e9 - tobs  # s, >0: scene after observation
    hr = math.radians(float(r.avgheading))
    nvec = (math.cos(hr), -math.sin(hr))  # unit normal (east, north) to heading (starboard side)
    for k, a in WINDAGE.items():
        ue, vn = uc + a * uw, vc + a * vw
        sh = np.abs(dts) * math.hypot(ue, vn)
        perp = np.abs(dts * (ue * nvec[0] + vn * nvec[1]))
        out[f"shift_max_km_{k}"] = round(float(sh.max()) / 1000, 3)
        out[f"shift_perp_max_m_{k}"] = round(float(perp.max()), 1)
        if k == "typical":
            out["shift_vec_mid_m"] = [round(ue * float(np.median(dts)), 1), round(vn * float(np.median(dts)), 1)]
            out["vel_typical_ms"] = [round(ue, 3), round(vn, 3)]
    out.update(u_cur=round(uc, 3), v_cur=round(vc, 3), u10=round(uw, 2), v10=round(vw, 2),
               dt_obs_min_h=round(float(np.abs(dts).min()) / 3600, 3), dt_obs_max_h=round(float(np.abs(dts).max()) / 3600, 3))
    return out


# ------------------------------------------------------------------------------------------ S2 plumbing (patches around pair_quality)

_ITEMS: dict = {}
_UNION: dict = {}
LAST: dict = {}


def get_item_cached(endpoint, coll, item_id, retries=4):
    import pystac
    if item_id in _ITEMS:
        return _ITEMS[item_id]
    fp = CACHE / "stac" / f"{item_id}.json"
    if fp.exists():
        it = pystac.Item.from_dict(json.loads(fp.read_text(encoding="utf-8")))
    else:
        it = _real_get_item(endpoint, coll, item_id, retries)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps(it.to_dict()), encoding="utf-8")
    _ITEMS[item_id] = it
    return it


def _contains(ub, b):
    return ub[0] <= b[0] and ub[1] <= b[1] and ub[2] >= b[2] and ub[3] >= b[3]


def read_crop_cached(item, epsg, bounds, res=10.0, workers=4, resampling="nearest"):
    from rasterio.transform import from_origin
    u = _UNION.get(item.id)
    if u is not None and u["epsg"] == epsg and _contains(u["bounds"], bounds):
        c = u["crop"]
        w, s, e, n = bounds
        c0 = int(round((w - u["bounds"][0]) / res)); r0 = int(round((u["bounds"][3] - n) / res))
        W, H = int(round((e - w) / res)), int(round((n - s) / res))
        sub = dict(c)
        sub["bands"] = c["bands"][:, r0:r0 + H, c0:c0 + W]
        sub["scl"] = c["scl"][r0:r0 + H, c0:c0 + W]
        sub["transform"] = from_origin(w, n, res, res)
        sub["width"], sub["height"] = W, H
        LAST["crop"], LAST["bounds"] = sub, list(bounds)
        return sub
    c = _real_read_crop(item, epsg, bounds, res=res, workers=min(workers, 4), resampling=resampling)
    LAST["crop"], LAST["bounds"] = c, list(bounds)
    return c


def prefetch_union(item, epsg, bounds):
    _UNION.clear()
    c = _real_read_crop(item, epsg, bounds, workers=4)
    _UNION[item.id] = dict(epsg=epsg, bounds=list(bounds), crop=c)
    return c


def drop_components_cap(labels, n, drop):
    r = _real_drop(labels, n, drop)
    LAST["lab"] = r[0]
    return r


_real_get_item = pq.get_item
_real_read_crop = st.read_crop
_real_drop = cm.drop_components
pq.get_item = get_item_cached
st.read_crop = read_crop_cached
cm.drop_components = drop_components_cap


# ------------------------------------------------------------------------------------------ extras per segment

def _to_utm(epsg):
    from pyproj import Transformer
    return Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)


def _raster(poly, shape, tr):
    from rasterio.features import rasterize
    if poly is None or poly.is_empty:
        return np.zeros(shape, bool)
    return rasterize([(poly, 1)], out_shape=shape, transform=tr, all_touched=True, fill=0, dtype="uint8").astype(bool)


def _det_in(lab, m):
    ids = np.unique(lab[m & (lab > 0)])
    return int((ids > 0).sum()), int(((lab > 0) & m).sum())


def extras(r, geo, g, cfg, res, drift, objs, outdir: Path) -> dict:
    """Shifted strip / zone / ship / objects / FDI stats + panel png, from the crop and labels captured in process_s2."""
    import rasterio
    from shapely.affinity import translate
    from shapely.geometry import LineString, Point
    crop, lab = LAST["crop"], LAST.get("lab")
    bands, tr = crop["bands"], crop["transform"]
    H, W = crop["scl"].shape
    epsg = res["epsg"]
    T = _to_utm(epsg)
    poly, _ = pq.strip_polygon(g, epsg, cfg)
    strip = _raster(poly, (H, W), tr)
    with rasterio.open(outdir / "quality.tif") as src:
        qa = src.read(1)
    water = qa == pq.Q_WATER
    if lab is None or lab.shape != (H, W):
        lab = np.zeros((H, W), np.int32)
    dx, dy = drift.get("shift_vec_mid_m", [0, 0])
    sh_poly = translate(poly, dx, dy)
    sh = _raster(sh_poly, (H, W), tr)
    trk = LineString([T.transform(*p) for p in geo["track"]])
    zr = ZONE_M + 1000 * drift.get("shift_max_km_high", 0)
    zone = _raster(trk.buffer(zr), (H, W), tr)
    t_scene = pd.Timestamp(res["scene_datetime"]).tz_convert("UTC") if pd.Timestamp(res["scene_datetime"]).tzinfo else pd.Timestamp(res["scene_datetime"], tz="UTC")
    f = seg_time_frac(r, t_scene)
    g_ = _geod()
    (lo0, la0), (lo1, la1) = geo["track"]
    _, _, L = g_.inv(lo0, la0, lo1, la1)
    slon, slat, _ = g_.fwd(lo0, la0, geo["heading"], f * L)
    sx, sy = T.transform(slon, slat)
    ship = _raster(Point(sx, sy).buffer(SHIP_R_M), (H, W), tr)
    # ---- the survey vessel itself: bright compact NIR blob near the predicted position (checks time and lateral geometry)
    shipinfo = find_ship(bands, qa, tr, (sx, sy), geo["heading"], float(r.avgspeed))
    corr = None
    if shipinfo.get("ship_found"):
        cdx, cdy = shipinfo["cross_vec_m"]
        corr = _raster(translate(poly, cdx + dx, cdy + dy), (H, W), tr)
        ship = ship | _raster(Point(*shipinfo["ship_xy"]).buffer(SHIP_R_M), (H, W), tr)
    n_s, px_s = _det_in(lab, strip)
    n_sh, px_sh = _det_in(lab, sh)
    n_z, px_z = _det_in(lab, zone)
    n_ship, _ = _det_in(lab, ship)
    n_zone_noship, px_zone_noship = _det_in(lab, zone & ~ship)
    b = {k: bands[i] for i, k in enumerate(st.BANDS)}
    fdi = indices.fdi(b)
    fw = fdi[strip & water]
    fz = fdi[zone & water]
    # objects: camera position + dist perpendicular to the camera side
    om = []
    for o in objs.itertuples():
        lo, la, _ = g_.fwd(o.longitude, o.latitude, geo["side_az"], float(o.dist) if np.isfinite(o.dist) else 0.0)
        om.append((lo, la, float(o.amaj) if np.isfinite(o.amaj) else np.nan, float(o.area) if np.isfinite(o.area) else np.nan))
    # geometry check: lateral deviation of object camera positions from the reconstructed straight track
    devs = [trk.distance(Point(*T.transform(o.longitude, o.latitude))) for o in objs.itertuples()]
    ex = dict(frac_scene_in_segment=round(f, 3), ship_lonlat=[round(slon, 5), round(slat, 5)],
              **{k: v for k, v in shipinfo.items() if k not in ("cross_vec_m", "ship_xy")},
              n_det_corrected=(_det_in(lab, corr & ~ship)[0] if corr is not None else None),
              det_px_corrected=(_det_in(lab, corr)[1] if corr is not None else None),
              usable_frac_corrected=(round(float((corr & water).sum() / max(corr.sum(), 1)), 4) if corr is not None else None),
              n_det_shifted=n_sh, det_px_shifted=px_sh, usable_frac_shifted=round(float((sh & water).sum() / max(sh.sum(), 1)), 4),
              shifted_in_crop=bool(sh.sum() >= 0.9 * max(strip.sum(), 1)),
              zone_radius_m=round(zr, 0), zone_area_km2=round(zone.sum() * 1e-4, 3), zone_usable_km2=round(float((zone & water).sum()) * 1e-4, 3),
              n_det_zone=n_z, det_px_zone=px_z, n_det_near_ship=n_ship, n_det_zone_excl_ship=n_zone_noship, det_px_zone_excl_ship=px_zone_noship,
              strip_usable_km2=round(float((strip & water).sum()) * 1e-4, 4),
              fdi_strip_median=round(float(np.nanmedian(fw)), 5) if fw.size else None,
              fdi_strip_p99=round(float(np.nanpercentile(fw, 99)), 5) if fw.size >= 100 else None,
              fdi_strip_max=round(float(np.nanmax(fw)), 5) if fw.size else None,
              fdi_zone_p999=round(float(np.nanpercentile(fz, 99.9)), 5) if fz.size >= 1000 else None,
              geom_dev_m_median=round(float(np.median(devs)), 1) if devs else None,
              geom_dev_m_max=round(float(np.max(devs)), 1) if devs else None,
              n_objects_located=len(om))
    # ---- panel: RGB | FDI | SWIR | detector, window 3 km around the ship at scene time (clipped to segment)
    fc = min(max(f, 0.0), 1.0)
    clon, clat, _ = g_.fwd(lo0, la0, geo["heading"], fc * L)
    cx, cy = T.transform(clon, clat)
    inv = ~tr
    cc, rr = inv * (cx, cy)
    half = 150
    r0, c0 = int(max(0, min(H - 2 * half, rr - half))), int(max(0, min(W - 2 * half, cc - half)))
    sl = (slice(r0, r0 + 2 * half), slice(c0, c0 + 2 * half))
    ex["panel_window"] = dict(center_lonlat=[round(clon, 5), round(clat, 5)], size_m=2 * half * 10)
    rgb = pq._stretch_rgb(bands)[sl]
    fv = np.nan_to_num(fdi[sl], nan=0.0)
    fimg = np.clip((fv + 0.02) / 0.06, 0, 1)
    fimg = (np.stack([fimg, fimg, fimg], -1) * 255).astype(np.uint8)
    sw = np.stack([bands[11], bands[8], bands[3]])[:, sl[0], sl[1]]
    swimg = (np.clip(np.nan_to_num(sw) / 0.12, 0, 1) ** (1 / 1.8) * 255).astype(np.uint8).transpose(1, 2, 0)
    det = (lab > 0)[sl]
    from scipy import ndimage
    ol = pq._outline(strip)[sl]
    olz = pq._outline(zone)[sl]
    m = (rgb * 0.6).astype(np.uint8)
    m[qa[sl] != pq.Q_WATER] = (m[qa[sl] != pq.Q_WATER] * 0.4 + np.array([60, 60, 60])).astype(np.uint8)
    m[ndimage.binary_dilation(det, iterations=1)] = (255, 40, 40)
    panels = []
    for img in (rgb, fimg, swimg, m):
        p = img.copy()
        p[olz] = (0, 255, 255)
        p[ol] = (255, 0, 255)
        # ship at scene time: yellow cross; objects: green dots
        marks = [((slon, slat), (255, 255, 0))] + [((a[0], a[1]), (0, 255, 0)) for a in om]
        if shipinfo.get("ship_found"):
            marks.append((T.transform(*shipinfo["ship_xy"], direction="INVERSE"), (255, 140, 0)))
        for (lo, la, *_), col in marks:
            x, y = T.transform(lo, la)
            ci, ri = inv * (x, y)
            ri, ci = int(ri) - r0, int(ci) - c0
            if 2 <= ri < 2 * half - 2 and 2 <= ci < 2 * half - 2:
                p[ri - 2:ri + 3, ci] = col
                p[ri, ci - 2:ci + 3] = col
        panels.append(np.kron(p, np.ones((2, 2, 1), np.uint8)))
    sep = np.full((panels[0].shape[0], 6, 3), 255, np.uint8)
    pq._save_png(outdir / "panel.png", np.concatenate([panels[0], sep, panels[1], sep, panels[2], sep, panels[3]], 1), max_side=2600)
    return ex


def find_ship(bands, qa, tr, pxy, heading, speed_kn, radius_m=2500.0) -> dict:
    """Nearest bright compact object (B8 above the local water median by > 0.03, 4..150 px, elongated or small) to the
    predicted vessel position. Offsets are split along/across the heading; along offset / speed = implied clock offset."""
    from scipy import ndimage
    b8 = np.nan_to_num(bands[7])
    H, W = b8.shape
    inv = ~tr
    c, r_ = inv * pxy
    rad = radius_m / 10
    r0, r1 = int(max(0, r_ - rad)), int(min(H, r_ + rad))
    c0, c1 = int(max(0, c - rad)), int(min(W, c + rad))
    if r1 - r0 < 10 or c1 - c0 < 10:
        return dict(ship_found=False, ship_note="predicted position outside crop")
    win = b8[r0:r1, c0:c1]
    wq = qa[r0:r1, c0:c1]
    wat = wq == 1
    if wat.sum() < 100:
        return dict(ship_found=False, ship_note="no water around predicted position")
    med = float(np.median(win[wat]))
    m = win > med + 0.03
    lab, n = ndimage.label(m, structure=np.ones((3, 3), bool))
    best = None
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        comp = lab[sl] == i
        npx = int(comp.sum())
        if npx < 4 or npx > 150:
            continue
        # neighbourhood must be mostly water (not a cloud field)
        rr, cc = np.nonzero(comp)
        rr, cc = rr + sl[0].start, cc + sl[1].start
        rs, cs = slice(max(0, rr.min() - 8), rr.max() + 9), slice(max(0, cc.min() - 8), cc.max() + 9)
        if (wq[rs, cs] == 1).mean() < 0.5:
            continue
        cy_, cx_ = rr.mean() + r0 + 0.5, cc.mean() + c0 + 0.5
        x, y = tr * (cx_, cy_)
        dd = math.hypot(x - pxy[0], y - pxy[1])
        if dd <= radius_m and (best is None or dd < best[0]):
            best = (dd, x, y, npx, float(win[sl][comp].max() - med))
    if best is None:
        return dict(ship_found=False, ship_note=f"no compact bright object within {radius_m:.0f} m")
    dd, x, y, npx, amp = best
    h = math.radians(heading)
    ae, an = math.sin(h), math.cos(h)  # along-track unit (east, north)
    vx, vy = x - pxy[0], y - pxy[1]
    along = vx * ae + vy * an
    cross = vx * an - vy * ae  # + = starboard
    spd = max(speed_kn * KN, 0.1)
    return dict(ship_found=True, ship_dist_m=round(dd, 0), ship_along_m=round(along, 0), ship_cross_m=round(cross, 0),
                ship_clock_offset_min=round(along / spd / 60, 1), ship_px=npx, ship_b8_excess=round(amp, 3),
                cross_vec_m=[cross * an, -cross * ae], ship_xy=[x, y],
                ship_note="nearest compact bright NIR object (candidate survey vessel; verify visually)")


# ------------------------------------------------------------------------------------------ level

def level_of(m: dict) -> tuple[str, str]:
    if m.get("decision") == "error":
        return "D", m.get("reason", "read_error")
    q = m.get("quality") or {}
    dr = m.get("drift") or {}
    ex = m.get("extras") or {}
    if m.get("decision") != "accept":
        return "D", f"pixels: {m.get('reason')} (cov {q.get('coverage')}, water {q.get('valid_water_frac')}, cloud {q.get('cloud_frac')}, glint {q.get('glint_frac')})"
    if abs(m["dt_h"]) > MAX_DT_H:
        return "D", "dt > 3 h"
    tol_km = m["scan_width_m"] / 2 / 1000 + 3.0
    if dr.get("shift_max_km_typical", 99) > tol_km:
        return "D", f"drift out of strip tolerance: {dr.get('shift_max_km_typical')} km > {tol_km:.2f} km"
    reasons = []
    if not ex.get("shifted_in_crop", False) or ex.get("usable_frac_shifted", 0) < 0.5:
        reasons.append(f"drift-shifted strip poorly usable ({ex.get('usable_frac_shifted')})")
    if ex.get("geom_dev_m_max") is not None and ex["geom_dev_m_max"] > 1000:
        reasons.append(f"reconstructed track deviates up to {ex['geom_dev_m_max']} m from object positions")
    if m.get("stdheading", 0) > 20:
        reasons.append(f"curved track (std heading {m['stdheading']:.0f} deg), straight-line strip unreliable")
    if ex.get("ship_found") and (ex.get("usable_frac_corrected") or 0) < 0.5:
        reasons.append(f"vessel-corrected strip poorly usable ({ex.get('usable_frac_corrected')})")
    if reasons:
        return "C", "; ".join(reasons)
    return "A", ("time ≤3 h, drift ≤ tolerance (typical), survey area + size classes from ADIS, strip pixels usable; "
                 "category: floating anthropogenic debris (ADIS >5/10/50 cm) vs MARIDA Marine Debris")


# ------------------------------------------------------------------------------------------ main

def load_cfg():
    cfg = pq.load_cfg(ROOT / "configs" / "case_pairs.yaml")
    cfg["network"]["workers"] = 4
    assert pq.harmonize_mode(cfg) is None, "detector must run without harmonization (configs/case_pairs.yaml)"
    return cfg


def run_drift(segs: pd.DataFrame, workers=4):
    keys = {}
    for r in segs.itertuples():
        keys[r.SegmentID] = r
    res = {}

    def job(r):
        try:
            return r.SegmentID, drift_for(r, r.t_scene)
        except Exception as e:  # noqa: BLE001
            return r.SegmentID, dict(error=str(e)[:300])
    # distinct (lat,lon,day) queries are cached; ≤ 4 parallel requests
    with ThreadPoolExecutor(workers) as ex:
        for sid, d in ex.map(job, list(keys.values())):
            res[sid] = d
    return res


def build_groups(segs: pd.DataFrame, cfg) -> list[dict]:
    groups = []
    for item_id, gdf in segs.groupby("s2_clear_item", sort=False):
        it = get_item_cached("earth-search", "sentinel-2-l2a", item_id)
        epsg = st.item_epsg(it)
        cur = None
        for r in gdf.sort_values("mindate").itertuples():
            geo = seg_geometry(r)
            poly, _ = pq.strip_polygon(pq_geom(r, geo), epsg, cfg)
            bb = pq.crop_box(poly, seg_cfg(r, cfg))
            if cur is not None:
                u = [min(cur["bounds"][0], bb[0]), min(cur["bounds"][1], bb[1]), max(cur["bounds"][2], bb[2]), max(cur["bounds"][3], bb[3])]
                if u[2] - u[0] <= MAX_UNION_M and u[3] - u[1] <= MAX_UNION_M:
                    cur["bounds"] = u
                    cur["segs"].append(r.SegmentID)
                    cur["prio"] = min(cur["prio"], (r.prio, r.adt))
                    continue
            cur = dict(item_id=item_id, epsg=epsg, bounds=list(bb), segs=[r.SegmentID], prio=(r.prio, r.adt))
            groups.append(cur)
    return sorted(groups, key=lambda x: x["prio"])


def seg_cfg(r, cfg: dict) -> dict:
    """Crop context large enough to contain the vessel's (extrapolated) position at scene time (for find_ship)."""
    f = seg_time_frac(r, pd.Timestamp(r.t_scene))
    d_out = max(0.0, -f, f - 1.0) * float(r.distance)
    c = copy.deepcopy(cfg)
    c["strip"]["context_m"] = float(min(max(cfg["strip"]["context_m"], d_out + 2500.0), 12000.0))
    return c


def process_segment(r, cfg, pred, drift: dict, objs: pd.DataFrame) -> dict:
    cfg = seg_cfg(r, cfg)
    outdir = SEG / str(r.SegmentID)
    geo = seg_geometry(r)
    g = pq_geom(r, geo)
    row = SimpleNamespace(endpoint="earth-search", collection="sentinel-2-l2a", item_id=r.s2_clear_item)
    base = dict(event_id=f"ADIS_SEG_{r.SegmentID}", segment_id=int(r.SegmentID), ship=r.Ship, side=r.Side,
                seg_start=str(r.mindate), seg_end=str(r.maxdate), seg_mid=str(r.timestamp), lat=float(r.Latitude), lon=float(r.Longitude),
                heading=float(r.avgheading), stdheading=float(r.stdheading), distance_m=float(r.distance), scan_width_m=float(r.Scan_width),
                cam_height_m=float(r.Camheight), survey_area_km2=float(r.area_scanned_km2),
                n_gt5cm=int(r["n_objects>5cm"]) if isinstance(r, dict) else int(getattr(r, "n5")),
                track_lonlat=[list(map(float, p)) for p in geo["track"]], band_lonlat=[list(map(float, p)) for p in geo["band"]])
    LAST.clear()
    t0 = time.time()
    try:
        res = pq.process_s2(row, g, cfg, pred, outdir)
    except Exception as e:  # noqa: BLE001
        res = dict(scene_id=r.s2_clear_item, source="earth-search/sentinel-2-l2a", decision="error",
                   reason=f"read_error:{type(e).__name__}", error=traceback.format_exc()[-1500:])
    base.update(res)
    if res.get("decision") != "error":
        t_scene = pd.Timestamp(res["scene_datetime"])
        t_scene = t_scene.tz_localize("UTC") if t_scene.tzinfo is None else t_scene.tz_convert("UTC")
        base["dt_h"] = round((t_scene - r.timestamp).total_seconds() / 3600, 3)
        dr = drift if drift and "error" not in drift else {}
        # drift recomputed with the exact STAC datetime (cached forcing)
        try:
            dr = drift_for(r, t_scene)
        except Exception as e:  # noqa: BLE001
            dr = dict(error=str(e)[:300], **(drift or {}))
        base["drift"] = dr
        try:
            base["extras"] = extras(r, geo, g, cfg, res, dr, objs, outdir)
        except Exception:  # noqa: BLE001
            base["extras_error"] = traceback.format_exc()[-1500:]
    else:
        base["dt_h"] = float(r.s2_clear_dt_h)
        base["drift"] = drift
    base["level"], base["level_reason"] = level_of(base)
    base["elapsed_s"] = round(time.time() - t0, 1)
    base["created"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "meta.json").write_text(json.dumps(base, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return base


# ------------------------------------------------------------------------------------------ table + report

def strip_excl_ship(m: dict, cfg: dict) -> tuple:
    """Detector pixels in the nominal strip outside SHIP_R_M of the vessel (predicted and found positions), from
    prob.tif >= threshold on quality code 1 (valid water). Approximation of the detector mask (no cloud/shadow component
    drop; on A pairs that drop removed 0 components). Returns (px, strip_px_excl, px_in_ship_zone)."""
    import rasterio
    from shapely.geometry import Point
    d = SEG / str(m["segment_id"])
    ex, det = m.get("extras") or {}, m.get("detector") or {}
    if not (d / "prob.tif").exists() or not ex:
        return None, None, None
    with rasterio.open(d / "prob.tif") as src:
        prob, tr, H, W = src.read(1), src.transform, src.height, src.width
    with rasterio.open(d / "quality.tif") as src:
        qa = src.read(1)
    g = dict(width_m=m["scan_width_m"], lines=[[tuple(p) for p in m["band_lonlat"]]], line=None, lat=m["lat"], lon=m["lon"],
             geometry_status="reconstructed")
    poly, _ = pq.strip_polygon(g, m["epsg"], cfg)
    strip = _raster(poly, (H, W), tr)
    T = _to_utm(m["epsg"])
    sx, sy = T.transform(*ex["ship_lonlat"])
    zone = Point(sx, sy).buffer(SHIP_R_M)
    if ex.get("ship_found"):
        h = math.radians(m["heading"])
        fx = sx + ex["ship_along_m"] * math.sin(h) + ex["ship_cross_m"] * math.cos(h)
        fy = sy + ex["ship_along_m"] * math.cos(h) - ex["ship_cross_m"] * math.sin(h)
        zone = zone.union(Point(fx, fy).buffer(SHIP_R_M))
    sz = _raster(zone, (H, W), tr)
    dm = (prob >= round(det["threshold"] * 255)) & (qa == pq.Q_WATER)
    return int((dm & strip & ~sz).sum()), int((strip & ~sz).sum()), int((dm & strip & sz).sum())


def evaluability(m: dict) -> dict:
    """Detector evaluable on this scene? Same function, thresholds and inputs as the studio (L95): sun zenith at the
    centre of the crop (lon/lat bounds) and scene time; median B3 of valid water inverted from rgb.png + quality.tif."""
    d = SEG / str(m["segment_id"])
    if "bounds_utm" not in m or not (d / "rgb.png").exists():
        return dict(detector_evaluable=None, detector_eval_reason="no scene", sun_zenith_deg=None, water_b3_median=None)
    import rasterio
    from pyproj import Transformer
    tr = Transformer.from_crs(f"EPSG:{m['epsg']}", "EPSG:4326", always_xy=True)
    w, s_, e, n = m["bounds_utm"]
    xs, ys = tr.transform([w, e, w, e], [s_, s_, n, n])
    zen = illum.solar_zenith(m["scene_datetime"], [min(xs), min(ys), max(xs), max(ys)])
    q = None
    if (d / "quality.tif").exists():
        with rasterio.open(d / "quality.tif") as ds:
            q = ds.read(1)
    b3 = illum.water_median(illum.rgb_png_b3(d / "rgb.png", 512), q, 1)
    ok, low, weak = illum.detector_evaluable(zen, b3)
    why = []
    if low:
        why.append(f"низкое солнце (зенит {zen:.1f}° ≥ {illum.SUN_ZENITH_MAX:.0f}°)")
    if weak:
        why.append(f"слабый сигнал воды (B3 {b3:.4f} < {illum.WATER_B3_MIN})")
    return dict(detector_evaluable=ok, detector_eval_reason="; ".join(why) if why else "оценивается",
                sun_zenith_deg=zen, water_b3_median=b3)


COLS = ["event_id", "scene_id", "sensor", "dt_h", "drift_shift_km", "in_strip", "usable_frac", "field_count_by_size",
        "survey_area_km2", "det_pixels", "det_area_m2", "level", "reason"]


def build_table(segs: pd.DataFrame) -> pd.DataFrame:
    rows = []
    cfg = load_cfg()
    sx = segs.set_index("SegmentID")
    for fp in sorted(SEG.glob("*/meta.json")):
        m = json.loads(fp.read_text(encoding="utf-8"))
        sid = m["segment_id"]
        s = sx.loc[sid] if sid in sx.index else None
        q, d, dr, ex = m.get("quality") or {}, m.get("detector") or {}, m.get("drift") or {}, m.get("extras") or {}
        tol = m["scan_width_m"] / 2 / 1000 + 3.0
        pxs, sps, pxship = strip_excl_ship(m, cfg) if m.get("decision") == "accept" else (None, None, None)
        ev = evaluability(m)
        rows.append(dict(
            event_id=m["event_id"], scene_id=m.get("scene_id"), sensor="Sentinel-2 L2A (MSI)", dt_h=m.get("dt_h"),
            drift_shift_km=dr.get("shift_max_km_typical"),
            in_strip=(dr.get("shift_max_km_typical", 99) <= tol) if dr else None,
            usable_frac=q.get("valid_water_frac"),
            field_count_by_size=(f">5cm:{int(s['n_objects>5cm'])};>10cm:{int(s['n_objects>10cm'])};>50cm:{int(s['n_objects>50cm'])}"
                                 if s is not None else None),
            survey_area_km2=m.get("survey_area_km2"), det_pixels=d.get("det_px_strip"), det_area_m2=d.get("det_area_m2"),
            level=m.get("level"), reason=m.get("level_reason"), **ev,
            # extra columns
            ship=m.get("ship"), side=m.get("side"), seg_mid_utc=m.get("seg_mid"), scene_datetime=m.get("scene_datetime"),
            lat=m.get("lat"), lon=m.get("lon"), scan_width_m=m.get("scan_width_m"),
            dhat_5cm_km2=None if s is None else round(float(s.dhat_5cm), 3),
            dhat_50cm_km2=None if s is None else round(float(s.dhat_50cm), 3),
            dhat_50cm_cal_km2=None if s is None else s.dhat_50cm_calibrated,
            det_px_strip_excl_ship=pxs, det_px_strip_ship_zone=pxship, strip_px_excl_ship=sps,
            n_det_strip=d.get("n_det"), n_det_crop=d.get("n_det_crop"), prob_max_strip=d.get("prob_max"),
            in_strip_strict=(dr.get("shift_perp_max_m_typical", 1e9) <= m["scan_width_m"] / 2) if dr else None,
            shift_perp_max_m=dr.get("shift_perp_max_m_typical"), shift_km_low=dr.get("shift_max_km_low"), shift_km_high=dr.get("shift_max_km_high"),
            wind_ms=(round(math.hypot(dr["u10"], dr["v10"]), 1) if dr.get("u10") is not None else None),
            u_cur=dr.get("u_cur"), v_cur=dr.get("v_cur"), u10=dr.get("u10"), v10=dr.get("v10"), current_src=dr.get("drift_current_src"),
            n_det_shifted=ex.get("n_det_shifted"), usable_frac_shifted=ex.get("usable_frac_shifted"),
            zone_usable_km2=ex.get("zone_usable_km2"), n_det_zone=ex.get("n_det_zone"), det_px_zone=ex.get("det_px_zone"),
            n_det_near_ship=ex.get("n_det_near_ship"), n_det_zone_excl_ship=ex.get("n_det_zone_excl_ship"),
            strip_usable_km2=ex.get("strip_usable_km2"), fdi_strip_p99=ex.get("fdi_strip_p99"), fdi_strip_max=ex.get("fdi_strip_max"),
            geom_dev_m_max=ex.get("geom_dev_m_max"), ship_found=ex.get("ship_found"), ship_along_m=ex.get("ship_along_m"),
            ship_cross_m=ex.get("ship_cross_m"), ship_clock_offset_min=ex.get("ship_clock_offset_min"), ship_px=ex.get("ship_px"),
            n_det_corrected=ex.get("n_det_corrected"), usable_frac_corrected=ex.get("usable_frac_corrected"), decision=m.get("decision"), qa_reason=m.get("reason"),
            cloud_frac=q.get("cloud_frac"), glint_frac=q.get("glint_frac"), coverage=q.get("coverage"),
            frac_scene_in_segment=ex.get("frac_scene_in_segment"), panel=(f"data/search/adis/seg/{sid}/panel.png"
                                                                         if (fp.parent / "panel.png").exists() else None)))
    t = pd.DataFrame(rows)
    if len(t):
        t["_n"] = t.field_count_by_size.str.extract(r">5cm:(\d+)").astype(float)
        t = t.sort_values(["level", "_n", "dt_h"], ascending=[True, False, True], key=lambda c: c.abs() if c.name == "dt_h" else c).drop(columns="_n")
    return t


def pick_best(t: pd.DataFrame, k=10) -> pd.DataFrame:
    if not len(t):
        return t
    t = t.copy()
    t["_lv"] = t.level.map({"A": 0, "C": 1, "D": 2})
    t["_n"] = t.field_count_by_size.str.extract(r">5cm:(\d+)").astype(float)
    t["_adt"] = t.dt_h.abs()
    t = t[t.panel.notna()]
    return t.sort_values(["_lv", "_n", "_adt"], ascending=[True, False, True]).head(k).drop(columns=["_lv", "_n", "_adt"])


def write_outputs(segs: pd.DataFrame, objs_all: pd.DataFrame):
    t = build_table(segs)
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "candidates.csv", index=False)
    best = pick_best(t)
    if BEST.exists():
        shutil.rmtree(BEST)
    BEST.mkdir(parents=True)
    for i, r in enumerate(best.itertuples(), 1):
        sid = r.event_id.replace("ADIS_SEG_", "")
        for fn in ("panel.png", "rgb.png", "mask.png", "quality.png"):
            src = SEG / sid / fn
            if src.exists():
                shutil.copy(src, BEST / f"{i:02d}_{sid}_{r.level}_{fn}")
    write_report(t, best, segs, objs_all)
    return t


def _fmt(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def write_report(t: pd.DataFrame, best: pd.DataFrame, segs: pd.DataFrame, objs_all: pd.DataFrame):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    n = len(t)
    lv = t.level.value_counts().to_dict() if n else {}
    A_all = t[t.level == "A"] if n else t
    nA_all = len(A_all)
    A_items_all = A_all[A_all.field_count_by_size.str.extract(r">5cm:(\d+)")[0].astype(float) > 0] if nA_all else A_all
    # «0 детекций при N шт./км²» и ложные тревоги — только где детектор оценивается (правило студии, L95)
    A = A_all[A_all.detector_evaluable == True] if nA_all else A_all  # noqa: E712
    nA = len(A)
    cnt = lambda df, k: int(df.field_count_by_size.str.extract(fr"{k}:(\d+)").astype(float).sum()[0]) if len(df) else 0  # noqa: E731
    A_items = A[A.field_count_by_size.str.extract(r">5cm:(\d+)")[0].astype(float) > 0] if nA else A
    ids_A = [int(e.replace("ADIS_SEG_", "")) for e in A.event_id] if nA else []
    oA = objs_all[objs_all.SegmentID.isin(ids_A)]
    L = []
    L.append("# ADIS ↔ Sentinel-2: синхронные пары (L100, §11 п.1)\n")
    L.append(f"Скрипт `scripts/search/adis_pairs.py`; таблица `data/search/adis/candidates.csv`; вырезки `data/search/adis/seg/<SegmentID>/` и лучшие — `data/search/adis/best/`. "
             f"Источник поля: ADIS (The Ocean Cleanup), 4TU {ADIS_DOI}, CC BY 4.0. Снимки: Sentinel-2 L2A (Earth Search). "
             "Маски качества и детектор — `scripts/case/pair_quality.py:process_s2` (без изменений), LightGBM `weights/lgbm`, гармонизация выключена (`configs/case_pairs.yaml`).\n")
    L.append(f"Отбор: |dt| ≤ 3 ч и облачность сцены ≤ 30 % (реестр L91) — {len(segs)} отрезков; обработано {n}.\n")
    L.append("## Итог по уровням\n")
    L.append("| уровень | отрезков | с предметами (>5 см) | предметов >5 / >10 / >50 см | обследовано, км² | пригодная полоса S2, км² | срабатываний в полосе (пикс.) | в зоне ±(0.5 км + дрейф), без судна (объектов / пикс.) |")
    L.append("|---|---:|---:|---|---:|---:|---:|---:|")
    for k in ("A", "C", "D"):
        d = t[t.level == k] if n else t
        if not len(d):
            L.append(f"| {k} | 0 | 0 | — | — | — | — | — |")
            continue
        wi = int((d.field_count_by_size.str.extract(r">5cm:(\d+)")[0].astype(float) > 0).sum())
        L.append(f"| {k} | {len(d)} | {wi} | {cnt(d, '>5cm')} / {cnt(d, '>10cm')} / {cnt(d, '>50cm')} | {d.survey_area_km2.sum():.3f} | "
                 f"{_fmt(d.strip_usable_km2.sum(), 3)} | {int(d.det_pixels.fillna(0).sum())} | "
                 f"{int(d.n_det_zone_excl_ship.fillna(0).sum())} / {int(d.det_px_zone.fillna(0).sum())} |")
    L.append("")
    if n:
        rs = t[t.level == "D"].reason.str.extract(r"^(pixels: \w+|drift|dt|read_error\S*)")[0].value_counts().to_dict()
        L.append(f"Причины D: {json.dumps(rs, ensure_ascii=False)}. Причины C: "
                 f"{json.dumps(t[t.level == 'C'].reason.str.slice(0, 60).value_counts().to_dict(), ensure_ascii=False)}.\n")
    L.append("## Оценивается ли детектор (правило студии L95, `src/macroplastic/case/illumination.py`)\n")
    L.append(f"Детектор «не оценивается» при зените Солнца ≥ {illum.SUN_ZENITH_MAX:.0f}° или медиане B3 пригодной воды < {illum.WATER_B3_MIN} "
             "(та же функция и те же входы, что в студии: время сцены и центр вырезки; B3 из rgb.png + quality.tif). "
             "Уровень A у таких пар сохраняется как связь «полевое число ↔ снимок» (синхронность), но в сводку «0 срабатываний при N шт./км²» "
             "и в оценку ложных тревог они НЕ входят.\n")
    if n:
        L.append("| уровень | отрезков | детектор оценивается | не оценивается: низкое солнце | слабый сигнал воды | с предметами, оценивается |")
        L.append("|---|---:|---:|---:|---:|---:|")
        for k in ("A", "C", "D"):
            d = t[t.level == k]
            wi = d[d.field_count_by_size.str.extract(r">5cm:(\d+)")[0].astype(float) > 0]
            L.append(f"| {k} | {len(d)} | {int((d.detector_evaluable == True).sum())} | "  # noqa: E712
                     f"{int(d.detector_eval_reason.str.contains('солнце', na=False).sum())} | "
                     f"{int(d.detector_eval_reason.str.contains('сигнал', na=False).sum())} | "
                     f"{int((wi.detector_evaluable == True).sum())} из {len(wi)} |")  # noqa: E712
        ne = t[t.detector_evaluable == False]  # noqa: E712
        ev_ = t[t.detector_evaluable == True]  # noqa: E712
        L.append(f"\nНе оценивается {len(ne)} отрезков на {ne.scene_id.nunique()} сценах из {t.scene_id.nunique()}; "
                 f"в их зонах {int(ne.det_px_zone.fillna(0).sum())} пикс. срабатываний против {int(ev_.det_px_zone.fillna(0).sum())} на оцениваемых "
                 f"(суда с неоцениваемыми сценами: {', '.join(sorted(ne.ship.dropna().unique()))}).\n")
    L.append("## Пары уровня A с предметами\n")
    if len(A_items_all):
        L.append("| отрезок | судно | сцена | dt, ч | дрейф (typ), км | попер. сдвиг, м | предметы >5/10/50 | шт./км² >5 см | обсл., км² | пригодно полосы | срабат. в полосе / сдвинутой / зоне (без судна) | FDI p99 полосы | детектор оценивается (зенит, B3) |")
        L.append("|---|---|---|---:|---:|---:|---|---:|---:|---:|---|---:|---|")
        for r in A_items_all.itertuples():
            L.append(f"| {r.event_id} | {r.ship} | {r.scene_id} | {r.dt_h:+.2f} | {_fmt(r.drift_shift_km)} | {_fmt(r.shift_perp_max_m, 0)} | "
                     f"{r.field_count_by_size} | {_fmt(r.dhat_5cm_km2, 1)} | {r.survey_area_km2:.3f} | {_fmt(r.usable_frac)} | "
                     f"{int(r.det_pixels or 0)} / {_fmt(r.n_det_shifted)} / {_fmt(r.n_det_zone_excl_ship)} | {_fmt(r.fdi_strip_p99, 4)} | "
                     f"{'да' if r.detector_evaluable == True else 'НЕТ'} ({_fmt(r.sun_zenith_deg, 1)}°, {_fmt(r.water_b3_median, 4)}) |")  # noqa: E712
    else:
        L.append("нет.")
    L.append("")
    if nA:
        area_obj = float(oA.area.sum())
        sa = float(A.survey_area_km2.sum())
        L.append("## Предел обнаружения (только пары A, где детектор оценивается)\n")
        L.append(f"- A-пар с оцениваемым детектором: {nA} из {nA_all} (с предметами {len(A_items)} из {len(A_items_all)}); обследовано {sa:.2f} км², предметов >5 см {cnt(A, '>5cm')}, >50 см {cnt(A, '>50cm')}; "
                 f"средняя плотность >5 см {cnt(A, '>5cm') / max(sa, 1e-9):.1f} шт./км², >50 см {cnt(A, '>50cm') / max(sa, 1e-9):.2f} шт./км².")
        L.append(f"- Суммарная площадь предметов сверху в A-отрезках {area_obj:.2f} м² на {sa:.2f} км² → доля покрытия {area_obj / max(sa * 1e6, 1):.1e}; "
                 f"крупнейший предмет {float(oA.area.max()) if len(oA) else float('nan'):.2f} м² = {100 * (float(oA.area.max()) if len(oA) else 0) / 100:.1f} % пикселя 10 м.")
        L.append(f"- Детектор: в полосах A {int(A.det_pixels.fillna(0).sum())} пикс.; в сдвинутых на дрейф полосах {int(A.n_det_shifted.fillna(0).sum())} объектов; "
                 f"в зонах ±(0.5 км + дрейф) без окрестности судна {int(A.n_det_zone_excl_ship.fillna(0).sum())} объектов "
                 f"({int(A.det_px_zone.fillna(0).sum())} пикс. вместе с судном) на {A.zone_usable_km2.sum():.1f} км² пригодной воды; "
                 f"рядом с позицией судна в момент съёмки — {int(A.n_det_near_ship.fillna(0).sum())}.")
    if nA:
        z = A[A.field_count_by_size.str.extract(r">5cm:(\d+)")[0].astype(float) == 0]
        L.append("\n## Срабатывания детектора при полевом нуле (A-пары с оцениваемым детектором, ADIS: 0 предметов >5 см в полосе)\n")
        L.append("Полевой ноль — не доказательство отсутствия (эффективность ADIS < 1), но предметов ≥ 5 см на этой воде камера не увидела. "
                 "Срабатывания здесь — оценка фона ложных тревог детектора на открытой воде.\n")
        L.append("| ветер U10 | A-пар с нулём | пригодная полоса, км² | срабат. в полосе, пикс. | пикс./км² полосы без ±0.5 км от судна | зона пригодно, км² | объектов в зоне (без судна) | объектов/км² зоны |")
        L.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for name, mm in (("< 7 м/с", z.wind_ms < 7), ("≥ 7 м/с", z.wind_ms >= 7), ("все", z.wind_ms.notna() | z.wind_ms.isna())):
            d = z[mm]
            su, zu = float(d.strip_usable_km2.fillna(0).sum()), float(d.zone_usable_km2.fillna(0).sum())
            dp, zn = int(d.det_pixels.fillna(0).sum()), int(d.n_det_zone_excl_ship.fillna(0).sum())
            dpx = int(d.det_px_strip_excl_ship.fillna(0).sum())
            L.append(f"| {name} | {len(d)} | {su:.2f} | {dp} (без судна ≈ {dpx}) | {dpx / su if su else float('nan'):.1f} | {zu:.1f} | {zn} | {zn / zu if zu else float('nan'):.2f} |")
    L.append("\n## Лучшие 10 вырезок (panel: RGB | FDI | SWIR B12-B8A-B4 | маска детектора; окно 3 км у позиции судна в момент съёмки)\n")
    L.append("Пурпур — номинальная полоса скана, голубой — зона ±(0.5 км + дрейф), жёлтый крест — судно в момент съёмки (интерполяция по отрезку), зелёные — предметы ADIS (камера + dist к борту).\n")
    for i, r in enumerate(best.itertuples(), 1):
        L.append(f"{i}. `data/search/adis/best/{i:02d}_{r.event_id.replace('ADIS_SEG_', '')}_{r.level}_panel.png` — {r.event_id}, {r.level}, {r.ship}, dt {r.dt_h:+.2f} ч, "
                 f"{r.field_count_by_size}, срабатываний в полосе {int(r.det_pixels or 0)} пикс., в зоне {_fmt(r.n_det_zone)} (у судна {_fmt(r.n_det_near_ship)}).")
    L.append("\n## Как читать\n- TP/FP утверждать нельзя: срабатывание в полосе не означает, что это предмет из счёта ADIS, отсутствие срабатываний не означает, что предметов нет. "
             "Пара A даёт только «на снимке N срабатываний при полевых M шт./км²».\n- Дрейф: HYCOM GOFS 3.1 без приливов (в Северном море приливное течение 0.5–1 м/с не учтено; при |dt| ≤ 0.2 ч это ≤ 0.4–0.7 км), ветер ERA5 (Open-Meteo), windage 1–3 %. Дрейф — область поиска, не доказательство.\n"
             "- Полоса скана 36–102 м — это 4–10 пикселей; «строго в полосе» (in_strip_strict) — поперечный сдвиг ≤ W/2, выполняется лишь при |dt| порядка минут. "
             "Уровень A по месту — по правилу репозитория (сдвиг typical ≤ W/2 + 3 км, `configs/case_pairs.yaml`), т.е. связь по плотности на участке, а не по отдельным предметам.\n"
             "- Геометрия полосы — прямая по центроиду/курсу/длине (Segments.gpkg не скачан); отклонение проверено по координатам камеры у предметов (geom_dev_m_max).\n")
    if nA:
        A_ = A_all.assign(_mid=pd.to_datetime(A_all.seg_mid_utc, format="mixed").dt.round("20min"))
        n_pass = A_.groupby(["ship", "_mid"]).ngroups
        sf = A_all[A_all.ship_found == True]  # noqa: E712
        L.append("## Выводы (L100)\n")
        L.append(f"- Пар A: {nA_all}, из них детектор оценивается на {nA} (остальные — низкое солнце/слабый сигнал: A только как «полевое число ↔ снимок»). Все A (независимых проходов судна ≈ {n_pass}: камеры левого и правого борта дают по отрезку на один проход), "
                 f"с |dt| ≤ 1 ч — {int((A_all.dt_h.abs() <= 1).sum())}; с предметами — {len(A_items_all)} (по 1 предмету на 0.46–0.56 км², ≈ 4 шт./км²; > 50 см — {cnt(A_all, '>50cm')}), "
                 f"из них с оцениваемым детектором — {len(A_items)} ({', '.join(A_items.event_id)}).")
        L.append(f"- Во всех {len(A_items)} оцениваемых A-парах с предметами (и в остальных {len(A_items_all) - len(A_items)}, где число не засчитывается) детектор даёт 0 пикселей в номинальной полосе, 0 — в полосе, сдвинутой на дрейф, "
                 f"и 0 — в полосе, поправленной по найденному на снимке судну (где оно найдено). Это ожидаемо: доля покрытия ~10⁻⁷–10⁻⁶, предмет ≤ 1 % пикселя.")
        L.append(f"- Время пар проверено по самому судну: кандидат «судно» найден у {len(sf)} из {nA_all} A-пар; медиана |вдоль трека| {sf.ship_along_m.abs().median():.0f} м "
                 f"(≈ {sf.ship_clock_offset_min.abs().median():.1f} мин хода), медиана |поперёк| {sf.ship_cross_m.abs().median():.0f} м (по всем A). "
                 "Поиск автоматический (ближайший компактный яркий объект в NIR) — при барашках может взять барашек (так на 355891: настоящее судно с кильватером — ~1.5 км вдоль трека, в полосе); "
                 "глазами подтверждено на 355772, 335573, 116519.")
        L.append("- Массовые срабатывания (сотни объектов в зоне) — только на сценах, где детектор не оценивается (низкое солнце). "
                 "На оцениваемых A-парах с полевым нулём срабатывания редкие и только при ветре ≥ 7 м/с (барашки, см. таблицу выше) — фон ложных тревог, а не мусор.")
        L.append("- Предел обнаружения: при полевых плотностях до ~4 шт./км² (> 5 см; на оцениваемых A предметов > 50 см нет, единственный — в 355772 при низком солнце) S2 + LightGBM предметов не видит; "
                 "регрессию «снимок → шт./км²» на этих парах строить нельзя (A с предметами — 0 срабатываний, A с нулём — только барашки). Нужны пары, где доля покрытия ≳ 10⁻², — в ADIS таких нет.")
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", type=int)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--summary-only", action="store_true")
    ap.add_argument("--drift-only", action="store_true")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    segs = load_segments()
    objs = load_objects()
    if a.summary_only:
        write_outputs(segs, objs)
        return
    cfg = load_cfg()
    todo = segs if not a.only else segs[segs.SegmentID.isin(a.only)]
    if not a.force:
        todo = todo[[not (SEG / str(s) / "meta.json").exists() for s in todo.SegmentID]]
    if a.limit:
        todo = todo.head(a.limit)
    print(f"segments to do: {len(todo)} / {len(segs)}", flush=True)
    t0 = time.time()
    drift = run_drift(todo)
    print(f"drift done {time.time() - t0:.0f} s; errors {sum('error' in d for d in drift.values())}", flush=True)
    if a.drift_only:
        return
    from macroplastic.models.lgbm_predict import load_predictor
    pred = load_predictor(ROOT / cfg["detector"]["weights"], harmonize=pq.harmonize_mode(cfg))
    groups = build_groups(todo, cfg)
    print(f"groups: {len(groups)}", flush=True)
    by = todo.set_index("SegmentID", drop=False)
    for gi, gr in enumerate(groups):
        it = get_item_cached("earth-search", "sentinel-2-l2a", gr["item_id"])
        tg = time.time()
        try:
            prefetch_union(it, gr["epsg"], gr["bounds"])
        except Exception as e:  # noqa: BLE001
            print(f"[prefetch error] {gr['item_id']}: {e}", flush=True)
            _UNION.clear()
        print(f"group {gi + 1}/{len(groups)} {gr['item_id']} {len(gr['segs'])} segs, union read {time.time() - tg:.0f} s", flush=True)
        for sid in gr["segs"]:
            r = by.loc[sid]
            r = SimpleNamespace(**r.to_dict())
            m = process_segment(r, cfg, pred, drift.get(sid), objs[objs.SegmentID == sid])
            ex, d = m.get("extras") or {}, m.get("detector") or {}
            print(f"  {m['event_id']} n={r.n5} dt={m.get('dt_h')} {m.get('decision')} {m.get('reason')} level={m['level']} "
                  f"det_strip={d.get('det_px_strip')} zone={ex.get('n_det_zone')} ship={ex.get('n_det_near_ship')} "
                  f"shift={(m.get('drift') or {}).get('shift_max_km_typical')} {m['elapsed_s']} s", flush=True)
        _UNION.clear()
        write_outputs(segs, objs)
    write_outputs(segs, objs)
    shrink_pngs()
    print("done", time.time() - t0, flush=True)


def shrink_pngs(max_side: int = 700):
    """Disk budget: overview rgb/mask/quality PNGs of segments outside best/ -> max 700 px (panel.png and best/ stay full)."""
    from PIL import Image
    best = {f.name.split("_")[1] for f in BEST.glob("*.png")}
    for fp in SEG.glob("*/*.png"):
        if fp.parent.name in best or fp.name == "panel.png":
            continue
        im = Image.open(fp)
        k = max(im.size) / max_side
        if k > 1:
            im.resize((int(im.size[0] / k), int(im.size[1] / k)), Image.NEAREST).save(fp, optimize=True)


if __name__ == "__main__":
    main()
