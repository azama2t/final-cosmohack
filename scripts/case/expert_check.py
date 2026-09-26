"""Повторная проверка расчёта, выбранного экспертом, без правки кода (постановка: «Демонстрация решения»; критерий Т5).

Эксперт называет запись поля, полосу или свои числа. Скрипт заново считает результат из исходных файлов
(CSV организаторов, реестр пар, сохранённые маски качества и вероятности детектора) и сверяет его с тем,
что отдают API и выгрузка. Настройки (порог детектора, пороги масок) задаются ключами, код не меняется.

  .venv\\Scripts\\python.exe scripts\\case\\expert_check.py --sample-id MPL-0197            # C = N/A, интервал, профиль, медиана
  .venv\\Scripts\\python.exe scripts\\case\\expert_check.py --zone-id Z-S3_HE460_MarLitter_transect03
  .venv\\Scripts\\python.exe scripts\\case\\expert_check.py --event-id S4:DOORS3:T1 --threshold 0.5 --max-cloud 0.3
  .venv\\Scripts\\python.exe scripts\\case\\expert_check.py --n 12 --area 0.20 --pred 75     # контрольный пример постановки
  .venv\\Scripts\\python.exe scripts\\case\\expert_check.py --event-id S3:HE460_MarLitter_transect03 --rerun   # снимок заново (сеть)
  ... --api http://127.0.0.1:8000     # сверять с запущенным сервисом, а не с API в процессе

Выход: таблица «что → пересчитано → сервис → совпало» и out/expert_check/<id>.json. Код выхода: 0 — всё совпало,
1 — есть расхождение, 2 — неверный вход (нет такой записи или полосы).
Что пересчитывается точно, а что — с оговоркой, написано в колонке «примечание». Детектор: вероятности хранятся
в prob.tif с шагом 1/255; удаление объектов у облаков и теней требует каналов снимка и без --rerun не повторяется.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "src", ROOT / "scripts" / "case"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

SAMPLES = ROOT / "task" / "macroplastic_marine_samples.csv"
PAIRS = ROOT / "data" / "pairs"
REGISTRY = ROOT / "data" / "case" / "run" / "registry_pairs.csv"
OUT = ROOT / "out" / "expert_check"


# ------------------------------------------------------------------------------------------ helpers

class Report:
    def __init__(self, title: str):
        self.title = title
        self.rows: list[dict] = []

    def add(self, what, mine, theirs=None, ok=None, note=""):
        self.rows.append({"что": what, "пересчитано": mine, "сервис": theirs, "ok": ok, "примечание": note})

    def cmp(self, what, mine, theirs, tol=1e-6, rel=0.0, note=""):
        if mine is None or theirs is None:
            ok = (mine is None and theirs is None)
        elif isinstance(mine, (int, float)) and isinstance(theirs, (int, float)):
            ok = abs(float(mine) - float(theirs)) <= max(tol, rel * abs(float(theirs)))
        else:
            ok = str(mine) == str(theirs)
        self.add(what, mine, theirs, ok, note)
        return ok

    @property
    def failed(self) -> list[dict]:
        return [r for r in self.rows if r["ok"] is False]

    def print(self):
        print(f"\n== {self.title}")
        w = max(len(str(r["что"])) for r in self.rows) if self.rows else 10
        for r in self.rows:
            mark = {True: "совпало", False: "РАСХОЖДЕНИЕ", None: "—"}[r["ok"]]
            line = f"  {str(r['что']):<{w}}  {_fmt(r['пересчитано']):>14}  {_fmt(r['сервис']):>14}  {mark}"
            if r["примечание"]:
                line += f"  ({r['примечание']})"
            print(line)


def _fmt(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


class Service:
    """GET against the running service (--api) or the same FastAPI app in-process (default, no port needed)."""

    def __init__(self, api: str | None):
        self.api = api.rstrip("/") if api else None
        if not self.api:
            from fastapi.testclient import TestClient
            from service.app import create_app
            self._c = TestClient(create_app())

    def get(self, path: str):
        if self.api:
            import urllib.error
            import urllib.request
            try:
                with urllib.request.urlopen(self.api + path, timeout=120) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.read()
        r = self._c.get(path)
        return r.status_code, r.content

    def json(self, path: str):
        s, b = self.get(path)
        return s, (json.loads(b) if b else None)

    @property
    def label(self) -> str:
        return self.api or "API в процессе (service.app)"


def _props(body):
    if not isinstance(body, dict):
        return None
    if "properties" in body:
        return body["properties"]
    feats = body.get("features")
    if feats:
        return feats[0].get("properties")
    return body


# ------------------------------------------------------------------------------------------ manual numbers

def check_manual(n: float, area: float, pred: float | None) -> Report:
    from macroplastic.case.concentration import concentration
    rep = Report(f"Свои числа: N = {n:g}, A = {area:g} км²")
    res = concentration(n, area)
    rep.add("C = N / A, шт./км²", None if res.value is None else round(res.value, 4), None, None,
            res.note or "src/macroplastic/case/concentration.py")
    if res.value is not None:
        rep.cmp("C тем же делением вручную", round(n / area, 4), round(res.value, 4), tol=1e-4)
        rep.add("95 % ДИ Пуассона, шт./км²", f"{res.lower:.1f}–{res.upper:.1f}", None, None, "Гарвуд: χ²-квантили для N, делённые на A")
        if pred is not None:
            rep.add("абсолютная ошибка прогноза, шт./км²", round(abs(pred - res.value), 4), None, None, f"прогноз {pred:g}")
    return rep


# ------------------------------------------------------------------------------------------ field record

def check_sample(sid: str, svc: Service) -> Report:
    from macroplastic.case import selection as S
    from macroplastic.case.concentration import concentration
    df = pd.read_csv(SAMPLES, low_memory=False)
    row = df[df.sample_id.astype(str) == sid]
    if row.empty:
        raise LookupError(f"sample_id {sid} нет в {SAMPLES.relative_to(ROOT)}")
    r = row.iloc[0]
    rep = Report(f"Запись поля {sid} ({r.event_id}; {r.record_type}; {r.target_scope}; {r.measurement_profile}; {r.size_class})")
    s, body = svc.json(f"/api/v3/observations/{sid}")
    p = _props(body) if s == 200 else None
    rep.cmp("HTTP /api/v3/observations/{id}", 200, s)
    pub = _num(r.concentration_items_km2)
    rep.cmp("концентрация из CSV, шт./км²", pub, None if p is None else _num(p.get("concentration_items_km2")), tol=1e-6)
    n, a = _num(r.density_numerator_items), _num(r.sampled_area_km2)
    if r.record_type == "item_observation":
        rep.add("C = N / A", None, None, None, "отдельный предмет, не плотность — C не считается")
    elif n is None or a is None:
        rep.add("C = N / A", None, None, None, "в источнике нет N или A — интервал не считается")
    else:
        res = concentration(n, a)
        c = n / a
        same = pub is None or abs(c - pub) <= max(0.02 * abs(pub), 0.05)
        rep.add("C = N / A, шт./км²", round(c, 4), pub, same, f"N = {n:g}, A = {a:g} км²; допуск 2 % к опубликованной")
        if same and p is not None:
            rep.cmp("95 % ДИ Пуассона, нижняя", round(res.lower, 4), _num(p.get("ci95_lo")), tol=1e-3)
            rep.cmp("95 % ДИ Пуассона, верхняя", round(res.upper, 4), _num(p.get("ci95_hi")), tol=1e-3)
    # profile membership and the map value (median of the dev part of the profile)
    cfg = S.load_config(S.CONFIG_YAML)
    member = None
    for prof in cfg.get("profiles") or {}:
        acc, rej = S.select(df, cfg, prof)
        if sid in set(acc.sample_id.astype(str)):
            member = (prof, acc)
            break
    if member is None:
        rep.add("профиль концентрации", "нет", None, None, "запись не входит ни в один профиль; оценки по полю нет")
        rep.cmp("оценка по полю на карте", None, None if p is None else (p.get("field_estimate") or {}).get("value"))
        return rep
    prof, acc = member
    rep.add("профиль концентрации", prof, None if p is None else (p.get("field_estimate") or {}).get("profile_config"),
            None if p is None else prof == (p.get("field_estimate") or {}).get("profile_config"), "configs/case_selection.yaml")
    ft = ((cfg.get("final_test") or {}).get("profiles") or {}).get(prof) or {}
    split = ROOT / (ft.get("file") or f"reports/case_splits/final_test_{prof}.csv")
    if not split.is_file():
        split = ROOT / "reports" / "case_splits" / f"final_test_{prof}.csv"
    sp = pd.read_csv(split)
    role = dict(zip(sp.sample_id.astype(str), sp.role)).get(sid)
    rep.add("роль в разбиении", role, None, None, f"{split.relative_to(ROOT)} (зафиксирован до моделей)")
    dev_ids = set(sp.loc[sp.role == "dev", "sample_id"].astype(str))
    med = float(np.median(acc.loc[acc.sample_id.astype(str).isin(dev_ids), "target"]))
    fe = None if p is None else p.get("field_estimate") or {}
    rep.cmp("оценка по полю = медиана dev профиля, шт./км²", round(med, 2), None if fe is None else _num(fe.get("value")),
            tol=0.006, note=f"{len(dev_ids)} dev-событий; на test модель не лучше медианы")
    return rep


# ------------------------------------------------------------------------------------------ strip / zone

def _decision_cfg(cfg: dict, a) -> tuple[dict, list[str]]:
    d = dict(cfg["decision"])
    changed = []
    for k, v in (("min_coverage", a.min_coverage), ("max_cloud_frac", a.max_cloud), ("max_land_frac", a.max_land),
                 ("min_valid_water_frac", a.min_water), ("glint_b11", a.glint_b11)):
        if v is not None and v != d.get(k):
            changed.append(f"{k} {d.get(k)} → {v}")
            d[k] = v
    return {**cfg, "decision": d}, changed


def check_zone(event_id: str | None, zone_id: str | None, svc: Service, a) -> Report:
    import pair_quality as PQ
    import rasterio
    from rasterio.features import rasterize
    from scipy import ndimage
    pq = pd.read_csv(PAIRS / "pair_quality.csv")
    if zone_id:
        d = zone_id[2:] if zone_id.startswith("Z-") else zone_id
        hit = pq[pq.dir == d]
    else:
        hit = pq[pq.event_id == event_id]
    if hit.empty:
        known = ", ".join(pq.event_id.head(5))
        raise LookupError(f"полосы {zone_id or event_id} нет в data/pairs/pair_quality.csv (29 полос; например {known})")
    q = hit.iloc[0]
    eid, d = q.event_id, q.dir
    zid = f"Z-{d}"
    meta = json.loads((PAIRS / "quality" / d / "meta.json").read_text(encoding="utf-8"))
    rep = Report(f"Полоса {zid} ({eid}; снимок {meta.get('scene_id')}; {meta.get('scene_datetime')})")
    s, body = svc.json(f"/api/v3/zones/{zid}")
    z = _props(body) if s == 200 else None
    rep.cmp("HTTP /api/v3/zones/{id}", 200, s)

    # --- pair registry: time shift and drift
    reg = {r["event_id"]: r for r in csv.DictReader(open(REGISTRY, encoding="utf-8"))}.get(eid) or {}
    shift, tol = _num(reg.get("drift_shift_km")), _num(reg.get("tolerance_km"))
    rep.add("Δt снимок − наблюдение, ч", _num(reg.get("dt_hours")), None if z is None else None, None, "data/case/run/registry_pairs.csv")
    drift_ok = None if shift is None or tol is None else shift <= tol
    rep.add("дрейф / допуск, км", None if shift is None else f"{shift:.1f} / {tol:.1f}",
            None if z is None else f"{_num(z.get('pair_drift_shift_km')):.1f} / {_num(z.get('pair_tolerance_km')):.1f}"
            if z.get("pair_drift_shift_km") is not None else None, None,
            "в допуске" if drift_ok else "дрейф больше допуска → пара несинхронна")
    pair_acc = reg.get("status") == "accept"
    rep.cmp("пара «снимок ↔ поле» подтверждена", "да" if pair_acc else "нет",
            None if z is None else ("да" if z.get("pair_status") == "accepted" else "нет"),
            note=f"реестр: {reg.get('status')} / этап {reg.get('stage')} / {reg.get('reason')}")

    # --- quality masks in the strip, rebuilt from quality.tif + the event geometry
    cfg = meta["config"]
    samples = pd.read_csv(SAMPLES, low_memory=False)
    g = PQ.event_geometry(samples, eid, cfg)
    poly, rule = PQ.strip_polygon(g, int(meta["epsg"]), cfg)
    with rasterio.open(PAIRS / "quality" / d / "quality.tif") as src:
        qa, tr = src.read(1), src.transform
    strip = rasterize([(poly, 1)], out_shape=qa.shape, transform=tr, all_touched=True, fill=0, dtype="uint8").astype(bool)
    mq = meta.get("quality") or {}
    ns = int(strip.sum())
    rep.cmp("пикселей в полосе", ns, mq.get("strip_px"), note=rule)
    valid = qa != PQ.Q_NODATA
    nsv = max(int((strip & valid).sum()), 1)
    qr = dict(coverage=round(float((strip & valid).sum() / max(ns, 1)), 4),
              cloud_frac=round(float((strip & np.isin(qa, (PQ.Q_CLOUD, PQ.Q_SPCLOUD))).sum() / nsv), 4),
              land_frac=round(float((strip & (qa == PQ.Q_LAND)).sum() / nsv), 4))
    water = round(float((strip & (qa == PQ.Q_WATER)).sum() / max(ns, 1)), 4)
    rep.cmp("покрытие полосы", qr["coverage"], mq.get("coverage"), tol=1e-4)
    rep.cmp("облака в полосе", qr["cloud_frac"], mq.get("cloud_frac"), tol=1e-4)
    rep.cmp("суша в полосе", qr["land_frac"], mq.get("land_frac"), tol=1e-4)
    vw = _num(mq.get("valid_water_frac"))
    rep.add("пригодная вода в полосе", water, vw, vw is not None and vw <= water + 1e-4,
            "из quality.tif; сервис ещё исключает пиксельный блик B11 — может быть меньше")
    if z is not None:
        rep.cmp("облака: сервис = маска", _num(mq.get("cloud_frac")), _num((z.get("quality") or {}).get("cloud_fraction")), tol=1e-4)
    cfg2, changed = _decision_cfg(cfg, a)
    q2 = {**qr, "valid_water_frac": vw if vw is not None else water, "glint_frac": mq.get("glint_frac", 0) or 0,
          "glint_b11_median": mq.get("glint_b11_median")}
    dec, why = PQ.decide(q2, cfg2)
    rc = _num(mq.get("red_sr_median_qa_cloud_strip"))  # Landsat: the same note as process_landsat
    if why == "cloud" and rc is not None and rc < 0.03:
        why = "cloud(qa_suspect:red_sr<0.03)"
    saved_dec = f"{meta.get('decision')} {meta.get('reason') or ''}".strip()
    if changed:
        rep.add("решение по маскам (пороги эксперта)", f"{dec} {why}".strip(), None, None,
                "; ".join(changed) + f"; с порогами конфига: {saved_dec}")
    else:
        rep.cmp("решение по маскам", f"{dec} {why}".strip(), saved_dec, note="пороги configs/case_pairs.yaml")

    # --- detector: saved probabilities (prob.tif, P*255) in the strip on usable water
    det_info = meta.get("detector") or {}
    n_det_expert = None
    if (PAIRS / "quality" / d / "prob.tif").is_file() and det_info:
        with rasterio.open(PAIRS / "quality" / d / "prob.tif") as src:
            prob = src.read(1).astype(np.float32) / 255.0
        thr_saved = float(det_info.get("threshold"))
        thr = a.threshold if a.threshold is not None else thr_saved
        water_ok = qa == PQ.Q_WATER
        det = (prob >= thr - 0.5 / 255) & water_ok
        lab, n = ndimage.label(det, structure=np.ones((3, 3), bool))
        if n:
            sizes = np.bincount(lab.ravel(), minlength=n + 1)[1:]
            keep = np.concatenate([[False], sizes >= int(cfg["detector"].get("min_px", 1))])
            det = keep[lab]
            lab, n = ndimage.label(det, structure=np.ones((3, 3), bool))
        in_strip = np.unique(lab[strip & det])
        n_det_expert = int((in_strip > 0).sum())
        px = int((det & strip).sum())
        sw = strip & water_ok
        pmax = round(float(prob[sw].max()), 4) if sw.any() else None
        dropped = int(det_info.get("n_dropped_cloud_shadow") or 0)
        note_q = "шаг prob.tif 1/255"
        if thr == thr_saved:
            ok_px = px == det_info.get("det_px_strip") or (dropped > 0 and px >= (det_info.get("det_px_strip") or 0))
            rep.add(f"пикселей детектора в полосе, P ≥ {thr:g}", px, det_info.get("det_px_strip"), ok_px,
                    note_q + (f"; у облаков удалено {dropped} объектов — без каналов не повторяется" if dropped else ""))
            rep.add("объектов детектора в полосе", n_det_expert, det_info.get("n_det"),
                    n_det_expert == det_info.get("n_det") or (dropped > 0 and n_det_expert >= (det_info.get("n_det") or 0)))
        else:
            rep.add(f"пикселей детектора в полосе, P ≥ {thr:g} (порог эксперта)", px, None, None,
                    f"рабочий порог {thr_saved:g}: {det_info.get('det_px_strip')} пикс.")
            rep.add("объектов детектора в полосе (порог эксперта)", n_det_expert, None, None,
                    f"рабочий порог: {det_info.get('n_det')}")
        rep.cmp("макс. вероятность в полосе", pmax, _num(det_info.get("prob_max")), tol=0.5 / 255 + 1e-4, note=note_q)
        if z is not None:
            rep.cmp("объектов: сервис = сохранённый результат", det_info.get("n_det"), (z.get("detector") or {}).get("n_objects"))
    else:
        rep.add("детектор", None, None, None, "Landsat: только маска QA_PIXEL, детектор обучен на каналах S2")

    # --- statuses: the rule of service/case_store.py (_zones_all_build), recomputed
    def verdict(decision: str, n_obj):
        if not pair_acc:
            return "insufficient_data"
        if decision != "accept" or n_obj is None:
            return "insufficient_data"
        return "detected" if n_obj > 0 else "not_detected"
    v_saved = verdict(meta.get("decision"), det_info.get("n_det") if det_info else None)
    rep.cmp("статус детекции", v_saved, None if z is None else z.get("detection_status"),
            note="пара не подтверждена → «недостаточно данных»" if not pair_acc else "")
    if changed or a.threshold is not None:
        rep.add("статус детекции с настройками эксперта", verdict(dec, n_det_expert), None, None,
                "без изменения кода и сохранённых файлов")
    rep.cmp("статус концентрации", "unavailable", None if z is None else z.get("concentration_status"),
            note="калибровки «снимок → шт./км²» нет")

    # --- export: the same zone in the CSV export
    s, b = svc.get("/api/v3/export?layer=zones&format=csv")
    rows = {r["zone_id"]: r for r in csv.DictReader(io.StringIO(b.decode("utf-8-sig")))} if s == 200 else {}
    e = rows.get(zid)
    rep.cmp("выгрузка CSV: статус", None if z is None else z.get("detection_status"), None if e is None else e.get("detection_status"))
    rep.cmp("выгрузка CSV: площадь полосы, км²", None if z is None else _num(z.get("area_km2")),
            None if e is None else _num(e.get("area_km2")), tol=1e-4)

    # --- optional: read the scene again (network) into out/expert_check/, tracked files untouched
    if a.rerun:
        best = pd.read_csv(PAIRS / "best_per_event.csv")
        br = best[best.event_id == eid]
        if br.empty:
            rep.add("пересчёт снимка", None, None, False, "события нет в data/pairs/best_per_event.csv")
        else:
            row = next(br.itertuples())
            outdir = OUT / d
            if row.collection.startswith("landsat"):
                res = PQ.process_landsat(row, g, cfg, outdir)
            else:
                from macroplastic.models.lgbm_predict import load_predictor
                pred = load_predictor(ROOT / cfg["detector"]["weights"], harmonize=PQ.harmonize_mode(cfg))
                res = PQ.process_s2(row, g, cfg, pred, outdir)
            rq, rd = res.get("quality") or {}, res.get("detector") or {}
            rep.cmp("снимок заново: пикселей в полосе", rq.get("strip_px"), mq.get("strip_px"))
            rep.cmp("снимок заново: облака", rq.get("cloud_frac"), mq.get("cloud_frac"), tol=1e-4)
            rep.cmp("снимок заново: решение", f"{res.get('decision')} {res.get('reason') or ''}".strip(),
                    f"{meta.get('decision')} {meta.get('reason') or ''}".strip())
            if rd:
                rep.cmp("снимок заново: объектов в полосе", rd.get("n_det"), det_info.get("n_det"))
                rep.cmp("снимок заново: макс. вероятность", rd.get("prob_max"), det_info.get("prob_max"), tol=1e-3)
            rep.add("файлы пересчёта", str(outdir.relative_to(ROOT)), None, None, "rgb / quality / prob / mask")
    return rep


# ------------------------------------------------------------------------------------------ main

def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample-id", help="запись поля, например MPL-0197")
    ap.add_argument("--event-id", help="событие с полосой-кандидатом, например S4:DOORS3:T1")
    ap.add_argument("--zone-id", help="полоса на карте, например Z-S3_HE460_MarLitter_transect03")
    ap.add_argument("--n", type=float, help="свои числа: число предметов N")
    ap.add_argument("--area", type=float, help="свои числа: обследованная площадь A, км²")
    ap.add_argument("--pred", type=float, help="свои числа: прогноз модели, шт./км² → абсолютная ошибка")
    ap.add_argument("--threshold", type=float, help="порог P детектора (рабочий — из weights/lgbm/meta.json, 0.63)")
    ap.add_argument("--min-coverage", type=float); ap.add_argument("--max-cloud", type=float)
    ap.add_argument("--max-land", type=float); ap.add_argument("--min-water", type=float)
    ap.add_argument("--glint-b11", type=float)
    ap.add_argument("--rerun", action="store_true", help="прочитать снимок заново и пересчитать маски и детектор (сеть, 10–60 с)")
    ap.add_argument("--api", help="адрес запущенного сервиса, например http://127.0.0.1:8000 (по умолчанию — API в процессе)")
    ap.add_argument("--json", help="куда сохранить отчёт (по умолчанию out/expert_check/<id>.json)")
    a = ap.parse_args(argv)
    if not (a.sample_id or a.event_id or a.zone_id or (a.n is not None and a.area is not None)):
        ap.error("нужен --sample-id, --event-id, --zone-id или пара --n и --area")
    reps: list[Report] = []
    try:
        if a.n is not None and a.area is not None:
            reps.append(check_manual(a.n, a.area, a.pred))
        if a.sample_id or a.event_id or a.zone_id:
            svc = Service(a.api)
            print(f"Сверка с: {svc.label}")
            if a.sample_id:
                reps.append(check_sample(a.sample_id, svc))
            if a.event_id or a.zone_id:
                reps.append(check_zone(a.event_id, a.zone_id, svc, a))
    except LookupError as e:
        print(f"Ошибка входа: {e}", file=sys.stderr)
        return 2
    for r in reps:
        r.print()
    bad = [x for r in reps for x in r.failed]
    key = (a.sample_id or a.zone_id or a.event_id or f"manual_{a.n:g}_{a.area:g}").replace(":", "_")
    out = Path(a.json) if a.json else OUT / f"{key}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"input": vars(a), "reports": [{"title": r.title, "rows": r.rows} for r in reps],
                               "mismatches": len(bad)}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\nИтог: {'расхождений нет' if not bad else f'расхождений {len(bad)}'}; отчёт — {out}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
