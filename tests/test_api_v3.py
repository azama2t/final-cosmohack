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
    assert header[:len(cs.ZONE_COLS)] == cs.ZONE_COLS  # contract order, 3.1 columns appended
    assert header[len(cs.ZONE_COLS):] == ["detection_status", "concentration_status", "field_estimate_items_km2"]


def test_zones_no_invented_concentration(client):
    fc = client.get("/api/v3/zones").json()
    assert fc["kind"] == "model_estimate"
    for f in fc["features"]:
        p = f["properties"]
        assert p["kind"] == "model_estimate"
        assert p["concentration"] is None and p["concentration_status"] == "unavailable"
        assert p["status"] == p["detection_status"] in ("detected", "not_detected", "insufficient_data")
        if p["support"].get("field_target_scope") == "all_litter":  # team rule: all_litter is never "plastic"
            assert "не только пластик" in p["support"]["field_scope_label"]
        fe = p["field_estimate"]
        assert fe is None or (fe["unit"] == "items/km2" and fe["label"] == "оценка по полевым данным, не по снимку")


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


# ------------------------------------------------------------------ contract 3.1
CAND_HEADER = ("event_id,source_id,lat,lon,obs_datetime,time_known,time_note,point_from,endpoint,collection,level,"
               "mission,item_id,scene_datetime,dt_hours,cloud_cover,tile,point_inside_footprint,accept,reject_reason\n")


def test_meta_31(client):
    m = client.get("/api/v3/meta").json()
    assert [q["id"] for q in m["quality_classes"]] == ["valid", "cloud", "shadow", "glint", "land", "nodata"]
    for q in m["quality_classes"]:
        assert q["label"] and len(q["color"]) == 9 and q["color"].startswith("#")
    assert [s["id"] for s in m["detection_statuses"]] == ["detected", "not_detected", "insufficient_data"]
    assert [s["id"] for s in m["concentration_statuses"]] == ["measured_nearby", "research_estimate", "unavailable"]
    for s in m["detection_statuses"] + m["concentration_statuses"]:
        assert s["label"] and s["color"].startswith("#")
    sdr = m["scene_date_range"]
    if cs.candidates_raw() and HAS_SAMPLES:
        assert sdr["min"] <= sdr["max"] and len(sdr["min"]) == 10
    else:
        assert sdr is None


def test_meta_31_no_scenes(empty_client):
    m = empty_client.get("/api/v3/meta").json()
    assert m["scene_date_range"] is None
    assert len(m["quality_classes"]) == 6 and len(m["concentration_statuses"]) == 3


@pytest.mark.skipif(not HAS_SAMPLES, reason="нет task/macroplastic_marine_samples.csv")
def test_scene_date_range_from_registry(tmp_path, monkeypatch, client):
    d = tmp_path / "pairs"
    d.mkdir()
    ev = "S3:HE419_MarLitter_transect01,S3_SE_NORTH_SEA,54.08,7.66,2014-04-03 15:19:30+00:00,True,,,"
    (d / "candidates.csv").write_text(
        CAND_HEADER
        + ev + "earth-search,sentinel-2-l2a,L2A,S2A,S2A_X_20140405,2014-04-05 10:00:00+00:00,42.7,5,,True,False,dt>1d\n"
        + ev + "planetary-computer,landsat-c2-l2,L2SP,L8,LC08_X_20140402,2014-04-02 10:00:00+00:00,-29.3,5,,True,True,\n",
        encoding="utf-8")
    monkeypatch.setitem(cs.PATHS, "pairs_dir", d)
    m = client.get("/api/v3/meta").json()
    assert m["scene_date_range"] == {"min": "2014-04-02", "max": "2014-04-05"}
    pr = client.get("/api/v3/pairs").json()["pairs"]
    assert {p["status"] for p in pr} == {"accepted", "rejected"}
    assert all(p["reject_reasons"] == ["DT_TOO_LARGE"] for p in pr if p["status"] == "rejected")
    z = client.get("/api/v3/zones").json()
    assert z["count"] == 0 and z["empty_reason"]


