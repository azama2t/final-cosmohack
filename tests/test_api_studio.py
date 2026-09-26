"""API v3 студии (L95): GET /api/v3/studio/scenes[/{id}[/view/{kind}.png]] — docs/CONTRACTS_V3.md, раздел 3.8.

Синтетическое дерево сцен во временном каталоге (ROOTS подменяются): пара S2 (rgb.png, quality.tif, prob.tif,
fdi.npy), пара Landsat (только quality.tif), сцена района (bands.tif 12 каналов, scl.tif, prob_lgbm.tif, scene.json),
сцена розыска + candidates.csv с уровнями. Плюс дымовой тест на реальных данных репозитория (пропуск, если их нет).
"""
from __future__ import annotations

import io
import json

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402

from service import case_store as cs  # noqa: E402
from service import routes_v3_studio as st  # noqa: E402
from service.app import create_app  # noqa: E402

EPSG = "EPSG:32635"
TR = from_origin(708000.0, 4835000.0, 10.0, 10.0)
H, W = 120, 100
S2_PAIR = "S2A_35TQJ_20240602_0_L2A"
LIVE_ID = "S2B_MSIL2A_20250511T022549_R046_T50LKR_20250511T060000"


def _tif(path, arr, desc=None, crs=EPSG, tr=TR):
    arr = np.asarray(arr)
    if arr.ndim == 2:
        arr = arr[None]
    with rasterio.open(path, "w", driver="GTiff", width=arr.shape[2], height=arr.shape[1], count=arr.shape[0],
                       dtype=arr.dtype, crs=crs, transform=tr) as ds:
        ds.write(arr)
        if desc:
            ds.descriptions = tuple(desc)


