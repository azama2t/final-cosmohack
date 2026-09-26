r"""Official MARIDA U-Net (L99 weights, scripts/case/unet_baseline.py) on the detector_v2 new data that has IMAGERY (L92).

U-Net needs image context, so it can only be scored where we keep the crops: the 22 S2 L2A pair crops (97 D objects of
reports/case_pairs/visual_labels.csv + all valid-water pixels) and the 234 random Cózar filament crops
(out/detector_v2/cozar_crops, same per-filament pixel registration as detector_v2_cozar.py features). Vessels / clouds /
FloatingObjects / PLP are pixel-feature files without imagery -> not scored ("—" in the table).
Input as in the service: L2A, no harmonisation. MARIDA val numbers of U-Net come from reports/detector_v2/unet_baseline.json.

    $env:CUDA_VISIBLE_DEVICES=""; $env:PYTHONPATH="src"; .venv\Scripts\python.exe scripts\case\detector_v2_unet_eval.py
Output: reports/detector_v2/unet_newdata.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "case"))
import torch  # noqa: E402

torch.set_num_threads(8)
import detector_retrain as DR  # noqa: E402
import detector_v2_cozar as CZ  # noqa: E402
import unet_baseline as UB  # noqa: E402
from macroplastic.features.pixel import select_bands  # noqa: E402
from macroplastic.live import stac  # noqa: E402

OUT = ROOT / "reports" / "detector_v2" / "unet_newdata.json"


def main():
    t0 = time.time()
    thr = float(json.loads(UB.OUT_VAL.read_text(encoding="utf-8"))["settings"]["unet_prob"]["threshold"])
    model = UB.load_model()
    import rasterio
    d = DR.pairs_table()
    res = {"threshold_prob": thr, "input": "S2 L2A, no harmonisation (as the service)", "pairs": {}, "cozar_l2a": {}}
    # ---- pair crops: D objects + all valid water
    flags = {"argmax": {}, "prob": {}}
    water = {"argmax": [0, 0], "prob": [0, 0]}
    per_crop = {}
    for sc in sorted(p.parent.name for p in DR.QDIR.glob("*/prob.tif") if (DR.BANDS_CACHE / f"{p.parent.name}.npz").is_file()):
        bands, scl = DR._pair_bands(sc)
        with rasterio.open(DR.QDIR / sc / "quality.tif") as ds:
            qa = ds.read(1)
        pmd, arg = UB.infer(model, select_bands(bands, stac.BANDS))
        w = qa == 1
        for k, m in (("argmax", arg), ("prob", pmd >= thr)):
            water[k][0] += int((m & w).sum()); water[k][1] += int(w.sum())
        per_crop[sc] = round(float(arg[w].mean()), 6) if w.any() else None
        for i, r in d[d.scene == sc].iterrows():
            y0, x0, y1, x1 = [int(v) for v in str(r["bbox"]).split()]
            flags["argmax"][int(i)] = bool(arg[y0:y1, x0:x1].any())
            flags["prob"][int(i)] = bool((pmd[y0:y1, x0:x1] >= thr).any())
        print(f"[pairs] {sc} ({time.time() - t0:.0f}s)", flush=True)
    ids = [int(i) for i in d[d.level == "D"].index]
    for k in ("argmax", "prob"):
        n = sum(flags[k][i] for i in ids)
        by = {DR.D_CLASSES[c]: f"{sum(flags[k][int(i)] for i in d[d['class'] == c].index)}/{int((d['class'] == c).sum())}" for c in DR.D_CLASSES}
        res["pairs"][k] = {"D_all": {"n": len(ids), "flagged": int(n), "rate": round(n / len(ids), 4), "ci95": DR.wilson(n, len(ids))},
                           "D_by_class": by, "water_flag_rate_all_valid_px": round(water[k][0] / max(water[k][1], 1), 6),
                           "n_water_px": water[k][1]}
    res["pairs"]["water_flag_per_crop_argmax"] = per_crop
    # ---- Cózar random filaments (L92), same registration as the features
    rule = json.loads((CZ.WORK / "cozar_register_rule.json").read_text(encoding="utf-8"))
    reg = pd.read_csv(CZ.WORK / "cozar_register.csv").set_index("fil_idx")
    fl = {"argmax": [], "prob": []}
    px = {"argmax": [0, 0], "prob": [0, 0]}
    for fi in CZ.fetched_ids():
        b, scl, pxx, pyy, spec, rlo, clo = CZ.load_crop(fi)
        rr = dict(rule)
        if fi in reg.index:
            g = reg.loc[fi]
            if g.r >= 0.6 and g.r - (g.r_second if pd.notna(g.r_second) else 0) >= 0.1:
                rr = {"order": "x_row", "dy": int(g.dy), "dx": int(g.dx)}
        r = pxx - rlo + rr["dy"]
        c = pyy - clo + rr["dx"]
        H, W = b.shape[1:]
        ok = (r >= 0) & (r < H) & (c >= 0) & (c < W)
        r, c = r[ok], c[ok]
        pmd, arg = UB.infer(model, select_bands(b, stac.BANDS))
        for k, m in (("argmax", arg), ("prob", pmd >= thr)):
            v = m[r, c]
            fl[k].append(bool(v.any()))
            px[k][0] += int(v.sum()); px[k][1] += int(len(v))
    for k in ("argmax", "prob"):
        n = int(sum(fl[k]))
        res["cozar_l2a"][k] = {"n": len(fl[k]), "flagged": n, "rate": round(n / len(fl[k]), 4), "ci95": DR.wilson(n, len(fl[k])),
                               "pixel_rate": round(px[k][0] / max(px[k][1], 1), 4)}
    res["seconds"] = round(time.time() - t0, 1)
    OUT.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("pairs", "cozar_l2a")}, ensure_ascii=False)[:1500])


if __name__ == "__main__":
    main()
