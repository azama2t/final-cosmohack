"""П3 (L129): обучение регрессора «окно S2 → бутылок 1.5 л на пиксель» на синтетике и проверка по правилу
configs/p3_synth.yaml: leave-one-date-out на реальных мишенях A* (PLP2019) + доля ≠0 на чистой воде (5 наших сцен).

  CUDA_VISIBLE_DEVICES=-1 .venv/Scripts/python.exe scripts/p3/lodo.py

Вход: out/p3/geom.npz, bg_blocks.npz, marida.npz (gen_synth.py); out/p3/plp/<дата>.npz, targets.csv (fetch_plp.py).
Выход: weights_exp/p3/fold_<дата>/, weights_exp/p3/final/ (model.txt + meta.json), reports/p3/p3_synth.json,
out/p3/plp_predictions.csv, out/p3/clean_water.csv.
"""
from __future__ import annotations

import glob
import hashlib
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
WEX = ROOT / "weights_exp" / "p3"
CFGF = ROOT / "configs" / "p3_synth.yaml"
CFG = yaml.safe_load(CFGF.read_text(encoding="utf-8"))
EQ = 100 * 16.64                  # бутылок на пиксель при доле мишени 1.0
FLAT = 560.0                      # §34 п.2: геом. среднее 470 и 670 (configs/zone_estimate.yaml)
THR = 0.63
SMOKE = bool(os.environ.get("P3_SMOKE"))  # быстрый прогон кода на малой геометрии (результаты не используются)
DATES = ["20190418", "20190503", "20190518", "20190528", "20190607"]
GROUP_OF_BAND = np.array([0 if pc.NATIVE[b] == 10 else (1 if pc.NATIVE[b] == 20 else 2) for b in pc.BANDS11])


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ------------------------------------------------------------------ эндмемберы PLP (интегральная аномалия мишени)
def plp_groups():
    """Группы пикселей мишеней (связные после расширения на 2 пикселя) по датам:
    Σf_бутылки, Σf_пакеты, Σf_тростник и Σ_окно (R − медиана воды) по 11 каналам."""
    from scipy import ndimage
    T = pd.read_csv(OUT / "plp" / "targets.csv")
    rows = []
    for d in DATES:
        z = np.load(OUT / "plp" / f"{d}.npz")
        b = z["bands"][pc.IDX12_TO_11]
        scl = z["scl"]
        t = T[T.date.astype(str) == d]
        m = np.zeros(scl.shape, bool)
        m[t.row, t.col] = True
        dil = ndimage.binary_dilation(m, iterations=2)
        lab, n = ndimage.label(dil)
        allg = ndimage.binary_dilation(m, iterations=3)
        for k in range(1, n + 1):
            g = lab == k
            # опорная вода — кольцо 3–6 пикселей вокруг группы (локальный уровень у берега), без других мишеней
            ring = ndimage.binary_dilation(g, iterations=4) & ~allg
            wat = ring & (scl == 6) & np.isfinite(b).all(0)
            med = np.median(b[:, wat], axis=1)
            dsum = np.nansum(b[:, g] - med[:, None], axis=1)
            tt = t[lab[t.row, t.col] == k]
            rows.append(dict(date=d, targets="".join(sorted(set(tt.target))), n_px=int(g.sum()), n_water_ref=int(wat.sum()),
                             f_bot=float(tt.frac_bottles.sum()), f_bag=float(tt.frac_bags.sum()),
                             f_reed=float(tt.frac_reeds.fillna(0).sum()), **{f"d_{bb}": float(v) for bb, v in zip(pc.BANDS11, dsum)},
                             noise_b8=float(1.4826 * np.median(np.abs(b[7, wat] - med[7])) * np.sqrt(g.sum()))))
    return pd.DataFrame(rows)


def endmembers(G: pd.DataFrame, train_dates):
    """Один эндмембер «пластик мишени PLP» (бутылки + пакеты) на единицу доли покрытия по дрону:
    Δ = Σ_групп f·ΣΔ / Σ_групп f² (МНК через 0) по группам ОБУЧАЮЩИХ дат.
    Отступление от черновика правила (записано в отчёте, до первого расчёта метрики): два эндмембера бутылки/пакеты по
    4–5 группам коллинеарны (знаки меняются между фолдами), поэтому один общий; опорная вода — кольцо 4–6 пикселей."""
    g = G[G.date.isin(train_dates) & (G.f_reed <= 0.1)]
    f = (g.f_bot + g.f_bag).to_numpy()
    Y = g[[f"d_{b}" for b in pc.BANDS11]].to_numpy()
    e = (f @ Y) / (f @ f)
    return dict(bottle=e, bag=e, reed=np.zeros(11), n_groups=int(len(g)))