def _json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def tree(tmp_path, monkeypatch):
    roots = {k: tmp_path / k for k in ("pairs", "pairs_fdi", "live", "service", "drift", "search", "cache",
                                        "det_current")}
    for p in roots.values():
        p.mkdir()
    # --- S2 pair: all four views
    d = roots["pairs"] / "S4_DOORS3_T1"
    d.mkdir()
    q = np.ones((H, W), np.uint8)
    q[:10] = 2      # land
    q[10:20] = 3    # cloud
    q[20:25] = 6    # glint
    _tif(d / "quality.tif", q)
    prob = np.zeros((H, W), np.uint8)
    prob[60, 50] = 250  # one detection pixel (P = 0.98 > thr 0.63)
    prob[70, 30] = 100  # below threshold (P = 0.39)
    _tif(d / "prob.tif", prob)
    Image.fromarray(np.full((H, W, 3), 90, np.uint8), "RGB").save(d / "rgb.png")
    _json(d / "meta.json", {"event_id": "S4:DOORS3:T1", "sample_ids": "MPL-0904", "field_items_km2": 192.54,
                            "obs_datetime": "2024-06-02 12:00:00+00:00", "dt_hours": -3.03,
                            "scene_id": S2_PAIR, "source": "earth-search/sentinel-2-l2a",
                            "scene_datetime": "2024-06-02T08:58:16.484000Z", "tile": "35TQJ",
                            "decision": "accept", "reason": "", "quality": {"valid_water_frac": 0.8},
                            "config": {"detector": {"weights": "weights/lgbm", "harmonize": "none"}},
                            "detector": {"threshold": 0.63, "n_det": 1, "det_px_strip": 1, "n_det_crop": 1,
                                         "det_px_crop": 1, "prob_max": 0.98, "harmonize_offset": None}})
    (roots["pairs_fdi"] / "S4_DOORS3_T1").mkdir()
    fdi = np.linspace(-0.01, 0.03, H * W, dtype=np.float32).reshape(H, W)
    np.save(roots["pairs_fdi"] / "S4_DOORS3_T1" / "fdi.npy", fdi)
    # --- Landsat pair: only the QA mask
    d = roots["pairs"] / "S3_HE419_MarLitter_transect29"
    d.mkdir()
    _tif(d / "quality.tif", np.full((40, 30), 3, np.uint8), crs="EPSG:32632", tr=from_origin(440700.0, 6085800.0, 30.0, 30.0))
    _json(d / "meta.json", {"event_id": "S3:HE419_MarLitter_transect29", "scene_id": "LC08_L2SP_196022_20140412_02_T1",
                            "source": "planetary-computer/landsat-c2-l2",
                            "scene_datetime": "2014-04-12T10:20:44.439949Z", "decision": "reject", "reason": "cloud",
                            "substitution_note": "Landsat: QA_PIXEL mask only", "detector": None})
    # --- region scene (live): 12 bands + SCL + two detectors
    d = roots["live"] / "bali" / "2025-05-11"
    d.mkdir(parents=True)
    rng = np.random.default_rng(0)
    bands = (0.02 + 0.01 * rng.random((12, H, W))).astype(np.float32)
    bands[:, :5, :5] = np.nan  # nodata corner
    trl = from_origin(300000.0, 9100000.0, 10.0, 10.0)
    _tif(d / "bands.tif", bands, desc=st.S2_BANDS, crs="EPSG:32750", tr=trl)
    scl = np.full((H, W), 6, np.uint8)
    scl[:, :10] = 5
    scl[:5, :5] = 0
    _tif(d / "scl.tif", scl, crs="EPSG:32750", tr=trl)
    pl = np.zeros((H, W), np.uint8)
    pl[100, 90] = 255
    _tif(d / "prob_lgbm.tif", pl, crs="EPSG:32750", tr=trl)
    _tif(d / "prob_mdd.tif", np.zeros((H, W), np.uint8), crs="EPSG:32750", tr=trl)
    _json(d / "prob_lgbm.json", {"threshold": 0.64, "weights": "lgbm_live", "harmonize": "water_median",
                                 "n_above_threshold_water": 49,
                                 "domain_note": "trained on ACOLITE rhorc (L1C); L2A levels differ"})
    cur = roots["det_current"] / "live" / "bali" / "2025-05-11"
    cur.mkdir(parents=True)
    pc = np.zeros((H, W), np.uint8)
    pc[100, 90] = 255
    pc[30, 30] = 100  # below threshold -> yellow
    _tif(cur / "prob.tif", pc, crs="EPSG:32750", tr=trl)
    dm = np.zeros((H, W), np.uint8)
    dm[100, 90] = 1
    _tif(cur / "det.tif", dm, crs="EPSG:32750", tr=trl)
    qc = np.ones((H, W), np.uint8)
    qc[:, :10] = 2
    qc[:5, :5] = 0
    qc[50:52, 50:52] = 6  # glint: only the current-mode quality mask has it
    _tif(cur / "quality.tif", qc, crs="EPSG:32750", tr=trl)
    _json(cur / "det.json", {"weights": "weights/lgbm", "harmonize": "none", "threshold": 0.63, "pixels": 1,
                             "objects": 1})
    # drift scene with raw channels but WITHOUT the current-mode run: detection must not fall back to the legacy mode
    dd = roots["drift"] / "bali" / "2025-05-13"
    dd.mkdir(parents=True)
    trd = from_origin(310000.0, 9100000.0, 10.0, 10.0)
    _tif(dd / "bands.tif", bands, desc=st.S2_BANDS, crs="EPSG:32750", tr=trd)
    _tif(dd / "scl.tif", scl, crs="EPSG:32750", tr=trd)
    _tif(dd / "prob_lgbm.tif", pl, crs="EPSG:32750", tr=trd)
    _json(dd / "prob_lgbm.json", {"threshold": 0.64, "weights": "lgbm_live", "harmonize": "water_median"})
    _json(dd / "scene.json", {"datetime": "2025-05-13T02:25:49Z", "scene_id": "S2A_MSIL2A_20250513T0225",
                              "source": "planetary-computer"})
    _json(d / "scene.json", {"region": "bali", "region_name": "Бали", "date": "2025-05-11",
                             "datetime": "2025-05-11T02:25:49Z", "scene_id": LIVE_ID, "tile": "50LKR",
                             "source": "planetary-computer", "water_frac": 0.9})
    # --- search scene + candidates with levels
    src = roots["search"] / "s9"
    (src / "cache" / "x").mkdir(parents=True)
    _json(src / "cache" / "x" / "meta.json", {"scene_id": "IGNORED"})  # cache subtree is skipped
    _tif(src / "cache" / "x" / "quality.tif", np.ones((5, 5), np.uint8))
    d = src / "pairs" / "E1__S2B_X"
    d.mkdir(parents=True)
    trs = from_origin(500000.0, 1000000.0, 10.0, 10.0)
    _tif(d / "quality.tif", np.ones((50, 50), np.uint8), tr=trs)
    _tif(d / "prob.tif", np.zeros((50, 50), np.uint8), tr=trs)
    _json(d / "meta.json", {"event_id": "S9:E1", "scene_id": "S2B_X", "source": "earth-search/sentinel-2-l1c",
                            "scene_datetime": "2020-01-01T10:00:00Z", "detector": {"threshold": 0.5}})
    (src / "candidates.csv").write_text(
        "event_id,scene_id,sensor,dt_h,drift_zone,usable_frac,level,reason\n"
        "S4:DOORS3:T1,S2A_35TQJ_20240602_0_L2A,S2,-3,,0.8,C,кандидат для ручной проверки\n"
        "S9:E1,S2B_X,S2,1,,0.5,D,glint on water rejected\n"
        "S9:E2,S2B_Y,S2,1,,0.5,x,мусорная строка\n", encoding="utf-8")
    for k, p in roots.items():
        monkeypatch.setitem(st.ROOTS, k, p)
    st._png_mem.clear()
    return roots


