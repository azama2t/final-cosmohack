"""§55 п.2 (L154): PRIME MODE по строкам CSV организаторов — СИНТЕТИКА, имитация снимков, которых у нас нет.

  CUDA_VISIBLE_DEVICES=-1 .venv/Scripts/python.exe scripts/case/prime_csv.py gen      # CPU: сцены и плитки
  .venv/Scripts/python.exe scripts/gpu_queue.py submit --name l154_prime_infer -- \
      .venv/Scripts/python.exe scripts/case/prime_csv.py infer [--gsd 0.05 0.5 10]     # GPU через очередь
  CUDA_VISIBLE_DEVICES=-1 .venv/Scripts/python.exe scripts/case/prime_csv.py score    # CPU: метрики, превью, index

Что делается (seed = 55, воспроизводимо):
  * строки: task/macroplastic_marine_samples.csv, строки верхнего уровня (parent_sample_id пуст) с items_count N,
    sampled_area_km2 A и concentration_items_km2 — все такие строки (220 строк / 187 событий), без случайной выборки;
  * категории — из дочерних строк (parent_sample_id = строка) с N > 0, иначе категория самой строки; размеры —
    лог-равномерно в пределах size_class строки (допущение: распределение размеров внутри класса в CSV не дано);
  * окно = вся площадь A строки (N предметов равномерно-случайно на площади A → та же концентрация N/A).
    Рендер разрежённый: сцена режется на плитки 12,8 × 12,8 м (256 пикс. при 0,05 м); рисуются только плитки
    с предметами + 3 пустые плитки на строку (оценка ложных срабатываний на чистой воде). Ложные срабатывания на
    остальных (нерисованных) пустых плитках берутся как средняя частота по всем пустым плиткам (пул по строкам);
  * фон — реальная вода с дрона: кадры PLP 2021–2023 (Plastic Litter Project, Zenodo 7085112 / 10046182),
    окна 256 пикс. без мишеней/буёв (фильтр по выбросам яркости), масштаб текстуры условный;
  * предметы — вырезанные по полигонам РЕАЛЬНЫЕ предметы TUN-MarineLitter (Zenodo 21965497, CC BY 4.0), класс по
    категории CSV, масштаб — до заданного размера (длинная сторона), случайный поворот;
  * счётчик — НАШ аэро-счётчик как есть (weights/photo_count/frcnn_winans_spatial_fp16.pth, порог из карточки 0,8),
    без дообучения на этих кадрах; плитка подаётся в масштабе обучения 0,02 м/пикс. (увеличение ×2,5, информации
    не добавляет);
  * деградация: та же плитка усредняется до GSD 0,2 / 0,5 / 1 / 3 / 10 м (box-фильтр) и подаётся детектору тем же
    размером 640 пикс.
Выход: out/prime_csv/ (плитки, предсказания), reports/prime/metrics.json + *.png, data/case/prime_csv/index.json +
превью <sample_id>.jpg (≤ 150 КБ). Это НЕ наблюдение и НЕ оценка по спутнику.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "task" / "macroplastic_marine_samples.csv"
WORK = ROOT / "out" / "prime_csv"
TILES = WORK / "tiles"
OUT_DATA = ROOT / "data" / "case" / "prime_csv"
OUT_REP = ROOT / "reports" / "prime"
WEIGHTS = ROOT / "weights" / "photo_count" / "frcnn_winans_spatial_fp16.pth"
CARD = ROOT / "weights" / "photo_count" / "model_card_aerial.json"
SEED = 55
GSD0 = 0.05                  # основной GSD, м/пикс.
TILE_PX = 256
TILE_M = TILE_PX * GSD0      # 12.8 м
TILE_M2 = TILE_M * TILE_M    # 163.84 м²
MODEL_GSD = 0.02             # GSD обучения аэро-счётчика (Winans 2023)
IN_PX = int(round(TILE_M / MODEL_GSD))   # 640
GSDS = [0.05, 0.2, 0.5, 1.0, 3.0, 10.0]
N_EMPTY = 3
BADGE = "СИНТЕТИКА — имитация снимков, которых у нас нет"
TUN = ROOT / "data" / "extra" / "count_ds" / "tun_marinelitter" / "Data"
TUN_NAMES = ["Cardboard", "Fabrics", "Glass", "Metal", "Other", "Plastic", "Wood"]
WATER_SRC = [
    ("data/extra/count_ds/plp2022_23_zenodo10046182/PLP2022_PLP2023_min_fraction_dataset/20230621/20230621_UAS_RGB.JPG", 0.0),
    ("data/extra/count_ds/plp2022_23_zenodo10046182/PLP2022_PLP2023_min_fraction_dataset/20220721/20220721_UAS.JPG", 0.0),
    ("data/extra/count_ds/plp2021_zenodo7085112/20210621/20210621_UAS_RGB.png", 0.25),   # верхняя четверть — берег
]
SIZE_CM = {  # size_class CSV -> диапазон длинной стороны, см (лог-равномерно; допущение)
    ">2 cm (macro)": (2.0, 50.0), ">2.5 cm (macro)": (2.5, 50.0), ">5 cm (macro+mega)": (5.0, 200.0),
    "5-50 cm (macroplastic)": (5.0, 50.0), ">50 cm (megaplastic)": (50.0, 300.0)}
ALL_LITTER_MIX = {"Plastic": 0.7, "Wood": 0.1, "Cardboard": 0.05, "Fabrics": 0.05, "Glass": 0.05, "Metal": 0.05}


def cat_to_mix(cat: str) -> dict:
    c = (cat or "").lower()
    if any(k in c for k in ("net", "rope", "line", "fisher")):
        return {"Fabrics": 0.5, "Other": 0.3, "Plastic": 0.2}
    if "all materials" in c or "macrolitter" in c or "macro litter" in c:
        return dict(ALL_LITTER_MIX)
    if "eps" in c or "foam" in c:
        return {"Plastic": 0.6, "Other": 0.4}
    return {"Plastic": 0.85, "Other": 0.15}


# ------------------------------------------------------------------ selection
def select_rows():
    import pandas as pd
    d = pd.read_csv(CSV)
    top = d[d.parent_sample_id.isna() & d.items_count.notna() & d.sampled_area_km2.notna()
            & d.concentration_items_km2.notna() & (d.sampled_area_km2 > 0)].copy()
    rows = []
    for _, r in top.iterrows():
        ch = d[(d.parent_sample_id == r.sample_id) & (d.items_count.fillna(0) > 0)]
        mix: dict = {}
        cats = []
        if len(ch):
            tot = float(ch.items_count.sum())
            for _, c in ch.iterrows():
                w = float(c.items_count) / tot
                cats.append({"category": c.litter_category, "items": int(c.items_count)})
                for k, v in cat_to_mix(c.litter_category).items():
                    mix[k] = mix.get(k, 0) + w * v
        else:
            mix = cat_to_mix(r.litter_category)
            cats.append({"category": r.litter_category, "items": int(r.items_count)})
        rows.append(dict(sample_id=r.sample_id, event_id=r.event_id, source_id=r.source_id,
                         source_short=r.source_short, region=r.region, date=r.date_utc,
                         lat=float(r.latitude), lon=float(r.longitude), size_class=r.size_class,
                         category=r.litter_category, target_scope=r.target_scope,
                         n_csv=int(r.items_count), area_km2=float(r.sampled_area_km2),
                         conc_csv=float(r.concentration_items_km2), categories=cats, mix=mix))
    return rows


# ------------------------------------------------------------------ assets
def water_patches(rng, max_n=400, T=TILE_PX):
    from PIL import Image
    pats = []
    for rel, top_frac in WATER_SRC:
        p = ROOT / rel
        if not p.exists():
            continue
        im = np.asarray(Image.open(p).convert("RGB"), np.float32)
        H, W, _ = im.shape
        y0 = int(H * top_frac)
        for y in range(y0, H - T, T // 2):
            for x in range(0, W - T, T // 2):
                w = im[y:y + T, x:x + T]
                lum = w.mean(2)
                med = np.median(lum)
                rb = w[..., 0] - w[..., 2]
                # мишени/буи/берег: доля резких выбросов яркости или «красноты» (вода — без них; блики волн — редкие)
                if (np.abs(lum - med) > 60).mean() > 0.002 or (rb > np.median(rb) + 45).mean() > 0.0005                         or med < 30 or med > 200:
                    continue
                pats.append(w.astype(np.uint8))
    rng.shuffle(pats)
    return pats[:max_n]


def tun_polys():
    by = {n: [] for n in TUN_NAMES}
    for lab in sorted(glob.glob(str(TUN / "*" / "labels" / "*.txt"))):
        img = lab.replace(os.sep + "labels" + os.sep, os.sep + "images" + os.sep)[:-4] + ".jpg"
        if not os.path.exists(img):
            continue
        for line in open(lab, encoding="utf-8"):
            p = line.split()
            if len(p) < 9:
                continue
            xy = np.array(p[1:], np.float32).reshape(-1, 2) * 640
            w, h = np.ptp(xy[:, 0]), np.ptp(xy[:, 1])
            if min(w, h) < 12 or max(w, h) > 600:
                continue
            by[TUN_NAMES[int(p[0])]].append((img, xy))
    return by


class Cutouts:
    def __init__(self, polys, rng):
        self.polys, self.rng, self.cache = polys, rng, {}

    def get(self, cls):
        from PIL import Image, ImageDraw
        lst = self.polys.get(cls) or self.polys["Plastic"]
        i = int(self.rng.integers(len(lst)))
        key = (cls, i)
        if key not in self.cache:
            img, xy = lst[i]
            im = Image.open(img).convert("RGB")
            x0, y0 = np.floor(xy.min(0)).astype(int)
            x1, y1 = np.ceil(xy.max(0)).astype(int)
            x0, y0 = max(x0, 0), max(y0, 0)
            crop = im.crop((x0, y0, x1, y1)).convert("RGBA")
            m = Image.new("L", crop.size, 0)
            ImageDraw.Draw(m).polygon([(float(a - x0), float(b - y0)) for a, b in xy], fill=255)
            crop.putalpha(m)
            self.cache[key] = (crop, f"{Path(img).name}#{cls}")
        return self.cache[key]


def paste_item(bg, crop, size_px, angle, cx, cy):
    """bg: float32 HxWx3; crop: RGBA PIL. Scales so the longest side = size_px (sub-pixel -> alpha scaled)."""
    from PIL import Image
    c = crop.rotate(angle, resample=Image.BICUBIC, expand=True)
    a = np.asarray(c.split()[3])
    ys, xs = np.nonzero(a > 10)
    if len(xs) == 0:
        return 0.0
    c = c.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    w, h = c.size
    s = size_px / max(w, h)
    nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    c = c.resize((nw, nh), Image.LANCZOS)
    arr = np.asarray(c, np.float32)
    rgb, al = arr[..., :3], arr[..., 3:] / 255.0
    if size_px < 1:
        al = al * (size_px ** 2) * (h / max(w, h) if w >= h else w / max(w, h))
    H, W, _ = bg.shape
    x0, y0 = int(round(cx - nw / 2)), int(round(cy - nh / 2))
    xa, ya, xb, yb = max(x0, 0), max(y0, 0), min(x0 + nw, W), min(y0 + nh, H)
    if xb <= xa or yb <= ya:
        return 0.0
    sub = (slice(ya - y0, yb - y0), slice(xa - x0, xb - x0))
    bg[ya:yb, xa:xb] = bg[ya:yb, xa:xb] * (1 - al[sub]) + rgb[sub] * al[sub]
    return float(al[sub].sum())


def sample_class(mix, rng):
    ks = list(mix)
    p = np.array([mix[k] for k in ks], float)
    return ks[int(rng.choice(len(ks), p=p / p.sum()))]


# ------------------------------------------------------------------ gen
def gen(a):
    from PIL import Image
    t0 = time.time()
    rng = np.random.default_rng(SEED)
    rows = select_rows()
    TILES.mkdir(parents=True, exist_ok=True)
    pats = water_patches(rng)
    polys = tun_polys()
    cut = Cutouts(polys, rng)
    print(f"строк {len(rows)}, событий {len({r['event_id'] for r in rows})}, фонов воды {len(pats)}, "
          f"полигонов TUN {sum(len(v) for v in polys.values())}", flush=True)
    tiles = []
    for r in rows:
        n_tiles_total = r["area_km2"] * 1e6 / TILE_M2       # площадь A строки в плитках (дробное)
        nt = max(2, int(round(math.sqrt(n_tiles_total))))   # раскладка по сетке nt × nt плиток
        lo, hi = SIZE_CM.get(r["size_class"], (2.0, 50.0))
        items = []
        for k in range(r["n_csv"]):
            tx, ty = int(rng.integers(nt)), int(rng.integers(nt))
            size_cm = float(np.exp(rng.uniform(np.log(lo), np.log(hi))))
            size_cm = min(size_cm, TILE_M * 100 * 0.8)
            margin = size_cm / 100 / 2 / GSD0 + 1
            px = float(rng.uniform(margin, TILE_PX - margin))
            py = float(rng.uniform(margin, TILE_PX - margin))
            items.append(dict(tile=(tx, ty), x=px, y=py, size_cm=round(size_cm, 1), cls=sample_class(r["mix"], rng),
                              angle=float(rng.uniform(0, 360))))
        by_tile: dict = {}
        for it in items:
            by_tile.setdefault(it["tile"], []).append(it)
        empties = []
        free = nt * nt - len(by_tile)
        for _ in range(min(N_EMPTY, free)):
            for _ in range(50):
                t = (int(rng.integers(nt)), int(rng.integers(nt)))
                if t not in by_tile and t not in empties:
                    empties.append(t)
                    break
        r.update(window_side_m=round(math.sqrt(r["area_km2"]) * 1000, 1), n_tiles_total=round(n_tiles_total, 1),
                 n_tiles_obj=len(by_tile), window_area_km2=r["area_km2"])
        for kind, tl in (("obj", list(by_tile)), ("empty", empties)):
            for t in tl:
                bg = pats[int(rng.integers(len(pats)))].astype(np.float32)
                bg = np.rot90(bg, int(rng.integers(4))).copy()
                if rng.random() < 0.5:
                    bg = bg[:, ::-1].copy()
                bg *= float(rng.uniform(0.9, 1.1))
                its = by_tile.get(t, [])
                gt = []
                for it in its:
                    crop, src = cut.get(it["cls"])
                    vis = paste_item(bg, crop, it["size_cm"] / 100 / GSD0, it["angle"], it["x"], it["y"])
                    gt.append(dict(x=round(it["x"], 1), y=round(it["y"], 1), size_cm=it["size_cm"], cls=it["cls"],
                                   src=src, alpha_px=round(vis, 2)))
                tid = f"{r['sample_id']}_{kind}_{t[0]}_{t[1]}"
                Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8)).save(TILES / f"{tid}.png")
                tiles.append(dict(tile_id=tid, sample_id=r["sample_id"], kind=kind, n_items=len(its), items=gt))
    man = dict(seed=SEED, gsd0=GSD0, tile_px=TILE_PX, tile_m=TILE_M, rows=rows, tiles=tiles,
               n_water_patches=len(pats), n_polys={k: len(v) for k, v in polys.items()},
               built=time.strftime("%Y-%m-%dT%H:%M:%S"), minutes=round((time.time() - t0) / 60, 2))
    (WORK / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    print(f"плиток {len(tiles)} (с предметами {sum(t['kind'] == 'obj' for t in tiles)}), "
          f"предметов {sum(t['n_items'] for t in tiles)}, {man['minutes']} мин", flush=True)


# ------------------------------------------------------------------ infer
def degrade(im, gsd):
    """PIL RGB 256 px at 0.05 m -> box-average to GSD -> bilinear back to IN_PX (0.02 m/px input scale)."""
    from PIL import Image
    if gsd <= GSD0 + 1e-9:
        return im.resize((IN_PX, IN_PX), Image.BICUBIC), GSD0
    n = max(1, int(round(TILE_M / gsd)))
    small = im.resize((n, n), Image.BOX)
    return small.resize((IN_PX, IN_PX), Image.BILINEAR), TILE_M / n


def infer(a):
    import torch
    from PIL import Image
    sys.path.insert(0, str(ROOT / "src"))
    from macroplastic.photo_count.model import load_model
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = load_model(WEIGHTS, dev)
    man = json.loads((WORK / "manifest.json").read_text(encoding="utf-8"))
    tiles = man["tiles"]
    for g in a.gsd:
        t0 = time.time()
        res = {}
        ims, ids = [], []

        def flush():
            with torch.inference_mode():
                from torchvision.transforms.functional import to_tensor
                ts = [to_tensor(x).to(dev) for x in ims]
                with torch.autocast("cuda", enabled=(dev == "cuda")):
                    outs = model(ts)
                for tid, p in zip(ids, outs):
                    keep = (p["labels"] == 1) & (p["scores"] >= 0.3)
                    b = p["boxes"][keep].float().cpu().numpy() * (TILE_PX / IN_PX)
                    s = p["scores"][keep].float().cpu().numpy()
                    res[tid] = [[round(float(v), 1) for v in bb] + [round(float(ss), 3)] for bb, ss in zip(b, s)]
            ims.clear()
            ids.clear()

        geff = None
        for t in tiles:
            im = Image.open(TILES / f"{t['tile_id']}.png").convert("RGB")
            x, geff = degrade(im, g)
            ims.append(x)
            ids.append(t["tile_id"])
            if len(ims) == a.batch:
                flush()
        if ims:
            flush()
        out = dict(gsd=g, gsd_eff=geff, device=dev, weights=str(WEIGHTS.relative_to(ROOT)), min_score=0.3,
                   n_tiles=len(res), seconds=round(time.time() - t0, 1), preds=res)
        (WORK / f"preds_{g:g}.json").write_text(json.dumps(out), encoding="utf-8")
        print(f"GSD {g} (эфф. {geff:.3f}): {len(res)} плиток за {out['seconds']} с на {dev}", flush=True)


# ------------------------------------------------------------------ score
def _font(sz):
    from PIL import ImageFont
    for f in ("C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(f):
            return ImageFont.truetype(f, sz)
    return ImageFont.load_default()


def score(a):
    from PIL import Image, ImageDraw
    card = json.loads(CARD.read_text(encoding="utf-8"))
    thr = float(card["threshold"])
    man = json.loads((WORK / "manifest.json").read_text(encoding="utf-8"))
    rows, tiles = man["rows"], man["tiles"]
    preds = {}
    for g in GSDS:
        f = WORK / f"preds_{g:g}.json"
        if f.exists():
            preds[g] = json.loads(f.read_text(encoding="utf-8"))
    gs = sorted(preds)
    by_row: dict = {}
    for t in tiles:
        by_row.setdefault(t["sample_id"], []).append(t)

    def matched(g, t):
        """Предметы плитки, у которых центр внутри рамки детекции (± 3 пикс. + полразмера): список bool."""
        bs = [b for b in preds[g]["preds"].get(t["tile_id"], []) if b[4] >= thr]
        out = []
        for it in t["items"]:
            m = 3 + it["size_cm"] / 100 / GSD0 / 2
            out.append(any(b[0] - m <= it["x"] <= b[2] + m and b[1] - m <= it["y"] <= b[3] + m for b in bs))
        return out

    n_match = {g: sum(sum(matched(g, t)) for t in tiles if t["kind"] == "obj") for g in gs}

    def ndet(g, tid):
        return sum(1 for b in preds[g]["preds"].get(tid, []) if b[4] >= thr)

    fp_rate = {}
    for g in gs:
        e = [ndet(g, t["tile_id"]) for t in tiles if t["kind"] == "empty"]
        fp_rate[g] = dict(fp_per_tile=float(np.mean(e)) if e else 0.0, n_empty_tiles=len(e),
                          n_fp=int(np.sum(e)), fp_per_km2=float(np.mean(e)) / TILE_M2 * 1e6 if e else 0.0)
    per_row = []
    for r in rows:
        rt = by_row.get(r["sample_id"], [])
        rec = dict(r)
        rec.pop("mix", None)
        rec["by_gsd"] = {}
        for g in gs:
            d_obj = sum(ndet(g, t["tile_id"]) for t in rt if t["kind"] == "obj")
            n_emp_rest = r["n_tiles_total"] - r["n_tiles_obj"]
            n_hat = d_obj + fp_rate[g]["fp_per_tile"] * n_emp_rest
            conc_hat = n_hat / r["window_area_km2"]
            rec["by_gsd"][f"{g:g}"] = dict(det_obj_tiles=d_obj, fp_extrap=round(fp_rate[g]["fp_per_tile"] * n_emp_rest, 2),
                                          n_found=round(n_hat, 2), conc_pred=round(conc_hat, 2))
        per_row.append(rec)

    def mets(g):
        k = f"{g:g}"
        y = np.array([r["conc_csv"] for r in per_row])
        p = np.array([r["by_gsd"][k]["conc_pred"] for r in per_row])
        n = np.array([r["n_csv"] for r in per_row])
        nh = np.array([r["by_gsd"][k]["n_found"] for r in per_row])
        dobj = np.array([r["by_gsd"][k]["det_obj_tiles"] for r in per_row])
        pos = y > 0
        within = np.abs(p[pos] - y[pos]) <= 0.3 * y[pos]
        # вариант «без ложных на пустой воде» (только детекции на плитках с предметами) — чтобы разделить
        # недосчёт (recall) и ложные срабатывания на воде
        area = np.array([r["area_km2"] for r in per_row])
        q = dobj / area
        noFP = dict(mae_items_km2=round(float(np.mean(np.abs(q - y))), 2), bias_items_km2=round(float(np.mean(q - y)), 2),
                    rel_bias=round(float(q.sum() / y.sum() - 1), 4),
                    pearson=round(float(np.corrcoef(y, q)[0, 1]), 4) if q.std() > 0 else None,
                    share_within_30pct=round(float((np.abs(q[pos] - y[pos]) <= 0.3 * y[pos]).mean()), 4))
        r_p = float(np.corrcoef(y, p)[0, 1]) if p.std() > 0 else None
        from scipy.stats import spearmanr
        r_s = float(spearmanr(y, p).correlation) if p.std() > 0 else None
        rl = float(np.corrcoef(np.log1p(y), np.log1p(p))[0, 1]) if p.std() > 0 else None
        return dict(gsd=g, gsd_eff=round(preds[g]["gsd_eff"], 3), n_rows=len(y),
                    mae_items_km2=round(float(np.mean(np.abs(p - y))), 2),
                    bias_items_km2=round(float(np.mean(p - y)), 2),
                    mean_csv_items_km2=round(float(y.mean()), 2), mean_pred_items_km2=round(float(p.mean()), 2),
                    rel_bias=round(float(p.sum() / y.sum() - 1), 4),
                    pearson=None if r_p is None else round(r_p, 4), spearman=None if r_s is None else round(r_s, 4),
                    pearson_log1p=None if rl is None else round(rl, 4),
                    share_within_30pct=round(float(within.mean()), 4), n_rows_csv_positive=int(pos.sum()),
                    count_mae_items=round(float(np.mean(np.abs(nh - n))), 3),
                    recall_obj_tiles=round(float(np.minimum(dobj, n).sum() / max(n.sum(), 1)), 4),
                    recall_matched=round(n_match[g] / max(int(n.sum()), 1), 4),
                    items_total_csv=int(n.sum()), det_total_obj_tiles=int(dobj.sum()),
                    fp=fp_rate[g], seconds_infer=preds[g]["seconds"], device=preds[g]["device"],
                    item_tiles_only_no_fp=noFP)

    M = {f"{g:g}": mets(g) for g in gs}
    # recall по размерам (все плитки с предметами; предмет «найден», если в его плитке детекций ≥ предметов — по
    # плитке: доля найденных = min(det, n)/n) — только для однопредметных плиток, чтобы не смешивать
    size_bins = [0, 5, 10, 20, 50, 100, 1e9]
    size_lab = ["< 5 см", "5–10 см", "10–20 см", "20–50 см", "50–100 см", "≥ 100 см"]
    rec_size = {}
    for g in gs:
        hits = [[0, 0] for _ in size_lab]
        for t in tiles:
            if t["kind"] != "obj":
                continue
            for it, ok in zip(t["items"], matched(g, t)):
                k = int(np.digitize(it["size_cm"], size_bins) - 1)
                hits[k][1] += 1
                hits[k][0] += int(ok)
        rec_size[f"{g:g}"] = {lab: dict(found=h[0], n=h[1], recall=round(h[0] / h[1], 4) if h[1] else None)
                             for lab, h in zip(size_lab, hits)}
    OUT_REP.mkdir(parents=True, exist_ok=True)
    OUT_DATA.mkdir(parents=True, exist_ok=True)
    # --- plots
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.8))
    xs = [M[f"{g:g}"]["gsd"] for g in gs]
    ax[0].plot(xs, [M[f"{g:g}"]["recall_matched"] for g in gs], "o-", color="#1f6feb", label="доля найденных предметов (рамка на предмете)")
    ax[0].plot(xs, [M[f"{g:g}"]["item_tiles_only_no_fp"]["share_within_30pct"] for g in gs], "^--", color="#2b8a3e",
               label="±30 % без ложных на пустой воде")
    ax[0].plot(xs, [M[f"{g:g}"]["share_within_30pct"] for g in gs], "s-", color="#d9480f", label="доля строк в пределах ±30 %")
    ax[0].set_xscale("log")
    ax[0].set_xlabel("разрешение снимка (GSD), м/пикс.")
    ax[0].set_ylim(-0.02, 1.02)
    ax[0].axvline(10, color="#888", ls="--", lw=1)
    ax[0].text(10, 0.9, "Sentinel-2\n10 м", ha="right", fontsize=9, color="#555")
    ax[0].set_title("Точность подсчёта от разрешения (СИНТЕТИКА)")
    ax[0].legend(fontsize=9)
    ax[0].grid(alpha=0.3)
    g0 = f"{gs[0]:g}"
    y = np.array([r["conc_csv"] for r in per_row])
    p = np.array([r["by_gsd"][g0]["conc_pred"] for r in per_row])
    qn = np.array([r["by_gsd"][g0]["det_obj_tiles"] / r["area_km2"] for r in per_row])
    ax[1].scatter(y + 1, p + 1, s=12, alpha=0.6, color="#1f6feb", label="с ложными на пустой воде (по всей A)")
    ax[1].scatter(y + 1, qn + 1, s=12, alpha=0.6, color="#2b8a3e", marker="^", label="только плитки с предметами")
    lim = [1, max(y.max(), p.max(), qn.max()) * 1.5 + 1]
    ax[1].plot(lim, lim, "k--", lw=1)
    ax[1].fill_between(lim, [v * 0.7 for v in lim], [v * 1.3 for v in lim], color="#aaa", alpha=0.25, label="±30 %")
    ax[1].set_xscale("log")
    ax[1].set_yscale("log")
    ax[1].set_xlabel("концентрация по CSV, шт./км² (+1)")
    ax[1].set_ylabel(f"предсказано счётчиком на GSD {gs[0]:g} м, шт./км² (+1)")
    ax[1].set_title(f"{len(per_row)} строк CSV · GSD {gs[0]:g} м: ложные {M[g0]['fp']['fp_per_km2']:.0f} шт./км² на чистой воде", fontsize=10)
    ax[1].legend(fontsize=9)
    ax[1].grid(alpha=0.3)
    fig.suptitle(BADGE, color="#b00", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT_REP / "prime_csv_resolution.png", dpi=110)
    plt.close(fig)
    # --- примеры (одна мозаика в reports/prime, не в демо-данные)
    f14, f12 = _font(14), _font(12)
    show_g = [g for g in (0.05, 0.5, 10.0) if g in preds]
    ex_rng = np.random.default_rng(SEED)
    cand = [r for r in per_row if any(t["kind"] == "obj" for t in by_row.get(r["sample_id"], []))]
    ex = [cand[int(i)] for i in ex_rng.choice(len(cand), size=min(6, len(cand)), replace=False)]
    P = 220
    mos = Image.new("RGB", (P * len(show_g), (P + 26) * len(ex) + 26), (18, 22, 28))
    for i, r in enumerate(ex):
        rt = [t for t in by_row[r["sample_id"]] if t["kind"] == "obj"]
        t = max(rt, key=lambda t: max(it["size_cm"] for it in t["items"]))
        im0 = Image.open(TILES / f"{t['tile_id']}.png").convert("RGB")
        y0 = i * (P + 26)
        for j, g in enumerate(show_g):
            x, _ = degrade(im0, g)
            x = x.resize((P, P), Image.BILINEAR)
            dr = ImageDraw.Draw(x)
            sc = P / TILE_PX
            for b in preds[g]["preds"].get(t["tile_id"], []):
                if b[4] >= thr:
                    dr.rectangle([b[0] * sc, b[1] * sc, b[2] * sc, b[3] * sc], outline=(255, 197, 61), width=2)
            mos.paste(x, (j * P, y0 + 22))
            ImageDraw.Draw(mos).text((j * P + 4, y0 + 4), f"{r['sample_id']} · GSD {g:g} м · найдено "
                                     f"{ndet(g, t['tile_id'])} из {t['n_items']} ({max(it['size_cm'] for it in t['items']):.0f} см)",
                                     fill=(230, 230, 230), font=f12)
    ImageDraw.Draw(mos).text((4, mos.size[1] - 20), BADGE + " · эксперимент, плитки 12,8 × 12,8 м", fill=(255, 120, 120),
                             font=f12)
    mos.save(OUT_REP / "prime_csv_examples.jpg", quality=80, optimize=True)
    meta = dict(
        badge=BADGE, kind="synthetic_experiment", seed=SEED, built=time.strftime("%Y-%m-%dT%H:%M:%S"),
        status="ЭКСПЕРИМЕНТ, не метрика качества сервиса (§55а): счётчик на нарисованных нами кадрах",
        generator="scripts/case/prime_csv.py gen|infer|score", csv="task/macroplastic_marine_samples.csv",
        selection="все строки верхнего уровня (parent_sample_id пуст) с items_count, sampled_area_km2 и "
                  "concentration_items_km2 — без случайной выборки",
        n_rows=len(per_row), n_events=len({r["event_id"] for r in per_row}),
        n_tiles=len(tiles), n_tiles_obj=sum(t["kind"] == "obj" for t in tiles),
        n_items=sum(t["n_items"] for t in tiles),
        counter=dict(weights="weights/photo_count/frcnn_winans_spatial_fp16.pth", version=card.get("version"),
                     threshold=thr, retrained_on_these=False,
                     input_scale=f"плитка {TILE_M} м подаётся {IN_PX} пикс. (= {MODEL_GSD} м/пикс., как при обучении)"),
        background="реальная вода с дрона: PLP 2021–2023 (Zenodo 7085112 / 10046182), окна 256 пикс. без мишеней",
        objects="вырезанные по полигонам реальные предметы TUN-MarineLitter (Zenodo 21965497, CC BY 4.0)",
        window="окно = площадь A строки (квадрат, плитки 12,8 м), N предметов равномерно-случайно (та же N/A); "
               "рисуются только плитки с предметами + 3 пустые на строку; ложные на остальных пустых — средняя "
               "частота по всем пустым плиткам (пул)",
        sizes="длинная сторона лог-равномерно в пределах size_class (допущение): " + json.dumps(SIZE_CM, ensure_ascii=False),
        not_proven=["что спутник считает штуки — не доказывает (на 10 м предметы не видны)",
                    "реальную точность на реальных снимках скоплений — таких снимков у нас нет",
                    "распределение размеров/форм/погружения предметов в море — взяты допущения",
                    "съёмку всей площади A — в реальности это тысячи кадров дрона"])
    metrics = dict(meta=meta, metrics_by_gsd=M, recall_by_size=rec_size, fp_on_empty_water=fp_rate,
                   figures=["reports/prime/prime_csv_resolution.png", "reports/prime/prime_csv_examples.jpg"],
                   per_row=[dict(sample_id=r["sample_id"], n_csv=r["n_csv"], conc_csv=r["conc_csv"],
                                 area_km2=r["area_km2"], n_tiles_total=r["n_tiles_total"],
                                 size_class=r["size_class"], by_gsd=r["by_gsd"]) for r in per_row])
    (OUT_REP / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    # в демо-индекс — только ссылка и итог эксперимента (meta.experiment); по сценам counter = null:
    # счётчик на открытой воде даёт ложные на бликах, число в карточке вводило бы в заблуждение
    didx = OUT_DATA / "index.json"
    if didx.exists():
        d = json.loads(didx.read_text(encoding="utf-8"))
        for sc_ in d["scenes"]:
            sc_["counter"] = None
        d["meta"]["experiment"] = dict(
            label="найдено счётчиком (эксперимент)", show_in_cards=False, metrics="reports/prime/metrics.json",
            figure="reports/prime/prime_csv_resolution.png",
            summary={f"{g:g}": dict(recall=M[f"{g:g}"]["recall_matched"],
                                    fp_per_164m2=round(M[f"{g:g}"]["fp"]["fp_per_tile"], 4)) for g in gs},
            note="наш аэро-счётчик как есть на синтетических кадрах; не метрика качества сервиса")
        didx.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    for g in gs:
        m = M[f"{g:g}"]
        print(f"GSD {g:>5g}: MAE {m['mae_items_km2']:8.2f} шт./км², смещение {m['bias_items_km2']:8.2f}, r {m['pearson']}, "
              f"±30 % {m['share_within_30pct']:.3f}, найдено предметов {m['recall_matched']:.3f}, "
              f"ложных/плитку {m['fp']['fp_per_tile']:.4f} | без ложных: MAE {m['item_tiles_only_no_fp']['mae_items_km2']}, "
              f"±30 % {m['item_tiles_only_no_fp']['share_within_30pct']}, r {m['item_tiles_only_no_fp']['pearson']}")


# ------------------------------------------------------------------ demo (§55а: ДЕМО-МАКЕТ, основной выход)
DEMO_PX = 512                 # кадр 10,24 × 10,24 м при 0,02 м/пикс. (как дрон; GSD обучения аэро-счётчика)
DEMO_GSD = 0.02
DEMO_MAX_ITEMS = 40
DEMO_BADGE = ("ДЕМО: как сервис будет выглядеть на детальных снимках. Изображения и числа — демонстрационные, "
              "не результат модели")
DEMO_NUM_LABEL = "демо-значение (из CSV организаторов)"
CLS_RU = {"Plastic": "пластик", "Fabrics": "сети/верёвки/ткань", "Other": "прочее (пенопласт, смешанное)",
          "Wood": "дерево", "Cardboard": "картон", "Glass": "стекло", "Metal": "металл"}
CLS_RGB = {"Plastic": (255, 197, 61), "Fabrics": (120, 220, 255), "Other": (255, 140, 200), "Wood": (190, 150, 90),
           "Cardboard": (220, 180, 120), "Glass": (160, 255, 160), "Metal": (200, 200, 220)}


def demo(a):
    from PIL import Image, ImageDraw
    t0 = time.time()
    rng = np.random.default_rng(SEED + 1)
    rows = select_rows()
    OUT_DATA.mkdir(parents=True, exist_ok=True)
    pats = water_patches(rng, T=DEMO_PX)
    cut = Cutouts(tun_polys(), rng)
    f11 = _font(11)
    scenes, total = [], 0
    for r in rows:
        lo, hi = SIZE_CM.get(r["size_class"], (2.0, 50.0))
        lo_v = max(lo, 5.0)           # демо: мельче 5 см на 0,02 м почти не видно — не рисуем
        k = int(min(r["n_csv"], DEMO_MAX_ITEMS))
        bg = pats[int(rng.integers(len(pats)))].astype(np.float32)
        bg = np.rot90(bg, int(rng.integers(4))).copy()
        if rng.random() < 0.5:
            bg = bg[:, ::-1].copy()
        cx, cy = rng.uniform(0.35, 0.65, 2) * DEMO_PX
        sig = rng.uniform(0.08, 0.16) * DEMO_PX
        boxes = []
        for _ in range(k):
            size_cm = float(np.exp(rng.uniform(np.log(lo_v), np.log(max(hi, lo_v * 1.5)))))
            size_px = min(size_cm / 100 / DEMO_GSD, DEMO_PX * 0.3)
            x = float(np.clip(rng.normal(cx, sig), size_px, DEMO_PX - size_px))
            y = float(np.clip(rng.normal(cy, sig), size_px, DEMO_PX - size_px))
            cls = sample_class(r["mix"], rng)
            crop, src = cut.get(cls)
            paste_item(bg, crop, size_px, float(rng.uniform(0, 360)), x, y)
            h = size_px / 2 + 2
            boxes.append(dict(cls=cls, cls_ru=CLS_RU.get(cls, cls), size_cm=round(size_cm, 1),
                              box=[round(max(x - h, 0) / DEMO_PX, 4), round(max(y - h, 0) / DEMO_PX, 4),
                                   round(min(x + h, DEMO_PX) / DEMO_PX, 4), round(min(y + h, DEMO_PX) / DEMO_PX, 4)],
                              src=src))
        img = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8))
        raw = OUT_DATA / f"{r['sample_id']}.jpg"
        img.save(raw, quality=85, optimize=True)
        # вариант с рамками (как будет выглядеть результат сервиса)
        bx = img.copy()
        dr = ImageDraw.Draw(bx)
        for b in boxes:
            x0, y0, x1, y1 = (v * DEMO_PX for v in b["box"])
            dr.rectangle([x0, y0, x1, y1], outline=CLS_RGB.get(b["cls"], (255, 197, 61)), width=2)
        dr.rectangle([0, DEMO_PX - 18, DEMO_PX, DEMO_PX], fill=(0, 0, 0))
        dr.text((4, DEMO_PX - 16), "ДЕМО · не результат модели · строка CSV " + r["sample_id"], fill=(255, 150, 150),
                font=f11)
        fb = OUT_DATA / f"{r['sample_id']}_boxes.jpg"
        bx.save(fb, quality=82, optimize=True)
        for f in (raw, fb):
            q = 75
            while f.stat().st_size > 150_000 and q >= 40:
                Image.open(f).save(f, quality=q, optimize=True)
                q -= 10
            total += f.stat().st_size
        by_cls: dict = {}
        for b in boxes:
            by_cls[b["cls_ru"]] = by_cls.get(b["cls_ru"], 0) + 1
        scenes.append(dict(
            id=r["sample_id"], csv_row_id=r["sample_id"], event_id=r["event_id"], source_id=r["source_id"],
            source=r["source_short"], region=r["region"], date=r["date"], lat=r["lat"], lon=r["lon"],
            size_class=r["size_class"], category=r["category"], categories=r["categories"],
            n_items=r["n_csv"], area_km2=r["area_km2"], items_per_km2=r["conc_csv"],
            numbers_label=DEMO_NUM_LABEL, gsd_m=DEMO_GSD, frame_m=round(DEMO_PX * DEMO_GSD, 2),
            frame_items_drawn=k, frame_classes=by_cls, boxes=boxes,
            image=f"/api/prime/csv_img/{r['sample_id']}.jpg",
            image_boxes=f"/api/prime/csv_img/{r['sample_id']}_boxes.jpg",
            title=f"Демо по строке CSV #{r['sample_id']}",
            card=f"{r['n_csv']} шт. на {r['area_km2']:.3g} км² · {r['conc_csv']:.1f} шт./км² — {DEMO_NUM_LABEL}",
            frame_note=(f"кадр {DEMO_PX * DEMO_GSD:.1f} × {DEMO_PX * DEMO_GSD:.1f} м, 0,02 м/пикс.: нарисовано {k} предм. "
                        f"(сгущено для показа; в CSV {r['n_csv']} шт. на {r['area_km2']:.3g} км²)"),
            counter=None))
    idx = dict(meta=dict(
        badge=DEMO_BADGE, toggle="PRIME MODE · демо-данные", kind="demo_synthetic", numbers_label=DEMO_NUM_LABEL,
        quality_metrics=None, quality_note="PRIME не даёт метрик качества: это демо-макет интерфейса по запросу жюри",
        seed=SEED + 1, built=time.strftime("%Y-%m-%dT%H:%M:%S"), generator="scripts/case/prime_csv.py demo",
        csv="task/macroplastic_marine_samples.csv",
        selection="все строки верхнего уровня CSV с items_count, sampled_area_km2, concentration_items_km2 "
                  "(parent_sample_id пуст) — без случайной выборки",
        n_scenes=len(scenes), n_events=len({s["event_id"] for s in scenes}),
        image_sources=["вода: кадры дрона PLP 2021–2023 (Plastic Litter Project, Zenodo 7085112 / 10046182)",
                       "предметы: вырезки по полигонам TUN-MarineLitter (Zenodo 21965497, CC BY 4.0)"],
        classes_map="категория CSV → класс вырезок TUN (сети/верёвки → ткань/прочее; пенопласт → прочее/пластик; "
                    "all materials → смесь)",
        not_proven=["это не снимки мест из CSV и не наблюдение", "числа — из строк CSV, не посчитаны моделью",
                    "рамки — известные генератору положения, не детекции"],
        previews_bytes=total, minutes=round((time.time() - t0) / 60, 2)), scenes=scenes)
    exp = OUT_REP / "metrics.json"
    if exp.exists():
        idx["meta"]["experiment"] = "reports/prime/metrics.json (счётчик на синтетике — эксперимент, отдельно)"
    (OUT_DATA / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"демо-сцен {len(scenes)} (событий {idx['meta']['n_events']}), картинки {total / 1e6:.1f} МБ, "
          f"{idx['meta']['minutes']} мин")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["demo", "gen", "infer", "score"])
    ap.add_argument("--gsd", type=float, nargs="+", default=GSDS)
    ap.add_argument("--batch", type=int, default=8)
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    {"demo": demo, "gen": gen, "infer": infer, "score": score}[a.cmd](a)


if __name__ == "__main__":
    main()
