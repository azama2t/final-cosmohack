"""API v3 (docs/CONTRACTS_V3.md): service/routes_v3.py + service/case_store.py."""
from __future__ import annotations

import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from service import case_store as cs
from service.app import create_app

HAS_SAMPLES = cs.PATHS["samples"].is_file()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setitem(cs.PATHS, "queries", tmp_path / "queries.jsonl")
    return TestClient(create_app())


@pytest.fixture()
def empty_client(tmp_path, monkeypatch):
    empty = tmp_path / "nothing"
    for k in list(cs.PATHS):
        monkeypatch.setitem(cs.PATHS, k, empty / k)
    monkeypatch.setitem(cs.PATHS, "queries", tmp_path / "queries.jsonl")
    return TestClient(create_app())


def _err(r, status, code):
    assert r.status_code == status, r.text
    body = r.json()
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert "details" in body["error"]


# ------------------------------------------------------------------ meta
def test_meta_contract(client):
    r = client.get("/api/v3/meta")
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "*"
    m = r.json()
    assert m["version"] == "3.0"
    assert m["units"]["concentration"] == "items/km2" and m["units"]["area"] == "km2"
    assert [s["id"] for s in m["statuses"]] == ["detected", "not_detected", "insufficient_data",
                                                "research_estimate", "concentration_unavailable"]
    assert {s["id"] for s in m["sources"]} == {"S1_GPGP2018", "S2_SARGASSO_MSM41", "S3_SE_NORTH_SEA",
                                               "S4_BLACK_SEA_DOORS3"}
    assert len(m["measurement_profiles"]) == 8
    assert {r["id"] for r in m["reject_reasons"]} >= {"NO_SCENE_IN_WINDOW", "CLOUD", "GLINT", "NODATA",
                                                      "LAND_OR_COAST", "DT_TOO_LARGE", "POSITION_UNCERTAIN",
                                                      "NOT_DENSITY", "SCOPE_MISMATCH"}
    if HAS_SAMPLES:
        assert sum(s["n"] for s in m["sources"]) == 935
        assert m["date_range"] == {"min": "2014-04-03", "max": "2024-06-18"}


# ------------------------------------------------------------------ observations
@pytest.mark.skipif(not HAS_SAMPLES, reason="нет task/macroplastic_marine_samples.csv")
def test_observations_all_and_filters(client):
    fc = client.get("/api/v3/observations").json()
    assert fc["type"] == "FeatureCollection" and fc["kind"] == "measurement"
    assert fc["count"] == 935 and fc["empty_reason"] is None
    ids = [f["id"] for f in fc["features"]]
    assert ids == sorted(ids)
    p = fc["features"][0]["properties"]
    assert p["kind"] == "measurement" and isinstance(p["quality_flags"], list)
    assert isinstance(p["linked_scenes"], list)

    s3 = client.get("/api/v3/observations", params={"source": "S3_SE_NORTH_SEA"}).json()
    assert s3["count"] > 0 and all(f["properties"]["source_id"] == "S3_SE_NORTH_SEA" for f in s3["features"])

    d = client.get("/api/v3/observations", params={"date_from": "2015-01-01", "date_to": "2015-12-31"}).json()
    assert d["count"] > 0 and all("2015-01-01" <= f["properties"]["date_utc"] <= "2015-12-31" for f in d["features"])

    b = client.get("/api/v3/observations", params={"bbox": "5,53,10,56"}).json()
    assert b["count"] > 0
    for f in b["features"]:
        lon, lat = f["geometry"]["coordinates"]
        assert 5 <= lon <= 10 and 53 <= lat <= 56

    it = client.get("/api/v3/observations", params={"record_type": "item_observation"}).json()
    assert all(f["properties"]["record_type"] == "item_observation" for f in it["features"])

    none = client.get("/api/v3/observations", params={"date_from": "2030-01-01"}).json()
    assert none["count"] == 0 and none["features"] == [] and none["empty_reason"]

    lim = client.get("/api/v3/observations", params={"limit": 3}).json()
    assert lim["count"] == 3


@pytest.mark.skipif(not HAS_SAMPLES, reason="нет task/macroplastic_marine_samples.csv")
def test_observation_one_and_404(client):
    r = client.get("/api/v3/observations/MPL-0001")
    assert r.status_code == 200
    f = r.json()
    assert f["id"] == "MPL-0001" and isinstance(f["pairs"], list)
    for p in f["pairs"]:
        assert p["sample_id"] == "MPL-0001" and p["status"] in ("accepted", "rejected")
    _err(client.get("/api/v3/observations/NOPE-1"), 404, "NOT_FOUND")


