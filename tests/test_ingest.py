"""Tests for lane L18: python -m macroplastic.ingest (safe unpack, format detection, convert, training)."""
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


def _run_all(src: Path, out: Path, rounds=20):
    from macroplastic.ingest.__main__ import main

    assert main([str(src), "--out", str(out), "--sample-per-group", "8"]) == 0
    s = json.loads((out / "report" / "summary.json").read_text(encoding="utf-8"))
    assert (out / "report" / "index.html").is_file() and (out / "adapter.yaml").is_file()
    assert "Сомнения" in (out / "report" / "index.html").read_text(encoding="utf-8")
    assert main([str(src), "--out", str(out), "--convert", "--workers", "2"]) == 0
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
        s, meta = _run_all(tp, tmp_path / "t2")
        assert s["candidates"][0]["format"] == "dirs"
        assert meta["band_fill"] == {"B1": "B2", "B8": "B8A"}
