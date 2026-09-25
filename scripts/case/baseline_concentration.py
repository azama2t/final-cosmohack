"""Бейзлайны концентрации шт./км² на полевых данных, CV по группам (6 схем, основная — из конфига).

Запуск (из корня репозитория):
  set CUDA_VISIBLE_DEVICES= & .venv\\Scripts\\python.exe scripts\\case\\baseline_concentration.py
  Схемы: event (событие), daycell (дата × ячейка 1°), cruiseday (день рейса), st (R км/T сут),
  route (5 непрерывных участков маршрута по дням, фолд = участок), route_buf1 (route + из train
  убраны записи ближе 1 сут к test). Основная схема — configs/case_selection.yaml: main_split.
  (опции: --profiles S2_visual_total_plastic S1_trawl_total_plastic --radius-km 100 --days 2 --folds 5 --seed 42)

Модели (все обучаются только на train-фолде):
  median      — медиана обучающего профиля;
  mean        — среднее обучающего профиля;
  knn5        — среднее 5 ближайших train-событий по большому кругу (координаты);
  knn5_log    — то же в log1p-пространстве (устойчиво к выбросам), обратно expm1;
  wind_lin    — (если в профиле есть ветер/волнение) линейная регрессия log1p(C) ~ wind_speed_kn + sea_state_beaufort.
Предикторы: latitude, longitude, (дата/время — только для групп), wind_speed_kn, sea_state_beaufort.
sampling_method/platform внутри профиля константы (профиль = один метод и одно судно), поэтому
как признак ничего не дают; это описание способа наблюдения, известное до подсчёта, — не утечка.
Поля ответа (concentration_*, items_count, density_numerator_items, reported_*, parent_*, …)
в предикторы не попадают — проверка selection.assert_no_leak.

Выход: data/case/selection_<profile>.csv, data/case/selection_<profile>_rejected.csv,
data/case/splits/<profile>_{event,daycell,cruiseday,st,route}.csv, data/case/splits/all_rows_st.csv,
reports/case_conc/predictions.csv, reports/case_conc/metrics.json, reports/case_conc/consistency.csv,
reports/case_conc/baseline.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.linear_model import LinearRegression  # noqa: E402
from sklearn.neighbors import BallTree  # noqa: E402

from macroplastic.case import concentration as C  # noqa: E402
from macroplastic.case import selection as S  # noqa: E402
from macroplastic.case import splits as P  # noqa: E402

OUT = REPO / "reports" / "case_conc"
DATA = REPO / "data" / "case"
FEATURES = S.ALLOWED_FEATURES
# (имя схемы, метод сплита, буфер в сутках)
SCHEMES = [("event", "event", 0.0), ("daycell", "daycell", 0.0), ("cruiseday", "cruiseday", 0.0),
           ("st", "st", 0.0), ("route", "route", 0.0), ("route_buf1", "route", 1.0)]


def predict_models(tr: pd.DataFrame, te: pd.DataFrame, k: int = 5) -> tuple[dict, np.ndarray]:
    """→ (предсказания по моделям, индексы k соседей в tr для каждой test-строки)."""
    S.assert_no_leak(FEATURES)
    y = tr["target"].to_numpy(float)
    out = {"median": np.full(len(te), np.median(y)), "mean": np.full(len(te), np.mean(y))}
    kk = min(k, len(tr))
    tree = BallTree(np.radians(tr[["latitude", "longitude"]].to_numpy(float)), metric="haversine")
    _, idx = tree.query(np.radians(te[["latitude", "longitude"]].to_numpy(float)), k=kk)
    out[f"knn{k}"] = y[idx].mean(axis=1)
    out[f"knn{k}_log"] = np.expm1(np.log1p(y)[idx].mean(axis=1))
    wcols = [c for c in ("wind_speed_kn", "sea_state_beaufort") if tr[c].notna().mean() > 0.8]
    if wcols:
        med = tr[wcols].median()
        lr = LinearRegression().fit(tr[wcols].fillna(med), np.log1p(y))
        out["wind_lin"] = np.clip(np.expm1(lr.predict(te[wcols].fillna(med))), 0, None)
    return out, idx


def group_bootstrap_diff(df: pd.DataFrame, a: str, b: str, unit: str = "boot_unit",
                         n_boot: int = 2000, seed: int = 0):
    """ДИ 95 % разности MAE(a) − MAE(b); ресэмплинг по единицам зависимости (дни рейса), не по строкам."""
    rng = np.random.default_rng(seed)
    pa = df[df.model == a].set_index("sample_id")
    pb = df[df.model == b].set_index("sample_id").loc[pa.index]
    ea, eb = (pa.y_pred - pa.y_true).abs().to_numpy(), (pb.y_pred - pb.y_true).abs().to_numpy()
    grp = pa[unit].to_numpy()
    ug = np.unique(grp)
    idx_by = {g: np.nonzero(grp == g)[0] for g in ug}
    diffs = []
    for _ in range(n_boot):
        pick = np.concatenate([idx_by[g] for g in rng.choice(ug, size=len(ug), replace=True)])
        diffs.append(ea[pick].mean() - eb[pick].mean())
    return float(ea.mean() - eb.mean()), float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))


def run_profile(d_all, cfg, profile, args):
    acc, rej = S.select(d_all, cfg, profile)
    DATA.mkdir(parents=True, exist_ok=True)
    acc.to_csv(DATA / f"selection_{profile}.csv", index=False, encoding="utf-8")
    rej.to_csv(DATA / f"selection_{profile}_rejected.csv", index=False, encoding="utf-8")
    fld = [C.concentration(n, a) for n, a in zip(acc["density_numerator_items"], acc["sampled_area_km2"])]
    acc["field_lower"] = [r.lower for r in fld]
    acc["field_upper"] = [r.upper for r in fld]
    acc["boot_unit"] = P.cruiseday_groups(acc)          # единица бутстрепа — день рейса (+ событие)
    tdays = P.times_days(acc)

    splits, preds, fold_rows, nb_rows = {}, [], [], []
    for scheme, method, buf in SCHEMES:
        if method not in splits:
            sp = P.make_split(acc, method, n_folds=args.folds, seed=args.seed, radius_km=args.radius_km,
                              days=args.days, k_blocks=args.k_blocks)
            P.assert_no_overlap(sp)
            P.save_split(sp, f"{profile}_{method}")
            splits[method] = sp
        sp = splits[method]
        m = acc.merge(sp[["sample_id", "group", "fold"]], on="sample_id")
        for f in sorted(m.fold.unique()):
            te_mask = (m.fold == f).to_numpy()
            tr_mask = P.buffered_train_mask(sp, acc, f, buf) if buf > 0 else ~te_mask
            tr, te = m[tr_mask], m[te_mask]
            preds_f, idx = predict_models(tr, te)
            # доля kNN-соседей из того же/соседнего дня рейса (|Δt| ≤ 1 сут) — индикатор «утечки маршрута»
            t_tr, t_te = tdays[tr_mask], tdays[te_mask]
            near = np.abs(t_tr[idx] - t_te[:, None]) <= 1.0
            nb_rows.append({"scheme": scheme, "fold": int(f), "n_nb": near.size, "n_nb_1d": int(near.sum())})
            for name, yp in preds_f.items():
                preds.append(pd.DataFrame({
                    "profile": profile, "split": scheme, "fold": f, "sample_id": te.sample_id,
                    "event_id": te.event_id, "group": te.group, "boot_unit": te.boot_unit,
                    "date_utc": te.date_utc, "latitude": te.latitude, "longitude": te.longitude,
                    "y_true": te.target, "field_lower": te.field_lower, "field_upper": te.field_upper,
                    "model": name, "y_pred": yp}))
                fold_rows.append({"profile": profile, "split": scheme, "fold": int(f), "model": name,
                                  "n_train": int(tr_mask.sum()), **C.all_metrics(te.target, yp)})
    pr = pd.concat(preds, ignore_index=True)
    folds = pd.DataFrame(fold_rows)
    nb = pd.DataFrame(nb_rows).groupby("scheme")[["n_nb", "n_nb_1d"]].sum()
    nb["share_nb_1d"] = nb.n_nb_1d / nb.n_nb
    overall = (pr.groupby(["profile", "split", "model"])
               .apply(lambda g: pd.Series(C.all_metrics(g.y_true, g.y_pred)), include_groups=False)
               .reset_index())
    chk = {mt: P.check_split(sp, acc) for mt, sp in splits.items()}
    boot = {}
    for scheme, _, _ in SCHEMES:
        sub = pr[pr.split == scheme]
        for mdl in [x for x in sub.model.unique() if x != "median"]:
            boot[(scheme, mdl)] = group_bootstrap_diff(sub, mdl, "median")
    return acc, rej, pr, folds, overall, chk, boot, nb


def fmt(x, nd=1):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", nargs="*", default=None)
    ap.add_argument("--radius-km", type=float, default=100.0)
    ap.add_argument("--days", type=float, default=2.0)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--k-blocks", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    d = S.load_samples()
    cfg = S.load_config()
    ms = cfg["main_split"]
    args.k_blocks = args.k_blocks or ms["k_blocks"]
    args.seed = args.seed if args.seed is not None else ms["seed"]
    main_scheme = "route_buf1" if (ms["method"] == "route" and ms["buffer_days"] == 1.0) else ms["method"]
    profiles = args.profiles or list(cfg["profiles"])
    OUT.mkdir(parents=True, exist_ok=True)

    chk = S.consistency_check(d, **cfg.get("consistency", {}))
    chk.to_csv(OUT / "consistency.csv", index=False, encoding="utf-8")
    csum = S.consistency_summary(chk)

    scene = P.scene_ids_from_pairs(d)
    sp_all = P.make_split(d, "st", n_folds=args.folds, seed=args.seed, radius_km=args.radius_km,
                          days=args.days, scene_id=scene)
    P.assert_no_overlap(sp_all)
    P.save_split(sp_all, "all_rows_st")
    chk_all = P.check_split(sp_all, d)

    all_pr, all_folds, report = [], [], {}
    lines = ["# Бейзлайны концентрации шт./км² (полевые данные)", "",
             f"**Основной сплит (зафиксирован в configs/case_selection.yaml до моделей): `{main_scheme}`** — "
             f"{args.k_blocks} непрерывных участков маршрута по дням рейса (фолд = участок), из train убраны "
             f"записи ближе {ms['buffer_days']:g} сут к test.", "",
             "Схемы для сравнения: `event` (группа = событие, 5 фолдов), `daycell` (дата × ячейка 1°), "
             "`cruiseday` (день рейса = источник × судно × дата), "
             f"`st` (компоненты R = {args.radius_km:g} км, T = {args.days:g} сут), `route` (участки без буфера). "
             "Метрики по out-of-fold предсказаниям; log1p_mae = mean |log1p(pred) − log1p(true)| (MAPE не используется). "
             "ДИ разности MAE — бутстреп по дням рейса (2000 повторов), одинаково для всех схем. "
             "«Соседи ±1 сут» — доля kNN-соседей из train, взятых из того же или соседнего дня рейса.", "",
             f"Предикторы: {', '.join(FEATURES)} (kNN — только координаты; wind_lin — ветер/волнение, только S1). "
             "Поля ответа исключены (selection.LEAK_EXPLICIT, assert_no_leak).", ""]
    for prof in profiles:
        acc, rej, pr, folds, overall, chk_sp, boot, nb = run_profile(d, cfg, prof, args)
        all_pr.append(pr), all_folds.append(folds)
        report[prof] = {"rows": len(acc), "events": int(acc.event_id.nunique()),
                        "rejected": len(rej), "reasons": rej.reason.value_counts().to_dict(),
                        "main_split": main_scheme,
                        "overall": overall.to_dict("records"),
                        "bootstrap_mae_diff_vs_median": {f"{k[0]}:{k[1]}": v for k, v in boot.items()},
                        "knn_neighbors_within_1d": nb.share_nb_1d.to_dict(),
                        "split_checks": {k: v.to_dict("records") for k, v in chk_sp.items()}}
        y = acc.target
        lines += [f"## {prof}", "", cfg["profiles"][prof]["description"], "",
                  f"Строк {len(acc)}, событий {acc.event_id.nunique()}, дней рейса {acc.boot_unit.nunique()}; "
                  f"C: медиана {fmt(y.median())}, среднее {fmt(y.mean())}, min {fmt(y.min())}, max {fmt(y.max())} шт./км².", ""]
        if acc.field_lower.notna().any():
            w = (acc.field_upper - acc.field_lower) / acc.target
            lines += [f"Пуассоновский 95 % ДИ полевого значения: медианная относительная ширина "
                      f"{fmt(w.median(), 2)} × C (N медиана {fmt(acc.density_numerator_items.median(), 0)}).", ""]
        lines += ["| схема | групп | median MAE / RMSE / log1p | knn5_log MAE / RMSE / log1p | Δ MAE knn5_log − median [95 % ДИ] | Δ MAE knn5 − median [95 % ДИ] | соседи ±1 сут |",
                  "|---|---:|---|---|---|---|---:|"]
        ov = overall.set_index(["split", "model"])
        for scheme, method, _ in SCHEMES:
            ng = pr[pr.split == scheme].group.nunique()
            a, b = ov.loc[(scheme, "median")], ov.loc[(scheme, "knn5_log")]
            d1, d2 = boot[(scheme, "knn5_log")], boot[(scheme, "knn5")]
            star = " **(основной)**" if scheme == main_scheme else ""
            lines.append(f"| {scheme}{star} | {ng} | {fmt(a.mae)} / {fmt(a.rmse)} / {fmt(a.log1p_mae, 3)} | "
                         f"{fmt(b.mae)} / {fmt(b.rmse)} / {fmt(b.log1p_mae, 3)} | "
                         f"{d1[0]:+.1f} [{d1[1]:+.1f}; {d1[2]:+.1f}] | {d2[0]:+.1f} [{d2[1]:+.1f}; {d2[2]:+.1f}] | "
                         f"{nb.share_nb_1d[scheme]:.0%} |")
        lines += ["", "Все модели (MAE / RMSE / log1p_mae / bias):", "",
                  "| схема | " + " | ".join(sorted(overall.model.unique())) + " |",
                  "|---|" + "---|" * overall.model.nunique()]
        for scheme, _, _ in SCHEMES:
            cells = []
            for mdl in sorted(overall.model.unique()):
                r = ov.loc[(scheme, mdl)]
                cells.append(f"{fmt(r.mae)} / {fmt(r.rmse)} / {fmt(r.log1p_mae, 3)} / {fmt(r.bias)}")
            lines.append(f"| {scheme} | " + " | ".join(cells) + " |")
        for scheme in (main_scheme, "st"):
            fs = folds[folds.split == scheme]
            lines += ["", f"По фолдам, схема {scheme} (MAE / RMSE; n test, n train):", "",
                      "| модель | " + " | ".join(f"f{f}" for f in sorted(fs.fold.unique())) + " |",
                      "|---|" + "---|" * fs.fold.nunique()]
            for mdl in ("median", "knn5", "knn5_log"):
                g = fs[fs.model == mdl].sort_values("fold")
                lines.append(f"| {mdl} | " + " | ".join(f"{a:.1f} / {r:.1f} ({n}, {t})" for a, r, n, t
                                                        in zip(g.mae, g.rmse, g.n, g.n_train)) + " |")
        route_chk = chk_sp["route"]
        lines += ["", f"Проверка основного сплита: общих event_id / групп / дней рейса между участками — "
                  f"{int(route_chk.shared_events.sum())} / {int(route_chk.shared_groups.sum())} / "
                  f"{int(route_chk.shared_cruise_days.sum())}; ближайшее расстояние test↔train "
                  f"{', '.join(fmt(x) for x in route_chk.min_dist_km)} км; ближайший интервал без буфера "
                  f"{', '.join(fmt(x, 2) for x in route_chk.min_dt_days)} сут.", ""]
        lines += ["Отказы (причина → строк):", ""] + [f"- {k}: {v}" for k, v in rej.reason.value_counts().items()] + [""]
    lines += ["## Сверка C = N/A (весь реестр)", "",
              "| источник | профиль | scope | строк | совпало (≤2 %) | макс. отн. расхождение | A vs L×W макс. |",
              "|---|---|---|---:|---:|---:|---:|"]
    for r in csum.itertuples():
        lines.append(f"| {r.source_id} | {r.measurement_profile} | {r.target_scope} | {r.rows} | {r.match} | "
                     f"{r.max_rel_diff:.2e} | {fmt(r.area_strip_max_rel_diff, 3)} |")
    lines += ["", f"Итого {int(chk.match.sum())}/{len(chk)} строк с N и A совпадают с опубликованной C. "
              "all_litter (S3/S4) — общий мусор, не пластик; сверка по нему только проверяет формулу.", "",
              f"Общий st-сплит по всем {len(d)} строкам (scene_id из data/pairs/best_per_event.csv): "
              f"групп {sp_all.group.nunique()}, общих event_id/групп/сцен между фолдами "
              f"{int(chk_all.shared_events.sum())}/{int(chk_all.shared_groups.sum())}/{int(chk_all.shared_scenes.sum())}.", ""]

    pd.concat(all_pr).to_csv(OUT / "predictions.csv", index=False, encoding="utf-8")
    pd.concat(all_folds).to_csv(OUT / "metrics_by_fold.csv", index=False, encoding="utf-8")
    report["consistency"] = {"rows": len(chk), "match": int(chk.match.sum())}
    report["args"] = vars(args)
    (OUT / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=float),
                                      encoding="utf-8")
    (OUT / "baseline.md").write_text("\n".join(lines), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
