"""One-time final evaluation on the MARIDA test split (run once, after the model is frozen).

Nothing is tuned here: the threshold comes from weights/<model>/meta.json (chosen on val).
Writes reports/lgbm_final_test.json ({"test_md": {...}, "val_md": {...}, "ci95": [...], ...}),
which scripts/final_numbers.py picks up.

    .venv\\Scripts\\python.exe scripts\\final_test.py            # final model weights/lgbm
    .venv\\Scripts\\python.exe scripts\\final_test.py --also weights_exp/lgbm/l3_final_backup   # reference only
"""
import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "experiments"))
sys.path.insert(0, str(ROOT / "src"))
import l16_common as C  # noqa: E402

_B = _FIDX = None
BANDS = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]


def _init(model_dir):
    global _B, _FIDX
    import lightgbm as lgb
    from macroplastic.features.pixel import feature_names

    _B = lgb.Booster(model_file=str(Path(model_dir) / "model.txt"))
    meta = json.loads((Path(model_dir) / "meta.json").read_text(encoding="utf-8"))
    allf = feature_names(meta.get("feature_level", "win"))
    _FIDX = [allf.index(n) for n in meta["features"]]


def _full(name):
    from macroplastic.features.pixel import compute_features

    img, cl, _conf, _, _ = C.read_patch(name)
    f = compute_features(img, BANDS, "win")
    n_f, h, w = f.shape
    pr = _B.predict(f[_FIDX].reshape(len(_FIDX), -1).T, num_threads=1).reshape(h, w).astype(np.float32)
    pr[np.isnan(img).any(0)] = 0.0
    m = cl > 0
    return pr[m], cl[m]


def evaluate(model_dir: Path, split: str):
    meta = json.loads((model_dir / "meta.json").read_text(encoding="utf-8"))
    thr = float(meta["threshold"])
    # the only place allowed to read the MARIDA test split (l16_common refuses it on purpose)
    names = [ln.strip() for ln in (C.MARIDA / "splits" / f"{split}_X.txt").read_text().splitlines() if ln.strip()]
    with ProcessPoolExecutor(max_workers=C.THREADS, initializer=_init, initargs=(str(model_dir),)) as ex:
        res = list(ex.map(_full, names, chunksize=4))
    prob = np.concatenate([r[0] for r in res])
    y = np.concatenate([r[1] for r in res]) == 1
    pid = np.concatenate([np.full(len(r[1]), i) for i, r in enumerate(res)])
    scene = np.array([n.rsplit("_", 1)[0] for n in names])[pid]
    pred = prob >= thr
    tp, fp, fn, _ = C.confusion(y, pred)
    sc = C.scores(tp, fp, fn)
    rng = np.random.default_rng(0)  # scene bootstrap CI for F1
    scenes = np.unique(scene)
    per = {s: C.confusion(y[scene == s], pred[scene == s]) for s in scenes}
    f1s = []
    for _ in range(2000):
        pick = rng.choice(scenes, len(scenes), replace=True)
        t = sum(per[s][0] for s in pick); p = sum(per[s][1] for s in pick); n = sum(per[s][2] for s in pick)
        f1s.append(2 * t / max(1, 2 * t + p + n))
    return {"f1_md": sc.get("f1"), "iou_md": sc.get("iou"), "precision_md": sc.get("precision"),
            "recall_md": sc.get("recall"), "tp": tp, "fp": fp, "fn": fn, "threshold": thr,
            "n_patches": len(names), "n_scenes": int(len(scenes)), "md_px": int(y.sum()),
            "ci95": [round(float(np.percentile(f1s, 2.5)), 4), round(float(np.percentile(f1s, 97.5)), 4)]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(ROOT / "weights" / "lgbm"))
    ap.add_argument("--also", nargs="*", default=[], help="reference models (reported, not selected)")
    a = ap.parse_args()
    out = ROOT / "reports" / "lgbm_final_test.json"
    if out.exists():
        sys.exit(f"{out} already exists: the test split is evaluated once. Delete it only if the run crashed.")
    t0 = time.time()
    md = Path(a.model)
    rep = {"model": str(md.relative_to(ROOT)).replace("\\", "/"), "when": datetime.now().isoformat(timespec="seconds"),
           "note": "MARIDA test evaluated once after freezing; threshold from val; nothing selected on test",
           "test_md": evaluate(md, "test"), "val_md": evaluate(md, "val"), "reference": {}}
    for ref in a.also:
        rp = ROOT / ref
        rep["reference"][ref.replace("\\", "/")] = {"test_md": evaluate(rp, "test"), "val_md": evaluate(rp, "val")}
    rep["seconds"] = round(time.time() - t0, 1)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("model", "test_md", "val_md")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
