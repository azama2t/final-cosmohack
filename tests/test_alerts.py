"""§51 п.6 / п.7 / п.10 — risk of stranding + importance rank + alert level (docs/ALERTS.md)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from service import case_store as cs
from service.app import app
from src.macroplastic.case import alerts as A

HAS = (cs.PATHS["scene_zones_dir"] / "index.json").is_file()
pytestmark = pytest.mark.skipif(not HAS, reason="scripts/case/scene_zones.py not run")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setitem(cs.PATHS, "queries", tmp_path / "queries.jsonl")
    return TestClient(app)


# ---------------------------------------------------------------- rule (docs/ALERTS.md), pure functions
def test_size_shore_drift_factors_are_bucketed_1_2_3():
    # size_factor now shares the §51 п.4 «крупное скопление» definition (is_large/major_axis_m), not a raw
    # area threshold — service/case_store.py:sz_large already decides is_large (mask area OR major axis)
    assert A.size_factor(is_large=True, major_axis_m=600) == 3
    assert A.size_factor(is_large=False, major_axis_m=150) == 2
    assert A.size_factor(is_large=False, major_axis_m=10) == 1
    assert A.size_factor(is_large=False, major_axis_m=None) == 1  # honest default, not a guess

    assert A.shore_factor(2.0) == 3
    assert A.shore_factor(10.0) == 2
    assert A.shore_factor(50.0) == 1
    assert A.shore_factor(None) == 1  # no geometry -> does not inflate importance

    assert A.drift_factor(80.0) == 3
    assert A.drift_factor(20.0) == 2
    assert A.drift_factor(1.0) == 1
    assert A.drift_factor(None) == 2  # no forecast -> neutral, not 0 and not max


def test_importance_rank_is_the_product_1_to_27():
    assert A.importance_rank(True, 600, 2.0, 80.0) == 27  # 3*3*3
    assert A.importance_rank(False, None, None, None) == 1 * 1 * 2  # size 1, shore 1, drift neutral 2


def test_alert_level_capped_without_independent_confirmation_or_with_likely_organic():
    # rank 27 (all factors max) would be «высокий», but without confirmation it is capped at «средний»
    assert A.alert_level_from_rank(27, confirmed=True) == "высокий"
    assert A.alert_level_from_rank(27, confirmed=False) == "средний"
    assert A.alert_level_from_rank(6, confirmed=False) == "средний"
    assert A.alert_level_from_rank(5, confirmed=False) == "слабый"
    # §51 п.9: a «likely organic» flag must not read as a high-priority plastic alert, even if confirmed
    assert A.alert_level_from_rank(27, confirmed=True, likely_organic=True) == "средний"


def test_material_bump_nets_up_bottles_down():
    assert A.material_bump("слабый", "thread from fishing net") == "средний"
    assert A.material_bump("высокий", "plastic bottle") == "слабый"
    assert A.material_bump("средний", "unlabelled fragment") == "средний"  # no keyword match -> unchanged


def test_shore_km_for_a_known_point_and_honest_null_without_geometry():
    km, reason = A.shore_km(-1.6, 35.5)  # Alboran Sea, near the Spanish/Moroccan coast
    assert reason is None and km is not None and 0 <= km < 50
    km2, reason2 = A.shore_km(None, None)
    assert km2 is None and reason2 == "нет геометрии"


def test_shore_km_uses_10m_contour_and_never_silently_zero_for_land():
    # L142 (§51 bugfix): the coarse 1:110m contour put real sea points inside a simplified land polygon -> shore_km=0
    # (19/77 finds). The finer 1:10m contour (data_cache/natural_earth) must be preferred, and a point genuinely on
    # land must come back as None + a reason, never as a silent 0.
    assert A._land()[1] == "Natural Earth 1:10m", "ne_10m_land.shp not found — run scripts/fetch_natural_earth_10m.py"
    km, reason = A.shore_km(-3.7, 40.4)  # middle of mainland Spain
    assert km is None and reason and "на суше" in reason


def test_non_find_gets_honest_nulls_not_a_guessed_rank():
    out = A.zone_alert(is_find=False, is_large=True, major_axis_m=600, lon=0.0, lat=0.0, region="x", date="2024-01-01",
                       confirmed=False)
    assert out["importance_rank"] is None and out["alert_level"] is None
    assert out["shore_km_reason"] == "не находка" and out["drift_reason"] == "не находка"


# ---------------------------------------------------------------- wired into the real zones (service/case_store.py)
def test_scene_zones_carry_alert_fields_and_never_fabricate_material():
    feats = cs.scene_zones_all()
    finds = [f["properties"] for f in feats if f["properties"].get("is_find")]
    assert finds, "expected at least one satellite find in the built scene_zones data"
    for p in finds:
        assert p["alert_level"] in ("слабый", "средний", "высокий")
        assert 1 <= p["importance_rank"] <= 27
        assert p["alert_material"] is None  # §51: material is never determined for satellite zones
        assert p["alert_material_note"]
        if p["shore_km"] is None:
            assert p["shore_km_reason"]
        if p["stranded_pct_72h"] is None:
            assert p["drift_reason"]  # e.g. "нет прогноза дрейфа для этой сцены" — never a silent 0
    non_finds = [f["properties"] for f in feats if not f["properties"].get("is_find")]
    if non_finds:
        assert non_finds[0]["alert_level"] is None
    # §51 п.9: an experimental «likely organic» flag never reads as a high-priority plastic alert
    for p in finds:
        if p.get("likely_organic"):
            assert p["alert_level"] != "высокий"


def test_scene_zones_api_exposes_alert_fields_and_filters_by_level(client):
    fc = client.get("/api/v3/scene_zones", params={"is_find": "true"}).json()
    assert fc["count"] > 0
    levels = {f["properties"]["alert_level"] for f in fc["features"]}
    assert levels <= {"слабый", "средний", "высокий"}
    one_level = next(iter(levels))
    filtered = client.get("/api/v3/scene_zones", params={"is_find": "true", "alert_level": one_level}).json()
    assert filtered["count"] > 0
    assert all(f["properties"]["alert_level"] == one_level for f in filtered["features"])
    bad = client.get("/api/v3/scene_zones", params={"alert_level": "critical"})
    assert bad.status_code == 400


def test_scene_zones_csv_has_alert_columns(client):
    r = client.get("/api/v3/export", params={"layer": "scene_zones", "format": "csv", "is_find": "true"})
    assert r.status_code == 200
    header = r.text.splitlines()[0]
    for col in ("shore_km", "shore_km_reason", "stranded_pct_72h", "drift_reason", "importance_rank", "alert_level",
                "alert_material", "alert_material_note"):
        assert col in header
