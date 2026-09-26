"""L123 (INBOX §30 п.4): canonical label scheme, labels_map.csv, «состав по классам» rule, target populations."""
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

from macroplastic.labels import composition as C  # noqa: E402
from macroplastic.labels import schema as S  # noqa: E402
from macroplastic.labels import targets as T  # noqa: E402


@pytest.fixture(scope="module")
def rows():
    return S.load_map()


def test_map_is_valid(rows):
    assert S.validate(rows) == []


def test_required_datasets_present(rows):
    have = {r["dataset"] for r in rows}
    for ds in ("FML", "Winans2023", "TOCL_RMS", "MARIDA", "MADOS", "ADIS", "UCWD"):
        assert ds in have, ds


def test_winans_counts_from_data_and_not_all_plastic(rows):
    w = {r["source_label"]: r for r in S.dataset_rows("Winans2023", rows)}
    evald = {k: int(re.search(r"eval (\d+)", v["n_detail"]).group(1)) for k, v in w.items()}
    assert evald == {"unidentified object": 1551, "buoy": 867, "net cloth": 223, "processed wood": 217,
                     "tire": 144, "metal": 102, "line fragment": 81, "vessel": 10}
    assert sum(int(r["n"]) for r in w.values()) == 10703
    mats = {r["material"] for r in w.values()}
    assert mats >= {"plastic", "organic", "metal", "other", "unknown"}
    assert w["unidentified object"]["material"] == "unknown"
    assert w["processed wood"]["material"] == "organic"
    # plastic is only inferred from the form in Winans -> never "sure"
    assert all(r["material_confidence"] == "unsure" for r in w.values() if r["material"] == "plastic")
    assert S.material_shares("Winans2023", rows)["plastic"] < 0.5


def test_all_plastic_mapping_is_rejected(rows):
    bad = [dict(r) for r in S.dataset_rows("Winans2023", rows)]
    for r in bad:
        r["material"], r["material_confidence"] = "plastic", "unsure"
    errs = S.validate(bad)
    assert any("все метки сведены к «пластик»" in e for e in errs)


def test_sea_foam_is_not_polystyrene(rows):
    for ds in ("MARIDA", "MADOS"):
        foam = S.lookup(ds, "Foam", rows)
        assert foam["role"] == "negative" and foam["material"] == ""
    assert S.lookup("TOCL_RMS", "foam plastics", rows)["material"] == "plastic"
    bad = [dict(S.lookup("MARIDA", "Foam", rows))]
    bad[0].update(role="aggregation", material="plastic", material_confidence="unsure")
    assert any("пена" in e for e in S.validate(bad))


def test_satellite_has_no_forms_and_no_items(rows):
    sat = [r for r in rows if r["sensor"] == "satellite"]
    assert sat and all(r["form"] == "" for r in sat)
    assert all(r["role"] != "item" for r in sat)
    md = S.lookup("MARIDA", "Marine Debris", rows)
    assert md["role"] == "aggregation" and md["material"] == "unknown"
    bad = [dict(md, form="bottle")]
    assert any("бутылок" in e for e in S.validate(bad))


def test_satellite_outputs_mention_no_bottles():
    """Scene zones / satellite API texts must never speak of item forms (INBOX §30 п.4: «на S2 никаких бутылок»)."""
    pat = re.compile(r"бутыл|bottle|пакет[аов]?\b|plastic bag", re.I)
    files = list((ROOT / "data" / "case" / "scene_zones").rglob("*.json")) + \
        list((ROOT / "data" / "case" / "scene_zones").rglob("*.geojson")) + \
        [ROOT / "service" / "case_store.py", ROOT / "service" / "routes_v3.py"]
    hits = [str(f.relative_to(ROOT)) for f in files if f.exists() and pat.search(f.read_text(encoding="utf-8", errors="replace"))]
    assert hits == []


def test_tocl_classes_and_organics(rows):
    t = {r["source_label"]: r for r in S.dataset_rows("TOCL_RMS", rows)}
    assert len(t) == 11 and sum(int(r["n"]) for r in t.values()) == 38812
    assert t["organics"]["origin"] == "natural" and t["organics"]["material"] == "organic"
    assert t["Unidentified Floating Object (UFO)"]["material"] == "unknown"
    assert t["PET bottle"]["form"] == "bottle"


def test_adis_counts_include_animal_plant(rows):
    a = {r["source_label"]: r for r in S.dataset_rows("ADIS", rows)}
    assert a["animal"]["origin"] == "natural" and a["plant"]["origin"] == "natural"
    assert sum(int(r["n"]) for r in a.values()) == 22642


def test_composition_single_class_models_not_determined():
    for survey in ("water_camera", "aerial"):
        out = C.composition_for(survey, boxes=[[0, 0, 1, 1]] * 3, card={"threshold": 0.8})
        assert out["status"] == "not_determined"
        assert out["text"] == "всего предметов, состав не определён"
        assert out["total"] == 3 and out["classes"] == [] and out["reason"]


def test_composition_satellite_never_classes():
    """§33: спутниковая зона — «состав не определён», никаких классов, даже при карточке с material_eval."""
    conf = [[100, 0, 0, 0, 0], [0, 90, 0, 0, 10], [0, 0, 40, 0, 5], [0, 0, 0, 50, 0], [5, 5, 5, 0, 200]]
    for kw in ({"survey": "satellite"}, {"survey": "aerial", "sensor": "satellite"}):
        out = C.composition_for(**kw, boxes=[1, 2], card={"material_eval": _me(conf)}, box_materials=["organic"] * 2)
        assert out["status"] == "not_determined" and out["text"] == "состав не определён"
        assert out["classes"] == [] and "форма" in out["reason"]


