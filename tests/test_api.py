"""API tests on fixtures generated into a temp dir (scripts/make_fixtures.py). Run:
.venv\\Scripts\\python.exe -m pytest -q tests\\test_api.py
"""
from __future__ import annotations

import csv
import importlib.util
import io
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_fixture_builder():
    spec = importlib.util.spec_from_file_location("make_fixtures", ROOT / "scripts" / "make_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def data_root(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("fixtures")
    _load_fixture_builder().build(out, 0)
    return out


@pytest.fixture(scope="session")
def client(data_root) -> TestClient:
    from service.app import create_app

    return TestClient(create_app(data_root))


def _read(root: Path, rel: str):
    return json.loads((root / rel).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- service
def test_health(client, data_root):
    r = client.get("/health")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "ok" and j["data_kind"] == "fixture" and j["regions"] == 2
    assert j["data_root"] == data_root.resolve().name and ":" not in j["data_root"]


def test_manifest_as_is(client, data_root):
    assert client.get("/api/manifest").json() == _read(data_root, "manifest.json")


def test_regions(client):
    r = client.get("/api/regions")
    assert r.status_code == 200
    regs = r.json()
    assert [x["id"] for x in regs] == ["honduras", "durban"]
    h = regs[0]
    assert set(h) >= {"id", "name", "country", "center", "bounds", "zoom", "summary", "dates"}
    assert h["dates"] == ["2025-09-14", "2025-10-19", "2026-03-02"]


def test_regions_fast(client):
    client.get("/api/regions")
    t0 = time.perf_counter()
    for _ in range(20):
        client.get("/api/regions")
    assert (time.perf_counter() - t0) / 20 < 0.05


def test_region_detail(client, data_root):
    j = client.get("/api/region/honduras").json()
    assert j["id"] == "honduras" and len(j["dates"]) == 3
    assert j["timeseries"] == _read(data_root, "honduras/timeseries.json")


@pytest.mark.parametrize("url", ["/api/region/atlantis", "/api/zones?region=atlantis",
                                 "/api/timeseries?region=atlantis", "/api/compare?a=atlantis&b=durban",
                                 "/api/zones?region=honduras&date=2020-01-01",
                                 "/api/zones?region=honduras&model=unet",
                                 "/api/export?region=atlantis&layer=h3", "/api/nope"])
def test_404(client, url):
    assert client.get(url).status_code == 404


def test_bad_params_422(client):
    assert client.get("/api/compare?a=honduras:2025/10/19&b=durban").status_code == 422
    assert client.get("/api/export?region=honduras&layer=prob").status_code == 422
    assert client.get("/api/export?region=honduras&format=shp").status_code == 422


# ---------------------------------------------------------------- analysis
def test_compare_numbers(client, data_root):
    j = client.get("/api/compare?a=honduras:2025-10-19&b=durban&model=mdd").json()
    a, b = j["a"], j["b"]
    assert (a["region"], a["date"], a["model"]) == ("honduras", "2025-10-19", "mdd")
    assert (b["region"], b["date"], b["model"]) == ("durban", "2026-04-21", "mdd")  # default = latest
    for side, rid, date in ((a, "honduras", "2025-10-19"), (b, "durban", "2026-04-21")):
        det = _read(data_root, f"{rid}/{date}/mdd/detections.geojson")["features"]
        cells = _read(data_root, f"{rid}/{date}/mdd/h3.geojson")["features"]
        shares = [c["properties"]["share_permille"] for c in cells if c["properties"]["share_permille"] is not None]
        k = side["kpi"]
        assert k["n_detections"] == len(det)
        assert k["total_debris_area_m2"] == pytest.approx(sum(f["properties"]["area_m2"] for f in det), abs=0.1)
        assert k["mean_index"] == pytest.approx(sum(shares) / len(shares), abs=1e-3)
        assert k["max_index"] == pytest.approx(max(shares), abs=1e-3)
        assert k["observed_cells"] == len(shares)
        assert k["flagged_cells"] == sum(1 for s in shares if s > 0)
        assert k["cloud_frac"] == _read(data_root, f"{rid}/{date}/rgb.json")["cloud_frac"]
        # consistent with the precomputed timeseries
        ts = [r for r in _read(data_root, f"{rid}/timeseries.json") if r["date"] == date and r["model"] == "mdd"][0]
        assert k["mean_index"] == pytest.approx(ts["mean_index"], abs=1e-3)
        assert k["total_debris_area_m2"] == pytest.approx(ts["total_debris_area_m2"], abs=0.1)
    d = j["diff"]
    assert set(d) == {"total_debris_area_m2", "n_detections", "mean_index", "max_index",
                      "cloud_frac", "observed_cells", "flagged_cells"}
    ka, kb = a["kpi"], b["kpi"]
    assert d["n_detections"]["delta"] == kb["n_detections"] - ka["n_detections"]
    assert d["total_debris_area_m2"]["ratio"] == pytest.approx(
        kb["total_debris_area_m2"] / ka["total_debris_area_m2"], rel=1e-3)


def test_zones(client, data_root):
    j = client.get("/api/zones?region=durban").json()
    assert j == _read(data_root, "durban/2026-04-21/mdd/zones.json")
    j2 = client.get("/api/zones?region=durban&date=2025-11-07&model=lgbm").json()
    assert j2["model"] == "lgbm" and j2["date"] == "2025-11-07"


def test_timeseries_filter(client):
    all_ = client.get("/api/timeseries?region=honduras").json()
    mdd = client.get("/api/timeseries?region=honduras&model=mdd").json()
    assert len(all_) == 6 and len(mdd) == 3 and all(r["model"] == "mdd" for r in mdd)
    assert [r["date"] for r in mdd] == sorted(r["date"] for r in mdd)


# ---------------------------------------------------------------- export
def test_export_geojson(client, data_root):
    r = client.get("/api/export?region=honduras&date=2025-09-14&model=mdd&layer=detections&format=geojson")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert "honduras_2025-09-14_mdd_detections.geojson" in r.headers["content-disposition"]
    assert r.json() == _read(data_root, "honduras/2025-09-14/mdd/detections.geojson")
    z = client.get("/api/export?region=honduras&layer=zones&format=geojson").json()
    assert z["type"] == "FeatureCollection" and z["features"][0]["geometry"]["type"] == "Point"


@pytest.mark.parametrize("layer", ["detections", "h3", "zones"])
def test_export_csv(client, layer):
    r = client.get(f"/api/export?region=durban&layer={layer}&format=csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert ".csv" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows and "lon" in rows[0] and "lat" in rows[0]
    lon, lat = float(rows[0]["lon"]), float(rows[0]["lat"])
    assert 30 < lon < 32.5 and -31 < lat < -29  # Durban box
    if layer == "detections":
        assert {"id", "area_m2", "mean_prob", "max_prob"} <= set(rows[0])


# ---------------------------------------------------------------- files
def test_data_files(client):
    r = client.get("/data/manifest.json")
    assert r.status_code == 200 and r.json()["kind"] == "fixture"
    p = client.get("/data/honduras/2025-09-14/rgb.png")
    assert p.status_code == 200 and p.content[:4] == b"\x89PNG"
    assert "max-age" in p.headers.get("cache-control", "")
    g = client.get("/data/honduras/2025-09-14/mdd/h3.geojson")
    assert g.status_code == 200 and g.json()["type"] == "FeatureCollection"
    assert client.get("/data/honduras/nope.png").status_code == 404


@pytest.mark.parametrize("path", ["/data/..%2f..%2fservice%2fapp.py", "/data/%2e%2e/%2e%2e/pyproject.toml",
                                  "/data/..%5c..%5cservice%5capp.py", "/data/C:%5cWindows%5cwin.ini",
                                  "/data/%2fetc%2fpasswd"])
def test_path_traversal(client, path):
    assert client.get(path).status_code == 404


def test_safe_path_unit(data_root):
    from service import core

    for bad in ("../x", "..\\..\\x", "honduras/../../x", "/../../etc/passwd", "C:\\Windows\\win.ini", ""):
        with pytest.raises(core.NotFound):
            core.safe_path(data_root, bad)
    assert core.safe_path(data_root, "manifest.json").name == "manifest.json"


def test_gzip(client):
    r = client.get("/api/region/honduras", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"


def test_spa_fallback(client):
    for url in ("/", "/region/honduras", "/compare"):
        r = client.get(url)
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]


# ---------------------------------------------------------------- no data
def test_no_data_mode(tmp_path, monkeypatch):
    from service import core
    from service.app import create_app

    empty = tmp_path / "empty"
    empty.mkdir()
    c = TestClient(create_app(empty))
    h = c.get("/health").json()
    assert h["status"] == "ok" and h["data_kind"] == "none" and h["regions"] == 0
    assert "make_fixtures" in h["hint"]
    assert c.get("/api/regions").json() == []
    m = c.get("/api/manifest").json()
    assert m["regions"] == [] and m["kind"] == "none" and "hint" in m
    assert c.get("/api/region/honduras").status_code == 404
    assert c.get("/data/manifest.json").status_code == 404
    assert c.get("/").status_code == 200

    # auto-resolution with nothing found anywhere -> none
    monkeypatch.delenv(core.ENV_VAR, raising=False)
    monkeypatch.setattr(core, "SERVICE_DIR", tmp_path)
    assert core.resolve_root(None) == (None, "none")
    c2 = TestClient(create_app(None))
    assert c2.get("/health").json()["data_kind"] == "none"


def test_env_root(data_root, monkeypatch):
    from service import core

    monkeypatch.setenv(core.ENV_VAR, str(data_root))
    root, how = core.resolve_root(None)
    assert root == data_root.resolve() and how == "env"
