"""Incidents API: incidents with statuses + event feed (/api/incidents*, /api/feed), link with review labels.

Runs on a temporary copy of service/demo; labels + incidents journal go to a temp dir via $MACROPLASTIC_LABELS.
    .venv\\Scripts\\python.exe -m pytest -q tests\\test_incidents.py
"""
from __future__ import annotations

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
    out = tmp_path_factory.mktemp("demo_inc") / "demo"
    shutil.copytree(DEMO, out, ignore=shutil.ignore_patterns("drift.json", "*.tif", "*.png", "*.jpg"))
    return out


@pytest.fixture(scope="module")
def labels_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("labels_inc")


@pytest.fixture(scope="module")
def client(data_root, labels_dir):
    mp = pytest.MonkeyPatch()
    mp.setenv("MACROPLASTIC_LABELS", str(labels_dir))
    from service.app import create_app

    yield TestClient(create_app(data_root))
    mp.undo()


def _dets(root: Path) -> list[dict]:
    m = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    out = []
    for r in m["regions"]:
        for d in r["dates"]:
            for model in d.get("models") or []:
                p = root / r["id"] / d["date"] / model / "detections.geojson"
                if p.is_file():
                    for f in json.loads(p.read_text(encoding="utf-8"))["features"]:
                        out.append(f["properties"])
    return out


def _real(root: Path, skip: int = 0) -> dict:
    real = [p for p in _dets(root) if not p.get("artifact")]
    assert len(real) > skip
    return real[skip]


def test_initial_state_and_summary(client, data_root):
    dets = _dets(data_root)
    n_art = sum(1 for p in dets if p.get("artifact"))
    s = client.get("/api/incidents/summary").json()
    for k in ("total", "by_status", "reviewed", "confirmed_share_of_reviewed", "confirmed_share_text"):
        assert k in s, k
    assert s["by_kind"]["detection"] == len(dets)
    assert s["by_status"]["excluded"] == n_art
    assert s["by_status"]["detected"] == s["total"] - n_art  # zones + non-artifact detections
    assert s["reviewed"] == 0 and s["confirmed_share_of_reviewed"] is None


def test_list_filters_and_detail(client, data_root):
    j = client.get("/api/incidents", params={"region": "mumbai", "limit": 5}).json()
    assert 0 < len(j["items"]) <= 5 and j["n_total"] >= len(j["items"])
    assert all(i["region"] == "mumbai" for i in j["items"])
    ex = client.get("/api/incidents", params={"status": "excluded", "limit": 10000}).json()
    assert all(i["status"] == "excluded" and i["artifact"] for i in ex["items"])
    zones = client.get("/api/incidents", params={"kind": "zone", "limit": 10000}).json()["items"]
    assert zones and all(i["id"].startswith("zone:") and i["priority"] is not None for i in zones)
    # a detection inside a ranked zone carries that zone's rank
    in_zone = [i for i in client.get("/api/incidents", params={"kind": "detection", "limit": 10000}).json()["items"]
               if i.get("zone_id")]
    assert in_zone
    z = client.get(f"/api/incidents/{in_zone[0]['zone_id']}").json()
    assert z["priority"] == in_zone[0]["priority"]
    d = client.get(f"/api/incidents/{_real(data_root)['id']}").json()
    for k in ("id", "region", "date", "model", "lon", "lat", "area_m2", "priority", "confirmed_by_other_model",
              "artifact", "status", "history"):
        assert k in d, k
    h = d["history"][0]
    assert h["source"] == "build" and h["actor"] == "system" and h["to"] == "detected" and h["from"] is None
    assert client.get("/api/incidents/nope_123").status_code == 404
    assert client.get("/api/incidents", params={"status": "bogus"}).status_code == 422


