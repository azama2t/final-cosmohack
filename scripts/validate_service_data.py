"""Validate a service data root against docs/CONTRACTS.md.

Usage: python scripts/validate_service_data.py <root> [<root> ...] [--quiet]
Exit code 0 = no errors (warnings allowed), 1 = errors found.
Checks: required fields and types, files referenced by manifest exist, per-date/model files exist,
geojson <= 2 MB, PNG <= 2048 px, thumb.jpg (optional) <= 256 px / 30 KB, prob.tif uint8 single band (CRS required unless kind == fixture, nodata != 0),
share_permille consistency (null iff observed_frac < 0.5, = 1000*flagged/observed), timeseries sorted.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image

MAX_GEOJSON = 2 * 1024 * 1024
MAX_PX = 2048
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ID_RE = re.compile(r"^[a-z0-9_]+$")
NUM = (int, float)


class V:
    def __init__(self, root: Path):
        self.root, self.errors, self.warnings, self.checked = root, [], [], 0

    def err(self, where, msg):
        self.errors.append(f"{where}: {msg}")

    def warn(self, where, msg):
        self.warnings.append(f"{where}: {msg}")

    def need(self, obj, where, fields: dict):
        """fields: name -> type tuple (None in tuple = nullable)."""
        if not isinstance(obj, dict):
            self.err(where, f"expected object, got {type(obj).__name__}")
            return False
        ok = True
        for k, types in fields.items():
            if k not in obj:
                self.err(where, f"missing field '{k}'")
                ok = False
                continue
            v = obj[k]
            if v is None:
                if None not in types:
                    self.err(where, f"field '{k}' is null")
                    ok = False
                continue
            real = tuple(t for t in types if t is not None)
            if isinstance(v, bool) and bool not in real:
                self.err(where, f"field '{k}' is bool")
                ok = False
            elif not isinstance(v, real):
                self.err(where, f"field '{k}' has type {type(v).__name__}, expected {'/'.join(t.__name__ for t in real)}")
                ok = False
        return ok

    def load(self, rel: str):
        p = self.root / rel
        if not p.exists():
            self.err(rel, "file missing")
            return None
        self.checked += 1
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            self.err(rel, f"invalid JSON: {e}")
            return None

    def bounds(self, b, where):
        if not (isinstance(b, list) and len(b) == 4 and all(isinstance(x, NUM) for x in b)):
            self.err(where, f"bounds must be [w,s,e,n] numbers, got {b}")
            return
        w, s, e, n = b
        if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
            self.err(where, f"bounds out of range / not w<e, s<n: {b}")

    def png(self, rel, mode_rgba=False, size=None):
        p = self.root / rel
        if not p.exists():
            self.err(rel, "file missing")
            return None
        self.checked += 1
        try:
            im = Image.open(p)
            if max(im.size) > MAX_PX:
                self.err(rel, f"PNG {im.size} exceeds {MAX_PX} px")
            if mode_rgba and im.mode != "RGBA":
                self.err(rel, f"expected RGBA, got {im.mode}")
            if size and tuple(size) != im.size:
                self.warn(rel, f"size {im.size} != rgb.json {tuple(size)}")
            return im.size
        except Exception as e:  # noqa: BLE001
            self.err(rel, f"cannot open PNG: {e}")
            return None


def check_geojson_size(v: V, rel):
    p = v.root / rel
    if p.exists() and p.stat().st_size > MAX_GEOJSON:
        v.err(rel, f"{p.stat().st_size / 1e6:.2f} MB > 2 MB")


def coords_ok(coords) -> bool:
    if isinstance(coords, (list, tuple)) and coords and isinstance(coords[0], NUM):
        return -180 <= coords[0] <= 180 and -90 <= coords[1] <= 90
    return all(coords_ok(c) for c in coords)


def check_detections(v: V, rel, region, date, model):
    fc = v.load(rel)
    check_geojson_size(v, rel)
    if fc is None:
        return 0
    if fc.get("type") != "FeatureCollection" or not isinstance(fc.get("features"), list):
        v.err(rel, "not a FeatureCollection")
        return 0
    for i, f in enumerate(fc["features"]):
        w = f"{rel}#{i}"
        g = f.get("geometry") or {}
        if g.get("type") not in ("Polygon", "MultiPolygon"):
            v.err(w, f"geometry type {g.get('type')}")
        elif not coords_ok(g.get("coordinates", [])):
            v.err(w, "coordinates not lon/lat (WGS84)")
        pr = f.get("properties")
        if v.need(pr, w, {"id": (str,), "region": (str,), "date": (str,), "area_m2": NUM, "mean_prob": NUM,
                          "max_prob": NUM, "model": (str,)}):
            if not (0 <= pr["mean_prob"] <= 1 and 0 <= pr["max_prob"] <= 1):
                v.err(w, "prob out of 0..1")
            if pr["area_m2"] < 0:
                v.err(w, "negative area")
            if (pr["region"], pr["date"], pr["model"]) != (region, date, model):
                v.err(w, f"region/date/model mismatch {pr['region']}/{pr['date']}/{pr['model']}")
            if "confirmed" in pr:  # optional (L15): cross-model confirmation within 20 m
                cb = pr.get("confirmed_by")
                if not isinstance(pr["confirmed"], bool):
                    v.err(w, "confirmed must be bool")
                elif pr["confirmed"] and (not isinstance(cb, str) or cb == model):
                    v.err(w, f"confirmed_by must be the other model's id, got {cb!r}")
                elif not pr["confirmed"] and cb is not None:
                    v.err(w, "confirmed_by must be null when confirmed is false")
            if "artifact" in pr and pr["artifact"] not in (None, "seam", "wake", "ship"):  # optional (L37)
                v.err(w, f"artifact must be seam|wake|ship|null, got {pr['artifact']!r}")
        if i > 5000:
            break
    return len(fc["features"])


def check_h3(v: V, rel, date, model):
    fc = v.load(rel)
    check_geojson_size(v, rel)
    if fc is None:
        return
    if fc.get("type") != "FeatureCollection" or not isinstance(fc.get("features"), list):
        v.err(rel, "not a FeatureCollection")
        return
    if not fc["features"]:
        v.warn(rel, "no cells")
    for i, f in enumerate(fc["features"]):
        w = f"{rel}#{i}"
        if (f.get("geometry") or {}).get("type") != "Polygon":
            v.err(w, "geometry must be Polygon")
        pr = f.get("properties")
        if not v.need(pr, w, {"h3": (str,), "res": (int,), "date": (str,), "flagged_water_px": (int,),
                              "observed_water_px": (int,), "observed_frac": NUM, "share_permille": NUM + (None,),
                              "n_detections": (int,), "threshold": NUM, "model": (str,)}):
            continue
        if pr["res"] != 8:
            v.err(w, f"res {pr['res']} != 8")
        if pr["date"] != date or pr["model"] != model:
            v.err(w, "date/model mismatch")
        if not 0 <= pr["observed_frac"] <= 1:
            v.err(w, "observed_frac out of 0..1")
        if pr["flagged_water_px"] > pr["observed_water_px"]:
            v.err(w, "flagged > observed")
        sp = pr["share_permille"]
        if pr["observed_frac"] < 0.5 and sp is not None:
            v.err(w, "share_permille must be null when observed_frac < 0.5")
        if sp is not None:
            if pr["observed_water_px"] <= 0:
                v.err(w, "share defined with observed_water_px == 0")
            elif abs(sp - 1000 * pr["flagged_water_px"] / pr["observed_water_px"]) > 1e-2 + 1e-3 * abs(sp):
                v.err(w, f"share_permille {sp} != 1000*flagged/observed")
        elif pr["observed_frac"] >= 0.5 and pr["observed_water_px"] > 0:
            v.warn(w, "share_permille null although observed_frac >= 0.5")


def check_zones(v: V, rel, region, date, model):
    z = v.load(rel)
    if z is None:
        return
    if not v.need(z, rel, {"region": (str,), "date": (str,), "model": (str,), "threshold": NUM, "zones": (list,)}):
        return
    if (z["region"], z["date"], z["model"]) != (region, date, model):
        v.err(rel, "region/date/model mismatch")
    if len(z["zones"]) > 10:
        v.err(rel, f"{len(z['zones'])} zones > 10")
    for i, zz in enumerate(z["zones"]):
        w = f"{rel}#zone{i}"
        if v.need(zz, w, {"rank": (int,), "h3": (str,), "index": NUM + (None,), "area_m2": NUM, "reason": (str,),
                          "lon": NUM, "lat": NUM, "repeat_dates": (int,), "mean_prob": NUM + (None,)}):
            if zz["rank"] != i + 1:
                v.err(w, f"rank {zz['rank']} != {i + 1}")
            if "n_confirmed" in zz and (not isinstance(zz["n_confirmed"], int) or isinstance(zz["n_confirmed"], bool)
                                        or zz["n_confirmed"] < 0):
                v.err(w, "n_confirmed must be a non-negative int")  # optional (L15)
            st = zz.get("score_terms")  # optional (L37): base x agreement x date_penalty = score
            if st is not None:
                try:
                    vals = {k: (st[k]["value"] if isinstance(st[k], dict) else st[k])
                            for k in ("base", "agreement", "date_penalty", "score")}
                    prod = vals["base"] * vals["agreement"] * vals["date_penalty"]
                    if abs(prod - vals["score"]) > 0.01 + 1e-3 * abs(vals["score"]):
                        v.err(w, f"score_terms: base*agreement*date_penalty {prod:.3f} != score {vals['score']}")
                    if not 1 <= vals["agreement"] <= 2 or vals["date_penalty"] not in (0.5, 1, 1.0):
                        v.err(w, f"score_terms out of range {vals}")
                    if "score" in zz and abs(zz["score"] - vals["score"]) > 0.01:
                        v.err(w, "score_terms.score != score")
                except (KeyError, TypeError) as e:
                    v.err(w, f"score_terms malformed: {e}")
            if not (-180 <= zz["lon"] <= 180 and -90 <= zz["lat"] <= 90):
                v.err(w, "lon/lat out of range")


def check_prob_tif(v: V, rel, kind):
    p = v.root / rel
    if not p.exists():
        v.err(rel, "file missing")
        return
    v.checked += 1
    try:
        import rasterio
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with rasterio.open(p) as ds:
                if ds.count != 1 or ds.dtypes[0] != "uint8":
                    v.err(rel, f"expected 1 band uint8, got {ds.count} x {ds.dtypes[0]}")
                if ds.crs is None:
                    (v.warn if kind == "fixture" else v.err)(rel, "no CRS (must be scene UTM CRS)")
                if ds.nodata == 0:
                    v.err(rel, "nodata=0 is not allowed")
    except ImportError:
        v.warn(rel, "rasterio not installed, tif not checked")
    except Exception as e:  # noqa: BLE001
        v.err(rel, f"cannot open: {e}")


def check_drift(v: V, rel):
    d = v.load(rel)
    if d is None:
        return
    if v.need(d, rel, {"region": (str,), "date": (str,), "start_time": (str,), "hours": (list,),
                       "particles": (list,), "forcing": (dict,), "note": (str,)}):
        for i, pt in enumerate(d["particles"][:50]):
            if not v.need(pt, f"{rel}#p{i}", {"id": (int,), "path": (list,)}):
                break
            if pt["path"] and not (isinstance(pt["path"][0], list) and len(pt["path"][0]) == 3):
                v.err(f"{rel}#p{i}", "path items must be [lon, lat, t_hours]")
                break


def check_timeseries(v: V, region):
    rel = f"{region}/timeseries.json"
    ts = v.load(rel)
    if ts is None:
        return
    if not isinstance(ts, list):
        v.err(rel, "must be a list")
        return
    for i, r in enumerate(ts):
        v.need(r, f"{rel}#{i}", {"date": (str,), "model": (str,), "total_debris_area_m2": NUM,
                                 "mean_index": NUM + (None,), "n_detections": (int,), "cloud_frac": NUM + (None,)})
    dates = [r.get("date", "") for r in ts if isinstance(r, dict)]
    if dates != sorted(dates):
        v.err(rel, "not sorted by date")


def validate(root: Path) -> V:
    v = V(root)
    man = v.load("manifest.json")
    if man is None:
        return v
    if not v.need(man, "manifest", {"version": (int,), "generated": (str,), "kind": (str,), "index": (dict,),
                                    "models": (dict,), "regions": (list,), "sources": (list,)}):
        return v
    kind = man["kind"]
    if kind not in ("real", "demo", "fixture"):
        v.err("manifest", f"kind '{kind}' not in real|demo|fixture")
    v.need(man["index"], "manifest.index", {"name": (str,), "unit": (str,), "h3_res": (int,), "formula": (str,),
                                            "note": (str,)})
    for m, mm in man["models"].items():
        if v.need(mm, f"manifest.models.{m}", {"name": (str,), "threshold": NUM}) and not 0 <= mm["threshold"] <= 1:
            v.err(f"manifest.models.{m}", "threshold out of 0..1")
    for i, s in enumerate(man["sources"]):
        v.need(s, f"manifest.sources[{i}]", {"name": (str,), "license": (str,)})
    if not man["regions"]:
        v.err("manifest", "no regions")
    if "demo" in man:  # L43: optional explicit demo region {region, date, reason}
        dm = man["demo"]
        if v.need(dm, "manifest.demo", {"region": (str,), "date": (str,), "reason": (str,)}):
            reg = next((r for r in man["regions"] if isinstance(r, dict) and r.get("id") == dm["region"]), None)
            if reg is None:
                v.err("manifest.demo", f"region '{dm['region']}' not in manifest.regions")
            elif dm["date"] not in [d.get("date") for d in reg.get("dates", []) if isinstance(d, dict)]:
                v.err("manifest.demo", f"date {dm['date']} not among the dates of '{dm['region']}'")
    for reg in man["regions"]:
        rw = f"manifest.regions[{reg.get('id', '?') if isinstance(reg, dict) else '?'}]"
        if not v.need(reg, rw, {"id": (str,), "name": (str,), "center": (list,), "bounds": (list,), "zoom": NUM,
                                "summary": (dict,), "dates": (list,)}):
            continue
        rid = reg["id"]
        if not ID_RE.match(rid):
            v.err(rw, "id must be snake_case latin")
        v.bounds(reg["bounds"], rw)
        if not (len(reg["center"]) == 2 and all(isinstance(x, NUM) for x in reg["center"])):
            v.err(rw, "center must be [lon, lat]")
        v.need(reg["summary"], f"{rw}.summary", {"latest_date": (str,), "index_permille": NUM + (None,),
                                                 "n_detections": (int,), "total_debris_area_m2": NUM})
        for k in ("country", "tile"):
            if k not in reg:
                v.warn(rw, f"missing optional field '{k}'")
        check_timeseries(v, rid)
        if not reg["dates"]:
            v.err(rw, "no dates")
        for dm in reg["dates"]:
            dw = f"{rw}.dates[{dm.get('date', '?') if isinstance(dm, dict) else '?'}]"
            if not v.need(dm, dw, {"date": (str,), "scene_id": (str,), "cloud_frac": NUM + (None,), "bounds": (list,),
                                   "rgb": (str,), "models": (list,), "drift": (str, None)}):
                continue
            date = dm["date"]
            if not DATE_RE.match(date):
                v.err(dw, "date must be YYYY-MM-DD")
            v.bounds(dm["bounds"], dw)
            if "\\" in dm["rgb"] or (dm["drift"] and "\\" in dm["drift"]):
                v.err(dw, "paths must use '/'")
            rj = v.load(f"{rid}/{date}/rgb.json")
            size = None
            if rj is not None and v.need(rj, f"{rid}/{date}/rgb.json", {"bounds": (list,), "width": (int,),
                                                                         "height": (int,), "crs": (str,)}):
                v.bounds(rj["bounds"], f"{rid}/{date}/rgb.json")
                size = (rj["width"], rj["height"])
                for k in ("scene_id", "cloud_frac"):
                    if k not in rj:
                        v.warn(f"{rid}/{date}/rgb.json", f"missing field '{k}'")
            v.png(dm["rgb"], size=size)
            if dm["drift"]:
                check_drift(v, dm["drift"])
            if "quality" in dm:  # optional (CONTRACTS additions): {"glint_or_haze", "haze", "note"}
                v.need(dm["quality"], f"{dw}.quality", {"glint_or_haze": (bool,), "haze": (bool,), "note": (str,)})
            if dm.get("thumb"):  # optional (CONTRACTS additions 03:35): jpg, 256 px long side, <= 30 KB
                tp = v.root / dm["thumb"]
                if not tp.exists():
                    v.err(dw, f"thumb missing: {dm['thumb']}")
                else:
                    v.checked += 1
                    if tp.stat().st_size > 30_000:
                        v.err(dm["thumb"], f"{tp.stat().st_size} bytes > 30 KB")
                    if max(Image.open(tp).size) > 256:
                        v.err(dm["thumb"], "thumb larger than 256 px")
            nc = dm.get("n_confirmed")  # optional (L15): {model: confirmed detections}, only with >= 2 models
            if nc is not None:
                if not isinstance(nc, dict) or set(nc) != set(dm["models"]) or not all(
                        isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in nc.values()):
                    v.err(dw, f"n_confirmed must be {{model: int>=0}} for models {dm['models']}, got {nc!r}")
            for m in dm["models"]:
                if m not in man["models"]:
                    v.err(dw, f"model '{m}' not in manifest.models")
                base = f"{rid}/{date}/{m}"
                v.png(f"{base}/prob.png", mode_rgba=True, size=size)
                check_prob_tif(v, f"{base}/prob.tif", kind)
                check_detections(v, f"{base}/detections.geojson", rid, date, m)
                check_h3(v, f"{base}/h3.geojson", date, m)
                check_zones(v, f"{base}/zones.json", rid, date, m)
    return v


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    bad = 0
    for r in a.roots:
        v = validate(Path(r))
        print(f"== {r}: {v.checked} files checked, {len(v.errors)} errors, {len(v.warnings)} warnings")
        for e in v.errors[:200]:
            print("  ERROR", e)
        if not a.quiet:
            for w in v.warnings[:30]:
                print("  warn ", w)
            if len(v.warnings) > 30:
                print(f"  ... {len(v.warnings) - 30} more warnings")
        bad += bool(v.errors)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
