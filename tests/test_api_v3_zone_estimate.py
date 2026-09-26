"""§34 п.2 (26.09 12:58): research estimate items/km2 of satellite-zone finds (PLP target calibration) — formula = API =
export; null + reason for non-finds; jury-human 12:56 T5: the zone export follows the UI filters (aquatoria, dates,
status) and carries is_find."""
from __future__ import annotations

import csv
import io
import json
import math

import pytest
from fastapi.testclient import TestClient

from service import case_store as cs
from service.app import app
from macroplastic.case import zone_estimate as ZE

HAS = (cs.PATHS["scene_zones_dir"] / "index.json").is_file()
pytestmark = pytest.mark.skipif(not HAS, reason="scripts/case/scene_zones.py not run")

WORDS = ("исследовательская оценка", "калибровка на искусственных мишенях PLP", "бутылки", "1.5 л",
         "для природных скоплений не проверена", "мелкие предметы → больше штук")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setitem(cs.PATHS, "queries", tmp_path / "queries.jsonl")
    return TestClient(app)


def _csv_rows(client, **params) -> list[dict]:
    body = client.get("/api/v3/export", params={"layer": "scene_zones", "format": "csv", **params}).content.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(body)))


def _gj(client, **params) -> dict:
    return json.loads(client.get("/api/v3/export", params={"layer": "scene_zones", "format": "geojson", **params}).content)


def _sig3(x: float):
    v = float(f"{x:.3g}")
    return int(v) if abs(v) >= 100 else v


def test_calibration_from_config_all_points():
    """lo/hi = min/max items per pixel over all calibration points (bottles fraction × 100 m² × 16.64/m²), rounded to 10."""
    cfg = ZE.load_config()
    cal = ZE.calibration(cfg)
    raw = [p["bottles_fraction"] * cfg["pixel_m2"] * p["bottles_per_m2"] for p in cfg["calibration_points"]]
    assert (cal["lo"], cal["hi"]) == (round(min(raw), -1), round(max(raw), -1)) == (470, 670)
    assert cal["value"] == round(math.sqrt(470 * 670), 2) and cal["n_dates"] == len({str(p["date"]) for p in cfg["calibration_points"]})
    assert "115_bridge_s2.md" in cfg["source"]
    # a new A* date (calibration update) changes the interval without code changes
    cfg2 = {**cfg, "calibration_points": cfg["calibration_points"] + [
        {"date": "2099-01-01", "bottles_fraction": 0.8, "bottles_per_m2": 16.64}]}
    assert ZE.calibration(cfg2)["hi"] == round(0.8 * 100 * 16.64, -1) and ZE.calibration(cfg2)["n_dates"] == cal["n_dates"] + 1


