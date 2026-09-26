"""scripts/case/expert_check.py: повторная проверка выбранного экспертом расчёта без правки кода (постановка, «Демонстрация»; Т5)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "src", ROOT / "scripts" / "case"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import expert_check as E  # noqa: E402

HAS_DATA = (ROOT / "task" / "macroplastic_marine_samples.csv").is_file() and (ROOT / "data" / "pairs" / "pair_quality.csv").is_file()


def _run(tmp_path, *args):
    out = tmp_path / "r.json"
    rc = E.main([*args, "--json", str(out)])
    return rc, json.loads(out.read_text(encoding="utf-8"))


def test_manual_control_example(tmp_path):
    rc, r = _run(tmp_path, "--n", "12", "--area", "0.20", "--pred", "75")
    assert rc == 0
    rows = {x["что"]: x for x in r["reports"][0]["rows"]}
    assert rows["C = N / A, шт./км²"]["пересчитано"] == 60.0
    assert rows["абсолютная ошибка прогноза, шт./км²"]["пересчитано"] == 15.0
    assert rows["95 % ДИ Пуассона, шт./км²"]["пересчитано"] == "31.0–104.8"


def test_manual_bad_area_no_division(tmp_path):
    rc, r = _run(tmp_path, "--n", "3", "--area", "-1")
    assert rc == 0
    assert r["reports"][0]["rows"][0]["пересчитано"] is None


def test_no_input_is_usage_error():
    with pytest.raises(SystemExit) as e:
        E.main([])
    assert e.value.code == 2


@pytest.mark.skipif(not HAS_DATA, reason="нет CSV организаторов или реестра пар")
def test_sample_matches_api(tmp_path):
    rc, r = _run(tmp_path, "--sample-id", "MPL-0197")
    assert rc == 0 and r["mismatches"] == 0
    rows = {x["что"]: x for x in r["reports"][0]["rows"]}
    assert rows["оценка по полю = медиана dev профиля, шт./км²"]["ok"] is True


@pytest.mark.skipif(not HAS_DATA, reason="нет CSV организаторов или реестра пар")
@pytest.mark.parametrize("zone", ["Z-S3_HE460_MarLitter_transect03", "Z-S4_DOORS3_T6"])
def test_zone_matches_api_and_export(tmp_path, zone):
    rc, r = _run(tmp_path, "--zone-id", zone)
    assert rc == 0 and r["mismatches"] == 0
    whats = {x["что"] for x in r["reports"][0]["rows"]}
    assert {"статус детекции", "выгрузка CSV: статус", "пикселей в полосе"} <= whats


@pytest.mark.skipif(not HAS_DATA, reason="нет CSV организаторов или реестра пар")
def test_expert_settings_are_reported_not_failed(tmp_path):
    rc, r = _run(tmp_path, "--event-id", "S4:DOORS3:T1", "--threshold", "0.3", "--max-cloud", "0.5")
    assert rc == 0
    whats = {x["что"] for x in r["reports"][0]["rows"]}
    assert "статус детекции с настройками эксперта" in whats


@pytest.mark.skipif(not HAS_DATA, reason="нет CSV организаторов или реестра пар")
@pytest.mark.parametrize("args", [["--zone-id", "Z-NOPE"], ["--sample-id", "MPL-99999"]])
def test_unknown_id_exit_2(tmp_path, args):
    assert E.main([*args, "--json", str(tmp_path / "x.json")]) == 2


@pytest.mark.skipif(not (ROOT / "data" / "case" / "run" / "registry_pairs.csv").is_file(), reason="нет реестра пар")
def test_registry_pairs_has_geometry_source_and_masks():
    """Постановка, «Реестр сопоставления»: scene_id, источник, время, сдвиг, геометрия, маски качества, sample_id, причины."""
    import pandas as pd
    reg = pd.read_csv(ROOT / "data" / "case" / "run" / "registry_pairs.csv")
    for col in ("event_id", "sample_ids", "best_item_id", "scene_source", "scene_datetime", "dt_hours", "drift_shift_km",
                "tolerance_km", "geometry_wkt", "geometry_source", "quality_decision", "quality_mask", "status", "reason"):
        assert col in reg.columns, col
    assert reg.geometry_wkt.notna().all()
    assert reg.geometry_wkt.str.match(r"^(POINT|LINESTRING|MULTILINESTRING) \(").all()
    has_q = reg.quality_decision.notna()
    assert reg.loc[has_q, "quality_mask"].notna().all() and reg.loc[has_q, "scene_source"].notna().all()
