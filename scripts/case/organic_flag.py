r"""L143 (INBOX §51 п.9): флаг «вероятно органика» — проверка на MARIDA val и расчёт для зон сервиса. ЭКСПЕРИМЕНТ.

  $env:CUDA_VISIBLE_DEVICES="-1"; $env:PYTHONPATH="src"; .venv\Scripts\python.exe scripts\case\organic_flag.py val
  $env:CUDA_VISIBLE_DEVICES="-1"; $env:PYTHONPATH="src"; .venv\Scripts\python.exe scripts\case\organic_flag.py zones

Правило (docs/ORGANIC.md, записано до расчёта): NDVI ≥ 0,20 и FAI > 0 — src/macroplastic/case/organic.py.
val   — MARIDA val (НЕ test): доля флага по классам 1–4 (пиксели; объекты = связные области класса в патче, медианы);
        то же среди пикселей, которые детектор weights/lgbm назвал мусором (P ≥ 0,63). -> reports/organic/val_flag.json
zones — для каждой сцены data/case/scene_zones/<key>/ пересобирает кластеры детекции так же, как scripts/case/scene_zones.py
        (дилатация диском CLUSTER_M/2, MIN_ZONE_PX), сверяет число зон с zones.geojson и пишет organic.json
        {zone_id: {likely_organic, ndvi_median, fai_median, organic_px_share, n_px}}. Зоны сцены не перезаписываются.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
import numpy as np  # noqa: E402
from scipy import ndimage  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "case"))
from macroplastic.case import organic as O  # noqa: E402

BANDS = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
I4, I8, I11 = BANDS.index("B4"), BANDS.index("B8"), BANDS.index("B11")
CLS = {1: "Marine Debris", 2: "Dense Sargassum", 3: "Sparse Sargassum", 4: "Natural Organic Material"}
OUT = ROOT / "reports" / "organic"


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, round(c - h, 3)) + 0.0, min(1.0, round(c + h, 3))]


def run_val():
    import lightgbm as lgb
    from macroplastic.data import marida
    from macroplastic.features.pixel import compute_features, feature_names

    wd = ROOT / "weights" / "lgbm"
    meta = json.loads((wd / "meta.json").read_text(encoding="utf-8"))
    bst = lgb.Booster(model_file=str(wd / "model.txt"))
    allf = feature_names(meta.get("feature_level", "win"))
    fidx = [allf.index(n) for n in meta["features"]]
    thr = float(meta["threshold"])
    sens = (0.10, 0.20, 0.30)
    px = {c: {"n": 0, "flag": 0, **{f"flag_ndvi{t:.2f}": 0 for t in sens}, "ndvi_ge": 0, "fai_gt": 0,
              "det_n": 0, "det_flag": 0} for c in CLS}
    ob = {c: {"n": 0, "flag": 0} for c in CLS}
    scenes = {c: {} for c in CLS}
    t0 = time.time()
    names = marida.list_patches("val")
    for p in names:
        img, cl, _conf, _ = marida.load_patch(p)
        bad = np.isnan(img).any(0)
        if not np.isin(cl, list(CLS)).any():
            continue
        f = compute_features(img, BANDS, "win")
        _, h, w = f.shape
        pr = bst.predict(f[fidx].reshape(len(fidx), -1).T, num_threads=4).reshape(h, w)
        pr[bad] = 0
        det = pr >= thr
        b4, b8, b11 = img[I4], img[I8], img[I11]
        nv_ok, fa_ok = O.flag_pixels(b4, b8, b11)
        flag = nv_ok & fa_ok
        nv = O.ndvi(b4, b8)
        scene = marida.parse_scene(p.name)["scene"]
        for c in CLS:
            m = (cl == c) & ~bad
            if not m.any():
                continue
            d = px[c]
            d["n"] += int(m.sum()); d["flag"] += int((flag & m).sum())
            d["ndvi_ge"] += int((nv_ok & m).sum()); d["fai_gt"] += int((fa_ok & m).sum())
            for t in sens:
                d[f"flag_ndvi{t:.2f}"] += int(((np.nan_to_num(nv, nan=-9) >= t) & fa_ok & m).sum())
            d["det_n"] += int((det & m).sum()); d["det_flag"] += int((det & m & flag).sum())
            s = scenes[c].setdefault(str(scene), [0, 0])
            s[0] += int(m.sum()); s[1] += int((flag & m).sum())
            lab, n = ndimage.label(m, structure=np.ones((3, 3), bool))
            for i in range(1, n + 1):
                mm = lab == i
                z = O.zone_flag(b4[mm], b8[mm], b11[mm])
                if z["likely_organic"] is None:
                    continue
                ob[c]["n"] += 1; ob[c]["flag"] += int(z["likely_organic"])
    res = {"split": "MARIDA val (официальный), test не читался", "n_patches": len(names), "rule": O.RULE,
           "sources": O.SOURCES, "detector": {"weights": "weights/lgbm", "threshold": thr}, "seconds": round(time.time() - t0, 1),
           "classes": {}}
    for c, name in CLS.items():
        d, o = px[c], ob[c]
        res["classes"][name] = {
            "pixels": d["n"], "flag_px": d["flag"], "flag_px_share": round(d["flag"] / d["n"], 3) if d["n"] else None,
            "ndvi_ge_0.20_share": round(d["ndvi_ge"] / d["n"], 3) if d["n"] else None,
            "fai_gt_0_share": round(d["fai_gt"] / d["n"], 3) if d["n"] else None,
            "sensitivity_flag_px_share": {f"NDVI≥{t:.2f}": round(d[f"flag_ndvi{t:.2f}"] / d["n"], 3) if d["n"] else None for t in sens},
            "objects": o["n"], "flag_obj": o["flag"], "flag_obj_share": round(o["flag"] / o["n"], 3) if o["n"] else None,
            "flag_obj_ci95_wilson": wilson(o["flag"], o["n"]),
            "detector_md_px": d["det_n"], "detector_md_px_flagged": d["det_flag"],
            "detector_md_flag_share": round(d["det_flag"] / d["det_n"], 3) if d["det_n"] else None,
            "scenes": {k: {"px": v[0], "flag_share": round(v[1] / v[0], 3)} for k, v in sorted(scenes[c].items())},
        }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "val_flag.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, r in res["classes"].items():
        print(f"{name:26s} px {r['pixels']:6d} flag {r['flag_px_share']}  obj {r['flag_obj']}/{r['objects']} "
              f"{r['flag_obj_ci95_wilson']}  det {r['detector_md_px_flagged']}/{r['detector_md_px']}  sens {r['sensitivity_flag_px_share']}")
    print("seconds", res["seconds"])


def run_zones():
    import rasterio
    import scene_zones as SZ

    zroot = ROOT / "data" / "case" / "scene_zones"
    rpx = int(round(SZ.CLUSTER_M / 2 / 10))
    yy, xx = np.ogrid[-rpx:rpx + 1, -rpx:rpx + 1]
    disk = (xx * xx + yy * yy) <= rpx * rpx
    tot = {"scenes": 0, "zones": 0, "flagged": 0, "mismatch": [], "skipped": []}
    for s in SZ.scene_list(None):
        od = zroot / s["key"]
        zf = od / "zones.geojson"
        if not zf.is_file():
            tot["skipped"].append(s["key"]); continue
        zones = [f for f in json.loads(zf.read_text(encoding="utf-8"))["features"]
                 if not str(f["properties"].get("zone_id", "")).endswith("-000")]
        with rasterio.open(s["sdir"] / "bands.tif") as ds:
            names = list(ds.descriptions)
            B = {b: ds.read(names.index(b) + 1).astype(np.float32) for b in ("B4", "B8", "B11")}
        with rasterio.open(s["ddir"] / "det.tif") as dtf:
            det = dtf.read(1) > 0
        out = {}
        if det.any():
            grow = ndimage.binary_dilation(det, structure=disk)
            cl, nc = ndimage.label(grow, structure=np.ones((3, 3), bool))
            k = 0
            for c in range(1, nc + 1):
                dm = det & (cl == c)
                if int(dm.sum()) < SZ.MIN_ZONE_PX:
                    continue
                k += 1
                zid = f"SZ-{s['key']}-{k:03d}"
                out[zid] = O.zone_flag(B["B4"][dm], B["B8"][dm], B["B11"][dm])
        ids = {f["properties"]["zone_id"] for f in zones}
        if ids != set(out):
            tot["mismatch"].append({"key": s["key"], "zones": len(ids), "recomputed": len(out)})
            out = {k: v for k, v in out.items() if k in ids}
        doc = {"rule": O.RULE, "sources": O.SOURCES, "label": O.LABEL, "model_note": O.NOT_SEPARATED,
               "validation": "reports/organic/val_flag.json (MARIDA val)", "zones": out}
        (od / "organic.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        tot["scenes"] += 1; tot["zones"] += len(out); tot["flagged"] += sum(1 for v in out.values() if v["likely_organic"])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "zones_flag.json").write_text(json.dumps(tot, ensure_ascii=False, indent=1), encoding="utf-8")
    print({k: (v if not isinstance(v, list) else len(v)) for k, v in tot.items()})
    if tot["mismatch"]:
        print("mismatch:", tot["mismatch"][:5])


if __name__ == "__main__":
    {"val": run_val, "zones": run_zones}[sys.argv[1] if len(sys.argv) > 1 else "val"]()
