"""Visual labelling of detector objects on the real S2 L2A pair crops (jury-3 actions 2-3, O3 / T2).

  .venv\\Scripts\\python.exe scripts\\case\\pairs_visual_review.py sample     # stratified sample + blind contact sheets
  .venv\\Scripts\\python.exe scripts\\case\\pairs_visual_review.py score      # labels -> precision, CI, kappa, md/json

Visual labelling by image chips (NOT field truth), one annotator. Object sets:
  H = detector as in the pipeline (harmonize="water_median", scripts/case/pair_quality.py) - 975 objects on 22 crops
  N = the same LightGBM with harmonize=None on the same crops (5 objects)
Sample: all objects in observation strips (6) + all N objects + 100 H objects outside strips, stratified by crop
(min(n, 5) per crop, the rest proportional to size, rng 78). Sheets show only an anonymous id (no scene, no rule type,
no P), order shuffled. Repeatability: 20 random labelled objects are re-drawn under new ids and re-labelled blind.
Inputs: reports/case_pairs/detector_review.json, out/l78_bands (pairs_detector_review.py), data/pairs/quality/*.
Labels are typed by the annotator into out/l78b/labels_pass1.csv and out/l78b/labels_pass2.csv (vid,class,confidence,note).
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "case"))
import pairs_detector_review as R  # noqa: E402

OUT = ROOT / "reports" / "case_pairs"
WORK = ROOT / "out" / "l78b"
SHEETS = OUT / "visual_sheets"
CLASSES = ["accumulation", "foam_whitecap", "glint", "cloud", "ship_wake", "seam_artifact", "unclear"]
CLASS_RU = {"accumulation": "вероятное скопление / плавучий материал", "foam_whitecap": "пена / барашки", "glint": "блик",
            "cloud": "облако / край облака", "ship_wake": "судно / кильватер", "seam_artifact": "шов / артефакт",
            "unclear": "неясно"}
N_OUT = 100
SEED = 78
CHIP, CTX = 64, 256


def load_review():
    return json.loads((OUT / "detector_review.json").read_text(encoding="utf-8"))


def crop_ctx(d: str):
    """lab (harmonised, pipeline), lab_none (harmonize=None), bands, qa, strip of one crop."""
    import rasterio
    from macroplastic.live import stac
    from macroplastic.models.lgbm_predict import load_predictor
    base = R.QDIR / d
    m = json.loads((base / "meta.json").read_text(encoding="utf-8"))
    with rasterio.open(base / "prob.tif") as ds:
        prob = ds.read(1).astype(np.float32) / 255.0
        tr = ds.transform
    with rasterio.open(base / "quality.tif") as ds:
        qa = ds.read(1)
    bands, scl = R.load_bands(base)
    strip, _ = R.strip_mask(m, qa.shape, tr)
    lab, n, _, _ = R.rebuild_detections(prob, qa, bands, m)
    if "none" not in _P:
        _P["none"] = load_predictor(ROOT / m["config"]["detector"]["weights"], harmonize=None)
    p0 = np.nan_to_num(_P["none"].predict_proba(bands, stac.BANDS, water_mask=(scl == 6)), nan=0.0)
    lab0, n0, _, _ = R.rebuild_detections((np.clip(np.round(p0 * 255), 0, 255) / 255.0).astype(np.float32), qa, bands, m)
    return dict(meta=m, lab=lab, n=n, lab0=lab0, n0=n0, bands=bands, qa=qa, strip=strip, prob=prob, prob0=p0)


_P: dict = {}


def objects_of(lab, n):
    from scipy import ndimage
    if n == 0:
        return []
    sl = ndimage.find_objects(lab)
    idx = np.arange(1, n + 1)
    com = np.array(ndimage.center_of_mass(lab > 0, lab, idx)).reshape(-1, 2)
    npx = np.bincount(lab.ravel(), minlength=n + 1)[1:]
    return [dict(k=k + 1, bbox=[sl[k][0].start, sl[k][1].start, sl[k][0].stop, sl[k][1].stop],
                 row=float(com[k, 0]), col=float(com[k, 1]), n_px=int(npx[k])) for k in range(n)]


# ------------------------------------------------------------------------------------------ sample

def sample():
    rev = load_review()
    rng = np.random.default_rng(SEED)
    rows = []
    dirs = [c["dir"] for c in rev["crops"]]
    ctxs = {}
    for d in dirs:
        c = crop_ctx(d)
        oh = objects_of(c["lab"], c["n"])
        o0 = objects_of(c["lab0"], c["n0"])
        if not oh and not o0:
            continue
        ctxs[d] = c
        for o in oh:
            o.update(dir=d, set="H", in_strip=bool(c["strip"][c["lab"] == o["k"]].any()),
                     prob_max=round(float(c["prob"][c["lab"] == o["k"]].max()), 3))
        for o in o0:
            px = c["lab0"] == o["k"]
            kh = np.unique(c["lab"][px & (c["lab"] > 0)])
            o.update(dir=d, set="N", in_strip=bool(c["strip"][px].any()), prob_max=round(float(c["prob0"][px].max()), 3),
                     same_as_H=[int(x) for x in kh])
        rows.append((d, oh, o0))
    # strata: H outside strip by crop
    out_by = {d: [o for o in oh if not o["in_strip"]] for d, oh, _ in rows}
    alloc = {d: min(len(v), 5) for d, v in out_by.items()}
    rest = N_OUT - sum(alloc.values())
    left = {d: len(v) - alloc[d] for d, v in out_by.items()}
    tot = sum(left.values())
    extra = {d: int(np.floor(rest * left[d] / tot)) for d in left} if tot else {}
    k = rest - sum(extra.values())
    for d in sorted(left, key=lambda d: -(rest * left[d] / tot - extra[d]) if tot else 0)[:k]:
        extra[d] += 1
    chosen = []
    for d, v in out_by.items():
        m_ = alloc[d] + extra.get(d, 0)
        pick = rng.choice(len(v), size=min(m_, len(v)), replace=False) if v else []
        for i in sorted(pick):
            chosen.append(dict(v[i], stratum=f"H_out:{d}", stratum_N=len(v), stratum_n=int(min(m_, len(v)))))
    for d, oh, o0 in rows:
        for o in oh:
            if o["in_strip"]:
                chosen.append(dict(o, stratum=f"H_in:{d}", stratum_N=None, stratum_n=None))
        for o in o0:
            chosen.append(dict(o, stratum=f"N:{d}", stratum_N=None, stratum_n=None))
    # one visual id per location: N objects that coincide with a sampled H object share its label
    for o in chosen:
        o["object_id"] = f"{o['dir']}:{o['set']}{o['k']:04d}"
    hkeys = {(o["dir"], o["k"]): o for o in chosen if o["set"] == "H"}
    order = rng.permutation(len(chosen))
    vid = {}
    n_vid = 0
    for i in order:
        o = chosen[i]
        same = [hkeys[(o["dir"], k_)] for k_ in o.get("same_as_H", []) if (o["dir"], k_) in hkeys] if o["set"] == "N" else []
        if same and same[0].get("vid"):
            o["vid"] = same[0]["vid"]
            continue
        n_vid += 1
        o["vid"] = f"V{n_vid:03d}"
        vid[o["vid"]] = o
    # second pass: all N objects that share with an H object get the H vid (if H got it later)
    for o in chosen:
        if o["set"] == "N":
            for k_ in o.get("same_as_H", []):
                h = hkeys.get((o["dir"], k_))
                if h and h["vid"] != o["vid"]:
                    vid.pop(o["vid"], None)
                    o["vid"] = h["vid"]
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "sample.json").write_text(json.dumps(chosen, ensure_ascii=False, indent=1), encoding="utf-8")
    # repeat set: 20 random labelled locations under new ids
    vids = sorted({o["vid"] for o in chosen})
    rep = sorted(rng.choice(vids, size=20, replace=False).tolist(), key=lambda _: rng.random())
    rep_map = {f"W{i + 1:03d}": v for i, v in enumerate(rep)}
    (WORK / "repeat_map.json").write_text(json.dumps(rep_map, indent=1), encoding="utf-8")
    SHEETS.mkdir(parents=True, exist_ok=True)
    for p in list(SHEETS.glob("*.png")) + list(SHEETS.glob("*.jpg")):
        p.unlink()
    loc = {o["vid"]: o for o in chosen}
    items = [(v, loc[v]) for v in sorted(loc)]
    render_sheets(items, ctxs, "sheet")
    render_sheets([(w, loc[v]) for w, v in rep_map.items()], ctxs, "repeat")
    print(f"sample: {len(chosen)} objects, {len(loc)} locations; H out {sum(o['set'] == 'H' and not o['in_strip'] for o in chosen)}, "
          f"H in {sum(o['set'] == 'H' and o['in_strip'] for o in chosen)}, N {sum(o['set'] == 'N' for o in chosen)}")


def _stretch_pair(b, win_ctx, idx):
    out = []
    for i in idx:
        x = np.nan_to_num(b[i][win_ctx])
        out.append((np.percentile(x, 1), np.percentile(x, 99.7)))
    return out


def _img(b, win, idx, lims):
    ch = [np.clip((np.nan_to_num(b[i][win]) - lo) / max(hi - lo, 1e-6), 0, 1) for i, (lo, hi) in zip(idx, lims)]
    return np.dstack(ch) ** (1 / 1.4)


def render_sheets(items, ctxs, prefix, per=10):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from scipy import ndimage
    for s0 in range(0, len(items), per):
        chunk = items[s0:s0 + per]
        fig, axes = plt.subplots(len(chunk), 4, figsize=(14, 3.5 * len(chunk)))
        axes = np.atleast_2d(axes)
        for (vid, o), ax in zip(chunk, axes):
            c = ctxs[o["dir"]]
            b, lab = c["bands"], (c["lab"] if o["set"] == "H" else c["lab0"])
            H, W = lab.shape
            r, cc = int(round(o["row"])), int(round(o["col"]))
            def win(h):
                y0 = int(np.clip(r - h // 2, 0, max(H - h, 0))); x0 = int(np.clip(cc - h // 2, 0, max(W - h, 0)))
                return (slice(y0, y0 + h), slice(x0, x0 + h)), y0, x0
            wc, cy0, cx0 = win(CTX)
            w6, y0, x0 = win(CHIP)
            lims_rgb = _stretch_pair(b, wc, (3, 2, 1))
            lims_sw = _stretch_pair(b, wc, (10, 7, 3))
            rgb = _img(b, w6, (3, 2, 1), lims_rgb)
            obj = lab[w6] == o["k"]
            ring = ndimage.binary_dilation(obj, iterations=2) & ~ndimage.binary_dilation(obj, iterations=1)
            ax[0].imshow(rgb, interpolation="nearest"); ax[0].set_title(f"{vid}  RGB 64 px (640 м)", fontsize=10)
            rgb2 = rgb.copy(); rgb2[ring] = (1, 0, 1)
            ax[1].imshow(rgb2, interpolation="nearest"); ax[1].set_title("то же + кольцо вокруг объекта", fontsize=9)
            ax[2].imshow(_img(b, w6, (10, 7, 3), lims_sw), interpolation="nearest"); ax[2].set_title("SWIR B11 B8 B4, 64 px", fontsize=9)
            ax[3].imshow(_img(b, wc, (3, 2, 1), lims_rgb), interpolation="nearest")
            ax[3].add_patch(Rectangle((x0 - cx0 - 0.5, y0 - cy0 - 0.5), CHIP, CHIP, fill=False, ec="#ff00ff", lw=1))
            ax[3].set_title("контекст RGB 256 px (2.56 км)", fontsize=9)
            for a_ in ax:
                a_.set_xticks([]); a_.set_yticks([])
        fig.tight_layout()
        fig.savefig(SHEETS / f"{prefix}_{s0 // per + 1:02d}.jpg", dpi=62, pil_kwargs={"quality": 85})
        plt.close(fig)


# ------------------------------------------------------------------------------------------ score

def read_labels(p: Path) -> dict:
    out = {}
    with open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["vid"].strip()] = dict(cls=r["class"].strip(), conf=r["confidence"].strip(), note=r.get("note", "").strip())
            assert out[r["vid"].strip()]["cls"] in CLASSES, r
    return out


def wilson(k, n, z=1.96):
    if n == 0:
        return [None, None]
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(float(c - h), 4), round(float(c + h), 4)]


def kappa(a, b):
    labs = sorted(set(a) | set(b))
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(k_) / n) * (b.count(k_) / n) for k_ in labs)
    return (round((po - pe) / (1 - pe), 4) if pe < 1 else None), round(po, 4)  # None: one class only, kappa undefined


def score():
    chosen = json.loads((WORK / "sample.json").read_text(encoding="utf-8"))
    L1 = read_labels(WORK / "labels_pass1.csv")
    L2 = read_labels(WORK / "labels_pass2.csv") if (WORK / "labels_pass2.csv").is_file() else {}
    rep_map = json.loads((WORK / "repeat_map.json").read_text(encoding="utf-8"))
    rev = load_review()
    rtype = {(d, o["k"]): o["type"] for d, ob in rev["objects"].items() for o in ob}
    dec = {c["dir"]: (c["decision"], c["reason"]) for c in rev["crops"]}
    # labels csv
    rows = []
    for o in chosen:
        lb = L1[o["vid"]]
        rows.append(dict(object_id=o["object_id"], visual_id=o["vid"], scene=o["dir"], set=o["set"],
                         harmonize=("water_median" if o["set"] == "H" else "none"), in_strip=o["in_strip"],
                         stratum=o["stratum"], bbox=" ".join(map(str, o["bbox"])), n_px=o["n_px"], prob_max=o["prob_max"],
                         pair_decision=" ".join(x for x in dec[o["dir"]] if x),
                         rule_type=rtype.get((o["dir"], o["k"])) if o["set"] == "H" else None,
                         **{"class": lb["cls"], "confidence": lb["conf"], "note": lb["note"]}))
    cols = ["object_id", "visual_id", "scene", "set", "harmonize", "in_strip", "stratum", "bbox", "n_px", "prob_max",
            "pair_decision", "rule_type", "class", "confidence", "note"]
    with open(OUT / "visual_labels.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    rng = np.random.default_rng(0)
    pos = lambda r: r["class"] == "accumulation"  # noqa: E731
    # H outside strip: stratified estimate over the 969 outside-strip objects
    ho = [r for r in rows if r["set"] == "H" and not r["in_strip"]]
    strata = {}
    for r, o in zip(rows, chosen):
        if r["set"] == "H" and not r["in_strip"]:
            strata.setdefault(o["stratum"], dict(N=o["stratum_N"], y=[]))["y"].append(1.0 if pos(r) else 0.0)
    Ntot = sum(s["N"] for s in strata.values())

    def strat_est(st):
        return sum(s["N"] * np.mean(s["y"]) for s in st.values()) / sum(s["N"] for s in st.values())
    est = strat_est(strata)
    boots = []
    for _ in range(2000):
        st = {k_: dict(N=s["N"], y=rng.choice(s["y"], size=len(s["y"]), replace=True)) for k_, s in strata.items()}
        boots.append(strat_est(st))
    ci_strat = [round(float(np.percentile(boots, 2.5)), 4), round(float(np.percentile(boots, 97.5)), 4)]
    # cluster bootstrap by scene (resample scenes with their weights)
    keys = list(strata)
    cb = []
    for _ in range(2000):
        pick = rng.choice(len(keys), size=len(keys), replace=True)
        st = {f"{i}_{j}": strata[keys[j]] for i, j in enumerate(pick)}
        cb.append(strat_est(st))
    ci_scene = [round(float(np.percentile(cb, 2.5)), 4), round(float(np.percentile(cb, 97.5)), 4)]
    k_raw = sum(pos(r) for r in ho)

    def dist(rs):
        return {c_: sum(r["class"] == c_ for r in rs) for c_ in CLASSES}

    def weighted_dist():
        out = {}
        for c_ in CLASSES:
            st = {k_: dict(N=s["N"], y=[1.0 if r["class"] == c_ else 0.0 for r, o in zip(rows, chosen)
                                          if o["stratum"] == k_ and r["set"] == "H" and not r["in_strip"]]) for k_, s in strata.items()}
            out[c_] = round(strat_est(st), 4)
        return out
    hin = [r for r in rows if r["set"] == "H" and r["in_strip"]]
    nset = [r for r in rows if r["set"] == "N"]
    # all H population (in + out): out estimate weighted by 969, in exact
    n_in = len(hin)
    est_all = (est * Ntot + sum(pos(r) for r in hin)) / (Ntot + n_in)
    by_scene = {}
    for r in rows:
        if r["set"] == "H":
            by_scene.setdefault(r["scene"], []).append(r)
    res = dict(
        created=time.strftime("%Y-%m-%dT%H:%M:%S"),
        protocol=("визуальная разметка по вырезкам (не полевая), одним аннотатором (ИИ-агент, осмотр вырезок RGB и SWIR 64 px и "
                  "контекста 256 px); листы без сцены, типа по правилам и P; порядок случайный; положительный класс = "
                  "«вероятное скопление / плавучий материал»"),
        classes=CLASS_RU, n_labelled_objects=len(rows), n_locations=len({r["visual_id"] for r in rows}),
        H_out=dict(population=Ntot, n=len(ho), k_accumulation=int(k_raw), precision_stratified=round(float(est), 4),
                   ci95_stratified_bootstrap=ci_strat, ci95_scene_bootstrap=ci_scene,
                   precision_unweighted=round(k_raw / len(ho), 4), ci95_wilson_unweighted=wilson(k_raw, len(ho)),
                   class_share_stratified=weighted_dist(), class_counts_sample=dist(ho)),
        H_in_strip=dict(n=n_in, k_accumulation=int(sum(pos(r) for r in hin)), ci95_wilson=wilson(sum(pos(r) for r in hin), n_in),
                        classes={r["object_id"]: r["class"] for r in hin}),
        H_all=dict(population=Ntot + n_in, precision_est=round(float(est_all), 4)),
        N_none=dict(n=len(nset), k_accumulation=int(sum(pos(r) for r in nset)), ci95_wilson=wilson(sum(pos(r) for r in nset), len(nset)),
                    classes={r["object_id"]: r["class"] for r in nset},
                    note="без гармонизации детектор на 22 вырезках даёт только эти объекты; разметка — те же места, что у H, если совпадают"),
        by_scene={d: dict(n=len(v), k_accumulation=int(sum(pos(r) for r in v)), classes=dist(v)) for d, v in by_scene.items()},
        rule_vs_visual=_confusion([r for r in rows if r["set"] == "H"]),
    )
    if L2:
        a = [L1[v]["cls"] for w_, v in rep_map.items() if w_ in L2]
        b = [L2[w_]["cls"] for w_ in rep_map if w_ in L2]
        kap, po = kappa(a, b)
        ab = [x == "accumulation" for x in a]; bb = [x == "accumulation" for x in b]
        kap2, po2 = kappa(ab, bb)
        res["repeatability"] = dict(n=len(a), kappa_7class=kap, agreement_7class=po, kappa_binary=kap2, agreement_binary=po2,
                                    pairs=[dict(repeat_id=w_, visual_id=v, first=L1[v]["cls"], second=L2[w_]["cls"])
                                           for w_, v in rep_map.items() if w_ in L2],
                                    protocol="20 случайных мест из разметки, новые номера и новый порядок, переразметка без доступа к первой")
    res["final_numbers_block"] = final_numbers_block(res)
    (OUT / "visual_review.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k_: res[k_] for k_ in ("H_out", "H_in_strip", "N_none", "repeatability") if k_ in res}, ensure_ascii=False, indent=1))


def final_numbers_block(res) -> dict:
    """Keys proposed for reports/final_numbers.json -> case.pairs_detector (added by the owner of final_numbers.py)."""
    rev = load_review()
    T = rev["summary"]["totals"]
    hv_p = OUT / "harmonize_val.json"
    hv = json.loads(hv_p.read_text(encoding="utf-8")) if hv_p.is_file() else None
    blk = {
        "source": "scripts/case/pairs_detector_review.py, scripts/case/pairs_visual_review.py, scripts/case/marida_harmonize_val.py",
        "l2a_crops": len(rev["crops"]),
        "objects_water_median": T["n_obj"],
        "objects_in_strips": T["n_in_strip"],
        "objects_harmonize_none": sum(((c.get("no_harmonize") or {}).get("None") or {}).get("n_obj", 0) for c in rev["crops"]),
        "rule_false_share": T["false_share_n"],
        "rule_false_share_without_he460_t03": rev["summary"]["without_he460_t03"]["false_share_n"],
        "visual_protocol": "визуальная разметка по вырезкам (не полевая), один аннотатор (ИИ-агент)",
        "visual_n_labelled": res["n_labelled_objects"],
        "visual_precision_out_strip": res["H_out"]["precision_stratified"],
        "visual_precision_out_strip_ci95": res["H_out"]["ci95_stratified_bootstrap"],
        "visual_precision_out_strip_wilson_unweighted": res["H_out"]["ci95_wilson_unweighted"],
        "visual_class_share_out_strip": res["H_out"]["class_share_stratified"],
        "visual_in_strips": f"{res['H_in_strip']['k_accumulation']}/{res['H_in_strip']['n']}",
        "visual_harmonize_none": f"{res['N_none']['k_accumulation']}/{res['N_none']['n']}",
    }
    if "repeatability" in res:
        rp = res["repeatability"]
        blk.update(visual_repeat_n=rp["n"], visual_kappa_7class=rp["kappa_7class"], visual_agreement_7class=rp["agreement_7class"],
                   visual_kappa_binary=rp["kappa_binary"])
    if hv:
        v, pb = hv["variants"], hv["paired_bootstrap"]
        def f1(k):
            x = v[k]
            return x.get("f1_md", x.get("f1"))
        blk.update(marida_val_f1_none=f1("none"), marida_val_f1_per_scene=f1("per_scene"), marida_val_f1_per_patch=f1("per_patch"),
                   marida_val_dF1_per_scene=pb["per_scene_minus_none"], marida_val_dF1_per_patch=pb["per_patch_minus_none"],
                   marida_val_test_used=False)
    return blk


SHORT = {"accumulation": "СКОПЛ", "foam_whitecap": "пена", "glint": "блик", "cloud": "облако", "ship_wake": "судно",
         "seam_artifact": "шов", "unclear": "неясно"}


def contact():
    """One labelled contact sheet: RGB chips 64 px of all labelled locations, grouped by class (after labelling)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    chosen = json.loads((WORK / "sample.json").read_text(encoding="utf-8"))
    L1 = read_labels(WORK / "labels_pass1.csv")
    ctxs = {d: crop_ctx(d) for d in sorted({o["dir"] for o in chosen})}
    items = sorted(chosen, key=lambda o: (CLASSES.index(L1[o["vid"]]["cls"]), o["vid"]))
    ncol = 12
    nrow = int(np.ceil(len(items) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(ncol * 1.35, nrow * 1.55))
    for ax in axes.ravel():
        ax.axis("off")
    for o, ax in zip(items, axes.ravel()):
        c = ctxs[o["dir"]]
        b = c["bands"]
        H, W = b.shape[1:]
        r, cc = int(round(o["row"])), int(round(o["col"]))
        y0 = int(np.clip(r - CTX // 2, 0, max(H - CTX, 0))); x0 = int(np.clip(cc - CTX // 2, 0, max(W - CTX, 0)))
        wc = (slice(y0, y0 + CTX), slice(x0, x0 + CTX))
        y1 = int(np.clip(r - CHIP // 2, 0, max(H - CHIP, 0))); x1 = int(np.clip(cc - CHIP // 2, 0, max(W - CHIP, 0)))
        w6 = (slice(y1, y1 + CHIP), slice(x1, x1 + CHIP))
        ax.imshow(_img(b, w6, (3, 2, 1), _stretch_pair(b, wc, (3, 2, 1))), interpolation="nearest")
        lb = L1[o["vid"]]["cls"]
        tag = ("*" if o["in_strip"] else "") + ("N" if o["set"] == "N" else "")
        ax.set_title(f"{o['vid']}{tag} {SHORT[lb]}", fontsize=7, color=("#c00000" if lb == "accumulation" else "black"))
    fig.suptitle("Визуальная разметка по вырезкам (не полевая), один аннотатор. RGB 64 px (640 м), объект в центре. "
                 "* — в полосе обследования, N — объект детектора без гармонизации", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "visual_contact_sheet.jpg", dpi=90, pil_kwargs={"quality": 85})
    plt.close(fig)


def _confusion(rs):
    out = {}
    for r in rs:
        out.setdefault(r["rule_type"] or "-", {}).setdefault(r["class"], 0)
        out[r["rule_type"] or "-"][r["class"]] += 1
    return out


if __name__ == "__main__":
    {"sample": sample, "score": score, "contact": contact}[sys.argv[1]]()
