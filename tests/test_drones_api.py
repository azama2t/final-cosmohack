"""§54 п.2 (L151): «Дроны» — /api/v3/drones, /api/v3/drones/{set}/frames, превью. Данные — data/case/drones (в git)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from service.app import app

ROOT = Path(__file__).resolve().parents[1]
IDX = ROOT / "data" / "case" / "drones" / "index.json"
GROUPS = {"plastic", "algae", "wood", "other"}
SENSORS = {"drone", "aircraft", "vessel", "shore"}

pytestmark = pytest.mark.skipif(not IDX.is_file(), reason="нет data/case/drones/index.json")


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def sets(client):
    r = client.get("/api/v3/drones")
    assert r.status_code == 200
    return r.json()


def test_sets_contract(sets):
    assert "не спутник" in sets["banner"]
    ids = [s["id"] for s in sets["sets"]]
    for need in ("ucwd", "tun_marinelitter", "martin2021", "maharjan2022"):
        assert need in ids
    for s in sets["sets"]:
        for k in ("name", "sensor", "sensor_label", "region", "license", "link", "n_frames", "groups_labelled"):
            assert k in s, (s["id"], k)
        assert s["sensor"] in SENSORS
        assert set(s["groups_labelled"]) <= GROUPS
        assert s["n_frames"] >= 1
        if s["sensor"] != "drone":
            assert "не дрон" in s["sensor_label"]  # aircraft / vessel are labelled as such, never as a drone


def test_no_invented_india_set(sets):
    assert not any("инди" in (s["name"] + s["region"]).lower() for s in sets["sets"])
    assert any("Инди" in n["what"] for n in sets["not_included"])


def test_maharjan_license_is_missing_not_invented(sets):
    m = next(s for s in sets["sets"] if s["id"] == "maharjan2022")
    assert "лицензии нет" in m["license"]
    assert m["frame_area_m2"] == 4.0


def test_frames_units_only_with_known_area(client, sets):
    for s in sets["sets"]:
        j = client.get(f"/api/v3/drones/{s['id']}/frames").json()
        assert j["frames"], s["id"]
        for f in j["frames"]:
            if f["frame_area_m2"]:
                if f["n_objects"] is not None:
                    assert f["density_m2"] == pytest.approx(f["n_objects"] / f["frame_area_m2"], rel=1e-3)
                    assert f["density_km2"] == pytest.approx(f["n_objects"] / f["frame_area_m2"] * 1e6, rel=1e-3)
            else:
                assert f["density_m2"] is None and f["density_km2"] is None
                assert "площадь кадра неизвестна" in f["area_note"]
            if f["objects"] is not None:
                assert f["n_objects"] == len(f["objects"])
                for o in f["objects"]:
                    assert o["group"] in GROUPS
                    x, y, w, h = o["bbox"]
                    assert -0.01 <= x <= 1.01 and -0.01 <= y <= 1.01 and 0 <= w <= 1.01 and 0 <= h <= 1.01
                    assert o["group"] in s["groups_labelled"]
            assert f["image"].startswith(f"/api/v3/drones/{s['id']}/img/")


def test_martin_has_no_boxes_but_published_density(client):
    j = client.get("/api/v3/drones/martin2021/frames").json()
    assert j["set"]["frame_area_m2"] == pytest.approx(138.6)
    for f in j["frames"]:
        assert f["objects"] is None and f["n_objects"] is None and f["density_km2"] is None
        assert f["published"]["density_km2"] == pytest.approx(f["published"]["density_m2"] * 1e6, rel=1e-3)


def test_model_on_every_frame_and_summary(client, sets):
    """§56: our counter ran on all frames; summary X of Y / false Z = sum over frames; honest training note."""
    fn = json.loads((ROOT / "reports" / "final_numbers.json").read_text(encoding="utf-8"))["case"]["sections"]["photo_count"]
    for s in sets["sets"]:
        m = s["model"]
        assert m is not None, s["id"]
        assert m["trained_on_this_set"] == (s["id"] in ("winans2023", "fml"))
        assert ("не обучался" in m["training_note"]) != m["trained_on_this_set"]
        assert m["iou"] == 0.5 and m["threshold"] > 0
        j = client.get(f"/api/v3/drones/{s['id']}/frames").json()
        tp = fp = gt = n = 0
        for f in j["frames"]:
            p = f["model"]
            assert p is not None and p["n"] == len(p["boxes"]) == len(p["scores"])
            assert all(sc >= m["threshold"] - 1e-6 for sc in p["scores"])
            assert f["sensor"] == s["sensor"]
            assert f["date"] or f["date_note"] == "дата не указана"
            n += p["n"]
            if f["objects"] is not None:
                assert p["n_labelled"] == f["n_objects"] and p["tp"] + p["fp"] == p["n"] and p["tp"] + p["fn"] == p["n_labelled"]
                tp, fp, gt = tp + p["tp"], fp + p["fp"], gt + p["n_labelled"]
        sm = m["summary"]
        assert sm["n_pred"] == n
        if sm["found"] is not None:
            assert (sm["found"], sm["labelled"], sm["false"]) == (tp, gt, fp)
        if s["id"] == "winans2023":
            assert m["checked_metric"]["count_mae_per_frame"] == fn["area"]["count_mae"]
        if s["id"] == "fml":
            assert m["checked_metric"]["count_mae_per_frame"] == fn["frame"]["mae"]


def test_no_src_paths_and_no_algae_claim(client, sets):
    for s in sets["sets"]:
        j = client.get(f"/api/v3/drones/{s['id']}/frames").json()
        assert all("src" not in f for f in j["frames"])
        assert "algae" not in s["groups_labelled"]


def test_image_and_errors(client, sets):
    s = sets["sets"][0]
    j = client.get(f"/api/v3/drones/{s['id']}/frames").json()
    r = client.get(j["frames"][0]["image"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert len(r.content) <= 200 * 1024
    assert client.get("/api/v3/drones/nope/frames").status_code == 404
    assert client.get(f"/api/v3/drones/{s['id']}/img/..%2Findex.json").status_code == 404


def test_previews_budget():
    total = sum(p.stat().st_size for p in (IDX.parent / "img").rglob("*.jpg"))
    assert total <= 30 * 1024 * 1024
