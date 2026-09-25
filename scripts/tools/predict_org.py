#!/usr/bin/env python
"""predict_org.py -- organiser test images -> masks in THEIR format (the submission), same preprocessing as train.

    $env:CUDA_VISIBLE_DEVICES="-1"; $env:PYTHONPATH="src"
    .venv\\Scripts\\python.exe scripts\\tools\\predict_org.py --images <their test folder> `
        --config data\\ingest\\org\\adapter.yaml --weights weights\\lgbm --out out\\sub_asis `
        --format png --value-debris 3 --value-bg 0 --zip

* Preprocessing = the adapter config used for training (bands.source order, radiometry scale/offset, nodata,
  resolution) -- the SAME code path as `python -m macroplastic.ingest --convert` (organizer_adapter.load_image).
  Only layout is replaced: every raster under --images (or a glob) is predicted, masks are not needed.
* Model = macroplastic.models.lgbm_predict.LGBMPredictor (the predictor inference.py uses), `--weights` = any
  folder with model.txt + meta.json (weights\\lgbm "as-is" or weights_exp\\lgbm_ingest\\<name>_s0 "trained on
  their train"). Threshold: --threshold > meta.json threshold.
* Output: <out>/<image stem>.<png|tif> with --value-debris where P >= threshold, --value-bg elsewhere (nodata
  pixels -> --value-nodata, default = --value-bg); tif keeps the source georeference. --prob: <out>_prob/ uint8
  0..255 probabilities. --zip: <out>.zip with the masks only (flat, file names = image names).
* Fails loudly (exit 2) if the model's bands are missing in the data (unless meta.json band_fill or
  --fill-missing), and warns if every mask is empty.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import warnings
import zipfile
from pathlib import Path

if not os.environ.get("CUDA_VISIBLE_DEVICES"):  # PowerShell 5.1: $env:X="" deletes the variable -> "-1"
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
warnings.filterwarnings("ignore", message=".*NotGeoreferenced.*")
warnings.filterwarnings("ignore", category=UserWarning)
import warnings as _w
_w.filterwarnings("ignore", message=".*(geotransform|NotGeoreferenced).*")  # rasterio on PNG/plain TIFF: noise in PowerShell

import numpy as np  # noqa: E402

WL = {"B1": 443, "B2": 492, "B3": 560, "B4": 665, "B5": 704, "B6": 740, "B7": 783, "B8": 833, "B8A": 865,
      "B9": 945, "B10": 1374, "B11": 1614, "B12": 2202}


def _err(msg: str) -> None:
    print(f"[predict_org] ОШИБКА: {msg}", file=sys.stderr)


def model_bands(img: np.ndarray, names: list[str], need: list[str], fill: dict, allow_nearest: bool):
    """-> (C,H,W) in `need` order. Band absent (not in names or all-NaN) -> meta band_fill / nearest / error."""
    have = {n: i for i, n in enumerate(names) if np.isfinite(img[i]).any()}
    out, used_fill, missing = [], {}, []
    for b in need:
        if b in have:
            out.append(img[have[b]])
            continue
        src = fill.get(b)
        if src is None and allow_nearest and have:
            src = min((n for n in have if n in WL), key=lambda n: abs(WL[n] - WL[b]), default=None)
        if src is None or src not in have:
            missing.append(b)
            continue
        used_fill[b] = src
        out.append(img[have[src]])
    if missing:
        raise KeyError(missing)
    return np.stack(out).astype(np.float32), used_fill


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", default=None, help="folder with the organiser test images (recursive) or a glob "
                                                   "relative to the config root; default: layout.test_glob of --config")
    ap.add_argument("--config", required=True, help="adapter.yaml used for training (data/ingest/<name>/adapter.yaml)")
    ap.add_argument("--weights", default=str(ROOT / "weights" / "lgbm"), help="model dir (model.txt + meta.json)")
    ap.add_argument("--out", required=True, help="output folder for the masks")
    ap.add_argument("--format", choices=["png", "tif"], default="png")
    ap.add_argument("--value-debris", type=int, default=1, help="mask value for debris (their class code)")
    ap.add_argument("--value-bg", type=int, default=0, help="mask value for everything else")
    ap.add_argument("--value-nodata", type=int, default=None, help="value for nodata pixels (default = --value-bg)")
    ap.add_argument("--threshold", type=float, default=None, help="default: meta.json threshold of --weights")
    ap.add_argument("--prob", action="store_true", help="also write <out>_prob/<stem>.png|tif (uint8 P*255)")
    ap.add_argument("--zip", action="store_true", help="also write <out>.zip (masks only, flat)")
    ap.add_argument("--fill-missing", action="store_true",
                    help="bands the model needs but the data lacks -> nearest wavelength (a crutch; reported)")
    ap.add_argument("--device", choices=["cpu", "cuda", "auto"], default="cpu")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args(argv)
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    t0 = time.time()
    from macroplastic.features.pixel import BANDS11
    from macroplastic.ingest.formats import test_cfg
    from macroplastic.models.lgbm_predict import load_predictor
    from macroplastic.organizer_adapter import list_samples, load_config
    from macroplastic.organizer_adapter.adapter import load_image

    cfg = test_cfg(load_config(a.config), a.images)
    samples = list_samples(cfg)
    if a.limit:
        samples = samples[: a.limit]
    if not samples:
        _err(f"нет снимков в {a.images} (glob {cfg['layout']['image_glob']} от {cfg['root']})")
        return 2
    wd = Path(a.weights)
    if not (wd / "model.txt").is_file():
        _err(f"нет {wd / 'model.txt'}")
        return 2
    pred = load_predictor(wd, device=a.device)
    meta = pred.meta
    fill = dict(meta.get("band_fill") or {})
    thr = float(a.threshold if a.threshold is not None else pred.threshold)
    vnod = a.value_bg if a.value_nodata is None else a.value_nodata
    dt = np.uint8 if max(a.value_debris, a.value_bg, vnod) <= 255 else np.uint16
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pdir = Path(str(out) + "_prob")
    if a.prob:
        pdir.mkdir(parents=True, exist_ok=True)
    print(f"[predict_org] {len(samples)} снимков, модель {wd} (thr {thr:.3f}, zero_as {meta.get('zero_as', '-')}), "
          f"каналы источника {cfg['bands'].get('source')}, scale {cfg['radiometry'].get('scale')}, "
          f"offset {cfg['radiometry'].get('offset')} -> {out} ({a.format}, debris={a.value_debris}, bg={a.value_bg})")
    from PIL import Image
    import rasterio

    rows, fills, names_seen = [], {}, set()
    for i, s in enumerate(samples):
        src = Path(s.image_paths[0])
        stem = src.stem
        if stem in names_seen:
            _err(f"два снимка с одним именем {stem} — сабмит по имени файла неоднозначен")
            return 2
        names_seen.add(stem)
        try:
            img, names, grid = load_image(cfg, s)
        except ValueError as e:  # band count mismatch
            _err(f"{src.name}: {e}. Проверьте bands.source в {a.config} (число каналов в файле другое).")
            return 2
        try:
            x, used = model_bands(img, names, list(BANDS11), fill, a.fill_missing)
        except KeyError as e:
            _err(f"{src.name}: модели {wd.name} нужны каналы {e.args[0]}, в данных их нет (после адаптера есть: "
                 f"{[n for j, n in enumerate(names) if np.isfinite(img[j]).any()]}). Варианты: модель, обученная "
                 "на их train (train_lgbm_ingest заполнит каналы: meta band_fill), или --fill-missing (костыль).")
            return 2
        fills.update(used)
        p = pred.predict_proba(x, list(BANDS11))
        nod = ~np.isfinite(img).all(0)
        m = np.where(p >= thr, a.value_debris, a.value_bg).astype(dt)
        m[nod] = vnod
        fn = out / f"{stem}.{a.format}"
        if a.format == "png":
            Image.fromarray(m).save(fn)
        else:
            prof = {"driver": "GTiff", "height": m.shape[0], "width": m.shape[1], "count": 1,
                    "dtype": np.dtype(dt).name, "compress": "deflate"}
            with rasterio.open(src) as ds:
                if ds.crs is not None and m.shape == (ds.height, ds.width):
                    prof.update(crs=ds.crs, transform=ds.transform)
            with rasterio.open(fn, "w", **prof) as ds:
                ds.write(m[None])
        if a.prob:
            q = np.clip(np.rint(np.nan_to_num(p) * 255), 0, 255).astype(np.uint8)
            Image.fromarray(q).save(pdir / f"{stem}.png")
        rows.append({"image": str(src), "out": fn.name, "debris_px": int((m == a.value_debris).sum()),
                     "nodata_px": int(nod.sum()), "p_max": round(float(np.nanmax(p)) if p.size else 0.0, 4)})
        if i % 200 == 0 or i == len(samples) - 1:
            print(f"[predict_org] {i + 1}/{len(samples)} {src.name}: {rows[-1]['debris_px']} px", flush=True)
    tot = sum(r["debris_px"] for r in rows)
    rep = {"images": a.images, "config": a.config, "weights": str(wd), "threshold": thr, "n": len(rows),
           "debris_px_total": tot, "files_with_debris": sum(r["debris_px"] > 0 for r in rows),
           "band_fill_used": fills, "value_debris": a.value_debris, "value_bg": a.value_bg, "format": a.format,
           "seconds": round(time.time() - t0, 1), "files": rows}
    (out.parent / f"{out.name}_report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False),
                                                        encoding="utf-8")
    if fills:
        print(f"[predict_org] каналы заполнены соседними: {fills}")
    if tot == 0:
        print("[predict_org] ВНИМАНИЕ: во ВСЕХ масках 0 px мусора — проверьте bands.source/scale/offset в конфиге и "
              f"порог {thr:.3f} (p_max по файлам: {max(r['p_max'] for r in rows):.3f})", file=sys.stderr)
    if a.zip:
        zp = Path(str(out) + ".zip")
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            for r in rows:
                z.write(out / r["out"], r["out"])
        print(f"[predict_org] zip: {zp}")
    print(f"[predict_org] {len(rows)} масок, {tot} px мусора в {rep['files_with_debris']} файлах, "
          f"{rep['seconds']}s -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
