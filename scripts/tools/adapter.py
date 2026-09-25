r"""Run the dataset adapter (macroplastic.organizer_adapter) from the repository root.

Same arguments as `python -m macroplastic.organizer_adapter`; this wrapper only puts `src` on sys.path,
so no PYTHONPATH and no `cd src` are needed:

    .venv\Scripts\python.exe scripts\tools\adapter.py --config configs\adapter_example.yaml --dry-run
    .venv\Scripts\python.exe scripts\tools\adapter.py --config configs\adapter_org.yaml --out data\organizer --limit 20
    .venv\Scripts\python.exe scripts\tools\adapter.py --config configs\adapter_marida.yaml --out data\organizer --verify-marida 60

Relative paths in the YAML (`root`) are resolved against the repository root.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))

from macroplastic.organizer_adapter.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
