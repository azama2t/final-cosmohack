r"""L111 (INBOX §15 «ДЕМО»): одна сцена Cózar 2024, доказанно отложенная, как регион данных сервиса + текущий детектор.

  $env:CUDA_VISIBLE_DEVICES=""; .venv\Scripts\python.exe scripts\case\demo_scene.py select   # кандидаты + доказательство
  .venv\Scripts\python.exe scripts\case\demo_scene.py fetch  [--acq 33TXF_20190704]         # L2A вырезка -> data/live/cozar_demo/<date>/
  .venv\Scripts\python.exe scripts\case\demo_scene.py detect                                 # weights/lgbm, без гармонизации, порог 0.63
  .venv\Scripts\python.exe scripts\case\demo_scene.py zones                                  # зоны -> data/case/demo/<key>/

Отбор (select), всё по съёмке = тайл + дата и по тайлу:
  исключены тайлы MARIDA (train/val/test — обучение weights/lgbm и подбор порога), MADOS (обучение weights/lgbm),
  15 съёмок L98 (data/extra/features_cozar2024.npz, эксперименты L92), 234 съёмки L92 (out/detector_v2/cozar_sample.csv,
  features_cozar2024_l2a.npz), PLP/FO (reports/extra_data/registry.csv), суда/облака D (registry_negatives.csv),
  наши пары (data/pairs/candidates.csv) и районы (data/live, data/drift_check).
  Из оставшихся: ≥ 8 нитей в радиусе 6 км от самой плотной, L2A Earth Search той же съёмки, облачность тайла ≤ 5 %,
  ветер 10 м ERA5 (Open-Meteo archive) в час съёмки ≤ 5 м/с (условие LWD у Cózar et al. 2024: u10N ≤ 5 м/с).
Выход select: reports/case_demo/heldout_candidates.csv, reports/case_demo/heldout_check.json (все проверки выбранной).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "case"))

REG = ROOT / "reports" / "extra_data" / "registry_cozar2024.csv.gz"
OUTR = ROOT / "reports" / "case_demo"
REGION = "cozar_demo"
LIVE = ROOT / "data" / "live" / REGION
DEMO = ROOT / "data" / "case" / "demo"
RADIUS_KM = 6.0
CROP_KM = 14.0


def acq_key(tile: str, date: str) -> str:
    return f"{str(tile).lstrip('T')}_{str(date)[:10].replace('-', '')}"


# ------------------------------------------------------------------ exclusion sets
def exclusion_sets() -> dict[str, dict]:
    """name -> {"acq": set(tile_YYYYMMDD), "tiles": set(tile), "file": str, "role": str}."""
    from check_mados_overlap import scan_mados, scan_marida
    out = {}
    mar = scan_marida(ROOT / "data" / "MARIDA", "MARIDA")
    out["MARIDA"] = {"acq": {acq_key(t, d) for t, d in zip(mar.tile, mar.date)}, "tiles": set(mar.tile),
                     "file": "data/MARIDA/splits/{train,val,test}_X.txt + patches (scan_marida)",
                     "role": "обучение weights/lgbm (train), подбор порога 0.63 (val), test"}
    mad = scan_mados(ROOT / "data" / "MADOS", "MADOS")
    mad = mad[mad.tile.notna() & (mad.tile != "")]
    out["MADOS"] = {"acq": {acq_key(t, d) for t, d in zip(mad.tile, mad.date)}, "tiles": set(mad.tile),
                    "file": "data/MADOS (scan_mados, ACOLITE prefix)", "role": "обучение weights/lgbm"}
    z = np.load(ROOT / "data" / "extra" / "features_cozar2024.npz", allow_pickle=True)
    a = {str(x) for x in np.unique(z["acq"])} | {str(x) for x in np.unique(z["acq_bg"])}
    out["L98_cozar15"] = {"acq": a, "tiles": {x.split("_")[0] for x in a}, "file": "data/extra/features_cozar2024.npz (acq, acq_bg)",
                          "role": "эксперименты L92 (B Cózar, 15 съёмок)"}
    s = pd.read_csv(ROOT / "out" / "detector_v2" / "cozar_sample.csv")
    a = {acq_key(t, d) for t, d in zip(s.tile, s.date)}
    z2 = np.load(ROOT / "out" / "detector_v2" / "features_cozar2024_l2a.npz", allow_pickle=True)
    a |= {str(x) for x in np.unique(z2["acq"])}
    out["L92_cozar234"] = {"acq": a, "tiles": {x.split("_")[0] for x in a},
                           "file": "out/detector_v2/cozar_sample.csv + features_cozar2024_l2a.npz (acq)",
                           "role": "эксперименты L92 (234 случайные нити, полнота 36 %)"}
    r = pd.read_csv(ROOT / "reports" / "extra_data" / "registry.csv")
    out["PLP_FO"] = {"acq": {acq_key(t, d) for t, d in zip(r.tile, r.date) if isinstance(t, str) and isinstance(d, str)},
                     "tiles": {str(t).lstrip("T") for t in r.tile if isinstance(t, str)},
                     "file": "reports/extra_data/registry.csv", "role": "B PLP/FloatingObjects (эксперименты L92)"}
    n = pd.read_csv(ROOT / "reports" / "extra_data" / "registry_negatives.csv")
    out["D_vessels_clouds"] = {"acq": {acq_key(t, d) for t, d in zip(n.tile, n.date) if isinstance(t, str) and isinstance(d, str)},
                               "tiles": {str(t).lstrip("T") for t in n.tile if isinstance(t, str)},
                               "file": "reports/extra_data/registry_negatives.csv", "role": "D суда/облака (эксперименты L92)"}
    c = pd.read_csv(ROOT / "data" / "pairs" / "candidates.csv", low_memory=False)
    c = c[c["tile"].notna() & c["scene_datetime"].notna()]
    out["pairs"] = {"acq": {acq_key(t, d) for t, d in zip(c.tile, c.scene_datetime)},
                    "tiles": {str(t).lstrip("T") for t in c.tile}, "file": "data/pairs/candidates.csv",
                    "role": "снимки пар кейса (D пар в L92)"}
    lv = set()
    for root in (ROOT / "data" / "live", ROOT / "data" / "drift_check"):
        for sj in root.glob("*/*/scene.json"):
            if sj.parent.parent.name == REGION:
                continue
            j = json.loads(sj.read_text(encoding="utf-8"))
            lv.add(acq_key(j.get("tile") or "", sj.parent.name))
    out["live_drift"] = {"acq": lv, "tiles": {x.split("_")[0] for x in lv}, "file": "data/live/*/*, data/drift_check/*/*",
                         "role": "районы сервиса"}
    return out


def _wind(lat: float, lon: float, iso: str) -> tuple[float | None, str]:
    """ERA5 10 m wind at the acquisition hour (Open-Meteo archive API; CDS is not used)."""
    import requests
    day = iso[:10]
    url = ("https://archive-api.open-meteo.com/v1/archive?latitude=%.4f&longitude=%.4f&start_date=%s&end_date=%s"
           "&hourly=wind_speed_10m&wind_speed_unit=ms&timezone=UTC" % (lat, lon, day, day))
    for k in range(3):
        try:
            j = requests.get(url, timeout=30).json()
            h = int(iso[11:13]) + (1 if int(iso[14:16]) >= 30 else 0)
            v = j["hourly"]["wind_speed_10m"][min(h, 23)]
            return (None if v is None else float(v)), url
        except Exception:  # noqa: BLE001
            time.sleep(2 * (k + 1))
    return None, url


def _es_item(row):
    from pystac_client import Client
    from macroplastic.live import stac
    c = Client.open(stac.EARTH_SEARCH)
    parts = row.scene_id.split("_")
    sat, dtake = parts[0], parts[2]
    its = [it for it in c.search(collections=["sentinel-2-l2a"], intersects=dict(type="Point", coordinates=[row.lon_pix, row.lat_pix]),
                                 datetime=row.date).items()
           if stac.tile_of(it) == row.tile and str(it.properties.get("s2:datatake_id", "")).startswith(f"G{sat}_{dtake}")]
    orig = [it for it in its if str(it.properties.get("s2:processing_baseline", "99")) < "04"]
    its = sorted(orig or its, key=lambda i: i.id)
    return its[0] if its else None


def cmd_select(a):
    d = pd.read_csv(REG)
    ex = exclusion_sets()
    d["acq"] = [acq_key(t, x) for t, x in zip(d.tile, d.date)]
    bad_tiles = set().union(*(v["tiles"] for v in ex.values()))
    bad_acq = set().union(*(v["acq"] for v in ex.values()))
    ok = d[(d.georef == "stac_transform") & ~d.tile.isin(bad_tiles) & ~d.acq.isin(bad_acq)
           & (d.date >= "2017-06-01")].copy()
    print(f"filaments {len(d)}; after exclusion (tiles/acq of {len(ex)} sets, georef ok, >=2017-06): {len(ok)} "
          f"in {ok.acq.nunique()} acquisitions / {ok.tile.nunique()} tiles", flush=True)
    rows = []
    for acq, g in ok.groupby("acq"):
        if len(g) < a.min_fil:
            continue
        lat, lon = g.lat_pix.values, g.lon_pix.values
        kx = 111.32 * np.cos(np.radians(lat.mean()))
        dx = (lon[:, None] - lon[None, :]) * kx
        dy = (lat[:, None] - lat[None, :]) * 110.57
        near = (np.hypot(dx, dy) <= RADIUS_KM).sum(1)
        i = int(np.argmax(near))
        sel = np.hypot(dx[i], dy[i]) <= RADIUS_KM
        rows.append(dict(acq=acq, tile=g.tile.iloc[0], date=g.date.iloc[0], datetime_utc=g.datetime_utc.iloc[0],
                         scene_id=g.scene_id.iloc[0], n_fil_scene=len(g), px_scene=int(g.n_pixels_fil.sum()),
                         n_fil_cluster=int(sel.sum()), px_cluster=int(g.n_pixels_fil.values[sel].sum()),
                         lat=float(lat[sel].mean()), lon=float(lon[sel].mean()), lat_pix=float(lat[i]), lon_pix=float(lon[i])))
    c = pd.DataFrame(rows).sort_values(["px_cluster", "n_fil_cluster"], ascending=False)
    c = c[c.n_fil_cluster >= a.min_fil].head(a.top)
    print(f"{len(c)} candidate acquisitions with >= {a.min_fil} filaments within {RADIUS_KM} km", flush=True)
    res = []
    for r in c.itertuples():
        w, wurl = _wind(r.lat, r.lon, r.datetime_utc)
        it = None
        try:
            it = _es_item(pd.Series(dict(scene_id=r.scene_id, lon_pix=r.lon_pix, lat_pix=r.lat_pix, date=r.date, tile=r.tile)))
        except Exception as e:  # noqa: BLE001
            print("  stac error", r.acq, e)
        cc = it.properties.get("eo:cloud_cover") if it else None
        res.append({**r._asdict(), "wind10m_ms": w, "wind_url": wurl, "l2a_item": it.id if it else None,
                    "l2a_cloud_cover": cc, "l2a_baseline": it.properties.get("s2:processing_baseline") if it else None})
        print(f"  {r.acq}: fil {r.n_fil_cluster} px {r.px_cluster} wind {w} L2A {it.id if it else None} cc {cc}", flush=True)
    R = pd.DataFrame(res).drop(columns=["Index"])
    R["eligible"] = R.wind10m_ms.notna() & (R.wind10m_ms <= 5.0) & R.l2a_item.notna() & (R.l2a_cloud_cover.fillna(99) <= a.max_cc)
    OUTR.mkdir(parents=True, exist_ok=True)
    R.to_csv(OUTR / "heldout_candidates.csv", index=False)
    print(R[["acq", "n_fil_cluster", "px_cluster", "wind10m_ms", "l2a_cloud_cover", "eligible"]].to_string())
    ex_dump = {k: {"file": v["file"], "role": v["role"], "n_acq": len(v["acq"]), "n_tiles": len(v["tiles"])} for k, v in ex.items()}
    (OUTR / "exclusion_sets.json").write_text(json.dumps(ex_dump, ensure_ascii=False, indent=1), encoding="utf-8")


def check_acq(acq: str) -> dict:
    """Every exclusion set: is the acquisition / its tile in it? + date and time distance to the nearest same-tile acq."""
    ex = exclusion_sets()
    tile, d8 = acq.split("_")
    out = {}
    for k, v in ex.items():
        same_tile = sorted(x for x in v["acq"] if x.split("_")[0] == tile)
        out[k] = {"file": v["file"], "role": v["role"], "n_acq": len(v["acq"]), "n_tiles": len(v["tiles"]),
                  "same_acquisition": acq in v["acq"], "same_tile": tile in v["tiles"], "same_tile_acqs": same_tile[:20]}
    return out


def cmd_fetch(a):
    from macroplastic.live import stac
    c = pd.read_csv(OUTR / "heldout_candidates.csv")
    r = c[c.acq == a.acq].iloc[0] if a.acq else c[c.eligible].iloc[0]
    it = _es_item(pd.Series(dict(scene_id=r.scene_id, lon_pix=r.lon_pix, lat_pix=r.lat_pix, date=r.date, tile=r.tile)))
    epsg = stac.item_epsg(it)
    bounds = stac.crop_bounds(it, float(r.lon), float(r.lat), CROP_KM * 1000)[1]
    print("item", it.id, "epsg", epsg, "bounds", bounds, flush=True)
    crop = stac.read_crop(it, epsg, bounds)
    out = LIVE / str(r.date)
    stac.REGIONS.setdefault(REGION, {"region_name": "Демо Cózar 2024 (отложенная сцена)",
                                     "region_name_en": "Cózar 2024 held-out demo scene", "country": "—"})
    meta = stac.write_scene(out, REGION, it, crop, extra=dict(
        selection=f"L111 held-out Cózar 2024 scene {r.acq} (reports/case_demo/heldout_scene.md)",
        cozar_l1c_product=r.scene_id, wind10m_ms=_wind(float(r.lat), float(r.lon), it.properties["datetime"])[0],
        wind_source="ERA5 via Open-Meteo archive API at the L2A sensing hour (select used the Cózar dec_time hour)"))
    print(json.dumps({k: meta[k] for k in ("scene_id", "date", "crop_cloud_frac", "water_frac", "width", "height")}))


def cmd_detect(a):
    import yaml
    from studio_detector_current import run_scene
    from macroplastic.models.lgbm_predict import load_predictor
    cfg = yaml.safe_load((ROOT / "configs" / "case_pairs.yaml").read_text(encoding="utf-8"))
    assert cfg["detector"]["harmonize"] == "none" and cfg["detector"]["weights"] == "weights/lgbm"
    pred = load_predictor(ROOT / cfg["detector"]["weights"], harmonize="none", device="cpu", num_threads=a.threads)
    for sdir in sorted(LIVE.glob("*/bands.tif")):
        out = DEMO / f"{REGION}_{sdir.parent.name}"
        info = run_scene(sdir.parent, pred, cfg, out)
        print(sdir.parent.name, json.dumps(info))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("select"); s.add_argument("--min-fil", type=int, default=8); s.add_argument("--top", type=int, default=25)
    s.add_argument("--max-cc", type=float, default=5.0)
    f = sp.add_parser("fetch"); f.add_argument("--acq")
    dd = sp.add_parser("detect"); dd.add_argument("--threads", type=int, default=6)
    ch = sp.add_parser("check"); ch.add_argument("acq")
    zz = sp.add_parser("zones")
    a = ap.parse_args()
    if a.cmd == "select":
        cmd_select(a)
    elif a.cmd == "fetch":
        cmd_fetch(a)
    elif a.cmd == "detect":
        cmd_detect(a)
    elif a.cmd == "check":
        print(json.dumps(check_acq(a.acq), ensure_ascii=False, indent=1))
    elif a.cmd == "zones":
        from demo_zones import main as zmain  # noqa: F401
        zmain()
