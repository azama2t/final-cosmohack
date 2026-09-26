"""Исследовательская оценка шт./км² для спутниковых зон (решение 26.09 12:58, п.2).

Калибровка на искусственных мишенях PLP (reports/count_datasets/115_bridge_s2.md): пиксель S2, где детектор сработал,
при покрытии 28–40 % ≈ 470–670 бутылок PET 1.5 л на 100 м². Для зоны-находки:

    N_зоны = n_пикселей_детектора × [lo; hi]          (lo / hi — min / max предметов на пиксель по всем датам A*)
    C      = N_зоны / площадь_зоны (км²)              (площадь — геодезическая площадь контура зоны, measured.zone_area_km2)
    value  = n × sqrt(lo·hi) / площадь                  (геометрическое среднее границ: интервал мультипликативный)

Параметры — только в configs/zone_estimate.yaml (точки калибровки с источником); чисел калибровки в коде нет.
Оценка даётся только находкам: detection_status = detected, не снимок обучения детектора, без признаков ложного
срабатывания и не при ветре > 5 м/с. Иначе — None + причина. Статус — «исследовательская оценка», не «измерено».

Один и тот же код используют сервис (service/case_store.py: API и выгрузка) и сводка для final_numbers (summary_from_dir).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "configs" / "zone_estimate.yaml"

FLAG_RU = {"foam": "пена", "glint": "блик", "ship": "судно/кильватер", "seam": "шов/граница яркости",
           "coast": "берег/прибой", "shallow": "мелководье/мутная вода", "cloud": "облака", "wind": "ветер > 5 м/с"}


def load_config(path: Path = CONFIG) -> dict:
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def items_per_pixel(pt: dict, pixel_m2: float) -> float:
    if pt.get("items_per_pixel") is not None:
        return float(pt["items_per_pixel"])
    return float(pt["bottles_fraction"]) * float(pixel_m2) * float(pt["bottles_per_m2"])


def calibration(cfg: dict) -> dict:
    """lo / hi / value items per detector pixel from all calibration points (all A* dates) of the config."""
    pts = cfg.get("calibration_points") or []
    if not pts:
        raise ValueError("configs/zone_estimate.yaml: calibration_points пуст")
    pm2 = float(cfg.get("pixel_m2", 100))
    raw = [items_per_pixel(p, pm2) for p in pts]
    step = float(cfg.get("round_items_to") or 0)
    rnd = (lambda x: int(round(x / step) * step)) if step else (lambda x: x)
    lo, hi = rnd(min(raw)), rnd(max(raw))
    value = math.sqrt(lo * hi) if cfg.get("point", "geometric_mean") == "geometric_mean" else (lo + hi) / 2
    return {"lo": lo, "hi": hi, "value": round(value, 2), "point": cfg.get("point", "geometric_mean"),
            "raw_items_per_pixel": [round(x, 1) for x in raw], "n_points": len(pts),
            "dates": sorted({str(p.get("date")) for p in pts}), "n_dates": len({str(p.get("date")) for p in pts}),
            "campaigns": sorted({str(p.get("campaign")) for p in pts if p.get("campaign")}),
            "pixel_m2": pm2,
            "points": [{"date": str(p.get("date")), "level": p.get("level"), "campaign": p.get("campaign"),
                        "target": p.get("target"), "s2_product": p.get("s2_product"),
                        "coverage_fraction": p.get("bottles_fraction"), "items_per_pixel": round(r, 1),
                        "detector_p": p.get("detector_p")} for p, r in zip(pts, raw)],
            "source": " ".join(str(cfg.get("source") or "").split()), "config": "configs/zone_estimate.yaml"}


def sig(x: float, n: int = 3):
    """n significant digits; int when the result has no fractional part (≥ 10^(n-1))."""
    if x is None or not math.isfinite(x):
        return None
    if x == 0:
        return 0
    v = float(f"{x:.{n}g}")
    return int(v) if abs(v) >= 10 ** (n - 1) else v


def fmt(x) -> str:
    """37600 -> «37 600» (no-break space), 0.5 -> «0,5»."""
    if x is None:
        return "—"
    if isinstance(x, int) or float(x).is_integer():
        return f"{int(x):,}".replace(",", " ")
    return f"{x}".replace(".", ",")


def not_eligible_reason(p: dict) -> Optional[str]:
    """None if the zone is a find that gets an estimate; otherwise the reason (Russian), shown next to null."""
    st = p.get("detection_status")
    flags = list(p.get("flags") or [])
    if st == "not_detected":
        return "не обнаружено: объектов детектора нет — оценивать нечего"
    wind = "wind" in flags or bool(p.get("wind_high"))
    signs = [f for f in flags if f != "wind"]
    if st != "detected" or signs:
        if not signs and wind:
            return ("ветер > 5 м/с: полосы перемешаны (правило Cózar 2024), калибровка PLP — при слабом ветре; "
                    "оценку не даём")
        fl = ", ".join(FLAG_RU.get(f, f) for f in signs) or "признаки ложного срабатывания"
        return (f"недостаточно данных ({fl}{'; ветер > 5 м/с' if wind else ''}) — вероятно ложное срабатывание; "
                "оценку не даём")
    if wind:
        return ("ветер > 5 м/с: полосы перемешаны (правило Cózar 2024), калибровка PLP — при слабом ветре; оценку не даём")
    if p.get("training_scene"):
        return f"снимок из обучения детектора ({p['training_scene']}) — находкой не считается; оценку не даём"
    m = p.get("measured") or {}
    if not (m.get("n_pixels") or 0) > 0 or not (m.get("zone_area_km2") or 0) > 0:
        return "нет пикселей детектора или площади зоны"
    return None


def estimate(p: dict, cfg: dict, cal: Optional[dict] = None) -> Optional[dict]:
    """research_estimate of one zone (properties of zones.geojson or of the API) or None."""
    if not_eligible_reason(p):
        return None
    cal = cal or calibration(cfg)
    m = p.get("measured") or {}
    n, a = int(m["n_pixels"]), float(m["zone_area_km2"])
    nd = int(cfg.get("sig_digits", 3))
    n_lo, n_hi, n_val = n * cal["lo"], n * cal["hi"], n * cal["value"]
    v, lo, hi = sig(n_val / a, nd), sig(n_lo / a, nd), sig(n_hi / a, nd)
    unit = cfg.get("unit", "шт./км²")
    stat = "геометрическое среднее границ" if cal["point"] == "geometric_mean" else "середина интервала"
    short_method = "калибровка на искусственных мишенях PLP (бутылки 1.5 л)"
    return {
        "value": v, "lo": lo, "hi": hi, "unit": unit, "unit_id": "items/km2",
        "stat": stat,
        "status": cfg.get("status", "исследовательская оценка"), "status_id": cfg.get("status_id", "research_estimate"),
        "method": cfg.get("method"), "note": cfg.get("note"),
        "label": (f"≈ {fmt(v)} [{fmt(lo)}–{fmt(hi)}] {unit} · {cfg.get('status')} · {short_method}, "
                  f"{cfg.get('note')}"),
        "n_items": {"value": sig(n_val, nd), "lo": n_lo, "hi": n_hi},
        "n_items_label": f"N ≈ {fmt(sig(n_val, nd))} [{fmt(n_lo)}–{fmt(n_hi)}] шт. в зоне",
        "n_pixels": n,
        # audit 13:10: C depends on the zone contour — detector pixels are a small share of it
        "basis": "на площадь контура зоны (объекты детектора ближе 300 м объединены, контур + 150 м)",
        "det_px_share_of_zone_pct": round(n * cal["pixel_m2"] / 1e6 / a * 100, 2),
        "caveats": [
            "пиксели ниже порога детектора не считаются → по полноте оценка снизу (полнота на нитях Cózar — 36 %)",
            "мелкие предметы при том же покрытии → больше штук (калибровка — бутылки 1.5 л)",
            "класс детектора — любой плавающий материал, не только пластик → возможна переоценка"],
        "items_per_pixel": {"lo": cal["lo"], "hi": cal["hi"], "value": cal["value"]},
        "area_ref": "properties.area_km2 (= measured.zone_area_km2, площадь контура зоны)",
        "formula": (f"C = n_пикселей × [{cal['lo']}; {cal['hi']}] / площадь_зоны (км²); значение — {stat} "
                    f"(n × {cal['value']} / площадь); {nd} значащие цифры"),
        "calibration": {"n_dates": cal["n_dates"], "dates": cal["dates"], "level": "A* (искусственные мишени)",
                        "source": cal["source"], "config": cal["config"]},
        "not_what": (f"интервал — диапазон {cal['n_points']} сработавших пикселей мишеней "
                     f"{', '.join(cal['campaigns']) or 'PLP'} (дат A*: {cal['n_dates']}), не доверительный интервал и "
                     "не измерение"),
    }


def is_find(p: dict) -> bool:
    return p.get("detection_status") == "detected" and not p.get("training_scene")


def summary(props: Iterable[dict], cfg: dict) -> dict:
    """Numbers for final_numbers / «Цифры»: over all zones with an estimate."""
    cal = calibration(cfg)
    props = list(props)
    ests = [(p, estimate(p, cfg, cal)) for p in props]
    ok = [(p, e) for p, e in ests if e]
    nd = int(cfg.get("sig_digits", 3))
    out = {"status": cfg.get("status"), "method": cfg.get("method"), "note": cfg.get("note"), "unit": cfg.get("unit"),
           "items_per_pixel_lo": cal["lo"], "items_per_pixel_hi": cal["hi"], "items_per_pixel_value": cal["value"],
           "point": cal["point"], "calibration_n_dates": cal["n_dates"], "calibration_dates": cal["dates"],
           "source": cal["source"], "config": cal["config"],
           "n_finds": sum(1 for p in props if is_find(p)), "n_zones_with_estimate": len(ok)}
    if not ok:
        return out
    vals = sorted(e["value"] for _, e in ok)
    npx = sum(e["n_pixels"] for _, e in ok)
    area = sum(float(p["measured"]["zone_area_km2"]) for p, _ in ok)
    med = vals[len(vals) // 2] if len(vals) % 2 else sig((vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2, nd)
    out.update({"c_median": med, "c_min": vals[0], "c_max": vals[-1],
                "c_lo_min": min(e["lo"] for _, e in ok), "c_hi_max": max(e["hi"] for _, e in ok),
                "n_pixels_total": npx, "area_km2_total": round(area, 3),
                "n_items_total_lo": npx * cal["lo"], "n_items_total_hi": npx * cal["hi"],
                "n_items_total_value": sig(npx * cal["value"], nd),
                "c_pooled": sig(npx * cal["value"] / area, nd), "c_pooled_lo": sig(npx * cal["lo"] / area, nd),
                "c_pooled_hi": sig(npx * cal["hi"] / area, nd)})
    b = [(p, e) for p, e in ok if p.get("n_cozar_filaments")]
    if b:
        p, e = sorted(b, key=lambda t: (-(t[0].get("n_cozar_filaments") or 0), -t[0]["measured"]["n_pixels"]))[0]
        out["example"] = {"zone_id": p.get("zone_id"), "n_cozar_filaments": p.get("n_cozar_filaments"),
                          "n_pixels": e["n_pixels"], "area_km2": p["measured"]["zone_area_km2"],
                          "value": e["value"], "lo": e["lo"], "hi": e["hi"]}
    return out


def summary_from_dir(sz_dir: Path = ROOT / "data" / "case" / "scene_zones", cfg: Optional[dict] = None) -> dict:
    """The same summary from data/case/scene_zones (evaluable scenes only, as the service)."""
    cfg = cfg or load_config()
    idx_p = Path(sz_dir) / "index.json"
    if not idx_p.is_file():
        return {"available": False}
    idx = json.loads(idx_p.read_text(encoding="utf-8"))
    props = []
    for s in idx.get("scenes") or []:
        zp = Path(sz_dir) / s["key"] / "zones.geojson"
        if s.get("evaluable") and not s.get("error") and zp.is_file():
            props.extend(f["properties"] for f in json.loads(zp.read_text(encoding="utf-8")).get("features") or [])
    return {"available": True, **summary(props, cfg)}
