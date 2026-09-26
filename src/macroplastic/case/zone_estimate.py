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


REQUIRED = ("status", "method", "note", "unit", "calibration_points")


def validate_config(cfg: dict) -> dict:
    """Raise ValueError if the config is incomplete (e.g. read while being written); return it otherwise."""
    if not isinstance(cfg, dict):
        raise ValueError("configs/zone_estimate.yaml: не словарь")
    miss = [k for k in REQUIRED if not cfg.get(k)]
    if miss:
        raise ValueError(f"configs/zone_estimate.yaml: нет ключей {', '.join(miss)}")
    calibration(cfg)
    return cfg


def load_config(path: Path = CONFIG) -> dict:
    import yaml
    return validate_config(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


def write_config_atomic(text: str, path: Path = CONFIG) -> None:
    """Write the config atomically: validate, write a temp file next to it, then os.replace (readers never see a
    half-written file)."""
    import os
    import tempfile
    import yaml
    validate_config(yaml.safe_load(text) or {})
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


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
            "coverage_pct_lo": round(100 * min(float(p.get("bottles_fraction") or 0) for p in pts)),
            "coverage_pct_hi": round(100 * max(float(p.get("bottles_fraction") or 0) for p in pts)),
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


def scenario_text(cfg: dict, cal: dict) -> Optional[str]:
    """§36 п.2: «исследовательский сценарий по искусственным мишеням PLP (допущения: …)» with numbers from the points."""
    t = " ".join(str(cfg.get("scenario") or "").split())
    return t.format(lo=cal["lo"], hi=cal["hi"], cov_lo=cal["coverage_pct_lo"], cov_hi=cal["coverage_pct_hi"]) if t else None


def firing_caveat(cfg: dict) -> Optional[str]:
    """P1: firing is not monotonic in the number of items — the 470–670 scenario is two points, not a law."""
    f = cfg.get("firing") or {}
    if not f:
        return None
    return (f"срабатывание не монотонно по числу: из {f['n_water_pixels_ge400']} водных пикселей мишеней с ≥ 400 "
            f"бутылками детектор сработал на {f['n_fired']}; пиксель {f.get('max_not_fired_campaign', 'PLP')} с ≈ "
            f"{fmt(int(f['max_not_fired']))} бутылками — без срабатывания; сценарий — две точки, не закон")


def natural_pair_note(cfg: dict) -> Optional[str]:
    """The natural-pair sentence (ISPRA 604) — only after it is confirmed (natural_pair.confirmed in the config)."""
    np_ = cfg.get("natural_pair") or {}
    return " ".join(str(np_.get("text") or "").split()) or None if np_.get("confirmed") else None


def plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    w = one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many
    return f"{n} {w}"


def fmt_ru(x: Optional[float]) -> str:
    """1.54 -> «1,5», 53.9 -> «54» (two significant digits, decimal comma)."""
    return "—" if x is None else fmt(sig(float(x), 2))


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


def estimate(p: dict, cfg: dict, cal: Optional[dict] = None, field_range: Optional[tuple] = None) -> Optional[dict]:
    """research_estimate of one zone (properties of zones.geojson or of the API) or None.
    Shown as a lower bound «≥ ~X» (X = n × lo / area, 2 significant digits): lo/hi are the spread of the calibration
    points, not a confidence interval (jury 13:47, audit В19). field_range = (min, max) field items/km2 for the context."""
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
    lb, n_lb = sig(n_lo / a, 2), sig(n_lo, 2)
    npx_d = f"{plural(cal['n_points'], 'пиксель', 'пикселя', 'пикселей')} на {plural(cal['n_dates'], 'дате', 'датах', 'датах')}"
    bound_note = f"неопределённость калибровки не оценена: {npx_d} PLP"
    # confirmation: level_B_cozar = crosses a Cózar filament (independent human labels) -> normal; no independent labels -> muted
    scen_line = " ".join(str(cfg.get("scenario_line") or "").split()).format(
        x=fmt(lb), cov_lo=cal["coverage_pct_lo"], cov_hi=cal["coverage_pct_hi"]) or None
    muted = p.get("verification") != "level_B_cozar"
    fr = (f"с полевыми шт./км² (среднее по маршруту: {fmt_ru(field_range[0])}–{fmt_ru(field_range[1])}) не сравнивать "
          "напрямую: другой масштаб (внутри нити против среднего по маршруту)") if field_range else (
          "с полевыми шт./км² (среднее по маршруту) не сравнивать напрямую: другой масштаб (внутри нити против среднего по "
          "маршруту)")
    scen = scenario_text(cfg, cal)
    nat = natural_pair_note(cfg)
    return {
        "value": v, "lo": lo, "hi": hi, "unit": unit, "unit_id": "items/km2",
        # §36 п.2: 470–670 items per pixel is a research SCENARIO on artificial targets, not a CI, not checked on nature
        "scenario": scen, "kind": "scenario",
        "natural_pair_note": nat,
        "stat": stat,
        # §39 п.3: the card says «Количество … не определено»; the PLP scenario is a separate, collapsed block.
        # The number (n × lo / area, 2 significant digits) is unchanged; the key `lower_bound` is kept for compatibility.
        "quantity_line": cfg.get("quantity_line"),
        "scenario_title": cfg.get("scenario_title"), "scenario_line": scen_line,
        "scenario_status": cfg.get("scenario_status"), "scenario_status_id": "research_scenario", "collapsed": True,
        "scenario_value": lb,
        "lower_bound": lb, "lower_bound_label": f"≈ {fmt(lb)} {unit} ({bound_note})",
        "display_value": lb, "label_short": f"≈ {fmt(lb)} {unit} · {cfg.get('scenario_title') or 'сценарий'}",
        "n_items_display": n_lb,
        "calibration_spread": {"lo": lo, "hi": hi, "items_per_pixel_lo": cal["lo"], "items_per_pixel_hi": cal["hi"],
                               "label": f"сценарий {cal['lo']}–{cal['hi']} предметов-бутылок на пиксель: разброс "
                                        f"{plural(cal['n_points'], 'точки', 'точек', 'точек')} калибровки на мишенях PLP "
                                        f"({fmt(lo)}–{fmt(hi)} {unit}), не доверительный интервал"},
        "interval_kind": "calibration_spread", "ci": None,
        "status": cfg.get("status", "исследовательская оценка"), "status_id": cfg.get("status_id", "research_estimate"),
        "method": cfg.get("method"), "note": cfg.get("note"),
        # jury 14:06: «(i)» line straight from the API; the calibration in force (flat PLP unless another is accepted)
        "formula_short": f"пиксели маски × {cal['lo']}–{cal['hi']} / площадь контура = пересчёт доли покрытия",
        "calibration_id": cfg.get("calibration_id", "flat_plp"),
        "calibration_name": cfg.get("calibration_name"),
        "method_essence": (f"по сути доля покрытия пикселей детектора × калибровка PLP; независимая проверка — "
                           f"{npx_d}" + (f"; {firing_caveat(cfg)}" if firing_caveat(cfg) else "")),
        "firing_caveat": firing_caveat(cfg),
        "context": ("плотность внутри контура нити в пересчёте на бутылки PET 1,5 л — не среднее по маршруту; " + fr
                    + (f"; {nat}" if nat else "")),
        "muted": muted,
        "muted_reason": (None if not muted else "независимой разметки нет (не совпадает с разметкой Cózar 2024) — "
                                                "сценарий показывать приглушённо"),
        "label": scen_line or (f"≈ {fmt(lb)} {unit} ({bound_note}) · {short_method}, {cfg.get('note')}"),
        "n_items": {"value": sig(n_val, nd), "lo": n_lo, "hi": n_hi, "lower_bound": n_lb},
        "n_items_label": f"N ≈ {fmt(n_lb)} шт. в зоне (сценарий)",
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
        "formula": (f"C = n_пикселей × [{cal['lo']}; {cal['hi']}] / площадь_зоны (км²); показываемое число = "
                    f"n × {cal['lo']} / площадь (2 значащие цифры); value — {stat} (n × {cal['value']} / площадь), "
                    f"lo/hi — разброс калибровки ({nd} значащие цифры)"),
        "calibration": {"n_dates": cal["n_dates"], "dates": cal["dates"], "level": "A* (искусственные мишени)",
                        "source": cal["source"], "config": cal["config"]},
        "not_what": (f"lo–hi — диапазон {cal['n_points']} сработавших пикселей мишеней "
                     f"{', '.join(cal['campaigns']) or 'PLP'} (дат A*: {cal['n_dates']}), не доверительный интервал; "
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
           "scenario": scenario_text(cfg, cal), "kind": "scenario", "natural_pair_note": natural_pair_note(cfg),
           "quantity_line": cfg.get("quantity_line"), "scenario_title": cfg.get("scenario_title"),
           "scenario_status": cfg.get("scenario_status"),
           "calibration_id": cfg.get("calibration_id", "flat_plp"), "calibration_name": cfg.get("calibration_name"),
           "firing_caveat": firing_caveat(cfg), "firing": dict(cfg.get("firing") or {}) or None,
           "formula_short": f"пиксели маски × {cal['lo']}–{cal['hi']} / площадь контура = пересчёт доли покрытия",
           "coverage_pct_lo": cal["coverage_pct_lo"], "coverage_pct_hi": cal["coverage_pct_hi"],
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
    lbs = sorted(e["lower_bound"] for _, e in ok)
    lbm = lbs[len(lbs) // 2] if len(lbs) % 2 else sig((lbs[len(lbs) // 2 - 1] + lbs[len(lbs) // 2]) / 2, 2)
    out.update({"lower_bound_median": lbm, "lower_bound_min": lbs[0], "lower_bound_max": lbs[-1],
                "n_muted": sum(1 for _, e in ok if e["muted"]), "n_not_muted": sum(1 for _, e in ok if not e["muted"]),
                # §39: confirmation instead of «требует проверки»; the scenario number (unchanged) under its own name
                "n_confirmed_cozar_b": sum(1 for _, e in ok if not e["muted"]),
                "n_no_independent_labels": sum(1 for _, e in ok if e["muted"]),
                "scenario_value_median": lbm, "scenario_value_min": lbs[0], "scenario_value_max": lbs[-1],
                "interval_kind": "calibration_spread"})
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
                          "value": e["value"], "lo": e["lo"], "hi": e["hi"], "lower_bound": e["lower_bound"]}
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
