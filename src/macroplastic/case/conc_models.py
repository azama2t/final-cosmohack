"""Кандидаты основной модели концентрации шт./км² и бейзлайн (критерий Т3; запуск — scripts/case/conc_model_cv.py).

Все модели обучаются на одном профиле (один метод, размерный класс, судно) и предсказывают C_i
в шт./км² для события. Вход — только поля, известные ДО подсчёта предметов (RAW_INPUTS):
координаты, дата/время, условия наблюдения S1 (ветер, Бофорт), платформа/метод как категория.
Поля ответа (selection.LEAK_EXPLICIT: concentration_*, items_count, density_numerator_items,
source_*_items, reported_*, parent_*, zero_scope) в X не попадают — assert_no_leak() на входах и
на итоговых колонках X. sampled_area_km2 (A) в X НЕ входит: это план съёмки (не ответ), используется
только как вес/offset пуассоновской модели (C = N/A, N ~ Poisson(A·λ(x))).

Модели:
  median      — медиана обучающего профиля (бейзлайн постановки);
  mean        — среднее обучающего профиля;
  geomean     — среднее в log1p-пространстве, expm1 (геометрическое среднее, устойчиво к хвосту);
  pooled      — ΣN/ΣA (≡ Σ(C·A)/ΣA): MLE общей интенсивности Пуассона, пуассоновская модель без признаков;
  knn5_log    — среднее log1p(C) 5 ближайших train-событий по большому кругу, соседи ±1 сут запрещены (как в основном сплите);
  ridge_log   — Ridge(alpha=1) на log1p(C), стандартизованные признаки;
  poisson_glm — PoissonRegressor(alpha=0.1, log-link) на C с весом A: эквивалентно N ~ Poisson, offset log A;
  tweedie_glm — TweedieRegressor(power=1.5, alpha=0.1, log-link) на C с весом A;
  lgbm_log    — LightGBM (100 деревьев, 4 листа, lr 0.05, min_child_samples 5) на log1p(C).
Гиперпараметры фиксированы заранее (не подбираются ни на dev, ни на test).

Интервал прогноза (90 %): квантили q05/q95 лог-остатков r = log1p(y) − log1p(ŷ), полученных
out-of-fold на обучающей части (вложенная CV по участкам маршрута с буфером) →
[expm1(log1p(ŷ)+q05), expm1(log1p(ŷ)+q95)], q05 ≤ 0 ≤ q95 (интервал всегда содержит ŷ), низ ≥ 0.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from . import concentration as C
from . import selection as S
from . import splits as P

RAW_INPUTS = ["latitude", "longitude", "date_utc", "datetime_start_iso", "wind_speed_kn",
              "sea_state_beaufort", "platform", "sampling_method"]
WEATHER = ["wind_speed_kn", "sea_state_beaufort"]
CATEGORICAL = ["platform", "sampling_method"]
MODEL_NAMES = ["median", "mean", "geomean", "pooled", "knn5_log", "ridge_log", "poisson_glm",
               "tweedie_glm", "lgbm_log"]
BASELINE = "median"
Q_LO, Q_HI = 0.05, 0.95


# ---------------------------------------------------------------- признаки
def _utc(df: pd.DataFrame) -> pd.Series:
    t = pd.to_datetime(df.get("datetime_start_iso"), utc=True, errors="coerce")
    d = pd.to_datetime(df.get("date_utc"), utc=True, errors="coerce") + pd.Timedelta(hours=12)
    return t.fillna(d)


def base_features(df: pd.DataFrame) -> pd.DataFrame:
    """Признаки без обучения: координаты, сезон (день года sin/cos), местное солнечное время
    (UTC + lon/15, sin/cos), ветер/волнение как есть. Только из RAW_INPUTS."""
    S.assert_no_leak(RAW_INPUTS)
    t = _utc(df)
    doy = t.dt.dayofyear.to_numpy(float)
    hour = (t.dt.hour + t.dt.minute / 60.0).to_numpy(float)
    lon = df["longitude"].to_numpy(float)
    solar = np.mod(hour + lon / 15.0, 24.0)
    X = pd.DataFrame({"latitude": df["latitude"].to_numpy(float), "longitude": lon,
                      "doy_sin": np.sin(2 * np.pi * doy / 365.25), "doy_cos": np.cos(2 * np.pi * doy / 365.25),
                      "hour_sin": np.sin(2 * np.pi * solar / 24), "hour_cos": np.cos(2 * np.pi * solar / 24)},
                     index=df.index)
    for c in WEATHER:
        X[c] = pd.to_numeric(df[c], errors="coerce") if c in df else np.nan
    for c in CATEGORICAL:
        X[c] = df[c].astype(str) if c in df else ""
    return X


@dataclass
class FeatureSpec:
    """Какие колонки берутся (решается по train): погода — если заполнена ≥ 80 % train;
    категории — one-hot, если в train > 1 значения (внутри профиля — константа → не берётся)."""
    numeric: list = field(default_factory=list)
    fill: dict = field(default_factory=dict)
    cats: dict = field(default_factory=dict)

    @classmethod
    def fit(cls, Xb: pd.DataFrame) -> "FeatureSpec":
        num = ["latitude", "longitude", "doy_sin", "doy_cos", "hour_sin", "hour_cos"]
        num += [c for c in WEATHER if Xb[c].notna().mean() >= 0.8]
        fill = {c: float(Xb[c].median()) for c in num}
        cats = {c: sorted(Xb[c].unique().tolist()) for c in CATEGORICAL if Xb[c].nunique() > 1}
        return cls(num, fill, cats)

    def transform(self, Xb: pd.DataFrame) -> pd.DataFrame:
        X = Xb[self.numeric].astype(float).fillna(self.fill)
        for c, levels in self.cats.items():
            for lv in levels[1:]:
                X[f"{c}={lv}"] = (Xb[c] == lv).astype(float)
        S.assert_no_leak(X.columns)
        return X


# ---------------------------------------------------------------- модели
class Model:
    name = "base"
    params: dict = {}

    def fit(self, df: pd.DataFrame, y: np.ndarray, area: Optional[np.ndarray] = None) -> "Model":
        raise NotImplementedError

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        raise NotImplementedError

    def to_dict(self) -> dict:
        return {"name": self.name, "params": self.params}


class Constant(Model):
    def __init__(self, kind: str):
        self.name, self.params = kind, {}

    def fit(self, df, y, area=None):
        y = np.asarray(y, float)
        if self.name == "median":
            self.value = float(np.median(y))
        elif self.name == "mean":
            self.value = float(np.mean(y))
        elif self.name == "geomean":
            self.value = float(np.expm1(np.mean(np.log1p(y))))
        elif self.name == "pooled":
            a = np.ones_like(y) if area is None else np.asarray(area, float)
            self.value = float(np.sum(y * a) / np.sum(a))
        else:
            raise ValueError(self.name)
        return self

    def predict(self, df):
        return np.full(len(df), self.value)

    def to_dict(self):
        return {"name": self.name, "params": {}, "value": self.value}


class KNNLog(Model):
    def __init__(self, k: int = 5, exclude_days: float = 1.0):
        self.name, self.params = f"knn{k}_log", {"k": k, "exclude_days": exclude_days}
        self.k, self.exclude_days = k, exclude_days

    def fit(self, df, y, area=None):
        self.lat = np.radians(df["latitude"].to_numpy(float))
        self.lon = np.radians(df["longitude"].to_numpy(float))
        self.t = P.times_days(df)
        self.ly = np.log1p(np.asarray(y, float))
        return self

    def predict(self, df):
        lat = df["latitude"].to_numpy(float)
        lon = df["longitude"].to_numpy(float)
        t = P.times_days(df)
        D = P.haversine_km(lat[:, None], lon[:, None], np.degrees(self.lat)[None, :], np.degrees(self.lon)[None, :])
        D = np.where(np.abs(t[:, None] - self.t[None, :]) <= self.exclude_days, np.inf, D)   # запрет соседей ±1 сут
        out = np.empty(len(df))
        for i in range(len(df)):
            ok = np.isfinite(D[i])
            order = np.argsort(np.where(ok, D[i], np.inf), kind="stable")[:min(self.k, int(ok.sum()))]
            out[i] = np.expm1(self.ly[order].mean()) if len(order) else np.expm1(self.ly.mean())
        return out

    def to_dict(self):
        """Опорные точки dev (координаты, время, log1p C) — модель kNN целиком."""
        return {"name": self.name, "params": self.params, "features": ["latitude", "longitude"],
                "note": "соседи с |Δt| ≤ exclude_days не берутся",
                "points": {"lat": np.degrees(self.lat).round(6).tolist(), "lon": np.degrees(self.lon).round(6).tolist(),
                           "t_days": np.round(self.t, 5).tolist(), "log1p_c": np.round(self.ly, 6).tolist()}}


class _Linear(Model):
    def fit(self, df, y, area=None):
        from sklearn.preprocessing import StandardScaler
        Xb = base_features(df)
        self.spec = FeatureSpec.fit(Xb)
        X = self.spec.transform(Xb)
        self.cols = list(X.columns)
        self.scaler = StandardScaler().fit(X.to_numpy())
        Z = self.scaler.transform(X.to_numpy())
        self._fit(Z, np.asarray(y, float), None if area is None else np.asarray(area, float))
        return self

    def _Z(self, df):
        return self.scaler.transform(self.spec.transform(base_features(df)).to_numpy())

    def to_dict(self):
        return {"name": self.name, "params": self.params, "features": self.cols,
                "scaler_mean": self.scaler.mean_.tolist(), "scaler_scale": self.scaler.scale_.tolist(),
                "coef": self.est.coef_.tolist(), "intercept": float(self.est.intercept_),
                "fill": self.spec.fill, "cats": self.spec.cats}


class RidgeLog(_Linear):
    def __init__(self, alpha: float = 1.0):
        self.name, self.params, self.alpha = "ridge_log", {"alpha": alpha}, alpha

    def _fit(self, Z, y, area):
        from sklearn.linear_model import Ridge
        self.est = Ridge(alpha=self.alpha).fit(Z, np.log1p(y))

    def predict(self, df):
        return np.clip(np.expm1(self.est.predict(self._Z(df))), 0, None)


class GLMRate(_Linear):
    """Регрессия интенсивности λ(x) = exp(xβ) по C = N/A с весом A.
    Пуассон: Σ A·(C log λ − λ) = Σ (N log λ − A λ) + const — ровно правдоподобие N ~ Poisson(A·λ),
    т.е. offset log(A). Tweedie p = 1.5 — то же с большей дисперсией (сверхдисперсия)."""

    def __init__(self, power: float = 1.0, alpha: float = 0.1):
        self.power, self.alpha = power, alpha
        self.name = "poisson_glm" if power == 1.0 else "tweedie_glm"
        self.params = {"power": power, "alpha": alpha, "link": "log", "weight": "sampled_area_km2"}

    def _fit(self, Z, y, area):
        from sklearn.linear_model import PoissonRegressor, TweedieRegressor
        w = np.ones_like(y) if area is None else area / area.mean()
        if self.power == 1.0:
            self.est = PoissonRegressor(alpha=self.alpha, max_iter=3000, tol=1e-8)
        else:
            self.est = TweedieRegressor(power=self.power, alpha=self.alpha, link="log", max_iter=3000, tol=1e-8)
        self.est.fit(Z, y, sample_weight=w)

    def predict(self, df):
        return self.est.predict(self._Z(df))


class LGBMLog(Model):
    def __init__(self, n_estimators: int = 100, learning_rate: float = 0.05, num_leaves: int = 4,
                 min_child_samples: int = 5, seed: int = 42):
        self.name = "lgbm_log"
        self.params = dict(n_estimators=n_estimators, learning_rate=learning_rate, num_leaves=num_leaves,
                           min_child_samples=min_child_samples, seed=seed)

    def fit(self, df, y, area=None):
        import lightgbm as lgb
        Xb = base_features(df)
        self.spec = FeatureSpec.fit(Xb)
        X = self.spec.transform(Xb)
        self.cols = list(X.columns)
        p = self.params
        self.est = lgb.LGBMRegressor(n_estimators=p["n_estimators"], learning_rate=p["learning_rate"],
                                     num_leaves=p["num_leaves"], min_child_samples=p["min_child_samples"],
                                     random_state=p["seed"], deterministic=True, force_row_wise=True,
                                     n_jobs=1, verbose=-1)
        self.est.fit(X.to_numpy(), np.log1p(np.asarray(y, float)))
        return self

    def predict(self, df):
        X = self.spec.transform(base_features(df)).to_numpy()
        return np.clip(np.expm1(self.est.predict(X)), 0, None)

    def to_dict(self):
        return {"name": self.name, "params": self.params, "features": self.cols}


def make_model(name: str) -> Model:
    if name in ("median", "mean", "geomean", "pooled"):
        return Constant(name)
    if name == "knn5_log":
        return KNNLog(5, 1.0)
    if name == "ridge_log":
        return RidgeLog(1.0)
    if name == "poisson_glm":
        return GLMRate(1.0, 0.1)
    if name == "tweedie_glm":
        return GLMRate(1.5, 0.1)
    if name == "lgbm_log":
        return LGBMLog()
    raise ValueError(name)


def fit_model(name: str, df: pd.DataFrame) -> Model:
    return make_model(name).fit(df, df["target"].to_numpy(float), df["sampled_area_km2"].to_numpy(float))


# ---------------------------------------------------------------- CV и интервалы
def route_folds(df: pd.DataFrame, k_blocks: int) -> np.ndarray:
    _, block = P.route_block_groups(df, k_blocks)
    return block


def buffered_mask(df: pd.DataFrame, fold: np.ndarray, f: int, buffer_days: float) -> np.ndarray:
    t = P.times_days(df)
    te = fold == f
    tr = ~te
    if buffer_days > 0 and te.any():
        tr &= np.abs(t[:, None] - t[te][None, :]).min(axis=1) > buffer_days
    return tr


def oof_predictions(name: str, df: pd.DataFrame, k_blocks: int = 5, buffer_days: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Out-of-fold предсказания по участкам маршрута с буфером → (ŷ, fold)."""
    fold = route_folds(df, k_blocks)
    pred = np.full(len(df), np.nan)
    for f in np.unique(fold):
        te = fold == f
        tr = buffered_mask(df, fold, f, buffer_days)
        pred[te] = fit_model(name, df[tr]).predict(df[te])
    return pred, fold


