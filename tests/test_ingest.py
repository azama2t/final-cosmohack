"""Tests for lanes L18 + L32: python -m macroplastic.ingest (safe unpack, format detection, README hints, all-mask class values, blockers, convert train+test), train_lgbm_ingest --cv, predict_org, score, forensics/provenance pairing."""
from __future__ import annotations

import csv
import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
FIRE_BS = Path(r"C:\Users\User\hack\fire-monitoring\data\train\bs")


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --------------------------------------------------------------------------- safety
@pytest.mark.parametrize("bad", ["../evil.txt", "a/../../evil.txt", "/etc/passwd", "\\\\server\\share\\x",
                                 "C:/Windows/x.txt", "c:evil.txt", "aux.txt", "data/CON", "x/nul.tif",
                                 "lpt1", "a:stream.txt", "dir /x.tif", "trail./x"])
def test_member_name_rejected(bad):
    from macroplastic.ingest import UnsafeArchiveError, check_member_name

    with pytest.raises(UnsafeArchiveError):
        check_member_name(bad)


def test_member_name_ok():
    from macroplastic.ingest import check_member_name

    assert str(check_member_name("./a/b.tif")) == "a/b.tif"
    assert str(check_member_name("a\\b\\auxiliary.tif")) == "a/b/auxiliary.tif"


def test_zip_slip_refused(tmp_path):
    from macroplastic.ingest import UnsafeArchiveError, safe_extract

    zp = tmp_path / "evil.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("ok/readme.txt", "fine")
        zf.writestr("../evil.txt", "escaped")
    dest = tmp_path / "sub" / "out"
    with pytest.raises(UnsafeArchiveError, match="traversal"):
        safe_extract(zp, dest)
    assert not (tmp_path / "sub" / "evil.txt").exists() and not (tmp_path / "evil.txt").exists()
    assert not dest.exists()  # nothing half-written


def test_cli_refuses_zip_slip(tmp_path):
    from macroplastic.ingest.__main__ import main

    zp = tmp_path / "evil.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("../evil.txt", "escaped")
    assert main([str(zp), "--out", str(tmp_path / "o")]) == 2
    assert (tmp_path / "o" / "REFUSED.txt").is_file() and not (tmp_path / "evil.txt").exists()


def _tar_with(tmp_path, member: tarfile.TarInfo, data: bytes = b"") -> Path:
    tp = tmp_path / "x.tar.gz"
    with tarfile.open(tp, "w:gz") as tf:
        ti = tarfile.TarInfo("ok.txt")
        ti.size = 2
        tf.addfile(ti, io.BytesIO(b"ok"))
        tf.addfile(member, io.BytesIO(data) if data else None)
    return tp


def test_tar_symlink_and_abs_refused(tmp_path):
    from macroplastic.ingest import UnsafeArchiveError, safe_extract

    ln = tarfile.TarInfo("link")
    ln.type = tarfile.SYMTYPE
    ln.linkname = "/etc/passwd"
    with pytest.raises(UnsafeArchiveError, match="symlink"):
        safe_extract(_tar_with(tmp_path, ln), tmp_path / "o1")
    ab = tarfile.TarInfo("/abs.txt")
    ab.size = 1
    with pytest.raises(UnsafeArchiveError, match="absolute"):
        safe_extract(_tar_with(tmp_path, ab, b"x"), tmp_path / "o2")


def test_zip_case_duplicates_and_bomb_refused(tmp_path):
    from macroplastic.ingest import Limits, UnsafeArchiveError, safe_extract

    zp = tmp_path / "dup.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("A.tif", "1")
        zf.writestr("a.tif", "2")
    with pytest.raises(UnsafeArchiveError, match="duplicate"):
        safe_extract(zp, tmp_path / "o1")
    bp = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("zeros.bin", b"\0" * (20 * 1024 * 1024))
    with pytest.raises(UnsafeArchiveError, match="ratio"):
        safe_extract(bp, tmp_path / "o2")
    with pytest.raises(UnsafeArchiveError, match="limit"):
        safe_extract(bp, tmp_path / "o3", Limits(max_ratio=1e9, max_total_bytes=1024))


