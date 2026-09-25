"""L90: registry of new negatives (and checked-but-rejected sources) + leakage check by Sentinel-2 tile + date.

Inputs: data/extra/registry_vessels_fi.csv, data/extra/registry_clouds_cmc.csv (written by l90_vessels_fi.py /
l90_clouds_cmc.py) and the list of checked sources below (REJECTED).
Protected / known scenes (tile, date):
  MARIDA  data/MARIDA/patches/S2_<d-m-yy>_<TILE>  + split of every patch (splits/*_X.txt) -> train / val / test
  MADOS   reports/mados_overlap.csv (tile/date only where content-matched to MARIDA; MADOS files carry no tile/date)
  live    data/live/<region>/<date>/scene.json (service scenes, used for demo and visual checks)
  pairs   data/pairs/pair_quality.csv scene_id (case event <-> image pairs)
  other new sets: data/extra/registry.csv (L89: PLP, FloatingObjects), data/extra/registry_cozar2024.csv (L98)
Rule: same tile AND same date = same acquisition -> 'same_acq:<set>' (leak if the other set is val/test);
same tile, other date -> 'same_tile:<set>' (place overlap, no shared pixels).

Output: data/extra/registry_negatives.csv (+ copy reports/extra_data/registry_negatives.csv), summary json on stdout.
"""
from __future__ import annotations

import collections
import csv
import json
import re
import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXTRA = ROOT / "data" / "extra"
REP = ROOT / "reports" / "extra_data"
TILE_RE = re.compile(r"(?<![0-9A-Z])T?(\d{2}[C-X][A-HJ-NP-Z]{2})(?![A-Z])")

REJECTED = [
    ("S2-SHIPS (Ciocarlan & Stoian 2021; Zenodo 3923841 / Google Drive из README)", "судно", "—",
     "Zenodo: файлы restricted; Google Drive: HTTP 404", "https://zenodo.org/records/3923841"),
    ("Sentinel-2 dataset for ship detection and characterization (Zenodo 10418786)", "судно", "CC-BY-4.0",
     "скачан (66 МБ): только RGB PNG 1098×1098 + CSV X/Y в пикселях, имена без тайла/геопривязки → 11 каналов не получить",
     "https://zenodo.org/records/10418786"),
    ("Sentinel2-Ship и SDGSAT-Ship (Zenodo 11079267)", "судно", "CC-BY-4.0",
     "оглавление zip по HTTP Range: 906 PNG + повёрнутые рамки, имена анонимные → без каналов и координат",
     "https://zenodo.org/records/11079267"),
    ("Ship-S2-AIS (HF isaaccorley/ships-s2-ais, AGENIUM Space)", "судно", "собственная лицензия AGENIUM (PDF) / apache-2.0 в карточке HF",
     "архив 2.2 ГБ (tar.gz внутри zip) > бюджета 2 ГБ; лицензия противоречива", "https://huggingface.co/datasets/isaaccorley/ships-s2-ais"),
    ("Floating-Marine-Debris-Data (Duarte & Azevedo 2023, GitHub)", "пена/пемза/«морские сопли»/водоросли/дерево", "CC-BY-4.0",
     "CSV в Git LFS: «Git LFS is disabled for this repository» (403); к тому же отдельные пиксели без окна (уровень win невозможен)",
     "https://github.com/miguelmendesduarte/Floating-Marine-Debris-Data"),
    ("sargassum-satellite-ml (aquab1t, Zenodo 17246345)", "саргассум", "не указана",
     "sargassum_data.csv: 196 037 пикселей, 5 каналов (Blue..SWIR1), без сцены/координат → 11 каналов и окно невозможны",
     "https://github.com/aquab1t/sargassum-satellite-ml"),
    ("ShipWakes keypoints (Del Prete 2023, Zenodo 7947694)", "кильватер", "CC-BY-4.0",
     "только B8 после масштабирования+CLAHE, ключевые точки; не отражательная способность", "https://zenodo.org/records/7947694"),
    ("xS2Wakes (Zenodo 10018939)", "кильватер", "см. запись",
     "L2A после CLAHE (обработанные изображения), Датские фьорды; не отражательная способность — признаки несопоставимы",
     "https://zenodo.org/records/10018939"),
    ("Ulva prolifera, Жёлтое море (Zenodo 21502433)", "водоросли (зелёный прилив)", "CC-BY-4.0",
     "5 сцен S2 L1C (SNAP, .rar 5–497 МБ) без разметки — только снимки; своя разметка дала бы уровень C, не D",
     "https://zenodo.org/records/21502433"),
    ("Cloud Mask Catalogue: subscenes.zip (L1C TOA, 15.2 ГБ)", "облако", "CC-BY-4.0",
     "не качали: > бюджета; взяты только маски+контуры (9 МБ), пиксели — L2A той же съёмки", "https://zenodo.org/records/4172871"),
    ("«Marine Debris Archive»","мусор", "CC-BY-4.0", "это MARIDA (MARIne Debris Archive) — уже есть, дубль", "data/MARIDA"),
    ("MADOS (нефть, пена, суда)", "нефть/пена/судно", "CC-BY-4.0", "уже есть (L13), по заданию не дублировать", "data/MADOS"),
    ("PLP, FloatingObjects", "мусор/мишени", "CC-BY-4.0", "у L89 (не пересекаемся)", "data/extra/registry.csv"),
]


