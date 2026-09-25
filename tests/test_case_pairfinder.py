"""L71: поиск синхронной сцены для полевого наблюдения (src/macroplastic/case/pairfinder.py, POST /api/v3/pairfinder).
Сеть не нужна: синтетический поисковик или офлайн-кэш data/pairs/cache."""
from __future__ import annotations

import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from macroplastic.case import pairfinder as pf

FOOT = {"type": "Polygon", "coordinates": [[[29, 43], [30.5, 43], [30.5, 44], [29, 44], [29, 43]]]}
S4_T1 = {"lon": 29.6227, "lat": 43.60877}   # точка события S4:DOORS3:T1 (реестр пар, кэш STAC в репозитории)


def feat(i, platform, t, cloud=5.0, geom=FOOT, mgrs="35TQJ"):
    return {"id": i, "geometry": geom, "datetime": t, "platform": platform, "cloud": cloud, "mgrs": mgrs,
            "wrs_path": 180, "wrs_row": 30, "proc_level": None}


class FakeSearch:
    """Отдаёт фиксированные сцены по коллекции; считает вызовы (сети нет)."""

    def __init__(self, by_coll: dict):
        self.by_coll, self.calls, self.network_calls = by_coll, [], 0

    def __call__(self, url, coll, bbox, dt0, dt1):
        self.calls.append((url, coll, bbox, dt0, dt1))
        feats = [f for f in self.by_coll.get((url, coll), [])
                 if pd.Timestamp(dt0) <= pd.Timestamp(f["datetime"]) <= pd.Timestamp(dt1)]
        return "cache", feats


ES = "https://earth-search.aws.element84.com/v1"
PC = "https://planetarycomputer.microsoft.com/api/stac/v1"


# ---------------------------------------------------------------- rule
def test_sync_window_typical_3km_is_4_2_hours():
    assert pf.sync_window_hours(3.0, 0.2) == pytest.approx(4.1667, abs=1e-3)
    assert round(pf.sync_window_hours(3.0, 0.2), 1) == 4.2
    assert pf.shift_km(0.2, 4.1667) == pytest.approx(3.0, abs=1e-3)


def test_sync_window_in_response_default_request():
    cfg = pf.load_cfg()
    q = pf.parse_request({"geometry": {"lon": 10, "lat": 50}, "datetime": "2020-01-01T10:00:00Z",
                          "speed_ms": 0.2, "tolerance_km": 3}, cfg)
    res = pf.find(q, cfg, searcher=FakeSearch({}), now=pd.Timestamp("2026-09-25", tz="UTC"))
    assert res["sync_window"]["max_abs_dt_hours"] == pytest.approx(4.17, abs=0.01)
    assert res["count"] == 0 and res["empty_reason"]           # пусто -> причина, не ошибка
    by = pf.parse_request({"geometry": {"lon": 10, "lat": 50}, "datetime": "2020-01-01"}, cfg)
    assert by.scenario == "typical" and by.speed_ms == pytest.approx(0.2)
    assert by.tolerance_km == pytest.approx(3.005)             # ширина 10 м по умолчанию / 2 + 3 км (как L59b)


def test_cache_key_matches_find_pairs(tmp_path, monkeypatch):
    """Общий кэш: ключ pairfinder совпадает с find_pairs.stac_search (иначе офлайн не найдёт реестровые запросы)."""
    fp = pf.find_pairs_mod()
    monkeypatch.setattr(fp, "CACHE", tmp_path)
    bbox, dt0, dt1 = pf.search_args(29.6, 43.6, pd.Timestamp("2024-06-02T12:00:00Z"), 5.5)
    (tmp_path / f"{pf.cache_key(ES, 'sentinel-2-l2a', bbox, dt0, dt1)}.json").write_text(json.dumps([{"id": "X"}]))

    def boom(*a, **k):
        raise AssertionError("network must not be used")
    monkeypatch.setattr(fp._session, "post", boom)
    assert fp.stac_search(ES, "sentinel-2-l2a", bbox, dt0, dt1) == [{"id": "X"}]
    st, feats = pf.Searcher(offline=True)(ES, "sentinel-2-l2a", bbox, dt0, dt1)
    assert st == "cache" and feats == [{"id": "X"}]
    st, feats = pf.Searcher(offline=True)(ES, "sentinel-2-l1c", bbox, dt0, dt1)
    assert st == "cache_miss" and feats is None


# ---------------------------------------------------------------- decisions on synthetic scenes
def _run(body, scenes, now="2026-09-25"):
    cfg = pf.load_cfg()
    return pf.find(pf.parse_request(body, cfg), cfg, searcher=FakeSearch(scenes), now=pd.Timestamp(now, tz="UTC"))


