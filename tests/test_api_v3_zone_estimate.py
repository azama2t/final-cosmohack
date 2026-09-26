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

WORDS = ("исследовательская оценка", "исследовательский сценарий по искусственным мишеням PLP",
         "допущения: предметы размера бутылки PET 1,5 л, покрытие пикселя 28–40 %", "470–670 предметов-бутылок на пиксель",
         "не доверительный интервал и не проверено на природе", "мелкие предметы → больше штук")


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


def _sig2(x: float):
    v = float(f"{x:.2g}")
    return int(v) if abs(v) >= 10 else v


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
        assert re["n_items_label"].startswith("N ≥ ~") and "контура зоны" in re["basis"]
        assert re["det_px_share_of_zone_pct"] == round(n * 100 / 1e6 / a * 100, 2)
        assert "сработавших пикселей" in re["not_what"] and "не доверительный" in re["not_what"]
        for w in WORDS:
            assert w in re["label"]
        assert "измерено" not in re["label"]
        # jury 13:47 / audit В19: the shown number is a lower bound «≥ ~X» (X = n × 470 / area, 2 significant digits);
        # lo/hi — the spread of the 2 calibration points, not a confidence interval; no «[lo–hi]» in the label
        lb = _sig2(n * 470 / a)
        assert re["lower_bound"] == re["display_value"] == lb and re["n_items_display"] == _sig2(n * 470)
        assert re["label"].startswith(f"≥ ~{ZE.fmt(lb)} шт./км² (нижняя граница; неопределённость калибровки не оценена: "
                                      "2 пикселя на 2 датах PLP)")
        assert "[" not in re["label"] and re["label_short"].startswith(f"≥ ~{ZE.fmt(lb)}")
        assert re["interval_kind"] == "calibration_spread" and re["ci"] is None
        cs_ = re["calibration_spread"]
        assert (cs_["lo"], cs_["hi"]) == (re["lo"], re["hi"]) and "не доверительный интервал" in cs_["label"]
        assert "доля покрытия пикселей детектора × калибровка PLP" in re["method_essence"]
        assert "2 пикселя на 2 датах" in re["method_essence"]
        assert re["context"].startswith("плотность внутри контура нити в пересчёте на бутылки PET 1,5 л — не среднее по маршруту")
        assert "(среднее по маршруту: 1,5–54)" in re["context"] and "3–4 порядка" not in re["context"]
        assert "другой масштаб (внутри нити против среднего по маршруту)" in re["context"]
        # P1: firing is not monotonic in N — the 470–670 scenario is two points, not a law (interval unchanged)
        assert re["firing_caveat"] == ("срабатывание не монотонно по числу: из 9 водных пикселей мишеней с ≥ 400 бутылками "
                                       "детектор сработал на 2; пиксель PLP2018 с ≈ 1 670 бутылками — без срабатывания; "
                                       "сценарий — две точки, не закон")
        assert re["method_essence"].endswith(re["firing_caveat"]) and re["items_per_pixel"]["lo"] == 470
        assert re["muted"] == (p["verification"] != "level_B_cozar")
        # §36 п.2: 470–670 per pixel — a research scenario on artificial PLP targets, not a CI, not checked on nature
        assert re["kind"] == "scenario" and re["scenario"] in re["label"] and "сценарий" in re["method"]
        assert "не доверительный интервал" in re["calibration_spread"]["label"] and "сценарий 470–670" in re["calibration_spread"]["label"]
        nat = ZE.natural_pair_note(ZE.load_config())
        assert re["natural_pair_note"] == nat and ((nat in re["context"]) if nat else "ISPRA" not in re["context"])
        # export CSV / GeoJSON: the same numbers
        r = rows[zid]
        assert (float(r["research_estimate_value"]), float(r["research_calibration_spread_lo"]),
                float(r["research_calibration_spread_hi"])) == want
        assert float(r["research_estimate_lower_bound"]) == lb
        assert r["research_estimate_muted"] == ("true" if re["muted"] else "false")
        assert r["research_estimate_context"] == re["context"] and r["research_method_essence"] == re["method_essence"]
        assert r["research_scenario"] == re["scenario"] and r["research_estimate_method"] == re["method"]
        # jury 14:06: «(i)» line and the calibration in force
        assert re["formula_short"] == "пиксели маски × 470–670 / площадь контура = пересчёт доли покрытия"
        assert re["calibration_id"] == "flat_plp" and "плоская калибровка" in re["calibration_name"]
        assert r["research_formula_short"] == re["formula_short"] and r["research_calibration_name"] == re["calibration_name"]
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
    for k in ("lower_bound_median", "lower_bound_min", "lower_bound_max", "n_muted", "n_not_muted", "firing_caveat",
              "firing", "scenario", "calibration_name", "formula_short"):
        assert s_dir[k] == s_api[k] == h[k], k