def test_status_transitions(client, data_root, labels_dir):
    iid = _real(data_root, 0)["id"]
    bad = client.post(f"/api/incidents/{iid}/status", json={"to": "resolved"})
    assert bad.status_code == 409 and "allowed" in bad.json()
    assert client.post(f"/api/incidents/{iid}/status", json={"to": "nonsense"}).status_code == 422
    assert client.post("/api/incidents/nope_123/status", json={"to": "under_review"}).status_code == 404
    r = client.post(f"/api/incidents/{iid}/status", json={"to": "under_review", "note": "смотрю"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "under_review"
    r = client.post(f"/api/incidents/{iid}/status", json={"to": "confirmed"})
    assert r.json()["status"] == "confirmed"
    r = client.post(f"/api/incidents/{iid}/status", json={"to": "resolved", "note": "убрано"})
    j = r.json()
    assert j["status"] == "resolved"
    assert [h["to"] for h in j["history"]] == ["detected", "under_review", "confirmed", "resolved"]
    assert all(h["actor"] == "operator" and h["source"] == "api" for h in j["history"][1:])
    # journal is append-only JSONL next to labels.jsonl
    lines = (labels_dir / "incidents.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 and json.loads(lines[-1])["to"] == "resolved"
    assert client.post(f"/api/incidents/{iid}/status", json={"to": "confirmed"}).status_code == 409


def test_artifact_excluded_can_be_reopened(client, data_root):
    art = [p for p in _dets(data_root) if p.get("artifact")]
    if not art:
        pytest.skip("no artifacts in demo")
    iid = art[0]["id"]
    d = client.get(f"/api/incidents/{iid}").json()
    assert d["status"] == "excluded" and d["history"][0]["note"].startswith("артефакт")
    assert client.post(f"/api/incidents/{iid}/status", json={"to": "confirmed"}).status_code == 409
    assert client.post(f"/api/incidents/{iid}/status", json={"to": "under_review"}).json()["status"] == "under_review"


def test_review_label_creates_events(client, data_root):
    p1, p2 = _real(data_root, 1), _real(data_root, 2)
    for p, lab in ((p1, "debris"), (p2, "foam")):
        body = {k: p[k] for k in ("id", "region", "date", "model", "lon", "lat")} | {"label": lab}
        assert client.post("/api/review/label", json=body).status_code == 200
    a = client.get(f"/api/incidents/{p1['id']}").json()
    b = client.get(f"/api/incidents/{p2['id']}").json()
    assert a["status"] == "confirmed" and a["history"][-1]["actor"] == "operator"
    assert a["history"][-1]["source"] == "review" and a["label"] == "debris"
    assert b["status"] == "false_alarm" and b["verdict"] == "false_alarm"
    s = client.get("/api/incidents/summary").json()
    assert s["reviewed"] >= 2
    assert s["confirmed_share_of_reviewed"] == pytest.approx(s["confirmed_reviewed"] / s["reviewed"], abs=1e-3)
    assert "% проверенных" in s["confirmed_share_text"]
    # flag "ложное" from the map -> under_review
    p3 = _real(data_root, 3)
    body = {k: p3[k] for k in ("id", "region", "date", "model", "lon", "lat")}
    assert client.post("/api/review/flag", json=body).status_code == 200
    assert client.get(f"/api/incidents/{p3['id']}").json()["status"] == "under_review"


def test_feed(client, data_root):
    j = client.get("/api/feed", params={"limit": 50}).json()
    items = j["items"]
    assert 0 < len(items) <= 50
    for e in items:
        for k in ("ts", "kind", "text", "region", "incident_id", "lon", "lat", "priority"):
            assert k in e, k
    ts = [e["ts"] for e in items]
    assert ts == sorted(ts, reverse=True)
    kinds = {e["kind"] for e in items}
    assert {"confirmed", "false_alarm", "resolved"} <= kinds  # operator events are the newest
    conf = next(e for e in items if e["kind"] == "confirmed" and e["incident_id"] == _real(data_root, 1)["id"])
    assert conf["text"].startswith("подтверждено оператором · ")
    foam = next(e for e in items if e["kind"] == "false_alarm")
    assert foam["text"].startswith("исключено как пена")
    new = client.get("/api/feed", params={"kind": "new", "limit": 5}).json()["items"]
    assert new and all(e["text"].startswith("новое пятно · ") and "м²" in e["text"] for e in new)
    exc = client.get("/api/feed", params={"kind": "excluded", "limit": 5}).json()["items"]
    if any(p.get("artifact") for p in _dets(data_root)):
        assert exc and all(e["text"].startswith("исключено как ") for e in exc)
    reg = client.get("/api/feed", params={"region": "mumbai", "limit": 20}).json()["items"]
    assert reg and all(e["region"] == "mumbai" for e in reg)
