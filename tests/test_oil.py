"""Класс «Нефтяное пятно» (L101, INBOX §14): метрики, порог OSI, полигоны с площадью, API /api/v3/oil/*.

Синтетические данные во временном каталоге (ROOTS роутера подменяются); плюс проверка замороженного
configs/oil_eval.yaml (пропуск, если его нет).
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import numpy as np
import pytest

from macroplastic.oil import metrics as OM
from macroplastic.oil.baseline import osi, osi_score
from macroplastic.oil import spills as SP

REPO = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ metrics
def test_binary_metrics_values_and_zero_division():
    m = OM.binary_metrics(8, 2, 2)
    assert m["precision"] == pytest.approx(0.8) and m["recall"] == pytest.approx(0.8)
    assert m["f1"] == pytest.approx(0.8) and m["iou"] == pytest.approx(8 / 12)
    z = OM.binary_metrics(0, 0, 0)
    assert z["f1"] == 0.0 and z["iou"] == 0.0 and z["precision"] == 0.0


def test_scene_counts_pooled_and_bootstrap():
    true = np.array([1, 1, 0, 0, 1, 0, 1, 1], bool)
    pred = np.array([1, 0, 0, 1, 1, 0, 1, 1], bool)
    scene = np.array(["a", "a", "a", "a", "b", "b", "c", "c"])
    c = OM.scene_counts(true, pred, scene)
    assert c["a"] == (1, 1, 1, 2) and c["b"] == (1, 0, 0, 1) and c["c"] == (2, 0, 0, 2)
    p = OM.pooled_from_counts(c)
    assert (p["tp"], p["fp"], p["fn"]) == (4, 1, 1)
    ci1 = OM.bootstrap_ci(c, reps=500, seed=0)
    ci2 = OM.bootstrap_ci(c, reps=500, seed=0)
    assert ci1 == ci2  # deterministic
    for k in ("precision", "recall", "f1", "iou"):
        lo, hi = ci1[k]
        assert 0.0 <= lo <= hi <= 1.0
    assert ci1["f1"][0] <= p["f1"] <= ci1["f1"][1]


def test_best_threshold_picks_separating_value():
    true = np.array([0, 0, 0, 1, 1], bool)
    score = np.array([0.1, 0.2, 0.3, 0.7, 0.9], np.float32)
    t, m = OM.best_threshold(true, score, [0.25, 0.5, 0.8])
    assert t == 0.5 and m["f1"] == 1.0


# ------------------------------------------------------------------ OSI baseline
def test_osi_formula_sign_and_nan():
    b2, b3, b4 = np.array([0.05, 0.0, np.nan]), np.array([0.04, 0.1, 0.1]), np.array([0.03, 0.1, 0.1])
    v = osi(b2, b3, b4)
    assert v[0] == pytest.approx((0.04 + 0.03) / 0.05) and np.isnan(v[1]) and np.isnan(v[2])
    rows = np.zeros((3, 11), np.float32)
    rows[:, 1], rows[:, 2], rows[:, 3] = b2, b3, b4
    s_pos, s_neg = osi_score(rows, 1), osi_score(rows, -1)
    assert s_pos[0] == pytest.approx(1.4, rel=1e-5) and s_neg[0] == pytest.approx(-1.4, rel=1e-5)
    assert np.isneginf(s_pos[1]) and np.isneginf(s_neg[2])  # NaN -> never oil
    stack = rows.T.reshape(11, 3, 1)
    assert osi_score(stack, 1).shape == (3, 1)


# ------------------------------------------------------------------ polygons + area
def _utm_tr():
    from rasterio.transform import from_origin

    return from_origin(500000.0, 4500000.0, 10.0, 10.0)  # UTM 33N, 10 m pixels


def test_spill_features_area_class_and_min_px():
    pytest.importorskip("rasterio")
    m = np.zeros((50, 60), bool)
    m[5:15, 5:25] = True     # 200 px = 0.02 km²
    m[30:32, 40:42] = True   # 4 px (dropped with min_px=10)
    prob = np.where(m, 0.9, 0.0).astype(np.float32)
    scene = {"scene_id": "S2X_TEST", "date": "2025-01-02", "region": "r", "source": "Sentinel-2 L2A",
             "experimental": True, "model": "weights_exp/oil/final"}
    f = SP.spill_features(m, _utm_tr(), "EPSG:32633", scene, prob=prob, min_px=10, water_px=3000)
    assert len(f) == 1
    p = f[0]["properties"]
    assert p["class"] == "oil_spill" and p["experimental"] is True and p["unit_note"] == SP.UNIT_NOTE
    assert p["n_px"] == 200 and p["area_km2"] == pytest.approx(0.02)
    assert p["scene_frac"] == pytest.approx(200 / 3000) and p["prob_mean"] == pytest.approx(0.9)
    g = f[0]["geometry"]
    assert g["type"] == "Polygon"
    lon, lat = np.array(g["coordinates"][0]).T
    assert (12 < lon).all() and (lon < 16).all() and (40 < lat).all() and (lat < 41).all()  # EPSG:4326
    assert 12 < p["lon"] < 16 and 40 < p["lat"] < 41
    f_all = SP.spill_features(m, _utm_tr(), "EPSG:32633", scene, min_px=1)
    assert [x["properties"]["n_px"] for x in f_all] == [200, 4]  # sorted by area
    assert SP.spill_features(np.zeros((5, 5), bool), _utm_tr(), "EPSG:32633", scene) == []
    rows = SP.csv_rows(f)
    assert list(rows[0]) == SP.CSV_FIELDS and rows[0]["class"] == "oil_spill"


# ------------------------------------------------------------------ API
@pytest.fixture()
def oil_dir(tmp_path, monkeypatch):
    pytest.importorskip("rasterio")
    from service import routes_v3_oil as ro

    d = tmp_path / "oil"
    d.mkdir()
    m = np.zeros((50, 60), bool)
    m[5:15, 5:25] = True
    m[30:40, 40:45] = True
    base = {"date": "2025-01-02", "region": "adria", "source": "Sentinel-2 L2A", "experimental": True,
            "model": "weights_exp/oil/final"}
    fa = SP.spill_features(m, _utm_tr(), "EPSG:32633", {**base, "scene_id": "S2A_ONE"}, min_px=1, water_px=3000)
    for f in fa:
        f["properties"]["scene_key"] = "live.adria.2025-01-02"
    (d / "live.adria.2025-01-02.geojson").write_text(json.dumps(SP.feature_collection(fa)), encoding="utf-8")
    idx = {"experimental": True, "model": "weights_exp/oil/final", "threshold": 0.06, "min_px": 10,
           "metrics_val": {"f1": 0.88}, "metrics_test": {"f1": 0.73}, "generated_at": "2026-09-26T02:00:00",
           "scenes": [
               {"scene_key": "live.adria.2025-01-02", "kind": "live", "scene_id": "S2A_ONE", "date": "2025-01-02",
                "region": "adria", "source": "Sentinel-2 L2A", "oil_km2": 0.025, "oil_frac_water": 0.0083,
                "n_spills": 2, "water_km2": 0.3, "bbox": [14.9, 40.5, 15.1, 40.7], "status": "ok"},
               {"scene_key": "pair.X", "kind": "pair", "scene_id": "S2B_TWO", "date": "2024-06-03", "region": "X",
                "source": "Sentinel-2 L2A", "oil_km2": 0.0, "oil_frac_water": 0.0, "n_spills": 0, "water_km2": 49.7,
                "bbox": [30.0, 42.0, 30.1, 42.1], "status": "ok"},
               {"scene_key": "pair.CLOUDY", "kind": "pair", "scene_id": "S2B_THREE", "date": "2016-04-08",
                "region": "CLOUDY", "source": "Sentinel-2 L2A", "oil_km2": None, "oil_frac_water": None,
                "n_spills": None, "water_km2": 41.6, "valid_water_frac": 0.02, "bbox": [3.0, 54.0, 3.1, 54.1],
                "status": "no_estimate_low_water", "status_reason": "пригодной воды 2 % < 50 % — нет оценки"}]}
    (d / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    monkeypatch.setitem(ro.ROOTS, "oil", d)
    ro._cache.clear()
    return d


@pytest.fixture()
def client(oil_dir):
    from fastapi.testclient import TestClient

    from service.app import create_app

    return TestClient(create_app())


def test_api_meta(client):
    r = client.get("/api/v3/oil/meta")
    assert r.status_code == 200 and "charset=utf-8" in r.headers["content-type"]
    j = r.json()
    assert j["class"] == "oil_spill" and j["experimental"] is True and j["counts_applicable"] is False
    assert j["unit_note"] == "площадь, не объём/масса" and j["color"]["fill"].startswith("#")
    assert j["n_scenes"] == 3 and j["metrics"]["test"]["f1"] == 0.73


def test_api_spills_geojson_filters(client):
    r = client.get("/api/v3/oil/spills")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/geo+json")
    assert "charset=utf-8" in r.headers["content-type"]
    j = r.json()
    assert j["type"] == "FeatureCollection" and j["total"] == 2
    p = j["features"][0]["properties"]
    for k in ("class", "area_km2", "scene_id", "date", "source", "experimental"):
        assert k in p
    assert p["class"] == "oil_spill" and p["experimental"] is True
    assert j["total_area_km2"] == pytest.approx(0.025)
    assert client.get("/api/v3/oil/spills", params={"min_area_km2": 0.01}).json()["total"] == 1
    assert client.get("/api/v3/oil/spills", params={"scene_id": "S2B_TWO"}).json()["total"] == 0
    assert client.get("/api/v3/oil/spills", params={"scene_id": "live.adria.2025-01-02"}).json()["total"] == 2
    e = client.get("/api/v3/oil/spills", params={"date_from": "2025-02-01"}).json()
    assert e["total"] == 0 and e["empty_reason"]
    assert client.get("/api/v3/oil/spills", params={"bbox": "0,0,1,1"}).json()["total"] == 0
    assert client.get("/api/v3/oil/spills", params={"limit": 1}).json()["features"].__len__() == 1


def test_api_unknown_param_and_bad_values_400(client):
    r = client.get("/api/v3/oil/spills", params={"foo": "1"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "BAD_PARAM"
    assert "charset=utf-8" in r.headers["content-type"]
    assert client.get("/api/v3/oil/spills", params={"bbox": "1,2"}).status_code == 400
    assert client.get("/api/v3/oil/spills", params={"date_from": "x"}).status_code == 400
    assert client.get("/api/v3/oil/scenes", params={"kind": "moon"}).status_code == 400
    assert client.get("/api/v3/oil/export", params={"format": "xls"}).status_code == 400
    assert client.get("/api/v3/oil/meta", params={"a": "b"}).status_code == 400


def test_api_scenes_and_export(client):
    j = client.get("/api/v3/oil/scenes").json()
    assert j["total"] == 3 and j["items"][0]["class"] == "oil_spill"
    assert client.get("/api/v3/oil/scenes", params={"kind": "pair"}).json()["total"] == 2
    cl = [i for i in j["items"] if i["scene_key"] == "pair.CLOUDY"][0]
    assert cl["status"] == "no_estimate_low_water" and cl["oil_km2"] is None and cl["n_spills"] is None  # «нет оценки», не 0
    ok = [i for i in j["items"] if i["scene_key"] == "pair.X"][0]
    assert ok["status"] == "ok" and ok["oil_km2"] == 0.0
    r = client.get("/api/v3/oil/export", params={"format": "csv"})
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    assert "charset=utf-8" in r.headers["content-type"] and "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 2 and rows[0]["class"] == "oil_spill" and float(rows[0]["area_km2"]) > 0
    g = client.get("/api/v3/oil/export").json()
    assert g["type"] == "FeatureCollection" and g["total"] == 2


def test_api_empty_without_data(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from service import routes_v3_oil as ro
    from service.app import create_app

    monkeypatch.setitem(ro.ROOTS, "oil", tmp_path / "none")
    ro._cache.clear()
    c = TestClient(create_app())
    j = c.get("/api/v3/oil/spills").json()
    assert j["total"] == 0 and j["features"] == [] and j["empty_reason"]
    assert c.get("/api/v3/oil/meta").status_code == 200


def test_other_v3_routes_still_work(client):
    assert client.get("/api/v3/definitely-not-here").status_code == 404  # v3 catch-all still last


# ------------------------------------------------------------------ frozen evaluation config
def test_oil_eval_config_frozen_and_disjoint():
    import hashlib

    yaml = pytest.importorskip("yaml")
    p = REPO / "configs" / "oil_eval.yaml"
    if not p.is_file():
        pytest.skip("configs/oil_eval.yaml absent")
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    comp = cfg["composition"]
    h = hashlib.sha256(json.dumps(comp, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    assert h == cfg["composition_sha256"]
    tr, va, te = (set(comp[s]["scenes"]) for s in ("train", "val", "test"))
    assert not (tr & va) and not (tr & te) and not (va & te)
    assert not (set(cfg["exclusions"]["scenes"]) & (tr | va | te))
    assert comp["val"]["n_oil_scenes"] > 0 and comp["test"]["n_oil_scenes"] > 0
