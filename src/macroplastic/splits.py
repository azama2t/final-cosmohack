"""Official MARIDA splits and scene grouping.

MARIDA layout: <root>/splits/{train,val,test}_X.txt list patch names like '11-6-18_16PCC_0'
(no 'S2_' prefix, no '.tif'); date is D-M-YY without zero padding. The patch lives in
<root>/patches/S2_11-6-18_16PCC/S2_11-6-18_16PCC_0.tif.

    from macroplastic.splits import read_splits, scene_of, scene_overlap
    sp = read_splits(root)                  # {'train': [...694], 'val': [...328], 'test': [...359]}
    scene_of('11-6-18_16PCC_0')             # 'S2_11-6-18_16PCC'
    rep = scene_overlap(sp)                 # scenes shared between splits

Rule (SPEC 1.7): the test split is never used for model/threshold selection.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from pathlib import Path

SPLIT_NAMES = ("train", "val", "test")
EXPECTED_SIZES = {"train": 694, "val": 328, "test": 359}

_PATCH_RE = re.compile(r"^(?:S2_)?(\d{1,2})-(\d{1,2})-(\d{2})_([0-9]{2}[A-Z]{3})(?:_(\d+))?$")


@dataclass(frozen=True)
class PatchId:
    """Parsed MARIDA patch/scene name."""
    date_str: str          # as in the name, e.g. '11-6-18'
    date: _dt.date         # 2018-06-11
    tile: str              # MGRS tile, e.g. '16PCC'
    index: int | None      # patch number inside the scene (None for a scene name)

    @property
    def scene(self) -> str:
        """'S2_<D-M-YY>_<TILE>' (scene folder name)."""
        return f"S2_{self.date_str}_{self.tile}"

    @property
    def name(self) -> str:
        """Split-file form '<D-M-YY>_<TILE>_<N>'."""
        return f"{self.date_str}_{self.tile}" + ("" if self.index is None else f"_{self.index}")


def _clean(name: str) -> str:
    s = Path(str(name).strip()).name
    for ext in (".tif", ".tiff"):
        if s.lower().endswith(ext):
            s = s[: -len(ext)]
    for suf in ("_cl", "_conf"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s


def parse_patch(name: str) -> PatchId:
    """Parse '11-6-18_16PCC_0', 'S2_11-6-18_16PCC_0.tif', '..._cl.tif' or a scene 'S2_11-6-18_16PCC'."""
    s = _clean(name)
    m = _PATCH_RE.match(s)
    if not m:
        raise ValueError(f"not a MARIDA patch/scene name: {name!r}")
    d, mo, yy, tile, idx = m.groups()
    date = _dt.date(2000 + int(yy), int(mo), int(d))
    return PatchId(f"{int(d)}-{int(mo)}-{yy}", date, tile, None if idx is None else int(idx))


def scene_of(patch: str) -> str:
    """Scene id 'S2_DD-MM-YY_TILE' (as the folder name, e.g. 'S2_11-6-18_16PCC') of a patch."""
    return parse_patch(patch).scene


def date_of(patch: str) -> _dt.date:
    return parse_patch(patch).date


def tile_of(patch: str) -> str:
    return parse_patch(patch).tile


def read_split(marida_root, split: str) -> list[str]:
    """Patch names of one official split, in file order (blank lines and CRLF stripped)."""
    if split not in SPLIT_NAMES:
        raise ValueError(f"split must be one of {SPLIT_NAMES}, got {split!r}")
    p = Path(marida_root) / "splits" / f"{split}_X.txt"
    if not p.exists():
        raise FileNotFoundError(str(p))
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def read_splits(marida_root) -> dict[str, list[str]]:
    """{'train': [...], 'val': [...], 'test': [...]} (official MARIDA splits)."""
    return {s: read_split(marida_root, s) for s in SPLIT_NAMES}


def scenes_by_split(splits: dict[str, list[str]]) -> dict[str, set[str]]:
    return {k: {scene_of(p) for p in v} for k, v in splits.items()}


def group_by_scene(patches: list[str]) -> dict[str, list[str]]:
    """{scene: [patches]} preserving order; use scenes as groups for GroupKFold etc."""
    out: dict[str, list[str]] = {}
    for p in patches:
        out.setdefault(scene_of(p), []).append(p)
    return out


def scene_overlap(splits: dict[str, list[str]]) -> dict:
    """Report on scenes (and patches) shared between splits.

    Returns {'n_patches': {split: n}, 'n_scenes': {split: n},
             'shared_scenes': {'train&val': [...], ...}, 'duplicate_patches': {'train&val': [...], ...},
             'scene_disjoint': bool}.
    Measured 25.09.2026 on MARIDA: official splits are scene-disjoint (train 36 / val 12 / test 15
    scenes, 63 total, no shared scene, no duplicate patch).
    """
    sc = scenes_by_split(splits)
    keys = list(splits)
    shared, dup = {}, {}
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            shared[f"{a}&{b}"] = sorted(sc[a] & sc[b])
            dup[f"{a}&{b}"] = sorted(set(splits[a]) & set(splits[b]))
    return {
        "n_patches": {k: len(v) for k, v in splits.items()},
        "n_scenes": {k: len(v) for k, v in sc.items()},
        "shared_scenes": shared,
        "duplicate_patches": dup,
        "scene_disjoint": all(len(v) == 0 for v in shared.values()),
    }


def overlap_text(report: dict) -> str:
    """Human-readable summary of scene_overlap()."""
    lines = ["patches: " + ", ".join(f"{k} {v}" for k, v in report["n_patches"].items()),
             "scenes:  " + ", ".join(f"{k} {v}" for k, v in report["n_scenes"].items())]
    for k, v in report["shared_scenes"].items():
        lines.append(f"shared scenes {k}: {len(v)}; duplicate patches: {len(report['duplicate_patches'][k])}")
    lines.append(f"scene-disjoint: {report['scene_disjoint']}")
    return "\n".join(lines)
