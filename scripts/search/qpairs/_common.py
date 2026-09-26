"""L105 (поиск пар для количества): общие пути и кэш HTTP. Сырьё — data/extra/qpairs/ (вне git)."""
from __future__ import annotations
import json, re, time, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "extra" / "qpairs"
UA = "curl/8.0 (L105 research)"


def slug(s: str, n: int = 90) -> str:
    return "".join(c if c.isalnum() else "_" for c in s)[:n]


def get_json(url: str, fn: Path, sleep: float = 0.5):
    """GET с кэшем в файле: повторный запуск читает fn, сеть не трогает."""
    fn.parent.mkdir(parents=True, exist_ok=True)
    if not fn.exists():
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        fn.write_bytes(urllib.request.urlopen(req, timeout=90).read())
        time.sleep(sleep)
    return json.loads(fn.read_text(encoding="utf-8"))


def oa_abstract(w: dict) -> str:
    ii = w.get("abstract_inverted_index") or {}
    return " ".join(k for _, k in sorted((p, k) for k, v in ii.items() for p in v))