@pytest.mark.skipif(not HAS_SAMPLES, reason="нет task/macroplastic_marine_samples.csv")
def test_deterministic(client):
    for u in ("/api/v3/observations?source=S2_SARGASSO_MSM41", "/api/v3/pairs", "/api/v3/zones", "/api/v3/scenes"):
        assert client.get(u).content == client.get(u).content


# ------------------------------------------------------------------ bad input
@pytest.mark.parametrize("url,status,code", [
    ("/api/v3/observations?bbox=1,2", 400, "BAD_BBOX"),
    ("/api/v3/observations?bbox=a,b,c,d", 400, "BAD_BBOX"),
    ("/api/v3/observations?bbox=10,0,5,1", 400, "BAD_BBOX"),
    ("/api/v3/observations?bbox=0,-100,1,1", 400, "BAD_BBOX"),
    ("/api/v3/observations?date_from=2020-13-01", 400, "BAD_DATE"),
    ("/api/v3/observations?date_from=2020-02-01&date_to=2020-01-01", 400, "BAD_DATE"),
    ("/api/v3/observations?source=XX", 400, "BAD_PARAM"),
    ("/api/v3/observations?record_type=foo", 400, "BAD_PARAM"),
    ("/api/v3/observations?limit=0", 400, "BAD_PARAM"),
    ("/api/v3/observations?geometry=poly", 400, "BAD_PARAM"),
    ("/api/v3/pairs?status=maybe", 400, "BAD_PARAM"),
    ("/api/v3/pairs?max_dt_hours=-1", 400, "BAD_PARAM"),
    ("/api/v3/zones?status=bogus", 400, "BAD_PARAM"),
    ("/api/v3/scenes?mission=Sentinel-9", 400, "BAD_PARAM"),
    ("/api/v3/export?layer=h3", 400, "BAD_PARAM"),
    ("/api/v3/export", 400, "BAD_PARAM"),
    ("/api/v3/export?layer=zones&format=xls", 400, "BAD_PARAM"),
    ("/api/v3/export?layer=zones&query_id=q_nope", 404, "NOT_FOUND"),
    ("/api/v3/zones/Z-nope", 404, "NOT_FOUND"),
    ("/api/v3/scenes/nope", 404, "NOT_FOUND"),
    ("/api/v3/no_such_endpoint", 404, "NOT_FOUND"),
])
def test_bad_inputs(client, url, status, code):
    _err(client.get(url), status, code)


# ------------------------------------------------------------------ empty data
def test_empty_data(empty_client):
    c = empty_client
    m = c.get("/api/v3/meta").json()
    assert m["date_range"] == {"min": None, "max": None} and all(s["n"] == 0 for s in m["sources"])
    for url, key in (("/api/v3/observations", "features"), ("/api/v3/pairs", "pairs"), ("/api/v3/scenes", "scenes"),
                     ("/api/v3/zones", "features")):
        r = c.get(url)
        assert r.status_code == 200, url
        body = r.json()
        assert body["count"] == 0 and body[key] == [] and body["empty_reason"], url
    r = c.get("/api/v3/metrics")
    assert r.status_code == 200 and r.json()["empty_reason"]
    assert r.json()["control_example"]["computed"] == 60.0
    for layer in ("observations", "pairs", "zones"):
        for fmt in ("csv", "geojson"):
            assert c.get("/api/v3/export", params={"layer": layer, "format": fmt}).status_code == 200
    _err(c.get("/api/v3/observations/MPL-0001"), 404, "NOT_FOUND")


# ------------------------------------------------------------------ export consistency
@pytest.mark.skipif(not HAS_SAMPLES, reason="нет task/macroplastic_marine_samples.csv")
def test_export_csv_matches_observations(client):
    params = {"source": "S2_SARGASSO_MSM41", "date_from": "2015-01-01"}
    fc = client.get("/api/v3/observations", params=params).json()
    r = client.get("/api/v3/export", params={**params, "layer": "observations", "format": "csv"})
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"] and ".csv" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert len(rows) == fc["count"] > 0
    by_id = {f["id"]: f["properties"] for f in fc["features"]}
    for row in rows:
        p = by_id[row["sample_id"]]
        assert row["kind"] == "measurement"
        v = row["concentration_items_km2"]
        assert (p["concentration_items_km2"] is None) if v == "" else (float(v) == p["concentration_items_km2"])
        assert row["linked_scenes"] == ";".join(p["linked_scenes"])
    # original CSV columns first, in the same order
    with open(cs.PATHS["samples"], encoding="utf-8-sig") as f:
        header = next(csv.reader(f))
    assert list(rows[0].keys())[:len(header)] == header

    g = client.get("/api/v3/export", params={**params, "layer": "observations", "format": "geojson"})
    gj = json.loads(g.content)
    assert gj["count"] == fc["count"] and [f["id"] for f in gj["features"]] == [f["id"] for f in fc["features"]]


