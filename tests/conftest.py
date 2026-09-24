"""Shared test setup: src/ and repo root on sys.path; MARIDA location fixture.

MARIDA root: env MARIDA_ROOT, else <repo>/data/MARIDA. Tests that need it are skipped if absent.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO / "src"), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")


def _marida_root() -> Path:
    env = os.environ.get("MARIDA_ROOT")
    return Path(env) if env else REPO / "data" / "MARIDA"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO


@pytest.fixture(scope="session")
def marida_root() -> Path:
    """Unpacked MARIDA (splits/ + patches/); skips the test if missing or incomplete."""
    p = _marida_root()
    if not (p / "splits" / "val_X.txt").exists() or not (p / "patches").is_dir():
        pytest.skip(f"MARIDA not found at {p} (set MARIDA_ROOT)")
    return p