def test_mask_rule_inference():
    from macroplastic.ingest.formats import infer_mask_rule, scene_group
    import re

    r = infer_mask_rule([("s/x_1.tif", "s/x_1_label.tif"), ("t/y_2.tif", "t/y_2_label.tif")])
    assert r and re.sub(r["regex"], r["replace"], "/s/x_1.tif") == "/s/x_1_label.tif"
    r = infer_mask_rule([("d/images/a.tif", "d/masks/a.png"), ("d/images/b.tif", "d/masks/b.png")])
    assert r and re.sub(r["regex"], r["replace"], "/d/images/b.tif") == "/d/masks/b.png"
    r = infer_mask_rule([("a_img.tif", "a_mask.tif"), ("b_img.tif", "b_mask.tif")])
    assert r and re.sub(r["regex"], r["replace"], "/b_img.tif") == "/b_mask.tif"
    assert scene_group("S2_1-12-19_48MYU_0") == "1-12-19_48MYU"
    assert scene_group("BS_tr_000001") == "BS_tr"


# --------------------------------------------------------------------------- end to end (synthetic, no external data)
BANDS = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"]


def _synthetic_zip(tmp_path: Path, n_scenes=4, per_scene=3, size=48) -> Path:
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.transform import from_origin

    rng = np.random.default_rng(0)
    zp = tmp_path / "org_data.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
        for s in range(n_scenes):
            for i in range(per_scene):
                water = np.array([400, 350, 300, 200, 150, 130, 120, 100, 90, 60, 50, 40], np.float32)
                img = water[:, None, None] * (1 + 0.1 * rng.standard_normal((len(BANDS), size, size))) + 1000
                m = np.zeros((size, size), np.uint8)
                yy, xx = rng.integers(4, size - 8, 2)
                m[yy:yy + 4, xx:xx + 4] = 1
                img[7:9, m == 1] += 900  # NIR bright debris (DN, before the +1000 offset)
                img[:, :2, :] = 0  # nodata stripe
                m[:2, :] = 255
                tf = from_origin(500000 + 1000 * s, 1700000 + 1000 * i, 10, 10)
                prof = dict(driver="GTiff", height=size, width=size, count=len(BANDS), dtype="uint16",
                            crs="EPSG:32616", transform=tf)
                name = f"tiles/S2_2022060{s + 1}_T16PCC_{i:03d}"
                with MemoryFile() as mf:
                    with mf.open(**prof) as ds:
                        ds.write(np.clip(img, 0, 65535).astype(np.uint16))
                        ds.descriptions = tuple(f"B{b[1:].zfill(2)}" if b != "B8A" else "B8A" for b in BANDS)
                    zf.writestr(name + ".tif", mf.read())
                with MemoryFile() as mf:
                    with mf.open(**{**prof, "count": 1, "dtype": "uint8", "nodata": 255}) as ds:
                        ds.write(m[None])
                    zf.writestr(name + "_mask.tif", mf.read())
    return zp


def _run_all(src: Path, out: Path, rounds=20, extra=(), convert_extra=()):
    from macroplastic.ingest.__main__ import main

    assert main([str(src), "--out", str(out), "--sample-per-group", "8", *extra]) == 0
    s = json.loads((out / "report" / "summary.json").read_text(encoding="utf-8"))
    assert (out / "report" / "index.html").is_file() and (out / "adapter.yaml").is_file()
    assert "Сомнения" in (out / "report" / "index.html").read_text(encoding="utf-8")
    assert main([str(src), "--out", str(out), "--convert", "--workers", "2", *convert_extra]) == 0
    tr = _load_script("train_lgbm_ingest")
    meta = tr.main(["--data-root", str(out), "--out", str(out / "model"), "--set", f"lgbm.num_boost_round={rounds}", "lgbm.num_leaves=15"])
    return s, meta


def test_end_to_end_synthetic(tmp_path):
    zp = _synthetic_zip(tmp_path)
    s, meta = _run_all(zp, tmp_path / "ing")
    assert s["candidates"][0]["format"] == "suffix"
    text = s["config_text"]
    assert "B8A" in text and "offset: -0.1" in text  # DN with +1000 offset detected (L2A >= 04.00)
    assert any(d["topic"] == "смещение L2A" for d in s["doubts"])
    assert any(d["topic"] == "класс 0" for d in s["doubts"])  # {0,1} mask: is 0 background or unlabelled?
    rows = list(csv.DictReader(open(meta["manifest"], encoding="utf-8")))
    assert len(rows) == 12 and len({r["group"] for r in rows}) == 4  # scene = date + tile
    assert meta["val"]["f1"] > 0.5 and "split" in meta and "group split" in meta["split"]
    import rasterio

    with rasterio.open(rows[0]["image"]) as ds:
        a = ds.read()
        assert ds.dtypes[0] == "float32" and np.isnan(a[:, :2]).all() and np.nanmedian(a[7]) < 0.05


