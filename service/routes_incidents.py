"""Incidents with statuses and an event feed (lane L46, INBOX 2.5; reference: NOAA ERMA / EMSA CleanSeaNet).

Every detection and every priority zone of the data root is an incident:
  detection id = detections.geojson properties.id;  zone id = "zone:<region>:<date>:<model>:<h3>".
Statuses: detected -> under_review -> confirmed | false_alarm -> resolved; artifacts (properties.artifact:
seam / wake / ship) start as "excluded" (system filter, not a human verdict; can be reopened).

State = initial state built from the data root (one "build" event per incident, ts = scene sensing time)
      + review labels ($MACROPLASTIC_LABELS/labels.jsonl, written by service/review.py; read here, not copied)
      + status journal incidents.jsonl next to labels.jsonl (append-only, POST /api/incidents/{id}/status).
Label -> event: debris -> confirmed, foam|algae|ship_wake|cloud|other -> false_alarm (actor operator, source review);
flag_false ("ложное" on the map) -> under_review if the incident is still "detected".
Contract: docs/CONTRACTS.md, section "Инциденты и лента событий".
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
import threading
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from . import core, review

router = APIRouter()

STATUSES = ("detected", "under_review", "confirmed", "false_alarm", "resolved", "excluded")
STATUS_RU = {"detected": "обнаружено", "under_review": "на проверке", "confirmed": "подтверждено",
             "false_alarm": "ложная тревога", "resolved": "закрыто", "excluded": "исключено (артефакт)"}
# allowed manual transitions (POST /api/incidents/{id}/status); anything else -> 409
TRANSITIONS = {
    "detected": ("under_review", "confirmed", "false_alarm"),
    "under_review": ("confirmed", "false_alarm", "detected"),
    "confirmed": ("resolved", "under_review"),
    "false_alarm": ("resolved", "under_review"),
    "resolved": ("under_review",),
    "excluded": ("under_review",),
}
LABEL_TO_STATUS = {"debris": "confirmed", "foam": "false_alarm", "algae": "false_alarm",
                   "ship_wake": "false_alarm", "cloud": "false_alarm", "other": "false_alarm"}
LABEL_RU_FEED = {"foam": "пена", "algae": "водоросли", "ship_wake": "кильватер/судно", "cloud": "облако",
                 "other": "другое"}
ARTIFACT_RU_FEED = {"seam": "шов детекторов", "wake": "кильватер", "ship": "судно"}
_SCENE_TS = re.compile(r"_(\d{8})T(\d{6})_")
_lock = threading.Lock()
_write_lock = threading.Lock()
_cache: dict[str, Any] = {}


# ---------------------------------------------------------------- paths / store
def incidents_path() -> Path:
    """incidents.jsonl in the same folder as labels.jsonl ($MACROPLASTIC_LABELS dir or *.jsonl path)."""
    return review.labels_path().parent / "incidents.jsonl"


def _store_of(request: Request) -> core.Store:
    """The Store of the app that serves the request (honours create_app(data_root)).

    service/app.py keeps the store in a closure (`store()`), not on app.state; routers get it from the closure of
    the /health endpoint. Fallback: core.Store.open(None) ($MACROPLASTIC_DATA / service/data / service/demo)."""
    app = request.app
    getter = getattr(app.state, "store", None)
    if getter is None:
        for r in getattr(app, "routes", []):
            if getattr(r, "path", None) == "/health":
                for cell in (getattr(r.endpoint, "__closure__", None) or ()):
                    fn = cell.cell_contents
                    if callable(fn) and getattr(fn, "__name__", "") == "store":
                        getter = fn
                        break
                break
        if getter is None:
            getter = lambda: core.Store.open(None)  # noqa: E731
        app.state.store = getter
    return getter() if callable(getter) else getter


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _norm_ts(ts: Optional[str]) -> str:
    """ISO in UTC '+00:00' form so that string order = time order."""
    if not ts:
        return ""
    try:
        t = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return str(ts)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def scene_ts(date: str, scene_id: Optional[str]) -> str:
    """Sensing time from a Sentinel-2 product id (…_YYYYMMDDTHHMMSS_…), else the date at 00:00 UTC."""
    m = _SCENE_TS.search(scene_id or "")
    if m and m.group(1) == date.replace("-", ""):
        d, t = m.group(1), m.group(2)
        return f"{d[:4]}-{d[4:6]}-{d[6:]}T{t[:2]}:{t[2:4]}:{t[4:]}+00:00"
    return f"{date}T00:00:00+00:00"


def short_name(reg: dict) -> str:
    name = str(reg.get("name") or reg.get("id") or "")
    return name.split(" (")[0].strip() or name


def fmt_area(a) -> str:
    try:
        v = float(a)
    except (TypeError, ValueError):
        return "? м²"
    v = round(v, -2) if v >= 1000 else round(v)
    return f"{int(v):,}".replace(",", " ") + " м²"


def _read_jsonl(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _sig(p: Path) -> tuple:
    try:
        s = p.stat()
        return (str(p), s.st_mtime_ns, s.st_size)
    except OSError:
        return (str(p), None, None)


# ---------------------------------------------------------------- initial state (from the data root)
def _zone_res(zones: list[dict]) -> Optional[int]:
    for z in zones:
        try:
            import h3

            return h3.get_resolution(z["h3"])
        except Exception:
            continue
    return None


def build_initial(st: core.Store) -> dict[str, dict]:
    """id -> incident with one 'build' event. Order of insertion: region, date, model, zones, detections."""
    out: dict[str, dict] = {}
    if not st.has_data():
        return out
    for reg in st.regions():
        rid = reg.get("id")
        rname = short_name(reg)
        for d in reg.get("dates") or []:
            date = d.get("date")
            if not date:
                continue
            ts = scene_ts(date, d.get("scene_id"))
            models = d.get("models") or []
            for m in models:
                zfile = st._optional(rid, date, m, "zones") or {}
                zones = [z for z in (zfile.get("zones") or []) if z.get("h3")]
                rank_of = {z["h3"]: z.get("rank") for z in zones}
                res = _zone_res(zones)
                for z in zones:
                    iid = f"zone:{rid}:{date}:{m}:{z['h3']}"
                    out[iid] = _incident(
                        iid, "zone", rid, rname, date, m, d.get("scene_id"), z.get("lon"), z.get("lat"),
                        z.get("area_m2"), z.get("rank"), bool((z.get("n_confirmed") or 0) > 0), None,
                        {"h3": z["h3"], "n_detections": z.get("n_detections"), "score": z.get("score"),
                         "mean_prob": z.get("mean_prob")}, ts)
                for f in core._features(st._optional(rid, date, m, "detections")):
                    p = f["properties"]
                    iid = p.get("id")
                    if not iid:
                        continue
                    lon, lat = p.get("lon"), p.get("lat")
                    if lon is None or lat is None:
                        lon, lat = core.centroid(f.get("geometry"))
                    art = core.artifact_of(p)
                    prio, zone_id = None, None
                    if not art and res is not None and lon is not None:
                        try:
                            import h3

                            cell = h3.latlng_to_cell(float(lat), float(lon), res)
                        except Exception:
                            cell = None
                        if cell in rank_of:
                            prio, zone_id = rank_of[cell], f"zone:{rid}:{date}:{m}:{cell}"
                    extra = {"max_prob": p.get("max_prob"), "mean_prob": p.get("mean_prob"),
                             "confirmed_by": p.get("confirmed_by"), "zone_id": zone_id}
                    out[str(iid)] = _incident(str(iid), "detection", rid, rname, date, m, d.get("scene_id"),
                                              lon, lat, p.get("area_m2"), prio, p.get("confirmed") is True, art,
                                              extra, ts)
    return out


def _incident(iid, kind, rid, rname, date, model, scene_id, lon, lat, area, prio, conf, art, extra, ts) -> dict:
    status = "excluded" if art else "detected"
    note = (f"артефакт: {ARTIFACT_RU_FEED.get(art, core.artifact_ru(art))} — исключён из индекса и зон"
            if art else ("зона приоритета обследования" if kind == "zone" else "новое пятно"))
    inc = {"id": iid, "kind": kind, "region": rid, "region_name": rname, "date": date, "model": model,
           "scene_id": scene_id,
           "lon": round(float(lon), 6) if lon is not None else None,
           "lat": round(float(lat), 6) if lat is not None else None,
           "area_m2": area, "priority": prio, "confirmed_by_other_model": bool(conf), "artifact": art,
           "status": status, "verdict": None, "label": None, "updated": ts,
           "history": [{"ts": ts, "actor": "system", "from": None, "to": status, "note": note, "source": "build"}]}
    inc.update({k: v for k, v in (extra or {}).items()})
    return inc


# ---------------------------------------------------------------- events
def journal_events() -> list[dict]:
    """Operator events from labels.jsonl + incidents.jsonl, normalised and sorted by ts (stable)."""
    evs = []
    for i, r in enumerate(review.read_labels()):
        kind = r.get("kind", "label")
        prov = r.get("provenance") or {}
        ts = _norm_ts(prov.get("ts") or r.get("ts"))
        if kind == "label" and r.get("label") in LABEL_TO_STATUS:
            evs.append({"id": str(r.get("id")), "ts": ts, "to": LABEL_TO_STATUS[r["label"]], "label": r["label"],
                        "note": r.get("note"), "actor": "operator", "source": "review", "type": "label", "seq": (0, i)})
        elif kind == "flag_false":
            evs.append({"id": str(r.get("id")), "ts": ts, "to": "under_review", "label": None, "note": r.get("note"),
                        "actor": "operator", "source": "review", "type": "flag", "seq": (0, i)})
    for i, r in enumerate(_read_jsonl(incidents_path())):
        if r.get("kind", "status") != "status" or r.get("to") not in STATUSES:
            continue
        evs.append({"id": str(r.get("id")), "ts": _norm_ts(r.get("ts")), "to": r["to"], "label": None,
                    "note": r.get("note"), "actor": r.get("actor") or "operator", "source": r.get("source") or "api",
                    "type": "status", "seq": (1, i)})
    evs.sort(key=lambda e: (e["ts"], e["seq"]))
    return evs


def apply_event(inc: dict, ev: dict) -> bool:
    """Apply one journal event to an incident (in place). Returns False for a no-op (ignored flag)."""
    cur = inc["status"]
    to = ev["to"]
    if ev["type"] == "flag":
        if cur != "detected":
            return False
        note = "отмечено «ложное» на карте" + (f": {ev['note']}" if ev.get("note") else "")
    elif ev["type"] == "label":
        lab = ev["label"]
        note = f"метка оператора: {review.LABELS_RU.get(lab, lab)}" + (f" — {ev['note']}" if ev.get("note") else "")
        inc["label"] = lab
        inc["verdict"] = to
    else:
        note = ev.get("note")
        if to in ("confirmed", "false_alarm") and ev.get("actor") == "operator":
            inc["verdict"] = to
    h = {"ts": ev["ts"], "actor": ev["actor"], "from": cur, "to": to, "note": note, "source": ev["source"]}
    if ev.get("label"):
        h["label"] = ev["label"]
    inc["history"].append(h)
    inc["status"] = to
    inc["updated"] = ev["ts"]
    return True


def state(st: core.Store) -> dict:
    """{'incidents': id -> incident, 'orphans': n events without a known incident}. Cached by file signatures."""
    man = st.root / "manifest.json" if st.root else Path("-")
    base_key = (str(st.root), _sig(man))
    j_key = (_sig(review.labels_path()), _sig(incidents_path()))
    with _lock:
        if _cache.get("base_key") != base_key:
            _cache["base"] = build_initial(st)
            _cache["base_key"] = base_key
            _cache.pop("full_key", None)
        if _cache.get("full_key") == (base_key, j_key):
            return _cache["full"]
        incs = copy.deepcopy(_cache["base"])
        orphans = 0
        for ev in journal_events():
            inc = incs.get(ev["id"])
            if inc is None:
                orphans += 1
                continue
            apply_event(inc, ev)
        full = {"incidents": incs, "orphans": orphans}
        _cache["full"] = full
        _cache["full_key"] = (base_key, j_key)
        return full


def invalidate() -> None:
    with _lock:
        _cache.pop("full_key", None)


# ---------------------------------------------------------------- views
def public(inc: dict, with_history: bool = False) -> dict:
    out = {k: v for k, v in inc.items() if k != "history"}
    out["status_ru"] = STATUS_RU.get(inc["status"], inc["status"])
    if inc.get("artifact"):
        out["artifact_ru"] = ARTIFACT_RU_FEED.get(inc["artifact"], core.artifact_ru(inc["artifact"]))
    out["allowed"] = list(TRANSITIONS.get(inc["status"], ()))
    out["n_events"] = len(inc["history"])
    if with_history:
        out["history"] = list(inc["history"])
    return out


def _tail(inc: dict) -> str:
    parts = [inc.get("region_name") or inc["region"], fmt_area(inc.get("area_m2"))]
    if inc.get("priority") is not None:
        parts.append(f"приоритет {inc['priority']}")
    return " · ".join(parts)


def feed_text(inc: dict, h: dict) -> tuple[str, str]:
    """(kind, Russian text) of one history entry."""
    to, src = h["to"], h["source"]
    tail = _tail(inc)
    if src == "build":
        if to == "excluded":
            a = ARTIFACT_RU_FEED.get(inc.get("artifact"), core.artifact_ru(inc.get("artifact")))
            return "excluded", f"исключено как {a} · {tail}"
        if inc["kind"] == "zone":
            return "zone", f"зона приоритета {inc.get('priority')} · {inc.get('region_name')} · " \
                           f"{fmt_area(inc.get('area_m2'))}"
        return "new", f"новое пятно · {tail}"
    who = "оператором" if h.get("actor") == "operator" else "системой"
    if to == "confirmed":
        return "confirmed", f"подтверждено {who} · {tail}"
    if to == "false_alarm":
        lab = h.get("label")
        if lab in LABEL_RU_FEED:
            return "false_alarm", f"исключено как {LABEL_RU_FEED[lab]} · {tail}"
        return "false_alarm", f"ложная тревога ({who}) · {tail}"
    if to == "under_review":
        if h.get("from") in ("resolved", "excluded", "confirmed", "false_alarm"):
            return "reopened", f"возвращено на проверку · {tail}"
        if src == "review":
            return "under_review", f"отмечено «ложное» на карте, на проверке · {tail}"
        return "under_review", f"взято на проверку · {tail}"
    if to == "resolved":
        return "resolved", f"закрыто · {tail}"
    if to == "detected":
        return "detected", f"снято с проверки · {tail}"
    return to, f"{STATUS_RU.get(to, to)} · {tail}"


def _prio_key(v) -> float:
    return float(v) if isinstance(v, (int, float)) else 1e9


def _filtered(incs: dict, region, status, model, date, kind) -> list[dict]:
    out = []
    for inc in incs.values():
        if region and inc["region"] != region:
            continue
        if status and inc["status"] not in status:
            continue
        if model and inc["model"] != model:
            continue
        if date and inc["date"] != date:
            continue
        if kind and inc["kind"] != kind:
            continue
        out.append(inc)
    return out


def _statuses(status: Optional[str]) -> Optional[set]:
    if not status:
        return None
    s = {x.strip() for x in status.split(",") if x.strip()}
    bad = s - set(STATUSES)
    if bad:
        raise core.BadRequest(f"status: одно из {', '.join(STATUSES)} (можно через запятую)")
    return s


def summary(incs: dict, region: Optional[str] = None, kind: Optional[str] = None) -> dict:
    items = _filtered(incs, region, None, None, None, kind)
    by_status = {s: 0 for s in STATUSES}
    by_kind: dict[str, int] = {}
    for inc in items:
        by_status[inc["status"]] = by_status.get(inc["status"], 0) + 1
        by_kind[inc["kind"]] = by_kind.get(inc["kind"], 0) + 1
    reviewed = [i for i in items if i.get("verdict") in ("confirmed", "false_alarm")]
    n_conf = sum(1 for i in reviewed if i["verdict"] == "confirmed")
    share = round(n_conf / len(reviewed), 4) if reviewed else None
    text = (f"подтверждено {round(100 * share)} % проверенных ({n_conf} из {len(reviewed)})"
            if reviewed else "проверенных пока нет")
    return {"region": region, "kind": kind, "total": len(items), "by_status": by_status, "by_kind": by_kind,
            "reviewed": len(reviewed), "confirmed_reviewed": n_conf, "confirmed_share_of_reviewed": share,
            "confirmed_share_text": text,
            "rules": {"reviewed": "есть вердикт оператора (последний): метка «Проверки» или статус "
                                  "confirmed/false_alarm, поставленный оператором",
                      "confirmed_share_of_reviewed": "доля вердиктов «подтверждено» среди проверенных; "
                                                     "исключённые системой артефакты не считаются проверенными"}}


# ---------------------------------------------------------------- API
@router.get("/api/incidents/summary", tags=["incidents"], summary="Сводка инцидентов по статусам")
def api_summary(request: Request, region: Optional[str] = None,
                kind: Optional[str] = Query(None, pattern="^(detection|zone)$")):
    st = _store_of(request)
    return summary(state(st)["incidents"], region, kind)


@router.get("/api/incidents", tags=["incidents"], summary="Инциденты (находки и зоны) со статусами")
def api_incidents(request: Request, region: Optional[str] = None, status: Optional[str] = None,
                  model: Optional[str] = None, date: Optional[str] = None,
                  kind: Optional[str] = Query(None, pattern="^(detection|zone)$"),
                  limit: int = Query(200, ge=1, le=10000)):
    st = _store_of(request)
    s = state(st)
    items = _filtered(s["incidents"], region, _statuses(status), model, date, kind)
    # latest update first; within the same time: zone rank (1 first), then larger area
    items.sort(key=lambda i: (_prio_key(i.get("priority")), -core._num(i.get("area_m2")), i["id"]))
    items.sort(key=lambda i: i["updated"] or "", reverse=True)
    return {"region": region, "status": status, "n_total": len(items), "statuses": list(STATUSES),
            "status_ru": STATUS_RU, "transitions": {k: list(v) for k, v in TRANSITIONS.items()},
            "items": [public(i) for i in items[:limit]]}


@router.get("/api/incidents/{iid}", tags=["incidents"], summary="Инцидент с историей статусов")
def api_incident(request: Request, iid: str):
    inc = state(_store_of(request))["incidents"].get(iid)
    if inc is None:
        raise core.NotFound(f"нет инцидента {iid}")
    return public(inc, with_history=True)


@router.post("/api/incidents/{iid}/status", tags=["incidents"], summary="Сменить статус инцидента")
async def api_set_status(request: Request, iid: str):
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        raise core.BadRequest("ожидается JSON-объект {to, note?}")
    to = body.get("to")
    if to not in STATUSES:
        raise core.BadRequest(f"to: одно из {', '.join(STATUSES)}")
    note = body.get("note")
    note = str(note)[:1000] if note else None
    st = _store_of(request)
    p = incidents_path()
    with _write_lock:  # validate + append atomically (state() takes _lock itself)
        inc = state(st)["incidents"].get(iid)
        if inc is None:
            raise core.NotFound(f"нет инцидента {iid}")
        cur = inc["status"]
        allowed = TRANSITIONS.get(cur, ())
        if to not in allowed:
            return JSONResponse({"detail": f"переход {cur} → {to} недопустим; из «{STATUS_RU.get(cur, cur)}» "
                                           f"можно: {', '.join(allowed) or '—'}", "from": cur, "to": to,
                                 "allowed": list(allowed)}, status_code=409)
        rec = {"kind": "status", "id": iid, "region": inc["region"], "date": inc["date"], "model": inc["model"],
               "from": cur, "to": to, "note": note, "actor": "operator", "source": "api", "ts": _now(),
               "user": "local"}
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        invalidate()
    inc = state(st)["incidents"][iid]
    return public(inc, with_history=True)


@router.get("/api/feed", tags=["incidents"], summary="Лента событий (новые пятна, проверка, исключения)")
def api_feed(request: Request, limit: int = Query(50, ge=1, le=1000), region: Optional[str] = None,
             kind: Optional[str] = None, since: Optional[str] = None):
    s = state(_store_of(request))
    kinds = {k.strip() for k in kind.split(",")} if kind else None
    since_n = _norm_ts(since) if since else None
    evs = []
    for inc in s["incidents"].values():
        if region and inc["region"] != region:
            continue
        for h in inc["history"]:
            k, text = feed_text(inc, h)
            if kinds and k not in kinds:
                continue
            if since_n and h["ts"] <= since_n:
                continue
            evs.append({"ts": h["ts"], "kind": k, "text": text, "region": inc["region"],
                        "region_name": inc.get("region_name"), "incident_id": inc["id"],
                        "incident_kind": inc["kind"], "date": inc["date"], "model": inc["model"],
                        "lon": inc.get("lon"), "lat": inc.get("lat"), "priority": inc.get("priority"),
                        "area_m2": inc.get("area_m2"), "status": inc["status"], "actor": h["actor"],
                        "source": h["source"]})
    evs.sort(key=lambda e: (_prio_key(e["priority"]), e["incident_id"]))
    evs.sort(key=lambda e: e["ts"], reverse=True)
    return {"n_total": len(evs), "limit": limit, "items": evs[:limit]}
