"""Format recognition (image<->mask pairing, chip CSV) and automatic adapter-config generation.

Recognised layouts (a candidate list with scores is produced, the best one drives the YAML):
  a) "suffix":   chips + masks next to them / in a sibling folder, mask name = image name + suffix
                 (_mask, _label, _cl, _gt, _lbl, _seg ...), e.g. x_0.tif + x_0_label.tif;
  b) "dirs":     images/ and masks/ (labels/, gt/, annotations/ ...) with the same file names;
  c) "chip_csv": image chips + a CSV "chip -> class/value" (chip-level classification, no masks):
                 the mask becomes the chip class on every valid pixel (flag chip_level in manifest).
  (fallback)     "ids": pairs found only by a shared numeric id in the name (e.g. X_000001_S2.tif +
                 X_000001_MASK.tif) -> adapter mask_glob + id regexes.
Everything uncertain is written to a list of "doubts" (never silently assumed).
"""
from __future__ import annotations

import collections
import csv
import importlib.util
import json
import re
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
S2_CANON = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B10", "B11", "B12"]
MODEL_BANDS = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
MASK_SUFFIX_RE = re.compile(r"([_\-.]?(mask|masks|label|labels|lbl|gt|cl|seg|segmentation|annotation|annot|"
                            r"target|class|classes|y))$", re.I)
IMAGE_SUFFIX_RE = re.compile(r"([_\-.]?(img|image|images|s2|sentinel2|sentinel-2|data|x|rgb|input))$", re.I)
MASK_DIR_RE = re.compile(r"^(mask|masks|label|labels|lbl|gt|groundtruth|ground_truth|annotation|annotations|"
                         r"annot|seg|segmentation|targets?|y)$", re.I)
IMAGE_DIR_RE = re.compile(r"^(image|images|img|imgs|x|inputs?|data|s2|sentinel2|sentinel-2|chips?|patches|tiles)$",
                          re.I)
SPLIT_DIR = {"train": r"(^|/)(train|training)(/|$)", "val": r"(^|/)(val|valid|validation|dev)(/|$)",
             "test": r"(^|/)(test|testing)(/|$)"}
NAME_ALIASES = {  # organiser band names -> ours
    "COASTAL": "B1", "AEROSOL": "B1", "BLUE": "B2", "GREEN": "B3", "RED": "B4", "REDEDGE1": "B5", "RE1": "B5",
    "REDEDGE2": "B6", "RE2": "B6", "REDEDGE3": "B7", "RE3": "B7", "NIR": "B8", "NIR08": "B8", "NARROWNIR": "B8A",
    "NIR2": "B8A", "NIR09": "B8A", "REDEDGE4": "B8A", "WATERVAPOR": "B9", "WV": "B9", "CIRRUS": "B10",
    "SWIR1": "B11", "SWIR16": "B11", "SWIR2": "B12", "SWIR22": "B12",
}
BY_COUNT = {  # band count -> (guess, alternatives text)
    13: (S2_CANON, "L1C, все 13 каналов по порядку B1..B12 с B8A после B8"),
    12: (["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"],
         "L2A без B10 (порядок как в SAFE); альтернатива — 13 без B1"),
    11: (MODEL_BANDS, "порядок MARIDA (B1..B8, B8A, B11, B12); альтернатива — L2A без B1 и B10"),
    10: (["B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"],
         "10 и 20 м каналы без B1/B9/B10; альтернатива — с B1 без B8A"),
    4: (["B2", "B3", "B4", "B8"], "B2,B3,B4,B8 (10 м); альтернатива — B4,B3,B2,B8 (RGB+NIR)"),
    3: (["B4", "B3", "B2"], "RGB-квиклук; альтернатива — B2,B3,B4 (BGR)"),
}


def load_inspect():
    """scripts/tools/inspect_dataset.py as a module (re-used, not duplicated)."""
    name = "inspect_dataset"
    if name in sys.modules:
        return sys.modules[name]
    p = REPO / "scripts" / "tools" / "inspect_dataset.py"
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def doubt(topic: str, text: str, options: str = "", where: str = "", level: str = "check") -> dict:
    return {"topic": topic, "text": text, "options": options, "where": where, "level": level}


def canon_or_alias(name: str) -> str:
    from macroplastic.organizer_adapter import canon_band

    s = re.sub(r"[^A-Za-z0-9]", "", str(name)).upper()
    if s in NAME_ALIASES:
        return NAME_ALIASES[s]
    return canon_band(name)


# --------------------------------------------------------------------------- templates / regex helpers
def _tpl_to_regex(tpl: str) -> str:
    out = []
    for part in re.split(r"(\{n\}|\{date\}|\{tile\})", tpl):
        if part == "{n}":
            out.append(r"\d+")
        elif part == "{date}":
            out.append(r"[0-9][0-9T:\-]*")
        elif part == "{tile}":
            out.append(r"T?\d{2}[A-Z]{3}")
        else:
            out.append(re.escape(part))
    return "".join(out)


def group_regex(group: str) -> str:
    """inspect group 'dir_tpl/name_tpl.ext' -> regex on a posix path (anchored at the end)."""
    d, _, f = group.rpartition("/")
    fre = _tpl_to_regex(f)
    if d in ("", "."):
        return r"(^|/)" + fre + "$"
    return r"(^|/)" + "/".join(_tpl_to_regex(x) for x in d.split("/")) + "/" + fre + "$"


def _strip_suffix(stem: str, rx: re.Pattern) -> str:
    s = stem
    for _ in range(2):
        s2 = rx.sub("", s)
        if s2 == s or not s2:
            break
        s = s2
    return s


def scene_group(sample_id: str) -> str:
    """Group key for a split: MGRS tile + date if present, else id without trailing numeric tokens."""
    toks = re.split(r"[_\-]", sample_id)
    tile = next((t for t in toks if re.fullmatch(r"T?\d{2}[C-X][A-Z]{2}", t)), None)
    m = re.search(r"(\d{1,2}-\d{1,2}-\d{2,4}|(19|20)\d{6}|\d{4}-\d{2}-\d{2})", sample_id)
    if tile and m:
        return f"{m.group(1)}_{tile}"
    s = re.sub(r"([_\-]\d+)+$", "", sample_id)
    return s or sample_id


def assign_groups(ids: list[str]) -> tuple[list[str], str]:
    g = [scene_group(i) for i in ids]
    n = len(set(g))
    if n >= 4 and n < len(ids):
        return g, f"по имени (сцена/префикс без последних чисел), {n} групп"
    return list(ids), f"по файлу ({len(ids)} групп): общий префикс не даёт >= 4 групп"


