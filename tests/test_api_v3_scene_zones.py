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


def test_wind_rule_cozar2024(client):
    """§29 А: wind > 5 m/s (Cózar 2024) — «не обнаружено» becomes «недостаточно данных (ветер …)», the scene leaves the
    «observed» denominator (LWD null); found zones keep their status."""
    idx = cs.scene_zones_index()
    sc = {s["key"]: s for s in idx["scenes"]}
    fc = client.get("/api/v3/scene_zones").json()
    for f in fc["features"]:
        p = f["properties"]
        w = sc[p["scene_key"]].get("wind10m_ms")
        high = w is not None and w > 5
        assert bool(p.get("wind_high")) == high
        assert p["detection_status"] != "not_informative"
        if high:
            assert p["measured"]["lwd_m2_km2"] is None and p["measured"]["lwd_note"] and p["wind_note"]
            assert sc[p["scene_key"]]["in_observed_area"] is False and sc[p["scene_key"]]["lwd_m2_km2"] is None
            assert p["detection_status"] != "not_detected"
            if p["zone_id"].endswith("-000"):
                assert p["detection_status"] == "insufficient_data" and "ветер > 5 м/с" in p["detection_label"]
                assert "Cózar 2024" in p["detection_label"]
        else:
            assert "wind" not in p["flags"] and not p["measured"].get("lwd_note")



def test_field_cards_s28(client):
    """§28: profile interval = bootstrap over cruise days (+ one new place), Poisson labelled «только ошибка счёта»;
    ADIS authors' trawl calibration next to our raw number; numbers from final_numbers (no hardcode)."""
    fnq = json.loads((cs.REPO / "reports" / "final_numbers.json").read_text(encoding="utf-8"))["case"]["sections"]["quantity"]
    pp = cs.profile_pooled("S2_visual_total_plastic")
    f2 = fnq["field_S2"]
    assert pp["mean"] == f2["pooled_C"] and (pp["boot_lo95"], pp["boot_hi95"]) == (f2["boot_lo95"], f2["boot_hi95"])
    assert (pp["event_lo95"], pp["event_hi95"]) == (f2["event_lo95"], f2["event_hi95"])
    assert pp["poisson_label"] == "только ошибка счёта (Пуассон)" and cs.profile_pooled("S1_trawl_total_plastic") is None
    meta = client.get("/api/v3/meta").json()
    assert meta["quantity_levels"]["patchiness"]["sd_ln_c"] == fnq["variance_S2"]["sd_within_day"]
    ac = fnq["adis_forecast"]["authors_calibrated"]
    p = client.get("/api/v3/scene_zones", params={"scene_kind": "demo"}).json()["features"][0]["properties"]
    a = p["field_nearby"]["authors_calibration"]
    assert (a["C"], a["lo_typ"], a["hi_typ"], a["ours_raw_C"]) == (ac["C"], ac["lo_typ"], ac["hi_typ"], ac["ours_raw_C"])
    for it in p["field_nearby"]["items"]:
        c = it.get("authors_cal_10cm_items_km2")
        assert c is None or c >= 0


def test_meta_headline_numbers_from_final_numbers(client):
    """§31 п.2 а: first-screen «Главное» — field profiles (measurement) + ADIS + satellite, numbers not hardcoded."""
    fnq = json.loads((cs.REPO / "reports" / "final_numbers.json").read_text(encoding="utf-8"))["case"]["sections"]["quantity"]
    h = client.get("/api/v3/meta").json()["headline"]
    by = {r["key"]: r for r in h["field"]}
    assert (by["S2"]["value"], by["S2"]["lo"], by["S2"]["hi"]) == (
        fnq["field_S2"]["pooled_C"], fnq["field_S2"]["boot_lo95"], fnq["field_S2"]["boot_hi95"])
    assert "бутстреп" in by["S2"]["interval_label"]
    af = fnq["adis_forecast"]
    assert (by["ADIS"]["value"], by["ADIS"]["lo"], by["ADIS"]["hi"]) == (af["C"], af["lo"], af["hi"])
    for r in h["field"]:
        assert r["kind"] == "measurement" and r["size_class"] and r["material"] and r["lo"] <= r["value"] <= r["hi"]
    fc = client.get("/api/v3/scene_zones").json()
    assert h["satellite"]["n_zones"] == fc["total"]
    assert h["satellite"]["n_level_b"] == sum(1 for f in fc["features"] if f["properties"]["verification"] == "level_B_cozar")
    assert h["satellite"]["quantity_label"] == "концентрация по снимку не подтверждена"
