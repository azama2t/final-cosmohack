"""L133 (INBOX §35 А) — fixed evaluation sets for the discovery harness: composition + local S2 window cache.

Stages (run in this order, ONCE per set version; a new composition = a new version, old scores are not mixed):
  .venv/Scripts/python.exe scripts/discovery/build_sets.py compose   # -> data/discovery/<ver>/candidates.csv (+labels)
  .venv/Scripts/python.exe scripts/discovery/build_sets.py fetch     # S2 L2A windows -> data/discovery/<ver>/win/*.npz
  .venv/Scripts/python.exe scripts/discovery/build_sets.py freeze    # manifest.csv + sha256 -> configs/discovery_sets.yaml

Window contract (every sample of every set):
  128 x 128 px at 10 m (1.28 km), 11 bands B1..B8A,B11,B12 (B9 dropped; MARIDA has no B9), reflectance float32, NaN =
  nodata; SCL (uint8, 0 where unknown); roi (bool HxW) = the zone the method must count items in.
Answers (N, observed density) live ONLY in labels.csv, never in the window file / meta passed to a method.

Sources (see configs/discovery_sets.yaml for the rules written before the first run):
  A1 A*  : docs/research/pairs/p1_pixels.csv + p1_targets.csv (L127): PLP2018, PLP2019 x5, Maathuis 2026, Themistocleous
  A2 field: reports/search/adis_candidates.csv (level A, 66) + data/pairs/pair_quality.csv (accept, S2 only)
  A3 background: MARIDA test split (local patches, read in place), Finland vessels (boxes), CMC clouds (masks)
  A4 Cózar 2024 windrows (soft): data/extra/cozar2024 (filament pixels from the cached Zenodo NetCDF blocks)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
VERSION = "v1"
OUT = ROOT / "data" / "discovery" / VERSION
WIN = 128
BANDS11 = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
BANDS12 = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"]
MARIDA = ROOT / "data" / "MARIDA"
MARIDA_CLASSES = {2: "dense_sargassum", 3: "sparse_sargassum", 4: "natural_organic", 5: "ship", 6: "cloud",
                  7: "marine_water", 8: "sediment_water", 9: "foam", 10: "turbid_water", 11: "shallow_water",
                  12: "waves", 13: "cloud_shadow", 14: "wakes", 15: "mixed_water"}
MARIDA_CAP_PER_CLASS = 40
SEED = 133


# ----------------------------------------------------------------------------------------------------------- compose
def _roi_center(n=WIN, k=64):
    m = np.zeros((n, n), bool)
    a = (n - k) // 2
    m[a:a + k, a:a + k] = True
    return m


def compose_astar(rows, labels):
    p = pd.read_csv(ROOT / "docs" / "research" / "pairs" / "p1_pixels.csv")
    keep = {  # pair_id -> (N items in the ROI, basis)
        "P1-PLP2018-20180607": (3738.0, "3 600 PET 1.5 l bottles + 138 LDPE bags on 3 targets 10x10 m (nets not counted)"),
        "P1-Themistocleous2020-20181215": (1500.0, "1 500 bottles (0.5 and 1.5 l) on a 3x10 m target"),
    }
    for pid, g in p.groupby("pair_id"):
        if pid.startswith("P1-PLP2019-"):
            keep[pid] = (round(float(g.items_in_pixel.fillna(0).sum()), 1),
                         "sum over target pixels of drone bottle fraction x 100 m2 x 16.64 /m2; bags NOT counted")
        if pid.startswith("P1-Maathuis2026-") and pid.split("-")[-1] >= "20250310":
            keep[pid] = (360.0, "360 PET bottles (3x30 m at 4 /m2 or 3x15 m at 8 /m2) + 1 polyester sheet not counted")
    for pid, (n, basis) in keep.items():
        g = p[p.pair_id == pid]
        date = pid.split("-")[-1]
        scene = str(g.scene_id.iloc[0])
        rows.append(dict(set="A1", sample_id=pid, group=date, kind="astar", source=_src_of(scene), item_id=scene,
                         epsg=int(g.epsg.iloc[0]), cx=float(g.x.mean()), cy=float(g.y.mean()), date=date,
                         roi="center64", campaign=str(g.campaign.iloc[0])))
        labels.append(dict(sample_id=pid, set="A1", N_true=n, basis=basis, obs_density_km2=np.nan))
    # Maathuis controls (no targets deployed, N = 0): same place, item found by search at fetch time
    g = p[p.pair_id == "P1-Maathuis2026-20250310"]
    for date in ("20250305", "20250324"):
        pid = f"P1-Maathuis2026-{date}"
        rows.append(dict(set="A1", sample_id=pid, group=date, kind="astar", source="planetary-computer",
                         item_id=f"search:31UFT:{date}", epsg=32631, cx=float(g.x.mean()), cy=float(g.y.mean()),
                         date=date, roi="center64", campaign="Maathuis2026"))
        labels.append(dict(sample_id=pid, set="A1", N_true=0.0, basis="control date, no targets (Maathuis 2026)",
                           obs_density_km2=np.nan))


def _src_of(item_id: str) -> str:
    return "planetary-computer" if "_MSIL2A_" in item_id else "earth-search"


def compose_field(rows, labels):
    a = pd.read_csv(ROOT / "reports" / "search" / "adis_candidates.csv")
    a = a[(a.level == "A") & (a.decision == "accept")]
    for r in a.itertuples():
        sid = f"ADIS-{r.event_id}"
        rows.append(dict(set="A2", sample_id=sid, group=sid, kind="adis", source=_src_of(r.scene_id), item_id=r.scene_id,
                         lat=float(r.lat), lon=float(r.lon), date=str(r.scene_datetime)[:10].replace("-", ""),
                         roi="full"))
        labels.append(dict(sample_id=sid, set="A2", N_true=np.nan, obs_density_km2=float(r.dhat_5cm_km2),
                           basis=f"ADIS >5 cm density (dhat_5cm_km2), {r.field_count_by_size}, "
                                 f"survey {r.survey_area_km2:.3f} km2, dt {r.dt_h} h"))
    q = pd.read_csv(ROOT / "data" / "pairs" / "pair_quality.csv")
    ev = pd.read_csv(ROOT / "data" / "pairs" / "events.csv").set_index("event_id")
    q = q[(q.decision == "accept") & q.source.str.contains("sentinel-2")]
    for r in q.itertuples():
        e = ev.loc[r.event_id]
        sid = f"CSV-{r.event_id}"
        rows.append(dict(set="A2", sample_id=sid, group=sid, kind="csv_pair", source=r.source.split("/")[0],
                         item_id=r.scene_id, lat=float(e.lat), lon=float(e.lon),
                         date=str(r.scene_datetime)[:10].replace("-", ""), roi="full"))
        labels.append(dict(sample_id=sid, set="A2", N_true=np.nan, obs_density_km2=float(r.field_items_km2),
                           basis=f"field_items_km2 of the CSV transect (data/pairs), dt {r.dt_hours} h"))


def compose_marida(rows, labels):
    import rasterio
    rng = np.random.default_rng(SEED)
    names = [s.strip() for s in (MARIDA / "splits" / "test_X.txt").read_text().split() if s.strip()]
    per_class = {c: [] for c in MARIDA_CLASSES}
    for nm in names:
        folder = "S2_" + "_".join(nm.split("_")[:2])
        f = MARIDA / "patches" / folder / f"S2_{nm}_cl.tif"
        if not f.exists():
            continue
        with rasterio.open(f) as d:
            cl = d.read(1).astype(int)
        H, W = cl.shape
        deb = cl == 1
        for c in MARIDA_CLASSES:
            rr, cc = np.nonzero(cl == c)
            if not len(rr):
                continue
            order = rng.permutation(len(rr))[:20]
            for k in order:
                y, x = int(rr[k]), int(cc[k])
                y0 = int(np.clip(y - WIN // 2, 0, H - WIN)); x0 = int(np.clip(x - WIN // 2, 0, W - WIN))
                ry, rx = y - y0, x - x0  # ROI 32x32 around the labelled pixel, inside the window
                a0, b0 = int(np.clip(ry - 16, 0, WIN - 32)), int(np.clip(rx - 16, 0, WIN - 32))
                if deb[y0 + a0:y0 + a0 + 32, x0 + b0:x0 + b0 + 32].any():
                    continue
                per_class[c].append((nm, folder, y0, x0, a0, b0, int((cl[y0 + a0:y0 + a0 + 32, x0 + b0:x0 + b0 + 32] == c).sum())))
                break
    for c, lst in per_class.items():
        idx = rng.permutation(len(lst))[:MARIDA_CAP_PER_CLASS]
        for i in sorted(idx):
            nm, folder, y0, x0, a0, b0, npx = lst[i]
            sid = f"MARIDA-{nm}-{MARIDA_CLASSES[c]}"
            rows.append(dict(set="A3", sample_id=sid, group=f"MARIDA-{nm.rsplit('_', 1)[0]}", kind="marida",
                             source="local", item_id=f"patches/{folder}/S2_{nm}.tif", date=nm.split("_")[0],
                             win_r0=y0, win_c0=x0, roi=f"box32:{a0}:{b0}", background=MARIDA_CLASSES[c]))
            labels.append(dict(sample_id=sid, set="A3", N_true=0.0, obs_density_km2=np.nan,
                               basis=f"MARIDA test split, class {c} {MARIDA_CLASSES[c]} ({npx} labelled px in ROI), "
                                     "no Marine Debris label in ROI"))


def compose_vessels(rows, labels, per_chip=1, max_n=40):
    import geopandas as gpd
    v = np.load(ROOT / "data" / "extra" / "features_vessels_fi.npz", allow_pickle=True)
    chips = json.loads(str(v["chips_meta"]))
    rng = np.random.default_rng(SEED)
    out = []
    for ci, ch in enumerate(chips):
        lay = ch["date"].replace("-", "")
        try:
            g = gpd.read_file(ROOT / "data" / "extra" / "finland_vessels" / f"{ch['tile']}.gpkg", layer=lay)
        except Exception:  # noqa: BLE001
            continue
        if g.crs.to_epsg() != ch["epsg"]:
            g = g.to_crs(ch["epsg"])
        w, s, e, n = ch["bounds"]
        c = g.geometry.centroid
        m = (c.x > w + 700) & (c.x < e - 700) & (c.y > s + 700) & (c.y < n - 700)
        g = g[m]
        if not len(g):
            continue
        for k in rng.permutation(len(g))[:per_chip]:
            gc = g.geometry.iloc[k].centroid
            out.append(dict(set="A3", sample_id=f"FIVES-{ch['tile']}-{lay}-c{ci}-b{int(k)}", group=f"FIVES-{ch['tile']}-{lay}",
                            kind="vessel", source=ch["source"], item_id=ch["item"], epsg=int(ch["epsg"]),
                            cx=float(gc.x), cy=float(gc.y), date=lay, roi="center32", background="ship_fi"))
    idx = np.random.default_rng(SEED).permutation(len(out))[:max_n]
    for i in sorted(idx):
        rows.append(out[i])
        labels.append(dict(sample_id=out[i]["sample_id"], set="A3", N_true=0.0, obs_density_km2=np.nan,
                           basis="Finnish vessel box (annotated ship), not debris"))


def compose_clouds(rows, labels):
    import geopandas as gpd
    c = np.load(ROOT / "data" / "extra" / "features_clouds_cmc.npz", allow_pickle=True)
    scenes = json.loads(str(c["scenes_meta"]))
    SRC = ROOT / "data" / "extra" / "cloudmask_catalogue"
    OFF0, HALO = 65, 360.0
    for sm in scenes:
        mask = np.load(SRC / "masks" / f"{sm['scene']}.npy")  # (1022,1022,3) CLEAR, CLOUD, SHADOW at 20 m
        shp = next((SRC / "shapefiles" / sm["scene"]).glob("*.shp"))
        w, s, e, n = gpd.read_file(shp).total_bounds
        mx0 = w + (OFF0 + sm["align_dx"]) * 20.0
        my0 = n - (OFF0 + sm["align_dy"]) * 20.0
        bw, bs, be, bn = sm["bounds"]
        # core of the L90 crop (the part that was actually read), in mask pixel coords
        c0 = int(round((bw + HALO - mx0) / 20)); c1 = int(round((be - HALO - mx0) / 20))
        r0 = int(round((my0 - (bn - HALO)) / 20)); r1 = int(round((my0 - (bs + HALO)) / 20))
        from scipy import ndimage
        for ki, kname in ((1, "cloud"), (2, "cloud_shadow"), (0, "clear_water")):
            frac = ndimage.uniform_filter(mask[..., ki].astype(np.float32), 16, mode="constant")
            sub = np.full_like(frac, -1)
            a0, a1 = max(r0 + 40, 0), min(r1 - 40, 1022); b0, b1 = max(c0 + 40, 0), min(c1 - 40, 1022)
            if a1 <= a0 or b1 <= b0:
                continue
            sub[a0:a1, b0:b1] = frac[a0:a1, b0:b1]
            if sub.max() < 0.9:
                continue
            cand = np.argwhere(sub >= 0.9)
            y, x = cand[len(cand) // 2]
            cx, cy = mx0 + (x + 0.5) * 20.0, my0 - (y + 0.5) * 20.0
            sid = f"CMC-{sm['tile']}-{sm['date']}-{kname}"
            rows.append(dict(set="A3", sample_id=sid, group=f"CMC-{sm['tile']}-{sm['date']}", kind="cmc",
                             source="planetary-computer", item_id=sm["item"], epsg=int(sm["epsg"]), cx=float(cx),
                             cy=float(cy), date=sm["date"].replace("-", ""), roi="center32", background=f"cmc_{kname}"))
            labels.append(dict(sample_id=sid, set="A3", N_true=0.0, obs_density_km2=np.nan,
                               basis=f"CMC hand mask: ROI >= 90 % {kname} (20 m mask, L90 alignment r={sm['align_r']})"))


def compose_cozar(rows, labels, per_scene=3):
    fc = pd.read_csv(ROOT / "data" / "extra" / "cozar2024" / "features_check.csv")
    fc = fc[(fc.status == "ok") & (fc.best_shift == "(0, 0)")]
    reg = pd.read_csv(ROOT / "data" / "extra" / "registry_cozar2024.csv", low_memory=False).set_index("fil_idx")
    for scene, g in fc.groupby("scene_id"):
        for r in g.sort_values("n_px", ascending=False).head(per_scene).itertuples():
            rg = reg.loc[r.fil_idx]
            sid = f"COZAR-{int(r.fil_idx)}"
            rows.append(dict(set="A4", sample_id=sid, group=f"COZAR-{scene}", kind="cozar", source="earth-search",
                             item_id=r.l2a_item, epsg=int(rg.epsg), fil_idx=int(r.fil_idx), n_px=int(r.n_px),
                             ul_x=float(rg.ul_x), ul_y=float(rg.ul_y), row_c=float(rg.row_centroid),
                             col_c=float(rg.col_centroid), date=str(rg.date).replace("-", ""), roi="filament"))
            labels.append(dict(sample_id=sid, set="A4", N_true=np.nan, obs_density_km2=np.nan,
                               basis="Cózar 2024 filament (level B): no count; plausibility band only"))


def compose():
    OUT.mkdir(parents=True, exist_ok=True)
    rows, labels = [], []
    compose_astar(rows, labels)
    compose_field(rows, labels)
    compose_marida(rows, labels)
    compose_vessels(rows, labels)
    compose_clouds(rows, labels)
    compose_cozar(rows, labels)
    c = pd.DataFrame(rows)
    c.to_csv(OUT / "candidates.csv", index=False)
    pd.DataFrame(labels).to_csv(OUT / "labels.csv", index=False)
    print(c.groupby(["set", "kind"]).size().to_string())


# ------------------------------------------------------------------------------------------------------------- fetch
_ITEMS: dict = {}


def get_item(source: str, item_id: str, lon=None, lat=None):
    from macroplastic.live import stac
    key = (source, item_id)
    if key in _ITEMS:
        return _ITEMS[key]
    for a in range(4):
        try:
            if item_id.startswith("search:"):
                _, tile, d = item_id.split(":")
                ds = f"{d[:4]}-{d[4:6]}-{d[6:]}"
                its = stac.search(source, lon, lat, f"{ds}/{ds}", tile=tile)
                it = its[0] if its else None
            else:
                cl = stac.open_client(source)
                its = list(cl.search(collections=["sentinel-2-l2a"], ids=[item_id]).items())
                it = its[0] if its else None
            _ITEMS[key] = it
            return it
        except Exception as e:  # noqa: BLE001
            print("item retry", item_id, e, flush=True)
            time.sleep(3 * (a + 1))
    return None


def win_path(sample_id: str) -> Path:
    """Window file of a sample (':' is not allowed in Windows file names -> '_')."""
    return OUT / "win" / f"{sample_id.replace(':', '_')}.npz"


def _bounds(cx, cy):
    x0 = round((cx - WIN * 5) / 10) * 10.0
    y1 = round((cy + WIN * 5) / 10) * 10.0
    return [x0, y1 - WIN * 10, x0 + WIN * 10, y1]


def fetch_one(r: dict) -> dict:
    from pyproj import Transformer
    from macroplastic.live import stac
    f = win_path(r["sample_id"])
    if f.exists():
        return dict(sample_id=r["sample_id"], status="cached")
    t0 = time.time()
    lon = lat = None
    if pd.notna(r.get("lat")) and r.get("lat") is not None and not np.isnan(r.get("lat", np.nan)):
        lon, lat = float(r["lon"]), float(r["lat"])
    elif pd.notna(r.get("cx")):
        tr = Transformer.from_crs(f"EPSG:{int(r['epsg'])}", "EPSG:4326", always_xy=True)
        lon, lat = tr.transform(float(r["cx"]), float(r["cy"]))
    item = get_item(r["source"], r["item_id"], lon, lat)
    if item is None:
        return dict(sample_id=r["sample_id"], status="no item")
    epsg = stac.item_epsg(item)
    if r["kind"] == "cozar":
        cx = r["ul_x"] + (r["col_c"] + 0.5) * 10.0
        cy = r["ul_y"] - (r["row_c"] + 0.5) * 10.0
    elif pd.notna(r.get("cx")) and int(r["epsg"]) == epsg:
        cx, cy = float(r["cx"]), float(r["cy"])
    else:
        cx, cy = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform(lon, lat)
    b = _bounds(cx, cy)
    try:
        crop = stac.read_crop(item, epsg, b, workers=6)
    except Exception as e:  # noqa: BLE001
        return dict(sample_id=r["sample_id"], status=f"read error {type(e).__name__}: {e}"[:200])
    bands = crop["bands"][[BANDS12.index(k) for k in BANDS11]].astype(np.float32)
    roi = make_roi(r, b)
    meta = dict(sample_id=r["sample_id"], set=r["set"], item_id=item.id, source=r["source"], epsg=epsg, bounds=b,
                datetime=item.properties.get("datetime"), date=str(r["date"]), lon=lon, lat=lat,
                cloud_cover=item.properties.get("eo:cloud_cover"), offset_in_pixels=crop["offset_in_pixels"],
                background=r.get("background") if isinstance(r.get("background"), str) else None)
    f.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(f, bands=bands, scl=crop["scl"].astype(np.uint8), roi=roi, meta=np.array(json.dumps(meta)))
    return dict(sample_id=r["sample_id"], status="ok", s=round(time.time() - t0, 1),
                nan_frac=round(float(np.isnan(bands).any(0).mean()), 3))


def make_roi(r: dict, b) -> np.ndarray:
    kind = r["roi"]
    if kind == "full":
        return np.ones((WIN, WIN), bool)
    if kind == "center64":
        return _roi_center(WIN, 64)
    if kind == "center32":
        return _roi_center(WIN, 32)
    if kind == "filament":
        sys.path.insert(0, str(ROOT / "scripts" / "search"))
        from cozar_remote import open_remote, read_filament
        _, f = open_remote()
        px, py, _ = read_filament(f, int(r["fil_idx"]), int(r["n_px"]))
        # catalogue: pixel_x = row, pixel_y = col of the tile 10 m grid (scripts/search/cozar_features.py);
        # window row index counted from the top edge b[3] (window is on the same 10 m grid)
        rr = ((b[3] - (r["ul_y"] - (px.astype(float) + 0.5) * 10.0)) / 10.0 - 0.5).round().astype(int)
        cc = (((r["ul_x"] + (py.astype(float) + 0.5) * 10.0) - b[0]) / 10.0 - 0.5).round().astype(int)
        m = np.zeros((WIN, WIN), bool)
        ok = (rr >= 0) & (rr < WIN) & (cc >= 0) & (cc < WIN)
        m[rr[ok], cc[ok]] = True
        return m
    raise ValueError(kind)


def marida_window(r: dict):
    """MARIDA windows are read in place from the local patch (no copy)."""
    import rasterio
    with rasterio.open(MARIDA / r["item_id"]) as d:
        a = d.read().astype(np.float32)
        tf = d.transform
        epsg = d.crs.to_epsg()
    y0, x0 = int(r["win_r0"]), int(r["win_c0"])
    bands = a[:, y0:y0 + WIN, x0:x0 + WIN]
    _, a0, b0 = r["roi"].split(":")
    roi = np.zeros((WIN, WIN), bool)
    roi[int(a0):int(a0) + 32, int(b0):int(b0) + 32] = True
    x_ul, y_ul = tf * (x0, y0)
    meta = dict(sample_id=r["sample_id"], set=r["set"], item_id=f"MARIDA:{r['item_id']}", source="local", epsg=epsg,
                bounds=[x_ul, y_ul - WIN * 10, x_ul + WIN * 10, y_ul], datetime=None, date=str(r["date"]),
                lon=None, lat=None, product="MARIDA patch (as distributed), no SCL")
    return bands, np.zeros((WIN, WIN), np.uint8), roi, meta


def fetch(workers=4, only=None, ids_file=None):
    c = pd.read_csv(OUT / "candidates.csv", low_memory=False)
    c = c[c.kind != "marida"]
    if only:
        c = c[c.set.isin(only)]
    if ids_file:
        ids = set(Path(ids_file).read_text(encoding="utf-8").split())
        c = c[c.sample_id.isin(ids)]
    c = c[[not win_path(s).exists() for s in c.sample_id]]
    recs = [r._asdict() for r in c.itertuples(index=False)]
    log = []
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fetch_one, r): r["sample_id"] for r in recs}
        for i, fu in enumerate(as_completed(futs)):
            try:
                res = fu.result()
            except Exception as e:  # noqa: BLE001
                res = dict(sample_id=futs[fu], status=f"error {type(e).__name__}: {e}"[:200])
            log.append(res)
            print(i + 1, len(recs), res, flush=True)
    old = OUT / f"fetch_log_{os.getpid()}.csv"
    df = pd.DataFrame(log)
    if old.exists():
        df = pd.concat([pd.read_csv(old), df]).drop_duplicates("sample_id", keep="last")
    df.to_csv(old, index=False)


# ------------------------------------------------------------------------------------------------------------ freeze
def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def freeze():
    c = pd.read_csv(OUT / "candidates.csv", low_memory=False)
    rows = []
    for r in c.itertuples(index=False):
        if r.kind == "marida":
            p = MARIDA / r.item_id
            rows.append(dict(set=r.set, sample_id=r.sample_id, group=r.group, kind=r.kind, file=f"MARIDA:{r.item_id}",
                             win_r0=r.win_r0, win_c0=r.win_c0, roi=r.roi, sha256=sha256_file(p), status="ok"))
            continue
        f = win_path(r.sample_id)
        if f.exists():
            rows.append(dict(set=r.set, sample_id=r.sample_id, group=r.group, kind=r.kind,
                             file=str(f.relative_to(ROOT)).replace("\\", "/"), roi=r.roi, sha256=sha256_file(f),
                             status="ok"))
        else:
            rows.append(dict(set=r.set, sample_id=r.sample_id, group=r.group, kind=r.kind, file="", roi=r.roi,
                             sha256="", status="missing"))
    m = pd.DataFrame(rows)
    m.to_csv(OUT / "manifest.csv", index=False)
    lab = OUT / "labels.csv"
    print(m.groupby(["set", "kind", "status"]).size().to_string())
    ms, ls_ = sha256_file(OUT / "manifest.csv"), sha256_file(lab)
    print("manifest sha256", ms)
    print("labels sha256", ls_)
    cnt = m[m.status == "ok"].groupby("set").size().to_dict()
    miss = m[m.status != "ok"].sample_id.tolist()
    cfgp = ROOT / "configs" / "discovery_sets.yaml"
    txt = cfgp.read_text(encoding="utf-8").split("\nfrozen:")[0].rstrip("\n")
    txt += "\nfrozen:   # записано build_sets.py freeze до первого прогона методов; harness проверяет перед каждым прогоном\n"
    txt += f"  at: '{time.strftime('%Y-%m-%d %H:%M')}'\n"
    txt += f"  manifest: data/discovery/{VERSION}/manifest.csv\n  manifest_sha256: {ms}\n"
    txt += f"  labels: data/discovery/{VERSION}/labels.csv\n  labels_sha256: {ls_}\n"
    txt += f"  n_ok: {json.dumps(cnt)}\n  missing: {json.dumps(miss, ensure_ascii=False)}\n"
    cfgp.write_text(txt, encoding="utf-8")
    (ROOT / "configs" / "discovery_sets.yaml.sha256").write_text(
        f"{sha256_file(cfgp)} *configs/discovery_sets.yaml\n", encoding="utf-8")
    print("yaml sha256", sha256_file(cfgp))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["compose", "fetch", "freeze"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--ids-file")
    a = ap.parse_args()
    {"compose": compose, "freeze": freeze}.get(a.stage, lambda: fetch(a.workers, a.only, a.ids_file))()
    sys.stdout.flush()
    os._exit(0)  # GDAL/HTTP threads sometimes keep the interpreter alive after all windows are written
