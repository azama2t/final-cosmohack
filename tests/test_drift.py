"""Drift: offline check with synthetic constant forcing -> OceanDrift -> drift.json matches the contract."""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pytest

pytest.importorskip("opendrift")

from macroplastic.drift.forcing import Window, write_constant  # noqa: E402
from macroplastic.drift.run import (build_drift_json, dumps_compact, haversine_km, particles_json,  # noqa: E402
                                    seed_points, simulate, validate_drift)


def test_seed_points_spread():
    c = np.array([[-30.0, 0.0], [-30.01, 0.01]])
    s = seed_points(c, n_total=200, sigma_m=150, max_r_m=300)
    assert s.shape == (200, 2)
    d = np.minimum(haversine_km(s[:, 0], s[:, 1], c[0, 0], c[0, 1]), haversine_km(s[:, 0], s[:, 1], c[1, 0], c[1, 1]))
    assert d.max() <= 0.301 and d.mean() > 0.05
    many = np.random.default_rng(0).uniform(size=(600, 2))
    assert seed_points(many, n_total=300).shape == (300, 2)


def test_drift_json_synthetic(tmp_path):
    start = dt.datetime(2025, 3, 1, 10, 30, tzinfo=dt.timezone.utc)
    # open Atlantic, far from land: no stranding
    win = Window.around([-30.1, -0.1, -29.9, 0.1], start)
    cur = tmp_path / "cur.nc"
    wind = tmp_path / "wind.nc"
    write_constant(win, cur, "currents", u=0.1, v=0.0)
    write_constant(win, wind, "wind", u=0.0, v=5.0)
    seeds = seed_points(np.array([[-30.0, 0.0]]), n_total=20)
    sim = simulate(seeds, start, cur, wind, wind_drift_factor=0.02, horizontal_diffusivity=0.0)
    assert sim["lon"].shape == (20, 73)
    assert not sim["stranded"].any()
    # expected displacement: east 0.1 m/s, north 0.02*5 = 0.1 m/s over 72 h -> ~25.9 km each
    dx = haversine_km(sim["lon"][:, 0], 0.0, sim["lon"][:, -1], 0.0).mean()
    dy = haversine_km(0.0, sim["lat"][:, 0], 0.0, sim["lat"][:, -1]).mean()
    assert 20 < dx < 32 and 20 < dy < 32, (dx, dy)
    ens = [{"wind_drift_factor": 0.01, "particles": particles_json(sim, ids=range(5))}]
    d = build_drift_json("synthetic", "2025-03-01", start, sim,
                         {"currents": "const", "wind": "const", "wind_drift_factor": 0.02, "model": "OpenDrift OceanDrift"},
                         "демонстрационный прогноз, без валидации", ensemble=ens)
    assert validate_drift(d) == []
    back = json.loads(dumps_compact(d))
    assert back["hours"] == list(range(73))
    assert back["particles"][0]["path"][0][2] == 0 and back["particles"][0]["path"][-1][2] == 72
    assert back["start_time"] == "2025-03-01T10:30:00Z"
    # contract violations are reported
    bad = dict(back, hours=list(range(10)))
    assert validate_drift(bad)
