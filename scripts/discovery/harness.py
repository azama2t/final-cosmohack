"""Оценщик двигателя открытий «снимок S2 → число штук» (версия наборов v2; правка оценщика 26.09 14:03).

Метод = python-файл с функцией
    predict(window_bands: dict[str, np.ndarray], meta: dict) -> (N_items, lo, hi) | None
  None (или N = None) = «нет надёжной оценки» (воздержание) — на A2 это правильный ответ, раз видимого сигнала нет.
  window_bands: 'B1'..'B12' (11 полос без B9, отражение, 10 м, 128×128, NaN = нет данных) + 'SCL' (uint8, 0 = нет);
  meta: roi (bool H×W — зона, где считать предметы), roi_area_km2, pixel_m, epsg, bounds, lon, lat (центр окна),
        date (YYYYMMDD), datetime (или None), has_scl. НИКАКИХ id, набора, класса фона, ответов.
Необязательно:
  predict_cover(window_bands, meta) -> (м², lo, hi)  — площадь пластика в ROI (проверяется на PLP2021);
  fit(train) — train = [dict(bands, meta, N (или nan), cover_m2 (или nan), campaign)] только из A1 (мишени),
               разбиение leave-one-CAMPAIGN-out (PLP2018 / PLP2019 / Maathuis2026 / Themistocleous2020 / PLP2021);
  NAME, DESCRIPTION.

Наборы и правила — configs/discovery_sets_v2.yaml (sha256 манифеста, меток и типов плиток проверяются перед прогоном).
Лидерборд — docs/research/discovery/LEADERBOARD.csv (раздельные колонки), строки только через record().
v1 (одна формула, LODO по датам) — configs/discovery_sets.yaml, строки v1 — LEADERBOARD_v1.csv (не смешиваются).

  .venv/Scripts/python.exe scripts/discovery/harness.py run scripts/discovery/baselines/b1_flat_plp.py
  .venv/Scripts/python.exe scripts/discovery/harness.py board
  .venv/Scripts/python.exe scripts/discovery/harness.py check --deep
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
CFG_PATH = ROOT / "configs" / "discovery_sets_v2.yaml"
LEADERBOARD = ROOT / "docs" / "research" / "discovery" / "LEADERBOARD.csv"
BANDS11 = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
WIN, TILE = 128, 32
MIN_ITEMS = 1.0              # «выдано число»: N̂ ≥ 1 предмет в ROI (A2 воздержание, A3 ложные, A4 обнаружение, зоны)
FP_LIMIT = 0.01
G2_HAT, G2_OBS = 1000.0, 100.0
A5_MIN = 5
N_BOOT = 2000
ZONE_TYPES = ["ship", "foam", "organic", "cloud", "glint", "waves_wakes", "water"]
ZONE_PRIORITY = {t: i for i, t in enumerate(ZONE_TYPES)}
LB_COLUMNS = (["date", "set_version", "method", "code_sha256", "natural_counter",
               "E1_err", "E1_ci_lo", "E1_ci_hi", "B1_bias", "C1_cover", "dE1_vs_b1", "dE1_ci_lo", "dE1_ci_hi",
               "Ec_plp2021_cover", "ABST2", "ABST2_ci_lo", "ABST2_ci_hi", "G2_thousands",
               "FP3", "FP3_ci_lo", "FP3_ci_hi", "Z3_zones_km2", "Z3_ci_lo", "Z3_ci_hi"]
              + [f"Z3_{t}" for t in ZONE_TYPES]
              + ["R4_detect", "R4_ci_lo", "R4_ci_hi", "n_A1", "n_A1_cover", "n_A2", "n_A3", "n_A4", "n_A5", "n_tiles",
                 "n_abstain", "n_errors", "ref_score", "gate_product", "runtime_s", "sets_sha256", "note"])


# ------------------------------------------------------------------------------------------------------------ sets
def load_cfg(path: Path = CFG_PATH) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def set_dir(cfg: dict) -> Path:
    return ROOT / "data" / "discovery" / cfg["version"]


def verify_sets(cfg: dict, deep: bool = False) -> str:
    """Frozen composition check (manifest, labels, tile types sha256 == cfg frozen). Returns short combined sha."""
    fr = cfg.get("frozen") or {}
    d = set_dir(cfg)
    got = {"manifest_sha256": sha256_file(d / "manifest.csv"), "labels_sha256": sha256_file(d / "labels.csv")}
    if fr.get("tile_types_sha256"):
        got["tile_types_sha256"] = sha256_file(d / "tile_types.csv")
    for k, v in got.items():
        if fr.get(k) != v:
            raise RuntimeError(f"набор {cfg['version']}: {k} = {v} не совпадает с frozen {fr.get(k)} — состав изменён; "
                               "новый состав = новая версия (build_sets.py), старые баллы не смешивать")
    if deep:
        m = pd.read_csv(d / "manifest.csv")
        for r in m[m.status == "ok"].itertuples():
            p = (ROOT / "data" / "MARIDA" / r.file.split(":", 1)[1]) if r.file.startswith("MARIDA:") else ROOT / r.file
            if sha256_file(p) != r.sha256:
                raise RuntimeError(f"окно {r.sample_id}: sha256 не совпадает")
    return hashlib.sha256("".join(got[k] for k in sorted(got)).encode()).hexdigest()[:16]


@dataclass
class Sample:
    sample_id: str
    set: str
    group: str
    kind: str
    bands: dict
    meta: dict
    label: dict = field(default_factory=dict)
    fold: str = ""


def _center_lonlat(epsg, bounds):
    from pyproj import Transformer
    x, y = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
    return Transformer.from_crs(f"EPSG:{int(epsg)}", "EPSG:4326", always_xy=True).transform(x, y)


def _marida_date(s: str) -> str:
    d, m, y = s.split("-")
    return f"20{int(y):02d}{int(m):02d}{int(d):02d}"


def _public_meta(raw: dict, roi: np.ndarray, has_scl: bool, kind: str) -> dict:
    lon, lat = _center_lonlat(raw["epsg"], raw["bounds"])
    date = raw.get("date") or ""
    if kind == "marida":
        date = _marida_date(date)
    return dict(roi=roi, roi_area_km2=float(roi.sum()) * 1e-4, pixel_m=10.0, epsg=int(raw["epsg"]),
                bounds=[float(v) for v in raw["bounds"]], lon=float(lon), lat=float(lat), date=str(date),
                datetime=raw.get("datetime"), has_scl=bool(has_scl))


_CACHE: dict = {}


def load_sets(cfg: dict | None = None, sets=("A1", "A2", "A3", "A4", "A5"), verify: bool = True) -> list[Sample]:
    cfg = cfg or load_cfg()
    key = (cfg["version"], tuple(sets))
    if key in _CACHE:
        return _CACHE[key]
    if verify:
        verify_sets(cfg)
    from build_sets import marida_window
    d = set_dir(cfg)
    m = pd.read_csv(d / "manifest.csv", low_memory=False)
    m = m[(m.status == "ok") & m.set.isin(sets)]
    if "fold" not in m:
        m["fold"] = m["group"]
    lab = pd.read_csv(d / "labels.csv").set_index("sample_id")
    cand = pd.read_csv(d / "candidates.csv", low_memory=False)
    bg = dict(zip(cand.sample_id, cand.background)) if "background" in cand else {}
    camp = dict(zip(cand.sample_id, cand.campaign)) if "campaign" in cand else {}
    out = []
    for r in m.to_dict("records"):
        if r["kind"] == "marida":
            rr = dict(r, item_id=r["file"].split(":", 1)[1], date=r["sample_id"].split("-", 1)[1].split("_")[0])
            b, scl, roi, raw = marida_window(rr)
            raw["date"] = rr["date"]
        else:
            z = np.load(ROOT / r["file"], allow_pickle=False)
            b, scl, roi, raw = z["bands"], z["scl"], z["roi"], json.loads(str(z["meta"]))
        bands = {k: b[i] for i, k in enumerate(BANDS11)}
        bands["SCL"] = scl
        L = lab.loc[r["sample_id"]].to_dict()
        b_ = bg.get(r["sample_id"])
        L["background"] = (("marida_" if r["kind"] == "marida" else "") + b_) if isinstance(b_, str) else None
        c_ = camp.get(r["sample_id"])
        L["campaign"] = c_ if isinstance(c_, str) else None
        out.append(Sample(r["sample_id"], r["set"], str(r["group"]), r["kind"], bands,
                          _public_meta(raw, roi, bool(np.asarray(scl).any()), r["kind"]), L, str(r["fold"])))
    _CACHE[key] = out
    return out


def load_tile_types(cfg: dict) -> pd.DataFrame | None:
    f = set_dir(cfg) / "tile_types.csv"
    return pd.read_csv(f) if f.exists() else None


# ---------------------------------------------------------------------------------------------------------- method
def load_method(path: str | Path):
    path = Path(path).resolve()
    spec = importlib.util.spec_from_file_location(f"discovery_method_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "predict"):
        raise AttributeError(f"{path}: нет функции predict(window_bands, meta)")
    return mod


def code_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


def _safe_predict(predict, bands, meta):
    """-> (N, lo, hi, abstained, error). None / N None = abstention; exception / non-finite = error (N̂ 0)."""
    try:
        r = predict(bands, dict(meta))
        if r is None or (isinstance(r, (tuple, list)) and len(r) and r[0] is None):
            return 0.0, 0.0, 0.0, True, ""
        n, lo, hi = (float(v) for v in r)
        if not all(math.isfinite(v) for v in (n, lo, hi)):
            raise ValueError(f"non-finite {r}")
        return max(n, 0.0), max(lo, 0.0), max(hi, 0.0), False, ""
    except Exception as e:  # noqa: BLE001
        return 0.0, 0.0, 0.0, False, f"{type(e).__name__}: {e}"[:200]


def _tile_roi(tr: int, tc: int) -> np.ndarray:
    m = np.zeros((WIN, WIN), bool)
    m[tr * TILE:(tr + 1) * TILE, tc * TILE:(tc + 1) * TILE] = True
    return m


def run(predict, fit=None, samples: list[Sample] | None = None, predict_cover=None, tile_scan: bool = True,
        progress: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(per-sample predictions, per-tile predictions). A1: leave-one-CAMPAIGN-out when fit is given."""
    samples = samples if samples is not None else load_sets()
    a1 = [s for s in samples if s.set == "A1"]
    rest = [s for s in samples if s.set != "A1"]
    rows = []

    def one(s):
        t0 = time.time()
        n, lo, hi, ab, err = _safe_predict(predict, s.bands, s.meta)
        row = dict(sample_id=s.sample_id, set=s.set, group=s.group, fold=s.fold, kind=s.kind, N_hat=n, lo=lo, hi=hi,
                   abstain=ab, error=err, s=round(time.time() - t0, 3))
        if predict_cover is not None and s.kind == "astar_cover":
            c, clo, chi, cab, cerr = _safe_predict(predict_cover, s.bands, s.meta)
            row.update(cover_hat=None if cab else c, cover_lo=clo, cover_hi=chi, error=err or cerr)
        rows.append(row)

    def tr(ss):
        return [dict(bands=s.bands, meta=dict(s.meta), N=float(s.label.get("N_true", np.nan)),
                     cover_m2=float(s.label.get("cover_m2", np.nan) if s.label.get("cover_m2") is not None else np.nan),
                     campaign=s.label.get("campaign")) for s in ss]

    for f in sorted({s.fold for s in a1}):
        if fit is not None:
            fit(tr([x for x in a1 if x.fold != f]))
        for s in a1:
            if s.fold == f:
                one(s)
    if fit is not None:
        fit(tr(a1))
    for i, s in enumerate(rest):
        one(s)
        if progress and (i + 1) % 100 == 0:
            print(f"  {i + 1}/{len(rest)}", flush=True)
    tiles = []
    if tile_scan:
        for s in [x for x in rest if x.set in ("A2", "A3")]:
            for tr_ in range(WIN // TILE):
                for tc in range(WIN // TILE):
                    m = dict(s.meta, roi=_tile_roi(tr_, tc), roi_area_km2=TILE * TILE * 1e-4)
                    n, _, _, ab, err = _safe_predict(predict, s.bands, m)
                    tiles.append(dict(sample_id=s.sample_id, set=s.set, group=s.group, tile_r=tr_, tile_c=tc,
                                      pos=(not ab) and n >= MIN_ITEMS, error=err))
    return pd.DataFrame(rows), pd.DataFrame(tiles)


# ----------------------------------------------------------------------------------------------------------- score
def per_sample(preds: pd.DataFrame, samples: list[Sample]) -> pd.DataFrame:
    by = {s.sample_id: s for s in samples}
    p = preds.copy()
    for k in ("N_true", "obs_density_km2", "cover_m2", "background", "campaign"):
        p[k] = [by[i].label.get(k) for i in p.sample_id]
    p["roi_area_km2"] = [by[i].meta["roi_area_km2"] for i in p.sample_id]
    p["c_hat_km2"] = p.N_hat / p.roi_area_km2.clip(lower=1e-6)
    gave = (~p.abstain.astype(bool)) & (p.N_hat >= MIN_ITEMS)
    cnt = (p.set == "A1") & (p.kind == "astar")
    N = p.N_true.astype(float)
    lr = np.log10((p.N_hat + 1) / (N + 1))
    p.loc[cnt, "err"] = np.abs(lr[cnt])
    p.loc[cnt, "bias"] = lr[cnt]
    p.loc[cnt, "covered"] = ((~p.abstain[cnt].astype(bool)) & (p.lo[cnt] <= N[cnt]) & (N[cnt] <= p.hi[cnt])).astype(float)
    cov = (p.set == "A1") & (p.kind == "astar_cover")
    if "cover_hat" in p and cov.any():
        ch = pd.to_numeric(p.cover_hat, errors="coerce").fillna(0.0)
        p.loc[cov, "cover_err"] = np.abs(np.log10((ch[cov] + 1) / (p.cover_m2[cov].astype(float) + 1)))
    a2 = p.set == "A2"
    p.loc[a2, "abst"] = (~gave[a2]).astype(float)
    c = p.obs_density_km2[a2].astype(float)
    p.loc[a2, "gross"] = ((p.c_hat_km2[a2] >= G2_HAT) & (c < G2_OBS) & gave[a2]).astype(float)
    a3 = p.set == "A3"
    p.loc[a3, "fp"] = gave[a3].astype(float)
    a4 = p.set == "A4"
    p.loc[a4, "det"] = gave[a4].astype(float)
    return p


def zones_table(tiles: pd.DataFrame, types: pd.DataFrame | None) -> pd.DataFrame:
    """One row per window: area scanned (km², excl. 'debris' tiles), zones total and by type."""
    if tiles is None or tiles.empty:
        return pd.DataFrame()
    from scipy import ndimage
    t = tiles.copy()
    if types is not None:
        t = t.merge(types[["sample_id", "tile_r", "tile_c", "type"]], on=["sample_id", "tile_r", "tile_c"], how="left")
    else:
        t["type"] = "water"
    t["type"] = t["type"].fillna("water")
    out = []
    n_side = WIN // TILE
    for sid, g in t.groupby("sample_id", sort=False):
        pos = np.zeros((n_side, n_side), bool)
        typ = np.full((n_side, n_side), "water", object)
        for r in g.itertuples():
            typ[r.tile_r, r.tile_c] = r.type
            pos[r.tile_r, r.tile_c] = bool(r.pos) and r.type != "debris"
        valid = typ != "debris"
        row = dict(sample_id=sid, set=g.set.iloc[0], group=g.group.iloc[0],
                   area_km2=float(valid.sum()) * TILE * TILE * 1e-4, zones=0)
        for z in ZONE_TYPES:
            row[f"area_{z}"] = float((typ == z).sum()) * TILE * TILE * 1e-4
            row[f"zones_{z}"] = 0
        lab, n = ndimage.label(pos)
        for k in range(1, n + 1):
            ts = sorted(set(typ[lab == k]), key=lambda x: ZONE_PRIORITY.get(x, 99))
            row["zones"] += 1
            row[f"zones_{ts[0]}"] += 1
        out.append(row)
    return pd.DataFrame(out)


def _group_arrays(p: pd.DataFrame, mask, col: str, num=None):
    q = p[mask]
    if num is not None:  # ratio of sums (zones / area)
        g = q.groupby("group").agg(s=(col, "sum"), n=(num, "sum"))
        return g["s"].to_numpy(float), g["n"].to_numpy(float)
    q = q[q[col].notna()]
    g = q.groupby("group")[col].agg(["sum", "count"])
    return g["sum"].to_numpy(float), g["count"].to_numpy(float)


def reference_score(E1, ABST2, FP3, R4):
    z = lambda v: 0.0 if v is None or not np.isfinite(v) else v  # noqa: E731
    return z(E1) + (1 - (ABST2 if np.isfinite(ABST2) else 0.0)) + 10 * max(0.0, z(FP3) - FP_LIMIT) + \
        0.25 * (1 - (R4 if np.isfinite(R4) else 0.0))


def score(p: pd.DataFrame, zt: pd.DataFrame | None = None, ref: pd.DataFrame | None = None, n_boot: int = N_BOOT,
          seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    cnt = (p.set == "A1") & (p.kind == "astar")
    parts = {"E1": (cnt, "err"), "B1": (cnt, "bias"), "C1": (cnt, "covered"),
             "Ec": ((p.set == "A1") & (p.kind == "astar_cover"), "cover_err"),
             "ABST2": (p.set == "A2", "abst"), "G2": (p.set == "A2", "gross"), "FP3": (p.set == "A3", "fp"),
             "R4": (p.set == "A4", "det")}
    point, boot = {}, {}

    def put(k, s, n):
        point[k] = float(s.sum() / n.sum()) if n.sum() else float("nan")
        if len(s):
            w = rng.multinomial(len(s), np.full(len(s), 1 / len(s)), size=n_boot)
            boot[k] = (w @ s) / np.maximum(w @ n, 1e-12)
        else:
            boot[k] = np.full(n_boot, np.nan)

    for k, (mask, col) in parts.items():
        if col not in p:
            point[k], boot[k] = float("nan"), np.full(n_boot, np.nan)
            continue
        put(k, *_group_arrays(p, mask, col))
    if zt is not None and not zt.empty:
        put("Z3", *_group_arrays(zt, zt.set.isin(["A2", "A3"]), "zones", "area_km2"))
        for z in ZONE_TYPES:
            s = zt[f"zones_{z}"].sum()
            a = zt[f"area_{z}"].sum()
            point[f"Z3_{z}"] = float(s / a) if a else float("nan")
    else:
        point["Z3"] = float("nan")
        boot["Z3"] = np.full(n_boot, np.nan)
    ci = {k: (float(np.nanpercentile(v, 2.5)), float(np.nanpercentile(v, 97.5))) if np.isfinite(v).any()
          else (float("nan"), float("nan")) for k, v in boot.items()}
    res = dict(point=point, ci=ci,
               n={"A1": int(cnt.sum()), "A1_cover": int(((p.set == "A1") & (p.kind == "astar_cover")).sum()),
                  **{s: int((p.set == s).sum()) for s in ("A2", "A3", "A4", "A5")}},
               n_errors=int((p.error.fillna("") != "").sum()), n_abstain=int(p.abstain.astype(bool).sum()),
               n_tiles=int(0 if zt is None or zt.empty else zt.area_km2.sum() / (TILE * TILE * 1e-4)))
    point["ref_score"] = reference_score(point["E1"], point["ABST2"], point["FP3"], point["R4"])
    if ref is not None:
        a = p[cnt].set_index("sample_id")["err"]
        b = ref[(ref.set == "A1") & (ref.kind == "astar")].set_index("sample_id")["err"]
        j = a.index.intersection(b.index)
        d = (a[j] - b[j]).to_numpy(float)
        if len(d):
            w = rng.multinomial(len(d), np.full(len(d), 1 / len(d)), size=n_boot) / len(d)
            db = w @ d
            res["dE1"] = (float(d.mean()), float(np.percentile(db, 2.5)), float(np.percentile(db, 97.5)))
    return res


def natural_counter_verdict(n_a5: int) -> str:
    return (f"нет: A5 = {n_a5} природных пар < {A5_MIN} — ни один метод не является «природным счётчиком»; "
            "вывод по A1 ограничен искусственными мишенями") if n_a5 < A5_MIN else \
        f"A5 = {n_a5} пар — проверка «природного счётчика» возможна отдельно по A5"


def a1_by_campaign(p: pd.DataFrame) -> pd.DataFrame:
    q = p[(p.set == "A1") & (p.kind == "astar")]
    return q.groupby("fold").agg(dates=("err", "size"), E1=("err", "mean"), B1=("bias", "mean"),
                                 C1=("covered", "mean"), N_sum=("N_true", "sum"), N_hat_sum=("N_hat", "sum")).reset_index()


def a3_by_background(p: pd.DataFrame) -> pd.DataFrame:
    q = p[p.set == "A3"]
    return q.groupby("background").agg(n=("fp", "size"), fp_rate=("fp", "mean"), N_hat_max=("N_hat", "max")).reset_index()


def zones_by_type(zt: pd.DataFrame) -> pd.DataFrame:
    if zt is None or zt.empty:
        return pd.DataFrame()
    rows = [dict(type=z, area_km2=round(zt[f"area_{z}"].sum(), 2), zones=int(zt[f"zones_{z}"].sum()),
                 zones_per_km2=round(zt[f"zones_{z}"].sum() / zt[f"area_{z}"].sum(), 4) if zt[f"area_{z}"].sum() else None)
            for z in ZONE_TYPES]
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------------ evaluation
def runs_dir(cfg: dict) -> Path:
    return set_dir(cfg) / "runs"


def b1_reference(cfg: dict) -> pd.DataFrame | None:
    fs = sorted((f for f in runs_dir(cfg).glob("b1_flat_plp__*.csv") if not f.stem.endswith("__zones")),
                key=lambda f: f.stat().st_mtime)
    return pd.read_csv(fs[-1]) if fs else None


def evaluate(predict, fit=None, name: str = "method", code_sha: str = "inline", note: str = "",
             record_to: Path | None = LEADERBOARD, cfg: dict | None = None,
             sets=("A1", "A2", "A3", "A4", "A5"), predict_cover=None, progress: bool = True) -> dict:
    cfg = cfg or load_cfg()
    sets_sha = verify_sets(cfg)
    t0 = time.time()
    samples = load_sets(cfg, sets)
    preds, tiles = run(predict, fit, samples, predict_cover, tile_scan=True, progress=progress)
    p = per_sample(preds, samples)
    zt = zones_table(tiles, load_tile_types(cfg))
    ref = b1_reference(cfg) if name != "b1_flat_plp" else None
    res = score(p, zt, ref)
    if name == "b1_flat_plp":
        res["dE1"] = (0.0, 0.0, 0.0)
    rd = runs_dir(cfg)
    rd.mkdir(parents=True, exist_ok=True)
    p.to_csv(rd / f"{name}__{code_sha}.csv", index=False)
    if not zt.empty:
        zt.to_csv(rd / f"{name}__{code_sha}__zones.csv", index=False)
    n_a5 = int((cfg.get("sets", {}).get("A5") or {}).get("n", 0))
    res.update(name=name, code_sha256=code_sha, runtime_s=round(time.time() - t0, 1), sets_sha256=sets_sha,
               set_version=cfg["version"], a1=a1_by_campaign(p), a3=a3_by_background(p), zones=zones_by_type(zt),
               per_sample=p, note=note, n_a5=n_a5, natural_counter=natural_counter_verdict(n_a5))
    res["gate_product"] = gate(res, cfg)
    if record_to is not None:
        record(res, record_to)
    return res


def gate(res: dict, cfg: dict) -> str:
    """Replaces b1 in the zone card only if: ΔE1 (LOCO) CI upper < 0; ABST2 ≥ b1; FP3 ≤ max(b1, 1 %); Z3 ≤ b1."""
    if res.get("name") == "b1_flat_plp":
        return "reference"
    lb = read_leaderboard(version=cfg["version"])
    b1 = lb[lb.method == "b1_flat_plp"]
    if "dE1" not in res or b1.empty:
        return "no b1 reference"
    b1 = b1.iloc[-1]
    pt = res["point"]
    ok = (res["dE1"][2] < 0) and (pt["ABST2"] >= float(b1.ABST2) - 1e-12) and \
         (pt["FP3"] <= max(float(b1.FP3), FP_LIMIT) + 1e-12) and (pt["Z3"] <= float(b1.Z3_zones_km2) + 1e-12)
    return "PASS (только мишени; не природный счётчик)" if ok else "fail"


def record(res: dict, path: Path = LEADERBOARD) -> dict:
    """Append one row to the leaderboard (the only way methods get onto it)."""
    pt, ci = res["point"], res["ci"]
    f = lambda v: "" if v is None or not np.isfinite(v) else f"{v:.4f}"  # noqa: E731
    d = res.get("dE1", (np.nan, np.nan, np.nan))
    row = dict(date=time.strftime("%Y-%m-%d %H:%M"), set_version=res["set_version"], method=res["name"],
               code_sha256=res["code_sha256"], natural_counter=f"нет (A5 = {res['n_a5']} < {A5_MIN})"
               if res["n_a5"] < A5_MIN else f"A5 = {res['n_a5']}",
               E1_err=f(pt["E1"]), E1_ci_lo=f(ci["E1"][0]), E1_ci_hi=f(ci["E1"][1]), B1_bias=f(pt["B1"]),
               C1_cover=f(pt["C1"]), dE1_vs_b1=f(d[0]), dE1_ci_lo=f(d[1]), dE1_ci_hi=f(d[2]),
               Ec_plp2021_cover=f(pt["Ec"]) or "n/a", ABST2=f(pt["ABST2"]), ABST2_ci_lo=f(ci["ABST2"][0]),
               ABST2_ci_hi=f(ci["ABST2"][1]), G2_thousands=f(pt["G2"]), FP3=f(pt["FP3"]), FP3_ci_lo=f(ci["FP3"][0]),
               FP3_ci_hi=f(ci["FP3"][1]), Z3_zones_km2=f(pt["Z3"]), Z3_ci_lo=f(ci["Z3"][0]), Z3_ci_hi=f(ci["Z3"][1]),
               **{f"Z3_{z}": f(pt.get(f"Z3_{z}", float("nan"))) for z in ZONE_TYPES},
               R4_detect=f(pt["R4"]), R4_ci_lo=f(ci["R4"][0]), R4_ci_hi=f(ci["R4"][1]), n_A1=res["n"]["A1"],
               n_A1_cover=res["n"]["A1_cover"], n_A2=res["n"]["A2"], n_A3=res["n"]["A3"], n_A4=res["n"]["A4"],
               n_A5=res["n_a5"], n_tiles=res["n_tiles"], n_abstain=res["n_abstain"], n_errors=res["n_errors"],
               ref_score=f(pt["ref_score"]), gate_product=res["gate_product"], runtime_s=res["runtime_s"],
               sets_sha256=res["sets_sha256"], note=res.get("note", ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LB_COLUMNS)
        if new:
            w.writeheader()
        w.writerow(row)
    return row


def read_leaderboard(path: Path = LEADERBOARD, version: str | None = None) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=LB_COLUMNS)
    lb = pd.read_csv(path, dtype={"code_sha256": str})
    return lb[lb.set_version == version] if version else lb


def evaluate_file(path: str, name: str | None = None, note: str = "", record_to: Path | None = LEADERBOARD,
                  sets=("A1", "A2", "A3", "A4", "A5")) -> dict:
    mod = load_method(path)
    name = name or getattr(mod, "NAME", Path(path).stem)
    return evaluate(mod.predict, getattr(mod, "fit", None), name=name, code_sha=code_hash(path), note=note,
                    record_to=record_to, sets=sets, predict_cover=getattr(mod, "predict_cover", None))


def print_result(res: dict) -> None:
    pt, ci = res["point"], res["ci"]
    g = lambda k: f"{pt[k]:.3f} [{ci[k][0]:.3f}; {ci[k][1]:.3f}]" if np.isfinite(pt.get(k, np.nan)) else "n/a"  # noqa
    print(f"\n== {res['name']} ({res['code_sha256']}), наборы {res['set_version']} [{res['sets_sha256']}], "
          f"{res['runtime_s']} s, воздержаний {res['n_abstain']}, ошибок {res['n_errors']}")
    print(f"ПРИРОДНЫЙ СЧЁТЧИК: {res['natural_counter']}")
    print(f"A1 мишени (LOCO по кампаниям, {res['n']['A1']} дат): ошибка E1 {g('E1')}; смещение B1 {pt['B1']:+.3f}; "
          f"покрытие интервала C1 {pt['C1']:.2f}; PLP2021 площадь Ec {g('Ec')}")
    if "dE1" in res:
        print(f"   ΔE1 vs б1 {res['dE1'][0]:+.3f} [{res['dE1'][1]:+.3f}; {res['dE1'][2]:+.3f}]   в продукт: {res['gate_product']}")
    print(f"A2 без сигнала ({res['n']['A2']}): воздержание ABST2 {g('ABST2')}; «тысячи при единицах» G2 {pt['G2']:.3f}")
    print(f"A3 фон ({res['n']['A3']}): FP3 {g('FP3')}; ложные зоны Z3 {g('Z3')} зон/км² ({res['n_tiles']} плиток)")
    print(f"A4 Cózar ({res['n']['A4']}): обнаружение нитей R4 {g('R4')}")
    print(f"A5 природные пары: {res['n_a5']}")
    print(f"справочно (не вывод): ref_score {pt['ref_score']:.3f}")
    print(res["a1"].to_string(index=False))
    print(res["zones"].to_string(index=False))
    print(res["a3"].to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("method")
    r.add_argument("--name")
    r.add_argument("--note", default="")
    r.add_argument("--no-record", action="store_true")
    r.add_argument("--sets", nargs="*", default=["A1", "A2", "A3", "A4", "A5"],
                   help="отладка на части наборов: такой прогон НЕ пишется в лидерборд")
    sub.add_parser("board")
    c = sub.add_parser("check")
    c.add_argument("--deep", action="store_true")
    a = ap.parse_args()
    if a.cmd == "run":
        partial = sorted(a.sets) != ["A1", "A2", "A3", "A4", "A5"]
        res = evaluate_file(a.method, a.name, a.note, None if (a.no_record or partial) else LEADERBOARD, tuple(a.sets))
        print_result(res)
    elif a.cmd == "board":
        cfg = load_cfg()
        lb = read_leaderboard(version=cfg["version"])
        cols = ["date", "method", "code_sha256", "E1_err", "B1_bias", "C1_cover", "dE1_ci_hi", "ABST2", "FP3",
                "Z3_zones_km2", "R4_detect", "n_A5", "natural_counter", "gate_product"]
        print(lb.sort_values("E1_err")[cols].to_string(index=False))
    else:
        cfg = load_cfg()
        print("ok", cfg["version"], verify_sets(cfg, deep=a.deep))


if __name__ == "__main__":
    main()
