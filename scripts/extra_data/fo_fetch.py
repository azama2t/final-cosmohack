"""FloatingObjects (Mifdal et al. 2021) + RefinedFloatingObjects (Russwurm et al. 2023): pixels for the labels.

The original scenes are only in an 18.9 GB zip (deflate, no random access). Instead, for every labelled region we
read the SAME Sentinel-2 acquisition (tile + date) as harmonized L2A from Earth Search COGs with the project's own
reader (src/macroplastic/live/stac.py, same scale/offset rules as the live scenes) - only the 10.24 km COG blocks
that contain labels, chosen greedily under a download budget.

  .venv/Scripts/python.exe scripts/extra_data/fo_fetch.py plan              # resolve items, cells, scores -> plan.csv
  .venv/Scripts/python.exe scripts/extra_data/fo_fetch.py fetch --budget-mb 1400

Outputs (data/extra/floatingobjects/, not in git):
  items.json      region -> STAC item id, tile, date, epsg, block grid origin
  cells.csv       every labelled 10.24 km cell: region, cell, n lines/points, line km, score, est. MB, selected
  crops/<region>/<cell>.tif   uint16 DN*1e4 reflectance (0 = nodata), 12 bands + SCL as band 13
  fetch_ledger.csv            what was read, estimated MB (block-size estimate from asset Content-Length)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
# 01:30 a plan run hung >10 min on one region (no HTTP timeout in GDAL/pystac by default)
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "60")
os.environ.setdefault("GDAL_HTTP_CONNECTTIMEOUT", "20")
from macroplastic.live import stac  # noqa: E402

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402

FO = ROOT / "data" / "extra" / "floatingobjects"
CELL = 10240.0  # one 1024 px COG block of a 10 m band
# DUPLICATE / LEAK: same acquisition as MARIDA S2_24-4-19_36JUN and our live scene data/live/durban/2019-04-24
SKIP_FETCH = {"durban_20190424"}


def label_sets():
    """(region, kind, path): kind 'lines' = original FloatingObjects, 'points' = refined points (type 1/0)."""
    out = []
    for p in sorted((FO / "shapefiles").glob("*.shp")):
        out.append((p.stem, "lines", p))
    for p in sorted((FO / "refined").glob("*.shp")):
        if "qualitative" in p.stem:
            continue
        out.append((p.stem, "points", p))
    return out


def read_labels(path: Path) -> gpd.GeoDataFrame:
    g = gpd.read_file(path)
    g = g[g.geometry.notna() & ~g.geometry.is_empty].copy()
    if g.crs is None:
        g = g.set_crs(4326)
    return g


def tile_hint(g) -> str | None:
    if "image" in g.columns:
        v = str(g["image"].dropna().iloc[0]) if g["image"].notna().any() else ""
        if "_T" in v:
            return v.rsplit("_T", 1)[1][:5]
    return None


def retry(fn, *args, tries=6, limit_s=240, **kw):
    """Retry + watchdog: a call that hangs longer than limit_s is abandoned (daemon thread) and retried.
    (01:30-01:50 two plan runs hung >10 min on one region with an idle CPU - stuck HTTP read.)"""
    import threading
    for k in range(tries):
        box_ = {}

        def run():
            try:
                box_["v"] = fn(*args, **kw)
            except Exception as e:  # noqa: BLE001
                box_["e"] = e
        th = threading.Thread(target=run, daemon=True)
        th.start()
        th.join(limit_s)
        if "v" in box_:
            return box_["v"]
        err = box_.get("e", TimeoutError(f"no answer in {limit_s}s"))
        if k == tries - 1:
            raise err
        print(f"  retry {k + 1}: {type(err).__name__}: {str(err)[:120]}", flush=True)
        time.sleep(3 * (k + 1))


_CLIENT = None


def client():
    global _CLIENT
    if _CLIENT is None:
        from pystac_client import Client
        _CLIENT = Client.open(stac.EARTH_SEARCH, timeout=60)
    return _CLIENT


def resolve_item(region: str, g):
    from shapely.geometry import shape
    date = region.rsplit("_", 1)[1]
    date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    pts = g.to_crs(4326).geometry.representative_point()
    c = pts.unary_union.centroid if hasattr(pts, "unary_union") else pts.union_all().centroid
    its = (lambda: list(client().search(collections=["sentinel-2-l2a"],
                                             intersects=dict(type="Point", coordinates=[c.x, c.y]),
                                             datetime=f"{date}/{date}").items()))()
    hint = tile_hint(g)
    best = None
    for it in its:
        fp = shape(it.geometry)
        cover = float(np.mean([fp.contains(p) for p in pts]))
        orig = str(it.properties.get("s2:processing_baseline", "99")) < "04"
        key = (stac.tile_of(it) == hint if hint else True, cover, orig, -len(it.id))
        if best is None or key > best[0]:
            best = (key, it)
    if best is None:
        return None, date, hint
    return best[1], date, hint


def block_grid(item):
    href = item.assets[stac.ES_ASSET["B4"]].href
    with rasterio.open(href) as s:
        t = s.transform
        return dict(x0=t.c, y0=t.f, W=s.width, H=s.height, epsg=s.crs.to_epsg())


def asset_block_mb(item) -> dict:
    """Average compressed MB per internal block, per band asset (Content-Length / number of blocks). Parallel."""
    import requests
    from concurrent.futures import ThreadPoolExecutor

    def one(b):
        href = item.assets[stac.ES_ASSET[b]].href
        try:
            n = int(requests.head(href, timeout=30).headers.get("Content-Length", 0))
            with rasterio.open(href) as s:
                bh, bw = s.block_shapes[0]
                nb = math.ceil(s.height / bh) * math.ceil(s.width / bw)
                px_m = abs(s.transform.a)
            return b, dict(mb=n / 1e6 / nb, block_m=bw * px_m)
        except Exception as e:  # noqa: BLE001
            return b, dict(mb=1.5, block_m=10240.0, err=str(e))

    with ThreadPoolExecutor(13) as ex:
        return dict(ex.map(one, stac.BANDS + ["SCL"]))


def run_children(cmds: list[list[str]], timeout_s: int, parallel: int, rounds: int, done_fn) -> None:
    """Run child processes (hard timeout -> kill), up to `parallel` at once; re-run failures `rounds` times.
    Needed because a stuck GDAL/curl read can hold the GIL (01:30-02:00: 3 runs hung, even a watchdog thread froze)."""
    import subprocess
    todo = [c for c in cmds if not done_fn(c)]
    for rnd in range(rounds):
        if not todo:
            return
        running = []
        queue = list(todo)
        while queue or running:
            while queue and len(running) < parallel:
                c = queue.pop(0)
                running.append((c, subprocess.Popen([sys.executable, "-u", __file__] + c), time.time()))
            time.sleep(1)
            for item in list(running):
                c, pr, t0 = item
                if pr.poll() is not None:
                    running.remove(item)
                elif time.time() - t0 > timeout_s:
                    pr.kill()
                    running.remove(item)
                    print(f"  KILLED after {timeout_s}s: {' '.join(c)}", flush=True)
        todo = [c for c in todo if not done_fn(c)]
        if todo:
            print(f"round {rnd + 1}: {len(todo)} failed, retry", flush=True)
    if todo:
        print(f"GAVE UP on {len(todo)}: {todo}", flush=True)


def cmd_resolve(a):
    path = Path(a.path)
    g = read_labels(path)
    t0 = time.time()
    # no watchdog threads here: the parent's process timeout is the watchdog (nested threads + GDAL deadlocked
    # deterministically for tunisia/tangshan, while the same calls finish in 9 s in a plain process)
    it, date, hint = resolve_item(a.region, g)
    if it is None:
        print(f"{a.region}: NO ITEM on {date}", flush=True)
        return
    grid = block_grid(it)
    bm = asset_block_mb(it)
    rec = dict(item_id=it.id, tile=stac.tile_of(it), tile_hint=hint, date=date,
               baseline=it.properties.get("s2:processing_baseline"), epsg=grid["epsg"], grid=grid,
               block_mb=bm, kind=a.kind, n_labels=len(g), self_href=it.get_self_href())
    (FO / "items_parts").mkdir(parents=True, exist_ok=True)
    (FO / "items_parts" / f"{a.region}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    print(f"{a.region:32s} {a.kind:6s} {it.id} tile={stac.tile_of(it)} hint={hint} labels={len(g)} "
          f"{time.time() - t0:.0f}s", flush=True)


def cmd_plan(a):
    cache_p = FO / "items.json"
    items = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.is_file() else {}
    sets = label_sets()
    cmds = [["resolve", r, k, str(p)] for r, k, p in sets if r not in items]
    run_children(cmds, timeout_s=90, parallel=1, rounds=8,
                 done_fn=lambda c: (FO / "items_parts" / f"{c[1]}.json").is_file())
    for r, k, p in sets:
        f = FO / "items_parts" / f"{r}.json"
        if r not in items and f.is_file():
            items[r] = json.loads(f.read_text(encoding="utf-8"))
    cache_p.write_text(json.dumps(items, indent=1), encoding="utf-8")
    rows = []
    for region, kind, path in sets:
        if region not in items:
            print(f"{region}: no item -> no cells", flush=True)
            continue
        g = read_labels(path)
        grid = items[region]["grid"]
        u = g.to_crs(grid["epsg"])
        if kind == "lines":
            # length of each line inside each cell (sample every 10 m)
            acc = defaultdict(lambda: [0, 0.0])
            for geom in u.geometry:
                segs = getattr(geom, "geoms", [geom])
                for s in segs:
                    L = s.length
                    n = max(2, int(L / 10) + 1)
                    ds = np.linspace(0, L, n)
                    xy = np.array([s.interpolate(d).coords[0][:2] for d in ds])
                    cx = np.floor((xy[:, 0] - grid["x0"]) / CELL).astype(int)
                    cy = np.floor((grid["y0"] - xy[:, 1]) / CELL).astype(int)
                    for key in set(zip(cx, cy)):
                        acc[key][0] += 1
                    for key, cnt in zip(*np.unique(np.stack([cx, cy], 1), axis=0, return_counts=True)):
                        acc[tuple(key)][1] += cnt * 10 / 1000
            for (cx, cy), (nl, km) in acc.items():
                rows.append(dict(region=region, kind=kind, cell=f"c{cx:02d}_r{cy:02d}", cx=cx, cy=cy, n_lines=nl,
                                 line_km=round(km, 2), n_pos_pts=0, n_neg_pts=0))
        else:
            p = u.geometry.representative_point()
            cx = np.floor((p.x.values - grid["x0"]) / CELL).astype(int)
            cy = np.floor((grid["y0"] - p.y.values) / CELL).astype(int)
            t = u["type"].values.astype(int)
            df = pd.DataFrame(dict(cx=cx, cy=cy, t=t))
            for (x, y), d in df.groupby(["cx", "cy"]):
                rows.append(dict(region=region, kind=kind, cell=f"c{x:02d}_r{y:02d}", cx=x, cy=y, n_lines=0, line_km=0.0,
                                 n_pos_pts=int((d.t == 1).sum()), n_neg_pts=int((d.t == 0).sum())))
    cells = pd.DataFrame(rows)
    # merge line and point cells of the same region+cell (e.g. lagos_20190101, venice_20180630 have both)
    cells = (cells.groupby(["region", "cell", "cx", "cy"], as_index=False)
             .agg(kind=("kind", lambda s: "+".join(sorted(set(s)))), n_lines=("n_lines", "sum"),
                  line_km=("line_km", "sum"), n_pos_pts=("n_pos_pts", "sum"), n_neg_pts=("n_neg_pts", "sum")))
    # score: positive pixels ~ line length (100 px per km) + verified points (pos weigh 3, neg 1)
    cells["score"] = cells.line_km * 100 * 0.2 + cells.n_pos_pts * 3 + cells.n_neg_pts * 1.0
    FO.mkdir(parents=True, exist_ok=True)
    cells.to_csv(FO / "cells.csv", index=False)
    print(f"{len(items)} regions, {len(cells)} labelled cells -> {FO / 'cells.csv'}")


def est_cost(bm: dict, cells: list[tuple[int, int]]) -> float:
    """MB to read the union of cells: count distinct blocks per band."""
    mb = 0.0
    for b, d in bm.items():
        k = d["block_m"] / CELL
        blocks = {(int(cx // k), int(cy // k)) for cx, cy in cells}
        mb += len(blocks) * d["mb"]
    return mb


def select(cells: pd.DataFrame, items: dict, budget_mb: float, done: set) -> pd.DataFrame:
    """Greedy: best cell of every region first (diversity), then best score per marginal MB."""
    cells = cells[cells.region.isin(items) & ~cells.region.isin(SKIP_FETCH)].copy()
    chosen = defaultdict(list)
    spent = 0.0
    for (r, c) in done:
        chosen[r].append(c)
    for r in chosen:
        spent += est_cost(items[r]["block_mb"], chosen[r])
    order = cells.sort_values("score", ascending=False)
    # pass 1: one per region
    for r, d in order.groupby("region", sort=False):
        top = d.iloc[0]
        key = (int(top.cx), int(top.cy))
        if key in chosen[r]:
            continue
        add = est_cost(items[r]["block_mb"], chosen[r] + [key]) - est_cost(items[r]["block_mb"], chosen[r])
        if spent + add <= budget_mb:
            chosen[r].append(key)
            spent += add
    # pass 2: marginal gain
    while True:
        best = None
        for _, row in cells.iterrows():
            key = (int(row.cx), int(row.cy))
            if key in chosen[row.region]:
                continue
            add = est_cost(items[row.region]["block_mb"], chosen[row.region] + [key]) - \
                est_cost(items[row.region]["block_mb"], chosen[row.region])
            val = row.score / max(add, 0.5)
            if spent + add <= budget_mb and (best is None or val > best[0]):
                best = (val, row.region, key, add)
        if best is None:
            break
        _, r, key, add = best
        chosen[r].append(key)
        spent += add
    cells["selected"] = [(int(cx), int(cy)) in chosen[r] for r, cx, cy in zip(cells.region, cells.cx, cells.cy)]
    print(f"selected {int(cells.selected.sum())} cells in {sum(1 for r in chosen if chosen[r])} regions, "
          f"estimated {spent:.0f} MB")
    return cells


def write_crop(path: Path, crop: dict):
    b = crop["bands"]
    dn = np.where(np.isfinite(b), np.clip(np.round(b * 10000.0), 1, 65535), 0).astype(np.uint16)
    arr = np.concatenate([dn, crop["scl"][None].astype(np.uint16)], 0)
    prof = dict(driver="GTiff", width=arr.shape[2], height=arr.shape[1], count=arr.shape[0], dtype="uint16",
                crs=crop["crs"], transform=crop["transform"], nodata=0, compress="deflate", predictor=2,
                tiled=True, blockxsize=256, blockysize=256)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part.tif")
    with rasterio.open(tmp, "w", **prof) as dst:
        dst.write(arr)
        for i, n in enumerate(stac.BANDS + ["SCL"], 1):
            dst.set_band_description(i, n)
        dst.update_tags(scale="reflectance = DN/10000 (0 = nodata); band 13 = Sen2Cor SCL",
                        scale_offset_note=crop["scale_offset_note"][:900])
    tmp.replace(path)


def cmd_fetch(a):
    items = json.loads((FO / "items.json").read_text(encoding="utf-8"))
    cells = pd.read_csv(FO / "cells.csv")
    ledger_p = FO / "fetch_ledger.csv"
    ledger = pd.read_csv(ledger_p) if ledger_p.is_file() else pd.DataFrame(
        columns=["region", "cells", "file", "est_mb", "read_s", "time"])
    done = set()
    for _, l in ledger.iterrows():
        for c in str(l.cells).split(";"):
            cx, cy = c[1:].split("_r")
            done.add((l.region, (int(cx), int(cy))))
    spent_before = float(ledger.est_mb.sum()) if len(ledger) else 0.0
    cells = select(cells, items, a.budget_mb, done)
    cells.to_csv(FO / "cells.csv", index=False)
    cmds = []
    for r, d in cells[cells.selected].groupby("region"):
        groups = defaultdict(list)
        for cx, cy in zip(d.cx.astype(int), d.cy.astype(int)):
            if (r, (cx, cy)) in done:
                continue
            groups[(cx // 2, cy // 2)].append((cx, cy))
        for gk, cc in sorted(groups.items()):
            cmds.append(["fetch-group", r] + [f"c{x:02d}_r{y:02d}" for x, y in sorted(cc)])
    print(f"{len(cmds)} reads to do", flush=True)
    parts = FO / "ledger_parts"

    def done_fn(c):
        return (parts / f"{c[1]}__{'_'.join(c[2:])}.json").is_file()

    run_children(cmds, timeout_s=900, parallel=3, rounds=3, done_fn=done_fn)
    for f in sorted(parts.glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        if not ((ledger.region == rec["region"]) & (ledger.cells == rec["cells"])).any():
            ledger.loc[len(ledger)] = [rec[k] for k in ["region", "cells", "file", "est_mb", "read_s", "time"]]
    ledger.to_csv(ledger_p, index=False)
    print(f"ledger: {len(ledger)} reads, est {ledger.est_mb.sum():.0f} MB", flush=True)


def cmd_fetch_group(a):
    items = json.loads((FO / "items.json").read_text(encoding="utf-8"))
    r = a.region
    meta = items[r]
    cc = []
    for c in a.cells:
        cx, cy = c[1:].split("_r")
        cc.append((int(cx), int(cy)))
    it = client().get_collection("sentinel-2-l2a").get_item(meta["item_id"])
    grid, epsg, bm = meta["grid"], meta["epsg"], meta["block_mb"]
    xs = [c[0] for c in cc]
    ys = [c[1] for c in cc]
    x0 = grid["x0"] + min(xs) * CELL
    x1 = min(grid["x0"] + (max(xs) + 1) * CELL, grid["x0"] + grid["W"] * 10)
    y1 = grid["y0"] - min(ys) * CELL
    y0 = max(grid["y0"] - (max(ys) + 1) * CELL, grid["y0"] - grid["H"] * 10)
    est = est_cost(bm, cc)
    t0 = time.time()
    crop = stac.read_crop(it, epsg, [x0, y0, x1, y1])
    name = "_".join(a.cells)
    out = FO / "crops" / r / f"{name}.tif"
    write_crop(out, crop)
    rec = dict(region=r, cells=";".join(a.cells), file=str(out.relative_to(ROOT)).replace("\\", "/"),
               est_mb=round(est, 1), read_s=round(time.time() - t0, 1), time=time.strftime("%H:%M:%S"))
    (FO / "ledger_parts").mkdir(parents=True, exist_ok=True)
    (FO / "ledger_parts" / f"{r}__{name}.json").write_text(json.dumps(rec), encoding="utf-8")
    print(f"{r} {name}: {crop['width']}x{crop['height']} est {est:.0f} MB, {time.time() - t0:.0f}s", flush=True)


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("plan")
    f = sp.add_parser("fetch")
    f.add_argument("--budget-mb", type=float, default=1400)
    r = sp.add_parser("resolve")
    r.add_argument("region")
    r.add_argument("kind")
    r.add_argument("path")
    g = sp.add_parser("fetch-group")
    g.add_argument("region")
    g.add_argument("cells", nargs="+")
    a = ap.parse_args()
    {"plan": cmd_plan, "fetch": cmd_fetch, "resolve": cmd_resolve, "fetch-group": cmd_fetch_group}[a.cmd](a)
    if a.cmd in ("resolve", "fetch-group"):
        # children finished their work (file written) but sometimes hung at interpreter exit (GDAL/curl threads):
        # leave immediately
        sys.stdout.flush()
        os._exit(0)


if __name__ == "__main__":
    main()
