"""L95: детектор в ТЕКУЩЕМ режиме (weights/lgbm, без гармонизации, порог weights/lgbm/meta.json = как у пар) на сценах
студии, у которых есть сырые каналы, но вероятность посчитана только прежним режимом (data/live, data/drift_check:
prob_lgbm.tif = weights/lgbm_live + гармонизация water_median — отвергнут, docs/DECISIONS.md 25.09 22:40).

Маска качества и правило детекций — те же, что в scripts/case/pair_quality.py (строки «quality mask» и «detector on the
whole crop»): пригодная вода = stac.water_mask(cloud_buffer 5) без спектрального облака/блика и nodata; детекция =
prob ≥ порога на пригодной воде, 8-связные компоненты; отбрасываются компоненты < min_px, у облака (5 px) и тени.
Вся вырезка сцены — одна «полоса» (у сцен районов нет трансекты).

Выход (кэш студии, не в git): out/studio_cache/detector_current/<kind>/<region>/<date>/{prob.tif, quality.tif, det.json}
  prob.tif  uint8 = round(P*255); quality.tif — коды pair_quality.py (0 nodata, 1 вода, 2 суша, 3 облако SCL,
  4 спектральное облако, 5 прочее, 6 блик); det.json — режим, порог, pixels, objects, время.
Повторный запуск пропускает готовые сцены (--force — пересчитать).

    CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/case/studio_detector_current.py [--threads 6] [--limit N]
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
import yaml  # noqa: E402
from scipy import ndimage  # noqa: E402

from macroplastic.grid import cloudmask  # noqa: E402
from macroplastic.live import stac  # noqa: E402

OUT = ROOT / "out" / "studio_cache" / "detector_current"
SOURCES = {"live": ROOT / "data" / "live", "drift": ROOT / "data" / "drift_check"}
Q_NODATA, Q_WATER, Q_LAND, Q_CLOUD, Q_SPCLOUD, Q_OTHER, Q_GLINT = 0, 1, 2, 3, 4, 5, 6
Q_DESC = ('quality code: {"0": "nodata", "1": "valid water", "2": "land", "3": "cloud/shadow/cirrus (SCL 3,8,9,10)", '
          '"4": "spectral cloud (grid.cloudmask)", "5": "other", "6": "sun glint"}')


def _write(path: Path, arr, ref, desc: str):
    prof = dict(driver="GTiff", width=ref.width, height=ref.height, count=1, dtype="uint8", crs=ref.crs,
                transform=ref.transform, compress="deflate")
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr.astype(np.uint8)[None])
        dst.descriptions = (desc,)


def run_scene(sdir: Path, pred, cfg: dict, out: Path) -> dict:
    t0 = time.time()
    with rasterio.open(sdir / "bands.tif") as ds:
        names = list(ds.descriptions)
        raw = ds.read().astype(np.float32)
        ref = ds
        bands = np.stack([raw[names.index(b)] for b in stac.BANDS])
        with rasterio.open(sdir / "scl.tif") as s:
            scl = s.read(1)
        # --- quality mask: scripts/case/pair_quality.py
        valid = np.isfinite(bands).all(0) & (scl != 0)
        water = stac.water_mask(bands, scl, cloud_buffer_px=cfg["detector"]["cloud_buffer_px"]).astype(bool)
        spc = cloudmask.spectral_cloud(bands[1], bands[10], (scl == 6) & valid)
        scl_cloud = np.isin(scl, cloudmask.CLOUD_SCL)
        w6 = (scl == 6) & valid
        gc = cfg["decision"]
        glint_scene = bool(w6.sum() and spc[w6].mean() > gc["glint_scene_spc_frac"]
                           and scl_cloud[valid].mean() < gc["glint_scene_max_scl_cloud"])
        glint = spc & glint_scene
        spc = spc & (not glint_scene)
        cloud = (scl_cloud | spc) & valid
        land = np.isin(scl, (4, 5, 11)) & valid & ~cloud
        water_ok = water & ~spc & ~glint & valid
        qa = np.full(scl.shape, Q_OTHER, np.uint8)
        qa[~valid] = Q_NODATA
        qa[water_ok] = Q_WATER
        qa[land] = Q_LAND
        qa[scl_cloud & valid] = Q_CLOUD
        qa[spc & ~scl_cloud] = Q_SPCLOUD
        qa[glint & ~scl_cloud] = Q_GLINT
        # --- detector on the whole crop (current mode)
        t1 = time.time()
        prob = np.nan_to_num(pred.predict_proba(raw, names, water_mask=(scl == 6)), nan=0.0)
        del raw
        thr = float(pred.threshold)
        det = (prob >= thr) & water_ok
        lab, n = ndimage.label(det, structure=np.ones((3, 3), bool))
        n_raw = n
        drop = np.zeros(n, bool)
        if n:
            sizes = np.bincount(lab.ravel(), minlength=n + 1)[1:]
            drop |= sizes < cfg["detector"]["min_px"]
            drop |= cloudmask.near_cloud_components(lab, n, cloud, cfg["detector"]["cloud_buffer_px"])
            drop |= cloudmask.shadow_components(lab, n, bands[1], bands[2], bands[3], bands[7], water_ok)
        lab, n = cloudmask.drop_components(lab, n, drop)
        det = lab > 0
        out.mkdir(parents=True, exist_ok=True)
        _write(out / "prob.tif", np.clip(np.round(prob * 255), 0, 255), ref, "P(marine debris)*255, weights/lgbm, harmonize none")
        _write(out / "quality.tif", qa, ref, Q_DESC)
        _write(out / "det.tif", det, ref, "detections after filters (1 = object pixel)")
    info = {"scene_dir": sdir.relative_to(ROOT).as_posix(), "weights": "weights/lgbm", "harmonize": "none",
            "threshold": thr, "min_px": cfg["detector"]["min_px"], "cloud_buffer_px": cfg["detector"]["cloud_buffer_px"],
            "pixels": int(det.sum()), "objects": int(n), "objects_raw": int(n_raw), "dropped_cloud_shadow": int(drop.sum()),
            "water_px": int(water_ok.sum()), "prob_max_water": round(float(prob[water_ok].max()), 4) if water_ok.any() else None,
            "glint_scene": glint_scene, "runtime_s": round(time.time() - t1, 1), "total_s": round(time.time() - t0, 1),
            "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (out / "det.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return info


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    from macroplastic.models.lgbm_predict import load_predictor
    cfg = yaml.safe_load((ROOT / "configs" / "case_pairs.yaml").read_text(encoding="utf-8"))
    assert cfg["detector"]["harmonize"] == "none" and cfg["detector"]["weights"] == "weights/lgbm"
    pred = load_predictor(ROOT / cfg["detector"]["weights"], harmonize="none", device="cpu", num_threads=a.threads)
    k = 0
    for kind, root in SOURCES.items():
        for sdir in sorted(p.parent for p in root.glob("*/*/bands.tif")):
            out = OUT / kind / sdir.parent.name / sdir.name
            if (out / "det.json").is_file() and not a.force:
                continue
            if a.limit and k >= a.limit:
                return
            k += 1
            if not (sdir / "scl.tif").is_file():
                print(f"{kind}/{sdir.parent.name}/{sdir.name}: no scl.tif, skipped", flush=True)
                continue
            info = run_scene(sdir, pred, cfg, out)
            print(f"{kind}/{sdir.parent.name}/{sdir.name}: {info['pixels']} px, {info['objects']} obj, "
                  f"{info['total_s']} s", flush=True)


if __name__ == "__main__":
    main()
