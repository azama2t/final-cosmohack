"""L91 (§11 п.3): новые полевые данные с числами -> реестр кандидатов для розыска снимков.

Источники (сырьё в data/extra/field/, не в git):
  * Lebreton et al. 2018, figshare 5873142 (CC BY 4.0): MosaicDebrisInfo — 1595 объектов > ~0.2 м
    с координатами/размером на аэромозаиках C-130 (02 и 06.10.2016), StationInfo — 31 бин мозаик.
  * ADIS, de Vries et al. 2026, 4TU.ResearchData 10.4121/ddede7f5-aca5-42ae-b851-e0bbb9a2c4c2 (CC BY 4.0):
    Segments.csv — 21 444 отрезка по 10 км (время GPS, скан-ширина, площадь, счёт >5/>10/>50 см),
    Objects.csv — 22 642 предмета (время, координаты камеры, размер).
Для каждого события/отрезка — есть ли Sentinel-2 L2A (earth-search) в ±1 сут, центр внутри следа снимка.
Аэро- и корабельные фото — другой тип изображения, чем Sentinel-2: здесь только числа, не пиксели.

Запуск:  .venv\\Scripts\\python.exe scripts/extra/field_candidates.py [--offline]
Выход:   reports/extra_data/field_candidates.csv, data/extra/field/adis_s2_match.csv, data/extra/field/summary.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "extra" / "field"
CACHE = RAW / "stac_cache"
OUT_CSV = ROOT / "reports" / "extra_data" / "field_candidates.csv"
ES = "https://earth-search.aws.element84.com/v1"
COLL = "sentinel-2-l2a"
S2_START = pd.Timestamp("2015-06-23", tz="UTC")

LEB_DOI = "10.6084/m9.figshare.5873142"
ADIS_DOI = "10.4121/ddede7f5-aca5-42ae-b851-e0bbb9a2c4c2"
# Lebreton 2018, Methods: полёт 1 — 02.10.2016 18:56–21:14 UTC, полёт 2 — 06.10.2016 22:14 – 07.10 00:37 UTC,
# высота ~400 м, 140 уз, кадр ~360 м поперёк × 240 м вдоль, ~0.1 м/пикс.
FLIGHTS = {"161002": ("2016-10-02T18:56Z", "2016-10-02T21:14Z"), "161006": ("2016-10-06T22:14Z", "2016-10-07T00:37Z")}

_session = requests.Session()
OFFLINE = False


def stac_search(bbox, dt0: str, dt1: str, retries: int = 5) -> list[dict]:
    body = {"collections": [COLL], "bbox": [round(b, 4) for b in bbox], "datetime": f"{dt0}/{dt1}", "limit": 100}
    key = hashlib.sha1(json.dumps([ES, body], sort_keys=True).encode()).hexdigest()
    fp = CACHE / f"{key}.json"
    if fp.exists():
        return json.loads(fp.read_text())
    if OFFLINE:
        raise RuntimeError("offline and not cached")
    feats, nxt = [], (f"{ES}/search", body)
    while nxt:
        link, payload = nxt
        for att in range(retries):
            try:
                r = _session.post(link, json=payload, timeout=60)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                r.raise_for_status()
                js = r.json()
                break
            except Exception:  # noqa: BLE001
                if att == retries - 1:
                    raise
                time.sleep(2.0 * 2 ** att)
        for f in js.get("features", []):
            p = f.get("properties", {})
            feats.append(dict(id=f["id"], geometry=f.get("geometry"), datetime=p.get("datetime"),
                              cloud=p.get("eo:cloud_cover"), mgrs=p.get("s2:mgrs_tile") or p.get("grid:code")))
        nxt = None
        for ln in js.get("links", []):
            if ln.get("rel") == "next" and js.get("features"):
                nxt = (ln["href"], ln.get("body") or payload)
                break
    CACHE.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(feats))
    return feats


def match_points(items: list[dict], pts: list[tuple[float, float, pd.Timestamp]], half_h: float = 24.0):
    """Для каждой точки (lon, lat, t): снимки S2, чей след содержит точку и |dt| <= half_h."""
    geoms = [(it, shape(it["geometry"]), pd.Timestamp(it["datetime"])) for it in items if it.get("geometry")]
    res = []
    for lon, lat, t in pts:
        p = Point(lon, lat)
        hits = []
        for it, g, ti in geoms:
            dt = (ti - t).total_seconds() / 3600
            if abs(dt) <= half_h and g.contains(p):
                hits.append((abs(dt), dt, it["cloud"], it["id"]))
        hits.sort()
        if hits:
            # лучший — ближайший по времени; отдельно — лучший по облачности (<= 30 %)
            clear = [h for h in hits if h[2] is not None and h[2] <= 30]
            b = hits[0]
            c = clear[0] if clear else None
            res.append(dict(s2_n=len(hits), s2_best_dt_h=round(b[1], 2), s2_best_cloud=b[2], s2_best_item=b[3],
                            s2_clear_n=len(clear), s2_clear_dt_h=round(c[1], 2) if c else None,
                            s2_clear_cloud=c[2] if c else None, s2_clear_item=c[3] if c else None))
        else:
            res.append(dict(s2_n=0))
    return res


# ---------------------------------------------------------------- Lebreton 2018 aerial
def lebreton_aerial() -> pd.DataFrame:
    d = RAW / "csv"
    st = pd.read_csv(d / "Lebreton2018_SamplingInformation-StationInfo.csv", decimal=",")
    st = st[st["Sampling type"] == "RGB mosaic"].copy()
    m = pd.read_csv(d / "Lebreton2018_SamplingInformation-MosaicDebrisInfo.csv", decimal=",")
    img = m["Image ID (at The Ocean Cleanup database)"].astype(str)
    m["flight"] = img.str.extract(r"C01_(\d{6})_")[0]
    m["frame"] = img.str.extract(r"_(\d{5})G\d")[0].astype(float)
    # время объекта: линейно по номеру мозаики между началом и концом съёмки (ровная скорость 140 уз)
    tt = []
    for fl, g in m.groupby("flight"):
        t0, t1 = (pd.Timestamp(x) for x in FLIGHTS[fl])
        f0, f1 = g.frame.min(), g.frame.max()
        tt.append((t0 + (g.frame - f0) / (f1 - f0) * (t1 - t0)).rename("t_est"))
    m["t_est"] = pd.concat(tt)
    rows = []
    for _, s in st.iterrows():
        ev = s["Sampling event ID"]
        g = m[m["Sampling event ID"] == ev]
        day = pd.Timestamp(int(s["Start year (UTC)"]), int(s["Start month (UTC)"]), int(s["Start day (UTC)"]), tz="UTC")
        fl = day.strftime("%y%m%d")
        if len(g):
            ts, te = g.t_est.min(), g.t_est.max()
            lat, lon = g["Start latitude debris (degrees)"], g["Start longitude debris (degrees)"]
            cats = g["Object type"].value_counts().to_dict()
            sizes = {"0.2-0.5m": int((g["Length (m)"] < 0.5).sum()), "0.5-1m": int(g["Length (m)"].between(0.5, 1, "left").sum()),
                     "1-3m": int(g["Length (m)"].between(1, 3, "left").sum()), ">=3m": int((g["Length (m)"] >= 3).sum())}
            ge50 = int((g["< 50 cm?"].astype(str).str.lower() == "no").sum())
        else:
            t0, t1 = (pd.Timestamp(x) for x in FLIGHTS[fl])
            ts, te, cats, sizes, ge50 = t0, t1, {}, {}, 0
            lat = lon = pd.Series(dtype=float)
        area = float(s["Sampling area (km2)"])
        n = len(g)
        rows.append(dict(
            candidate_id=f"LEB16_AER_{int(ev)}", source="Lebreton2018_aerial_RGB_mosaic", dataset_doi=LEB_DOI, license="CC BY 4.0",
            source_event_id=f"S1:{int(ev)} (BigBin{int(ev) - 446})", platform="Aircraft C-130 Hercules, RGB CS-4800i ~0.1 m",
            image_type="aerial_RGB (не Sentinel-2)",
            datetime_start_utc=ts.isoformat(), datetime_end_utc=te.isoformat(), time_uncertainty_min=10,
            time_note="по номеру мозаики между началом/концом съёмки из Methods (18:56–21:14 и 22:14–00:37 UTC)",
            lat=float(s["Start latitude (degrees)"]), lon=float(s["Start longitude (degrees)"]),
            lat_min=float(lat.min()) if n else None, lat_max=float(lat.max()) if n else None,
            lon_min=float(lon.min()) if n else None, lon_max=float(lon.max()) if n else None,
            track_or_box="центр мозаики; объекты в [lat_min..lat_max]×[lon_min..lon_max]",
            survey_width_m=360, survey_area_km2=round(area, 3), n_items=n, n_items_ge50cm=ge50,
            density_items_km2=round(n / area, 2) if area else None,
            density_ge50cm_km2=round(ge50 / area, 2) if area else None,
            counts_by_category=json.dumps(cats, ensure_ascii=False), counts_by_size=json.dumps(sizes),
            max_object_len_m=float(g["Length (m)"].max()) if n else None,
            total_topview_m2=round(float(g["Topview area (m^2)"].sum()), 2) if n else 0.0,
            object_coords="да, каждый предмет: начало/конец (MosaicDebrisInfo)",
            in_org_csv="да (S1 aerial_GT50, но без времени и без объектов)",
        ))
    return pd.DataFrame(rows), m


# ---------------------------------------------------------------- ADIS
def adis_segments() -> pd.DataFrame:
    s = pd.read_csv(RAW / "adis" / "Segments.csv")
    s["t"] = pd.to_datetime(s.timestamp, format="mixed").dt.tz_localize("UTC")  # GPS time (≈UTC+18 с)
    s["t0"] = pd.to_datetime(s.mindate, format="mixed").dt.tz_localize("UTC")
    s["t1"] = pd.to_datetime(s.maxdate, format="mixed").dt.tz_localize("UTC")
    s["Ship"] = s.Ship.replace({"Maestk Tender": "Maersk Tender"})
    s["date"] = s.t.dt.strftime("%Y-%m-%d")
    return s


def adis_match(s: pd.DataFrame, workers: int = 8) -> pd.DataFrame:
    groups = list(s[s.t >= S2_START].groupby(["Ship", "date"]))

    def job(kg):
        (ship, date), g = kg
        pad = 0.2
        bbox = [max(g.Longitude.min() - pad, -180), max(g.Latitude.min() - pad, -90),
                min(g.Longitude.max() + pad, 180), min(g.Latitude.max() + pad, 90)]
        if bbox[2] - bbox[0] > 180:  # через линию перемены дат — пропуск (в данных нет)
            return g.index, [dict(s2_n=-1)] * len(g), "dateline"
        d0 = (pd.Timestamp(date) - pd.Timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
        d1 = (pd.Timestamp(date) + pd.Timedelta(days=2)).strftime("%Y-%m-%dT00:00:00Z")
        try:
            items = stac_search(bbox, d0, d1)
        except Exception as e:  # noqa: BLE001
            return g.index, [dict(s2_n=-1)] * len(g), f"error:{e}"
        pts = list(zip(g.Longitude, g.Latitude, g.t))
        return g.index, match_points(items, pts), f"ok:{len(items)}"

    out, log = {}, []
    with ThreadPoolExecutor(workers) as ex:
        for i, (idx, res, st) in enumerate(ex.map(job, groups)):
            for j, r in zip(idx, res):
                out[j] = r
            log.append(st)
            if (i + 1) % 100 == 0:
                print(f"  ADIS STAC {i + 1}/{len(groups)}", file=sys.stderr, flush=True)
    r = pd.DataFrame.from_dict(out, orient="index")
    errs = sum(1 for x in log if not x.startswith("ok"))
    print(f"ADIS: {len(groups)} групп судно×день, ошибок {errs}", file=sys.stderr)
    return s.join(r), dict(groups=len(groups), errors=errs,
                          items_total=sum(int(x.split(':')[1]) for x in log if x.startswith("ok")))


def adis_rows(sm: pd.DataFrame, obj: pd.DataFrame) -> pd.DataFrame:
    obj = obj.copy()
    names = {0: "animal", 1: "buoy", 2: "fibrous", 3: "hard_plastic", 4: "plant"}
    obj["cls"] = obj["class"].map(names)
    per = obj.groupby("SegmentID")
    cats = per.cls.value_counts().unstack(fill_value=0)
    maxlen = per.amaj.max()
    toparea = per.area.sum()
    sel = sm[sm.s2_n.fillna(0) > 0]
    rows = []
    for _, s in sel.iterrows():
        sid = int(s.SegmentID)
        c = cats.loc[sid].to_dict() if sid in cats.index else {}
        c = {k: int(v) for k, v in c.items() if v}
        rows.append(dict(
            candidate_id=f"ADIS_SEG_{sid}", source="ADIS_ship_GoPro_YOLOv5", dataset_doi=ADIS_DOI, license="CC BY 4.0",
            source_event_id=f"SegmentID {sid}", platform=f"{s.Ship}, GoPro {s.Side}, h={s.Camheight} м",
            image_type="ship_camera_oblique (не Sentinel-2)",
            datetime_start_utc=s.t0.isoformat(), datetime_end_utc=s.t1.isoformat(), time_uncertainty_min=0,
            time_note="GPS time (mindate/maxdate отрезка)",
            lat=round(float(s.Latitude), 5), lon=round(float(s.Longitude), 5),
            lat_min=None, lat_max=None, lon_min=None, lon_max=None,
            track_or_box=f"центроид отрезка {s.distance / 1000:.1f} км, курс {s.avgheading:.0f}°, {s.avgspeed:.1f} уз",
            survey_width_m=round(float(s.Scan_width), 1), survey_area_km2=round(float(s.area_scanned_km2), 4),
            n_items=int(s["n_objects>5cm"]), n_items_ge50cm=int(s["n_objects>50cm"]),
            density_items_km2=round(float(s.dhat_5cm), 2), density_ge50cm_km2=round(float(s.dhat_50cm), 2),
            counts_by_category=json.dumps(c), counts_by_size=json.dumps({">5cm": int(s["n_objects>5cm"]), ">10cm": int(s["n_objects>10cm"]), ">50cm": int(s["n_objects>50cm"])}),
            max_object_len_m=round(float(maxlen.get(sid, np.nan)), 2) if sid in maxlen.index else None,
            total_topview_m2=round(float(toparea.get(sid, 0.0)), 2),
            object_coords="да, каждый предмет: координаты камеры + время (Objects.csv)",
            in_org_csv="нет",
            s2_n=int(s.s2_n), s2_best_dt_h=s.s2_best_dt_h, s2_best_cloud=s.s2_best_cloud, s2_best_item=s.s2_best_item,
            s2_clear_n=s.get("s2_clear_n"), s2_clear_dt_h=s.get("s2_clear_dt_h"), s2_clear_cloud=s.get("s2_clear_cloud"),
            s2_clear_item=s.get("s2_clear_item"),
        ))
    return pd.DataFrame(rows)


def level_and_note(r) -> tuple[str, str]:
    """Уровень §11: A требует снимок + поле по времени/месту/площади/категории. Здесь только C или D."""
    n = r.get("s2_n")
    if n is None or pd.isna(n) or n <= 0:
        return "D", "нет Sentinel-2 L2A в ±1 сут над точкой (открытый океан вне плана съёмки S2) — пара невозможна"
    dt = abs(r.get("s2_clear_dt_h") if pd.notna(r.get("s2_clear_dt_h")) else r.get("s2_best_dt_h"))
    clear = pd.notna(r.get("s2_clear_item"))
    big = (r.get("max_object_len_m") or 0) >= 10 or (r.get("total_topview_m2") or 0) >= 100
    note = f"S2 в {dt:.1f} ч, {'есть' if clear else 'нет'} снимка с облачностью ≤30 %; "
    note += "предметы крупнее пикселя 10 м есть" if big else "все предметы меньше пикселя S2 (10 м) — ожидаем невидимость, годится как поле-число/отрицательная проверка"
    return "C", note


def main():
    global OFFLINE
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    OFFLINE = a.offline

    leb, mobj = lebreton_aerial()
    pts = [(r.lon, r.lat, pd.Timestamp(r.datetime_start_utc)) for r in leb.itertuples()]
    by_day = {}
    for i, r in leb.iterrows():
        by_day.setdefault(r.datetime_start_utc[:10], []).append(i)
    for day, idx in by_day.items():
        sub = leb.loc[idx]
        bbox = [sub.lon.min() - 0.3, sub.lat.min() - 0.3, sub.lon.max() + 0.3, sub.lat.max() + 0.3]
        d0 = (pd.Timestamp(day) - pd.Timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
        d1 = (pd.Timestamp(day) + pd.Timedelta(days=2)).strftime("%Y-%m-%dT00:00:00Z")
        items = stac_search(bbox, d0, d1)
        res = match_points(items, [pts[i] for i in idx])
        for i, rr in zip(idx, res):
            for k, v in rr.items():
                leb.loc[i, k] = v
        print(f"Lebreton {day}: S2 items in bbox ±1d = {len(items)}", file=sys.stderr)

    s = adis_segments()
    sm, st = adis_match(s)
    sm.drop(columns=["t", "t0", "t1"]).to_csv(RAW / "adis_s2_match.csv", index=False)
    obj = pd.read_csv(RAW / "adis" / "Objects.csv")
    ad = adis_rows(sm, obj)

    reg = pd.concat([leb, ad], ignore_index=True)
    lv = reg.apply(lambda r: level_and_note(r.to_dict()), axis=1)
    reg["evidence_level"] = [x[0] for x in lv]
    reg["note"] = [x[1] for x in lv]
    # приоритет розыска: ненулевой счёт + чистый снимок ближе по времени
    reg["priority"] = 0
    m = reg.evidence_level == "C"
    dtc = reg.s2_clear_dt_h.abs().where(reg.s2_clear_dt_h.notna(), 99)
    reg.loc[m & (reg.n_items > 0) & (dtc <= 3), "priority"] = 1
    reg.loc[m & (reg.n_items > 0) & (dtc > 3) & (dtc <= 24), "priority"] = 2
    reg.loc[m & (reg.n_items == 0) & (dtc <= 3), "priority"] = 3
    reg.loc[m & (reg.priority == 0), "priority"] = 4
    reg = reg.sort_values(["priority", "source", "datetime_start_utc"])
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    reg.to_csv(OUT_CSV, index=False, encoding="utf-8")

    # сводка для отчёта
    s2 = sm[sm.t >= S2_START]
    summ = dict(
        lebreton=dict(bins=len(leb), objects=int(len(mobj)), s2_bins_pm1d=int((leb.s2_n.fillna(0) > 0).sum()),
                      area_km2=round(float(leb.survey_area_km2.sum()), 2),
                      max_len_m=float(mobj["Length (m)"].max()), objects_ge3m=int((mobj["Length (m)"] >= 3).sum()),
                      max_bin_topview_m2=float(leb.total_topview_m2.max()),
                      max_bin_cover_frac=float((leb.total_topview_m2 / (leb.survey_area_km2 * 1e6)).max())),
        adis=dict(segments=len(s), segments_s2_era=len(s2), area_km2=round(float(s.area_scanned_km2.sum()), 1),
                  objects=int(len(obj)), n50=int(s["n_objects>50cm"].sum()), stac=st,
                  seg_with_s2_pm1d=int((s2.s2_n > 0).sum()), seg_with_clear_s2_pm1d=int(s2.s2_clear_n.fillna(0).gt(0).sum()),
                  seg_nonzero_with_s2=int(((s2.s2_n > 0) & (s2["n_objects>5cm"] > 0)).sum()),
                  seg_nonzero_clear_le3h=int(((s2.s2_clear_dt_h.abs() <= 3) & (s2["n_objects>5cm"] > 0)).sum()),
                  seg_any_clear_le3h=int((s2.s2_clear_dt_h.abs() <= 3).sum()),
                  obj_ge10m=int((obj.amaj >= 10).sum()), obj_ge5m=int((obj.amaj >= 5).sum())),
        registry=dict(rows=len(reg), by_level=reg.evidence_level.value_counts().to_dict(),
                      by_priority=reg.priority.value_counts().sort_index().to_dict()),
    )
    (RAW / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(summ, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
