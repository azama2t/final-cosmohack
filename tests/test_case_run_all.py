"""Smoke сквозного маршрута кейса (scripts/case/run_all.py) на 5 событиях, без сети (кэш STAC).

Проверяется критерий Т1: повторный запуск подготовки формирует реестр принятых и отклонённых пар/записей
с идентификаторами, источниками и причинами; записи согласованы с исходным CSV.
Все выходы — во временном каталоге (--workdir), реальные data/ и reports/ не трогаются.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pandas as pd
import pytest

os.environ["CUDA_VISIBLE_DEVICES"] = ""
ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "task" / "macroplastic_marine_samples.csv"
CACHE = ROOT / "data" / "pairs" / "cache"
QUALITY = ROOT / "data" / "pairs" / "quality"
# по одному событию S1 и S2 (сцен нет), S3 с детекцией, S4 с бликом, S4 принятое по качеству
EVENTS = ["S1:0.1", "S2:MSM41_litter-T1", "S3:HE460_MarLitter_transect03", "S4:DOORS3:T12", "S4:DOORS3:T2"]

pytestmark = pytest.mark.skipif(not (SAMPLES.is_file() and CACHE.is_dir() and any(CACHE.glob("*.json"))),
                                reason="нет task/ CSV или кэша STAC data/pairs/cache")


def _load():
    spec = importlib.util.spec_from_file_location("case_run_all", ROOT / "scripts" / "case" / "run_all.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    ra = _load()
    w = tmp_path_factory.mktemp("case_run")
    rc = ra.main(["all", "--offline", "--workdir", str(w), "--events", ",".join(EVENTS)])
    assert rc == 0
    return ra, w


def test_subset_is_source_rows(run):
    _, w = run
    src = pd.read_csv(SAMPLES, dtype=str, keep_default_na=False)
    sub = pd.read_csv(w / "samples.csv", dtype=str, keep_default_na=False)
    exp = src[src.event_id.isin(EVENTS)].reset_index(drop=True)
    pd.testing.assert_frame_equal(sub, exp)


def test_pair_registry_one_row_per_csv_event(run):
    _, w = run
    src = pd.read_csv(w / "samples.csv")
    reg = pd.read_csv(w / "case" / "run" / "registry_pairs.csv")
    assert sorted(reg.event_id) == sorted(src.event_id.unique())          # каждое событие ровно один раз
    assert reg.event_id.is_unique
    assert set(reg.status) <= {"accept", "reject"}
    assert reg.reason.notna().all() and (reg.reason.str.len() > 0).all()
    for r in reg.itertuples():                                           # согласованность с CSV
        g = src[src.event_id == r.event_id]
        assert r.source_id == g.source_id.iloc[0]
        assert r.n_samples == len(g)
        assert r.sample_ids.split(";") == list(g.sample_id)
    by = reg.set_index("event_id")
    assert by.loc["S1:0.1", "stage"] == "metadata" and "no_scene_in_window" in by.loc["S1:0.1", "reason"]
    assert "mission_not_launched" in by.loc["S2:MSM41_litter-T1", "reason"]
    if (QUALITY / "S4_DOORS3_T12" / "meta.json").is_file():
        assert "quality_reject: glint" in by.loc["S4:DOORS3:T12", "reason"]
        assert by.loc["S4:DOORS3:T12", "status_without_drift"] == "reject"
    if (QUALITY / "S3_HE460_MarLitter_transect03" / "meta.json").is_file():
        assert by.loc["S3:HE460_MarLitter_transect03", "status_without_drift"] == "accept"
        assert by.loc["S3:HE460_MarLitter_transect03", "n_det"] > 0
    # дрейф: при известном |dt| в сутки типичный сдвиг > 3 км — отказ с числами в причине
    assert "sync_unreliable_drift" in by.loc["S4:DOORS3:T2", "reason"]


def test_sample_registry_matches_selection(run):
    ra, w = run
    src = pd.read_csv(w / "samples.csv")
    reg = pd.read_csv(w / "case" / "run" / "registry_samples.csv")
    cfg = ra.S.load_config()
    for prof in cfg["profiles"]:
        r = reg[reg.profile == prof]
        assert sorted(r.sample_id) == sorted(src.sample_id) and r.sample_id.is_unique
        assert set(r.status) <= {"accept", "reject"} and (r.reason.str.len() > 0).all()
        acc, rej = ra.S.select(src, cfg, prof)
        assert set(r[r.status == "accept"].sample_id) == set(acc.sample_id)
        got = r[r.status == "reject"].set_index("sample_id").reason
        assert (got.loc[rej.sample_id].to_numpy() == rej.reason.to_numpy()).all()
    s2 = reg[reg.profile == cfg["default_profile"]].set_index("sample_id")
    # причины отказа из правил команды и фильтров профиля
    reasons = set(s2.reason)
    assert any(x.startswith("all_litter") for x in reasons)          # S3/S4 — общий мусор, не пластик
    assert "source_id_not_in_profile" in reasons                    # S1 не в профиле S2


def test_export_consistent(run):
    _, w = run
    src = pd.read_csv(w / "samples.csv")
    obs = pd.read_csv(w / "export" / "observations.csv", encoding="utf-8-sig")
    assert sorted(obs.sample_id) == sorted(src.sample_id)
    gj = json.loads((w / "export" / "observations.geojson").read_text(encoding="utf-8"))
    assert len(gj["features"]) == len(src)
    req = json.loads((w / "export" / "requests.json").read_text(encoding="utf-8"))
    assert {r["file"] for r in req} >= {"observations.csv", "pairs.csv", "zones.geojson"}


def test_summary_and_repeatability(run, tmp_path):
    ra, w = run
    s = json.loads((w / "run_summary.json").read_text(encoding="utf-8"))
    assert s["selection"]["csv_rows"] == len(pd.read_csv(w / "samples.csv"))
    assert s["pairs"]["events"] == len(EVENTS)
    assert all(v for v in s["outputs_sha256"].values())
    assert all(st["ok"] for st in s["steps"].values())
    # повторная подготовка в тот же каталог даёт те же sha256 реестров и таблиц пар
    keys = [k for k in s["outputs_sha256"] if "/export/" not in k and "detector" not in k]
    assert ra.main(["prepare", "--offline", "--workdir", str(w), "--events", ",".join(EVENTS)]) == 0
    s2 = json.loads((w / "run_summary.json").read_text(encoding="utf-8"))
    assert {k: s["outputs_sha256"][k] for k in keys} == {k: s2["outputs_sha256"][k] for k in keys}


def test_offline_cache_miss_is_reason_not_crash(tmp_path):
    ra = _load()
    w = tmp_path / "w"
    rc = ra.main(["prepare", "--offline", "--workdir", str(w), "--pairs-cache", str(tmp_path / "empty_cache"),
                  "--events", "S1:0.1,S4:DOORS3:T2"])
    assert rc == 0
    reg = pd.read_csv(w / "case" / "run" / "registry_pairs.csv")
    assert len(reg) == 2 and (reg.status == "reject").all()
    assert reg.reason.str.contains("error").all()
    assert not (w / "pairs" / "best_per_event.csv").exists()
