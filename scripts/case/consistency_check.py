r"""L67 — самопроверка согласованности и скорости API v3 (критерий Т5).

Сверяет по id: алгоритм (исходные таблицы) = /api/v3 JSON = экспорт CSV = экспорт GeoJSON; повтор сохранённого
запроса (GET /queries/{id}/run дважды) = тот же SHA-256; некорректные входы -> 4xx с сообщением; пустой результат ->
200 + пустой список; p50/p95 по эндпоинтам (TestClient и живой экземпляр на своём порту).

Изоляция сохранённых запросов: case_store.PATHS["queries"] подменяется на временный файл (tempfile) ДО создания
приложения; service/labels/queries.jsonl не трогается (SHA-256 файла сверяется до и после). Живой экземпляр
запускается тем же способом: `python -c "<подмена PATHS['queries']>; service.__main__.main([...])"` на --port.

Запуск:
  .venv\Scripts\python.exe scripts\case\consistency_check.py                 # всё, включая живой :8091
  .venv\Scripts\python.exe scripts\case\consistency_check.py --no-live --n 5 # быстро
  ... --ui-url http://127.0.0.1:5173   # заготовка сравнения UI (Playwright) — пока TODO
Выход: reports/selfcheck/consistency_<YYYYmmdd_HHMM>.md|json
"""
from __future__ import annotations

import argparse
import copy
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Optional

os.environ["CUDA_VISIBLE_DEVICES"] = ""
ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SAMPLES = ROOT / "task" / "macroplastic_marine_samples.csv"
CANDIDATES = ROOT / "data" / "pairs" / "candidates.csv"
PAIR_QUALITY = ROOT / "data" / "pairs" / "pair_quality.csv"
CONC_METRICS = ROOT / "reports" / "case_conc" / "metrics.json"
PREDICTIONS = ROOT / "reports" / "case_conc" / "predictions.csv"
DET_METRICS = ROOT / "reports" / "case_detector" / "metrics.json"
LGBM_TEST = ROOT / "reports" / "lgbm_final_test.json"
SELECTION = {"S2_visual_total_plastic": ROOT / "data" / "case" / "selection_S2_visual_total_plastic.csv",
             "S1_trawl_total_plastic": ROOT / "data" / "case" / "selection_S1_trawl_total_plastic.csv"}
DEV_CV = ROOT / "reports" / "case_conc" / "dev_cv.json"
CONC_MODEL_YAML = ROOT / "configs" / "case_conc_model.yaml"
REAL_QUERIES = ROOT / "service" / "labels" / "queries.jsonl"

# Сохранённые запросы (раздел 8 контракта). scope в query-объекте нет -> total_plastic проверяется отдельно.
QUERIES = [
    {"key": "S2_sargasso", "name": "S2 Саргассово море (total_plastic — через selection)",
     "query": {"bbox": [-71, 22, -57, 33], "sources": ["S2_SARGASSO_MSM41"], "profiles": ["S2_visual_GT2"]},
     "selection": "S2_visual_total_plastic", "scope": "total_plastic"},
    {"key": "S4_black_sea", "name": "Чёрное море, все пары", "query": {"sources": ["S4_BLACK_SEA_DOORS3"]}},
    {"key": "S3_north_sea_2016", "name": "Северное море, 2016",
     "query": {"sources": ["S3_SE_NORTH_SEA"], "date_from": "2016-01-01", "date_to": "2016-12-31"}},
    {"key": "S1_gpgp_trawl", "name": "ГПМП, трал 5–50 см",
     "query": {"sources": ["S1_GPGP2018"], "profiles": ["S1_trawl_5_to_50"]},
     "selection": "S1_trawl_total_plastic", "scope": "total_plastic"},
    {"key": "empty_bbox", "name": "Пустой bbox (Южная Атлантика, наблюдений нет)",
     "query": {"bbox": [-30, -50, -29, -49]}},
]
BAD_QUERY = {"key": "bad_dates", "name": "Некорректные даты (задом наперёд)",
             "query": {"date_from": "2016-12-31", "date_to": "2016-01-01"}}

TOL_NUM = 1e-6

# Описание известных видов расхождений для отчёта владельцу (check -> файл/поле/ожидаемое)
OWNERS = {
    "pairs.drift_shift_km": ("service/case_store.py::_pairs_build", "pair.drift_shift_km (+ tolerance_km)",
                             "брать drift_shift_km и tolerance_km из data/pairs/candidates.csv (сейчас всегда null)"),
    "pairs.reject_reasons_nonempty": ("service/case_store.py::_CAND_REASON / _codes",
                                      "pair.reject_reasons",
                                      "у отклонённой пары ≥1 код; sync_unreliable_drift -> новый код "
                                      "(напр. DRIFT_TOO_LARGE, добавить в REJECT_REASONS), time_unknown(...) -> "
                                      "POSITION_UNCERTAIN/TIME_UNKNOWN"),
    "export.zones_query_id": ("service/routes_v3.py::_query_to_params / export",
                              "export?layer=zones&query_id=… при query.sources",
                              "тот же набор зон, что в /queries/{id}/run (run фильтрует зоны через связанные "
                              "наблюдения по sources, экспорт — нет)"),
    "zones.area_km2_vs_polygon": ("service/case_store.py::zones_all (area_km2 = pair_quality.strip_area_km2)",
                                  "zone.area_km2 vs zone.geometry",
                                  "площадь в карточке/CSV = площадь нарисованного полигона (±1%), либо явно "
                                  "подписать area_basis «по пикселям снимка» (растровая полоса больше круга на 2–3%)"),
    "zone_vs_pair.status": ("service/case_store.py::zones_all / _pairs_build / scenes_all",
                            "zone.detection_status vs pair.status / scene.status",
                            "зона detected/not_detected стоит на паре и снимке со статусом rejected; нужно "
                            "либо помечать такие зоны (напр. flags=['pair_rejected_drift']), либо не считать "
                            "снимок проверенным; и в observation.linked_scenes, и в zone.support одна логика"),
    "obs.linked_scenes_vs_zones": ("service/case_store.py::linked_scenes_by_sample",
                                   "observation.linked_scenes",
                                   "образец, на котором построена зона со сценой S, имеет S в linked_scenes "
                                   "(сейчас берутся только accepted пары, а их 0)"),
}


# ------------------------------------------------------------------ helpers
def fnum(x) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        v = float(x)
    else:
        s = str(x).strip()
        if s == "" or s.lower() in ("nan", "none", "null"):
            return None
        try:
            v = float(s)
        except ValueError:
            return None
    return v if math.isfinite(v) else None


def near(a, b, tol=TOL_NUM, rel=1e-9) -> bool:
    a, b = fnum(a), fnum(b)
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= max(tol, rel * max(abs(a), abs(b)))


def read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _yaml(p: Path) -> dict:
    if not p.is_file():
        return {}
    import yaml
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def csv_text(body: bytes) -> list[dict]:
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))