# ------------------------------------------------------------------ синтетические окна
class Synth:
    def __init__(self):
        self.G = dict(np.load(OUT / "geom.npz"))
        bg = np.load(OUT / "bg_blocks.npz")
        self.blocks, self.starts = bg["blocks"], bg["starts"]
        m = np.load(OUT / "marida.npz")
        self.shapes, self.mwin = m["shapes"], m["water_windows"]

    def backgrounds(self, idx):
        out = np.empty((len(idx), 11, pc.W, pc.W), np.float32)
        kind, bi = self.G["bg_kind"][idx], self.G["bg_idx"][idx]
        for k, (kd, j) in enumerate(zip(kind, bi)):
            if kd == 1:
                out[k] = self.mwin[j % len(self.mwin)]
            else:
                b, r, c = self.starts[j % len(self.starts)]
                out[k] = self.blocks[b, :, r:r + pc.W, c:c + pc.W]
        return out

    def spectra(self, idx, em, use_marida=True):
        G = self.G
        u = G["u_bottle"][idx][:, None]
        plp = u * em["bottle"][None] + (1 - u) * em["bag"][None]
        mar = self.shapes[G["marida_idx"][idx]] * em["bottle"][7] * G["amp_marida"][idx][:, None]
        sel = (G["spec_marida"][idx] & use_marida)[:, None]
        d = np.where(sel, mar, plp)
        return (d * G["wet"][idx][:, None] * G["jitter"][idx]).astype(np.float32)

    def X(self, idx, em, chunk=20000, use_marida=True):
        parts = []
        for s in range(0, len(idx), chunk):
            ii = idx[s:s + chunk]
            win = self.backgrounds(ii)
            A = self.G["A"][ii].astype(np.float32)                  # (n, 3, 15, 15)
            d = self.spectra(ii, em, use_marida)                     # (n, 11)
            win += A[:, GROUP_OF_BAND] * d[:, :, None, None]
            parts.append(pc.features(win))
        return np.concatenate(parts)


def train(X, y, seed=0):
    import lightgbm as lgb
    p = CFG["model"]["params"]
    params = dict(objective="l1", num_leaves=p["num_leaves"], learning_rate=p["learning_rate"],
                  min_data_in_leaf=p["min_data_in_leaf"], feature_fraction=p["feature_fraction"],
                  bagging_fraction=p["bagging_fraction"], bagging_freq=p["bagging_freq"], seed=seed, verbose=-1,
                  num_threads=16)
    return lgb.train(params, lgb.Dataset(X, y, feature_name=pc.FEATURE_NAMES), num_boost_round=30 if SMOKE else p["n_rounds"])


def gated(m, X, tau):
    p = np.maximum(m.predict(X, num_threads=16), 0)
    return np.where(p >= tau, p, 0.0)


# ------------------------------------------------------------------ реальные проверки
def p1_dates():
    f = OUT / "plp" / "targets_p1.csv"
    if not f.exists():
        return []
    return sorted(set(pd.read_csv(f).date.astype(str)))


def plp_eval_rows(date):
    T = pd.read_csv(OUT / "plp" / "targets.csv")
    if date not in DATES:  # даты A* от L127 (Maathuis 2026): правда = items_in_pixel (бутылки PET), пакетов нет
        T = pd.read_csv(OUT / "plp" / "targets_p1.csv")
    t = T[T.date.astype(str) == date].copy()
    if "items" not in t:
        t["items"] = t.frac_bottles * EQ
    t["campaign"] = t.get("campaign", pd.Series("PLP2019", index=t.index)).fillna("PLP2019")
    t["surface"] = t.get("surface", pd.Series("sea", index=t.index)).fillna("sea")
    z = np.load(OUT / "plp" / f"{date}.npz")
    b = z["bands"][pc.IDX12_TO_11]
    X = pc.features(pc.windows_at(b, t.row.to_numpy(), t.col.to_numpy()))
    return t, X


def clean_water_X(n_per=50000, seed=0):
    import rasterio
    from scipy import ndimage
    rng = np.random.default_rng(seed)
    out = []
    for s in CFG["check"]["clean_water"]["scenes"]:
        d = ROOT / "data" / s
        scl = rasterio.open(d / "scl.tif").read(1)
        b = rasterio.open(d / "bands.tif").read().astype(np.float32)[pc.IDX12_TO_11]
        ok = ndimage.binary_erosion(scl == 6, iterations=2) & np.isfinite(b).all(0)
        ys, xs = np.where(ok)
        k = rng.choice(len(ys), min(n_per, len(ys)), replace=False)
        X = pc.features(pc.windows_at(b, ys[k], xs[k]))
        out.append((s, X, int(ok.sum())))
        log("clean", s, "water px", int(ok.sum()), "sampled", len(k))
    return out


