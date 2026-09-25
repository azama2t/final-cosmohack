"""Доказательство пользы поиска синхронной сцены: прогон pairfinder по всем 318 событиям реестра офлайн
(кэш data/pairs/cache, тот же, что у find_pairs; сеть не используется) и сравнение с наивным окном ±1 сут.

  .venv\\Scripts\\python.exe scripts\\case\\pairfinder_evidence.py
Выход: reports/case_pairfinder/evidence_numbers.json, reports/case_pairfinder/evidence_events.csv (печать таблиц).
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

os.environ["MACROPLASTIC_PAIRFINDER_OFFLINE"] = "1"
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import pandas as pd  # noqa: E402

from macroplastic.case import pairfinder as pf  # noqa: E402

OUT = ROOT / "reports" / "case_pairfinder"


def hindcast(ev: pd.DataFrame) -> dict:
    """Проверка режима прогноза на архиве: каждая сцена + k × период повторения (k = ±1..3) должна совпасть
    с реальной съёмкой той же миссии над тем же тайлом. Считаются только предсказания, попавшие внутрь окна
    какого-либо запроса реестра, вернувшего сцены этого тайла (иначе нечем проверить)."""
    fp = pf.find_pairs_mod()
    rows = []
    for r in ev.itertuples():
        t = pd.Timestamp(r.obs_datetime)
        bbox, dt0, dt1 = pf.search_args(r.lon, r.lat, t, 5 + (0 if r.time_known else 0.5))
        for _, url, coll, _ in fp.SOURCES:
            p = fp.CACHE / f"{pf.cache_key(url, coll, bbox, dt0, dt1)}.json"
            if not p.exists():
                continue
            for f in json.loads(p.read_text()):
                m = fp.PLATFORM.get(str(f.get("platform") or "").lower())
                if m not in pf.REPEAT_DAYS:
                    continue
                tile = (f"{f.get('wrs_path')}/{f.get('wrs_row')}" if coll.startswith("landsat")
                        else str(f.get("mgrs") or "").replace("MGRS-", ""))
                rows.append(dict(coll=coll, m=m, tile=tile, t=pd.Timestamp(f["datetime"]),
                                 w0=pd.Timestamp(dt0), w1=pd.Timestamp(dt1)))
    d = pd.DataFrame(rows)
    d["t"] = pd.to_datetime(d.t, utc=True)
    res = []
    for (coll, m, tile), g in d.groupby(["coll", "m", "tile"]):
        scenes = sorted(g.t.unique())
        wins = g[["w0", "w1"]].drop_duplicates().values
        cyc = pd.Timedelta(days=pf.REPEAT_DAYS[m])
        for s in scenes:
            for k in (1, 2, 3, -1, -2, -3):
                p = s + k * cyc
                if not any(a <= p - pd.Timedelta(hours=1) and p + pd.Timedelta(hours=1) <= b for a, b in wins):
                    continue
                err = min(abs((x - p).total_seconds()) / 60 for x in scenes)
                res.append(dict(coll=coll, m=m, tile=tile, err_min=err))
    r = pd.DataFrame(res)
    return {"predictions_checked": len(r), "hit_within_5min": int((r.err_min <= 5).sum()),
            "hit_share": round(float((r.err_min <= 5).mean()), 3),
            "by_collection": {c: {"n": len(g), "hit_5min": int((g.err_min <= 5).sum())} for c, g in r.groupby("coll")},
            "misses_by_tile": {k: int(v) for k, v in r[r.err_min > 5].groupby("tile").size().items()},
            "median_error_min_hits": round(float(r[r.err_min <= 5].err_min.median()), 2)}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = pf.load_cfg()
    ev = pd.read_csv(ROOT / "data" / "pairs" / "events.csv")
    cand = pd.read_parquet(ROOT / "data" / "pairs" / "candidates.parquet")
    pq = pd.read_csv(ROOT / "data" / "pairs" / "pair_quality.csv") if (ROOT / "data" / "pairs" / "pair_quality.csv").exists() else None
    rows, miss = [], 0
    for r in ev.itertuples():
        t = pd.Timestamp(r.obs_datetime)
        body = {"geometry": {"lon": float(r.lon), "lat": float(r.lat)},
                "datetime": t.strftime("%Y-%m-%dT%H:%M:%SZ") if r.time_known else t.strftime("%Y-%m-%d"),
                "window_days": 5, "offline": True}
        if pd.notna(r.width_m):
            body["width_m"] = float(r.width_m)
        if pd.notna(r.wind_ms):
            body["wind_ms"] = float(r.wind_ms)
        res = pf.run(body, cfg, now=pd.Timestamp("2026-09-25", tz="UTC"))
        miss += sum(s["status"] != "cache" for s in res["search"]["sources"])
        cs = [c for c in res["candidates"] if c["decision"] != "reject"]
        near = min(cs, key=lambda c: abs(c["dt_hours"])) if cs else None
        same_day = [c for c in cs if c["scene_datetime"][:10] == t.strftime("%Y-%m-%d")]
        same_day_clear = [c for c in same_day if c["scene_cloud_cover"] is not None and c["scene_cloud_cover"] <= 60]
        wmax = res["sync_window"]["max_abs_dt_hours"]
        rows.append(dict(
            event_id=r.event_id, source_id=r.source_id, time_known=bool(r.time_known), obs=res["query"]["obs_datetime"],
            n_scenes=len(cs), tolerance_km=res["sync_window"]["tolerance_km"], max_abs_dt_h=wmax,
            n_sync=res["n_synchronous"], n_cond=res["n_synchronous_if_time_in_window"],
            nearest_scene=near["scene_id"] if near else None, nearest_dt_h=near["dt_hours"] if near else None,
            nearest_cloud=near["scene_cloud_cover"] if near else None,
            shift_needed_h=round(max(abs(near["dt_hours"]) - wmax, 0), 2) if (near and r.time_known) else None,
            same_day=len(same_day) > 0, same_day_clear=len(same_day_clear) > 0,
            same_day_best=(same_day_clear or same_day)[0]["scene_id"] if same_day else None,
            same_day_window=(f"{(same_day_clear or same_day)[0]['sync_time_window']['from'][11:16]}–"
                             f"{(same_day_clear or same_day)[0]['sync_time_window']['to'][11:16]}") if same_day else None,
            meta_accept=bool(cand[(cand.event_id == r.event_id)].accept_meta.any()),
            drift_accept=bool(cand[(cand.event_id == r.event_id)].accept.any())))
    t = pd.DataFrame(rows)
    if pq is not None:
        qd = pq.set_index("event_id")[["scene_id", "decision", "reason"]]
        t = t.join(qd.rename(columns={"scene_id": "l61_scene", "decision": "l61_quality", "reason": "l61_reason"}), on="event_id")
    t.to_csv(OUT / "evidence_events.csv", index=False)

    def per_src(mask):
        return {s: int(n) for s, n in t[mask].groupby("source_id").size().items()}
    known = t.time_known
    num = {
        "events": len(t), "cache_misses": miss,
        "any_scene_pm5d": int((t.n_scenes > 0).sum()), "any_scene_pm5d_by_source": per_src(t.n_scenes > 0),
        "naive_pm1d_meta_accept": int(t.meta_accept.sum()), "naive_pm1d_meta_accept_by_source": per_src(t.meta_accept),
        "naive_pm1d_with_drift": int(t.drift_accept.sum()),
        "pairfinder_synchronous_time_known": int((t.n_sync > 0).sum()),
        "pairfinder_conditional_date_only": int((t.n_cond > 0).sum()),
        "pairfinder_conditional_by_source": per_src(t.n_cond > 0),
        "same_day_scene": int(t.same_day.sum()), "same_day_scene_by_source": per_src(t.same_day),
        "same_day_scene_cloud_le60": int(t.same_day_clear.sum()), "same_day_scene_cloud_le60_by_source": per_src(t.same_day_clear),
        "time_known_with_scene": int((known & (t.n_scenes > 0)).sum()),
        "time_known_shift_needed_h": t[known & t.shift_needed_h.notna()].shift_needed_h.describe().round(1).to_dict(),
        "time_known_shift_le_4h": int((known & (t.shift_needed_h <= 4)).sum()),
        "time_known_shift_le_12h": int((known & (t.shift_needed_h <= 12)).sum()),
        "max_abs_dt_h_typical_3km": round(pf.sync_window_hours(3.0, 0.2), 2),
    }
    if "l61_quality" in t:
        c = t[t.n_cond > 0]
        num["conditional_with_l61_quality"] = dict(Counter(zip(c.l61_quality.fillna("—"), c.l61_reason.fillna(""))))
        num["conditional_with_l61_quality"] = {f"{a} {b}".strip(): v for (a, b), v in num["conditional_with_l61_quality"].items()}
    num["forecast_hindcast"] = hindcast(ev)
    (OUT / "evidence_numbers.json").write_text(json.dumps(num, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(num, ensure_ascii=False, indent=1, default=str))
    cols = ["event_id", "obs", "n_scenes", "nearest_dt_h", "nearest_cloud", "same_day_best", "same_day_window",
            "l61_quality", "l61_reason"]
    print(t[t.n_cond > 0][[c for c in cols if c in t]].to_string())
    print(t[known & (t.n_scenes > 0)][["event_id", "obs", "nearest_scene", "nearest_dt_h", "nearest_cloud", "shift_needed_h",
                                       "meta_accept"]].sort_values("shift_needed_h").to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
