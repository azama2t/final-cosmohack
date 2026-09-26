"""Приёмка 16:03 (reports/qa/matvey_acceptance.md, «Второй проход»): п.1 — полосы обследования под рамкой района
(фильтр + честная причина в API / CSV / GeoJSON); п.4 — 5 обязательных примеров защиты (MATVEY_ACCEPTANCE п.4)."""
from __future__ import annotations

import json
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from service import case_store as cs
from service.app import app

HONDURAS = "-88.381,15.627,-88.003,16.006"  # рамка зон района «Гондурас · Омоа» (+0.1°)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setitem(cs.PATHS, "queries", tmp_path / "queries.jsonl")
    return TestClient(app)


def test_strips_follow_region_frame_with_reason(client):
    all_ = client.get("/api/v3/zones").json()
    assert all_["count"] > 0 and all_["filter_note"] and all_["empty_reason"] is None
    api = client.get("/api/v3/zones", params={"bbox": HONDURAS}).json()
    gj = json.loads(client.get("/api/v3/export", params={"layer": "zones", "format": "geojson", "bbox": HONDURAS}).content)
    r = client.get("/api/v3/export", params={"layer": "zones", "format": "csv", "bbox": HONDURAS})
    n_csv = len(r.content.decode("utf-8-sig").strip().splitlines()) - 1
    assert api["count"] == gj["count"] == n_csv == 0  # the район frame filters the strips: API = GeoJSON = CSV
    assert api["empty_reason"].startswith("В рамке района нет полос обследования")
    assert gj["empty_reason"] == api["empty_reason"] == unquote(r.headers["x-empty-reason"])
    assert unquote(r.headers["x-filter-note"]) == gj["filter_note"] == cs.STRIPS_FILTER_NOTE
    assert api["filters_applied"]["bbox"] == [float(x) for x in HONDURAS.split(",")]
    # a field aquatoria still selects by linked samples, same count in export
    for src in cs.SOURCE_IDS:
        a = client.get("/api/v3/zones", params={"source": src}).json()["count"]
        c = client.get("/api/v3/export", params={"layer": "zones", "format": "csv", "source": src}).content
        assert a == len(c.decode("utf-8-sig").strip().splitlines()) - 1


def test_defense_examples_five_with_evidence(client):
    d = client.get("/api/v3/defense_examples").json()
    kinds = [e["kind"] for e in d["examples"]]
    assert kinds == ["success", "miss", "false_alarm", "background_error", "no_analysis"] and d["missing"] == []
    assert d["settings"]["threshold"] and d["settings"]["model_sha256"] and d["settings"]["rules"]
    four = {"обнаружено", "не обнаружено", "недостаточно данных", "исследовательская оценка"}
    for e in d["examples"]:
        for k in ("reference", "verdict", "basis", "status_explanation", "rule"):
            assert e[k], (e["kind"], k)
        assert e["status_label"] in four and e["geometry"]["type"] in ("Polygon", "MultiPolygon")
        assert e["image"]["rgb_url"] and e["image"]["quality_url"]
        for u in (e["image"]["crop_url"], e["image"]["rgb_url"], e["image"]["quality_url"]):
            if u:
                assert client.get(u).status_code == 200, u
        assert e["repeat"]["command"].startswith(".venv/Scripts/python.exe scripts/case/")
        # texts shown to the jury: no service paths / keys (the path goes to `source`, not shown)
        texts = [e[k] for k in ("label", "title", "reference", "verdict", "basis", "status_explanation", "rule")]
        texts += [e["image"]["crop_note"], e.get("status_note") or ""]
        for t in texts:
            for bad in ("final_numbers", "reports/", ".md", ".py", "scripts/", "src/", "_px_", "n_pixels"):
                assert bad not in t, (e["kind"], bad, t)
        assert e["source"]
        if e["zone_id"]:  # zone examples agree with the zone API
            z = client.get(f"/api/v3/scene_zones/{e['zone_id']}").json()
            zp = z.get("properties", z)
            assert zp["status_label"] == e["status_label"]
    ex = {e["kind"]: e for e in d["examples"]}
    assert "level_B" not in ex["success"]["reference"] and "Cózar" in ex["success"]["reference"]
    assert ex["miss"]["model_result"]["n_pixels_in_bbox"] < 0.5 * int(ex["miss"]["reference"].split(", ")[-1].split()[0])
    assert ex["miss"]["status_note"] == "статус зоны, задевшей нить: обнаружено; большая часть нити не отмечена"
    assert "2,8 %" in ex["miss"]["basis"]
    assert set(ex["background_error"]["model_result"]["flags"]) <= {"foam", "glint"}
    assert "ship" in ex["false_alarm"]["model_result"]["flags"]
    assert ex["no_analysis"]["verdict"] == "анализ невозможен" and ex["no_analysis"]["model_result"]["n_zones"] is None
    assert client.get("/api/v3/defense_examples", params={"x": "1"}).status_code == 400
