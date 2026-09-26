r"""ISPRA MSFD «Modulo 2bis» (визуальный учёт плавающего макромусора с судна, Италия, 2018–2023) × Sentinel-2 L2A того же дня.

Воспроизводимый прогон по фиксированному списку docs/research/pairs/ispra_167.csv (167 трансект с >= 5 предметами и сценой S2
того же дня). Для каждой трансекты: сцена исходной обработки `_0` (на ней обучен и настроен детектор; `_1` — только для
сравнения, флаг --compare1), маски качества и детектор — scripts/case/pair_quality.py:process_s2 без изменений (weights/lgbm,
без гармонизации, порог из весов), правило освещённости — src/macroplastic/case/illumination.py.

  $env:CUDA_VISIBLE_DEVICES="-1"; .venv\Scripts\python.exe scripts\search\ispra_scan.py fetch        # 8 архивов ISPRA (21 МБ) -> data/extra/ispra/ + csv (LibreOffice)
  .venv\Scripts\python.exe scripts\search\ispra_scan.py run [--workers 3] [--compare1 10]            # все 167 -> data/search/ispra/
  .venv\Scripts\python.exe scripts\search\ispra_scan.py levels                                        # -> docs/research/pairs/ispra_levels.csv

Что считается (на `_0`):
  - маски: decision/reason, доля пригодной воды, облака, блик в зоне ±500 м вокруг трека; детектор оценивается (зенит, B3);
  - пиксели детектора: зона ±500 м (как в pair_quality, после удаления компонент у облаков/теней), узкая ±50 м (prob >= порога
    на пригодной воде, без удаления компонент — приближение);
  - контрольная соседняя вода: кольцо 1.5–2.5 км от трека (та же вырезка), пикселей детектора на км² пригодной воды —
    фон сцены; «превышение» = пикс./км² в зоне ±500 м минус пикс./км² в кольце;
  - предметы с GPS: сколько ближе 50/100 м к пикселю детектора, расстояние ближайшего;
  - окна Cózar/Arias 2024 (reports/extra_data/registry_cozar2024.csv.gz, пиксельная рамка) той же даты: сколько в 1/3 км от
    трека, предметов в 100/500 м от рамки (каталог кончается 17.09.2021);
  - Δt = время S2 минус время учёта (начало…конец трансекты) в двух вариантах: время в файле местное (CET/CEST) или UTC —
    в файлах пояс не указан.
Уровни (levels): D — нет сцены `_0`, маски отклонили или детектор не оценивается; C — место и день совпадают, пиксели годны,
но видимого сигнала у трека нет (превышение <= 0 и окон в 1 км нет) или |Δt| > 3 ч в одном из вариантов; «A-кандидат» —
годные пиксели, |Δt| <= 3 ч в обоих вариантах и видимый сигнал у трека (предмет <= 100 м от пикселя детектора или окно
Cózar в <= 100 м от трека); A/C/D для кандидатов — только после просмотра глазами (словарь VISUAL ниже).
Балл для «10 лучших» = плотность предметов (шт./км² при полосе 5 м по протоколу SNPA 2024) × видимый сигнал (превышение
пикс./км² на `_0` + пиксели окон Cózar в 1 км на км² зоны) × чистая сцена (1 — принято и оценивается, иначе 0).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "case"))
import pair_quality as pq  # noqa: E402
from macroplastic.live import stac as st  # noqa: E402
from macroplastic.case import illumination as illum  # noqa: E402

BASE = "http://www.db-strategiamarina.isprambiente.it/app/files/datiMonitoraggio2018_2023/"
ZIPS = ["Modulo_2bis_Adriatico_2018-2023.zip", "Modulo_2bis_Ionio_2018-2023.zip", "Modulo_2bis_Med_Occ_2018-2023.zip",
        "Modulo_2bis_2020_Adriatico_2018-2023.zip", "Modulo_2bis_2020_Ionio_2018-2023.zip", "Modulo_2bis_2020_Med_Occ_2018-2023.zip",
        "Modulo_2bis_ISPRA_2020.zip", "Modulo_2bis_ISPRA_2021.zip"]
RAW = ROOT / "data" / "extra" / "ispra"
OUT = ROOT / "data" / "search" / "ispra"
LIST = ROOT / "docs" / "research" / "pairs" / "ispra_167.csv"
LEVELS = ROOT / "docs" / "research" / "pairs" / "ispra_levels.csv"
REG = ROOT / "reports" / "extra_data" / "registry_cozar2024.csv.gz"
STRIP_M = 5.0          # ширина полосы учёта по протоколу SNPA 2024 (Modulo 2bis, табл. 10.7) — в файлах 2018–2020 не записана
ZONE_M, NARROW_M = 500.0, 50.0
CTRL_IN, CTRL_OUT = 1500.0, 2500.0
# ручной просмотр кандидатов (панели в data/search/ispra/<tid>__<item>/mask.png и увеличения); ключ (трансекта, дата)
VISUAL = {
    ("604", "2019-09-16"): ("A (место-день, не калибровочная)",
                            "нити Cózar 11489/11490 пересекают трек, на увеличении — две слабые нити С–Ю, пиксели детектора на левой; "
                            "ни один из 16 предметов не ближе 50 м к пикселю детектора (ближайший 85 м); суммарная площадь предметов "
                            "~10⁻⁵ площади полосы — сигнал нити дают не посчитанные предметы"),
    ("IT_m1lt02_3", "2019-10-28"): ("D", "пиксели у мелких кучевых облаков и их краёв; нити/пятна нет"),
    ("M1T6", "2019-07-24"): ("D", "на _0 ближайший предмет в 434 м от пикселя (совпадение 29 м было только на _1)"),
}


# ------------------------------------------------------------------------------------------------ fetch

def fetch():
    (RAW / "zip").mkdir(parents=True, exist_ok=True)
    import requests
    for z in ZIPS:
        fp = RAW / "zip" / z
        if not fp.exists():
            r = requests.get(BASE + z, timeout=300)
            r.raise_for_status()
            fp.write_bytes(r.content)
            print("downloaded", z, len(r.content))
        with zipfile.ZipFile(fp) as zf:
            zf.extractall(RAW / "x" / fp.stem)
    soffice = next((p for p in [r"C:\Program Files\LibreOffice\program\soffice.exe", "soffice", "libreoffice"]
                    if Path(p).exists() or p in ("soffice", "libreoffice")), None)
    (RAW / "csv").mkdir(parents=True, exist_ok=True)
    for f in sorted((RAW / "x").rglob("*.xls*")):
        if list((RAW / "csv").glob(f"{f.stem}-*.csv")):
            continue
        # every sheet -> <file>-<sheet>.csv, UTF-8, comma (StarCalc filter options, sheet = -1)
        subprocess.run([soffice, "--headless", "--convert-to", 'csv:Text - txt - csv (StarCalc):44,34,76,1,,0,false,true,false,false,false,-1',
                        "--outdir", str(RAW / "csv"), str(f)], check=False, capture_output=True, timeout=300)
    print("csv files:", len(list((RAW / "csv").glob("*.csv"))))


# ------------------------------------------------------------------------------------------------ transects

def _num(x):
    return float(str(x).replace(",", "."))


def glob_escape(s: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", s)


def load_transect(csvdir: Path, file: str, tid: str, date: str) -> dict:
    """Трек (lon, lat), время начала/конца (как в файле), предметы с GPS. Новый формат (2021+): точки BEG/START/LIT/STOP/END с GPS
    и временем; старый (2018–2020): начало/конец трансекты + предметы без времени."""
    # new format: sheet MacroFlotCamp (also MacroFloatCamp, MacroFlotCamp_<season> in the ISPRA ferry files)
    newfs = [f for f in sorted(csvdir.glob(f"{glob_escape(file)}-Macro*Camp*.csv"))
             if "-DD_" not in f.name and "plastiche" not in f.name.lower()]
    if newfs:
        d = pd.concat([pd.read_csv(f, dtype=str).rename(columns=lambda c: c.strip()) for f in newfs], ignore_index=True)
        d = d[d.COD_Effort == tid].copy()
        d["lat"], d["lon"] = d.Latitude.map(_num), d.Longitude.map(_num)
        pts = d[d.Cod_Points.isin(["BEG", "START", "LIT", "STOP", "END"])].sort_values("Time")
        line = list(dict.fromkeys(zip(pts.lon.round(6), pts.lat.round(6))))
        it = d[d.Cod_Points == "LIT"]
        items = pd.DataFrame(dict(lon=it.lon, lat=it.lat, cat=it.IDCategoriaRifiuto, size=it.Size, material=it.Material))
        meta = dict(fmt="new (GPS per item)", strip_field=d.Strip.iloc[0] if len(d) else None, speed_kn=d.Mean_speed.iloc[0] if len(d) else None)
        t0, t1 = (pts.Time.iloc[0], pts.Time.iloc[-1]) if len(pts) else (None, None)
    else:
        f = pd.read_csv(csvdir / f"{file}-MacroplasticheFlot.csv", dtype=str).rename(columns=lambda c: c.strip())
        y, m, dd = date.split("-")
        f = f[(f.NationalStationID == tid) & (f.Year.astype(int) == int(y)) & (f.Month.astype(int) == int(m)) & (f.Day.astype(int) == int(dd))]
        r = f.iloc[0]
        line = [(_num(r.LongitudeInizio), _num(r.LatitudeInizio)), (_num(r.LongitudeFine), _num(r.LatitudeFine))]
        c = pd.read_csv(csvdir / f"{file}-MacroplasticheFlotCamp.csv", dtype=str).rename(columns=lambda c: c.strip())
        c = c[c.NationalStationID == tid]
        items = pd.DataFrame(dict(lon=c.Longitude.map(_num), lat=c.Latitude.map(_num), cat=c.ComposizioneLivII, size=c.Grandezza,
                                  material=c.ComposizioneLivI)) if len(c) else pd.DataFrame(columns=["lon", "lat", "cat", "size", "material"])
        meta = dict(fmt="old (start/end, items without time)", speed_kn=r.VelocitaImbarcazione, sea_state=r.StatoMare)
        t0, t1 = r.Time, None
    line = [p for p in line if np.isfinite(p[0]) and np.isfinite(p[1])]
    note = ""
    if line:  # bad GPS rows: drop points > 30 km from the median point
        mlon, mlat = np.median([p[0] for p in line]), np.median([p[1] for p in line])
        kx = 111.32 * math.cos(math.radians(mlat))
        keep = [p for p in line if math.hypot((p[0] - mlon) * kx, (p[1] - mlat) * 111.32) <= 30]
        if len(keep) < len(line):
            note = f"dropped {len(line) - len(keep)} track points > 30 km from median"
        line = keep
        from pyproj import Geod
        L = Geod(ellps="WGS84").line_length([p[0] for p in line], [p[1] for p in line]) if len(line) > 1 else 0
        if L > 60000 and len(items):  # long ferry routes: keep ±20 km around the median item
            ilon, ilat = float(items.lon.median()), float(items.lat.median())
            line = [p for p in line if math.hypot((p[0] - ilon) * kx, (p[1] - ilat) * 111.32) <= 20]
            note = (note + "; " if note else "") + "track clipped to ±20 km around the median item"
    return dict(line=line, t0=t0, t1=t1, items=items.reset_index(drop=True), meta=meta, note=note)


def _hms(x):
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", str(x))
    return f"{int(m.group(1)):02d}:{m.group(2)}:{m.group(3) or '00'}" if m else None


def _local_offset_h(day: str) -> int:
    d = pd.Timestamp(day)

    def last_sun(mo):
        x = pd.Timestamp(year=d.year, month=mo, day=31)
        return x - pd.Timedelta(days=(x.dayofweek + 1) % 7)
    return 2 if last_sun(3) <= d < last_sun(10) else 1


def delta_t(day: str, t0, t1, speed_kn, length_km, s2_time: str) -> dict:
    """S2 minus survey time (start…end), hours, for 'time in file is local (CET/CEST)' and 'time is UTC'."""
    out = {}
    h0 = _hms(t0)
    if not h0:
        return dict(dt_local="?", dt_utc="?")
    try:
        T0 = pd.Timestamp(f"{day} {h0}")
    except ValueError:  # invalid time in the source file (e.g. 82:55:00)
        return dict(dt_local=f"? (время в файле {t0})", dt_utc="?")
    h1 = _hms(t1)
    try:
        h1_ok = bool(h1) and pd.Timestamp(f"{day} {h1}") is not None
    except ValueError:
        h1_ok = False
    if h1_ok:
        dur = (pd.Timestamp(f"{day} {h1}") - T0).total_seconds() / 3600
    else:
        try:
            dur = length_km / (float(str(speed_kn).replace(",", ".")) * 1.852)
        except Exception:  # noqa: BLE001
            dur = 0.0
    s2 = pd.Timestamp(s2_time)
    for tag, sh in (("dt_local", _local_offset_h(day)), ("dt_utc", 0)):
        a = (s2 - (T0 - pd.Timedelta(hours=sh))).total_seconds() / 3600
        out[tag] = f"{a - dur:+.1f}…{a:+.1f}" if dur > 0 else f"{a:+.1f}"
        out[tag + "_absmin"] = min(abs(a), abs(a - dur)) if not (a - dur <= 0 <= a) else 0.0
        out[tag + "_absmax"] = max(abs(a), abs(a - dur))
    return out


# ------------------------------------------------------------------------------------------------ S2

_CLIENT = None


def s2_items(day: str, lon: float, lat: float):
    """Same-day L2A items at the point, per tile: (_0 item, _1 item or None), tiles sorted by cloud of the _0 item."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = st.open_client("earth-search")
    its = list(_CLIENT.search(collections=["sentinel-2-l2a"], intersects=dict(type="Point", coordinates=[lon, lat]),
                              datetime=f"{day}T00:00:00Z/{day}T23:59:59Z").items())
    by = {}
    for it in its:
        by.setdefault(st.tile_of(it), []).append(it)
    res = []
    for tile, lst in by.items():
        lst = sorted(lst, key=lambda i: i.id)
        i0 = next((i for i in lst if i.id.endswith("_0_L2A")), lst[0])
        i1 = next((i for i in lst if i.id.endswith("_1_L2A")), None)
        res.append((i0, i1))
    return sorted(res, key=lambda p: p[0].properties.get("eo:cloud_cover", 100))