# --------------------------------------------------------------------------- the 3 artificial organiser sets
def test_testsets_marida_fire_chips(tmp_path, marida_root):
    mk = _load_script("make_ingest_testsets")
    zp = mk.make_marida_zip(tmp_path, 8, 0)
    s, meta = _run_all(zp, tmp_path / "t1")
    assert s["candidates"][0]["format"] == "suffix" and "_label" in s["config_text"]
    assert "scale: 0.0001" in s["config_text"] and meta["n_train_target_px"] > 0

    cd = mk.make_chips_csv(tmp_path, 12, 0)
    s, meta = _run_all(cd, tmp_path / "t3")
    assert s["candidates"][0]["format"] == "chip_csv" and "chip" in meta["val"]

    if FIRE_BS.is_dir():
        tp = mk.make_fire_targz(tmp_path, 8, 0)
        # burn severity: no "debris" class -> L32 blocker "target class not named"; acknowledged with --force
        s, meta = _run_all(tp, tmp_path / "t2", convert_extra=("--force",))
        assert any(d["level"] == "blocker" and d["topic"] == "целевой класс" for d in s["doubts"])
        assert s["candidates"][0]["format"] == "dirs"
        assert meta["band_fill"] == {"B1": "B2", "B8": "B8A"}


# --------------------------------------------------------------------------- L32: organiser pipeline (rehearsal-like set)
ORG_ORDER = ["B8", "B4", "B3", "B2", "B11", "B12", "B5", "B6", "B7", "B8A", "B1"]
README_TXT = ("Dataset: Sentinel-2 chips, GeoTIFF uint16 (DN, reflectance x 10000).\n"
              "Band order: " + ", ".join(ORG_ORDER) + ".\n"
              "Mask classes (PNG, uint8): 0 - background, 1 - water, 2 - other floating objects, 3 - marine debris.\n"
              "Metric: F1 score of class 3 over all pixels.\n")


def _org_folder(root: Path, n_scenes=5, per_scene=4, n_test_scenes=2, size=40, readme=True) -> Path:
    """Rehearsal-like organiser set: train/images/r<k>_<n>.tif + train/masks/r<k>_<n>.png (0..3, debris = 3 only in
    the LAST scenes -> a one-group sample would miss it), test/images/*.tif, README.txt."""
    import rasterio
    from PIL import Image

    rng = np.random.default_rng(1)
    water = {"B1": 400, "B2": 350, "B3": 300, "B4": 200, "B5": 150, "B6": 130, "B7": 120, "B8": 100, "B8A": 90,
             "B11": 50, "B12": 40}
    for split, scenes in (("train", range(n_scenes)), ("test", range(n_scenes, n_scenes + n_test_scenes))):
        (root / split / "images").mkdir(parents=True, exist_ok=True)
        if split == "train":
            (root / split / "masks").mkdir(parents=True, exist_ok=True)
        for k in scenes:
            for n in range(1, per_scene + 1):
                img = np.stack([np.full((size, size), water[b], np.float32) for b in ORG_ORDER])
                img *= 1 + 0.05 * rng.standard_normal(img.shape)
                m = np.zeros((size, size), np.uint8)
                m[20:, :] = 1  # labelled water
                m[2:5, 2:5] = 2
                if k >= n_scenes - 2 or split == "test":
                    y, x = rng.integers(6, size - 6, 2)
                    m[y:y + 3, x:x + 3] = 3
                    img[ORG_ORDER.index("B8"), m == 3] += 1500
                    img[ORG_ORDER.index("B8A"), m == 3] += 1400
                name = f"r{k:02d}_{n:03d}"
                prof = dict(driver="GTiff", height=size, width=size, count=11, dtype="uint16")
                with rasterio.open(root / split / "images" / f"{name}.tif", "w", **prof) as ds:
                    ds.write(np.clip(img, 1, 65535).astype(np.uint16))
                if split == "train":
                    Image.fromarray(m).save(root / split / "masks" / f"{name}.png")
                else:
                    (root.parent / "hidden").mkdir(exist_ok=True)
                    Image.fromarray(m).save(root.parent / "hidden" / f"{name}.png")
    if readme:
        (root / "README.txt").write_text(README_TXT, encoding="utf-8")
    return root


