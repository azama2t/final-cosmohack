r"""Скачивает таблицы PANGAEA (формат tab) для S2/S3 и сохраняет сырьё в data/case/pangaea/.

Источники:
  931834 — метаданные трансект MSM41 (Gutow et al. 2021), CC BY 4.0  -> сегменты прерванных трансект S2
           (по ссылке textfile отдаётся xlsx «Transect meta data», сохраняется как .xlsx)
  931833 — объекты MSM41, CC BY 4.0
  890782 — трансекты HE419/HE460 (Gutow et al. 2018), CC BY 3.0      -> начало/конец S3
  890781 — объекты S3, CC BY 3.0

Вежливо: последовательно, один запрос за раз, до 5 ретраев с паузой. Уже скачанные файлы не перекачиваются
(--force для перекачки). Пишет data/case/pangaea/manifest.json (url, sha256, размер, дата загрузки, лицензия).

Запуск: .venv\Scripts\python.exe scripts\case\fetch_pangaea.py [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "case" / "pangaea"
DATASETS = {
    "931834": ("CC BY 4.0", "S2 MSM41 transect metadata (Gutow et al. 2021)"),
    "931833": ("CC BY 4.0", "S2 MSM41 floating litter objects (Gutow et al. 2021)"),
    "890782": ("CC BY 3.0", "S3 HE419/HE460 transects (Gutow et al. 2018)"),
    "890781": ("CC BY 3.0", "S3 floating litter objects (Gutow et al. 2018)"),
}
URL = "https://doi.pangaea.de/10.1594/PANGAEA.{}?format=textfile"


def fetch(url: str, retries: int = 5) -> bytes:
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, timeout=60, headers={"User-Agent": "final-cosmohack case geometry (research)"})
            if r.status_code == 200 and r.content:
                return r.content
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:  # noqa: PERF203
            last = repr(e)
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    mpath = OUT / "manifest.json"
    man = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {}
    for ds, (lic, title) in DATASETS.items():
        url = URL.format(ds)
        have = [OUT / f"PANGAEA_{ds}{ext}" for ext in (".tab", ".xlsx") if (OUT / f"PANGAEA_{ds}{ext}").exists()]
        p = have[0] if have else None
        if a.force or p is None:
            data = fetch(url)
            # 931834 публикуется как вложенный файл: ?format=textfile отдаёт исходный xlsx (сигнатура zip "PK")
            p = OUT / f"PANGAEA_{ds}{'.xlsx' if data[:2] == b'PK' else '.tab'}"
            p.write_bytes(data)
            man[ds] = {"downloaded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            time.sleep(1.0)
        data = p.read_bytes()
        man.setdefault(ds, {}).update({
            "file": p.relative_to(ROOT).as_posix(), "url": url, "doi": f"10.1594/PANGAEA.{ds}",
            "license": lic, "title": title, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        print(f"{ds}: {len(data):>8} B  sha256={man[ds]['sha256'][:16]}...  {lic}")
    mpath.write_text(json.dumps(man, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
