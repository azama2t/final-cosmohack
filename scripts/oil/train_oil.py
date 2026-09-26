"""Класс «Нефтяное пятно» (INBOX §14, L101): отдельная бинарная голова LightGBM на признаках pixel.py.

Подкоманды (корень репозитория; CPU, <= 6 потоков; CUDA_VISIBLE_DEVICES=""):
  .venv\\Scripts\\python.exe scripts\\oil\\train_oil.py split      # инвентарь MADOS + проверки пересечений -> configs/oil_eval.yaml (ДО обучения)
  .venv\\Scripts\\python.exe scripts\\oil\\train_oil.py train      # варианты LightGBM + порог OSI, выбор ТОЛЬКО по val -> weights_exp/oil/
  .venv\\Scripts\\python.exe scripts\\oil\\train_oil.py test       # ОДИН раз: замороженные модель/пороги на MADOS test; отметка в oil_eval.yaml
  .venv\\Scripts\\python.exe scripts\\oil\\train_oil.py infer      # реальные сцены сервиса (data/live, вырезки пар) -> data/case/oil/
Основной детектор мусора weights/lgbm не читается и не меняется. MARIDA test не читается (только готовая таблица
пересечений out/l13_cache/mados_overlap_with_test.csv, построенная по изображениям без меток).

Данные: MADOS (Kikaki, Kakogeorgiou et al. 2024, ISPRS; Zenodo 10664073, CC BY 4.0), класс 6 Oil Spill (положительный),
13 Oil Platform и все прочие размеченные классы — отрицательные. Сплит — официальный сплит MADOS (по сценам: каждая
сцена целиком в одном сплите — проверяется), минус исключения (см. split).
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ.setdefault("L13_THREADS", "6")
warnings.filterwarnings("ignore")

from macroplastic.data import mados as MD  # noqa: E402
from macroplastic.features.pixel import BANDS11, feature_names  # noqa: E402
from macroplastic.oil import metrics as OM  # noqa: E402
from macroplastic.oil.baseline import osi_score  # noqa: E402

THREADS = 6
CFG_PATH = ROOT / "configs" / "oil_eval.yaml"
LGBM_CFG = ROOT / "configs" / "lgbm.yaml"
OUT = ROOT / "out" / "l101"
REP = ROOT / "reports" / "oil"
WEXP = ROOT / "weights_exp" / "oil"
OIL_DATA = ROOT / "data" / "case" / "oil"
PROB_CACHE = ROOT / "out" / "l101" / "prob"
OVERLAP_TRVAL = ROOT / "reports" / "mados_overlap.csv"
OVERLAP_TEST = ROOT / "out" / "l13_cache" / "mados_overlap_with_test.csv"
OIL, PLAT = 16, 17  # extended MARIDA codes of MADOS 6 / 13 (macroplastic.data.mados.LUT_EXTRA)
MIN_VOTES, SAME_ACQ_RATIO = 8, 0.01  # as scripts/train_lgbm_mados.py


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def sha256_obj(o) -> str:
    return hashlib.sha256(json.dumps(o, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


# ============================================================================ split
def _scan_crop(name):
    r = MD.resolve_root()
    sc, cr = MD.parse_patch(name)
    cl = MD._read(r / sc / "10" / f"{sc}_L2R_cl_{cr}.tif")
    return name, int((cl == 6).sum()), int((cl == 13).sum()), int((cl > 0).sum())


def _lbp_crop(name):
    import train_lgbm_mados as TM

    b4, b8 = TM._mados_b4b8(name)
    key, ok = TM.lbp_key(b4, b8)
    return key, ok


def _index_part(names):
    ks, pids, pix = [], [], []
    for i, n in names:
        key, ok = _lbp_crop(n)
        ok[1::2, :] = False
        ok[:, 1::2] = False
        yy, xx = np.nonzero(ok)
        ks.append(key[ok]); pids.append(np.full(len(yy), i, np.int32)); pix.append((yy * 240 + xx).astype(np.int32))
    return np.concatenate(ks), np.concatenate(pids), np.concatenate(pix)


_IDX = None


def _init(path):
    global _IDX
    z = np.load(path)
    _IDX = (z["keys"], z["pid"], z["pix"])


def _match(name):
    keys, pid, pix = _IDX
    key, ok = _lbp_crop(name)
    yy, xx = np.nonzero(ok)
    k = key[ok]
    j = np.minimum(np.searchsorted(keys, k), len(keys) - 1)
    hit = keys[j] == k
    if not hit.any():
        return name, []
    q = pid[j[hit]]
    mp = pix[j[hit]]
    dy = (mp // 240 - yy[hit]).astype(np.int64)
    dx = (mp % 240 - xx[hit]).astype(np.int64)
    code = (q.astype(np.int64) * 1024 + (dy + 512)) * 1024 + (dx + 512)
    u, c = np.unique(code, return_counts=True)
    out = []
    for cc, n in zip(u[c >= MIN_VOTES], c[c >= MIN_VOTES]):
        qq = int(cc // (1024 * 1024)); ddy = int((cc // 1024) % 1024) - 512; ddx = int(cc % 1024) - 512
        h = max(0, min(240, 240 + ddy) - max(0, ddy)); w = max(0, min(240, 240 + ddx) - max(0, ddx))
        if h * w > 0:
            out.append((qq, ddy, ddx, int(n), int(h * w)))
    return name, out


def intra_mados_check(eval_crops: list[str], train_crops: list[str]) -> list[dict]:
    """Та же радиометрически устойчивая LBP-проверка, что в train_lgbm_mados.py overlap, но MADOS val/test <-> MADOS
    train: не является ли сцена проверки тем же снимком/местом, что сцена обучения (MADOS-сцены анонимны)."""
    OUT.mkdir(parents=True, exist_ok=True)
    idx_path = OUT / "mados_train_lbp_index.npz"
    names = list(enumerate(train_crops))
    if not idx_path.is_file():
        chunks = [names[i::THREADS * 4] for i in range(THREADS * 4)]
        with ProcessPoolExecutor(max_workers=THREADS) as ex:
            res = list(ex.map(_index_part, chunks))
        keys = np.concatenate([r[0] for r in res]); pid = np.concatenate([r[1] for r in res])
        pix = np.concatenate([r[2] for r in res])
        o = np.argsort(keys, kind="stable")
        np.savez(idx_path, keys=keys[o], pid=pid[o], pix=pix[o], names=np.array(train_crops))
    z = np.load(idx_path)
    assert [str(s) for s in z["names"]] == train_crops, "stale index"
    del z
    with ProcessPoolExecutor(max_workers=THREADS, initializer=_init, initargs=(str(idx_path),)) as ex:
        res = list(ex.map(_match, eval_crops, chunksize=8))
    rows = []
    for name, hits in res:
        for q, dy, dx, n, ov in hits:
            rows.append({"eval_crop": name, "train_crop": train_crops[q], "dy": dy, "dx": dx, "votes": n,
                         "overlap_px": ov, "kind": "same_acquisition" if n / ov >= SAME_ACQ_RATIO
                         else "same_place_other_date"})
    return rows


def cmd_split(a):
    if CFG_PATH.is_file() and not a.force:
        raise SystemExit(f"{CFG_PATH} уже заморожен; новая проверка = новый файл (или --force до обучения)")
    t0 = time.time()
    split_of = MD.split_of()
    crops = MD.list_patches("all")
    with ProcessPoolExecutor(max_workers=THREADS) as ex:
        scan = list(ex.map(_scan_crop, crops, chunksize=16))
    by_scene = collections.defaultdict(lambda: collections.Counter())
    scene_splits = collections.defaultdict(set)
    for name, n6, n13, nl in scan:
        sc = MD.parse_patch(name)[0]
        s = split_of.get(name, "none")
        scene_splits[sc].add(s)
        c = by_scene[sc]
        c["crops"] += 1; c["oil_px"] += n6; c["platform_px"] += n13; c["labelled_px"] += nl
        c["oil_crops"] += int(n6 > 0)
    multi = {k: sorted(v) for k, v in scene_splits.items() if len(v) > 1}
    assert not multi, f"MADOS scenes in several splits: {multi}"
    print(f"[split] scanned {len(crops)} crops, {time.time() - t0:.0f}s; every scene in one split", flush=True)

    # MARIDA overlap (images only; MARIDA test labels are never read)
    import csv
    excl = {}
    for f in (OVERLAP_TRVAL, OVERLAP_TEST):
        for r in csv.DictReader(open(f, encoding="utf-8")):
            if r["kind"] == "same_acquisition" and r["marida_split"] in ("val", "test"):
                excl[r["mados_scene"]] = f"same acquisition as MARIDA {r['marida_split']} ({r['marida_scene']})"
    # MADOS eval <-> MADOS train (anonymous scenes)
    tr_crops = [c for c in crops if split_of.get(c) == "train"]
    ev_crops = [c for c in crops if split_of.get(c) in ("val", "test")]
    t1 = time.time()
    intra = intra_mados_check(ev_crops, tr_crops)
    acq = collections.defaultdict(set)
    place = collections.defaultdict(set)
    for r in intra:
        es, ts = MD.parse_patch(r["eval_crop"])[0], MD.parse_patch(r["train_crop"])[0]
        (acq if r["kind"] == "same_acquisition" else place)[es].add(ts)
    print(f"[split] intra-MADOS check {len(ev_crops)} eval crops vs {len(tr_crops)} train crops: "
          f"{len(acq)} eval scenes same acquisition as a train scene, {len(place)} same place; {time.time() - t1:.0f}s",
          flush=True)
    # rule: a train scene that is the same acquisition as an eval scene is dropped from training (eval kept fixed)
    for es, tss in acq.items():
        for ts in tss:
            excl.setdefault(ts, f"same acquisition as MADOS {split_of[[c for c in crops if c.startswith(es + '_')][0]]} "
                                f"scene {es}")
    (OUT / "intra_mados_overlap.json").write_text(json.dumps(intra, indent=0), encoding="utf-8")

    comp = {}
    for sp in ("train", "val", "test"):
        scenes = sorted((s for s in by_scene if sp in scene_splits[s] and s not in excl),
                        key=lambda s: int(s.split("_")[1]))
        oil_sc = [s for s in scenes if by_scene[s]["oil_px"] > 0]
        comp[sp] = {
            "scenes": scenes, "n_scenes": len(scenes), "oil_scenes": oil_sc, "n_oil_scenes": len(oil_sc),
            "n_crops": int(sum(by_scene[s]["crops"] for s in scenes)),
            "oil_px": int(sum(by_scene[s]["oil_px"] for s in scenes)),
            "oil_crops": int(sum(by_scene[s]["oil_crops"] for s in scenes)),
            "platform_px": int(sum(by_scene[s]["platform_px"] for s in scenes)),
            "labelled_px": int(sum(by_scene[s]["labelled_px"] for s in scenes)),
            "oil_px_by_scene": {s: int(by_scene[s]["oil_px"]) for s in oil_sc},
        }
        comp[sp]["scenes_sha256"] = sha256_obj(scenes)
    r = MD.resolve_root()
    cfg = {
        "version": 1,
        "frozen_at": dt.datetime.now().isoformat(timespec="seconds"),
        "frozen_by": "L101 (INBOX §14), before any oil training",
        "task": "binary pixel head 'oil spill' (MADOS 6) vs every other labelled MADOS pixel (incl. 13 Oil Platform)",
        "source": {"dataset": "MADOS (Kikaki, Kakogeorgiou et al. 2024, ISPRS J.; Zenodo 10664073; CC BY 4.0)",
                   "root": "data/MADOS", "positive_class": {"mados": 6, "name": "Oil Spill"},
                   "context_negative": {"mados": 13, "name": "Oil Platform"},
                   "radiometry": "ACOLITE rhorc (Rayleigh-corrected, from L1C)",
                   "split_files_sha256": {s: sha256_file(r / "splits" / f"{s}_X.txt") for s in ("train", "val", "test")}},
        "split_rule": "official MADOS split (scene-level: verified every Scene_k lies in one split) minus exclusions",
        "exclusions": {"rule": ["MADOS scene = same S2 acquisition as a MARIDA val/test scene (reports/mados_overlap.csv, "
                                "out/l13_cache/mados_overlap_with_test.csv — image LBP match, no MARIDA test labels read)",
                                "MADOS train scene = same acquisition as a MADOS val/test scene (LBP B4/B8 match, "
                                "out/l101/intra_mados_overlap.json) -> dropped from training"],
                       "scenes": dict(sorted(excl.items(), key=lambda t: int(t[0].split("_")[1]))),
                       "overlap_tables_sha256": {str(OVERLAP_TRVAL.relative_to(ROOT)).replace("\\", "/"): sha256_file(OVERLAP_TRVAL),
                                                 str(OVERLAP_TEST.relative_to(ROOT)).replace("\\", "/"): sha256_file(OVERLAP_TEST)},
                       "intra_mados_same_place_other_date": {k: sorted(v) for k, v in sorted(place.items())},
                       "note_ours": "MADOS scenes do not include the case pairs/live scenes (2015-2022 ACOLITE crops "
                                    "vs our 2018-2026 L2A scenes); MARIDA has no oil class"},
        "composition": comp,
        "composition_sha256": sha256_obj(comp),
        "metric": {"unit": "labelled pixels only (unlabelled MADOS pixels excluded, as the MADOS/MARIDA protocol)",
                   "primary": "pooled F1 of oil vs rest over all labelled pixels of the split",
                   "also": ["precision", "recall", "IoU", "per-scene F1 on oil scenes"],
                   "ci": "scene bootstrap 2000 reps, numpy default_rng(0), percentile 2.5/97.5",
                   "nan_rule": "pixel with a NaN band -> not oil"},
        "selection": {"only_on": "val", "lgbm_threshold_grid": [0.02, 0.98, 0.01],
                      "baseline": "OSI = (B3+B4)/B2 (Rajendran et al. 2021, MethodsX 8:101327); sign and threshold "
                                  "chosen on val (grid = 1..99 percentiles of val OSI)",
                      "variants": "a few LightGBM variants (features, pos_weight), seed 0; best val F1 -> final"},
        "test_policy": "MADOS test oil scenes are evaluated ONCE at the end with frozen model + thresholds; the "
                       "opening is recorded below (test_opened)",
        "test_opened": None,
    }
    CFG_PATH.write_text("# FROZEN before oil training (L101). Do not edit the composition; a new check = a new file.\n"
                        + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")
    for sp in ("train", "val", "test"):
        c = comp[sp]
        print(f"[split] {sp}: {c['n_scenes']} scenes ({c['n_oil_scenes']} with oil), {c['oil_px']} oil px in "
              f"{c['oil_crops']} crops, {c['platform_px']} platform px, {c['labelled_px']} labelled px", flush=True)
    print(f"[split] excluded {len(excl)} scenes -> {CFG_PATH}", flush=True)


# ============================================================================ data
def load_cfg():
    cfg = yaml.safe_load(CFG_PATH.read_text(encoding="utf-8"))
    assert sha256_obj(cfg["composition"]) == cfg["composition_sha256"], "oil_eval.yaml composition changed"
    return cfg


def _extract(name):
    from macroplastic.features.pixel import compute_features

    img, cl, conf, _ = MD.load_patch(name, extra=True)
    m = cl > 0
    f = compute_features(img, MD.BAND_NAMES, "win")
    return f[:, m].T.copy(), cl[m], conf[m]


def load_split(split: str, scenes: list[str]):
    """Labelled MADOS pixels of the given scenes, 'win' features, extended codes (16 = oil, 17 = platform).
    train/val reuse out/l13_cache (train_lgbm_mados.load_mados, same features); test -> out/l101/mados_test_win.npz."""
    keep = set(scenes)
    if split in ("train", "val"):
        import train_lgbm_mados as TM

        X, y, conf, pid, patches = TM.load_mados(split)
    else:
        path = OUT / "mados_test_win.npz"
        patches = MD.list_patches("test")
        names = feature_names("win")
        if path.is_file():
            z = np.load(path, allow_pickle=False)
            assert list(z["names"]) == names and list(z["patches"]) == patches
            X, y, conf, pid = z["X"], z["y"], z["conf"], z["pid"]
        else:
            t = time.time()
            with ProcessPoolExecutor(max_workers=THREADS) as ex:
                res = list(ex.map(_extract, patches, chunksize=8))
            X = np.concatenate([r[0] for r in res]).astype(np.float32)
            y = np.concatenate([r[1] for r in res]).astype(np.uint8)
            conf = np.concatenate([r[2] for r in res]).astype(np.uint8)
            pid = np.concatenate([np.full(len(r[1]), i, np.int32) for i, r in enumerate(res)])
            OUT.mkdir(parents=True, exist_ok=True)
            np.savez(path, X=X, y=y, conf=conf, pid=pid, names=np.array(names), patches=np.array(patches))
            print(f"[cache] MADOS test: {len(patches)} patches, {len(y)} px, {time.time() - t:.0f}s", flush=True)
    psc = np.array([MD.parse_patch(p)[0] for p in patches])
    scene = psc[pid]
    m = np.isin(scene, list(keep))
    return X[m], y[m], conf[m], scene[m]


def sample_train(y, seed, cap=30000, cap_water=60000):
    rng = np.random.default_rng(seed)
    idx = [np.flatnonzero(y == OIL)]
    for c in np.unique(y):
        if c == OIL:
            continue
        ii = np.flatnonzero(y == c)
        k = cap_water if c == 7 else cap
        if len(ii) > k:
            ii = np.sort(rng.choice(ii, k, replace=False))
        idx.append(ii)
    return np.sort(np.concatenate(idx))


def train_booster(X, y, fnames, params_over: dict, pos_weight: float, seed: int):
    import lightgbm as lgb

    base = yaml.safe_load(LGBM_CFG.read_text(encoding="utf-8"))["lgbm"]
    params = {**base, **params_over}
    rounds = int(params.pop("num_boost_round"))
    params.update(seed=seed, bagging_seed=seed, feature_fraction_seed=seed, data_random_seed=seed,
                  num_threads=THREADS, verbose=-1, deterministic=True, force_row_wise=True, objective="binary")
    label = (y == OIL).astype(np.float32)
    w = np.where(label > 0, float(pos_weight), 1.0).astype(np.float32)
    ds = lgb.Dataset(X, label=label, weight=w, feature_name=fnames, free_raw_data=True)
    return lgb.train(params, ds, num_boost_round=rounds)


def evaluate(true, score, thr, scene):
    pred = score >= thr
    cnt = OM.scene_counts(true, pred, scene)
    pooled = OM.pooled_from_counts(cnt)
    ci = OM.bootstrap_ci(cnt)
    per_scene = {s: round(OM.binary_metrics(*c[:3])["f1"], 4) for s, c in cnt.items() if c[3] > 0}
    return {"pooled": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in pooled.items()},
            "ci95": {k: [round(x, 4) for x in v] for k, v in ci.items()},
            "per_oil_scene_f1": per_scene,
            "median_oil_scene_f1": round(float(np.median(list(per_scene.values()))), 4) if per_scene else None,
            "n_scenes": len(cnt), "n_oil_scenes": len(per_scene), "n_px": int(len(true)), "n_oil_px": int(true.sum())}


def fp_by_class(y, pred):
    names = {**{k: v for k, v in enumerate(["", "Marine Debris", "Dense Sargassum", "Sparse Floating Algae",
                                              "Natural Organic Material", "Ship", "", "Marine Water",
                                              "Sediment-Laden Water", "Foam", "Turbid Water", "Shallow Water",
                                              "Waves & Wakes"])}, 16: "Oil Spill", 17: "Oil Platform",
             18: "Jellyfish", 19: "Sea snot"}
    return {names.get(int(c), str(c)): {"n": int(np.sum(y == c)), "pred_oil": int(np.sum(pred[y == c]))}
            for c in np.unique(y)}


VARIANTS = {
    "win_pw1": {"features": "win", "pos_weight": 1.0, "params": {}},
    "win_pw3": {"features": "win", "pos_weight": 3.0, "params": {}},
    "min_pw1": {"features": "min", "pos_weight": 1.0, "params": {}},
    "win_pw1_l31": {"features": "win", "pos_weight": 1.0, "params": {"num_leaves": 31, "min_data_in_leaf": 100}},
    # run 2 (after run 1 hit the threshold-grid edge 0.02; still selected on val only): stronger regularisation
    "win_pw1_l15": {"features": "win", "pos_weight": 1.0, "params": {"num_leaves": 15, "min_data_in_leaf": 500}},
    "win_pw1_l15_r200": {"features": "win", "pos_weight": 1.0,
                         "params": {"num_leaves": 15, "min_data_in_leaf": 500, "num_boost_round": 200}},
    "win_pw1_l31_ff5": {"features": "win", "pos_weight": 1.0,
                        "params": {"num_leaves": 31, "min_data_in_leaf": 100, "feature_fraction": 0.5,
                                   "lambda_l2": 10.0}},
}
# run 1 grid = configs/oil_eval.yaml selection grid [0.02..0.98]; run 2 extends it downwards (edge hit), val only
GRID = np.round(np.concatenate([[0.001, 0.002, 0.005, 0.01, 0.015], np.arange(0.02, 0.98 + 1e-9, 0.01)]), 3)


def cmd_train(a):
    cfg = load_cfg()
    comp = cfg["composition"]
    t0 = time.time()
    Xtr, ytr, _, str_ = load_split("train", comp["train"]["scenes"])
    Xva, yva, _, sva = load_split("val", comp["val"]["scenes"])
    print(f"[data] train {len(ytr)} px ({int(np.sum(ytr == OIL))} oil, {len(set(str_))} scenes); "
          f"val {len(yva)} px ({int(np.sum(yva == OIL))} oil, {len(set(sva))} scenes); {time.time() - t0:.0f}s",
          flush=True)
    assert not (set(str_) & set(sva)), "scene leak train/val"
    true_va = yva == OIL
    nan_va = np.isnan(Xva[:, :11]).any(1)
    all_names = feature_names("win")
    grid = GRID
    runs = {}
    variants = [v for v in VARIANTS if not a.only or v in a.only]
    for vname in variants:
        v = VARIANTS[vname]
        for seed in a.seeds:
            fn = feature_names(v["features"])
            fidx = [all_names.index(n) for n in fn]
            sel = sample_train(ytr, seed)
            t1 = time.time()
            bst = train_booster(Xtr[sel][:, fidx], ytr[sel], fn, v["params"], v["pos_weight"], seed)
            p = bst.predict(Xva[:, fidx], num_threads=THREADS).astype(np.float32)
            p[nan_va] = 0.0
            thr, _ = OM.best_threshold(true_va, p, grid)
            ev = evaluate(true_va, p, thr, sva)
            name = f"{vname}_s{seed}"
            runs[name] = {"variant": vname, "seed": seed, "threshold": thr, "val": ev,
                          "fp_by_class_val": fp_by_class(yva, p >= thr), "train_px": int(len(sel)),
                          "train_oil_px": int(np.sum(ytr[sel] == OIL)), "seconds": round(time.time() - t1, 1)}
            d = WEXP / name
            d.mkdir(parents=True, exist_ok=True)
            bst.save_model(str(d / "model.txt"))
            meta = {"model": "lgbm", "task": "binary", "target": "oil_spill (MADOS 6) vs rest",
                    "feature_level": v["features"], "features": fn, "channels": BANDS11,
                    "input": "reflectance 0..1 (trained on MADOS ACOLITE rhorc)", "classes": [0, 1], "md_class": 1,
                    "threshold": thr, "postprocess_min_px": 0, "seed": seed, "variant": v,
                    "val": ev, "eval_config": "configs/oil_eval.yaml",
                    "eval_composition_sha256": cfg["composition_sha256"], "experimental": True,
                    "note": "threshold chosen on MADOS val (oil_eval.yaml); MADOS test not read here"}
            (d / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
            pv = ev["pooled"]
            print(f"[{name}] val F1={pv['f1']:.3f} {ev['ci95']['f1']} P={pv['precision']:.3f} R={pv['recall']:.3f} "
                  f"IoU={pv['iou']:.3f} thr={thr:.2f} med scene F1={ev['median_oil_scene_f1']} "
                  f"({runs[name]['seconds']}s)", flush=True)
    # baseline OSI: sign + threshold on val
    best = None
    for sign in (1, -1):
        s = osi_score(Xva[:, :11], sign)
        fin = np.isfinite(s)
        g = np.unique(np.percentile(s[fin], np.arange(1, 100)))
        thr, m = OM.best_threshold(true_va, s, g)
        if best is None or m["f1"] > best[2]["f1"]:
            best = (sign, thr, m)
    sign, thr, _ = best
    s = osi_score(Xva[:, :11], sign)
    runs["baseline_osi"] = {"variant": "OSI=(B3+B4)/B2 threshold", "sign": sign, "threshold": float(thr),
                            "val": evaluate(true_va, s, thr, sva), "fp_by_class_val": fp_by_class(yva, s >= thr)}
    pv = runs["baseline_osi"]["val"]
    print(f"[baseline_osi] sign={sign} thr={thr:.4f} val F1={pv['pooled']['f1']:.3f} {pv['ci95']['f1']} "
          f"P={pv['pooled']['precision']:.3f} R={pv['pooled']['recall']:.3f} IoU={pv['pooled']['iou']:.3f}", flush=True)
    REP.mkdir(parents=True, exist_ok=True)
    prev = json.loads((REP / "val_runs.json").read_text(encoding="utf-8")) if (REP / "val_runs.json").is_file() else {}
    prev.update(runs)
    (REP / "val_runs.json").write_text(json.dumps(prev, indent=1, ensure_ascii=False), encoding="utf-8")
    lg = {k: r for k, r in prev.items() if k != "baseline_osi"}
    win = max(lg, key=lambda k: (lg[k]["val"]["pooled"]["f1"], -len(k)))
    print(f"[select] best on val: {win} F1={lg[win]['val']['pooled']['f1']:.3f}", flush=True)


def cmd_final(a):
    """Copy the chosen run (by val F1 or --run) to weights_exp/oil/final + baseline params."""
    import shutil

    runs = json.loads((REP / "val_runs.json").read_text(encoding="utf-8"))
    lg = {k: r for k, r in runs.items() if k != "baseline_osi"}
    # rule: variant with the best MEAN val F1 over its seeds (a max over single runs is optimistic), then seed 0
    by_v = collections.defaultdict(list)
    for k, r in lg.items():
        by_v[r["variant"]].append(r["val"]["pooled"]["f1"])
    means = {v: float(np.mean(f)) for v, f in by_v.items() if len(f) >= 3}
    best_v = max(means, key=means.get)
    name = a.run or f"{best_v}_s0"
    d = WEXP / "final"
    d.mkdir(parents=True, exist_ok=True)
    for f in ("model.txt", "meta.json"):
        shutil.copy2(WEXP / name / f, d / f)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    meta["selected_run"] = name
    meta["selected_by"] = ("variant with max mean pooled F1 over seeds 0,1,2 on MADOS val (configs/oil_eval.yaml), "
                           "seed 0; means: " + json.dumps({k: round(v, 4) for k, v in sorted(means.items())}))
    meta["val_seed_f1"] = {k: lg[k]["val"]["pooled"]["f1"] for k in lg if k.startswith(best_v + "_s")}
    meta["baseline_osi"] = {k: runs["baseline_osi"][k] for k in ("sign", "threshold")}
    (d / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[final] {name} -> {d}")


def cmd_test(a):
    cfg = load_cfg()
    if cfg.get("test_opened") and not a.again:
        raise SystemExit(f"test already opened: {cfg['test_opened']}")
    import lightgbm as lgb

    d = WEXP / "final"
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    Xte, yte, _, ste = load_split("test", cfg["composition"]["test"]["scenes"])
    true = yte == OIL
    all_names = feature_names("win")
    fidx = [all_names.index(n) for n in meta["features"]]
    bst = lgb.Booster(model_file=str(d / "model.txt"))
    p = bst.predict(Xte[:, fidx], num_threads=THREADS).astype(np.float32)
    p[np.isnan(Xte[:, :11]).any(1)] = 0.0
    res = {"opened_at": dt.datetime.now().isoformat(timespec="seconds"), "model": meta["selected_run"],
           "lgbm": {"threshold": meta["threshold"], "test": evaluate(true, p, meta["threshold"], ste),
                    "fp_by_class_test": fp_by_class(yte, p >= meta["threshold"])}}
    b = meta["baseline_osi"]
    s = osi_score(Xte[:, :11], b["sign"])
    res["baseline_osi"] = {**b, "test": evaluate(true, s, b["threshold"], ste),
                           "fp_by_class_test": fp_by_class(yte, s >= b["threshold"])}
    REP.mkdir(parents=True, exist_ok=True)
    (REP / "test.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    # record the opening (append-only field; composition hash unchanged)
    txt = CFG_PATH.read_text(encoding="utf-8")
    txt = txt.replace("test_opened: null", f"test_opened: '{res['opened_at']} by L101, once, model "
                                            f"{res['model']} + OSI baseline, frozen thresholds -> reports/oil/test.json'")
    CFG_PATH.write_text(txt, encoding="utf-8")
    for k in ("lgbm", "baseline_osi"):
        t = res[k]["test"]
        print(f"[test] {k}: F1={t['pooled']['f1']:.3f} {t['ci95']['f1']} P={t['pooled']['precision']:.3f} "
              f"R={t['pooled']['recall']:.3f} IoU={t['pooled']['iou']:.3f} ({t['n_oil_scenes']} oil scenes)")


# ============================================================================ inference on real scenes
def _scenes_live():
    out = []
    for p in sorted((ROOT / "data" / "live").glob("*/*/bands.tif")):
        d = p.parent
        out.append({"key": f"live.{d.parent.name}.{d.name}", "kind": "live", "dir": d})
    return out


def _scenes_pairs():
    out = []
    for p in sorted((ROOT / "out" / "l78_bands").glob("*.npz")):
        q = ROOT / "data" / "pairs" / "quality" / p.stem
        if (q / "meta.json").is_file():
            out.append({"key": f"pair.{p.stem}", "kind": "pair", "dir": q, "npz": p})
    return out


def _load_scene(s):
    import rasterio
    from rasterio.transform import Affine

    if s["kind"] == "live":
        d = s["dir"]
        with rasterio.open(d / "bands.tif") as src:
            x = src.read().astype(np.float32)
            names = list(src.descriptions)
            tr, crs = src.transform, src.crs.to_string()
        with rasterio.open(d / "water_mask.tif") as src:
            water = src.read(1).astype(bool)
        sj = json.loads((d / "scene.json").read_text(encoding="utf-8"))
        info = {"scene_id": sj.get("scene_id"), "date": sj.get("date"), "region": sj.get("region"),
                "source": f"Sentinel-2 L2A ({sj.get('source', '')})", "datetime": sj.get("datetime"),
                "water_rule": "data/live/.../water_mask.tif (SCL water, 2 px land / 5 px cloud buffer)",
                "cloud_frac": round(float(sj.get("crop_cloud_frac") or 0) + float(sj.get("crop_shadow_frac") or 0), 4)}
        with rasterio.open(d / "scl.tif") as src:
            scl = src.read(1)
        sea = ~np.isin(scl, (0, 4, 5)) & np.isfinite(x).all(0)  # not nodata / vegetation / bare soil (clouds counted)
        info["valid_water_frac"] = round(float(water.sum()) / max(1, int(sea.sum())), 4)
        return x, names, water, tr, crs, info
    z = np.load(s["npz"])
    enc = z["bands"].astype(np.float32)
    x = np.where(enc > 0, (enc - 1000) / 10000.0, np.nan).astype(np.float32)
    names = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"]
    m = json.loads((s["dir"] / "meta.json").read_text(encoding="utf-8"))
    with rasterio.open(s["dir"] / "quality.tif") as src:
        q = src.read(1)
        tr, crs = src.transform, src.crs.to_string()
    assert q.shape == x.shape[1:], (s["key"], q.shape, x.shape)
    info = {"scene_id": m.get("scene_id"), "date": str(m.get("scene_datetime", ""))[:10], "region": s["dir"].name,
            "source": f"Sentinel-2 {m.get('level', 'L2A')} ({m.get('source', '')})", "datetime": m.get("scene_datetime"),
            "water_rule": "data/pairs/quality/<dir>/quality.tif == 1 (valid water)"}
    sky = (q != 0) & (q != 2)  # not nodata, not land
    info["cloud_frac"] = round(float(np.isin(q, (3, 4)).sum()) / max(1, int(sky.sum())), 4)
    info["valid_water_frac"] = round(float((q == 1).sum()) / max(1, int(sky.sum())), 4)  # usable water / not land
    return x, names, q == 1, tr, crs, info


# Scene-level gate: the same max_cloud_frac 0.2 as the pairs quality config (configs/case_pairs.yaml decision) and the
# live-scene selection (crop cloud <= 0.2). Added after the first inference run: pair S3_HE460 transect01 (89 % cloud)
# gave 484 'spills' = 50 % of the remaining water between clouds (cloud edges / shadows) — not tuned on MADOS test.
MAX_CLOUD = 0.2
# Orchestrator 26.09 ~02:40: usable water < 50 % of the non-land part of the crop -> 'no estimate' (null), never '0'.
MIN_VALID_WATER = 0.5
# Scene-wide gate: > 5 % of the observed water flagged -> a scene-wide artefact (haze / glint / L2A-vs-rhorc shift), not
# a spill map (the same idea as the 'glint_scene' rule of pair_quality.py). MADOS train: oil covers <= 7.7 % of a 2.4 km
# crop in 99 % of oil crops; our crops are ~25 km. Set after seeing the first 10 live scenes (bali 2018-02-27: 31 %,
# bali 2026-04-28: 19 % of water under haze) — documented heuristic, not tuned on MADOS test.
MAX_SCENE_FRAC = 0.05
# min component = 15 px (0.0015 km²) ~ 5th percentile of MADOS-train oil annotation components (14.5 px; median 108 px)
MIN_PX = 15


def cmd_infer(a):
    from macroplastic.models.lgbm_predict import LGBMPredictor
    from macroplastic.oil import spills as SP

    d = WEXP / "final"
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    pred = LGBMPredictor(d, num_threads=THREADS, harmonize=None)
    thr = float(meta["threshold"])
    b = meta["baseline_osi"]
    test = json.loads((REP / "test.json").read_text(encoding="utf-8")) if (REP / "test.json").is_file() else None
    experimental = True  # set by the rule in docs/OIL.md (always experimental: domain shift + optics)
    OIL_DATA.mkdir(parents=True, exist_ok=True)
    scenes = (_scenes_live() if "live" in a.sources else []) + (_scenes_pairs() if "pairs" in a.sources else [])
    index = []
    for s in scenes:
        t0 = time.time()
        x, names, water, tr, crs, info = _load_scene(s)
        prob = pred.predict_proba(x, names)
        PROB_CACHE.mkdir(parents=True, exist_ok=True)
        np.save(PROB_CACHE / f"{s['key']}.npy", np.clip(np.round(prob * 255), 0, 255).astype(np.uint8))
        from macroplastic.oil.spills import component_mask
        finite = np.isfinite(x).all(0)
        n_nomask = component_mask((prob >= thr) & finite, a.min_px)[1]  # before any quality mask
        oil = (prob >= thr) & water
        idx_b = [names.index(k) for k in BANDS11]
        osi_s = osi_score(x[idx_b], b["sign"])
        osi_m = (osi_s >= b["threshold"]) & water
        low = info["valid_water_frac"] < MIN_VALID_WATER
        gated = (not low) and info["cloud_frac"] > MAX_CLOUD
        lab_pre, n_masked = component_mask(oil, a.min_px)  # after the quality/water mask, before scene gates
        pre_frac = float((lab_pre > 0).sum()) / max(1, int(water.sum()))
        wide = (not low) and (not gated) and pre_frac > MAX_SCENE_FRAC
        if low or gated or wide:  # scene-level gates (rules above): no estimate, the scene stays in the index
            oil = np.zeros_like(oil)
        feats = SP.spill_features(oil, tr, crs, {**info, "experimental": experimental, "model": "weights_exp/oil/final"},
                                  prob=prob, min_px=a.min_px, water_px=int(water.sum()))
        for f in feats:
            f["properties"]["scene_key"] = s["key"]
        (OIL_DATA / f"{s['key']}.geojson").write_text(json.dumps(SP.feature_collection(
            feats, scene_key=s["key"], unit_note=SP.UNIT_NOTE), ensure_ascii=False), encoding="utf-8")
        a_px = abs(tr.a * tr.e) / 1e6
        kept = int(sum(f["properties"]["n_px"] for f in feats))
        status = ("no_estimate_low_water" if low else "skipped_cloudy" if gated else
                  "suspect_scene_wide" if wide else "ok")
        ok = status == "ok"
        row = {"scene_key": s["key"], "kind": s["kind"], **{k: info[k] for k in ("scene_id", "date", "region", "source")},
               "water_px": int(water.sum()), "water_km2": round(float(water.sum()) * a_px, 4),
               "oil_px": kept if ok else None, "oil_km2": round(kept * a_px, 6) if ok else None,
               "oil_frac_water": round(kept / max(1, int(water.sum())), 8) if ok else None,
               "n_spills": len(feats) if ok else None,
               "n_spills_no_mask": int(n_nomask), "n_spills_after_mask": int(n_masked),
               "cloud_frac": info["cloud_frac"], "valid_water_frac": info["valid_water_frac"],
               "status": status,
               "status_reason": (f"пригодной воды {info['valid_water_frac']:.0%} < {MIN_VALID_WATER:.0%} — нет оценки"
                                 if low else
                                 f"облачность кадра {info['cloud_frac']:.2f} > {MAX_CLOUD}: края облаков и тени дают "
                                 "ложные «пятна» — нет оценки" if gated else
                                 (f"голова отметила {pre_frac:.1%} воды сцены (> {MAX_SCENE_FRAC:.0%}): сплошная дымка/блик/"
                                  "сдвиг L2A, а не карта пятен — нет оценки" if wide else None)),
               "flagged_frac_before_gate": round(pre_frac, 6),
               "osi_px": int(osi_m.sum()), "osi_frac_water": round(int(osi_m.sum()) / max(1, int(water.sum())), 8),
               "bbox": _bbox4326(tr, crs, x.shape[1:]), "seconds": round(time.time() - t0, 1),
               "file": f"data/case/oil/{s['key']}.geojson"}
        index.append(row)
        print(f"[infer] {s['key']}: {status}; water {row['water_km2']} km2 (valid {info['valid_water_frac']:.2f}), "
              f"oil {row['oil_km2']} km2, spills no-mask/mask/final {n_nomask}/{n_masked}/{row['n_spills']}; "
              f"{row['seconds']}s", flush=True)
    out = {"generated_at": dt.datetime.now().isoformat(timespec="seconds"),
           "class": SP.CLASS_ID, "class_label": SP.CLASS_LABEL, "unit": "km²", "unit_note": SP.UNIT_NOTE,
           "experimental": experimental, "model": "weights_exp/oil/final", "selected_run": meta.get("selected_run"),
           "threshold": thr, "min_px": a.min_px, "harmonize": "none", "max_cloud_frac": MAX_CLOUD,
           "max_scene_frac": MAX_SCENE_FRAC, "min_valid_water_frac": MIN_VALID_WATER,
           "metrics_val": meta["val"]["pooled"], "metrics_val_ci95": meta["val"]["ci95"],
           "metrics_test": test["lgbm"]["test"]["pooled"] if test else None,
           "metrics_test_ci95": test["lgbm"]["test"]["ci95"] if test else None,
           "baseline_osi": {**b, "val": json.loads((REP / "val_runs.json").read_text(encoding="utf-8"))["baseline_osi"]["val"]["pooled"],
                            "test": test["baseline_osi"]["test"]["pooled"] if test else None},
           "domain_note": "обучено на MADOS (ACOLITE rhorc из L1C); сцены сервиса — Sen2Cor L2A, без гармонизации; "
                          "проверки на реальных сценах нет (нет разметки нефти)",
           "scenes": index}
    (OIL_DATA / "index.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[infer] {len(index)} scenes -> {OIL_DATA / 'index.json'}")


def cmd_compact(a):
    """Упростить уже записанные полигоны (EPSG:4326, допуск ~5 м), не меняя area_km2/n_px (они по пикселям)."""
    from macroplastic.oil.spills import simplify_geom

    tot0 = tot1 = 0
    for p in sorted(OIL_DATA.glob("*.geojson")):
        tot0 += p.stat().st_size
        fc = json.loads(p.read_text(encoding="utf-8"))
        for f in fc["features"]:
            g = simplify_geom(f["geometry"], a.tol_deg)
            f["geometry"] = json.loads(json.dumps(g), parse_float=lambda x: round(float(x), 6))
        p.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tot1 += p.stat().st_size
    print(f"[compact] {tot0 / 1e6:.1f} MB -> {tot1 / 1e6:.1f} MB")


CONTROLS = {  # known spills seen by Sentinel-2 (qualitative positive control, NOT labelled pixels -> level C)
    "wakashio": {"lon": 57.74, "lat": -20.42, "size_m": 15000, "source": "planetary-computer",
                 "items": ["S2A_MSIL2A_20200811T062451_R091_T40KEC_20200814T190217",
                           "S2B_MSIL2A_20200816T062449_R091_T40KEC_20200818T144910"],
                 "ref": "MV Wakashio, Mauritius, grounding 25.07.2020, fuel oil leak from 06.08.2020 "
                        "(Rajendran et al. 2021, MethodsX 8:101327 — the OSI paper uses the same S2 scenes)"},
}


def cmd_control(a):
    """Положительный контроль на L2A: известный разлив (Wakashio) — видит ли голова/OSI пятно на снимке сервиса-типа."""
    import rasterio
    from macroplastic.live import stac
    from macroplastic.models.lgbm_predict import LGBMPredictor
    from macroplastic.oil import spills as SP

    d = WEXP / "final"
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    pred = LGBMPredictor(d, num_threads=THREADS, harmonize=None)
    thr = float(meta["threshold"]); b = meta["baseline_osi"]
    outd = OUT / "control"
    outd.mkdir(parents=True, exist_ok=True)
    res = []
    for name, c in CONTROLS.items():
        cl = stac.open_client(c["source"])
        for iid in c["items"]:
            it = cl.get_collection("sentinel-2-l2a").get_item(iid)
            epsg, bounds = stac.crop_bounds(it, c["lon"], c["lat"], c["size_m"])
            cache = outd / f"{name}_{iid[:30]}.npz"
            if cache.is_file():
                z = np.load(cache)
                x, scl = z["bands"], z["scl"]
                from rasterio.transform import from_origin
                tr = from_origin(bounds[0], bounds[3], 10.0, 10.0)
            else:
                crop = stac.read_crop(it, epsg, bounds, workers=THREADS)
                x, scl, tr = crop["bands"], crop["scl"], crop["transform"]
                np.savez_compressed(cache, bands=x, scl=scl)
            water = stac.water_mask(x, scl).astype(bool)
            prob = pred.predict_proba(x, stac.BANDS)
            oil = (prob >= thr) & water
            idx_b = [stac.BANDS.index(k) for k in BANDS11]
            osi_m = (osi_score(x[idx_b], b["sign"]) >= b["threshold"]) & water
            date = it.properties["datetime"][:10]
            info = {"scene_id": iid, "date": date, "region": f"control_{name}", "source": "Sentinel-2 L2A (planetary-computer)",
                    "experimental": True, "model": "weights_exp/oil/final"}
            feats = SP.spill_features(oil, tr, f"EPSG:{epsg}", info, prob=prob, min_px=a.min_px, water_px=int(water.sum()))
            key = f"control.{name}.{date}"
            (outd / f"{key}.geojson").write_text(json.dumps(SP.feature_collection(feats), ensure_ascii=False), encoding="utf-8")
            from PIL import Image
            rgb = (np.clip(np.nan_to_num(x[[3, 2, 1]]) / 0.15, 0, 1) ** 0.6 * 255).astype(np.uint8).transpose(1, 2, 0)
            o1 = rgb.copy(); o1[oil & (prob >= thr)] = [255, 0, 255]
            o2 = rgb.copy(); o2[osi_m] = [0, 255, 255]
            Image.fromarray(np.concatenate([rgb, o1, o2], 1)[::2, ::2]).save(outd / f"{key}.png")
            kept = int(sum(f["properties"]["n_px"] for f in feats))
            row = {"key": key, "water_km2": round(water.sum() / 1e4, 3), "oil_km2": round(kept / 1e4, 4),
                   "n_spills": len(feats), "oil_frac_water": round(kept / max(1, water.sum()), 5),
                   "osi_km2": round(osi_m.sum() / 1e4, 4), "cloud_frac_scl": round(float(np.isin(scl, (3, 8, 9, 10)).mean()), 4)}
            res.append(row)
            print(f"[control] {row}", flush=True)
    (REP / "control.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")


def _bbox4326(tr, crs, shape):
    from rasterio.warp import transform_bounds

    h, w = shape
    x0, y0 = tr * (0, h)
    x1, y1 = tr * (w, 0)
    return [round(v, 6) for v in transform_bounds(crs, "EPSG:4326", min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))]


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("split"); s.add_argument("--force", action="store_true")
    t = sub.add_parser("train"); t.add_argument("--seeds", type=int, nargs="*", default=[0])
    t.add_argument("--only", nargs="*", default=None)
    f = sub.add_parser("final"); f.add_argument("--run", default=None)
    te = sub.add_parser("test"); te.add_argument("--again", action="store_true", help="never (recorded)")
    i = sub.add_parser("infer"); i.add_argument("--sources", nargs="*", default=["live", "pairs"])
    i.add_argument("--min-px", type=int, default=MIN_PX)
    c = sub.add_parser("compact"); c.add_argument("--tol-deg", type=float, default=5e-5)
    k = sub.add_parser("control"); k.add_argument("--min-px", type=int, default=MIN_PX)
    a = ap.parse_args()
    {"control": cmd_control, "compact": cmd_compact, "split": cmd_split, "train": cmd_train, "final": cmd_final, "test": cmd_test, "infer": cmd_infer}[a.cmd](a)


if __name__ == "__main__":
    main()
