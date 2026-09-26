"""§39 п.3 / аудит В20: документы о количестве по снимку совпадают с продуктом (четыре статуса, сценарий PLP свёрнут).

Числа — из reports/final_numbers.json (case.sections.scene_zones, research_log); сценарий считает тот же код, что сервис
(src/macroplastic/case/zone_estimate.py). Запуск: .venv\\Scripts\\python.exe -m pytest -q tests/test_docs_s39.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FN = ROOT / "reports" / "final_numbers.json"
SECTION = "Как получаем шт./км² по снимку и насколько этому верить"
DOCS = ["README.md", "reports/report.md", "docs/QUANTITY.md"]
FORBIDDEN = ("нижняя граница", "нижней границы", "нижнюю границу", "требует проверки", "требуют проверки")


def _fn() -> dict:
    return json.loads(FN.read_text(encoding="utf-8"))


def _sz() -> dict:
    return _fn()["case"]["sections"]["scene_zones"]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_scene_zones_keys_for_deck():
    sz = _sz()
    for k in ("wind_rule_ms", "class_label", "status4", "n_confirmed_cozar_b", "n_no_independent_labels", "quantity_line",
              "research_estimate"):
        assert sz.get(k) is not None, k
    assert set(sz["status4"]) == {"detected", "not_detected", "insufficient_data"}
    assert sum(sz["status4"].values()) == sz["n_zones"]
    assert sz["status4"]["detected"] == sz["by_level_b"] + sz["by_unverified"]
    assert sz["n_confirmed_cozar_b"] == sz["by_level_b"]
    assert sz["n_confirmed_cozar_b"] + sz["n_no_independent_labels"] == sz["n_finds"]
    assert sz["wind_rule_ms"] == sz["wind_zero_ms"]
    assert "пластик не подтверждён" in sz["class_label"]


def test_research_estimate_equals_service_code():
    sys.path.insert(0, str(ROOT / "src"))
    from macroplastic.case.zone_estimate import summary_from_dir
    fresh = summary_from_dir()
    re_ = _sz()["research_estimate"]
    if not fresh.get("available"):
        pytest.skip("нет data/case/scene_zones")
    for k in ("quantity_line", "scenario_title", "scenario_status", "scenario_value_median", "scenario_value_min",
              "scenario_value_max", "n_zones_with_estimate", "n_confirmed_cozar_b", "n_no_independent_labels",
              "items_per_pixel_lo", "items_per_pixel_hi", "coverage_pct_lo", "coverage_pct_hi"):
        assert re_.get(k) == fresh.get(k), k
    h = re_["harness"]
    assert h["available"] and h["n_a5_natural"] == 0
    assert set(h["plp_worse_than_zero_on"]) >= {"ABST2", "FP3"}


def test_research_log_counts():
    rl = _fn()["case"]["sections"]["research_log"]
    ids = re.findall(r"^\|\s*([A-Z]+)(\d+[a-z]?)\s*\|", _read("docs/PIPELINE.md"), re.M)
    assert rl["n_experiments"] == len(ids) > 0
    assert sum(rl["by_prefix"].values()) == rl["n_experiments"]
    assert sum(rl["pairs_by_class"].values()) == rl["n_pairs_registry"] > 0


@pytest.mark.parametrize("rel", DOCS)
def test_section_how_we_count_present(rel):
    t = _read(rel)
    re_ = _sz()["research_estimate"]
    assert SECTION in t, rel
    part = t[t.index(SECTION):]
    assert re_["quantity_line"] in part
    med = f"{int(re_['scenario_value_median']):,}".replace(",", " ")
    assert med in part, (rel, med)
    assert "хуже ответа «0»" in part


@pytest.mark.parametrize("rel", DOCS + ["reports/report_final.md"])
def test_no_forbidden_words(rel):
    t = _read(rel).lower()
    for w in FORBIDDEN:
        assert w not in t, (rel, w)
    assert "шт./км² — не выдаём" not in t


def test_demo_block_no_forbidden_words():
    t = _read("docs/DEMO.md")
    a, b = "<!-- L111:scene_zones:begin -->", "<!-- L111:scene_zones:end -->"
    if a not in t:
        pytest.skip("нет блока демо-сцены")
    block = t[t.index(a):t.index(b)].lower()
    for w in FORBIDDEN:
        assert w not in block, w


DECK_KEYS = [  # ключи, которые читает presentation/make_deck_checkpoint.py (§41)
    "resolution_physics.pixel_m", "resolution_physics.item_cm_min", "resolution_physics.item_cm_max",
    "resolution_physics.countable_pct_10m", "resolution_physics.countable_pct_03m", "ispra.n_transects", "ispra.A",
    "targets.n_dates_counted", "estimator.n_methods", "synthetic_p3.verdict", "archives.n_files",
    "photo_count.transfer.verdict", "photo_count.materials.macro_f1", "jury.score_first", "jury.score_last",
    "research_log.n_hypotheses", "scene_zones.wind_rule_ms", "scene_zones.research_estimate.calibration_n_dates",
]


@pytest.mark.parametrize("path", DECK_KEYS)
def test_deck_keys_present(path):
    cur = _fn()["case"]["sections"]
    for part in path.split("."):
        assert isinstance(cur, dict) and part in cur, path
        cur = cur[part]
    assert cur is not None, path


def test_targets_not_confused_with_calibration():
    s = _fn()["case"]["sections"]
    t = s["targets"]
    assert t["n_dates_counted"] == t["n_dates_counted_s2"] + t["n_dates_counted_planetscope"]
    assert t["n_dates_counted"] > s["scene_zones"]["research_estimate"]["calibration_n_dates"]