@pytest.fixture()
def client(tree):
    return TestClient(create_app())


def _scenes(client, **params):
    r = client.get("/api/v3/studio/scenes", params=params)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/json; charset=utf-8"
    return r.json()


def test_list_sources_views_and_reasons(client):
    j = _scenes(client)
    ids = [s["id"] for s in j["scenes"]]
    assert j["total"] == 5 and j["count"] == 5
    assert set(ids) == {"pair.S4_DOORS3_T1", "pair.S3_HE419_MarLitter_transect29", "live.bali.2025-05-11",
                        "search.s9.pairs~E1__S2B_X", "drift.bali.2025-05-13"}
    assert "search.s9.cache~x" not in ids
    assert ids == sorted(ids, key=lambda i: next(s["datetime"] for s in j["scenes"] if s["id"] == i))
    assert j["by_source_type"] == {"drift": 1, "live": 1, "pair": 2, "search": 1}
    assert [k["id"] for k in j["kinds"]] == ["rgb", "spectral", "detection", "quality"]
    by = {s["id"]: s for s in j["scenes"]}
    p = by["pair.S4_DOORS3_T1"]
    assert p["available_views"] == ["rgb", "spectral", "detection", "quality"]
    assert p["platform"] == "Sentinel-2A" and p["collection"] == "sentinel-2-l2a" and p["catalog"] == "earth-search"
    assert p["datetime"].startswith("2024-06-02T08:58") and p["date"] == "2024-06-02"
    assert len(p["coordinates"]) == 4 and p["bounds"][0] < p["bounds"][2] and p["pixel_m"] == 10.0
    assert p["views"]["spectral"]["variants"] == ["fdi"]
    assert p["views"]["detection"]["url"] == "/api/v3/studio/scenes/pair.S4_DOORS3_T1/view/detection.png"
    assert "legend" not in p["views"]["detection"] and "level_records" not in p  # brief list
    assert p["field"]["field_items_km2"] == 192.54
    ls = by["pair.S3_HE419_MarLitter_transect29"]
    assert ls["available_views"] == ["quality"] and ls["platform"] == "Landsat-8"
    for k in ("rgb", "spectral", "detection"):
        assert ls["views"][k]["available"] is False and ls["views"][k]["url"] is None
        assert "Landsat" in ls["views"][k]["reason"]
    lv = by["live.bali.2025-05-11"]
    assert lv["available_views"] == ["rgb", "spectral", "detection", "quality"]
    assert lv["views"]["spectral"]["variants"] == ["fdi", "swir"]
    assert lv["views"]["detection"]["variants"] is None  # the legacy prob_mdd/prob_lgbm are not offered
    assert lv["platform"] == "Sentinel-2B" and lv["collection"] == "sentinel-2-l2a"
    assert -180 <= lv["bounds"][0] <= 180 and lv["bounds"][1] < 0  # UTM south zone -> southern latitudes