def _date_marida(d: str) -> str:
    dd, mm, yy = d.split("-")
    return f"20{int(yy):02d}-{int(mm):02d}-{int(dd):02d}"


def protected():
    """dict tile -> list of (date, set_name)."""
    idx = collections.defaultdict(set)
    split = {}
    for s in ("train", "val", "test"):
        f = ROOT / "data" / "MARIDA" / "splits" / f"{s}_X.txt"
        for ln in f.read_text().splitlines():
            if ln.strip():
                split[ln.strip().rsplit("_", 1)[0]] = split.get(ln.strip().rsplit("_", 1)[0], set()) | {s}
    for p in (ROOT / "data" / "MARIDA" / "patches").iterdir():
        m = re.match(r"S2_(\d{1,2}-\d{1,2}-\d{2})_(\d{2}[A-Z]{3})", p.name)
        if m:
            key = p.name[3:]
            for s in sorted(split.get(key, {"?"})):
                idx[m.group(2)].add((_date_marida(m.group(1)), f"MARIDA_{s}"))
    mo = pd.read_csv(ROOT / "reports" / "mados_overlap.csv")
    for _, r in mo.dropna(subset=["tile"]).iterrows():
        idx[r["tile"]].add((str(r["date"])[:10], "MADOS"))
    for sj in (ROOT / "data" / "live").glob("*/*/scene.json"):
        j = json.loads(sj.read_text(encoding="utf-8"))
        if j.get("tile"):
            idx[j["tile"]].add((j["date"][:10], "live"))
    pq = ROOT / "data" / "pairs" / "pair_quality.csv"
    if pq.is_file():
        for sid in pd.read_csv(pq)["scene_id"].dropna().unique():
            s = str(sid)
            if not s.startswith("S2"):
                continue
            m = TILE_RE.search(s.split("_", 1)[1])
            d = re.search(r"(20\d{2})(\d{2})(\d{2})", s)
            if m and d:
                idx[m.group(1)].add((f"{d.group(1)}-{d.group(2)}-{d.group(3)}", "pairs"))
    for f, name in ((EXTRA / "registry.csv", "L89"), (EXTRA / "registry_cozar2024.csv", "L98_cozar")):
        if f.is_file():
            r = pd.read_csv(f, usecols=["tile", "date"]).dropna()
            for t, d in r.itertuples(index=False):
                idx[str(t)].add((str(d)[:10], name))
    return idx


def check(idx, tile: str, date: str):
    same_acq = sorted({s for d, s in idx.get(tile, ()) if d == date})
    same_tile = sorted({s for d, s in idx.get(tile, ()) if d != date})
    return ";".join(same_acq), ";".join(same_tile)


def main():
    idx = protected()
    rows = []
    for f in ("registry_vessels_fi.csv", "registry_clouds_cmc.csv"):
        p = EXTRA / f
        if not p.is_file():
            print("missing", p)
            continue
        for r in csv.DictReader(open(p, encoding="utf-8")):
            acq, tl = check(idx, r["tile"], r["date"])
            rows.append(dict(source=r["source"], scene=r["scene"], tile=r["tile"], date=r["date"], geometry=r["geometry"],
                             klass=r["klass"], level=r["level"], n_px=r["n_px"], license=r["license"],
                             status="принят (признаки посчитаны)", same_acquisition=acq or "нет", same_tile_other_date=tl or "нет",
                             leak="ДА" if any(k in acq for k in ("val", "test", "live", "pairs")) else "нет",
                             qc=(f"видимый корпус B8_dmed15>0.01: {r['visible_hull']}" if "visible_hull" in r
                                 else f"совмещение маски r={r.get('align_r')}"),
                             link="https://zenodo.org/records/15019034" if "finland" in r["source"]
                             else "https://zenodo.org/records/4172871"))
    for name, klass, lic, reason, link in REJECTED:
        rows.append(dict(source=name, scene="—", tile="—", date="—", geometry="—", klass=klass, level="—", n_px=0,
                         license=lic, status=f"отклонён: {reason}", same_acquisition="—", same_tile_other_date="—",
                         leak="—", qc="—", link=link))
    out = EXTRA / "registry_negatives.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    REP.mkdir(parents=True, exist_ok=True)
    shutil.copy(out, REP / "registry_negatives.csv")
    acc = [r for r in rows if r["status"].startswith("принят")]
    summ = dict(rows=len(rows), accepted_rows=len(acc), rejected_sources=len(REJECTED),
                tile_dates=len({(r["tile"], r["date"]) for r in acc}),
                same_acq=collections.Counter(r["same_acquisition"] for r in acc),
                same_tile=collections.Counter(r["same_tile_other_date"] for r in acc),
                leak=sum(r["leak"] == "ДА" for r in acc),
                by_class=collections.Counter(r["klass"] for r in acc))
    print(json.dumps(summ, ensure_ascii=False, default=str, indent=1))


if __name__ == "__main__":
    sys.exit(main())
