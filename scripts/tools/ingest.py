r"""Run the dataset loader (macroplastic.ingest) from the repository root, without PYTHONPATH.

    .venv\Scripts\python.exe scripts\tools\ingest.py D:\org\train.zip --out data\ingest\org
    .venv\Scripts\python.exe scripts\tools\ingest.py D:\org\train.zip --out data\ingest\org --convert
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))

from macroplastic.ingest.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
