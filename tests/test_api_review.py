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
    assert names == ["flagged_water_px", "mean_prob", "repeat_dates"]
    for t in why["terms"]:
        assert set(t) >= {"name", "value", "weight", "contribution"}
    prod = 1.0
    for t in why["terms"]:
        prod *= t["contribution"]
    assert abs(prod - why["score"]) < 0.01 * max(1.0, why["score"])
    if "score" in z:
        assert abs(why["score"] - z["score"]) < 0.01
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
    assert {h["status"] for h in j["history"]} <= {"found", "clean", "no_observation", "no_image"}
    row = next(h for h in j["history"] if h["date"] == date)
    assert row["status"] == "found"
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
