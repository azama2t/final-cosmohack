"""L72: геометрия трансект S2/S3 по сохранённым таблицам PANGAEA (офлайн, data/case/pangaea/)."""
from __future__ import annotations

import hashlib
import json

import pandas as pd
import pytest

from macroplastic.case import geometry as G

INTERRUPTED = G.INTERRUPTED_S2
AREA_TOL = 0.04   # |Σ длин сегментов × 10 м − площадь автора| / площадь автора; максимум факт. 3.25 % (T18)

pytestmark = pytest.mark.skipif(not (G.PANGAEA_DIR / G.S2_XLSX).exists(),
                                reason="нет data/case/pangaea (scripts/case/fetch_pangaea.py)")


@pytest.fixture(scope="module")
def built():
    return G.build()


def test_manifest_sha256_matches_files():
    man = json.loads((G.PANGAEA_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert set(man) == {"931834", "931833", "890782", "890781"}
    for ds, m in man.items():
        data = (G.ROOT / m["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == m["sha256"], ds
        assert m["license"] in ("CC BY 4.0", "CC BY 3.0")


def test_all_s2_s3_events_covered(built):
    seg, chk = built
    csv = pd.read_csv(G.SAMPLES)
    want = set(csv[csv.source_id.isin(["S2_SARGASSO_MSM41", "S3_SE_NORTH_SEA"])].event_id)
    assert set(seg.event_id) == want and len(want) == 104


@pytest.mark.parametrize("eid", INTERRUPTED)
def test_interrupted_have_segments_and_author_area(built, eid):
    seg, chk = built
    g = seg[seg.event_id == eid]
    assert len(g) >= 2
    assert (g.geometry_status == "segment").all()
    author = float(g.area_author_km2.iloc[0])
    csv = pd.read_csv(G.SAMPLES)
    assert author == pytest.approx(float(csv[csv.event_id == eid].sampled_area_km2.iloc[0]), abs=5e-4)
    seg_area = g.length_km.sum() * 10 / 1000
    assert abs(seg_area - author) / author <= AREA_TOL
    # независимая проверка: геодезическая длина концов сегментов ≈ «Distance» автора (±1 %)
    assert (abs(g.length_km_geodesic / g.length_km - 1) < 0.01).all()


@pytest.mark.parametrize("eid", INTERRUPTED)
def test_segment_times_inside_event_interval(built, eid):
    seg, chk = built
    g = seg[seg.event_id == eid].sort_values("segment")
    c = chk.set_index("event_id").loc[eid]
    t0, t1 = pd.Timestamp(c.csv_time_start), pd.Timestamp(c.csv_time_end)
    starts, ends = pd.to_datetime(g.time_start_utc), pd.to_datetime(g.time_end_utc)
    assert (starts >= t0).all() and (ends <= t1).all() and (starts < ends).all()
    assert (starts.iloc[1:].values >= ends.iloc[:-1].values).all()     # сегменты не перекрываются, есть разрыв
    assert starts.iloc[0] == t0 and ends.iloc[-1] == t1                 # крайние времена = интервал события в CSV
    assert c.effort_min < c.span_min                                    # усилие меньше полного интервала
    assert bool(c.segments_inside_csv_interval)


def test_endpoints_match_csv_except_reconstructed(built):
    seg, chk = built
    ok = chk[chk.geometry_status != "reconstructed_approx"]
    assert (ok.start_vs_csv_km < 0.01).all() and (ok.end_vs_csv_km < 0.01).all()
    assert (chk.segments_inside_csv_interval).all()
    assert chk.csv_equals_author_area.all()   # CSV хранит площадь автора (README данных)


def test_s3_degenerate_transect_reconstructed(built):
    seg, chk = built
    g = seg[seg.event_id == "S3:HE460_MarLitter_transect01"].iloc[0]
    assert g.geometry_status == "reconstructed_approx"
    assert g.length_km_geodesic == pytest.approx(15.102, abs=0.01)
    assert "start == end" in g.note
    # остальные S3 — одиночные отрезки из 890782
    s3 = seg[seg.source_id == "S3_SE_NORTH_SEA"]
    assert (s3.geometry_status == "single").sum() == 40


def test_event_track_multilinestring_and_point_fallback():
    t = G.event_track("S2:MSM41_litter-T18")
    assert t["geometry"]["type"] == "MultiLineString" and len(t["geometry"]["coordinates"]) == 2
    assert t["window"] == ("2015-04-07T16:12:00Z", "2015-04-07T17:24:00Z")
    assert t["area_author_km2"] == pytest.approx(0.22)
    p = G.event_track("S3:HE460_MarLitter_transect01", allow_approx=False)
    assert p["geometry"]["type"] == "Point"
    assert G.event_track("S1:does-not-exist") is None


def test_events_override_changes_only_s2_s3():
    ev = pd.DataFrame([
        dict(event_id="S3:HE460_MarLitter_transect01", lat=54.238, lon=7.92,
             obs_datetime=pd.Timestamp("2016-04-08 16:38:30", tz="UTC"), time_note="interval_mid",
             point_from="published_transect_center"),
        dict(event_id="S4:DOORS3:T1", lat=43.6, lon=29.6, obs_datetime=pd.Timestamp("2024-06-02 12:00", tz="UTC"),
             time_note="date_only_noon", point_from="reported_transect_center")])
    out = G.events_override(ev)
    a, b = out.iloc[0], out.iloc[1]
    assert a.point_from == "geometry_track_center"
    assert G.haversine_km((a.lon, a.lat), (7.92, 54.238)) == pytest.approx(7.57, abs=0.05)
    assert a.obs_datetime == pd.Timestamp("2016-04-08 16:38:30", tz="UTC")
    assert b.lat == 43.6 and b.point_from == "reported_transect_center"


def test_outputs_written():
    for f in ("transects.csv", "transects.geojson", "checks.csv"):
        assert (G.OUT_DIR / f).exists(), f
    gj = json.loads((G.OUT_DIR / "transects.geojson").read_text(encoding="utf-8"))
    types = {f["properties"]["event_id"]: f["geometry"]["type"] for f in gj["features"]}
    assert all(types[e] == "MultiLineString" for e in INTERRUPTED)
    assert len(types) == 104
