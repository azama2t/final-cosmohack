"""§55 п.1 «Реальное время»: /api/v3/fresh_s2 (+ /refresh, /zones, /crops) на временном наборе; без сети и без запуска
обработки (процесс подменён)."""
from __future__ import annotations

import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from service import routes_nasa as rn
from service.app import app

TODAY = dt.date(2026, 9, 26)


def _zone(key, i, st):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
            "properties": {"zone_id": f"SZ-{key}-{i:03d}", "detection_status": st, "wind_high": False}}


def _scene(tmp, region, date, zones):
    key = f"fresh-{region}-{date}"
    d = tmp / key
    (d / "crops").mkdir(parents=True)
    (d / "zones.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": zones}), encoding="utf-8")
    (d / "rgb.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (d / "crops" / "z1.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    n_z = sum(not z["properties"]["zone_id"].endswith("-000") for z in zones)
    n_f = sum(z["properties"]["detection_status"] == "detected" and not z["properties"]["zone_id"].endswith("-000")
              for z in zones)
    return {"key": key, "region": region, "region_name": region.title(), "date": date,
            "datetime": f"{date}T10:00:00Z", "bounds": [10, 40, 11, 41], "evaluable": True, "n_zones_total": len(zones),
            "n_zones": n_z, "n_finds": n_f, "processed_at": "2026-09-26T19:00:00Z", "processing_s": 61.0,
            "source": "Sentinel-2 L2A (STAC)"}


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    k1, k2, k3 = "fresh-tiber-2026-09-20", "fresh-tiber-2026-09-25", "fresh-nile-2026-08-01"
    scenes = [
        _scene(tmp_path, "tiber", "2026-09-20", [_zone(k1, 0, "not_detected")]),
        _scene(tmp_path, "tiber", "2026-09-25", [_zone(k2, 1, "detected"), _zone(k2, 2, "insufficient_data")]),
        _scene(tmp_path, "nile", "2026-08-01", [_zone(k3, 1, "detected")]),  # старше 30 дней — не в счётчике
    ]
    idx = {"generated": "2026-09-26T19:05:00Z", "last_update": "2026-09-26T19:05:00Z", "new_scenes_last_run": 2,
           "label": rn.FRESH_LABEL, "model": {"weights": "weights/lgbm", "threshold": 0.63}, "scenes": scenes}
    (tmp_path / "index.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(rn, "FRESH_DIR", tmp_path)
    monkeypatch.setattr(rn, "_today_utc", lambda: TODAY)
    monkeypatch.setattr(rn, "REFRESH_LOCK", tmp_path / "refresh.lock")
    monkeypatch.setattr(rn, "REFRESH_LOG", tmp_path / "refresh.log")
    rn._refresh_state.update(proc=None, started=None)
    yield TestClient(app)
    rn._refresh_state.update(proc=None, started=None)


def test_counters_and_labels(fresh):
    j = fresh.get("/api/v3/fresh_s2").json()
    assert j["folder"] == "Реальное время" and j["subfolder"] == "Sentinel-2 · обработано нашей моделью"
    assert j["scenes_30d"] == 2 and j["zones_30d"] == 2 and j["finds_30d"] == 1
    assert j["counter"] == "за 30 дней обработано 2 снимков, найдено 2 зон, из них находок 1"
    assert j["last_update"] == "2026-09-26T19:05:00Z" and j["new_scenes_last_run"] == 2
    assert j["human_checked"] is False and j["in_case_numbers"] is False
    assert j["label"] == "автоматически, не проверено человеком"
    for s in j["scenes"]:
        assert s["label"] == "автоматически, не проверено человеком" and s["human_checked"] is False
        assert s["processed_at"] and s["date"] and s["mission"] == "Sentinel-2" and "Sentinel-2" in s["source"]


def test_tree_regions_dates(fresh):
    j = fresh.get("/api/v3/fresh_s2").json()
    by = {r["id"]: r for r in j["regions"]}
    assert set(by) == {"tiber", "nile"}
    t = by["tiber"]
    assert t["n_scenes"] == 2 and t["n_zones"] == 2 and t["n_finds"] == 1 and t["last_date"] == "2026-09-25"
    assert [d["date"] for d in t["dates"]] == ["2026-09-25", "2026-09-20"]  # свежие первыми
    assert t["dates"][0]["zones_url"] == "/api/v3/fresh_s2/fresh-tiber-2026-09-25/zones"
    assert t["center"] == [10.5, 40.5]


def test_honesty_nasa_not_run(fresh):
    h = fresh.get("/api/v3/fresh_s2").json()["honesty"]
    assert "250–375 м" in h["nasa"] and "не запускаем" in h["nasa"] and "10 м" in h["nasa"]
    assert "Sentinel-2" in h["detector_on"] and "2–5 дней" in h["detector_on"]
    assert "нет" in h["quantity"]


def test_zones_rgb_crops(fresh):
    fc = fresh.get("/api/v3/fresh_s2/fresh-tiber-2026-09-25/zones").json()
    assert fc["label"] == "автоматически, не проверено человеком"
    for f in fc["features"]:
        p = f["properties"]
        assert p["layer_kind"] == "fresh_s2" and p["human_checked"] is False and p["in_case_numbers"] is False
    assert fresh.get("/api/v3/fresh_s2/fresh-tiber-2026-09-25/rgb.jpg").status_code == 200
    assert fresh.get("/api/v3/fresh_s2/fresh-tiber-2026-09-25/crops/z1.jpg").status_code == 200
    assert fresh.get("/api/v3/fresh_s2/fresh-tiber-2026-09-25/crops/..%5Cindex.json").status_code == 404
    assert fresh.get("/api/v3/fresh_s2/nope/zones").status_code == 404


class _Proc:
    def __init__(self):
        self.rc = None

    def poll(self):
        return self.rc


def test_refresh_once_then_409(fresh, monkeypatch):
    calls = []
    proc = _Proc()
    monkeypatch.setattr(rn, "_spawn", lambda cmd, log, env: calls.append((cmd, env)) or proc)
    r = fresh.post("/api/v3/fresh_s2/refresh?days=2")
    assert r.status_code == 200 and r.json()["started"] is True
    cmd, env = calls[0]
    assert any(c.endswith("live_batch.py") for c in cmd) and "--days" in cmd and "2" in cmd
    assert env["CUDA_VISIBLE_DEVICES"] == ""
    assert fresh.get("/api/v3/fresh_s2").json()["refresh"]["running"] is True
    assert fresh.post("/api/v3/fresh_s2/refresh").status_code == 409  # параллельный запуск запрещён
    assert len(calls) == 1
    proc.rc = 0  # закончился -> можно снова
    assert fresh.post("/api/v3/fresh_s2/refresh").status_code == 200 and len(calls) == 2


def test_refresh_blocked_by_lock(fresh, monkeypatch, tmp_path):
    monkeypatch.setattr(rn, "_spawn", lambda *a: pytest.fail("не должен запускаться"))
    (tmp_path / "refresh.lock").write_text("123 daily", encoding="utf-8")
    r = fresh.post("/api/v3/fresh_s2/refresh")
    assert r.status_code == 409 and "другой запуск" in r.json()["detail"]


def test_funnel_window_catalog(fresh, tmp_path):
    idx = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    idx["funnel"] = {"window_days": 30, "window_label": "поиск за: 1 мес", "found": 10, "downloaded": 10,
                     "passed_quality": 4, "processed": 3, "with_finds": 1, "zones": 2, "finds": 1}
    idx["search_window"] = {"days": 30, "label": "поиск за: 1 мес"}
    idx["last_success"] = "2026-09-26T19:05:00Z"
    (tmp_path / "index.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    cat = {"generated": "x", "scenes": [
        {"region": "tiber", "date": "2026-09-25", "status": "processed", "suitable": True, "key": "fresh-tiber-2026-09-25"},
        {"region": "tiber", "date": "2026-09-24", "status": "rejected_quality", "suitable": False,
         "reason": "облачность вырезки 0.90 > 0.4"},
        {"region": "nile", "date": "2026-09-24", "status": "not_in_pc", "suitable": True, "reason": "нет в PC"}]}
    (tmp_path / "catalog.json").write_text(json.dumps(cat, ensure_ascii=False), encoding="utf-8")
    j = fresh.get("/api/v3/fresh_s2").json()
    assert j["funnel_label"] == ("поиск за: 1 мес: найдено 10 → скачано 10 → исключено 6 → обработано 3 → "
                                 "снимков с находками 1 → находок 1; не проверено человеком")
    assert j["search_window"]["label"] == "поиск за: 1 мес"
    assert j["last_success_label"] == "последняя успешная обработка: 2026-09-26 19:05 UTC"
    ages = {s["date"]: s["age_label"] for s in j["scenes"]}
    assert ages["2026-09-25"] == "снят вчера" and "сегодня" not in ages["2026-08-01"]
    c = fresh.get("/api/v3/fresh_s2/catalog?region=tiber").json()
    assert c["n"] == 2 and {e["status"] for e in c["scenes"]} == {"processed", "rejected_quality"}
    assert next(e for e in c["scenes"] if e["status"] == "rejected_quality")["reason"].startswith("облачность")
    assert fresh.get("/api/v3/fresh_s2/catalog?status=not_in_pc").json()["n"] == 1
