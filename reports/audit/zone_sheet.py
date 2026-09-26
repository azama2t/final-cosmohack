"""Audit: contact sheets of scene-zone crops by status (from the auditor's service) for visual review."""
import io, json, sys, urllib.request
from pathlib import Path
from PIL import Image, ImageDraw
ROOT = Path(__file__).resolve().parents[2]
B = "http://127.0.0.1:8096"
d = json.load(open(ROOT / "reports/audit/scene_zones.json", encoding="utf-8"))
sel = sys.argv[1]  # e.g. live_detected | demo | live_insufficient
fs = [f["properties"] for f in d["features"]]
if sel == "live_detected":
    fs = [p for p in fs if p["scene_kind"] != "demo" and p["status"] == "detected"]
elif sel == "demo":
    fs = [p for p in fs if p["scene_kind"] == "demo"]
S, cols = 200, 10
rows = (len(fs) + cols - 1) // cols
sheet = Image.new("RGB", (cols * S, rows * (S + 14)), "black")
dr = ImageDraw.Draw(sheet)
idx = []
for i, p in enumerate(fs):
    url = f"{B}/api/v3/scene_zones/scenes/{p['scene_key']}/{p['crop_file']}"
    try:
        im = Image.open(io.BytesIO(urllib.request.urlopen(url, timeout=30).read())).convert("RGB").resize((S, S))
    except Exception as e:
        im = Image.new("RGB", (S, S), "gray")
    x, y = (i % cols) * S, (i // cols) * (S + 14)
    sheet.paste(im, (x, y))
    dr.text((x + 2, y + S), f"{i} {p['zone_id'][3:][-22:]} {p['measured']['n_pixels']}px", fill="white")
    idx.append({"i": i, "zone_id": p["zone_id"], "px": p["measured"]["n_pixels"], "cozar": p.get("n_cozar_filaments")})
sheet.save(ROOT / f"reports/audit/zones_{sel}.jpg", quality=85)
json.dump(idx, open(ROOT / f"reports/audit/zones_{sel}.json", "w"), indent=0)
print(len(fs), sheet.size)
