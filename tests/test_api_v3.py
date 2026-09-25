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
    assert header[len(cs.ZONE_COLS):] == cs.ZONE_COLS_31[len(cs.ZONE_COLS):]
    assert header[len(cs.ZONE_COLS):len(cs.ZONE_COLS) + 3] == ["detection_status", "concentration_status",
                                                                "field_estimate_items_km2"]


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
        assert fe is None or (fe["unit"] == "items/km2" and fe["label"].startswith("оценка по полевым данным, не по снимку"))


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
    ({"name": "x", "query": "text"}, "BAD_PARAM"),
])
def test_queries_bad_body(client, body, code):
    _err(client.post("/api/v3/queries", json=body), 400, code)


@pytest.mark.parametrize("body,unknown", [({"name": "x", "query": {"foo": 1}}, ["foo"]),
                                          ({"name": "x", "query": {"scope": ["total_plastic"], "zzz": 1}},
                                           ["scope", "zzz"]),
                                          ({"name": "x", "query": {}, "extra": 1}, ["extra"])])
def test_queries_unknown_fields_422(client, body, unknown):
    r = client.post("/api/v3/queries", json=body)
    _err(r, 422, "BAD_PARAM")
    assert r.json()["error"]["details"]["unknown"] == unknown and r.json()["error"]["details"]["allowed"]


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


# ------------------------------------------------------------------ L68 field concentration model (L62d)
HAS_CONC_MODEL = cs.PATHS["conc_model_cfg"].is_file() and cs.PATHS["dev_cv"].is_file()


@pytest.mark.skipif(not HAS_CONC_MODEL, reason="нет configs/case_conc_model.yaml / reports/case_conc/dev_cv.json")
def test_metrics_from_l68_dev_cv(client, tmp_path, monkeypatch):
    monkeypatch.setitem(cs.PATHS, "final_test", tmp_path / "final_test.json")  # not computed yet
    c = client.get("/api/v3/metrics").json()["concentration"]
    sel = cs.selected_models()
    for prof, pr in c["profiles"].items():
        assert pr["main"]["name"] == sel[prof]["model"]          # main model = configs/case_conc_model.yaml
        assert pr["baseline"]["name"].startswith("median")
        lo, hi = pr["main"]["delta_mae_ci95"]
        assert lo <= pr["main"]["delta_mae_vs_median"] <= hi
        cov = pr["main"]["coverage"]
        assert pr["main"]["coverage_label"] == f"≈{round(cov * 100)} % по CV"  # actual coverage, not "90 %"
        assert pr["strength"]
    if "S2_visual_total_plastic" in c["profiles"]:
        s2 = c["profiles"]["S2_visual_total_plastic"]
        assert s2["main"]["name"] == "ridge_log" and "слабый выигрыш / зависит от разбиения" in s2["strength"]
        assert s2["main"]["coverage_label"] == "≈84 % по CV"
    assert c["final_test"] is None and c["final_test_status"] == "будет посчитан один раз в приёмке"
    assert c["split"]["method"] == "route"


def test_metrics_final_test_present(client, tmp_path, monkeypatch):
    ft = tmp_path / "final_test.json"
    ft.write_text(json.dumps({"S2_visual_total_plastic": {"mae": 1.0}}), encoding="utf-8")
    monkeypatch.setitem(cs.PATHS, "final_test", ft)
    c = client.get("/api/v3/metrics").json()["concentration"]
    assert c["final_test"] == {"S2_visual_total_plastic": {"mae": 1.0}} and c["final_test_status"].startswith("посчитан один раз")


def test_metrics_no_l68_files(empty_client):
    c = empty_client.get("/api/v3/metrics").json()["concentration"]
    assert c["profiles"] == {} and c["main"] is None and c["final_test"] is None