def _me(conf, classes=("plastic", "organic", "metal", "other", "unknown"), **kw):
    return {"classes": list(classes), "confusion": conf, "heldout": "test", "heldout_independent": True,
            "source_dataset": "Winans2023", **kw}


def test_rule_needs_heldout_labelled_and_quality(rows):
    good = [[100, 0, 0, 0, 0], [0, 90, 0, 0, 10], [0, 0, 40, 0, 5], [0, 0, 0, 50, 0], [5, 5, 5, 0, 200]]
    ok, why = C.accepted_classes(_me(good), rows)
    # Winans: plastic and other are guesses from the form -> never reported; organic/metal are explicit labels
    assert ok == ["organic", "metal"]
    assert "не размечен" in why["plastic"] and "не размечен" in why["other"]
    assert C.accepted_classes(_me(good, heldout_independent=False), rows)[0] == []
    few = [[100, 0, 0, 0, 0], [0, 90, 0, 0, 10], [0, 0, 20, 0, 0], [0, 0, 0, 50, 0], [5, 5, 5, 0, 200]]
    assert C.accepted_classes(_me(few), rows)[0] == ["organic"]
    weak = [[100, 0, 0, 0, 0], [0, 50, 0, 0, 50], [0, 0, 40, 0, 5], [0, 0, 0, 50, 0], [5, 5, 5, 0, 200]]
    assert C.accepted_classes(_me(weak), rows)[0] == ["metal"]
    assert C.accepted_classes({**_me(good), "source_dataset": None}, rows)[0] == []


def test_composition_by_class_counts_boxes():
    conf = [[100, 0, 0, 0, 0], [0, 90, 0, 0, 10], [0, 0, 40, 0, 5], [0, 0, 0, 50, 0], [5, 5, 5, 0, 200]]
    card = {"material_eval": _me(conf)}
    mats = ["organic", "plastic", "metal", "organic", None, "unknown"]
    out = C.composition_for("aerial", boxes=list(range(6)), card=card, box_materials=mats)
    assert out["status"] == "by_class"
    got = {c["material"]: c["count"] for c in out["classes"]}
    assert got == {"organic": 2, "metal": 1}
    assert out["undetermined"] == 3 and out["total"] == 6
    assert "на другом источнике не проверено" in out["text"]
    with pytest.raises(ValueError):
        C.composition_for("aerial", boxes=[1, 2], card=card, box_materials=["organic"])


def test_targets_table():
    ids = {t["id"] for t in T.TARGETS}
    assert {"field_S2", "adis", "photo_water", "photo_aerial", "scene_zones"} <= ids
    for t in T.TARGETS:
        assert t["material"] and t["size"] and t["unit"]
    aer = next(t for t in T.TARGETS if t["id"] == "photo_aerial")
    assert "164 м² кадра берега" in aer["unit"]
    sz = next(t for t in T.TARGETS if t["id"] == "scene_zones")
    assert "не выдаём" in sz["unit"]
    md = T.markdown()
    assert md.count("\n") == len(T.TARGETS) + 2


def test_labels_doc_mentions_table():
    doc = ROOT / "docs" / "LABELS.md"
    if not doc.exists():
        pytest.skip("docs/LABELS.md ещё не создан")
    s = doc.read_text(encoding="utf-8")
    for w in ("material", "form", "context", "sensor", "пенопласт", "164 м² кадра берега", "состав не определён"):
        assert w in s, w


@pytest.mark.skipif(not (ROOT / "data" / "extra" / "count_ds" / "winans2023_hawaii_aerial").exists(),
                    reason="данные наборов вне git")
def test_counts_match_local_files():
    from macroplastic.labels.recount import check
    assert check(datasets=["Winans2023", "TOCL_RMS", "UCWD", "ADIS", "Maharjan2022", "TUD-GV"]) == []


def test_card_accepted_list_and_labels_override():
    conf = [[100, 0, 0, 0, 0], [0, 90, 0, 0, 10], [0, 0, 40, 0, 5], [0, 0, 0, 50, 0], [5, 5, 5, 0, 200]]
    card = {"material_eval": _me(conf, accepted=["organic"], labels_ru={"organic": "дерево (обработанное)"})}
    out = C.composition_for("aerial", boxes=[1, 2, 3], card=card, box_materials=["organic", "metal", None])
    assert [c["material"] for c in out["classes"]] == ["organic"]
    assert out["by_class"] == {"дерево (обработанное)": 1, "состав не определён": 2}
    assert "metal" in out["not_reported"]


@pytest.mark.skipif(not (ROOT / "reports" / "labels" / "material_winans.json").exists(), reason="M1 не запускался")
def test_m1_decision_consistent():
    import json
    d = json.loads((ROOT / "reports" / "labels" / "material_winans.json").read_text(encoding="utf-8"))
    te = d["splits"]["test"]
    for m in d["decision"]["accepted"]:
        pm = te["per_material"][m]
        assert m in ("organic", "metal")  # only explicitly labelled Winans materials
        assert pm["n_true"] >= C.MIN_TEST_ITEMS and pm["precision"] >= C.MIN_PRECISION and pm["recall"] >= C.MIN_RECALL
        assert pm["mae_diff_ci95"][1] < 0
    assert "plastic" not in d["decision"]["accepted"]
