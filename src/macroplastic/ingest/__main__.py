"""python -m macroplastic.ingest <archive or folder> [--out data/ingest/<name>] [--convert]

Step 1 (always): safe unpack (archives) -> inspect -> format detection -> <out>/adapter.yaml (auto, with
         'ПРОВЕРЬ ЭТО' comments) -> <out>/report/index.html + summary.json.
Step 2 (--convert): <out>/adapter.yaml (possibly edited by a human) -> internal format
         <out>/converted/<name>/{images,masks}/*.tif + manifest.csv (+ group column).
Then:  python scripts/train_lgbm_ingest.py --data-root <out>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

if not os.environ.get("CUDA_VISIBLE_DEVICES"):  # PowerShell 5.1: $env:X="" deletes the variable -> use "-1"
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
REPO = Path(__file__).resolve().parents[3]
import warnings as _w
_w.filterwarnings("ignore", message=".*(geotransform|NotGeoreferenced).*")  # rasterio on PNG/plain TIFF: noise in PowerShell


def _name_from(src: Path) -> str:
    n = src.name
    for suf in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz", ".tar", ".zip", ".7z"):
        if n.lower().endswith(suf):
            n = n[: -len(suf)]
            break
    n = re.sub(r"[^A-Za-z0-9_.\-]+", "_", n).strip("._") or "dataset"
    if n.split(".")[0].lower() in {"con", "prn", "aux", "nul"}:
        n = "_" + n
    return n


def main(argv=None) -> int:
    from .unpack import Limits, UnsafeArchiveError, prepare_input

    ap = argparse.ArgumentParser(prog="python -m macroplastic.ingest", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="archive (zip / tar[.gz] / 7z) or folder")
    ap.add_argument("--out", default=None, help="output folder (default data/ingest/<name>)")
    ap.add_argument("--name", default=None, help="dataset name (default: from the archive/folder name)")
    ap.add_argument("--convert", action="store_true", help="write the internal format using <out>/adapter.yaml")
    ap.add_argument("--config", default=None, help="adapter YAML to use for --convert (default <out>/adapter.yaml)")
    ap.add_argument("--format", dest="force_format", default=None,
                    choices=["suffix", "dirs", "ids", "chip_csv"], help="force a format candidate")
    ap.add_argument("--regen", action="store_true", help="overwrite <out>/adapter.yaml even if edited")
    ap.add_argument("--limit", type=int, default=None, help="convert only the first N samples")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--sample-per-group", type=int, default=40)
    ap.add_argument("--max-files", type=int, default=20000)
    ap.add_argument("--max-unpacked-gb", type=float, default=60.0)
    ap.add_argument("--max-members", type=int, default=200000)
    g = ap.add_argument_group("overrides (priority: flag > organiser README > automatic guess)")
    g.add_argument("--target-class", type=int, default=None, help="organiser mask value = marine debris")
    g.add_argument("--ignore-values", default=None, help="comma mask values to ignore (e.g. 255 or -1); '' = none")
    g.add_argument("--channels", default=None, help="band names in FILE order, e.g. B8,B4,B3,B2,B11,B12,B5,B6,B7,B8A,B1")
    g.add_argument("--scale", type=float, default=None, help="reflectance = DN * scale + offset (e.g. 0.0001)")
    g.add_argument("--offset", type=float, default=None, help="e.g. -0.1 for L2A baseline >= 04.00 raw DN")
    g.add_argument("--test-glob", default=None, help="glob (relative to data root) of test images without masks")
    g.add_argument("--max-masks", type=int, default=20000, help="masks read for class values (stratified above)")
    ap.add_argument("--split", choices=["train", "test", "all"], default="all",
                    help="--convert: train (with masks), test (layout.test_glob, no masks) or all (default)")
    ap.add_argument("--force", action="store_true", help="--convert even if adapter.yaml still lists ingest_blockers")
    a = ap.parse_args(argv)
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    src = Path(a.src)
    name = a.name or _name_from(src)
    out = (Path(a.out) if a.out else REPO / "data" / "ingest" / name).resolve()
    out.mkdir(parents=True, exist_ok=True)
    cfg_path = Path(a.config) if a.config else out / "adapter.yaml"
    t0 = time.time()
    try:
        up = prepare_input(src, out, Limits(max_files=a.max_members, max_total_bytes=int(a.max_unpacked_gb * 1024 ** 3)))
    except UnsafeArchiveError as e:
        print(f"[ingest] ОТКАЗ: архив небезопасен или не поддержан: {e}", file=sys.stderr)
        (out / "REFUSED.txt").write_text(f"{src}\n{e}\n", encoding="utf-8")
        return 2
    print(f"[ingest] {up.kind}: {up.n_files} files, {up.n_bytes / 1e6:.1f} MB -> root {up.root} "
          f"({time.time() - t0:.1f}s)")

    stamp = out / ".adapter.auto.sha1"
    need_report = not (out / "report" / "summary.json").is_file() or not cfg_path.is_file() or not a.convert
    if need_report:
        from .report import build

        py = r".venv\Scripts\python.exe"
        nxt = f"{py} -m macroplastic.ingest {src} --out {out} --convert"
        ov = {"target_class": a.target_class, "channels": a.channels.split(",") if a.channels else None,
              "scale": a.scale, "offset": a.offset, "test_glob": a.test_glob, "max_masks": a.max_masks,
              "ignore_values": ([int(v) for v in a.ignore_values.split(",") if v.strip()]
                                if a.ignore_values is not None else None)}
        s = build(up.root, out, name, str(src), up.as_dict(), force_format=a.force_format,
                  sample_per_group=a.sample_per_group, max_files=a.max_files, config_path=cfg_path, next_command=nxt,
                  overrides=ov)
        text = s["config_text"]
        edited = cfg_path.is_file() and stamp.is_file() and \
            hashlib.sha1(cfg_path.read_bytes()).hexdigest() != stamp.read_text().strip()
        if edited and not a.regen:
            alt = out / "adapter.auto.yaml"
            alt.write_text(text, encoding="utf-8")
            print(f"[ingest] {cfg_path} was edited by hand -> kept; fresh auto config: {alt}")
        elif a.config is None or not cfg_path.is_file():
            cfg_path.write_text(text, encoding="utf-8")
            stamp.write_text(hashlib.sha1(cfg_path.read_bytes()).hexdigest(), encoding="utf-8")
        best = s["candidates"][0] if s["candidates"] else {}
        nb = sum(d["level"] == "blocker" for d in s["doubts"])
        print(f"[ingest] format: {best.get('format')} (score {best.get('score')}): {best.get('evidence')}")
        print(f"[ingest] doubts: {len(s['doubts'])} (blockers {nb}); report: {out / 'report' / 'index.html'}")
        for d in s["doubts"]:
            if d["level"] in ("blocker", "high"):
                print(f"   [{d['level']}] {d['topic']}: {d['text'][:160]}")
        for d in s.get("docs", [])[:3]:
            print(f"[ingest] --- {d['rel']} ({d['n_lines']} строк) ---")
            for ln in d["text"].splitlines()[:25]:
                print("   | " + ln)
        h = s.get("hints") or {}
        print(f"[ingest] из README: целевой класс={h.get('target')} каналы={h.get('bands')} scale={h.get('scale')}")
        mv = (s.get("config_info") or {}).get("mask_values")
        if mv:
            print(f"[ingest] значения масок (проверено {mv['n_checked']} из {mv['n_total']}): {mv['values']}")
        print(f"[ingest] config: {cfg_path}   ({time.time() - t0:.1f}s)")
        if nb:
            print(f"[ingest] БЛОКЕРОВ: {nb} — --convert не запустится, пока они не сняты (см. ingest_blockers в "
                  f"{cfg_path})")
    if a.convert:
        from .formats import check_blockers, convert

        bl = check_blockers(cfg_path)
        if bl and not a.force:
            print(f"[ingest] ОТКАЗ --convert: в {cfg_path} есть ingest_blockers:", file=sys.stderr)
            for b in bl:
                print("   - " + b, file=sys.stderr)
            print("   исправьте конфиг/флаги (--target-class, --channels, --scale) или добавьте --force", file=sys.stderr)
            return 3
        t1 = time.time()
        import yaml

        has_test = bool(((yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}).get("layout") or {})
                        .get("test_glob"))
        if a.split in ("test", "all") and has_test:
            tman = convert(cfg_path, out / "converted_test", limit=a.limit, workers=a.workers, split="test")
            import csv as _csv

            nt = sum(1 for _ in _csv.DictReader(open(tman, encoding="utf-8")))
            print(f"[ingest] test (без масок): {nt} снимков -> {tman}")
        elif a.split == "test":
            print("[ingest] layout.test_glob пуст: задайте --test-glob или используйте predict_org.py --images",
                  file=sys.stderr)
            return 2
        if a.split == "test":
            return 0
        man = convert(cfg_path, out / "converted", limit=a.limit, workers=a.workers)
        import csv

        rows = list(csv.DictReader(open(man, encoding="utf-8")))
        n_md = sum(int(r.get("n_debris_px") or 0) for r in rows)
        n_lab = sum(int(r.get("n_labelled_px") or 0) for r in rows)
        info = {"manifest": str(man), "n_samples": len(rows), "n_labelled_px": n_lab, "n_target_px": n_md,
                "n_groups": len({r.get("group") for r in rows}), "seconds": round(time.time() - t1, 1)}
        (out / "convert_summary.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
        print(f"[ingest] converted {len(rows)} samples, labelled px {n_lab}, target(1) px {n_md} -> {man} "
              f"({info['seconds']}s)")
        print(f"[ingest] next: .venv\\Scripts\\python.exe scripts\\train_lgbm_ingest.py --data-root {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