def test_parse_hints_readme():
    from macroplastic.ingest.formats import parse_hints

    h = parse_hints([{"rel": "README.txt", "text": README_TXT, "n_lines": 4}], 11)
    assert h["target"] == 3 and h["bands"] == ORG_ORDER and h["scale"] == 1e-4
    assert h["classes"][0] == "background" and "F1" in h["metric_line"]
    h2 = parse_hints([{"rel": "a.md", "text": "Classes: water = 1, plastic litter = 5, foam = 2", "n_lines": 1}])
    assert h2["target"] == 5


def test_scene_prefix_one_group_and_pairs_within_scene(tmp_path):
    ins = _load_script("tools/inspect_dataset")
    assert ins.name_template("r05_001")[0] == ins.name_template("r12_003")[0] == "r{n}_{n}"
    assert ins.name_template("S2_20220601_T16PCC_001")[0] == "S2_{date}_{tile}_{n}"  # sensor token stays literal
    assert ins.name_template("x_B02")[0] == "x_B02"  # band token stays literal
    root = _org_folder(tmp_path / "org")
    s = ins.run(root, tmp_path / "ins", sample_per_group=4)
    img_groups = [g for g in s["groups"] if g["group"].startswith("train/images")]
    assert len(img_groups) == 1 and img_groups[0]["n_files"] == 20
    rows = list(csv.DictReader(open(tmp_path / "ins" / "pairs_guess.csv", encoding="utf-8")))
    assert len(rows) == 20 and all(Path(r["image"]).stem == Path(r["mask"]).stem for r in rows)
    assert len({r["group"] for r in rows}) == 5  # scene groups for forensics folds


def test_ingest_org_readme_all_masks_test_convert(tmp_path):
    from macroplastic.ingest.__main__ import main

    root = _org_folder(tmp_path / "org")
    out = tmp_path / "ing"
    assert main([str(root), "--out", str(out), "--sample-per-group", "4"]) == 0
    s = json.loads((out / "report" / "summary.json").read_text(encoding="utf-8"))
    mv = s["config_info"]["mask_values"]
    assert mv["n_checked"] == mv["n_total"] == 20 and 3 in {int(k) for k in mv["values"]}
    assert s["hints"]["target"] == 3 and not [d for d in s["doubts"] if d["level"] == "blocker"]
    import yaml

    cfg = yaml.safe_load((out / "adapter.yaml").read_text(encoding="utf-8"))
    assert cfg["classes"]["map"] == {0: 99, 3: 1, 1: 7, 2: 7}
    assert cfg["bands"]["source"] == ORG_ORDER and cfg["layout"]["test_glob"] == "test/**/*.tif"
    assert "README" in (out / "report" / "index.html").read_text(encoding="utf-8")
    assert main([str(root), "--out", str(out), "--convert", "--workers", "2"]) == 0
    tm = list(csv.DictReader(open(next((out / "converted_test").glob("*/manifest.csv")), encoding="utf-8")))
    assert len(tm) == 8 and all(not r["mask"] for r in tm)
    tr = list(csv.DictReader(open(next((out / "converted").glob("*/manifest.csv")), encoding="utf-8")))
    assert len(tr) == 20 and sum(int(r["n_debris_px"]) for r in tr) == 8 * 9


def test_ingest_blocker_without_readme_and_cli_override(tmp_path):
    from macroplastic.ingest.__main__ import main

    root = _org_folder(tmp_path / "org", readme=False)
    out = tmp_path / "ing"
    assert main([str(root), "--out", str(out), "--sample-per-group", "4"]) == 0
    s = json.loads((out / "report" / "summary.json").read_text(encoding="utf-8"))
    assert any(d["level"] == "blocker" and d["topic"] == "целевой класс" for d in s["doubts"])
    assert main([str(root), "--out", str(out), "--convert"]) == 3  # refused: target class not named
    assert main([str(root), "--out", str(out), "--target-class", "3", "--channels", ",".join(ORG_ORDER),
                 "--scale", "0.0001", "--offset", "0", "--sample-per-group", "4"]) == 0
    s = json.loads((out / "report" / "summary.json").read_text(encoding="utf-8"))
    assert not [d for d in s["doubts"] if d["level"] == "blocker"] and s["overrides"]["target_class"] == 3
    assert "из --channels" in (out / "adapter.yaml").read_text(encoding="utf-8")


