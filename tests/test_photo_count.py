"""Photo counter (L109): metrics on hand-made cases + API contract with a stub model (no weights/GPU needed)."""
import io
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from macroplastic.photo_count import metrics as M  # noqa: E402
from macroplastic.photo_count.model import density_per_km2  # noqa: E402


def test_iou_basic():
    a = np.array([[0, 0, 10, 10]])
    b = np.array([[0, 0, 10, 10], [5, 0, 15, 10], [20, 20, 30, 30]])
    np.testing.assert_allclose(M.iou_matrix(a, b)[0], [1.0, 50 / 150, 0.0])


def test_ap_perfect_and_empty():
    gts = [np.array([[0, 0, 10, 10]]), np.array([[0, 0, 5, 5], [10, 10, 20, 20]])]
    preds = [(gts[0], np.array([0.9])), (gts[1], np.array([0.8, 0.7]))]
    assert M.ap_at_iou(preds, gts) == pytest.approx(1.0)
    assert M.ap_at_iou(preds, gts, interp="voc") == pytest.approx(1.0)
    none = [(np.zeros((0, 4)), np.zeros(0)), (np.zeros((0, 4)), np.zeros(0))]
    assert M.ap_at_iou(none, gts) == 0.0


def test_ap_half_recall_and_fp_order():
    gts = [np.array([[0, 0, 10, 10], [20, 20, 30, 30]])]
    # one TP (high score), one FP (lower score): recall 0.5, precision 1 up to r=0.5
    preds = [(np.array([[0, 0, 10, 10], [50, 50, 60, 60]]), np.array([0.9, 0.5]))]
    assert M.ap_at_iou(preds, gts, interp="voc") == pytest.approx(0.5)
    assert M.ap_at_iou(preds, gts) == pytest.approx(51 / 101)  # COCO 101 points: r in 0..0.5 -> 51 points at P=1
    # FP ranked first: precision at r=0.5 drops to 0.5
    preds2 = [(np.array([[0, 0, 10, 10], [50, 50, 60, 60]]), np.array([0.5, 0.9]))]
    assert M.ap_at_iou(preds2, gts, interp="voc") == pytest.approx(0.25)


def test_duplicate_detection_is_fp():
    gts = [np.array([[0, 0, 10, 10]])]
    s, tp = M.match_image(np.array([[0, 0, 10, 10], [0, 0, 10, 10]]), np.array([0.9, 0.8]), gts[0])
    assert tp.tolist() == [True, False]


def test_count_metrics_and_bootstrap():
    cm = M.count_metrics([1, 2, 5], [1, 3, 3])
    assert cm["mae"] == pytest.approx(1.0) and cm["exact"] == pytest.approx(1 / 3) and cm["bias"] == pytest.approx(1 / 3)
    lo, hi = M.bootstrap_ci(lambda i: float(np.mean(np.array([0, 1, 2, 3])[i % 4])), 4, b=200)
    assert 0 <= lo <= 1.5 <= hi <= 3


def test_density_needs_area():
    assert density_per_km2(5, None) is None
    assert density_per_km2(5, 0) is None
    assert density_per_km2(5, 100.0) == pytest.approx(5 / 1e-4)


# ------------------------------------------------------------------ API contract (stub model)
class _Stub:
    threshold = 0.9
    device = "cpu"
    card = {"version": "stub-1", "gsd_train_m": 0.02,
            "count_interval": {"by_pred_count": [{"pred_from": 0, "pred_to": 0, "q025": 0, "q975": 1},
                                                 {"pred_from": 1, "pred_to": 999, "q025": -1, "q975": 2}],
                               "overall_val": [-1, 2], "coverage_on_test": 0.95, "method": "stub"},
            "correction": {"factor": [2.0, 1.0, 1.0, 1.0],
                           "factor_ci95": [[1.5, 2.5], [0.9, 1.1], [0.9, 1.1], [0.9, 1.1]]}}

    def count(self, image, threshold=None):
        thr = self.threshold if threshold is None else threshold
        b = np.array([[10, 10, 50, 40], [100, 100, 140, 130]], dtype=float)
        s = np.array([0.95, 0.6])
        k = s >= thr
        return b[k], s[k], thr


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from macroplastic.photo_count import model as PM
    monkeypatch.setattr(PM.Counter, "get", classmethod(lambda cls, survey="water_camera": _Stub()))
    from service.app import create_app
    return TestClient(create_app())


def _jpeg():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), (20, 60, 90)).save(buf, "JPEG")
    return buf.getvalue()


