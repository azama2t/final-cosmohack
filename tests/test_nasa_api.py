"""§54 п.1: NASA GIBS «ежедневно» — /api/v3/nasa/{layers,latest,regions}; сеть подменена (без интернета)."""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from service import case_store as cs
from service import routes_nasa as rn
from service.app import app

TODAY = dt.date(2026, 9, 26)


@pytest.fixture()
def client(monkeypatch):
    rn._cache.clear()
    monkeypatch.setattr(rn, "_today_utc", lambda: TODAY)
    yield TestClient(app)
    rn._cache.clear()


def _mock(monkeypatch, answers: dict):
    """answers: date -> True/False/None; records probed URLs."""
    calls = []

    def probe(url):
        calls.append(url)
        return next((v for d, v in answers.items() if f"/{d}/" in url), False)
    monkeypatch.setattr(rn, "_probe", probe)
    return calls


def test_layers_contract(client):
    j = client.get("/api/v3/nasa/layers").json()
    ids = [l["id"] for l in j["layers"]]
    for need in ("VIIRS_SNPP_CorrectedReflectance_TrueColor", "MODIS_Terra_CorrectedReflectance_TrueColor",
                 "MODIS_Aqua_CorrectedReflectance_TrueColor"):
        assert need in ids
    assert j["default_layer"] in ids and j["folder"] == "NASA · ежедневно"
    for l in j["layers"]:
        assert l["crs"] == "EPSG:3857" and l["source"] == "NASA" and "NASA" in l["attribution"]
        assert "{date}" in l["tile_url"] and "{z}/{y}/{x}" in l["tile_url"]
        assert "пластик" in l["caption"] or "не обнаружение пластика" in l["caption"]
        if l["cadence"] == "daily":
            assert 250 <= l["resolution_m"] <= 375
            assert "GoogleMapsCompatible_Level9" in l["tile_url"] and l["max_native_zoom"] == 9
            assert l["caption"] == rn.CAPTION
    assert "пластик на таком разрешении не обнаруживается" in j["caption"]


def test_latest_today_is_partial(client, monkeypatch):
    calls = _mock(monkeypatch, {"2026-09-26": True})
    j = client.get("/api/v3/nasa/latest").json()
    assert j["date"] == "2026-09-26" and j["verified"] and j["partial"] and j["latest_full"] == "2026-09-25"
    assert "/2026-09-26/" in j["tile_url"] and len(calls) == 1
    # cached: no second network call
    j2 = client.get("/api/v3/nasa/latest").json()
    assert j2["cached"] and len(calls) == 1


def test_latest_falls_back_to_yesterday(client, monkeypatch):
    _mock(monkeypatch, {"2026-09-26": False, "2026-09-25": True})
    j = client.get("/api/v3/nasa/latest", params={"layer": "MODIS_Terra_CorrectedReflectance_TrueColor"}).json()
    assert j["date"] == "2026-09-25" and j["latest_full"] == "2026-09-25" and not j["partial"] and not j["unverified"]
    assert [c["date"] for c in j["checked"]] == ["2026-09-26", "2026-09-25"]


def test_latest_no_network_yesterday_unverified(client, monkeypatch):
    calls = _mock(monkeypatch, {"2026-09-26": None})
    j = client.get("/api/v3/nasa/latest").json()
    assert j["date"] == "2026-09-25" and j["unverified"] is True and j["verified"] is False
    assert "не проверена" in j["note"]
    client.get("/api/v3/nasa/latest")
    assert len(calls) == 2  # a network failure is not cached


def test_latest_unknown_layer_422(client):
    assert client.get("/api/v3/nasa/latest", params={"layer": "nope"}).status_code == 422


def test_regions_same_as_case(client):
    j = client.get("/api/v3/nasa/regions").json()
    got = {r["id"] for r in j["regions"]}
    assert got == {r["id"] for r in cs.sz_regions()}
    for r in j["regions"]:
        w, s, e, n = r["bbox"]
        assert w < e and s < n and w <= r["center"][0] <= e and s <= r["center"][1] <= n
        assert 3 <= r["zoom"] <= 9


def test_fresh_s2_separate_and_labelled(client):
    """«Свежий Sentinel-2»: отдельный набор, подпись «автоматически, не проверено человеком», не в основных зонах."""
    j = client.get("/api/v3/fresh_s2").json()
    assert j["label"] == "автоматически, не проверено человеком" and j["human_checked"] is False
    assert j["in_case_numbers"] is False and "Sentinel-2" in j["source"]
    main_keys = {s["key"] for s in cs.scene_zones_index().get("scenes") or []}
    for s in j["scenes"]:
        assert s["key"].startswith("fresh-") and s["key"] not in main_keys
        fc = client.get(s["zones_url"]).json()
        for f in fc["features"]:
            p = f["properties"]
            assert p["auto_label"] == "автоматически, не проверено человеком" and p["in_case_numbers"] is False
            assert p["status_label"]
        r = client.get(s["rgb_url"])
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    main_zone_scenes = {f["properties"].get("scene_key") for f in cs.scene_zones_all()}
    assert not any(str(k).startswith("fresh-") for k in main_zone_scenes)
    assert client.get("/api/v3/fresh_s2/nope/zones").status_code == 404
