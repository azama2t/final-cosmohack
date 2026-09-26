"""§11 «Расследование данных»: числа в README, отчёте, деке, речи и вопросах = reports/final_numbers.json.

Проверяется:
  1. разделы case.sections.{search, labeled_data, adis_pairs, baselines, quantity, oil, detector_v2} в final_numbers.json
     совпадают с тем, что сейчас собирает scripts/case/collect_search.py из файлов в git (иначе final_numbers устарел);
  2. reports/search/search_numbers.json — тот же сбор;
  3. README.md и reports/report.md совпадают с шаблонами, отрендеренными из текущего final_numbers.json;
  4. ключевые числа расследования стоят в README, отчёте, деке и вопросах в том же виде, что в final_numbers.json;
  5. внутренняя согласованность: уровни A–D в сумме = строк, ADIS A+C+D = отрезков, воронка = реестр пар, утечек 0;
  6. формулировки: доля покрытия ≠ мусор/шт./км², масса не заявляется, нефть — эксперимент.
Если тест упал — пересоберите: .venv\\Scripts\\python.exe scripts\\case\\build_docs.py --no-run
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FN = ROOT / "reports" / "final_numbers.json"
KEYS = ("search", "labeled_data", "adis_pairs", "baselines", "quantity", "oil", "detector_v2", "independent_check", "scene_zones")

pytestmark = pytest.mark.skipif(not FN.exists(), reason="нет reports/final_numbers.json")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def fn():
    return json.loads(FN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def sec(fn):
    s = fn["case"]["sections"]
    missing = [k for k in KEYS if k not in s]
    assert not missing, f"в final_numbers.json нет разделов {missing}: пересоберите scripts/final_numbers.py"
    return s


@pytest.fixture(scope="module")
def fresh():
    cs = _load("collect_search", ROOT / "scripts" / "case" / "collect_search.py")
    return json.loads(json.dumps(cs.collect(), ensure_ascii=False))


@pytest.fixture(scope="module")
def deck_texts():
    m = _load("make_deck_case", ROOT / "scripts" / "make_deck_case.py")
    S, texts, _ = m.generate()
    slides = "\n".join("\n".join(m.slide_strings(s)) for s in S)
    return slides, {p.name: t for p, t in texts.items()}


def test_sections_equal_fresh_collect(sec, fresh):
    for k in KEYS:
        assert sec[k] == fresh[k], f"case.sections.{k} устарел относительно исходников — пересоберите final_numbers.py"


def test_search_numbers_json_is_fresh(fresh):
    p = ROOT / "reports" / "search" / "search_numbers.json"
    if not p.exists():
        pytest.skip("нет reports/search/search_numbers.json")
    assert json.loads(p.read_text(encoding="utf-8")) == fresh, "search_numbers.json устарел: scripts/case/collect_search.py"


def test_readme_and_report_rendered_from_current_numbers(fn):
    rd = _load("render_docs", ROOT / "scripts" / "render_docs.py")
    ctx = dict(fn)
    ctx["derived"] = rd.derived(fn)
    for src, dst in (("templates/README.md.tmpl", "README.md"), ("templates/reports/report.md.tmpl", "reports/report.md")):
        want = rd.render((ROOT / src).read_text(encoding="utf-8"), ctx, [])
        got = (ROOT / dst).read_text(encoding="utf-8")
        assert got == want, f"{dst} не совпадает с шаблоном и final_numbers.json — пересоберите render_docs.py"


def _key_strings(s: dict) -> dict:
    ad, ld, bl, v2, q, oil = (s[k] for k in ("adis_pairs", "labeled_data", "baselines", "detector_v2", "quantity", "oil"))
    c = q["calibration"]
    return {
        "ADIS пар по месту и времени": f"{ad['A']} пар по месту и времени",
        "ADIS A с предметами": f"{ad['A_with_items']} пар",
        "ADIS плотность": f"{ad['A_with_items_density_min']:.1f}–{ad['A_with_items_density_max']:.1f}",
        "B новых съёмок": str(ld["b_new_acq"]),
        "U-Net test F1": f"{bl['test']['unet_argmax']['f1']:.3f}",
        "LightGBM test F1": f"{bl['test']['lgbm']['f1']:.3f}",
        "пар для калибровки": f"{c['n_pairs_k_x2_min']}–{c['n_pairs_k_x2_max']}",
        "нефть test F1": f"{oil['test_f1']:.3f}",
        "дообучение вариантов": f"{v2['n_variants']} вариантов с новыми данными",
    }


@pytest.mark.parametrize("doc", ["README.md", "reports/report.md"])
def test_key_numbers_in_markdown(sec, doc):
    txt = (ROOT / doc).read_text(encoding="utf-8")
    bad = {k: v for k, v in _key_strings(sec).items() if v not in txt}
    assert not bad, f"{doc}: нет чисел расследования {bad}"


def test_key_numbers_in_deck_and_qa(sec, deck_texts):
    slides, texts = deck_texts
    ks = _key_strings(sec)
    bad = {k: v for k, v in ks.items() if v not in slides}
    assert not bad, f"дека: нет чисел расследования {bad}"
    qa = texts["QA.md"]
    ad, q = sec["adis_pairs"], sec["quantity"]["calibration"]
    for v in (f"Все {ad['A']} пар".lower(), f"{q['n_pairs_k_x2_min']}–{q['n_pairs_k_x2_max']}"):
        assert v in qa.lower(), f"QA.md: нет «{v}»"
    for question in ("пары уровня A", "калибровк", "Откуда B и D", "утечки"):
        assert question.lower() in qa.lower(), f"QA.md: нет вопроса про «{question}»"


def test_deck_file_contains_search_slides(sec):
    pptx = pytest.importorskip("pptx")
    prs = pptx.Presentation(str(ROOT / "reports" / "case_deck.pptx"))
    text = "\n".join(sh.text_frame.text for sl in prs.slides for sh in sl.shapes if sh.has_text_frame)
    for v in ("Как мы искали данные", "Пары по месту и времени (ADIS)", "Новые размеченные данные B/D", "Разбор ошибок на сложном фоне",
              "Приложение · Нефтяное пятно — эксперимент",
              _key_strings(sec)["U-Net test F1"]):
        assert v in text, f"reports/case_deck.pptx: нет «{v}» — пересоберите make_deck_case.py"


def test_internal_consistency(fn, sec):
    s, ad, ld = sec["search"], sec["adis_pairs"], sec["labeled_data"]
    for key, r in (s.get("by_source") or {}).items():
        if r.get("available"):
            assert sum(r[lv] for lv in "ABCD") == r["rows"], key
            assert sum(r["d_reasons"].values()) == r["D"], f"{key}: причины D не покрывают все D"
    if ad.get("available"):
        assert ad["A"] + ad["B"] + ad["C"] + ad["D"] == ad["segments"]
        assert ad["A_with_items"] <= ad["A"] and ad["A_zero"] + ad["A_with_items"] == ad["A"]
        assert ad["fa_calm"]["pairs"] + ad["fa_windy"]["pairs"] == ad["A_zero_eval"]
        assert ad["A_eval"] + ad["A_not_eval"] == ad["A"] and ad["A_with_items_eval"] <= ad["A_with_items"]
        assert ad["A"] == s["by_source"]["ADIS"]["A"]
    assert s["events_total"] == fn["case"]["pairs"]["events"], "воронка docs/img/funnel.json ≠ реестр пар"
    assert s["csv_A"] == 0 or s["csv_A"] == sum(s["by_source"][k]["A"] for k in ("S1", "S2", "S3", "S4"))
    assert ld["leaks_total"] == 0, "найдены утечки новых данных с MARIDA/MADOS/нашими сценами — разберитесь до сдачи"
    assert sec["oil"]["experimental"] is True and sec["oil"]["enabled_by_default"] is False
    b = sec["baselines"]["test"]
    assert b["lgbm"]["f1"] == fn["case"]["sections"]["marida_test"]["lgbm_f1"], "LightGBM test в baselines ≠ marida_test"


def test_wording_no_mass_no_coverage_as_litter():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    part = readme.split("## Как мы искали данные", 1)[1].split("\n## 1.", 1)[0]
    assert "не мусор и не шт./км²" in part
    assert not re.search(r"\b(кг|тонн\w*|т/км²|г/м²)\b", part), "в разделе заявлена масса"
    assert "эксперимент" in part.lower() and "нефт" in part.lower()
    assert "не задаёт общий предел для всех скоплений" in part
    assert "пар по месту и времени" in part


FORBIDDEN = (r"предел обнаружения доказан", r"(это|—) предел обнаружения", r"\d+ пар A\b", r"калибровочн\w* пар\w* \(ADIS",
             r"доверительн\w* интервал\w* сценари")


def test_wording_section15(deck_texts):
    """INBOX §15: нет «предел обнаружения» как доказанного факта; 66 ADIS — «пары по месту и времени», не калибровочные."""
    slides, texts = deck_texts
    docs = {"README.md": (ROOT / "README.md").read_text(encoding="utf-8"),
            "reports/report.md": (ROOT / "reports/report.md").read_text(encoding="utf-8"),
            "docs/QUANTITY.md": (ROOT / "docs/QUANTITY.md").read_text(encoding="utf-8"), "slides": slides, **texts}
    bad = [(name, pat) for name, txt in docs.items() for pat in FORBIDDEN if re.search(pat, txt)]
    assert not bad, f"формулировки §15 нарушены: {bad}"
    trace = re.compile(r"\bL\d{2,3}\b|оркестрат|INBOX|SPEC-GAPS|tasklog")
    leaks = [(name, m.group(0)) for name, txt in docs.items() for m in [trace.search(txt)] if m]
    assert not leaks, f"следы внутреннего процесса в сдаче: {leaks}"
    ft = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Главный количественный результат" in ft and "медиана профиля (на карте)" in ft
    q = docs["docs/QUANTITY.md"]
    assert "Пара по месту и времени" in q and "Калибровочная пара" in q and "Видимый сигнал" in q