def process(T: dict, item_id: str, outdir: Path, cfg, pred, reg_day: pd.DataFrame) -> dict:
    import rasterio
    from pyproj import Geod, Transformer
    from rasterio.features import rasterize
    from shapely import wkt
    from shapely.geometry import LineString, Point
    line = T["line"]
    L_m = Geod(ellps="WGS84").line_length([p[0] for p in line], [p[1] for p in line])
    lon_c, lat_c = float(np.mean([p[0] for p in line])), float(np.mean([p[1] for p in line]))
    g = dict(sample_ids="ispra", lat=lat_c, lon=lon_c, width_m=2 * ZONE_M, length_km=L_m / 1000, field_items_km2=None,
             field_sample_id="ispra", sampling_method="ISPRA MSFD visual", line=None, lines=[line],
             geometry_status="track", geometry_source="ISPRA Modulo 2bis")
    r = pq.process_s2(SimpleNamespace(endpoint="earth-search", collection="sentinel-2-l2a", item_id=item_id), g, cfg, pred, outdir)
    q, det = r["quality"], r["detector"]
    thr = float(pred.threshold)
    tr = Transformer.from_crs("EPSG:4326", f"EPSG:{r['epsg']}", always_xy=True)
    with rasterio.open(outdir / "prob.tif") as s:
        prob, atr, H, W = s.read(1), s.transform, s.height, s.width
    with rasterio.open(outdir / "quality.tif") as s:
        qa = s.read(1)
    water = qa == pq.Q_WATER
    dm = (prob >= round(thr * 255)) & water
    ln = LineString([tr.transform(*p) for p in line])

    def ras(geom):
        return rasterize([(geom, 1)], out_shape=(H, W), transform=atr, all_touched=True, fill=0, dtype="uint8").astype(bool)
    zone, narrow = ras(ln.buffer(ZONE_M, cap_style=2)), ras(ln.buffer(NARROW_M, cap_style=2))
    ring = ras(ln.buffer(CTRL_OUT).difference(ln.buffer(CTRL_IN)))
    zw, rw = float((zone & water).sum()) * 1e-4, float((ring & water).sum()) * 1e-4
    z_px, r_px = int((dm & zone).sum()), int((dm & ring).sum())
    z_d = z_px / zw if zw > 0 else None
    r_d = r_px / rw if rw > 0 else None
    ys, xs = np.where(dm)
    dmin = []
    if len(xs):
        px = np.c_[atr.c + (xs + 0.5) * atr.a, atr.f + (ys + 0.5) * atr.e]
        for it in T["items"].itertuples():
            x, y = tr.transform(it.lon, it.lat)
            dmin.append(float(np.hypot(px[:, 0] - x, px[:, 1] - y).min()))
    # Cózar windows of the same date near the track (pixel bbox)
    lw = []
    for w in reg_day.itertuples():
        pu = LineString([tr.transform(*xy) for xy in wkt.loads(w.bbox_wkt).exterior.coords]).convex_hull
        dd = pu.distance(ln)
        if dd <= 3000:
            lw.append((int(w.fil_idx), dd, int(w.n_pixels_fil), pu))
    n_lw100 = n_lw500 = 0
    for it in T["items"].itertuples():
        P = Point(*tr.transform(it.lon, it.lat))
        d_ = min([p.distance(P) for *_, p in lw], default=1e9)
        n_lw100 += d_ <= 100
        n_lw500 += d_ <= 500
    w_, s_, e_, n_ = r["bounds_utm"]
    inv = Transformer.from_crs(f"EPSG:{r['epsg']}", "EPSG:4326", always_xy=True)
    xs4, ys4 = inv.transform([w_, e_, w_, e_], [s_, s_, n_, n_])
    zen = illum.solar_zenith(r["scene_datetime"], [min(xs4), min(ys4), max(xs4), max(ys4)])
    b3 = illum.water_median(illum.rgb_png_b3(outdir / "rgb.png", 512), qa, 1)
    ok, low, weak = illum.detector_evaluable(zen, b3)
    return dict(s2_time=r["scene_datetime"][:19], decision=r["decision"], reason=r["reason"], water=q["valid_water_frac"],
                cloud=q["cloud_frac"], glint=q["glint_frac"], land=q["land_frac"], evaluable=bool(ok), sun_zenith=round(float(zen), 1),
                water_b3=None if b3 is None else round(float(b3), 4), track_km=round(L_m / 1000, 2),
                det_px_500=int(det["det_px_strip"]), det_obj_500=int(det["n_det"]), det_px_50=int((dm & narrow).sum()),
                zone_water_km2=round(zw, 3), zone_px_per_km2=None if z_d is None else round(z_d, 2),
                ctrl_water_km2=round(rw, 3), ctrl_px=r_px, ctrl_px_per_km2=None if r_d is None else round(r_d, 2),
                excess_px_per_km2=None if (z_d is None or r_d is None) else round(z_d - r_d, 2),
                items_le50m_det=int(sum(x <= 50 for x in dmin)), items_le100m_det=int(sum(x <= 100 for x in dmin)),
                item_min_d_det_m=round(min(dmin)) if dmin else None,
                lw_3km=len(lw), lw_1km=int(sum(x[1] <= 1000 for x in lw)), lw_min_m=round(min([x[1] for x in lw])) if lw else None,
                lw_px_1km=int(sum(x[2] for x in lw if x[1] <= 1000)), lw_ids=";".join(f"{x[0]}@{x[1]:.0f}m" for x in lw[:10]),
                items_le100m_lw=int(n_lw100), items_le500m_lw=int(n_lw500))


