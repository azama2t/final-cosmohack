"""EDA реестра полевых наблюдений кейса (task/macroplastic_marine_samples.csv).

Запуск:  .venv\\Scripts\\python.exe scripts\\case\\eda_samples.py
Выход:   reports/case_eda/  (summary.md, summary.json, *.csv, 4 PNG)
Без сети. Спутниковое покрытие считается только по календарю миссий (номинальная
повторяемость), а не по архиву: это верхняя граница, а не найденные сцены.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "task" / "macroplastic_marine_samples.csv"
OUT = ROOT / "reports" / "case_eda"

# Поля-утечки: ответ или производные от него (постановка, README) -> не признаки.
LEAK_FIELDS = [
    "concentration_items_km2", "concentration_g_km2", "concentration_value_orig",
    "concentration_unit_orig", "items_count", "density_numerator_items",
    "source_object_filtered_items", "source_reported_total_items",
    "reported_concentration_items_km2", "reported_concentration_g_km2",
    "parent_concentration_items_km2", "parent_sample_id", "zero_scope",
]

# Календарь миссий: (начало данных, номинальная повторяемость на экваторе, сут)
MISSIONS = {
    "S2A": ("2015-06-27", 10.0),
    "S2B": ("2017-07-06", 10.0),  # запуск 07.03.2017, данные в архиве с лета 2017
    "L8": ("2013-04-11", 16.0),
    "L9": ("2021-10-31", 16.0),
}
WINDOWS = [0, 1, 3, 5]

# Размеры сторон пикселей (м) против размерного порога профиля (см)
PROFILE_MIN_SIZE_CM = {
    "S1_trawl_5_to_50": 5, "S1_trawl_GT5_H": 5, "S1_trawl_GT5_N": 5, "S1_trawl_GT5_F": 5,
    "S1_aerial_GT50": 50, "S2_visual_GT2": 2, "S3_visual_GT2": 2, "S4_visual_GT2_5": 2.5,
}


def md_table(df: pd.DataFrame) -> str:
    df = df.copy()
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join("" if pd.isna(v) else (f"{v:.4g}" if isinstance(v, float) else str(v)) for v in r.values) + " |")
    return "\n".join(lines)


def mission_rates(date: pd.Timestamp) -> dict[str, float]:
    """Число номинальных пролётов в сутки по миссиям, работающим на дату."""
    rates = {}
    for m, (start, rev) in MISSIONS.items():
        if date >= pd.Timestamp(start):
            rates[m] = 1.0 / rev
    return rates


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    d = pd.read_csv(CSV)
    d["date"] = pd.to_datetime(d["date_utc"])
    res: dict = {"rows": len(d), "cols": d.shape[1] - 1, "events": int(d.event_id.nunique())}
    md: list[str] = ["# EDA реестра наблюдений (task/macroplastic_marine_samples.csv)", "",
                     f"Строк {len(d)}, полей {d.shape[1] - 1}, событий {d.event_id.nunique()}. Генерирует `scripts/case/eda_samples.py`.", ""]

    # 1. Распределения категориальных полей
    cats = ["source_id", "record_type", "target_scope", "measurement_profile", "size_class",
            "position_role", "material", "missions_calendar_eligible", "concentration_basis"]
    for c in cats:
        vc = d[c].value_counts(dropna=False).rename_axis(c).reset_index(name="rows")
        vc.to_csv(OUT / f"dist_{c}.csv", index=False)
        res[f"dist_{c}"] = dict(zip(vc[c].astype(str), vc["rows"].astype(int)))
    md += ["## 1. Состав", ""]
    ct = pd.crosstab([d.source_id, d.measurement_profile], [d.record_type, d.target_scope])
    ct.columns = [f"{a}/{b}" for a, b in ct.columns]
    ct = ct.reset_index()
    ct.to_csv(OUT / "crosstab_profile_scope.csv", index=False)
    md += [md_table(ct), ""]

    # 2. Пропуски
    miss = (d.isna().groupby(d.source_id).mean() * 100).round(0).T
    miss.to_csv(OUT / "missing_pct_by_source.csv")
    key = ["time_start_utc", "time_end_utc", "latitude", "lat_start", "lat_end", "sampled_area_km2",
           "transect_length_km", "transect_width_m", "items_count", "density_numerator_items",
           "concentration_items_km2", "sea_state_beaufort", "wind_speed_kn", "parent_sample_id"]
    md += ["## 2. Пропуски, % строк по источнику (ключевые поля)", "", md_table(miss.loc[key].reset_index().rename(columns={"index": "field"})), ""]

    # 3. Даты и время, bbox
    g = d.groupby("source_id")
    geo = g.agg(rows=("sample_id", "size"), events=("event_id", "nunique"),
                date_min=("date_utc", "min"), date_max=("date_utc", "max"),
                n_dates=("date_utc", "nunique"),
                time_known_pct=("time_start_utc", lambda s: round(100 * s.notna().mean())),
                lat_min=("latitude", "min"), lat_max=("latitude", "max"),
                lon_min=("longitude", "min"), lon_max=("longitude", "max")).reset_index()
    for c in ["lat_min", "lat_max", "lon_min", "lon_max"]:
        geo[c] = geo[c].round(2)
    geo.to_csv(OUT / "sources_time_bbox.csv", index=False)
    res["sources"] = geo.to_dict(orient="records")
    md += ["## 3. Даты, время, география", "", md_table(geo), ""]
    # длительность трансект (переход через полночь учтён)
    tr = d[d.record_type == "transect_density"].copy()
    ts = pd.to_datetime(tr.date_utc + " " + tr.time_start_utc.fillna(""), errors="coerce")
    te = pd.to_datetime(tr.date_utc + " " + tr.time_end_utc.fillna(""), errors="coerce")
    dur = (te - ts).dt.total_seconds() / 3600
    wrap = dur < 0
    dur = dur.where(~wrap, dur + 24)
    tr["dur_h"] = dur
    durt = tr.groupby("measurement_profile").agg(n=("dur_h", "size"), dur_known=("dur_h", lambda s: int(s.notna().sum())),
                                                 dur_med_h=("dur_h", "median"), dur_max_h=("dur_h", "max"),
                                                 midnight_wrap=("dur_h", lambda s: int(wrap.loc[s.index].sum()))).reset_index()
    durt.to_csv(OUT / "transect_duration.csv", index=False)
    md += ["Длительность наблюдения (трансекты; конец < начала = переход через полночь, +24 ч):", "", md_table(durt), ""]

    # 4. События и зависимость строк
    ev = d.groupby("event_id").agg(rows=("sample_id", "size"), source=("source_id", "first"),
                                   profiles=("measurement_profile", "nunique"),
                                   has_item=("record_type", lambda s: (s == "item_observation").any())).reset_index()
    evs = ev.groupby("source").agg(events=("event_id", "size"), rows_med=("rows", "median"), rows_max=("rows", "max"),
                                   multi_row_events=("rows", lambda s: int((s > 1).sum())),
                                   events_with_items=("has_item", "sum")).reset_index()
    evs.to_csv(OUT / "events_by_source.csv", index=False)
    # один день = одна сцена: события в один день и в одном районе попадут в один тайл
    ed = d.groupby("event_id").agg(source=("source_id", "first"), date=("date_utc", "first"),
                                   lat=("latitude", "mean"), lon=("longitude", "mean")).reset_index()
    same_day = ed.groupby(["source", "date"]).size()
    evs["events_per_day_med"] = evs.source.map(same_day.groupby(level=0).median())
    evs["events_per_day_max"] = evs.source.map(same_day.groupby(level=0).max())
    md += ["## 4. События (строки одного события зависимы)", "", md_table(evs), "",
           "Несколько событий в один день = потенциально одна спутниковая сцена -> группа сплита «день×район», не event_id.", ""]
    res["events_by_source"] = evs.to_dict(orient="records")

    # 5. Целевая величина: трансектные плотности по профилю и scope
    t = d[d.record_type == "transect_density"]
    tgt = t.groupby(["measurement_profile", "target_scope"]).agg(
        rows=("sample_id", "size"), events=("event_id", "nunique"),
        conc_known=("concentration_items_km2", lambda s: int(s.notna().sum())),
        zeros=("concentration_items_km2", lambda s: int((s == 0).sum())),
        area_known=("sampled_area_km2", lambda s: int(s.notna().sum())),
        numerator_known=("density_numerator_items", lambda s: int(s.notna().sum())),
        time_known=("time_start_utc", lambda s: int(s.notna().sum())),
        median=("concentration_items_km2", "median"), p90=("concentration_items_km2", lambda s: s.quantile(0.9)),
        max=("concentration_items_km2", "max"), date_min=("date_utc", "min"), date_max=("date_utc", "max")).reset_index()
    tgt.to_csv(OUT / "targets_by_profile_scope.csv", index=False)
    md += ["## 5. Кандидаты целевой величины (transect_density)", "", md_table(tgt), ""]
    res["targets"] = tgt.to_dict(orient="records")
    zs = d.zero_scope.value_counts().rename_axis("zero_scope").reset_index(name="rows")
    md += ["Нули и их область (`zero_scope`):", "", md_table(zs), ""]

    # 6. Проверка C = N / A
    m = t.density_numerator_items.notna() & t.sampled_area_km2.notna() & t.concentration_items_km2.notna()
    r = t[m].copy()
    r["c_calc"] = r.density_numerator_items / r.sampled_area_km2
    r["abs_err"] = (r.c_calc - r.concentration_items_km2).abs()
    r["rel_err"] = r.abs_err / r.concentration_items_km2.replace(0, np.nan)
    chk = r.groupby("measurement_profile").agg(rows=("c_calc", "size"), max_abs_err=("abs_err", "max"),
                                               max_rel_err=("rel_err", "max")).reset_index()
    chk.to_csv(OUT / "formula_check.csv", index=False)
    no_num = t[~m].groupby("measurement_profile").size().rename("rows_without_N_or_A").reset_index()
    ctrl = 12 / 0.20
    res["control_example"] = {"N": 12, "A_km2": 0.20, "C": ctrl, "model": 75, "abs_err": abs(75 - ctrl)}
    res["formula_check"] = chk.to_dict(orient="records")
    md += ["## 6. Проверка C = N/A", "", f"Контроль: 12 / 0.20 = {ctrl:g} шт./км²; модель 75 -> |ошибка| = {abs(75 - ctrl):g} шт./км².", "",
           md_table(chk), "", "Строки без N или A (C = N/A проверить нельзя; published estimate):", "", md_table(no_num), ""]

    # 7. quality_flags
    qf = d.quality_flags.fillna("").str.split(";").explode().str.strip()
    qf = qf.replace("", "(пусто)")
    qft = pd.crosstab(qf.values, d.loc[qf.index, "source_id"].values, rownames=["quality_flags"], colnames=["source_id"])
    qft.to_csv(OUT / "quality_flags_by_source.csv")
    md += ["## 7. quality_flags по источникам (строки)", "", md_table(qft.reset_index().rename(columns={"quality_flags": "flag"})), ""]

    # 8. Календарь миссий: ожидаемое число пролётов в окне ±k сут на событие
    ev1 = d.groupby("event_id").agg(source=("source_id", "first"), profile=("measurement_profile", "first"),
                                    date=("date", "first")).reset_index()
    rows = []
    for _, e in ev1.iterrows():
        rates = mission_rates(e.date)
        rec = {"event_id": e.event_id, "source": e.source, "date": e.date.date().isoformat(),
               "missions": "+".join(rates) or "none",
               "s2_ok": any(k.startswith("S2") for k in rates), "landsat_ok": any(k.startswith("L") for k in rates)}
        for k in WINDOWS:
            days = 2 * k + 1
            rec[f"exp_S2_pm{k}"] = days * sum(v for m_, v in rates.items() if m_.startswith("S2"))
            rec[f"exp_L_pm{k}"] = days * sum(v for m_, v in rates.items() if m_.startswith("L"))
            lam = days * sum(rates.values())
            rec[f"p_any_pm{k}"] = 1 - np.exp(-lam)  # пуассоновское приближение, без учёта плана съёмки
        rows.append(rec)
    cal = pd.DataFrame(rows)
    cal.to_csv(OUT / "events_mission_calendar.csv", index=False)
    calsum = cal.groupby("source").agg(events=("event_id", "size"), s2_calendar=("s2_ok", "sum"),
                                       landsat_calendar=("landsat_ok", "sum"),
                                       **{f"E_pm{k}": (f"p_any_pm{k}", "sum") for k in WINDOWS}).reset_index()
    for k in WINDOWS:
        calsum[f"E_pm{k}"] = calsum[f"E_pm{k}"].round(1)
    tot = calsum.drop(columns="source").sum(numeric_only=True)
    tot["source"] = "ИТОГО"
    calsum = pd.concat([calsum, tot.to_frame().T], ignore_index=True)
    calsum.to_csv(OUT / "mission_calendar_summary.csv", index=False)
    res["calendar"] = calsum.to_dict(orient="records")
    md += ["## 8. Календарь миссий (без сети, верхняя граница)", "",
           "E_pm{k} — ожидаемое число событий, у которых есть хотя бы один номинальный пролёт S2/Landsat в окне ±k сут "
           "(1 - exp(-λ), λ = (2k+1)·Σ 1/revisit; S2 10 сут на спутник, Landsat 16 сут). Не учитывает план съёмки "
           "(открытый океан S2 не снимает систематически), облака, блик и дрейф.", "", md_table(calsum), ""]

    # 9. Размер предмета против пикселя
    sz = pd.DataFrame([{"profile": p, "min_item_cm": s, "pixel_10m_area_m2": 100,
                        "item_to_pixel_area_ratio_at_min": (s / 100) ** 2 / 100} for p, s in PROFILE_MIN_SIZE_CM.items()])
    # плотность -> доля покрытия пикселя: C шт./км² * 1 пикс (1e-4 км²) = предметов на пиксель
    t_known = t[t.concentration_items_km2.notna()]
    per_px = t_known.groupby("measurement_profile").concentration_items_km2.agg(["median", "max"]) * 1e-4
    per_px.columns = ["items_per_10m_px_median", "items_per_10m_px_max"]
    sz = sz.merge(per_px, left_on="profile", right_index=True, how="left")
    sz.to_csv(OUT / "size_vs_pixel.csv", index=False)
    md += ["## 9. Размер предмета против пикселя 10 м", "", md_table(sz), "",
           "Даже максимум реестра (~2200 шт./км²) даёт ~0.22 предмета на пиксель 10×10 м: отдельные предметы "
           "невидимы, связь «пиксель -> плотность» возможна только через скопления/полосы.", ""]

    # 10. Утечки
    leak = pd.DataFrame({"field": LEAK_FIELDS,
                         "non_null_rows": [int(d[f].notna().sum()) for f in LEAK_FIELDS]})
    leak.to_csv(OUT / "leak_fields.csv", index=False)
    md += ["## 10. Поля-утечки (исключены из признаков)", "", md_table(leak), ""]

    # ---- графики ----
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
    colors = {"S1_GPGP2018": "#2a6fdb", "S2_SARGASSO_MSM41": "#e8833a", "S3_SE_NORTH_SEA": "#3aa675", "S4_BLACK_SEA_DOORS3": "#8e5bd0"}

    # (a) концентрация по профилю, лог-шкала, нули отдельно
    fig, ax = plt.subplots(figsize=(8, 4.2))
    profs = tgt.assign(lab=tgt.measurement_profile + "\n" + tgt.target_scope)
    for i, (_, p) in enumerate(profs.iterrows()):
        s = t[(t.measurement_profile == p.measurement_profile) & (t.target_scope == p.target_scope)].concentration_items_km2.dropna()
        pos = s[s > 0]
        src = t[t.measurement_profile == p.measurement_profile].source_id.iloc[0]
        ax.scatter(np.full(len(pos), i) + np.random.default_rng(0).uniform(-0.25, 0.25, len(pos)), pos, s=8,
                   alpha=0.6, color=colors[src])
        nz = int((s == 0).sum())
        if nz:
            ax.annotate(f"нулей {nz}", (i, 0.02), ha="center", fontsize=7, color="#555")
    ax.set_yscale("log")
    ax.set_ylim(0.01, 5000)
    ax.set_xticks(range(len(profs)))
    ax.set_xticklabels(profs.lab, rotation=60, ha="right", fontsize=7)
    ax.set_ylabel("concentration_items_km2, шт./км² (лог)")
    ax.set_title("Плотности по профилю и совокупности: разные величины, порядки различаются")
    fig.tight_layout()
    fig.savefig(OUT / "conc_by_profile.png", dpi=130)
    plt.close(fig)

    # (b) карта событий
    fig, axes = plt.subplots(1, 4, figsize=(12, 3.2))
    for ax, (src, gdf) in zip(axes, ed.groupby("source")):
        ax.scatter(gdf.lon, gdf.lat, s=10, color=colors[src])
        ax.set_title(f"{src}\n{len(gdf)} событий", fontsize=8)
        ax.set_xlabel("lon")
        ax.set_ylabel("lat")
    fig.suptitle("События реестра (средняя точка строк события)", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "events_map.png", dpi=130)
    plt.close(fig)

    # (c) временная шкала событий и миссий
    fig, ax = plt.subplots(figsize=(9, 3))
    srcs = list(colors)
    for i, src in enumerate(srcs):
        dd = ed[ed.source == src].date.map(pd.Timestamp)
        ax.scatter(dd, np.full(len(dd), i), s=12, color=colors[src])
    for j, (m_, (start, _)) in enumerate(MISSIONS.items()):
        ax.axvline(pd.Timestamp(start), color="#888", lw=0.8, ls="--")
        ax.text(pd.Timestamp(start), 1.01, f" {m_}", transform=ax.get_xaxis_transform(), fontsize=7, color="#555")
    ax.set_yticks(range(len(srcs)))
    ax.set_yticklabels(srcs, fontsize=8)
    ax.set_title("Даты событий и начало данных миссий (пунктир)", pad=14)
    fig.tight_layout()
    fig.savefig(OUT / "timeline_missions.png", dpi=130)
    plt.close(fig)

    # (d) ожидаемое число пар по окну
    fig, ax = plt.subplots(figsize=(6, 3.2))
    cs = calsum[calsum.source != "ИТОГО"]
    bottom = np.zeros(len(WINDOWS))
    for _, rr in cs.iterrows():
        vals = np.array([float(rr[f"E_pm{k}"]) for k in WINDOWS])
        ax.bar([f"±{k}" for k in WINDOWS], vals, bottom=bottom, color=colors[rr.source], label=rr.source)
        bottom += vals
    ax.set_ylabel("ожидаемо событий с пролётом")
    ax.set_xlabel("окно, сут")
    ax.set_title("Календарная верхняя граница пар (без плана съёмки и облаков)", fontsize=8)
    ax.legend(fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(OUT / "pairs_calendar.png", dpi=130)
    plt.close(fig)

    (OUT / "summary.md").write_text("\n".join(md), encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"ok: {OUT}")


if __name__ == "__main__":
    main()
