r"""§54 п.1 (доп.): «свежий Sentinel-2» — последние дни для 1–2 районов, наш детектор (CPU), ОТДЕЛЬНЫЙ набор.

Не смешивается с основными зонами кейса (data/case/scene_zones): сцены лежат в out/fresh_s2/live/<region>/<date>/
(не в data/live — их не подхватит scripts/case/scene_zones.py), зоны — в data/case/fresh_s2/<key>/, индекс —
data/case/fresh_s2/index.json. Подпись набора: «автоматически, не проверено человеком».

  1) снимки (STAC, Earth Search -> пиксели Planetary Computer), по одному на дату:
     .venv/Scripts/python.exe scripts/fetch_live.py --region tiber --date 2026-09-26 --out out/fresh_s2/live
  2) детектор + зоны (тот же режим и те же правила, что у основного слоя: weights/lgbm, без гармонизации, порог 0.63):
     $env:CUDA_VISIBLE_DEVICES=""; .venv/Scripts/python.exe scripts/case/fresh_s2.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "case"))

import pandas as pd  # noqa: E402
import yaml  # noqa: E402

import scene_zones as sz  # noqa: E402
import studio_detector_current as sdc  # noqa: E402

LIVE = ROOT / "out" / "fresh_s2" / "live"
DET = ROOT / "out" / "fresh_s2" / "det"
OUT = ROOT / "data" / "case" / "fresh_s2"
LABEL = "автоматически, не проверено человеком"
NOTE = ("свежий снимок Sentinel-2 L2A за последние дни, обработан нашим детектором автоматически; зоны не проверены "
        "человеком и не входят в основные зоны кейса и в его числа; класс детектора — любой плавающий материал, "
        "не только пластик; количества (шт., масса) нет")


def main():
    from macroplastic.models.lgbm_predict import load_predictor
    cfg = yaml.safe_load((ROOT / "configs" / "case_pairs.yaml").read_text(encoding="utf-8"))
    assert cfg["detector"]["harmonize"] == "none" and cfg["detector"]["weights"] == "weights/lgbm"
    pred = load_predictor(ROOT / cfg["detector"]["weights"], harmonize="none", device="cpu", num_threads=6)
    cozar = pd.read_csv(ROOT / "reports" / "extra_data" / "registry_cozar2024.csv.gz",
                        usecols=["fil_idx", "tile", "date", "bbox_wkt", "n_pixels_fil"])
    minfo = sz.model_info()
    sz.OUT = OUT  # build_scene writes to sz.OUT / key
    OUT.mkdir(parents=True, exist_ok=True)
    infos = []
    for sdir in sorted(p.parent for p in LIVE.glob("*/*/bands.tif")):
        r, d = sdir.parent.name, sdir.name
        ddir = DET / r / d
        if not (ddir / "det.json").is_file():
            di = sdc.run_scene(sdir, pred, cfg, ddir)
            print(f"det {r}/{d}: {di['pixels']} px, {di['objects']} obj", flush=True)
        s = {"key": f"fresh-{r}-{d}", "kind": "fresh_s2", "sdir": sdir, "ddir": ddir, "region": r, "date": d}
        try:
            i = sz.build_scene(s, cozar, minfo)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            i = {"key": s["key"], "region": r, "date": d, "error": repr(e)}
        i.update({"auto_label": LABEL, "human_checked": False, "source": "Sentinel-2 L2A (STAC)"})
        print(f"{s['key']}: evaluable={i.get('evaluable')} zones={i.get('n_zones_total')} {i.get('by_status')}", flush=True)
        infos.append(i)
    idx = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "model": minfo,
           "label": LABEL, "note": NOTE, "human_checked": False, "separate_from": "data/case/scene_zones (основные зоны)",
           "scenes": sorted(infos, key=lambda i: i["key"])}
    (OUT / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", OUT / "index.json")


if __name__ == "__main__":
    main()