def test_export_zones_csv_columns(client):
    r = client.get("/api/v3/export", params={"layer": "zones", "format": "csv"})
    assert r.status_code == 200
    header = r.content.decode("utf-8-sig").splitlines()[0].split(",")
    assert header == cs.ZONE_COLS


def test_zones_no_invented_concentration(client):
    fc = client.get("/api/v3/zones").json()
    assert fc["kind"] == "model_estimate"
    for f in fc["features"]:
        p = f["properties"]
        assert p["kind"] == "model_estimate"
        assert p["concentration"] is None and p["concentration_status"] == "concentration_unavailable"
        assert p["status"] in cs.STATUS_IDS


# ------------------------------------------------------------------ saved queries
def test_queries_round_trip(client):
    q = {"bbox": [5, 53, 10, 56], "date_from": "2014-01-01", "date_to": "2016-12-31",
         "statuses": ["detected"], "sources": ["S3_SE_NORTH_SEA"], "profiles": [], "layers": ["observations", "zones"],
         "scene_id": None}
    r = client.post("/api/v3/queries", json={"name": "Северное море", "query": q})
    assert r.status_code == 201, r.text
    rec = r.json()
    qid = rec["query_id"]
    assert qid.startswith("q_") and rec["name"] == "Северное море" and rec["created_at"].endswith("Z")
    assert rec["query"]["bbox"] == [5.0, 53.0, 10.0, 56.0]

    lst = client.get("/api/v3/queries").json()["queries"]
    assert [x["query_id"] for x in lst] == [qid]

    run = client.get(f"/api/v3/queries/{qid}/run")
    assert run.status_code == 200
    body = run.json()
    assert body["query_id"] == qid and body["query"] == rec["query"]
    assert body["summary"]["n_obs"] == body["observations"]["count"]
    assert body["summary"]["n_zones"] == body["zones"]["count"]
    direct = client.get("/api/v3/observations", params={"bbox": "5,53,10,56", "date_from": "2014-01-01",
                                                        "date_to": "2016-12-31", "source": "S3_SE_NORTH_SEA"}).json()
    assert body["observations"]["count"] == direct["count"]
    exp = client.get("/api/v3/export", params={"layer": "observations", "format": "geojson", "query_id": qid})
    assert json.loads(exp.content)["count"] == direct["count"]

    assert client.delete(f"/api/v3/queries/{qid}").status_code == 204
    assert client.get("/api/v3/queries").json()["queries"] == []
    _err(client.get(f"/api/v3/queries/{qid}/run"), 404, "NOT_FOUND")
    _err(client.delete(f"/api/v3/queries/{qid}"), 404, "NOT_FOUND")


@pytest.mark.parametrize("body,code", [
    ({"query": {}}, "BAD_PARAM"),                                    # no name
    ({"name": "x", "query": {"bbox": [1, 2]}}, "BAD_BBOX"),
    ({"name": "x", "query": {"date_from": "2018-99-01"}}, "BAD_DATE"),
    ({"name": "x", "query": {"statuses": ["nope"]}}, "BAD_PARAM"),
    ({"name": "x", "query": {"foo": 1}}, "BAD_PARAM"),
    ({"name": "x", "query": "text"}, "BAD_PARAM"),
])
def test_queries_bad_body(client, body, code):
    _err(client.post("/api/v3/queries", json=body), 400, code)


def test_queries_not_json(client):
    _err(client.post("/api/v3/queries", content=b"not json", headers={"Content-Type": "application/json"}),
         400, "BAD_PARAM")


def test_cors_preflight(client):
    r = client.options("/api/v3/queries", headers={"Origin": "http://localhost:5173",
                                                   "Access-Control-Request-Method": "POST"})
    assert r.status_code in (200, 204) and r.headers["access-control-allow-origin"] == "*"


def test_metrics_shape(client):
    m = client.get("/api/v3/metrics").json()
    assert set(m) >= {"split", "detector", "concentration", "control_example"}
    assert m["concentration"]["unit"] == "items/km2"
    assert m["control_example"]["computed"] == 60.0