def test_muted_only_for_unverified_finds(client):
    """Orchestrator 13:5x (4): «требует проверки» — muted = true; the 14 zones crossing a Cózar filament — normal."""
    fc = client.get("/api/v3/scene_zones").json()
    est = [f["properties"] for f in fc["features"] if f["properties"]["research_estimate"]]
    b = [p for p in est if p["verification"] == "level_B_cozar"]
    assert len(b) >= 10 and all(p["research_estimate"]["muted"] is False for p in b)
    u = [p for p in est if p["verification"] != "level_B_cozar"]
    assert u and all(p["research_estimate"]["muted"] is True and p["research_estimate"]["muted_reason"] for p in u)
    s = fc["research_estimate"]
    assert (s["n_not_muted"], s["n_muted"]) == (len(b), len(u))
    assert s["lower_bound_min"] <= s["lower_bound_median"] <= s["lower_bound_max"]


def test_natural_pair_note_only_when_confirmed():
    """§36 п.2: the ISPRA 604 sentence appears in the context only with natural_pair.confirmed = true."""
    cfg = ZE.load_config()
    p = {"detection_status": "detected", "flags": [], "verification": "level_B_cozar",
         "measured": {"n_pixels": 10, "zone_area_km2": 0.5}}
    off = ZE.estimate(p, {**cfg, "natural_pair": {**cfg["natural_pair"], "confirmed": False}}, field_range=(1.54, 53.9))
    on = ZE.estimate(p, {**cfg, "natural_pair": {**cfg["natural_pair"], "confirmed": True}}, field_range=(1.54, 53.9))
    assert off["natural_pair_note"] is None and "ISPRA" not in off["context"]
    assert on["natural_pair_note"].startswith("на природной паре ISPRA 604 (16.09.2019; протокол SNPA Modulo 2bis")
    assert on["context"].endswith("сигнал нити дают не посчитанные предметы")
    for w in ("15 из 16 — полимеры", "ширина полосы в файле 2019 г. не записана", "протокол 2024 — ≤ 5 м",
              "часовой пояс не указан", "ни один из 16 предметов не ближе 50 м к пикселю детектора, ближайший — 85 м",
              "~10⁻⁵ площади полосы"):
        assert w in on["natural_pair_note"], w
    s_on = ZE.summary([p], {**cfg, "natural_pair": {**cfg["natural_pair"], "confirmed": True}})
    assert s_on["natural_pair_note"] == on["natural_pair_note"] and s_on["kind"] == "scenario" and s_on["scenario"]


def test_natural_pair_on_in_api_and_no_old_wording(client):
    """§38 п.6: ISPRA 604 (critic's wording) is on — in natural_pair_note, context, CSV; «2–4 предмета на га» nowhere."""
    cfg = ZE.load_config()
    assert cfg["natural_pair"]["confirmed"] is True
    fc = client.get("/api/v3/scene_zones", params={"is_find": "true"}).json()
    body = json.dumps(fc, ensure_ascii=False) + client.get(
        "/api/v3/export", params={"layer": "scene_zones", "format": "csv"}).content.decode("utf-8-sig")
    assert "2–4 предмет" not in body and "2-4 предмет" not in body
    for f in fc["features"]:
        re = f["properties"]["research_estimate"]
        assert re["natural_pair_note"] == ZE.natural_pair_note(cfg) and re["context"].endswith(re["natural_pair_note"])
    rows = _csv_rows(client, is_find="true")
    assert rows and all(r["research_natural_pair_note"] == ZE.natural_pair_note(cfg) for r in rows)


def test_config_half_written_keeps_last_valid(client, tmp_path, monkeypatch):
    """L132 14:3x: a config read mid-write must not give 500 — the loader keeps the last valid version;
    ZE.write_config_atomic writes via a temp file + os.replace and refuses an invalid config."""
    good = ZE.CONFIG.read_text(encoding="utf-8")
    p = tmp_path / "zone_estimate.yaml"
    ZE.write_config_atomic(good, p)
    assert p.read_text(encoding="utf-8") == good and not list(tmp_path.glob("*.tmp"))
    monkeypatch.setitem(cs.PATHS, "zone_estimate_cfg", p)
    zid = "SZ-demo-cozar-2021-03-11-016"
    ok = client.get(f"/api/v3/scene_zones/{zid}")
    assert ok.status_code == 200
    ref = ok.json()["properties"]["research_estimate"]
    # a truncated file (calibration_points cut off), as seen while rewriting
    p.write_text(good[: good.index("calibration_points:")], encoding="utf-8")
    r = client.get(f"/api/v3/scene_zones/{zid}")
    assert r.status_code == 200 and r.json()["properties"]["research_estimate"] == ref
    assert client.get("/api/v3/export", params={"layer": "scene_zones", "format": "csv"}).status_code == 200
    with pytest.raises(ValueError):
        ZE.write_config_atomic(good[: good.index("calibration_points:")], p)
    ZE.write_config_atomic(good, p)
    assert client.get(f"/api/v3/scene_zones/{zid}").json()["properties"]["research_estimate"] == ref
