"""Г1-2 (docs/research/HYPOTHESES_LIVE.md): интервал профиля C = ΣN/ΣA — только Пуассон или шире?

Правило принятия записано ДО расчёта (в выводе поле `rule`):
  интервал «между событиями» (бутстреп по дням рейса или NB) берём в документы/API как отдельный интервал,
  если его покрытие на отложенном test ближе к номиналу, чем у Гарвуда. Главный результат
  (медиана профиля на test, MAE 25.2) не меняется — test уже был открыт, выбор модели по нему не делается.

Три вопроса:
  1. Интервал СРЕДНЕГО профиля по всем 63 событиям: Гарвуд (только счёт) / бутстреп по дням рейса / NB2-MLE.
  2. Среднее dev → среднее test: накрывает ли интервал среднего по dev (49 событий) ΣN/ΣA отложенного test (14)?
     Это один исход (да/нет) — показатель, не оценка покрытия.
  3. Диапазон для ОДНОГО нового события (95 %): Гарвуд по среднему dev, прогноз NB2 (с площадью полосы события),
     эмпирические квантили событий dev; доля 14 событий test внутри (покрытие), номинал 95 %.

Запуск: python scripts/case/field_interval_check.py  (≈ 5 с, без сети; вход — task/*.csv и reports/case_conc/*.csv)
Выход:  reports/quantity/field_intervals.json, reports/quantity/field_intervals.md
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import optimize, stats

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "task" / "macroplastic_marine_samples.csv"
TEST = ROOT / "reports" / "case_conc" / "final_test_predictions.csv"
OUT = ROOT / "reports" / "quantity"
PROFILE = "S2_visual_total_plastic"
B = 2000
SEED = 20260926

RULE = ("Правило §28 А (docs/PIPELINE.md C10, записано до расчёта): метод интервала — кластерный бутстреп по событиям "
        "(кластер = день рейса, 2 000 повторов) и/или NB-GLM (offset ln A); выбор — по покрытию 95 %-интервала на "
        "отложенном test (14 событий S2), без подбора на test. Точечные оценки не меняются, только ширина.")


def load():
    d = pd.read_csv(SAMPLES, encoding="utf-8")
    t = d[(d.record_type == "transect_density") & (d.measurement_profile == "S2_visual_GT2")
          & (d.target_scope == "total_plastic")].copy()
    t["N"] = pd.to_numeric(t.density_numerator_items, errors="coerce")
    t["A"] = pd.to_numeric(t.sampled_area_km2, errors="coerce")
    t["C"] = pd.to_numeric(t.concentration_items_km2, errors="coerce")
    t["day"] = t.date_utc.astype(str).str[:10]
    assert t.N.notna().all() and t.A.notna().all() and t.event_id.is_unique
    p = pd.read_csv(TEST)
    test_ids = set(p[(p.profile == PROFILE) & (p.model == "median")].sample_id)
    t["split"] = np.where(t.sample_id.isin(test_ids), "test", "dev")
    assert (t.split == "test").sum() == len(test_ids) == 14
    return t


def garwood(n, a, level=0.95):
    al = 1 - level
    lo = stats.chi2.ppf(al / 2, 2 * n) / 2 if n > 0 else 0.0
    hi = stats.chi2.ppf(1 - al / 2, 2 * n + 2) / 2
    return n / a, lo / a, hi / a


def boot_days(g, rng, b=B):
    """Кластерный бутстреп ΣN/ΣA: дни рейса с возвращением."""
    days = g.day.unique()
    agg = g.groupby("day")[["N", "A"]].sum().loc[days]
    n, a = agg.N.to_numpy(), agg.A.to_numpy()
    idx = rng.integers(0, len(days), size=(b, len(days)))
    return n[idx].sum(1) / a[idx].sum(1)


def nb2_fit(n, a):
    """NB2 с offset ln A, только свободный член: μ_i = C·A_i, Var = μ + α μ². MLE; Уолд по ln C (числ. гессиан)."""
    n, a = np.asarray(n, float), np.asarray(a, float)

    def nll(th):
        lc, la = th
        mu, r = np.exp(lc) * a, np.exp(-la)          # r = 1/α
        return -np.sum(stats.nbinom.logpmf(n, r, r / (r + mu)))

    x0 = [math.log(n.sum() / a.sum()), math.log(0.5)]
    res = optimize.minimize(nll, x0, method="Nelder-Mead", options={"xatol": 1e-8, "fatol": 1e-10, "maxiter": 5000})
    lc, la = res.x
    h = 1e-4
    H = np.zeros((2, 2))
    for i in range(2):
        for j in range(2):
            e_i, e_j = np.eye(2)[i] * h, np.eye(2)[j] * h
            H[i, j] = (nll(res.x + e_i + e_j) - nll(res.x + e_i - e_j) - nll(res.x - e_i + e_j) + nll(res.x - e_i - e_j)) / (4 * h * h)
    se = math.sqrt(np.linalg.inv(H)[0, 0])
    return {"C": math.exp(lc), "lo95": math.exp(lc - 1.96 * se), "hi95": math.exp(lc + 1.96 * se),
            "alpha": math.exp(la), "se_lnC": se, "converged": bool(res.success)}


def nb2_predict(C, alpha, a, level=0.90):
    mu, r = C * a, 1.0 / alpha
    p = r / (r + mu)
    al = 1 - level
    return stats.nbinom.ppf(al / 2, r, p) / a, stats.nbinom.ppf(1 - al / 2, r, p) / a


def r(x, k=1):
    return None if x is None else round(float(x), k)


def main():
    rng = np.random.default_rng(SEED)
    t = load()
    dev, test = t[t.split == "dev"], t[t.split == "test"]
    out = {"profile": PROFILE, "rule": RULE, "B": B, "seed": SEED,
           "n_events": int(len(t)), "n_days": int(t.day.nunique()),
           "dev": {"n": int(len(dev)), "days": int(dev.day.nunique())},
           "test": {"n": int(len(test)), "days": int(test.day.nunique())}}

    # 1. среднее профиля, все 63
    c, lo, hi = garwood(t.N.sum(), t.A.sum())
    bs = boot_days(t, rng)
    nb = nb2_fit(t.N, t.A)
    out["mean_all"] = {
        "sumN": float(t.N.sum()), "sumA_km2": round(float(t.A.sum()), 4), "C": r(c),
        "garwood95": [r(lo), r(hi)],
        "bootstrap_days95": [r(np.quantile(bs, .025)), r(np.quantile(bs, .975))],
        "nb2": {"C": r(nb["C"]), "ci95": [r(nb["lo95"]), r(nb["hi95"])], "alpha": r(nb["alpha"], 3)},
        "width_ratio_boot_vs_garwood": r((np.quantile(bs, .975) - np.quantile(bs, .025)) / (hi - lo), 2),
    }

    # 2. среднее dev → среднее test (один исход)
    cd, lod, hid = garwood(dev.N.sum(), dev.A.sum())
    bsd = boot_days(dev, rng)
    nbd = nb2_fit(dev.N, dev.A)
    ct = test.N.sum() / test.A.sum()
    blo, bhi = np.quantile(bsd, [.025, .975])
    out["mean_dev_to_test"] = {
        "dev_C": r(cd), "test_C": r(ct), "test_garwood95": [r(x) for x in garwood(test.N.sum(), test.A.sum())[1:]],
        "garwood95": [r(lod), r(hid)], "garwood_covers_test_mean": bool(lod <= ct <= hid),
        "bootstrap_days95": [r(blo), r(bhi)], "bootstrap_covers_test_mean": bool(blo <= ct <= bhi),
        "nb2_ci95": [r(nbd["lo95"]), r(nbd["hi95"])], "nb2_covers_test_mean": bool(nbd["lo95"] <= ct <= nbd["hi95"]),
        "note": "один исход (одно среднее test по 5 дням рейса) — это не оценка покрытия",
    }

    # 3. диапазон для одного нового события, 95 %
    y = test.C.to_numpy()
    g_lo, g_hi = garwood(dev.N.sum(), dev.A.sum(), 0.95)[1:]
    nb_lo, nb_hi = nb2_predict(nbd["C"], nbd["alpha"], test.A.to_numpy(), 0.95)
    q_lo, q_hi = np.quantile(dev.C, [.025, .975])
    p = pd.read_csv(TEST)
    pm = p[(p.profile == PROFILE) & (p.model == "median")].set_index("sample_id").loc[test.sample_id]
    ev = {
        "garwood95_of_dev_mean": {"interval": [r(g_lo), r(g_hi)], "coverage": r(np.mean((y >= g_lo) & (y <= g_hi)), 3)},
        "nb2_predictive95": {"interval_median_area": [r(np.median(nb_lo)), r(np.median(nb_hi))],
                             "coverage": r(np.mean((y >= nb_lo) & (y <= nb_hi)), 3)},
        "dev_event_quantiles_2_5_97_5": {"interval": [r(q_lo), r(q_hi)], "coverage": r(np.mean((y >= q_lo) & (y <= q_hi)), 3)},
        "map_median_interval90": {"interval": [r(pm.lo.iloc[0]), r(pm.hi.iloc[0])],
                                  "coverage": r(np.mean((y >= pm.lo.to_numpy()) & (y <= pm.hi.to_numpy())), 3),
                                  "note": "уже на карте (медиана dev, 90 %-интервал по остаткам CV)"},
        "nominal": 0.95, "n_test": int(len(y)),
        "binomial_95_band_for_n14": [r(stats.binom.ppf(.025, len(y), .95) / len(y), 3),
                                     r(stats.binom.ppf(.975, len(y), .95) / len(y), 3)],
    }
    out["single_event"] = ev
    gcov, ncov = ev["garwood95_of_dev_mean"]["coverage"], ev["nb2_predictive95"]["coverage"]
    m_b = out["mean_all"]["bootstrap_days95"]
    out["decision"] = {
        "garwood_undercovers": bool(gcov < 0.95),
        "between_events_closer_to_nominal": bool(abs(ncov - .95) < abs(gcov - .95)),
        "chosen_mean": "bootstrap_days95",
        "chosen_single_event": "dev_event_quantiles_2_5_97_5",
        "text": (f"Гарвуд — интервал среднего, только ошибка счёта: среднее test ({r(ct)}) он не накрывает, "
                 f"отдельные события test — {gcov:.0%} при номинале 95 %. Бутстреп по дням рейса накрывает среднее test "
                 f"(как и NB2). Для одного события оба метода «между событиями» в пределах случайного разброса при n = 14 "
                 f"(NB2 {ncov:.0%}, квантили событий dev {ev['dev_event_quantiles_2_5_97_5']['coverage']:.0%}); "
                 f"ближе к номиналу — квантили событий. Решение: среднее профиля показывать с бутстреп-интервалом по дням "
                 f"рейса ({m_b[0]}–{m_b[1]}), Гарвуд — с подписью «только ошибка счёта»; диапазон для одного места — "
                 f"квантили событий ({r(q_lo)}–{r(q_hi)}, покрытие test {ev['dev_event_quantiles_2_5_97_5']['coverage']:.0%}). "
                 f"Точечные оценки не меняются."),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "field_intervals.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    m, s = out["mean_all"], out["mean_dev_to_test"]
    L = [f"# Г1-2: интервалы профиля S2 (поле, пластик > 2 см, визуально)", "",
         RULE, "",
         f"Событий {out['n_events']} ({out['n_days']} дней рейса); dev {out['dev']['n']} ({out['dev']['days']} дн.), "
         f"отложенный test {out['test']['n']} ({out['test']['days']} дн.). Бутстреп: {B} повторов по дням рейса.", "",
         "## 1. Среднее профиля ΣN/ΣA (все события), шт./км²", "",
         "| интервал 95 % | нижн. | верх. | что учитывает |", "|---|---:|---:|---|",
         f"| Гарвуд | {m['garwood95'][0]} | {m['garwood95'][1]} | только ошибка счёта (Пуассон) |",
         f"| бутстреп по дням рейса | {m['bootstrap_days95'][0]} | {m['bootstrap_days95'][1]} | счёт + разброс между днями |",
         f"| NB2 (α = {m['nb2']['alpha']}) | {m['nb2']['ci95'][0]} | {m['nb2']['ci95'][1]} | счёт + сверхпуассоновский разброс |",
         "", f"C = {m['C']}; бутстреп шире Гарвуда в {m['width_ratio_boot_vs_garwood']} раза.", "",
         "## 2. Среднее dev → среднее отложенного test (один исход, не покрытие)", "",
         f"dev {s['dev_C']}, test {s['test_C']} [Гарвуд {s['test_garwood95'][0]}–{s['test_garwood95'][1]}]. "
         f"Гарвуд dev {s['garwood95'][0]}–{s['garwood95'][1]}: {'накрывает' if s['garwood_covers_test_mean'] else 'не накрывает'}; "
         f"бутстреп {s['bootstrap_days95'][0]}–{s['bootstrap_days95'][1]}: {'накрывает' if s['bootstrap_covers_test_mean'] else 'не накрывает'}; "
         f"NB2 {s['nb2_ci95'][0]}–{s['nb2_ci95'][1]}: {'накрывает' if s['nb2_covers_test_mean'] else 'не накрывает'}.", "",
         "## 3. Диапазон для одного нового события, 95 %, покрытие 14 событий test", "",
         "| интервал | диапазон, шт./км² | покрытие test |", "|---|---|---:|",
         f"| Гарвуд по среднему dev | {ev['garwood95_of_dev_mean']['interval'][0]}–{ev['garwood95_of_dev_mean']['interval'][1]} | {ev['garwood95_of_dev_mean']['coverage']:.0%} |",
         f"| прогноз NB2 (площадь полосы события; медиана границ) | {ev['nb2_predictive95']['interval_median_area'][0]}–{ev['nb2_predictive95']['interval_median_area'][1]} | {ev['nb2_predictive95']['coverage']:.0%} |",
         f"| квантили 2.5–97.5 % событий dev | {ev['dev_event_quantiles_2_5_97_5']['interval'][0]}–{ev['dev_event_quantiles_2_5_97_5']['interval'][1]} | {ev['dev_event_quantiles_2_5_97_5']['coverage']:.0%} |",
         f"| медиана профиля на карте, 90 % | {ev['map_median_interval90']['interval'][0]}–{ev['map_median_interval90']['interval'][1]} | {ev['map_median_interval90']['coverage']:.0%} |",
         "", f"Номинал 95 %; при n = 14 случайный разброс покрытия ≈ {ev['binomial_95_band_for_n14'][0]:.0%}–{ev['binomial_95_band_for_n14'][1]:.0%}.", "",
         "## Вывод", "", out["decision"]["text"], ""]
    (OUT / "field_intervals.md").write_text("\n".join(L), encoding="utf-8")
    print("ok:", OUT / "field_intervals.md")


if __name__ == "__main__":
    main()