# ------------------------------------------------------------------------------------------------ run

def run(workers: int, compare1: int, csvdir: Path, only: str | None):
    L = pd.read_csv(LIST)
    if only:
        L = L[L["чётность"] == only]
    reg = pd.read_csv(REG)
    cfg = pq.load_cfg(ROOT / "configs" / "case_pairs.yaml")
    cfg["network"]["workers"] = 4
    assert pq.harmonize_mode(cfg) is None, "detector must run without harmonization"
    from macroplastic.models.lgbm_predict import load_predictor
    pred = load_predictor(ROOT / cfg["detector"]["weights"], harmonize=None)
    res_fp = OUT / "results.csv"
    OUT.mkdir(parents=True, exist_ok=True)
    rows = pd.read_csv(res_fp).to_dict("records") if res_fp.exists() else []
    done = {(r["n"], r["variant"]) for r in rows if r.get("status") == "ok" or str(r.get("status", "")).startswith("no")}

    def job(l):
        n, file, tid, day = int(l["№"]), l["файл_ISPRA"], str(l["трансекта"]), l["дата_учёта"]
        base = dict(n=n, file=file, tid=tid, date=day, n_items=int(l["предметов"]), variant="_0")
        if (n, "_0") in done:
            return None
        try:
            T = load_transect(csvdir, file, tid, day)
            if len(T["line"]) < 2:
                return dict(base, status="no track")
            pairs = s2_items(day, float(np.mean([p[0] for p in T["line"]])), float(np.mean([p[1] for p in T["line"]])))
            if not pairs:
                return dict(base, status="no S2")
            i0, i1 = pairs[0]
            d = OUT / f"{tid}__{i0.id}".replace("/", "_")
            m = process(T, i0.id, d, cfg, pred, reg[reg.date == day])
            dt = delta_t(day, T["t0"], T["t1"], T["meta"].get("speed_kn"), m["track_km"], m["s2_time"])
            return dict(base, status="ok", s2=i0.id, s2_1=None if i1 is None else i1.id, tile_cloud=round(i0.properties.get("eo:cloud_cover", -1), 1),
                        t_start=T["t0"], t_end=T["t1"], fmt=T["meta"]["fmt"], track_note=T["note"],
                        items_cat=";".join(f"{k}:{v}" for k, v in T["items"].cat.value_counts().head(6).items()),
                        items_size=";".join(f"{k}:{v}" for k, v in T["items"]["size"].value_counts().sort_index().items()),
                        path=str(d.relative_to(ROOT)).replace("\\", "/"), **dt, **m)
        except Exception as e:  # noqa: BLE001
            return dict(base, status="error", reason=f"{type(e).__name__}: {str(e)[:200]}")
    todo = [l for _, l in L.iterrows()]
    with ThreadPoolExecutor(workers) as ex:
        for k, r in enumerate(ex.map(job, todo)):
            if r is None:
                continue
            rows = [x for x in rows if not (x["n"] == r["n"] and x["variant"] == r["variant"])] + [r]
            pd.DataFrame(rows).sort_values(["n", "variant"]).to_csv(res_fp, index=False)
            print({k2: r.get(k2) for k2 in ("n", "tid", "date", "status", "s2", "decision", "det_px_500", "det_px_50", "excess_px_per_km2",
                                              "items_le50m_det", "lw_1km")}, flush=True)
    if compare1:
        levels()
        lv = pd.read_csv(LEVELS)
        top = lv[lv["топ10"] == True].head(compare1)  # noqa: E712
        R = pd.read_csv(res_fp)
        for t in top.itertuples():
            r0 = R[(R.n == t[1]) & (R.variant == "_0")].iloc[0]
            if not isinstance(r0.get("s2_1"), str):
                continue
            T = load_transect(csvdir, r0.file, str(r0.tid), r0.date)
            d = OUT / f"{r0.tid}__{r0.s2_1}"
            m = process(T, r0.s2_1, d, cfg, pred, reg[reg.date == r0.date])
            rows.append(dict(n=int(r0.n), file=r0.file, tid=r0.tid, date=r0.date, n_items=int(r0.n_items), variant="_1", status="ok",
                             s2=r0.s2_1, path=str(d.relative_to(ROOT)).replace("\\", "/"), **m))
            pd.DataFrame(rows).sort_values(["n", "variant"]).to_csv(res_fp, index=False)


