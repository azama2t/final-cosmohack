"""3.10 satellite scene zones (L111, INBOX §15): measured / probable / scenario, export, saved query, wording."""
from __future__ import annotations

import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from service import case_store as cs
from service.app import app

HAS = (cs.PATHS["scene_zones_dir"] / "index.json").is_file()
pytestmark = pytest.mark.skipif(not HAS, reason="scripts/case/scene_zones.py not run")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setitem(cs.PATHS, "queries", tmp_path / "queries.jsonl")
    return TestClient(app)


def test_scene_zones_blocks_and_statuses(client):
    fc = client.get("/api/v3/scene_zones").json()
    assert fc["kind"] == "detection_zone" and fc["count"] == fc["total"] > 0
    assert fc["model"]["weights"] == "weights/lgbm" and len(fc["model"]["sha256"]) == 64
    for f in fc["features"]:
        p = f["properties"]
        assert p["kind"] == "detection_zone" and p["concentration"] is None
        assert p["detection_status"] in ("detected", "not_detected", "insufficient_data")
        m = p["measured"]
        assert m["zone_area_km2"] > 0 and m["suspicious_area_m2"] == m["n_pixels"] * 100
        sc = p["scenario"]
        # K2: scenario only with independent level-B evidence (Cózar filament) and no false-alarm signs
        assert sc["shown"] == (p.get("verification") == "level_B_cozar")
        if sc["shown"]:
            assert p["n_cozar_filaments"] > 0 and not p["flags"] and p["detection_status"] == "detected"
        if p["detection_status"] == "detected" and p.get("verification") == "unverified":
            assert p["concentration_status"] == "unavailable" and "не проверено" in p["detection_label"]
        assert (p["concentration_status"] == "research_estimate") == sc["shown"]
        if p["flags"]:
            assert p["detection_status"] == "insufficient_data" and not sc["shown"] and sc["reason"]
        if sc["shown"]:
            assert sc["lo"] < sc["typical_lo"] < sc["typical_hi"] < sc["hi"]
            assert "ЕСЛИ" in sc["label"] and "Не доверительный интервал" in sc["not_what"]
            assert any("Cózar" in b["where"] for b in sc["basis"])
        fn = p["field_nearby"]
        assert "Измерение ≠ оценка" in fn["note"]
        for it in fn["items"]:
            assert it["ci95_lo"] <= (it["c_items_km2"] or 0) <= it["ci95_hi"]


def test_demo_scene_present_and_detail(client):
    fc = client.get("/api/v3/scene_zones", params={"scene_kind": "demo"}).json()
    assert fc["count"] > 0
    f = fc["features"][0]
    d = client.get(f"/api/v3/scene_zones/{f['id']}").json()
    assert d["id"] == f["id"] and d["detections"]["count"] > 0
    assert all(x["properties"]["zone_id"] == f["id"] for x in d["detections"]["features"])
    for u in (d["scene"]["preview_url"], d["scene"]["quality_url"]):
        assert client.get(u).status_code == 200
    assert client.get("/api/v3/scene_zones/SZ-nope").status_code == 404
    assert client.get("/api/v3/scene_zones/scenes/nope/rgb.jpg").status_code == 404


def test_scene_zones_filters_and_bad_params(client):
    allz = client.get("/api/v3/scene_zones").json()["total"]
    det = client.get("/api/v3/scene_zones", params={"detection_status": "detected"}).json()
    assert 0 < det["total"] < allz and all(f["properties"]["detection_status"] == "detected" for f in det["features"])
    r = client.get("/api/v3/scene_zones", params={"foo": "1"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "BAD_PARAM"
    assert client.get("/api/v3/scene_zones", params={"date_from": "2030-01-01"}).json()["count"] == 0


def test_scene_zones_export_matches_api(client):
    fc = client.get("/api/v3/scene_zones", params={"scene_kind": "demo"}).json()
    gj = json.loads(client.get("/api/v3/export", params={"layer": "scene_zones", "format": "geojson",
                                                           "scene_kind": "demo"}).content)
    assert [f["id"] for f in gj["features"]] == [f["id"] for f in fc["features"]]
    body = client.get("/api/v3/export", params={"layer": "scene_zones", "format": "csv", "scene_kind": "demo"}
                      ).content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(body)))
    assert list(rows[0].keys()) == cs.SZ_COLS and len(rows) == fc["count"]
    by = {f["id"]: f["properties"] for f in fc["features"]}
    for r in rows:
        p = by[r["zone_id"]]
        assert float(r["suspicious_area_m2"]) == p["measured"]["suspicious_area_m2"]
        assert r["scenario_shown"] == ("true" if p["scenario"]["shown"] else "false")
        if not p["scenario"]["shown"]:
            assert r["scenario_lo_items_km2"] == ""
        assert r["kind"] == "detection_zone"


def test_saved_query_runs_scene_zones(client):
    q = {"bbox": None, "date_from": "2021-03-01", "date_to": "2021-03-31", "statuses": [], "sources": [],
         "profiles": [], "layers": ["zones"], "scene_id": None}
    qid = client.post("/api/v3/queries", json={"name": "демо Cózar", "query": q}).json()["query_id"]
    run = client.get(f"/api/v3/queries/{qid}/run").json()
    direct = client.get("/api/v3/scene_zones", params={"date_from": "2021-03-01", "date_to": "2021-03-31"}).json()
    assert run["summary"]["n_scene_zones"] == direct["total"] > 0
    exp = json.loads(client.get("/api/v3/export", params={"layer": "scene_zones", "query_id": qid}).content)
    assert exp["count"] == direct["total"]


def test_meta_wording_and_model_version(client):
    m = client.get("/api/v3/meta").json()
    v = m["detector"]["version"]
    assert v["weights"] == "weights/lgbm" and len(v["sha256"]) == 64 and v["threshold"] == 0.63
    ql = m["quantity_levels"]
    assert "пары по месту и времени" in ql["place_time_pairs"]["label"]
    assert "калибровоч" not in ql["place_time_pairs"]["label"]
    assert ql["calibration_pairs"]["n"] == 0
    assert "не задаёт общий предел" in ql["visible_signal"]["note"]
    txt = json.dumps(m, ensure_ascii=False)
    assert "предел обнаружения доказан" not in txt
    z = client.get("/api/v3/zones").json()
    assert z["model"]["weights_sha256"] == v["sha256"] and z["model"]["trained_at"]
