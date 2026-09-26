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
            assert "GoogleMapsCompatible_Level9" in l["tile_url_gibs"] and l["max_native_zoom"] == 9
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


# ------------------------------------------------------------------ §60 А2: GIBS tile proxy (network mocked)
@pytest.fixture()
def tiles(monkeypatch, tmp_path):
    monkeypatch.setattr(rn, "_today_utc", lambda: TODAY)
    monkeypatch.setattr(rn, "TILE_CACHE", tmp_path / "nasa_tiles")
    monkeypatch.setitem(rn._tile_bytes, "total", None)
    calls = []

    def fetch(url):
        calls.append(url)
        if "/9/300/" in url:
            return 404, None, ""
        if "/9/301/" in url:
            return 0, None, ""
        return 200, b"\xff\xd8JPEGDATA" + url.encode()[-8:], "image/jpeg"
    monkeypatch.setattr(rn, "_fetch_tile", fetch)
    return TestClient(app), calls


def test_layers_tile_url_is_our_proxy(client):
    j = client.get("/api/v3/nasa/layers").json()
    for l in j["layers"]:
        assert l["tile_url"].startswith("/api/v3/nasa/tile/" + l["id"] + "/{date}/{z}/{y}/{x}.")
        assert l["tile_url_gibs"].startswith("https://gibs.earthdata.nasa.gov/") and l["proxied"] is True


def test_tile_proxy_fetch_then_cache(tiles):
    c, calls = tiles
    u = "/api/v3/nasa/tile/VIIRS_SNPP_CorrectedReflectance_TrueColor/2026-09-20/3/2/5.jpg"
    r = c.get(u)
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/jpeg")
    assert r.headers["x-cache"] == "MISS" and "NASA" in r.headers["x-attribution"]
    assert "max-age" in r.headers["cache-control"]
    assert calls == ["https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_SNPP_CorrectedReflectance_TrueColor/default/"
                     "2026-09-20/GoogleMapsCompatible_Level9/3/2/5.jpg"]
    r2 = c.get(u)
    assert r2.status_code == 200 and r2.headers["x-cache"] == "HIT" and r2.content == r.content and len(calls) == 1


@pytest.mark.parametrize("path,code", [
    ("NOT_A_LAYER/2026-09-20/3/2/5.jpg", 404),
    ("VIIRS_SNPP_CorrectedReflectance_TrueColor/2026-09-20/3/2/5.png", 404),       # wrong extension
    ("VIIRS_SNPP_CorrectedReflectance_TrueColor/2026-09-27/3/2/5.jpg", 422),       # future (UTC)
    ("VIIRS_SNPP_CorrectedReflectance_TrueColor/2025-09-01/3/2/5.jpg", 422),       # older than a year
    ("VIIRS_SNPP_CorrectedReflectance_TrueColor/26-09-2026/3/2/5.jpg", 422),
    ("VIIRS_SNPP_CorrectedReflectance_TrueColor/2026-09-20/10/2/5.jpg", 422),      # z > max_zoom 9
    ("VIIRS_SNPP_CorrectedReflectance_TrueColor/2026-09-20/3/8/5.jpg", 422),       # y ≥ 2^z
    ("VIIRS_SNPP_CorrectedReflectance_TrueColor/2026-09-20/9/300/5.jpg", 404),     # GIBS 404
    ("VIIRS_SNPP_CorrectedReflectance_TrueColor/2026-09-20/9/301/5.jpg", 504),     # GIBS timeout
])
def test_tile_proxy_rejects(tiles, path, code):
    c, calls = tiles
    r = c.get("/api/v3/nasa/tile/" + path)
    assert r.status_code == code
    if code == 422 or path.startswith("NOT_A") or path.endswith(".png"):
        assert calls == []  # nothing outside the allow-list goes upstream


def test_tile_cache_evicts_oldest(tiles, monkeypatch):
    import os
    c, _ = tiles
    monkeypatch.setattr(rn, "TILE_CACHE_MAX_BYTES", 40)
    for i, x in enumerate((1, 2, 3)):
        c.get(f"/api/v3/nasa/tile/MODIS_Terra_CorrectedReflectance_TrueColor/2026-09-20/2/1/{x}.jpg")
        for f in rn.TILE_CACHE.rglob("*.jpg"):
            if f.name == f"{x}.jpg":
                os.utime(f, (1000 + i, 1000 + i))
    left = sorted(f.name for f in rn.TILE_CACHE.rglob("*.jpg"))
    total = sum(f.stat().st_size for f in rn.TILE_CACHE.rglob("*.jpg"))
    assert total <= 40 and "3.jpg" in left and "1.jpg" not in left
