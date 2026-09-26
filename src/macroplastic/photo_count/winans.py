"""Winans et al. 2023 aerial shoreline debris (Zenodo 8381113, CC BY 4.0): 640x640 px chips, GSD 0.02 m
(-> 12.8 x 12.8 m = 163.84 m^2 per chip), georeferenced (NAD83 / UTM 5N in *.aux.xml), 8 classes -> 1 "item".

The authors' split (training_data.csv / evaluation_data.csv) is NOT spatially independent: 174 of 420 evaluation chips
overlap a training chip on the ground, 104 overlap another evaluation chip. We therefore re-split by spatial components:
chips whose footprints come closer than BUFFER_M are joined (union-find); whole components go to train/val/test.
"""
import re
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "data" / "extra" / "count_ds" / "winans2023_hawaii_aerial" / "imagery_and_labels"
CHIPS = BASE / "processed_image_chips"
BUFFER_M = 25.0
CHIP_PX = 640


def geotransform(img):
    s = (Path(str(img) + ".aux.xml")).read_text()
    m = re.search(r"<GeoTransform>([^<]+)</GeoTransform>", s)
    return [float(x) for x in m.group(1).split(",")]


def footprint(g, w=CHIP_PX, h=CHIP_PX):
    x0, px, _, y0, _, py = g
    return (x0, y0 + h * py, x0 + w * px, y0)  # xmin, ymin, xmax, ymax (py < 0)


def load_all():
    """-> dict name -> {"path", "boxes" (N,4 px xyxy), "labels" [str], "author_split", "fp", "gsd"}"""
    import pandas as pd
    out = {}
    for split, csv in (("train", "training_data.csv"), ("eval", "evaluation_data.csv")):
        df = pd.read_csv(BASE / csv)
        for name, g in df.groupby("filename"):
            p = CHIPS / name
            if not p.exists():
                continue
            gt = geotransform(p)
            out[name] = {"path": p, "boxes": g[["xmin", "ymin", "xmax", "ymax"]].to_numpy(float),
                         "labels": list(g["label"]), "author_split": split, "fp": footprint(gt), "gsd": abs(gt[1])}
    return out


def components(chips, buffer_m=BUFFER_M):
    names = sorted(chips)
    parent = list(range(len(names)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    F = np.array([chips[n]["fp"] for n in names])
    for i in range(len(names)):
        b = F[i]
        near = np.where((F[:, 0] < b[2] + buffer_m) & (F[:, 2] > b[0] - buffer_m) &
                        (F[:, 1] < b[3] + buffer_m) & (F[:, 3] > b[1] - buffer_m))[0]
        for j in near:
            ri, rj = find(i), find(int(j))
            if ri != rj:
                parent[rj] = ri
    comp = {}
    for i, n in enumerate(names):
        comp.setdefault(find(i), []).append(n)
    return list(comp.values())


def spatial_split(chips, test_frac=0.25, val_frac=0.15, seed=0):
    """Whole spatial components -> test until >= test_frac of chips, then val, rest train (seeded order)."""
    comps = components(chips)
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(comps))
    n = len(chips)
    split = {}
    acc_t = acc_v = 0
    for k in order:
        c = comps[k]
        if acc_t < test_frac * n:
            s = "test"; acc_t += len(c)
        elif acc_v < val_frac * n:
            s = "val"; acc_v += len(c)
        else:
            s = "train"
        for name in c:
            split[name] = s
    return split, comps


def geo_boxes(chip, boxes):
    x0, _, _, y1 = chip["fp"][0], None, None, chip["fp"][3]
    g = chip["gsd"]
    b = np.asarray(boxes, float).reshape(-1, 4)
    return np.stack([x0 + b[:, 0] * g, y1 - b[:, 3] * g, x0 + b[:, 2] * g, y1 - b[:, 1] * g], 1) if len(b) else b


def dedup_count(chips_boxes, iou_thr=0.3):
    """chips_boxes: list of geo boxes arrays (one per chip). Greedy merge of boxes from overlapping chips (IoU >= thr)
    -> number of unique items on the ground."""
    from .metrics import iou_matrix
    kept = np.zeros((0, 4))
    for b in chips_boxes:
        if len(b) == 0:
            continue
        if len(kept):
            m = iou_matrix(b, kept).max(1) >= iou_thr
            b = b[~m]
        kept = np.concatenate([kept, b]) if len(b) else kept
    return len(kept)


def union_area(fps, res=0.25):
    """Area (m^2) of the union of chip footprints: overlapping chips are grouped (union-find) and each group is
    rasterised at res metres (far-apart groups on different islands are never rasterised together)."""
    F = np.asarray(fps, float)
    n = len(F)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(n):
        ov = np.where((F[:, 0] < F[i, 2]) & (F[:, 2] > F[i, 0]) & (F[:, 1] < F[i, 3]) & (F[:, 3] > F[i, 1]))[0]
        for j in ov:
            ri, rj = find(i), find(int(j))
            if ri != rj:
                parent[rj] = ri
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    total = 0.0
    for idx in groups.values():
        G = F[idx]
        if len(G) == 1:
            total += (G[0, 2] - G[0, 0]) * (G[0, 3] - G[0, 1])
            continue
        x0, y0 = G[:, 0].min(), G[:, 1].min()
        Wd = int(np.ceil((G[:, 2].max() - x0) / res)) + 1
        Hd = int(np.ceil((G[:, 3].max() - y0) / res)) + 1
        grid = np.zeros((Hd, Wd), bool)
        for f in G:
            grid[int(round((f[1] - y0) / res)):int(round((f[3] - y0) / res)),
                 int(round((f[0] - x0) / res)):int(round((f[2] - x0) / res))] = True
        total += float(grid.sum() * res * res)
    return total


def stable_bucket(name, k=5):
    return zlib.crc32(name.encode()) % k