@pytest.mark.skipif(not (HAS_CONC_MODEL and HAS_SAMPLES), reason="нет файлов модели L68 или CSV")
def test_field_estimate_in_and_out_of_domain(tmp_path, monkeypatch):
    monkeypatch.setitem(cs.PATHS, "final_test", tmp_path / "none.json")  # model branch (before the test decision)
    s2 = cs.field_estimate_at(-69.83, 30.07, "2015-04-02T12:00:00Z")      # Sargasso, S2 profile area
    assert s2["model"] == cs.selected_models()["S2_visual_total_plastic"]["model"]
    assert s2["unit"] == "items/km2" and s2["basis"] == "field_model"
    assert s2["label"] == "оценка по полевым данным, не по снимку"
    assert 0 <= s2["lo"] <= s2["value"] <= s2["hi"]
    assert s2["interval"].endswith("% по CV") and "90" not in s2["interval"]
    assert s2 == cs.field_estimate_at(-69.83, 30.07, "2015-04-02T12:00:00Z")  # deterministic
    s1 = cs.field_estimate_at(-140.0, 32.0, "2015-08-02T12:00:00Z")      # GPGP, S1 profile area
    assert s1["profile_config"] == "S1_trawl_total_plastic" and s1["model"] == "knn5_log"
    assert cs.field_estimate_at(7.6, 54.0, "2015-08-02T12:00:00Z") is None   # North Sea: outside every profile
    assert cs.field_estimate_at(None, None, None) is None
    for f in cs.zones_all():  # current zones are S3/S4 -> outside the field model areas
        assert f["properties"]["field_estimate"] is None


@pytest.mark.skipif(not (HAS_CONC_MODEL and HAS_SAMPLES), reason="нет файлов модели L68 или CSV")
def test_field_estimate_matches_weights_ridge(tmp_path, monkeypatch):
    """Ridge prediction = expm1(z·coef + b) from the weights JSON; interval from yaml q_lo/q_hi."""
    import math
    monkeypatch.setitem(cs.PATHS, "final_test", tmp_path / "none.json")
    w = cs.conc_weights("S2_visual_total_plastic")
    if (w.get("model") or {}).get("name") != "ridge_log":
        pytest.skip("S2 main model is not ridge_log")
    fe = cs.field_estimate_at(-65.0, 28.0, "2015-04-10T15:00:00Z")
    ly = cs._predict_log1p(w, -65.0, 28.0, "2015-04-10T15:00:00Z")
    q = cs.selected_models()["S2_visual_total_plastic"]["interval"]
    assert fe["value"] == round(math.expm1(ly), 2)
    assert fe["hi"] == round(math.expm1(ly + q["q_hi"]), 2)


def test_field_estimate_without_model_files(tmp_path, monkeypatch):
    monkeypatch.setitem(cs.PATHS, "conc_model_cfg", tmp_path / "none.yaml")
    assert cs.field_estimate_at(-69.83, 30.07, "2015-04-02T12:00:00Z") is None


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


# ------------------------------------------------------------------ L62e (L67 consistency findings)
HAS_PAIRS = (cs.PATHS["pairs_dir"] / "candidates.csv").is_file()


def test_meta_has_drift_and_time_codes(client):
    ids = {r["id"] for r in client.get("/api/v3/meta").json()["reject_reasons"]}
    assert {"DRIFT_TOO_LARGE", "TIME_UNKNOWN"} <= ids


@pytest.mark.skipif(not (HAS_PAIRS and HAS_SAMPLES), reason="нет реестра пар")
def test_pairs_drift_fields_and_codes(client):
    pairs = client.get("/api/v3/pairs").json()["pairs"]
    assert all(p["reject_reasons"] for p in pairs if p["status"] == "rejected")
    cand = {}
    for r in csv.DictReader(open(cs.PATHS["pairs_dir"] / "candidates.csv", encoding="utf-8-sig")):
        cand.setdefault((r["event_id"], r.get("item_id") or ""), r)
    n = 0
    for p in pairs:
        r = cand.get((p["event_id"], p["scene_id"] or ""))
        if r and r.get("drift_shift_km"):
            n += 1
            assert abs(p["drift_shift_km"] - float(r["drift_shift_km"])) < 0.001
            assert abs(p["tolerance_km"] - float(r["tolerance_km"])) < 0.001
            if "sync_unreliable_drift" in (r.get("reject_reason") or ""):
                assert "DRIFT_TOO_LARGE" in p["reject_reasons"]
        assert p["status_without_drift"] in ("accepted", "rejected")
    if "drift_shift_km" in next(iter(cand.values()), {}):
        assert n > 0


