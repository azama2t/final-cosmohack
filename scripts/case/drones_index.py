"""§54 п.2 (L151): prebuilt index of detailed frames for the «Дроны» group -> data/case/drones/index.json
+ previews data/case/drones/img/<set>/<frame>.jpg (<= 200 KB each, <= 30 MB total; data/case is in git,
service/data is git-ignored, so previews live here).

Only sets from docs/COUNT_DATASETS.md whose files are on disk (data/extra/count_ds/...); no set is invented
(a beach set from India is NOT in the catalogue -> not included). Labels are taken as-is from the set:
 - classes are mapped to groups plastic / algae / wood / other ONLY from the set's own class names;
 - items per frame = number of labelled boxes/polygons (Martin 2021: no boxes -> null);
 - шт./м², шт./км² ONLY when the frame area is known (GSD / published frame area), else "площадь кадра неизвестна";
 - our counter's prediction ONLY for sets where its metrics were checked on held-out frames
   (FML: MAE 0.59 шт./кадр, Winans: 1.85 шт./кадр — reports/final_numbers.json photo_count), and only on the
   held-out TEST frames of those checks.

Run: CUDA_VISIBLE_DEVICES=-1 .venv/Scripts/python.exe scripts/case/drones_index.py [--no-predict]
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
CDS = ROOT / "data" / "extra" / "count_ds"
OUT = ROOT / "data" / "case" / "drones"
IMG = OUT / "img"
MAX_SIDE = 1024
MAX_BYTES = 200 * 1024
PER_SET = 16

GROUPS = {"plastic": "пластик", "algae": "водоросли", "wood": "дерево", "other": "прочее"}


def _r(x, k=4):
    return round(float(x), k)


def save_preview(src: Path | Image.Image, set_id: str, fid: str):
    im = src if isinstance(src, Image.Image) else Image.open(src)
    im = im.convert("RGB")
    w0, h0 = im.size
    s = min(1.0, MAX_SIDE / max(w0, h0))
    if s < 1:
        im = im.resize((round(w0 * s), round(h0 * s)), Image.LANCZOS)
    (IMG / set_id).mkdir(parents=True, exist_ok=True)
    for q in (82, 75, 68, 60, 52, 45):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        if buf.tell() <= MAX_BYTES:
            break
    (IMG / set_id / f"{fid}.jpg").write_bytes(buf.getvalue())
    return (w0, h0), im.size, buf.tell()


def pick(items, n=PER_SET):
    """deterministic, spread over the sorted list, frames with >= 1 labelled object first"""
    items = sorted(items, key=lambda t: t[0])
    if len(items) <= n:
        return items
    idx = np.linspace(0, len(items) - 1, n).round().astype(int)
    return [items[i] for i in sorted(set(idx))]


def density(n, area_m2):
    if n is None or not area_m2:
        return None, None
    return _r(n / area_m2, 4), round(n / area_m2 * 1e6)


def frame_rec(set_id, fid, src_name, orig, prev, nbytes, objects, area_m2, area_note=None, extra=None):
    n = len(objects) if objects is not None else None
    by = {}
    for o in objects or []:
        by[o["group"]] = by.get(o["group"], 0) + 1
    d_m2, d_km2 = density(n, area_m2)
    rec = {"id": fid, "image": f"/api/v3/drones/{set_id}/img/{fid}.jpg", "source_file": src_name,
           "width": prev[0], "height": prev[1], "orig_width": orig[0], "orig_height": orig[1], "bytes": nbytes,
           "objects": objects, "n_objects": n, "n_by_group": by if objects is not None else None,
           "frame_area_m2": area_m2, "density_m2": d_m2, "density_km2": d_km2,
           "area_note": area_note or (None if area_m2 else "площадь кадра неизвестна")}
    if extra:
        rec.update(extra)
    return rec


def yolo_objects(lab: Path, names, gmap):
    out = []
    if not lab.exists():
        return out
    for line in lab.read_text().split("\n"):
        p = line.split()
        if len(p) < 5:
            continue
        c = int(float(p[0]))
        name = names[c] if c < len(names) else str(c)
        v = [float(x) for x in p[1:]]
        o = {"cls": name, "group": gmap.get(name, "other")}
        if len(v) == 4:
            cx, cy, w, h = v
            o["bbox"] = [_r(cx - w / 2), _r(cy - h / 2), _r(w), _r(h)]
        else:
            pts = np.array(v[: len(v) // 2 * 2]).reshape(-1, 2)
            if len(pts) > 40:
                pts = pts[np.linspace(0, len(pts) - 1, 40).round().astype(int)]
            o["poly"] = [[_r(x), _r(y)] for x, y in pts]
            x0, y0 = pts.min(0)
            x1, y1 = pts.max(0)
            o["bbox"] = [_r(x0), _r(y0), _r(x1 - x0), _r(y1 - y0)]
        out.append(o)
    return out


def px_objects(boxes, labels, w, h, gmap):
    out = []
    for (x0, y0, x1, y1), name in zip(boxes, labels):
        out.append({"cls": name, "group": gmap.get(name, "other"),
                    "bbox": [_r(x0 / w), _r(y0 / h), _r((x1 - x0) / w), _r((y1 - y0) / h)]})
    return out


# ------------------------------------------------------------------------------------------------ sets
def set_ucwd():
    base = CDS / "ucwd" / "UCWD.coco"
    names = ["Glass", "Metal", "Packet", "Plastic bag", "Plastic bottle", "Plastic container", "Plastic fragment"]
    gmap = {"Plastic bag": "plastic", "Plastic bottle": "plastic", "Plastic container": "plastic",
            "Plastic fragment": "plastic", "Glass": "other", "Metal": "other", "Packet": "other"}
    items = []
    for sp in ("train", "valid", "test"):
        d = json.loads((base / sp / "_annotations.coco.json").read_text(encoding="utf8"))
        cats = {c["id"]: c["name"] for c in d["categories"]}
        anns = {}
        for a in d["annotations"]:
            anns.setdefault(a["image_id"], []).append(a)
        for im in d["images"]:
            p = base / sp / im["file_name"]
            if p.exists() and anns.get(im["id"]):
                items.append((f"{sp}/{im['file_name']}", p, im, [(cats[a["category_id"]], a["bbox"]) for a in anns[im["id"]]]))
    frames = []
    for k, (key, p, im, anns) in enumerate(pick(items)):
        fid = f"ucwd_{k:02d}"
        orig, prev, nb = save_preview(p, "ucwd", fid)
        W, H = im["width"], im["height"]
        objs = [{"cls": c, "group": gmap.get(c, "other"), "bbox": [_r(x / W), _r(y / H), _r(w / W), _r(h / H)]}
                for c, (x, y, w, h) in anns]
        frames.append(frame_rec("ucwd", fid, im.get("extra", {}).get("name", im["file_name"]), orig, prev, nb, objs, None,
                                "площадь кадра неизвестна (GSD нет: EXIF удалён)"))
    meta = {"id": "ucwd", "name": "UCWD — мусор с дрона (пляжи и другие поверхности)", "sensor": "drone", "sensor_label": "дрон",
            "view": "надир", "region": "место съёмки в наборе не указано (пляжи, газоны, дороги)", "center": None,
            "license": "CC-BY-4.0", "link": "https://doi.org/10.5281/zenodo.20630252",
            "catalog_row": "B1", "dataset_size": "4 071 кадр / 26 258 рамок (в наборе)",
            "classes_src": names, "class_groups": gmap, "gsd_m": None, "frame_area_m2": None,
            "note": "Кадров на диске в выборке 21 (образец набора); показаны кадры с разметкой."}
    return meta, frames


def set_tun():
    base = CDS / "tun_marinelitter" / "Data"
    names = ["Cardboar", "Fabrics", "Glass", "Metal", "Other", "Plastic", "Wood"]
    gmap = {"Plastic": "plastic", "Wood": "wood"}
    items = []
    for sp in ("valid", "test"):
        for p in (base / sp / "images").glob("*.jpg"):
            lab = base / sp / "labels" / (p.stem + ".txt")
            objs = yolo_objects(lab, names, gmap)
            if objs:
                items.append((f"{sp}/{p.name}", p, objs))
    # prefer frames with more than one class so the legend is informative
    items.sort(key=lambda t: t[0])
    rich = [t for t in items if len({o["group"] for o in t[2]}) >= 2]
    chosen = pick(rich if len(rich) >= PER_SET else items)
    frames = []
    for k, (key, p, objs) in enumerate(chosen):
        fid = f"tun_{k:02d}"
        orig, prev, nb = save_preview(p, "tun_marinelitter", fid)
        frames.append(frame_rec("tun_marinelitter", fid, p.name, orig, prev, nb, objs, None,
                                "площадь кадра неизвестна (GSD в наборе не указан)"))
    meta = {"id": "tun_marinelitter", "name": "TUN-MarineLitter — пляжи Туниса", "sensor": "drone",
            "sensor_label": "дрон", "view": "надир, плитки кадра", "region": "Тунис, пляжи", "center": None,
            "license": "CC-BY-4.0", "link": "https://doi.org/10.5281/zenodo.21965497", "catalog_row": "B9",
            "dataset_size": "3 676 изображений / 7 922 полигона (в наборе)", "classes_src": names,
            "class_groups": {n: gmap.get(n, "other") for n in names}, "gsd_m": None, "frame_area_m2": None,
            "note": "Разметка — полигоны (контуры предметов). «Cardboar» — так в наборе (картон)."}
    return meta, frames


def _xlsx_rows(path, sheet):
    z = zipfile.ZipFile(path)
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    ss = ["".join(t.text or "" for t in si.iter("{%s}t" % ns["m"]))
          for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns)]
    rows = []
    for r in ET.fromstring(z.read(f"xl/worksheets/{sheet}.xml")).findall(".//m:row", ns):
        row = {}
        for c in r.findall("m:c", ns):
            v = c.find("m:v", ns)
            if v is None:
                continue
            val = ss[int(v.text)] if c.get("t") == "s" else v.text
            row[re.match(r"[A-Z]+", c.get("r")).group(0)] = val
        rows.append(row)
    return rows


def set_martin():
    base = CDS / "martin_redsea_uav"
    rows = _xlsx_rows(base / "martin2021_dib_mmc1.xlsx", "sheet1")
    st = {}
    for r in rows[2:]:
        try:
            sid = int(float(r["A"]))
        except (KeyError, ValueError):
            continue
        st[sid] = {"lat": float(r["B"]), "lon": float(r["C"]),
                   "date": (dt.date(1899, 12, 30) + dt.timedelta(days=int(float(r["D"])))).isoformat(),
                   "drone": r.get("E"), "density_m2": float(r["O"]) if r.get("O") else None,
                   "beach_area_m2": float(r["F"]) if r.get("F") else None, "n_images": int(float(r["G"])) if r.get("G") else None}
    AREA = 138.6
    frames = []
    for k, p in enumerate(sorted((base / "images").glob("*.JPG"))):
        sid = int(re.match(r"st(\d+)_", p.name).group(1))
        s = st.get(sid, {})
        fid = f"martin_{k:02d}"
        orig, prev, nb = save_preview(p, "martin2021", fid)
        dm2 = s.get("density_m2")
        extra = {"station": sid, "lat": s.get("lat"), "lon": s.get("lon"), "date": s.get("date"),
                 "published": None if dm2 is None else {
                     "density_m2": _r(dm2, 4), "density_km2": round(dm2 * 1e6),
                     "scope": f"пляж, станция {sid} (опубликовано: Martin et al. 2021, Data in Brief, табл. mmc1)",
                     "per_frame_expected": _r(dm2 * AREA, 1),
                     "per_frame_note": f"ожидание на кадр {AREA} м² по плотности пляжа, не счёт на этом кадре"}}
        frames.append(frame_rec("martin2021", fid, p.name, orig, prev, nb, None, AREA,
                                "рамок в наборе нет: число на кадре не размечено; плотность — опубликованная по пляжу",
                                extra))
    lats = [f["lat"] for f in frames if f.get("lat")]
    lons = [f["lon"] for f in frames if f.get("lon")]
    meta = {"id": "martin2021", "name": "Martin et al. 2021 — пляжи Красного моря", "sensor": "drone",
            "sensor_label": "дрон", "view": "надир, 10 м", "region": "Саудовская Аравия, побережье Красного моря",
            "center": [round(float(np.mean(lons)), 4), round(float(np.mean(lats)), 4)] if lats else None,
            "license": "CC-BY-4.0 (кадры)", "link": "https://data.mendeley.com/datasets/gpdsntb3y6/1",
            "catalog_row": "A3", "dataset_size": "1 287 кадров, 44 пляжа (в наборе); на диске 5 кадров (5 пляжей)",
            "classes_src": [], "class_groups": {}, "gsd_m": None, "frame_area_m2": AREA,
            "note": "Рамок нет — классы и число предметов на кадре не размечены; показана опубликованная плотность "
                    "пляжа (шт./м²) и площадь кадра 138,6 м²."}
    return meta, frames


def set_maharjan():
    base = CDS / "maharjan_river_uav" / "Nisha-main" / "Datagithub"
    gmap = {"plastic": "plastic"}
    frames = []
    for sub, reg, tag in (("train_data_v5_Laos", "Лаос", "laos"), ("train_data_v5_talathai", "Таиланд", "thai")):
        items = []
        for p in sorted((base / sub / "images").glob("*.png")):
            lab = base / sub / "labels" / (p.stem + ".txt")
            objs = yolo_objects(lab, ["plastic"], gmap)
            if len(objs) >= 2:
                try:
                    with Image.open(p) as im:
                        im.load()
                except Exception:  # noqa: BLE001 - truncated tiles are skipped
                    continue
                items.append((p.name, p, objs))
        for k, (key, p, objs) in enumerate(pick(items, PER_SET // 2)):
            fid = f"maharjan_{tag}_{k:02d}"
            orig, prev, nb = save_preview(p, "maharjan2022", fid)
            frames.append(frame_rec("maharjan2022", fid, f"{sub}/{p.name}", orig, prev, nb, objs, 4.0, None,
                                    {"subregion": reg}))
    meta = {"id": "maharjan2022", "name": "Maharjan 2022 — реки Лаоса и Таиланда", "sensor": "drone",
            "sensor_label": "дрон", "view": "надир над водой", "region": "Лаос и Таиланд, реки", "center": None,
            "license": "файла лицензии нет (открытость — по статье Remote Sens. 14, 3049)",
            "link": "https://github.com/Nisha484/Nisha", "catalog_row": "A4",
            "dataset_size": "1 000 плиток / 2 094 рамки (в наборе)", "classes_src": ["plastic"],
            "class_groups": gmap, "gsd_m": 0.0082, "frame_area_m2": 4.0,
            "note": "Плитка 2 × 2 м (GSD 0,82 см) — шт./м² считаются на площади плитки. Наш счётчик на этом наборе "
                    "не переносится (AP50 0,01 без дообучения) — его прогноз не показан."}
    return meta, frames


def set_winans(predict):
    from macroplastic.photo_count import winans as W
    ch = W.load_all()
    sp, _ = W.spatial_split(ch)
    names = sorted(n for n in ch if sp[n] == "test" and len(ch[n]["boxes"]) >= 3)
    gmap = {"processed wood": "wood"}
    chosen = pick([(n,) for n in names])
    model = card = None
    if predict:
        from macroplastic.photo_count.model import load_card, load_model
        card = load_card("aerial")
        model = load_model(Path(card["_dir"]) / card["weights_file"], "cpu") if card else None
    frames = []
    lonlat = []
    try:
        from pyproj import Transformer
        trs = {z: Transformer.from_crs(f"EPSG:269{z:02d}", "EPSG:4326", always_xy=True) for z in (4, 5)}
    except Exception:  # noqa: BLE001
        trs = None
    for k, (n,) in enumerate(chosen):
        c = ch[n]
        fid = f"winans_{k:02d}"
        orig, prev, nb = save_preview(c["path"], "winans2023", fid)
        objs = px_objects(c["boxes"], c["labels"], *orig, gmap)
        extra = {}
        zm = re.search(r"UTM zone (\d+)N", Path(str(c["path"]) + ".aux.xml").read_text()) if trs else None
        tr = trs.get(int(zm.group(1))) if zm else None  # chips are in NAD83 UTM 4N or 5N (per-chip .aux.xml)
        if tr:
            x0, y0, x1, y1 = c["fp"]
            lon, lat = tr.transform((x0 + x1) / 2, (y0 + y1) / 2)
            extra.update(lat=round(lat, 5), lon=round(lon, 5))
            lonlat.append((lon, lat))
        if model is not None:
            from macroplastic.photo_count.model import predict_images
            with Image.open(c["path"]) as im:
                b, s = predict_images(model, [im.convert("RGB")], "cpu", 1)[0]
            thr = float(card["threshold"])
            b = b[s >= thr]
            extra["prediction"] = pred_rec("aerial", card, b, orig, 163.84, len(objs))
        frames.append(frame_rec("winans2023", fid, n, orig, prev, nb, objs, 163.84, None, extra))
    center = [round(float(np.mean([a for a, _ in lonlat])), 4), round(float(np.mean([b for _, b in lonlat])), 4)] if lonlat else None
    cls = ["buoy", "line fragment", "metal", "net cloth", "processed wood", "tire", "unidentified object", "vessel"]
    meta = {"id": "winans2023", "name": "Winans et al. 2023 — берег Гавайев (аэросъёмка)", "sensor": "aircraft",
            "sensor_label": "самолёт (пилотируемая аэросъёмка), не дрон", "view": "надир, GSD 2 см",
            "region": "Гавайи, берег", "center": center, "license": "CC-BY-4.0",
            "link": "https://doi.org/10.5281/zenodo.8381113", "catalog_row": "A1",
            "dataset_size": "1 588 кадров / 10 703 рамки (в наборе)", "classes_src": cls,
            "class_groups": {c: gmap.get(c, "other") for c in cls}, "gsd_m": 0.02, "frame_area_m2": 163.84,
            "note": "Кадры — из отложенного (test) пространственного разбиения, на котором проверен наш счётчик: "
                    "ошибка 1,85 шт./кадр. Класса «пластик» в разметке нет (буи, сети, тросы — материал не указан) "
                    "→ «прочее»."}
    return meta, frames


def set_fml(predict):
    import importlib.util
    spec = importlib.util.spec_from_file_location("pc_train", ROOT / "scripts" / "photo_count" / "train.py")
    T = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(T)
    from macroplastic.photo_count import data as D
    test = T.items_for("grouped", "test")
    items = []
    for img_p, lab in test:
        objs = yolo_objects(Path(lab), ["floating litter"], {})
        if len(objs) >= 3:
            items.append((img_p.name, img_p, objs))
    chosen = pick(items, 12)
    model = card = None
    if predict:
        from macroplastic.photo_count.model import load_card, load_model
        card = load_card("water_camera")
        model = load_model(Path(card["_dir"]) / card["weights_file"], "cpu") if card else None
    frames = []
    for k, (key, p, objs) in enumerate(chosen):
        fid = f"fml_{k:02d}"
        orig, prev, nb = save_preview(p, "fml", fid)
        extra = {}
        ft = D.frame_time(p.name)
        if ft:
            extra["date"] = ft.isoformat(sep=" ")[:16]
        if model is not None:
            from macroplastic.photo_count.model import predict_images
            with Image.open(p) as im:
                b, s = predict_images(model, [im.convert("RGB")], "cpu", 1)[0]
            b = b[s >= float(card["threshold"])]
            extra["prediction"] = pred_rec("water_camera", card, b, orig, None, len(objs))
        frames.append(frame_rec("fml", fid, p.name, orig, prev, nb, objs, None,
                                "площадь кадра неизвестна (косой вид с судна, GSD/GPS нет) — только шт./кадр", extra))
    meta = {"id": "fml", "name": "FML — плавающий мусор с безэкипажного судна", "sensor": "vessel",
            "sensor_label": "судно (USV, камера у воды), не дрон", "view": "косой вид", "region": "место не указано в наборе",
            "center": None, "license": "CC-BY-4.0", "link": "https://doi.org/10.17882/106148", "catalog_row": "B2",
            "dataset_size": "5 490 кадров / 17 156 рамок (в наборе)", "classes_src": ["floating litter"],
            "class_groups": {"floating litter": "other"}, "gsd_m": None, "frame_area_m2": None,
            "note": "Кадры — из отложенных сессий съёмки (test), на которых проверен наш счётчик: ошибка 0,59 шт./кадр. "
                    "Материал в разметке не указан → «прочее»."}
    return meta, frames


def pred_rec(survey, card, boxes, orig, area_m2, n_true):
    W_, H_ = orig
    n = int(len(boxes))
    fn = json.loads((ROOT / "reports" / "final_numbers.json").read_text(encoding="utf8"))["case"]["sections"]["photo_count"]
    if survey == "aerial":
        metric = {"count_mae_per_frame": fn["area"]["count_mae"], "n_test_frames": fn["area"]["n_test_frames"],
                  "test": "Winans 2023, отложенные участки берега", "source": "reports/final_numbers.json case.sections.photo_count.area"}
    else:
        metric = {"count_mae_per_frame": fn["frame"]["mae"], "n_test_frames": fn["frame"]["n_images"],
                  "test": "FML, отложенные сессии съёмки", "source": "reports/final_numbers.json case.sections.photo_count.frame"}
    d_m2, d_km2 = density(n, area_m2)
    return {"model": card.get("version"), "survey": survey, "threshold": float(card["threshold"]), "n": n,
            "n_labelled": n_true, "abs_error": abs(n - n_true), "density_m2": d_m2, "density_km2": d_km2,
            "boxes": [[_r(x0 / W_), _r(y0 / H_), _r((x1 - x0) / W_), _r((y1 - y0) / H_)] for x0, y0, x1, y1 in boxes],
            "metric": metric, "classes": "без классов: счётчик считает предметы, материал не определяет"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-predict", action="store_true")
    a = ap.parse_args()
    IMG.mkdir(parents=True, exist_ok=True)
    sets = []
    for fn in (set_ucwd, set_tun, set_martin, set_maharjan,
               lambda: set_winans(not a.no_predict), lambda: set_fml(not a.no_predict)):
        meta, frames = fn()
        grp = sorted({g for f in frames for g in (f["n_by_group"] or {})}, key=list(GROUPS).index)
        meta.update(n_frames=len(frames), groups_labelled=grp,
                    algae_note="водоросли в этом наборе не размечены",
                    counter_checked=any("prediction" in f for f in frames))
        sets.append({"meta": meta, "frames": frames})
        print(meta["id"], len(frames), "frames", grp, flush=True)
    total = sum(p.stat().st_size for p in IMG.rglob("*.jpg"))
    idx = {"version": 1, "built": dt.datetime.now().isoformat(timespec="seconds"),
           "builder": "scripts/case/drones_index.py", "catalog": "docs/COUNT_DATASETS.md",
           "banner": "дрон, не спутник — со спутника считаются зоны, не отдельные предметы",
           "groups": GROUPS, "previews_bytes": total,
           "not_included": [
               {"what": "пляжи Индии", "why": "такого набора нет в каталоге docs/COUNT_DATASETS.md"},
               {"what": "водоросли / растительность", "why": "в показанных наборах не размечены; гиацинт размечен только в Saigon (B5), "
                                                          "его кадров на диске нет"}],
           "sets": sets}
    (OUT / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf8")
    print("previews", round(total / 1e6, 2), "MB")


if __name__ == "__main__":
    main()
