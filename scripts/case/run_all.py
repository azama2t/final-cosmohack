r"""Сквозной маршрут кейса одной командой: CSV → отбор → пары → качество + детектор → концентрация →
метрики детектора → экспорт через API v3.

  set CUDA_VISIBLE_DEVICES= & .venv\Scripts\python.exe scripts\case\run_all.py all --offline
  .venv\Scripts\python.exe scripts\case\run_all.py prepare [--offline] [--force]
  .venv\Scripts\python.exe scripts\case\run_all.py eval
  .venv\Scripts\python.exe scripts\case\run_all.py export
  powershell -File run.ps1 -Case all            (то же через run.ps1)

Подкоманды:
  prepare  1) отбор записей по configs/case_selection.yaml (src/macroplastic/case/selection.py) + реестр образцов;
           2) пары «событие ↔ сцена» (scripts/case/find_pairs.py, кэш STAC data/pairs/cache/);
           3) маски качества + детектор на парах (scripts/case/pair_quality.py, продолжает с места);
           → реестр пар по событиям data/case/run/registry_pairs.csv (каждое event_id CSV: accept/reject + причина).
  eval     4) концентрация по полевым данным (scripts/case/baseline_concentration.py);
           5) метрики детектора на MARIDA test/val пересчитываются из сохранённых предсказаний
              data/case/detector_preds/*.npz и меток MARIDA *_cl.tif (снимки test не читаются, модель не запускается);
              сверка с reports/case_detector/metrics.json. Нет меток → только sha256 metrics.json.
  export   6) GeoJSON + CSV слоёв observations / pairs / zones через API v3 (FastAPI TestClient) в out/case_export/.
  all      prepare + eval + export.
Флаги: --offline — сеть не используется (пары только из кэша, качество — только из готовых meta.json);
       --force   — пересчитать качество пар заново (pair_quality.py --force, нужна сеть, ~минуты на пару).
Отладка/тест: --events ID1,ID2 или --limit-events N и --workdir DIR — подмножество событий, все выходы в DIR
       (реальные data/ и reports/ не трогаются; качество — из готовых meta.json; концентрация пропускается).

Итог: reports/case_run/run_summary.json (версии, конфиги с sha256, числа, метрики, sha256 выходных таблиц,
время шагов). Повторный запуск с теми же входами даёт те же sha256 таблиц (проверено: docs/CASE_RUN.md, раздел «Повторяемость»).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import importlib.util
import json
import os
import platform
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from macroplastic.case import selection as S  # noqa: E402

SAMPLES = ROOT / "task" / "macroplastic_marine_samples.csv"
CONFIGS = [ROOT / "configs" / "case_selection.yaml", ROOT / "configs" / "case_pairs.yaml"]
DET_DIR = ROOT / "data" / "case" / "detector_preds"
DET_METRICS = ROOT / "reports" / "case_detector" / "metrics.json"
MARIDA = ROOT / "data" / "MARIDA"
DET_MODELS = ["lgbm", "rf_argmax", "rf_prob", "fdi_ndvi_box", "fdi_interval", "fdi_threshold", "ndvi_threshold"]

EXPORTS = [  # (имя файла, query-string /api/v3/export)
    ("observations.geojson", "layer=observations&format=geojson"),
    ("observations.csv", "layer=observations&format=csv"),
    ("pairs.geojson", "layer=pairs&format=geojson"),
    ("pairs.csv", "layer=pairs&format=csv"),
    ("zones.geojson", "layer=zones&format=geojson"),
    ("zones.csv", "layer=zones&format=csv"),
    # сохранённые примеры запросов (сценарий Т5): пары Чёрного моря |dt| ≤ 24 ч, принятые пары, зоны с детекцией
    ("example_pairs_black_sea_24h.csv", "layer=pairs&format=csv&source=S4_BLACK_SEA_DOORS3&max_dt_hours=24"),
    ("example_pairs_accepted.csv", "layer=pairs&format=csv&status=accepted"),
    ("example_zones_detected.geojson", "layer=zones&format=geojson&detection_status=detected"),
]


# ------------------------------------------------------------------------------------------ utils
def sha256(p: Path) -> str | None:
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"case_{name}", ROOT / "scripts" / "case" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@contextmanager
def argv(args: list[str]):
    old = sys.argv
    sys.argv = [old[0]] + args
    try:
        yield
    finally:
        sys.argv = old


@contextmanager
def patched(obj, **kw):
    old = {k: getattr(obj, k) for k in kw}
    for k, v in kw.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(obj, k, v)


class Ctx:
    """Пути выходов: реальные (по умолчанию) или подкаталог --workdir (подмножество событий)."""

    def __init__(self, a):
        self.a = a
        self.offline, self.force = a.offline, a.force
        self.sub = bool(a.workdir)
        if self.sub:
            w = Path(a.workdir).resolve()
            self.samples = w / "samples.csv"
            self.pairs = w / "pairs"
            self.case = w / "case"
            self.reports = w / "reports"
            self.export = w / "export"
            self.summary = w / "run_summary.json"
        else:
            self.samples = SAMPLES
            self.pairs = ROOT / "data" / "pairs"
            self.case = ROOT / "data" / "case"
            self.reports = ROOT / "reports"
            self.export = ROOT / "out" / "case_export"
            self.summary = ROOT / "reports" / "case_run" / "run_summary.json"
        self.run = self.case / "run"
        self.steps: list[dict] = []
        self.summary.parent.mkdir(parents=True, exist_ok=True)
        self.data = json.loads(self.summary.read_text(encoding="utf-8")) if self.summary.is_file() else {}

    @contextmanager
    def step(self, name: str):
        t0 = time.time()
        rec = {"step": name, "ok": False}
        print(f"[run_all] >>> {name}", flush=True)
        try:
            yield rec
            rec["ok"] = True
        finally:
            rec["seconds"] = round(time.time() - t0, 1)
            self.steps.append(rec)
            print(f"[run_all] <<< {name}: {rec['seconds']} s{'' if rec['ok'] else ' (ОШИБКА)'}"
                  + (f" — {rec['note']}" if rec.get("note") else ""), flush=True)


# ------------------------------------------------------------------------------------------ prepare
def prepare_samples(c: Ctx):
    """--workdir: подмножество событий исходного CSV (строки как есть, байт в байт по полям)."""
    if not c.sub:
        return
    d = pd.read_csv(SAMPLES, dtype=str, keep_default_na=False, encoding="utf-8")
    ev = list(dict.fromkeys(d.event_id))
    if c.a.events:
        want = [e.strip() for e in c.a.events.split(",") if e.strip()]
        miss = [e for e in want if e not in ev]
        if miss:
            raise SystemExit(f"нет таких event_id в CSV: {miss}")
    else:
        want = ev[: c.a.limit_events or 5]
    c.samples.parent.mkdir(parents=True, exist_ok=True)
    d[d.event_id.isin(want)].to_csv(c.samples, index=False, encoding="utf-8")


def step_selection(c: Ctx, rec: dict):
    cfg = S.load_config()
    d = S.load_samples(c.samples)
    c.case.mkdir(parents=True, exist_ok=True)
    c.run.mkdir(parents=True, exist_ok=True)
    long, stats = [], {}
    for prof in cfg["profiles"]:
        acc, rej = S.select(d, cfg, prof)
        acc.to_csv(c.case / f"selection_{prof}.csv", index=False, encoding="utf-8")
        rej.to_csv(c.case / f"selection_{prof}_rejected.csv", index=False, encoding="utf-8")
        long.append(pd.DataFrame({"sample_id": acc.sample_id, "event_id": acc.event_id, "profile": prof,
                                  "status": "accept", "reason": "ok: " + prof}))
        long.append(rej.assign(profile=prof, status="reject")[["sample_id", "event_id", "profile", "status", "reason"]])
        stats[prof] = {"accepted_rows": len(acc), "accepted_events": int(acc.event_id.nunique()),
                       "rejected_rows": len(rej), "reasons": rej.reason.value_counts().sort_index().to_dict()}
    reg = pd.concat(long, ignore_index=True)
    reg = reg.merge(d[["sample_id", "source_id", "record_type", "target_scope", "measurement_profile"]], on="sample_id")
    order = {s: i for i, s in enumerate(d.sample_id)}
    reg["_o"] = reg.sample_id.map(order)
    reg = reg.sort_values(["profile", "_o"], kind="stable").drop(columns="_o")
    reg.to_csv(c.run / "registry_samples.csv", index=False, encoding="utf-8")
    c.data["selection"] = {"csv_rows": len(d), "events": int(d.event_id.nunique()),
                           "default_profile": cfg["default_profile"], "profiles": stats}
    rec["note"] = ", ".join(f"{p}: {v['accepted_rows']} принято / {v['rejected_rows']} отказ" for p, v in stats.items())


def step_pairs(c: Ctx, rec: dict):
    fp = load_script("find_pairs")
    calls = {"n": 0}
    lock = threading.Lock()
    orig_post = fp._session.post

    def counting_post(*a, **k):
        with lock:
            calls["n"] += 1
        return orig_post(*a, **k)

    orig_search = fp.stac_search

    def offline_search(url, coll, bbox, dt0, dt1, retries=5):
        # одна попытка: при промахе кэша — сразу ошибка «error:OfflineCacheMiss» в строке реестра, без ожиданий
        return orig_search(url, coll, bbox, dt0, dt1, retries=1)

    def no_network(*a, **k):
        with lock:
            calls["n"] += 1
        raise ConnectionError("offline: запроса нет в кэше data/pairs/cache")

    c.pairs.mkdir(parents=True, exist_ok=True)
    if c.sub:   # find_pairs пишет best_per_event.csv только если есть принятые пары — убрать остаток прошлого прогона
        for n in ("best_per_event.csv", "pair_quality.csv"):
            (c.pairs / n).unlink(missing_ok=True)
    kw = dict(CSV=c.samples, OUT=c.pairs, REPORT=c.reports / "case_pairs" / "summary.md")
    if c.a.pairs_cache:   # другой каталог кэша STAC (тест промахов кэша / замер «холодного» запуска)
        kw["CACHE"] = Path(c.a.pairs_cache).resolve()
    if c.offline:
        kw["stac_search"] = offline_search
    with patched(fp, **kw), patched(fp._session, post=no_network if c.offline else counting_post), argv([]):
        fp.main()
    cand = pd.read_csv(c.pairs / "candidates.csv", low_memory=False)
    errs = cand.reject_reason.fillna("").str.startswith("error")
    rec["note"] = (f"строк {len(cand)}, запросов мимо кэша {calls['n']}, ошибок {int(errs.sum())}"
                   + (" (offline: промахи кэша)" if c.offline and calls["n"] else ""))
    c.data["pairs_search"] = {"cache_misses_network_calls": calls["n"], "error_rows": int(errs.sum()),
                              "offline": c.offline}


def step_quality(c: Ctx, rec: dict):
    if not (c.pairs / "best_per_event.csv").is_file():   # ни одной пары по метаданным (бывает на подмножестве)
        (c.pairs / "pair_quality.csv").unlink(missing_ok=True)
        rec["note"] = "нет пар, принятых по метаданным: проверять нечего"
        return
    pq = load_script("pair_quality")
    summary_only = c.offline or c.sub
    args = ["--summary-only"] if summary_only else (["--force"] if c.force else [])
    if c.sub:
        # подмножество: читаем готовые meta.json из реальной data/pairs/quality, таблицу пишем в workdir
        kw = dict(PAIRS=c.pairs, REPORT=c.reports / "case_pairs" / "quality.md")
    else:
        kw = {}
    with patched(pq, **kw), argv(args):
        pq.main()
    t = pd.read_csv(c.pairs / "pair_quality.csv") if (c.pairs / "pair_quality.csv").is_file() else pd.DataFrame()
    if c.sub and len(t):
        best = pd.read_csv(c.pairs / "best_per_event.csv") if (c.pairs / "best_per_event.csv").is_file() else None
        keep = set(best.event_id) if best is not None else set()
        t = t[t.event_id.isin(keep)]
        t.to_csv(c.pairs / "pair_quality.csv", index=False)
    rec["note"] = ("только готовые meta.json" if summary_only else ("пересчёт всех пар" if c.force else "продолжение с места")) \
        + f"; пар с результатом {len(t)}"


def _event_geometry_cols(eid: str, g: pd.DataFrame) -> dict:
    """WKT (WGS84) of the observed strip centre line: PANGAEA track segments for S2/S3 (the same source as
    find_pairs / pair_quality), else the CSV transect line, else the reported point; plus width / length."""
    top = g[g.parent_sample_id.isna()] if "parent_sample_id" in g and g.parent_sample_id.isna().any() else g
    r0 = top.iloc[0]
    num = lambda v: None if pd.isna(v) else float(v)  # noqa: E731
    out = {"geometry_source": None, "geometry_wkt": None,
           "transect_width_m": num(r0.get("transect_width_m")), "transect_length_km": num(r0.get("transect_length_km"))}
    t = None
    if eid[:3] in ("S2:", "S3:"):
        try:
            from macroplastic.case.geometry import event_track
            t = event_track(eid)
        except Exception:  # noqa: BLE001  (no PANGAEA files -> CSV geometry)
            t = None
    fmt = lambda p: f"{p[0]:.6f} {p[1]:.6f}"  # noqa: E731
    if t is not None:
        geo = t["geometry"]
        if geo["type"] == "Point":
            out["geometry_wkt"] = f"POINT ({fmt(geo['coordinates'])})"
        elif geo["type"] == "LineString":
            out["geometry_wkt"] = "LINESTRING (" + ", ".join(fmt(p) for p in geo["coordinates"]) + ")"
        else:
            out["geometry_wkt"] = "MULTILINESTRING (" + ", ".join(
                "(" + ", ".join(fmt(p) for p in seg) + ")" for seg in geo["coordinates"]) + ")"
        out["geometry_source"] = f"pangaea_track ({t['geometry_status']})"
    elif all(pd.notna(r0.get(k)) for k in ("lon_start", "lat_start", "lon_end", "lat_end")):
        out["geometry_wkt"] = f"LINESTRING ({fmt((r0.lon_start, r0.lat_start))}, {fmt((r0.lon_end, r0.lat_end))})"
        out["geometry_source"] = "samples_csv_line"
    elif pd.notna(r0.get("longitude")) and pd.notna(r0.get("latitude")):
        out["geometry_wkt"] = f"POINT ({fmt((r0.longitude, r0.latitude))})"
        out["geometry_source"] = "samples_csv_point"
    return out


def build_pair_registry(c: Ctx) -> pd.DataFrame:
    """Одна строка на event_id исходного CSV: accept / reject + причина по этапам metadata → drift → quality."""
    d = pd.read_csv(c.samples, low_memory=False)
    cand = pd.read_csv(c.pairs / "candidates.csv", low_memory=False)
    best = pd.read_csv(c.pairs / "best_per_event.csv") if (c.pairs / "best_per_event.csv").is_file() else pd.DataFrame(columns=["event_id"])
    pq = pd.read_csv(c.pairs / "pair_quality.csv") if (c.pairs / "pair_quality.csv").is_file() else pd.DataFrame(columns=["event_id"])
    sel = pd.read_csv(c.run / "registry_samples.csv")
    cfg = S.load_config()
    sel = sel[sel.profile == cfg["default_profile"]]
    best_i, pq_i = best.set_index("event_id"), pq.set_index("event_id")
    rows = []
    for eid, g in d.groupby("event_id", sort=False):
        cg = cand[cand.event_id == eid]
        sg = sel[sel.event_id == eid]
        n_sel = int((sg.status == "accept").sum())
        r = {"event_id": eid, "source_id": g.source_id.iloc[0], "n_samples": len(g),
             "sample_ids": ";".join(g.sample_id), "date_utc": g.date_utc.iloc[0],
             "selection_accepted_rows": n_sel,
             "selection_reason": ("ok" if n_sel else ";".join(sorted(sg.reason.unique()))),
             "n_candidates": int(cg.item_id.notna().sum()), "n_accept_meta": int(cg.accept_meta.astype(bool).sum()),
             "n_accept_drift": int(cg.accept.astype(bool).sum())}
        fails = []
        if not len(cg):
            fails.append(("metadata", "no_candidates_row"))
        elif not r["n_accept_meta"]:
            sc = cg[cg.item_id.notna()]
            if len(sc):
                top = sc.assign(_a=sc.dt_hours.abs()).sort_values(["_a", "item_id"], kind="stable").iloc[0]
                fails.append(("metadata", f"closest {top.item_id}: {top.reject_reason}"))
            else:
                fails.append(("metadata", ";".join(sorted(set(";".join(cg.reject_reason.fillna("")).split(";")) - {""}))))
        b = best_i.loc[eid] if eid in best_i.index else None
        if b is not None:
            r.update(best_item_id=b.item_id, mission=b.mission, level=b.level, scene_datetime=b.scene_datetime,
                     dt_hours=b.dt_hours, cloud_cover=b.cloud_cover, drift_shift_km=b.drift_shift_km,
                     tolerance_km=b.tolerance_km)
            if not r["n_accept_drift"]:
                fails.append(("drift", f"sync_unreliable_drift: сдвиг {b.drift_shift_km} км > допуск {b.tolerance_km} км"))
            q = pq_i.loc[eid] if eid in pq_i.index else None
            if q is None:
                fails.append(("quality", "quality_not_computed"))
            else:
                r.update(quality_decision=q.decision, quality_reason=q.reason, n_det=q.n_det)
                if q.decision != "accept":
                    fails.append(("quality", f"quality_{q.decision}: {q.reason}"))
        r["status"] = "reject" if fails else "accept"
        r["stage"] = fails[0][0] if fails else "all"
        r["reason"] = " | ".join(f"{s}: {t}" for s, t in fails) if fails else "ok: metadata+drift+quality"
        qf = [f for f in fails if f[0] != "drift"]
        r["status_without_drift"] = "reject" if qf else "accept"
        # geometry of the observation (постановка «Реестр сопоставления»: геометрия, источник сцены, маски качества);
        # new columns go last so that older readers by name are unaffected
        r.update(_event_geometry_cols(eid, g))
        if b is not None:
            r["scene_source"] = f"{b.endpoint}/{b.collection}"
        qdir = Path("data") / "pairs" / "quality" / eid.replace(":", "_").replace("/", "_")
        if (ROOT / qdir / "quality.tif").is_file():
            r["quality_mask"] = (qdir / "quality.tif").as_posix()
        rows.append(r)
    reg = pd.DataFrame(rows)
    reg.to_csv(c.run / "registry_pairs.csv", index=False, encoding="utf-8")
    return reg


def step_registry(c: Ctx, rec: dict):
    reg = build_pair_registry(c)
    cand = pd.read_csv(c.pairs / "candidates.csv", low_memory=False)
    pq = pd.read_csv(c.pairs / "pair_quality.csv") if (c.pairs / "pair_quality.csv").is_file() else pd.DataFrame()
    c.data["pairs"] = {
        "events": len(reg),
        "candidate_rows": len(cand), "candidate_rows_with_scene": int(cand.item_id.notna().sum()),
        "rows_accept_meta": int(cand.accept_meta.astype(bool).sum()), "rows_accept_drift": int(cand.accept.astype(bool).sum()),
        "events_accept_meta": int(cand[cand.accept_meta.astype(bool)].event_id.nunique()),
        "events_accept_drift": int(cand[cand.accept.astype(bool)].event_id.nunique()),
        "quality_decisions": pq.decision.value_counts().sort_index().to_dict() if len(pq) else {},
        "quality_reject_reasons": pq[pq.decision != "accept"].reason.value_counts().sort_index().to_dict() if len(pq) else {},
        "pairs_with_detections": int((pd.to_numeric(pq.get("n_det"), errors="coerce") > 0).sum()) if len(pq) else 0,
        "registry_status": reg.status.value_counts().sort_index().to_dict(),
        "registry_stage": reg.stage.value_counts().sort_index().to_dict(),
        "registry_status_without_drift": reg.status_without_drift.value_counts().sort_index().to_dict(),
    }
    rec["note"] = f"событий {len(reg)}: {c.data['pairs']['registry_status']}, без учёта дрейфа {c.data['pairs']['registry_status_without_drift']}"


# ------------------------------------------------------------------------------------------ eval
def step_concentration(c: Ctx, rec: dict):
    if c.sub:
        rec["note"] = "пропущено для подмножества (--workdir): CV требует полного профиля"
        return
    bc = load_script("baseline_concentration")
    import contextlib
    log = c.reports / "case_run" / "concentration_stdout.md"   # отчёт baseline.md, который скрипт печатает
    with argv([]), open(log, "w", encoding="utf-8") as fh, contextlib.redirect_stdout(fh):
        bc.main()
    m = json.loads((ROOT / "reports" / "case_conc" / "metrics.json").read_text(encoding="utf-8"))
    out = {}
    for prof, v in m.items():
        if not isinstance(v, dict) or "overall" not in v:
            continue
        ms = v["main_split"]
        ov = {r["model"]: r for r in v["overall"] if r["split"] == ms}
        out[prof] = {"rows": v["rows"], "events": v["events"], "main_split": ms,
                     **{f"{mdl}_{k}": ov[mdl][k] for mdl in ("median", "knn5", "knn5_log") if mdl in ov
                        for k in ("mae", "rmse", "log1p_mae")},
                     "delta_mae_knn5_vs_median_ci95": v["bootstrap_mae_diff_vs_median"].get(f"{ms}:knn5")}
    out["consistency_N_over_A"] = m.get("consistency")
    c.data["concentration"] = out
    rec["note"] = "; ".join(f"{p}: median MAE {v['median_mae']:.1f} vs knn5 {v['knn5_mae']:.1f}"
                            for p, v in out.items() if isinstance(v, dict) and "median_mae" in v)


def _label_path(name: str) -> Path:
    import re
    m = re.match(r"^(?:S2_)?(\d{1,2})-(\d{1,2})-(\d{2})_([0-9]{2}[A-Z]{3})_(\d+)$", name)  # как l16_common._RE
    d, mo, yy, tile, i = m.groups()
    scene = f"S2_{d}-{mo}-{yy}_{tile}"
    return MARIDA / "patches" / scene / f"{scene}_{i}_cl.tif"


def recompute_detector(split: str) -> dict | None:
    """TP/FP/FN/P/R/F1/IoU всех 7 моделей из сохранённых масок (packbits) и меток MARIDA *_cl.tif (только метки)."""
    import rasterio
    z = np.load(DET_DIR / f"{split}_preds.npz")
    names = [str(n) for n in z["names"]]
    paths = [_label_path(n) for n in names]
    if not all(p.is_file() for p in paths):
        return None
    tot = {m: np.zeros(3, np.int64) for m in DET_MODELS}
    masks = {m: z[f"mask_{m}"] for m in DET_MODELS}
    for i, p in enumerate(paths):
        with rasterio.open(p) as ds:
            cl = ds.read(1).astype(np.uint8)
        lab = cl > 0
        y = cl[lab] == 1
        for m in DET_MODELS:
            pm = np.unpackbits(masks[m][i], axis=-1)[:, :256].astype(bool)[lab]
            tot[m] += [np.count_nonzero(y & pm), np.count_nonzero(~y & pm), np.count_nonzero(y & ~pm)]
    out = {}
    for m, (tp, fp, fn) in tot.items():
        tp, fp, fn = int(tp), int(fp), int(fn)
        out[m] = {"tp": tp, "fp": fp, "fn": fn,
                  "precision_md": tp / (tp + fp) if tp + fp else None, "recall_md": tp / (tp + fn) if tp + fn else None,
                  "f1_md": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
                  "iou_md": tp / (tp + fp + fn) if tp + fp + fn else None}
    return {"n_patches": len(names), "models": out}


def step_detector(c: Ctx, rec: dict):
    saved = json.loads(DET_METRICS.read_text(encoding="utf-8"))
    res = {"metrics_json": rel(DET_METRICS), "metrics_json_sha256": sha256(DET_METRICS),
           "preds_sha256": {p.name: sha256(p) for p in sorted(DET_DIR.glob("*_preds.npz"))}, "splits": {}}
    for split in ("test", "val"):
        r = recompute_detector(split) if (DET_DIR / f"{split}_preds.npz").is_file() else None
        if r is None:
            res["splits"][split] = {"recomputed": False, "note": "нет npz или меток MARIDA — взяты числа metrics.json"}
            continue
        match = {m: all(r["models"][m][k] == saved[split][m][k] for k in ("tp", "fp", "fn")) for m in DET_MODELS}
        res["splits"][split] = {"recomputed": True, "n_patches": r["n_patches"], "all_match_metrics_json": all(match.values()),
                                "match": match, "models": r["models"]}
    out = c.reports / "case_run" / "detector_recomputed.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    test = saved["test"]
    c.data["detector"] = {
        "source": rel(DET_METRICS), "metrics_json_sha256": res["metrics_json_sha256"],
        "recomputed_from_preds": {s: v.get("all_match_metrics_json") if v["recomputed"] else None for s, v in res["splits"].items()},
        "test": {m: {k: test[m][k] for k in ("f1_md", "precision_md", "recall_md", "iou_md", "ci95_f1", "tp", "fp", "fn")}
                 for m in DET_MODELS},
        "val_f1": {m: saved["val"][m]["f1_md"] for m in DET_MODELS if m in saved.get("val", {})},
    }
    def _state(v):  # без разметки MARIDA пересчёт пропускается — это не «совпал»
        if not v.get("recomputed"):
            return "не пересчитан (нет data/MARIDA)"
        return "совпал" if v.get("all_match_metrics_json") else "НЕ совпал"
    rec["note"] = (f"test F1 lgbm {test['lgbm']['f1_md']:.4f}; пересчёт из npz: "
                   + ", ".join(f"{s} — {_state(v)}" for s, v in res["splits"].items()))


def step_search_numbers(c: Ctx, rec: dict):
    """§11 «Расследование данных»: числа из reports/search, extra_data, detector_v2, quantity, oil (офлайн, файлы в git)
    -> reports/search/search_numbers.json; в final_numbers.json тот же сбор попадает в case.sections."""
    if c.sub:
        rec["note"] = "пропущено (--workdir)"
        return
    m = load_script("collect_search")
    res = m.collect()
    (ROOT / "reports" / "search").mkdir(parents=True, exist_ok=True)
    m.OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    s, ad = res["search"], res["adis_pairs"]
    rec["note"] = (f"поиск A/B/C/D {s.get('totals')}; ADIS A {ad.get('A')}, с предметами {ad.get('A_with_items')}, "
                   f"пикселей детектора {ad.get('A_with_items_det_px')}")


# ------------------------------------------------------------------------------------------ export
def step_export(c: Ctx, rec: dict):
    import tempfile
    from fastapi.testclient import TestClient
    from service import case_store as cs
    from service.app import create_app

    c.export.mkdir(parents=True, exist_ok=True)
    tmpq = Path(tempfile.mkdtemp()) / "queries.jsonl"   # сохранённые запросы API не трогаем
    over = {"queries": tmpq}
    if c.sub:
        over.update(samples=c.samples, pairs_dir=c.pairs, conc_metrics=c.reports / "case_conc" / "metrics.json")
    old = {k: cs.PATHS[k] for k in over}
    cs.PATHS.update(over)
    try:
        cl = TestClient(create_app())
        info = []
        for fname, qs in EXPORTS:
            r = cl.get(f"/api/v3/export?{qs}")
            if r.status_code != 200:
                raise RuntimeError(f"/api/v3/export?{qs}: HTTP {r.status_code} {r.text[:200]}")
            (c.export / fname).write_bytes(r.content)
            n = None
            if fname.endswith(".geojson"):
                n = len(json.loads(r.content)["features"])
            else:
                n = max(0, len(list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))) - 1)
            info.append({"file": fname, "request": f"GET /api/v3/export?{qs}", "records": n})
    finally:
        cs.PATHS.update(old)
    (c.export / "requests.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    c.data["export"] = {"dir": rel(c.export), "files": info}
    rec["note"] = ", ".join(f"{i['file']}: {i['records']}" for i in info[:6])


# ------------------------------------------------------------------------------------------ summary
def output_files(c: Ctx) -> list[Path]:
    fs = sorted(c.case.glob("selection_*.csv")) + [c.run / "registry_samples.csv", c.run / "registry_pairs.csv"]
    fs += [c.pairs / n for n in ("events.csv", "candidates.csv", "best_per_event.csv", "pair_quality.csv")]
    if not c.sub:
        fs += [ROOT / "reports" / "case_conc" / n for n in ("predictions.csv", "metrics_by_fold.csv", "consistency.csv", "metrics.json")]
    fs += [c.reports / "case_run" / "detector_recomputed.json"]
    fs += [c.export / f for f, _ in EXPORTS]
    return [f for f in fs if f.is_file()]


def versions() -> dict:
    v = {"python": platform.python_version(), "platform": platform.platform()}
    for m in ("numpy", "pandas", "sklearn", "lightgbm", "rasterio", "shapely", "fastapi", "yaml"):
        try:
            v[m] = __import__(m).__version__
        except Exception:  # noqa: BLE001
            v[m] = None
    try:
        v["git_commit"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                         timeout=10).stdout.strip() or None
    except Exception:  # noqa: BLE001
        v["git_commit"] = None
    return v


def write_summary(c: Ctx, cmd: str):
    outs = output_files(c)
    tab = {rel(p): sha256(p) for p in outs}
    c.data.update({
        "command": " ".join(["run_all.py"] + sys.argv[1:]),
        "last_subcommand": cmd,
        "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "offline": c.offline, "force": c.force, "workdir": rel(Path(c.a.workdir)) if c.sub else None,
        "versions": versions(),
        "inputs": {rel(p): sha256(p) for p in [c.samples] + CONFIGS + [ROOT / "weights" / "lgbm" / "meta.json"]},
        "outputs_sha256": tab,
        # отпечаток — по выходам, не зависящим от окружения: detector_recomputed.json зависит от наличия
        # разметки MARIDA (не в git; без неё пересчёт пропускается), его sha256 остаётся в outputs_sha256
        "outputs_fingerprint": hashlib.sha256(json.dumps(
            {k: v for k, v in tab.items() if not k.endswith("detector_recomputed.json")}, sort_keys=True).encode()).hexdigest(),
        "fingerprint_excludes": ["reports/case_run/detector_recomputed.json (зависит от наличия MARIDA)"],
    })
    steps = c.data.get("steps", {})
    for s in c.steps:
        steps[s["step"]] = s
    c.data["steps"] = steps
    c.summary.write_text(json.dumps(c.data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"[run_all] итог: {rel(c.summary)}; отпечаток выходов {c.data['outputs_fingerprint'][:16]}", flush=True)


PLAN = {
    "prepare": [("1_selection", step_selection), ("2_pairs", step_pairs), ("3_quality_detector", step_quality),
                ("3b_registry", step_registry)],
    "eval": [("4_concentration", step_concentration), ("5_detector_marida", step_detector),
             ("5b_search_numbers", step_search_numbers)],
    "export": [("6_export_api_v3", step_export)],
}


def main(argv_: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["prepare", "eval", "export", "all"])
    ap.add_argument("--offline", action="store_true", help="без сети: пары из кэша, качество из готовых meta.json")
    ap.add_argument("--force", action="store_true", help="пересчитать качество всех пар (нужна сеть)")
    ap.add_argument("--workdir", default=None, help="подмножество событий: все выходы в этот каталог")
    ap.add_argument("--events", default=None, help="event_id через запятую (с --workdir)")
    ap.add_argument("--pairs-cache", default=None, help="каталог кэша STAC вместо data/pairs/cache (отладка)")
    ap.add_argument("--limit-events", type=int, default=0, help="первые N событий CSV (с --workdir)")
    a = ap.parse_args(argv_)
    if (a.events or a.limit_events) and not a.workdir:
        ap.error("--events/--limit-events только вместе с --workdir (чтобы не перезаписать полный реестр)")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    c = Ctx(a)
    t0 = time.time()
    cmds = ["prepare", "eval", "export"] if a.cmd == "all" else [a.cmd]
    prepare_samples(c)
    rc = 0
    try:
        for cmd in cmds:
            for name, fn in PLAN[cmd]:
                with c.step(name) as rec:
                    fn(c, rec)
    except Exception as e:  # noqa: BLE001
        print(f"[run_all] ОШИБКА: {type(e).__name__}: {e}", flush=True)
        c.data["error"] = f"{type(e).__name__}: {e}"
        rc = 1
    else:
        c.data.pop("error", None)
    c.steps.append({"step": f"total_{a.cmd}", "ok": rc == 0, "seconds": round(time.time() - t0, 1)})
    write_summary(c, a.cmd)
    print("[run_all] время шагов: " + "; ".join(f"{s['step']} {s['seconds']} s" for s in c.steps), flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
