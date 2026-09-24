"""Common helpers: project root, determinism, CPU/GPU detection, logging, YAML loading.

    from macroplastic.utils import ROOT, seed_everything, available_cpus, cuda_usable, get_logger, load_yaml
"""
from __future__ import annotations

import logging
import os
import random
import sys
from pathlib import Path
from typing import Any

import numpy as np

#: Repository root (.../final-cosmohack), i.e. two levels above src/macroplastic.
ROOT = Path(__file__).resolve().parents[2]
CONFIGS = ROOT / "configs"

_MAX_THREADS = 16


def seed_everything(seed: int = 42, deterministic_torch: bool = True) -> None:
    """Seed python/numpy/(torch if importable); make torch deterministic (no cudnn benchmark, no TF32)."""
    random.seed(seed)
    np.random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    try:
        import torch  # slow import, optional
    except Exception:
        return
    torch.manual_seed(seed)
    try:
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except Exception:
        pass
    if deterministic_torch:
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False


def _windows_affinity_count() -> int | None:
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t),
                                               ctypes.POINTER(ctypes.c_size_t)]
        proc_mask = ctypes.c_size_t()
        sys_mask = ctypes.c_size_t()
        if not k32.GetProcessAffinityMask(k32.GetCurrentProcess(), ctypes.byref(proc_mask), ctypes.byref(sys_mask)):
            return None
        return bin(proc_mask.value).count("1") or None
    except Exception:
        return None


def available_cpus() -> int:
    """CPUs usable by this process: affinity mask (Linux sched_getaffinity / Windows
    GetProcessAffinityMask), else os.cpu_count(). Never less than 1."""
    n = None
    if hasattr(os, "sched_getaffinity"):
        try:
            n = len(os.sched_getaffinity(0))
        except Exception:
            n = None
    elif sys.platform == "win32":
        n = _windows_affinity_count()
    if not n:
        n = os.cpu_count() or 1
    return max(1, int(n))


def auto_threads(value: Any = None, cap: int = _MAX_THREADS) -> int:
    """None/0/'auto' -> min(available_cpus(), cap); otherwise max(1, int(value))."""
    if value is None or value == 0 or (isinstance(value, str) and value.strip().lower() in ("", "0", "auto")):
        return max(1, min(available_cpus(), cap))
    return max(1, int(value))


def cuda_usable() -> bool:
    """True only if torch imports and a tiny tensor can really be placed on CUDA.

    Respects CUDA_VISIBLE_DEVICES="" (returns False without importing torch).
    Any exception -> False (the caller falls back to CPU).
    """
    if os.environ.get("CUDA_VISIBLE_DEVICES") == "":
        return False
    try:
        import torch

        if not torch.cuda.is_available():
            return False
        torch.zeros(1, device="cuda")
        return True
    except Exception:
        return False


def resolve_device(requested: str = "auto") -> str:
    """'cpu' | 'cuda' | 'auto' -> 'cpu' or 'cuda' ('cuda' only if cuda_usable())."""
    requested = (requested or "auto").lower()
    if requested == "cpu":
        return "cpu"
    return "cuda" if cuda_usable() else "cpu"


def get_logger(name: str = "macroplastic", level: int = logging.INFO) -> logging.Logger:
    """Logger to stderr with a compact format (handler is added once)."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
        logger.addHandler(h)
        logger.propagate = False
    logger.setLevel(level)
    return logger


def load_yaml(path: str | os.PathLike) -> Any:
    """Load a YAML file (UTF-8). Empty file -> {}. A relative path missing from cwd is tried from ROOT."""
    import yaml

    p = Path(path)
    if not p.exists() and not p.is_absolute() and (ROOT / p).exists():
        p = ROOT / p
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {} if data is None else data