def test_train_cv_predict_org_score(tmp_path):
    from macroplastic.ingest.__main__ import main

    root = _org_folder(tmp_path / "org")
    out = tmp_path / "ing"
    assert main([str(root), "--out", str(out), "--sample-per-group", "4"]) == 0
    assert main([str(root), "--out", str(out), "--convert", "--workers", "2"]) == 0
    tr = _load_script("train_lgbm_ingest")
    meta = tr.main(["--data-root", str(out), "--cv", "2", "--zero-as", "both", "--baseline", str(REPO / "weights" / "lgbm"),
                    "--out", str(tmp_path / "m"), "--set", "lgbm.num_boost_round=30", "lgbm.num_leaves=7"])
    v = meta["cv"]["variants"]
    assert {"train:negative", "train:ignore"} <= set(v) and any(k.startswith("baseline:") for k in v)
    assert meta["zero_as"] in ("negative", "ignore") and 0 < meta["threshold"] < 1
    assert (tmp_path / "m" / "cv.json").is_file() and v["train:" + meta["zero_as"]]["f1"] > 0.5
    po = _load_script("tools/predict_org")
    sub = tmp_path / "sub"
    assert po.main(["--config", str(out / "adapter.yaml"), "--weights", str(tmp_path / "m"), "--out", str(sub),
                    "--value-debris", "3", "--value-bg", "0", "--zip", "--prob"]) == 0
    names = sorted(p.name for p in sub.glob("*.png"))
    assert names == sorted(p.stem + ".png" for p in (root / "test" / "images").glob("*.tif"))
    from PIL import Image

    a = np.array(Image.open(sub / names[0]))
    assert a.dtype == np.uint8 and a.shape == (40, 40) and set(np.unique(a)) <= {0, 3}
    import zipfile as zf

    assert sorted(zf.ZipFile(str(sub) + ".zip").namelist()) == names
    sc = _load_script("tools/score")
    r = sc.score(str(sub), str(root.parent / "hidden"), 3)
    assert r["n_files"] == 8 and r["n_missing_pred"] == 0 and r["target_metrics"]["f1"] > 0.5
    r_self = sc.score(str(root.parent / "hidden"), str(root.parent / "hidden"), 3, zero_as="ignore")
    assert r_self["target_metrics"]["f1"] == 1.0 and 0 in r_self["ignored_gt_values"]
    # missing model bands -> clear error (exit 2), not silent empty masks
    import yaml

    cfg = yaml.safe_load((out / "adapter.yaml").read_text(encoding="utf-8"))
    cfg["bands"]["output"] = ["B2", "B3", "B4", "B8"]
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    assert po.main(["--config", str(bad), "--weights", str(REPO / "weights" / "lgbm"), "--out", str(tmp_path / "s2")]) == 2


def test_forensics_pairs_do_not_collide_across_scenes(tmp_path):
    lf = _load_script("tools/label_forensics")
    root = _org_folder(tmp_path / "org")
    import argparse

    a = argparse.Namespace(manifest=None, pairs_csv=None, images=[str(root / "train" / "images")],
                           masks=str(root / "train" / "masks"), exclude_regex=None, id_regex=None, split_lists=None,
                           meta_csv=None, group_regex=r"^(r\d+)_", filter=None)
    rows = lf.build_pairs(a)
    assert len(rows) == 20 and all(Path(r["images"][0]).stem == Path(r["mask"]).stem for r in rows)
    assert len({r["group"] for r in rows}) == 5


def test_provenance_skips_mask_folders():
    import re

    pc = _load_script("tools/provenance_check")
    rx = re.compile(pc.DEFAULT_EXCLUDE, re.I)
    assert rx.search("org/train/masks/r01_001.png") and rx.search("x/S2_1-12-19_48MYU_0_cl.tif")
    assert not rx.search("org/train/images/r01_001.tif")
