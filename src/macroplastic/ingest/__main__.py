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

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
REPO = Path(__file__).resolve().parents[3]


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
        s = build(up.root, out, name, str(src), up.as_dict(), force_format=a.force_format,
                  sample_per_group=a.sample_per_group, max_files=a.max_files, config_path=cfg_path, next_command=nxt)
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
        print(f"[ingest] config: {cfg_path}   ({time.time() - t0:.1f}s)")
    if a.convert:
        from .formats import convert

        t1 = time.time()
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
