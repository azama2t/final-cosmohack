"""Однократная финальная проверка модели концентрации на отложенном test (критерий Т3).

НЕ ЗАПУСКАТЬ до приёмки 26.09 12:00 (configs/case_selection.yaml: final_test.not_before).
Единственное место, где читаются метки отложенного test (role=test в data/case/splits/final_test_<profile>.csv).
Ничего не подбирается: основная модель, её гиперпараметры и квантили интервала берутся из
configs/case_conc_model.yaml: selected (выбраны по CV на dev); модель и бейзлайн «медиана обучающего
профиля» обучаются на всём dev (буфер не используется) и сравниваются на ОДНОЙ проверочной выборке.
Остальные кандидаты протокола считаются справочно (reference, не выбираются).

    .venv\\Scripts\\python.exe scripts\\case\\final_test_conc.py

Выход: reports/case_conc/final_test.json (+ final_test_predictions.csv: эталон, предсказание, интервал).
Повторный запуск отказывает, если final_test.json уже есть (удалять только если запуск упал).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

OUT = REPO / "reports" / "case_conc" / "final_test.json"
CFG = REPO / "configs" / "case_conc_model.yaml"


def evaluate_profile(profile: str, sel: dict, proto: dict) -> tuple[dict, "object"]:
    import numpy as np
    import pandas as pd

    from macroplastic.case import concentration as C
    from macroplastic.case import conc_models as M

    parts = M.load_profile_parts(profile)                    # сверка sha256 состава test/dev
    allr = parts["all"]
    dev, test = parts["dev"], allr[allr.role == "test"].reset_index(drop=True)
    assert not set(dev.event_id) & set(test.event_id)
    assert not set(M.P.cruise_day_keys(dev)) & set(M.P.cruise_day_keys(test))
    cv = proto["cv"]
    base, main = proto["baseline"], sel["model"]
    # замороженная модель: обучение на всём dev должно совпасть с weights/case_conc/<profile>.json
    frozen = json.loads((REPO / sel["weights"]).read_text(encoding="utf-8"))
    names = [base] + list(proto["candidates"])
    preds, rows = [], {}
    for name in names:
        m = M.fit_model(name, dev)
        if name == main:
            d_now, d_frz = m.to_dict(), frozen["model"]
            for k in ("value", "coef", "intercept"):
                if k in d_frz and not np.allclose(np.asarray(d_now[k], float), np.asarray(d_frz[k], float), atol=1e-8):
                    raise RuntimeError(f"{profile}/{name}: модель на dev не совпадает с замороженной ({k})")
            q = (sel["interval"]["q_lo"], sel["interval"]["q_hi"])
        else:
            oof, _ = M.oof_predictions(name, dev, cv["k_blocks"], cv["buffer_days"])   # только dev
            q = M.log_residual_quantiles(dev.target.to_numpy(float), oof)
        yp = m.predict(test)
        lo, hi = M.interval(yp, q)
        g = pd.DataFrame({"profile": profile, "sample_id": test.sample_id, "event_id": test.event_id,
                          "cruise_day": M.P.cruise_day_keys(test).to_numpy(), "date_utc": test.date_utc,
                          "latitude": test.latitude, "longitude": test.longitude,
                          "y_true": test.target.to_numpy(float), "y_pred": yp, "lo": lo, "hi": hi,
                          "field_lower": np.nan, "field_upper": np.nan, "model": name,
                          "role": "main" if name == main else ("baseline" if name == base else "reference")})
        if test.density_numerator_items.notna().all():
            fr = [C.concentration(n, a) for n, a in zip(test.density_numerator_items, test.sampled_area_km2)]
            g["field_lower"], g["field_upper"] = [r.lower for r in fr], [r.upper for r in fr]
        preds.append(g)
        rows[name] = {**M.metrics_with_interval(g), "q_lo": q[0], "q_hi": q[1], "role": g.role.iloc[0]}
    pr = pd.concat(preds, ignore_index=True)
    diff = {}
    for name in names:
        if name == base:
            continue
        d = M.bootstrap_diff(pr, name, base, "mae")
        dl = M.bootstrap_diff(pr, name, base, "log1p_mae")
        diff[name] = {"d_mae": d[0], "ci95": [d[1], d[2]], "d_log1p_mae": dl[0], "ci95_log": [dl[1], dl[2]]}
    res = {"n_test": len(test), "test_cruise_days": int(test.date_utc.nunique()), "n_dev": len(dev),
           "main_model": main, "baseline": base,
           "main": rows[main], "baseline_metrics": rows[base], "main_vs_baseline": diff.get(main),
           "main_better_significant": bool(main != base and diff[main]["ci95"][1] < 0),
           "reference": {k: {**v, **diff.get(k, {})} for k, v in rows.items() if k not in (main, base)},
           "note": "ДИ — бутстреп по дням рейса test (их мало → ДИ широкий)"}
    return res, pr


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--allow-early", action="store_true",
                    help="запуск до final_test.not_before (только осознанно: test читается один раз)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    if out.exists():
        sys.exit(f"{out} уже есть: отложенный test считается один раз. Удалять только если запуск упал.")
    import yaml
    from macroplastic.case import selection as S

    fz = S.load_config()["final_test"]
    not_before = datetime.fromisoformat(str(fz["not_before"]))
    if datetime.now() < not_before and not a.allow_early:
        sys.exit(f"до {not_before:%d.%m %H:%M} отложенный test не читается (приёмка). Отказ.")
    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    proto, sel = cfg["protocol"], cfg["selected"]
    rep = {"when": datetime.now().isoformat(timespec="seconds"),
           "note": "отложенный test (участок маршрута + буфер 1 сут) оценён один раз после заморозки модели; "
                   "модель/гиперпараметры/интервал выбраны на dev; на test ничего не выбирается",
           "config": "configs/case_conc_model.yaml", "split": "configs/case_selection.yaml: final_test",
           "profiles": {}}
    preds = []
    for prof in proto["profiles"]:
        res, pr = evaluate_profile(prof, sel[prof], proto)
        res["test_sha256"] = fz["profiles"][prof]["test_sha256"]
        rep["profiles"][prof] = res
        preds.append(pr)
    import pandas as pd
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.concat(preds).to_csv(out.with_name(out.stem + "_predictions.csv"), index=False, encoding="utf-8")
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for prof, r in rep["profiles"].items():
        print(prof, r["main_model"], "MAE", round(r["main"]["mae"], 2), "vs median", round(r["baseline_metrics"]["mae"], 2),
              "Δ", r["main_vs_baseline"])


if __name__ == "__main__":
    main()
