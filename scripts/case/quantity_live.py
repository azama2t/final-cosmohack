"""L93: спутниковая доля покрытия (м²/км² пригодной воды) на реальных сценах подготовки data/live/<region>/<date>/.

Для каждой сцены:
  пригодная вода = water_mask == 1, без края 4 px / nodata, без SCL 4/5 (суда/платформы), без SCL-облаков
  (3, 8, 9, 10) и спектрального облака (grid.cloudmask.spectral_cloud) — как в scripts/build_service_data.py;
  маска детектора = prob ≥ порога на пригодной воде, компоненты ≥ 2 px, не касаются непригодных пикселей,
  не ближе 5 px к облаку (grid.cloudmask.near_cloud_components). Фильтр артефактов (суда/кильватер/шов,
  grid.artifacts) НЕ применяется → доля покрытия здесь — верхняя оценка относительно карты сервиса.
Два режима детектора:
  current — weights/lgbm без гармонизации (текущий режим детектора пар, решение 25.09);
  service — prob_lgbm.tif из data/live (weights/lgbm_live + гармонизация water_median; из него построена карта).
Пороги: сетка внутри «равноценного» диапазона по MARIDA val (F1 ≥ max − 0.01: 0.10–0.88, weights/lgbm/meta.json)
+ рабочий порог. Правило задано до расчёта на сценах.
Кэш построчно: out/l93/live_cov.jsonl (повторный запуск пропускает готовые сцены).

    CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/case/quantity_live.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from scipy import ndimage  # noqa: E402

from macroplastic.case import coverage as V  # noqa: E402
from macroplastic.grid.cloudmask import CLOUD_BUFFER_PX, CLOUD_SCL, near_cloud_components, spectral_cloud  # noqa: E402

LIVE = ROOT / "data" / "live"
CACHE = ROOT / "out" / "l93" / "live_cov.jsonl"
EDGE_PX = 4
MIN_PX = 2
THRESHOLDS = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.63, 0.64, 0.70, 0.80, 0.88]


def read1(p: Path):
    with rasterio.open(p) as ds:
        return ds.read(1), ds.transform


def valid_water(sdir: Path, bands: dict | None):
    water, tr = read1(sdir / "water_mask.tif")
    water = water == 1
    shape = water.shape
    edge = np.zeros(shape, bool)
    edge[:EDGE_PX], edge[-EDGE_PX:], edge[:, :EDGE_PX], edge[:, -EDGE_PX:] = True, True, True, True
    cloud = np.zeros(shape, bool)
    if (sdir / "scl.tif").exists():
        scl, _ = read1(sdir / "scl.tif")
        if scl.shape == shape:
            if (scl == 0).any():
                edge |= ndimage.binary_dilation(scl == 0, structure=np.ones((3, 3), bool), iterations=EDGE_PX)
            water &= ~np.isin(scl, (4, 5))
            cloud |= np.isin(scl, CLOUD_SCL)
    water &= ~edge
    n_spec = 0
    if bands is not None:
        spec = spectral_cloud(bands["B2"], bands["B11"], water)
        n_spec = int(spec.sum())
        water &= ~spec
        cloud |= spec
    water &= ~cloud
    return water, cloud, n_spec


def masked_rows(prob, water, cloud):
    rows = []
    for t in THRESHOLDS:
        m = V.detection_mask(prob, water, t, min_px=MIN_PX, drop_touching_invalid=True)
        lab, n = ndimage.label(m, structure=V.EIGHT)
        near = near_cloud_components(lab, n, cloud, CLOUD_BUFFER_PX)
        if near.any():
            keep = np.concatenate([[False], ~near])
            m = keep[lab]
            n = int((~near).sum())
        r = V.coverage(m, water)
        rows.append({"threshold": t, "detected_px": r.detected_px, "n_objects": int(n),
                     "m2_per_km2": r.m2_per_km2, "detected_area_m2": r.detected_area_m2})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    from macroplastic.models.lgbm_predict import load_predictor
    pred = load_predictor(ROOT / "weights" / "lgbm", harmonize="none", device="cpu")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if CACHE.exists():
        for line in CACHE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["scene"])
    scenes = sorted(p.parent for p in LIVE.glob("*/*/bands.tif"))
    k = 0
    for sdir in scenes:
        key = f"{sdir.parent.name}/{sdir.name}"
        if key in done:
            continue
        if a.limit and k >= a.limit:
            break
        k += 1
        t0 = time.time()
        with rasterio.open(sdir / "bands.tif") as ds:
            names = list(ds.descriptions)
            arr = ds.read().astype(np.float32)
        bands = {b: arr[names.index(b)] for b in ("B2", "B11") if b in names}
        bands = bands if len(bands) == 2 else None
        water, cloud, n_spec = valid_water(sdir, bands)
        scl, _ = read1(sdir / "scl.tif")
        p_cur = pred.predict_proba(arr, names, water_mask=(scl == 6))
        del arr
        p_srv, _ = read1(sdir / "prob_lgbm.tif")
        sj = json.loads((sdir / "scene.json").read_text(encoding="utf-8"))
        pj = json.loads((sdir / "prob_lgbm.json").read_text(encoding="utf-8"))
        row = {"scene": key, "region": sdir.parent.name, "date": sdir.name, "scene_id": sj.get("scene_id"),
               "water_px": int(water.sum()), "water_km2": float(water.sum()) * V.PIXEL_AREA_M2 / 1e6,
               "spectral_cloud_px": n_spec, "cloud_frac_crop": sj.get("crop_cloud_frac"),
               "floor_ppm_1obj": V.detection_floor_ppm(float(water.sum()) * 1e-4, MIN_PX),
               "current": {"weights": "weights/lgbm", "harmonize": "none", "threshold": float(pred.threshold),
                           "rows": masked_rows(p_cur, water, cloud)},
               "service": {"weights": pj.get("weights"), "harmonize": pj.get("harmonize"),
                           "threshold": float(pj.get("threshold", 0.5)),
                           "rows": masked_rows(p_srv, water, cloud) if p_srv.shape == water.shape else None},
               "seconds": round(time.time() - t0, 1)}
        with open(CACHE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        cur = {r["threshold"]: r["m2_per_km2"] for r in row["current"]["rows"]}
        print(f"{key}: water {row['water_km2']:.1f} km², current@0.63 {cur[0.63]:.2f} ppm, {row['seconds']} s", flush=True)


if __name__ == "__main__":
    main()
