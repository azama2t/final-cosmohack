"""L105: Ashmore Reef (Hajbane et al. 2021, Front. Mar. Sci. 8:613399; figshare 12433823, CC BY 4.0): полёты БПЛА
с поштучным счётом предметов > 5 см, сопоставленные со снимками S2 L2A. Маски и детектор — scripts/case/pair_quality.py:process_s2
(импорт, без изменений), конфиг configs/case_pairs.yaml, детектор weights/lgbm без гармонизации.
  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe -X utf8 scripts/search/qpairs/ashmore_pairs.py
Шаги (каждый кэшируется в data/extra/qpairs/ashmore/): 1) скачать Plastic_Metadata.xlsx; 2) листы -> csv/;
3) drone_events.csv (полёты БПЛА) и events_for_stac.csv (местное время AWST = UTC+8); 4) stac_s2.csv (окно ±3 сут);
5) pair_quality для сцен с |dt| ≤ 30 ч -> pairs/<событие>__<сцена>/ и pairs.csv; 6) coverage.csv (доля площади предметов)."""
import json
import os
import sys
import types
import urllib.request

os.environ["CUDA_VISIBLE_DEVICES"] = ""
from _common import RAW, ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts" / "case"))
sys.path.insert(0, str(ROOT / "src"))
import pandas as pd  # noqa: E402

import stac_check  # noqa: E402
import xlsx2csv  # noqa: E402

D = RAW / "ashmore"
XLSX_URL = "https://ndownloader.figshare.com/files/26011175"
COLS = ["Sampling event ID", "Sampling type", "Location", "Front/Random", "Start day (Local)", "Start month (Local)",
        "Start year (Local)", "Start Local time (hours) ", "End Local time (hours)", "Start latitude (degrees)",
        "Start longitude (degrees)", "End latitude (degrees)", "End longitude (degrees)", "Sampling area (km2)",
        "Wind speed (Knots)", "# [ ] total", "# [ ] 5-10", "# [ ] 10-50", "# [ ] >50"]


def prepare():
    D.mkdir(parents=True, exist_ok=True)
    x = D / "Plastic_Metadata.xlsx"
    if not x.exists():
        req = urllib.request.Request(XLSX_URL, headers={"User-Agent": "curl/8.0"})
        x.write_bytes(urllib.request.urlopen(req, timeout=120).read())
    if not (D / "csv" / "Sampling_Event.csv").exists():
        xlsx2csv.convert(x, D / "csv")
    e = pd.read_csv(D / "csv" / "Sampling_Event.csv", low_memory=False)
    e = e[e["Sampling type"] == "Drone"]
    e[COLS].to_csv(D / "drone_events.csv", index=False)
    a = e[e.Location == "Ashmore Reef"]
    dates = [f"{int(y)}-{int(m):02d}-{int(d):02d}"
             for y, m, d in zip(a["Start year (Local)"], a["Start month (Local)"], a["Start day (Local)"])]
    pd.DataFrame(dict(id=a["Sampling event ID"], date=dates, t_utc_h=a["Start Local time (hours) "] - 8,
                      lat=(a["Start latitude (degrees)"] + a["End latitude (degrees)"]) / 2,
                      lon=(a["Start longitude (degrees)"] + a["End longitude (degrees)"]) / 2)
                 ).to_csv(D / "events_for_stac.csv", index=False)
    if not (D / "stac_s2.csv").exists():
        rows = pd.read_csv(D / "events_for_stac.csv").astype(str).to_dict("records")
        stac_check.write(stac_check.check(rows, 3.0), D / "stac_s2.csv")
    # доля площади предметов (рамка длина × ширина) от площади полёта
    it = pd.read_csv(D / "csv" / "Debris_Items.csv", low_memory=False)
    it = it[it["Sampling type"] == "Drone"]
    ev = e.set_index("Sampling event ID")
    g = it.groupby("Sampling event ID").apply(lambda t: pd.Series(dict(
        n=len(t), items_area_m2=(t["Length (cm)"] * t["Width (cm)"]).sum() / 1e4, max_len_cm=t["Length (cm)"].max())))
    g["survey_m2"] = ev.loc[g.index, "Sampling area (km2)"] * 1e6
    g["coverage"] = g.items_area_m2 / g.survey_m2
    g.to_csv(D / "coverage.csv")


def pairs():
    import pair_quality as pq
    from macroplastic.models.lgbm_predict import load_predictor
    cfg = pq.load_cfg(ROOT / "configs" / "case_pairs.yaml")
    assert pq.harmonize_mode(cfg) is None
    pred = load_predictor(ROOT / cfg["detector"]["weights"], harmonize=None)
    ev = pd.read_csv(D / "drone_events.csv").set_index("Sampling event ID")
    st = pd.read_csv(D / "stac_s2.csv")
    st = st[(st.source == "earth-search") & st["item"].notna() & (st.dt_h.abs() <= 30)]
    res = []
    for _, s in st.iterrows():
        e = ev.loc[s.id]
        g = dict(line=[(e["Start longitude (degrees)"], e["Start latitude (degrees)"]),
                       (e["End longitude (degrees)"], e["End latitude (degrees)"])],
                 lines=None, width_m=29.7, lat=(e["Start latitude (degrees)"] + e["End latitude (degrees)"]) / 2,
                 lon=(e["Start longitude (degrees)"] + e["End longitude (degrees)"]) / 2)
        od = D / "pairs" / f"{s.id}__{s['item']}"
        fp = od / "meta.json"
        if fp.exists():
            m = json.loads(fp.read_text(encoding="utf-8"))
        else:
            row = types.SimpleNamespace(endpoint="earth-search", collection="sentinel-2-l2a", item_id=s["item"])
            m = pq.process_s2(row, g, cfg, pred, od)
            m.update(event=s.id, dt_h=float(s.dt_h), front=e["Front/Random"], items_km2_gt5cm=float(e["# [ ] total"]),
                     area_km2=float(e["Sampling area (km2)"]))
            fp.write_text(json.dumps(m, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        q, d = m["quality"], m["detector"]
        res.append(dict(event=s.id, front=m["front"], items_km2_gt5cm=round(m["items_km2_gt5cm"], 1), scene=m["scene_id"],
                        dt_h=m["dt_h"], decision=m["decision"], reason=m["reason"], strip_px=q["strip_px"],
                        valid_water=q["valid_water_frac"], cloud=q["cloud_frac"], land=q["land_frac"], glint=q["glint_frac"],
                        b11w=q["glint_b11_median"], n_det_strip=d["n_det"], det_px_strip=d["det_px_strip"],
                        n_det_crop=d["n_det_crop"], prob_max=d["prob_max"]))
        print(res[-1], flush=True)
    pd.DataFrame(res).to_csv(D / "pairs.csv", index=False)


if __name__ == "__main__":
    prepare()
    pairs()
