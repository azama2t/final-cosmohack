"""Реестр кандидатов пар «полевое наблюдение <-> спутниковая сцена» (Sentinel-2, Landsat 8/9) по метаданным STAC.

Для каждого события (event_id) task/macroplastic_marine_samples.csv берётся одна точка и один момент:
  * точка — строка с ролью центра трансекты/мозаики (published/reported_transect_center, transect_midpoint_arithmetic,
    mosaic_center); если такой нет — среднее координат строк события (позиции наблюдения);
  * время — середина интервала time_start..time_end (переход через полночь учитывается), иначе начало;
    если времени нет — полдень даты, time_known=False, окно расширяется на 0.5 сут с каждой стороны.
Поиск: STAC bbox точки (+-0.01 градуса) в окне +-window_days. Коллекции:
  Earth Search v1 sentinel-2-l2a, sentinel-2-l1c; Planetary Computer sentinel-2-l2a, landsat-c2-l2.
Каждый ответ кэшируется в data/pairs/cache/<sha1>.json, поэтому перезапуск продолжает с места остановки.
Отказы не выбрасываются: строка с accept=False и reject_reason (через ';').

Запуск:  .venv\\Scripts\\python.exe scripts\\case\\find_pairs.py [--window-days 5] [--accept-days 1] [--max-cloud 60]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "task" / "macroplastic_marine_samples.csv"
OUT = ROOT / "data" / "pairs"
CACHE = OUT / "cache"
REPORT = ROOT / "reports" / "case_pairs" / "summary.md"

ES = "https://earth-search.aws.element84.com/v1"
PC = "https://planetarycomputer.microsoft.com/api/stac/v1"
SOURCES = [  # (endpoint name, url, collection, level)
    ("earth-search", ES, "sentinel-2-l2a", "L2A"),
    ("earth-search", ES, "sentinel-2-l1c", "L1C"),
    ("planetary-computer", PC, "sentinel-2-l2a", "L2A"),
    ("planetary-computer", PC, "landsat-c2-l2", "L2SP"),
]
LAUNCH = {"S2A": "2015-06-23", "S2B": "2017-03-07", "L8": "2013-02-11", "L9": "2021-09-27"}
FAMILY = {"sentinel-2-l2a": ["S2A", "S2B"], "sentinel-2-l1c": ["S2A", "S2B"], "landsat-c2-l2": ["L8", "L9"]}
CENTER_ROLES = ["published_transect_center", "reported_transect_center", "transect_midpoint_arithmetic", "mosaic_center"]
PLATFORM = {"sentinel-2a": "S2A", "sentinel-2b": "S2B", "sentinel-2c": "S2C", "landsat-8": "L8", "landsat-9": "L9",
            "landsat-7": "L7", "landsat-5": "L5"}


# ---------------------------------------------------------------- events
def build_events(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for eid, g in df.groupby("event_id", sort=False):
        c = g[g.position_role.isin(CENTER_ROLES)]
        if len(c):
            r0 = c.iloc[0]
            lat, lon, how = float(r0.latitude), float(r0.longitude), str(r0.position_role)
        else:
            lat, lon = float(g.latitude.mean()), float(g.longitude.mean())
            how = "mean_of_" + "+".join(sorted(g.position_role.unique()))
        r = g.iloc[0]
        ts, te = r.time_start_utc, r.time_end_utc
        day = pd.Timestamp(r.date_utc, tz="UTC")
        if isinstance(ts, str) and ts:
            t0 = day + pd.Timedelta(ts)
            if isinstance(te, str) and te:
                t1 = day + pd.Timedelta(te)
                if t1 < t0:
                    t1 += pd.Timedelta(days=1)
                obs, tnote = t0 + (t1 - t0) / 2, "interval_mid"
            else:
                obs, tnote = t0, "start"
            known = True
        else:
            obs, tnote, known = day + pd.Timedelta(hours=12), "date_only_noon", False
        rows.append(dict(event_id=eid, source_id=r.source_id, lat=lat, lon=lon, point_from=how,
                         obs_datetime=obs, time_known=known, time_note=tnote, n_rows=len(g)))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- STAC
_session = requests.Session()


def stac_search(url: str, coll: str, bbox, dt0: str, dt1: str, retries: int = 5) -> list[dict]:
    body = {"collections": [coll], "bbox": bbox, "datetime": f"{dt0}/{dt1}", "limit": 100}
    key = hashlib.sha1(json.dumps([url, body], sort_keys=True).encode()).hexdigest()
    fp = CACHE / f"{key}.json"
    if fp.exists():
        return json.loads(fp.read_text())
    feats, nxt, delay = [], (f"{url}/search", body), 2.0
    while nxt:
        link, payload = nxt
        for att in range(retries):
            try:
                resp = _session.post(link, json=payload, timeout=60)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                js = resp.json()
                break
            except Exception as e:  # noqa: BLE001
                if att == retries - 1:
                    raise
                time.sleep(delay * (2 ** att))
        for f in js.get("features", []):
            p = f.get("properties", {})
            feats.append(dict(id=f["id"], geometry=f.get("geometry"), datetime=p.get("datetime"),
                              platform=p.get("platform"), cloud=p.get("eo:cloud_cover"),
                              mgrs=p.get("s2:mgrs_tile") or p.get("grid:code"),
                              wrs_path=p.get("landsat:wrs_path"), wrs_row=p.get("landsat:wrs_row"),
                              proc_level=p.get("processing:level") or p.get("s2:processing_baseline")))
        nxt = None
        for ln in js.get("links", []):
            if ln.get("rel") == "next" and js.get("features"):
                nxt = (ln["href"], ln.get("body") or payload)
                break
    fp.write_text(json.dumps(feats))
    return feats


def query_event(ev: dict, src: tuple, window_days: float) -> tuple[dict, tuple, list[dict] | str]:
    name, url, coll, level = src
    half = window_days + (0 if ev["time_known"] else 0.5)
    t = ev["obs_datetime"]
    dt0 = (t - pd.Timedelta(days=half)).strftime("%Y-%m-%dT%H:%M:%SZ")
    dt1 = (t + pd.Timedelta(days=half)).strftime("%Y-%m-%dT%H:%M:%SZ")
    d = 0.01
    bbox = [ev["lon"] - d, ev["lat"] - d, ev["lon"] + d, ev["lat"] + d]
    try:
        return ev, src, stac_search(url, coll, bbox, dt0, dt1)
    except Exception as e:  # noqa: BLE001
        return ev, src, f"error:{type(e).__name__}:{str(e)[:80]}"


def make_rows(ev: dict, src: tuple, res, a: argparse.Namespace) -> list[dict]:
    name, url, coll, level = src
    base = {k: ev[k] for k in ("event_id", "source_id", "lat", "lon", "obs_datetime", "time_known", "time_note", "point_from")}
    base.update(endpoint=name, collection=coll, level=level)
    fam = FAMILY[coll]
    launched = [m for m in fam if ev["obs_datetime"] >= pd.Timestamp(LAUNCH[m], tz="UTC") - pd.Timedelta(days=a.window_days)]
    empty = dict(mission=None, item_id=None, scene_datetime=pd.NaT, dt_hours=np.nan, cloud_cover=np.nan, tile=None,
                 point_inside_footprint=None, accept=False)
    if isinstance(res, str):
        return [base | empty | dict(mission="/".join(fam), reject_reason=res)]
    if not res:
        reason = "no_scene_in_window" if launched else "mission_not_launched"
        if not ev["time_known"]:
            reason += ";time_unknown(window_by_day)"
        return [base | empty | dict(mission="/".join(fam), reject_reason=reason)]
    out = []
    pt = Point(ev["lon"], ev["lat"])
    for it in res:
        mission = PLATFORM.get(str(it.get("platform") or "").lower(), str(it.get("platform")))
        st = pd.Timestamp(it["datetime"])
        st = st.tz_localize("UTC") if st.tzinfo is None else st.tz_convert("UTC")
        dth = (st - ev["obs_datetime"]).total_seconds() / 3600
        try:
            inside = bool(shape(it["geometry"]).intersects(pt)) if it.get("geometry") else None
        except Exception:  # noqa: BLE001
            inside = None
        if coll.startswith("landsat"):
            tile = f"{it.get('wrs_path')}/{it.get('wrs_row')}"
        else:
            tile = str(it.get("mgrs") or "").replace("MGRS-", "")
        cc = it.get("cloud")
        reasons = []
        if mission not in fam:
            reasons.append(f"mission_not_target({mission})")
        if inside is False:
            reasons.append("point_outside_footprint")
        acc_h = a.accept_days * 24 + (0 if ev["time_known"] else 12)
        if abs(dth) > acc_h:
            reasons.append(f"dt>{a.accept_days:g}d")
        if cc is None:
            reasons.append("cloud_unknown")
        elif cc > a.max_cloud:
            reasons.append(f"cloud_cover>{a.max_cloud:g}")
        if not ev["time_known"]:
            reasons.append("time_unknown(window_by_day)")
        hard = [r for r in reasons if not r.startswith("time_unknown")]
        out.append(base | dict(mission=mission, item_id=it["id"], scene_datetime=st, dt_hours=round(dth, 2),
                               cloud_cover=cc, tile=tile, point_inside_footprint=inside, accept=not hard,
                               reject_reason=";".join(reasons)))
    return out


# ---------------------------------------------------------------- summary
def summarize(cand: pd.DataFrame, ev: pd.DataFrame, a: argparse.Namespace) -> str:
    L = ["# Кандидаты пар «полевое наблюдение ↔ спутниковая сцена»", "",
         f"Скрипт `scripts/case/find_pairs.py`, параметры: окно поиска ±{a.window_days:g} сут, отбор: |dt| ≤ {a.accept_days:g} сут, "
         f"облачность сцены ≤ {a.max_cloud:g} %, точка внутри контура сцены. Только метаданные STAC (eo:cloud_cover по сцене/тайлу, "
         "не в точке). Таблица: `data/pairs/candidates.parquet` (+ csv).", "",
         f"Событий: {len(ev)}; строк-кандидатов: {int(cand.item_id.notna().sum())}; строк без сцены: {int(cand.item_id.isna().sum())}; "
         f"событий без времени (окно по суткам): {int((~ev.time_known).sum())}.", ""]
    sc = cand[cand.item_id.notna() & (cand.point_inside_footprint != False)].copy()  # noqa: E712
    sc["adt_d"] = sc.dt_hours.abs() / 24
    sc["fam"] = np.where(sc.collection.str.startswith("landsat"), "Landsat 8/9", "Sentinel-2")
    sc.loc[~sc.mission.isin(["S2A", "S2B", "L8", "L9"]), "fam"] = "другие (" + sc.mission.astype(str) + ")"
    srcs = sorted(ev.source_id.unique())
    nev = ev.groupby("source_id").size()
    L += ["## События хотя бы с одной сценой в окне (точка внутри контура)", "",
          "| Источник | событий | " + " | ".join(f"{f} ±{w}" for f in ("S2", "L8/9", "любая") for w in (1, 3, 5)) + " | S2 ±1, облачн.≤" + f"{a.max_cloud:g} | L8/9 ±1, облачн.≤{a.max_cloud:g} |",
          "|---|---:|" + "---:|" * 11]
    tot = []
    for s in srcs + ["ВСЕ"]:
        sub = sc if s == "ВСЕ" else sc[sc.source_id == s]
        cells = []
        for fam in ("Sentinel-2", "Landsat 8/9", None):
            ff = sub if fam is None else sub[sub.fam == fam]
            ff = ff[ff.mission.isin(["S2A", "S2B", "L8", "L9"])]
            for w in (1, 3, 5):
                extra = (ff.time_known == False) * 0.5  # noqa: E712
                cells.append(ff[ff.adt_d <= w + extra].event_id.nunique())
        for fam in ("Sentinel-2", "Landsat 8/9"):
            ff = sub[(sub.fam == fam) & (sub.cloud_cover <= a.max_cloud)]
            extra = (ff.time_known == False) * 0.5  # noqa: E712
            cells.append(ff[ff.adt_d <= a.accept_days + extra].event_id.nunique())
        n = len(ev) if s == "ВСЕ" else int(nev[s])
        L.append(f"| {s} | {n} | " + " | ".join(str(c) for c in cells) + " |")
    L += ["", f"Принято (accept=True, все условия): событий {cand[cand.accept].event_id.nunique()}, "
          f"пар {int(cand.accept.sum())}; по миссиям: "
          + ", ".join(f"{m} — {k} соб." for m, k in cand[cand.accept].groupby('mission').event_id.nunique().items()), ""]
    # level found for S2
    s2 = sc[sc.fam == "Sentinel-2"]
    if len(s2):
        L += ["## Уровень обработки Sentinel-2 по коллекциям", "", "| endpoint / collection | событий со сценой ±5 сут | сцен (строк) | годы |", "|---|---:|---:|---|"]
        for (e, c), g in s2.groupby(["endpoint", "collection"]):
            yrs = ",".join(sorted(g.scene_datetime.dt.year.astype(str).unique()))
            L.append(f"| {e} / {c} | {g.event_id.nunique()} | {len(g)} | {yrs} |")
        L.append("")
    # distributions
    if len(sc):
        L += ["## Распределение |dt| и облачности (сцены в окне, точка внутри контура)", "",
              "| семейство | n | |dt| медиана, ч | |dt| p25–p75, ч | облачность медиана, % | доля облачн. ≤ 20 % | ≤ 60 % |", "|---|---:|---:|---:|---:|---:|---:|"]
        for fam, g in sc.groupby("fam"):
            ad = g.dt_hours.abs()
            L.append(f"| {fam} | {len(g)} | {ad.median():.1f} | {ad.quantile(.25):.1f}–{ad.quantile(.75):.1f} | "
                     f"{g.cloud_cover.median():.1f} | {(g.cloud_cover <= 20).mean():.0%} | {(g.cloud_cover <= 60).mean():.0%} |")
        L.append("")
        h = pd.cut(sc.dt_hours / 24, bins=[-6, -3, -1, 0, 1, 3, 6], right=True)
        L += ["Гистограмма dt (сут, сцена минус наблюдение), все семейства: " +
              "; ".join(f"{iv}: {n}" for iv, n in h.value_counts(sort=False).items()), ""]
    # reasons
    rr = cand[~cand.accept].reject_reason.fillna("").str.split(";").explode()
    rr = rr[rr != ""].str.replace(r"^error:.*", "error", regex=True)
    L += ["## Причины отказа (строк; у строки может быть несколько)", "", "| причина | строк |", "|---|---:|"]
    for k, v in rr.value_counts().head(12).items():
        L.append(f"| {k} | {v} |")
    L.append("")
    # events with no scene at all by source
    L += ["## События без единой сцены ±{0:g} сут (любая миссия/коллекция)".format(a.window_days), ""]
    have = set(sc[sc.mission.isin(["S2A", "S2B", "L8", "L9"])].event_id)
    for s in srcs:
        e = ev[ev.source_id == s]
        L.append(f"- {s}: {int((~e.event_id.isin(have)).sum())} из {len(e)}")
    L.append("")
    # top-10
    acc = cand[cand.accept].copy()
    if len(acc):
        acc["score"] = (acc.dt_hours.abs() / 24 + acc.cloud_cover / 100 + (~acc.time_known) * 0.5).round(1)
        acc["lvl_rank"] = acc.level.map({"L2A": 0, "L2SP": 0, "L1C": 1})
        acc["sdate"] = acc.scene_datetime.dt.date
        acc = acc.sort_values(["score", "lvl_rank"]).drop_duplicates(["event_id", "mission", "sdate"])
        acc.drop(columns=["lvl_rank", "sdate"]).drop_duplicates("event_id").to_csv(OUT / "best_per_event.csv", index=False)
        acc = acc.head(10)
        L += ["## 10 лучших пар (минимум |dt|/сут + облачность/100 + 0.5 за неизвестное время; дубли ES/PC одной сцены свёрнуты, приоритет L2A)", "",
              "| event_id | источник | lat, lon | наблюдение UTC | время известно | миссия / уровень | item_id | dt, ч | облачн., % | тайл |",
              "|---|---|---|---|---|---|---|---:|---:|---|"]
        for r in acc.itertuples():
            L.append(f"| {r.event_id} | {r.source_id} | {r.lat:.3f}, {r.lon:.3f} | {r.obs_datetime:%Y-%m-%d %H:%M} | {'да' if r.time_known else 'нет'} | "
                     f"{r.mission} / {r.level} ({r.endpoint}) | {r.item_id} | {r.dt_hours:+.1f} | {r.cloud_cover:.1f} | {r.tile} |")
        L.append("")
    L += ["## Оговорки", "",
          "- Облачность — по всей сцене/тайлу (eo:cloud_cover), не в точке; для отбора пары нужна проверка SCL/QA_PIXEL в точке.",
          "- dt не учитывает дрейф объектов: за сутки плавающий мусор смещается на километры; пара «точка ↔ пиксель» — только кандидат.",
          "- Для событий без времени наблюдения момент = полдень даты, окно расширено на ±0.5 сут.",
          "- Точка события — центр трансекты/мозаики, у трансект длиной десятки км сцена может покрывать только часть маршрута.", ""]
    return "\n".join(L)


def point_scl(n: int) -> str:
    """SCL (Sen2Cor) в окне 5x5 пикселей 20 м вокруг точки для n лучших принятых пар S2 L2A Planetary Computer."""
    import planetary_computer
    import rasterio
    from pyproj import Transformer
    from rasterio.windows import Window
    b = pd.read_csv(OUT / "best_per_event.csv") if (OUT / "best_per_event.csv").exists() else None
    c = pd.read_parquet(OUT / "candidates.parquet")
    acc = c[c.accept & (c.endpoint == "planetary-computer") & (c.level == "L2A")].copy()
    acc["score"] = acc.dt_hours.abs() / 24 + acc.cloud_cover / 100 + (~acc.time_known) * 0.5
    acc = acc.sort_values("score").drop_duplicates("event_id").head(n)
    names = {0: "nodata", 1: "defect", 2: "dark", 3: "cl_shadow", 4: "veg", 5: "bare", 6: "water", 7: "unclass",
             8: "cloud_med", 9: "cloud_high", 10: "cirrus", 11: "snow"}
    L = ["## Облачность в точке по SCL (окно 5×5 пикселей 20 м, примеры)", "",
         "| event_id | item_id | облачн. сцены, % | SCL в точке | доля классов в окне |", "|---|---|---:|---|---|"]
    for r in acc.itertuples():
        try:
            href = planetary_computer.sign(requests.get(f"{PC}/collections/sentinel-2-l2a/items/{r.item_id}", timeout=60).json()["assets"]["SCL"]["href"])
            with rasterio.open(href) as ds:
                x, y = Transformer.from_crs(4326, ds.crs, always_xy=True).transform(r.lon, r.lat)
                row, col = ds.index(x, y)
                a = ds.read(1, window=Window(col - 2, row - 2, 5, 5))
            vals, cnt = np.unique(a, return_counts=True)
            frac = ", ".join(f"{names.get(int(v), v)} {k / a.size:.0%}" for v, k in zip(vals, cnt))
            L.append(f"| {r.event_id} | {r.item_id} | {r.cloud_cover:.1f} | {names.get(int(a[2, 2]))} | {frac} |")
        except Exception as e:  # noqa: BLE001
            L.append(f"| {r.event_id} | {r.item_id} | {r.cloud_cover:.1f} | ошибка: {type(e).__name__} | |")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-days", type=float, default=5)
    ap.add_argument("--accept-days", type=float, default=1)
    ap.add_argument("--max-cloud", type=float, default=60)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="только первые N событий (отладка)")
    ap.add_argument("--summary-only", action="store_true")
    ap.add_argument("--point-scl", type=int, default=0, help="проверить SCL в точке для N лучших пар S2 L2A (PC)")
    a = ap.parse_args()
    a.workers = min(a.workers, 4)
    CACHE.mkdir(parents=True, exist_ok=True)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    ev = build_events(pd.read_csv(CSV))
    if a.limit:
        ev = ev.head(a.limit)
    ev.to_csv(OUT / "events.csv", index=False)
    evs = ev.to_dict("records")
    jobs = [(e, s) for e in evs for s in SOURCES]
    rows, done, t0 = [], 0, time.time()
    with ThreadPoolExecutor(a.workers) as ex:
        futs = [ex.submit(query_event, e, s, a.window_days) for e, s in jobs]
        for f in as_completed(futs):
            e, s, res = f.result()
            rows += make_rows(e, s, res, a)
            done += 1
            if done % 50 == 0:
                print(f"{done}/{len(jobs)} {time.time() - t0:.0f}s", flush=True)
    cand = pd.DataFrame(rows).sort_values(["source_id", "event_id", "collection", "endpoint", "dt_hours"]).reset_index(drop=True)
    cand["point_inside_footprint"] = cand.point_inside_footprint.astype("boolean")
    cand.to_parquet(OUT / "candidates.parquet", index=False)
    cand.to_csv(OUT / "candidates.csv", index=False)
    text = summarize(cand, ev, a)
    if a.point_scl:
        text += "\n" + point_scl(a.point_scl)
    REPORT.write_text(text, encoding="utf-8")
    nerr = int(cand.reject_reason.fillna("").str.startswith("error").sum())
    print(f"rows={len(cand)} accepted_events={cand[cand.accept].event_id.nunique()} errors={nerr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
