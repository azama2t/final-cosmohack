"""Бейзлайны концентрации шт./км² на полевых данных, CV по пространственно-временным группам.

Запуск (из корня репозитория):
  set CUDA_VISIBLE_DEVICES= & .venv\\Scripts\\python.exe scripts\\case\\baseline_concentration.py
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
data/case/splits/<profile>_{event,daycell,st}.csv, data/case/splits/all_rows_st.csv,
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
FEATURES = ["latitude", "longitude", "wind_speed_kn", "sea_state_beaufort"]


def predict_models(tr: pd.DataFrame, te: pd.DataFrame, k: int = 5) -> dict[str, np.ndarray]:
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
    return out


def group_bootstrap_diff(df: pd.DataFrame, a: str, b: str, n_boot: int = 2000, seed: int = 0):
    """ДИ 95 % разности MAE(a) − MAE(b), ресэмплинг по группам (не по строкам)."""
    rng = np.random.default_rng(seed)
    pa = df[df.model == a].set_index("sample_id")
    pb = df[df.model == b].set_index("sample_id").loc[pa.index]
    ea, eb = (pa.y_pred - pa.y_true).abs(), (pb.y_pred - pb.y_true).abs()
    grp = pa["group"].to_numpy()
    ug = np.unique(grp)
    idx_by = {g: np.nonzero(grp == g)[0] for g in ug}
    diffs = []
    for _ in range(n_boot):
        pick = np.concatenate([idx_by[g] for g in rng.choice(ug, size=len(ug), replace=True)])
        diffs.append(ea.to_numpy()[pick].mean() - eb.to_numpy()[pick].mean())
    return float(ea.mean() - eb.mean()), float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))


def run_profile(d_all, cfg, profile, args):
    acc, rej = S.select(d_all, cfg, profile)
    DATA.mkdir(parents=True, exist_ok=True)
    acc.to_csv(DATA / f"selection_{profile}.csv", index=False, encoding="utf-8")
    rej.to_csv(DATA / f"selection_{profile}_rejected.csv", index=False, encoding="utf-8")
    # интервал полевой концентрации (если есть N)
    fld = [C.concentration(n, a) for n, a in zip(acc["density_numerator_items"], acc["sampled_area_km2"])]
    acc["field_lower"] = [r.lower for r in fld]
    acc["field_upper"] = [r.upper for r in fld]
    acc["field_status"] = [r.status for r in fld]

    splits = {}
    for method in ("event", "daycell", "st"):
        sp = P.make_split(acc, method, n_folds=args.folds, seed=args.seed,
                          radius_km=args.radius_km, days=args.days)
        P.assert_no_overlap(sp)
        P.save_split(sp, f"{profile}_{method}")
        splits[method] = sp

    preds, fold_rows = [], []
    for method, sp in splits.items():
        m = acc.merge(sp[["sample_id", "group", "fold"]], on="sample_id")
        for f in sorted(m.fold.unique()):
            tr, te = m[m.fold != f], m[m.fold == f]
            for name, yp in predict_models(tr, te).items():
                preds.append(pd.DataFrame({
                    "profile": profile, "split": method, "fold": f, "sample_id": te.sample_id,
                    "event_id": te.event_id, "group": te.group, "latitude": te.latitude,
                    "longitude": te.longitude, "y_true": te.target, "field_lower": te.field_lower,
                    "field_upper": te.field_upper, "model": name, "y_pred": yp}))
                met = C.all_metrics(te.target, yp)
                fold_rows.append({"profile": profile, "split": method, "fold": int(f), "model": name, **met})
    pr = pd.concat(preds, ignore_index=True)
    folds = pd.DataFrame(fold_rows)
    overall = (pr.groupby(["profile", "split", "model"])
               .apply(lambda g: pd.Series(C.all_metrics(g.y_true, g.y_pred)), include_groups=False)
               .reset_index())
    chk = {m: P.check_split(sp, acc) for m, sp in splits.items()}
    boot = {}
    for method in splits:
        sub = pr[pr.split == method]
        for mdl in [x for x in sub.model.unique() if x != "median"]:
            boot[f"{method}:{mdl}-median"] = group_bootstrap_diff(sub, mdl, "median")
    return acc, rej, pr, folds, overall, chk, boot


def fmt(x, nd=1):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", nargs="*", default=None)
    ap.add_argument("--radius-km", type=float, default=100.0)
    ap.add_argument("--days", type=float, default=2.0)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    d = S.load_samples()
    cfg = S.load_config()
    profiles = args.profiles or list(cfg["profiles"])
    OUT.mkdir(parents=True, exist_ok=True)

    # сверка C = N/A по всему реестру
    chk = S.consistency_check(d, **cfg.get("consistency", {}))
    chk.to_csv(OUT / "consistency.csv", index=False, encoding="utf-8")
    csum = S.consistency_summary(chk)

    # общий st-сплит по всем 935 строкам (с scene_id из реестра пар) — для других задач
    scene = P.scene_ids_from_pairs(d)
    sp_all = P.make_split(d, "st", n_folds=args.folds, seed=args.seed, radius_km=args.radius_km,
                          days=args.days, scene_id=scene)
    P.assert_no_overlap(sp_all)
    P.save_split(sp_all, "all_rows_st")
    chk_all = P.check_split(sp_all, d)

    all_pr, all_folds, all_over, report = [], [], [], {}
    lines = ["# Бейзлайны концентрации шт./км² (полевые данные)", "",
             f"Сплит `st`: связные компоненты R = {args.radius_km:g} км, T = {args.days:g} сут (+ event_id), "
             f"{args.folds} фолдов, seed {args.seed}. Для сравнения — `event` (группа = событие) и "
             "`daycell` (дата × ячейка 1°). Метрики — по out-of-fold предсказаниям всех фолдов; "
             "log1p_mae = mean |log1p(pred) − log1p(true)| (MAPE не используется).", "",
             "Предикторы: только координаты (kNN) и ветер/волнение (wind_lin, только S1); поля ответа исключены.", ""]
    for prof in profiles:
        acc, rej, pr, folds, overall, chk_sp, boot = run_profile(d, cfg, prof, args)
        all_pr.append(pr), all_folds.append(folds), all_over.append(overall)
        report[prof] = {"rows": len(acc), "events": int(acc.event_id.nunique()),
                        "rejected": len(rej), "reasons": rej.reason.value_counts().to_dict(),
                        "overall": overall.to_dict("records"), "bootstrap_mae_diff_vs_median": boot,
                        "split_checks": {k: v.to_dict("records") for k, v in chk_sp.items()}}
        y = acc.target
        lines += [f"## {prof}", "", cfg["profiles"][prof]["description"], "",
                  f"Строк {len(acc)}, событий {acc.event_id.nunique()}; C: медиана {fmt(y.median())}, "
                  f"среднее {fmt(y.mean())}, min {fmt(y.min())}, max {fmt(y.max())} шт./км²; нулей {int((y == 0).sum())}.",
                  ""]
        if acc.field_lower.notna().any():
            w = (acc.field_upper - acc.field_lower) / acc.target
            lines += [f"Пуассоновский 95 % ДИ полевого значения: медианная относительная ширина "
                      f"{fmt(w.median(), 2)} × C (N медиана {fmt(acc.density_numerator_items.median(), 0)}).", ""]
        lines += ["| сплит | групп | модель | MAE | RMSE | medAE | log1p_mae | bias |", "|---|---:|---|---:|---:|---:|---:|---:|"]
        for r in overall.itertuples():
            ng = pr[(pr.split == r.split)].group.nunique()
            lines.append(f"| {r.split} | {ng} | {r.model} | {fmt(r.mae)} | {fmt(r.rmse)} | {fmt(r.median_ae)} | "
                         f"{fmt(r.log1p_mae, 3)} | {fmt(r.bias)} |")
        lines += ["", "MAE по фолдам (сплит st):", "",
                  "| модель | " + " | ".join(f"f{f}" for f in sorted(folds.fold.unique())) + " |",
                  "|---|" + "---:|" * folds.fold.nunique()]
        fs = folds[folds.split == "st"]
        for mdl, g in fs.groupby("model"):
            g = g.sort_values("fold")
            lines.append(f"| {mdl} | " + " | ".join(f"{m:.1f} (n={n})" for m, n in zip(g.mae, g.n)) + " |")
        lines += ["", "RMSE по фолдам (сплит st):", "",
                  "| модель | " + " | ".join(f"f{f}" for f in sorted(folds.fold.unique())) + " |",
                  "|---|" + "---:|" * folds.fold.nunique()]
        for mdl, g in fs.groupby("model"):
            g = g.sort_values("fold")
            lines.append(f"| {mdl} | " + " | ".join(f"{m:.1f}" for m in g.rmse) + " |")
        lines += ["", "Разность MAE (модель − медиана), 95 % бутстреп-ДИ по группам:", ""]
        for k, (dv, lo, hi) in boot.items():
            lines.append(f"- {k}: {dv:+.1f} [{lo:+.1f}; {hi:+.1f}] шт./км²")
        c = chk_sp["st"]
        lines += ["", "Проверка сплита st: общих event_id/групп между фолдами — "
                  f"{int(c.shared_events.sum())}/{int(c.shared_groups.sum())}; ближайшее расстояние test↔train "
                  f"по фолдам {', '.join(fmt(x) for x in c.min_dist_km)} км; ближайший интервал "
                  f"{', '.join(fmt(x, 2) for x in c.min_dt_days)} сут; ближайшее расстояние при |Δt| ≤ 1 сут "
                  f"{', '.join(fmt(x) for x in c.min_dist_km_within_1d)} км.", ""]
    lines += ["## Сверка C = N/A (весь реестр)", "",
              "| источник | профиль | scope | строк | совпало (≤2 %) | макс. отн. расхождение | A vs L×W макс. |",
              "|---|---|---|---:|---:|---:|---:|"]
    for r in csum.itertuples():
        lines.append(f"| {r.source_id} | {r.measurement_profile} | {r.target_scope} | {r.rows} | {r.match} | "
                     f"{r.max_rel_diff:.2e} | {fmt(r.area_strip_max_rel_diff, 3)} |")
    lines += ["", f"Итого {int(chk.match.sum())}/{len(chk)} строк с N и A совпадают с опубликованной C.", "",
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