def test_decisions_time_known():
    scenes = {(ES, "sentinel-2-l2a"): [feat("A_near", "sentinel-2a", "2024-06-02T08:58:00Z"),
                                       feat("B_far", "sentinel-2b", "2024-06-03T08:58:00Z", mgrs="35TQK")],
              (PC, "landsat-c2-l2"): [feat("C_out", "landsat-9", "2024-06-02T08:44:00Z",
                                           geom={"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]})]}
    res = _run({"geometry": {"type": "Point", "coordinates": [29.62, 43.61]}, "datetime": "2024-06-02T10:00:00Z"}, scenes)
    d = {c["scene_id"]: c for c in res["candidates"]}
    assert d["A_near"]["decision"] == "synchronous" and d["A_near"]["drift"]["shift_km_selected"] <= 3.005
    assert d["B_far"]["decision"] == "not_synchronous" and any(r.startswith("drift_too_large") for r in d["B_far"]["reasons"])
    assert d["C_out"]["decision"] == "reject" and "outside_footprint" in d["C_out"]["reasons"]
    assert res["best"] == "A_near" and res["n_synchronous"] == 1
    assert res["candidates"][0]["scene_id"] == "A_near"            # синхронные первыми
    assert set(d["A_near"]["drift"]["shift_km"]) == {"low", "typical", "high"}


def test_date_only_gives_time_window_and_worst_case():
    scenes = {(ES, "sentinel-2-l2a"): [feat("A", "sentinel-2a", "2024-06-02T08:58:00Z")]}
    res = _run({"geometry": {"lon": 29.62, "lat": 43.61}, "datetime": "2024-06-02"}, scenes)
    c = res["candidates"][0]
    assert res["query"]["time_known"] is False
    assert c["decision"] == "synchronous_if_time_in_window" and not c["synchronous"]
    assert c["drift"]["time_worst_case"] and c["drift"]["dt_drift_hours"] == pytest.approx(3.03 + 12, abs=0.01)
    # допуск 3.005 км -> 4.174 ч = 4 ч 10 мин 25 с вокруг пролёта 08:58; окно округлено внутрь до минуты
    assert c["sync_time_window"]["from"] == "2024-06-02T04:48:00Z" and c["sync_time_window"]["to"] == "2024-06-02T13:08:00Z"


def test_dedupe_same_acquisition_es_pc():
    scenes = {(ES, "sentinel-2-l2a"): [feat("ES_L2A", "sentinel-2a", "2024-06-02T08:58:16Z")],
              (ES, "sentinel-2-l1c"): [feat("ES_L1C", "sentinel-2a", "2024-06-02T08:58:16Z")],
              (PC, "sentinel-2-l2a"): [feat("PC_L2A", "Sentinel-2A", "2024-06-02T08:46:01Z", mgrs="MGRS-35TQJ")]}
    res = _run({"geometry": {"lon": 29.62, "lat": 43.61}, "datetime": "2024-06-02T09:00:00Z"}, scenes)
    assert res["count"] == 1
    assert res["candidates"][0]["scene_id"] == "ES_L2A" and len(res["candidates"][0]["also_available"]) == 2


def test_line_partial_footprint_and_speed():
    scenes = {(ES, "sentinel-2-l2a"): [feat("A", "sentinel-2a", "2024-06-02T09:00:00Z")]}
    body = {"geometry": {"type": "LineString", "coordinates": [[30.3, 43.5], [30.7, 43.5]]},
            "datetime": "2024-06-02T11:00:00Z", "speed_ms": 0.1, "tolerance_km": 1.0}
    res = _run(body, scenes)
    c = res["candidates"][0]
    assert 0.3 < c["footprint"]["geometry_fraction_inside"] < 0.7 and any(r.startswith("partial") for r in c["reasons"])
    assert res["sync_window"]["scenario"] == "custom"
    assert res["sync_window"]["max_abs_dt_hours"] == pytest.approx(1 / 0.36, abs=0.01)


def test_line_partial_footprint_sync_value():
    scenes = {(ES, "sentinel-2-l2a"): [feat("A", "sentinel-2a", "2024-06-02T09:00:00Z")]}
    body = {"geometry": {"lon_start": 30.3, "lat_start": 43.5, "lon_end": 30.7, "lat_end": 43.5},
            "datetime": "2024-06-02T11:00:00Z", "speed_ms": 0.1, "tolerance_km": 1.0}
    c = _run(body, scenes)["candidates"][0]
    assert c["drift"]["shift_km_selected"] == pytest.approx(0.72, abs=0.01)
    assert c["decision"] == "synchronous"


def test_forecast_for_future_date():
    scenes = {(ES, "sentinel-2-l2a"): [feat("S2A_past", "sentinel-2a", "2026-09-20T08:58:00Z")],
              (PC, "landsat-c2-l2"): [feat("L9_past", "landsat-9", "2026-09-12T08:44:00Z")]}
    res = _run({"geometry": {"lon": 29.62, "lat": 43.61}, "datetime": "2026-10-10"}, scenes, now="2026-09-25")
    assert res["query"]["mode"] == "forecast"
    t = {c["scene_datetime"] for c in res["candidates"]}
    assert "2026-10-10T08:58:00Z" in t                              # 20.09 + 2 × 10 сут
    assert "2026-10-14T08:44:00Z" in t                              # 12.09 + 2 × 16 сут (в окне ±5.5 сут)
    assert all(c["predicted"] for c in res["candidates"])


# ---------------------------------------------------------------- input validation
@pytest.mark.parametrize("body,code", [
    ({"datetime": "2024-06-02"}, "BAD_GEOMETRY"),
    ({"geometry": {"lon": 200, "lat": 10}, "datetime": "2024-06-02"}, "BAD_GEOMETRY"),
    ({"geometry": {"type": "Polygon", "coordinates": []}, "datetime": "2024-06-02"}, "BAD_GEOMETRY"),
    ({"geometry": {"lon": 10, "lat": 10}}, "BAD_DATE"),
    ({"geometry": {"lon": 10, "lat": 10}, "datetime": "02.06.2024"}, "BAD_DATE"),
    ({"geometry": {"lon": 10, "lat": 10}, "datetime": "2024-06-02", "window_days": 99}, "BAD_PARAM"),
    ({"geometry": {"lon": 10, "lat": 10}, "datetime": "2024-06-02", "speed_ms": -1}, "BAD_PARAM"),
    ({"geometry": {"lon": 10, "lat": 10}, "datetime": "2024-06-02", "speed_ms": 0}, "BAD_PARAM"),
    ({"geometry": {"lon": 10, "lat": 10}, "datetime": "2024-06-02", "drift_scenario": "storm"}, "BAD_PARAM"),
    ({"geometry": {"lon": 10, "lat": 10}, "datetime": "2024-06-02", "drift_scenario": "low", "speed_ms": 0.1}, "BAD_PARAM"),
    ({"geometry": {"lon": 10, "lat": 10}, "datetime": "2024-06-02", "tolerance_km": "abc"}, "BAD_PARAM"),
    ({"geometry": {"lon": 10, "lat": 10}, "datetime": "2024-06-02", "quality": "yes"}, "BAD_PARAM"),
    ({"geometry": {"lon": 10, "lat": 10}, "datetime": "2024-06-02", "foo": 1}, "BAD_PARAM"),
])
def test_bad_input_422(body, code):
    with pytest.raises(pf.PairfinderError) as e:
        pf.parse_request(body)
    assert e.value.status == 422 and e.value.code == code and e.value.message


# ---------------------------------------------------------------- API (offline, cache of the registry)
@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv(pf.OFFLINE_ENV, "1")
    from service.app import create_app
    return TestClient(create_app())


def test_api_bad_json_400(client):
    r = client.post("/api/v3/pairfinder", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "BAD_PARAM"
    r = client.post("/api/v3/pairfinder", content=b"")
    assert r.status_code == 400
    r = client.post("/api/v3/pairfinder", json=[1, 2])
    assert r.status_code == 400 and r.json()["error"]["message"]


def test_api_bad_values_422(client):
    r = client.post("/api/v3/pairfinder", json={"geometry": {"lon": 10, "lat": 95}, "datetime": "2024-06-02"})
    assert r.status_code == 422
    e = r.json()["error"]
    assert e["code"] == "BAD_GEOMETRY" and "lat" in e["message"]
    assert r.headers["access-control-allow-origin"] == "*"


@pytest.mark.skipif(not (pf.ROOT / "data" / "pairs" / "cache").is_dir(), reason="нет кэша STAC")
def test_api_offline_registry_event_s4(client):
    """S4:DOORS3:T1 (дата без времени): пролёты 2 июня ~08:45–09:00 UTC -> условно синхронны, если трансекта
    в окне ~04:45–13:10 UTC. Ответ берётся из кэша репозитория, сеть не нужна."""
    r = client.post("/api/v3/pairfinder", json={"geometry": S4_T1, "datetime": "2024-06-02", "quality": True})
    assert r.status_code == 200, r.text
    js = r.json()
    assert js["query"]["offline"] is True and js["search"]["network_calls"] == 0
    assert all(s["status"] == "cache" for s in js["search"]["sources"])
    assert js["n_synchronous"] == 0 and js["n_synchronous_if_time_in_window"] >= 2
    best = js["candidates"][0]
    assert best["scene_datetime"].startswith("2024-06-02") and best["decision"] == "synchronous_if_time_in_window"
    assert best["quality"]["status"] in ("ok", "unavailable")      # офлайн: из кэша масок или «недоступно»


def test_api_offline_cache_miss_is_empty_200(client):
    r = client.post("/api/v3/pairfinder", json={"geometry": {"lon": -150.123, "lat": 30.456}, "datetime": "2019-07-01"})
    assert r.status_code == 200
    js = r.json()
    assert js["count"] == 0 and js["candidates"] == [] and "кэш" in js["empty_reason"]
