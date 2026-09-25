"""Harmonization of Earth Search S2 L2A scale/offset (lane L6). Needs network (Earth Search + AWS COGs).

Same tile 16PCC, same 6x6 km open-water box (Gulf of Honduras), open-water pixels (SCL == 6):
 1. same acquisition 2020-08-29 in two processings: _0 (baseline 02.14, offset 0) vs _1 (baseline 05.00, raster:bands
    offset -0.1, earthsearch:boa_offset_applied=True) -> medians must agree (tolerance 0.004 reflectance);
 2. pre-2022 scene (2020-08-29 _0, baseline 02.14) vs two season-matched post-2022 scenes (2024-09-02 _0 and
    2025-08-18 _1, baseline 05.11) -> same level (tolerance 0.015 reflectance: different days/water states/glint;
    a double -1000 DN correction would be off by 0.1). April 2025 (05.11) differs by 0.016 in B2 (glint/haze).
 3. applying raster:bands offset blindly to a boa_offset_applied item gives negative water reflectance (the trap);
 4. S2C_16PCC_20250219_0 (offset -0.1, boa_offset_applied=False, pixels already offset-free) is handled by the
    data-based rule stac.es_offset_in_pixels (median B12 DN of SCL-6 water >= 1000 <=> offset still in pixels).
Run: CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe -m pytest tests/test_live_harmonize.py -s
"""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LON, LAT, SIZE = -88.20, 15.90, 6000
BANDS = ["B2", "B3", "B4", "B8"]
TOL_SAME = 0.004
TOL_DATES = 0.015


def _items(ids):
    from pystac_client import Client
    from macroplastic.live import stac
    try:
        c = Client.open(stac.EARTH_SEARCH)
        its = {it.id: it for it in c.search(collections=["sentinel-2-l2a"], ids=ids).items()}
    except Exception as e:  # pragma: no cover
        pytest.skip(f"Earth Search not reachable: {e!r}")
    missing = set(ids) - set(its)
    if missing:
        pytest.skip(f"items missing: {missing}")
    return its


def _water_medians(item, raw_offset=False):
    from macroplastic.live import stac
    epsg, b = stac.crop_bounds(item, LON, LAT, SIZE)
    scl = stac._read_asset(item.assets["scl"].href, epsg, b)
    water = scl == 6
    assert water.mean() > 0.5, f"{item.id}: too little clear water ({water.mean():.2f})"
    inpix, _ = stac.es_offset_in_pixels(item, stac._read_asset(item.assets["swir22"].href, epsg, b), scl)
    out = {}
    for band in BANDS:
        dn = stac._read_asset(item.assets[stac.ES_ASSET[band]].href, epsg, b).astype(np.float64)
        if raw_offset:
            s, o = stac._scale_offset_es(item.assets[stac.ES_ASSET[band]])
        else:
            s, o, _ = stac.band_scale_offset(item, band, inpix)
        r = dn * s + o
        out[band] = float(np.median(r[water & (dn > 0)]))
    return out


A0, A1, NEW = "S2B_16PCC_20200829_0_L2A", "S2B_16PCC_20200829_1_L2A", "S2B_16PCC_20250405_0_L2A"
POST = ["S2A_16PCC_20240902_0_L2A", "S2C_16PCC_20250818_1_L2A"]
# metadata-inconsistent item: raster:bands offset -0.1 AND earthsearch:boa_offset_applied=False, but pixels offset-free
ODD = "S2C_16PCC_20250219_0_L2A"


@pytest.fixture(scope="module")
def med():
    its = _items([A0, A1, NEW, ODD] + POST)
    m = {k: _water_medians(its[k]) for k in [A0, A1, NEW, ODD] + POST}
    m["raw_new"] = _water_medians(its[NEW], raw_offset=True)
    for k, v in m.items():
        print(k, {b: round(x, 4) for b, x in v.items()})
    return m


def test_same_acquisition_two_baselines(med):
    for b in BANDS:
        assert abs(med[A0][b] - med[A1][b]) <= TOL_SAME, (b, med[A0][b], med[A1][b])


def test_pre2022_vs_post2022_same_level(med):
    for post in POST:
        for b in BANDS:
            assert abs(med[A0][b] - med[post][b]) <= TOL_DATES, (post, b, med[A0][b], med[post][b])


def test_blind_offset_is_double_correction(med):
    # applying the -0.1 from raster:bands on top of already-offset pixels drives water below zero
    assert all(med["raw_new"][b] < 0 for b in BANDS), med["raw_new"]
    assert all(med[NEW][b] > 0 for b in BANDS)


def test_inconsistent_flag_item_is_not_double_corrected(med):
    # flag says "not applied", data say "already removed": the data-based rule must not subtract 0.1
    assert all(0 < med[ODD][b] < 0.08 for b in BANDS), med[ODD]
    # (its level differs from 2020-08-29 by ~0.017 in B2 -- very clear/dark water that day, not a 0.1 offset error)


def test_planetary_computer_matches_earth_search_and_is_not_clamped():
    """Same acquisition Haiti 2025-01-27 (18QYF, baseline 05.11) from PC (raw DN with +1000, offset from product
    metadata XML) and from Earth Search (offset removed, dark water clamped to DN=1):
    B12 water (not clamped on ES) must agree within 0.002; PC B4 water must contain values < 0 (not clamped)."""
    from macroplastic.live import stac
    try:
        pc = stac.search("planetary-computer", -72.55, 18.72, "2025-01-27/2025-01-27", tile="18QYF")
    except Exception as e:  # pragma: no cover
        pytest.skip(f"Planetary Computer not reachable: {e!r}")
    es = _items(["S2B_18QYF_20250127_0_L2A"])["S2B_18QYF_20250127_0_L2A"]
    assert pc, "PC item missing"
    pc = pc[0]
    epsg, b = stac.crop_bounds(es, -72.55, 18.72, 4000)
    out = {}
    for name, it, amap in (("pc", pc, stac.PC_ASSET), ("es", es, stac.ES_ASSET)):
        scl = stac._read_asset(it.assets[amap["SCL"]].href, epsg, b)
        w = scl == 6
        b12 = stac._read_asset(it.assets[amap["B12"]].href, epsg, b)
        inpix = stac.es_offset_in_pixels(it, b12, scl)[0] if name == "es" else None
        r = {}
        for band in ("B4", "B12"):
            dn = stac._read_asset(it.assets[amap[band]].href, epsg, b).astype(np.float64)
            s, o, _ = stac.band_scale_offset(it, band, inpix)
            r[band] = dn[w & (dn > 0)] * s + o
        out[name] = r
    print({k: {bb: round(float(np.median(v)), 4) for bb, v in r.items()} for k, r in out.items()})
    assert abs(np.median(out["pc"]["B12"]) - np.median(out["es"]["B12"])) <= 0.002
    assert (out["pc"]["B4"] < 0).mean() > 0.2          # real negative Sen2Cor values are kept on PC
    assert (out["es"]["B4"] <= 0.0001).mean() > 0.5    # ES: clamped at DN=1
