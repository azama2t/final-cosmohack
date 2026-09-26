"""L119 (INBOX §23 п.5): полевой прогноз шт./км² для размерного профиля ADIS — без спутника.

Проверка зафиксирована ДО расчёта метрик: configs/adis_forecast.yaml (sha256 в .sha256; скрипт отказывается
работать при расхождении). Что делает:
  1. C = N / A по каждому отрезку ADIS (10 км, одна камера) с точным (Гарвуд) 95 % интервалом Пуассона;
  2. два разбиения leave-group-out: регионы 10°×10° (основное) и суда (вторичное); отложенная доля групп — test;
  3. на train — GroupKFold(5) по группам → out-of-fold прогноз (val) для M0 медиана, B1 медиана по широтной
     зоне × сезону, M1 LightGBM log1p, M2 LightGBM Poisson с offset log A; интервал 90 % (вложенно для M1/M2);
  4. правило принятия (§24) по val; test — один раз в конце для M0, B1 и выбранного кандидата.
Признаки — только известное без счёта: широта, sin/cos долготы, sin/cos дня года, ln расстояния до берега.

Запуск (CPU, ≤ 6 потоков; кэш расстояния до берега — out/adis_forecast/):
  .venv/Scripts/python.exe scripts/case/adis_forecast.py val     # только val (test не читается)
  .venv/Scripts/python.exe scripts/case/adis_forecast.py final   # val + решение + test один раз → отчёт
Выход: reports/quantity/adis_forecast.{json,md}; out/adis_forecast/{segments_C.csv,val_*.csv,test_*.csv}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("OMP_NUM_THREADS", "6")

import numpy as np
import pandas as pd
import yaml
from scipy.stats import chi2

ROOT = Path(__file__).resolve().parents[2]
CFG = ROOT / "configs" / "adis_forecast.yaml"
CFG_SHA = ROOT / "configs" / "adis_forecast.yaml.sha256"
OUT = ROOT / "out" / "adis_forecast"
REP = ROOT / "reports" / "quantity"
GSHHS_DIR = Path.home() / ".local/share/cartopy/shapefiles/gshhs/i"
FEATURES = ["lat", "lon_sin", "lon_cos", "doy_sin", "doy_cos", "log_dist_coast_km"]
MODELS = ["M0_median", "B1_band_season_median", "M1_gbm_log1p", "M2_gbm_poisson"]
CANDIDATES = MODELS[1:]
REF = "M0_median"
N_BOOT = 2000
B1_MIN = 30
Q_LO, Q_HI = 0.05, 0.95
RATE_FLOOR = 0.01  # items/km², только для Poisson-девиансa (M0 = 0 даёт бесконечность)
GBM = dict(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=100, subsample=0.8,
           subsample_freq=1, colsample_bytree=1.0, reg_lambda=1.0, n_jobs=6, random_state=119, verbose=-1)


# ------------------------------------------------------------------ config
def load_cfg() -> dict:
    want = CFG_SHA.read_text(encoding="utf-8").split()[0].strip()
    got = hashlib.sha256(CFG.read_bytes()).hexdigest()
    if want != got:
        sys.exit(f"configs/adis_forecast.yaml changed after freezing: {got} != {want}")
    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    cfg["_sha256"] = got
    return cfg


# ------------------------------------------------------------------ field value C = N / A
def garwood(n: np.ndarray, conf: float = 0.95) -> tuple[np.ndarray, np.ndarray]:
    """Векторный точный интервал Пуассона — та же формула, что concentration.poisson_count_interval."""
    n = np.asarray(n, float)
    a = 1 - conf
    lo = np.where(n > 0, chi2.ppf(a / 2, np.maximum(2 * n, 1e-12)) / 2, 0.0)
    hi = chi2.ppf(1 - a / 2, 2 * n + 2) / 2
    return lo, hi


def season(month: pd.Series) -> pd.Series:
    return (month % 12) // 3  # 0 DJF, 1 MAM, 2 JJA, 3 SON


def load_segments(cfg: dict) -> pd.DataFrame:
    dc = cfg["data"]
    d = pd.read_csv(ROOT / dc["file"])
    d["Ship"] = d["Ship"].replace(dc["ship_name_fix"])
    d = d[(d["area_scanned_km2"] >= 0.1) & (d["nphotos"] > 0)].reset_index(drop=True)
    t = pd.to_datetime(d["timestamp"], format="mixed")
    out = pd.DataFrame({
        "SegmentID": d["SegmentID"].astype(int), "ship": d["Ship"], "t": t,
        "lat": d["Latitude"].astype(float), "lon": d["Longitude"].astype(float),
        "A": d["area_scanned_km2"].astype(float)})
    for k, c in cfg["profile"]["classes"].items():
        out[f"N_{k}"] = d[c["count"]].astype(float)
    out["region"] = ((np.floor(out.lat / 10) * 10).astype(int).astype(str) + "_"
                     + (np.floor(out.lon / 10) * 10).astype(int).astype(str))
    out["band20"] = (np.floor(out.lat / 20) * 20).astype(int)
    out["season"] = season(out.t.dt.month)
    out["shipday"] = out.ship + "|" + out.t.dt.strftime("%Y-%m-%d")
    doy = out.t.dt.dayofyear.to_numpy(float)
    out["lon_sin"], out["lon_cos"] = np.sin(np.radians(out.lon)), np.cos(np.radians(out.lon))
    out["doy_sin"], out["doy_cos"] = np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)
    out["dist_coast_km"] = dist_coast(out)
    out["log_dist_coast_km"] = np.log1p(out["dist_coast_km"])
    return out


def dist_coast(seg: pd.DataFrame) -> np.ndarray:
    """Расстояние до ближайшей вершины береговой линии GSHHS i (L1 + L5 Антарктида), км; кэш по SegmentID."""
    cache = OUT / "dist_coast.csv"
    if cache.exists():
        c = pd.read_csv(cache).set_index("SegmentID")["dist_coast_km"]
        if set(seg.SegmentID) <= set(c.index):
            return c.loc[seg.SegmentID].to_numpy(float)
    import shapefile  # pyshp (зависимость cartopy)
    from sklearn.neighbors import BallTree
    pts = []
    for lvl in ("L1", "L5"):
        for shp in shapefile.Reader(str(GSHHS_DIR / f"GSHHS_i_{lvl}.shp")).iterShapes():
            pts.append(np.asarray(shp.points, float))
    P = np.concatenate(pts)  # lon, lat
    tree = BallTree(np.radians(P[:, ::-1]), metric="haversine")
    dist, _ = tree.query(np.radians(seg[["lat", "lon"]].to_numpy(float)), k=1)
    km = dist[:, 0] * 6371.0088
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"SegmentID": seg.SegmentID, "dist_coast_km": km}).to_csv(cache, index=False)
    return km


# ------------------------------------------------------------------ splits
def test_groups(groups: pd.Series, seed: int, target: float, cap: float, group_cap: float) -> list[str]:
    sz = groups.value_counts()
    n = int(sz.sum())
    order = sorted(sz.index, key=lambda g: hashlib.sha256(f"{seed}:{g}".encode()).hexdigest())
    chosen, s = [], 0
    for g in order:
        if s / n >= target:
            break
        if (s + sz[g]) / n > cap or sz[g] / n > group_cap:
            continue
        chosen.append(g)
        s += int(sz[g])
    return chosen


def make_split(seg: pd.DataFrame, cfg: dict, scheme: str) -> pd.Series:
    """'test' / 'train' по группам схемы (region / ship)."""
    sp = cfg["splits"]
    col = "region" if scheme == "region" else "ship"
    tg = set(test_groups(seg[col], sp["seed"], sp["test_fraction_target"], sp["test_fraction_cap"],
                         sp["test_group_cap"]))
    return pd.Series(np.where(seg[col].isin(tg), "test", "train"), index=seg.index)


def group_col(scheme: str) -> str:
    return "region" if scheme == "region" else "ship"


def gkf(groups: np.ndarray, k: int):
    from sklearn.model_selection import GroupKFold
    k = min(k, len(np.unique(groups)))
    return list(GroupKFold(n_splits=k).split(np.zeros(len(groups)), groups=groups))


# ------------------------------------------------------------------ models
def _lgbm(objective: str):
    import lightgbm as lgb
    return lgb.LGBMRegressor(objective=objective, **GBM)


def fit_predict(name: str, tr: pd.DataFrame, te: pd.DataFrame, prof: str) -> tuple[np.ndarray, dict]:
    """Точечный прогноз C для te по модели, обученной на tr; + параметры интервала для M0/B1."""
    y = (tr[f"N_{prof}"] / tr.A).to_numpy(float)
    if name == "M0_median":
        return np.full(len(te), np.median(y)), {"lo": np.full(len(te), np.quantile(y, Q_LO)),
                                                   "hi": np.full(len(te), np.quantile(y, Q_HI))}
    if name == "B1_band_season_median":
        key_tr = tr.band20.astype(str) + "|" + tr.season.astype(str)
        key_te = te.band20.astype(str) + "|" + te.season.astype(str)
        st = pd.DataFrame({"k": key_tr.to_numpy(), "y": y}).groupby("k")["y"]
        cnt, med = st.size(), st.median()
        qlo, qhi = st.quantile(Q_LO), st.quantile(Q_HI)
        ok = key_te.map(cnt).fillna(0).to_numpy() >= B1_MIN
        pt = np.where(ok, key_te.map(med).to_numpy(float), np.median(y))
        lo = np.where(ok, key_te.map(qlo).to_numpy(float), np.quantile(y, Q_LO))
        hi = np.where(ok, key_te.map(qhi).to_numpy(float), np.quantile(y, Q_HI))
        return pt, {"lo": lo, "hi": hi, "fallback_share": float(1 - ok.mean())}
    X, Xt = tr[FEATURES].to_numpy(float), te[FEATURES].to_numpy(float)
    if name == "M1_gbm_log1p":
        m = _lgbm("regression").fit(X, np.log1p(y))
        return np.clip(np.expm1(m.predict(Xt)), 0, None), {}
    if name == "M2_gbm_poisson":
        m = _lgbm("poisson").fit(X, tr[f"N_{prof}"].to_numpy(float), init_score=np.log(tr.A.to_numpy(float)))
        return np.exp(m.predict(Xt, raw_score=True)), {}
    raise ValueError(name)


def predict_with_interval(name: str, tr: pd.DataFrame, te: pd.DataFrame, prof: str, gcol: str) -> pd.DataFrame:
    pt, extra = fit_predict(name, tr, te, prof)
    if name in ("M0_median", "B1_band_season_median"):
        lo, hi = extra["lo"], extra["hi"]
        q = (np.nan, np.nan)
    else:  # вложенная GroupKFold(3) на обучающей части → квантили лог-остатков
        inner = np.full(len(tr), np.nan)
        for a, b in gkf(tr[gcol].to_numpy(), 3):
            inner[b], _ = fit_predict(name, tr.iloc[a], tr.iloc[b], prof)
        r = np.log1p((tr[f"N_{prof}"] / tr.A).to_numpy(float)) - np.log1p(inner)
        q = (min(float(np.quantile(r, Q_LO)), 0.0), max(float(np.quantile(r, Q_HI)), 0.0))
        lo = np.clip(np.expm1(np.log1p(pt) + q[0]), 0, None)
        hi = np.expm1(np.log1p(pt) + q[1])
    lo, hi = np.minimum(lo, pt), np.maximum(hi, pt)
    return pd.DataFrame({"SegmentID": te.SegmentID.to_numpy(), "model": name, "y_pred": pt, "lo": lo, "hi": hi,
                         "q_lo": q[0], "q_hi": q[1],
                         "b1_fallback_share": extra.get("fallback_share", np.nan)})


def base_rows(te: pd.DataFrame, prof: str) -> pd.DataFrame:
    return pd.DataFrame({"SegmentID": te.SegmentID.to_numpy(), "region": te.region.to_numpy(),
                         "ship": te.ship.to_numpy(), "shipday": te.shipday.to_numpy(),
                         "N": te[f"N_{prof}"].to_numpy(float), "A": te.A.to_numpy(float),
                         "y_true": (te[f"N_{prof}"] / te.A).to_numpy(float)})


def val_oof(seg: pd.DataFrame, role: pd.Series, scheme: str, prof: str, k: int = 5) -> pd.DataFrame:
    tr_all = seg[role == "train"].reset_index(drop=True)
    gcol = group_col(scheme)
    rows = []
    for f, (a, b) in enumerate(gkf(tr_all[gcol].to_numpy(), k)):
        tr, te = tr_all.iloc[a].reset_index(drop=True), tr_all.iloc[b].reset_index(drop=True)
        base = base_rows(te, prof)
        for name in MODELS:
            p = predict_with_interval(name, tr, te, prof, gcol)
            rows.append(base.assign(fold=f, n_train=len(tr)).merge(p, on="SegmentID"))
    return pd.concat(rows, ignore_index=True)


def test_pred(seg: pd.DataFrame, role: pd.Series, scheme: str, prof: str, names: list[str]) -> pd.DataFrame:
    tr = seg[role == "train"].reset_index(drop=True)
    te = seg[role == "test"].reset_index(drop=True)
    base = base_rows(te, prof)
    return pd.concat([base.assign(fold=-1, n_train=len(tr)).merge(
        predict_with_interval(n, tr, te, prof, group_col(scheme)), on="SegmentID") for n in names],
        ignore_index=True)


# ------------------------------------------------------------------ metrics
def errors(g: pd.DataFrame) -> dict[str, np.ndarray]:
    mu = np.maximum(g.y_pred.to_numpy(float), RATE_FLOOR) * g.A.to_numpy(float)
    N = g.N.to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        dev = 2 * (np.where(N > 0, N * np.log(N / mu), 0.0) - (N - mu))
    return {"mae": np.abs(g.y_pred - g.y_true).to_numpy(float),
            "log1p_mae": np.abs(np.log1p(g.y_pred) - np.log1p(g.y_true)).to_numpy(float),
            "se": ((g.y_pred - g.y_true) ** 2).to_numpy(float), "dev": dev}


def metrics(g: pd.DataFrame) -> dict:
    e = errors(g)
    inside = (g.y_true >= g.lo) & (g.y_true <= g.hi)
    return {"n": int(len(g)), "mae": float(e["mae"].mean()), "log1p_mae": float(e["log1p_mae"].mean()),
            "rmse": float(np.sqrt(e["se"].mean())), "poisson_dev": float(e["dev"].mean()),
            "bias": float((g.y_pred - g.y_true).mean()), "coverage90": float(inside.mean()),
            "width_median": float((g.hi - g.lo).median()), "pred_median": float(g.y_pred.median()),
            "share_pred_zero": float((g.y_pred <= 0).mean())}


def boot_delta(pr: pd.DataFrame, a: str, b: str, unit: str, seed: int = 0) -> dict:
    """95 % ДИ разности metric(a) − metric(b) (mae, log1p_mae), ресэмплинг единиц unit (группы)."""
    pa = pr[pr.model == a].set_index("SegmentID").sort_index()
    pb = pr[pr.model == b].set_index("SegmentID").loc[pa.index]
    ea, eb = errors(pa.reset_index()), errors(pb.reset_index())
    codes, ug = pd.factorize(pa[unit])
    G = len(ug)
    cnt = np.bincount(codes, minlength=G).astype(float)
    rng = np.random.default_rng(seed)
    W = np.stack([np.bincount(rng.integers(0, G, G), minlength=G) for _ in range(N_BOOT)]).astype(float)
    out = {"unit": unit, "n_units": int(G)}
    for m in ("mae", "log1p_mae"):
        sa = np.bincount(codes, weights=ea[m], minlength=G)
        sb = np.bincount(codes, weights=eb[m], minlength=G)
        d = (W @ sa - W @ sb) / (W @ cnt)
        out[m] = {"delta": float(ea[m].mean() - eb[m].mean()),
                  "lo": float(np.percentile(d, 2.5)), "hi": float(np.percentile(d, 97.5))}
    return out


def passes(bd: dict) -> bool:
    return bool(bd["mae"]["hi"] < 0 and bd["log1p_mae"]["delta"] <= 0)


def evaluate(pr: pd.DataFrame, unit: str, names: list[str]) -> dict:
    res = {}
    for n in names:
        g = pr[pr.model == n]
        r = {"metrics": metrics(g)}
        if n != REF:
            r["vs_M0"] = boot_delta(pr, n, REF, unit)
            r["vs_M0_shipday"] = boot_delta(pr, n, REF, "shipday")
            r["passes_rule"] = passes(r["vs_M0"])
        if n == "B1_band_season_median":
            r["b1_fallback_share"] = float(g.b1_fallback_share.mean())
        res[n] = r
    return res


# ------------------------------------------------------------------ field summary (C = N/A, Poisson)
def field_summary(seg: pd.DataFrame, prof: str) -> dict:
    N, A = seg[f"N_{prof}"].to_numpy(float), seg.A.to_numpy(float)
    lo, hi = garwood(N.sum()[None])
    c = N / A
    by = seg.assign(N=N).groupby("region").agg(n_seg=("A", "size"), N=("N", "sum"), A=("A", "sum"),
                                                 ships=("ship", "nunique"))
    blo, bhi = garwood(by.N.to_numpy())
    by["C"] = by.N / by.A
    by["C_lo"], by["C_hi"] = blo / by.A, bhi / by.A
    top = by[by.A >= 20].sort_values("C", ascending=False)
    return {"n_segments": int(len(seg)), "sum_N": float(N.sum()), "sum_A_km2": float(A.sum()),
            "C_pooled": float(N.sum() / A.sum()), "C_pooled_lo": float(lo[0] / A.sum()),
            "C_pooled_hi": float(hi[0] / A.sum()), "share_zero": float((N == 0).mean()),
            "C_seg_median": float(np.median(c)), "C_seg_p75": float(np.quantile(c, 0.75)),
            "C_seg_p90": float(np.quantile(c, 0.90)), "C_seg_max": float(c.max()),
            "regions_A_ge_20km2": int(len(top)),
            "regions_top5": top.head(5).reset_index().round(3).to_dict("records"),
            "regions_bottom5": top.tail(5).reset_index().round(3).to_dict("records")}


# ------------------------------------------------------------------ внешняя оценка: калибровка авторов ADIS (Г1-1, §28 Б)
ADIS_PAPER = "de Vries et al. 2026, Environ. Res. Commun., doi 10.1088/2515-7620/ae8152"


def external_calibrated(cfg: dict) -> dict:
    """Калиброванная авторами ADIS плотность (dhat_*_calibrated, lo95/hi95) — ВНЕШНЯЯ справка.
    Не признак, не цель модели, не замена нашей C = N/A. Те же отрезки (A ≥ 0.1 км², nphotos > 0)."""
    d = pd.read_csv(ROOT / cfg["data"]["file"])
    d = d[(d["area_scanned_km2"] >= 0.1) & (d["nphotos"] > 0)].reset_index(drop=True)
    A = d["area_scanned_km2"].to_numpy(float)
    n10, n50 = d["n_objects>10cm"].to_numpy(float), d["n_objects>50cm"].to_numpy(float)
    c10 = d["dhat_10cm_calibrated"].to_numpy(float)
    res = {"source": ADIS_PAPER, "dataset": "4TU 10.4121/ddede7f5-aca5-42ae-b851-e0bbb9a2c4c2 (CC BY 4.0)",
           "checks": {"readme_label_10cm": {
               "cal10_zero_where_n10_zero": bool(np.all(c10[n10 == 0] == 0)),
               "cal10_positive_where_n50_zero": int(((c10 > 0) & (n50 == 0)).sum()),
               "corr_cal10_vs_n10_over_A": float(np.corrcoef(c10, n10 / A)[0, 1]),
               "corr_cal10_vs_n50_over_A": float(np.corrcoef(c10, n50 / A)[0, 1]),
               "verdict": "dhat_10cm_calibrated* — класс > 10 см; подпись «>50 cm» в Readme — ошибка копирования"}},
           "profiles": {}}
    for p, sz in (("gt10cm", "10cm"), ("gt50cm", "50cm")):
        n = d[f"n_objects>{sz}"].to_numpy(float)
        obs = d[f"dhat_{sz}"].to_numpy(float)
        c = d[f"dhat_{sz}_calibrated"].to_numpy(float)
        lo, hi = d[f"dhat_{sz}_calibrated_lo95"].to_numpy(float), d[f"dhat_{sz}_calibrated_hi95"].to_numpy(float)
        neg = c < 0
        ok, pos = ~neg, (c > 0)
        has_iv = pos & np.isfinite(lo) & np.isfinite(hi)
        point = float((c[ok] * A[ok]).sum() / A[ok].sum())
        r_lo = float((lo[has_iv] * A[has_iv]).sum() / (c[has_iv] * A[has_iv]).sum())
        r_hi = float((hi[has_iv] * A[has_iv]).sum() / (c[has_iv] * A[has_iv]).sum())
        ratio_obs = obs[n > 0] / (n[n > 0] / A[n > 0])
        po = pos & (obs > 0)
        res["profiles"][p] = {
            "n_segments": int(len(d)), "ours_raw_C": float(n.sum() / A.sum()),
            "authors_observed_C": float((obs * A).sum() / A.sum()),
            "authors_observed_over_raw_median": float(np.median(ratio_obs)),
            "authors_observed_over_raw_minmax": [float(ratio_obs.min()), float(ratio_obs.max())],
            "calibrated_C": point, "calibrated_lo_corr": point * r_lo, "calibrated_hi_corr": point * r_hi,
            "calibrated_lo_typ": point * float(np.median(lo[has_iv] / c[has_iv])),
            "calibrated_hi_typ": point * float(np.median(hi[has_iv] / c[has_iv])),
            "cal_over_observed_median": float(np.median(c[po] / obs[po])),
            "seg_lo_over_c_median": float(np.median(lo[has_iv] / c[has_iv])),
            "seg_hi_over_c_median": float(np.median(hi[has_iv] / c[has_iv])),
            "n_negative_calibrated": int(neg.sum()), "n_positive": int(pos.sum()),
            "n_positive_without_interval": int((pos & ~has_iv).sum())}
    return res


def external_md(o: dict) -> list[str]:
    e = o.get("external_adis_calibrated")
    if not e:
        return []
    ch = e["checks"]["readme_label_10cm"]
    g10 = e["profiles"]["gt10cm"]
    L = ["## 1б. Рядом — ВНЕШНЯЯ оценка: калибровка авторов ADIS (не наша, не признак, не прогноз)", "",
         f"Источник: {e['source']}; данные {e['dataset']}, колонки `dhat_*`, `dhat_*_calibrated`, `*_lo95`, `*_hi95` (шт./км²). "
         "По статье: наблюдаемая плотность = счёт / площадь обзора × 2 (поправка на спад обнаружения с расстоянием); "
         "калиброванная — плюс мультипликативные поправки на освещение (солнечный аспект) и скорость и перевод к "
         "«эквиваленту мега-трала» (в тексте 1 : 5.7 для 10–50 см, 1 : 4.8 для > 50 см); интервал логнормальный, "
         "дисперсии поправок и счёта сложены (Тейлор 1-го порядка).", "",
         "| профиль | наша C = ΣN/ΣA (без поправок) | авторы: наблюдаемая | **авторы: калиброванная, ΣC·A/ΣA** | "
         "95 % диапазон (типичная ширина интервала авторов) | медиана калибр. / наблюд. | отрезков с C_cal < 0 (исключены) | C_cal > 0 без интервала |",
         "|---|---:|---:|---:|---|---:|---:|---:|"]
    for p, r in e["profiles"].items():
        lab = "> 10 см" if p == "gt10cm" else "> 50 см"
        L.append(f"| {lab} | {r['ours_raw_C']:.2f} | {r['authors_observed_C']:.2f} | **{r['calibrated_C']:.2f}** | "
                 f"{r['calibrated_lo_typ']:.2f}–{r['calibrated_hi_typ']:.2f} | {r['cal_over_observed_median']:.2f} | "
                 f"{r['n_negative_calibrated']} | {r['n_positive_without_interval']} из {r['n_positive']} |")
    L += ["", "Оговорки (проверено по файлу 26.09):",
          f"- Подпись Readme у `dhat_10cm_calibrated*` («>50 cm») — ошибка: колонка = 0 везде, где предметов > 10 см нет, и > 0 в "
          f"{ch['cal10_positive_where_n50_zero']} отрезках без предметов > 50 см; корреляция с N>10/A {ch['corr_cal10_vs_n10_over_A']:.2f}, "
          f"с N>50/A {ch['corr_cal10_vs_n50_over_A']:.2f}. Это класс > 10 см.",
          f"- «Наблюдаемая» авторов на каждом отрезке ровно в {g10['authors_observed_over_raw_median']:.0f} раза больше нашей N/A "
          "(их поправка ×2 на спад обнаружения).",
          "- В файле калиброванная / наблюдаемая — медиана ≈ 1.4–1.5 (по судам 1.2–2.9), а не 5.7 / 4.8 из текста статьи; точная "
          "формула — в приложении S1, его мы не сверяли. Число авторов берём как есть и не пересчитываем.",
          "- Есть отрезки с отрицательной калиброванной плотностью и отрицательными границами (артефакт расчёта у авторов) — они "
          "исключены; у части положительных отрезков интервала нет (NaN).",
          f"- 95 % диапазон = средняя × медианные отношения lo/C и hi/C по отрезкам с интервалом (×{g10['seg_lo_over_c_median']:.2f} … "
          f"×{g10['seg_hi_over_c_median']:.1f} для > 10 см): допущение, что ошибка поправки общая для всех отрезков и не усредняется. "
          "Своего интервала для суммы по отрезкам авторы не дают. Если взвешивать границы площадью (Σlo·A / ΣC·A, Σhi·A / ΣC·A), верх "
          f"уходит до {g10['calibrated_hi_corr']:.0f} шт./км² из-за единичных отрезков с hi/C в сотни раз — это видно в json "
          "(`calibrated_*_corr`), для показа не берём.",
          "- В прогнозе (раздел 2) и в правиле принятия эти колонки НЕ участвуют: в них зашит счёт (утечка). Наше число не "
          "заменяется (§24); калиброванное — справка «по данным авторов ADIS».", ""]
    return L


# ------------------------------------------------------------------ run
def run(stage: str) -> dict:
    t0 = time.time()
    cfg = load_cfg()
    seg = load_segments(cfg)
    OUT.mkdir(parents=True, exist_ok=True)
    profiles = [cfg["profile"]["primary"]] + list(cfg["profile"]["sensitivity"])
    # C = N/A по отрезку с интервалом Пуассона (кэш для карты/проверки)
    segC = seg[["SegmentID", "ship", "t", "lat", "lon", "A", "region", "dist_coast_km"]].copy()
    for p in profiles:
        lo, hi = garwood(seg[f"N_{p}"].to_numpy())
        segC[f"N_{p}"] = seg[f"N_{p}"]
        segC[f"C_{p}"], segC[f"C_{p}_lo95"], segC[f"C_{p}_hi95"] = seg[f"N_{p}"] / seg.A, lo / seg.A, hi / seg.A
    segC.to_csv(OUT / "segments_C.csv", index=False)

    out = {"L": "L119", "generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "config": "configs/adis_forecast.yaml",
           "config_sha256": cfg["_sha256"], "features": FEATURES, "gbm_params": GBM, "stage": stage,
           "field": {p: field_summary(seg, p) for p in profiles}, "schemes": {}, "decision": {}}
    for scheme in ("region", "ship"):
        role = make_split(seg, cfg, scheme)
        gcol = group_col(scheme)
        sc = {"group": gcol, "primary": scheme == "region",
              "n_train": int((role == "train").sum()), "n_test": int((role == "test").sum()),
              "groups_train": int(seg.loc[role == "train", gcol].nunique()),
              "groups_test": int(seg.loc[role == "test", gcol].nunique()),
              "test_groups": sorted(seg.loc[role == "test", gcol].unique().tolist()),
              "test_share": float((role == "test").mean()), "profiles": {}}
        tr_g, te_g = set(seg.loc[role == "train", gcol]), set(seg.loc[role == "test", gcol])
        assert not (tr_g & te_g), "группа в train и test"
        for p in profiles:
            pr = val_oof(seg, role, scheme, p)
            pr.to_csv(OUT / f"val_{scheme}_{p}.csv", index=False)
            sc["profiles"][p] = {"val": evaluate(pr, gcol, MODELS),
                                 "val_folds": int(pr.fold.nunique())}
            print(f"[{time.time() - t0:5.0f}s] val {scheme} {p}", flush=True)
        out["schemes"][scheme] = sc

    for p in profiles:
        vr, vs = out["schemes"]["region"]["profiles"][p]["val"], out["schemes"]["ship"]["profiles"][p]["val"]
        ok = [c for c in CANDIDATES if vr[c]["passes_rule"] and vs[c]["passes_rule"]]
        sel = min(ok, key=lambda c: vr[c]["metrics"]["mae"]) if ok else None
        out["decision"][p] = {"passed_val_both": ok, "selected_on_val": sel,
                              "flag_low_coverage": bool(sel and min(vr[sel]["metrics"]["coverage90"],
                                                                    vs[sel]["metrics"]["coverage90"]) < 0.80)}

    if stage == "final":
        log = OUT / "test_touch.log"
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"{out['generated']} test scored, config {cfg['_sha256']}\n")
        for scheme in ("region", "ship"):
            role = make_split(seg, cfg, scheme)
            for p in profiles:
                sel = out["decision"][p]["selected_on_val"]
                names = ["M0_median", "B1_band_season_median"] + ([sel] if sel and sel not in
                                                                   ("B1_band_season_median",) else [])
                pr = test_pred(seg, role, scheme, p, names)
                pr.to_csv(OUT / f"test_{scheme}_{p}.csv", index=False)
                out["schemes"][scheme]["profiles"][p]["test"] = evaluate(pr, group_col(scheme), names)
        for p in profiles:
            dec = out["decision"][p]
            sel = dec["selected_on_val"]
            if sel is None:
                dec["final"] = "M0_median"
                dec["why"] = ("ни один кандидат (B1, M1, M2) не прошёл правило на val в обеих схемах → "
                              "медиана профиля остаётся")
            else:
                tr_ = out["schemes"]["region"]["profiles"][p]["test"][sel]
                acc = tr_["passes_rule"]
                dec["test_region_passes"] = acc
                dec["final"] = sel if acc else "M0_median"
                dec["why"] = (f"{sel} прошёл val и test (регионы)" if acc else
                              f"{sel} прошёл val, но не подтвердился на отложенных регионах → медиана")
        out["decision_primary"] = out["decision"][cfg["profile"]["primary"]]["final"]
    out["external_adis_calibrated"] = external_calibrated(cfg)
    out["runtime_s"] = round(time.time() - t0, 1)
    return out


# ------------------------------------------------------------------ report
def _f(x, d=2):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{d}f}"


def _row(name: str, r: dict, d=2) -> str:
    m = r["metrics"]
    if "vs_M0" in r:
        b = r["vs_M0"]
        dm = f"{b['mae']['delta']:+.2f} [{b['mae']['lo']:+.2f}; {b['mae']['hi']:+.2f}]"
        dl = f"{b['log1p_mae']['delta']:+.3f} [{b['log1p_mae']['lo']:+.3f}; {b['log1p_mae']['hi']:+.3f}]"
        s = r["vs_M0_shipday"]["mae"]
        ds = f"[{s['lo']:+.2f}; {s['hi']:+.2f}]"
        ok = "да" if r["passes_rule"] else "нет"
    else:
        dm = dl = ds = "—"
        ok = "эталон"
    return (f"| {name} | {m['n']} | {_f(m['mae'])} | {_f(m['log1p_mae'], 3)} | {_f(m['rmse'])} | "
            f"{_f(m['poisson_dev'])} | {m['coverage90'] * 100:.0f} % | {_f(m['pred_median'])} | {dm} | {dl} | {ds} | {ok} |")


HDR = ("| модель | n отрезков | MAE, шт./км² | MAE log1p | RMSE | Poisson-девианс | покрытие 90 % | медиана прогноза | "
       "ΔMAE − M0 [95 % ДИ, группы] | ΔMAE log1p − M0 [95 % ДИ] | ΔMAE, ДИ по судо-суткам* | правило |\n"
       "|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|---|")
LABEL = {"M0_median": "M0 медиана train (эталон)", "B1_band_season_median": "B1 медиана зона 20° × сезон",
         "M1_gbm_log1p": "M1 LightGBM log1p", "M2_gbm_poisson": "M2 LightGBM Poisson + offset ln A"}


def render_md(o: dict, cfg: dict) -> str:
    prim = cfg["profile"]["primary"]
    L = [f"# L119 — полевой прогноз шт./км² для профиля ADIS (INBOX §23 п.5)", "",
         f"Сгенерировано `scripts/case/adis_forecast.py {o['stage']}` ({o['generated']}). Правило зафиксировано до расчёта "
         f"метрик: `configs/adis_forecast.yaml`, sha256 `{o['config_sha256'][:16]}…` (скрипт не запустится при расхождении).",
         "Только поле: признаки — широта, sin/cos долготы, sin/cos дня года, ln расстояния до берега (GSHHS i). "
         "Спутниковых признаков нет (это L110 `reports/quantity/cell_calibration.md`). Ветра/течений нет (офлайн недоступны, "
         "ничего не скачивали).", ""]
    if "decision_primary" in o:
        d = o["decision"][prim]
        L += [f"**Решение (профиль > 10 см, правило §24): {LABEL[d['final']]}** — {d['why']}.", ""]
    L += ["## 1. Поле: C = N / A по отрезкам (измерение)", "",
          "| профиль | отрезков | ΣN / ΣA | C [95 % Пуассон] | доля нулевых | медиана / p75 / p90 / макс отрезка |",
          "|---|---:|---|---|---:|---|"]
    for p, f in o["field"].items():
        L.append(f"| {cfg['profile']['classes'][p]['label']} | {f['n_segments']} | {f['sum_N']:.0f} / {f['sum_A_km2']:.0f} км² | "
                 f"{f['C_pooled']:.2f} [{f['C_pooled_lo']:.2f}–{f['C_pooled_hi']:.2f}] | {f['share_zero'] * 100:.0f} % | "
                 f"{f['C_seg_median']:.2f} / {f['C_seg_p75']:.2f} / {f['C_seg_p90']:.2f} / {f['C_seg_max']:.0f} |")
    f = o["field"][prim]
    L += ["", f"Регионы 10° (ΣA ≥ 20 км², {f['regions_A_ge_20km2']} шт.), профиль > 10 см — самые высокие и низкие ΣN/ΣA [Пуассон 95 %]:", "",
          "| регион 10° | отрезков | судов | ΣN | ΣA, км² | C [95 %] |", "|---|---:|---:|---:|---:|---|"]
    for r in f["regions_top5"] + f["regions_bottom5"]:
        L.append(f"| {r['region']} | {r['n_seg']} | {r['ships']} | {r['N']:.0f} | {r['A']:.1f} | {r['C']:.2f} [{r['C_lo']:.2f}–{r['C_hi']:.2f}] |")
    L += ["", "Отрезок ADIS — 10 км одной камеры (площадь обзора медиана ≈ 0.8 км²); интервал — только счётная (пуассоновская) "
          "ошибка, без поправки на пропуски мелких предметов. Отрезок на отрезке: C = N / A; отрезки двух бортов одного прохода зависимы.", ""]
    L += external_md(o)
    for scheme, sc in o["schemes"].items():
        name = "регионы 10° × 10° (основное)" if scheme == "region" else "суда (вторичное)"
        L += [f"## 2{'a' if scheme == 'region' else 'b'}. Разбиение: {name}", "",
              f"Отложено (test) {sc['groups_test']} групп / {sc['n_test']} отрезков ({sc['test_share'] * 100:.1f} %); "
              f"train {sc['groups_train']} групп / {sc['n_train']} отрезков; val — GroupKFold(5) по группам train.",
              f"Test-группы: {', '.join(sc['test_groups'])}.", ""]
        for p, pp in sc["profiles"].items():
            lab = cfg["profile"]["classes"][p]["label"] + (" — **основной**" if p == prim else " — чувствительность")
            L += [f"### {lab}: val (out-of-fold, {pp['val_folds']} фолдов)", "", HDR]
            for n, r in pp["val"].items():
                L.append(_row(LABEL[n], r))
            b1 = pp["val"]["B1_band_season_median"].get("b1_fallback_share")
            L.append("")
            if b1 is not None:
                L.append(f"B1: доля отрезков, где страта < {B1_MIN} отрезков и взята общая медиана, — {b1 * 100:.0f} %.")
                L.append("")
            if "test" in pp:
                L += [f"### {lab}: отложенный test (один раз)", "", HDR]
                for n, r in pp["test"].items():
                    L.append(_row(LABEL[n], r))
                L.append("")
    L += ["\\* ДИ по судо-суткам — дополнительно, в правило не входит. Poisson-девианс: прогноз < 0.01 шт./км² "
          "заменён на 0.01 (иначе у нулевой медианы бесконечность); в правило не входит.", "",
          "## 3. Решение по правилу (зафиксировано до расчёта)", "",
          "Кандидат принимается, если на val в ОБЕИХ схемах верхняя граница 95 % ДИ ΔMAE(кандидат − M0) < 0 (бутстреп по группам) "
          "и ΔMAE log1p ≤ 0; затем то же на отложенном test регионов (один раз). Иначе — медиана профиля.", "",
          "| профиль | прошли val (обе схемы) | выбран по val | итог |", "|---|---|---|---|"]
    for p, d in o["decision"].items():
        L.append(f"| {cfg['profile']['classes'][p]['label']} | {', '.join(d['passed_val_both']) or 'нет'} | "
                 f"{d['selected_on_val'] or '—'} | {LABEL.get(d.get('final', ''), '— (test не считался)')} |")
    v = o["schemes"]["region"]["profiles"][prim]["val"]
    m0, m1, m2 = (v[k]["metrics"] for k in ("M0_median", "M1_gbm_log1p", "M2_gbm_poisson"))
    L += ["", "## 4. Почему медиана «выигрывает» и что это значит", "",
          f"- На уровне отрезка {f['share_zero'] * 100:.0f} % значений — ноль, поэтому медиана train = "
          f"{m0['pred_median']:.2f} шт./км²; MAE минимизируется условной медианой, и прогноз «0» по MAE почти не бьётся.",
          f"- По метрикам вне правила (без ДИ, не для решения; val, регионы, > 10 см): RMSE немного ниже — M0 {m0['rmse']:.2f} → "
          f"M1 {m1['rmse']:.2f} / M2 {m2['rmse']:.2f}; Poisson-девианс {m0['poisson_dev']:.2f} → {m1['poisson_dev']:.2f} / "
          f"{m2['poisson_dev']:.2f} (у M0 значение задаёт пол 0.01 шт./км²). Вероятно, география (регионы 10° различаются по ΣN/ΣA "
          "от 0 до ~4 шт./км², раздел 1) говорит кое-что о СРЕДНЕЙ плотности, но не о типичном отрезке; это не проверено правилом.",
          "- Правило зафиксировано заранее (MAE + MAE log1p); по нему модель не принята. Менять правило после результата нельзя (§24).",
          "- Следствие для карты: «медиана отрезка» ADIS = 0 не означает «чисто»; как описательное число поля честнее ΣN/ΣA профиля "
          "(и по региону) с интервалом Пуассона (раздел 1) — это измерение, а не прогноз.", ""]
    L += ["## 5. Как говорить", "",
          "- Полевая оценка для места без измерений — медиана профиля ADIS; это оценка по полевым данным, не по снимку.",
          "- Прогноз по географии и сезону (без счёта и без спутника) проверен на отложенных регионах и судах; "
          "правило принятия — заранее, выбор — только по val.",
          f"- Интервал поля — точный Пуассон на отрезке; разброс между отрезками намного шире (нулевых {f['share_zero'] * 100:.0f} %).",
          ""]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["val", "final", "render"])
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_cfg()
    if a.stage == "render":  # только перерисовать md из сохранённого json (test не трогается)
        o = json.loads((REP / "adis_forecast.json").read_text(encoding="utf-8"))
        o["external_adis_calibrated"] = external_calibrated(cfg)  # описательное; test не трогается
        (REP / "adis_forecast.json").write_text(json.dumps(o, ensure_ascii=False, indent=1, default=str),
                                                encoding="utf-8")
        (REP / "adis_forecast.md").write_text(render_md(o, cfg), encoding="utf-8")
        return
    o = run(a.stage)
    dst = REP if a.stage == "final" else OUT  # val-прогон не трогает reports/
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "adis_forecast.json").write_text(json.dumps(o, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (dst / "adis_forecast.md").write_text(render_md(o, cfg), encoding="utf-8")
    print(json.dumps(o["decision"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
