"""Геометрия трансект S2/S3 по первоисточникам PANGAEA (задача L72; критерии Т1, О1).

Первоисточники (скачиваются scripts/case/fetch_pangaea.py в data/case/pangaea/, работа офлайн):
  S2  PANGAEA.931834 — xlsx «Transect meta data» (Gutow et al. 2021, CC BY 4.0): по строке на сегмент, у прерванных
      трансект T18/T22/T35/T49 — два сегмента («… interrupted», «continued») и строка «Summary of complete Transect»
      с авторской площадью; дата — серийный номер Excel, время — доля суток (UTC).
  S2  PANGAEA.931833 — позиции наблюдателя в момент регистрации предмета (без времени) — проверка «предметы на пути».
  S3  PANGAEA.890782 — начало/конец/время каждой трансекты (Gutow et al. 2018, CC BY 3.0).
  S3  PANGAEA.890781 — строки Start/End и позиции предметов; нужны только для HE460_MarLitter_transect01, у которой в
      890782 (и в CSV организаторов) начало == конец.

Выход build(): таблица сегментов (одна строка на сегмент) и таблица проверок (одна строка на событие).
write_outputs() пишет data/case/geometry/transects.csv, transects.geojson, checks.csv.

Функции для подключения (не меняют поведение find_pairs/pair_quality/service, пока их не вызовут):
  event_track(event_id)  -> dict: сегменты, MultiLineString, центр пути (половина пройденной длины), время середины
                            усилия, окно [начало первого сегмента, конец последнего], площадь автора.
  events_override(ev_df) -> копия таблицы событий find_pairs.build_events с lat/lon/obs_datetime из геометрии.

Эталон площади для концентрации — опубликованная площадь автора (README данных); длина сегментов её не заменяет.
"""
from __future__ import annotations

import io
import json
import math
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
PANGAEA_DIR = ROOT / "data" / "case" / "pangaea"
OUT_DIR = ROOT / "data" / "case" / "geometry"
SAMPLES = ROOT / "task" / "macroplastic_marine_samples.csv"

S2_XLSX = "PANGAEA_931834.xlsx"
S2_OBJ = "PANGAEA_931833.tab"
S3_TR = "PANGAEA_890782.tab"
S3_OBJ = "PANGAEA_890781.tab"
INTERRUPTED_S2 = ("S2:MSM41_litter-T18", "S2:MSM41_litter-T22", "S2:MSM41_litter-T35", "S2:MSM41_litter-T49")
STRIP_WIDTH_M = 10.0
EARTH_R_KM = 6371.0088
ITEM_OFF_TRACK_KM = 0.5   # предмет дальше этого от линии сегментов считается «вне пути»

COLUMNS = ["event_id", "source_id", "segment", "n_segments", "lon_start", "lat_start", "lon_end", "lat_end",
           "time_start_utc", "time_end_utc", "duration_min", "length_km", "length_km_geodesic", "width_m",
           "area_segment_km2", "area_author_km2", "geometry_status", "source", "note"]


# ---------------------------------------------------------------- geo helpers
def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """a, b = (lon, lat)."""
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_R_KM * math.asin(min(1.0, math.sqrt(h)))


def destination(p: tuple[float, float], bearing_deg: float, dist_km: float) -> tuple[float, float]:
    lon1, lat1 = map(math.radians, p)
    br, d = math.radians(bearing_deg), dist_km / EARTH_R_KM
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(br))
    lon2 = lon1 + math.atan2(math.sin(br) * math.sin(d) * math.cos(lat1), math.cos(d) - math.sin(lat1) * math.sin(lat2))
    return (math.degrees(lon2), math.degrees(lat2))


def bearing_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def point_segment_km(p, a, b) -> float:
    """Расстояние точки p до отрезка ab (локальная равнопромежуточная проекция; отрезки ≤ 30 км)."""
    lat0 = math.radians((a[1] + b[1] + p[1]) / 3)
    kx, ky = EARTH_R_KM * math.cos(lat0) * math.pi / 180, EARTH_R_KM * math.pi / 180
    ax, ay, bx, by, px, py = a[0] * kx, a[1] * ky, b[0] * kx, b[1] * ky, p[0] * kx, p[1] * ky
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