# --------------------------------------------------------------------------- inventory
def inventory(inspect_summary: dict, inspect_dir: Path) -> dict:
    """files.csv of inspect -> {rel: {group, mask_like, count, dtype, ...}} + group meta."""
    gmeta = {g["group"]: g for g in inspect_summary["groups"]}
    files = {}
    with open(inspect_dir / "files.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            g = gmeta.get(r["group"], {})
            r["mask_like"] = bool(g.get("mask_like"))
            files[r["rel"]] = r
    return {"files": files, "groups": gmeta}


def gm_count_str(gm: dict) -> str:
    """Most common band count of a group as the string used in files.csv."""
    return next(iter(gm.get("count") or {"": 0}), "")


def _parent(rel: str) -> str:
    return rel.rpartition("/")[0]


def _path_sim(a: str, b: str) -> int:
    pa, pb = _parent(a).split("/"), _parent(b).split("/")
    n = 0
    for x, y in zip(pa, pb):
        if x != y:
            break
        n += 1
    return n * 10 - abs(len(pa) - len(pb))


def pair_by_name(inv: dict) -> list[tuple[str, str]]:
    """(image_rel, mask_rel) by normalised stem (mask suffix / image suffix stripped)."""
    imgs = [r for r, v in inv["files"].items() if not v["mask_like"] and not v.get("error")]
    masks = [r for r, v in inv["files"].items() if v["mask_like"]]
    by_key = collections.defaultdict(list)
    for r in imgs:
        stem = Path(r).stem
        by_key[stem.lower()].append(r)
        k2 = _strip_suffix(stem, IMAGE_SUFFIX_RE).lower()
        if k2 != stem.lower():
            by_key[k2].append(r)
    pairs = []
    used = set()
    for m in masks:
        stem = Path(m).stem
        cands = []
        for k in (_strip_suffix(stem, MASK_SUFFIX_RE).lower(), stem.lower()):
            cands += [c for c in by_key.get(k, []) if c != m]
        cands = sorted(set(cands), key=lambda c: -_path_sim(c, m))
        if not cands:
            continue
        best = cands[0]
        if len(cands) > 1 and _path_sim(cands[1], m) == _path_sim(best, m):
            continue  # ambiguous
        if best in used:
            continue
        used.add(best)
        pairs.append((best, m))
    return pairs


def pair_by_id(inspect_summary: dict, inv: dict) -> tuple[list[tuple[str, str]], list[str], str | None, str | None]:
    """Fallback: inspect families (shared numeric ids). -> pairs, alternative image groups, image group, mask group."""
    fams = [f for f in inspect_summary.get("families", []) if f["images"] and f["masks"]]
    if not fams:
        return [], [], None, None
    fam = max(fams, key=lambda f: sum(inv["groups"][g]["n_files"] for g in f["members"]))

    def score(g):
        gm = inv["groups"][g]
        d = gm.get("descriptions") or []
        s2 = sum(canon_or_alias(x) in S2_CANON for x in d if x)
        cnt = max((int(k) for k in gm["count"] if k not in ("None",)), default=0)
        return (s2, cnt >= 4, cnt)

    imgs = sorted(fam["images"], key=score, reverse=True)
    mg = fam["masks"][0]
    ig = imgs[0]
    by_id = collections.defaultdict(dict)
    for r, v in inv["files"].items():
        if v["group"] in (ig, mg):
            by_id[v["sample_id"]][v["group"]] = r
    pairs = [(d[ig], d[mg]) for d in by_id.values() if ig in d and mg in d]
    return pairs, imgs[1:], ig, mg


# --------------------------------------------------------------------------- mask rule inference
def infer_mask_rule(pairs: list[tuple[str, str]]) -> dict | None:
    """Single regex/replace (adapter layout.mask_from_image) reproducing >= 95 % of pairs, else None."""
    if not pairs:
        return None
    rules = collections.Counter()
    for img, msk in pairs[:200]:
        idir, mdir = _parent(img), _parent(msk)
        ist, iext = Path(img).stem, Path(img).suffix
        mst, mext = Path(msk).stem, Path(msk).suffix
        n = 0
        while n < min(len(ist), len(mst)) and ist[n] == mst[n]:
            n += 1
        # cut the common prefix back to a token boundary if the suffixes are words (x_img / x_mask)
        isuf, msuf = ist[n:], mst[n:]
        if isuf and msuf:
            while n > 0 and ist[n - 1] not in "_-.":
                n -= 1
            isuf, msuf = ist[n:], mst[n:]
        dparts_i, dparts_m = idir.split("/") if idir else [], mdir.split("/") if mdir else []
        dswap = None
        if idir != mdir:
            if len(dparts_i) != len(dparts_m):
                return None
            diff = [(a, b) for a, b in zip(dparts_i, dparts_m) if a != b]
            if len(diff) != 1:
                return None
            dswap = diff[0]
        if dswap:
            rx = "/" + re.escape(dswap[0]) + "/(.*)" + re.escape(isuf + iext) + "$"
            rep = "/" + dswap[1] + "/\\g<1>" + msuf + mext
        else:
            rx = re.escape(isuf + iext) + "$"
            rep = msuf + mext
        rules[(rx, rep)] += 1
    (rx, rep), _ = rules.most_common(1)[0]
    ok = sum(re.sub(rx, rep, "/" + i) == "/" + m for i, m in pairs)
    if ok < 0.95 * len(pairs):
        return None
    return {"regex": rx, "replace": rep, "reproduces": ok, "of": len(pairs)}


# --------------------------------------------------------------------------- chip csv
def find_chip_csv(inspect_summary: dict, inv: dict, data_root: Path) -> list[dict]:
    imgs = {Path(r).stem.lower(): r for r, v in inv["files"].items() if not v["mask_like"]}
    names = {Path(r).name.lower(): r for r, v in inv["files"].items() if not v["mask_like"]}
    out = []
    for t in inspect_summary.get("tables", []):
        if t.get("ext") != ".csv" or not t.get("columns"):
            continue
        import pandas as pd

        try:
            df = pd.read_csv(data_root / t["rel"])
        except Exception:
            continue
        best_id = None
        for c in df.columns:
            v = df[c].dropna().astype(str).str.strip()
            if not len(v):
                continue
            keys = v.map(lambda s: Path(s.replace("\\", "/")).stem.lower())
            hit = float(np.mean([k in imgs or Path(s).name.lower() in names for k, s in zip(keys, v)]))
            if hit >= 0.5 and (best_id is None or hit > best_id[1]):
                best_id = (str(c), hit)
        if not best_id:
            continue
        labs = []
        for c in df.columns:
            if str(c) == best_id[0]:
                continue
            s = df[c].dropna()
            nu = s.nunique()
            if nu < 2 or nu > 50 or nu > 0.5 * max(len(s), 1):
                continue  # constant, or id-like
            if s.dtype.kind == "f" and not np.all(np.mod(s.to_numpy(), 1) == 0):
                continue  # continuous value (fraction, coordinate) -- not a class label
            nm = 3 if re.search(r"label|class|target|category|debris|y$|is_", str(c), re.I) else 0
            if re.search(r"split|fold|set$|subset", str(c), re.I):
                continue
            labs.append((nm - nu / 100.0, str(c), int(nu)))
        split_col = next((str(c) for c in df.columns if re.search(r"^(split|set|subset|fold|partition)$", str(c), re.I)
                          and set(df[c].dropna().astype(str).str.lower()) & {"train", "val", "valid", "test"}), None)
        if not labs:
            continue
        labs.sort(reverse=True)
        out.append({"csv": t["rel"], "id_col": best_id[0], "id_match": round(best_id[1], 3),
                    "label_col": labs[0][1], "label_alternatives": [x[1] for x in labs[1:4]],
                    "n_rows": int(len(df)), "n_unique_labels": labs[0][2], "split_col": split_col,
                    "label_counts": {str(k): int(v) for k, v in df[labs[0][1]].value_counts().head(20).items()}})
    out.sort(key=lambda r: -r["id_match"])
    return out


# --------------------------------------------------------------------------- organiser docs (README) and hints
DOC_NAME_RE = re.compile(r"(readme|description|info|about|classes|labels?|legend|task|metric|dataset|notes?)", re.I)
DEBRIS_RE = re.compile(r"(marine[ _-]?debris|debris|plastic|litter|trash|garbage|waste|macroplastic|"
                       r"мусор|пластик|отход)", re.I)
BAND_TOK_RE = re.compile(r"\bB(?:0?[1-9]|1[0-2])A?\b", re.I)


def find_docs(data_root: Path, max_docs: int = 8, max_lines: int = 60) -> list[dict]:
    """README* / *.md / *.txt (and names of *.pdf/*.doc*) in the dataset -> [{rel, text, n_lines}].

    Plain file lists (every line = a file name / id) are skipped; README-like names first."""
    out = []
    for p in sorted(Path(data_root).rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        ext = p.suffix.lower()
        rel = str(p.relative_to(data_root)).replace("\\", "/")
        if ext in (".pdf", ".doc", ".docx", ".rtf", ".html", ".htm"):
            out.append({"rel": rel, "text": f"[{ext} — открыть руками]", "n_lines": 0, "prio": 0})
            continue
        if ext not in (".txt", ".md", ".rst", "") and not p.name.lower().startswith("readme"):
            continue
        if p.stat().st_size > 200_000:
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        nonempty = [ln for ln in lines if ln.strip()]
        if not nonempty:
            continue
        listy = sum(bool(re.fullmatch(r"[\w./\\\-]+", ln.strip())) and " " not in ln.strip() for ln in nonempty)
        if listy >= 0.9 * len(nonempty) and not p.name.lower().startswith("readme"):
            continue  # split list / file list, not documentation
        prio = 2 if p.name.lower().startswith("readme") else 1 if DOC_NAME_RE.search(p.stem) else 0.5
        out.append({"rel": rel, "text": "\n".join(lines[:max_lines]), "n_lines": len(lines), "prio": prio})
    out.sort(key=lambda d: (-d["prio"], d["rel"]))
    return out[:max_docs]


def parse_hints(docs: list[dict], n_bands: int | None = None) -> dict:
    """Machine-readable hints from organiser docs: target class value, class legend, band order, scale, metric."""
    h: dict = {"classes": {}, "target": None, "target_line": None, "bands": None, "bands_line": None,
               "scale": None, "metric_line": None, "zero_line": None}
    for d in docs:
        for ln in d["text"].splitlines():
            s = ln.strip()
            if not s:
                continue
            # class legend: "3 - marine debris", "3: debris", "debris = 3", "debris (3)"
            for m in re.finditer(r"(?<![\w.])(\d{1,3})\s*(?:[-–—:=]|->|→)\s*([A-Za-zА-Яа-яЁё][^,;.\n()]{1,40})", s):
                h["classes"].setdefault(int(m.group(1)), m.group(2).strip())
            for m in re.finditer(r"([A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё _-]{1,40}?)\s*(?:=|:|\(|->|→)\s*(\d{1,3})\b\)?", s):
                h["classes"].setdefault(int(m.group(2)), m.group(1).strip())
            for m in re.finditer(r"(?<![\w.])(\d{1,3})\s*\(([A-Za-zА-Яа-яЁё][^()]{1,40})\)", s):
                h["classes"].setdefault(int(m.group(1)), m.group(2).strip())
            toks = BAND_TOK_RE.findall(s)
            if len(toks) >= 3 and h["bands"] is None and (n_bands is None or len(toks) == n_bands):
                from macroplastic.organizer_adapter import canon_band

                h["bands"], h["bands_line"] = [canon_band(t) for t in toks], s
            if h["scale"] is None and re.search(r"(x|×|\*|/)\s*10\s?000|10\s?000\s*(x|×)|scale[^0-9]*1e-?4|0\.0001", s,
                                                re.I):
                h["scale"] = 1e-4
            if h["metric_line"] is None and re.search(r"\b(metric|f1|iou|dice|score|метрик)", s, re.I):
                h["metric_line"] = s
            if h["zero_line"] is None and re.search(r"(?<![\w.])0\s*(?:[-–—:=]|->|→)", s):
                h["zero_line"] = s
    deb = sorted(v for v, name in h["classes"].items() if DEBRIS_RE.search(name))
    if len(deb) == 1:
        h["target"] = deb[0]
        h["target_line"] = f"{deb[0]} = {h['classes'][deb[0]]}"
    elif len(deb) > 1:
        h["target_candidates"] = deb
    return h


def mask_values_all(mask_paths: list[str], max_files: int = 20000, threads: int = 8) -> dict:
    """Class values over ALL masks (stratified sample by scene group if more than max_files).

    -> {values: {v: px}, files_with: {v: n}, n_checked, n_total, per_group_target?}"""
    import concurrent.futures as cf

    ins = load_inspect()
    paths = list(mask_paths)
    n_total = len(paths)
    if n_total > max_files:  # stratified by scene group: every group keeps its share (at least 1 file)
        by = collections.defaultdict(list)
        for p in paths:
            by[scene_group(Path(p).stem)].append(p)
        rng = np.random.default_rng(0)
        frac = max_files / n_total
        paths = []
        for g, ps in sorted(by.items()):
            k = max(1, int(round(frac * len(ps))))
            paths += [ps[i] for i in sorted(rng.choice(len(ps), min(k, len(ps)), replace=False))]

    def _count(p):
        try:
            a = ins.read_array(Path(p), full=True)[0]
        except Exception:
            return None
        if a.dtype.kind == "f":
            a = np.where(np.isfinite(a), a, -1)
        u, c = np.unique(np.rint(a).astype(np.int64), return_counts=True)
        return dict(zip(u.tolist(), c.tolist()))

    with cf.ThreadPoolExecutor(threads) as ex:
        counts = list(ex.map(_count, paths))
    px, files = collections.Counter(), collections.Counter()
    n_bad = 0
    for c in counts:
        if c is None:
            n_bad += 1
            continue
        px.update(c)
        files.update(c.keys())
    return {"values": {int(k): int(v) for k, v in sorted(px.items())},
            "files_with": {int(k): int(v) for k, v in sorted(files.items())},
            "n_checked": len(paths) - n_bad, "n_total": n_total, "n_unreadable": n_bad}


# --------------------------------------------------------------------------- band / radiometry / classes guesses
def guess_bands(gm: dict, doubts: list) -> dict:
    descs = gm.get("descriptions")
    cnt = max((int(k) for k in gm["count"] if k not in ("None",)), default=0)
    if descs and any(descs):
        mapped = [canon_or_alias(d) if d else f"band{i + 1}" for i, d in enumerate(descs)]
        s2 = [m for m in mapped if m in S2_CANON]
        from macroplastic.organizer_adapter import canon_band

        rename = {str(d): m for d, m in zip(descs, mapped) if d and m in S2_CANON and canon_band(d) != m}
        other = [str(d) for d, m in zip(descs, mapped) if m not in S2_CANON]
        if other:
            doubts.append(doubt("каналы", f"каналы без соответствия S2: {other} — в выход не идут "
                                          "(SCL/QA — маски качества, не признаки).", where="bands.output"))
        if len(s2) >= 3:
            return {"source": "descriptions", "rename": rename, "names": mapped,
                    "output": [b for b in S2_CANON if b in s2 and b != "B10"], "by": "descriptions"}
    guess = BY_COUNT.get(cnt)
    if guess:
        doubts.append(doubt("порядок каналов", f"в файлах нет имён каналов (descriptions), каналов {cnt}. "
                                               f"Принято: {guess[0]} — {guess[1]}. Это ДОГАДКА по числу каналов.",
                            "проверить по документации организаторов; по превью (RGB должен выглядеть естественно); "
                            "по спектру воды (B8/B11/B12 ≈ 0, B2 > B4 над чистой водой)", "bands.source", "high"))
        return {"source": list(guess[0]), "rename": {}, "names": list(guess[0]),
                "output": [b for b in S2_CANON if b in guess[0] and b != "B10"], "by": "count"}
    names = [f"band{i + 1}" for i in range(cnt)]
    doubts.append(doubt("порядок каналов", f"{cnt} каналов без имён — не могу сопоставить с Sentinel-2.",
                        "вписать список имён в bands.source вручную", "bands.source", "blocker"))
    return {"source": names, "rename": {}, "names": names, "output": names, "by": "unknown"}


def guess_radiometry(gm: dict, band_names: list[str], doubts: list) -> dict:
    stats = [s for s, n in zip(gm.get("band_stats", []), band_names) if n in S2_CANON and s.get("p99") is not None]
    dtype = next(iter(gm.get("dtype") or {"?": 0}))
    if not stats:
        doubts.append(doubt("масштаб", "нет статистики пикселей по каналам S2 — масштаб не определён.",
                            where="radiometry.scale", level="high"))
        return {"scale": 1.0, "offset": 0.0, "nodata": []}
    p99 = max(s["p99"] for s in stats)
    # robust low percentile: zeros (unflagged nodata) contaminate p1, step to p5/p50 when there are many
    p1 = min(s["p1"] if s.get("zero_frac", 0) < 0.005 else s["p5"] if s.get("zero_frac", 0) < 0.04 else s["p50"]
             for s in stats)
    p50 = [round(s["p50"], 4) for s in stats]
    r = {"scale": 1.0, "offset": 0.0, "nodata": []}
    if dtype.startswith("float") and p99 <= 2.0:
        r["why"] = f"float, p99 max {p99:.3g} <= 2 -> уже отражательная способность (scale 1)"
        if p1 < -0.05:
            doubts.append(doubt("масштаб", f"есть заметно отрицательные значения (p1 = {p1:.3g}) — похоже на "
                                           "атмосферную коррекцию типа ACOLITE rhorc или смещение L2A уже применено.",
                                where="radiometry"))
    elif 100 < p99 < 30000:
        r.update(scale=0.0001, nodata=[0])
        r["why"] = f"{dtype}, p1..p99 = {p1:.0f}..{p99:.0f} -> похоже на DN Sentinel-2 (x1e-4)"
        if p1 >= 900:
            r["offset"] = -0.1
            doubts.append(doubt("смещение L2A", f"1-й перцентиль по всем каналам {p1:.0f} >= ~1000: похоже на L2A "
                                               "baseline >= 04.00 (после 25.01.2022) с BOA_ADD_OFFSET = -1000, "
                                               "принято offset -0.1.",
                                "offset 0 если данные до 2022 или смещение уже вычтено; проверить по воде: B8 ≈ 0–0.03",
                                "radiometry.offset", "high"))
        else:
            doubts.append(doubt("масштаб DN", f"{r['why']}. Смещение L2A (-1000 DN) не видно (p1 = {p1:.0f}) — "
                                             "принято offset 0. Медианы каналов: " + str(p50),
                                "если снимки L2A после 25.01.2022 и значения воды ≈ 1000+, поставить offset -0.1",
                                "radiometry"))
        doubts.append(doubt("nodata", "DN 0 принят за «нет данных» (стандарт S2 L2A). Если 0 — настоящее значение "
                                      "(тёмная вода в L1C почти не бывает 0), убрать.", where="nodata.values"))
    elif dtype == "uint8":
        r["scale"] = 1 / 255.0
        doubts.append(doubt("масштаб", "uint8 0..255: вероятно квиклук/растянутые данные — физическую отражательную "
                                       "способность восстановить нельзя; принято x/255 (только для относительных "
                                       "признаков).", where="radiometry.scale", level="high"))
    else:
        doubts.append(doubt("масштаб", f"{dtype}, p1..p99 = {p1:.3g}..{p99:.3g}: диапазон не похож ни на "
                                       "отражательную способность, ни на DN — масштаб не определён (оставлен 1).",
                            where="radiometry.scale", level="blocker"))
    return r


UNLAB = 99  # our code for "organiser 0 / background": train_lgbm_ingest --zero-as negative|ignore decides


def guess_classes(mask_gm: dict | None, doubts: list, all_values: dict | None = None, ov: dict | None = None,
                  hints: dict | None = None) -> dict:
    """Organiser mask values -> our scheme.  Priority: CLI (--target-class/--ignore-values) > README > guess.

    all_values: mask_values_all() over ALL paired masks (never one name group only)."""
    ov = ov or {}
    hints = hints or {}
    if all_values and all_values.get("values"):
        vals = dict(all_values["values"])
    elif mask_gm and mask_gm.get("class_balance"):
        vals = {int(r["value"]): int(r["pixels"]) for r in mask_gm["class_balance"]}
    else:
        return {"map": {}, "default": 0, "ignore_values": [0], "values": []}
    file_nod = [float(k) for k in (mask_gm or {}).get("nodata", {}) if k not in ("None", "nan")]
    if ov.get("ignore_values") is not None:
        ignore = sorted(int(v) for v in ov["ignore_values"])
    else:
        ignore = sorted({int(v) for v in file_nod if np.isfinite(v)} | ({255} if 255 in vals and max(
            [v for v in vals if v != 255] or [0]) < 100 else set()) | ({-1} if -1 in vals else set()))
    real = {v: n for v, n in vals.items() if v not in ignore}
    tot = sum(real.values()) or 1
    shares = ", ".join(f"{v}: {n / tot:.2%}" for v, n in sorted(real.items()))
    ks = sorted(real)
    chk = (f" (проверено масок: {all_values['n_checked']} из {all_values['n_total']})" if all_values else
           " (значения из одной группы имён — НЕ по всем маскам)")
    target, why = None, None
    if ov.get("target_class") is not None:
        target, why = int(ov["target_class"]), "--target-class"
    elif hints.get("target") is not None:
        target, why = int(hints["target"]), f"README: «{hints.get('target_line')}»"
    if target is not None:
        if target not in vals:
            doubts.append(doubt("целевой класс", f"целевой класс {target} ({why}) НЕ встречается в масках: значения "
                                                 f"{sorted(vals)}{chk}.", "проверить --target-class / README",
                                "classes.map", "blocker"))
        others = [k for k in ks if k not in (target, 0)]
        mp = {**({0: UNLAB} if 0 in ks and target != 0 else {}), target: 1, **{k: 7 for k in others}}
        doubts.append(doubt("целевой класс", f"цель = {target} ({why}); остальные ненулевые {others} -> фон (7); "
                                             f"0 -> {UNLAB} («фон/не размечено»: при обучении --zero-as negative "
                                             f"считает его отрицательным, --zero-as ignore — игнорирует). Доли: "
                                             f"{shares}{chk}.", where="classes.map", level="info"))
        return {"map": mp, "default": 0, "ignore_values": ignore, "values": ks, "target": target, "why": why}
    if hints.get("target_candidates"):
        doubts.append(doubt("целевой класс", f"в README несколько классов похожи на мусор: "
                                             f"{hints['target_candidates']} — выбрать --target-class.",
                            where="classes.map", level="blocker"))
    if set(ks) <= set(range(16)) and 0 in ks and (len(ks) >= 6 or ({1, 7} <= set(ks) and len(ks) >= 4)):
        doubts.append(doubt("схема классов", f"значения маски {ks} (доли: {shares}) похожи на схему MARIDA "
                                             "(0 = не размечено, 1 = Marine Debris, 7 = вода ...) — принято как есть.",
                            "если это другая схема с ~такими же номерами — задать classes.map явно", "classes.map",
                            "high"))
        return {"map": {}, "default": "keep", "ignore_values": sorted(set(ignore) | {0}), "values": ks,
                "target": 1, "why": "MARIDA-like"}
    if ks == [0, 1]:
        doubts.append(doubt("класс 0", f"маска бинарная {{0, 1}} (доли: {shares}{chk}). Принято: 1 = мусор (наш 1), "
                                       f"0 = фон/не размечено (наш {UNLAB}).",
                            "обучение: --zero-as negative (0 = отрицательный; если метрика по ВСЕМ пикселям) или "
                            "--zero-as ignore (0 = не размечено); проверить оба через --cv", "classes.map"))
        return {"map": {0: UNLAB, 1: 1}, "default": 0, "ignore_values": ignore, "values": ks, "target": 1}
    if 0 in ks:
        pos = [k for k in ks if k != 0]
        rare = min(pos, key=lambda k: real[k]) if pos else None
        lvl = "blocker" if len(pos) > 1 else "high"
        doubts.append(doubt("целевой класс", f"маска многоклассовая {ks} (доли: {shares}{chk}). Какой класс = «мусор», "
                                             f"НЕ указано (ни --target-class, ни README). Догадка: самый редкий "
                                             f"ненулевой {rare} -> цель (наш 1), остальные -> 7, 0 -> {UNLAB}.",
                            "перезапустить ingest с --target-class <значение> (или поправить classes.map и удалить "
                            "ingest_blockers в adapter.yaml)", "classes.map", lvl))
        mp = {0: UNLAB, **{k: (1 if k == rare else 7) for k in pos}}
        return {"map": mp, "default": 0, "ignore_values": ignore, "values": ks, "target": rare, "why": "guess"}
    rare = min(real, key=real.get) if real else None
    doubts.append(doubt("целевой класс", f"значения маски {ks} без 0 (доли: {shares}{chk}). Принято: самый редкий "
                                         f"{rare} -> цель (наш 1), остальные -> фон (наш 7).",
                        "--target-class <значение>", "classes.map", "blocker" if len(ks) > 2 else "high"))
    return {"map": {k: (1 if k == rare else 7) for k in ks}, "default": 0, "ignore_values": ignore, "values": ks,
            "target": rare, "why": "guess"}


# --------------------------------------------------------------------------- detection
def detect(inspect_summary: dict, inspect_dir: Path, data_root: Path) -> dict:
    """-> {'candidates': [...], 'inv': inventory, 'pairs': [...], 'chip_csv': [...]}"""
    inv = inventory(inspect_summary, inspect_dir)
    n_img = sum(1 for v in inv["files"].values() if not v["mask_like"])
    n_mask = sum(1 for v in inv["files"].values() if v["mask_like"])
    cands = []
    pairs = pair_by_name(inv)
    alt_groups = []
    id_pairs, alt_img_groups, ig, mg = pair_by_id(inspect_summary, inv)
    if pairs:
        in_mask_dir = np.mean([any(MASK_DIR_RE.match(p) for p in _parent(m).split("/")) for _, m in pairs])
        same_name = np.mean([Path(i).stem == Path(m).stem for i, m in pairs])
        rule = infer_mask_rule(pairs)
        frac = len(pairs) / max(n_mask, 1)
        if in_mask_dir >= 0.9 and same_name >= 0.9:
            cands.append({"format": "dirs", "score": 0.6 + 0.4 * frac, "pairs": pairs, "rule": rule,
                          "evidence": f"{len(pairs)} пар с одинаковыми именами в папках снимков и масок "
                                      f"(например {pairs[0][0]} ↔ {pairs[0][1]})"})
        else:
            cands.append({"format": "suffix", "score": 0.55 + 0.4 * frac, "pairs": pairs, "rule": rule,
                          "evidence": f"{len(pairs)} пар «имя ↔ имя+суффикс» (например {pairs[0][0]} ↔ "
                                      f"{pairs[0][1]})"})
    if id_pairs and len(id_pairs) > len(pairs) * 1.2:
        cands.append({"format": "ids", "score": 0.5 + 0.3 * len(id_pairs) / max(n_mask, 1), "pairs": id_pairs,
                      "rule": None, "image_group": ig, "mask_group": mg, "alt_image_groups": alt_img_groups,
                      "evidence": f"{len(id_pairs)} пар по общему числовому id: группа снимков {ig} ↔ масок {mg}"
                                  + (f"; другие группы снимков с теми же id: {alt_img_groups}" if alt_img_groups
                                     else "")})
    chip = find_chip_csv(inspect_summary, inv, data_root)
    for c in chip:
        # a chip CSV competes with masks: wins only if masks are absent / rare
        sc = 0.45 + 0.4 * c["id_match"] - (0.4 if pairs and len(pairs) >= 0.5 * n_img else 0)
        cands.append({"format": "chip_csv", "score": sc, "chip": c, "pairs": [],
                      "evidence": f"{c['csv']}: колонка «{c['id_col']}» совпадает с именами {c['id_match']:.0%} "
                                  f"снимков, метка «{c['label_col']}» ({c['n_unique_labels']} значений: "
                                  f"{c['label_counts']})"})
    if not cands:
        cands.append({"format": "images_only", "score": 0.1, "pairs": [],
                      "evidence": f"{n_img} растров без масок и без CSV с метками — похоже на тест/неразмеченные"})
    cands.sort(key=lambda c: -c["score"])
    for c in cands:
        c["score"] = round(float(c["score"]), 3)
    return {"candidates": cands, "inv": inv, "n_images": n_img, "n_masks": n_mask, "chip_csv": chip,
            "alt_groups": alt_groups}


# --------------------------------------------------------------------------- config
def _q(s) -> str:
    """YAML single-quoted scalar (backslashes literal)."""
    return "'" + str(s).replace("'", "''") + "'"


def _flow(v) -> str:
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_flow_key(k)}: {_flow(x)}" for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_flow(x) for x in v) + "]"
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return repr(v)
    return _q(v)


