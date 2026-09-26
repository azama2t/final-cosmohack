r"""L111 (INBOX §15, CRITERIA_CHECK П1): слой «Спутниковые зоны» — зоны детекции на реальных сценах, текущий детектор.

  $env:CUDA_VISIBLE_DEVICES=""; $env:PYTHONPATH="src"; .venv\Scripts\python.exe scripts\case\scene_zones.py [--only demo]

Сцены (все — текущий режим детектора: weights/lgbm, без гармонизации, порог 0.63, маска качества как у пар):
  demo   — отложенная сцена Cózar 2024: data/live/cozar_demo/<date>/ + data/case/demo/cozar_demo_<date>/ (scripts/case/demo_scene.py);
  live   — районы сервиса: data/live/<region>/<date>/ + out/studio_cache/detector_current/live/<region>/<date>/ (L95);
  drift  — data/drift_check/<region>/<date>/ + out/studio_cache/detector_current/drift/<region>/<date>/.
Правило освещённости (src/macroplastic/case/illumination.py, общее со студией и ADIS): зенит Солнца ≥ 58° или медиана B3
пригодной воды < 0.003 → «детектор не оценивается» → зон нет (сцена записана в index.json с причиной).

Зона = кластер объектов детектора (пиксели P ≥ порога на пригодной воде после фильтров облака/тени), объекты ближе
CLUSTER_M друг к другу объединены; зона — контур кластера, расширенный на CLUSTER_M/2. Кластеры < MIN_ZONE_PX пикселей
зонами не становятся (учтены в итогах сцены). Сцена без единого объекта → одна зона «не обнаружено» = вся пригодная вода.

Признаки ложного срабатывания (эвристики, не классификатор; на размеченных данных НЕ проверены):
  судно/кильватер/шов — macroplastic.grid.artifacts.classify (правила reports/artifacts.md): ≥ 30 % пикселей зоны;
  блик — медиана B11 пригодной воды в зоне > 0.01 (порог glint_b11 configs/case_pairs.yaml) или ≥ 20 % блика (код 6);
  пена — «белый» спектр: прирост B2 над водой ≥ 0.8 прироста B8 (у плавучего материала NIR растёт сильнее синего)
         или ветер 10 м ≥ 7 м/с (барашки; ADIS L100: ложные только при U10 ≥ 7 м/с).
Выход: data/case/scene_zones/index.json, data/case/scene_zones/<key>/{zones.geojson, detections.geojson, scene.json,
       rgb.jpg, quality.png} (rgb/quality — EPSG:4326 по bounds, ≤ 1024 px).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
from rasterio import features  # noqa: E402
from scipy import ndimage  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from macroplastic.case import illumination as ill  # noqa: E402
from macroplastic.grid.artifacts import classify as classify_artifacts  # noqa: E402

OUT = ROOT / "data" / "case" / "scene_zones"
CACHE = ROOT / "out" / "studio_cache" / "detector_current"
CLUSTER_M = 300.0
MIN_ZONE_PX = 5
GLINT_B11 = 0.01
FOAM_WHITE = 0.8
FOAM_WIND = 7.0
WIND_ZERO = 5.0         # §29 А / Г3-1: Cózar et al. 2024 (Nat. Commun., data/extra/cozar2024/paper.txt, Methods, LWD):
                        # «we removed from a0 the sea surface area associated with wind speeds higher than 5 m·s−1 … the
                        # probability of detection dropped sharply above 5 m·s−1» — the same rule (u10 > 5), not tuned
WIND_LABEL = "недостаточно данных (ветер > 5 м/с: мусор перемешивается, полосы не видны — правило Cózar 2024)"
ART_FRAC = 0.3
MAX_PX = 1024
SHIP_NEAR_M = 500.0     # K2 (audit): a bright target (ship blob or dry hull) with SWIR response this close -> boat traffic
SHIP_TARGET_B11 = 0.015  # target component max B11: ships/boats (dry superstructure) respond in SWIR, wet floating matter does not
HULL_B11 = 0.03         # SWIR reflectance of a dry hull (water ~0.001-0.01)
COAST_M = 300.0         # land (quality code 2) closer than this -> coast / surf, not evaluated
SHALLOW_B3_RATIO = 2.0  # zone water B3 median >= 2 x scene water median and >= SHALLOW_B3_ABS -> shallow / turbid water
SHALLOW_B3_ABS = 0.03
# В12 (audit, 26.09): rules checked on the 8 named zones (4 boats with wake, 4 shoal / mud-bank edges) and on the 14 Cózar
# (level B) zones of 30SXE — none of the 14 changes (out/case_demo/diag_b12.py on probable.signs.diag):
BOAT_B11 = 0.01         # small bright object (<= 40 px, B8 >= max(0.04, 4x water)) within 500 m with SWIR B11 >= 0.01 -> boat
                        # (boats 0.012-0.031; bright filament pixels of the Cózar scene <= 0.0032)
SHOAL_B4 = 0.02         # zone water red B4 >= 0.02 and (>= 2x scene water B4 or NIR B8 >= 0.013) -> bright bottom / mud bank
SHOAL_B8 = 0.013        # (shoals B4 0.022-0.026, B8 0.014-0.022; Cózar zones B4 <= 0.0036, B8 <= 0.0019)

# INBOX §23 п.2 (26.09): no items/km2 scenario for satellite zones — the text in Cózar et al. 2021 gives only a lower
# bound for windrows, a narrow honestly derived range does not exist; the zone's quantity status is «not confirmed».
QUANTITY = {
    "status": "not_confirmed",
    "label": "концентрация по снимку не подтверждена",
    "detail": "перевод площади в штуки не показываем: нет калибровочных пар (см. docs/QUANTITY.md)",
}
LWD_WIND_NOTE = ("ветер > 5 м/с: по правилу Cózar 2024 эта вода не входит в знаменатель «обследовано» — LWD не "
                 "считается")
CLOUD_ZONE = 0.2        # cloud / cloud shadow (quality codes 3, 4) >= 20 % of the zone -> cloud sign


def model_info() -> dict:
    p = ROOT / "weights" / "lgbm" / "model.txt"
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    return {"weights": "weights/lgbm", "file": "weights/lgbm/model.txt", "sha256": h, "sha256_short": h[:12],
            "trained_at": dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "threshold": 0.63, "harmonize": "none", "class": "MARIDA Marine Debris (любой плавающий мусор, не только пластик)"}


def scene_list(only: set | None) -> list[dict]:
    out = []
    for sdir in sorted((ROOT / "data" / "live" / "cozar_demo").glob("*/bands.tif")):
        d = sdir.parent.name
        out.append({"key": f"demo-cozar-{d}", "kind": "demo", "sdir": sdir.parent,
                    "ddir": ROOT / "data" / "case" / "demo" / f"cozar_demo_{d}", "region": "cozar_demo", "date": d})
    for kind, root in (("live", ROOT / "data" / "live"), ("drift", ROOT / "data" / "drift_check")):
        for sdir in sorted(root.glob("*/*/bands.tif")):
            r, d = sdir.parent.parent.name, sdir.parent.name
            if r == "cozar_demo":
                continue
            dd = CACHE / kind / r / d
            if (dd / "det.tif").is_file():
                out.append({"key": f"{kind}-{r}-{d}", "kind": kind, "sdir": sdir.parent, "ddir": dd, "region": r, "date": d})
    return [s for s in out if not only or s["kind"] in only]


_TRAIN = None


def training_scene(sj: dict) -> str | None:
    """«снимок из обучающей выборки детектора»: the same acquisition (tile + date) as a MARIDA / MADOS scene."""
    global _TRAIN
    if _TRAIN is None:
        sys.path.insert(0, str(ROOT / "scripts"))
        from check_mados_overlap import scan_mados, scan_marida
        mar = scan_marida(ROOT / "data" / "MARIDA", "MARIDA")
        mad = scan_mados(ROOT / "data" / "MADOS", "MADOS")
        _TRAIN = {}
        for df, name in ((mar, "MARIDA"), (mad, "MADOS")):
            for t, d, sp in zip(df.tile, df.date, df.splits):
                if isinstance(t, str) and t and isinstance(d, str) and d:
                    _TRAIN[(t.lstrip("T"), d[:10])] = f"{name} ({sp})"
    return _TRAIN.get((str(sj.get("tile") or "").lstrip("T"), str(sj.get("date") or "")[:10]))


def wind(lat: float, lon: float, iso: str) -> float | None:
    import requests
    day = iso[:10]
    url = ("https://archive-api.open-meteo.com/v1/archive?latitude=%.4f&longitude=%.4f&start_date=%s&end_date=%s"
           "&hourly=wind_speed_10m&wind_speed_unit=ms&timezone=UTC" % (lat, lon, day, day))
    for k in range(3):
        try:
            j = requests.get(url, timeout=30).json()
            h = min(int(iso[11:13]) + (1 if int(iso[14:16]) >= 30 else 0), 23)
            v = j["hourly"]["wind_speed_10m"][h]
            return None if v is None else float(v)
        except Exception:  # noqa: BLE001
            time.sleep(2 * (k + 1))
    return None


def poisson_ci(n: int, a: float):
    from scipy.stats import chi2
    lo = 0.0 if n == 0 else chi2.ppf(0.025, 2 * n) / 2
    hi = chi2.ppf(0.975, 2 * (n + 1)) / 2
    return round(lo / a, 2), round(hi / a, 2)


_ADIS = None
_ORG = None


def adis_calibrated(r) -> dict:
    """§28 Б (L119 rule): the ADIS authors' trawl calibration of this segment (> 10 cm; de Vries et al. 2026) next to our
    raw C = N(> 10 cm) / A. C_cal < 0 or NaN -> not shown; lo/hi NaN -> «интервал не дан»."""
    def f(k):
        try:
            v = float(r[k])
        except (KeyError, TypeError, ValueError):
            return None
        return None if math.isnan(v) else v
    a = f("area_scanned_km2")
    n10 = f("n_objects>10cm")
    cal, lo, hi = f("dhat_10cm_calibrated"), f("dhat_10cm_calibrated_lo95"), f("dhat_10cm_calibrated_hi95")
    ok = cal is not None and cal >= 0
    return {"raw_10cm_items_km2": round(n10 / a, 2) if (n10 is not None and a) else None,
            "authors_cal_10cm_items_km2": round(cal, 2) if ok else None,
            "authors_cal_10cm_lo95": round(lo, 2) if ok and lo is not None else None,
            "authors_cal_10cm_hi95": round(hi, 2) if ok and hi is not None else None}


def field_nearby(lon: float, lat: float, scene_date: str) -> dict:
    """Nearest independent field measurements: ADIS segments (> 5 cm, C = N/A) and the organisers' CSV."""
    global _ADIS, _ORG
    if _ADIS is None:
        _ADIS = pd.read_csv(ROOT / "data" / "extra" / "field" / "adis_s2_match.csv", low_memory=False)
        _ORG = pd.read_csv(ROOT / "task" / "macroplastic_marine_samples.csv", low_memory=False)
    kx = 111.32 * math.cos(math.radians(lat))
    d = _ADIS
    km = np.hypot((d.Longitude - lon) * kx, (d.Latitude - lat) * 110.57)
    idx = np.argsort(km.values)[:3]
    items = []
    for i in idx:
        r = d.iloc[i]
        n, a = int(r["n_objects>5cm"]), float(r["area_scanned_km2"])
        lo, hi = poisson_ci(n, a) if a > 0 else (None, None)
        dd = (dt.date.fromisoformat(str(r["date"])[:10]) - dt.date.fromisoformat(scene_date)).days
        items.append({"source": "ADIS (4TU.ResearchData, судовая камера)", "segment_id": int(r["SegmentID"]),
                      "ship": str(r["Ship"]), "date": str(r["date"])[:10], "days_from_scene": dd,
                      "distance_km": round(float(km.iloc[i]), 1), "lon": round(float(r.Longitude), 5), "lat": round(float(r.Latitude), 5),
                      "n_items": n, "area_km2": round(a, 4), "size_class": "> 5 см",
                      "c_items_km2": round(n / a, 2) if a > 0 else None, "ci95_lo": lo, "ci95_hi": hi,
                      "ci95_method": "точный интервал Пуассона для N, делённый на A", **adis_calibrated(r)})
    o = _ORG
    ko = np.hypot((o.longitude - lon) * kx, (o.latitude - lat) * 110.57)
    j = int(np.nanargmin(ko.values))
    org = {"sample_id": str(o.iloc[j]["sample_id"]), "source_id": str(o.iloc[j]["source_id"]),
           "distance_km": round(float(ko.iloc[j]), 1)}
    near = items[0]["distance_km"] if items else None
    return {"items": items, "nearest_organizer_sample": org,
            "note": ("Измерение ≠ оценка: это независимые полевые счёты C = N/A в другое время и в другом месте; "
                     "со сценой они не связаны и не калибруют её." + (
                         f" Ближайший отрезок ADIS — за {near:.0f} км: только для порядка величин." if near and near > 50 else ""))}