# ---------------------------------------------------------------- readers
def read_xlsx_rows(path: Path) -> list[dict]:
    """Первый лист xlsx как список {колонка_буква: значение} (без openpyxl: zip + XML)."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    z = zipfile.ZipFile(path)
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
            ss.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
    rows = []
    for row in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).iter(f"{{{ns['m']}}}row"):
        d = {"_row": int(row.get("r"))}
        for c in row.findall("m:c", ns):
            v = c.find("m:v", ns)
            val = None if v is None else v.text
            if c.get("t") == "s" and val is not None:
                val = ss[int(val)]
            elif c.get("t") == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{ns['m']}}}t"))
            col = re.match(r"[A-Z]+", c.get("r")).group(0)
            d[col] = val
        rows.append(d)
    return rows


def read_pangaea_tab(path: Path) -> pd.DataFrame:
    """Таблица данных PANGAEA tab (после строки '*/')."""
    txt = path.read_text(encoding="utf-8")
    i = txt.index("*/\n") + 3 if "*/\n" in txt else txt.index("*/\r\n") + 4
    return pd.read_csv(io.StringIO(txt[i:]), sep="\t", dtype=str, keep_default_na=False)


def _f(v) -> float | None:
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def _excel_dt(day_serial: float, frac: float) -> datetime:
    t = datetime(1899, 12, 30, tzinfo=timezone.utc) + timedelta(days=float(day_serial))
    minutes = round(float(frac) * 24 * 60)  # в таблице минутное разрешение
    return t + timedelta(minutes=minutes)


def _iso(t: datetime | None) -> str | None:
    return None if t is None else t.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- S2
def s2_segments(pdir: Path = PANGAEA_DIR) -> tuple[pd.DataFrame, dict]:
    """Сегменты всех трансект MSM41 из 931834. Возвращает (сегменты, {event_id: {'author_area', 'comments'}})."""
    rows = read_xlsx_rows(pdir / S2_XLSX)
    segs, meta, last_day = [], {}, None
    for r in rows[1:]:
        tno = r.get("A")
        if tno is None:
            continue
        eid = f"S2:MSM41_litter-T{int(float(tno))}"
        m = meta.setdefault(eid, {"author_area": None, "comments": [], "rows": []})
        comment = (r.get("Z") or "").strip()
        mref = re.search(r"[Tt]ransect\s+(\d+)", comment)
        if mref and int(mref.group(1)) != int(float(tno)):
            comment = f"source comment refers to T{mref.group(1)}, not T{int(float(tno))} (misplaced in 931834): {comment}"
        if comment:
            m["comments"].append(f"row {r['_row']}: {comment}")
        day = r.get("B")
        if day not in (None, "continued"):
            last_day = float(day)
        lat0, lon0, lat1, lon1 = (_f(r.get(k)) for k in ("F", "G", "H", "I"))
        area = _f(r.get("K"))
        if None in (lat0, lon0, lat1, lon1):
            if comment.lower().startswith("summary") and area is not None:
                m["author_area"] = area            # итог прерванной трансекты (округлён автором)
                m["summary_row"] = r["_row"]
            continue
        c0, c1 = _f(r.get("C")), _f(r.get("D"))
        t0 = _excel_dt(last_day, c0) if c0 is not None else None
        t1 = _excel_dt(last_day, c1) if c1 is not None else None
        if t0 and t1 and t1 < t0:
            t1 += timedelta(days=1)
        m["rows"].append(r["_row"])
        segs.append(dict(event_id=eid, source_id="S2_SARGASSO_MSM41", lon_start=lon0, lat_start=lat0,
                         lon_end=lon1, lat_end=lat1, t0=t0, t1=t1,
                         duration_min=_f(r.get("E")), length_km=_f(r.get("J")), width_m=STRIP_WIDTH_M,
                         area_segment_km2=area,
                         source=f"PANGAEA.931834 'Transect meta data'!row {r['_row']}",
                         note=("continued" if day == "continued" else "") + (f"; {comment}" if comment else "")))
    df = pd.DataFrame(segs)
    for eid, m in meta.items():
        if m["author_area"] is None and len(m["rows"]) == 1:
            m["author_area"] = float(df.loc[df.event_id == eid, "area_segment_km2"].iloc[0])
    return df, meta


# ---------------------------------------------------------------- S3
def s3_segments(pdir: Path = PANGAEA_DIR) -> pd.DataFrame:
    tr = read_pangaea_tab(pdir / S3_TR)
    obj = read_pangaea_tab(pdir / S3_OBJ)
    out = []
    for i, r in tr.iterrows():
        eid = "S3:" + r["Event"]
        a = (float(r["Longitude"]), float(r["Latitude"]))
        b = (float(r["Longitude 2"]), float(r["Latitude 2"]))
        t0 = datetime.fromisoformat(r["Date/Time"]).replace(tzinfo=timezone.utc)
        t1 = datetime.fromisoformat(r["Date/Time 2"]).replace(tzinfo=timezone.utc) if r["Date/Time 2"] else None
        if t1 is not None and t1 < t0:
            t1 += timedelta(days=1)
        L = float(r["Transect l [m]"]) / 1000
        w = float(r["Transect w [m]"])
        row = dict(event_id=eid, source_id="S3_SE_NORTH_SEA", lon_start=a[0], lat_start=a[1], lon_end=b[0],
                   lat_end=b[1], t0=t0, t1=t1, duration_min=(t1 - t0).total_seconds() / 60 if t1 else None, length_km=L,
                   width_m=w, area_segment_km2=float(r["Area [km**2] (Transect)"]),
                   source=f"PANGAEA.890782 data row {i + 1}", note="", geometry_status="single")
        if a == b:
            row.update(_s3_reconstruct(r["Event"], a, L, obj))
        out.append(row)
    return pd.DataFrame(out)


def _s3_reconstruct(event: str, start: tuple[float, float], length_km: float, obj: pd.DataFrame) -> dict:
    """Начало == конец в 890782. Предметы в 890781 записаны в позиции судна → направление пути известно:
    конец = начало + length_km по азимуту на самый дальний предмет (приближение прямым галсом)."""
    o = obj[(obj["Event"] == event) & (obj["Comment (Date/Time)"] == "")]
    pts = [(float(x), float(y)) for x, y in zip(o["Longitude"], o["Latitude"])]
    if not pts:
        return dict(geometry_status="point_degenerate", lon_end=start[0], lat_end=start[1],
                    note="start == end in PANGAEA.890782; no object positions to recover direction -> point")
    far = max(pts, key=lambda p: haversine_km(start, p))
    brg = bearing_deg(start, far)
    end = destination(start, brg, length_km)
    offs = [point_segment_km(p, start, end) for p in pts]
    return dict(geometry_status="reconstructed_approx", lon_end=round(end[0], 5), lat_end=round(end[1], 5),
                source=f"PANGAEA.890782 (start, length) + PANGAEA.890781 ({len(pts)} object positions, direction)",
                note=(f"start == end in PANGAEA.890782 (and in organisers' CSV); end = start + {length_km:.3f} km at "
                      f"bearing {brg:.1f} deg towards farthest object ({haversine_km(start, far):.2f} km from start); "
                      f"objects max {max(offs):.3f} km off the reconstructed line; straight-course assumption"))


# ---------------------------------------------------------------- build + checks
def _finalize(df: pd.DataFrame, author: dict[str, float]) -> pd.DataFrame:
    df = df.assign(_k0=df.event_id.map(lambda e: _eid_key(e)[0]), _k1=df.event_id.map(lambda e: _eid_key(e)[1]))
    df = df.sort_values(["_k0", "_k1", "t0"]).drop(columns=["_k0", "_k1"])
    df["segment"] = df.groupby("event_id").cumcount() + 1
    df["n_segments"] = df.groupby("event_id")["segment"].transform("max")
    df["length_km_geodesic"] = [round(haversine_km((a, b), (c, d)), 4) for a, b, c, d in
                                zip(df.lon_start, df.lat_start, df.lon_end, df.lat_end)]
    df["time_start_utc"] = df.t0.map(_iso)
    df["time_end_utc"] = df.t1.map(_iso)
    df["area_author_km2"] = df.event_id.map(author)
    if "geometry_status" not in df:
        df["geometry_status"] = None
    df["geometry_status"] = df["geometry_status"].fillna("")
    df.loc[(df.geometry_status == "") & (df.n_segments > 1), "geometry_status"] = "segment"
    df.loc[df.geometry_status == "", "geometry_status"] = "single"
    df["note"] = df["note"].fillna("").str.strip("; ")
    return df[COLUMNS].reset_index(drop=True)


def _eid_key(e: str):
    m = re.search(r"(\d+)$", e)
    return (e[: m.start()] if m else e, int(m.group(1)) if m else 0)


@lru_cache(maxsize=2)
def _build_cached(pdir: str, samples: str):
    return _build(Path(pdir), Path(samples))


def build(pdir: Path = PANGAEA_DIR, samples: Path = SAMPLES) -> tuple[pd.DataFrame, pd.DataFrame]:
    seg, chk = _build_cached(str(pdir), str(samples))
    return seg.copy(), chk.copy()


def _build(pdir: Path, samples: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    csv = pd.read_csv(samples)
    csv = csv[csv.source_id.isin(["S2_SARGASSO_MSM41", "S3_SE_NORTH_SEA"])]
    ev = csv.sort_values("record_type", ascending=False).drop_duplicates("event_id").set_index("event_id")
    s2, meta = s2_segments(pdir)
    s3 = s3_segments(pdir)
    author = {e: m["author_area"] for e, m in meta.items()}
    author.update(dict(zip(s3.event_id, s3.area_segment_km2)))   # S3: опубликованная площадь 890782
    all_seg = pd.concat([s2, s3], ignore_index=True)
    all_seg = all_seg[all_seg.event_id.isin(ev.index)]
    seg = _finalize(all_seg, author)
    for eid, m in meta.items():
        if eid in ev.index and len(m["comments"]):
            extra = " | ".join(c for c in m["comments"] if "Summary" in c or "interrupted" in c.lower()
                               or "continu" in c.lower())
            if extra and m.get("summary_row"):
                seg.loc[seg.event_id == eid, "source"] += f"; summary row {m['summary_row']}"
    chk = checks(seg, ev, pdir)
    return seg, chk


def _s2_items(pdir: Path) -> pd.DataFrame:
    o = read_pangaea_tab(pdir / S2_OBJ)
    o = o.assign(event_id="S2:" + o["Event"], lon=pd.to_numeric(o["Longitude"], errors="coerce"),
                 lat=pd.to_numeric(o["Latitude"], errors="coerce"))
    return o[["event_id", "lon", "lat"]].dropna()


def _s3_items(pdir: Path) -> pd.DataFrame:
    o = read_pangaea_tab(pdir / S3_OBJ)
    o = o[o["Comment (Date/Time)"] == ""]
    o = o.assign(event_id="S3:" + o["Event"], lon=pd.to_numeric(o["Longitude"], errors="coerce"),
                 lat=pd.to_numeric(o["Latitude"], errors="coerce"))
    return o[["event_id", "lon", "lat"]].dropna()


def checks(seg: pd.DataFrame, ev: pd.DataFrame, pdir: Path = PANGAEA_DIR) -> pd.DataFrame:
    items = pd.concat([_s2_items(pdir), _s3_items(pdir)], ignore_index=True)
    rows = []
    for eid, g in seg.groupby("event_id", sort=False):
        e = ev.loc[eid]
        L = float(g.length_km.sum())
        seg_area = L * float(g.width_m.iloc[0]) / 1000
        author_area = float(g.area_author_km2.iloc[0])
        csv_area = _f(e.sampled_area_km2)
        # время CSV
        day = pd.Timestamp(e.date_utc, tz="UTC")
        c0 = day + pd.Timedelta(e.time_start_utc)
        c1 = day + pd.Timedelta(e.time_end_utc)
        if c1 < c0:
            c1 += pd.Timedelta(days=1)
        t0 = pd.Timestamp(g.time_start_utc.iloc[0])
        t1 = pd.Timestamp(g.time_end_utc.iloc[-1])
        inside = bool(all(c0 <= pd.Timestamp(a) <= pd.Timestamp(b) <= c1
                          for a, b in zip(g.time_start_utc, g.time_end_utc)))
        first, last = g.iloc[0], g.iloc[-1]
        start_match = haversine_km((first.lon_start, first.lat_start), (e.lon_start, e.lat_start))
        end_match = haversine_km((last.lon_end, last.lat_end), (e.lon_end, e.lat_end))
        it = items[items.event_id == eid]
        lines = [((r.lon_start, r.lat_start), (r.lon_end, r.lat_end)) for r in g.itertuples()]
        d = [min(point_segment_km((x, y), a, b) for a, b in lines) for x, y in zip(it.lon, it.lat)]
        gap_km = sum(haversine_km((g.lon_end.iloc[i], g.lat_end.iloc[i]), (g.lon_start.iloc[i + 1], g.lat_start.iloc[i + 1]))
                     for i in range(len(g) - 1))
        tr = track(g)
        rows.append(dict(
            event_id=eid, source_id=first.source_id, n_segments=len(g),
            geometry_status=";".join(sorted(set(g.geometry_status))),
            sum_length_km=round(L, 4), sum_length_km_geodesic=round(float(g.length_km_geodesic.sum()), 4),
            csv_transect_length_km=_f(e.transect_length_km),
            area_from_segments_km2=round(seg_area, 6), area_author_km2=author_area, csv_sampled_area_km2=csv_area,
            rel_diff_segments_vs_author=round((seg_area - author_area) / author_area, 4),
            csv_equals_author_area=bool(csv_area is not None and abs(csv_area - author_area) < 5e-4),
            time_start_utc=_iso(t0.to_pydatetime()), time_end_utc=_iso(t1.to_pydatetime()),
            csv_time_start=_iso(c0.to_pydatetime()), csv_time_end=_iso(c1.to_pydatetime()),
            effort_min=round(float(g.duration_min.sum()), 1), span_min=round((t1 - t0).total_seconds() / 60, 1),
            segments_inside_csv_interval=inside,
            start_vs_csv_km=round(start_match, 4), end_vs_csv_km=round(end_match, 4), gap_between_segments_km=round(gap_km, 3),
            csv_lon=_f(e.longitude), csv_lat=_f(e.latitude), csv_position_role=e.position_role,
            track_center_lon=round(tr["center"][0], 5), track_center_lat=round(tr["center"][1], 5),
            center_shift_km=round(haversine_km(tr["center"], (float(e.longitude), float(e.latitude))), 3),
            effort_mid_utc=tr["effort_mid_utc"],
            csv_interval_mid_utc=_iso((c0 + (c1 - c0) / 2).to_pydatetime()),
            n_items_source=len(it), max_item_offtrack_km=round(max(d), 3) if d else None,
            n_items_offtrack=int(sum(x > ITEM_OFF_TRACK_KM for x in d)),
        ))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- track helpers
def track(g: pd.DataFrame) -> dict:
    """Центр пути (точка на половине суммарной длины сегментов; разрывы не считаются) и середина усилия по времени."""
    g = g.sort_values("segment")
    segs = [((r.lon_start, r.lat_start), (r.lon_end, r.lat_end), pd.Timestamp(r.time_start_utc),
             pd.Timestamp(r.time_end_utc)) for r in g.itertuples()]
    lens = [haversine_km(a, b) for a, b, _, _ in segs]
    total = sum(lens)
    if total == 0:
        center = segs[0][0]
    else:
        half, acc = total / 2, 0.0
        for (a, b, _, _), L in zip(segs, lens):
            if acc + L >= half:
                f = (half - acc) / L if L else 0
                center = (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]))
                break
            acc += L
    durs = [(t1 - t0).total_seconds() for _, _, t0, t1 in segs]
    half_t, acc, mid = sum(durs) / 2, 0.0, segs[0][2]
    for (_, _, t0, t1), d in zip(segs, durs):
        if acc + d >= half_t:
            mid = t0 + pd.Timedelta(seconds=half_t - acc)
            break
        acc += d
    return {"center": center, "effort_mid_utc": _iso(mid.to_pydatetime()),
            "window": (_iso(segs[0][2].to_pydatetime()), _iso(segs[-1][3].to_pydatetime())),
            "segment_windows": [(_iso(t0.to_pydatetime()), _iso(t1.to_pydatetime())) for _, _, t0, t1 in segs],
            "lines": [[list(a), list(b)] for a, b, _, _ in segs]}


def event_track(event_id: str, allow_approx: bool = True, pdir: Path = PANGAEA_DIR,
                samples: Path = SAMPLES) -> dict | None:
    """Геометрия события для find_pairs / API. None — событие не S2/S3 или нет в первоисточнике.
    allow_approx=False: реконструированный конец (HE460_MarLitter_transect01) не используется -> Point."""
    seg, _ = build(pdir, samples)
    g = seg[seg.event_id == event_id]
    if g.empty:
        return None
    t = track(g)
    status = ";".join(sorted(set(g.geometry_status)))
    if "point_degenerate" in status or ("reconstructed_approx" in status and not allow_approx):
        geom = {"type": "Point", "coordinates": [float(g.lon_start.iloc[0]), float(g.lat_start.iloc[0])]}
        t["center"] = tuple(geom["coordinates"])
    elif len(g) == 1:
        geom = {"type": "LineString", "coordinates": t["lines"][0]}
    else:
        geom = {"type": "MultiLineString", "coordinates": t["lines"]}
    return {"event_id": event_id, "geometry": geom, "geometry_status": status, "n_segments": int(len(g)),
            "center": t["center"], "effort_mid_utc": t["effort_mid_utc"], "window": t["window"],
            "segment_windows": t["segment_windows"], "area_author_km2": float(g.area_author_km2.iloc[0]),
            "length_km": float(g.length_km.sum()), "source": "; ".join(g.source), "note": " | ".join(x for x in g.note if x)}


def events_override(ev: pd.DataFrame, allow_approx: bool = True) -> pd.DataFrame:
    """Для таблицы find_pairs.build_events: lat/lon := центр пути, obs_datetime := середина усилия по сегментам;
    добавляет geom_window_start/end и point_from='geometry_track_center'. Прочие события без изменений."""
    ev = ev.copy()
    for c in ("geom_window_start", "geom_window_end", "geometry_status"):
        if c not in ev:
            ev[c] = None
    for i, r in ev.iterrows():
        t = event_track(r.event_id, allow_approx=allow_approx)
        if t is None:
            continue
        ev.at[i, "lon"], ev.at[i, "lat"] = round(t["center"][0], 6), round(t["center"][1], 6)
        ev.at[i, "obs_datetime"] = pd.Timestamp(t["effort_mid_utc"])
        ev.at[i, "time_note"] = "effort_mid_segments"
        ev.at[i, "point_from"] = "geometry_track_center"
        ev.at[i, "geom_window_start"], ev.at[i, "geom_window_end"] = t["window"]
        ev.at[i, "geometry_status"] = t["geometry_status"]
    return ev


# ---------------------------------------------------------------- output
def to_geojson(seg: pd.DataFrame, chk: pd.DataFrame) -> dict:
    feats = []
    c = chk.set_index("event_id")
    for eid, g in seg.groupby("event_id", sort=False):
        tr = track(g)
        st = ";".join(sorted(set(g.geometry_status)))
        if "point_degenerate" in st:
            geom = {"type": "Point", "coordinates": [g.lon_start.iloc[0], g.lat_start.iloc[0]]}
        elif len(g) == 1:
            geom = {"type": "LineString", "coordinates": tr["lines"][0]}
        else:
            geom = {"type": "MultiLineString", "coordinates": tr["lines"]}
        r = c.loc[eid]
        props = {"event_id": eid, "source_id": g.source_id.iloc[0], "n_segments": int(len(g)),
                 "geometry_status": st, "length_km": round(float(g.length_km.sum()), 4),
                 "area_author_km2": float(g.area_author_km2.iloc[0]),
                 "area_from_segments_km2": float(r.area_from_segments_km2),
                 "window_start_utc": tr["window"][0], "window_end_utc": tr["window"][1],
                 "effort_mid_utc": tr["effort_mid_utc"], "segment_windows": tr["segment_windows"],
                 "track_center": [round(tr["center"][0], 6), round(tr["center"][1], 6)],
                 "source": "; ".join(g.source), "note": " | ".join(x for x in g.note if x)}
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    return {"type": "FeatureCollection", "features": feats}


def write_outputs(out: Path = OUT_DIR, pdir: Path = PANGAEA_DIR, samples: Path = SAMPLES) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    seg, chk = build(pdir, samples)
    seg.to_csv(out / "transects.csv", index=False, lineterminator="\n", float_format="%.6f")
    chk.to_csv(out / "checks.csv", index=False, lineterminator="\n")
    gj = to_geojson(seg, chk)
    (out / "transects.geojson").write_text(json.dumps(gj, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"segments": len(seg), "events": seg.event_id.nunique(), "out": str(out)}


if __name__ == "__main__":
    print(write_outputs())
