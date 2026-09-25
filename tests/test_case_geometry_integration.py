"""Геометрия трансект PANGAEA подключена к реестру пар (find_pairs), маскам качества (pair_quality) и API v3.

Офлайн: нужны data/case/pangaea (fetch_pangaea.py) и data/case/geometry/transects.geojson (geometry.py).
"""
from __future__ import annotations

import importlib.util
import json

import pandas as pd
import pytest

from macroplastic.case import geometry as G

ROOT = G.ROOT
T18 = "S2:MSM41_litter-T18"
HE01 = "S3:HE460_MarLitter_transect01"
CFG = {"strip": {"point_buffer_m": 500, "min_half_width_m": 10}}

pytestmark = pytest.mark.skipif(not (G.PANGAEA_DIR / G.S2_XLSX).exists()
                                or not (G.OUT_DIR / "transects.geojson").exists(),
                                reason="нет data/case/pangaea или data/case/geometry")


def _load(name):
    spec = importlib.util.spec_from_file_location(f"l75_{name}", ROOT / "scripts" / "case" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pq():
    return _load("pair_quality")


@pytest.fixture(scope="module")
def fp():
    return _load("find_pairs")


@pytest.fixture(scope="module")
def samples():
    return pd.read_csv(G.SAMPLES, low_memory=False)


def _utm(lon, lat):
    return (32600 if lat >= 0 else 32700) + int((lon + 180) // 6) + 1


def _gap_mid(eid):
    seg, _ = G.build()
    g = seg[seg.event_id == eid].sort_values("segment")
    a = (g.lon_end.iloc[0], g.lat_end.iloc[0])
    b = (g.lon_start.iloc[1], g.lat_start.iloc[1])
    mids = [((r.lon_start + r.lon_end) / 2, (r.lat_start + r.lat_end) / 2) for r in g.itertuples()]
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), mids


# ------------------------------------------------------------------ pair_quality: strip
@pytest.mark.parametrize("eid", G.INTERRUPTED_S2)
def test_strip_multilinestring_does_not_cover_gap(pq, samples, eid):
    from pyproj import Transformer
    from shapely.geometry import Point
    g = pq.event_geometry(samples, eid, {"geometry": {"use": True, "allow_approx": True}})
    assert g["geometry_source"] == "pangaea_track" and g["geometry_status"] == "segment"
    assert len(g["lines"]) == 2
    gap, mids = _gap_mid(eid)
    epsg = _utm(*gap)
    poly, rule = pq.strip_polygon(g, epsg, CFG)
    assert "gap not covered" in rule and poly.geom_type == "MultiPolygon"
    tr = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    assert not poly.contains(Point(*tr.transform(*gap)))
    for m in mids:
        assert poly.contains(Point(*tr.transform(*m)))
    # полоса = 2 сегмента × 20 м (полуширина 10 м, минимум 1 пиксель): площадь ≈ Σ длин × 20 м
    seg, _ = G.build()
    L = float(seg[seg.event_id == eid].length_km_geodesic.sum())
    assert poly.area / 1e6 == pytest.approx(L * 0.02, rel=0.03)


def test_strip_without_geometry_is_csv_line_over_gap(pq, samples):
    """geometry.use=false -> прежнее поведение: отрезок начало-конец CSV (перерыв внутри полосы)."""
    from pyproj import Transformer
    from shapely.geometry import Point
    g = pq.event_geometry(samples, T18, {"geometry": {"use": False}})
    assert g["lines"] is None and g["geometry_source"] == "samples_csv"
    gap, _ = _gap_mid(T18)
    epsg = _utm(*gap)
    poly, rule = pq.strip_polygon(g, epsg, CFG)
    assert rule.startswith("transect line")
    # одна сплошная полоса по отрезку начало->конец (без разбиения на сегменты)
    assert poly.geom_type == "Polygon"


def test_he460_01_strip_is_line_not_circle(pq, samples):
    g = pq.event_geometry(samples, HE01, {"geometry": {"use": True, "allow_approx": True}})
    assert g["geometry_status"] == "reconstructed_approx" and len(g["lines"]) == 1
    poly, rule = pq.strip_polygon(g, 32632, CFG)
    assert "PANGAEA track" in rule
    assert poly.area / 1e6 == pytest.approx(15.102 * 0.02, rel=0.02)   # ~0.30 км², а не круг 0.785 км²
    g2 = pq.event_geometry(samples, HE01, {"geometry": {"use": True, "allow_approx": False}})
    poly2, rule2 = pq.strip_polygon(g2, 32632, CFG)
    assert g2["lines"] is None and rule2.startswith("point")


def test_he460_01_meta_uses_track():
    m = ROOT / "data" / "pairs" / "quality" / "S3_HE460_MarLitter_transect01" / "meta.json"
    if not m.exists():
        pytest.skip("нет meta.json пары HE460 transect01")
    meta = json.loads(m.read_text(encoding="utf-8"))
    assert meta["geometry_source"] == "pangaea_track" and meta["has_line"] is True
    assert "PANGAEA track" in meta["strip_rule"]
    assert meta["decision"] == "reject" and meta["reason"] == "cloud"


# ------------------------------------------------------------------ find_pairs
def test_find_pairs_geometry_default_and_switch(fp, tmp_path):
    ev = fp.build_events(pd.read_csv(G.SAMPLES))
    cfg = fp.load_geometry_cfg(ROOT / "configs" / "case_pairs.yaml")
    assert cfg == {"use": True, "allow_approx": True}
    empty = tmp_path / "c.yaml"
    empty.write_text("drift: {}\n", encoding="utf-8")
    assert fp.load_geometry_cfg(empty) == {"use": True, "allow_approx": True}
    out = fp.apply_geometry(ev)
    r0, r1 = ev.set_index("event_id").loc[T18], out.set_index("event_id").loc[T18]
    assert r1.point_from == "geometry_track_center" and r1.geometry_status == "segment"
    assert (r1.obs_datetime - r0.obs_datetime) == pd.Timedelta(minutes=6)    # середина усилия, не интервала
    assert len(json.loads(r1.geom_segment_windows)) == 2
    s4 = out[out.source_id == "S4_BLACK_SEA_DOORS3"]
    assert s4.geometry_status.isna().all()
    he = fp.apply_geometry(ev, allow_approx=False).set_index("event_id").loc[HE01]
    assert G.haversine_km((he.lon, he.lat), (ev.set_index("event_id").loc[HE01].lon,
                                             ev.set_index("event_id").loc[HE01].lat)) < 0.1


def test_dt_to_segments(fp):
    w = json.dumps([["2015-04-20T14:42:00Z", "2015-04-20T14:59:00Z"], ["2015-04-20T15:50:00Z", "2015-04-20T16:33:00Z"]])
    assert fp.dt_to_segments_h(pd.Timestamp("2015-04-20T14:50:00Z"), w) == 0.0
    assert fp.dt_to_segments_h(pd.Timestamp("2015-04-20T15:20:00Z"), w) == pytest.approx(0.35, abs=0.01)
    assert pd.isna(fp.dt_to_segments_h(pd.Timestamp("2015-04-20T15:20:00Z"), None))


def test_candidates_have_geometry_columns():
    p = ROOT / "data" / "pairs" / "candidates.csv"
    if not p.exists():
        pytest.skip("нет data/pairs/candidates.csv")
    c = pd.read_csv(p, low_memory=False)
    assert {"geometry_status", "dt_hours_segment"} <= set(c.columns)
    assert set(c[c.event_id == T18].geometry_status) == {"segment"}
    assert set(c[c.event_id == HE01].geometry_status) == {"reconstructed_approx"}
    assert c[c.source_id.isin(["S1_GPGP2018", "S4_BLACK_SEA_DOORS3"])].geometry_status.isna().all()


# ------------------------------------------------------------------ API v3
@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from service import case_store as cs
    from service.app import create_app
    monkeypatch.setitem(cs.PATHS, "queries", tmp_path / "queries.jsonl")
    return TestClient(create_app())


def test_observations_multilinestring_for_t18(client):
    fc = client.get("/api/v3/observations", params={"source": "S2_SARGASSO_MSM41", "geometry": "line"}).json()
    t18 = [f for f in fc["features"] if f["properties"]["event_id"] == T18]
    assert t18
    for f in t18:
        assert f["geometry"]["type"] == "MultiLineString" and len(f["geometry"]["coordinates"]) == 2
        p = f["properties"]
        assert p["geometry_status"] == "segment" and p["segments"] == 2
        assert p["area_author_km2"] == pytest.approx(0.22)
        assert p["geometry_source"].startswith("PANGAEA.931834")
        assert len(p["track_center"]) == 2
        # концентрация — по-прежнему по авторской площади (поле CSV не меняется)
        assert p["sampled_area_km2"] in (None, pytest.approx(0.22))
    # точечный режим (по умолчанию) — точка, но свойства трека есть
    pt = client.get("/api/v3/observations", params={"source": "S2_SARGASSO_MSM41"}).json()
    f0 = next(f for f in pt["features"] if f["properties"]["event_id"] == T18)
    assert f0["geometry"]["type"] == "Point" and f0["properties"]["segments"] == 2


def test_observations_s1_s4_have_no_track(client):
    fc = client.get("/api/v3/observations", params={"source": "S4_BLACK_SEA_DOORS3", "geometry": "line",
                                                    "limit": 5}).json()
    for f in fc["features"]:
        assert f["properties"]["geometry_status"] is None and f["properties"]["segments"] is None


def test_pairs_geometry_track_and_wkt(client):
    body = client.get("/api/v3/pairs", params={"sample_id": "MPL-0257"}).json()
    ps = body.get("pairs") or body.get("items") or []
    assert ps and all(p["geometry"]["type"] == "MultiLineString" for p in ps)
    r = client.get("/api/v3/export", params={"layer": "pairs", "format": "csv", "sample_id": "MPL-0257"})
    assert r.status_code == 200 and "MULTILINESTRING" in r.text


def test_zone_he460_01_strip_from_track(client):
    from service import case_store as cs
    z = next((f for f in cs.zones_all() if f["properties"]["event_id"] == HE01), None)
    if z is None:
        pytest.skip("нет зоны HE460 transect01")
    assert z["geometry"]["type"] == "Polygon"
    assert z["properties"]["area_km2"] == pytest.approx(15.102 * 0.02, rel=0.05)


def test_store_strip_polygon_segments_gap():
    from shapely.geometry import Point, shape
    from service import case_store as cs
    lines = cs.track_lines(T18)
    assert lines and len(lines) == 2
    gap, mids = _gap_mid(T18)
    g = cs.strip_polygon(gap[0], gap[1], lines, 10.0)
    assert g["type"] == "MultiPolygon"
    poly = shape(g)
    assert not poly.contains(Point(*gap))
    assert all(poly.contains(Point(*m)) for m in mids)
    assert cs.wkt({"type": "MultiLineString", "coordinates": [[[0, 0], [1, 1]], [[2, 2], [3, 3]]]}) == \
        "MULTILINESTRING ((0 0, 1 1), (2 2, 3 3))"
