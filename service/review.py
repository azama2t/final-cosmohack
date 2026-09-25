"""Human review ("проверка человеком", lane L19, INBOX 1.6): queue of doubtful detections, labels, retrain jobs.

Labels file: $MACROPLASTIC_LABELS (directory -> <dir>/labels.jsonl, or a *.jsonl path), default
service/labels/labels.jsonl. One JSON object per line, append-only:
  {"kind": "label", "id", "region", "date", "model", "lon", "lat", "label", "note",
   "provenance": {"user": "local", "ts", "app_version", "source_scene_id", "model", "max_prob"}}
  {"kind": "flag_false", ...same keys, label = null}   <- "ложное" mark from the map (queue input)
Retrain: scripts/retrain_with_labels.py in a background process; one job at a time.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Optional

from . import core, place

LABELS_ENV = "MACROPLASTIC_LABELS"
LABELS = ("debris", "foam", "algae", "ship_wake", "cloud", "other")
LABELS_RU = {"debris": "мусор", "foam": "пена", "algae": "водоросли", "ship_wake": "судно/след", "cloud": "облако",
             "other": "другое"}
NEAR_THR = 0.15
_lock = threading.Lock()


def labels_path() -> Path:
    env = os.environ.get(LABELS_ENV)
    if env:
        p = Path(env)
        return p if p.suffix == ".jsonl" else p / "labels.jsonl"
    return core.SERVICE_DIR / "labels" / "labels.jsonl"


def read_labels(path: Optional[Path] = None) -> list[dict]:
    p = path or labels_path()
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _det_props(st: core.Store, rid: str, date: str, model: str, det_id: str) -> Optional[dict]:
    fc = st._optional(rid, date, model, "detections")
    for f in core._features(fc):
        if f["properties"].get("id") == det_id:
            return f["properties"]
    return None


def add_record(st: core.Store, body: dict, kind: str, app_version: str) -> dict:
    for k in ("id", "region", "date", "model", "lon", "lat"):
        if body.get(k) in (None, ""):
            raise core.BadRequest(f"нет поля {k!r}")
    label = body.get("label")
    if kind == "label" and label not in LABELS:
        raise core.BadRequest(f"label: одно из {', '.join(LABELS)}")
    try:
        lon, lat = float(body["lon"]), float(body["lat"])
    except (TypeError, ValueError):
        raise core.BadRequest("lon/lat: числа") from None
    rid, date, model = st.resolve(str(body["region"]), str(body["date"]), str(body["model"]))
    props = _det_props(st, rid, date, model, str(body["id"])) or {}
    entry = st.date_entry(rid, date)
    note = body.get("note")
    rec = {
        "kind": kind, "id": str(body["id"]), "region": rid, "date": date, "model": model,
        "lon": round(lon, 6), "lat": round(lat, 6), "label": label if kind == "label" else None,
        "note": (str(note)[:1000] if note else None),
        "provenance": {"user": "local", "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                       "app_version": app_version, "source_scene_id": entry.get("scene_id"), "model": model,
                       "max_prob": props.get("max_prob", body.get("max_prob")),
                       "detection_known": bool(props)},
    }
    p = labels_path()
    with _lock:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def latest_labels(recs: list[dict]) -> dict[tuple, dict]:
    """(region, date, model, id) -> last 'label' record (a later label overrides an earlier one)."""
    out = {}
    for r in recs:
        if r.get("kind", "label") == "label":
            out[(r.get("region"), r.get("date"), r.get("model"), r.get("id"))] = r
    return out


# ---------------------------------------------------------------- queue
def queue(st: core.Store, rid: str, model: Optional[str] = None, limit: int = 50,
          include_labeled: bool = False) -> dict:
    reg = st.region(rid)
    recs = read_labels()
    labeled = latest_labels(recs)
    flagged = {(r.get("region"), r.get("date"), r.get("model"), r.get("id")): r
               for r in recs if r.get("kind") == "flag_false"}
    items = []
    n_art_excl = 0
    for d in st.region_dates(rid):
        date = d["date"]
        models = d.get("models") or []
        for m in models:
            if model and m != model:
                continue
            thr = place.model_threshold(st, m)
            fc = st._optional(rid, date, m, "detections")
            has_bands = place.bands_path(rid, date) is not None
            for f in core._features(fc):
                p = f["properties"]
                key = (rid, date, m, p.get("id"))
                if key in labeled and not include_labeled:
                    continue
                reasons, codes, prio = [], [], 0.0
                art = core.artifact_of(p)
                if key in flagged:
                    reasons.append("отмечено «ложное» на карте")
                    codes.append("user_flag")
                    prio += 3
                if art:
                    # L38: artifacts (seam / wake / ship) are excluded from the index and zones and do not go to the
                    # queue - except a user flag (above) or a conflict: the second model confirms the object, so the
                    # artifact filter may have removed a real finding.
                    if p.get("confirmed") is True:
                        reasons.append(f"помечено как «{core.artifact_ru(art)}», но вторая модель тоже видит объект — "
                                       "проверьте, не исключена ли настоящая находка")
                        codes.append("artifact_conflict")
                        prio += 1.5
                    if not codes:
                        n_art_excl += 1
                        continue
                elif len(models) > 1 and p.get("confirmed") is False:
                    other = ", ".join(x for x in models if x != m)
                    reasons.append(f"расхождение моделей: {other} здесь ничего не видит")
                    codes.append("disagreement")
                    prio += 2
                mp = p.get("max_prob")
                if not art and mp is not None and abs(float(mp) - thr) <= NEAR_THR:
                    reasons.append(f"около порога: max P {float(mp):.2f} при пороге {thr:.2f}")
                    codes.append("near_threshold")
                    prio += 1 + (NEAR_THR - abs(float(mp) - thr)) / NEAR_THR
                if not codes:
                    continue
                lon, lat = place.det_lonlat(f)
                if lon is None:
                    continue
                item = {"id": p.get("id"), "region": rid, "date": date, "model": m, "lon": lon, "lat": lat,
                        "max_prob": mp, "mean_prob": p.get("mean_prob"), "area_m2": p.get("area_m2"),
                        "threshold": thr, "confirmed": p.get("confirmed"), "confirmed_by": p.get("confirmed_by"),
                        "reasons": codes, "reason": "; ".join(reasons), "priority": round(prio, 3),
                        "scene_id": d.get("scene_id"),
                        "crop_rgb": place.crop_url(rid, date, lon, lat, size_m=1000, model=m),
                        "crop_false_color": (place.crop_url(rid, date, lon, lat, size_m=1000, model=m, bands="false")
                                             if has_bands else None)}
                if art:
                    item["artifact"] = art
                    item["artifact_ru"] = core.artifact_ru(art)
                if key in labeled:
                    item["label"] = labeled[key].get("label")
                items.append(item)
    items.sort(key=lambda x: (-x["priority"], x["date"], x["id"] or ""))
    n = len(items)
    return {"region": rid, "region_name": reg.get("name"), "model": model, "n_total": n,
            "n_labeled": sum(1 for k in labeled if k[0] == rid), "labels": list(LABELS), "labels_ru": LABELS_RU,
            "n_artifacts_excluded": n_art_excl,
            "rules": {"near_threshold": f"|max_prob − порог| ≤ {NEAR_THR}",
                      "disagreement": "confirmed = false при наличии второй модели на эту дату",
                      "user_flag": "запись kind=flag_false (POST /api/review/flag)",
                      "artifact": "объекты с properties.artifact (шов детекторов / кильватер / судно) исключены из "
                                  "индекса и зон и в очередь не попадают; исключения: отметка «ложное» с карты "
                                  "(user_flag) или вторая модель подтверждает объект (artifact_conflict — фильтр "
                                  "артефактов мог убрать настоящую находку). Для артефактов причины «около порога» "
                                  "и «расхождение моделей» не применяются",
                      "artifact_conflict": "artifact задан и confirmed = true"},
            "items": items[:max(1, min(int(limit), 1000))]}


# ---------------------------------------------------------------- retrain jobs
class Jobs:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()

    def start(self, extra_args: Optional[list[str]] = None) -> dict:
        labels = [r for r in read_labels() if r.get("kind", "label") == "label"]
        if not labels:
            raise core.BadRequest(f"нет меток для дообучения: файл {labels_path()} пуст или отсутствует — "
                                  "сначала отметьте находки в очереди проверки (POST /api/review/label)")
        with self.lock:
            for j in self.jobs.values():
                if j["status"] == "running" and j["proc"].poll() is None:
                    raise core.BadRequest(f"дообучение уже идёт: job {j['id']}")
            ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            jid = f"{ts}_{uuid.uuid4().hex[:6]}"
            out = place.REPO / "weights_exp" / "review" / jid
            out.mkdir(parents=True, exist_ok=True)
            log = out / "log.txt"
            cmd = [sys.executable, str(place.REPO / "scripts" / "retrain_with_labels.py"),
                   "--labels", str(labels_path()), "--out", str(out)] + list(extra_args or [])
            env = dict(os.environ)
            env["CUDA_VISIBLE_DEVICES"] = ""
            env["PYTHONPATH"] = str(place.REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
            env["PYTHONIOENCODING"] = "utf-8"
            fh = open(log, "w", encoding="utf-8")
            proc = subprocess.Popen(cmd, cwd=str(place.REPO), stdout=fh, stderr=subprocess.STDOUT, env=env)
            job = {"id": jid, "status": "running", "started": dt.datetime.now().isoformat(timespec="seconds"),
                   "out_dir": str(out), "log_path": str(log), "n_labels": len(labels), "cmd": cmd,
                   "proc": proc, "fh": fh}
            self.jobs[jid] = job
            return self.public(job)

    def get(self, jid: str) -> dict:
        job = self.jobs.get(jid)
        if job is None:
            # a job from an earlier server run: read its folder
            out = place.REPO / "weights_exp" / "review" / jid
            if not jid.replace("_", "").isalnum() or not out.is_dir():
                raise core.NotFound(f"нет задачи {jid}")
            job = {"id": jid, "status": "unknown", "out_dir": str(out), "log_path": str(out / "log.txt"),
                   "proc": None, "fh": None}
        return self.public(job)

    def public(self, job: dict) -> dict:
        proc = job.get("proc")
        if proc is not None and job["status"] == "running":
            rc = proc.poll()
            if rc is not None:
                job["status"] = "done" if rc == 0 else "error"
                job["returncode"] = rc
                if job.get("fh"):
                    job["fh"].close()
        out = Path(job["out_dir"])
        res_p = out / "result.json"
        result = None
        if res_p.is_file():
            try:
                result = json.loads(res_p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                result = None
            if job["status"] == "unknown":
                job["status"] = "done" if result and not result.get("error") else "error"
        log = ""
        lp = Path(job["log_path"])
        if lp.is_file():
            try:
                log = lp.read_text(encoding="utf-8", errors="replace")[-4000:]
            except OSError:
                log = ""
        return {k: v for k, v in {"id": job["id"], "status": job["status"], "started": job.get("started"),
                                  "n_labels": job.get("n_labels"), "returncode": job.get("returncode"),
                                  "out_dir": job["out_dir"], "log_tail": log, "result": result}.items()}
