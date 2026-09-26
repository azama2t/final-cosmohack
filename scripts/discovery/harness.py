"""L133 (INBOX §35 А) — «ОЦЕНЩИК» двигателя открытий «снимок S2 → число штук».

Метод = python-файл с функцией
    predict(window_bands: dict[str, np.ndarray], meta: dict) -> (N_items, lo, hi)
  window_bands: 'B1'..'B12' (11 полос без B9, отражение, 10 м, 128×128, NaN = нет данных) + 'SCL' (uint8, 0 = нет);
  meta: roi (bool H×W — зона, где считать предметы), roi_area_km2, pixel_m, epsg, bounds, lon, lat (центр окна),
        date (YYYYMMDD), datetime (или None), has_scl. НИКАКИХ id, набора, класса фона, ответов.
и, по желанию, fit(train: list[dict(bands=..., meta=..., N=...)]) — обучение ТОЛЬКО на датах A* (leave-one-date-out:
для каждой даты A1 fit получает остальные даты, затем один fit на всех датах A1 перед A2–A4). NAME / DESCRIPTION —
необязательные строки модуля.

Балл — на ЗАФИКСИРОВАННЫХ наборах configs/discovery_sets.yaml (версия, sha256 манифеста и меток проверяются перед
каждым прогоном). Формула — там же (score.formula). Запись в docs/research/discovery/LEADERBOARD.csv — функцией record().

  .venv/Scripts/python.exe scripts/discovery/harness.py run scripts/discovery/baselines/b1_flat_plp.py
  .venv/Scripts/python.exe scripts/discovery/harness.py run my_method.py --name my_v3 --note "окно 5×5"
  .venv/Scripts/python.exe scripts/discovery/harness.py board            # лидерборд текущей версии наборов
  .venv/Scripts/python.exe scripts/discovery/harness.py check            # проверка целостности наборов
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
CFG_PATH = ROOT / "configs" / "discovery_sets.yaml"
LEADERBOARD = ROOT / "docs" / "research" / "discovery" / "LEADERBOARD.csv"
BANDS11 = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
WIN = 128
FP_MIN_ITEMS = 1.0           # A3: N̂ ≥ 1 предмет в ROI = ложное «N > 0»
FP_LIMIT = 0.01              # A3: порог доли ложных
A4_BAND = (1.0e4, 1.0e8)     # A4: полоса правдоподобия внутри нити (Cózar 2021; см. yaml)
G2_HAT, G2_OBS = 1000.0, 100.0
N_BOOT = 2000
LB_COLUMNS = ["date", "set_version", "method", "code_sha256", "score", "score_ci_lo", "score_ci_hi", "E1_astar_lodo",
              "E1_ci_lo", "E1_ci_hi", "cov1", "dE1_vs_b1", "dE1_ci_lo", "dE1_ci_hi", "E2_field", "E2_ci_lo", "E2_ci_hi",
              "G2_thousands", "FP3", "FP3_ci_lo", "FP3_ci_hi", "P4_cozar", "A4_pos_frac", "n_A1", "n_A2", "n_A3", "n_A4",
              "n_errors", "gate_product", "runtime_s", "sets_sha256", "note"]


# ------------------------------------------------------------------------------------------------------------ sets
def load_cfg(path: Path = CFG_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def set_dir(cfg: dict) -> Path:
    return ROOT / "data" / "discovery" / cfg["version"]


def verify_sets(cfg: dict, deep: bool = False) -> str:
    """Frozen composition check: manifest + labels sha256 must equal configs/discovery_sets.yaml -> frozen.
    deep=True also re-hashes every window file. Returns the combined sets sha256 (short)."""
    fr = cfg.get("frozen") or {}
    d = set_dir(cfg)
    got = {"manifest_sha256": sha256_file(d / "manifest.csv"), "labels_sha256": sha256_file(d / "labels.csv")}
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
    return hashlib.sha256((got["manifest_sha256"] + got["labels_sha256"]).encode()).hexdigest()[:16]


@dataclass
class Sample:
    sample_id: str
    set: str
    group: str
    kind: str
    bands: dict
    meta: dict
    label: dict = field(default_factory=dict)


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


def load_sets(cfg: dict | None = None, sets=("A1", "A2", "A3", "A4"), verify: bool = True) -> list[Sample]:
    cfg = cfg or load_cfg()
    key = (cfg["version"], tuple(sets))
    if key in _CACHE:
        return _CACHE[key]
    if verify:
        verify_sets(cfg)
    sys.path.insert(0, str(ROOT / "scripts" / "discovery"))
    from build_sets import marida_window
    d = set_dir(cfg)
    m = pd.read_csv(d / "manifest.csv", low_memory=False)
    m = m[(m.status == "ok") & m.set.isin(sets)]
    lab = pd.read_csv(d / "labels.csv").set_index("sample_id")
    # background class of A3 windows — for the per-class table only (never passed to a method)
    cand = pd.read_csv(d / "candidates.csv", low_memory=False)
    bg = dict(zip(cand.sample_id, cand.background)) if "background" in cand else {}
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
        out.append(Sample(r["sample_id"], r["set"], str(r["group"]), r["kind"], bands,
                          _public_meta(raw, roi, bool(np.asarray(scl).any()), r["kind"]), L))
    _CACHE[key] = out
    return out


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


def _safe_predict(predict, s: Sample):
    try:
        r = predict(s.bands, dict(s.meta))
        n, lo, hi = (float(v) for v in r)
        if not all(math.isfinite(v) for v in (n, lo, hi)):
            raise ValueError(f"non-finite {r}")
        return max(n, 0.0), max(lo, 0.0), max(hi, 0.0), ""
    except Exception as e:  # noqa: BLE001
        return 0.0, 0.0, 0.0, f"{type(e).__name__}: {e}"[:200]


def run(predict, fit=None, samples: list[Sample] | None = None, progress: bool = False) -> pd.DataFrame:
    """Predictions for every sample; A1 leave-one-date-out when fit is given."""
    samples = samples if samples is not None else load_sets()
    a1 = [s for s in samples if s.set == "A1"]
    rest = [s for s in samples if s.set != "A1"]
    rows = []

    def one(s, t0):
        n, lo, hi, err = _safe_predict(predict, s)
        rows.append(dict(sample_id=s.sample_id, set=s.set, group=s.group, kind=s.kind, N_hat=n, lo=lo, hi=hi,
                         error=err, s=round(time.time() - t0, 3)))

    def tr(ss):
        return [dict(bands=s.bands, meta=dict(s.meta), N=float(s.label["N_true"])) for s in ss]

    for s in a1:
        t0 = time.time()
        if fit is not None:
            fit(tr([x for x in a1 if x.group != s.group]))
        one(s, t0)
    if fit is not None:
        fit(tr(a1))
    for i, s in enumerate(rest):
        one(s, time.time())
        if progress and (i + 1) % 100 == 0:
            print(f"  {i + 1}/{len(rest)}", flush=True)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------------------------------------- score
def per_sample(preds: pd.DataFrame, samples: list[Sample]) -> pd.DataFrame:
    by = {s.sample_id: s for s in samples}
    p = preds.copy()
    p["N_true"] = [by[i].label.get("N_true") for i in p.sample_id]
    p["obs_density_km2"] = [by[i].label.get("obs_density_km2") for i in p.sample_id]
    p["roi_area_km2"] = [by[i].meta["roi_area_km2"] for i in p.sample_id]
    p["background"] = [by[i].label.get("background") for i in p.sample_id]
    p["c_hat_km2"] = p.N_hat / p.roi_area_km2.clip(lower=1e-6)
    a1 = p.set == "A1"
    p.loc[a1, "err"] = np.abs(np.log10((p.N_hat[a1] + 1) / (p.N_true[a1].astype(float) + 1)))
    p.loc[a1, "covered"] = ((p.lo[a1] <= p.N_true[a1]) & (p.N_true[a1] <= p.hi[a1])).astype(float)
    a2 = p.set == "A2"
    c = p.obs_density_km2[a2].astype(float)
    p.loc[a2, "err"] = np.abs(np.log10((p.c_hat_km2[a2] + 1) / (c + 1)))
    p.loc[a2, "gross"] = ((p.c_hat_km2[a2] >= G2_HAT) & (c < G2_OBS)).astype(float)
    a3 = p.set == "A3"
    p.loc[a3, "fp"] = (p.N_hat[a3] >= FP_MIN_ITEMS).astype(float)
    a4 = p.set == "A4"
    ok4 = (p.N_hat[a4] > 0) & (p.c_hat_km2[a4] >= A4_BAND[0]) & (p.c_hat_km2[a4] <= A4_BAND[1])
    p.loc[a4, "implausible"] = (~ok4).astype(float)
    p.loc[a4, "pos"] = (p.N_hat[a4] > 0).astype(float)
    return p


def _group_arrays(p: pd.DataFrame, set_: str, col: str):
    q = p[p.set == set_]
    g = q.groupby("group")[col].agg(["sum", "count"])
    return g["sum"].to_numpy(float), g["count"].to_numpy(float)


def combine(E1, E2, G2, FP3, P4):
    return E1 + 0.5 * E2 + 1.0 * G2 + 10.0 * np.maximum(0.0, FP3 - FP_LIMIT) + 0.25 * P4


def score(p: pd.DataFrame, ref: pd.DataFrame | None = None, n_boot: int = N_BOOT, seed: int = 0) -> dict:
    """Metrics + cluster bootstrap (by group within each set). ref = per-sample table of b1 for paired ΔE1."""
    rng = np.random.default_rng(seed)
    parts = {"E1": ("A1", "err"), "cov1": ("A1", "covered"), "E2": ("A2", "err"), "G2": ("A2", "gross"),
             "FP3": ("A3", "fp"), "P4": ("A4", "implausible"), "pos4": ("A4", "pos")}
    point, boot = {}, {}
    for k, (st, col) in parts.items():
        s, n = _group_arrays(p, st, col)
        point[k] = float(s.sum() / n.sum()) if n.sum() else float("nan")
        if len(s):
            w = rng.multinomial(len(s), np.full(len(s), 1 / len(s)), size=n_boot)
            boot[k] = (w @ s) / np.maximum(w @ n, 1)
        else:
            boot[k] = np.full(n_boot, np.nan)
    z = lambda v: 0.0 if not np.isfinite(v) else v  # noqa: E731 - missing set contributes 0
    point["SCORE"] = float(combine(z(point["E1"]), z(point["E2"]), z(point["G2"]), z(point["FP3"]), z(point["P4"])))
    nz = {k: np.nan_to_num(v) for k, v in boot.items()}
    boot["SCORE"] = combine(nz["E1"], nz["E2"], nz["G2"], nz["FP3"], nz["P4"])
    ci = {k: (float(np.nanpercentile(v, 2.5)), float(np.nanpercentile(v, 97.5))) for k, v in boot.items()}
    res = dict(point=point, ci=ci, n={s: int((p.set == s).sum()) for s in ("A1", "A2", "A3", "A4")},
               n_errors=int((p.error.fillna("") != "").sum()))
    if ref is not None:
        a = p[p.set == "A1"].set_index("sample_id")["err"]
        b = ref[ref.set == "A1"].set_index("sample_id")["err"]
        j = a.index.intersection(b.index)
        d = (a[j] - b[j]).to_numpy(float)
        if len(d):
            w = rng.multinomial(len(d), np.full(len(d), 1 / len(d)), size=n_boot) / len(d)
            db = w @ d
            res["dE1"] = (float(d.mean()), float(np.percentile(db, 2.5)), float(np.percentile(db, 97.5)))
    return res


def by_set_table(p: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for st, g in p.groupby("set"):
        r = dict(set=st, n=len(g), groups=g.group.nunique())
        if st == "A1":
            r.update(E1=g.err.mean(), cov1=g.covered.mean())
        if st == "A2":
            r.update(E2=g.err.mean(), G2=g.gross.mean())
        if st == "A3":
            r.update(FP3=g.fp.mean())
        if st == "A4":
            r.update(P4=g.implausible.mean(), pos=g.pos.mean())
        rows.append(r)
    return pd.DataFrame(rows)


def a3_by_background(p: pd.DataFrame) -> pd.DataFrame:
    q = p[p.set == "A3"]
    return q.groupby("background").agg(n=("fp", "size"), fp_rate=("fp", "mean"), N_hat_max=("N_hat", "max")).reset_index()


# ------------------------------------------------------------------------------------------------------ evaluation
def runs_dir(cfg: dict) -> Path:
    return set_dir(cfg) / "runs"


def b1_reference(cfg: dict) -> pd.DataFrame | None:
    """Per-sample table of the latest b1_flat_plp run of this set version (for paired ΔE1)."""
    fs = sorted(runs_dir(cfg).glob("b1_flat_plp__*.csv"), key=lambda f: f.stat().st_mtime)
    return pd.read_csv(fs[-1]) if fs else None


def evaluate(predict, fit=None, name: str = "method", code_sha: str = "inline", note: str = "",
             record_to: Path | None = LEADERBOARD, cfg: dict | None = None, sets=("A1", "A2", "A3", "A4"),
             progress: bool = True) -> dict:
    cfg = cfg or load_cfg()
    sets_sha = verify_sets(cfg)
    t0 = time.time()
    samples = load_sets(cfg, sets)
    preds = run(predict, fit, samples, progress)
    p = per_sample(preds, samples)
    ref = b1_reference(cfg) if name != "b1_flat_plp" else None
    res = score(p, ref)
    if name == "b1_flat_plp":
        res["dE1"] = (0.0, 0.0, 0.0)
    rd = runs_dir(cfg)
    rd.mkdir(parents=True, exist_ok=True)
    p.to_csv(rd / f"{name}__{code_sha}.csv", index=False)
    res.update(name=name, code_sha256=code_sha, runtime_s=round(time.time() - t0, 1), sets_sha256=sets_sha,
               set_version=cfg["version"], table=by_set_table(p), a3=a3_by_background(p), per_sample=p, note=note)
    res["gate_product"] = gate(res, cfg)
    if record_to is not None:
        record(res, record_to)
    return res


def gate(res: dict, cfg: dict) -> str:
    """§35 Б / §24: replaces b1 in the product only if ΔE1 CI upper < 0, E2 ≤ E2(b1), FP3 ≤ max(FP3(b1), 1 %)."""
    if res.get("name") == "b1_flat_plp":
        return "reference"
    lb = read_leaderboard(version=cfg["version"])
    b1 = lb[lb.method == "b1_flat_plp"]
    if "dE1" not in res or b1.empty:
        return "no b1 reference"
    b1 = b1.iloc[-1]
    pt = res["point"]
    ok = (res["dE1"][2] < 0) and (pt["E2"] <= float(b1.E2_field) + 1e-12) and \
         (pt["FP3"] <= max(float(b1.FP3), FP_LIMIT) + 1e-12)
    return "PASS" if ok else "fail"


def record(res: dict, path: Path = LEADERBOARD) -> dict:
    """Append one row to the leaderboard (the only way methods get onto it)."""
    pt, ci = res["point"], res["ci"]
    f3 = lambda v: "" if v is None or not np.isfinite(v) else f"{v:.4f}"  # noqa: E731
    d = res.get("dE1", (np.nan, np.nan, np.nan))
    row = dict(date=time.strftime("%Y-%m-%d %H:%M"), set_version=res["set_version"], method=res["name"],
               code_sha256=res["code_sha256"], score=f3(pt["SCORE"]), score_ci_lo=f3(ci["SCORE"][0]),
               score_ci_hi=f3(ci["SCORE"][1]), E1_astar_lodo=f3(pt["E1"]), E1_ci_lo=f3(ci["E1"][0]),
               E1_ci_hi=f3(ci["E1"][1]), cov1=f3(pt["cov1"]), dE1_vs_b1=f3(d[0]), dE1_ci_lo=f3(d[1]),
               dE1_ci_hi=f3(d[2]), E2_field=f3(pt["E2"]), E2_ci_lo=f3(ci["E2"][0]), E2_ci_hi=f3(ci["E2"][1]),
               G2_thousands=f3(pt["G2"]), FP3=f3(pt["FP3"]), FP3_ci_lo=f3(ci["FP3"][0]), FP3_ci_hi=f3(ci["FP3"][1]),
               P4_cozar=f3(pt["P4"]), A4_pos_frac=f3(pt["pos4"]), n_A1=res["n"]["A1"], n_A2=res["n"]["A2"],
               n_A3=res["n"]["A3"], n_A4=res["n"]["A4"], n_errors=res["n_errors"], gate_product=res["gate_product"],
               runtime_s=res["runtime_s"], sets_sha256=res["sets_sha256"], note=res.get("note", ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LB_COLUMNS)
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
                  sets=("A1", "A2", "A3", "A4")) -> dict:
    mod = load_method(path)
    name = name or getattr(mod, "NAME", Path(path).stem)
    return evaluate(mod.predict, getattr(mod, "fit", None), name=name, code_sha=code_hash(path), note=note,
                    record_to=record_to, sets=sets)


def print_result(res: dict) -> None:
    pt, ci = res["point"], res["ci"]
    print(f"\n== {res['name']} ({res['code_sha256']}), наборы {res['set_version']} [{res['sets_sha256']}], "
          f"{res['runtime_s']} s, ошибок {res['n_errors']}")
    print(f"SCORE {pt['SCORE']:.3f} [{ci['SCORE'][0]:.3f}; {ci['SCORE'][1]:.3f}]   E1 (A*, LODO) {pt['E1']:.3f} "
          f"[{ci['E1'][0]:.3f}; {ci['E1'][1]:.3f}]   cov1 {pt['cov1']:.2f}")
    if "dE1" in res:
        print(f"ΔE1 vs b1 {res['dE1'][0]:+.3f} [{res['dE1'][1]:+.3f}; {res['dE1'][2]:+.3f}]   в продукт: {res['gate_product']}")
    print(f"E2 {pt['E2']:.3f} [{ci['E2'][0]:.3f}; {ci['E2'][1]:.3f}]  G2 {pt['G2']:.3f}  FP3 {pt['FP3']:.3f} "
          f"[{ci['FP3'][0]:.3f}; {ci['FP3'][1]:.3f}]  P4 {pt['P4']:.3f} (N̂>0: {pt['pos4']:.2f})")
    print(res["table"].to_string(index=False))
    print(res["a3"].to_string(index=False))
    a1 = res["per_sample"][res["per_sample"].set == "A1"][["sample_id", "N_true", "N_hat", "lo", "hi", "err"]]
    print(a1.to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("method")
    r.add_argument("--name")
    r.add_argument("--note", default="")
    r.add_argument("--no-record", action="store_true")
    r.add_argument("--sets", nargs="*", default=["A1", "A2", "A3", "A4"],
                   help="отладка на части наборов: такой прогон НЕ пишется в лидерборд")
    sub.add_parser("board")
    c = sub.add_parser("check")
    c.add_argument("--deep", action="store_true")
    a = ap.parse_args()
    if a.cmd == "run":
        partial = sorted(a.sets) != ["A1", "A2", "A3", "A4"]
        res = evaluate_file(a.method, a.name, a.note, None if (a.no_record or partial) else LEADERBOARD, tuple(a.sets))
        print_result(res)
    elif a.cmd == "board":
        cfg = load_cfg()
        lb = read_leaderboard(version=cfg["version"])
        cols = ["date", "method", "code_sha256", "score", "score_ci_lo", "score_ci_hi", "E1_astar_lodo", "dE1_vs_b1",
                "dE1_ci_hi", "E2_field", "G2_thousands", "FP3", "P4_cozar", "gate_product"]
        print(lb.sort_values("score")[cols].to_string(index=False))
    else:
        cfg = load_cfg()
        print("ok", cfg["version"], verify_sets(cfg, deep=a.deep))


if __name__ == "__main__":
    main()
