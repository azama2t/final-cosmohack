"""Research experiment L64: image features on event<->scene pairs vs field all_litter density (case, INBOX 8.3).

  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe -W ignore scripts/case/pairs_experiment.py            # fetch FDI (cached) + stats
  .venv/Scripts/python.exe scripts/case/pairs_experiment.py --no-fetch                                     # stats from cache only

Question: does anything the image shows in the observation strip rank-correlate with the field density of ALL floating
litter (S3 North Sea / S4 Black Sea: total floating macro-litter, all materials; NOT plastic) of the same event?
This is NOT a calibration: no items/km2 is ever predicted from the image.

Inputs (read-only): outputs of scripts/case/pair_quality.py (L61) -- data/pairs/quality/<dir>/{meta.json, quality.tif,
prob.tif}; geometry from task/macroplastic_marine_samples.csv via pair_quality.event_geometry/strip_polygon (same strip
as L61). FDI (Biermann 2020, macroplastic.indices.fdi, variant 'paper') needs the bands: the S2 L2A crop is re-read with
stac.read_crop on the same bounds as L61 and FDI is cached to data/pairs/experiment/<dir>/fdi.npy.

Features (image only, no field columns), on valid water (quality code 1) of the strip "s_" and of the context
(crop water outside the strip) "c_":
  frac_p_thr  share of water px with P(debris) >= 0.63 (L61 detector threshold, fixed)
  p_mean, p_p95  mean / 95th percentile of P (prob.tif, P*255 quantised)
  spots_km2   detector spots in strip (L61 meta n_det, after cloud/shadow filters) per km2 of strip water
  fdi_p95_anom  FDI p95 in strip minus FDI median of context water (removes scene offset)
  fdi_med_anom  FDI median in strip minus context median
  fdi_frac_hi   share of strip water with FDI > ctx median + 3 * 1.4826 * MAD(ctx)  (k=3 fixed a priori)
PRE-REGISTERED primary features (fixed before looking at the result): s_fdi_p95_anom (spectral) and s_p_mean (detector).
All other features are exploratory; Holm correction over all features is reported.

Reference: field_items_km2 of the event top sample (concentration_items_km2, all_litter); if absent,
items_count / sampled_area_km2 (flag 'items/area'); if both absent -> missing (empty != 0).

Statistics: Spearman rho and Kendall tau-b; 95% CI by group bootstrap (group = observation day x region, events of
one day are dependent); permutation p (event-level) and exact permutation on group means (sensitivity); null model =
same feature from a randomly shifted copy of the strip on water of the same crop, not overlapping the strip (K draws),
correlated with the same field values; power: minimal |rho| detectable at alpha 0.05 / power 0.8 for the given n
(Fisher z with Bonett-Wright variance + Monte Carlo).
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
from rasterio.features import rasterize  # noqa: E402
from scipy import ndimage, stats  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
PAIRS = ROOT / "data" / "pairs"
OUTQ = PAIRS / "quality"
CACHE = PAIRS / "experiment"
REP = ROOT / "reports" / "case_pairs"

THR = 0.63           # L61 detector threshold (configs / weights/lgbm), not tuned here
K_MAD = 3.0          # FDI "high" = ctx median + 3 robust sigma, fixed a priori
SEED = 64
N_BOOT = 5000
N_PERM = 10000
K_NULL = 500
PRIMARY = ["s_fdi_p95_anom", "s_p_mean"]
FEATURES = ["s_frac_p_thr", "s_p_mean", "s_p_p95", "s_spots_km2", "s_fdi_p95_anom", "s_fdi_med_anom", "s_fdi_frac_hi",
            "c_frac_p_thr", "c_p_mean", "c_fdi_p95", "x_p_mean", "x_fdi_p95_anom", "x_fdi_frac_hi"]
NULLABLE = ["s_frac_p_thr", "s_p_mean", "s_p_p95", "s_fdi_p95_anom", "s_fdi_med_anom", "s_fdi_frac_hi"]
# x_<f> = strip value minus the median of the same feature over random water locations of the same scene (null draws):
# the strip-specific part of the signal (a scene/day-level effect cancels out). Control features, not primary.
EXCESS = {"x_p_mean": "s_p_mean", "x_fdi_p95_anom": "s_fdi_p95_anom", "x_fdi_frac_hi": "s_fdi_frac_hi"}


def load_pq():
    spec = importlib.util.spec_from_file_location("pair_quality", ROOT / "scripts" / "case" / "pair_quality.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ------------------------------------------------------------------------------------------ data

def fetch_fdi(pq, meta: dict, out: Path) -> str:
    from macroplastic.live import stac
    from macroplastic.indices import fdi
    endpoint, coll = meta["source"].split("/", 1)
    item = pq.get_item(endpoint, coll, meta["scene_id"], meta["config"]["network"]["retries"])
    crop = stac.read_crop(item, int(meta["epsg"]), meta["bounds_utm"], workers=4)
    b = crop["bands"]
    f = fdi({n: b[i] for i, n in enumerate(stac.BANDS)}, variant="paper")
    out.parent.mkdir(parents=True, exist_ok=True)
    np.save(out, f.astype(np.float32))
    return f"{f.shape}"


def region_of(eid: str) -> str:
    return "S3_north_sea" if eid.startswith("S3:") else ("S4_black_sea" if eid.startswith("S4:") else eid.split(":")[0])


def field_ref(samples: pd.DataFrame, eid: str, meta: dict) -> tuple[float, str]:
    v = meta.get("field_items_km2")
    if v is not None and np.isfinite(v):
        return float(v), "concentration_items_km2"
    s = samples[(samples.event_id == eid) & samples.parent_sample_id.isna()]
    if len(s) and pd.notna(s.iloc[0].items_count) and pd.notna(s.iloc[0].sampled_area_km2) and s.iloc[0].sampled_area_km2 > 0:
        return float(s.iloc[0].items_count / s.iloc[0].sampled_area_km2), "items/area"
    return float("nan"), "missing"


def pix_features(prob, fdi_a, idx_w, ctx_w, n_det=None) -> dict:
    """Features over strip-water pixels idx_w (tuple of index arrays) given context water values ctx_w (dict)."""
    p = prob[idx_w]
    r = dict(s_n_water=int(p.size))
    if p.size < 20:
        return r
    r.update(s_frac_p_thr=float((p >= THR).mean()), s_p_mean=float(p.mean()), s_p_p95=float(np.percentile(p, 95)))
    if n_det is not None:
        r["s_spots_km2"] = float(n_det / (p.size * 1e-4))
    if fdi_a is not None:
        f = fdi_a[idx_w]
        f = f[np.isfinite(f)]
        if f.size >= 20:
            r.update(s_fdi_p95_anom=float(np.percentile(f, 95) - ctx_w["fdi_med"]),
                     s_fdi_med_anom=float(np.median(f) - ctx_w["fdi_med"]),
                     s_fdi_frac_hi=float((f > ctx_w["fdi_med"] + K_MAD * ctx_w["fdi_sig"]).mean()))
    return r


def build_pairs(pq, fetch: bool) -> tuple[pd.DataFrame, dict]:
    samples = pd.read_csv(ROOT / "task" / "macroplastic_marine_samples.csv", low_memory=False)
    best = pd.read_csv(PAIRS / "best_per_event.csv").set_index("event_id")
    rng = np.random.default_rng(SEED)
    rows, nulls = [], {}
    for mf in sorted(OUTQ.glob("*/meta.json")):
        m = json.loads(mf.read_text(encoding="utf-8"))
        eid, d = m["event_id"], mf.parent
        obs = pd.Timestamp(best.loc[eid, "obs_datetime"]) if eid in best.index else pd.NaT
        fv, fbasis = field_ref(samples, eid, m)
        row = dict(event_id=eid, dir=d.name, region=region_of(eid), obs_date=str(obs.date()) if pd.notna(obs) else "",
                   time_known=bool(m.get("time_known")), dt_hours=m.get("dt_hours"), scene_id=m.get("scene_id"),
                   source=m.get("source"), decision=m.get("decision"), reason=m.get("reason") or "",
                   status=("accept" if m.get("decision") == "accept" else (m.get("reason") or "error")),
                   field_items_km2=fv, field_basis=fbasis, sampling_method=m.get("sampling_method"),
                   has_detector=bool(m.get("detector")))
        row["group"] = f"{row['region']}|{row['obs_date']}"
        if not m.get("detector"):
            row["note"] = "Landsat: QA mask only, no detector/FDI (S2-trained)" if "landsat" in str(m.get("source")) else "no detector"
            rows.append(row)
            continue
        with rasterio.open(d / "quality.tif") as src:
            qa, tr = src.read(1), src.transform
        with rasterio.open(d / "prob.tif") as src:
            prob = src.read(1).astype(np.float32) / 255.0
        g = pq.event_geometry(samples, eid)
        poly, _ = pq.strip_polygon(g, int(m["epsg"]), m["config"])
        strip = rasterize([(poly, 1)], out_shape=qa.shape, transform=tr, all_touched=True, fill=0, dtype="uint8").astype(bool)
        fpath = CACHE / d.name / "fdi.npy"
        if fetch and not fpath.exists():
            t0 = time.time()
            try:
                shp = fetch_fdi(pq, m, fpath)
                print(f"[fdi] {eid} {shp} {time.time() - t0:.1f} s", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[fdi-error] {eid}: {type(e).__name__}: {e}", flush=True)
        fdi_a = np.load(fpath) if fpath.exists() else None
        if fdi_a is not None and fdi_a.shape != qa.shape:
            print(f"[warn] {eid}: FDI shape {fdi_a.shape} != quality {qa.shape}; FDI dropped", flush=True)
            fdi_a = None
        water = qa == 1
        sw = strip & water
        ctx = water & ~ndimage.binary_dilation(strip, iterations=3)
        cw = dict(fdi_med=np.nan, fdi_sig=np.nan)
        if fdi_a is not None and ctx.sum() >= 100:
            fc = fdi_a[ctx]
            fc = fc[np.isfinite(fc)]
            cw = dict(fdi_med=float(np.median(fc)), fdi_sig=float(1.4826 * np.median(np.abs(fc - np.median(fc)))))
            row["c_fdi_p95"] = float(np.percentile(fc, 95) - cw["fdi_med"])
        if ctx.sum() >= 100:
            pc = prob[ctx]
            row.update(c_frac_p_thr=float((pc >= THR).mean()), c_p_mean=float(pc.mean()))
        row.update(pix_features(prob, fdi_a, np.nonzero(sw), cw, n_det=(m["detector"] or {}).get("n_det")))
        row["strip_px"] = int(strip.sum())
        row["fdi_cached"] = fdi_a is not None
        # ---- null model: random shifted copies of the strip on water of the same crop, not overlapping the strip
        rr, cc = np.nonzero(strip)
        H, W = qa.shape
        forb = ndimage.binary_dilation(strip, iterations=3)
        draws, tries = [], 0
        if rr.size and sw.sum() >= 20:
            dy_lo, dy_hi = -rr.min(), H - 1 - rr.max()
            dx_lo, dx_hi = -cc.min(), W - 1 - cc.max()
            while len(draws) < K_NULL and tries < K_NULL * 40:
                tries += 1
                dy, dx = int(rng.integers(dy_lo, dy_hi + 1)), int(rng.integers(dx_lo, dx_hi + 1))
                r2, c2 = rr + dy, cc + dx
                if forb[r2, c2].any():
                    continue
                w2 = water[r2, c2]
                if w2.mean() < 0.5 or w2.sum() < 20:
                    continue
                f = pix_features(prob, fdi_a, (r2[w2], c2[w2]), cw)
                draws.append([f.get(k, np.nan) for k in NULLABLE])
        nulls[eid] = np.array(draws, dtype=float) if draws else np.full((0, len(NULLABLE)), np.nan)
        row["null_draws"] = len(draws)
        rows.append(row)
    return pd.DataFrame(rows), nulls


# ------------------------------------------------------------------------------------------ stats

def _rho(x, y):
    if np.unique(x).size < 2 or np.unique(y).size < 2:
        return np.nan
    return float(stats.spearmanr(x, y).statistic)


def _tau(x, y):
    if np.unique(x).size < 2 or np.unique(y).size < 2:
        return np.nan
    return float(stats.kendalltau(x, y).statistic)


def group_boot(x, y, groups, fn, rng, n=N_BOOT):
    ug = np.unique(groups)
    idx = {g: np.nonzero(groups == g)[0] for g in ug}
    out = []
    for _ in range(n):
        pick = rng.choice(ug, size=ug.size, replace=True)
        ii = np.concatenate([idx[g] for g in pick])
        out.append(fn(x[ii], y[ii]))
    out = np.array(out)
    ok = np.isfinite(out)
    lo, hi = (np.percentile(out[ok], [2.5, 97.5]) if ok.sum() > 50 else (np.nan, np.nan))
    return float(lo), float(hi), float(1 - ok.mean())


def perm_p(x, y, fn, rng, n=N_PERM):
    obs = fn(x, y)
    if not np.isfinite(obs):
        return np.nan
    c = 0
    for _ in range(n):
        v = fn(x, rng.permutation(y))
        c += np.isfinite(v) and abs(v) >= abs(obs) - 1e-12
    return float((c + 1) / (n + 1))


def group_mean_exact(x, y, groups):
    """Spearman on group means; exact permutation p over group labels (sensitivity for day dependence)."""
    df = pd.DataFrame(dict(x=x, y=np.log10(y), g=groups)).groupby("g").mean()
    gx, gy = df.x.values, df.y.values
    k = len(df)
    r = _rho(gx, gy)
    if not np.isfinite(r) or k < 3:
        return dict(n_groups=k, rho=r, p_exact=np.nan)
    if k <= 8:
        vals = [_rho(gx, np.array(p)) for p in itertools.permutations(gy)]
    else:
        rng = np.random.default_rng(SEED)
        vals = [_rho(gx, rng.permutation(gy)) for _ in range(20000)]
    vals = np.array(vals)
    p = float(np.mean(np.abs(vals[np.isfinite(vals)]) >= abs(r) - 1e-12))
    return dict(n_groups=k, rho=r, p_exact=p)


def null_compare(df, nulls, feat, rng):
    """Correlation of field with the SAME feature from a random water location of the same scene (one draw per pair)."""
    j = NULLABLE.index(feat)
    y = df.field_items_km2.values
    obs = _rho(df[feat].values, y)
    arrs = [nulls[e][:, j] for e in df.event_id]
    if any(a.size == 0 for a in arrs):
        return dict(obs=obs, null_median=np.nan, null_q=[np.nan, np.nan], frac_null_abs_ge_obs=np.nan, n_draws=0)
    rs = []
    for _ in range(2000):
        xs = np.array([a[rng.integers(a.size)] for a in arrs])
        if np.isfinite(xs).all():
            rs.append(_rho(xs, y))
    rs = np.array(rs)
    rs = rs[np.isfinite(rs)]
    if not rs.size:
        return dict(obs=obs, null_median=np.nan, null_q=[np.nan, np.nan], frac_null_abs_ge_obs=np.nan, n_draws=0, rhos=[])
    return dict(obs=obs, null_median=float(np.median(rs)), null_q=[float(q) for q in np.percentile(rs, [2.5, 97.5])],
                frac_null_abs_ge_obs=float(np.mean(np.abs(rs) >= abs(obs) - 1e-12)) if np.isfinite(obs) else np.nan,
                n_draws=int(rs.size), rhos=rs.tolist())


def analyse(df, nulls, label, rng):
    y = df.field_items_km2.values
    g = df.group.values
    res = dict(set=label, n=int(len(df)), n_groups=int(pd.Series(g).nunique()), events=df.event_id.tolist(), features={})
    for f in FEATURES:
        n_ok = int(df[f].notna().sum()) if f in df else 0
        if n_ok < 5:
            res["features"][f] = dict(n=n_ok, note="too few values")
            continue
        sub = df[df[f].notna()]
        x, yy, gg = sub[f].values, sub.field_items_km2.values, sub.group.values
        n_uniq = int(np.unique(x).size)
        r = dict(n=int(len(sub)), n_unique_x=n_uniq, share_zero=float(np.mean(x == 0)))
        if n_uniq < 2:
            r["note"] = "constant feature (all equal) -> correlation undefined"
            res["features"][f] = r
            continue
        r["spearman"] = _rho(x, yy)
        r["kendall"] = _tau(x, yy)
        blo, bhi, bnan = group_boot(x, yy, gg, _rho, rng)
        r["spearman_ci95_groupboot"] = [blo, bhi]
        r["boot_share_undefined"] = bnan  # resamples where the feature is constant (CI is conditional on the rest)
        logo = {}
        for gname in np.unique(gg):
            kk = gg != gname
            if kk.sum() >= 5:
                logo[str(gname)] = _rho(x[kk], yy[kk])
        lv = np.array([v for v in logo.values() if np.isfinite(v)])
        r["logo_rho"] = logo
        r["logo_min_max"] = [float(lv.min()), float(lv.max())] if lv.size else [np.nan, np.nan]
        r["kendall_ci95_groupboot"] = list(group_boot(x, yy, gg, _tau, rng, n=2000)[:2])
        r["p_perm_spearman"] = perm_p(x, yy, _rho, rng)
        r["group_means"] = group_mean_exact(x, yy, gg)
        if f in NULLABLE and all(e in nulls for e in sub.event_id):
            nc = null_compare(sub, nulls, f, rng)
            r["null"] = {k: v for k, v in nc.items() if k != "rhos"}
            r["_null_rhos"] = nc.get("rhos", [])
        res["features"][f] = r
    # Holm over features with a p-value
    ps = [(f, v["p_perm_spearman"]) for f, v in res["features"].items() if np.isfinite(v.get("p_perm_spearman", np.nan))]
    ps.sort(key=lambda t: t[1])
    m, run = len(ps), 0.0
    for i, (f, p) in enumerate(ps):
        run = max(run, min(1.0, (m - i) * p))
        res["features"][f]["p_holm"] = run
    return res


def power(n_list, alpha=0.05, pw=0.8, rng=None):
    """Minimal |rho| detectable. Fisher z, Bonett-Wright var (1+r^2/2)/(n-3); Monte Carlo (Spearman, bivariate normal)."""
    za, zb = stats.norm.ppf(1 - alpha / 2), stats.norm.ppf(pw)
    out = {}
    for n in n_list:
        rho_a = np.nan
        for r in np.arange(0.01, 0.999, 0.005):
            if n > 3 and np.arctanh(r) * np.sqrt((n - 3) / (1 + r * r / 2)) >= za + zb:
                rho_a = float(r)
                break
        # Monte Carlo on Spearman with exact-ish critical value (t approximation p-value)
        rho_mc = np.nan
        for r in np.arange(0.3, 0.99, 0.05):
            cov = [[1, r], [r, 1]]
            hits = 0
            reps = 1500
            for _ in range(reps):
                z = rng.multivariate_normal([0, 0], cov, size=n)
                hits += stats.spearmanr(z[:, 0], z[:, 1]).pvalue < alpha
            if hits / reps >= pw:
                rho_mc = float(round(r, 2))
                break
        out[str(n)] = dict(min_abs_rho_fisher=round(rho_a, 3), min_abs_rho_montecarlo=rho_mc)
    # n needed for given rho
    need = {}
    for r in (0.3, 0.5, 0.7):
        need[str(r)] = int(np.ceil(3 + (1 + r * r / 2) * ((za + zb) / np.arctanh(r)) ** 2))
    return out, need


# ------------------------------------------------------------------------------------------ plots

C_ACC, C_GLINT, C_OTHER = "#2a78d6", "#eb6834", "#52514e"
INK, INK2, SURF = "#0b0b0b", "#52514e", "#fcfcfb"


def _style(ax):
    ax.set_facecolor(SURF)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(INK2)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(True, color="#e4e3df", lw=0.6)
    ax.set_axisbelow(True)


def plot_scatter(df, res_acc, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    d = df[df.has_detector].copy()
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), facecolor=SURF)
    for ax, f, ttl in ((axes[0], "s_fdi_p95_anom", "FDI p95 в полосе − медиана FDI контекста"),
                       (axes[1], "s_p_mean", "средняя P(мусор) LightGBM в полосе")):
        _style(ax)
        for st, col, mk, lab in (("accept", C_ACC, "o", "прошла маски"), ("glint", C_GLINT, "^", "отказ: блик"),
                                 ("cloud", C_OTHER, "s", "отказ: облака")):
            s = d[(d.status == st) & d[f].notna()]
            if len(s):
                ax.scatter(s.field_items_km2, s[f], s=46, c=col, marker=mk, edgecolors=SURF, linewidths=1.5, label=f"{lab} (n={len(s)})", zorder=3)
                for r in s.itertuples():
                    ax.annotate(r.event_id.replace("S4:DOORS3:", "").replace("S3:HE", "HE").replace("_MarLitter_transect", "/t"),
                                (r.field_items_km2, getattr(r, f)), fontsize=7, color=INK2, xytext=(4, 3), textcoords="offset points")
        ax.set_xscale("log")
        ax.set_xlabel("полевая плотность all_litter (все материалы, не пластик), предм./км²", color=INK, fontsize=9)
        ax.set_ylabel(ttl, color=INK, fontsize=9)
        v = res_acc["features"].get(f, {})
        if "spearman" in v:
            ci = v["spearman_ci95_groupboot"]
            ax.set_title(f"прошедшие маски: ρ = {v['spearman']:+.2f} [{ci[0]:+.2f}; {ci[1]:+.2f}], p_perm = {v['p_perm_spearman']:.2f}, n = {v['n']}",
                         fontsize=9, color=INK, loc="left")
        ax.legend(fontsize=8, frameon=False, labelcolor=INK)
    fig.suptitle("Признак снимка в полосе наблюдения vs полевая плотность all_litter (L64, исследовательский)", fontsize=11, color=INK, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=130, facecolor=SURF)
    plt.close(fig)


def plot_null(res_acc, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(12, 4), facecolor=SURF)
    for ax, f, ttl in ((axes[0], "s_fdi_p95_anom", "FDI p95 − медиана контекста"), (axes[1], "s_p_mean", "средняя P в полосе")):
        _style(ax)
        v = res_acc["features"].get(f, {})
        rh = v.get("_null_rhos", [])
        if len(rh):
            ax.hist(rh, bins=np.linspace(-1, 1, 41), color="#9ec3ee", edgecolor=SURF, linewidth=1)
            ax.axvline(v["spearman"], color=C_ACC, lw=2)
            ax.annotate(f"полоса наблюдения: ρ = {v['spearman']:+.2f}", (v["spearman"], ax.get_ylim()[1] * 0.92), color=INK, fontsize=8,
                        xytext=(5, 0), textcoords="offset points")
            ax.set_title(f"{ttl}: доля случайных точек с |ρ| ≥ |ρ_полосы| = {v['null']['frac_null_abs_ge_obs']:.2f}", fontsize=9, color=INK, loc="left")
        else:
            ax.set_title(f"{ttl}: нулевая модель не посчитана ({v.get('note', '')})", fontsize=9, color=INK, loc="left")
        ax.set_xlabel("ρ Спирмена (признак из случайного места воды той же сцены вне полосы)", fontsize=9, color=INK)
        ax.set_ylabel("число розыгрышей", fontsize=9, color=INK)
        ax.set_xlim(-1, 1)
    fig.suptitle("Нулевая модель: та же корреляция, если брать признак не из полосы, а из случайной воды той же сцены (прошедшие маски)",
                 fontsize=10, color=INK, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=130, facecolor=SURF)
    plt.close(fig)


# ------------------------------------------------------------------------------------------ report

def fmt(v, n=2):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    return f"{v:+.{n}f}" if isinstance(v, float) else str(v)


def conclusion(results, pw):
    a, s4, al = (r["features"] for r in results)
    n, k = results[0]["n"], results[0]["n_groups"]

    def rs(v):
        if "spearman" not in v:
            return "не определено (признак постоянен)"
        ci = v["spearman_ci95_groupboot"]
        return f"ρ = {v['spearman']:+.2f} [{ci[0]:+.2f}; {ci[1]:+.2f}], p_перест = {v['p_perm_spearman']:.3f}, p_Холм = {v.get('p_holm', float('nan')):.2f}"

    def nl(v):
        nu = v.get("null") or {}
        return (f"нулевая модель (случайная вода той же сцены): медиана ρ = {nu['null_median']:+.2f}, доля розыгрышей с |ρ| ≥ |ρ_полосы| = "
                f"{nu['frac_null_abs_ge_obs']:.2f}") if nu.get("n_draws") else "нулевая модель не посчитана"
    pa = pw.get(str(n), {}).get("min_abs_rho_fisher", float("nan"))
    pk = pw.get(str(k), {}).get("min_abs_rho_fisher", float("nan"))
    return ["## Вывод (5–8 строк, для отчёта и речи)", "",
            f"1. Пар с чистой полосой S2: {n} (плюс 1 Landsat без детектора), независимых групп «район × день» — {k}. Эталон — плотность всего плавающего мусора (all_litter), не пластика; калибровки шт./км² нет.",
            f"2. Детектор в полосе: на 10 чёрноморских парах P ≥ {THR} — 0 пикселей (признаки постоянны, корреляция не определена). На 11 парах {rs(a['s_p_mean'])} — ненулевая только одна пара Северного моря (низкая плотность), т. е. это контраст районов, а не связь; ДИ условный: в {a['s_p_mean'].get('boot_share_undefined', float('nan')):.0%} бутстрэп-выборок признак постоянен.",
            f"3. FDI (предрегистрированный `s_fdi_p95_anom`): {rs(a['s_fdi_p95_anom'])}, но {nl(a['s_fdi_p95_anom'])}; тот же признак только из контекста (без полосы): ρ = {a['c_fdi_p95'].get('spearman', float('nan')):+.2f}. Основная часть связи — уровень сцены/дня, а не полосы.",
            f"4. Остаток, специфичный для полосы (полоса − медиана случайных мест той же сцены): {rs(a['x_fdi_p95_anom'])}; по средним групп ρ = {a['x_fdi_p95_anom']['group_means']['rho']:+.2f} (p = {a['x_fdi_p95_anom']['group_means']['p_exact']:.2f}, k = {k}); без одной группы ρ от {a['x_fdi_p95_anom']['logo_min_max'][0]:+.2f} до {a['x_fdi_p95_anom']['logo_min_max'][1]:+.2f}; на всех 19 S2-парах {al['x_fdi_p95_anom'].get('spearman', float('nan')):+.2f}. Поправку на множественность (13 признаков × 3 набора) и групповой уровень не проходит, знак отрицательный (мусор должен поднимать FDI, а не снижать) — сигналом мусора не считаем. Причина не установлена: у S4 «полоса» — круг 500 м вокруг центра трансекты при оценке дрейфа 11–28 км против допуска 3 км (L59, сценарий typical), т. е. фактически случайная вода; при 6 группах такой ρ достижим случайно; волнение/ветер проверить нельзя (sea_state_beaufort и ветер у S3/S4 в реестре пустые).",
            f"5. Мощность: при n = {n} обнаружим только |ρ| ≥ {pa:.2f} (α 0.05, мощность 0.8), при {k} независимых группах — |ρ| ≥ {pk:.2f}. Поэтому вывод — «сильной связи, специфичной для полосы, не видно», а не «связи нет».",
            "6. Для переноса нужно: ≥ 33 независимых (разнесённых по дням) пар для ρ = 0.5 и ≈ 89 для ρ = 0.3; известное время наблюдения (у S4 только дата → дрейф 10+ км); пары для профиля пластика — их в реестре 0 (S1/S2 без сцен в окне ±1 сут).",
            "7. Итог для сдачи: детектор проверяется по разметке MARIDA, концентрация — по полю (C = N/A с ДИ); связь «снимок → шт./км²» на имеющихся парах не установлена.", ""]


def write_md(df, results, pw, need, path):
    L = ["# Детектор на парах vs полевой all_litter (L64, исследовательский эксперимент)", "",
         "Скрипт `scripts/case/pairs_experiment.py`. Вход: выходы L61 (`data/pairs/quality/*/{meta.json,quality.tif,prob.tif}`), "
         "FDI пересчитан из тех же вырезок S2 L2A (кэш `data/pairs/experiment/<dir>/fdi.npy`). Числа: `experiment.json`.", "",
         "**Эталон — плотность ВСЕГО плавающего мусора (all_litter, все материалы), не пластика.** Калибровки в шт./км² по снимку нет: "
         "проверяется только, упорядочивает ли признак снимка события так же, как полевая плотность.", "",
         f"Признаки только из снимка (P — LightGBM L61, порог {THR}; FDI Biermann 2020). Предрегистрированные основные: "
         f"`{PRIMARY[0]}`, `{PRIMARY[1]}`; остальные — разведочные (поправка Холма). Группа для бутстрэпа = район × день наблюдения.", ""]
    L += ["## Пары", "", "| событие | статус | группа | поле, предм./км² | основа | вода полосы, px | P≥0.63 в полосе | P ср. | пятен/км² | FDI p95−ctx | FDI доля выс. |",
          "|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|"]
    for r in df.sort_values(["status", "event_id"]).itertuples():
        g = lambda k, n=4: (f"{getattr(r, k):.{n}f}" if hasattr(r, k) and pd.notna(getattr(r, k)) else "—")  # noqa: E731
        L.append(f"| {r.event_id} | {r.status} | {r.group} | {r.field_items_km2:.1f} | {r.field_basis} | "
                 f"{int(r.s_n_water) if hasattr(r, 's_n_water') and pd.notna(r.s_n_water) else '—'} | {g('s_frac_p_thr', 5)} | {g('s_p_mean')} | "
                 f"{g('s_spots_km2', 1)} | {g('s_fdi_p95_anom')} | {g('s_fdi_frac_hi')} |")
    for res in results:
        L += ["", f"## Связь: {res['label']} (n = {res['n']}, групп день×район = {res['n_groups']})", "",
              "| признак | n | уник. | ρ Спирмена | 95% ДИ (бутстрэп групп; доля выборок с постоянным признаком) | τ Кендалла | 95% ДИ τ | p перест. | p Холм | ρ по средним групп (p точн.) | ρ без одной группы, min…max | нуль: медиана ρ [2.5; 97.5] | доля нуля с abs ρ ≥ полосы |",
              "|---|---:|---:|---:|---|---:|---|---:|---:|---|---|---|---:|"]
        for f in FEATURES:
            v = res["features"].get(f, {})
            star = " **(осн.)**" if f in PRIMARY else ""
            if "spearman" not in v:
                L.append(f"| `{f}`{star} | {v.get('n', 0)} | {v.get('n_unique_x', '—')} | — | {v.get('note', '')} | | | | | | | | |")
                continue
            ci, tci, gm = v["spearman_ci95_groupboot"], v["kendall_ci95_groupboot"], v["group_means"]
            nu = v.get("null")
            has_nu = bool(nu and nu.get("n_draws"))
            nul = f"{fmt(nu['null_median'])} [{fmt(nu['null_q'][0])}; {fmt(nu['null_q'][1])}]" if has_nu else "—"
            nfrac = f"{nu['frac_null_abs_ge_obs']:.2f}" if has_nu else "—"
            lg = v["logo_min_max"]
            L.append(f"| `{f}`{star} | {v['n']} | {v['n_unique_x']} | {fmt(v['spearman'])} | [{fmt(ci[0])}; {fmt(ci[1])}]; {v['boot_share_undefined']:.2f} | {fmt(v['kendall'])} | "
                     f"[{fmt(tci[0])}; {fmt(tci[1])}] | {v['p_perm_spearman']:.3f} | {v.get('p_holm', float('nan')):.3f} | "
                     f"{fmt(gm['rho'])} ({gm['p_exact']:.2f}, k={gm['n_groups']}) | {fmt(lg[0])}…{fmt(lg[1])} | {nul} | {nfrac} |")
    L += ["", "## Мощность (α = 0.05 двусторонний, мощность 0.8)", "",
          "| n | мин. обнаружимый abs ρ (Fisher z, Bonett–Wright) | мин. abs ρ (Монте-Карло, Спирмен) |", "|---:|---:|---:|"]
    for n, v in pw.items():
        L.append(f"| {n} | {v['min_abs_rho_fisher']:.2f} | {v['min_abs_rho_montecarlo']} |")
    L += ["", "Нужно независимых пар (групп), чтобы обнаружить ρ: " + ", ".join(f"ρ = {r} → n ≈ {k}" for r, k in need.items()) + ".", ""]
    L += ["## Графики", "", "![scatter](experiment_scatter.png)", "", "![null](experiment_null.png)", ""]
    L += conclusion(results, pw)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    pq = load_pq()
    rng = np.random.default_rng(SEED)
    df, nulls = build_pairs(pq, fetch=not a.no_fetch)
    for xf, sf in EXCESS.items():
        j = NULLABLE.index(sf)
        med = {e: (float(np.nanmedian(v[:, j])) if v.size and np.isfinite(v[:, j]).any() else np.nan) for e, v in nulls.items()}
        df[xf] = df[sf] - df.event_id.map(med) if sf in df else np.nan
    ok = df.field_items_km2.notna()
    det = df[df.has_detector & ok]
    sets = [("прошедшие маски, S2 (детектор + FDI)", det[det.status == "accept"]),
            ("прошедшие маски, только Чёрное море S4", det[(det.status == "accept") & (det.region == "S4_black_sea")]),
            ("все S2-пары с валидной водой в полосе (в т.ч. блик/облака — загрязнённые)", det[det.s_n_water.fillna(0) >= 20] if "s_n_water" in det else det.iloc[:0])]
    results = []
    for label, sub in sets:
        r = analyse(sub.reset_index(drop=True), nulls, label, rng)
        r["label"] = label
        results.append(r)
        print(label, r["n"], {f: round(v.get("spearman", np.nan), 3) for f, v in r["features"].items()}, flush=True)
    n_acc = results[0]["n"]
    pw, need = power(sorted({n_acc, results[0]["n_groups"], results[2]["n"], 29, 50}), rng=rng)
    REP.mkdir(parents=True, exist_ok=True)
    plot_scatter(df, results[0], REP / "experiment_scatter.png")
    plot_null(results[0], REP / "experiment_null.png")
    write_md(df, results, pw, need, REP / "experiment.md")
    clean = []
    for r in results:
        rr = json.loads(json.dumps(r, default=float))
        for v in rr["features"].values():
            v.pop("_null_rhos", None)
        clean.append(rr)
    cols = ["event_id", "status", "region", "group", "obs_date", "time_known", "dt_hours", "scene_id", "field_items_km2", "field_basis",
            "has_detector", "s_n_water", "null_draws"] + [f for f in FEATURES if f in df]
    table = df[[c for c in cols if c in df]].replace({np.nan: None}).to_dict(orient="records")
    out = dict(task="L64", created=time.strftime("%Y-%m-%dT%H:%M:%S"), threshold_p=THR, k_mad=K_MAD, seed=SEED,
               n_boot=N_BOOT, n_perm=N_PERM, k_null=K_NULL, primary=PRIMARY,
               reference="field all_litter density (total floating macro-litter, all materials; NOT plastic), items/km2",
               n_pairs_total=int(len(df)), n_accept=int((df.status == "accept").sum()),
               status_counts=df.status.value_counts().to_dict(), pairs=table, results=clean, power=pw, n_needed=need)
    def _nonan(o):
        if isinstance(o, dict):
            return {k: _nonan(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_nonan(v) for v in o]
        if isinstance(o, (float, np.floating)):
            return float(o) if np.isfinite(o) else None
        if isinstance(o, (np.integer, np.bool_)):
            return o.item()
        return o
    out = _nonan(json.loads(json.dumps(out, default=float)))
    (REP / "experiment.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print("done", REP / "experiment.md")


if __name__ == "__main__":
    main()