def test_zone_area_is_polygon_area_and_pair_status(client):
    for f in client.get("/api/v3/zones").json()["features"]:
        p = f["properties"]
        ga = cs.geodesic_area_km2(f["geometry"])
        assert ga is None or abs(p["area_km2"] - ga) <= 1e-3 * max(ga, 1e-6) + 1e-4
        assert "strip_area_raster_km2" in p
        assert p["concentration_status"] == "unavailable" and p["support"]["linked_sample_ids"]
        if p["pair_status"] == "rejected" and "DRIFT_TOO_LARGE" in p["pair_reject_reasons"]:
            assert "pair_rejected_drift" in p["quality"]["flags"] and p["pair_sync"] == "unsynchronized"


@pytest.mark.skipif(not HAS_SAMPLES, reason="нет task/macroplastic_marine_samples.csv")
def test_zones_source_filter_and_export_equal_run(client):
    z = client.get("/api/v3/zones", params={"source": "S4_BLACK_SEA_DOORS3"}).json()["features"]
    for f in z:
        srcs = {(cs.sample_row(s) or {}).get("source_id") for s in f["properties"]["support"]["linked_sample_ids"]}
        assert "S4_BLACK_SEA_DOORS3" in srcs
    qid = client.post("/api/v3/queries", json={"name": "t", "query": {"sources": ["S4_BLACK_SEA_DOORS3"]}}).json()["query_id"]
    run = client.get(f"/api/v3/queries/{qid}/run").json()
    ex = client.get("/api/v3/export", params={"layer": "zones", "format": "geojson", "query_id": qid}).json()
    assert [f["id"] for f in ex["features"]] == [f["id"] for f in run["zones"]["features"]] == [f["id"] for f in z]
    # a zone's scene is among linked_scenes of its samples (unsynchronized ones listed separately)
    obs = {f["id"]: f["properties"] for f in run["observations"]["features"]}
    for f in run["zones"]["features"]:
        p = f["properties"]
        for s in p["support"]["linked_sample_ids"]:
            if s in obs and p["scene_id"]:
                assert p["scene_id"] in obs[s]["linked_scenes"]
                if p["pair_status"] == "rejected":
                    assert p["scene_id"] in obs[s]["linked_scenes_unsynced"]


@pytest.mark.skipif(not HAS_SAMPLES, reason="нет task/macroplastic_marine_samples.csv")
def test_query_with_scopes(client):
    r = client.post("/api/v3/queries", json={"name": "S2 пластик", "query": {"sources": ["S2_SARGASSO_MSM41"],
                                                                             "scopes": ["total_plastic"]}})
    assert r.status_code == 201 and r.json()["query"]["scopes"] == ["total_plastic"]
    qid = r.json()["query_id"]
    run = client.get(f"/api/v3/queries/{qid}/run").json()
    assert run["summary"]["n_obs"] == 63
    assert all(f["properties"]["target_scope"] == "total_plastic" for f in run["observations"]["features"])
    ex = client.get("/api/v3/export", params={"layer": "observations", "format": "geojson", "query_id": qid}).json()
    assert ex["count"] == 63
    _err(client.post("/api/v3/queries", json={"name": "x", "query": {"scopes": ["plastic"]}}), 400, "BAD_PARAM")


# ------------------------------------------------------------------ L62f
@pytest.mark.skipif(not HAS_SAMPLES, reason="нет task/macroplastic_marine_samples.csv")
def test_observation_source_license_and_poisson_ci(client):
    f = client.get("/api/v3/observations/MPL-0200").json()["properties"]
    assert f["source_license"] and f["source_short"] and f["source_doi"]
    if f["n_items"] is not None:
        from src.macroplastic.case.concentration import concentration as conc_fn
        res = conc_fn(f["n_items"], f["sampled_area_km2"])
        assert f["ci95_lo"] == round(res.lower, 4) and f["ci95_hi"] == round(res.upper, 4)
        assert f["ci95_lo"] <= f["concentration_items_km2"] <= f["ci95_hi"] and f["ci95_reason"] is None
    items = client.get("/api/v3/observations", params={"record_type": "item_observation", "limit": 5}).json()
    for x in items["features"]:
        p = x["properties"]
        assert p["ci95_lo"] is None and p["ci95_hi"] is None and p["ci95_reason"]
    s4 = client.get("/api/v3/observations", params={"source": "S4_BLACK_SEA_DOORS3"}).json()["features"]
    assert all(x["properties"]["ci95_lo"] is None and x["properties"]["ci95_reason"] for x in s4)  # no N in S4


