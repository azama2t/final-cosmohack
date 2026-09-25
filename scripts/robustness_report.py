"""Robustness checks of inference.py and the lgbm / fdi_rule predictors (lane L24).

    .venv\\Scripts\\python.exe scripts\\robustness_report.py [--out reports/robustness.md] [--work out/robustness]

Data: 8 MARIDA *val* patches that contain Marine Debris labels (never test) and one 512x512 crop
of a live L2A scene (data/live/manila/2019-05-13, the most "water" 512x512 window by water_mask.tif).
Everything synthetic is written under --work (gitignored out/). The same functions are used by
tests/test_robustness.py (run_checks() -> list of case dicts).

Items (task L24):
 1 empty chip (all NaN / all 0)          2 synthetic clouds on half of the chip
 3 sun glint stripes (+B8/B11)            4 radiometric noise / scale / offset (F1 on labelled px)
 5 other sizes                            6 other dtypes (uint16 DN, float64, L2A DN with +1000 offset)
 7 extra / permuted / missing channels    8 broken tif / non-tif files in the folder
 9 paths with spaces, Cyrillic, Windows-reserved names (aux.tif)
Every case: {'item', 'case', 'expect', 'result', 'status' PASS|WARN|FAIL, 'fix' (for FAIL/WARN)}.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
for _p in (str(REPO / "src"), str(REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

MARIDA = Path(os.environ.get("MARIDA_ROOT", REPO / "data" / "MARIDA"))
LIVE_SCENE = REPO / "data" / "live" / "manila" / "2019-05-13"
WORK = REPO / "out" / "robustness"
#: MARIDA val patches with >30 Marine Debris pixels (val split only; test is never read)
VAL_PATCHES = ["14-3-20_18QYF_3", "14-3-20_18QYF_4", "18-9-20_16PCC_15", "18-9-20_16PCC_27",
               "18-9-20_16PCC_39", "29-12-20_18QYF_1", "29-12-20_18QYF_3", "29-12-20_18QYF_8"]
MARIDA_NAMES = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
L2A_NAMES = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"]

# grading thresholds (documented in reports/robustness.md)
FP_PASS, FP_WARN = 0.001, 0.01          # share of perturbed pixels newly flagged as debris
DF1_PASS, DF1_WARN = 0.05, 0.15         # |F1 change| on labelled val pixels
DCOUNT_WARN = 0.5                       # relative change in number of detections -> WARN above


# ------------------------------------------------------------------------------------------ helpers

def _case(item, case, expect, result, status, fix=""):
    return {"item": item, "case": case, "expect": expect, "result": result, "status": status, "fix": fix}


def _grade_fp(fp: float) -> str:
    return "PASS" if fp <= FP_PASS else ("WARN" if fp <= FP_WARN else "FAIL")


def write_tif(path, arr, descriptions=None, dtype=None, crs="EPSG:32616", transform=None, nodata=None):
    """(C,H,W) -> GeoTIFF (path may contain reserved names: goes through io.win_path)."""
    import rasterio
    from rasterio.transform import from_origin

    from macroplastic.io import win_path

    a = np.asarray(arr)
    dtype = dtype or str(a.dtype)
    p = win_path(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    prof = dict(driver="GTiff", count=a.shape[0], height=a.shape[1], width=a.shape[2], dtype=dtype,
                crs=crs, transform=transform or from_origin(300000, 1800000, 10, 10))
    if nodata is not None:
        prof["nodata"] = nodata
    with rasterio.open(p, "w", **prof) as dst:
        dst.write(a.astype(dtype))
        if descriptions:
            for i, d in enumerate(descriptions, 1):
                dst.set_band_description(i, d)
    return Path(path)


def read_out(path) -> np.ndarray:
    from macroplastic.io import read_raster

    return read_raster(path, dtype=None)[0][0]


def run_cli(data_dir, out_dir, model="lgbm", extra=()) -> tuple[int, str]:
    """inference.py in a subprocess -> (exit code, stdout+stderr)."""
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"), CUDA_VISIBLE_DEVICES="", PYTHONIOENCODING="utf-8")
    cmd = [sys.executable, str(REPO / "inference.py"), "--data-dir", str(data_dir), "--output", str(out_dir),
           "--device", "cpu", "--model", model, *extra]
    r = subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True, timeout=300)
    txt = (r.stdout + r.stderr).decode("utf-8", "replace")
    return r.returncode, txt


def _last_err(txt: str) -> str:
    lines = [ln.strip() for ln in txt.splitlines() if "ERROR" in ln or "WARNING" in ln]
    return (" | ".join(lines))[:300] or "-"


def _fresh(d: Path) -> Path:
    from macroplastic.io import win_path

    if d.exists():
        shutil.rmtree(win_path(d), ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------------------------------ context

class Ctx:
    """Loaded predictors, val patches, live crop, work dir."""

    def __init__(self, work: Path = WORK, n_val: int = len(VAL_PATCHES)):
        from macroplastic.io import read_marida_patch
        from macroplastic.models import get_predictor

        self.work = _fresh(Path(work))
        self.lgbm = get_predictor("lgbm", fallback=False, device="cpu")
        self.fdi = get_predictor("fdi_rule")
        self.preds = {"lgbm": self.lgbm, "fdi_rule": self.fdi}
        self.val = []
        for pid in VAL_PATCHES[:n_val]:
            d = read_marida_patch(MARIDA, pid)
            self.val.append((pid, d["image"].astype(np.float32), d["cl"], d["profile"]))
        self.live, self.live_profile, self.live_window = self._live_crop()
        self._base = {}

    @staticmethod
    def _live_crop(size=512):
        import rasterio
        from rasterio.windows import Window

        with rasterio.open(LIVE_SCENE / "water_mask.tif") as s:
            wm = s.read(1) > 0
        H, W = wm.shape
        best, by, bx = -1.0, 0, 0
        for y in range(0, H - size + 1, 128):
            for x in range(0, W - size + 1, 128):
                f = wm[y:y + size, x:x + size].mean()
                if f > best:
                    best, by, bx = f, y, x
        with rasterio.open(LIVE_SCENE / "bands.tif") as s:
            win = Window(bx, by, size, size)
            arr = s.read(window=win).astype(np.float32)
            prof = dict(crs=s.crs, transform=s.window_transform(win))
        return arr, prof, (by, bx, float(best))

    def base(self, model: str):
        """Baseline probabilities: list for val patches + live crop."""
        if model not in self._base:
            p = self.preds[model]
            self._base[model] = ([p.predict_proba(a, MARIDA_NAMES) for _, a, _, _ in self.val],
                                 p.predict_proba(self.live, L2A_NAMES))
        return self._base[model]


# ------------------------------------------------------------------------------------------ 1 empty

def check_empty(ctx: Ctx) -> list[dict]:
    out = []
    chips = {"nan": np.full((11, 256, 256), np.nan, np.float32), "zero": np.zeros((11, 256, 256), np.float32)}
    for m, p in ctx.preds.items():
        for k, a in chips.items():
            try:
                q = p.predict_proba(a, MARIDA_NAMES)
                mx = float(np.nanmax(q))
                ok = mx < 0.5 / 255 and np.isfinite(q).all()
                out.append(_case(1, f"1.{m}.{k}.direct", "no exception, prob == 0 (<0.5/255)",
                                 f"max prob {mx:.2e}", "PASS" if ok else "FAIL"))
            except Exception as e:  # noqa: BLE001
                out.append(_case(1, f"1.{m}.{k}.direct", "no exception, prob == 0", f"{type(e).__name__}: {e}", "FAIL"))
    # half-NaN val chip: NaN half must be 0, the other half unchanged
    pid, a, cl, _ = ctx.val[0]
    b = a.copy()
    b[:, :, :128] = np.nan
    q = ctx.lgbm.predict_proba(b, MARIDA_NAMES)
    ok = float(q[:, :128].max()) == 0.0
    out.append(_case(1, "1.lgbm.half_nan.direct", "NaN half -> prob 0", f"max prob on NaN half {q[:, :128].max():.3g}",
                     "PASS" if ok else "FAIL"))
    # CLI: both models, folder with nan.tif + zero.tif (+ uint16 all-zero = typical L2A nodata)
    d = _fresh(ctx.work / "1_empty" / "in")
    write_tif(d / "nan.tif", chips["nan"])
    write_tif(d / "zero.tif", chips["zero"])
    write_tif(d / "zero_u16.tif", np.zeros((12, 256, 256)), dtype="uint16", nodata=0)
    for m in ("lgbm", "fdi_rule"):
        o = _fresh(ctx.work / "1_empty" / f"out_{m}")
        code, txt = run_cli(d, o, m)
        res, ok = [], code == 0
        for stem in ("nan", "zero", "zero_u16"):
            fp, fm = o / f"{stem}_prob.tif", o / f"{stem}_mask.tif"
            if fp.exists() and fm.exists():
                pm, mm = int(read_out(fp).max()), int(read_out(fm).max())
                res.append(f"{stem}: max prob_u8 {pm}, mask {mm}")
                ok &= pm == 0 and mm == 0
            else:
                res.append(f"{stem}: no output")
                ok = False
        out.append(_case(1, f"1.{m}.cli", "exit 0, *_prob.tif == 0, *_mask.tif == 0 for NaN / 0 / uint16-0 chips",
                         f"exit {code}; " + "; ".join(res), "PASS" if ok else "FAIL"))
    return out


# ------------------------------------------------------------------------------------------ 2 clouds

def _cloud(a: np.ndarray, names, seed=0, edge=(96, 128)):
    """Opaque cloud on cols < edge[0], linear thin-cloud ramp to edge[1]. Returns (arr, alpha)."""
    rng = np.random.default_rng(seed)
    C, H, W = a.shape
    x = np.arange(W)
    alpha = np.clip((edge[1] - x) / (edge[1] - edge[0]), 0, 1)[None, :].repeat(H, 0).astype(np.float32)
    # smooth texture 0.25..0.55, spectrally flat in VNIR, lower in SWIR (like real clouds)
    tex = rng.random((H // 16 + 1, W // 16 + 1)).astype(np.float32)
    from scipy import ndimage

    tex = ndimage.zoom(tex, 16, order=1)[:H, :W]
    level = 0.25 + 0.3 * tex
    swir = {"B11": 0.75, "B12": 0.55, "B9": 0.6}
    c = np.stack([level * swir.get(n, 1.0) for n in names])
    return (a * (1 - alpha) + c * alpha).astype(np.float32), alpha


def check_clouds(ctx: Ctx) -> list[dict]:
    out = []
    for m, p in ctx.preds.items():
        thr = p.threshold
        base_val, base_live = ctx.base(m)
        n_op = n_edge = f_op = f_edge = 0
        for (pid, a, cl, _), b0 in zip(ctx.val, base_val):
            b, al = _cloud(a, MARIDA_NAMES)
            q = p.predict_proba(b, MARIDA_NAMES)
            new = (q >= thr) & ~(b0 >= thr) & np.isfinite(a).all(0)
            op, ed = al >= 1, (al > 0) & (al < 1)
            n_op += op.sum(); n_edge += ed.sum(); f_op += new[op].sum(); f_edge += new[ed].sum()
        bl, al = _cloud(ctx.live, L2A_NAMES, edge=(192, 256))
        ql = p.predict_proba(bl, L2A_NAMES)
        newl = (ql >= thr) & ~(base_live >= thr)
        live_fp = float(newl[al > 0].mean())
        fp_op, fp_edge = f_op / max(n_op, 1), f_edge / max(n_edge, 1)
        worst = max(fp_op, fp_edge, live_fp)
        st = _grade_fp(worst)
        out.append(_case(2, f"2.{m}", f"new detections under cloud <= {FP_PASS:.1%} (WARN <= {FP_WARN:.0%})",
                         f"val opaque {fp_op:.3%} ({int(f_op)}/{int(n_op)}), val thin edge {fp_edge:.3%} "
                         f"({int(f_edge)}/{int(n_edge)}), live crop {live_fp:.3%}", st,
                         "" if st == "PASS" else "mask clouds before the model (SCL 8/9/10 or B2 > 0.15 / "
                         "cirrus test) and/or add cloud / cloud-edge negatives to training"))
    return out


# ------------------------------------------------------------------------------------------ 3 glint

def _glint(a, names, bands, delta, period=32):
    b = a.copy()
    H = a.shape[1]
    rows = (np.arange(H) // period) % 2 == 0
    for n in bands:
        b[names.index(n)][rows] += delta
    stripe = np.zeros(a.shape[1:], bool)
    stripe[rows] = True
    return b, stripe


def check_glint(ctx: Ctx) -> list[dict]:
    out = []
    for m, p in ctx.preds.items():
        thr = p.threshold
        base_val, base_live = ctx.base(m)
        for bands, graded in ((("B8", "B11"), True), (("B8",), False)):
            parts, worst = [], 0.0
            for delta in (0.01, 0.03):
                nn = nf = 0
                for (pid, a, cl, _), b0 in zip(ctx.val, base_val):
                    b, st_ = _glint(a, MARIDA_NAMES, bands, delta)
                    q = p.predict_proba(b, MARIDA_NAMES)
                    sel = st_ & (cl != 1) & np.isfinite(a).all(0)
                    new = (q >= thr) & ~(b0 >= thr)
                    nn += sel.sum(); nf += new[sel].sum()
                bl, stl = _glint(ctx.live, L2A_NAMES, bands, delta)
                ql = p.predict_proba(bl, L2A_NAMES)
                lf = float(((ql >= thr) & ~(base_live >= thr))[stl].mean())
                vf = nf / max(nn, 1)
                worst = max(worst, vf, lf)
                parts.append(f"+{delta}: val {vf:.3%}, live {lf:.3%}")
            st = _grade_fp(worst)
            if not graded and st == "FAIL":
                st = "WARN"  # +B8 alone is exactly the debris (FDI) signature: informative only
            tag = "+".join(bands)
            out.append(_case(3, f"3.{m}.{tag}", f"new detections in glint stripes (non-debris px) <= {FP_PASS:.1%}"
                             + ("" if graded else " (B8-only = FDI signature; informative, max WARN)"),
                             "; ".join(parts), st,
                             "" if st == "PASS" else "glint mask (e.g. B11 > 0.02-0.03 over open water / SCL + "
                             "sun-view geometry) or glint-augmented negatives in training"))
    return out


# ------------------------------------------------------------------------------------------ 4 radiometry

def _f1(pred, cl):
    lab = cl > 0
    y, t = pred[lab], cl[lab] == 1
    tp = int((y & t).sum()); fp = int((y & ~t).sum()); fn = int((~y & t).sum())
    return 2 * tp / max(2 * tp + fp + fn, 1)


def check_radiometry(ctx: Ctx) -> list[dict]:
    out = []
    rng = np.random.default_rng(1)
    tf = {"scale0.9": lambda a: a * 0.9, "scale1.1": lambda a: a * 1.1, "offset+0.005": lambda a: a + 0.005,
          "noise_s0.002": lambda a: a + rng.normal(0, 0.002, a.shape).astype(np.float32)}
    for m, p in ctx.preds.items():
        thr = p.threshold
        base_val, base_live = ctx.base(m)
        cls = np.concatenate([c.ravel() for _, _, c, _ in ctx.val])
        pb = np.concatenate([(b >= thr).ravel() for b in base_val])
        f1b, nb, nlb = _f1(pb, cls), int(pb.sum()), int((base_live >= thr).sum())
        for k, fn in tf.items():
            q = np.concatenate([(p.predict_proba(fn(a), MARIDA_NAMES) >= thr).ravel() for _, a, _, _ in ctx.val])
            ql = int((p.predict_proba(fn(ctx.live), L2A_NAMES) >= thr).sum())
            f1 = _f1(q, cls)
            d = f1 - f1b
            rel = (int(q.sum()) - nb) / max(nb, 1)
            st = "PASS" if abs(d) <= DF1_PASS else ("WARN" if abs(d) <= DF1_WARN else "FAIL")
            if st == "PASS" and abs(rel) > DCOUNT_WARN:
                st = "WARN"
            out.append(_case(4, f"4.{m}.{k}", f"|dF1| <= {DF1_PASS} (WARN <= {DF1_WARN}); detections within "
                             f"+-{DCOUNT_WARN:.0%}",
                             f"F1 {f1b:.3f} -> {f1:.3f} (d {d:+.3f}); val detections {nb} -> {int(q.sum())} "
                             f"({rel:+.0%}); live crop {nlb} -> {ql}", st,
                             "" if st == "PASS" else (
                                 "noise augmentation in training / denoised (window-mean) features; sigma 0.002 is "
                                 "~10% of open-water reflectance" if k.startswith("noise") else
                                 "radiometric harmonisation (lgbm harmonize='water_median') or scale/offset "
                                 "augmentation in training")))
    return out


# ------------------------------------------------------------------------------------------ 5 sizes

def _mosaic(ctx, h, w):
    """(11,h,w) from tiling val patches (NaN -> 0.02 so every pixel is valid)."""
    tiles = [np.nan_to_num(a, nan=0.02) for _, a, _, _ in ctx.val]
    ny, nx = -(-h // 256), -(-w // 256)
    rows = [np.concatenate([tiles[(i * nx + j) % len(tiles)] for j in range(nx)], 2) for i in range(ny)]
    return np.ascontiguousarray(np.concatenate(rows, 1)[:, :h, :w])


SIZES = [(100, 100), (1000, 1000), (300, 700), (700, 300), (513, 40), (1, 1), (5, 3)]


def check_sizes(ctx: Ctx) -> list[dict]:
    out = []
    d = _fresh(ctx.work / "5_sizes" / "in")
    for h, w in SIZES:
        a = _mosaic(ctx, h, w)
        write_tif(d / f"s{h}x{w}.tif", a)
        for m, p in ctx.preds.items():
            try:
                q = p.predict_proba(a, MARIDA_NAMES)
                ok = q.shape == (h, w) and np.isfinite(q).all()
                res = f"shape {q.shape}"
                if m == "lgbm" and max(h, w) > p.block:
                    # tiled (block 512 + halo) vs whole-image features
                    r = p.prepare_rows(a, MARIDA_NAMES)
                    q2 = p.predict_rows_many([r])[0]
                    dm = float(np.abs(q2 - q).max())
                    dd = int(((q2 >= p.threshold) != (q >= p.threshold)).sum())
                    res += f"; tiled vs untiled max|dp| {dm:.2e}, mask diff {dd} px"
                    ok &= dd == 0
                out.append(_case(5, f"5.{m}.{h}x{w}.direct", "no exception, prob shape == (H,W)",
                                 res, "PASS" if ok else "FAIL"))
            except Exception as e:  # noqa: BLE001
                out.append(_case(5, f"5.{m}.{h}x{w}.direct", "no exception, prob shape == (H,W)",
                                 f"{type(e).__name__}: {e}", "FAIL", "see 5.cli"))
    o = _fresh(ctx.work / "5_sizes" / "out")
    code, txt = run_cli(d, o)
    missing = [f"{h}x{w}" for h, w in SIZES if not (o / f"s{h}x{w}_prob.tif").exists()]
    bad = [f"{h}x{w}" for h, w in SIZES if (o / f"s{h}x{w}_prob.tif").exists()
           and read_out(o / f"s{h}x{w}_prob.tif").shape != (h, w)]
    ok = code == 0 and not missing and not bad
    out.append(_case(5, "5.cli", "exit 0, outputs of every size with the input's H,W",
                     f"exit {code}; missing {missing or '-'}; wrong shape {bad or '-'}; {_last_err(txt)}",
                     "PASS" if ok else "FAIL",
                     "" if ok else "inference.py: a per-file predictor error must skip that file (exit 1 at the end),"
                     " not return EXIT_MODEL and drop the pending batch; pixel._local_median: clamp the subsample "
                     "start (x[min(f//2, H-1)::f, min(f//2, W-1)::f]) or reflect-pad tiny chips"))
    return out


# ------------------------------------------------------------------------------------------ 6 dtypes

def check_dtypes(ctx: Ctx) -> list[dict]:
    out = []
    pid, a, cl, prof = ctx.val[1]
    a = np.nan_to_num(a, nan=0.0)
    live = ctx.live
    d = _fresh(ctx.work / "6_dtype" / "in")
    kw = dict(crs=prof.get("crs"), transform=prof.get("transform"))
    lk = dict(crs=ctx.live_profile["crs"], transform=ctx.live_profile["transform"])
    write_tif(d / "val_f32.tif", a, **kw)
    write_tif(d / "val_f64.tif", a.astype(np.float64), **kw)
    write_tif(d / "val_u16dn.tif", np.clip(np.rint(a * 1e4), 0, 65535), dtype="uint16", **kw)
    write_tif(d / "val_u16dn_off1000.tif", np.clip(np.rint(a * 1e4) + 1000, 0, 65535), dtype="uint16", **kw)
    write_tif(d / "live_f32.tif", live, descriptions=L2A_NAMES, **lk)
    write_tif(d / "live_u16dn.tif", np.clip(np.rint(live * 1e4), 0, 65535), dtype="uint16",
              descriptions=L2A_NAMES, **lk)
    # L2A processing baseline >= 04.00 (from 2022-01-25): DN = 10000 * rho + 1000 (BOA_ADD_OFFSET = -1000)
    write_tif(d / "live_u16dn_off1000.tif", np.clip(np.rint(live * 1e4) + 1000, 0, 65535), dtype="uint16",
              descriptions=L2A_NAMES, **lk)
    o = _fresh(ctx.work / "6_dtype" / "out")
    code, txt = run_cli(d, o)
    thr_u8 = ctx.lgbm.threshold

    def cmp(ref, other):
        if not (o / f"{other}_prob.tif").exists():
            return None, None, None
        p0, p1 = read_out(o / f"{ref}_prob.tif").astype(int), read_out(o / f"{other}_prob.tif").astype(int)
        m0, m1 = read_out(o / f"{ref}_mask.tif"), read_out(o / f"{other}_mask.tif")
        return int(np.abs(p0 - p1).max()), int((m0 != m1).sum()), int(m1.sum()) - int(m0.sum())

    for other, ref, exp_ok in (("val_f64", "val_f32", True), ("val_u16dn", "val_f32", True),
                               ("live_u16dn", "live_f32", True), ("val_u16dn_off1000", "val_f32", False),
                               ("live_u16dn_off1000", "live_f32", False)):
        dp, dm, dn = cmp(ref, other)
        if dp is None:
            out.append(_case(6, f"6.{other}", f"same result as {ref}", f"no output (exit {code}); {_last_err(txt)}",
                             "FAIL"))
            continue
        n_ref = int(read_out(o / f"{ref}_mask.tif").sum())
        res = f"max|d prob_u8| {dp}, mask differs in {dm} px, detections {n_ref} -> {n_ref + dn}"
        n_px = (a if other.startswith("val") else live)[0].size
        if exp_ok:
            st = "PASS" if dm <= 0.001 * n_px else "FAIL"
            out.append(_case(6, f"6.{other}", f"same mask as {ref} (<=0.1% px differ; 1e-4 DN quantisation and "
                             "clipping of negative rhorc to 0 allowed)", res, st))
        else:
            st = "PASS" if dm <= 0.001 * n_px else ("WARN" if dm <= 0.01 * n_px else "FAIL")
            if n_ref and abs(dn) > DCOUNT_WARN * n_ref:
                st = "FAIL"  # most detections lost / invented
            elif n_ref == 0 and dm == 0:
                st = "WARN"  # no detections to lose on this crop: inconclusive
            out.append(_case(6, f"6.{other}",
                             "L2A baseline>=04.00 DN (+1000 offset): same mask as reflectance (or a clear error)",
                             res + " (auto-scale multiplies by 1e-4 but does not subtract the 1000 offset -> "
                             "+0.1 on every band)", st,
                             "" if st == "PASS" else "in --scale auto read BOA_ADD_OFFSET/processing baseline "
                             "from metadata or add --offset; at least warn when min(DN) >= 1000 over water"))
    out.append(_case(6, "6.cli", "exit 0, DN detected and scaled with a warning", f"exit {code}; {_last_err(txt)}",
                     "PASS" if code == 0 and "looks like DN" in txt else "FAIL"))
    return out


# ------------------------------------------------------------------------------------------ 7 channels

def check_channels(ctx: Ctx) -> list[dict]:
    out = []
    live, lk = ctx.live, dict(crs=ctx.live_profile["crs"], transform=ctx.live_profile["transform"])
    root = _fresh(ctx.work / "7_channels")
    perm = [11, 3, 0, 7, 5, 9, 1, 10, 2, 8, 4, 6]
    b10 = np.full((1,) + live.shape[1:], 0.001, np.float32)
    drop_b8 = [i for i, n in enumerate(L2A_NAMES) if n != "B8"]
    variants = {
        "ref": (live, L2A_NAMES),
        "perm_desc": (live[perm], [L2A_NAMES[i] for i in perm]),
        "desc_b0x_lower": (live, ["b01", "b02", "b03", "b04", "b05", "b06", "b07", "b08", "b8a", "b09", "b11", "b12"]),
        "extra_b10_desc": (np.concatenate([live[:10], b10, live[10:]]), L2A_NAMES[:10] + ["B10"] + L2A_NAMES[10:]),
        "perm_nodesc": (live[perm], None),
        "extra_nodesc": (np.concatenate([live, b10]), None),
        "missing_b8_desc": (live[drop_b8], [L2A_NAMES[i] for i in drop_b8]),
        "missing_nodesc_10": (live[:10], None),
    }
    for k, (arr, desc) in variants.items():
        write_tif(root / k / "in" / f"{k}.tif", arr, descriptions=desc, **lk)
    runs = {}
    for k in variants:
        o = _fresh(root / k / "out")
        runs[k] = run_cli(root / k / "in", o) + (o,)
    ref_p = read_out(runs["ref"][2] / "ref_prob.tif")

    def same(k):
        f = runs[k][2] / f"{k}_prob.tif"
        return None if not f.exists() else int(np.abs(read_out(f).astype(int) - ref_p.astype(int)).max())

    for k, exp in (("perm_desc", "same result as ordered (mapped by band descriptions)"),
                   ("desc_b0x_lower", "'b08'/'b8a' descriptions normalised -> same result"),
                   ("extra_b10_desc", "extra band B10 ignored -> same result")):
        code, txt, _ = runs[k]
        dmax = same(k)
        out.append(_case(7, f"7.{k}", exp, f"exit {code}; max|d prob_u8| {dmax}",
                         "PASS" if code == 0 and dmax == 0 else "FAIL"))
    code, txt, o = runs["perm_nodesc"]
    dmax = same("perm_nodesc")
    nm = int(read_out(o / "perm_nodesc_mask.tif").sum()) if (o / "perm_nodesc_mask.tif").exists() else None
    out.append(_case(7, "7.perm_nodesc", "cannot be detected from data; ideally a warning (no descriptions -> "
                     "order assumed)", f"exit {code}, silently processed with the assumed s2_l2a_12 order; "
                     f"max|d prob_u8| {dmax}, detections {nm} vs {int(read_out(runs['ref'][2] / 'ref_mask.tif').sum())}; "
                     f"warning printed: {'order' in txt.lower() or 'description' in txt.lower()}", "WARN",
                     "log a warning when bands have no descriptions and the order is assumed by count"))
    for k, exp_code, words in (("extra_nodesc", 1, ["13 bands", "--channels"]),
                               ("missing_nodesc_10", 1, ["10 bands"]),
                               ("missing_b8_desc", 1, ["B8"])):
        code, txt, o = runs[k]
        msg = _last_err(txt)
        clear = all(w in txt for w in words)
        ok = code == exp_code and clear and not (o / f"{k}_prob.tif").exists()
        fix = ""
        if not ok and k == "missing_b8_desc":
            fix = ("inference.py load(): check select_bands()/required bands right after guess_channel_names "
                   "and return EXIT_DATA per file (skip it) instead of EXIT_MODEL + abort of the whole run")
        out.append(_case(7, f"7.{k}", f"exit {exp_code} (data error), message names the problem, no output",
                         f"exit {code}; {msg}", "PASS" if ok else "FAIL", fix))
    # --channels with a wrong-size set
    o = _fresh(root / "wrongset_out")
    code, txt = run_cli(root / "ref" / "in", o, extra=("--channels", "marida"))
    ok = code == 1 and "11" in txt
    out.append(_case(7, "7.channels_flag_mismatch", "--channels marida on 12-band file -> exit 1 with message",
                     f"exit {code}; {_last_err(txt)}", "PASS" if ok else "FAIL"))
    return out


# ------------------------------------------------------------------------------------------ 8 broken files

def check_broken(ctx: Ctx) -> list[dict]:
    out = []
    root = _fresh(ctx.work / "8_broken")
    d = root / "in"
    d.mkdir()
    good = ctx.val[0][1]
    write_tif(d / "a_good.tif", good)
    write_tif(d / "z_good.tif", good)
    (d / "broken.tif").write_bytes(os.urandom(4096))
    (d / "empty.tif").write_bytes(b"")
    full = (d / "a_good.tif").read_bytes()
    (d / "truncated.tif").write_bytes(full[: len(full) // 3])
    (d / "notes.txt").write_text("not an image", encoding="utf-8")
    (d / "photo.jpg").write_bytes(os.urandom(512))
    (d / "folder.tif").mkdir()
    write_tif(d / "single_band.tif", good[:1])
    o = root / "out"
    code, txt = run_cli(d, o)
    goods_ok = all((o / f"{s}_{k}.tif").exists() for s in ("a_good", "z_good") for k in ("prob", "mask"))
    bad_names = ["broken", "empty", "truncated", "single_band"]
    mentioned = {n: (n + ".tif") in txt for n in bad_names}
    no_out = all(not (o / f"{n}_prob.tif").exists() for n in bad_names)
    not_listed = all(x not in txt for x in ("notes.txt", "photo.jpg"))
    ok = code == 1 and goods_ok and all(mentioned.values()) and no_out
    out.append(_case(8, "8.mixed_folder", "good files processed; broken/empty/truncated/1-band tif skipped with a "
                     "per-file ERROR naming the file; .txt/.jpg/dir ignored; exit 1 (some files failed)",
                     f"exit {code}; good outputs {goods_ok}; bad files named {mentioned}; no bad outputs {no_out}; "
                     f"txt/jpg silent {not_listed}", "PASS" if ok else "FAIL",
                     "" if ok else "see result"))
    # truncated file: does rasterio raise, or return partly zero data silently?
    trunc_out = (o / "truncated_prob.tif").exists()
    out.append(_case(8, "8.truncated", "truncated tif -> read error, skipped", "skipped with error" if not trunc_out
                     else "processed (partial data read silently)", "PASS" if not trunc_out else "WARN"))
    # only non-tif files
    d2 = root / "only_txt"
    d2.mkdir()
    (d2 / "readme.txt").write_text("x", encoding="utf-8")
    code2, txt2 = run_cli(d2, root / "out2")
    out.append(_case(8, "8.no_images", "exit 1 'no input *.tif'", f"exit {code2}; {_last_err(txt2)}",
                     "PASS" if code2 == 1 and "no input" in txt2 else "FAIL"))
    code3, txt3 = run_cli(root / "does_not_exist", root / "out3")
    out.append(_case(8, "8.missing_dir", "exit 1 'data directory not found'", f"exit {code3}; {_last_err(txt3)}",
                     "PASS" if code3 == 1 and "not found" in txt3 else "FAIL"))
    # only broken files
    d4 = root / "only_broken"
    d4.mkdir()
    (d4 / "x.tif").write_bytes(os.urandom(100))
    code4, txt4 = run_cli(d4, root / "out4")
    out.append(_case(8, "8.only_broken", "exit 1, error names x.tif", f"exit {code4}; {_last_err(txt4)}",
                     "PASS" if code4 == 1 and "x.tif" in txt4 else "FAIL"))
    # output path is an existing file
    f5 = root / "out_is_file"
    f5.write_text("x", encoding="utf-8")
    d5 = root / "one_good"
    write_tif(d5 / "g.tif", good)
    code5, txt5 = run_cli(d5, f5)
    out.append(_case(8, "8.output_is_file", "exit 1 with a message, no traceback", f"exit {code5}; {_last_err(txt5)}",
                     "PASS" if code5 == 1 and "Traceback" not in txt5 else "FAIL"))
    # a live scene folder as-is (bands.tif + scl.tif + prob_lgbm.tif + water_mask.tif)
    d6 = root / "live_folder"
    d6.mkdir()
    write_tif(d6 / "bands.tif", ctx.live[:, :128, :128], descriptions=L2A_NAMES)
    write_tif(d6 / "scl.tif", np.full((1, 128, 128), 6), dtype="uint8")
    write_tif(d6 / "prob_lgbm.tif", np.zeros((1, 128, 128)), dtype="uint8")
    write_tif(d6 / "water_mask.tif", np.ones((1, 128, 128)), dtype="uint8")
    code6, txt6 = run_cli(d6, root / "out6")
    ok6 = (root / "out6" / "bands_prob.tif").exists()
    out.append(_case(8, "8.live_scene_folder", "data/live/<region>/<date> layout: bands.tif processed; 1-band "
                     "helper rasters (scl, prob_lgbm) ideally ignored", f"exit {code6}; bands processed {ok6}; "
                     f"{_last_err(txt6)}", "PASS" if code6 == 0 and ok6 else ("WARN" if ok6 else "FAIL"),
                     "skip 1-band rasters / known names (scl, prob_*) with a WARNING instead of counting them as "
                     "failed files (exit 1)"))
    return out


# ------------------------------------------------------------------------------------------ 9 paths

def check_paths(ctx: Ctx) -> list[dict]:
    from macroplastic.io import exists

    out = []
    root = _fresh(ctx.work / "9_paths")
    d = root / "папка с пробелами" / "данные S2"
    good = ctx.val[0][1]
    names = ["снимок 1", "aux", "con", "nul", "com1", "a b.c"]
    written = []
    for n in names:
        try:
            write_tif(d / f"{n}.tif", good)
            written.append(n)
        except Exception as e:  # noqa: BLE001
            out.append(_case(9, f"9.write.{n}", "test setup", f"cannot create {n}.tif: {e}", "WARN"))
    o = root / "выход с пробелами" / "результат"
    code, txt = run_cli(d, o)
    for n in written:
        ok = exists(o / f"{n}_prob.tif") and exists(o / f"{n}_mask.tif")
        if ok:
            pr = read_out(o / f"{n}_prob.tif")
            ok = pr.shape == good.shape[1:]
        out.append(_case(9, f"9.{n}", f"'{n}.tif' in a dir with spaces+Cyrillic -> outputs {n}_prob/_mask.tif",
                         f"exit {code}; outputs {'ok' if ok else 'MISSING'}", "PASS" if ok and code == 0 else "FAIL",
                         "" if ok else "see 9.cli"))
    import re

    m = re.search(r"files=(\d+)", txt)
    seen = int(m.group(1)) if m else -1
    ok = code == 0 and seen == len(written)
    out.append(_case(9, "9.cli", f"exit 0 and all {len(written)} input files counted (files={len(written)})",
                     f"exit {code}; inference.py saw files={seen}; {_last_err(txt)}", "PASS" if ok else "FAIL",
                     "" if ok else "io.list_images(): Path.is_file() is False for 'aux.tif'/'con.tif'/'nul.tif'/"
                     "'com1.tif' on Windows -> the files are dropped silently; use os.path.isfile(win_path(p)) "
                     "(or p.suffix check + DirEntry.is_file()) there"))
    return out


CHECKS = {1: check_empty, 2: check_clouds, 3: check_glint, 4: check_radiometry, 5: check_sizes,
          6: check_dtypes, 7: check_channels, 8: check_broken, 9: check_paths}
TITLES = {1: "Пустой чип (NaN / 0)", 2: "Облака (синтетика, половина чипа)", 3: "Солнечный блик (полосы +B8/B11)",
          4: "Шум / сдвиг радиометрии", 5: "Другие размеры", 6: "Другие dtype", 7: "Каналы: лишние / переставленные / "
          "отсутствующие", 8: "Битые tif / не-tif в папке", 9: "Пути: пробелы, кириллица, aux.tif"}


def run_checks(ctx: Ctx | None = None, items=None) -> list[dict]:
    ctx = ctx or Ctx()
    res = []
    for i, fn in CHECKS.items():
        if items and i not in items:
            continue
        t = time.perf_counter()
        try:
            r = fn(ctx)
        except Exception as e:  # noqa: BLE001
            import traceback

            r = [_case(i, f"{i}.crash", "check runs", f"{type(e).__name__}: {e} {traceback.format_exc()[-400:]}",
                       "FAIL")]
        for c in r:
            c["seconds"] = round(time.perf_counter() - t, 2)
        res.extend(r)
    return res


def render_md(res: list[dict], ctx: Ctx, seconds: float) -> str:
    n = {s: sum(r["status"] == s for r in res) for s in ("PASS", "WARN", "FAIL")}
    by, bx, wf = ctx.live_window
    L = ["# Устойчивость инференса (L24)", "",
         f"Сгенерировано `scripts/robustness_report.py` за {seconds:.0f} с. Модели: `lgbm` (weights/lgbm, порог "
         f"{ctx.lgbm.threshold}), `fdi_rule` (порог {ctx.fdi.threshold}). Устройство: CPU.", "",
         f"Данные: {len(ctx.val)} патчей MARIDA **val** с разметкой Marine Debris ({', '.join(p for p, *_ in ctx.val)}); "
         f"вырезка 512×512 живой сцены `data/live/manila/2019-05-13` (y={by}, x={bx}, доля воды {wf:.0%}). "
         "Test MARIDA не читается.", "",
         f"**Итог: {len(res)} проверок — PASS {n['PASS']}, WARN {n['WARN']}, FAIL {n['FAIL']}.**", "",
         "Пороги оценки: доля *новых* срабатываний на возмущённых пикселях ≤ "
         f"{FP_PASS:.1%} — PASS, ≤ {FP_WARN:.0%} — WARN, иначе FAIL; изменение F1 на размеченных пикселях val "
         f"|ΔF1| ≤ {DF1_PASS} — PASS, ≤ {DF1_WARN} — WARN (и WARN при изменении числа находок > ±{DCOUNT_WARN:.0%}). "
         "F1 — Marine Debris против остальных размеченных классов, по порогу модели. Облако: непрозрачная часть "
         "(96 столбцов) + полупрозрачный край (32 столбца), текстура 0.25–0.55, SWIR ниже. Блик: полосы по 32 строки "
         "через одну, прибавка к B8 и B11 (+0.01 / +0.03); вариант «только B8» — справочный (это и есть сигнатура FDI). "
         "Оговорка: на вырезке живой сцены у lgbm 0 находок и до возмущения, поэтому «live 0 → 0» — слабое "
         "свидетельство; основная оценка — по val-патчам.",
         ""]
    for i, title in TITLES.items():
        rs = [r for r in res if r["item"] == i]
        if not rs:
            continue
        L += [f"## {i}. {title}", "", "| Случай | Ожидание | Результат | Статус |", "|---|---|---|---|"]
        for r in rs:
            cell = lambda s: str(s).replace("|", "\\|").replace("\n", " ")  # noqa: E731
            L.append(f"| `{r['case']}` | {cell(r['expect'])} | {cell(r['result'])} | **{r['status']}** |")
        L.append("")
    bad = [r for r in res if r["status"] in ("FAIL", "WARN") and r.get("fix")]
    if bad:
        L += ["## Предлагаемые правки", ""]
        for r in bad:
            L.append(f"- `{r['case']}` ({r['status']}): {r['fix']}")
        L.append("")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(REPO / "reports" / "robustness.md"))
    ap.add_argument("--work", default=str(WORK))
    ap.add_argument("--items", default="", help="comma list of items 1..9 (default all)")
    a = ap.parse_args(argv)
    t = time.perf_counter()
    ctx = Ctx(Path(a.work))
    items = [int(x) for x in a.items.split(",") if x.strip()] or None
    res = run_checks(ctx, items)
    dt = time.perf_counter() - t
    Path(a.out).write_text(render_md(res, ctx, dt), encoding="utf-8")
    for r in res:
        print(f"{r['status']:4s} {r['seconds']:6.1f}s {r['case']:32s} {r['result'][:150]}")
    print(f"-> {a.out} ({dt:.1f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