def test_evidence_levels_from_candidates(client):
    by = {s["id"]: s for s in _scenes(client)["scenes"]}
    assert by["pair.S4_DOORS3_T1"]["level"] == "C" and by["pair.S4_DOORS3_T1"]["level_match"] == "event_id+scene_id"
    assert by["search.s9.pairs~E1__S2B_X"]["level"] == "D"
    assert by["live.bali.2025-05-11"]["level"] is None and by["live.bali.2025-05-11"]["level_match"] is None
    one = client.get("/api/v3/studio/scenes/pair.S4_DOORS3_T1").json()
    assert one["level_records"][0]["reason"] == "кандидат для ручной проверки"
    assert one["views"]["detection"]["legend"]["threshold"] == 0.63
    srch = client.get("/api/v3/studio/scenes/search.s9.pairs~E1__S2B_X").json()
    assert srch["level_records"][0]["reason"] == st.LEVEL_REASON_RU["D"]  # English registry text is not shown
    assert srch["level_records"][0]["reason_raw"] == "glint on water rejected"
    assert [x["id"] for x in _scenes(client, level="C,D")["scenes"]] == ["search.s9.pairs~E1__S2B_X",
                                                                         "pair.S4_DOORS3_T1"]
    assert _scenes(client, level="none")["total"] == 3