def _warp_png(arr, src_tr, src_crs, bounds4326, out_w, out_h, resampling):
    from rasterio.warp import reproject
    from rasterio.transform import from_bounds
    dst_tr = from_bounds(*bounds4326, out_w, out_h)
    out = np.zeros((arr.shape[0], out_h, out_w), arr.dtype)
    for k in range(arr.shape[0]):
        reproject(arr[k], out[k], src_transform=src_tr, src_crs=src_crs, dst_transform=dst_tr, dst_crs="EPSG:4326",
                  resampling=resampling)
    return out


def write_crop(od: Path, zid: str, m: np.ndarray, det: np.ndarray, rgb8: np.ndarray, lab: np.ndarray, art_px) -> str | None:
    """Small JPEG of the zone: left — the image, right — the same with detector pixels (red; ship/wake/seam objects
    yellow), native 10 m grid (UTM), zone bbox + 30 px, <= 220 px per panel."""
    from PIL import Image
    ys, xs = np.nonzero(m)
    if not len(ys):
        return None
    H, W = m.shape
    y0, y1 = max(ys.min() - 30, 0), min(ys.max() + 31, H)
    x0, x1 = max(xs.min() - 30, 0), min(xs.max() + 31, W)
    img = rgb8[:, y0:y1, x0:x1].transpose(1, 2, 0).copy()
    dd = det[y0:y1, x0:x1]
    ov = img.copy()
    ll = lab[y0:y1, x0:x1]
    artm = np.zeros(dd.shape, bool)
    for i in np.unique(ll[dd]):
        if i > 0 and art_px[i]:
            artm |= ll == i
    ov[dd & ~artm] = (255, 40, 60)
    ov[artm] = (255, 212, 59)
    both = np.concatenate([img, np.full((img.shape[0], 4, 3), 20, np.uint8), ov], axis=1)
    im = Image.fromarray(both)
    sc = min(1.0, 444 / im.width)
    if sc < 1:
        im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))), Image.NEAREST)
    elif im.width < 300:
        k = int(300 / im.width) + 1
        im = im.resize((im.width * k, im.height * k), Image.NEAREST)
    (od / "crops").mkdir(exist_ok=True)
    name = f"crops/{zid}.jpg"
    im.save(od / name, quality=85)
    return name


