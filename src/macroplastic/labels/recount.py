"""Recount source labels from the local data files (not from papers / READMEs) for data/labels_map.csv.

    python -m macroplastic.labels.recount            # print counts per (dataset, source_label)
    python -m macroplastic.labels.recount --check    # compare with the n column of data/labels_map.csv

Every counter returns {source_label: {"n": int, "detail": str}} or None when the files are not on this machine
(data/extra is outside git). Units: boxes for detection sets, objects for ADIS, pixels for MARIDA/MADOS.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EXTRA = ROOT / "data" / "extra"
CDS = EXTRA / "count_ds"


def _fmt(parts: dict) -> str:
    return " / ".join(f"{k} {v}" for k, v in parts.items())


def _merge(per_split: dict) -> dict:
    """{split: Counter} -> {label: {"n", "detail"}}"""
    labels = sorted({lab for c in per_split.values() for lab in c})
    out = {}
    for lab in labels:
        parts = {sp: int(c.get(lab, 0)) for sp, c in per_split.items()}
        out[lab] = {"n": sum(parts.values()), "detail": _fmt(parts)}
    return out


def winans():
    base = CDS / "winans2023_hawaii_aerial" / "imagery_and_labels"
    if not (base / "training_data.csv").exists():
        return None
    import pandas as pd
    per = {}
    for sp, f in (("train", "training_data.csv"), ("eval", "evaluation_data.csv")):
        per[sp] = collections.Counter(pd.read_csv(base / f)["label"])
    return _merge(per)


def tocl_rms():
    base = CDS / "tocl_rms" / "Supplementary materials C - label dataset"
    per = {}
    for sp in ("train", "val"):
        p = base / sp / "annotations" / f"{sp}_annotations.json"
        if not p.exists():
            return None
        d = json.loads(p.read_text(encoding="utf-8"))
        cat = {c["id"]: c["name"] for c in d["categories"]}
        per[sp] = collections.Counter(cat[a["category_id"]] for a in d["annotations"])
    return _merge(per)


def ucwd():
    base = CDS / "ucwd" / "UCWD.coco"
    per = {}
    for sp in ("train", "valid", "test"):
        p = base / sp / "_annotations.coco.json"
        if not p.exists():
            return None
        d = json.loads(p.read_text(encoding="utf-8"))
        cat = {c["id"]: c["name"] for c in d["categories"]}
        per[sp] = collections.Counter(cat[a["category_id"]] for a in d["annotations"])
    return _merge(per)


def _yolo_dir_counts(dirs: dict, names: dict) -> dict | None:
    per = {}
    for sp, d in dirs.items():
        if not d.exists():
            return None
        c = collections.Counter()
        for f in d.glob("*.txt"):
            for line in f.read_text(errors="replace").splitlines():
                parts = line.split()
                if len(parts) == 5:
                    c[names.get(int(float(parts[0])), parts[0])] += 1
        per[sp] = c
    return _merge(per)


def fml():
    base = EXTRA / "fml" / "fml_version2" / "full_dataset" / "labels" / "yolo_format"
    return _yolo_dir_counts({sp: base / sp for sp in ("train", "val", "test")}, {0: "garbage"})


def maharjan():
    base = CDS / "maharjan_river_uav" / "Nisha-main" / "Datagithub"
    return _yolo_dir_counts({"Laos": base / "train_data_v5_Laos" / "labels",
                             "Thailand": base / "train_data_v5_talathai" / "labels"}, {0: "0"})


def tud_gv():
    base = CDS / "tud_gv_od"
    if not (base / "classes.txt").exists():
        return None
    names = {i: n.strip() for i, n in enumerate((base / "classes.txt").read_text().splitlines()) if n.strip()}
    return _yolo_dir_counts({"all": base / "labels_txt"}, names)


ADIS_CLASSES = {0: "animal", 1: "buoy", 2: "fibrous", 3: "hard plastic", 4: "plant"}


def adis():
    p = EXTRA / "field" / "adis" / "Objects.csv"
    if not p.exists():
        return None
    import pandas as pd
    o = pd.read_csv(p, usecols=["class", "SegmentID"])
    c = collections.Counter(ADIS_CLASSES.get(int(k), str(k)) for k in o["class"])
    return {lab: {"n": int(n), "detail": "Objects.csv (все объекты)"} for lab, n in sorted(c.items())}


def adis_segment_share():
    """Share of ADIS segment counts (n_objects>5cm) that are model classes animal/plant: the segment count equals
    the number of ALL objects of the segment in 98.5 % of segments (checked 26.09, L123)."""
    po, ps = EXTRA / "field" / "adis" / "Objects.csv", EXTRA / "field" / "adis" / "Segments.csv"
    if not (po.exists() and ps.exists()):
        return None
    import pandas as pd
    o = pd.read_csv(po, usecols=["class", "SegmentID"])
    s = pd.read_csv(ps, usecols=["SegmentID", "n_objects>5cm", "n_objects>10cm"]).set_index("SegmentID")
    alln = o.groupby("SegmentID").size().reindex(s.index).fillna(0)
    plast = o[o["class"].isin([1, 2, 3])].groupby("SegmentID").size().reindex(s.index).fillna(0)
    in_seg = o[o["SegmentID"].isin(s.index)]
    nat = int(in_seg["class"].isin([0, 4]).sum())
    return {"segments": int(len(s)), "objects_in_segments": int(len(in_seg)),
            "segment_count_equals_all_objects": float((alln == s["n_objects>5cm"]).mean()),
            "segment_count_equals_plastic_classes_only": float((plast == s["n_objects>5cm"]).mean()),
            "animal_plant_in_segments": nat, "animal_plant_share": nat / max(len(in_seg), 1),
            "sum_n_objects_gt5cm": int(s["n_objects>5cm"].sum()), "sum_n_objects_gt10cm": int(s["n_objects>10cm"].sum())}


def _raster_counts(paths, names: dict) -> collections.Counter:
    import numpy as np
    import rasterio
    tot = np.zeros(256, np.int64)
    for p in paths:
        with rasterio.open(p) as ds:
            a = ds.read(1)
        tot += np.bincount(a.ravel().astype(np.int64), minlength=256)[:256]
    return collections.Counter({names.get(i, str(i)): int(v) for i, v in enumerate(tot) if v and i in names})


def marida():
    base = ROOT / "data" / "MARIDA" / "patches"
    if not base.exists():
        return None
    from macroplastic.data.marida import CLASS_NAMES
    c = _raster_counts(sorted(base.glob("*/*_cl.tif")), {k: v for k, v in CLASS_NAMES.items() if k})
    return {lab: {"n": n, "detail": "пиксели 10 м, все патчи"} for lab, n in sorted(c.items())}


def mados():
    base = ROOT / "data" / "MADOS"
    if not base.exists():
        return None
    from macroplastic.data.mados import MADOS_CLASSES
    try:
        from macroplastic.data.mados import resolve_root
        r = resolve_root(base)
    except FileNotFoundError:
        return None
    c = _raster_counts(sorted(r.glob("Scene_*/10/*_L2R_cl_*.tif")), dict(MADOS_CLASSES))
    return {lab: {"n": n, "detail": "пиксели 10 м, все кропы"} for lab, n in sorted(c.items())}


COUNTERS = {"Winans2023": winans, "TOCL_RMS": tocl_rms, "UCWD": ucwd, "FML": fml, "Maharjan2022": maharjan,
            "TUD-GV": tud_gv, "ADIS": adis, "MARIDA": marida, "MADOS": mados}


def recount(datasets=None) -> dict:
    out = {}
    for name, fn in COUNTERS.items():
        if datasets and name not in datasets:
            continue
        out[name] = fn()
    return out


def check(csv_path=None, datasets=None) -> list[str]:
    """Differences between the n column of labels_map.csv and a recount from local files (missing data skipped)."""
    from .schema import load_map
    rows = load_map(csv_path)
    rc = recount(datasets)
    errs = []
    for r in rows:
        got = rc.get(r["dataset"])
        if got is None or r["n"] in ("", None):
            continue
        g = got.get(r["source_label"])
        if g is None:
            errs.append(f"{r['dataset']}: метки «{r['source_label']}» нет в файлах")
        elif int(r["n"]) != g["n"]:
            errs.append(f"{r['dataset']}: «{r['source_label']}» в CSV {r['n']}, в файлах {g['n']}")
    for ds, got in rc.items():
        if not got:
            continue
        have = {r["source_label"] for r in rows if r["dataset"] == ds}
        for lab in got:
            if lab not in have:
                errs.append(f"{ds}: метка «{lab}» есть в файлах, но не сопоставлена в CSV")
    return errs


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--datasets", nargs="*")
    a = ap.parse_args(argv)
    if a.check:
        errs = check(datasets=a.datasets)
        print("\n".join(errs) if errs else "labels_map.csv: все числа совпадают с файлами (где файлы есть)")
        return 1 if errs else 0
    rc = recount(a.datasets)
    for ds, got in rc.items():
        if got is None:
            print(f"{ds}: файлов нет на этой машине")
            continue
        for lab, v in got.items():
            print(f"{ds} | {lab} | {v['n']} | {v['detail']}")
    if not a.datasets or "ADIS" in a.datasets:
        print("ADIS сегменты:", json.dumps(adis_segment_share(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