def test_filters_and_strict_params(client):
    assert [s["id"] for s in _scenes(client, date_from="2024-01-01", date_to="2024-12-31")["scenes"]] == \
        ["pair.S4_DOORS3_T1"]
    assert _scenes(client, source="pair")["total"] == 2
    assert _scenes(client, sources="live")["total"] == 1  # plural alias
    assert _scenes(client, view="spectral")["total"] == 3
    j = _scenes(client, bbox="-10,-10,-9,-9")
    assert j["total"] == 0 and j["empty_reason"]
    p = next(s for s in _scenes(client)["scenes"] if s["id"] == "pair.S4_DOORS3_T1")
    b = p["bounds"]
    assert [s["id"] for s in _scenes(client, bbox=",".join(map(str, b)))["scenes"]] == ["pair.S4_DOORS3_T1"]
    assert _scenes(client, limit=1, offset=1)["count"] == 1
    for url, code in [("/api/v3/studio/scenes?foo=1", "BAD_PARAM"), ("/api/v3/studio/scenes?bbox=1,2", "BAD_BBOX"),
                      ("/api/v3/studio/scenes?date_from=2024-13-01", "BAD_DATE"),
                      ("/api/v3/studio/scenes?source=moon", "BAD_PARAM"),
                      ("/api/v3/studio/scenes?source=pair&sources=live", "BAD_PARAM"),
                      ("/api/v3/studio/scenes/pair.S4_DOORS3_T1?x=1", "BAD_PARAM"),
                      ("/api/v3/studio/scenes/pair.S4_DOORS3_T1/view/rgb.png?foo=1", "BAD_PARAM"),
                      ("/api/v3/studio/scenes/pair.S4_DOORS3_T1/view/rgb.png?px=9", "BAD_PARAM"),
                      ("/api/v3/studio/scenes/pair.S4_DOORS3_T1/view/rgb.png?variant=swir", "BAD_PARAM"),
                      ("/api/v3/studio/scenes/pair.S4_DOORS3_T1/view/ndvi.png", "BAD_PARAM")]:
        r = client.get(url)
        assert r.status_code == 400, url
        assert r.headers["content-type"] == "application/json; charset=utf-8"
        assert r.json()["error"]["code"] == code, url
    r = client.get("/api/v3/studio/scenes/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_routes_not_shadowed_by_v3_catch_all(client):
    assert client.get("/api/v3/studio/scenes").status_code == 200
    r = client.get("/api/v3/no_such_endpoint")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert client.get("/api/v3/meta").status_code == 200


def _img(r):
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    return Image.open(io.BytesIO(r.content))


def test_views_render_from_real_files(client):
    base = "/api/v3/studio/scenes/pair.S4_DOORS3_T1/view"
    im = _img(client.get(f"{base}/rgb.png"))
    assert im.size == (W, H)
    r = client.get(f"{base}/detection.png")
    im = np.asarray(_img(r).convert("RGBA"))
    assert r.headers["x-evidence-level"] == "C" and r.headers["x-detection-pixels"] == "1"
    assert tuple(im[60, 50]) == (255, 45, 85, 242)       # above threshold
    assert im[70, 30, 3] > 0 and im[70, 30, 0] == 255     # 0.2 <= P < thr: yellow, semi-transparent
    assert im[0, 0, 3] == 0                               # P = 0: transparent
    # max-pool on downscale: the single detection pixel survives px=64
    r = client.get(f"{base}/detection.png", params={"px": 64})
    assert r.headers["x-detection-pixels"] == "1" and max(_img(r).size) == 64
    q = np.asarray(_img(client.get(f"{base}/quality.png")).convert("RGBA"))
    pal = {c: cs._rgba(qc["color"]) for qc in cs.QUALITY_CLASSES for c in qc["codes"]}
    assert tuple(q[5, 5]) == pal[2] and tuple(q[15, 5]) == pal[3] and tuple(q[22, 5]) == pal[6]
    assert tuple(q[100, 50]) == pal[1]
    r = client.get(f"{base}/spectral.png")
    assert r.headers["x-view-scale"].startswith("FDI") and _img(r).size == (W, H)
    live = "/api/v3/studio/scenes/live.bali.2025-05-11/view"
    im = np.asarray(_img(client.get(f"{live}/rgb.png")).convert("RGBA"))
    assert im[0, 0, 3] == 0 and im[50, 50, 3] == 255      # NaN bands -> transparent
    assert _img(client.get(f"{live}/spectral.png", params={"variant": "swir"})).size == (W, H)
    r = client.get(f"{live}/detection.png")
    assert r.headers["x-detection-pixels"] == "1" and "harmonization=none" in r.headers["x-view-scale"]
    im = np.asarray(_img(r).convert("RGBA"))
    assert tuple(im[100, 90]) == (255, 45, 85, 242) and im[30, 30, 3] > 0 and im[60, 60, 3] == 0
    assert client.get(f"{live}/detection.png", params={"variant": "mdd"}).status_code == 400
    q = np.asarray(_img(client.get(f"{live}/quality.png")).convert("RGBA"))
    assert tuple(q[80, 80]) == pal[1] and tuple(q[50, 5]) == pal[2] and tuple(q[0, 0]) == pal[0]
    assert tuple(q[50, 50]) == pal[6]  # current-mode quality mask (with glint), not the bare SCL


def test_missing_view_is_404_with_reason(client):
    r = client.get("/api/v3/studio/scenes/pair.S3_HE419_MarLitter_transect29/view/rgb.png")
    assert r.status_code == 404
    e = r.json()["error"]
    assert e["code"] == "NO_VIEW" and "Landsat" in e["details"]["reason"]
    assert e["details"]["available_views"] == ["quality"]
    assert client.get("/api/v3/studio/scenes/pair.S3_HE419_MarLitter_transect29/view/quality.png").status_code == 200


def test_png_cache_memory_disk_and_etag(client, tree):
    url = "/api/v3/studio/scenes/live.bali.2025-05-11/view/rgb.png"
    r1 = client.get(url)
    assert r1.headers["x-cache"] == "render"
    r2 = client.get(url)
    assert r2.headers["x-cache"] == "mem" and r2.content == r1.content
    st._png_mem.clear()
    r3 = client.get(url)
    assert r3.headers["x-cache"] == "disk" and r3.content == r1.content
    assert list((tree["cache"] / "live.bali.2025-05-11").glob("rgb-*.png"))
    r4 = client.get(url, headers={"If-None-Match": r1.headers["etag"]})
    assert r4.status_code == 304


def test_scene_detail_timeline(client):
    j = client.get("/api/v3/studio/scenes/live.bali.2025-05-11").json()
    assert j["id"] == "live.bali.2025-05-11" and j["region"] == "bali"
    assert [t["id"] for t in j["timeline"]] == ["live.bali.2025-05-11", "drift.bali.2025-05-13"]
    assert j["views"]["quality"]["legend"]["type"] == "classes"
    assert j["detector"]["threshold"] == 0.63


def test_real_repo_smoke():
    """Real data of the repository (data/pairs/quality is in git): pair scenes are listed, views of an S2 pair work."""
    if not (st.REPO / "data" / "pairs" / "quality" / "S4_DOORS3_T1" / "quality.tif").is_file():
        pytest.skip("data/pairs/quality not present")
    c = TestClient(create_app())
    j = c.get("/api/v3/studio/scenes", params={"source": "pair"}).json()
    assert j["total"] >= 20
    s = next(x for x in j["scenes"] if x["id"] == "pair.S4_DOORS3_T1")
    assert {"rgb", "detection", "quality"} <= set(s["available_views"])
    for k in s["available_views"]:
        r = c.get(s["views"][k]["url"], params={"px": 256})
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_detector_is_current_mode_only(client):
    by = {s["id"]: s for s in _scenes(client)["scenes"]}
    lv = by["live.bali.2025-05-11"]["detector"]
    assert lv["run"] is True and lv["weights"] == "lgbm" and lv["harmonization"] is False
    assert lv["threshold"] == 0.63 and lv["pixels"] == 1 and lv["objects"] == 1
    assert lv["label"].startswith("Детектор (текущий режим: веса lgbm, без гармонизации, порог 0,63): 1 объект, 1 пиксель")
    assert lv["legacy"]["weights"] == "lgbm_live" and "отвергнут" in lv["legacy"]["note"]
    p = by["pair.S4_DOORS3_T1"]["detector"]
    assert p["run"] and p["objects"] == 1 and p["pixels"] == 1 and p["strip"] == {"objects": 1, "pixels": 1}
    assert "в полосе наблюдения — 1 объект" in p["label"]
    dr = by["drift.bali.2025-05-13"]
    assert "detection" not in dr["available_views"] and dr["detector"]["run"] is False
    assert "прежний режим не показывается" in dr["views"]["detection"]["reason"]
    assert dr["detector"]["label"].startswith("Детектор на этой сцене не запускался")
    r = client.get("/api/v3/studio/scenes/drift.bali.2025-05-13/view/detection.png")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NO_VIEW"
    ls = by["pair.S3_HE419_MarLitter_transect29"]
    assert ls["detector"]["run"] is False and "Sentinel-2" in ls["detector"]["label"]
    assert ls["quality"]["reason_label"] == "облачность в полосе"


def test_legacy_harmonized_pair_is_not_shown(client, tree):
    d = tree["pairs"] / "S4_DOORS3_T1"
    m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    m["detector"]["harmonize_offset"] = [0.001] * 11
    (d / "meta.json").write_text(json.dumps(m), encoding="utf-8")
    st._memo.clear()
    s = client.get("/api/v3/studio/scenes/pair.S4_DOORS3_T1").json()
    assert "detection" not in s["available_views"] and s["detector"]["run"] is False
    assert "прежним режимом" in s["views"]["detection"]["reason"]


def test_no_english_text_in_api(client):
    import re
    eng = re.compile(r"\b[A-Za-z]{3,}\s+[A-Za-z]{2,}\s+[A-Za-z]{3,}\b")
    bad = []

    def walk(x, key=""):
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, k)
        elif isinstance(x, list):
            for v in x:
                walk(v, key)
        elif isinstance(x, str) and key in ("label", "reason", "source", "note", "decision_label", "reason_label",
                                            "footprint_note", "source_label", "empty_reason", "message"):
            if eng.search(x):
                bad.append((key, x))
    walk(_scenes(client))
    for sid in ("live.bali.2025-05-11", "pair.S4_DOORS3_T1", "drift.bali.2025-05-13",
                "pair.S3_HE419_MarLitter_transect29"):
        walk(client.get(f"/api/v3/studio/scenes/{sid}").json())
    assert not bad, bad
    body = client.get("/api/v3/studio/scenes/live.bali.2025-05-11").text
    assert "ACOLITE" not in body and "trained on" not in body