def build_scene(s: dict, cozar: pd.DataFrame, minfo: dict) -> dict:
    from PIL import Image
    from pyproj import Transformer, Geod
    from rasterio.enums import Resampling
    from shapely.geometry import shape, mapping, box
    from shapely.ops import transform as sh_tr, unary_union
    sj = json.loads((s["sdir"] / "scene.json").read_text(encoding="utf-8"))
    iso = sj["datetime"]
    bw = sj["bounds_wgs84"]
    with rasterio.open(s["sdir"] / "bands.tif") as ds:
        names = list(ds.descriptions)
        B = {b: ds.read(names.index(b) + 1).astype(np.float32) for b in ("B2", "B3", "B4", "B8", "B11")}
        tr, crs = ds.transform, ds.crs
    with rasterio.open(s["ddir"] / "quality.tif") as q:
        qa = q.read(1)
    with rasterio.open(s["ddir"] / "prob.tif") as p:
        prob = p.read(1).astype(np.float32) / 255.0
    with rasterio.open(s["ddir"] / "det.tif") as dtf:
        det = dtf.read(1) > 0
    water = qa == 1
    zen = ill.solar_zenith(iso, bw)
    b3w = round(float(np.nanmedian(B["B3"][water])), 4) if water.sum() >= 50 else None
    ok, low, weak = ill.detector_evaluable(zen, b3w)
    cx, cy = (bw[0] + bw[2]) / 2, (bw[1] + bw[3]) / 2
    w10 = sj.get("wind10m_ms")
    if w10 is None:
        w10 = wind(cy, cx, iso)
    water_km2 = float(water.sum()) * 1e-4
    info = {"key": s["key"], "kind": s["kind"], "region": s["region"], "region_name": sj.get("region_name") or s["region"],
            "date": s["date"], "datetime": iso, "scene_id": sj["scene_id"], "tile": sj.get("tile"),
            "bounds": bw, "sun_zenith_deg": zen, "water_b3_median": b3w, "evaluable": bool(ok), "low_sun": bool(low),
            "weak_signal": bool(weak), "wind10m_ms": None if w10 is None else round(float(w10), 2),
            "wind_source": "ERA5 через Open-Meteo archive API, час съёмки",
            "water_km2": round(water_km2, 3), "det_pixels": int(det.sum()),
            "det_area_m2": int(det.sum()) * 100,
            "lwd_m2_km2": round(int(det.sum()) * 100 / water_km2, 2) if water_km2 > 0 else None,
            "wind_high": bool(w10 is not None and w10 > WIND_ZERO),
            "model": minfo, "crop_note": "вырезка вокруг района/сцены, не весь тайл",
            "selection": sj.get("selection")}
    # §29 А: windy scenes are not part of the «observed» area (LWD denominator) at all
    info["in_observed_area"] = not info["wind_high"]
    info["observed_water_km2"] = info["water_km2"] if info["in_observed_area"] else 0.0
    if info["wind_high"]:
        info["lwd_m2_km2"] = None
    info["training_scene"] = training_scene(sj)
    if not ok:
        info["not_evaluated_reason"] = ("низкое солнце (зенит %.0f°)" % zen) if low else "слабый сигнал воды (медиана B3 < 0.003)"
    od = OUT / s["key"]
    od.mkdir(parents=True, exist_ok=True)
    # --- rgb + quality overlays in EPSG:4326
    H, W = qa.shape
    sc = min(1.0, MAX_PX / max(H, W))
    ow, oh = max(1, int(W * sc)), max(1, int(H * sc))
    wm = water if water.any() else np.isfinite(B["B3"])
    hi = np.array([np.nanpercentile(B[b][wm], 98) for b in ("B4", "B3", "B2")])
    hi = np.maximum(hi.max(), 0.02)
    rgb = np.stack([np.clip(np.nan_to_num(B[b]) / hi, 0, 1) ** (1 / 1.4) for b in ("B4", "B3", "B2")])
    rgb8 = (rgb * 255).astype(np.uint8)
    wr = _warp_png(rgb8, tr, crs, bw, ow, oh, Resampling.bilinear)
    Image.fromarray(wr.transpose(1, 2, 0)).save(od / "rgb.jpg", quality=85)
    pal = {0: (0, 0, 0, 102), 1: (0, 0, 0, 0), 2: (73, 80, 87, 204), 3: (255, 255, 255, 153), 4: (255, 255, 255, 153),
           5: (0, 0, 0, 60), 6: (255, 212, 59, 153)}
    qrgba = np.zeros((4, H, W), np.uint8)
    for k, c in pal.items():
        m = qa == k
        for i in range(4):
            qrgba[i][m] = c[i]
    wq = _warp_png(qrgba, tr, crs, bw, ow, oh, Resampling.nearest)
    Image.fromarray(wq.transpose(1, 2, 0), "RGBA").save(od / "quality.png", optimize=True)
    info.update({"rgb_file": "rgb.jpg", "quality_file": "quality.png",
                 "rgb_note": "B4,B3,B2, растяжка 0..p98 по пригодной воде, гамма 1/1.4; EPSG:4326 по bounds"})
    zones, dets = [], []
    if ok:
        to4326 = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
        geod = Geod(ellps="WGS84")
        lab, n = ndimage.label(det, structure=np.ones((3, 3), bool))
        art, _ = classify_artifacts(lab, n, B, water | det, det) if n else ([], {})
        art_px = np.zeros(n + 1, object)
        for i in range(n):
            art_px[i + 1] = art[i]
        # detections (objects) as polygons
        obj_geo = {}
        for g, v in features.shapes(lab.astype(np.int32), mask=lab > 0, transform=tr, connectivity=8):
            obj_geo.setdefault(int(v), []).append(shape(g))
        # clusters
        rpx = int(round(CLUSTER_M / 2 / 10))
        yy, xx = np.ogrid[-rpx:rpx + 1, -rpx:rpx + 1]
        disk = (xx * xx + yy * yy) <= rpx * rpx
        grow = ndimage.binary_dilation(det, structure=disk)
        cl, nc = ndimage.label(grow, structure=np.ones((3, 3), bool))
        wmed = {b: float(np.nanmedian(B[b][water])) for b in B} if water.sum() else {b: 0.0 for b in B}
        from macroplastic.grid.artifacts import ship_blobs
        land = qa == 2
        blobs = ship_blobs(B["B2"], B["B3"], B["B4"], B["B8"], water)
        hull = (np.nan_to_num(B["B8"]) >= max(0.06, 4 * wmed["B8"])) & (np.nan_to_num(B["B11"]) >= HULL_B11) & ~land
        target = blobs | hull
        tl, tn = ndimage.label(target, structure=np.ones((3, 3), bool))
        if tn:  # keep only targets with a SWIR response (K2 check: 7/7 audited boat zones kept, 0 targets on the Cózar scene)
            tmx = ndimage.maximum(np.nan_to_num(B["B11"]), tl, index=np.arange(1, tn + 1))
            keep = np.zeros(tn + 1, bool)
            keep[1:] = np.asarray(tmx) >= SHIP_TARGET_B11
            target = keep[tl]
        rs, rc = int(SHIP_NEAR_M / 10), int(COAST_M / 10)
        # diagnostics (В12): any bright compact object (no SWIR condition), for rule design
        bright_any = (blobs | ((np.nan_to_num(B["B8"]) >= max(0.04, 4 * wmed["B8"])) & ~land)) & ~det
        bl, bn = ndimage.label(bright_any, structure=np.ones((3, 3), bool))
        bsz = np.bincount(bl.ravel(), minlength=bn + 1)
        bright_any = (bsz <= 40)[bl] & (bl > 0)
        RD = 100  # 1 km diagnostic window
        cz = cozar[(cozar.tile == str(sj.get("tile") or "").lstrip("T")) & (cozar.date == s["date"])]
        cz_polys = []
        if len(cz):
            from shapely import wkt as _wkt
            cz_polys = [(int(r.fil_idx), _wkt.loads(r.bbox_wkt), int(r.n_pixels_fil)) for r in cz.itertuples()]
        k = 0
        small_px = 0
        for c in range(1, nc + 1):
            m = cl == c
            dm = det & m
            npx = int(dm.sum())
            if npx < MIN_ZONE_PX:
                small_px += npx
                continue
            k += 1
            zid = f"SZ-{s['key']}-{k:03d}"
            polys = [shape(g) for g, v in features.shapes(m.astype(np.uint8), mask=m, transform=tr, connectivity=8) if v == 1]
            poly = sh_tr(to4326, unary_union(polys).simplify(15))
            gj = json.loads(json.dumps(mapping(poly)), parse_float=lambda x: round(float(x), 6))
            area_km2 = abs(geod.geometry_area_perimeter(poly)[0]) / 1e6
            wz = water & m
            wz_km2 = float((wz | dm).sum()) * 1e-4
            ids = np.unique(lab[dm]); ids = ids[ids > 0]
            arts = {}
            for i in ids:
                a = art_px[i]
                if a:
                    arts[a] = arts.get(a, 0) + int((lab == i).sum())
            art_share = {a: round(v / npx, 3) for a, v in arts.items()}
            ex = {b: float(np.nanmedian(B[b][dm]) - wmed[b]) for b in B}
            white = (ex["B2"] / ex["B8"]) if ex["B8"] > 1e-4 else None
            b11z = float(np.nanmedian(B["B11"][wz])) if wz.sum() >= 20 else None
            glint_share = float((qa[m] == 6).mean())
            ys_, xs_ = np.nonzero(dm)
            rw = max(rs, rc, RD) + 1
            y0_, y1_ = max(ys_.min() - rw, 0), ys_.max() + rw + 1
            x0_, x1_ = max(xs_.min() - rw, 0), xs_.max() + rw + 1
            dmw = dm[y0_:y1_, x0_:x1_]
            dd_ = ndimage.distance_transform_edt(~dmw)
            near_s = dd_ <= rs
            near_c = dd_ <= rc
            ship_near_px = int((target[y0_:y1_, x0_:x1_] & near_s).sum())
            land_near_px = int((land[y0_:y1_, x0_:x1_] & near_c).sum())
            lw = land[y0_:y1_, x0_:x1_]
            bw_ = bright_any[y0_:y1_, x0_:x1_]
            diag = {"dist_land_m": round(float(dd_[lw].min()) * 10) if lw.any() else None,
                    "dist_bright_m": round(float(dd_[bw_].min()) * 10) if bw_.any() else None,
                    "n_bright_px_500m": int((bw_ & (dd_ <= 50)).sum()),
                    "b11_bright_max_500m": round(float(np.nanmax(np.where(bw_ & (dd_ <= 50), B["B11"][y0_:y1_, x0_:x1_], np.nan))), 4) if (bw_ & (dd_ <= 50)).any() else None,
                    "b4_water_zone": round(float(np.nanmedian(B["B4"][wz])), 4) if wz.sum() >= 20 else None,
                    "b8_water_zone": round(float(np.nanmedian(B["B8"][wz])), 4) if wz.sum() >= 20 else None,
                    "b11_det_median": round(float(np.nanmedian(B["B11"][dm])), 4),
                    "b8_det_median": round(float(np.nanmedian(B["B8"][dm])), 4),
                    "water_scene_b3": round(wmed["B3"], 4), "water_scene_b4": round(wmed["B4"], 4),
                    "n_det_px": npx, "n_obj": int(len(ids))}
            b3z = float(np.nanmedian(B["B3"][wz])) if wz.sum() >= 20 else None
            b3_ratio = (b3z / wmed["B3"]) if (b3z is not None and wmed["B3"] > 1e-4) else None
            shallow = b3z is not None and b3_ratio is not None and b3_ratio >= SHALLOW_B3_RATIO and b3z >= SHALLOW_B3_ABS
            b4z_, b8z_ = diag["b4_water_zone"], diag["b8_water_zone"]
            shoal = (b4z_ is not None and b4z_ >= SHOAL_B4 and (b4z_ >= 2 * wmed["B4"] or (b8z_ or 0) >= SHOAL_B8))
            boat = diag["b11_bright_max_500m"] is not None and diag["b11_bright_max_500m"] >= BOAT_B11
            shallow = shallow or shoal
            cloud_share = float(np.isin(qa[m], (3, 4)).mean())
            flags = []
            if cloud_share >= CLOUD_ZONE:
                flags.append("cloud")
            if (ship_near_px > 0 or boat) and not (art_share.get("ship", 0) + art_share.get("wake", 0) >= ART_FRAC):
                flags.append("ship")
            if land_near_px > 0:
                flags.append("coast")
            if shallow:
                flags.append("shallow")
            if art_share.get("ship", 0) + art_share.get("wake", 0) >= ART_FRAC:
                flags.append("ship")
            if art_share.get("seam", 0) >= ART_FRAC:
                flags.append("seam")
            if (b11z is not None and b11z > GLINT_B11) or glint_share >= 0.2:
                flags.append("glint")
            if (white is not None and white >= FOAM_WHITE) or (w10 is not None and w10 >= FOAM_WIND):
                flags.append("foam")
            pz = prob[dm]
            n_cz = sum(1 for _, g, _ in cz_polys if g.intersects(poly))
            det_status = "insufficient_data" if flags else "detected"
            # K2 / §23: «detected» only after all false-alarm filters; level B = the contour crosses a Cózar filament
            verification = "level_B_cozar" if (n_cz > 0 and not flags) else ("false_alarm_signs" if flags else "unverified")
            signs = {
                "foam": {"flag": "foam" in flags, "white_ratio_b2_b8": None if white is None else round(white, 3),
                         "wind10m_ms": info["wind10m_ms"],
                         "rule": f"прирост B2 над водой ≥ {FOAM_WHITE} прироста B8 («белый» спектр) или ветер ≥ {FOAM_WIND} м/с"},
                "glint": {"flag": "glint" in flags, "b11_water_median": None if b11z is None else round(b11z, 4),
                          "glint_share": round(glint_share, 3),
                          "rule": f"медиана B11 воды зоны > {GLINT_B11} или ≥ 20 % пикселей зоны — блик (маска качества)"},
                "ship": {"flag": "ship" in flags, "share": round(art_share.get("ship", 0) + art_share.get("wake", 0), 3),
                         "bright_target_px_within_500m": ship_near_px,
                         "rule": "≥ 30 % пикселей — у яркой цели или кильватера (reports/artifacts.md) или яркая цель с откликом в "
                                 "SWIR (судно, лодка: B11 ≥ 0,015) ближе 500 м, или малый яркий объект (≤ 40 пикс.) с B11 ≥ 0,01 ближе 500 м (лодка с кильватером)"},
                "coast": {"flag": "coast" in flags, "land_px_within_300m": land_near_px,
                          "rule": "суша (маска качества) ближе 300 м — прибой, кромка берега"},
                "shallow": {"flag": "shallow" in flags, "b3_water_zone": None if b3z is None else round(b3z, 4),
                            "b3_ratio_to_scene_water": None if b3_ratio is None else round(b3_ratio, 2),
                            "rule": "медиана B3 воды зоны ≥ 2× медианы воды снимка и ≥ 0,03, или B4 воды зоны ≥ 0,02 и (≥ 2× воды снимка или B8 ≥ 0,013) — мелководье, яркое дно, илистая банка"},
                "seam": {"flag": "seam" in flags, "share": art_share.get("seam", 0),
                         "rule": "≥ 30 % пикселей — прямая линия вдоль трека/границы яркости (шов)"},
                "cloud": {"flag": "cloud" in flags, "cloud_fraction": round(cloud_share, 3),
                          "rule": "облака или тени облаков (маска качества) ≥ 20 % зоны"},
                "diag": diag,
                "note": "эвристики, не классификатор; на размеченных данных не проверены",
            }
            area_m2 = npx * 100
            crop = write_crop(od, zid, m, det, rgb8, lab, art_px)
            lwd = round(area_m2 / wz_km2, 1) if wz_km2 > 0 else None
            zones.append({"type": "Feature", "id": zid, "geometry": gj, "properties": {
                "kind": "detection_zone", "layer_kind": "scene_zone", "zone_id": zid, "scene_key": s["key"],
                "scene_kind": s["kind"], "scene_id": sj["scene_id"], "region": s["region"],
                "title": f"{info['region_name']} · зона {k}", "mission": "Sentinel-2", "datetime": iso,
                "detection_status": det_status, "status": det_status,
                "concentration_status": "unavailable", "quantity": QUANTITY,
                "verification": verification, "training_scene": info.get("training_scene"),
                "flags": flags, "n_cozar_filaments": n_cz, "crop_file": crop,
                "wind_high": info["wind_high"],
                "measured": {"zone_area_km2": round(area_km2, 4), "suspicious_area_m2": area_m2, "n_pixels": npx,
                             "n_objects": int(len(ids)), "water_km2": round(wz_km2, 4),
                             "lwd_m2_km2": None if info["wind_high"] else lwd,
                             "lwd_note": LWD_WIND_NOTE if info["wind_high"] else None,
                             "quality": {"valid_water_fraction": round(float(wz.sum() / max(m.sum(), 1)), 3),
                                         "cloud_fraction": round(cloud_share, 3),
                                         "glint_fraction": round(glint_share, 3)},
                             "model": minfo},
                "probable": {"prob_max": round(float(pz.max()), 3), "prob_mean": round(float(pz.mean()), 3),
                             "signs": signs, "n_cozar_filaments": n_cz},
            }})
            for i in ids:
                g = sh_tr(to4326, unary_union(obj_geo.get(int(i), [])))
                pj = json.loads(json.dumps(mapping(g)), parse_float=lambda x: round(float(x), 7))
                pxm = lab == i
                dets.append({"type": "Feature", "id": f"D-{zid}-{int(i)}", "geometry": pj, "properties": {
                    "kind": "detection", "det_id": f"D-{zid}-{int(i)}", "zone_id": zid, "scene_id": sj["scene_id"],
                    "datetime": iso, "n_pixels": int(pxm.sum()), "area_m2": int(pxm.sum()) * 100,
                    "prob_max": round(float(prob[pxm].max()), 4), "prob_mean": round(float(prob[pxm].mean()), 4),
                    "threshold": 0.63, "in_strip": True, "artifact": art_px[i] or None}})
        info["n_zones"] = k
        info["small_cluster_px"] = small_px
        if k == 0:  # evaluated, nothing (big enough) found: the observed water of the crop is «не обнаружено»
            # §29 А: with wind > 5 m/s windrows are mixed away — «не обнаружено» becomes «недостаточно данных (ветер …)»
            zero_st = "insufficient_data" if info["wind_high"] else "not_detected"
            zid = f"SZ-{s['key']}-000"
            poly = box(*bw)
            gj = json.loads(json.dumps(mapping(poly)), parse_float=lambda x: round(float(x), 6))
            zones.append({"type": "Feature", "id": zid, "geometry": gj, "properties": {
                "kind": "detection_zone", "layer_kind": "scene_zone", "zone_id": zid, "scene_key": s["key"],
                "scene_kind": s["kind"], "scene_id": sj["scene_id"], "region": s["region"],
                "title": f"{info['region_name']} · вся вырезка", "mission": "Sentinel-2", "datetime": iso,
                "detection_status": zero_st, "status": zero_st, "concentration_status": "unavailable",
                "wind_high": info["wind_high"],
                "quantity": QUANTITY,
                "flags": ["wind"] if info["wind_high"] else [], "n_cozar_filaments": 0,
                "verification": "wind" if info["wind_high"] else None,
                "measured": {"zone_area_km2": round(abs(Geod(ellps="WGS84").geometry_area_perimeter(poly)[0]) / 1e6, 3),
                             "suspicious_area_m2": int(det.sum()) * 100, "n_pixels": int(det.sum()), "n_objects": int(n),
                             "water_km2": round(water_km2, 3), "lwd_m2_km2": info["lwd_m2_km2"],
                             "lwd_note": LWD_WIND_NOTE if info["wind_high"] else None,
                             "quality": {"valid_water_fraction": round(float(water.mean()), 3),
                                         "cloud_fraction": round(float(np.isin(qa, (3, 4)).mean()), 3),
                                         "glint_fraction": round(float((qa == 6).mean()), 3)},
                             "model": minfo},
                "probable": {"prob_max": round(float(prob[water].max()), 3) if water.any() else None, "prob_mean": None,
                             "signs": None, "n_cozar_filaments": 0},
            }})
        fn = field_nearby(cx, cy, s["date"])
        for z in zones:
            z["properties"]["field_nearby"] = fn
    info["n_zones_total"] = len(zones)
    info["by_status"] = {st: sum(1 for z in zones if z["properties"]["detection_status"] == st)
                         for st in ("detected", "not_detected", "insufficient_data")}
    (od / "zones.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": zones}, ensure_ascii=False), encoding="utf-8")
    (od / "detections.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": dets}, ensure_ascii=False), encoding="utf-8")
    (od / "scene.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    cozar = pd.read_csv(ROOT / "reports" / "extra_data" / "registry_cozar2024.csv.gz", usecols=["fil_idx", "tile", "date", "bbox_wkt", "n_pixels_fil"])
    minfo = model_info()
    scenes = scene_list(set(a.only) if a.only else None)
    print(len(scenes), "scenes", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)

    def one(s):
        try:
            i = build_scene(s, cozar, minfo)
            print(f"{s['key']}: evaluable={i['evaluable']} zones={i['n_zones_total']} {i['by_status']}", flush=True)
            return i
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            return {"key": s["key"], "error": repr(e)}
    with ThreadPoolExecutor(a.workers) as ex:
        infos = list(ex.map(one, scenes))
    idx_p = OUT / "index.json"
    old = json.loads(idx_p.read_text(encoding="utf-8"))["scenes"] if idx_p.is_file() and a.only else []
    keep = {i["key"]: i for i in old}
    keep.update({i["key"]: i for i in infos})
    idx = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "model": minfo,
           "scenario": None, "quantity": QUANTITY, "rules": {"cluster_m": CLUSTER_M, "min_zone_px": MIN_ZONE_PX, "glint_b11": GLINT_B11,
                                           "foam_white": FOAM_WHITE, "foam_wind_ms": FOAM_WIND, "artifact_share": ART_FRAC,
                                           "illumination": "src/macroplastic/case/illumination.py (зенит ≥ 58° или B3 воды < 0.003 → не оценивается)"},
           "scenes": sorted(keep.values(), key=lambda i: i["key"])}
    idx_p.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", idx_p)


if __name__ == "__main__":
    main()
