"""CLI: python -m macroplastic.organizer_adapter --config configs/adapter_marida.yaml --out data/organizer
[--limit N] [--split train] [--dry-run] [--verify-marida N]"""
from __future__ import annotations

import argparse
import json
import sys
import time

from .adapter import convert, list_samples, load_config, load_sample, verify_against_marida


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", default="data/organizer")
    ap.add_argument("--limit", type=int, default=None, help="convert only the first N samples")
    ap.add_argument("--split", default=None, help="convert only samples of this split")
    ap.add_argument("--workers", type=int, default=8, help="parallel threads for conversion")
    ap.add_argument("--dry-run", action="store_true", help="list samples + load the first one, write nothing")
    ap.add_argument("--verify-marida", type=int, default=0, metavar="N",
                    help="compare N random samples with macroplastic.data.marida.load_patch (MARIDA config only)")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    t0 = time.time()
    samples = list_samples(cfg)
    n_mask = sum(1 for s in samples if s.mask_path)
    splits = {}
    for s in samples:
        splits[s.split] = splits.get(s.split, 0) + 1
    print(f"[adapter] {cfg['name']}: {len(samples)} samples, {n_mask} with mask, splits={splits}, root={cfg['root']}")
    if not samples:
        print("[adapter] no samples found: check root / layout.image_glob / exclude_regex")
        return 2
    if a.dry_run:
        img, mask, names, grid = load_sample(cfg, samples[0])
        print(f"  first: {samples[0].id} img={img.shape} {img.dtype} bands={names} "
              f"min/med/max={float(__import__('numpy').nanmin(img)):.4f}/"
              f"{float(__import__('numpy').nanmedian(img)):.4f}/{float(__import__('numpy').nanmax(img)):.4f} "
              f"mask={None if mask is None else (mask.shape, sorted(set(mask.ravel().tolist())))} res={grid['res']}")
        return 0
    if a.verify_marida:
        r = verify_against_marida(cfg, a.verify_marida)
        print("[adapter] verify vs marida.load_patch:", json.dumps(r))
        if not r["identical"]:
            return 1
    man = convert(cfg, a.out, limit=a.limit, split=a.split, workers=a.workers)
    print(f"[adapter] wrote {man} in {time.time() - t0:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