def test_solar_zenith_reference_points():
    # ADIS 11.12.2021 10:56 UTC, North Sea 53.5N 4.6E: low winter sun (L94: «почти чёрный снимок»)
    assert abs(st.solar_zenith("2021-12-11T10:56:07Z", [4.5, 53.4, 4.7, 53.6]) - 76.9) < 1.0
    # equinox, local noon on the equator: sun near zenith
    assert st.solar_zenith("2024-03-20T12:07:00Z", [-0.1, -0.1, 0.1, 0.1]) < 3.0
    assert st.solar_zenith(None, [0, 0, 1, 1]) is None


def test_low_sun_detector_not_evaluated(client, tree):
    d = tree["pairs"] / "S4_DOORS3_T1"
    m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    m["scene_datetime"] = "2024-12-21T06:00:00Z"  # ~43.6N 29.5E, winter morning: zenith > 58
    (d / "meta.json").write_text(json.dumps(m), encoding="utf-8")
    st._memo.clear()
    s = client.get("/api/v3/studio/scenes/pair.S4_DOORS3_T1").json()
    assert s["illumination"]["low_sun"] is True and s["illumination"]["sun_zenith_deg"] >= st.SUN_ZENITH_MAX
    det = s["detector"]
    assert det["status"] == "not_evaluated" and det["objects"] is None and det["pixels"] is None
    assert det["raw"]["objects"] == 1 and det["label"].startswith("Детектор не оценивается: низкое солнце")
    assert "detection" not in s["available_views"] and "низкое солнце" in s["views"]["detection"]["reason"]
    r = client.get("/api/v3/studio/scenes/pair.S4_DOORS3_T1/view/detection.png")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NO_VIEW"