@pytest.mark.skipif(not (HAS_SAMPLES and HAS_CONC_MODEL), reason="нет CSV или модели L68")
def test_observation_model_estimate_from_dev_predictions(client):
    rows = list(csv.DictReader(open(cs.PATHS["dev_cv"].parent / "dev_predictions.csv", encoding="utf-8-sig")))
    main = {k: v["model"] for k, v in cs.selected_models().items()}
    r = next(x for x in rows if x["profile"] == "S2_visual_total_plastic" and x["model"] == main[x["profile"]])
    me = client.get(f"/api/v3/observations/{r['sample_id']}").json()["properties"]["model_estimate"]
    assert me["model"] == main["S2_visual_total_plastic"] and "прогноз по CV вне обучающего участка" in me["note"]
    assert abs(me["value"] - float(r["y_pred"])) < 1e-3 and abs(me["lo"] - float(r["lo"])) < 1e-3
    assert me["fold"] == int(r["fold"])
    s4 = client.get("/api/v3/observations", params={"source": "S4_BLACK_SEA_DOORS3"}).json()["features"]
    assert all(x["properties"]["model_estimate"] is None for x in s4)


def test_metrics_detector_same_test_rows(client):
    d = client.get("/api/v3/metrics").json()["detector"]
    if not cs.PATHS["det_metrics"].is_file():
        pytest.skip("нет reports/case_detector/metrics.json")
    assert [r["name"] for r in d["rows"]][:2] == ["LightGBM", "RandomForest (код MARIDA)"]
    assert {r["split"] for r in d["rows"]} == {"test"}
    for r in d["rows"]:
        for k in ("precision", "recall", "f1", "iou"):
            assert r[k] is not None and len(r[f"ci95_{k}"]) == 2
    assert d["main"]["f1"] > d["baseline"]["f1"] > d["fdi"]["f1"]
    box = d["fdi_ndvi"]
    ref = json.loads(cs.PATHS["det_metrics"].read_text(encoding="utf-8"))["test"]["fdi_ndvi_box"]
    assert "NDVI" in box["name"] and box["split"] == "test" and box in d["rows"]
    assert box["f1"] == round(ref["f1_md"], 4) and box["ci95_f1"] == ref["ci95_f1"]
    assert box["precision"] is not None and box["recall"] is not None and box["iou"] is not None


# ------------------------------------------------------------------ L62g (jury-1)
def test_glint_class_code6(client, tmp_path, monkeypatch):
    m = {q["id"]: q for q in client.get("/api/v3/meta").json()["quality_classes"]}
    assert m["glint"]["present"] is True and m["glint"]["codes"] == [6] and m["shadow"]["present"] is False
    np = pytest.importorskip("numpy")
    rasterio = pytest.importorskip("rasterio")
    from PIL import Image
    d = tmp_path / "pairs"
    q = d / "quality" / "S4_G"
    q.mkdir(parents=True)
    with rasterio.open(q / "quality.tif", "w", driver="GTiff", width=2, height=1, count=1, dtype="uint8") as ds:
        ds.write(np.array([[6, 1]], dtype=np.uint8), 1)
    (d / "pair_quality.csv").write_text("event_id,sample_ids,scene_id,dir,decision,reason\nS4:G,,SCN_G,S4_G,reject,glint\n",
                                        encoding="utf-8")
    monkeypatch.setitem(cs.PATHS, "pairs_dir", d)
    img = Image.open(io.BytesIO(client.get("/api/v3/scenes/SCN_G/quality.png").content))
    assert "#" + "".join(f"{v:02x}" for v in img.getpixel((0, 0))) == m["glint"]["color"]


@pytest.mark.skipif(not (HAS_PAIRS and HAS_SAMPLES), reason="нет реестра пар")
def test_pairs_time_uncertainty(client):
    pairs = client.get("/api/v3/pairs", params={"source": "S4_BLACK_SEA_DOORS3"}).json()["pairs"]
    assert pairs
    for p in pairs:
        if not p["time_known"]:
            assert p["dt_uncertainty_h"] == 12 and "±12 ч" in p["time_note"]
        else:
            assert p["dt_uncertainty_h"] == 0


