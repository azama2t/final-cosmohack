"""§51 (замечания эксперта с чекпоинта): п.2 независимая оценка по полю у каждой зоны + единицы; п.4 крупные скопления;
п.5 интегральная оценка снимка и района; п.3 динамика района по датам. Числа — из данных, API = CSV."""
from __future__ import annotations

import csv
import io
import math

import pytest
from fastapi.testclient import TestClient

from service import case_store as cs
from service.app import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setitem(cs.PATHS, "queries", tmp_path / "queries.jsonl")
    return TestClient(app)


def _csv(client, **params) -> dict:
    body = client.get("/api/v3/export", params={"layer": "scene_zones", "format": "csv", **params}).content
    return {r["zone_id"]: r for r in csv.DictReader(io.StringIO(body.decode("utf-8-sig")))}


def test_field_estimate_units_quantity_by_image(client):
    fc = client.get("/api/v3/scene_zones", params={"limit": 1000}).json()
    rows = _csv(client)
    n_acc = sum(1 for p in cs.pairs_all() if p["status"] == "accepted")
    region_km = cs._s51_cfg()["region_km"]
    for f in fc["features"]:
        p = f["properties"]
        fe, un = p["field_estimate"], p["units"]
        assert fe["trust_note"] == "независимая оценка по полю, не по снимку" and fe["unit"] == "items/km2"
        assert p["quantity_by_image"] == f"не определено (принятых пар {n_acc})"
        if fe["in_region"]:
            assert fe["basis"] == "nearest_record"
            assert fe["value"] is not None and fe["distance_km"] <= region_km and fe["date"] and fe["source"]
            assert fe["reason"] is None
        elif fe["basis"] == "basin_profile":  # the basin profile, signed as not a measurement of this spot
            bp = fe["basin_profile"]
            assert fe["value"] == bp["pooled_items_km2"] and bp["n"] >= 5 and bp["years"] and bp["nearest_km"] > region_km
            assert bp["lo95"] <= fe["value"] <= bp["hi95"] and fe["reason"] is None
            assert "профиль акватории по полевым данным — не измерение этого участка" in fe["label"]
            assert p["region"] in next(b["regions"] for b in cs._basins() if b["id"] == bp["basin"])
        else:  # neither: explicit reason
            assert fe["basis"] is None and fe["value"] is None
            assert fe["reason"].startswith("в районе полевых измерений нет") and "профиля акватории нет" in fe["reason"]
        # the nearest record is a real row of the organisers' CSV at that distance
        if fe["nearest"]:
            r = cs.sample_row(fe["nearest"]["sample_id"])
            assert float(r["concentration_items_km2"]) == fe["nearest"]["value"]
        assert un["default"] == "items_km2" and un["area_note"] == "площадь, не предметы"
        m = p["measured"]
        if p["detected_area_m2"] is not None and (m.get("water_km2") or p["area_km2"]):
            exp = p["detected_area_m2"] / (m.get("water_km2") or p["area_km2"])
            assert math.isclose(un["area_m2_per_km2"], round(exp, 1), abs_tol=0.051)
            assert math.isclose(un["coverage_pct"], round(un["area_m2_per_km2"] / 1e4, 4), abs_tol=1e-4)
        r = rows[p["zone_id"]]
        assert r["quantity_by_image"] == p["quantity_by_image"]
        assert r["field_in_region"] == str(fe["in_region"]).lower() and r["field_reason"] == (fe["reason"] or "")
        assert r["area_note"] == "площадь, не предметы"