def test_weak_water_signal_not_evaluated(client, monkeypatch):
    monkeypatch.setattr(st, "WATER_B3_MIN", 0.5)  # synthetic water B3 ≈ 0.025 -> «weak» against this floor
    st._memo.clear()
    s = client.get("/api/v3/studio/scenes/live.bali.2025-05-11").json()
    assert s["illumination"]["weak_signal"] is True and 0.01 < s["illumination"]["water_b3_median"] < 0.04
    assert s["detector"]["status"] == "not_evaluated" and "слабый сигнал воды" in s["detector"]["label"]
    monkeypatch.setattr(st, "WATER_B3_MIN", 0.003)
    st._memo.clear()
    s = client.get("/api/v3/studio/scenes/live.bali.2025-05-11").json()
    assert s["detector"]["status"] == "evaluated" and s["illumination"]["weak_signal"] is False


def test_field_count_next_to_level(client, tree):
    (tree["search"] / "s9" / "candidates.csv").write_text(
        "event_id,scene_id,level,reason,field_count_by_size,survey_area_km2,dhat_5cm_km2\n"
        "S4:DOORS3:T1,S2A_35TQJ_20240602_0_L2A,C,кандидат,,,\n"
        "S9:E1,S2B_X,A,time <= 3 h,>5cm:3;>10cm:2;>50cm:0,0.5644,5.316\n", encoding="utf-8")
    st._memo.clear()
    s = client.get("/api/v3/studio/scenes/search.s9.pairs~E1__S2B_X").json()
    f = s["field"]
    assert s["level"] == "A" and f["count"] == 3 and f["size_class"] == "> 5 см"
    assert f["count_by_size"] == {"> 5 см": 3, "> 10 см": 2, "> 50 см": 0}
    assert f["survey_area_km2"] == 0.5644 and f["items_km2"] == 5.316
    assert f["label"].startswith("Полевой счёт: 3 предмета > 5 см на 0,56 км² обзора; оценка 5,3 шт./км²")
    assert all(not k.startswith("_") for r in s["level_records"] for k in r)


def test_rgb_water_stretch_default(client):
    j = client.get("/api/v3/studio/scenes/live.bali.2025-05-11").json()
    assert j["views"]["rgb"]["variants"] == ["water", "natural"]
    base = "/api/v3/studio/scenes/live.bali.2025-05-11/view/rgb.png"
    rw, rn = client.get(base), client.get(base, params={"variant": "natural"})
    assert "0..p98 water" in rw.headers["x-view-scale"] and "0.0..0.16" in rn.headers["x-view-scale"]
    w = np.asarray(_img(rw).convert("RGB")).astype(float)[20:, 20:].mean()
    n = np.asarray(_img(rn).convert("RGB")).astype(float)[20:, 20:].mean()
    assert w > n + 30  # dark sea water is brightened