@pytest.mark.skipif(not (cs.PATHS["pairs_dir"] / "quality").is_dir(), reason="нет data/pairs/quality")
def test_zone_detections_and_export(client):
    zones = [z for z in client.get("/api/v3/zones").json()["features"]
             if (z["properties"].get("suspicious_pixels") or {}).get("n_objects")]
    if not zones:
        pytest.skip("нет зон с детекциями")
    z = client.get(f"/api/v3/zones/{zones[0]['id']}").json()
    det = z["detections"]
    assert det["kind"] == "detection" and det["count"] == len(det["features"]) > 0
    for f in det["features"]:
        p = f["properties"]
        assert f["geometry"]["type"] in ("Polygon", "MultiPolygon") and p["kind"] == "detection"
        assert p["prob_max"] <= 1.0 and p["prob_max"] >= p["threshold"] and p["area_m2"] > 0
        assert p["zone_id"] == zones[0]["id"] and isinstance(p["in_strip"], bool)
    assert any(f["properties"]["in_strip"] for f in det["features"])
    assert det["label"] == "Подозрительные пиксели детектора"
    src = {"source": "S3_SE_NORTH_SEA"}
    zs = client.get("/api/v3/zones", params=src).json()["features"]
    ex = client.get("/api/v3/export", params={"layer": "detections", "format": "geojson", **src}).json()
    assert [f["id"] for f in ex["features"]] == [f["id"] for zz in zs
                                                  for f in client.get(f"/api/v3/zones/{zz['id']}").json()["detections"]["features"]]
    rows = list(csv.DictReader(io.StringIO(client.get("/api/v3/export", params={
        "layer": "detections", "format": "csv", **src}).content.decode("utf-8-sig"))))
    assert [r["det_id"] for r in rows] == [f["id"] for f in ex["features"]]
    assert list(rows[0].keys()) == cs.DET_COLS


# ------------------------------------------------------------------ 3.2 (INBOX §9): candidate strips, final test
def test_candidate_strips_semantics(client):
    fc = client.get("/api/v3/zones").json()
    assert fc["layer_kind"] == "candidate_strip"
    for f in fc["features"]:
        p = f["properties"]
        assert p["layer_kind"] == "candidate_strip" and p["layer_label"] == "Снимок-кандидат: полоса обследования"
        assert p["concentration"] is None
        if p["detection_status"] == "detected":  # only on a confirmed (synchronous) pair
            assert p["pair_status"] == "accepted"
        if p["pair_status"] != "accepted":
            assert p["detection_status"] == p["status"] == "insufficient_data"
            assert p["detection_reason"].startswith("связь снимка с полевым измерением не подтверждена")
        sp = p["suspicious_pixels"]
        if sp is not None:
            assert sp["note"] == (cs.QREJ_NOTE if sp["quality_rejected"] else
                                  "подозрительные пиксели на снимке-кандидате, без полевого подтверждения")
        assert p["detector_verdict"] in ("detected", "not_detected", "insufficient_data")
    rows = list(csv.DictReader(io.StringIO(client.get("/api/v3/export", params={"layer": "zones", "format": "csv"})
                                           .content.decode("utf-8-sig"))))
    by = {f["id"]: f["properties"] for f in fc["features"]}
    for r in rows:
        assert r["layer_kind"] == "candidate_strip" and r["detector_verdict"] == by[r["zone_id"]]["detector_verdict"]


def test_meta_summary_and_detector_note(client):
    m = client.get("/api/v3/meta").json()
    s = m["summary"]
    zones = client.get("/api/v3/zones").json()["features"]
    assert s["n_strips"] == len(zones)
    assert s["n_confirmed_pairs"] == len({(p["event_id"], p["scene_id"]) for p in
                                          client.get("/api/v3/pairs?status=accepted").json()["pairs"]})
    assert s["text"].startswith(f"{s['n_strips']} обследованных участков со снимками-кандидатами; "
                                f"{s['n_confirmed_pairs']} подтверждённых пар")
    assert "не только пластик" in m["detector"]["note"]
    assert [x["id"] for x in m["layers"]] == ["observations", "zones", "detections"]


