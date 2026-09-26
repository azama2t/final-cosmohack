"""§55а (L154): PRIME MODE · демо-данные по строкам CSV организаторов — /api/prime/csv_scenes, /api/prime/csv_img.

Данные — data/case/prime_csv (scripts/case/prime_csv.py demo). Проверяем контракт для фронта (L140) и честность:
плашка «ДЕМО», числа = строка CSV с подписью «демо-значение», нет метрик качества, картинки ≤ 150 КБ, всего ≤ 40 МБ.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from service.app import app

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "case" / "prime_csv"
IDX = DATA / "index.json"
CSV = ROOT / "task" / "macroplastic_marine_samples.csv"

pytestmark = pytest.mark.skipif(not IDX.is_file(), reason="нет data/case/prime_csv/index.json")


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def idx(client):
    r = client.get("/api/prime/csv_scenes")
    assert r.status_code == 200
    return r.json()


def test_meta_is_demo(idx):
    m = idx["meta"]
    assert m["badge"].startswith("ДЕМО")
    assert "не результат модели" in m["badge"]
    assert m["toggle"] == "PRIME MODE · демо-данные"
    assert m["numbers_label"] == "демо-значение (из CSV организаторов)"
    assert m["quality_metrics"] is None
    assert m["n_scenes"] >= 100 and m["n_scenes"] == len(idx["scenes"])


def test_numbers_come_from_csv_rows(idx):
    rows = {r["sample_id"]: r for r in csv.DictReader(CSV.open(encoding="utf-8"))}
    for s in idx["scenes"]:
        r = rows[s["csv_row_id"]]
        assert s["n_items"] == int(float(r["items_count"]))
        assert abs(s["area_km2"] - float(r["sampled_area_km2"])) < 1e-9
        assert abs(s["items_per_km2"] - float(r["concentration_items_km2"])) < 1e-9
        assert abs(s["lat"] - float(r["latitude"])) < 1e-6 and abs(s["lon"] - float(r["longitude"])) < 1e-6
        assert s["numbers_label"] == "демо-значение (из CSV организаторов)"
        assert s["frame_items_drawn"] == len(s["boxes"]) <= s["n_items"]
        for b in s["boxes"]:
            x0, y0, x1, y1 = b["box"]
            assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1


def test_lite_and_images(client, idx):
    lite = client.get("/api/prime/csv_scenes?lite=1").json()
    assert len(lite["scenes"]) == len(idx["scenes"]) and "boxes" not in lite["scenes"][0]
    s = idx["scenes"][0]
    for url in (s["image"], s["image_boxes"]):
        r = client.get(url)
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        assert 0 < len(r.content) <= 150_000
    assert client.get("/api/prime/csv_img/..%2Fx.jpg").status_code == 404
    assert client.get("/api/prime/csv_img/nope_zz.jpg").status_code == 404


def test_size_budget():
    files = list(DATA.glob("*"))
    assert sum(f.stat().st_size for f in files) <= 40 * 1024 * 1024
    assert all(f.stat().st_size <= 150_000 for f in files if f.suffix == ".jpg")
    idx = json.loads(IDX.read_text(encoding="utf-8"))
    assert len(list(DATA.glob("*.jpg"))) == 2 * len(idx["scenes"])
