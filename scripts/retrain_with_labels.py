"""Retrain the pixel LightGBM with human review labels (zones/place/review API). Never replaces weights/ itself.

  .venv/Scripts/python.exe scripts/retrain_with_labels.py [--labels service/labels/labels.jsonl]
        [--out weights_exp/review/<ts>] [--seed 0] [--label-weight 5] [--radius 2]

Steps
 1. Labels (kind=label; the latest label per (region, date, model, id) wins). For each: live scene
    data/live/<region>/<date>/ ($MACROPLASTIC_LIVE overrides data/live). Object pixels = 8-connected components of
    prob_<model>.tif >= threshold (prob_<model>.json) that touch a disk of radius 2 px around the labelled point;
    if none - the max-P pixel inside that disk. debris -> Marine Debris (1); foam/algae/ship_wake/cloud/other ->
    non-MD negatives (MARIDA codes 9/3/14/6/15).
 2. Features 'win' from bands.tif with the same per-scene water-median harmonisation as live inference
    (offset = water_ref of weights/lgbm - median of SCL==6 water), window with a 40 px halo.
 3. Base training data and config = the accepted final model (weights/lgbm/meta.json: MARIDA train + MADOS,
    scripts/train_lgbm_mados.py pipeline, same seed). Two models: baseline (no labels) and baseline + labels
    (label pixels weight --label-weight).
 4. F1 Marine Debris on MARIDA val (threshold chosen on val, as in LightGBM/MADOS training) before/after; decision by SPEC 1.7:
    accept only if gain >= max(0.01, 2 x 0.0025). MARIDA test is never read.
 5. <out>/model.txt, meta.json, result.json. weights/lgbm is NOT replaced - result.json has the command for it.
Exit codes: 0 ok, 2 no labels / no usable pixels (result.json has "error").
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["CUDA_VISIBLE_DEVICES"] = ""

NOISE_STD = 0.0025  # seed noise of val F1 (3 seeds, MADOS training)
MIN_GAIN = max(0.01, 2 * NOISE_STD)
NEG_CODE = {"foam": 9, "algae": 3, "ship_wake": 14, "cloud": 6, "other": 15}
HALO = 40


def log(*a):
    print(*a, flush=True)


def fail(out: Path, msg: str, code: int = 2):
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps({"error": msg}, ensure_ascii=False, indent=1), encoding="utf-8")
    log("[error]", msg)
    sys.exit(code)


def read_labels(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    last = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("kind", "label") != "label" or r.get("label") not in ("debris", *NEG_CODE):
            continue
        last[(r.get("region"), r.get("date"), r.get("model"), r.get("id"))] = r
    return list(last.values())


def live_root() -> Path:
    return Path(os.environ.get("MACROPLASTIC_LIVE") or (ROOT / "data" / "live"))


_SCENE_CACHE: dict = {}


def scene_offset(sdir: Path, water_ref: np.ndarray):
    """Per-band harmonisation offset (BANDS11), as LGBMPredictor.water_offset with water mask SCL == 6."""
    if sdir in _SCENE_CACHE:
        return _SCENE_CACHE[sdir]
    import rasterio

    from macroplastic.features.pixel import select_bands

    with rasterio.open(sdir / "bands.tif") as ds:
        names = list(ds.descriptions)
        h, w = ds.height // 3, ds.width // 3  # decimated read is enough for a median
        arr = ds.read(out_shape=(ds.count, h, w)).astype(np.float32)
    with rasterio.open(sdir / "scl.tif") as ds:
        scl = ds.read(1, out_shape=(h, w))
    b = select_bands(arr, names)
    m = np.isfinite(b).all(0) & (scl == 6)
    off = None if m.sum() < 500 else (water_ref - np.median(b[:, m], axis=1)).astype(np.float32)
    _SCENE_CACHE[sdir] = off
    return off


def label_pixels(rec: dict, water_ref: np.ndarray, radius: int):
    """-> (X features (n,F), info dict) for one label, or (None, info) if unusable."""
    import rasterio
    from rasterio.warp import transform as wtransform
    from rasterio.windows import Window
    from scipy import ndimage

    from macroplastic.features.pixel import BANDS11, compute_features, select_bands

    info = {"id": rec.get("id"), "region": rec.get("region"), "date": rec.get("date"), "model": rec.get("model"),
            "label": rec.get("label")}
    sdir = live_root() / str(rec.get("region")) / str(rec.get("date"))
    pp = sdir / f"prob_{rec.get('model')}.tif"
    if not (sdir / "bands.tif").is_file() or not pp.is_file():
        info["skip"] = f"нет {sdir / 'bands.tif'} или {pp.name}"
        return None, info
    thr = 0.5
    pj = sdir / f"prob_{rec.get('model')}.json"
    if pj.is_file():
        thr = float(json.loads(pj.read_text(encoding="utf-8")).get("threshold", thr))
    with rasterio.open(pp) as ds:
        xs, ys = wtransform("EPSG:4326", ds.crs, [float(rec["lon"])], [float(rec["lat"])])
        col, row = ~ds.transform @ (xs[0], ys[0])
        col, row = int(np.floor(col)), int(np.floor(row))
        r0, c0 = row - 64, col - 64
        prob = ds.read(1, window=Window(c0, r0, 129, 129), boundless=True, fill_value=0).astype(np.float32) / 255
    cy = cx = 64
    yy, xx = np.mgrid[:129, :129]
    disk = (yy - cy) ** 2 + (xx - cx) ** 2 <= radius * radius
    lab, n = ndimage.label(prob >= thr, structure=np.ones((3, 3), bool))
    ids = np.unique(lab[disk & (lab > 0)])
    if len(ids):
        mask = np.isin(lab, ids)
        info["source"] = f"{len(ids)} компонент(ы) P ≥ {thr:.2f}"
    else:
        p = np.where(disk, prob, -1)
        k = np.unravel_index(np.argmax(p), p.shape)
        mask = np.zeros_like(disk)
        mask[k] = True
        info["source"] = f"нет компоненты ≥ порога рядом: взят пиксель max P = {prob[k]:.2f}"
    py, px = np.nonzero(mask)
    y0, y1 = py.min() - HALO, py.max() + HALO + 1
    x0, x1 = px.min() - HALO, px.max() + HALO + 1
    with rasterio.open(sdir / "bands.tif") as ds:
        names = list(ds.descriptions)
        arr = ds.read(window=Window(c0 + x0, r0 + y0, x1 - x0, y1 - y0), boundless=True,
                      fill_value=np.nan).astype(np.float32)
    b = select_bands(arr, names)
    off = scene_offset(sdir, water_ref)
    if off is not None:
        b = b + off[:, None, None]
    info["harmonize_offset"] = None if off is None else [round(float(v), 5) for v in off]
    f = compute_features(b, BANDS11, "win")
    X = f[:, py - y0, px - x0].T
    ok = np.isfinite(X[:, :11]).all(1)
    X = X[ok]
    info["n_px"] = int(len(X))
    return (X if len(X) else None), info


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--labels", default=os.environ.get("MACROPLASTIC_LABELS") or
                    str(ROOT / "service" / "labels" / "labels.jsonl"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--label-weight", type=float, default=5.0)
    ap.add_argument("--radius", type=int, default=2)
    ap.add_argument("--base-weights", default=str(ROOT / "weights" / "lgbm"))
    a = ap.parse_args()
    lp = Path(a.labels)
    if lp.is_dir():
        lp = lp / "labels.jsonl"
    out = Path(a.out) if a.out else ROOT / "weights_exp" / "review" / dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    t0 = time.time()

    labels = read_labels(lp)
    if not labels:
        fail(out, f"нет меток для дообучения в {lp}: отметьте находки в очереди проверки "
                  "(POST /api/review/label, метки debris|foam|algae|ship_wake|cloud|other)")
    base_meta = json.loads((Path(a.base_weights) / "meta.json").read_text(encoding="utf-8"))
    cfg = dict(base_meta["config"])
    water_ref = np.asarray(base_meta["water_ref"], np.float32)
    log(f"[labels] {len(labels)} меток из {lp}")

    Xl, yl, infos = [], [], []
    for r in labels:
        X, info = label_pixels(r, water_ref, a.radius)
        infos.append(info)
        log(f"[label] {info.get('id')} {info.get('label')}: {info.get('n_px', 0)} px ({info.get('source') or info.get('skip')})")
        if X is None:
            continue
        Xl.append(X)
        yl.append(np.full(len(X), 1 if r["label"] == "debris" else NEG_CODE[r["label"]], np.uint8))
    if not Xl:
        fail(out, "ни одна метка не дала пикселей (нет data/live/<region>/<date>/bands.tif или prob_<model>.tif)")
    Xl = np.concatenate(Xl).astype(np.float32)
    yl = np.concatenate(yl)

    import train_lgbm as TL
    import train_lgbm_mados as L13
    from macroplastic.features.pixel import feature_names

    seed = int(a.seed)
    np.random.seed(seed)
    Xva, yva, cva, _ = TL.load_split("val")  # MARIDA val: the only judge
    Xtr, ytr, ctr, _ = TL.load_split("train")
    sel = TL.sample_train(ytr, cfg, seed)
    parts = [(Xtr[sel], ytr[sel], ctr[sel], np.ones(len(sel), np.float32))]
    if cfg.get("data", "combined") in ("combined", "mados"):
        Xm, ym, cm, _info = L13.mados_pixels(cfg, seed)
        parts.append((Xm, ym, cm, np.full(len(ym), float(cfg.get("mados_weight", 1.0)), np.float32)))
    X = np.concatenate([p[0] for p in parts]); y = np.concatenate([p[1] for p in parts])
    c = np.concatenate([p[2] for p in parts]); sw = np.concatenate([p[3] for p in parts])
    fnames = base_meta["features"]
    all_names = feature_names("win")
    fidx = [all_names.index(n) for n in fnames]

    def fit_eval(Xa, ya, ca, swa):
        b = L13.train_weighted(Xa, ya, ca, swa, cfg, seed, fidx)
        pv = TL.prob_md(b, Xva[:, fidx], "binary", [0, 1])
        pv[np.isnan(Xva[:, :11]).any(1)] = 0.0
        thr, met, _ = TL.best_threshold(yva == 1, pv, cfg["threshold_grid"])
        return b, thr, met

    t1 = time.time()
    _, thr0, met0 = fit_eval(X, y, c, sw)
    log(f"[before] F1 MD val {met0['f1']:.4f} (thr {thr0:.2f}) {time.time() - t1:.0f}s")
    t1 = time.time()
    X2 = np.concatenate([X, Xl]); y2 = np.concatenate([y, yl])
    c2 = np.concatenate([c, np.ones(len(yl), np.uint8)])
    sw2 = np.concatenate([sw, np.full(len(yl), float(a.label_weight), np.float32)])
    b1, thr1, met1 = fit_eval(X2, y2, c2, sw2)
    log(f"[after]  F1 MD val {met1['f1']:.4f} (thr {thr1:.2f}) {time.time() - t1:.0f}s")
    # how the new model treats the labelled pixels themselves (training pixels -> optimistic, for sanity only)
    pl0 = None
    try:
        import lightgbm as lgb

        b0 = lgb.Booster(model_file=str(Path(a.base_weights) / "model.txt"))
        pl0 = TL.prob_md(b0, Xl[:, fidx], "binary", [0, 1])
    except Exception:
        pass
    pl1 = TL.prob_md(b1, Xl[:, fidx], "binary", [0, 1])
    agree = lambda p, t: float(np.mean((p >= t) == (yl == 1)))  # noqa: E731

    gain = met1["f1"] - met0["f1"]
    accepted = gain >= MIN_GAIN
    out.mkdir(parents=True, exist_ok=True)
    b1.save_model(str(out / "model.txt"))
    meta = dict(base_meta)
    meta.update({"threshold": round(thr1, 3), "input": base_meta.get("input", "") + " + human review labels",
                 "val_md": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in met1.items()},
                 "review_labels": {"file": str(lp), "n_labels": len(labels), "n_px": int(len(yl)),
                                   "n_md_px": int(np.sum(yl == 1)), "label_weight": a.label_weight},
                 "note": "retrained with review labels (scripts/retrain_with_labels.py); MARIDA test never read"})
    meta.pop("threshold_curve", None)
    (out / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    rel = os.path.relpath(out, ROOT)
    res = {
        "ok": True, "out_dir": str(out), "seed": seed,
        "n_labels": len(labels), "n_label_px": int(len(yl)), "n_label_md_px": int(np.sum(yl == 1)),
        "labels": infos,
        "val_f1_before": round(met0["f1"], 4), "val_f1_after": round(met1["f1"], 4), "gain": round(gain, 4),
        "threshold_before": round(thr0, 3), "threshold_after": round(thr1, 3),
        "val_before": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in met0.items()},
        "val_after": meta["val_md"],
        "reference_weights_lgbm_val_f1": (base_meta.get("val_md") or {}).get("f1"),
        "rule": f"принять, если прирост F1 MD на val MARIDA ≥ max(0.01, 2×{NOISE_STD}) = {MIN_GAIN:.4f}",
        "accepted": bool(accepted),
        "decision": ("принято" if accepted else "не принято") + f": прирост {gain:+.4f} "
                    + ("≥" if accepted else "<") + f" {MIN_GAIN:.4f}",
        "labelled_px_agreement": {"base_model": None if pl0 is None else round(agree(pl0, base_meta["threshold"]), 4),
                                  "new_model": round(agree(pl1, thr1), 4),
                                  "note": "доля пикселей меток, где модель согласна с человеком; это обучающие "
                                          "пиксели новой модели — оптимистично, не метрика качества"},
        "replace_command": (f'copy /Y "{rel}\\model.txt" weights\\lgbm\\model.txt && copy /Y "{rel}\\meta.json" '
                            "weights\\lgbm\\meta.json" if accepted else None),
        "replace_note": "веса в weights\\ автоматически не подменяются; подмена — решение человека",
        "seconds": round(time.time() - t0, 1),
        "caveat": "val MARIDA — ACOLITE rhorc, а метки — с живых сцен L2A: прирост на val мал по построению; "
                  "для оценки на живых сценах нужен отдельный размеченный набор",
    }
    (out / "result.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"[result] {res['decision']} -> {out / 'result.json'} ({res['seconds']}s)")


if __name__ == "__main__":
    main()
