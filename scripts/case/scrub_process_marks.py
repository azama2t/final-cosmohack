r"""Убрать из документов сдачи служебные метки процесса: номера задач «L86…L126», ссылки на внутренние указания
(«INBOX §23 п.2», «§24 п.4»), слово «оркестратор». Смысл и числа не меняются: номер задачи заменяется названием работы.

    .venv\Scripts\python.exe scripts\case\scrub_process_marks.py            # правит файлы из FILES
    .venv\Scripts\python.exe scripts\case\scrub_process_marks.py --check    # только показать, что осталось

Журналы работы (docs/LOG.md, docs/SEARCH_LOG.md, reports/tasklog/*) не трогаются. Концы строк сохраняются как были.
"""
from __future__ import annotations

import argparse
import glob
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = ["reports/case_demo/heldout_scene.md", "docs/PHOTO_COUNT.md", "docs/COUNT_DATASETS.md", "docs/LABELS.md",
         "docs/CRITERIA_CHECK.md", "docs/OIL.md", "docs/QUANTITY.md"] + sorted(
    str(Path(p).relative_to(ROOT)).replace("\\", "/") for p in glob.glob(str(ROOT / "docs" / "research" / "**" / "*.md"), recursive=True))

NAMES = {
    86: "розыск S3", 87: "розыск S4", 88: "розыск S1", 89: "разметка PLP/FloatingObjects", 90: "отрицательные примеры",
    91: "полевые данные", 92: "детектор v2", 93: "количество", 94: "фронт v3", 95: "студия", 96: "замеры производительности",
    97: "журнал поиска", 98: "каталог Cózar", 99: "бейзлайн U-Net", 100: "пары ADIS", 101: "нефть", 102: "документы",
    103: "чистый клон", 104: "жюри", 105: "поиск пар для количества", 106: "методы", 107: "аудит", 108: "критерии",
    109: "счётчик по фото", 110: "калибровка по ячейкам", 111: "карта и демо", 112: "поиск наборов (камера у воды)",
    113: "поиск наборов (дроны)", 114: "поиск наборов (аэро и ВР)", 115: "мост дрон → S2", 116: "поиск наборов (каталоги)",
    117: "исследование количества", 118: "гипотезы", 119: "прогноз ADIS", 120: "поиск на GitHub", 121: "классификация",
    122: "поиск наборов", 123: "метки и материал", 124: "жюри-человек", 125: "работоспособность", 126: "презентация",
}
SECT = r"§\s*(?:1[1-9]|[2-9]\d)(?:\s*[А-ЯA-Z](?![а-яa-z]))?(?:\s*п\.\s*[\d,–]+)?"
PAT_L = re.compile(r"\bL(\d{2,3})\b")
PAT_SECT_PAREN = re.compile(r"[,;]?\s*\(\s*" + SECT + r"\s*\)")
PAT_SECT = re.compile(r"[,;]?\s*" + SECT)
ORCH = [("оркестратором", "командой"), ("оркестратора", "команды"), ("оркестратору", "команде"), ("оркестратор", "команда"),
        ("Оркестратор", "Команда")]
TRACE = re.compile(r"\bL\d{2,3}\b|INBOX(?!_REQUESTS)|оркестрат|§\s*(?:1[1-9]|[2-9]\d)")


def scrub(t: str) -> str:
    t = re.sub(r"INBOX(?!_REQUESTS)\s*§\s*\d{1,2}(?:\s*[А-ЯA-Z](?![а-яa-z]))?(?:\s*п\.\s*[\d,–]+)?", "задание команды", t)
    t = PAT_SECT_PAREN.sub("", t)
    t = PAT_SECT.sub("", t)

    def lname(m):
        n = int(m.group(1))
        return f"«{NAMES[n]}»" if n in NAMES else "исполнитель"
    t = PAT_L.sub(lname, t)
    for a, b in ORCH:
        t = t.replace(a, b)
    t = re.sub(r"\(\s*[,;]?\s*\)", "", t)
    t = re.sub(r"(?<=\S)[ \t]{2,}(?=\S)", " ", t)
    t = re.sub(r" / *:", ":", t)
    t = re.sub(r"\| *:\s*", "| ", t)
    t = re.sub(r"(?<=[^\s-]) +([,;)])", r"\1", t)
    return t


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    left = 0
    for rel in FILES:
        p = ROOT / rel
        if not p.is_file():
            continue
        t = p.read_bytes().decode("utf-8")
        out = []
        for ln in t.splitlines(keepends=True):
            body = ln.rstrip("\r\n")
            out.append((body if a.check else scrub(body)) + ln[len(body):])
        new = "".join(out)
        n = len(TRACE.findall(new))
        left += n
        if not a.check and new != t:
            p.write_bytes(new.encode("utf-8"))
        print(f"{rel}: осталось меток {n}")
    return 0 if left == 0 or not a.check else 1


if __name__ == "__main__":
    raise SystemExit(main())