def _flow_key(k) -> str:
    return str(k) if isinstance(k, int) else _q(k)


def build_config(det: dict, cand: dict, data_root: Path, name: str, doubts: list, ov: dict | None = None,
                 hints: dict | None = None) -> tuple[str, dict]:
    """-> (YAML text with 'проверь это' comments, info dict).

    ov (CLI overrides, highest priority): target_class, ignore_values, channels (list), scale, offset, test_glob.
    hints: parse_hints() of the organiser README (second priority)."""
    ov = ov or {}
    hints = hints or {}
    inv = det["inv"]
    fmt = cand["format"]
    info = {"format": fmt}
    root_posix = str(Path(data_root).resolve()).replace("\\", "/")
    if fmt in ("suffix", "dirs", "ids"):
        img_rels = [i for i, _ in cand["pairs"]]
        msk_rels = [m for _, m in cand["pairs"]]
    elif fmt == "chip_csv":
        csvinfo = cand["chip"]
        import pandas as pd

        df = pd.read_csv(Path(data_root) / csvinfo["csv"])
        stems = {Path(r).stem.lower(): r for r, v in inv["files"].items() if not v["mask_like"]}
        img_rels = [stems[k] for k in (Path(str(s).replace("\\", "/")).stem.lower()
                                        for s in df[csvinfo["id_col"]].dropna()) if k in stems]
        msk_rels = []
    else:
        img_rels = [r for r, v in inv["files"].items() if not v["mask_like"]]
        msk_rels = []
    if not img_rels:
        raise RuntimeError("нет снимков для конфига")
    img_groups = collections.Counter(inv["files"][r]["group"] for r in img_rels)
    ig = img_groups.most_common(1)[0][0]
    gm = inv["groups"][ig]
    if len(img_groups) > 1:
        doubts.append(doubt("несколько групп снимков", f"снимки из {len(img_groups)} групп имён: "
                                                       f"{dict(img_groups)} — параметры каналов/масштаба взяты из {ig}.",
                            where="layout.image_glob"))
    mask_gm = None
    if msk_rels:
        mg = collections.Counter(inv["files"][r]["group"] for r in msk_rels).most_common(1)[0][0]
        mask_gm = inv["groups"][mg]
    # --- layout: image glob + exclusion of everything else it would catch
    exts = collections.Counter(Path(r).suffix.lower() for r in img_rels)
    ext = exts.most_common(1)[0][0]
    tops = {r.split("/")[0] if "/" in r else "" for r in img_rels}
    if len(tops) == 1 and "" not in tops:
        image_glob = f"{next(iter(tops))}/**/*{ext}"
    else:
        image_glob = f"**/*{ext}"
    img_set = set(img_rels)
    caught_other = [r for r in inv["files"] if r not in img_set and Path(r).suffix.lower() == ext and
                    (image_glob.startswith("**") or r.startswith(image_glob.split("/")[0] + "/"))]
    excl_groups = sorted({inv["files"][r]["group"] for r in caught_other})
    exclude_regex = "|".join(group_regex(g) for g in excl_groups) if excl_groups else None
    rule0 = cand.get("rule")
    if caught_other and rule0:  # prefer a short, readable exclusion derived from the mask naming rule
        rep = rule0["replace"]
        simple = ("/" + re.escape(rep.split("/")[1]) + "/") if rep.startswith("/") else re.escape(rep) + "$"
        if all(re.search(simple, "/" + r) for r in caught_other) and                 not any(re.search(simple, "/" + r) for r in img_rels):
            exclude_regex = simple
    if exclude_regex and len(exclude_regex) > 600:
        exclude_regex = None
        doubts.append(doubt("отбор снимков", f"под шаблон {image_glob} попадают {len(caught_other)} лишних файлов "
                                             f"из {len(excl_groups)} групп — исключающий regex слишком длинный, "
                                             "не записан; require_mask отсеет файлы без масок.",
                            where="layout.exclude_regex"))
    ids = [Path(r).stem for r in img_rels]
    id_regex = None
    if len(set(i.lower() for i in ids)) < len(ids):
        id_regex = r"([^/]+/[^/]+)\.[A-Za-z0-9]+$"
        doubts.append(doubt("id снимков", "имена снимков повторяются в разных папках — id = «папка/имя».",
                            where="layout.id_regex"))
    splits = {}
    for sp, rx in SPLIT_DIR.items():
        if any(re.search(rx, "/" + r) for r in img_rels):
            splits[sp] = rx
    layout = {"image_glob": image_glob, "exclude_regex": exclude_regex, "id_regex": id_regex}
    mask_lines = []
    if fmt in ("suffix", "dirs"):
        rule = cand.get("rule")
        if rule:
            mask_lines.append(f"  mask_from_image: {{regex: {_q(rule['regex'])}, replace: {_q(rule['replace'])}}}"
                              f"   # восстанавливает {rule['reproduces']}/{rule['of']} найденных пар")
        else:
            fmt = "ids"
    if fmt == "ids":
        mg_name = collections.Counter(inv["files"][r]["group"] for r in msk_rels).most_common(1)[0][0]
        ig_name = ig
        d, _, f = mg_name.rpartition("/")
        mglob = (re.sub(r"\{(n|date|tile)\}", "*", d) + "/" if d not in ("", ".") else "**/") + \
            re.sub(r"\{(n|date|tile)\}", "*", f)
        mask_lines.append(f"  mask_glob: {_q(mglob)}")

        def id_rx(g):
            f2 = g.rpartition("/")[2]
            stem = f2.rsplit(".", 1)[0]
            parts = re.split(r"(\{n\}|\{date\}|\{tile\})", stem)
            var_idx = [i for i, p in enumerate(parts) if p in ("{n}", "{date}", "{tile}")]
            if not var_idx:
                return r"([^/]+)\.[A-Za-z0-9]+$"
            a, b = var_idx[0], var_idx[-1]
            pre = _tpl_to_regex("".join(parts[:a]))
            mid = _tpl_to_regex("".join(parts[a:b + 1]))
            post = _tpl_to_regex("".join(parts[b + 1:]))
            return pre + "(" + mid + ")" + post + r"\.[A-Za-z0-9]+$"

        layout["id_regex"] = id_rx(ig_name)
        mask_lines.append(f"  mask_id_regex: {_q(id_rx(mg_name))}")
        info["image_group"] = ig_name
        if cand.get("alt_image_groups"):
            doubts.append(doubt("какой снимок брать", f"с масками по id совпадают несколько групп снимков: "
                                                      f"{[ig_name] + cand['alt_image_groups']}. Взята {ig_name} "
                                                      "(больше каналов S2).",
                                "pre/post, S1/S2 — выбрать нужную; пре+пост пока не поддержано одним конфигом",
                                "layout.image_glob", "high"))
            layout["image_glob"] = (re.sub(r"\{(n|date|tile)\}", "*", ig_name.rpartition("/")[0]) + "/"
                                    if ig_name.rpartition("/")[0] not in ("", ".") else "**/") + \
                re.sub(r"\{(n|date|tile)\}", "*", ig_name.rpartition("/")[2])
            layout["exclude_regex"] = None
    # --- bands, radiometry, nodata, resolution, classes   (priority: CLI > README > guess)
    cnt0 = max((int(k) for k in gm["count"] if k not in ("None",)), default=0)
    if ov.get("channels"):
        ch = [canon_or_alias(c) for c in ov["channels"]]
        if cnt0 and len(ch) != cnt0:
            doubts.append(doubt("порядок каналов", f"--channels даёт {len(ch)} имён, а в файлах {cnt0} каналов.",
                                where="bands.source", level="blocker"))
        bands = {"source": ch, "rename": {}, "names": ch, "output": [b for b in S2_CANON if b in ch and b != "B10"],
                 "by": "cli"}
    elif hints.get("bands") and (not cnt0 or len(hints["bands"]) == cnt0):
        ch = list(hints["bands"])
        doubts.append(doubt("порядок каналов", f"порядок каналов взят из README: «{hints['bands_line'][:160]}» -> {ch}.",
                            where="bands.source", level="info"))
        bands = {"source": ch, "rename": {}, "names": ch, "output": [b for b in S2_CANON if b in ch and b != "B10"],
                 "by": "readme"}
    else:
        bands = guess_bands(gm, doubts)
    rad = guess_radiometry(gm, bands["names"], doubts)
    if ov.get("scale") is not None or ov.get("offset") is not None:
        if ov.get("scale") is not None:
            rad["scale"] = float(ov["scale"])
        if ov.get("offset") is not None:
            rad["offset"] = float(ov["offset"])
        rad["why"] = f"задано в командной строке: scale {rad['scale']}, offset {rad['offset']}"
        doubts[:] = [d for d in doubts if d["topic"] not in ("масштаб", "масштаб DN", "смещение L2A")]
    elif hints.get("scale") and abs(rad["scale"] - hints["scale"]) > 1e-12:
        doubts.append(doubt("масштаб", f"README намекает на scale {hints['scale']}, автоугадывание дало "
                                       f"{rad['scale']} — проверить.", where="radiometry.scale", level="high"))
    miss = [b for b in MODEL_BANDS if b not in bands["output"]]
    if miss and bands["by"] != "unknown":
        doubts.append(doubt("нет каналов модели", f"в данных нет {miss} из 11 каналов нашей модели: при обучении "
                                                  "(train_lgbm_ingest) они заполняются ближайшим по длине волны "
                                                  "каналом (костыль, пишется в meta.json band_fill); веса, обученные "
                                                  "на MARIDA, к этим данным напрямую не применимы.",
                            where="bands.output / переобучение", level="check"))
    res = gm.get("res") or {}
    res0 = next(iter(res), "None")
    target_res = None
    try:
        rv = json.loads(res0) if res0 not in ("None", "null") else None
    except Exception:
        rv = None
    if rv is None:
        doubts.append(doubt("геопривязка", "у снимков нет CRS/разрешения — площади и сетка H3 недоступны, "
                                           "модель работает в пикселях (предполагаем 10 м).", where="resolution"))
    elif abs(float(rv[0]) - 10) > 0.5:
        doubts.append(doubt("разрешение", f"разрешение {rv[0]} (единицы CRS), модель MARIDA обучена на 10 м; оставлено "
                                          "как есть (оконные признаки в пикселях).",
                            "resolution.target: 10 — если хотим сопоставимость с нашими весами", "resolution.target"))
    nod_file = [k for k in gm.get("nodata", {}) if k not in ("None",)]
    if not nod_file and not rad["nodata"]:
        doubts.append(doubt("nodata", "в файлах снимков nodata не задан; NaN считаются пропусками.",
                            where="nodata.values", level="info"))
    all_vals = None
    if msk_rels:
        all_vals = mask_values_all([str(Path(data_root) / r) for r in msk_rels], max_files=ov.get("max_masks", 20000))
        info["mask_values"] = all_vals
        if all_vals["n_checked"] < all_vals["n_total"]:
            doubts.append(doubt("значения масок", f"значения классов посчитаны по стратифицированной выборке: "
                                                  f"проверено {all_vals['n_checked']} из {all_vals['n_total']} масок.",
                                where="--max-masks", level="check"))
    classes = (guess_classes(mask_gm, doubts, all_vals, ov, hints) if msk_rels else
               {"map": {}, "default": 0, "ignore_values": []})
    # images without masks (test part): glob for --convert (test is converted separately, require_mask false)
    test_glob = ov.get("test_glob")
    if not test_glob and fmt in ("suffix", "dirs", "ids") and msk_rels:
        all_img = [r for r, v in inv["files"].items() if not v["mask_like"] and Path(r).suffix.lower() == ext
                   and inv["files"][r].get("count") == gm_count_str(gm)]
        unp = [r for r in all_img if r not in img_set]
        if unp:
            ttops = {r.split("/")[0] for r in unp if "/" in r}
            if len(ttops) == 1 and not any(r.startswith(next(iter(ttops)) + "/") for r in img_rels):
                test_glob = f"{next(iter(ttops))}/**/*{ext}"
            else:
                sp = [r for r in unp if re.search(SPLIT_DIR["test"], "/" + r)]
                if sp and len(sp) == len(unp):
                    test_glob = "**/test/**/*" + ext
            info["n_unpaired_images"] = len(unp)
            if test_glob:
                doubts.append(doubt("test", f"{len(unp)} снимков без масок (похоже на test): layout.test_glob = "
                                            f"{test_glob} — `--convert` сконвертирует их отдельно в converted_test/, "
                                            "predict_org.py предскажет их напрямую.", where="layout.test_glob",
                                    level="info"))
    info["test_glob"] = test_glob
    if mask_gm:
        mres = next(iter(mask_gm.get("res") or {}), None)
        msz, isz = next(iter(mask_gm.get("size_hw") or {}), None), next(iter(gm.get("size_hw") or {}), None)
        if msz and isz and msz != isz:
            doubts.append(doubt("сетка маски", f"размер маски {msz} ≠ снимка {isz} (разрешение маски {mres}) — "
                                               "маска будет пересэмплирована nearest на сетку снимка.",
                                where="resolution", level="high"))
    chip_block = []
    if fmt == "chip_csv":
        c = cand["chip"]
        import pandas as pd

        df = pd.read_csv(Path(data_root) / c["csv"])
        labs = df[c["label_col"]].dropna()
        uniq = sorted(labs.unique().tolist(), key=lambda x: str(x))
        if labs.dtype.kind in "if" and labs.nunique() > 20:
            cmap = None
        elif set(map(str, uniq)) <= {"0", "1", "0.0", "1.0", "True", "False", "true", "false"}:
            cmap = {str(u): (1 if str(u).lower() in ("1", "1.0", "true") else 7) for u in uniq}
        else:
            deb = [u for u in uniq if re.search(r"debris|plastic|litter|trash|garbage|waste|musor|мусор", str(u), re.I)]
            if deb:
                cmap = {str(u): (1 if u in deb else 7) for u in uniq}
            else:
                cnt = labs.value_counts()
                rare = cnt.idxmin()
                cmap = {str(u): (1 if u == rare else 7) for u in uniq}
                doubts.append(doubt("метка чипа", f"значения «{c['label_col']}»: {c['label_counts']} — какое = мусор, "
                                                  f"не ясно; принято самое редкое «{rare}» -> 1.",
                                    where="chip_labels.map", level="high"))
        doubts.append(doubt("классификация чипов", f"метки на уровне чипа ({c['csv']}: {c['id_col']} -> "
                                                   f"{c['label_col']}), масок нет. При конвертации маска = класс чипа "
                                                   "на всех валидных пикселях (шумная пиксельная разметка!); метрика — "
                                                   "по чипам (агрегат вероятностей).",
                            "альтернативы колонки метки: " + str(c["label_alternatives"]), "chip_labels", "high"))
        chip_block = [
            "chip_labels:                  # chip-level classification (ingest extension, the adapter ignores it)",
            f"  csv: {_q(c['csv'])}          # relative to root",
            f"  id_col: {_q(c['id_col'])}           # values = chip file names (stem or name)",
            f"  label_col: {_q(c['label_col'])}     # ПРОВЕРЬ ЭТО; alternatives: {c['label_alternatives']}",
            f"  map: {_flow(cmap) if cmap else 'null'}   # CSV value -> our class (1 = debris, 7 = other); "
            "ПРОВЕРЬ ЭТО",
            f"  split_col: {_q(c['split_col']) if c['split_col'] else 'null'}",
        ]
        if cmap is None:
            doubts.append(doubt("метка чипа", f"«{c['label_col']}» — непрерывная величина (регрессия?), в классы не "
                                              "переводится автоматически.", where="chip_labels.map",
                                level="blocker"))
    # --- YAML text
    L = []
    L.append(f"# AUTO-GENERATED by python -m macroplastic.ingest  (format: {fmt}; score {cand['score']})")
    L.append("# Lines marked 'ПРОВЕРЬ ЭТО' are guesses -- see the 'Сомнения' section of report/index.html.")
    L.append("# Edit and re-run:  python -m macroplastic.ingest <src> --out <this folder> --convert")
    L.append(f"name: {_q(name)}")
    L.append(f"root: {_q(root_posix)}")
    L.append("layout:")
    L.append(f"  image_glob: {_q(layout['image_glob'])}")
    L.append(f"  exclude_regex: {_q(layout['exclude_regex']) if layout.get('exclude_regex') else 'null'}")
    L.append(f"  id_regex: {_q(layout['id_regex']) if layout.get('id_regex') else 'null'}   # null = file stem")
    L += mask_lines
    L.append(f"  split_regex: {_flow(splits)}" + ("   # official split from folder names" if splits else
                                                   "   # no official split found -> group split at training"))
    L.append(f"  require_mask: {'true' if msk_rels else 'false'}")
    L.append(f"  test_glob: {_q(test_glob) if test_glob else 'null'}   # images WITHOUT masks (test): --convert -> "
             "converted_test/, predict_org.py --images")
    L.append("bands:")
    src = bands["source"]
    L.append(f"  source: {_q(src) if isinstance(src, str) else _flow(src)}"
             + {"descriptions": "", "cli": "   # из --channels", "readme": "   # из README организаторов"}.get(
                 bands["by"], "   # ПРОВЕРЬ ЭТО: догадка по числу каналов"))
    L.append(f"  rename: {_flow(bands['rename'])}")
    L.append(f"  output: {_flow(bands['output'])}")
    L.append("  missing: nan                  # absent bands -> NaN (training fills them from neighbours)")
    L.append("radiometry:                     # reflectance = DN * scale + offset")
    L.append(f"  scale: {rad['scale']!r}   # ПРОВЕРЬ ЭТО: {rad.get('why', '')}")
    L.append(f"  offset: {rad['offset']!r}   # ПРОВЕРЬ ЭТО: L2A baseline >= 04.00 raw DN -> -0.1")
    L.append("  clip: null")
    L.append("nodata:")
    L.append(f"  values: {_flow(rad['nodata'])}")
    L.append("  use_file_nodata: true")
    L.append("resolution:")
    L.append(f"  target: {target_res if target_res else 'null'}   # null = native grid ({res0})")
    L.append("  resampling: bilinear")
    L.append("classes:")
    L.append("  mask_band: 1")
    L.append(f"  map: {_flow(classes['map'])}   # ПРОВЕРЬ ЭТО: organiser value -> ours (1 = Marine Debris, 7 = water/"
             f"other, {UNLAB} = organiser 0 / background (train --zero-as), 0 = ignore); values seen: "
             + str(classes.get("values", [])) + (f"; target from {classes['why']}" if classes.get("why") else ""))
    L.append(f"  default: {_flow(classes['default'])}")
    L.append(f"  ignore_values: {_flow(classes['ignore_values'])}")
    L.append("output:")
    L.append("  debris_class: 1")
    L += chip_block
    text = "\n".join(L) + "\n"
    info.update(n_images=len(img_rels), n_masks=len(msk_rels), image_group=ig, bands=bands, radiometry=rad,
                classes={k: v for k, v in classes.items()}, splits=list(splits))
    return text, info


