"""L67: быстрая самопроверка согласованности API v3 (критерий Т5) на TestClient.

Полная версия (все запросы, алгоритм = JSON = CSV = GeoJSON, скорость, живой экземпляр):
scripts/case/consistency_check.py -> reports/selfcheck/consistency_*.md|json.
Сохранённые запросы пишутся во временный файл (case_store.PATHS["queries"] -> tmp_path), service/labels не трогается.
Тесты с xfail — известные расхождения из reports/tasklog/67_consistency.md: станут XPASS, когда владелец починит.
"""
from __future__ import annotations

import csv
import importlib.util
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from service import case_store as cs
from service.app import create_app

ROOT = Path(__file__).resolve().parents[1]
HAS_DATA = cs.PATHS["samples"].is_file() and (cs.PATHS["pairs_dir"] / "pair_quality.csv").is_file()
pytestmark = pytest.mark.skipif(not HAS_DATA, reason="нет task/ CSV или data/pairs/pair_quality.csv")


def _load():
    spec = importlib.util.spec_from_file_location("consistency_check", ROOT / "scripts" / "case" / "consistency_check.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cc = _load()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setitem(cs.PATHS, "queries", tmp_path / "queries.jsonl")
    return TestClient(create_app())


def _save(client, query, name="t"):
    r = client.post("/api/v3/queries", json={"name": name, "query": query})
    assert r.status_code == 201, r.text
    return r.json()["query_id"]


def _csv(body: bytes) -> list[dict]:
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))


# ------------------------------------------------------------------ 2. repeat + JSON = export
@pytest.mark.parametrize("query", [{"sources": ["S4_BLACK_SEA_DOORS3"]},
                                   {"sources": ["S3_SE_NORTH_SEA"], "date_from": "2016-01-01",
                                    "date_to": "2016-12-31"},
                                   {"bbox": [-30, -50, -29, -49]}])
def test_run_twice_same_sha256(client, query):
    qid = _save(client, query)
    a = client.get(f"/api/v3/queries/{qid}/run").json()
    b = client.get(f"/api/v3/queries/{qid}/run").json()
    assert cc.canon_hash(a) == cc.canon_hash(b)
    assert a["summary"]["n_obs"] == len(a["observations"]["features"])
    assert a["summary"]["n_zones"] == len(a["zones"]["features"])


def test_observations_algo_json_csv_geojson(client):
    q = {"sources": ["S2_SARGASSO_MSM41"], "profiles": ["S2_visual_GT2"], "bbox": [-71, 22, -57, 33]}
    qid = _save(client, q)
    run = client.get(f"/api/v3/queries/{qid}/run").json()
    feats = run["observations"]["features"]
    ids = [f["id"] for f in feats]
    samples = cc.read_csv(cc.SAMPLES)
    assert {r["sample_id"] for r in cc.algo_observations(samples, q)} == set(ids) and ids
    rows = _csv(client.get(f"/api/v3/export?layer=observations&format=csv&query_id={qid}").content)
    gj = client.get(f"/api/v3/export?layer=observations&format=geojson&query_id={qid}").json()
    assert [r["sample_id"] for r in rows] == ids == [f["id"] for f in gj["features"]]
    assert cc.canon_hash(gj["features"]) == cc.canon_hash(feats)
    by = {f["id"]: f for f in feats}
    for r in rows:
        p = by[r["sample_id"]]
        exp = None if r["record_type"] == "item_observation" else cc.fnum(r["concentration_items_km2"])
        assert cc.near(p["properties"]["concentration_items_km2"], exp)
        assert cc.near(p["geometry"]["coordinates"][0], r["longitude"])
        assert cc.near(p["geometry"]["coordinates"][1], r["latitude"])


def test_selection_target_equals_api_concentration(client):
    sel = cc.read_csv(cc.SELECTION["S2_visual_total_plastic"])
    fc = client.get("/api/v3/observations?source=S2_SARGASSO_MSM41&scope=total_plastic&limit=100000").json()
    by = {f["id"]: f["properties"]["concentration_items_km2"] for f in fc["features"]}
    assert sel and all(cc.near(r["target"], by[r["sample_id"]], 1e-6) for r in sel)


def test_zones_json_equals_export_without_sources(client):
    qid = _save(client, {"date_from": "2014-01-01", "date_to": "2024-12-31"})
    run = client.get(f"/api/v3/queries/{qid}/run").json()
    zones = run["zones"]["features"]
    rows = _csv(client.get(f"/api/v3/export?layer=zones&format=csv&query_id={qid}").content)
    gj = client.get(f"/api/v3/export?layer=zones&format=geojson&query_id={qid}").json()
    assert [r["zone_id"] for r in rows] == [z["id"] for z in zones] == [f["id"] for f in gj["features"]]
    by = {z["id"]: z["properties"] for z in zones}
    for r in rows:
        p = by[r["zone_id"]]
        assert cc.near(r["area_km2"], p["area_km2"]) and r["status"] == p["status"]
        assert r["concentration_status"] == p["concentration_status"] and r["unit"] == "items/km2"
        assert r["concentration_items_km2"] == "" and p["concentration"] is None


def test_metrics_match_sources(client):
    ck = cc.Checker()
    cc.check_metrics(client, ck, {"det_metrics": cc.json.loads(cc.DET_METRICS.read_text(encoding="utf-8")),
                                  "lgbm_test": cc.json.loads(cc.LGBM_TEST.read_text(encoding="utf-8")),
                                  "conc_metrics": cc.json.loads(cc.CONC_METRICS.read_text(encoding="utf-8")),
                                  "dev_cv": cc.json.loads(cc.DEV_CV.read_text(encoding="utf-8"))
                                  if cc.DEV_CV.is_file() else {},
                                  "conc_model_selected": cc._yaml(cc.CONC_MODEL_YAML).get("selected") or {},
                                  "meta": cs.meta()})
    bad = [r for r in ck.rows if r["status"] != "ok"]
    assert not bad, bad


# ------------------------------------------------------------------ 3. invalid inputs / empty results
def test_invalid_inputs_are_4xx_with_message(client):
    ck = cc.Checker()
    out = cc.check_invalid(client, ck)
    bad = [r for r in out if not r["ok"]]
    assert not bad, bad
    assert all(r["status"] < 500 for r in out)
    assert client.get("/api/v3/queries").json()["queries"] == []  # nothing invalid was saved


# ------------------------------------------------------------------ formerly known inconsistencies (fixed in L62e)
def test_export_zones_query_id_equals_run(client):
    qid = _save(client, {"sources": ["S4_BLACK_SEA_DOORS3"]})
    run = client.get(f"/api/v3/queries/{qid}/run").json()
    gj = client.get(f"/api/v3/export?layer=zones&format=geojson&query_id={qid}").json()
    assert [f["id"] for f in gj["features"]] == [z["id"] for z in run["zones"]["features"]]


def test_rejected_pairs_have_reason(client):
    pairs = client.get("/api/v3/pairs?status=rejected").json()["pairs"]
    assert all(p["reject_reasons"] for p in pairs)


def test_pairs_drift_shift_from_registry(client):
    cand = {(r["event_id"], r.get("item_id") or ""): r for r in cc.read_csv(cc.CANDIDATES)}
    pairs = client.get("/api/v3/pairs?source=S3_SE_NORTH_SEA").json()["pairs"]
    for p in pairs:
        cr = cand.get((p["event_id"], p["scene_id"] or ""))
        if cr is not None:
            assert cc.near(p["drift_shift_km"], cc.fnum(cr.get("drift_shift_km")), 0.005)
