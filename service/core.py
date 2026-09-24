"""Data layer of the macroplastic service: data-root resolution, cached JSON reads, KPI, export.

All file formats: docs/CONTRACTS.md. No FastAPI imports here (easy to unit-test).
Index values are "share of observed water with debris signs" (permille), never plastic mass.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Optional

SERVICE_DIR = Path(__file__).resolve().parent
ENV_VAR = "MACROPLASTIC_DATA"
GEN_HINT = (r".venv\Scripts\python.exe scripts\make_fixtures.py --out service\demo_fixtures"
            "  (синтетика для проверки UI; реальные данные — пайплайн в service\\data)")
KPI_KEYS = ("total_debris_area_m2", "n_detections", "mean_index", "max_index",
            "cloud_frac", "observed_cells", "flagged_cells")
LAYERS = ("detections", "h3", "zones")
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class NotFound(LookupError):
    """Unknown region/date/model/file -> HTTP 404."""


class BadRequest(ValueError):
    """Malformed parameter -> HTTP 422."""


# ---------------------------------------------------------------- data root
def resolve_root(explicit: Optional[str | Path] = None) -> tuple[Optional[Path], str]:
    """Return (root, how). Order: explicit arg, $MACROPLASTIC_DATA, service/data, service/demo,
    service/demo_fixtures (first with manifest.json). None if nothing found."""
    if explicit:
        return Path(explicit).resolve(), "arg"
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).resolve(), "env"
    for name in ("data", "demo", "demo_fixtures"):
        p = SERVICE_DIR / name
        if (p / "manifest.json").is_file():
            return p.resolve(), name
    return None, "none"


def safe_path(root: Path, rel: str) -> Path:
    """Resolve rel inside root; raise NotFound if it escapes root or does not exist."""
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if not rel or "\x00" in rel or ":" in rel:
        raise NotFound("файл не найден")
    root_r = root.resolve()
    p = (root_r / rel).resolve()
    try:
        p.relative_to(root_r)
    except ValueError:
        raise NotFound("файл не найден") from None
    if not p.is_file():
        raise NotFound("файл не найден")
    return p


# ---------------------------------------------------------------- store
class Store:
    """Reads the data root lazily; JSON files cached by (path, mtime, size)."""

    def __init__(self, root: Optional[Path], how: str = "arg"):
        self.root = root
        self.how = how
        self._cache: dict[Path, tuple[tuple[float, int], Any]] = {}
        self._lock = threading.Lock()

    @classmethod
    def open(cls, explicit=None) -> "Store":
        return cls(*resolve_root(explicit))

    # ---- basic reads
    def has_data(self) -> bool:
        return self.root is not None and (self.root / "manifest.json").is_file()

    def read_json(self, rel: str) -> Any:
        if self.root is None:
            raise NotFound("нет данных")
        p = safe_path(self.root, rel)
        st = p.stat()
        key = (st.st_mtime, st.st_size)
        hit = self._cache.get(p)
        if hit is not None and hit[0] == key:
            return hit[1]
        with self._lock:
            obj = json.loads(p.read_text(encoding="utf-8"))
            self._cache[p] = (key, obj)
        return obj

    def manifest(self) -> dict:
        if not self.has_data():
            return {"version": 1, "kind": "none", "regions": [], "models": {}, "sources": [],
                    "hint": GEN_HINT}
        return self.read_json("manifest.json")

    def data_kind(self) -> str:
        if not self.has_data():
            return "none"
        kind = self.manifest().get("kind")
        if kind in ("real", "demo", "fixture"):
            return kind
        return {"data": "real", "demo": "demo", "demo_fixtures": "fixture"}.get(
            self.root.name if self.root else "", "real")

    def regions(self) -> list[dict]:
        return list(self.manifest().get("regions", []))

    def region(self, rid: str) -> dict:
        for r in self.regions():
            if r.get("id") == rid:
                return r
        raise NotFound(f"регион {rid!r} не найден")

    def region_dates(self, rid: str) -> list[dict]:
        return sorted(self.region(rid).get("dates", []), key=lambda d: d.get("date", ""))

    def date_entry(self, rid: str, date: Optional[str]) -> dict:
        dates = self.region_dates(rid)
        if not dates:
            raise NotFound(f"у региона {rid!r} нет дат")
        if not date:
            return dates[-1]
        for d in dates:
            if d.get("date") == date:
                return d
        raise NotFound(f"дата {date!r} для региона {rid!r} не найдена")

    def resolve(self, rid: str, date: Optional[str], model: Optional[str]) -> tuple[str, str, str]:
        """Validate (region, date, model) against the manifest; defaults: latest date, model mdd
        (or the first model available at that date)."""
        d = self.date_entry(rid, date)
        models = d.get("models") or []
        if not model:
            model = "mdd" if "mdd" in models or not models else models[0]
        if not _NAME_RE.match(model):
            raise BadRequest(f"model: недопустимое имя {model!r}")
        if models and model not in models:
            raise NotFound(f"модель {model!r} для {rid}/{d['date']} отсутствует (есть: {', '.join(models)})")
        return rid, d["date"], model

    def timeseries(self, rid: str, model: Optional[str] = None) -> list[dict]:
        self.region(rid)
        try:
            ts = self.read_json(f"{rid}/timeseries.json")
        except NotFound:
            ts = []
        if model:
            ts = [r for r in ts if r.get("model") == model]
        return sorted(ts, key=lambda r: (r.get("date", ""), r.get("model", "")))

    def layer(self, rid: str, date: str, model: str, layer: str) -> Any:
        if layer not in LAYERS:
            raise BadRequest(f"layer: допустимо {', '.join(LAYERS)}")
        fname = {"detections": "detections.geojson", "h3": "h3.geojson", "zones": "zones.json"}[layer]
        try:
            return self.read_json(f"{rid}/{date}/{model}/{fname}")
        except NotFound:
            raise NotFound(f"нет файла {rid}/{date}/{model}/{fname}") from None

    # ---- KPI
    def kpi(self, rid: str, date: Optional[str], model: Optional[str]) -> dict:
        rid, date, model = self.resolve(rid, date, model)
        det = _features(self._optional(rid, date, model, "detections"))
        cells = _features(self._optional(rid, date, model, "h3"))
        areas = [_num(f["properties"].get("area_m2")) for f in det]
        shares = [_num(f["properties"].get("share_permille")) for f in cells
                  if f["properties"].get("share_permille") is not None]
        cloud = None
        try:
            cloud = self.read_json(f"{rid}/{date}/rgb.json").get("cloud_frac")
        except NotFound:
            pass
        if cloud is None:
            cloud = self.date_entry(rid, date).get("cloud_frac")
        kpi = {
            "total_debris_area_m2": round(sum(areas), 1),
            "n_detections": len(det),
            "mean_index": round(sum(shares) / len(shares), 4) if shares else 0.0,
            "max_index": round(max(shares), 4) if shares else 0.0,
            "cloud_frac": cloud,
            "observed_cells": len(shares),
            "flagged_cells": sum(1 for s in shares if s > 0),
        }
        return {"region": rid, "date": date, "model": model, "kpi": kpi}

    def _optional(self, rid, date, model, layer):
        try:
            return self.layer(rid, date, model, layer)
        except NotFound:
            return None

    def compare(self, a: str, b: str, model: Optional[str]) -> dict:
        ka = self.kpi(*parse_ref(a), model)
        kb = self.kpi(*parse_ref(b), model)
        return {"a": ka, "b": kb, "diff": diff_kpi(ka["kpi"], kb["kpi"])}


# ---------------------------------------------------------------- helpers
def parse_ref(ref: str) -> tuple[str, Optional[str]]:
    """'honduras' or 'honduras:2025-10-19' -> (region, date|None)."""
    if not ref or not ref.strip():
        raise BadRequest("пустая ссылка на регион (ожидается region[:YYYY-MM-DD])")
    rid, _, date = ref.strip().partition(":")
    if date and not DATE_RE.match(date):
        raise BadRequest(f"дата {date!r}: ожидается YYYY-MM-DD")
    return rid, (date or None)


def diff_kpi(a: dict, b: dict) -> dict:
    """Per key: delta = b - a, ratio = b / a (None when a == 0 or a value is missing)."""
    out = {}
    for k in KPI_KEYS:
        va, vb = a.get(k), b.get(k)
        if va is None or vb is None:
            out[k] = {"delta": None, "ratio": None}
            continue
        delta = vb - va
        out[k] = {"delta": round(delta, 4) if isinstance(delta, float) else delta,
                  "ratio": round(vb / va, 4) if va else None}
    return out


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _features(fc) -> list[dict]:
    if not fc:
        return []
    feats = fc.get("features") or []
    for f in feats:
        f.setdefault("properties", {})
        if f["properties"] is None:
            f["properties"] = {}
    return feats


def centroid(geom: Optional[dict]) -> tuple[Optional[float], Optional[float]]:
    if not geom:
        return None, None
    try:
        from shapely.geometry import shape

        c = shape(geom).centroid
        if not c.is_empty:
            return round(c.x, 6), round(c.y, 6)
    except Exception:
        pass
    pts = []

    def walk(x):
        if isinstance(x, (list, tuple)) and len(x) >= 2 and all(isinstance(v, (int, float)) for v in x[:2]):
            pts.append(x)
        elif isinstance(x, (list, tuple)):
            for y in x:
                walk(y)
    walk(geom.get("coordinates"))
    if not pts:
        return None, None
    return round(sum(p[0] for p in pts) / len(pts), 6), round(sum(p[1] for p in pts) / len(pts), 6)


def zones_geojson(z: dict) -> dict:
    feats = []
    for zone in z.get("zones", []):
        props = {k: v for k, v in zone.items() if k not in ("lon", "lat")}
        props.update({"region": z.get("region"), "date": z.get("date"), "model": z.get("model")})
        feats.append({"type": "Feature", "properties": props,
                      "geometry": {"type": "Point", "coordinates": [zone.get("lon"), zone.get("lat")]}})
    return {"type": "FeatureCollection", "features": feats}


def to_csv(layer: str, obj: Any) -> str:
    """detections/h3: properties + centroid lon/lat; zones: zone rows (lon/lat already present)."""
    if layer == "zones":
        rows = []
        for zone in obj.get("zones", []):
            r = {"region": obj.get("region"), "date": obj.get("date"), "model": obj.get("model")}
            r.update(zone)
            rows.append(r)
    else:
        rows = []
        for f in _features(obj):
            r = dict(f["properties"])
            r["lon"], r["lat"] = centroid(f.get("geometry"))
            rows.append(r)
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    if not cols:
        cols = ["lon", "lat"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})
    return buf.getvalue()