# --------------------------------------------------------------------------- convert
def test_cfg(cfg: dict, images: str | None = None) -> dict:
    """Adapter config for images WITHOUT masks (organiser test): same bands/radiometry/nodata, no masks.

    images: folder or glob (absolute, or relative to cfg root); default layout.test_glob."""
    import copy

    c = copy.deepcopy(cfg)
    L = c["layout"]
    g = images or L.get("test_glob")
    if not g:
        raise SystemExit("layout.test_glob не задан: укажите --test-glob 'test/**/*.tif' (или predict_org.py --images)")
    gp = Path(g)
    if gp.is_absolute() or gp.exists():
        if gp.is_dir():
            ext = L.get("image_glob", "*.tif").rsplit(".", 1)[-1] if "." in L.get("image_glob", "") else "tif"
            c["root"], L["image_glob"] = gp.resolve(), f"**/*.{ext}"
        else:
            c["root"], L["image_glob"] = gp.parent.resolve(), gp.name
    else:
        L["image_glob"] = g
    for k in ("mask_from_image", "mask_glob", "mask_id_regex", "split_lists"):
        L.pop(k, None)
    L["require_mask"] = False  # exclude_regex is kept (e.g. MARIDA _cl/_conf next to the images)
    L["split_regex"] = {"test": ".*"}
    c["name"] = f"{cfg.get('name', 'org')}_test"
    return c


