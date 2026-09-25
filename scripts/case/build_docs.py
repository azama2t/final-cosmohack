r"""Сборка всех документов кейса одной командой, в правильном порядке.

    .venv\Scripts\python.exe scripts\case\build_docs.py            # полный порядок
    .venv\Scripts\python.exe scripts\case\build_docs.py --no-run   # без повторного маршрута (только тесты и документы)

Порядок:
  1. scripts/case/run_all.py all --offline      маршрут кейса → reports/case_run/run_summary.json (отпечаток, время)
  2. final_numbers → render_docs → make_deck_case  документы из текущих чисел (число тестов — с прошлого прогона)
  3. pytest тестов кейса (tests/test_case_*.py + tests/test_api_v3.py, включая сверку чисел документов)
     → reports/case_run/case_tests.json (passed / skipped / failed / секунды / время)
  4. final_numbers → render_docs → make_deck_case  ещё раз: в README и деку попадает фактическое число тестов этого прогона
  5. pytest tests/test_case_docs_numbers.py     README, дека, речь, демо и вопросы = final_numbers.json

Все числа README, отчёта, PREP, деки, речи, демо и вопросов берутся из reports/final_numbers.json. Руками после этой
команды ничего не правится. CPU; сеть не нужна (маршрут офлайн, из кэша).
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
TESTS_JSON = ROOT / "reports" / "case_run" / "case_tests.json"
DOCS_TEST = "tests/test_case_docs_numbers.py"


def case_test_files() -> list[str]:
    files = sorted(glob.glob("tests/test_case_*.py", root_dir=ROOT)) + ["tests/test_api_v3.py"]
    return [f.replace("\\", "/") for f in files]


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print("[build_docs] $", " ".join(cmd), flush=True)
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="", PYTHONIOENCODING="utf-8")
    p = subprocess.run(cmd, cwd=ROOT, env=env, text=True, encoding="utf-8", errors="replace", capture_output=True)
    out = (p.stdout or "") + (p.stderr or "")
    print("\n".join(out.strip().splitlines()[-6:]), flush=True)
    if check and p.returncode != 0:
        print(f"[build_docs] ОШИБКА: код {p.returncode}")
        sys.exit(p.returncode)
    return p


def docs() -> None:
    run([PY, "scripts/final_numbers.py"])
    run([PY, "scripts/render_docs.py"])
    run([PY, "scripts/make_deck_case.py"])


def pytest_case() -> dict:
    files = case_test_files()
    cmd = [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", *files]
    t0 = time.time()
    p = run(cmd, check=False)
    out = (p.stdout or "") + (p.stderr or "")
    last = [ln for ln in out.splitlines() if re.search(r"\d+ (passed|failed|error)", ln)]
    line = last[-1] if last else ""

    def grab(word):
        m = re.search(rf"(\d+) {word}", line)
        return int(m.group(1)) if m else 0
    res = {"passed": grab("passed"), "skipped": grab("skipped"), "failed": grab("failed") + grab("error"),
           "seconds": round(time.time() - t0, 1), "when": dt.datetime.now().strftime("%d.%m.%Y %H:%M"),
           "command": "python -m pytest -q " + " ".join(files), "summary": line.strip(" =")}
    TESTS_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[build_docs] тесты кейса: {res['passed']} passed, {res['skipped']} skipped, {res['failed']} failed "
          f"-> {TESTS_JSON.relative_to(ROOT)}")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="run_all → тесты → final_numbers → render_docs → make_deck_case → сверка")
    ap.add_argument("--no-run", action="store_true", help="не запускать маршрут run_all (взять текущий run_summary.json)")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.time()
    if not a.no_run:
        run([PY, "scripts/case/run_all.py", "all", "--offline"])
    run([PY, "scripts/case/pairs_detector_summary.py"])  # текущий результат детектора на снимках пар
    first = run([PY, "scripts/final_numbers.py"])  # noqa: F841
    run([PY, "scripts/render_docs.py"])
    run([PY, "scripts/make_deck_case.py"], check=False)  # при самом первом запуске ещё нет case_tests.json
    res = pytest_case()
    docs()
    fin = run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", DOCS_TEST], check=False)
    ok = fin.returncode == 0 and res["failed"] == 0
    print(f"[build_docs] {'готово' if ok else 'ЕСТЬ ОШИБКИ'} за {time.time() - t0:.0f} с")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
