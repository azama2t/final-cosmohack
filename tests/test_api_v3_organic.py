"""§51 п.9 (L143, docs/ORGANIC.md): органика отдельно. Детектор бинарный -> органика отдельным классом не выделяется;
спектральный флаг «вероятно органика» (NDVI ≥ 0,20 и FAI > 0, эксперимент) — поля API зон = CSV, фильтр organic."""
from __future__ import annotations

import csv
import io
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from service import case_store as cs
from service.app import app
from src.macroplastic.case import organic as O


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setitem(cs.PATHS, "queries", tmp_path / "queries.jsonl")
    return TestClient(app)


def test_detector_is_binary():
    meta = json.loads((cs.REPO / "weights" / "lgbm" / "meta.json").read_text(encoding="utf-8"))
    assert meta["task"] == "binary" and meta["classes"] == [0, 1]


def test_rule_on_synthetic_spectra():
    # water: low NIR -> no flag; vegetation-like (strong NIR over red) -> flag; flat bright (debris-like) -> no flag
    b4 = np.array([0.02, 0.03, 0.08]); b8 = np.array([0.015, 0.08, 0.09]); b11 = np.array([0.01, 0.03, 0.07])
    nv, fa = O.flag_pixels(b4, b8, b11)
    assert list(nv & fa) == [False, True, False]
    z = O.zone_flag(b4[1:2], b8[1:2], b11[1:2])
    assert z["likely_organic"] is True and z["ndvi_median"] >= O.NDVI_MIN and z["fai_median"] > 0
    assert O.zone_flag(np.array([np.nan]), np.array([np.nan]), np.array([np.nan]))["likely_organic"] is None


def test_organic_fields_api_equals_csv(client):
    fc = client.get("/api/v3/scene_zones", params={"limit": 1000}).json()
    body = client.get("/api/v3/export", params={"layer": "scene_zones", "format": "csv"}).content
    rows = {r["zone_id"]: r for r in csv.DictReader(io.StringIO(body.decode("utf-8-sig")))}
    n_eval = 0
    for f in fc["features"]:
        p = f["properties"]
        o = p["organic"]
        assert o["experiment"] is True and o["model_note"].startswith("органика отдельно не выделяется моделью")
        assert p["likely_organic"] == o["likely_organic"]
        r = rows[p["zone_id"]]
        assert r["likely_organic"] == {None: "", True: "true", False: "false"}[p["likely_organic"]]
        assert r["organic_label"] == (o["label"] or "")
        if p["likely_organic"] is None:
            continue
        n_eval += 1
        assert o["label"].startswith("Органика: ") and "эксперимент" in o["label"]
        assert (o["ndvi_median"] >= O.NDVI_MIN and o["fai_median"] > 0) == p["likely_organic"]
        cl = p["classification"]
        assert "algae_sargassum" not in cl["not_checked_backgrounds"]  # replaced by the flag result
        assert "водоросли/саргассум" not in (cl["not_checked_label"] or "")
        assert cl["organic_label"] == o["label"]
    assert n_eval > 0


def test_organic_filter(client):
    all_ = client.get("/api/v3/scene_zones", params={"limit": 1000}).json()["features"]
    yes = client.get("/api/v3/scene_zones", params={"limit": 1000, "organic": "true"}).json()["features"]
    no = client.get("/api/v3/scene_zones", params={"limit": 1000, "organic": "false"}).json()["features"]
    assert all(f["properties"]["likely_organic"] is True for f in yes)
    assert all(f["properties"]["likely_organic"] is False for f in no)
    assert len(yes) + len(no) == sum(1 for f in all_ if f["properties"]["likely_organic"] is not None)
    assert client.get("/api/v3/scene_zones", params={"organic": "maybe"}).status_code == 400


def test_val_report_numbers():
    v = json.loads((cs.REPO / "reports" / "organic" / "val_flag.json").read_text(encoding="utf-8"))
    assert "test не читался" in v["split"]
    c = v["classes"]
    for k in ("Marine Debris", "Dense Sargassum", "Sparse Sargassum", "Natural Organic Material"):
        assert c[k]["objects"] > 0 and 0 <= c[k]["flag_obj"] <= c[k]["objects"]


def test_organic_in_geojson_export(client):
    r = client.get("/api/v3/export", params={"layer": "scene_zones", "format": "geojson"})
    assert r.status_code == 200
    feats = r.json()["features"]
    assert feats and all("likely_organic" in f["properties"] and "organic" in f["properties"] for f in feats)
