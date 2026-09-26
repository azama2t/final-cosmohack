"""Пропуск скопления для примеров защиты (MATVEY_ACCEPTANCE п.4): нить каталога Cózar 2024 (уровень B) на отложенной
сцене 30SXE 11.03.2021, большую часть которой детектор не отметил.

Правило выбора (зафиксировано до просмотра картинок): среди нитей каталога в вырезке сцены с n_pixels_fil >= 100
берётся нить с наименьшей долей det_px_in_bbox / n_pixels_fil. det_px_in_bbox считается в прямоугольнике нити
(bbox_wkt каталога), поэтому это верхняя оценка отмеченного на нити: пропущено НЕ МЕНЕЕ n_pixels_fil - det_px_in_bbox.

Входы: reports/extra_data/registry_cozar2024.csv.gz, data/case/demo/cozar_demo_2021-03-11/{det,prob,quality}.tif,
data/live/cozar_demo/2021-03-11/bands.tif (для вырезки). Выход: data/case/scene_zones/defense_examples.json +
data/case/scene_zones/demo-cozar-2021-03-11/crops/SZ-demo-cozar-2021-03-11-miss-<fil_idx>.jpg.

    .venv/Scripts/python.exe scripts/case/defense_examples.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.features import geometry_mask
from rasterio.warp import transform_geom
from shapely import wkt
from shapely.geometry import box, mapping

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "case"))
from scene_zones import write_crop  # noqa: E402  (тот же вид вырезки, что у зон)

KEY, DATE, TILE = "demo-cozar-2021-03-11", "2021-03-11", "30SXE"
DD = ROOT / "data" / "case" / "demo" / f"cozar_demo_{DATE}"
SD = ROOT / "data" / "live" / "cozar_demo" / DATE
OD = ROOT / "data" / "case" / "scene_zones" / KEY
OUT = ROOT / "data" / "case" / "scene_zones" / "defense_examples.json"
MIN_FIL_PX = 100


def main() -> None:
    sj = json.loads((OD / "scene.json").read_text(encoding="utf-8"))
    frame = box(*sj["bounds"])
    cz = pd.read_csv(ROOT / "reports" / "extra_data" / "registry_cozar2024.csv.gz",
                     usecols=["fil_idx", "tile", "date", "bbox_wkt", "n_pixels_fil", "level", "scene_id"])
    cz = cz[(cz.tile == TILE) & (cz.date == DATE)]
    with rasterio.open(DD / "det.tif") as d:
        det, tr, crs, shp = d.read(1) > 0, d.transform, d.crs, d.shape
    with rasterio.open(DD / "prob.tif") as p:
        prob = p.read(1).astype(np.float32) / 255.0
    with rasterio.open(DD / "quality.tif") as q:
        qa = q.read(1)
    rows, masks = [], {}
    for r in cz.itertuples():
        g = wkt.loads(r.bbox_wkt)
        if not g.intersects(frame):
            continue
        m = ~geometry_mask([transform_geom("EPSG:4326", crs, mapping(g))], shp, tr, all_touched=True)
        masks[int(r.fil_idx)] = m
        n_det = int(det[m].sum())
        rows.append({"fil_idx": int(r.fil_idx), "level": r.level, "n_pixels_fil": int(r.n_pixels_fil),
                     "bbox_px": int(m.sum()), "water_px_in_bbox": int((qa[m] == 1).sum()),
                     "det_px_in_bbox": n_det, "prob_max_in_bbox": round(float(prob[m].max()), 3),
                     "det_share_upper": round(n_det / max(int(r.n_pixels_fil), 1), 3),
                     "bbox_wgs84": [round(x, 5) for x in g.bounds]})
    cand = [r for r in rows if r["n_pixels_fil"] >= MIN_FIL_PX]
    pick = min(cand, key=lambda r: (r["det_share_upper"], r["fil_idx"]))
    zid = f"SZ-{KEY}-miss-{pick['fil_idx']}"
    with rasterio.open(SD / "bands.tif") as ds:
        names = list(ds.descriptions)
        B = {b: ds.read(names.index(b) + 1).astype(np.float32) for b in ("B4", "B3", "B2")}
    wm = qa == 1
    hi = max(max(np.nanpercentile(B[b][wm], 98) for b in B), 0.02)
    rgb8 = (np.stack([np.clip(np.nan_to_num(B[b]) / hi, 0, 1) ** (1 / 1.4) for b in ("B4", "B3", "B2")]) * 255
            ).astype(np.uint8)
    lab = np.zeros(det.shape, np.int32)
    crop = write_crop(OD, zid, masks[pick["fil_idx"]], det, rgb8, lab, np.zeros(1, object))
    out = {"generated_by": "scripts/case/defense_examples.py",
           "rule": f"нити каталога Cózar 2024 в вырезке сцены {KEY} с n_pixels_fil >= {MIN_FIL_PX}; "
                   "берётся наименьшая доля det_px_in_bbox / n_pixels_fil (det в прямоугольнике нити — верхняя оценка)",
           "scene_key": KEY, "scene_id": sj["scene_id"], "n_filaments_acquisition": int(len(cz)),
           "n_filaments_in_frame": len(rows), "filaments": sorted(rows, key=lambda r: r["fil_idx"]),
           "miss": {**pick, "zone_like_id": zid, "crop_file": crop,
                    "missed_px_min": pick["n_pixels_fil"] - pick["det_px_in_bbox"]}}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"filaments in frame {len(rows)}; miss fil {pick['fil_idx']}: {pick['n_pixels_fil']} px, "
          f"det in bbox {pick['det_px_in_bbox']} -> {OUT.relative_to(ROOT)}, {crop}")


if __name__ == "__main__":
    main()