def log_residual_quantiles(y: np.ndarray, yhat: np.ndarray, q_lo: float = Q_LO, q_hi: float = Q_HI) -> tuple[float, float]:
    r = np.log1p(np.asarray(y, float)) - np.log1p(np.asarray(yhat, float))
    return min(float(np.quantile(r, q_lo)), 0.0), max(float(np.quantile(r, q_hi)), 0.0)


def interval(yhat: np.ndarray, q: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    ly = np.log1p(np.asarray(yhat, float))
    lo = np.clip(np.expm1(ly + q[0]), 0, None)
    hi = np.expm1(ly + q[1])
    return np.minimum(lo, yhat), np.maximum(hi, yhat)


def nested_cv(name: str, df: pd.DataFrame, k_blocks: int = 5, buffer_days: float = 1.0,
              inner_blocks: int = 4) -> pd.DataFrame:
    """Внешняя CV (участки маршрута + буфер) с интервалом из вложенной CV на обучающей части:
    q05/q95 лог-остатков считаются только по train внешнего фолда → честное покрытие на test."""
    fold = route_folds(df, k_blocks)
    rows = []
    for f in np.unique(fold):
        te = fold == f
        tr = buffered_mask(df, fold, f, buffer_days)
        dtr = df[tr].reset_index(drop=True)
        inner_pred, _ = oof_predictions(name, dtr, inner_blocks, buffer_days)
        ok = np.isfinite(inner_pred)
        q = log_residual_quantiles(dtr["target"].to_numpy(float)[ok], inner_pred[ok])
        m = fit_model(name, dtr)
        yp = m.predict(df[te])
        lo, hi = interval(yp, q)
        rows.append(pd.DataFrame({"sample_id": df.loc[te, "sample_id"].to_numpy(),
                                  "event_id": df.loc[te, "event_id"].to_numpy(),
                                  "cruise_day": P.cruise_day_keys(df[te]).to_numpy(),
                                  "fold": int(f), "n_train": int(tr.sum()),
                                  "y_true": df.loc[te, "target"].to_numpy(float), "y_pred": yp,
                                  "lo": lo, "hi": hi, "q_lo": q[0], "q_hi": q[1], "model": name}))
    return pd.concat(rows, ignore_index=True)


def metrics_with_interval(g: pd.DataFrame) -> dict:
    m = C.all_metrics(g.y_true, g.y_pred)
    inside = (g.y_true >= g.lo) & (g.y_true <= g.hi)
    m.update(coverage90=float(inside.mean()), width_median=float((g.hi - g.lo).median()),
             width_rel_median=float(((g.hi - g.lo) / g.y_pred.clip(lower=1e-9)).median()))
    return m


def bootstrap_diff(pr: pd.DataFrame, a: str, b: str, metric: str = "mae", unit: str = "cruise_day",
                   n_boot: int = 2000, seed: int = 0, level: float = 0.95) -> tuple[float, float, float]:
    """ДИ (level) разности metric(a) − metric(b), ресэмплинг дней рейса (единица зависимости)."""
    rng = np.random.default_rng(seed)
    pa = pr[pr.model == a].set_index("sample_id").sort_index()
    pb = pr[pr.model == b].set_index("sample_id").loc[pa.index]
    if metric == "mae":
        ea, eb = (pa.y_pred - pa.y_true).abs().to_numpy(), (pb.y_pred - pb.y_true).abs().to_numpy()
    elif metric == "log1p_mae":
        ea = (np.log1p(pa.y_pred) - np.log1p(pa.y_true)).abs().to_numpy()
        eb = (np.log1p(pb.y_pred) - np.log1p(pb.y_true)).abs().to_numpy()
    else:
        raise ValueError(metric)
    grp = pa[unit].to_numpy()
    ug = np.unique(grp)
    idx_by = {g: np.nonzero(grp == g)[0] for g in ug}
    diffs = np.empty(n_boot)
    for i in range(n_boot):
        pick = np.concatenate([idx_by[g] for g in rng.choice(ug, size=len(ug), replace=True)])
        diffs[i] = ea[pick].mean() - eb[pick].mean()
    a_ = (1 - level) / 2 * 100
    return float(ea.mean() - eb.mean()), float(np.percentile(diffs, a_)), float(np.percentile(diffs, 100 - a_))


def model_features(name: str, X_cols: list) -> list:
    """Какие признаки реально использует модель (для конфига и отчёта)."""
    if name in ("median", "mean", "geomean", "pooled"):
        return []
    if name.startswith("knn"):
        return ["latitude", "longitude"]
    return list(X_cols)


def select_primary(table: pd.DataFrame, baseline: str = BASELINE) -> tuple[str, str]:
    """Правило принятия (зафиксировано до запуска, configs/case_conc_model.yaml: protocol):
    кандидат принимается, если верхняя граница 95 % ДИ разности MAE(кандидат) − MAE(медиана)
    по дням рейса < 0; из принятых — наименьший CV-MAE. Нет принятых → основная = медиана."""
    ok = table[(table.model != baseline) & (table.d_mae_hi < 0)]
    if len(ok):
        best = ok.sort_values(["mae", "model"]).iloc[0]
        return str(best.model), (f"{best.model}: MAE {best.mae:.1f} < медианы, ДИ разности "
                                 f"[{best.d_mae_lo:+.1f}; {best.d_mae_hi:+.1f}] не содержит 0")
    return baseline, "ни один кандидат не лучше медианы с ДИ разности MAE, не пересекающим 0"


# ---------------------------------------------------------------- данные dev / test
def load_profile_parts(profile: str, cfg: Optional[dict] = None) -> dict:
    """→ {'all': принятые записи с колонкой role, 'dev': только dev}. Проверяет sha256 состава
    против configs/case_selection.yaml: final_test. Test-строки в 'dev' не попадают."""
    cfg = cfg or S.load_config()
    acc, _ = S.select(S.load_samples(), cfg, profile)
    fz = cfg["final_test"]
    ft = P.final_test_split(acc.drop(columns=["target"]), profile, seed=fz["seed"], k_blocks=fz["k_blocks"],
                            buffer_days=fz["buffer_days"])
    exp = fz["profiles"][profile]
    if P.composition_sha256(ft, "test") != exp["test_sha256"] or P.composition_sha256(ft, "dev") != exp["dev_sha256"]:
        raise RuntimeError(f"{profile}: состав final_test/dev не совпадает с зафиксированным sha256")
    acc = acc.merge(ft[["sample_id", "role"]], on="sample_id", how="left")
    return {"all": acc, "dev": acc[acc.role == "dev"].reset_index(drop=True)}


def save_json(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