def canon_hash(obj, drop=("ran_at", "generated_at")) -> str:
    o = copy.deepcopy(obj)
    if isinstance(o, dict):
        for k in drop:
            o.pop(k, None)
    s = json.dumps(o, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def file_sha(p: Path) -> Optional[str]:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def truthy(x) -> bool:
    return str(x).strip().lower() in ("true", "1", "yes")


class Checker:
    def __init__(self):
        self.rows: list[dict] = []

    def add(self, query: str, check: str, ok: bool, detail: str = "", example: Any = None, level: str = "fail",
            n: Optional[int] = None):
        self.rows.append({"query": query, "check": check, "status": "ok" if ok else level, "detail": detail,
                          "example": example, "n": n})
        return ok

    def counts(self) -> Counter:
        return Counter(r["status"] for r in self.rows)


def diff_ids(a, b) -> dict:
    a, b = set(a), set(b)
    return {"only_left": sorted(a - b)[:5], "only_right": sorted(b - a)[:5], "n_left": len(a), "n_right": len(b)}


# ------------------------------------------------------------------ algorithm side (independent of service code)
def algo_observations(samples: list[dict], q: dict) -> list[dict]:
    out = []
    for r in samples:
        if q.get("sources") and r["source_id"] not in q["sources"]:
            continue
        if q.get("profiles") and r["measurement_profile"] not in q["profiles"]:
            continue
        d = (r.get("date_utc") or "")[:10]
        if q.get("date_from") and (not d or d < q["date_from"]):
            continue
        if q.get("date_to") and (not d or d > q["date_to"]):
            continue
        if q.get("bbox"):
            b = q["bbox"]
            lon, lat = fnum(r["longitude"]), fnum(r["latitude"])
            if lon is None or not (b[0] <= lon <= b[2] and b[1] <= lat <= b[3]):
                continue
        out.append(r)
    return out


def geodesic_area_km2(geom: dict) -> Optional[float]:
    try:
        from pyproj import Geod
        from shapely.geometry import shape
        a, _ = Geod(ellps="WGS84").geometry_area_perimeter(shape(geom))
        return abs(a) / 1e6
    except Exception:
        return None


def bounds_of(geom) -> Optional[list[float]]:
    pts = []

    def walk(c):
        if isinstance(c, list) and c and isinstance(c[0], (int, float)):
            pts.append(c)
        elif isinstance(c, list):
            for x in c:
                walk(x)
    if not geom:
        return None
    walk(geom.get("coordinates"))
    if not pts:
        return None
    return [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]


def coords_ok(geom) -> bool:
    b = bounds_of(geom)
    return b is not None and -180 <= b[0] <= b[2] <= 180 and -90 <= b[1] <= b[3] <= 90


# ------------------------------------------------------------------ per-query consistency
def check_query(c, ck: Checker, spec: dict, src: dict, qrec: dict) -> dict:
    qk, qid, q = spec["key"], qrec["query_id"], qrec["query"]
    samples_by_id = src["samples_by_id"]
    info: dict[str, Any] = {"key": qk, "name": spec["name"], "query_id": qid, "query": q}

    # --- run twice: determinism
    r1 = c.get(f"/api/v3/queries/{qid}/run")
    r2 = c.get(f"/api/v3/queries/{qid}/run")
    ck.add(qk, "run.http_200", r1.status_code == 200 and r2.status_code == 200, f"{r1.status_code}/{r2.status_code}")
    run, run2 = r1.json(), r2.json()
    h1, h2 = canon_hash(run), canon_hash(run2)
    info["sha256_run"] = h1
    ck.add(qk, "run.sha256_repeat", h1 == h2, f"{h1[:16]} vs {h2[:16]}")

    obs = run["observations"]["features"]
    zones = run["zones"]["features"]
    scenes = run["scenes"]
    info.update({"n_obs": len(obs), "n_zones": len(zones), "n_scenes": len(scenes), "summary": run["summary"]})

    # --- summary vs lists
    sm = run["summary"]
    ck.add(qk, "summary.counts", sm["n_obs"] == len(obs) == run["observations"]["count"]
           and sm["n_zones"] == len(zones) == run["zones"]["count"] and sm["n_scenes"] == len(scenes),
           f"n_obs={sm['n_obs']} n_zones={sm['n_zones']} n_scenes={sm['n_scenes']}")
    ck.add(qk, "summary.by_status", sm["by_status"] == dict(sorted(Counter(z["properties"]["status"]
                                                                           for z in zones).items())),
           json.dumps(sm["by_status"]))
    if not obs:
        ck.add(qk, "empty.reason", bool(run["observations"].get("empty_reason")),
               str(run["observations"].get("empty_reason")))
    if not zones:
        ck.add(qk, "empty.zones_reason", bool(run["zones"].get("empty_reason")), str(run["zones"].get("empty_reason")))

    # --- algorithm = API: observations
    alg = algo_observations(src["samples"], q)
    ids_api = [f["id"] for f in obs]
    ck.add(qk, "obs.ids algo=api", set(ids_api) == {r["sample_id"] for r in alg} and len(ids_api) == len(alg),
           json.dumps(diff_ids([r["sample_id"] for r in alg], ids_api)), n=len(alg))
    ck.add(qk, "obs.sorted_by_id", ids_api == sorted(ids_api), "")
    bad_num, bad_geo, bad_units = [], [], []
    for f in obs:
        r = samples_by_id.get(f["id"])
        p = f["properties"]
        if r is None:
            bad_num.append(f["id"])
            continue
        exp_c = None if r["record_type"] == "item_observation" else fnum(r["concentration_items_km2"])
        for fld, exp in (("concentration_items_km2", exp_c), ("sampled_area_km2", fnum(r["sampled_area_km2"]))):
            if not near(p[fld], exp):
                bad_num.append({"id": f["id"], "field": fld, "api": p[fld], "csv": exp})
        g = f["geometry"]
        if not (g and g["type"] == "Point" and near(g["coordinates"][0], r["longitude"])
                and near(g["coordinates"][1], r["latitude"])):
            bad_geo.append({"id": f["id"], "api": g, "csv": [r["longitude"], r["latitude"]]})
        if p["kind"] != "measurement" or p["measurement_profile"] != r["measurement_profile"] \
                or p["target_scope"] != r["target_scope"]:
            bad_units.append(f["id"])
    ck.add(qk, "obs.numbers algo=api (conc, area)", not bad_num, "", bad_num[:3], n=len(obs))
    ck.add(qk, "obs.geometry algo=api [lon,lat] EPSG:4326", not bad_geo, "", bad_geo[:3], n=len(obs))
    ck.add(qk, "obs.kind/profile/scope algo=api", not bad_units, "", bad_units[:3], n=len(obs))

    # --- /observations list endpoint with the same filters = run
    params = {}
    if q.get("bbox"):
        params["bbox"] = ",".join(str(x) for x in q["bbox"])
    for k in ("date_from", "date_to"):
        if q.get(k):
            params[k] = q[k]
    if q.get("sources"):
        params["source"] = ",".join(q["sources"])
    if q.get("profiles"):
        params["profile"] = ",".join(q["profiles"])
    lst = c.get("/api/v3/observations", params={**params, "limit": 100000}).json()
    ck.add(qk, "obs.list endpoint = run", canon_hash(lst["features"]) == canon_hash(obs),
           f"{lst['count']} vs {len(obs)}")

    # --- export observations CSV / GeoJSON (query_id) = run
    e_csv = c.get("/api/v3/export", params={"layer": "observations", "format": "csv", "query_id": qid})
    e_gj = c.get("/api/v3/export", params={"layer": "observations", "format": "geojson", "query_id": qid})
    ck.add(qk, "export.obs http/headers", e_csv.status_code == 200 and e_gj.status_code == 200
           and "attachment" in e_csv.headers.get("content-disposition", ""), f"{e_csv.status_code}/{e_gj.status_code}")
    rows = csv_text(e_csv.content)
    gj = e_gj.json()
    ck.add(qk, "export.obs ids csv=geojson=json", [r["sample_id"] for r in rows] == ids_api
           and [f["id"] for f in gj["features"]] == ids_api,
           json.dumps(diff_ids([r["sample_id"] for r in rows], ids_api)), n=len(ids_api))
    ck.add(qk, "export.obs geojson = json (features)", canon_hash(gj["features"]) == canon_hash(obs), "")
    by = {f["id"]: f for f in obs}
    bad = []
    for r in rows:
        f = by.get(r["sample_id"])
        if not f:
            continue
        p = f["properties"]
        exp_c = p["concentration_items_km2"]
        csv_c = None if r["record_type"] == "item_observation" else fnum(r["concentration_items_km2"])
        if not near(csv_c, exp_c) or not near(r["longitude"], f["geometry"]["coordinates"][0]) \
                or not near(r["latitude"], f["geometry"]["coordinates"][1]) or r["kind"] != "measurement" \
                or not near(r["sampled_area_km2"], p["sampled_area_km2"]):
            bad.append({"id": r["sample_id"], "csv_conc": r["concentration_items_km2"], "json_conc": exp_c})
        if r["linked_scenes"] != ";".join(p["linked_scenes"]):
            bad.append({"id": r["sample_id"], "field": "linked_scenes"})
    ck.add(qk, "export.obs csv numbers = json", not bad, "", bad[:3], n=len(rows))

    # --- concentration: selection table / predictions (algorithm) = API
    if spec.get("selection"):
        prof = spec["selection"]
        sel = read_csv(SELECTION[prof])
        api_sc = c.get("/api/v3/observations", params={**params, "scope": spec["scope"], "limit": 100000}).json()
        by_sc = {f["id"]: f for f in api_sc["features"]}
        sel_ids = {r["sample_id"] for r in sel}
        # selection = all density rows of the profile; API with the query filters + scope must cover it
        ck.add(qk, f"selection({prof}) ids ⊆ api(scope={spec['scope']})",
               sel_ids <= set(by_sc), json.dumps(diff_ids(sel_ids, by_sc)), n=len(sel_ids))
        badc = [{"id": r["sample_id"], "selection_target": r["target"],
                 "api": (by_sc.get(r["sample_id"]) or {}).get("properties", {}).get("concentration_items_km2")}
                for r in sel if r["sample_id"] in by_sc
                and not near(r["target"], by_sc[r["sample_id"]]["properties"]["concentration_items_km2"], 1e-6)]
        ck.add(qk, "selection.target = api.concentration_items_km2", not badc, "", badc[:3], n=len(sel))
        ms = (src["conc_metrics"].get(prof) or {}).get("main_split")
        pr = [r for r in src["predictions"] if r["profile"] == prof and r["split"] == ms and r["model"] == "median"]
        badp = [{"id": r["sample_id"], "y_true": r["y_true"],
                 "api": by_sc[r["sample_id"]]["properties"]["concentration_items_km2"]}
                for r in pr if r["sample_id"] in by_sc
                and not near(r["y_true"], by_sc[r["sample_id"]]["properties"]["concentration_items_km2"], 1e-6)]
        ck.add(qk, f"predictions.y_true ({ms}) = api.concentration", not badp and bool(pr), f"n_pred={len(pr)}",
               badp[:3], n=len(pr))
        ck.add(qk, "units: selection unit = meta.units.concentration",
               src["meta"]["units"]["concentration"] == "items/km2", src["meta"]["units"]["concentration"])

    # --- zones: algorithm (pair_quality.csv) = run JSON = export CSV = export GeoJSON
    pq = src["pair_quality"]
    obs_ids = set(ids_api)
    exp_z = {}
    for r in pq:
        sids = {x for x in (r.get("sample_ids") or "").split(";") if x}
        if (q.get("sources") or q.get("profiles")) and not (sids & obs_ids):
            continue
        d = (r.get("scene_datetime") or "")[:10]
        if q.get("date_from") and (not d or d < q["date_from"]):
            continue
        if q.get("date_to") and (not d or d > q["date_to"]):
            continue
        n_det = fnum(r.get("n_det"))
        st = ("detected" if r["decision"] == "accept" and n_det and n_det > 0 else
              "not_detected" if r["decision"] == "accept" and n_det is not None else "insufficient_data")
        exp_z["Z-" + r["dir"]] = {"status": st, "area_km2": fnum(r["strip_area_km2"]),
                                  "detected_area_m2": fnum(r["det_area_m2"]), "scene_id": r["scene_id"],
                                  "sids": sorted(sids), "event_id": r["event_id"]}
    if q.get("bbox"):  # bbox for zones = geometry intersects; keep only those the API returned inside bbox
        b = q["bbox"]
        exp_z = {k: v for k, v in exp_z.items()
                 if any(samples_by_id.get(s) and b[0] - 0.1 <= fnum(samples_by_id[s]["longitude"]) <= b[2] + 0.1
                         and b[1] - 0.1 <= fnum(samples_by_id[s]["latitude"]) <= b[3] + 0.1 for s in v["sids"])}
    zid = [z["id"] for z in zones]
    ck.add(qk, "zones.ids algo(pair_quality)=api", set(zid) == set(exp_z), json.dumps(diff_ids(exp_z, zid)),
           n=len(exp_z))
    badz, badg, badst, bada = [], [], [], []
    for z in zones:
        p, e = z["properties"], exp_z.get(z["id"])
        if e:
            # L62e: area_km2 = geodesic area of the polygon (checked in zones.area_km2_vs_polygon);
            # the raster strip area of pair_quality lives in strip_area_raster_km2
            for fld, efld, tol in (("strip_area_raster_km2", "area_km2", 1e-4), ("detected_area_m2", "detected_area_m2", 0.1)):
                api_v = p.get(fld, p["area_km2"] if fld == "strip_area_raster_km2" else None)
                if not near(api_v, e[efld] if e[efld] is None else round(e[efld], 4 if efld == "area_km2" else 1), tol):
                    badz.append({"id": z["id"], "field": fld, "api": api_v, "pair_quality": e[efld]})
            if p["status"] != e["status"] or p["detection_status"] != e["status"]:
                badst.append({"id": z["id"], "api": p["status"], "expected": e["status"]})
        if p["concentration"] is not None or p["concentration_status"] not in ("unavailable", "research_estimate",
                                                                               "measured_nearby"):
            badst.append({"id": z["id"], "concentration": p["concentration"], "cs": p["concentration_status"]})
        if p["kind"] != "model_estimate":
            badst.append({"id": z["id"], "kind": p["kind"]})
        g = z["geometry"]
        ga = geodesic_area_km2(g) if g else None
        if ga is not None and p["area_km2"] and abs(ga - p["area_km2"]) > 0.02 * p["area_km2"]:
            bada.append({"id": z["id"], "area_km2": p["area_km2"], "polygon_geodesic_km2": round(ga, 4),
                         "rel_diff": round(p["area_km2"] / ga - 1, 4)})
        if not g or not coords_ok(g) or g.get("type") not in ("Polygon", "MultiPolygon"):
            badg.append({"id": z["id"], "geometry": (g or {}).get("type")})
        else:
            try:
                from shapely.geometry import Point, shape
                s0 = samples_by_id.get(p["support"]["field_sample_id"] or (p["support"]["linked_sample_ids"] or [""])[0])
                if s0 and shape(g).distance(Point(fnum(s0["longitude"]), fnum(s0["latitude"]))) > 1e-3:
                    badg.append({"id": z["id"], "sample_outside_polygon": s0["sample_id"]})
            except Exception as ex:  # pragma: no cover
                badg.append({"id": z["id"], "err": str(ex)})
    ck.add(qk, "zones.numbers algo=api (area_km2, detected_area_m2)", not badz, "", badz[:3], n=len(zones))
    ck.add(qk, "zones.statuses algo=api (detection, concentration=null)", not badst, "", badst[:3], n=len(zones))
    ck.add(qk, "zones.geometry: Polygon в EPSG:4326, образец внутри полигона", not badg, "", badg[:3], n=len(zones))
    rd = [abs(x["rel_diff"]) for x in bada]
    ck.add(qk, "zones.area_km2_vs_polygon", not bada,
           f"{len(bada)}/{len(zones)} зон: area_km2 (растровая полоса) vs геодез. площадь полигона, "
           f"макс. {max(rd) * 100 if rd else 0:.1f}%", sorted(bada, key=lambda x: -abs(x["rel_diff"]))[:2],
           level="fail" if rd and max(rd) > 0.1 else "warn", n=len(zones))

    z_csv = c.get("/api/v3/export", params={"layer": "zones", "format": "csv", "query_id": qid})
    z_gj = c.get("/api/v3/export", params={"layer": "zones", "format": "geojson", "query_id": qid}).json()
    zrows = csv_text(z_csv.content)
    ok_ids = [r["zone_id"] for r in zrows] == zid and [f["id"] for f in z_gj["features"]] == zid
    ck.add(qk, "export.zones_query_id", ok_ids,
           f"run.zones={len(zid)}, export csv={len(zrows)}, geojson={z_gj.get('count')}; "
           + json.dumps(diff_ids([r["zone_id"] for r in zrows], zid)), n=len(zid))
    if not ok_ids:
        info["export_zones_mismatch"] = {"run": len(zid), "export_csv": len(zrows), "export_geojson": z_gj["count"]}
    zby = {z["id"]: z for z in zones}
    zgj = {f["id"]: f for f in z_gj["features"]}
    badc = []
    for r in zrows:
        z = zby.get(r["zone_id"])
        if not z:
            continue
        p = z["properties"]
        b = bounds_of(z["geometry"])
        cen = [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2] if b else [None, None]
        probs = []
        if not near(r["area_km2"], p["area_km2"]):
            probs.append("area_km2")
        if not near(r["detected_area_m2"], p["detected_area_m2"]):
            probs.append("detected_area_m2")
        if r["status"] != p["status"] or r["detection_status"] != p["detection_status"] \
                or r["concentration_status"] != p["concentration_status"]:
            probs.append("status")
        if r["unit"] != src["meta"]["units"]["concentration"]:
            probs.append("unit")
        if r["concentration_items_km2"] != "" and p["concentration"] is None:
            probs.append("concentration")
        if not (near(r["centroid_lon"], cen[0], 2e-6) and near(r["centroid_lat"], cen[1], 2e-6)):
            probs.append("centroid")
        if r["linked_sample_ids"] != ";".join(p["support"]["linked_sample_ids"]) \
                or int(r["n_linked_samples"]) != p["support"]["n_linked_samples"]:
            probs.append("linked")
        if r["kind"] != "model_estimate":
            probs.append("kind")
        if r["zone_id"] in zgj and canon_hash(zgj[r["zone_id"]]) != canon_hash(z):
            probs.append("geojson!=json")
        if probs:
            badc.append({"id": r["zone_id"], "fields": probs})
    ck.add(qk, "export.zones csv/geojson numbers,statuses,centroid = json", not badc, "", badc[:3], n=len(zrows))

    # --- scenes of the run: n_zones and status vs zones
    zall = src["zones_all"]
    bads = []
    for s in scenes:
        nz = sum(1 for z in zall if z["properties"]["scene_id"] == s["scene_id"])
        if nz != s["n_zones"]:
            bads.append({"scene_id": s["scene_id"], "n_zones": s["n_zones"], "zones_with_scene": nz})
        if s["bounds"] and not coords_ok(s["footprint"]):
            bads.append({"scene_id": s["scene_id"], "bad_footprint": True})
    ck.add(qk, "scenes.n_zones/footprint = zones", not bads, "", bads[:3], n=len(scenes))

    # --- zone vs pair/scene status (a zone with a detector verdict should sit on an accepted pair)
    pairs_idx = src["pairs_by_sample_scene"]
    scene_st = {s["scene_id"]: s["status"] for s in src["scenes_all"]}
    badzp = []
    for z in zones:
        p = z["properties"]
        if p["detection_status"] in ("detected", "not_detected"):
            pst = {pairs_idx.get((s, p["scene_id"])) for s in p["support"]["linked_sample_ids"]}
            flagged = (p.get("pair_status") == "rejected" and "rejected" in pst
                       and "pair_rejected_drift" in ((p.get("quality") or {}).get("flags") or []))
            if not flagged and ("accepted" not in pst or scene_st.get(p["scene_id"]) != "accepted"):
                badzp.append({"zone": z["id"], "detection_status": p["detection_status"],
                              "pair_status": sorted(x for x in pst if x), "scene_status": scene_st.get(p["scene_id"])})
    ck.add(qk, "zone_vs_pair.status", not badzp, "зона с вердиктом детектора на отклонённой паре/снимке",
           badzp[:2], n=len(zones))
    # observation.linked_scenes must contain the scenes of its zones
    badl = []
    for z in zones:
        p = z["properties"]
        for s in p["support"]["linked_sample_ids"]:
            f = by.get(s)
            if f and p["detection_status"] != "insufficient_data" and p["scene_id"] not in f["properties"]["linked_scenes"]:
                badl.append({"sample_id": s, "zone": z["id"], "scene": p["scene_id"],
                             "linked_scenes": f["properties"]["linked_scenes"]})
    ck.add(qk, "obs.linked_scenes_vs_zones", not badl, "", badl[:2], n=len(zones))

    # --- pairs: candidates.csv (algorithm) = /pairs JSON = export CSV = export GeoJSON
    if q.get("sources"):
        pp = {"source": ",".join(q["sources"])}
        for k in ("date_from", "date_to"):
            if q.get(k):
                pp[k] = q[k]
        pj = c.get("/api/v3/pairs", params=pp).json()["pairs"]
        pc = csv_text(c.get("/api/v3/export", params={"layer": "pairs", "format": "csv", "query_id": qid}).content)
        pg = c.get("/api/v3/export", params={"layer": "pairs", "format": "geojson", "query_id": qid}).json()
        pids = [p["pair_id"] for p in pj]
        ck.add(qk, "pairs.ids json=export csv=geojson", [r["pair_id"] for r in pc] == pids
               and [f["id"] for f in pg["features"]] == pids, json.dumps(diff_ids([r["pair_id"] for r in pc], pids)),
               n=len(pids))
        info["n_pairs"] = len(pids)
        info["n_pairs_accepted"] = sum(1 for p in pj if p["status"] == "accepted")
        # candidates -> expected
        cand = src["cand_by_key"]
        pqd = {(r["event_id"], r["scene_id"]): r["decision"] for r in pq}
        bad_dt, bad_drift, bad_st, bad_rr, bad_csv = [], [], [], [], []
        pcb = {r["pair_id"]: r for r in pc}
        for p in pj:
            cr = cand.get((p["event_id"], p["scene_id"] or ""))
            if cr is None:
                continue
            if not near(p["dt_hours"], None if fnum(cr["dt_hours"]) is None else round(fnum(cr["dt_hours"]), 2), 0.005):
                bad_dt.append({"pair_id": p["pair_id"], "api": p["dt_hours"], "candidates": cr["dt_hours"]})
            if not near(p["drift_shift_km"], fnum(cr.get("drift_shift_km")), 0.005):
                bad_drift.append({"pair_id": p["pair_id"], "api": p["drift_shift_km"],
                                  "candidates": cr.get("drift_shift_km"), "tolerance_km": cr.get("tolerance_km")})
            exp_acc = truthy(cr["accept"]) and pqd.get((p["event_id"], p["scene_id"]), "accept") == "accept"
            if (p["status"] == "accepted") != exp_acc:
                bad_st.append({"pair_id": p["pair_id"], "api": p["status"], "candidates.accept": cr["accept"]})
            if p["status"] == "rejected" and not p["reject_reasons"]:
                bad_rr.append({"pair_id": p["pair_id"], "registry_note": p["registry_note"]})
            r = pcb.get(p["pair_id"])
            if r and (not near(r["dt_hours"], p["dt_hours"]) or r["status"] != p["status"]
                      or r["reject_reasons"] != ";".join(p["reject_reasons"])
                      or not near(r["drift_shift_km"], p["drift_shift_km"])):
                bad_csv.append(p["pair_id"])
        ck.add(qk, "pairs.dt_hours algo(candidates)=api", not bad_dt, "", bad_dt[:2], n=len(pj))
        ck.add(qk, "pairs.drift_shift_km", not bad_drift,
               f"{len(bad_drift)} пар: в candidates.csv есть drift_shift_km, в API null", bad_drift[:2], n=len(pj))
        ck.add(qk, "pairs.status algo(candidates+pair_quality)=api", not bad_st, "", bad_st[:2], n=len(pj))
        ck.add(qk, "pairs.reject_reasons_nonempty", not bad_rr, f"{len(bad_rr)} отклонённых пар без кода причины",
               bad_rr[:2], n=len(pj))
        ck.add(qk, "export.pairs csv = json (dt, drift, status, reasons)", not bad_csv, "", bad_csv[:3], n=len(pc))
        badpg = [p["pair_id"] for p in pj if p["geometry"] and not coords_ok(p["geometry"])]
        ck.add(qk, "pairs.geometry EPSG:4326", not badpg, "", badpg[:3], n=len(pj))

    # --- export determinism (same bytes twice)
    b1 = c.get("/api/v3/export", params={"layer": "zones", "format": "csv", "query_id": qid}).content
    ck.add(qk, "export.repeat same bytes", b1 == z_csv.content, "")
    return info


# ------------------------------------------------------------------ metrics (global)
def check_metrics(c, ck: Checker, src: dict) -> dict:
    m = c.get("/api/v3/metrics").json()
    m2 = c.get("/api/v3/metrics").json()
    ck.add("metrics", "metrics.sha256_repeat", canon_hash(m) == canon_hash(m2), "")
    det = src["det_metrics"]
    lt = src["lgbm_test"]
    main = (m.get("detector") or {}).get("main") or {}
    base = (m.get("detector") or {}).get("baseline") or {}
    dv = (det.get("val") or {})
    dtest = (det.get("test") or {})
    for lab, blk, key in (("main", main, "lgbm"), ("baseline", base, "rf_argmax"),
                          ("fdi", (m.get("detector") or {}).get("fdi") or {}, "fdi_threshold")):
        ref = dtest.get(key) or {}
        okk = all(near(blk.get(a), round(ref[b], 4), 1e-4) for a, b in (("precision", "precision_md"),
                  ("recall", "recall_md"), ("f1", "f1_md"), ("iou", "iou_md")) if ref.get(b) is not None) \
            and blk.get("ci95_f1") == ref.get("ci95_f1") and blk.get("split") == "test"
        ck.add("metrics", f"detector.{lab} P/R/F1/IoU + CI = case_detector test {key}", okk,
               f"api f1 {blk.get('f1')} vs {ref.get('f1_md')}")
    ck.add("metrics", "detector.main.val_f1 = case_detector val lgbm", near(main.get("val_f1"),
           round(dv.get("lgbm", {}).get("f1_md", float("nan")), 4), 1e-4),
           f"api {main.get('val_f1')} vs {dv.get('lgbm', {}).get('f1_md')}")
    ck.add("metrics", "detector.main.test_f1 = lgbm_final_test = case_detector test",
           near(main.get("test_f1"), round(lt["test_md"]["f1_md"], 4), 1e-4)
           and near(lt["test_md"]["f1_md"], (det.get("test") or {}).get("lgbm", {}).get("f1_md")),
           f"api {main.get('test_f1')} vs {lt['test_md']['f1_md']}")
    ck.add("metrics", "detector.main.test_iou = lgbm_final_test", near(main.get("test_iou"),
           round(lt["test_md"]["iou_md"], 4), 1e-4), f"api {main.get('test_iou')}")
    ck.add("metrics", "detector.baseline = RandomForest (MARIDA) on the same test",
           str(base.get("name", "")).startswith("RandomForest"), f"api {base.get('name')}")
    cm = src["conc_metrics"]
    dcv = (src.get("dev_cv") or {}).get("profiles") or {}
    sel = src.get("conc_model_selected") or {}
    conc = m.get("concentration") or {}
    bad = []
    for prof, d in (conc.get("profiles") or {}).items():
        if "main_split" in d:  # 3.1c format: metrics.json (L60b)
            rows = {(r["split"], r["model"]): r for r in cm[prof]["overall"]}
            ms = cm[prof].get("main_split")
            if d["main_split"] != ms:
                bad.append({"profile": prof, "api_split": d["main_split"], "metrics.json": ms})
            bm = rows.get((ms, "median"))
            if bm and not near(d["baseline"]["mae"], round(bm["mae"], 4), 1e-4):
                bad.append({"profile": prof, "baseline.mae": d["baseline"]["mae"], "metrics.json": bm["mae"]})
            mm = d.get("main") or {}
            r = rows.get((ms, mm.get("name")))
            if r and not near(mm.get("mae"), round(r["mae"], 4), 1e-4):
                bad.append({"profile": prof, "main.mae": mm.get("mae"), "metrics.json": r["mae"]})
            continue
        # L68 format: reports/case_conc/dev_cv.json + configs/case_conc_model.yaml
        tab = {r["model"]: r for r in (dcv.get(prof) or {}).get("table") or []}
        want_main = (sel.get(prof) or {}).get("model") or (dcv.get(prof) or {}).get("primary")
        mm, bb = d.get("main") or {}, d.get("baseline") or {}
        if mm.get("name") != want_main:
            bad.append({"profile": prof, "main.name": mm.get("name"), "case_conc_model.yaml": want_main})
        for lab, blk, mod in (("baseline", bb, "median"), ("main", mm, want_main)):
            r = tab.get(mod)
            if not r:
                bad.append({"profile": prof, lab: "нет строки в dev_cv.json", "model": mod})
                continue
            for af, jf in (("mae", "mae"), ("mae_log", "log1p_mae"), ("coverage", "coverage90"), ("rmse", "rmse")):
                if not near(blk.get(af), round(r[jf], 4) if fnum(r[jf]) is not None else None, 1e-4):
                    bad.append({"profile": prof, f"{lab}.{af}": blk.get(af), "dev_cv.json": r[jf]})
            if blk.get("n") != r.get("n"):
                bad.append({"profile": prof, f"{lab}.n": blk.get("n"), "dev_cv.json": r.get("n")})
        r = tab.get(want_main) or {}
        if want_main != "median" and r and not (near((mm.get("delta_mae_ci95") or [None])[0], round(r["d_mae_lo"], 4),
                                                     1e-4) and near(mm["delta_mae_ci95"][1], round(r["d_mae_hi"], 4), 1e-4)):
            bad.append({"profile": prof, "delta_mae_ci95": mm.get("delta_mae_ci95"),
                        "dev_cv.json": [r.get("d_mae_lo"), r.get("d_mae_hi")]})
        ydc = (sel.get(prof) or {}).get("dev_cv") or {}
        if ydc and not near(ydc.get("mae"), mm.get("mae"), 0.01):
            bad.append({"profile": prof, "yaml.dev_cv.mae": ydc.get("mae"), "api.main.mae": mm.get("mae")})
    ck.add("metrics", "concentration baseline/main MAE, coverage, CI = dev_cv.json / case_conc_model.yaml "
           "(или metrics.json main_split)", not bad, f"профилей {len(conc.get('profiles') or {})}", bad[:3])
    ck.add("metrics", "concentration.unit = meta.units", conc.get("unit") == src["meta"]["units"]["concentration"],
           str(conc.get("unit")))
    ce = m.get("control_example") or {}
    ck.add("metrics", "control_example 12 шт / 0.20 км² = 60", near(ce.get("computed"), 60.0)
           and near(ce.get("expected"), 60.0), json.dumps(ce))
    return {"detector_main": main, "detector_baseline": base, "concentration_profile": conc.get("profile"),
            "concentration_baseline": conc.get("baseline"), "concentration_main": conc.get("main")}


# ------------------------------------------------------------------ invalid / empty inputs
INVALID = [
    ("GET", "/api/v3/observations?bbox=1,2,3", 400, "BAD_BBOX"),
    ("GET", "/api/v3/observations?bbox=abc,1,2,3", 400, "BAD_BBOX"),
    ("GET", "/api/v3/zones?bbox=10,0,5,1", 400, "BAD_BBOX"),
    ("GET", "/api/v3/scenes?bbox=0,0,200,1", 400, "BAD_BBOX"),
    ("GET", "/api/v3/observations?date_from=2016-12-31&date_to=2016-01-01", 400, "BAD_DATE"),
    ("GET", "/api/v3/zones?date_from=2016-12-31&date_to=2016-01-01", 400, "BAD_DATE"),
    ("GET", "/api/v3/pairs?date_from=2016-13-01", 400, "BAD_DATE"),
    ("GET", "/api/v3/export?layer=zones&date_from=2016-12-31&date_to=2016-01-01", 400, "BAD_DATE"),
    ("GET", "/api/v3/zones?status=foo", 400, "BAD_PARAM"),
    ("GET", "/api/v3/zones?detection_status=maybe", 400, "BAD_PARAM"),
    ("GET", "/api/v3/zones?concentration_status=guess", 400, "BAD_PARAM"),
    ("GET", "/api/v3/observations?profile=XYZ", 400, "BAD_PARAM"),
    ("GET", "/api/v3/observations?source=S9_MARS", 400, "BAD_PARAM"),
    ("GET", "/api/v3/observations?scope=everything", 400, "BAD_PARAM"),
    ("GET", "/api/v3/observations?limit=0", 400, "BAD_PARAM"),
    ("GET", "/api/v3/pairs?status=maybe", 400, "BAD_PARAM"),
    ("GET", "/api/v3/pairs?max_dt_hours=-1", 400, "BAD_PARAM"),
    ("GET", "/api/v3/export?layer=foo", 400, "BAD_PARAM"),
    ("GET", "/api/v3/export", 400, "BAD_PARAM"),
    ("GET", "/api/v3/export?layer=zones&format=xml", 400, "BAD_PARAM"),
    ("GET", "/api/v3/observations/MPL-99999", 404, "NOT_FOUND"),
    ("GET", "/api/v3/zones/Z-nope", 404, "NOT_FOUND"),
    ("GET", "/api/v3/scenes/NOPE_SCENE", 404, "NOT_FOUND"),
    ("GET", "/api/v3/scenes/NOPE_SCENE/quality.png", 404, "NO_SCENE"),
    ("GET", "/api/v3/scenes/NOPE_SCENE/rgb.png", 404, "NO_SCENE"),
    ("GET", "/api/v3/queries/q_nope", 404, "NOT_FOUND"),
    ("GET", "/api/v3/queries/q_nope/run", 404, "NOT_FOUND"),
    ("DELETE", "/api/v3/queries/q_nope", 404, "NOT_FOUND"),
    ("GET", "/api/v3/export?layer=zones&query_id=q_nope", 404, "NOT_FOUND"),
    ("GET", "/api/v3/no_such_endpoint", 404, "NOT_FOUND"),
    ("POST", ("/api/v3/queries", "not json"), 400, "BAD_PARAM"),
    ("POST", ("/api/v3/queries", {"query": {}}), 400, "BAD_PARAM"),
    ("POST", ("/api/v3/queries", {"name": "x", "query": BAD_QUERY["query"]}), 400, "BAD_DATE"),
    ("POST", ("/api/v3/queries", {"name": "x", "query": {"bbox": "1,2,3,4"}}), 400, "BAD_BBOX"),
    ("POST", ("/api/v3/queries", {"name": "x", "query": {"bbox": [1, 2, 3]}}), 400, "BAD_BBOX"),
    ("POST", ("/api/v3/queries", {"name": "x", "query": {"statuses": ["foo"]}}), 400, "BAD_PARAM"),
    ("POST", ("/api/v3/queries", {"name": "x", "query": {"profiles": ["XYZ"]}}), 400, "BAD_PARAM"),
    ("POST", ("/api/v3/queries", {"name": "x", "query": {"scope": ["total_plastic"]}}), 422, "BAD_PARAM"),
]
EMPTY = [
    ("/api/v3/observations?bbox=-30,-50,-29,-49", "features"),
    ("/api/v3/zones?bbox=-30,-50,-29,-49", "features"),
    ("/api/v3/scenes?date_from=2030-01-01", "scenes"),
    ("/api/v3/pairs?sample_id=MPL-99999", "pairs"),
    ("/api/v3/zones?status=research_estimate", "features"),
    ("/api/v3/export?layer=zones&format=geojson&bbox=-30,-50,-29,-49", "features"),
    ("/api/v3/export?layer=observations&format=csv&bbox=-30,-50,-29,-49", "csv"),
    ("/api/v3/export?layer=pairs&format=csv&sample_id=MPL-99999", "csv"),
]


def check_invalid(c, ck: Checker) -> list[dict]:
    out = []
    for method, url, want, code in INVALID:
        if method == "POST":
            u, body = url
            r = c.post(u, content=body.encode()) if isinstance(body, str) else c.post(u, json=body)
            url = f"{u} {json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body}"
        elif method == "DELETE":
            r = c.delete(url)
        else:
            r = c.get(url)
        try:
            j = r.json()
            got, msg = j["error"]["code"], j["error"]["message"]
        except Exception:
            got, msg = None, r.text[:80]
        ok = r.status_code == want and got == code and bool(msg) and r.status_code < 500
        ck.add("invalid", f"{method} {url}", ok, f"{r.status_code} {got}: {msg}")
        out.append({"method": method, "url": url, "status": r.status_code, "code": got, "message": msg,
                    "expected": f"{want} {code}", "ok": ok})
    for url, key in EMPTY:
        r = c.get(url)
        if key == "csv":
            rows = csv_text(r.content) if r.status_code == 200 else None
            ok = r.status_code == 200 and rows == [] and len(r.content.decode("utf-8-sig").splitlines()) == 1
            msg = "только заголовок CSV" if ok else r.text[:80]
        else:
            j = r.json()
            ok = r.status_code == 200 and j.get(key) == [] and j.get("count") == 0 and bool(j.get("empty_reason"))
            msg = j.get("empty_reason")
        ck.add("empty", f"GET {url}", ok, f"{r.status_code}: {msg}")
        out.append({"method": "GET", "url": url, "status": r.status_code, "code": None, "message": msg,
                    "expected": "200 + пустой список + empty_reason", "ok": ok})
    return out


# ------------------------------------------------------------------ speed
def endpoints(ids: dict) -> list[tuple[str, str, str]]:
    """(name, method, url) — one per /api/v3 endpoint; 'scene' class gets the 3 s target."""
    q, s, z, sid = ids["query_id"], ids["scene_id"], ids["zone_id"], ids["sample_id"]
    return [
        ("GET /meta", "GET", "/api/v3/meta"),
        ("GET /observations (все 935)", "GET", "/api/v3/observations"),
        ("GET /observations?source=S2", "GET", "/api/v3/observations?source=S2_SARGASSO_MSM41"),
        ("GET /observations/{id}", "GET", f"/api/v3/observations/{sid}"),
        ("GET /pairs (все)", "GET", "/api/v3/pairs"),
        ("GET /pairs?status=accepted", "GET", "/api/v3/pairs?status=accepted"),
        ("GET /scenes", "GET", "/api/v3/scenes"),
        ("GET /scenes/{id}", "GET", f"/api/v3/scenes/{s}"),
        ("GET /scenes/{id}/rgb.png", "GET", f"/api/v3/scenes/{s}/rgb.png"),
        ("GET /scenes/{id}/quality.png", "GET", f"/api/v3/scenes/{s}/quality.png"),
        ("GET /scenes/{id}/mask.png", "GET", f"/api/v3/scenes/{s}/mask.png"),
        ("GET /zones", "GET", "/api/v3/zones"),
        ("GET /zones/{id}", "GET", f"/api/v3/zones/{z}"),
        ("GET /metrics", "GET", "/api/v3/metrics"),
        ("GET /export observations csv", "GET", "/api/v3/export?layer=observations&format=csv"),
        ("GET /export zones geojson", "GET", "/api/v3/export?layer=zones&format=geojson"),
        ("GET /export pairs csv", "GET", "/api/v3/export?layer=pairs&format=csv"),
        ("GET /export zones csv query_id", "GET", f"/api/v3/export?layer=zones&format=csv&query_id={q}"),
        ("GET /queries", "GET", "/api/v3/queries"),
        ("GET /queries/{id}", "GET", f"/api/v3/queries/{q}"),
        ("GET /queries/{id}/run", "GET", f"/api/v3/queries/{q}/run"),
        ("POST /queries", "POST", "/api/v3/queries"),
        ("DELETE /queries/{id}", "DELETE", "/api/v3/queries/{new}"),
        ("GET /observations?bbox=1,2,3 (400)", "GET", "/api/v3/observations?bbox=1,2,3"),
    ]


def is_scene(name: str) -> bool:
    return name.startswith("GET /scenes/{id}")


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def time_endpoints(call, ids: dict, n: int) -> list[dict]:
    """call(method, url, json_body) -> (status, bytes). Returns rows with p50/p95 in ms (1 warm-up excluded)."""
    out = []
    body = {"name": "L67 speed", "query": {"sources": ["S4_BLACK_SEA_DOORS3"]}}
    for name, method, url in endpoints(ids):
        ts, codes, size, cold = [], Counter(), 0, None
        for i in range(n + 1):
            u = url
            if method == "DELETE":
                st, b = call("POST", "/api/v3/queries", body)
                u = url.replace("{new}", json.loads(b)["query_id"])
            t0 = time.perf_counter()
            st, b = call(method, u, body if method == "POST" else None)
            ms = (time.perf_counter() - t0) * 1000
            if i == 0:
                cold = ms
                continue
            ts.append(ms)
            codes[st] += 1
            size = len(b)
        target = 3000 if is_scene(name) else 300
        out.append({"endpoint": name, "n": len(ts), "cold_ms": round(cold, 1), "p50_ms": round(pct(ts, .5), 1),
                    "p95_ms": round(pct(ts, .95), 1), "max_ms": round(max(ts), 1), "target_ms": target,
                    "ok": pct(ts, .95) < target, "status": dict(codes), "bytes": size})
    return out


def free_port(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def run_live(port: int, data_root: str, qfile: Path, ids_fn, n: int, log: Path) -> dict:
    if not free_port(port):
        return {"error": f"порт {port} занят — живой замер пропущен"}
    code = ("import sys; from pathlib import Path; sys.path.insert(0, r'%s'); from service import case_store as cs; "
            "cs.PATHS['queries'] = Path(r'%s'); from service.__main__ import main; "
            "main(['--port', '%d', '--data-root', r'%s'])" % (ROOT, qfile, port, data_root))
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": "", "PYTHONIOENCODING": "utf-8"}
    lf = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=str(ROOT), stdout=lf, stderr=subprocess.STDOUT, env=env)
    base = f"http://127.0.0.1:{port}"

    def call(method, url, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + url, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
    try:
        t0 = time.time()
        while time.time() - t0 < 90:
            try:
                if call("GET", "/health")[0] == 200:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            return {"error": "живой экземпляр не поднялся за 90 с", "log": str(log)}
        startup = time.time() - t0
        st, b = call("POST", "/api/v3/queries", {"name": "L67 live", "query": {"sources": ["S4_BLACK_SEA_DOORS3"]}})
        ids = ids_fn(json.loads(b)["query_id"])
        rows = time_endpoints(call, ids, n)
        # live = TestClient on the same query (numbers identical)
        st, b1 = call("GET", f"/api/v3/queries/{ids['query_id']}/run")
        st, b2 = call("GET", f"/api/v3/queries/{ids['query_id']}/run")
        return {"base": base, "startup_s": round(startup, 1), "rows": rows,
                "run_hash": [canon_hash(json.loads(b1)), canon_hash(json.loads(b2))], "log": str(log)}
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        lf.close()


# ------------------------------------------------------------------ UI (placeholder, L66)
def ui_compare(ui_url: Optional[str], zone_ids: list[str], api_zones: dict) -> dict:
    """Сравнение карточки зоны в UI с /api/v3/zones/{id} (Playwright). ЗАГОТОВКА.

    TODO(L66): когда фронт получит режим кейса —
      1) playwright.sync_api: открыть f"{ui_url}/?mode=case&zone={zid}" для каждого zid;
      2) прочитать из карточки data-атрибуты (data-zone-id, data-area-km2, data-detection-status,
         data-concentration-status, data-unit) — договориться с L66 о селекторах;
      3) сравнить с api_zones[zid]["properties"] теми же допусками, что и экспорт (near(), статусы строго);
      4) скриншот карточки в reports/selfcheck/ui_<zid>.png.
    Пакет playwright в .venv не ставим сами — через оркестратора."""
    if not ui_url:
        return {"status": "skipped", "reason": "--ui-url не задан"}
    return {"status": "not_implemented", "ui_url": ui_url, "zones": zone_ids[:3],
            "reason": "режим кейса фронта (L66) ещё не готов; см. TODO в ui_compare()"}


# ------------------------------------------------------------------ main
def load_sources(cs) -> dict:
    samples = read_csv(SAMPLES)
    cand = read_csv(CANDIDATES)
    return {
        "samples": samples, "samples_by_id": {r["sample_id"]: r for r in samples},
        "cand_by_key": {(r["event_id"], r.get("item_id") or ""): r for r in cand},
        "pair_quality": read_csv(PAIR_QUALITY),
        "conc_metrics": json.loads(CONC_METRICS.read_text(encoding="utf-8")) if CONC_METRICS.is_file() else {},
        "predictions": read_csv(PREDICTIONS),
        "det_metrics": json.loads(DET_METRICS.read_text(encoding="utf-8")) if DET_METRICS.is_file() else {},
        "lgbm_test": json.loads(LGBM_TEST.read_text(encoding="utf-8")) if LGBM_TEST.is_file() else {},
        "dev_cv": json.loads(DEV_CV.read_text(encoding="utf-8")) if DEV_CV.is_file() else {},
        "conc_model_selected": _yaml(CONC_MODEL_YAML).get("selected") or {},
        "zones_all": cs.zones_all(), "scenes_all": cs.scenes_all(),
        "pairs_by_sample_scene": {(p["sample_id"], p["scene_id"]): p["status"] for p in cs.pairs_all()},
        "meta": cs.meta(),
    }


def run_all(n_speed: int = 20, live: bool = True, port: int = 8091, data_root: str = "service/data",
            ui_url: Optional[str] = None, out_dir: Optional[Path] = None, write: bool = True) -> dict:
    from fastapi.testclient import TestClient
    from service import case_store as cs
    from service.app import create_app

    real_sha = file_sha(REAL_QUERIES)
    tmp = Path(tempfile.mkdtemp(prefix="l67_"))
    old_q = cs.PATHS["queries"]
    cs.PATHS["queries"] = tmp / "queries.jsonl"  # isolation: saved queries never touch service/labels
    ck = Checker()
    res: dict[str, Any] = {"when": dt.datetime.now().isoformat(timespec="seconds"),
                           "isolation": f"case_store.PATHS['queries'] -> {tmp / 'queries.jsonl'}"}
    t_start = time.time()
    try:
        c = TestClient(create_app())
        src = load_sources(cs)
        res["sources_mtime"] = {str(p.relative_to(ROOT)): dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(
            timespec="seconds") for p in (SAMPLES, CANDIDATES, PAIR_QUALITY, CONC_METRICS, PREDICTIONS, DET_METRICS,
                                          LGBM_TEST, DEV_CV, CONC_MODEL_YAML) if p.is_file()}
        # saved queries
        qinfo = []
        for spec in QUERIES:
            r = c.post("/api/v3/queries", json={"name": spec["name"], "query": spec["query"]})
            ck.add(spec["key"], "POST /queries 201", r.status_code == 201, f"{r.status_code}")
            rec = r.json()
            ck.add(spec["key"], "GET /queries/{id} = POST", c.get(f"/api/v3/queries/{rec['query_id']}").json() == rec, "")
            qinfo.append(check_query(c, ck, spec, src, rec))
        r = c.post("/api/v3/queries", json={"name": BAD_QUERY["name"], "query": BAD_QUERY["query"]})
        ck.add(BAD_QUERY["key"], "POST даты задом наперёд -> 400 BAD_DATE, не сохранён",
               r.status_code == 400 and r.json()["error"]["code"] == "BAD_DATE"
               and len(c.get("/api/v3/queries").json()["queries"]) == len(QUERIES),
               f"{r.status_code} {r.text[:120]}")
        qinfo.append({"key": BAD_QUERY["key"], "name": BAD_QUERY["name"], "query": BAD_QUERY["query"],
                      "response": {"status": r.status_code, **(r.json() if r.headers.get("content-type", "").startswith(
                          "application/json") else {})}})
        res["queries"] = qinfo
        res["metrics"] = check_metrics(c, ck, src)
        res["invalid_inputs"] = check_invalid(c, ck)
        # UI placeholder
        res["ui"] = ui_compare(ui_url, [z["id"] for z in src["zones_all"]], {z["id"]: z for z in src["zones_all"]})

        # speed (TestClient)
        z0 = next((z for z in src["zones_all"] if z["properties"]["detection_status"] == "detected"),
                  src["zones_all"][0] if src["zones_all"] else None)
        s0 = z0["properties"]["scene_id"] if z0 else "NONE"
        ids_fn = lambda qid: {"query_id": qid, "scene_id": s0, "zone_id": z0["id"] if z0 else "Z-none",  # noqa: E731
                              "sample_id": (z0["properties"]["support"]["linked_sample_ids"] or ["MPL-0001"])[0]
                              if z0 else "MPL-0001"}
        ids = ids_fn(qinfo[1]["query_id"])

        def tc_call(method, url, body=None):
            r = c.request(method, url, json=body) if body is not None else c.request(method, url)
            return r.status_code, r.content
        res["speed_testclient"] = time_endpoints(tc_call, ids, n_speed)
        if live:
            log = (out_dir or ROOT / "reports" / "selfcheck") / "live_8091.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            res["speed_live"] = run_live(port, data_root, tmp / "queries_live.jsonl", ids_fn, n_speed, log)
            lv = res["speed_live"]
            if "run_hash" in lv:
                ck.add("live", "live run sha256 repeat", lv["run_hash"][0] == lv["run_hash"][1], lv["run_hash"][0][:16])
        for key in ("speed_testclient", "speed_live"):
            rows = res.get(key)
            rows = rows.get("rows") if isinstance(rows, dict) else rows
            for row in rows or []:
                ck.add("speed", f"{key}: {row['endpoint']} p95<{row['target_ms']}ms", row["ok"],
                       f"p50 {row['p50_ms']} / p95 {row['p95_ms']} ms", level="warn")
    finally:
        cs.PATHS["queries"] = old_q
    ck.add("isolation", "service/labels/queries.jsonl не изменён", file_sha(REAL_QUERIES) == real_sha,
           f"sha256 {str(real_sha)[:16]}")
    res["checks"] = ck.rows
    res["stats"] = dict(ck.counts())
    res["seconds"] = round(time.time() - t_start, 1)
    res["findings"] = findings(ck.rows)
    if write:
        od = out_dir or ROOT / "reports" / "selfcheck"
        od.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
        (od / f"consistency_{stamp}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str),
                                                     encoding="utf-8")
        (od / f"consistency_{stamp}.md").write_text(to_md(res), encoding="utf-8")
        res["out"] = [str(od / f"consistency_{stamp}.json"), str(od / f"consistency_{stamp}.md")]
    return res


def findings(rows: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for r in rows:
        if r["status"] != "fail" and not (r["status"] == "warn" and r["check"] in OWNERS):
            continue
        k = r["check"]
        own = OWNERS.get(k, ("?", "?", "?"))
        f = out.setdefault(k, {"check": k, "level": r["status"], "queries": [], "details": [], "examples": [],
                               "file": own[0], "field": own[1], "expected": own[2]})
        if r["status"] == "fail":
            f["level"] = "fail"
        f["queries"].append(r["query"])
        f["details"].append(f"{r['query']}: {r['detail']}" if r["detail"] else r["query"])
        if r["example"]:
            f["examples"].append(r["example"][0] if isinstance(r["example"], list) else r["example"])
    return list(out.values())


def to_md(res: dict) -> str:
    L = [f"# L67 — согласованность API v3 ({res['when']})", "",
         f"Проверок: {sum(res['stats'].values())}; " + ", ".join(f"{k}: {v}" for k, v in sorted(res["stats"].items()))
         + f"; {res['seconds']} с. Изоляция: {res['isolation']}.", "",
         "## Запросы", "", "| запрос | query_id | набл. | зон | сцен | пар (принято) | ok | fail | SHA-256 run |",
         "|---|---|---|---|---|---|---|---|---|"]
    for q in res["queries"]:
        rows = [r for r in res["checks"] if r["query"] == q["key"]]
        cnt = Counter(r["status"] for r in rows)
        L.append(f"| {q['name']} | {q.get('query_id', '—')} | {q.get('n_obs', '—')} | {q.get('n_zones', '—')} | "
                 f"{q.get('n_scenes', '—')} | {q.get('n_pairs', '—')} ({q.get('n_pairs_accepted', '—')}) | "
                 f"{cnt.get('ok', 0)} | {cnt.get('fail', 0)} | {str(q.get('sha256_run', '—'))[:12]} |")
    L += ["", "## Расхождения (для владельцев)", ""]
    if not res["findings"]:
        L.append("Нет.")
    for f in res["findings"]:
        L += [f"- [{f['level']}] **{f['check']}** — " + "; ".join(f["details"])[:700],
              f"  - файл: `{f['file']}`; поле: {f['field']}", f"  - ожидаемое: {f['expected']}",
              f"  - примеры: `{json.dumps(f['examples'][:3], ensure_ascii=False)[:500]}`"]
    L += ["", "## Все проверки", "", "| запрос | проверка | итог | детали |", "|---|---|---|---|"]
    for r in res["checks"]:
        if r["query"] in ("speed",):
            continue
        d = (r["detail"] or "") + (f" пример: {json.dumps(r['example'], ensure_ascii=False)[:160]}"
                                   if r["status"] != "ok" and r["example"] else "")
        L.append(f"| {r['query']} | {r['check'].replace('|', '/')} | {r['status']} | {d.replace('|', '/')[:260]} |")
    for key, title in (("speed_testclient", "Скорость: TestClient"), ("speed_live", "Скорость: живой экземпляр")):
        v = res.get(key)
        if v is None:
            continue
        rows = v.get("rows") if isinstance(v, dict) else v
        L += ["", f"## {title}", ""]
        if isinstance(v, dict):
            L.append(f"{v.get('base', '')} старт {v.get('startup_s', '?')} с. {v.get('error', '')}")
            L.append("")
        L += ["| эндпоинт | n | холодный, мс | p50, мс | p95, мс | max, мс | цель | ок | байт |",
              "|---|---|---|---|---|---|---|---|---|"]
        for r in rows or []:
            L.append(f"| {r['endpoint']} | {r['n']} | {r['cold_ms']} | {r['p50_ms']} | {r['p95_ms']} | {r['max_ms']} | "
                     f"<{r['target_ms']} | {'да' if r['ok'] else 'НЕТ'} | {r['bytes']} |")
    L += ["", f"UI: {res['ui']}", ""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=20, help="запросов на эндпоинт для p50/p95")
    ap.add_argument("--no-live", action="store_true", help="не поднимать живой экземпляр")
    ap.add_argument("--port", type=int, default=8091)
    ap.add_argument("--data-root", default="service/data")
    ap.add_argument("--ui-url", default=None, help="URL фронта для сравнения карточки зоны (заготовка, TODO L66)")
    a = ap.parse_args(argv)
    res = run_all(a.n, not a.no_live, a.port, a.data_root, a.ui_url)
    print(json.dumps({"stats": res["stats"], "findings": [f["check"] for f in res["findings"]], "out": res.get("out"),
                      "seconds": res["seconds"]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
