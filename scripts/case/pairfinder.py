"""CLI к src/macroplastic/case/pairfinder.py (L71): есть ли синхронная сцена S2/Landsat для полевого наблюдения.

  .venv\\Scripts\\python.exe scripts\\case\\pairfinder.py --lon 30.93 --lat 43.07 --datetime 2024-06-02
  .venv\\Scripts\\python.exe scripts\\case\\pairfinder.py --line 7.1,54.2,7.3,54.3 --datetime 2016-04-08T16:38:30Z --speed-ms 0.1
  .venv\\Scripts\\python.exe scripts\\case\\pairfinder.py --lon 30.93 --lat 43.07 --datetime 2024-06-02T09:00:00Z --quality --json out.json
  ... --offline   (только кэш data/pairs/cache; то же, что MACROPLASTIC_PAIRFINDER_OFFLINE=1)

Печатает окно синхронизации и таблицу кандидатов; --json сохраняет полный ответ (тот же, что у POST /api/v3/pairfinder).
Код выхода: 0 — есть синхронный или условно синхронный кандидат, 1 — нет, 2 — ошибка ввода.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from macroplastic.case import pairfinder as pf  # noqa: E402


def build_body(a) -> dict:
    if a.line:
        v = [float(x) for x in a.line.split(",")]
        if len(v) != 4:
            raise pf.PairfinderError(422, "BAD_GEOMETRY", "--line: lon_start,lat_start,lon_end,lat_end")
        geom = {"lon_start": v[0], "lat_start": v[1], "lon_end": v[2], "lat_end": v[3]}
    elif a.lon is not None and a.lat is not None:
        geom = {"lon": a.lon, "lat": a.lat}
    else:
        raise pf.PairfinderError(422, "BAD_GEOMETRY", "Нужны --lon и --lat или --line")
    body = {"geometry": geom, "datetime": a.datetime, "window_days": a.window_days, "quality": a.quality}
    for k in ("drift_scenario", "speed_ms", "tolerance_km", "width_m", "wind_ms", "max_cloud"):
        v = getattr(a, k)
        if v is not None:
            body[k] = v
    if a.offline:
        body["offline"] = True
    return body


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lon", type=float)
    ap.add_argument("--lat", type=float)
    ap.add_argument("--line", help="lon_start,lat_start,lon_end,lat_end")
    ap.add_argument("--datetime", required=True, help="YYYY-MM-DDTHH:MM:SSZ или YYYY-MM-DD")
    ap.add_argument("--window-days", type=float, default=5)
    ap.add_argument("--drift-scenario", choices=["low", "typical", "high"])
    ap.add_argument("--speed-ms", type=float)
    ap.add_argument("--tolerance-km", type=float)
    ap.add_argument("--width-m", type=float)
    ap.add_argument("--wind-ms", type=float)
    ap.add_argument("--max-cloud", type=float)
    ap.add_argument("--quality", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--json", help="сохранить полный ответ в файл")
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args(argv)
    try:
        res = pf.run(build_body(a))
    except pf.PairfinderError as e:
        print(f"ошибка {e.status} {e.code}: {e.message} {e.details or ''}", file=sys.stderr)
        return 2
    sw = res["sync_window"]
    print(f"Окно синхронизации: |dt| ≤ {sw['max_abs_dt_hours']} ч (сценарий {sw['scenario']}, {sw['speed_ms']} м/с, "
          f"допуск {sw['tolerance_km']} км); по сценариям: {sw['max_abs_dt_hours_by_scenario']}")
    q = res["query"]
    print(f"Наблюдение {q['obs_datetime']} (время {'задано' if q['time_known'] else 'НЕ задано'}), режим {q['mode']}, "
          f"офлайн {q['offline']}; кандидатов {res['count']}, синхронных {res['n_synchronous']}, "
          f"условно (если время в окне) {res['n_synchronous_if_time_in_window']}")
    for c in res["candidates"][:a.top]:
        cc = "—" if c["scene_cloud_cover"] is None else f"{c['scene_cloud_cover']:.0f}%"
        line = (f"  {c['decision']:<30} {c['mission']:<4} {c['level']:<4} {c['scene_datetime']} dt {c['dt_hours']:+7.1f} ч "
                f"сдвиг {c['drift']['shift_km_selected']:6.1f} км  облачн. {cc:>5}  {c['scene_id']}")
        print(line)
        print(f"      {c['reason']}")
        if c.get("quality"):
            qq = c["quality"]
            print(f"      качество полосы: {qq.get('status')} {qq.get('decision', '')} {qq.get('reason') or ''} "
                  f"вода {qq.get('valid_water_frac')} облака {qq.get('cloud_frac')} блик {qq.get('glint_frac')} "
                  f"суша {qq.get('land_frac')} B11 воды {qq.get('glint_b11_median')}")
    if res["empty_reason"]:
        print("Пусто:", res["empty_reason"])
    for n in res["notes"]:
        print("Примечание:", n)
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if (res["n_synchronous"] or res["n_synchronous_if_time_in_window"]) else 1


if __name__ == "__main__":
    sys.exit(main())