def main():
    t0 = time.time()
    cfg_sha = hashlib.sha256(CFGF.read_bytes()).hexdigest()
    reg = (ROOT / "configs" / "p3_synth.yaml.sha256").read_text().split()[0]
    log("config sha256", cfg_sha[:16], "registered", reg[:16], "OK" if cfg_sha == reg else "MISMATCH")
    S = Synth()
    n = len(S.G["n_eq"])
    zv = np.where(S.G["is_zero_val"])[0]
    tr_all = np.where(~S.G["is_zero_val"])[0]
    rng = np.random.default_rng(1)
    perm = rng.permutation(tr_all)
    n_syn_test = len(perm) // 10
    syn_test, tr = perm[:n_syn_test], perm[n_syn_test:]
    GR = plp_groups()
    GR.to_csv(OUT / "plp_groups.csv", index=False)
    log("PLP groups", len(GR))
    P1D = p1_dates()
    log("L127 A* dates", P1D)
    CW = clean_water_X(n_per=2000 if SMOKE else 50000)
    res = dict(config=str(CFGF.relative_to(ROOT)), config_sha256=cfg_sha, registered_sha256=reg,
               n_synth_train=int(len(tr)), n_synth_test=int(len(syn_test)), n_zero_val=int(len(zv)), folds={})
    plp_rows = []
    cw_rows = []
    for fold in DATES + ["final"]:
        train_dates = [d for d in DATES if d != fold]
        em = endmembers(GR, train_dates)
        Xtr = S.X(tr, em)
        y = S.G["n_eq"][tr]
        m = train(Xtr, y)
        pz = np.maximum(m.predict(S.X(zv, em), num_threads=16), 0)
        tau = float(np.percentile(pz, 99.5))
        Xst = S.X(syn_test, em)
        pst = gated(m, Xst, tau)
        yst = S.G["n_eq"][syn_test]
        fr = dict(train_dates=train_dates, endmember_bottle=[round(float(v), 5) for v in em["bottle"]],
                  endmember_bag=[round(float(v), 5) for v in em["bag"]], n_groups=em["n_groups"], tau=round(tau, 2),
                  synth_test_mae=round(float(np.mean(np.abs(pst - yst))), 2),
                  synth_test_mae_zero_pred=round(float(np.mean(np.abs(yst))), 2),
                  synth_test_mae_nonzero_truth=round(float(np.mean(np.abs(pst - yst)[yst > 0])), 2))
        # чистая вода
        nz_all, n_all = 0, 0
        for s, Xc, nw in CW:
            pcw = gated(m, Xc, tau)
            nz = int((pcw > 0).sum())
            nz_all += nz
            n_all += len(pcw)
            cw_rows.append(dict(fold=fold, scene=s, n=len(pcw), nonzero=nz, share=nz / len(pcw),
                                max_pred=float(pcw.max()), water_px=nw))
        fr["clean_nonzero_share"] = nz_all / n_all
        # реальные мишени (для LODO — только своя дата; для final — все даты справочно, НЕ проверка)
        # даты L127 (не PLP2019): их LODO-модель = модель с эндмемберами всех 5 дат PLP2019 = final
        for d in (DATES + P1D if fold == "final" else [fold]):
            t, Xp = plp_eval_rows(d)
            pp = gated(m, Xp, tau)
            for (_, r), v, raw in zip(t.iterrows(), pp, np.maximum(m.predict(Xp, num_threads=16), 0)):
                plp_rows.append(dict(fold=(d if d in P1D else fold), date=d, campaign=r.campaign, surface=r.surface,
                                     pixel=r.pixel, target=r.target, frac_bottles=r.frac_bottles,
                                     frac_bags=r.frac_bags, frac_reeds=r.frac_reeds, material=r.material,
                                     truth=r["items"], truth_all=r["items"] + r.frac_bags * EQ,
                                     p_det=r.p_l115, flat=FLAT * (r.p_l115 >= THR), p3=float(v), p3_raw=float(raw)))
        wd = WEX / (f"fold_{fold}" if fold != "final" else "final")
        wd.mkdir(parents=True, exist_ok=True)
        m.save_model(str(wd / "model.txt"))
        (wd / "meta.json").write_text(json.dumps(dict(
            kind="p3_synth_lgbm_l1", unit="бутылок PET 1.5 л на пиксель 10 м (эквивалент, как §34 п.2)",
            features=pc.FEATURE_NAMES, window_px=pc.W, bands=pc.BANDS11, tau_exact=tau, config_sha256=cfg_sha, **fr),
            indent=1, ensure_ascii=False), encoding="utf-8")
        res["folds"][fold] = fr
        log("fold", fold, "tau", round(tau, 1), "synthMAE", fr["synth_test_mae"], "clean≠0", round(fr["clean_nonzero_share"], 5),
            f"{time.time() - t0:.0f} s")
        if fold == "final":
            # справочно: второй выход — число предметов реальных размеров (только синтетика)
            y2 = S.G["n_nat"][tr]
            m2 = train(Xtr, y2)
            p2 = np.maximum(m2.predict(Xst, num_threads=16), 0)
            y2t = S.G["n_nat"][syn_test]
            nzm = y2t > 0
            res["synthetic_natural_count"] = dict(
                mae=round(float(np.mean(np.abs(p2 - y2t))), 2), mae_zero_pred=round(float(np.mean(y2t)), 2),
                median_rel_err_nonzero=round(float(np.median(np.abs(p2[nzm] - y2t[nzm]) / y2t[nzm])), 3),
                note="синтетика; размер предмета по спектру не определяется → число предметов реальных размеров ≈ покрытие / средний размер")
    P = pd.DataFrame(plp_rows)
    P.to_csv(OUT / "plp_predictions.csv", index=False)
    pd.DataFrame(cw_rows).to_csv(OUT / "clean_water.csv", index=False)
    # ---- правило
    L = P[P.fold != "final"]
    prim = L[L.frac_bags <= 0.05]
    mae_p3 = float((prim.p3 - prim.truth).abs().mean())
    mae_flat = float((prim.flat - prim.truth).abs().mean())
    per = prim.groupby("date").apply(lambda g: pd.Series(dict(
        n=len(g), mae_p3=(g.p3 - g.truth).abs().mean(), mae_flat=(g.flat - g.truth).abs().mean(),
        mae_zero=g.truth.abs().mean()))).reset_index()
    wins = int((per.mae_p3 <= per.mae_flat).sum())
    need_wins = 3 if len(per) <= 5 else int(np.ceil(len(per) / 2))

    def sub(q):
        return dict(n_pixels=int(len(q)), n_dates=int(q.date.nunique()),
                    mae_p3=round(float((q.p3 - q.truth).abs().mean()), 1) if len(q) else None,
                    mae_flat=round(float((q.flat - q.truth).abs().mean()), 1) if len(q) else None,
                    mae_zero=round(float(q.truth.mean()), 1) if len(q) else None)
    cw = pd.DataFrame(cw_rows)
    cw_share = {f: float(g.nonzero.sum() / g.n.sum()) for f, g in cw.groupby("fold")}
    c1 = (mae_p3 <= 0.9 * mae_flat) and (wins >= need_wins)
    c2 = all(v <= 0.01 for v in cw_share.values())
    allp = L
    res.update(
        primary=dict(n_pixels=int(len(prim)), n_dates=int(prim.date.nunique()), mae_p3=round(mae_p3, 1),
                     mae_flat=round(mae_flat, 1), mae_zero=round(float(prim.truth.mean()), 1),
                     ratio=round(mae_p3 / mae_flat, 3), dates_p3_not_worse=wins, dates_needed=need_wins,
                     water_plp2019=sub(prim[prim.campaign == "PLP2019"]), land_maathuis=sub(prim[prim.campaign != "PLP2019"]),
                     per_date=per.round(1).to_dict("records")),
        secondary_all_pixels_bags_as_bottles=dict(
            n_pixels=int(len(allp)), mae_p3=round(float((allp.p3 - allp.truth_all).abs().mean()), 1),
            mae_flat=round(float((allp.flat - allp.truth_all).abs().mean()), 1)),
        plp_sea_pixels_nonzero=dict(n=int((L.material == "sea").sum()),
                                    p3_nonzero=int(((L.material == "sea") & (L.p3 > 0)).sum()),
                                    flat_nonzero=int(((L.material == "sea") & (L.flat > 0)).sum())),
        clean_water_nonzero_share=cw_share,
        criteria=dict(c1_mae=c1, c2_clean_water=c2, c3_no_retuning=True),
        accepted=bool(c1 and c2), seconds=round(time.time() - t0, 1))
    (ROOT / "reports" / "p3").mkdir(parents=True, exist_ok=True)
    (ROOT / "reports" / "p3" / "p3_synth.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=float),
                                                         encoding="utf-8")
    log("PRIMARY", res["primary"])
    log("clean", cw_share, "ACCEPTED" if res["accepted"] else "NOT ACCEPTED")


if __name__ == "__main__":
    main()