def test_api_count_contract(client):
    r = client.post("/api/v3/photo/count", content=_jpeg(), headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 200
    j = r.json()
    assert j["count"] == 1 and j["threshold"] == 0.9 and j["unit"] == "штук на кадр"
    assert j["model_version"] == "stub-1" and j["image"] == {"width": 320, "height": 180}
    assert set(j["boxes"][0]) == {"x1", "y1", "x2", "y2", "score", "label"}
    assert j["density"] is None and "площадь кадра" in j["density_reason"]
    assert any("Дроны" in s and "не проверено" in s for s in j["limitations"])
    assert "камера у воды" in j["survey"]


def test_api_threshold_area_multipart(client):
    r = client.post("/api/v3/photo/count?threshold=0.5&frame_area_m2=200", files={"file": ("x.jpg", _jpeg(), "image/jpeg")})
    j = r.json()
    assert r.status_code == 200 and j["count"] == 2
    assert j["density"]["items_per_km2"] == pytest.approx(2 / 2e-4)
    assert j["density"]["note"] == "на площади кадра, не спутник"


def test_api_errors(client):
    assert client.post("/api/v3/photo/count", content=b"").json()["error"]["code"] == "BAD_BODY"
    r = client.post("/api/v3/photo/count", content=b"xx", headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 415 and r.json()["error"]["code"] == "BAD_IMAGE"
    assert client.post("/api/v3/photo/count?threshold=1.5", content=_jpeg()).status_code == 400
    assert client.post("/api/v3/photo/count?bogus=1", content=_jpeg()).json()["error"]["code"] == "BAD_PARAM"


def test_api_meta(client):
    r = client.get("/api/v3/photo/meta")
    assert r.status_code == 200
    j = r.json()
    assert j["unit"] == "штук на кадр" and j["upload"]["path"] == "/api/v3/photo/count"
    assert j["density_note"] == "на площади кадра, не спутник"


def test_api_aerial_gsd_correction(client):
    # 320x180 px at GSD 0.02 m = 23.04 m^2; stub boxes 40 px = 80 cm -> bin "60-120 cm" (factor 1.0)
    r = client.post("/api/v3/photo/count?survey=aerial&gsd_m=0.02&threshold=0.5", content=_jpeg())
    j = r.json()
    assert r.status_code == 200 and j["survey_type"] == "aerial" and j["count"] == 2
    assert j["density"]["frame_area_m2"] == pytest.approx(23.04) and j["density"]["area_from"] == "gsd_m"
    assert "corrected_count" not in j["density"]  # threshold differs from the default -> no correction
    r = client.post("/api/v3/photo/count?survey=aerial&gsd_m=0.02", content=_jpeg())
    d = r.json()["density"]
    assert d["corrected_count"] == pytest.approx(1.0)
    assert d["items_per_km2_interval"][0] <= d["items_per_km2_corrected"] <= d["items_per_km2_interval"][1]
    assert any("Берег" in s for s in r.json()["limitations"])
    r = client.post("/api/v3/photo/count?survey=aerial&gsd_m=0.2", content=_jpeg())
    assert "вне проверенного" in r.json()["gsd_warning"]
    assert client.post("/api/v3/photo/count?survey=satellite", content=_jpeg()).status_code == 400


def test_corrected_count_bins():
    from macroplastic.photo_count.model import corrected_count
    b = np.array([[0, 0, 10, 10], [0, 0, 40, 10]])  # 20 cm and 80 cm at GSD 0.02
    assert corrected_count(b, 0.02, [2.0, 1.5, 1.0, 1.0]) == pytest.approx(3.0)
    assert corrected_count(np.zeros((0, 4)), 0.02, [2.0, 1, 1, 1]) == 0.0


def test_api_count_interval_and_composition(client):
    r = client.post("/api/v3/photo/count?frame_area_m2=100", content=_jpeg())  # default threshold 0.9 -> 1 box
    j = r.json()
    assert j["count"] == 1 and j["count_interval"]["interval"] == [0.0, 3.0]
    assert j["density"]["items_per_km2_count_interval"] == [0.0, pytest.approx(3 / 1e-4)]
    assert j["composition"]["status"] in ("not_determined", "by_class")
    assert j["value_source"] == "посчитано по детальному фото"
    r = client.post("/api/v3/photo/count?threshold=0.5", content=_jpeg())
    assert r.json()["count_interval"] is None  # interval is calibrated only for the default threshold
    m = client.get("/api/v3/photo/meta").json()
    assert "composition_rule" in m and "headline" in m


def test_headline_text_format():
    from service.routes_v3_photo import _headline
    h = _headline({"test_grouped": {"count_mae": 0.5949, "count_mae_ci95": [0.53, 0.66], "n_images": 1190}})
    assert h["text"] == "Счётчик предметов по фото: ошибка 0,59 шт./кадр на независимом тесте"
    assert _headline(None) is None


def test_clean_clone_weights_fallback(tmp_path, monkeypatch):
    """No weights_exp/: both product models come from weights/photo_count (fp16, in git)."""
    from macroplastic.photo_count import model as PM
    monkeypatch.setattr(PM, "WEIGHTS_DIR", tmp_path / "none")
    for sv in ("water_camera", "aerial"):
        card = PM.load_card(sv)
        assert card is not None and card["weights_file"].endswith("_fp16.pth")
        assert (PM.SHIPPED_DIR / card["weights_file"]).exists()


def test_no_weights_anywhere_503(tmp_path, monkeypatch):
    """No weights at all -> 503 MODEL_UNAVAILABLE with a reason (not a crash); meta says unavailable."""
    from macroplastic.photo_count import model as PM
    monkeypatch.setattr(PM, "WEIGHTS_DIR", tmp_path / "none")
    monkeypatch.setattr(PM, "SHIPPED_DIR", tmp_path / "none2")
    PM.Counter._inst.clear()
    from fastapi.testclient import TestClient
    from service.app import create_app
    c = TestClient(create_app())
    for q in ("", "?survey=aerial"):
        r = c.post("/api/v3/photo/count" + q, content=_jpeg())
        assert r.status_code == 503 and r.json()["error"]["code"] == "MODEL_UNAVAILABLE"
        assert "docs/PHOTO_COUNT.md" in r.json()["error"]["message"]
    m = c.get("/api/v3/photo/meta").json()
    assert m["available"] is False and m["surveys"]["aerial"]["available"] is False
    PM.Counter._inst.clear()