def check_blockers(config_path: Path) -> list[str]:
    import yaml

    raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    return [str(b) for b in (raw.get("ingest_blockers") or [])]


def convert(config_path: Path, out_dir: Path, limit: int | None = None, workers: int = 8,
            split: str | None = None) -> Path:
    """Adapter conversion + chip-level masks + group column. -> manifest.csv path.

    split='test': images without masks via layout.test_glob (see test_cfg), no group/chip post-processing."""
    import yaml
    import rasterio

    from macroplastic.organizer_adapter import convert as ad_convert, load_config

    raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    cfg = load_config(config_path)
    if split == "test":
        cfg = test_cfg(cfg)
    man = ad_convert(cfg, out_dir, limit=limit, verbose=True, workers=workers)
    if split == "test":
        return Path(man)
    rows = list(csv.DictReader(open(man, encoding="utf-8")))
    chip = raw.get("chip_labels")
    if chip:
        import pandas as pd

        df = pd.read_csv(Path(cfg["root"]) / chip["csv"])
        cmap = {str(k): int(v) for k, v in (chip.get("map") or {}).items()}
        lab = {Path(str(i).replace("\\", "/")).stem: str(v) for i, v in zip(df[chip["id_col"]], df[chip["label_col"]])}
        spl = ({Path(str(i).replace("\\", "/")).stem: str(v).lower() for i, v in zip(df[chip["id_col"]],
                                                                                     df[chip["split_col"]])}
               if chip.get("split_col") else {})
        # match CSV values that were floats written as "1.0"
        cmap.update({str(float(k)): v for k, v in list(cmap.items()) if re.fullmatch(r"-?\d+", k)})
        keep = []
        for r in rows:
            stem = Path(r["src_image"].split(";")[0]).stem
            v = lab.get(stem)
            if v is None or v not in cmap:
                continue
            cls = cmap[v]
            mp = Path(r["image"]).parent.parent / "masks" / Path(r["image"]).name
            with rasterio.open(r["image"]) as ds:
                img = ds.read()
                prof = {"driver": "GTiff", "height": ds.height, "width": ds.width, "count": 1, "dtype": "uint8",
                        "compress": "deflate"}
                if ds.crs is not None:
                    prof.update(crs=ds.crs, transform=ds.transform)
            m = np.where(np.isnan(img).any(0), 0, cls).astype(np.uint8)
            with rasterio.open(mp, "w", **prof) as ds:
                ds.write(m[None])
                ds.descriptions = ("class",)
            r.update(mask=str(mp), n_labelled_px=int((m > 0).sum()), n_debris_px=int((m == 1).sum()),
                     chip_label=v, chip_class=cls, chip_level=1)
            if spl.get(stem) in ("train", "val", "valid", "test"):
                r["split"] = "val" if spl[stem] == "valid" else spl[stem]
            keep.append(r)
        rows = keep
    groups, how = assign_groups([r["id"] for r in rows])
    for r, g in zip(rows, groups):
        r["group"] = g
        r.setdefault("chip_level", 0)
    cols = list(rows[0].keys()) if rows else ["id"]
    for r in rows:
        for c in r:
            if c not in cols:
                cols.append(c)
    with open(man, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    (Path(man).parent / "groups.txt").write_text(how + "\n", encoding="utf-8")
    return Path(man)