def _fake_final_test(tmp_path, better):
    ft = tmp_path / "final_test.json"
    ft.write_text(json.dumps({"when": "2026-09-25T19:15:01", "profiles": {"S2_visual_total_plastic": {
        "n_test": 14, "main_model": "ridge_log", "main": {"n": 14, "mae": 30.0, "rmse": 41.5, "log1p_mae": 0.85,
                                                          "coverage90": 0.71},
        "baseline_metrics": {"n": 14, "mae": 25.2, "rmse": 32.3, "log1p_mae": 0.6, "coverage90": 0.93,
                             "q_lo": -1.3393, "q_hi": 1.5345},
        "main_vs_baseline": {"d_mae": 4.8, "ci95": [-1.6, 12.9]}, "main_better_significant": better}}}),
        encoding="utf-8")
    return ft


@pytest.mark.skipif(not (HAS_CONC_MODEL and HAS_SAMPLES), reason="нет файлов модели L68 или CSV")
def test_final_test_decides_field_estimate(client, tmp_path, monkeypatch):
    import math
    monkeypatch.setitem(cs.PATHS, "final_test", _fake_final_test(tmp_path, False))
    fe = cs.field_estimate_at(-69.83, 30.07, "2015-04-02T12:00:00Z")
    med = cs.dev_cv()["profiles"]["S2_visual_total_plastic"]["dev_target"]["median"]
    assert fe["model"] == "median_train" and fe["value"] == round(med, 2)
    assert fe["label"] == cs.FIELD_MEDIAN_LABEL and "не лучше медианы" in fe["label"]
    assert fe["lo"] == round(math.expm1(math.log1p(med) - 1.3393), 2)
    c = client.get("/api/v3/metrics").json()["concentration"]
    assert c["final_test_status"] == "посчитан один раз 25.09"
    t = c["final_test_summary"]["S2_visual_total_plastic"]
    assert t["main_better_significant"] is False and t["delta_mae_ci95"] == [-1.6, 12.9]
    assert t["main"]["mae"] == 30.0 and t["baseline"]["mae"] == 25.2 and "медиана" in t["decision"]
    me = cs.model_estimate_for(next(iter(cs._dev_predictions())))
    if me and me["profile_config"] == "S2_visual_total_plastic":
        assert me["status"] == "исследовательская модель, на test не лучше медианы"
    monkeypatch.setitem(cs.PATHS, "final_test", _fake_final_test(tmp_path, True))
    import os
    os.utime(cs.PATHS["final_test"], ns=(1, 3_000_000_000_000_000_000))
    assert cs.field_estimate_at(-69.83, 30.07, "2015-04-02T12:00:00Z")["model"] == "ridge_log"


# ------------------------------------------------------------------ 3.3 (jury-2: median on the map)
HAS_FINAL = cs.PATHS["final_test"].is_file()


@pytest.mark.skipif(not (HAS_CONC_MODEL and HAS_SAMPLES and HAS_FINAL), reason="нет модели L68 / final_test / CSV")
def test_every_profile_observation_has_median_field_estimate(client):
    members = cs.profile_members()
    assert set(members.values()) >= {"S2_visual_total_plastic", "S1_trawl_total_plastic"}
    fc = client.get("/api/v3/observations").json()
    by = {f["id"]: f["properties"] for f in fc["features"]}
    ft = json.loads(cs.PATHS["final_test"].read_text(encoding="utf-8"))["profiles"]
    test_ids = {r["sample_id"] for r in csv.DictReader(open(cs.PATHS["final_test"].parent / "final_test_predictions.csv",
                                                              encoding="utf-8-sig"))}
    for sid, prof in members.items():
        fe = by[sid]["field_estimate"]
        med = cs.dev_cv()["profiles"][prof]["dev_target"]["median"]
        assert fe["model"] == "median_train" and fe["value"] == round(med, 2) and fe["unit"] == "items/km2"
        assert fe["note"].startswith("оценка по полевым данным, не по снимку")
        assert fe["lo"] <= fe["value"] <= fe["hi"]
        assert fe["interval_coverage_test"] == ft[prof]["baseline_metrics"]["coverage90"]
        re_ = by[sid]["research_estimate"]
        assert by[sid]["model_estimate"] == re_
        if re_ is not None:
            assert "на отложенном test не лучше медианы" in re_["note"]
    assert test_ids and all(by[s]["field_estimate"] is not None for s in test_ids if s in by)  # test events too
    for sid, p in by.items():
        if sid not in members:
            assert p["field_estimate"] is None