# ------------------------------------------------------------------------------------------------ levels

def levels():
    L = pd.read_csv(LIST)
    R = pd.read_csv(OUT / "results.csv")
    R0 = R[R.variant == "_0"].set_index("n")
    R1 = R[R.variant == "_1"].set_index("n") if (R.variant == "_1").any() else pd.DataFrame()
    out = []
    for l in L.itertuples(index=False):
        n = int(l[0])
        row = {"№": n, "файл_ISPRA": l[2], "трансекта": l[3], "дата_учёта": l[4], "предметов": int(l[5])}
        if n not in R0.index or R0.loc[n, "status"] != "ok":
            st_ = "не обработано" if n not in R0.index else R0.loc[n, "status"]
            row.update(уровень="D", тип=f"нет сцены/трека ({st_})" if n in R0.index else "не обработано", балл=0.0)
            if n in R0.index and isinstance(R0.loc[n].get("reason"), str):
                row["причина"] = R0.loc[n, "reason"]
            out.append(row)
            continue
        r = R0.loc[n]
        dens = r.n_items / (r.track_km * STRIP_M / 1000) if r.track_km else None
        clean = (r.decision == "accept") and bool(r.evaluable)
        ex = r.excess_px_per_km2 if pd.notna(r.excess_px_per_km2) else 0.0
        lw_d = (r.lw_px_1km / r.zone_water_km2) if r.zone_water_km2 else 0.0
        vis = max(ex, 0.0) + lw_d
        score = (dens or 0) * vis * (1.0 if clean else 0.0)
        dt_ok = (r.dt_local_absmax <= 3) and (r.dt_utc_absmax <= 3) if pd.notna(r.get("dt_local_absmax")) and pd.notna(r.get("dt_utc_absmax")) else False
        near = (r.items_le100m_det > 0) or (pd.notna(r.lw_min_m) and r.lw_min_m <= 100)
        if not clean:
            lvl, typ = "D", ("маски: " + str(r.reason)) if r.decision != "accept" else "детектор не оценивается (низкое солнце/слабый сигнал воды)"
        elif near and dt_ok:
            lvl, typ = "A-кандидат", "годные пиксели, |Δt| ≤ 3 ч, видимый сигнал у трека — нужен просмотр"
        elif vis > 0 or near:
            lvl, typ = "C", "место-день совпадают; сигнал в зоне, но не у предметов" + ("" if dt_ok else " / |Δt| > 3 ч в одном из вариантов пояса")
        else:
            lvl, typ = "C", "место-день совпадают, пиксели годны; видимого сигнала у трека нет (как ADIS)"
        v = VISUAL.get((str(r.tid), r.date))
        if v:
            lvl, typ = v[0], v[1]
        row.update(длина_км=r.track_km, плотность_шт_км2_полоса5м=round(dens, 1) if dens else None, S2_0=r.s2, S2_1=r.get("s2_1"),
                   S2_время_UTC=r.s2_time, облачность_тайла=r.tile_cloud, Δt_ч_если_местное=r.dt_local, Δt_ч_если_UTC=r.dt_utc,
                   маски=r.decision, причина=r.reason if isinstance(r.reason, str) else "", пригодная_вода=r.water, облака=r.cloud, блик=r.glint,
                   оценивается=bool(r.evaluable), зенит=r.sun_zenith, пикс_500м=int(r.det_px_500), пикс_50м=int(r.det_px_50),
                   пикс_на_км2_зона=r.zone_px_per_km2, пикс_на_км2_контроль=r.ctrl_px_per_km2, превышение_пикс_км2=r.excess_px_per_km2,
                   предметов_le50м_от_детектора=int(r.items_le50m_det), предметов_le100м_от_детектора=int(r.items_le100m_det),
                   ближайший_предмет_м=r.item_min_d_det_m, окон_Cózar_1км=int(r.lw_1km), окон_Cózar_3км=int(r.lw_3km), ближайшее_окно_м=r.lw_min_m,
                   предметов_le100м_от_окна=int(r.items_le100m_lw), категории=r.items_cat, размеры=r.items_size,
                   уровень=lvl, тип=typ, балл=round(score, 1), путь=r.path)
        if len(R1) and n in R1.index:
            r1 = R1.loc[n]
            row.update(пикс_500м_на_1=int(r1.det_px_500), предметов_le50м_на_1=int(r1.items_le50m_det), ближайший_предмет_м_на_1=r1.item_min_d_det_m)
        out.append(row)
    o = pd.DataFrame(out)
    o["топ10"] = False
    cand = o[(o["балл"] > 0) & ~o["уровень"].astype(str).str.startswith("D")]
    cand = cand.sort_values("балл", ascending=False).drop_duplicates(["трансекта", "дата_учёта"])  # one survey in two files
    top = cand.head(10).index
    o.loc[top, "топ10"] = True
    o.to_csv(LEVELS, index=False, encoding="utf-8")
    print(LEVELS, len(o), o["уровень"].value_counts().to_dict(), "top10:", int(o["топ10"].sum()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["fetch", "run", "levels"])
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--compare1", type=int, default=0)
    ap.add_argument("--csv-dir", default=str(RAW / "csv"))
    ap.add_argument("--only", default=None, help="чёт | нечёт (строки списка)")
    a = ap.parse_args()
    if a.stage == "fetch":
        fetch()
    elif a.stage == "run":
        run(a.workers, a.compare1, Path(a.csv_dir), a.only)
    else:
        levels()


if __name__ == "__main__":
    main()
