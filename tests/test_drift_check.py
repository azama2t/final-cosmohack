"""L45 API: /api/drift_check (drift forecast vs next snapshot) and /api/flow (u/v grids for particle animation).

Offline: synthetic drift_check.json / pair file / CF NetCDF in a temp dir (env MACROPLASTIC_DRIFT_CHECK,
MACROPLASTIC_FORCING). The last test checks the real reports/drift_check.json if it exists.
  .venv\\Scripts\\python.exe -m pytest -q tests\\test_drift_check.py
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

T0 = dt.datetime(2026, 1, 1, 7, tzinfo=dt.timezone.utc)  # forcing window start; drift start = T0 + 3 h (fallback)


def _pair(tmp: Path):
    stem = "testreg_2026-01-01_2026-01-03"
    contour = {"type": "MultiPolygon", "coordinates": [[[[10.0, 50.0], [10.1, 50.0], [10.1, 50.1], [10.0, 50.0]]]]}
    det = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [10.05, 50.02]},
         "properties": {"model": "mdd", "area_m2": 300, "hit": True, "hit_baseline": False, "hit_shift": False,
                        "dist_forecast_km": 0.1, "dist_baseline_km": 3.2}}]}
    detail = {"region": "testreg", "date1": "2026-01-01", "date2": "2026-01-03", "dt_h": 48.1, "hour_used": 48,
              "start_time": "2026-01-01T10:00:00Z", "time2": "2026-01-03T10:06:00Z", "cloud_source": "drift.json",
              "scene2": {"scene_id": "S2X"}, "particles_t2": [[10.05, 50.03], [10.06, 50.04]],
              "particles_t0": [[10.0, 50.0], [10.01, 50.0]], "stranded_t2": [], "contour_pct": 90,
              "contour": contour, "baseline_contour": contour, "shift_contour": contour,
              "bandwidth_m": {"forecast": 800, "baseline": 500, "shift": 800}, "detections2": det}
    row = {"region": "testreg", "date1": "2026-01-01", "date2": "2026-01-03", "dt_h": 48.1, "hour_used": 48,
           "n_det2": 1, "hits": 1, "hits_baseline": 0, "hits_shift": 0, "contour_pct": 90,
           "detail": f"reports/drift_check/pairs/{stem}.json", "figure": f"reports/figures/drift_check_{stem}.png"}
    summ = {"criterion": "test", "n_pairs": 1, "hit_rate": 1.0, "baseline_hit_rate": 0.0, "pairs": [row]}
    (tmp / "drift_check" / "pairs").mkdir(parents=True)
    (tmp / "figures").mkdir()
    (tmp / "drift_check.json").write_text(json.dumps(summ), encoding="utf-8")
    (tmp / "drift_check" / "pairs" / f"{stem}.json").write_text(json.dumps(detail), encoding="utf-8")
    (tmp / "figures" / f"drift_check_{stem}.png").write_bytes(b"\x89PNG\r\n\x1a\n")


def _forcing(tmp: Path):
    from macroplastic.drift.forcing import write_cf
    ep = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    th0 = (T0 - ep).total_seconds() / 3600
    times = th0 + np.arange(0, 79, 1.0)
    lat = np.linspace(49.0, 51.0, 100)  # 100 x 90 -> must be thinned to <= 64
    lon = np.linspace(9.0, 11.0, 90)
    # u grows linearly in time: 0.1 at hour 0 of the window -> 0.1 + 0.01 * k; v = 0.05; one land cell (NaN)
    u = (0.1 + 0.01 * np.arange(len(times)))[:, None, None] * np.ones((1, len(lat), len(lon)))
    v = np.full_like(u, 0.05)
    u[:, 0, 0] = np.nan
    write_cf(tmp / "testreg_2026-01-01_currents.nc", times, lat, lon,
             {"x_sea_water_velocity": u, "y_sea_water_velocity": v}, {"source": "synthetic"})
    win = [9.0, 49.0, 11.0, 51.0, T0.isoformat(), (T0 + dt.timedelta(hours=78)).isoformat()]
    (tmp / "testreg_2026-01-01_currents.json").write_text(json.dumps({"source": "synthetic", "window": win}),
                                                        encoding="utf-8")
    wt = th0 + np.arange(0, 79, 3.0)
    wl = np.linspace(48.5, 51.5, 7)
    wo = np.linspace(8.5, 11.5, 7)
    write_cf(tmp / "testreg_2026-01-01_wind.nc", wt, wl, wo,
             {"x_wind": np.full((len(wt), 7, 7), 5.0), "y_wind": np.full((len(wt), 7, 7), -2.0)}, {"source": "synthetic"})
    (tmp / "testreg_2026-01-01_wind.json").write_text(json.dumps({"source": "synthetic wind", "window": win}),
                                                     encoding="utf-8")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    chk, frc = tmp_path / "chk", tmp_path / "frc"
    chk.mkdir(); frc.mkdir()
    _pair(chk)
    _forcing(frc)
    monkeypatch.setenv("MACROPLASTIC_DRIFT_CHECK", str(chk))
    monkeypatch.setenv("MACROPLASTIC_FORCING", str(frc))
    from service import routes_drift_check as m
    m._FLOW_CACHE.clear()
    app = FastAPI()
    app.include_router(m.router)
    return TestClient(app)


def test_router_is_autoloaded(client, tmp_path):
    """service/app.py includes every service/routes_*.py router (before its /api catch-all)."""
    from service.app import create_app
    empty = tmp_path / "empty_data"
    empty.mkdir()
    c = TestClient(create_app(empty))
    r = c.get("/api/drift_check")
    assert r.status_code == 200 and r.json()["n_pairs"] == 1
    assert c.get("/api/flow", params={"region": "testreg", "date": "2026-01-01", "t": 1}).status_code == 200


def test_summary_and_pair(client):
    s = client.get("/api/drift_check").json()
    assert s["n_pairs"] == 1 and s["hit_rate"] == 1.0 and s["baseline_hit_rate"] == 0.0
    p = s["pairs"][0]
    assert p["url"] == "/api/drift_check/testreg/2026-01-01?date2=2026-01-03"
    assert client.get(p["figure_url"]).status_code == 200
    d = client.get("/api/drift_check/testreg/2026-01-01").json()
    assert d["date2"] == "2026-01-03" and d["hour_used"] == 48
    assert d["particles"] == [[10.05, 50.03], [10.06, 50.04]]
    assert d["contour"]["type"] == "Feature" and d["contour"]["geometry"]["type"] == "MultiPolygon"
    assert d["baseline"]["contour"]["properties"]["kind"] == "baseline_zero_drift"
    f = d["detections"]["features"][0]
    assert f["properties"]["hit"] is True and f["properties"]["hit_baseline"] is False
    assert d["stats"]["n_det2"] == 1


def test_pair_errors(client):
    assert client.get("/api/drift_check/testreg/2026-01-02").status_code == 404
    assert client.get("/api/drift_check/testreg/2026-01-01?date2=2026-01-09").status_code == 404
    assert client.get("/api/drift_check/Bad..Reg/2026-01-01").status_code == 422
    assert client.get("/api/drift_check/figure/other.png").status_code == 422


def test_flow_currents(client):
    r = client.get("/api/flow", params={"region": "testreg", "date": "2026-01-01", "kind": "currents", "t": 10})
    assert r.status_code == 200
    d = r.json()
    assert d["nx"] <= 64 and d["ny"] <= 64 and len(d["u"]) == d["nx"] * d["ny"] == len(d["v"])
    assert d["la1"] > d["la2"] and d["lo1"] < d["lo2"]  # rows north -> south
    # start = T0 + 3 h (no drift.json) -> t=10 is window hour 13 -> u = 0.1 + 0.13
    vals = [x for x in d["u"] if x is not None]
    assert abs(np.median(vals) - 0.23) < 1e-6
    assert d["start_time"] == "2026-01-01T10:00:00Z" and d["time"] == "2026-01-01T20:00:00Z"
    # the NaN (land) cell at south-west corner -> null in the last row, first column
    assert d["u"][(d["ny"] - 1) * d["nx"]] is None
    # interpolation between hourly steps
    d2 = client.get("/api/flow", params={"region": "testreg", "date": "2026-01-01", "t": 10.5}).json()
    assert abs(np.median([x for x in d2["u"] if x is not None]) - 0.235) < 1e-6


def test_flow_wind_and_errors(client):
    d = client.get("/api/flow", params={"region": "testreg", "date": "2026-01-01", "kind": "wind", "t": 72}).json()
    assert d["nx"] == 7 and d["ny"] == 7 and set(d["u"]) == {5.0} and set(d["v"]) == {-2.0}
    assert client.get("/api/flow", params={"region": "testreg", "date": "2026-01-01", "kind": "waves"}).status_code == 422
    assert client.get("/api/flow", params={"region": "testreg", "date": "2026-01-01", "t": 80}).status_code == 422
    assert client.get("/api/flow", params={"region": "nope", "date": "2026-01-01"}).status_code == 404
    lst = client.get("/api/flow/list").json()
    assert lst == [{"region": "testreg", "date": "2026-01-01", "kinds": ["currents", "wind"]}]


def test_real_drift_check_if_present():
    p = ROOT / "reports" / "drift_check.json"
    if not p.exists():
        pytest.skip("reports/drift_check.json not generated yet")
    from service import routes_drift_check as m
    app = FastAPI()
    app.include_router(m.router)
    c = TestClient(app)
    s = c.get("/api/drift_check").json()
    assert s["n_pairs"] == len(s["pairs"])
    for row in s["pairs"]:
        d = c.get(row["url"]).json()
        assert len(d["particles"]) > 0 and d["detections"]["type"] == "FeatureCollection"
        assert sum(f["properties"]["hit"] for f in d["detections"]["features"]) == row["hits"]