def test_formula_equals_api_equals_export_on_5_zones(client):
    fc = client.get("/api/v3/scene_zones").json()
    finds = [f for f in fc["features"] if f["properties"]["research_estimate"]]
    assert len(finds) >= 5
    # 5 zones: the biggest demo (Cózar) zone, smallest and largest estimate, two others
    finds.sort(key=lambda f: f["properties"]["research_estimate"]["value"])
    demo = max((f for f in finds if f["properties"]["scene_kind"] == "demo"), key=lambda f: f["properties"]["measured"]["n_pixels"])
    pick = {f["id"]: f for f in [demo, finds[0], finds[-1], finds[len(finds) // 3], finds[2 * len(finds) // 3]]}
    assert len(pick) == 5
    rows = {r["zone_id"]: r for r in _csv_rows(client)}
    gj = {f["id"]: f["properties"] for f in _gj(client)["features"]}
    for zid, f in pick.items():
        p = f["properties"]
        n, a = p["measured"]["n_pixels"], p["measured"]["zone_area_km2"]
        assert a == p["area_km2"]  # area — a separate field, the denominator of the estimate
        want = (_sig3(n * math.sqrt(470 * 670) / a), _sig3(n * 470 / a), _sig3(n * 670 / a))
        re = p["research_estimate"]
        assert (re["value"], re["lo"], re["hi"]) == want, zid
        assert re["lo"] <= re["value"] <= re["hi"]
        assert (re["n_items"]["lo"], re["n_items"]["hi"]) == (n * 470, n * 670)
        assert re["unit"] == "шт./км²" and re["status"] == "исследовательская оценка" and re["status_id"] == "research_estimate"
        assert "area_km2" not in re  # area not mixed into the estimate
        # audit 13:10: N of the zone next to C, contour basis, detector share of the contour, «2 сработавших пикселя»
        assert re["n_items_label"].startswith("N ≈") and "контура зоны" in re["basis"]
        assert re["det_px_share_of_zone_pct"] == round(n * 100 / 1e6 / a * 100, 2)
        assert "сработавших пикселей" in re["not_what"] and "не доверительный" in re["not_what"]
        for w in WORDS:
            assert w in re["label"]
        assert "измерено" not in re["label"]
        # export CSV / GeoJSON: the same numbers
        r = rows[zid]
        assert (float(r["research_estimate_value"]), float(r["research_estimate_lo"]), float(r["research_estimate_hi"])) == want
        assert float(r["zone_area_km2"]) == a and r["research_estimate_unit"] == "шт./км²"
        assert r["research_estimate_status"] == "исследовательская оценка" and r["is_find"] == "true"
        assert (int(r["research_n_items_lo"]), int(r["research_n_items_hi"])) == (n * 470, n * 670)
        assert (int(r["research_items_per_pixel_lo"]), int(r["research_items_per_pixel_hi"])) == (470, 670)
        assert r["research_estimate_reason"] == ""
        assert gj[zid]["research_estimate"] == re


def test_null_with_reason_for_non_finds(client):
    fc = client.get("/api/v3/scene_zones").json()
    rows = {r["zone_id"]: r for r in _csv_rows(client)}
    n_null = 0
    for f in fc["features"]:
        p = f["properties"]
        find_ok = (p["detection_status"] == "detected" and not p.get("training_scene") and not p["flags"]
                   and not p.get("wind_high"))
        assert bool(p["research_estimate"]) == find_ok == bool(p["is_find"] and not p.get("wind_high"))
        if not find_ok:
            n_null += 1
            assert p["research_estimate"] is None and p["research_estimate_reason"]
            assert p["concentration_status"] == "unavailable"
            r = rows[p["zone_id"]]
            assert r["research_estimate_value"] == "" and r["research_estimate_reason"] == p["research_estimate_reason"]
            if "ship" in p["flags"]:
                assert "судно" in p["research_estimate_reason"]
            if "wind" in p["flags"]:
                assert "ветер" in p["research_estimate_reason"]
            if p["detection_status"] == "not_detected":
                assert "не обнаружено" in p["research_estimate_reason"]
            if p["detection_status"] == "detected" and p.get("training_scene"):
                assert "обучения детектора" in p["research_estimate_reason"]
    assert n_null > 0
    assert any(f["properties"]["detection_status"] == "detected" and f["properties"].get("training_scene")
               and f["properties"]["research_estimate"] is None for f in fc["features"])


def test_export_follows_aquatoria_filter_jury_1256(client):
    """UI: any field «Акватория» (source) hides satellite zones (0 finds) — the export gives 0 rows as well; with dates too."""
    total = client.get("/api/v3/scene_zones").json()["total"]
    meta = client.get("/api/v3/meta").json()
    for s in meta["sources"]:
        api = client.get("/api/v3/scene_zones", params={"source": s["id"]}).json()
        assert api["total"] == 0 and "region" in api["empty_reason"]
        assert _csv_rows(client, source=s["id"]) == []
        g = _gj(client, source=s["id"])
        assert g["count"] == 0 and g["empty_reason"] == api["empty_reason"]
        assert _csv_rows(client, source=s["id"], date_from="2021-03-01", date_to="2021-03-31") == []
    prof = meta["measurement_profiles"][0]["id"]
    assert _csv_rows(client, profile=prof) == [] and _gj(client, profile=prof)["count"] == 0
    # dates only: UI count = export count (as before)
    d = {"date_from": "2021-03-01", "date_to": "2021-03-31"}
    n = client.get("/api/v3/scene_zones", params=d).json()["total"]
    assert 0 < n < total and len(_csv_rows(client, **d)) == n == _gj(client, **d)["count"]
    # status filter + dates
    d2 = {**d, "detection_status": "detected"}
    n2 = client.get("/api/v3/scene_zones", params=d2).json()["total"]
    assert len(_csv_rows(client, **d2)) == n2 > 0
    # plural alias of the saved-query object
    assert client.get("/api/v3/export", params={"layer": "scene_zones", "format": "csv", "sources": meta["sources"][0]["id"]}
                      ).status_code == 200
    # saved query with a field source: the same rule
    q = {"bbox": None, "date_from": None, "date_to": None, "statuses": [], "sources": [meta["sources"][0]["id"]],
         "profiles": [], "layers": ["zones"], "scene_id": None}
    qid = client.post("/api/v3/queries", json={"name": "акватория", "query": q}).json()["query_id"]
    assert client.get(f"/api/v3/queries/{qid}/run").json()["summary"]["n_scene_zones"] == 0
    assert json.loads(client.get("/api/v3/export", params={"layer": "scene_zones", "query_id": qid}).content)["count"] == 0


def test_is_find_in_export_and_filters(client):
    fc = client.get("/api/v3/scene_zones").json()
    finds = {f["id"] for f in fc["features"] if f["properties"]["is_find"]}
    detected = {f["id"] for f in fc["features"] if f["properties"]["detection_status"] == "detected"}
    rows = _csv_rows(client)
    assert {r["zone_id"] for r in rows if r["is_find"] == "true"} == finds
    assert len(finds) == client.get("/api/v3/meta").json()["headline"]["satellite"]["n_finds"]
    assert len(detected) >= len(finds)
    # training-scene detections are visible in the CSV (training_scene column)
    for r in rows:
        if r["zone_id"] in detected - finds:
            assert r["training_scene"] and r["is_find"] == "false"
    assert {f["id"] for f in client.get("/api/v3/scene_zones", params={"is_find": "true"}).json()["features"]} == finds
    assert len(_csv_rows(client, is_find="true")) == len(finds)
    assert client.get("/api/v3/scene_zones", params={"is_find": "maybe"}).status_code == 400


def test_region_filter_and_meta_regions(client):
    meta = client.get("/api/v3/meta").json()
    regs = meta["scene_zone_regions"]
    total = client.get("/api/v3/scene_zones").json()["total"]
    assert sum(r["n_zones"] for r in regs) == total
    assert sum(r["n_finds"] for r in regs) == meta["headline"]["satellite"]["n_finds"]
    r0 = regs[0]
    api = client.get("/api/v3/scene_zones", params={"region": r0["id"]}).json()
    assert api["total"] == r0["n_zones"] and all(f["properties"]["region"] == r0["id"] for f in api["features"])
    assert len(_csv_rows(client, region=r0["id"])) == r0["n_zones"]
    assert client.get("/api/v3/scene_zones", params={"region": "nowhere"}).status_code == 400
    sc = client.get("/api/v3/scene_zones/scenes").json()["scenes"]
    assert sum(s["n_finds"] for s in sc) == meta["headline"]["satellite"]["n_finds"] and all(s["region_short"] for s in sc)
    # «район · дата · N находок · облачность»: cloudiness of the crop (and of the tile) from the scene's scene.json
    assert all(s["cloud_pct"] is not None and 0 <= s["cloud_pct"] <= 100 for s in sc)
    assert all(s["tile_cloud_pct"] is None or 0 <= s["tile_cloud_pct"] <= 100 for s in sc)


def test_summary_same_code_as_final_numbers(client):
    """The final_numbers summary (ZE.summary_from_dir, read by collect_search) = the API summary."""
    s_dir = ZE.summary_from_dir(cs.PATHS["scene_zones_dir"])
    fc = client.get("/api/v3/scene_zones").json()
    s_api = fc["research_estimate"]
    h = client.get("/api/v3/meta").json()["headline"]["satellite"]["research_estimate"]
    for k in ("n_zones_with_estimate", "c_median", "c_min", "c_max", "items_per_pixel_lo", "items_per_pixel_hi",
              "c_pooled", "n_items_total_lo", "n_items_total_hi"):
        assert s_dir[k] == s_api[k] == h[k], k
    vals = sorted(f["properties"]["research_estimate"]["value"] for f in fc["features"] if f["properties"]["research_estimate"])
    assert s_api["n_zones_with_estimate"] == len(vals) and (s_api["c_min"], s_api["c_max"]) == (vals[0], vals[-1])
