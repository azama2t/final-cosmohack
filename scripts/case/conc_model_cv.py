"""L68: кандидаты основной модели концентрации против бейзлайна «медиана обучающего профиля» на dev.

Только dev-часть (configs/case_selection.yaml: final_test, role=dev, sha256 проверяется); отложенный
test и буфер не читаются. CV по участкам маршрута внутри dev (route, 5 блоков, буфер 1 сут, как
main_split / route_buf1). Интервалы 90 % — из вложенной CV на обучающей части внешнего фолда.
Протокол (кандидаты, гиперпараметры, правило принятия) — configs/case_conc_model.yaml: protocol,
записан до запуска.

    set CUDA_VISIBLE_DEVICES= & .venv\\Scripts\\python.exe scripts\\case\\conc_model_cv.py

Выход: reports/case_conc/dev_cv.md, dev_cv.json, dev_predictions.csv;
configs/case_conc_model.yaml (секция selected); weights/case_conc/<profile>.json (выбранная модель,
обученная на всём dev, + квантили остатков для интервала).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from macroplastic.case import conc_models as M  # noqa: E402
from macroplastic.case import selection as S  # noqa: E402

OUT = REPO / "reports" / "case_conc"
CFG = REPO / "configs" / "case_conc_model.yaml"
WEIGHTS = REPO / "weights" / "case_conc"


def run_profile(profile: str, proto: dict) -> dict:
    parts = M.load_profile_parts(profile)
    dev = parts["dev"]
    del parts                                                   # test не используется
    cv = proto["cv"]
    models = [proto["baseline"]] + list(proto["candidates"])
    pr = pd.concat([M.nested_cv(m, dev, cv["k_blocks"], cv["buffer_days"], cv["inner_blocks"]) for m in models],
                   ignore_index=True)
    pr.insert(0, "profile", profile)
    rows = []
    for m in models:
        g = pr[pr.model == m]
        r = {"model": m, **M.metrics_with_interval(g)}
        if m != proto["baseline"]:
            d = M.bootstrap_diff(pr, m, proto["baseline"], "mae")
            dl = M.bootstrap_diff(pr, m, proto["baseline"], "log1p_mae")
            # справочно (не для выбора): Бонферрони на число кандидатов
            db = M.bootstrap_diff(pr, m, proto["baseline"], "mae", level=1 - 0.05 / len(proto["candidates"]))
            r.update(d_mae=d[0], d_mae_lo=d[1], d_mae_hi=d[2], d_log=dl[0], d_log_lo=dl[1], d_log_hi=dl[2],
                     d_mae_bonf_lo=db[1], d_mae_bonf_hi=db[2])
        else:
            r.update(d_mae=0.0, d_mae_lo=np.nan, d_mae_hi=np.nan, d_log=0.0, d_log_lo=np.nan, d_log_hi=np.nan,
                     d_mae_bonf_lo=np.nan, d_mae_bonf_hi=np.nan)
        rows.append(r)
    table = pd.DataFrame(rows)
    per_fold = (pr.groupby(["model", "fold"])
                .apply(lambda g: pd.Series({"n": len(g), "n_train": int(g.n_train.iloc[0]),
                                            "mae": float((g.y_pred - g.y_true).abs().mean()),
                                            "coverage90": float(((g.y_true >= g.lo) & (g.y_true <= g.hi)).mean())}),
                       include_groups=False).reset_index())
    primary, why = M.select_primary(table, proto["baseline"])
    # устойчивость (справочно, выбор не меняет): другое число участков внутри dev
    sens = []
    for kb in (4, 6):
        for m in sorted({primary, "knn5_log", "geomean", "ridge_log", "pooled"} - {proto["baseline"]}):
            pk = pd.concat([M.nested_cv(x, dev, kb, cv["buffer_days"], cv["inner_blocks"]) for x in (proto["baseline"], m)],
                           ignore_index=True)
            d = M.bootstrap_diff(pk, m, proto["baseline"], "mae")
            base_mae = float((pk[pk.model == proto["baseline"]].y_pred - pk[pk.model == proto["baseline"]].y_true).abs().mean())
            sens.append({"k_blocks": kb, "model": m, "mae": float((pk[pk.model == m].y_pred - pk[pk.model == m].y_true).abs().mean()),
                         "baseline_mae": base_mae, "d_mae": d[0], "d_mae_lo": d[1], "d_mae_hi": d[2]})
    sens = pd.DataFrame(sens)
    # выбранная модель на всём dev + квантили остатков out-of-fold (внешняя CV) для интервала применения
    g = pr[pr.model == primary]
    q_app = M.log_residual_quantiles(g.y_true.to_numpy(), g.y_pred.to_numpy())
    fitted = M.fit_model(primary, dev)
    spec = M.FeatureSpec.fit(M.base_features(dev))
    X_cols = list(spec.transform(M.base_features(dev)).columns)
    S.assert_no_leak(X_cols)
    wt = {"profile": profile, "model": fitted.to_dict(), "trained_on": "dev (role=dev)", "n_train": len(dev),
          "interval": {"level": 0.9, "q_lo": q_app[0], "q_hi": q_app[1],
                       "method": "q05/q95 out-of-fold log1p-остатков dev-CV"},
          "created": datetime.now().isoformat(timespec="seconds")}
    WEIGHTS.mkdir(parents=True, exist_ok=True)
    M.save_json(wt, WEIGHTS / f"{profile}.json")
    if primary == "lgbm_log":
        fitted.est.booster_.save_model(str(WEIGHTS / f"{profile}_lgbm.txt"))
    return {"profile": profile, "n_dev": len(dev), "dev_cruise_days": int(dev.date_utc.nunique()),
            "dev_target": {"median": float(dev.target.median()), "mean": float(dev.target.mean()),
                           "min": float(dev.target.min()), "max": float(dev.target.max())},
            "folds_n": pr[pr.model == proto["baseline"]].groupby("fold").size().to_dict(),
            "X_columns": X_cols, "table": table, "per_fold": per_fold, "pred": pr,
            "primary": primary, "why": why, "q_app": q_app, "sens": sens}


def f(x, nd=1):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    proto = cfg["protocol"]
    OUT.mkdir(parents=True, exist_ok=True)
    res = {p: run_profile(p, proto) for p in proto["profiles"]}

    pd.concat([r["pred"] for r in res.values()])[
        ["profile", "sample_id", "event_id", "cruise_day", "fold", "n_train", "y_true", "y_pred", "lo", "hi",
         "q_lo", "q_hi", "model"]].to_csv(OUT / "dev_predictions.csv", index=False, encoding="utf-8")

    lines = ["# Основная модель концентрации против бейзлайна на dev (L68)", "",
             "Только dev-часть (отложенный test и буфер не читаются, sha256 состава сверен с "
             "configs/case_selection.yaml: final_test). CV: 5 участков маршрута внутри dev, из train убраны "
             "записи ближе 1 сут к test-участку (route_buf1). Интервал 90 %: q05/q95 лог-остатков из вложенной "
             "CV (4 участка) на обучающей части внешнего фолда. ДИ разности — бутстреп по дням рейса, 2000 повторов. "
             "Протокол и правило принятия записаны до запуска: configs/case_conc_model.yaml: protocol.", "",
             f"Кандидаты: {', '.join(proto['candidates'])}; бейзлайн: {proto['baseline']}. "
             "MAPE не используется (нули возможны в других профилях).", ""]
    js = {"protocol": proto, "profiles": {}}
    for p, r in res.items():
        t = r["table"]
        lines += [f"## {p}", "",
                  f"dev: {r['n_dev']} событий, {r['dev_cruise_days']} дней рейса; C на dev: медиана "
                  f"{f(r['dev_target']['median'])}, среднее {f(r['dev_target']['mean'])}, "
                  f"{f(r['dev_target']['min'])}–{f(r['dev_target']['max'])} шт./км². "
                  f"Событий по фолдам: {', '.join(str(v) for v in r['folds_n'].values())}.", "",
                  f"Признаки X (после отбора по train): {', '.join(r['X_columns'])}.", "",
                  "| модель | MAE | RMSE | medAE | log1p_mae | bias | Δ MAE vs медиана [95 % ДИ] | ДИ Бонферрони | Δ log1p_mae [95 % ДИ] | покрытие 90 % | медианная ширина |",
                  "|---|---:|---:|---:|---:|---:|---|---|---|---:|---:|"]
        for x in t.itertuples():
            star = " **(основная)**" if x.model == r["primary"] else ""
            dm = "—" if x.model == proto["baseline"] else f"{x.d_mae:+.1f} [{x.d_mae_lo:+.1f}; {x.d_mae_hi:+.1f}]"
            dl = "—" if x.model == proto["baseline"] else f"{x.d_log:+.3f} [{x.d_log_lo:+.3f}; {x.d_log_hi:+.3f}]"
            db = "—" if x.model == proto["baseline"] else f"[{x.d_mae_bonf_lo:+.1f}; {x.d_mae_bonf_hi:+.1f}]"
            lines.append(f"| {x.model}{star} | {f(x.mae)} | {f(x.rmse)} | {f(x.median_ae)} | {f(x.log1p_mae, 3)} | "
                         f"{f(x.bias)} | {dm} | {db} | {dl} | {x.coverage90:.0%} | {f(x.width_median)} |")
        pf = r["per_fold"]
        lines += ["", "MAE по участкам dev (f0–f4; n test / n train):", "",
                  "| модель | " + " | ".join(f"f{k}" for k in sorted(pf.fold.unique())) + " |",
                  "|---|" + "---|" * pf.fold.nunique()]
        for m in t.model:
            g = pf[pf.model == m].sort_values("fold")
            lines.append(f"| {m} | " + " | ".join(f"{a:.1f} ({int(n)}/{int(nt)})" for a, n, nt in zip(g.mae, g.n, g.n_train)) + " |")
        lines += ["", "Устойчивость к числу участков внутри dev (справочно, выбор по протоколу — k = 5): "
                  "Δ MAE vs медиана [95 % ДИ]", "", "| модель | k = 4 | k = 6 |", "|---|---|---|"]
        for m in r["sens"].model.unique():
            cells = []
            for kb in (4, 6):
                z = r["sens"][(r["sens"].model == m) & (r["sens"].k_blocks == kb)].iloc[0]
                cells.append(f"{z.mae:.1f} vs {z.baseline_mae:.1f}: {z.d_mae:+.1f} [{z.d_mae_lo:+.1f}; {z.d_mae_hi:+.1f}]")
            lines.append(f"| {m} | " + " | ".join(cells) + " |")
        lines += ["", f"**Выбор:** `{r['primary']}` — {r['why']}. Интервал применения 90 %: "
                  f"[ŷ·e^{r['q_app'][0]:+.2f}, ŷ·e^{r['q_app'][1]:+.2f}] в log1p-шкале "
                  f"(q05/q95 out-of-fold остатков dev).", ""]
        js["profiles"][p] = {k: v for k, v in r.items() if k not in ("table", "per_fold", "pred", "sens")}
        js["profiles"][p]["sensitivity_k_blocks"] = r["sens"].to_dict("records")
        js["profiles"][p]["table"] = t.to_dict("records")
        js["profiles"][p]["per_fold"] = pf.to_dict("records")
        js["profiles"][p]["folds_n"] = {str(k): int(v) for k, v in r["folds_n"].items()}
    M.save_json(js, OUT / "dev_cv.json")
    (OUT / "dev_cv.md").write_text("\n".join(lines), encoding="utf-8")

    # configs/case_conc_model.yaml: protocol без изменений + selected
    sel = {}
    for p, r in res.items():
        row = r["table"].set_index("model").loc[r["primary"]]
        base = r["table"].set_index("model").loc[proto["baseline"]]
        sel[p] = {"model": r["primary"], "reason": r["why"],
                  "params": M.make_model(r["primary"]).params,
                  "features": M.model_features(r["primary"], r["X_columns"]),
                  "weights": f"weights/case_conc/{p}.json",
                  "interval": {"level": 0.9, "q_lo": round(r["q_app"][0], 4), "q_hi": round(r["q_app"][1], 4)},
                  "dev_cv": {"mae": round(float(row.mae), 2), "rmse": round(float(row.rmse), 2),
                             "log1p_mae": round(float(row.log1p_mae), 4), "coverage90": round(float(row.coverage90), 3),
                             "baseline_mae": round(float(base.mae), 2)}}
    cfg["selected"] = sel
    cfg["selected_at"] = datetime.now().isoformat(timespec="seconds")
    header = ("# Модель концентрации шт./км² (L68). protocol записан ДО первого запуска CV (предрегистрация);\n"
              "# selected — результат scripts/case/conc_model_cv.py (CV на dev). Финальный test — "
              "scripts/case/final_test_conc.py (однократно, после 26.09 12:00).\n")
    CFG.write_text(header + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
