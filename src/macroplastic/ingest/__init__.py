"""Dataset ingest: archive/folder -> safe unpack -> human report -> auto adapter config -> internal format.

    python -m macroplastic.ingest <archive or folder> [--out data/ingest/<name>]           # report + adapter.yaml
    python -m macroplastic.ingest <archive or folder> [--out data/ingest/<name>] --convert # + internal format
    python scripts/train_lgbm_ingest.py --data-root data/ingest/<name>                     # LightGBM on it

Modules: unpack (safe extraction), formats (pairing / chip CSV / config generation / convert),
report (HTML + summary.json, re-uses scripts/tools/inspect_dataset.py).
"""
from .unpack import Limits, UnsafeArchiveError, check_member_name, prepare_input, safe_extract  # noqa: F401
