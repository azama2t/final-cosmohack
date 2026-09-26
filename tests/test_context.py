"""OSM context, objects touched by the demo drift cloud, repeated finds (service/routes_context.py).
.venv\\Scripts\\python.exe -m pytest -q tests\\test_context.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RID = "synth"
LON0, LAT0 = 30.0, 10.0


def _poly(lon, lat, d=0.0005):
    return {"type": "Polygon", "coordinates": [[[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d],
                                                [lon - d, lat + d], [lon - d, lat - d]]]}


def _det(i, date, lon, lat, area=500.0, artifact=None):
    p = {"id": f"{RID}_{date}_mdd_{i}", "region": RID, "date": date, "area_m2": area, "mean_prob": 0.7,
         "max_prob": 0.8, "model": "mdd", "lon": lon, "lat": lat, "confirmed": False, "confirmed_by": None}
    if artifact:
        p["artifact"] = artifact
    return {"type": "Feature", "geometry": _poly(lon, lat, 0.0002), "properties": p}


def _w(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    root = tmp_path_factory.mktemp("ctxdata")
    cdir = tmp_path_factory.mktemp("ctx")
    dates = {"2025-01-01": {}, "2025-02-01": {}, "2025-03-01": {"glint_or_haze": True, "haze": True}}
    dets = {
        "2025-01-01": [_det(0, "2025-01-01", LON0, LAT0, 400), _det(1, "2025-01-01", LON0 + 0.1, LAT0 + 0.1)],
        "2025-02-01": [_det(0, "2025-02-01", LON0 + 0.0003, LAT0, 600),
                       _det(1, "2025-02-01", LON0 + 0.1, LAT0 + 0.1, artifact="wake")],
        "2025-03-01": [_det(0, "2025-03-01", LON0 + 0.1, LAT0 + 0.1)],  # unreliable date (haze)
    }
    cell = {"type": "Feature", "geometry": _poly(LON0, LAT0, 0.01),
            "properties": {"h3": "x", "res": 8, "flagged_water_px": 5, "observed_water_px": 1000,
                           "observed_frac": 1.0, "share_permille": 5.0, "n_detections": 1}}
    entries = []
    for date, q in dates.items():
        base = root / RID / date
        _w(base / "rgb.json", {"bounds": [29.9, 9.9, 30.2, 10.2], "cloud_frac": 0.0})
        _w(base / "mdd" / "detections.geojson", {"type": "FeatureCollection", "features": dets[date]})
        _w(base / "mdd" / "h3.geojson", {"type": "FeatureCollection", "features": [cell]})
        _w(base / "mdd" / "zones.json", {"region": RID, "date": date, "model": "mdd", "zones": []})
        e = {"date": date, "cloud_frac": 0.0, "models": ["mdd"], "quality": q, "drift": None}
        entries.append(e)
    # drift for 2025-02-01: 10 particles drifting east ~110 m/h
    parts = [{"id": k, "path": [[LON0 + 0.001 * t, LAT0 + 0.0001 * k, t] for t in range(73)]} for k in range(10)]
    _w(root / RID / "2025-02-01" / "drift.json", {"region": RID, "date": "2025-02-01", "hours": list(range(73)),
                                                  "particles": parts, "note": "демонстрационный прогноз"})
    entries[1]["drift"] = f"{RID}/2025-02-01/drift.json"
    _w(root / "manifest.json", {"version": 1, "kind": "fixture", "models": {"mdd": {"threshold": 0.5}},
                                "regions": [{"id": RID, "name": "синтетика", "bounds": [29.9, 9.9, 30.2, 10.2],
                                             "center": [30.05, 10.05], "dates": entries}]})
    src = "© OpenStreetMap contributors (ODbL)"
    feats = [
        {"type": "Feature", "geometry": _poly(LON0 + 0.035, LAT0, 0.005),  # west edge at +0.03 deg (~3.29 km)
         "properties": {"id": "b", "kind": "beach", "kind_ru": "пляж", "name": "Пляж Тест", "source": src}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [LON0, LAT0 - 0.1]},
         "properties": {"id": "a", "kind": "aquaculture", "kind_ru": "аквакультура / ферма", "name": "Ферма",
                        "source": src}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [LON0 + 0.02, LAT0 + 0.01]},  # ~1 km N of track
         "properties": {"id": "p", "kind": "protected_area", "kind_ru": "охраняемая зона", "name": None,
                        "source": src}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [LON0 - 0.01, LAT0]},  # ~1.1 km west
         "properties": {"id": "m", "kind": "river_mouth", "kind_ru": "устье реки / канала", "name": "Река",
                        "source": src}},
    ]
    _w(cdir / f"{RID}.geojson", {"type": "FeatureCollection", "features": feats})
    return root, cdir


@pytest.fixture(scope="module")
def client(synth):
    root, cdir = synth
    mp = pytest.MonkeyPatch()
    mp.setenv("MACROPLASTIC_DATA", str(root))
    mp.setenv("MACROPLASTIC_CONTEXT", str(cdir))
    from service.app import create_app

    yield TestClient(create_app(root))
    mp.undo()


def test_context_geojson(client):
    r = client.get("/api/context", params={"region": RID})
    assert r.status_code == 200
    fc = r.json()
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 4
    assert all("OpenStreetMap" in f["properties"]["source"] for f in fc["features"])
    r = client.get("/api/context", params={"region": RID, "kind": "beach"})
    assert [f["properties"]["kind"] for f in r.json()["features"]] == ["beach"]
    assert client.get("/api/context", params={"region": "nope"}).status_code == 404
    assert client.get("/api/context", params={"region": "../x"}).status_code == 422


def test_drift_contacts_no_numbers(client):
    r = client.get("/api/threats", params={"region": RID, "date": "2025-02-01"})
    assert r.status_code == 200, r.text
    js = r.json()
    assert js["note"] == "демо-прогноз, не валидирован" and js["feed"] is False
    kinds = {o["kind"]: o for o in js["objects"]}
    assert set(kinds) == {"beach"}  # farm 11 km south and the far point are not touched at 300 m
    b = kinds["beach"]
    assert "Пляж Тест" in b["text"] and "OpenStreetMap" in b["source"]
    # team decision: no hours, no percentages anywhere in the answer
    for o in js["objects"]:
        assert not {"first_h", "median_h", "pct", "n_particles", "at_start"} & set(o)
        assert not any(ch.isdigit() for ch in o["text"]) and "%" not in o["text"]
    assert "n_particles" not in js and "threats" not in js
    # a larger buffer reaches the point ~1 km north of the track
    r2 = client.get("/api/threats", params={"region": RID, "date": "2025-02-01", "buffer_m": 1500}).json()
    assert {o["object_id"] for o in r2["objects"]} == {"b", "p"}
    assert client.get("/api/threats", params={"region": RID, "date": "2025-01-01"}).status_code == 404
    assert client.get("/api/threats", params={"region": RID, "date": "bad"}).status_code == 422


def test_repeats_neutral(client):
    r = client.get("/api/repeats", params={"region": RID})
    assert r.status_code == 200, r.text
    js = r.json()
    # the repeated find at (30.0, 10.0) on 2 reliable dates -> 1 cell; the other place is seen once on a reliable
    # date + once as an artifact (wake) + once on a hazy date -> not repeated
    assert js["n_cells"] == 1 and js["caption"] == "повторяемость (исследовательский слой)"
    c = js["cells"][0]
    assert c["n_dates"] == 2 and c["dates"] == ["2025-01-01", "2025-02-01"]
    assert c["area_m2"] == 1000.0
    assert "nearest_source" not in c and "источник" not in c["text"] and "усть" not in c["text"]
    assert "источник" not in js["rule"]
    assert client.get("/api/repeats").json()["n_cells"] == 1
    assert client.get("/api/repeats", params={"region": RID, "min_dates": 3}).json()["n_cells"] == 0
    assert client.get("/api/repeats", params={"region": "nope"}).status_code == 404
    # internal only: not in the public OpenAPI schema; the old /api/sources is gone
    assert "/api/repeats" not in client.get("/openapi.json").json()["paths"]
    assert client.get("/api/sources", params={"region": RID}).status_code == 404


def test_real_region_context():
    """Real OSM context + real data, if both exist locally (service/context, service/data)."""
    from service import core, routes_context as rc

    cdir = ROOT / "service" / "context"
    droot = ROOT / "service" / "data"
    files = sorted(cdir.glob("*.geojson")) if cdir.is_dir() else []
    if not files or not (droot / "manifest.json").is_file():
        pytest.skip("no real context/data")
    st = core.Store(droot, "arg")
    rid = "honduras" if (cdir / "honduras.geojson").is_file() else files[0].stem
    fc = rc.load_context(rid, cdir)
    assert fc["features"] and all(f["properties"]["kind"] in rc.KIND_RU for f in fc["features"])
    drift_dates = [d["date"] for d in st.region_dates(rid) if d.get("drift")]
    if drift_dates:
        t = rc.contacts(st, rid, drift_dates[-1], cdir=cdir)
        assert t["n_objects"] == len(t["objects"])
        assert all(o["kind"] in rc.TOUCH_KINDS for o in t["objects"])
    s = rc.repeats(st, rid)
    for x in s["cells"]:
        assert x["n_dates"] >= 2
