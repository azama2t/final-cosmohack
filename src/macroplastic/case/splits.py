"""Группированные разбиения полевых записей (Т4: независимая проверка).

Зависимые записи не разносятся между train и test:
- event   — все строки одного event_id (категории, объекты, повторные позиции) — одна группа;
- scene   — события, привязанные к одной спутниковой сцене (scene_id из data/pairs), — одна группа;
- daycell — «дата × ячейка cell_deg°» (один день рейса в одном районе: одна погода, одно море);
- st      — пространственно-временные группы: связная компонента графа, где ребро между
            записями, если расстояние ≤ R км И разница времени ≤ T сут (транзитивно: цепочка
            станций одного рейса сливается в один блок).
- cruiseday — «день рейса»: источник × судно × дата UTC;
- route   — «участок маршрута»: дни рейса по порядку режутся на k непрерывных блоков, фолд = блок
            (blocked CV по времени); buffered_train_mask() дополнительно убирает из train записи
            ближе buffer_days к test. route + буфер 1 сут — основной сплит (configs/case_selection.yaml),
            т.к. kNN на st/event/daycell выигрывает за счёт соседей того же дня рейса (L60b).
Все схемы дополнительно объединяются с event_id (union-find), так что событие никогда
не разрезается. Фолды — жадная балансировка групп по размеру с фиксированным seed.

Составы сохраняются в data/case/splits/<name>.csv (sample_id, event_id, scene_id, group, fold);
check_split() проверяет 0 общих event_id/групп/сцен между фолдом и остальными и считает
ближайшее расстояние (км) и время (сут) между test-фолдом и train.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[3]
SPLITS_DIR = REPO / "data" / "case" / "splits"
PAIRS_BEST = REPO / "data" / "pairs" / "best_per_event.csv"
EARTH_R_KM = 6371.0088


# ---------------------------------------------------------------- геометрия / время
def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_R_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def times_days(df: pd.DataFrame) -> np.ndarray:
    """Время записи в сутках от эпохи: datetime_start_iso, иначе date_utc + 12:00."""
    t = pd.to_datetime(df.get("datetime_start_iso"), utc=True, errors="coerce")
    d = pd.to_datetime(df.get("date_utc"), utc=True, errors="coerce") + pd.Timedelta(hours=12)
    t = t.fillna(d)
    return (t - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().to_numpy() / 86400.0


# ---------------------------------------------------------------- union-find
class _DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, i):
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, i, j):
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self.p[max(ri, rj)] = min(ri, rj)


def _union_by_key(dsu: _DSU, keys: Sequence) -> None:
    first = {}
    for i, k in enumerate(keys):
        if k is None or (isinstance(k, float) and np.isnan(k)):
            continue
        if k in first:
            dsu.union(first[k], i)
        else:
            first[k] = i


def _labels(dsu: _DSU, n: int, prefix: str) -> np.ndarray:
    roots = [dsu.find(i) for i in range(n)]
    order = {r: j for j, r in enumerate(dict.fromkeys(roots))}
    return np.array([f"{prefix}{order[r]:04d}" for r in roots], dtype=object)


# ---------------------------------------------------------------- схемы групп
def event_groups(df: pd.DataFrame) -> np.ndarray:
    dsu = _DSU(len(df))
    _union_by_key(dsu, df["event_id"].tolist())
    return _labels(dsu, len(df), "ev")


def scene_ids_from_pairs(df: pd.DataFrame, pairs_csv: Path | str = PAIRS_BEST,
                         accepted_only: bool = True) -> pd.Series:
    """scene_id (item_id лучшей пары) для event_id; нет пары → NaN."""
    p = Path(pairs_csv)
    if not p.exists():
        return pd.Series(np.nan, index=df.index, dtype=object)
    b = pd.read_csv(p)
    if accepted_only and "accept" in b:
        b = b[b["accept"].astype(str).str.lower() == "true"]
    m = dict(zip(b["event_id"], b["item_id"]))
    return df["event_id"].map(m)


def scene_groups(df: pd.DataFrame, scene_id: Optional[Iterable] = None) -> np.ndarray:
    dsu = _DSU(len(df))
    _union_by_key(dsu, df["event_id"].tolist())
    if scene_id is not None:
        _union_by_key(dsu, list(scene_id))
    return _labels(dsu, len(df), "sc")


def daycell_groups(df: pd.DataFrame, cell_deg: float = 1.0) -> np.ndarray:
    dsu = _DSU(len(df))
    _union_by_key(dsu, df["event_id"].tolist())
    key = (df["date_utc"].astype(str) + "|" + np.floor(df["latitude"] / cell_deg).astype(int).astype(str)
           + "|" + np.floor(df["longitude"] / cell_deg).astype(int).astype(str))
    _union_by_key(dsu, key.tolist())
    return _labels(dsu, len(df), "dc")


def cruise_keys(df: pd.DataFrame) -> pd.Series:
    """Рейс = источник × платформа (S2: MSM41 на RV Maria S. Merian; S1-трал: RV Ocean Starr)."""
    def col(c):
        return df[c].astype(str) if c in df else pd.Series("", index=df.index)
    return col("source_id") + "|" + col("platform")


def cruise_day_keys(df: pd.DataFrame) -> pd.Series:
    return cruise_keys(df) + "|" + df["date_utc"].astype(str)


def cruiseday_groups(df: pd.DataFrame) -> np.ndarray:
    """«День рейса»: все события одного рейса за одни сутки UTC (+ event_id)."""
    dsu = _DSU(len(df))
    _union_by_key(dsu, df["event_id"].tolist())
    _union_by_key(dsu, cruise_day_keys(df).tolist())
    return _labels(dsu, len(df), "cd")


def route_block_groups(df: pd.DataFrame, k_blocks: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """«Участок маршрута»: дни каждого рейса по порядку режутся на k_blocks непрерывных
    отрезков с примерно равным числом событий; граница блока проходит только между днями,
    поэтому день рейса (и событие) никогда не делится. → (group, block_index 0..k-1).
    Блок i разных рейсов получает один индекс — фолд = блок (blocked CV по времени)."""
    key = cruise_keys(df)
    day = df["date_utc"].astype(str)
    group = np.empty(len(df), dtype=object)
    block = np.zeros(len(df), dtype=int)
    for c in key.unique():
        m = (key == c).to_numpy()
        days = day[m].value_counts().sort_index()
        cum = days.cumsum().to_numpy()
        total = cum[-1]
        # день относится к блоку по доле накопленных событий в его середине
        mid = cum - days.to_numpy() / 2.0
        b = np.minimum((mid / total * k_blocks).astype(int), k_blocks - 1)
        # на случай пустых блоков (дней меньше k) — переиндексация по порядку
        _, b = np.unique(b, return_inverse=True)
        day_to_b = dict(zip(days.index, b))
        bb = day[m].map(day_to_b).to_numpy()
        block[m] = bb
        group[m] = [f"rb|{c}|{i}" for i in bb]
    return group, block


def spatiotemporal_groups(df: pd.DataFrame, radius_km: float = 50.0, days: float = 1.0,
                          scene_id: Optional[Iterable] = None) -> np.ndarray:
    """Связные компоненты: ребро, если dist ≤ radius_km и |Δt| ≤ days (+ event_id, + сцена)."""
    n = len(df)
    dsu = _DSU(n)
    _union_by_key(dsu, df["event_id"].tolist())
    if scene_id is not None:
        _union_by_key(dsu, list(scene_id))
    lat, lon = df["latitude"].to_numpy(float), df["longitude"].to_numpy(float)
    t = times_days(df)
    for i in range(n):
        dist = haversine_km(lat[i], lon[i], lat[i + 1:], lon[i + 1:])
        close = (dist <= radius_km) & (np.abs(t[i + 1:] - t[i]) <= days)
        for j in np.nonzero(close)[0]:
            dsu.union(i, i + 1 + j)
    return _labels(dsu, n, "st")


# ---------------------------------------------------------------- фолды
def assign_folds(groups: Sequence, n_folds: int = 5, seed: int = 42) -> np.ndarray:
    """Группа целиком в одном фолде; жадно по размеру (крупные первыми, порядок равных —
    по seed), в фолд с наименьшим числом строк."""
    g = pd.Series(list(groups))
    sizes = g.value_counts()
    rng = np.random.default_rng(seed)
    uniq = sizes.index.to_numpy()
    rng.shuffle(uniq)
    uniq = sorted(uniq, key=lambda k: -sizes[k])  # стабильная сортировка сохраняет shuffle
    load = np.zeros(n_folds, dtype=int)
    fold_of = {}
    for k in uniq:
        f = int(np.argmin(load))
        fold_of[k] = f
        load[f] += sizes[k]
    return g.map(fold_of).to_numpy()


def make_split(df: pd.DataFrame, method: str = "st", n_folds: int = 5, seed: int = 42,
               radius_km: float = 50.0, days: float = 1.0, cell_deg: float = 1.0,
               scene_id: Optional[Iterable] = None, k_blocks: int = 5) -> pd.DataFrame:
    fold = None
    if method == "event":
        grp = event_groups(df)
    elif method == "scene":
        grp = scene_groups(df, scene_id)
    elif method == "daycell":
        grp = daycell_groups(df, cell_deg)
    elif method == "st":
        grp = spatiotemporal_groups(df, radius_km, days, scene_id)
    elif method == "cruiseday":
        grp = cruiseday_groups(df)
    elif method == "route":
        grp, fold = route_block_groups(df, k_blocks)
    else:
        raise ValueError(method)
    scene = (pd.Series(list(scene_id), index=df.index) if scene_id is not None
             else pd.Series(np.nan, index=df.index, dtype=object))
    out = pd.DataFrame({"sample_id": df["sample_id"].to_numpy(), "event_id": df["event_id"].to_numpy(),
                        "scene_id": scene.to_numpy(), "group": grp,
                        "cruise_day": cruise_day_keys(df).to_numpy(),
                        "fold": assign_folds(grp, n_folds, seed) if fold is None else fold})
    out.attrs.update(method=method, n_folds=n_folds, seed=seed, radius_km=radius_km, days=days,
                     cell_deg=cell_deg, k_blocks=k_blocks)
    return out


def save_split(split: pd.DataFrame, name: str, out_dir: Path | str = SPLITS_DIR) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.csv"
    split.to_csv(path, index=False, encoding="utf-8")
    return path


# ---------------------------------------------------------------- проверка
def check_split(split: pd.DataFrame, df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """По каждому фолду (test) против остальных (train): общие event_id / group / scene_id
    (должно быть 0) и, если передан df с координатами/временем (выровненный с split по
    sample_id), ближайшее расстояние в км, ближайший интервал в сутках и ближайшее
    расстояние среди train-записей в пределах ±1 сут."""
    rows = []
    if df is not None:
        df = df.set_index("sample_id").loc[split["sample_id"]].reset_index()
        lat, lon = df["latitude"].to_numpy(float), df["longitude"].to_numpy(float)
        t = times_days(df)
    for f in sorted(split["fold"].unique()):
        te, tr = split["fold"] == f, split["fold"] != f
        r = {"fold": int(f), "n_test": int(te.sum()), "n_train": int(tr.sum()),
             "test_events": split.loc[te, "event_id"].nunique(),
             "shared_events": len(set(split.loc[te, "event_id"]) & set(split.loc[tr, "event_id"])),
             "shared_groups": len(set(split.loc[te, "group"]) & set(split.loc[tr, "group"])),
             "shared_scenes": len(set(split.loc[te, "scene_id"].dropna())
                                  & set(split.loc[tr, "scene_id"].dropna()))}
        if "cruise_day" in split:
            r["shared_cruise_days"] = len(set(split.loc[te, "cruise_day"]) & set(split.loc[tr, "cruise_day"]))
        if df is not None and tr.any():
            ti, ri = np.nonzero(te.to_numpy())[0], np.nonzero(tr.to_numpy())[0]
            D = haversine_km(lat[ti][:, None], lon[ti][:, None], lat[ri][None, :], lon[ri][None, :])
            T = np.abs(t[ti][:, None] - t[ri][None, :])
            r["min_dist_km"] = float(D.min())
            r["min_dt_days"] = float(T.min())
            within = np.where(T <= 1.0, D, np.inf)
            r["min_dist_km_within_1d"] = float(within.min()) if np.isfinite(within).any() else np.nan
        rows.append(r)
    return pd.DataFrame(rows)


def buffered_train_mask(split: pd.DataFrame, df: pd.DataFrame, fold: int,
                        buffer_days: float = 1.0) -> np.ndarray:
    """Маска train для фолда с временным буфером: из train убираются записи ближе
    buffer_days к любой test-записи (соседние дни рейса на границе блока)."""
    df = df.set_index("sample_id").loc[split["sample_id"]].reset_index()
    t = times_days(df)
    te = (split["fold"] == fold).to_numpy()
    tr = ~te
    if buffer_days > 0 and te.any():
        dt = np.abs(t[:, None] - t[te][None, :]).min(axis=1)
        tr &= dt > buffer_days
    return tr


def assert_no_overlap(split: pd.DataFrame, cruise_day: bool = False) -> None:
    chk = check_split(split)
    bad = chk[(chk.shared_events > 0) | (chk.shared_groups > 0) | (chk.shared_scenes > 0)]
    if cruise_day:
        bad = pd.concat([bad, chk[chk.get("shared_cruise_days", 0) > 0]])
    if len(bad):
        raise AssertionError(f"пересечение групп между фолдами:\n{bad}")
