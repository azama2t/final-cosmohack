"""Check scene overlap MARIDA <-> MADOS (or any second dataset) by Sentinel-2 tile + acquisition date.

Usage (repo root):
    .venv\\Scripts\\python.exe scripts\\check_mados_overlap.py --marida data\\MARIDA --other data\\MADOS
    .venv\\Scripts\\python.exe scripts\\check_mados_overlap.py --marida data\\MARIDA --other data\\MARIDA   # self-check
Output: reports/mados_overlap.csv (+ printed summary). --out to change.

Layouts understood for --other (auto-detected):
  * MARIDA:  <root>/patches/S2_<d-m-yy>_<TILE>/..._<i>.tif, splits/{train,val,test}_X.txt
  * MADOS (Zenodo 10664073, README github.com/gkakogeorgiou/mados):
        <root>/Scene_<k>/{10,20,60}/<ACOLITE prefix>_L2R_rhorc_<wl>_<crop>.tif, ..._L2R_cl_<crop>.tif, ..._conf_...
        <root>/splits/{train,val,test}_X.txt
    Scene folders are anonymous (Scene_0..Scene_173). Tile/date are taken from the ACOLITE file prefix
    (e.g. 'S2A_MSI_2020_09_23_16_07_29_T16PCC_L2R_...'), else from GeoTIFF tags, else reported as unknown.
    MADOS patches are stored non-georeferenced (stack_patches.py writes '+proj=latlong'), so spatial
    footprint matching is NOT attempted; tile+date is the key.
Rule: any MADOS scene with the same tile AND date as a MARIDA scene is the same S2 acquisition ->
keep it in the same split as the MARIDA scene (and never let it into a split different from MARIDA's).
Same tile, different date is listed as 'same_tile_other_date' (spatial overlap only, no leakage of pixels).
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]

RE_MARIDA = re.compile(r"S2_(\d{1,2})-(\d{1,2})-(\d{2})_(\d{2}[A-Z]{3})")
RE_ACOLITE = re.compile(r"S2[ABC]?_MSI_(\d{4})_(\d{2})_(\d{2})_\d{2}_\d{2}_\d{2}_T?(\d{2}[A-Z]{3})")
RE_SAFE = re.compile(r"S2[ABC]_MSI\w*?_(\d{4})(\d{2})(\d{2})T\d{6}_.*?_T(\d{2}[A-Z]{3})")
RE_GENERIC_DATE = re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})")
RE_GENERIC_TILE = re.compile(r"(?<![0-9A-Z])T?(\d{2}[C-X][A-Z]{2})(?![A-Z])")


def _find_root(p: Path, marker: str) -> Path:
    if any(p.glob(marker)):
        return p
    for sub in p.iterdir() if p.is_dir() else []:
        if sub.is_dir() and any(sub.glob(marker)):
            return sub
    return p


def read_splits(root: Path) -> dict[str, str]:
    out = {}
    for s in ("train", "val", "test"):
        f = root / "splits" / f"{s}_X.txt"
        if f.is_file():
            for ln in f.read_text().splitlines():
                if ln.strip():
                    out[ln.strip()] = s
    return out


def scan_marida(root: Path, label: str) -> pd.DataFrame:
    root = _find_root(root, "patches")
    splits = read_splits(root)
    rows = []
    for d in sorted((root / "patches").iterdir()):
        m = RE_MARIDA.match(d.name)
        if not d.is_dir() or not m:
            continue
        dd, mm, yy, tile = m.groups()
        date = dt.date(2000 + int(yy), int(mm), int(dd)).isoformat()
        sp = defaultdict(int)
        n = 0
        for f in d.glob("S2_*.tif"):
            if f.stem.endswith(("_cl", "_conf")):
                continue
            n += 1
            sp[splits.get(f.stem[3:], "none")] += 1
        rows.append({"dataset": label, "scene": d.name, "tile": tile, "date": date, "n_patches": n,
                     "splits": ";".join(f"{k}:{v}" for k, v in sorted(sp.items())), "source": "folder name"})
    return pd.DataFrame(rows)


def _tile_date_from_tags(f: Path):
    try:
        import rasterio
        with rasterio.open(f) as ds:
            tags = {**ds.tags(), **{k: v for k, v in ds.tags(ns="IMAGE_STRUCTURE").items()}}
        blob = " ".join(f"{k}={v}" for k, v in tags.items())
    except Exception:
        return None
    for rx in (RE_ACOLITE, RE_SAFE):
        m = rx.search(blob)
        if m:
            y, mo, d, tile = m.groups()
            return tile, f"{y}-{mo}-{d}", "tif tags"
    md, mt = RE_GENERIC_DATE.search(blob), RE_GENERIC_TILE.search(blob)
    if md and mt:
        return mt.group(1), f"{md.group(1)}-{md.group(2)}-{md.group(3)}", "tif tags (generic)"
    return None


def scan_mados(root: Path, label: str) -> pd.DataFrame:
    root = _find_root(root, "Scene_*")
    splits = read_splits(root)
    rows = []
    for d in sorted(root.glob("Scene_*"), key=lambda p: int(re.sub(r"\D", "", p.name) or 0)):
        if not d.is_dir():
            continue
        files = sorted(d.rglob("*.tif"))
        tile = date = None
        src = ""
        for f in files:
            m = RE_ACOLITE.search(f.name) or RE_SAFE.search(f.name)
            if m:
                y, mo, dd, tile = m.groups()
                date, src = f"{y}-{mo}-{dd}", "file name"
                break
        if tile is None and files:
            r = _tile_date_from_tags(files[0])
            if r:
                tile, date, src = r
        crops = {re.split(r"_cl_", f.stem)[-1] for f in files if "_cl_" in f.name}
        sp = defaultdict(int)
        for c in crops:
            sp[splits.get(f"{d.name}_{c}", "none")] += 1
        rows.append({"dataset": label, "scene": d.name, "tile": tile or "", "date": date or "",
                     "n_patches": len(crops), "splits": ";".join(f"{k}:{v}" for k, v in sorted(sp.items())),
                     "source": src or "UNKNOWN (no tile/date in names or tags)"})
    return pd.DataFrame(rows)


def scan(root: Path, label: str) -> pd.DataFrame:
    if any(_find_root(root, "patches").glob("patches")):
        return scan_marida(root, label)
    if any(_find_root(root, "Scene_*").glob("Scene_*")):
        return scan_mados(root, label)
    raise SystemExit(f"unknown layout: {root} (no 'patches/' nor 'Scene_*')")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--marida", default=str(REPO / "data" / "MARIDA"))
    ap.add_argument("--other", default=str(REPO / "data" / "MADOS"))
    ap.add_argument("--out", default=str(REPO / "reports" / "mados_overlap.csv"))
    a = ap.parse_args()
    other = Path(a.other)
    if not other.exists():
        sys.exit(f"--other folder not found: {other} (MADOS not downloaded yet?)")
    A = scan(Path(a.marida), "MARIDA")
    B = scan(other, "OTHER")
    self_check = Path(a.marida).resolve() == other.resolve()
    unknown = B[B["tile"] == ""]
    rows = []
    for _, b in B[B["tile"] != ""].iterrows():
        same_tile = A[A["tile"] == b["tile"]]
        for _, r in same_tile.iterrows():
            if self_check and r["scene"] == b["scene"]:
                kind = "identical (self-check)"
            elif r["date"] == b["date"]:
                kind = "same_tile_same_date"
            else:
                kind = "same_tile_other_date"
            if kind == "same_tile_other_date":
                ddays = abs((dt.date.fromisoformat(r["date"]) - dt.date.fromisoformat(b["date"])).days)
            else:
                ddays = 0
            rec = ("KEEP IN SAME SPLIT as MARIDA scene (same S2 acquisition)" if kind != "same_tile_other_date"
                   else "no pixel leakage; spatial overlap only (optional: same split if |days|<=5)")
            rows.append({"other_scene": b["scene"], "marida_scene": r["scene"], "tile": b["tile"],
                         "other_date": b["date"], "marida_date": r["date"], "days_apart": ddays, "kind": kind,
                         "marida_splits": r["splits"], "other_splits": b["splits"],
                         "other_n_patches": b["n_patches"], "recommendation": rec})
    res = pd.DataFrame(rows, columns=["other_scene", "marida_scene", "tile", "other_date", "marida_date", "days_apart",
                                      "kind", "marida_splits", "other_splits", "other_n_patches", "recommendation"])
    res = res.sort_values(["kind", "tile", "other_date"]).reset_index(drop=True)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(out, index=False, encoding="utf-8")
    B.to_csv(out.with_name(out.stem + "_other_scenes.csv"), index=False, encoding="utf-8")

    same = res[res["kind"] != "same_tile_other_date"]
    print(f"MARIDA scenes: {len(A)}; other scenes: {len(B)} ({len(unknown)} without tile/date)")
    print(f"same tile+date pairs: {len(same)}; other scenes involved: {same['other_scene'].nunique()}")
    print(f"same tile, other date pairs: {(res['kind'] == 'same_tile_other_date').sum()}")
    if self_check:
        ok = (same["other_scene"] == same["marida_scene"]).all() and same["other_scene"].nunique() == len(A)
        print(f"SELF-CHECK {'OK' if ok else 'FAILED'}: every MARIDA scene matched itself")
        # MARIDA scenes whose patches span several official splits (info for L1 splits)
        multi = A[A["splits"].str.count(":") > 1]
        print(f"MARIDA scenes spread over >1 official split: {len(multi)} of {len(A)}")
    if len(unknown):
        print("WARNING: scenes without tile/date (inspect file names/tags manually):", ", ".join(unknown["scene"][:20]))
    print(f"-> {out}")


if __name__ == "__main__":
    main()
