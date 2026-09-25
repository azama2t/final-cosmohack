"""L19 API: zone / crop / place / place_report.pdf / calendar / review queue + labels + retrain (0 labels).

Runs on a temporary copy of service/demo (small real data root). Labels go to a temp dir via $MACROPLASTIC_LABELS.
    .venv\\Scripts\\python.exe -m pytest -q tests\\test_api_review.py
"""
from __future__ import annotations

import io
import json
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEMO = ROOT / "service" / "demo"
pytestmark = pytest.mark.skipif(not (DEMO / "manifest.json").is_file(), reason="service/demo missing")


@pytest.fixture(scope="module")
def data_root(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("demo") / "demo"
    shutil.copytree(DEMO, out, ignore=shutil.ignore_patterns("drift.json"))
    return out


@pytest.fixture(scope="module")
def labels_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("labels")


@pytest.fixture(scope="module")
def client(data_root, labels_dir):
    mp = pytest.MonkeyPatch()
    mp.setenv("MACROPLASTIC_LABELS", str(labels_dir))
    from service.app import create_app

    yield TestClient(create_app(data_root))
    mp.undo()


def _manifest(root: Path) -> dict:
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))


def _first_zone(root: Path) -> tuple[str, str, str, dict]:
    m = _manifest(root)
    for r in m["regions"]:
        for d in r["dates"]:
            for model in d.get("models") or []:
                p = root / r["id"] / d["date"] / model / "zones.json"
                if p.is_file():
                    z = json.loads(p.read_text(encoding="utf-8"))
                    if z.get("zones"):
                        return r["id"], d["date"], model, z["zones"][0]
    pytest.skip("no zones in demo")


def _png_ok(content: bytes):
    from PIL import Image

    im = Image.open(io.BytesIO(content))
    assert im.format == "PNG" and min(im.size) >= 128


# ---------------------------------------------------------------- zone
def test_zone_why(client, data_root):
    rid, date, model, z = _first_zone(data_root)
    r = client.get("/api/zone", params={"region": rid, "date": date, "model": model, "h3": z["h3"]})
    assert r.status_code == 200, r.text
    j = r.json()
    for k in ("rank", "h3", "lon", "lat", "index", "area_m2", "n_detections", "mean_prob", "max_prob",
              "repeat_dates", "observed_frac", "cloud_frac", "date", "why", "crop"):
        assert k in j, k
    assert j["rank"] == z["rank"] == 1 and j["h3"] == z["h3"]
    why = j["why"]
    assert "flagged_water_px" in why["formula"] and isinstance(why["text"], str) and why["text"]
    names = [t["name"] for t in why["terms"]]
    # L38b: with zones.json score_terms (current build) terms[] carry every multiplier of the final score
    base_names = ["flagged_water_px", "mean_prob", "repeat_dates"]
    assert names == (base_names + ["agreement", "date_penalty"] if z.get("score_terms") else base_names)
    for t in why["terms"]:
        assert set(t) >= {"name", "value", "weight", "contribution"}
    prod = 1.0
    for t in why["terms"]:
        prod *= t["contribution"]
    assert abs(prod - why["score"]) < 0.01 * max(1.0, why["score"])
    if "score" in z:
        assert abs(why["score"] - z["score"]) < 0.01
    if z.get("score_terms"):
        assert "agreement" in why["formula"] and "date_penalty" in why["formula"]
        assert "согласия" in why["formula_text"] and "дымке/блике" in why["formula_text"]
    assert "не масса" in why["text"] or "не измеренная" in why["text"]
    assert j["crop"].startswith("/api/crop?")


def test_zone_errors(client, data_root):
    rid, date, model, _ = _first_zone(data_root)
    assert client.get("/api/zone", params={"region": rid, "h3": "zzz"}).status_code == 422
    assert client.get("/api/zone", params={"region": "nope", "h3": "88452061b5fffff"}).status_code == 404