def test_is_large_threshold_and_scene_summary(client):
    thr, ax_thr = cs._s51_cfg()["large_km2"], cs._s51_cfg()["large_axis_m"]
    assert (thr, ax_thr) == (0.1, 500)  # configs/zone_estimate.yaml: large_mask_km2, large_axis_m
    fc = client.get("/api/v3/scene_zones", params={"limit": 1000}).json()
    rows = _csv(client)
    per: dict[str, list] = {}
    for f in fc["features"]:
        p = f["properties"]
        mask_km2 = (p["detected_area_m2"] or 0) / 1e6
        exp = bool(p["is_find"] and (mask_km2 >= thr or (p["major_axis_m"] or 0) >= ax_thr))
        assert p["is_large"] == exp
        if exp:
            assert ("площадь маски" in p["large_reason"]) == (mask_km2 >= thr)
            assert ("большая ось" in p["large_reason"]) == (p["major_axis_m"] >= ax_thr)
        else:
            assert p["large_reason"] is None
        # the axis has no buffer: it is shorter than the diagonal of the zone contour bbox
        if p["major_axis_m"] is not None:
            b = cs._geom_bounds(f["geometry"])
            diag = math.hypot((b[2] - b[0]) * 111320 * math.cos(math.radians((b[1] + b[3]) / 2)), (b[3] - b[1]) * 110570)
            assert p["major_axis_m"] < diag
        assert rows[p["zone_id"]]["is_large"] == str(p["is_large"]).lower()
        if p["is_large"]:
            per.setdefault(p["scene_key"], []).append(p["area_km2"])
    sc = {s["scene_key"]: s for s in client.get("/api/v3/scene_zones/scenes").json()["scenes"]}
    for k, s in sc.items():
        if not s["evaluable"]:
            assert s["n_large"] is None
            continue
        assert s["n_large"] == len(per.get(k, [])) and s["large_threshold_km2"] == thr
        assert math.isclose(s["large_area_km2"], round(sum(per.get(k, [])), 4), abs_tol=1e-4)


def test_scene_and_region_integral(client):
    fc = client.get("/api/v3/scene_zones", params={"limit": 1000}).json()
    sc = {s["scene_key"]: s for s in client.get("/api/v3/scene_zones/scenes").json()["scenes"]}
    agg: dict[str, list] = {}
    for f in fc["features"]:
        p = f["properties"]
        if p["is_find"]:
            a = agg.setdefault(p["scene_key"], [0.0, 0.0])
            a[0] += p["area_km2"]
            a[1] += p["detected_area_m2"] / 1e6
    for k, s in sc.items():
        if not s["evaluable"]:
            assert s["total_find_area_km2"] is None and s["coverage_pct"] is None
            continue
        area, mask = agg.get(k, [0.0, 0.0])
        assert math.isclose(s["total_find_area_km2"], round(area, 4), abs_tol=1e-4)
        assert math.isclose(s["find_mask_area_km2"], round(mask, 4), abs_tol=1e-4)
        if not s["valid_water_km2"]:
            assert s["coverage_pct"] is None
        else:
            assert math.isclose(s["coverage_pct"], round(mask / s["valid_water_km2"] * 100, 4), abs_tol=2e-4)
        assert s["integral_note"].startswith("площадь, не число предметов")
    regs = client.get("/api/v3/meta").json()["scene_zone_regions"]
    for r in regs:
        g = r["integral"]
        rows = [s for s in sc.values() if s["region"] == r["id"] and s["evaluable"]]
        assert g["n_evaluable"] == len(rows) and g["n_finds"] == r["n_finds"]
        assert math.isclose(g["total_find_area_km2"], sum(s["total_find_area_km2"] for s in rows), abs_tol=1e-3)
        assert "площадь, не число предметов" in g["note"]


def test_region_dynamics(client):
    regs = client.get("/api/v3/meta").json()["scene_zone_regions"]
    sc = {s["scene_key"]: s for s in client.get("/api/v3/scene_zones/scenes").json()["scenes"]}
    thr_km, max_days = cs._s51_cfg()["region_km"], cs._s51_cfg()["max_days"]
    for r in regs:
        d = client.get(f"/api/v3/regions/{r['id']}/dynamics").json()
        assert d["region"] == r["id"] and d["count"] == r["n_scenes"] == len(d["rows"])
        assert d["summary"] == r["integral"]
        dates = [x["datetime"] for x in d["rows"]]
        assert dates == sorted(dates)
        for x in d["rows"]:
            s = sc[x["scene_key"]]
            assert x["n_finds"] == (s["n_finds"] if x["evaluable"] else None)
            assert x["total_find_area_km2"] == s["total_find_area_km2"] and x["cloud_pct"] == s["cloud_pct"]
            if x["field_items_km2"] is None:
                assert x["field_reason"] and x["field"] is None
            else:
                assert x["field"]["distance_km"] <= thr_km
                days = abs((__import__("datetime").date.fromisoformat(x["field"]["date"])
                            - __import__("datetime").date.fromisoformat(x["date"])).days)
                assert days <= max_days
    assert client.get("/api/v3/regions/nope/dynamics").status_code == 404
    assert client.get("/api/v3/regions/honduras/dynamics", params={"x": 1}).status_code == 400