def test_zone_status_filters(client):
    for st in ("detected", "not_detected", "insufficient_data"):
        fc = client.get("/api/v3/zones", params={"detection_status": st}).json()
        assert all(f["properties"]["detection_status"] == st for f in fc["features"])
    allz = client.get("/api/v3/zones").json()["count"]
    un = client.get("/api/v3/zones", params={"concentration_status": "unavailable"}).json()["count"]
    old = client.get("/api/v3/zones", params={"status": "concentration_unavailable"}).json()["count"]
    assert un == old == allz
    assert client.get("/api/v3/zones", params={"concentration_status": "research_estimate"}).json()["count"] == 0
    _err(client.get("/api/v3/zones?detection_status=research_estimate"), 400, "BAD_PARAM")
    _err(client.get("/api/v3/zones?concentration_status=concentration_unavailable"), 400, "BAD_PARAM")


@pytest.mark.skipif(not HAS_SAMPLES, reason="нет task/macroplastic_marine_samples.csv")
def test_field_estimate_lookup(tmp_path, monkeypatch):
    conc = tmp_path / "case_conc"
    conc.mkdir()
    (conc / "predictions.csv").write_text(
        "profile,split,fold,sample_id,event_id,group,latitude,longitude,y_true,field_lower,field_upper,model,y_pred\n"
        "S2_visual_total_plastic,event,0,MPL-0200,e,g,30,-69,15.5,1.8,56.1,median,38.8\n"
        "S2_visual_total_plastic,event,0,MPL-0200,e,g,30,-69,15.5,1.8,56.1,knn5,21.25\n", encoding="utf-8")
    monkeypatch.setitem(cs.PATHS, "conc_metrics", conc / "metrics.json")
    fe = cs.field_estimate(["MPL-0200"])
    assert fe["value"] == 21.25 and fe["unit"] == "items/km2" and fe["basis"] == "field_model"
    assert fe["measurement_profile"] == "S2_visual_GT2"
    assert fe["label"] == "оценка по полевым данным, не по снимку"
    assert fe["lo"] is None and fe["hi"] is None  # no prediction interval computed -> no invented bounds
    assert cs.field_estimate(["MPL-0001"]) is None and cs.field_estimate([]) is None


@pytest.mark.skipif(not HAS_SAMPLES, reason="нет task/macroplastic_marine_samples.csv")
def test_all_litter_not_called_plastic_and_items_not_density(client):
    m = client.get("/api/v3/meta").json()
    lab = {s["id"]: s["label"] for s in m["target_scopes"]}
    assert "не только пластик" in lab["all_litter"]
    fc = client.get("/api/v3/observations", params={"scope": "all_litter"}).json()
    assert fc["count"] > 0
    for f in fc["features"]:
        p = f["properties"]
        assert p["is_plastic_scope"] is False and "не только пластик" in p["target_scope_label"]
    items = client.get("/api/v3/observations", params={"record_type": "item_observation"}).json()
    assert items["count"] > 0
    assert all(f["properties"]["concentration_items_km2"] is None and f["properties"]["concentration_g_km2"] is None
               for f in items["features"])


def test_quality_png_rgba_palette(tmp_path, monkeypatch, client):
    np = pytest.importorskip("numpy")
    rasterio = pytest.importorskip("rasterio")
    from PIL import Image
    d = tmp_path / "pairs"
    q = d / "quality" / "S4_T"
    q.mkdir(parents=True)
    a = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.uint8)
    with rasterio.open(q / "quality.tif", "w", driver="GTiff", width=3, height=2, count=1, dtype="uint8") as ds:
        ds.write(a, 1)
    (d / "pair_quality.csv").write_text("event_id,sample_ids,scene_id,dir,decision,reason\nS4:T,,SCN_1,S4_T,reject,cloud\n",
                                        encoding="utf-8")
    monkeypatch.setitem(cs.PATHS, "pairs_dir", d)
    r = client.get("/api/v3/scenes/SCN_1/quality.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(r.content))
    assert img.mode == "RGBA"
    colors = {c["id"]: c["color"] for c in client.get("/api/v3/meta").json()["quality_classes"]}

    def hx(px):
        return "#" + "".join(f"{v:02x}" for v in px)
    assert hx(img.getpixel((1, 0))) == colors["valid"]
    assert hx(img.getpixel((2, 0))) == colors["land"]
    assert hx(img.getpixel((0, 1))) == hx(img.getpixel((1, 1))) == colors["cloud"]
    assert hx(img.getpixel((0, 0))) == hx(img.getpixel((2, 1))) == colors["nodata"]
    sc = client.get("/api/v3/scenes/SCN_1").json()
    assert sc["quality_url"] == "/api/v3/scenes/SCN_1/quality.png"
    _err(client.get("/api/v3/scenes/SCN_2/quality.png"), 404, "NO_SCENE")