# ---------------------------------------------------------------- crop
def test_crop_png(client, data_root):
    rid, date, model, z = _first_zone(data_root)
    r = client.get("/api/zone", params={"region": rid, "date": date, "model": model, "h3": z["h3"]})
    r = client.get(r.json()["crop"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    _png_ok(r.content)
    # overview-layer source (rgb.png of the data root) always works
    r = client.get("/api/crop", params={"region": rid, "date": date, "lon": z["lon"], "lat": z["lat"],
                                        "size_m": 1500, "highlight": 1, "src": "png"})
    assert r.status_code == 200
    _png_ok(r.content)


def test_crop_false_color_or_404(client, data_root, monkeypatch):
    rid, date, model, z = _first_zone(data_root)
    monkeypatch.setenv("MACROPLASTIC_LIVE", str(data_root / "__no_live__"))
    r = client.get("/api/crop", params={"region": rid, "date": date, "lon": z["lon"] + 1e-6, "lat": z["lat"],
                                        "bands": "false"})
    assert r.status_code == 404 and "RGB" in r.json()["detail"]
    r = client.get("/api/crop", params={"region": rid, "date": date, "lon": z["lon"] + 2e-6, "lat": z["lat"]})
    assert r.status_code == 200
    _png_ok(r.content)


# ---------------------------------------------------------------- place + pdf
def test_place(client, data_root):
    rid, date, model, z = _first_zone(data_root)
    r = client.get("/api/place", params={"region": rid, "h3": z["h3"], "model": model})
    assert r.status_code == 200, r.text
    j = r.json()
    dates = [d["date"] for d in _manifest(data_root)["regions"][[x["id"] for x in _manifest(data_root)["regions"]]
                                                                  .index(rid)]["dates"]]
    assert [h["date"] for h in j["history"]] == sorted(dates)
    assert {h["status"] for h in j["history"]} <= {"found", "clean", "no_observation", "no_image", "unreliable"}
    row = next(h for h in j["history"] if h["date"] == date)
    # the zone's own date: found, or «ненадёжно» by the calendar rule but with findings in the cell
    assert row["status"] == "found" or (row["status"] == "unreliable" and row["has_findings"])
    assert set(j["status_rules"]) >= {"found", "clean", "unreliable", "no_image", "no_observation"}
    assert j["sources"] and j["limitations"] and j["formula"]
    assert len(j["boundary"]) == 6


def test_place_pdf(client, data_root):
    import time

    rid, date, model, z = _first_zone(data_root)
    t = time.time()
    r = client.get("/api/place_report.pdf", params={"region": rid, "h3": z["h3"], "model": model, "date": date})
    dt = time.time() - t
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-" and r.content.rstrip().endswith(b"%%EOF")
    assert r.content.count(b"/Type /Page\n") + r.content.count(b"/Type /Page ") + \
        r.content.count(b"/Type/Page") >= 1
    assert dt < 15  # target <= 5 s on a warm process; generous for CI


# ---------------------------------------------------------------- calendar
def test_calendar(client, data_root):
    m = _manifest(data_root)
    rid = m["regions"][0]["id"]
    r = client.get("/api/calendar", params={"region": rid, "model": "mdd"})
    assert r.status_code == 200
    cal = r.json()
    assert [c["date"] for c in cal] == sorted(d["date"] for d in m["regions"][0]["dates"])  # real dates only
    for c in cal:
        assert c["status"] in ("detected", "clean", "unreliable", "no_image")
        assert set(c) >= {"date", "status", "n_detections", "cloud_frac", "quality_flags", "reason"}
        if c["status"] == "detected":
            assert c["n_detections"] > 0


def _same_rule(client, rid: str, h3: str, model: str):
    place = client.get("/api/place", params={"region": rid, "h3": h3, "model": model}).json()
    cal = {c["date"]: c for c in client.get("/api/calendar", params={"region": rid, "model": model}).json()}
    assert set(cal) == {h["date"] for h in place["history"]}
    for h in place["history"]:
        c = cal[h["date"]]
        assert (h["status"] == "unreliable") == (c["status"] == "unreliable"), (h["date"], h["status"], c["status"])
        assert (h["status"] == "no_image") == (c["status"] == "no_image"), (h["date"], h["status"], c["status"])
        if h["status"] == "unreliable":
            assert h["reason"] == c["reason"]
    return place, cal


def test_place_calendar_same_unreliable_rule(client, data_root):
    """L27: /api/place and /api/calendar share place.date_reliability() → identical «ненадёжно» per date."""
    rid, _date, model, z = _first_zone(data_root)
    _same_rule(client, rid, z["h3"], model)


def test_haze_date_unreliable_in_both(tmp_path):
    """A date with a haze flag is «ненадёжно» in the calendar AND in the place history (was «чисто»/«найдено»)."""
    root = tmp_path / "demo"
    shutil.copytree(DEMO, root, ignore=shutil.ignore_patterns("drift.json"))
    rid, date, model, z = _first_zone(root)
    m = _manifest(root)
    reg = next(r for r in m["regions"] if r["id"] == rid)
    d = next(x for x in reg["dates"] if x["date"] == date)
    d["quality"] = {**(d.get("quality") or {}), "haze": True}
    (root / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    from service.app import create_app

    c = TestClient(create_app(root))
    place, cal = _same_rule(c, rid, z["h3"], model)
    row = next(h for h in place["history"] if h["date"] == date)
    assert row["status"] == "unreliable" and cal[date]["status"] == "unreliable"
    assert "дымка" in row["reason"] and row["has_findings"] is True
    assert place["summary"]["n_unreliable"] >= 1


# ---------------------------------------------------------------- review
def test_review_queue_label_and_retrain(client, data_root, labels_dir):
    rid, date, model, _ = _first_zone(data_root)
    r = client.get("/api/review/queue", params={"region": rid, "limit": 500})
    assert r.status_code == 200
    q = r.json()
    assert q["labels"] == ["debris", "foam", "algae", "ship_wake", "cloud", "other"]
    assert q["items"], "demo has near-threshold / disagreement detections"
    it = q["items"][0]
    for k in ("id", "region", "date", "model", "lon", "lat", "max_prob", "reason", "crop_rgb", "crop_false_color"):
        assert k in it
    assert it["crop_rgb"].startswith("/api/crop?")

    # retrain with 0 labels -> clear error, no process started
    r = client.post("/api/review/retrain")
    assert r.status_code == 422 and "нет меток" in r.json()["detail"]

    # bad label
    bad = {k: it[k] for k in ("id", "region", "date", "model", "lon", "lat")}
    assert client.post("/api/review/label", json={**bad, "label": "plastic"}).status_code == 422
    assert client.post("/api/review/label", json={"label": "debris"}).status_code == 422

    body = {**bad, "label": "foam", "note": "белая полоса вдоль фронта"}
    r = client.post("/api/review/label", json=body)
    assert r.status_code == 200, r.text
    rec = r.json()
    pv = rec["provenance"]
    assert pv["user"] == "local" and pv["ts"] and pv["app_version"] and pv["model"] == it["model"]
    assert pv["source_scene_id"] and pv["max_prob"] == it["max_prob"]
    f = labels_dir / "labels.jsonl"
    assert f.is_file()
    lines = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert lines[-1]["label"] == "foam" and lines[-1]["id"] == it["id"]
    assert not (ROOT / "service" / "labels" / "labels.jsonl").is_file() or \
        it["id"] not in (ROOT / "service" / "labels" / "labels.jsonl").read_text(encoding="utf-8")

    labs = client.get("/api/review/labels").json()
    assert any(x["id"] == it["id"] for x in labs["items"])
    # labelled item leaves the queue
    q2 = client.get("/api/review/queue", params={"region": rid, "limit": 500}).json()
    assert all(x["id"] != it["id"] for x in q2["items"])

    # flag "ложное" -> goes to the top of the queue
    other = q2["items"][-1]
    r = client.post("/api/review/flag", json={k: other[k] for k in ("id", "region", "date", "model", "lon", "lat")})
    assert r.status_code == 200 and r.json()["kind"] == "flag_false"
    q3 = client.get("/api/review/queue", params={"region": rid, "limit": 500}).json()
    assert q3["items"][0]["id"] == other["id"] and "user_flag" in q3["items"][0]["reasons"]


def test_retrain_status_unknown(client):
    assert client.get("/api/review/retrain/nope_000").status_code == 404


def test_retrain_script_no_labels(tmp_path):
    import subprocess

    out = tmp_path / "r"
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "retrain_with_labels.py"),
                        "--labels", str(tmp_path / "none.jsonl"), "--out", str(out)],
                       capture_output=True, text=True, encoding="utf-8", timeout=120,
                       env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8", "CUDA_VISIBLE_DEVICES": ""})
    assert p.returncode == 2
    res = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert "нет меток" in res["error"]


# ---------------------------------------------------------------- L38: artifacts + score_terms
def _strip_build_fields(root: Path) -> None:
    """L38b: remove the build's own artifact marks and score_terms from the whole temp copy, so the tests below
    control every artifact / score term themselves and do not depend on the current demo data."""
    for p in root.rglob("detections.geojson"):
        fc = json.loads(p.read_text(encoding="utf-8"))
        hit = False
        for f in fc.get("features") or []:
            if "artifact" in (f.get("properties") or {}):
                f["properties"].pop("artifact")
                hit = True
        if hit:
            p.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    for p in root.rglob("zones.json"):
        zj = json.loads(p.read_text(encoding="utf-8"))
        hit = False
        for zz in zj.get("zones") or []:
            if "score_terms" in zz:
                zz.pop("score_terms")
                hit = True
        if hit:
            p.write_text(json.dumps(zj, ensure_ascii=False), encoding="utf-8")


def _mix_artifacts(root: Path) -> dict:
    """Mark detections of zone №1's date/model as artifacts (one in the zone cell, one confirmed by the second model)
    and add score_terms to the zones of that date. Returns ids per kind. The build's own artifact / score_terms
    fields are stripped first (_strip_build_fields), so the expected counts do not depend on the demo data."""
    from service import core, place as pl

    _strip_build_fields(root)
    rid, date, model, z = _first_zone(root)
    dp = root / rid / date / model / "detections.geojson"
    fc = json.loads(dp.read_text(encoding="utf-8"))
    feats = fc["features"]
    in_cell = [f for f in feats if pl.cell_of(*pl.det_lonlat(f)) == z["h3"]]
    out_cell = [f for f in feats if f not in in_cell]
    assert in_cell and len(out_cell) >= 3
    # a plain seam must not be confirmed by the second model (confirmed artifact = 'artifact_conflict' reason)
    seam = next((f for f in in_cell if f["properties"].get("confirmed") is not True), in_cell[0])
    seam["properties"]["confirmed"] = False
    free = [f for f in out_cell if f["properties"].get("confirmed") is not True]
    wake, ship = free[0], free[1]
    conflict = next((f for f in out_cell if f["properties"].get("confirmed") is True), None)
    if conflict is None:
        conflict = next(f for f in out_cell if f not in (wake, ship))
        conflict["properties"]["confirmed"] = True
    seam["properties"]["artifact"] = "seam"
    wake["properties"]["artifact"] = "wake"
    ship["properties"]["artifact"] = "ship"
    conflict["properties"]["artifact"] = "seam"
    # the ship sits near the threshold: a queue reason for a normal detection, but not for an artifact
    thr = pl.model_threshold(core.Store.open(root), model)
    ship["properties"]["max_prob"] = round(thr + 0.02, 4)
    dp.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    zp = root / rid / date / model / "zones.json"
    zj = json.loads(zp.read_text(encoding="utf-8"))
    for zz in zj["zones"]:
        base = float(zz["flagged_water_px"]) * float(zz["mean_prob"])
        agree = 1.5 if (zz.get("n_confirmed") or 0) > 0 else 1.0
        zz["score_terms"] = {"base": round(base, 3), "agreement": agree, "date_penalty": 1.0,
                             "score": round(base * agree, 3)}
    zp.write_text(json.dumps(zj, ensure_ascii=False), encoding="utf-8")
    ids = {k: f["properties"]["id"] for k, f in
           (("seam", seam), ("wake", wake), ("ship", ship), ("conflict", conflict))}
    return {"root": root, "rid": rid, "date": date, "model": model, "zone": z, "ids": ids, "n_feats": len(feats)}


@pytest.fixture()
def art_env(tmp_path, monkeypatch):
    root = tmp_path / "demo"
    shutil.copytree(DEMO, root, ignore=shutil.ignore_patterns("drift.json"))
    info = _mix_artifacts(root)
    monkeypatch.setenv("MACROPLASTIC_LABELS", str(tmp_path / "labels"))
    from service.app import create_app

    return TestClient(create_app(root)), info


def test_artifacts_review_queue_rule(art_env):
    c, info = art_env
    ids = info["ids"]
    q = c.get("/api/review/queue", params={"region": info["rid"], "limit": 1000}).json()
    got = {it["id"]: it for it in q["items"]}
    # plain artifacts (seam in the zone, wake, ship near the threshold) are not in the queue
    for k in ("seam", "wake", "ship"):
        assert ids[k] not in got, k
    assert q["n_artifacts_excluded"] >= 3
    assert "artifact" in q["rules"] and "artifact_conflict" in q["rules"]
    # artifact confirmed by the second model -> in the queue with its own reason only
    it = got[ids["conflict"]]
    assert it["reasons"] == ["artifact_conflict"] and it["artifact"] == "seam" and it["artifact_ru"] == "шов детекторов"
    assert "вторая модель" in it["reason"]
    # a user flag brings an artifact back (priority of a user flag, no other reasons)
    wake = ids["wake"]
    r = c.post("/api/review/flag", json={"id": wake, "region": info["rid"], "date": info["date"],
                                         "model": info["model"], "lon": 0.0, "lat": 0.0})
    assert r.status_code == 200
    q2 = c.get("/api/review/queue", params={"region": info["rid"], "limit": 1000}).json()
    w = next(x for x in q2["items"] if x["id"] == wake)
    assert w["reasons"] == ["user_flag"] and w["artifact"] == "wake" and w["priority"] == 3
    assert q2["n_artifacts_excluded"] == q["n_artifacts_excluded"] - 1


def test_artifacts_kpi_calendar_zone_place(art_env):
    c, info = art_env
    rid, date, model = info["rid"], info["date"], info["model"]
    k = c.get("/api/kpi", params={"region": rid, "date": date, "model": model}).json()["kpi"]
    assert k["n_artifacts"] == 4 and k["n_detections"] == info["n_feats"] - 4
    cal = {x["date"]: x for x in c.get("/api/calendar", params={"region": rid, "model": model}).json()}
    assert cal[date]["n_artifacts"] == 4 and cal[date]["n_detections"] == info["n_feats"] - 4
    z = c.get("/api/zone", params={"region": rid, "date": date, "model": model, "h3": info["zone"]["h3"]}).json()
    assert z["n_artifacts"] == 1 and z["artifacts"][0]["id"] == info["ids"]["seam"]
    assert z["artifacts"][0]["artifact_ru"] == "шов детекторов"
    assert all(d["id"] != info["ids"]["seam"] for d in z["detections"])
    place = c.get("/api/place", params={"region": rid, "h3": info["zone"]["h3"], "model": model}).json()
    row = next(h for h in place["history"] if h["date"] == date)
    assert row["n_artifacts"] == 1 and all(d["id"] != info["ids"]["seam"] for d in row["detections"])
    # the other model's files are untouched: no n_artifacts key («без поля — всё как сейчас»)
    other = "lgbm" if model == "mdd" else "mdd"
    assert "n_artifacts" not in c.get("/api/kpi", params={"region": rid, "date": date, "model": other}).json()["kpi"]


def test_zone_why_score_terms(art_env):
    c, info = art_env
    rid, date, model, z = info["rid"], info["date"], info["model"], info["zone"]
    j = c.get("/api/zone", params={"region": rid, "date": date, "model": model, "h3": z["h3"]}).json()
    why = j["why"]
    st = [t for t in why["score_terms"] if t["kind"] != "info"]
    assert [t["name"] for t in st] == ["base", "agreement", "date_penalty"]
    base, agree, pen = (t["value"] for t in st)
    assert abs(base * agree * pen - why["score"]) < 0.01 * max(1.0, why["score"])
    assert why["score_mode"] == "mult" and "agreement" in why["formula"] and "score_terms" in why["formula_text"]
    # L38b: terms[] = the three factors of the base score + agreement + date_penalty; product = final score,
    # product of the first three = base score (the frontend reads terms[0..2] by index)
    assert [t["name"] for t in why["terms"]] == ["flagged_water_px", "mean_prob", "repeat_dates", "agreement",
                                                 "date_penalty"]
    prod = 1.0
    for t in why["terms"]:
        prod *= t["contribution"]
    assert abs(prod - why["score"]) < 0.01 * max(1.0, why["score"])
    prod3 = why["terms"][0]["contribution"] * why["terms"][1]["contribution"] * why["terms"][2]["contribution"]
    assert abs(prod3 - why["base_score"]) < 0.01 * max(1.0, why["base_score"])
    assert why["terms"][3]["contribution"] == agree and why["terms"][4]["contribution"] == pen
    if (z.get("n_confirmed") or 0) > 0:
        assert agree == 1.5 and "Согласие моделей повышает балл" in why["text"]
    assert j["score_terms"]["base"] == base


def test_score_terms_shapes():
    from service import place as pl

    assert pl.score_terms({}) is None and pl.score_terms({"score_terms": {"agreement": 2}}) is None
    a = pl.score_terms({"score_terms": {"base": 100, "agreement": 1.5, "date_penalty": 0.5}})
    assert a["score"] == 75 and a["mode"] == "mult"
    b = pl.score_terms({"score_terms": [{"name": "base_score", "value": 100, "label": "база"},
                                        {"name": "agree_mult", "value": 1.2},
                                        {"name": "unreliable_penalty", "value": 20}, {"name": "score", "value": 100},
                                        {"name": "n_px", "value": 7}]})
    assert b["mode"] == "sub" and b["score"] == 100 and b["terms"][0]["label"] == "база"
    assert b["terms"][-1] == {"name": "n_px", "label": "n_px", "value": 7, "kind": "info"}
    c = pl.score_terms({"score_terms": {"base": {"value": 10, "note": "x"}, "score": 10}})
    assert c["agreement"] == 1 and c["date_penalty"] == 1 and c["terms"][0]["note"] == "x"
    assert pl.final_score({"score_terms": {"base": 10, "agreement": 2}}) == 20
    assert pl.final_score({"score": 3.5}) == 3.5


def test_pdf_artifacts_line(art_env):
    c, info = art_env
    from service import core, pdf

    st = core.Store.open(info["root"])
    rid, date, model, z = info["rid"], info["date"], info["model"], info["zone"]
    zone = c.get("/api/zone", params={"region": rid, "date": date, "model": model, "h3": z["h3"]}).json()
    line = pdf.artifacts_line(st, rid, date, model, zone)
    assert line.startswith(f"Исключено как артефакты на {date}: 4 объект") and "шов детекторов: 1" in line
    other = "lgbm" if model == "mdd" else "mdd"
    assert pdf.artifacts_line(st, rid, date, other, None) is None  # no artifacts -> no line
    r = c.get("/api/place_report.pdf", params={"region": rid, "h3": z["h3"], "model": model, "date": date})
    assert r.status_code == 200 and r.content[:5] == b"%PDF-"
