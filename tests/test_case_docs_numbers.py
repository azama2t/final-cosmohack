"""Числа в деке, речи, демо, вопросах и README совпадают с reports/final_numbers.json.

Генератор scripts/make_deck_case.py записывает каждый прочитанный путь final_numbers.json в USED. Здесь проверяется:
  1. каждое подставленное значение равно значению в final_numbers.json (независимое чтение);
  2. каждое число в текстах деки/речи/демо/вопросов — либо подставленное значение, либо единица/константа постановки
     из короткого белого списка (10 м, 2 см, 90 %, …), а не число «из головы»;
  3. файлы на диске (docs/SPEECH.md, DEMO.md, QA.md, reports/case_deck.pptx) собраны из текущего final_numbers.json;
  4. отпечаток прогона в деке = reports/case_run/run_summary.json;
  5. README и дека показывают одинаковые ключевые числа (F1 test, MAE test S2/S1, пары 29/12/0, число тестов).
Если тест упал — пересоберите документы: .venv\\Scripts\\python.exe scripts\\case\\build_docs.py
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FN = ROOT / "reports" / "final_numbers.json"
DECK = ROOT / "reports" / "case_deck.pptx"

pytestmark = pytest.mark.skipif(not FN.exists(), reason="нет reports/final_numbers.json")

# Числа, которые не являются результатами: единицы, размерные классы и константы постановки, номера разделов,
# названия (Sentinel-2, S1..S4, B11, L2A, F1, log1p), годы источников, номера слайдов и тайминг речи.
WHITELIST = {"0", "95", "1920", "1080", "1366", "768"}  # ноль; уровень доверия 95 %; окна браузера для демо 1920×1080, 1366×768
TIMING = re.compile(r"^\d:\d\d$")


def _gen():
    spec = importlib.util.spec_from_file_location("make_deck_case", ROOT / "scripts" / "make_deck_case.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    S, texts, k = m.generate()
    return m, S, texts, k


@pytest.fixture(scope="module")
def gen():
    return _gen()


@pytest.fixture(scope="module")
def fn():
    return json.loads(FN.read_text(encoding="utf-8"))


def _get(d, path):
    cur = d
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def _variants(v) -> set[str]:
    """Как значение может выглядеть в тексте: 0–3 знака, точка/запятая, ASCII-минус/настоящий минус, проценты доли."""
    out: set[str] = set()
    vals = []
    if isinstance(v, bool):
        return out
    if isinstance(v, (int, float)):
        vals.append(float(v))
    elif isinstance(v, str):
        out.update(re.findall(r"\d+(?:[.,]\d+)?", v))
        return out
    elif isinstance(v, (list, tuple)):
        for x in v:
            out |= _variants(x)
        return out
    elif isinstance(v, dict):
        for x in v.values():
            out |= _variants(x)
        return out
    for x in vals:
        for d in range(0, 4):
            s = f"{abs(x):.{d}f}"
            out.update({s, s.replace(".", ",")})
        if float(x).is_integer():
            out.add(str(int(abs(x))))
        if abs(x) <= 1:
            out.add(f"{abs(x) * 100:.0f}")
    return out


def _numbers(text: str) -> list[str]:
    text = re.sub(r"\b\d{1,2}:\d\d\b", " ", text)          # тайминг речи и демо (0:40, 1:25–1:40)
    text = re.sub(r"≈ \d+ слов\w*", " ", text)             # число слов речи считается по самому тексту
    text = re.sub(r"(?m)^#+ \d+\.", " ", text)             # номера вопросов и слайдов в заголовках
    text = re.sub(r"(т\.|трансекта )\d+", " ", text)         # имя трансекты (HE460 т.03)
    text = re.sub(r"`[^`]*`", " ", text)                 # команды и пути в обратных кавычках
    text = re.sub(r"\b[\w./\\-]+\.(py|md|json|csv|pptx|yaml|jpg|png|tif|ps1)\b", " ", text)  # имена файлов
    text = re.sub(r"\b(S[1-4]|B\d+|L2A|L1C|F1|log1p|MSM41|HE\d+|T\d+|v3|ERA5|MADOS|Sentinel-2A?|DOORS\s?3?)\b", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\b[0-9a-f]{7,}\b", " ", text)          # хэши коммитов и отпечатки
    return re.findall(r"\d+(?:[.,]\d+)?", text)


def test_used_values_equal_final_numbers(gen, fn):
    m, *_ = gen
    assert m.USED, "генератор не прочитал ни одного числа"
    assert not m.MISSING, f"не найдены пути: {m.MISSING}"
    for path, v in m.USED.items():
        assert _get(fn, path) == v, path


def test_every_number_in_texts_is_sourced(gen):
    m, S, texts, k = gen
    allowed = set(WHITELIST)
    for v in m.USED.values():
        allowed |= _variants(v)
    bad = {}
    chunks = {f"slide {i}": "\n".join(m.slide_strings(s)) for i, s in enumerate(S, 1)}
    chunks.update({p.name: t for p, t in texts.items()})
    for name, txt in chunks.items():
        for tok in _numbers(txt):
            if tok in allowed or TIMING.match(tok):
                continue
            bad.setdefault(name, set()).add(tok)
    assert not bad, f"числа без источника в final_numbers.json: {bad}"


def test_no_previous_detector_mode_numbers_out_of_context(gen, fn):
    """Числа прежнего режима детектора (с гармонизацией: 975 объектов, 721/724 на HE460 т.03, 6 в полосах) допустимы
    только в предложении, явно помеченном «прежний режим». Текущий режим — case.detector_current."""
    m, S, texts, _ = gen
    dr = fn["case"].get("detector_review") or {}
    old = {str(dr[key]) for key in ("n_obj", "he460_n_obj", "he460_n_out_strip") if isinstance(dr.get(key), int)}
    cur = fn["case"].get("detector_current") or {}
    old -= {str(cur.get(key)) for key in ("n_obj", "n_in_strip", "n_crops")}
    chunks = {f"slide {i}": "\n".join(m.slide_strings(s)) for i, s in enumerate(S, 1)}
    chunks.update({p.name: t for p, t in texts.items()})
    bad = []
    # README: контекст — абзац вместе со своим списком (жирный заголовок «Прежний режим …» помечает весь список)
    for block in (ROOT / "README.md").read_text(encoding="utf-8").split("\n\n"):
        for tok in re.findall(r"\b\d+\b", block):
            if tok in old and "прежн" not in block.lower():
                bad.append(f"README.md: «{block.strip()[:120]}»")
    # дека, речь, демо, вопросы: контекст — одно предложение
    for name, txt in chunks.items():
        for sent in re.split(r"(?<=[.;!?])\s+|\n", txt):
            for tok in re.findall(r"\b\d+\b", sent):
                if tok in old and "прежн" not in sent.lower():
                    bad.append(f"{name}: «{sent.strip()[:120]}»")
    assert not bad, "числа прежнего режима детектора без пометки «прежний режим»:\n" + "\n".join(bad)


def test_md_files_are_fresh(gen):
    _, _, texts, _ = gen
    for p, t in texts.items():
        assert p.exists(), p
        assert p.read_text(encoding="utf-8") == t, f"{p.relative_to(ROOT)} устарел: пересоберите scripts/case/build_docs.py"


def _deck_text() -> str:
    from pptx import Presentation
    prs = Presentation(str(DECK))
    parts = []
    for sl in prs.slides:
        for sh in sl.shapes:
            if sh.has_text_frame:
                parts.append(sh.text_frame.text)
            if getattr(sh, "has_table", False) and sh.has_table:
                parts += [c.text for r in sh.table.rows for c in r.cells]
        if sl.has_notes_slide:
            parts.append(sl.notes_slide.notes_text_frame.text)
    return "\n".join(parts)


def test_deck_is_fresh(gen):
    pytest.importorskip("pptx")
    m, S, _, _ = gen
    deck = _deck_text()
    for i, s in enumerate(S, 1):
        for line in m.slide_strings(s):
            body = line[2:] if line.startswith("• ") else line
            assert body in deck, f"слайд {i}: нет строки «{body[:80]}» — дека устарела, пересоберите build_docs.py"


def test_fingerprint_matches_run_summary(fn):
    pytest.importorskip("pptx")
    rs = json.loads((ROOT / "reports" / "case_run" / "run_summary.json").read_text(encoding="utf-8"))
    fp = (rs.get("outputs_fingerprint") or "")[:16]
    assert fp and fp == fn["case"]["run"]["outputs_fingerprint_short"], "final_numbers.json не пересобран после прогона"
    assert fp in _deck_text(), "отпечаток в деке не совпадает с run_summary.json"


def test_readme_and_deck_show_same_key_numbers(fn):
    pytest.importorskip("pptx")
    c = fn["case"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    deck = _deck_text()
    ft = c["sections"]["field_test"]
    keys = {
        "F1 test": f"{c['detector']['test']['lgbm']['f1']:.3f}",
        "MAE test S2 модель": f"{ft['S2']['main_mae']:.1f}", "MAE test S2 медиана": f"{ft['S2']['median_mae']:.1f}",
        "MAE test S1 модель": f"{ft['S1']['main_mae']:.1f}", "MAE test S1 медиана": f"{ft['S1']['median_mae']:.1f}",
        "пары по метаданным": str(c["pairs"]["events_accept_meta"]), "пары по маскам": str(c["pairs"]["quality_accept"]),
        "синхронные пары": str(c["pairs"]["events_accept_drift"]), "тесты кейса": f"{c['n_tests']} passed",
        "отпечаток": c["run"]["outputs_fingerprint_short"],
    }
    for name, val in keys.items():
        assert val in readme, f"README: нет {name} = {val}"
        assert val in deck, f"дека: нет {name} = {val}"
