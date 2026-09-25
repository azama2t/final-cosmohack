"""Summary of OSM context, objects touched by the demo drift cloud, repeated finds -> reports/context.md (+ .json).

Team decision: no hours/percentages for drift, no "source" label, no linking to river mouths.

    .venv/Scripts/python.exe scripts/context_report.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service import core, routes_context as rc  # noqa: E402

KINDS = ["aquaculture", "beach", "port", "marina", "protected_area", "river_mouth", "outfall", "wastewater_plant"]
SHORT = {"aquaculture": "ферм", "beach": "пляж", "port": "порт", "marina": "марин", "protected_area": "охр",
         "river_mouth": "устье", "outfall": "выпуск", "wastewater_plant": "очистн"}


def main():
    st = core.Store(ROOT / "service" / "data", "arg")
    cdir = ROOT / "service" / "context"
    size = sum(p.stat().st_size for p in cdir.glob("*.geojson"))
    L = ["# Контекст L47: объекты OSM, пересечение демо-дрейфа с объектами, повторяемость находок", "",
         "Источник объектов: © OpenStreetMap contributors (ODbL), Overpass API; bbox района ± 10 км. "
         f"Файлы `service/context/<район>.geojson`, всего {size / 1024:.0f} КБ (≤ 2 МБ — хранятся в git).", "",
         "Решение команды (INBOX 13:20): без численных предупреждений (часы, проценты) и без событий в ленте; "
         "метка «постоянный источник» не выдаётся, находки не связываются с устьями/выпусками.", "",
         "## Объекты по видам", "",
         "| район | " + " | ".join(SHORT[k] for k in KINDS) + " | всего |", "|---" * (len(KINDS) + 2) + "|"]
    summary = {"regions": {}, "context_bytes": size}
    tot = {k: 0 for k in KINDS}
    for reg in st.regions():
        rid = reg["id"]
        try:
            fc = rc.load_context(rid, cdir)
        except core.NotFound:
            L.append(f"| {rid} | " + " | ".join("—" for _ in KINDS) + " | нет файла |")
            continue
        cnt = {k: 0 for k in KINDS}
        for f in fc["features"]:
            cnt[f["properties"]["kind"]] = cnt.get(f["properties"]["kind"], 0) + 1
        for k in KINDS:
            tot[k] += cnt[k]
        summary["regions"][rid] = {"objects": cnt}
        L.append(f"| {rid} | " + " | ".join(str(cnt[k]) for k in KINDS) + f" | {sum(cnt.values())} |")
    L.append("| **всего** | " + " | ".join(str(tot[k]) for k in KINDS) + f" | {sum(tot.values())} |")
    summary["objects_total"] = tot

    L += ["", "## Объекты, которые задевает облако частиц (демо-прогноз, не валидирован)", "",
          "Только для панели дрейфа (`/api/threats`), не предупреждение и не событие ленты. " + rc.DRIFT_RULE.format(
              b=rc.DEFAULT_BUFFER_M) + ".", "",
          "| район | дата | объектов | ферм / охр. / пляжей / марин / портов | примеры |", "|---|---|---|---|---|"]
    n_scenes = n_obj = n_hit = 0
    for reg in st.regions():
        rid = reg["id"]
        for d in st.region_dates(rid):
            if not d.get("drift"):
                continue
            try:
                t = rc.contacts(st, rid, d["date"], cdir=cdir)
            except core.NotFound:
                continue
            n_scenes += 1
            ob = t["objects"]
            n_obj += len(ob)
            n_hit += 1 if ob else 0
            by = {k: sum(1 for x in ob if x["kind"] == k) for k in rc.TOUCH_KINDS}
            named = [f"{x['kind_ru']} «{x['name']}»" for x in ob if x.get("name")][:3]
            summary["regions"].setdefault(rid, {}).setdefault("drift_objects", {})[d["date"]] = {"n": len(ob), **by}
            L.append(f"| {rid} | {d['date']} | {len(ob)} | {by['aquaculture']} / {by['protected_area']} / "
                     f"{by['beach']} / {by['marina']} / {by['port']} | {'; '.join(named) or '—'} |")
    L.append(f"\nСцен с дрейфом: {n_scenes}; из них задевают ≥ 1 объект: {n_hit}; всего пар «сцена–объект»: {n_obj}.")

    L += ["", "## Повторяющиеся находки (повторяемость, требует проверки)", "",
          "Внутренний эндпоинт `/api/repeats`. Правило: " + rc.REPEAT_RULE.format(n=2) + ".", "",
          "| район | ячеек (≥ 2 дат) | ≥ 3 дат | находки обеих моделей | только lgbm | только mdd |",
          "|---|---|---|---|---|---|"]
    all_c = 0
    for reg in st.regions():
        rid = reg["id"]
        cells = rc.repeats(st, rid)["cells"]
        all_c += len(cells)
        n3 = sum(1 for x in cells if x["n_dates"] >= 3)
        both = sum(1 for x in cells if len(x["models"]) >= 2)
        lg = sum(1 for x in cells if x["models"] == ["lgbm"])
        md = sum(1 for x in cells if x["models"] == ["mdd"])
        summary["regions"].setdefault(rid, {})["repeats"] = {"n": len(cells), "n_3dates": n3, "both_models": both,
                                                             "lgbm_only": lg, "mdd_only": md}
        L.append(f"| {rid} | {len(cells)} | {n3} | {both} | {lg} | {md} |")
    L.append(f"\nВсего ячеек с повторяющимися находками: {all_c}. Это повторяемость находок в ячейке, требует "
             "проверки; повторы одной модели (особенно lgbm) часто дают стационарные объекты (причалы, суда у "
             "порта), а не мусор.")
    summary["n_repeat_cells"] = all_c
    summary["n_drift_scenes"] = n_scenes
    summary["n_drift_scenes_touching"] = n_hit
    summary["n_drift_objects"] = n_obj
    (ROOT / "reports" / "context.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    (ROOT / "reports" / "context.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"objects {sum(tot.values())}; drift scenes {n_scenes} (touching {n_hit}), pairs {n_obj}; "
          f"repeat cells {all_c}; context {size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
