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
        assert p["detection_status"] in ("detected", "not_detected", "not_informative", "insufficient_data")
        m = p["measured"]
        assert m["zone_area_km2"] > 0 and m["suspicious_area_m2"] == m["n_pixels"] * 100
        # INBOX §23 п.2: no items/km2 scenario at all; quantity status «концентрация по снимку не подтверждена»
        assert p["scenario"] is None and "калибровочных пар" in p["scenario_reason"]
        assert p["concentration_status"] == "unavailable"
        assert p["quantity"]["status"] == "not_confirmed" and p["concentration_label"] == "концентрация по снимку не подтверждена"
        # «обнаружено детектором» only after all false-alarm filters; ships never «detected»
        if p["flags"]:
            assert p["detection_status"] == "insufficient_data" and "обнаружено" not in p["detection_label"]
        if "ship" in p["flags"]:
            assert p["detection_label"].startswith("ложное срабатывание")
        if p["detection_status"] == "detected":
            assert not p["flags"] and p["detection_label"].startswith("обнаружено детектором")
            if p["n_cozar_filaments"]:
                assert p["verification"] == "level_B_cozar" and "совпадает с разметкой Cózar (B)" in p["detection_label"]
            else:
                assert p["verification"] == "unverified" and "требует проверки" in p["detection_label"]
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
        assert r["quantity_status"] == "not_confirmed" and not any(k.startswith("scenario") for k in r)
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


def test_no_items_scenario_numbers_anywhere(client):
    """INBOX §23 п.2: no 10⁴–10⁸ / «160–500 000» items/km2 for satellite zones in API or export."""
    body = client.get("/api/v3/scene_zones").text + client.get(
        "/api/v3/export", params={"layer": "scene_zones", "format": "csv"}).content.decode("utf-8-sig")
    for bad in ("typical_lo", "10⁴", "100 млн", "500 000", "Условный диапазон"):
        assert bad not in body


def test_demo_scene_cozar_zones_detected_level_b(client):
    fc = client.get("/api/v3/scene_zones", params={"scene_kind": "demo"}).json()
    b = [f["properties"] for f in fc["features"] if f["properties"]["n_cozar_filaments"]]
    assert len(b) >= 10
    for p in b:
        assert p["detection_label"] == "обнаружено детектором · совпадает с разметкой Cózar (B)"


def test_wind_rule_zero_not_informative(client):
    """Г3-1: wind >= 5 m/s — «не обнаружено» becomes «ноль не информативен», LWD gets a note."""
    idx = cs.scene_zones_index()
    wind = {s["key"]: s.get("wind10m_ms") for s in idx["scenes"]}
    fc = client.get("/api/v3/scene_zones").json()
    for f in fc["features"]:
        p = f["properties"]
        w = wind.get(p["scene_key"])
        high = w is not None and w >= 5
        assert bool(p.get("wind_high")) == high
        if high:
            assert p["detection_status"] != "not_detected" and p["measured"]["lwd_note"] and p["wind_note"]
            if p["zone_id"].endswith("-000"):
                assert p["detection_status"] == "not_informative"
                assert p["detection_label"] == "ноль не информативен (ветер ≥ 5 м/с)"
        else:
            assert p["detection_status"] != "not_informative" and not p["measured"].get("lwd_note")
    ni = client.get("/api/v3/scene_zones", params={"detection_status": "not_informative"}).json()
    assert all(f["properties"]["detection_status"] == "not_informative" for f in ni["features"])
