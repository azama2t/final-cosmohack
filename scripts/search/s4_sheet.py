"""S4: contact sheet of same-day pairs (RGB | FDI | SWIR false colour | detector mask) -> reports/search/s4_pairs.jpg.
  PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/search/s4_sheet.py
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
S = ROOT / "data/search/s4/crops"
OUT = ROOT / "reports/search/s4_pairs.jpg"
TILE = 300
VIEWS = ["rgb.png", "fdi.png", "swir.png", "mask.png"]


def main():
    metas = sorted(S.glob("*/*/meta.json"), key=lambda p: (int(p.parent.parent.name.split("_T")[-1]), p.parent.name))
    rows = []
    for m in metas:
        js = json.loads(m.read_text(encoding="utf-8"))
        q, d = js.get("quality") or {}, js.get("detector") or {}
        label = (f"{js['transect']} {js.get('field_items_km2')} items/km2 | {js.get('scene_id')} {str(js.get('scene_datetime'))[:16]} | "
                 f"dt est {js.get('dt_est_h')} h (min {js.get('dt_min_abs_h')}, max {js.get('dt_max_abs_h')}) | "
                 f"{js.get('decision')} {js.get('reason') or ''} water {q.get('valid_water_frac')} | det strip {d.get('n_det')} crop {d.get('n_det_crop')}")
        rows.append((m.parent, label))
    W, H = TILE * len(VIEWS), (TILE + 22) * len(rows) + 24
    sheet = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(sheet)
    dr.text((4, 4), "S4 DOORS-3 same-day pairs: RGB | FDI (red: FDI>0.01) | SWIR false colour B12-B8A-B4 | LightGBM mask (red), strip = magenta",
            fill="black")
    y = 24
    for d, label in rows:
        dr.text((4, y + 4), label[:190], fill="black")
        for i, v in enumerate(VIEWS):
            p = d / v
            if p.exists():
                im = Image.open(p).convert("RGB")
                im.thumbnail((TILE, TILE))
                sheet.paste(im, (i * TILE, y + 22))
        y += TILE + 22
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(OUT, quality=85)
    print(OUT, len(rows))


if __name__ == "__main__":
    main()