@pytest.mark.skipif(not (HAS_CONC_MODEL and HAS_SAMPLES and HAS_FINAL), reason="нет модели L68 / final_test / CSV")
def test_export_obs_estimate_columns(client):
    params = {"source": "S2_SARGASSO_MSM41", "scope": "total_plastic"}
    fc = client.get("/api/v3/observations", params=params).json()
    rows = list(csv.DictReader(io.StringIO(client.get("/api/v3/export", params={
        **params, "layer": "observations", "format": "csv"}).content.decode("utf-8-sig"))))
    by = {f["id"]: f["properties"] for f in fc["features"]}
    assert list(rows[0].keys())[-len(cs.OBS_EST_COLS):] == cs.OBS_EST_COLS
    for r in rows:
        fe, re_ = by[r["sample_id"]]["field_estimate"], by[r["sample_id"]]["research_estimate"]
        assert float(r["field_estimate_items_km2"]) == fe["value"] and r["field_estimate_model"] == "median_train"
        assert (r["research_estimate_items_km2"] == "") == (re_ is None)
    gj = client.get("/api/v3/export", params={**params, "layer": "observations", "format": "geojson"}).json()
    assert all("field_estimate" in f["properties"] and "research_estimate" in f["properties"] for f in gj["features"])


@pytest.mark.skipif(not (HAS_CONC_MODEL and HAS_FINAL), reason="нет модели L68 / final_test")
def test_metrics_median_interval_coverage(client):
    c = client.get("/api/v3/metrics").json()["concentration"]
    for prof, pr in c["profiles"].items():
        mf = pr["map_field_estimate"]
        assert mf["model"] == "median_train" and "на отложенном test" in mf["interval"]
        assert mf["interval_coverage_test"] == pr["final_test"]["baseline"]["coverage"] or             abs(mf["interval_coverage_test"] - pr["final_test"]["baseline"]["coverage"]) < 1e-4


# ------------------------------------------------------------------ 3.4 (detector_review.md §7)
@pytest.mark.skipif(not (cs.PATHS["pairs_dir"] / "quality").is_dir(), reason="нет data/pairs/quality")
def test_detections_match_pair_quality(client):
    zones = client.get("/api/v3/zones").json()["features"]
    fc = client.get("/api/v3/export", params={"layer": "detections", "format": "geojson"}).json()
    n_crop = 0
    for z in zones:
        meta = cs.quality_meta(z["properties"]["quality_dir"]) or {}
        n_crop += int((meta.get("detector") or {}).get("n_det_crop") or 0)
    assert fc["count"] == n_crop  # the same final mask as pair_quality (quality.tif == 1, cloud buffer)
    by_zone = {}
    for f in fc["features"]:
        if f["properties"]["in_strip"]:
            by_zone[f["properties"]["zone_id"]] = by_zone.get(f["properties"]["zone_id"], 0) + 1
    for z in zones:  # one rule "in strip": raster strip, all_touched
        sp = z["properties"]["suspicious_pixels"]
        if sp is not None:
            assert by_zone.get(z["id"], 0) == sp["n_objects"], z["id"]
            assert sp["quality_rejected"] == (z["properties"]["quality_decision"] != "accept")
            if sp["quality_rejected"]:
                assert sp["note"] == cs.QREJ_NOTE
    for f in fc["features"]:
        assert f["properties"]["quality_rejected"] in (True, False)


@pytest.mark.skipif(not (cs.PATHS["pairs_dir"] / "pair_quality.csv").is_file(), reason="нет pair_quality.csv")
def test_glint_fraction_and_summary(client):
    pq = {r["event_id"]: r for r in csv.DictReader(open(cs.PATHS["pairs_dir"] / "pair_quality.csv",
                                                         encoding="utf-8-sig"))}
    for z in client.get("/api/v3/zones").json()["features"]:
        p = z["properties"]
        g = pq[p["event_id"]].get("glint_frac")
        assert p["quality"]["glint_fraction"] == (round(float(g), 4) if g not in (None, "") else None)
    s = client.get("/api/v3/meta").json()["summary"]
    assert s["text"].endswith(f"подозрительные пиксели в полосах пар, прошедших маски качества: "
                              f"{s['n_suspicious_in_quality_ok_strips']}")
