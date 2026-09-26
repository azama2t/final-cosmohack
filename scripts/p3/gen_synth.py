"""П3 (L129): генератор синтетических пар «окно S2 → число предметов» на реальных компонентах (configs/p3_synth.yaml).

  CUDA_VISIBLE_DEVICES=-1 .venv/Scripts/python.exe scripts/p3/gen_synth.py [--n 240000]

Шаги (всё сохраняется в out/p3/, вне git):
 1. sizes.npz      — площади предметов в плане, м²: ADIS Objects.csv (area, классы 1–3: буй/волокно/жёсткий пластик),
                     Lebreton 2018 MosaicDebrisInfo (Topview area), Winans 2023 рамки × 0.02² × 0.6; бутылка 0.026.
 2. marida.npz     — формы спектра мусора: пиксели MARIDA класса 1 (Marine Debris), Δ = пиксель − медиана воды (класс 7)
                     патча, Δ_B8 > 0.01; окна воды MARIDA 15 × 15 (≥ 60 % класса 7, без других классов).
 3. bg_blocks.npz  — блоки воды наших сцен S2 L2A (data/live/*/*/bands.tif, SCL = 6, P_lgbm_live < 0.3), БЕЗ 5 сцен
                     проверки чистой воды и без сцены с бликом; окна начинаются с кратных 6 пикселям (сетки 20/60 м).
 4. geom.npz       — геометрия: для каждого окна — источник фона, раскладка, покрытие на сетке 1 м → PSF (гауссиана,
                     σ 0.4–0.6 нативного пикселя, сдвиг ±0.3 пикселя на группу каналов 10/20/60 м) → агрегирование в пиксели
                     канала → карты A (n, 3 группы, 15, 15) в «эквиваленте покрытия мишени PLP»; метки N_eq (бутылки 1.5 л),
                     N_nat (предметы реальных размеров), случайные величины для спектра (одинаковые во всех фолдах).
Спектр мусора подставляется в lodo.py (зависит от фолда: эндмемберы PLP — только из обучающих дат).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import p3_common as pc  # noqa: E402

OUT = ROOT / "out" / "p3"
CFG = yaml.safe_load((ROOT / "configs" / "p3_synth.yaml").read_text(encoding="utf-8"))
CLEAN = [s.replace("live/", "") for s in CFG["check"]["clean_water"]["scenes"]]
FILL_PLP = 416 * 0.026 / 25          # физическая доля бутылок в площади мишени PLP (0.433)
EQ_PER_M2 = 16.64                    # бутылок на м² площади мишени


# ------------------------------------------------------------------ 1. размеры
def build_sizes():
    a = pd.read_csv(ROOT / "data/extra/field/adis/Objects.csv")
    adis = a.loc[a["class"].isin([1, 2, 3]) & (a.area > 0), "area"].to_numpy(float)
    lb = pd.read_csv(ROOT / "data/extra/field/csv/Lebreton2018_SamplingInformation-MosaicDebrisInfo.csv")
    col = [c for c in lb.columns if c.lower().startswith("topview area")][0]
    leb = pd.to_numeric(lb[col].astype(str).str.replace(",", "."), errors="coerce").dropna().to_numpy(float)
    leb = leb[leb > 0]
    wd = ROOT / "data/extra/count_ds/winans2023_hawaii_aerial/imagery_and_labels"
    w = pd.concat([pd.read_csv(wd / "training_data.csv"), pd.read_csv(wd / "evaluation_data.csv")])
    win = ((w.xmax - w.xmin) * (w.ymax - w.ymin) * 0.02 ** 2 * 0.6).to_numpy(float)
    win = win[win > 0]
    s = dict(adis=adis, lebreton=leb, winans=win, bottle=np.array([0.026]))
    np.savez(OUT / "sizes.npz", **s)
    return s, {k: dict(n=int(len(v)), median_m2=float(np.median(v)), p90_m2=float(np.percentile(v, 90))) for k, v in s.items()}


# ------------------------------------------------------------------ 2. MARIDA
def build_marida(rng):
    import rasterio
    shapes, wins = [], []
    files = [f for f in sorted(glob.glob(str(ROOT / "data/MARIDA/patches/*/*.tif"))) if not f.endswith(("_cl.tif", "_conf.tif"))]
    for f in files:
        cl = rasterio.open(f[:-4] + "_cl.tif").read(1)
        if not (cl == 7).any():
            continue
        x = rasterio.open(f).read().astype(np.float32)
        wat = (cl == 7) & np.isfinite(x).all(0)
        if wat.sum() < 20:
            continue
        med = np.median(x[:, wat], axis=1)
        md = (cl == 1) & np.isfinite(x).all(0)
        if md.any():
            d = x[:, md].T - med[None]
            d = d[d[:, pc.BI["B8"]] > 0.01]
            shapes.append(d)
        # окна воды: ≥ 60 % класса 7, прочие размеченные классы отсутствуют
        other = (cl != 0) & (cl != 7)
        H, Wd = cl.shape
        for _ in range(80):
            r, c = rng.integers(0, H - pc.W), rng.integers(0, Wd - pc.W)
            cw = cl[r:r + pc.W, c:c + pc.W]
            xw = x[:, r:r + pc.W, c:c + pc.W]
            if (cw == 7).mean() >= 0.6 and not other[r:r + pc.W, c:c + pc.W].any() and np.isfinite(xw).all():
                wins.append(xw)
    shapes = np.concatenate(shapes).astype(np.float32)
    shapes = shapes / shapes[:, pc.BI["B8"]][:, None]          # форма, нормированная на Δ_B8
    wins = np.stack(wins).astype(np.float32) if wins else np.zeros((0, 11, pc.W, pc.W), np.float32)
    np.savez(OUT / "marida.npz", shapes=shapes, water_windows=wins)
    return shapes, wins


# ------------------------------------------------------------------ 3. фон наших сцен
def build_bg(rng, n_blocks=2, bs=240):
    import rasterio
    from rasterio.windows import Window
    blocks, meta = [], []
    for sd in sorted(glob.glob(str(ROOT / "data/live/*/*/"))):
        key = "/".join(Path(sd).parts[-2:])
        if key in CLEAN or not os.path.exists(os.path.join(sd, "bands.tif")):
            continue
        sj = json.loads(Path(sd, "scene.json").read_text(encoding="utf-8"))
        if sj.get("glint_or_haze"):
            continue
        scl = rasterio.open(os.path.join(sd, "scl.tif")).read(1)
        pf = os.path.join(sd, "prob_lgbm.tif")
        prob = rasterio.open(pf).read(1) if os.path.exists(pf) else np.zeros_like(scl)
        H, Wd = scl.shape
        cand = []
        for r in range(0, H - bs, 60):
            for c in range(0, Wd - bs, 60):
                s = scl[r:r + bs, c:c + bs]
                if (s == 6).mean() >= 0.97 and (prob[r:r + bs, c:c + bs] < 0.3 * 255).all():
                    cand.append((r, c))
        if not cand:
            continue
        pick = [cand[i] for i in rng.choice(len(cand), min(n_blocks, len(cand)), replace=False)]
        with rasterio.open(os.path.join(sd, "bands.tif")) as ds:
            for r, c in pick:
                x = ds.read(window=Window(c, r, bs, bs)).astype(np.float32)[pc.IDX12_TO_11]
                ok = (scl[r:r + bs, c:c + bs] == 6) & np.isfinite(x).all(0)
                blocks.append(np.where(ok[None], x, np.nan).astype(np.float32))
                meta.append(dict(scene=key, r=r, c=c))
        print("bg", key, len(pick), flush=True)
    blocks = np.stack(blocks)
    # допустимые начала окон: кратны 6 (относительно блока, а блок кратен 60 → сетки 20/60 м совпадают), вся вода
    starts = []
    for b in range(len(blocks)):
        ok = np.isfinite(blocks[b]).all(0)
        for r in range(0, bs - pc.W, 6):
            for c in range(0, bs - pc.W, 6):
                if ok[r:r + pc.W, c:c + pc.W].all():
                    starts.append((b, r, c))
    starts = np.array(starts, np.int32)
    np.savez(OUT / "bg_blocks.npz", blocks=blocks, starts=starts)
    pd.DataFrame(meta).to_csv(OUT / "bg_blocks.csv", index=False)
    return blocks, starts, meta


# ------------------------------------------------------------------ 4. геометрия
CAN = 180  # холст 180 × 180 м (кратен 10/20/60 м), окно 15 × 15 пикселей = [0, 150) м


def render_batch(rng, B, sizes):
    g = CFG["generator"]
    lay_names = list(g["layouts"])
    lay_p = np.array([g["layouts"][k] for k in lay_names], float)
    src_names = list(g["item_sizes"]["mix"])
    src_p = np.array([g["item_sizes"]["mix"][k] for k in src_names], float)
    zero = rng.random(B) < g["share_zero"]
    lay = rng.choice(len(lay_names), B, p=lay_p / lay_p.sum())
    src = rng.choice(len(src_names), B, p=src_p / src_p.sum())
    fc = np.exp(rng.uniform(np.log(0.003), np.log(1.0), B))
    yy, xx = np.mgrid[0:CAN, 0:CAN].astype(np.float32) + 0.5
    D = np.zeros((B, CAN, CAN), np.float32)
    cen = 75.0
    for i in range(B):
        if zero[i]:
            continue
        L = lay_names[lay[i]]
        if L == "uniform":
            D[i] = 1.0
        elif L == "cluster":
            s = rng.uniform(3, 30)
            py, px = cen + rng.uniform(-10, 10, 2)
            D[i] = np.exp(-((yy - py) ** 2 + (xx - px) ** 2) / (2 * s * s))
        elif L == "windrow":
            th = rng.uniform(0, np.pi)
            py, px = cen + rng.uniform(-10, 10, 2)
            w = np.exp(rng.uniform(np.log(2), np.log(20)))
            ln = rng.uniform(30, 150)
            u = (xx - px) * np.cos(th) + (yy - py) * np.sin(th)
            v = -(xx - px) * np.sin(th) + (yy - py) * np.cos(th)
            D[i] = ((np.abs(v) < w / 2) & (np.abs(u) < ln / 2)).astype(np.float32)
        else:  # target_rect
            h, w = rng.uniform(2, 12, 2)
            py, px = cen + rng.uniform(-8, 8, 2)
            D[i] = ((np.abs(yy - py) < h / 2) & (np.abs(xx - px) < w / 2)).astype(np.float32)
    mx = D.reshape(B, -1).max(1)
    cexp = np.where(mx[:, None, None] > 0, D / np.maximum(mx, 1e-9)[:, None, None], 0) * fc[:, None, None]
    # предметы: на ячейку 1 м² — размер из библиотеки источника, число ~ Пуассон(покрытие / размер)
    cov = np.zeros_like(cexp)
    nat = np.zeros_like(cexp)
    for i in range(B):
        if zero[i]:
            continue
        lib = sizes[src_names[src[i]]]
        m = cexp[i] > 0
        a = lib[rng.integers(0, len(lib), m.sum())]
        n = rng.poisson(cexp[i][m] / a)
        nat[i][m] = n
        cov[i][m] = np.minimum(1.0, n * a)
    geq = cov / FILL_PLP                                   # эквивалент покрытия мишени PLP
    ci = slice(70, 80)
    n_eq = geq[:, ci, ci].mean((1, 2)) * 100 * EQ_PER_M2   # бутылок 1.5 л в центральном пикселе (до PSF)
    n_nat = nat[:, ci, ci].sum((1, 2))
    # PSF + сдвиг (Фурье), агрегирование в пиксели канала, nearest → 10 м, вырезка 15 × 15
    Fq = np.fft.rfft2(geq)
    fy = np.fft.fftfreq(CAN)[:, None]
    fx = np.fft.rfftfreq(CAN)[None, :]
    lo, hi = g["psf"]["sigma_native_px"]
    shmax = g["psf"]["band_shift_px"]
    A = np.zeros((B, 3, pc.W, pc.W), np.float32)
    for gi, r in enumerate((10, 20, 60)):
        sig = rng.uniform(lo, hi, B) * r
        sh = rng.uniform(-shmax, shmax, (B, 2)) * r
        H = np.exp(-2 * np.pi ** 2 * sig[:, None, None] ** 2 * (fx ** 2 + fy ** 2)) * \
            np.exp(-2j * np.pi * (fy * sh[:, 0, None, None] + fx * sh[:, 1, None, None]))
        img = np.fft.irfft2(Fq * H, s=(CAN, CAN)).astype(np.float32)
        k = CAN // r
        agg = img.reshape(B, k, r, k, r).mean((2, 4))
        up = np.repeat(np.repeat(agg, r // 10, 1), r // 10, 2)
        A[:, gi] = up[:, :pc.W, :pc.W]
    return dict(A=A.astype(np.float16), n_eq=n_eq.astype(np.float32), n_nat=n_nat.astype(np.float32),
                zero=zero, layout=lay.astype(np.int8), src=src.astype(np.int8), fc=fc.astype(np.float32))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=CFG["generator"]["n_train"])
    ap.add_argument("--n_zero_val", type=int, default=20000)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(CFG["generator"]["seed"])
    t0 = time.time()
    sizes, sstat = build_sizes()
    print("sizes", sstat, flush=True)
    shapes, mwins = build_marida(rng)
    print("marida shapes", shapes.shape, "water windows", mwins.shape, f"{time.time() - t0:.0f} s", flush=True)
    blocks, starts, meta = build_bg(rng)
    print("bg blocks", blocks.shape, "starts", len(starts), "scenes", len({m['scene'] for m in meta}),
          f"{time.time() - t0:.0f} s", flush=True)
    n = a.n + a.n_zero_val
    parts = []
    for s in range(0, n, 500):
        parts.append(render_batch(rng, min(500, n - s), sizes))
        if (s // 500) % 40 == 0:
            print("geom", s, f"{time.time() - t0:.0f} s", flush=True)
    G = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    # последние n_zero_val — синтетическая проверка «без мусора» для порога τ: принудительно без мусора
    G["A"][a.n:] = 0
    G["n_eq"][a.n:] = 0
    G["n_nat"][a.n:] = 0
    G["zero"][a.n:] = True
    G["is_zero_val"] = np.arange(n) >= a.n
    # фон: доля MARIDA-воды, остальное — наши сцены; случайные величины спектра (общие для всех фолдов)
    share_m = 0.2 if len(mwins) else 0.0
    use_m = rng.random(n) < share_m
    G["bg_kind"] = use_m.astype(np.int8)
    G["bg_idx"] = np.where(use_m, rng.integers(0, max(len(mwins), 1), n), rng.integers(0, len(starts), n)).astype(np.int64)
    G["spec_marida"] = rng.random(n) < 0.5
    G["marida_idx"] = rng.integers(0, len(shapes), n)
    G["u_bottle"] = rng.random(n).astype(np.float32)                 # доля бутылок в смеси бутылки/пакеты PLP
    G["amp_marida"] = np.exp(rng.normal(0, 0.35, n)).astype(np.float32)
    G["wet"] = np.exp(rng.normal(0, 0.25, n)).astype(np.float32)
    G["jitter"] = np.exp(rng.normal(0, 0.10, (n, 11))).astype(np.float32)
    np.savez(OUT / "geom.npz", **G)
    info = dict(n_train=a.n, n_zero_val=a.n_zero_val, sizes=sstat, marida_shapes=int(len(shapes)),
                marida_water_windows=int(len(mwins)), bg_blocks=int(len(blocks)), bg_window_starts=int(len(starts)),
                bg_scenes=sorted({m["scene"] for m in meta}), clean_excluded=CLEAN,
                share_debris_train=float((~G["zero"][:a.n]).mean()),
                n_eq_quantiles=[float(q) for q in np.percentile(G["n_eq"][:a.n][~G["zero"][:a.n]], [5, 25, 50, 75, 95])],
                seconds=round(time.time() - t0, 1))
    (OUT / "geom_info.json").write_text(json.dumps(info, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False)[:1500])


if __name__ == "__main__":
    main()
