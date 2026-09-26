r"""§55 п.1 «Реальное время»: массовая обработка НОВЫХ снимков Sentinel-2 L2A нашей моделью (CPU), отдельный набор.

По всем районам кейса (18 районов stac.REGIONS + демо-акватория Cózar 2024 = 19) ищет в STAC (Earth Search) все сцены
тайла района за последние --days суток, отбирает по облачности ВЫРЕЗКИ района (SCL 8/9/10 ≤ --max-crop-cloud, по
умолчанию 0,40) и валидности, пиксели берёт из Planetary Computer (правило fetch_live: у Earth Search тёмная вода
обрезана на DN = 1 — такие сцены пропускаются и попадут в обработку, когда появятся в PC), затем:
  детектор weights/lgbm (порог 0.63, без гармонизации) + маска качества (облака, блик, суша) — studio_detector_current.run_scene
  → зоны и статусы (суда, пена, ветер > 5 м/с, блик, облака) — scene_zones.build_scene (те же правила, что у 286 зон кейса).

Растры — в data_cache/fresh_s2/ (вне git; bands.tif удаляется после обработки, если не --keep-bands);
в git — data/case/fresh_s2/fresh-<район>-<дата>/ (scene.json, zones.geojson, detections.geojson, rgb.jpg ≤ 150 КБ,
quality.png) и data/case/fresh_s2/index.json (+ runs.json — журнал запусков: время, новых сцен, отбор).
Основные зоны кейса (data/case/scene_zones, 286 зон) не трогаются.

  $env:CUDA_VISIBLE_DEVICES=""; .venv/Scripts/python.exe scripts/case/live_batch.py --days 30 --workers 6
  .venv/Scripts/python.exe scripts/case/live_batch.py --days 3            # ежедневно (scripts/live_daily.ps1)
  .venv/Scripts/python.exe scripts/case/live_batch.py --index-only        # только пересобрать index.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import shutil
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""
ROOT = Path(__file__).resolve().parents[2]
TMP = ROOT / "data_cache" / "fresh_s2" / "tmp"  # временные файлы GDAL/rasterio — сюда, чистятся после снимка
TMP.mkdir(parents=True, exist_ok=True)
for _v in ("TMP", "TEMP", "TMPDIR", "CPL_TMPDIR"):
    os.environ[_v] = str(TMP)
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "case"))

from macroplastic.live import stac  # noqa: E402

CACHE = ROOT / "data_cache" / "fresh_s2"
LIVE = CACHE / "live"
DET = CACHE / "det"
OUT = ROOT / "data" / "case" / "fresh_s2"
LOGDIR = ROOT / "out" / "live_batch"
LABEL = "автоматически, не проверено человеком"
SOURCE = "Sentinel-2 L2A (STAC: Earth Search — поиск, Planetary Computer — пиксели)"
NOTE = ("новые снимки Sentinel-2 L2A (10 м) за последние 30 суток, обработаны нашим детектором автоматически; зоны не "
        "проверены человеком и не входят в основные зоны кейса (286) и в его числа; класс детектора — любой плавающий "
        "материал, не только пластик; количества (шт., масса) по спутниковому снимку нет")
NASA_NOTE = ("NASA MODIS/VIIRS (250–375 м на пиксель) — только ежедневный контекст (облака, цветение, пятна): детектор "
             "обучен на Sentinel-2 10 м, на пикселе 250–375 м скопления физически не видны, поэтому модель на кадрах NASA "
             "не запускается. Детекция идёт на каждом новом Sentinel-2 (новый снимок каждого места раз в 2–5 дней).")
RGB_MAX = 150_000
MIN_FREE = 25 * 1024 ** 3  # < 25 ГБ свободно на диске C — не скачивать (§59 п.3)
MAX_CACHE = 1024 ** 3  # data_cache/fresh_s2 ≤ 1 ГБ


def cache_size() -> int:
    return sum(f.stat().st_size for f in CACHE.rglob("*") if f.is_file()) if CACHE.is_dir() else 0

# 19-й район — демо-акватория Cózar 2024 (Альборан): в stac.REGIONS её нет, центр — по сцене кейса
EXTRA_REGIONS = {"cozar_demo": dict(region_name="Альборанское море (акватория демо Cózar 2024)",
                                    region_name_en="Alboran Sea (Cózar 2024 demo area)", country="Испания",
                                    tile="30SXE"),
                 # акватории организаторов: точки событий CSV (data/case/prime_csv/index.json), где их больше всего и
                 # куда Sentinel-2 снимает (Северное море); открытый океан (Тихоокеанское мусорное пятно, ~31–33° с. ш.,
                 # 135–143° з. д.) Sentinel-2 не снимает — там сцен нет
                 "org_german_bight": dict(region_name="Германская бухта (акватория CSV организаторов, Gutow 2018)",
                                          region_name_en="German Bight (organisers' CSV area)", country="Германия",
                                          tile=None, center=(7.66, 54.08)),
                 "org_north_sea": dict(region_name="Северное море, 55° с. ш. 6° в. д. (акватория CSV организаторов)",
                                       region_name_en="North Sea 55N 6E (organisers' CSV area)", country="—",
                                       tile=None, center=(6.0, 55.0))}


def regions() -> dict:
    regs = {k: dict(v) for k, v in stac.REGIONS.items()}
    for k, v in EXTRA_REGIONS.items():
        if k in regs:
            continue
        v = dict(v)
        if v.get("center"):
            regs[k] = v
            continue
        sj = sorted((ROOT / "data" / "live" / k).glob("*/scene.json"))
        if not sj:
            continue
        b = json.loads(sj[0].read_text(encoding="utf-8"))["bounds_wgs84"]
        v["center"] = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        regs[k] = v
    stac.REGIONS.update({k: v for k, v in regs.items() if k not in stac.REGIONS})
    return regs


def _load(p: Path, default):
    """Чтение JSON, который параллельный запуск может как раз переписывать."""
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _write(p: Path, obj, **kw):
    """Атомарная запись (API читает index.json/catalog.json во время обработки)."""
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, default=str, **kw), encoding="utf-8")
    os.replace(tmp, p)


def key_of(r, d):
    return f"fresh-{r}-{d}"


def now_utc():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ 1. поиск + отбор (сеть, потоки)
def search_region(r: str, reg: dict, days: int, any_tile: bool = False) -> tuple[str, list, str | None]:
    """Сцены за последние days суток; одна на дату: тайл района, иначе (any_tile) соседний тайл MGRS, накрывающий
    центр района (другая орбита / край тайла), с наименьшей облачностью тайла."""
    lon, lat = reg["center"]
    end = dt.date.today() + dt.timedelta(days=1)
    start = end - dt.timedelta(days=days + 1)
    try:
        its = stac.search("earth-search", lon, lat, f"{start}/{end}", max_cloud=100,
                          tile=None if any_tile or not reg.get("tile") else reg["tile"])
    except Exception as e:  # noqa: BLE001
        return r, [], f"STAC: {e!r}"[:300]
    best: dict = {}
    for it in its:  # stac.search sorts by tile cloud cover
        d = it.properties["datetime"][:10]
        if d not in best or (reg.get("tile") and stac.tile_of(it) == reg["tile"] and stac.tile_of(best[d]) != reg["tile"]):
            best[d] = it
    return r, list(best.values()), None


def screen_item(r: str, reg: dict, it, max_crop_cloud: float, min_valid: float) -> dict:
    from fetch_live import crop_cloud
    lon, lat = reg["center"]
    d = it.properties["datetime"][:10]
    row = {"region": r, "date": d, "id": it.id, "tile": stac.tile_of(it), "tile_cloud": it.properties.get("eo:cloud_cover")}
    try:
        epsg, bounds = stac.crop_bounds(it, lon, lat, 25_000)
        vf, cf, sf, wf, gl = crop_cloud(it, epsg, bounds)
    except Exception as e:  # noqa: BLE001
        row.update(ok=False, why=f"screen error {e!r}"[:200])
        return row
    row.update({"crop_valid": round(vf, 3), "crop_cloud": round(cf, 3), "crop_shadow": round(sf, 3),
                "scl_water": round(wf, 3), "water_b8a": None if gl != gl else round(gl, 4), "epsg": epsg,
                "bounds": list(bounds), "ok": False})
    if vf < min_valid:
        row["why"] = f"вырезка на краю тайла: валидно {vf:.2f} < {min_valid}"
    elif cf > max_crop_cloud:
        row["why"] = f"облачность вырезки {cf:.2f} > {max_crop_cloud}"
    elif wf < 0.01:
        row["why"] = "в вырезке нет воды (SCL 6)"
    else:
        row["ok"] = True
    return row


# ------------------------------------------------------------------ 2. обработка сцены (процессы)
_W: dict = {}


def _init_worker(threads: int):
    import tempfile
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    wt = TMP / str(os.getpid())
    wt.mkdir(parents=True, exist_ok=True)
    for v in ("TMP", "TEMP", "TMPDIR", "CPL_TMPDIR"):
        os.environ[v] = str(wt)
    tempfile.tempdir = str(wt)
    import pandas as pd
    import yaml
    import scene_zones as sz
    from macroplastic.models.lgbm_predict import load_predictor
    regions()
    cfg = yaml.safe_load((ROOT / "configs" / "case_pairs.yaml").read_text(encoding="utf-8"))
    assert cfg["detector"]["harmonize"] == "none" and cfg["detector"]["weights"] == "weights/lgbm"
    _W["cfg"] = cfg
    _W["pred"] = load_predictor(ROOT / cfg["detector"]["weights"], harmonize="none", device="cpu", num_threads=threads)
    _W["cozar"] = pd.read_csv(ROOT / "reports" / "extra_data" / "registry_cozar2024.csv.gz",
                              usecols=["fil_idx", "tile", "date", "bbox_wkt", "n_pixels_fil"])
    _W["minfo"] = sz.model_info()
    sz.OUT = OUT


def shrink_rgb(p: Path, limit: int = RGB_MAX):
    from PIL import Image
    if not p.is_file() or p.stat().st_size <= limit:
        return
    im = Image.open(p).convert("RGB")
    for side, q in ((1600, 80), (1400, 75), (1200, 72), (1024, 70), (900, 65), (768, 60)):
        im2 = im.copy()
        im2.thumbnail((side, side))
        buf = io.BytesIO()
        im2.save(buf, "JPEG", quality=q, optimize=True)
        if buf.tell() <= limit:
            break
    p.write_bytes(buf.getvalue())


def compact(rgb_limit: int = RGB_MAX):
    """Git-часть набора компактная: quality.png (API его не отдаёт) -> data_cache/fresh_s2/quality/, rgb.jpg ≤ лимита."""
    qd = CACHE / "quality"
    qd.mkdir(parents=True, exist_ok=True)
    for d in OUT.glob("fresh-*"):
        q = d / "quality.png"
        if q.is_file():
            q.replace(qd / f"{d.name}.png")
        shrink_rgb(d / "rgb.jpg", rgb_limit)
        # §60: у каждой зоны — и дата съёмки, и дата обработки
        sj, zj = d / "scene.json", d / "zones.geojson"
        if sj.is_file() and zj.is_file():
            info = json.loads(sj.read_text(encoding="utf-8"))
            fc = json.loads(zj.read_text(encoding="utf-8"))
            ch = False
            for f in fc.get("features") or []:
                pr = f.setdefault("properties", {})
                if pr.get("processed_at") != info.get("processed_at") or "acquired_at" not in pr:
                    pr.update({"acquired_at": info.get("datetime"), "acquisition_date": info.get("date"),
                               "processed_at": info.get("processed_at"), "human_checked": False,
                               "auto_label": LABEL})
                    ch = True
            if ch:
                zj.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")


def process_scene(r: str, row: dict, keep_bands: bool) -> dict:
    import scene_zones as sz
    import studio_detector_current as sdc
    from fetch_live import pick_item
    t0 = time.time()
    d = row["date"]
    key = key_of(r, d)
    reg = stac.REGIONS[r]
    lon, lat = reg["center"]
    res = {"key": key, "region": r, "date": d, "status": "error"}
    sdir, ddir = LIVE / r / d, DET / r / d
    free = shutil.disk_usage(ROOT).free
    if free < MIN_FREE or cache_size() > MAX_CACHE:
        res.update(status="no_disk", why=f"остановлено: нет места на диске (свободно {free / 1e9:.1f} ГБ, кэш "
                                         f"{cache_size() / 1e9:.2f} ГБ) — снимок не скачан")
        return res
    try:
        it = pick_item(d, row.get("tile") or reg["tile"], lon, lat)
        if stac.source_of(it) != "planetary-computer" or stac.item_epsg(it) != row["epsg"]:
            res.update(status="skip_no_pc", why="сцены ещё нет в Planetary Computer (у Earth Search тёмная вода "
                                                "обрезана на DN = 1) — будет обработана, когда появится")
            return res
        for k in range(3):  # сеть PC иногда рвёт чтение COG (RasterioIOError) — повтор
            try:
                crop = stac.read_crop(it, row["epsg"], row["bounds"])
                break
            except Exception:  # noqa: BLE001
                if k == 2:
                    raise
                time.sleep(5 * (k + 1))
        meta = stac.write_scene(sdir, r, it, crop, extra=dict(
            water_b8a_median_20m=row.get("water_b8a"),
            selection=f"live_batch {now_utc()}: crop cloud {row['crop_cloud']} <= 0.40, valid {row['crop_valid']}"))
        del crop
        t_read = time.time() - t0
        di = sdc.run_scene(sdir, _W["pred"], _W["cfg"], ddir)
        t_det = time.time() - t0 - t_read
        s = {"key": key, "kind": "fresh_s2", "sdir": sdir, "ddir": ddir, "region": r, "date": d}
        info = sz.build_scene(s, _W["cozar"], _W["minfo"])
        od = OUT / key
        shrink_rgb(od / "rgb.jpg")
        info.update({"auto_label": LABEL, "human_checked": False, "source": SOURCE, "in_case_numbers": False,
                     "processed_at": now_utc(), "processing_s": round(time.time() - t0, 1),
                     "timing_s": {"read": round(t_read, 1), "detector": round(t_det, 1),
                                  "zones": round(time.time() - t0 - t_read - t_det, 1)},
                     "crop_cloud_frac": row["crop_cloud"], "crop_valid_frac": row["crop_valid"],
                     "tile_cloud_pct": row.get("tile_cloud"), "stac_item": it.id,
                     "det_pixels_raw": di.get("pixels"), "det_objects": di.get("objects")})
        info.setdefault("region_name", meta.get("region_name"))
        (od / "scene.json").write_text(json.dumps(info, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        res.update(status="ok", n_zones=info.get("n_zones_total"), by_status=info.get("by_status"),
                   evaluable=info.get("evaluable"), processing_s=info["processing_s"])
    except Exception as e:  # noqa: BLE001
        res.update(error=repr(e)[:300], tb=traceback.format_exc()[-1500:])
    finally:  # промежуточные растры не храним (§58: диск): в git — только зоны JSON + превью
        if not keep_bands:
            shutil.rmtree(sdir, ignore_errors=True)
            shutil.rmtree(ddir, ignore_errors=True)
        wt = TMP / str(os.getpid())  # временные файлы этого процесса — очистка после каждого снимка
        shutil.rmtree(wt, ignore_errors=True)
        wt.mkdir(parents=True, exist_ok=True)
    res["t_s"] = round(time.time() - t0, 1)
    return res


# ------------------------------------------------------------------ 3. индекс
def scene_counts(key: str) -> dict:
    """Зоны снимка: кандидаты-кластеры (без записи «вся вырезка», id …-000) и находки (статус detected)."""
    p = OUT / key / "zones.geojson"
    out = {"zones": 0, "finds": 0, "whole_crop_status": None, "status_all": {}}
    if not p.is_file():
        return out
    for f in json.loads(p.read_text(encoding="utf-8")).get("features") or []:
        pr = f.get("properties") or {}
        st = pr.get("detection_status")
        out["status_all"][st] = out["status_all"].get(st, 0) + 1
        if str(pr.get("zone_id", "")).endswith("-000"):
            out["whole_crop_status"] = pr.get("detection_status")
            continue
        out["zones"] += 1
        out["finds"] += pr.get("detection_status") == "detected"
    return out


WINDOW_LABELS = {30: "1 мес", 90: "3 мес", 180: "6 мес", 365: "год"}
CAT_SOURCE = "STAC Earth Search (поиск, маска облаков SCL 20 м); пиксели 10 м — Planetary Computer (STAC)"
STATUS_RU = {"processed": "обработан", "rejected_quality": "не прошёл качество", "not_in_pc": "нет пикселей в PC",
             "error": "ошибка обработки", "pending": "пригоден, в очереди", "screen_error": "ошибка проверки",
             "no_disk": "остановлено: нет места на диске",
             "not_checked": "найден в каталоге, не проверен (не успели до заморозки)"}


def window_label(days: int) -> str:
    return next((f"поиск за: {v}" for k, v in sorted(WINDOW_LABELS.items()) if days <= k), f"поиск за: {days} сут.")


def _legacy_results() -> list[dict]:
    """Итоги из логов запусков (строки «[время] i/N key status t s zones=… причина»)."""
    import re
    pat = re.compile(r"\[(\S+)\] \d+/\d+ (fresh-\S+) (ok|skip_no_pc|error) (\S+) s zones=\S+ ?(.*)$")
    out = []
    for lp in sorted(LOGDIR.glob("*.log")):
        for line in lp.read_text(encoding="utf-8", errors="replace").splitlines():
            m = pat.match(line)
            if m:
                out.append({"key": m.group(2), "status": m.group(3), "at": m.group(1),
                            "why": m.group(5).strip()[:300] or None})
    return out


def build_catalog(scenes: list[dict]) -> dict:
    """Каждый найденный в STAC снимок: дата, источник, пригодность, причина отказа, статус, время обработки."""
    cp = OUT / "catalog.json"
    cat = {}
    if cp.is_file():
        try:
            cat = {f"{e['region']}|{e['date']}": e for e in json.loads(cp.read_text(encoding="utf-8")).get("scenes", [])}
        except Exception:  # noqa: BLE001
            cat = {}
    regs = regions()
    for fp in sorted(LOGDIR.glob("found_*.json")):
        for row in _load(fp, []):
            e = cat.setdefault(f"{row['region']}|{row['date']}", {"region": row["region"], "date": row["date"],
                                                                  "status": "not_checked", "suitable": None})
            e.setdefault("stac_item", row.get("id"))
            e.setdefault("tile", row.get("tile"))
            e.setdefault("tile_cloud_pct", row.get("tile_cloud"))
    for sp in sorted(LOGDIR.glob("screen_*.json")):
        for row in _load(sp, []):
            if "date" not in row:  # формат пробного запуска (по районам) — пропуск
                continue
            e = cat.setdefault(f"{row['region']}|{row['date']}", {"region": row["region"], "date": row["date"]})
            if e.get("status") == "processed":
                continue
            e.update({"stac_item": row.get("id"), "tile": row.get("tile"), "tile_cloud_pct": row.get("tile_cloud"),
                      "crop_cloud_frac": row.get("crop_cloud"), "crop_valid_frac": row.get("crop_valid"),
                      "scl_water_frac": row.get("scl_water"), "suitable": bool(row.get("ok")),
                      "reason": None if row.get("ok") else row.get("why")})
            e["status"] = ("pending" if row.get("ok") else
                           "screen_error" if str(row.get("why", "")).startswith("screen error") else "rejected_quality")
    res = _legacy_results()
    for rp in sorted(LOGDIR.glob("results_*.json")):
        res += _load(rp, [])
    for x in res:
        if not x.get("key") or x["status"] == "ok":
            continue
        r, d = x["key"][len("fresh-"):-11], x["key"][-10:]
        e = cat.setdefault(f"{r}|{d}", {"region": r, "date": d, "suitable": True})
        if e.get("status") != "processed":
            e.update(status={"skip_no_pc": "not_in_pc", "no_disk": "no_disk"}.get(x["status"], "error"),
                     reason=(x.get("why") or x.get("error") or "")[:300])
    for s in scenes:
        e = cat.setdefault(f"{s['region']}|{s['date']}", {"region": s["region"], "date": s["date"]})
        e.update({"status": "processed", "suitable": True, "reason": None, "key": s["key"],
                  "stac_item": e.get("stac_item") or s.get("scene_id"), "scene_id": s.get("scene_id"),
                  "tile": s.get("tile") or e.get("tile"), "processed_at": s.get("processed_at"),
                  "processing_s": s.get("processing_s"), "n_zones": s.get("n_zones"), "n_finds": s.get("n_finds"),
                  "zone_status_all": s.get("zone_status_all"), "whole_crop_status": s.get("whole_crop_status"),
                  "datetime": s.get("datetime")})
    today = dt.date.today()
    for e in cat.values():
        e["region_name"] = (regs.get(e["region"]) or {}).get("region_name", e["region"])
        e["source"] = CAT_SOURCE
        e["status_label"] = STATUS_RU.get(e.get("status"), e.get("status"))
        try:
            e["age_days"] = (today - dt.date.fromisoformat(e["date"])).days
        except Exception:  # noqa: BLE001
            e["age_days"] = None
    rows = sorted(cat.values(), key=lambda e: (e["region"], e["date"]))
    out = {"generated": now_utc(), "source": CAT_SOURCE, "status_labels": STATUS_RU, "scenes": rows}
    _write(cp, out, indent=1)
    return out


def _reasons(w: list[dict]) -> dict:
    out: dict = {}
    for e in w:
        st, why = e.get("status"), str(e.get("reason") or "")
        if st == "rejected_quality":
            k = ("облачность вырезки > 40 %" if why.startswith("облачность") else
                 "вырезка на краю тайла (мало валидных пикселей)" if why.startswith("вырезка на краю") else
                 "в вырезке нет воды" if "нет воды" in why else "другое (качество)")
        elif st == "not_in_pc":
            k = "пикселей ещё нет в Planetary Computer"
        elif st in ("error", "screen_error"):
            k = "сбой чтения/обработки (сеть)"
        else:
            continue
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _scene_result(e: dict) -> str:
    zs = e.get("zone_status_all") or {}
    if (e.get("n_finds") or 0) > 0:
        return "detected"
    if e.get("whole_crop_status"):
        return e["whole_crop_status"]
    return "insufficient_data" if zs.get("insufficient_data") else "not_detected"


def _zone_status(proc: list[dict]) -> dict:
    """Все записи-зоны обработанных снимков по статусам (вкл. запись «вся вырезка» у снимков без кластеров)."""
    out = {"detected": 0, "not_detected": 0, "insufficient_data": 0}
    for e in proc:
        for k, v in (e.get("zone_status_all") or {}).items():
            out[k] = out.get(k, 0) + v
    return out


def _coverage(w: list[dict]) -> list[dict]:
    """По районам и месяцам: найдено / обработано / в очереди — что покрыто, что нет."""
    by: dict = {}
    for e in w:
        r = by.setdefault(e["region"], {"region": e["region"], "region_name": e.get("region_name"), "months": {}})
        m = r["months"].setdefault(e["date"][:7], {"found": 0, "processed": 0, "pending": 0, "excluded": 0})
        m["found"] += 1
        st = e.get("status")
        m["processed" if st == "processed" else "pending" if st == "pending" else "excluded"] += 1
    return sorted(by.values(), key=lambda r: r["region"])


def funnel(rows: list[dict], days: int) -> dict:
    cut = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    w = [e for e in rows if e["date"] >= cut]
    proc = [e for e in w if e.get("status") == "processed"]
    pend = sum(e.get("status") in ("pending", "no_disk") for e in w)
    unchk = sum(e.get("status") == "not_checked" for e in w)
    return {"window_days": days, "window_label": window_label(days), "complete": pend == 0 and unchk == 0,
            "status_note": (None if pend == 0 and unchk == 0 else
                            f"архив обработан не полностью: {pend} пригодных снимков не обработаны, {unchk} найденных "
                            "не проверены по облачности (обработка на CPU ~1,5–2 мин на снимок); показан уже "
                            "полученный результат, продолжение — live_batch.py --reuse-screen"),
            "found": len(w),
            "downloaded": sum(e.get("crop_cloud_frac") is not None or e.get("status") == "processed" for e in w),
            "found_note": "найдено = все снимки Sentinel-2 L2A в каталоге STAC (Earth Search) по центру района, одна сцена на дату",
            "passed_quality": sum(bool(e.get("suitable")) for e in w),
            "processed": len(proc),
            "with_finds": sum((e.get("n_finds") or 0) > 0 for e in proc),
            "zones": sum(e.get("n_zones") or 0 for e in proc),
            "finds": sum(e.get("n_finds") or 0 for e in proc),
            "not_in_pc": sum(e.get("status") == "not_in_pc" for e in w),
            "errors": sum(e.get("status") in ("error", "screen_error") for e in w),
            "pending": pend,  # пригодны, не обработаны (очередь или пауза по диску)
            "not_checked": sum(e.get("status") == "not_checked" for e in w),
            "rejected_quality": sum(e.get("status") == "rejected_quality" for e in w),
            "excluded": sum(e.get("status") in ("rejected_quality", "not_in_pc", "error", "screen_error") for e in w),
            "not_processed": sum(e.get("status") in ("pending", "no_disk") for e in w),
            "no_disk": sum(e.get("status") == "no_disk" for e in w),
            "excluded_reasons": _reasons(w),
            "zone_status": _zone_status(proc),
            "scenes_by_result": {
                "with_finds": sum((e.get("n_finds") or 0) > 0 for e in proc),
                "not_detected": sum((e.get("n_finds") or 0) == 0 and _scene_result(e) == "not_detected" for e in proc),
                "insufficient_data": sum((e.get("n_finds") or 0) == 0 and _scene_result(e) == "insufficient_data"
                                         for e in proc)},
            "coverage": _coverage(w),
            "steps": ("найдено (STAC, снимки района, одна сцена на дату) → скачано (маска облаков SCL 20 м вырезки) → "
                      "прошло качество (облачность вырезки ≤ 40 %, валидно ≥ 75–90 %, есть вода) → обработано (пиксели "
                      "10 м из Planetary Computer + детектор + фильтры) → снимков с находками → зон")}


def build_index(run: dict | None = None) -> dict:
    from scene_zones import model_info
    scenes = []
    for p in sorted(OUT.glob("fresh-*/scene.json")):
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        s.setdefault("auto_label", LABEL)
        s.setdefault("human_checked", False)
        s.setdefault("source", SOURCE)
        s.setdefault("in_case_numbers", False)
        c = scene_counts(s["key"])
        s.update({"n_zones": c["zones"], "n_finds": c["finds"], "whole_crop_status": c["whole_crop_status"],
                  "zone_status_all": c["status_all"]})
        scenes.append(s)
    runs_p = OUT / "runs.json"
    runs = _load(runs_p, []) if runs_p.is_file() else []
    if run:
        runs.append(run)
        _write(runs_p, runs[-200:], indent=1)
    cat = build_catalog(scenes)
    win = max([30] + [int(x.get("days") or 0) for x in runs if x.get("finished") and x.get("trigger") != "test"])
    oldest = max([e.get("age_days") or 0 for e in cat["scenes"]] + [0])  # окно расширено (идёт или завершено)
    win = max(win, oldest)
    win = next((k for k in sorted(WINDOW_LABELS) if win <= k), win)
    cut = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    s30 = [s for s in scenes if str(s.get("date", "")) >= cut and not s.get("error")]
    last = next((x for x in reversed(runs) if x.get("finished")), None)
    last_ok = next((x for x in reversed(runs) if x.get("finished") and not x.get("failed")), None)
    idx = {"generated": now_utc(), "model": model_info(), "label": LABEL, "note": NOTE, "nasa_note": NASA_NOTE,
           "human_checked": False, "in_case_numbers": False, "source": SOURCE,
           "separate_from": "data/case/scene_zones (основные зоны кейса, 286)",
           "last_success": last_ok.get("finished") if last_ok else None,
           "last_success_trigger": last_ok.get("trigger") if last_ok else None,
           "search_window": {"days": win, "label": window_label(win)},
           "funnel": funnel(cat["scenes"], win), "funnel_30d": funnel(cat["scenes"], 30),
           "last_update": last.get("finished") if last else None,
           "new_scenes_last_run": last.get("new_scenes") if last else None,
           "scenes_30d": len(s30), "zones_30d": sum(s["n_zones"] for s in s30), "finds_30d": sum(s["n_finds"] for s in s30),
           "counting": ("зона = кластер пикселей детектора ≥ 5 пикс. (правила основного слоя); запись «вся вырезка» "
                        "(снимок без кластеров) зоной не считается; находка = зона со статусом «обнаружено»"),
           "regions_30d": len({s["region"] for s in s30}),
           "scenes": sorted(scenes, key=lambda s: (s["region"], s.get("date", "")))}
    _write(OUT / "index.json", idx, indent=1)
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--older-than-days", type=int, default=0,
                    help="брать только снимки старше N суток (расширение окна 1 мес -> 3 мес без повторов)")
    ap.add_argument("--regions", nargs="*")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--threads", type=int, default=3, help="LightGBM threads per worker")
    ap.add_argument("--max-crop-cloud", type=float, default=0.40)
    ap.add_argument("--min-valid", type=float, default=0.90)
    ap.add_argument("--max-scenes", type=int, default=0)
    ap.add_argument("--keep-bands", action="store_true")
    ap.add_argument("--index-only", action="store_true")
    ap.add_argument("--compact", action="store_true", help="с --index-only: quality.png -> data_cache, rgb.jpg ≤ --rgb-limit")
    ap.add_argument("--rgb-limit", type=int, default=RGB_MAX)
    ap.add_argument("--reuse-screen", action="store_true", help="брать отбор из прежних screen_*.json (без сети)")
    ap.add_argument("--trigger", default="cli")
    ap.add_argument("--force", action="store_true", help="игнорировать lock другого запуска")
    ap.add_argument("--exclude-screen", nargs="*", default=[],
                    help="screen_*.json параллельного запуска: его отобранные (ok) даты не брать")
    ap.add_argument("--any-tile", action="store_true",
                    help="все тайлы MGRS, накрывающие центр района (не только тайл района): больше дат/орбит")
    a = ap.parse_args()
    if a.index_only:
        if a.compact:
            compact(a.rgb_limit)
        idx = build_index()
        print(f"index: {len(idx['scenes'])} scenes, 30d {idx['scenes_30d']} / zones {idx['zones_30d']} / "
              f"finds {idx['finds_30d']}")
        print("funnel", json.dumps(idx["funnel"], ensure_ascii=False))
        return 0
    LOGDIR.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    lock = CACHE / "refresh.lock"  # защита от параллельного запуска (API /refresh, задача Планировщика, вручную)
    if lock.is_file() and time.time() - lock.stat().st_mtime < 3 * 3600 and not a.force:
        print(f"другой запуск ещё идёт ({lock}, {lock.read_text(encoding='utf-8')[:80]}) — выход", flush=True)
        return 3
    lock.write_text(f"{os.getpid()} {now_utc()} {a.trigger}", encoding="utf-8")
    try:
        return run(a)
    finally:
        lock.unlink(missing_ok=True)


def run(a) -> int:
    started = now_utc()
    regs = regions()
    sel = {k: v for k, v in regs.items() if not a.regions or k in a.regions}
    done = {p.parent.name for p in OUT.glob("fresh-*/scene.json")}
    for f in a.exclude_screen:
        done |= {key_of(x["region"], x["date"]) for x in json.loads(Path(f).read_text(encoding="utf-8")) if x.get("ok")}
    print(f"[{started}] search {len(sel)} regions, last {a.days} d, crop cloud <= {a.max_crop_cloud}", flush=True)
    items, errs = [], {}
    with ThreadPoolExecutor(10) as ex:
        for r, its, err in ex.map(lambda kv: search_region(kv[0], kv[1], a.days, a.any_tile), sel.items()):
            if err:
                errs[r] = err
            old_cut = (dt.date.today() - dt.timedelta(days=a.older_than_days)).isoformat()
            new = [it for it in its if key_of(r, it.properties["datetime"][:10]) not in done
                   and (not a.older_than_days or it.properties["datetime"][:10] < old_cut)]
            print(f"  {r}: STAC {len(its)}, новых {len(new)}{' ERR ' + err if err else ''}", flush=True)
            items += [(r, it) for it in new]
    (LOGDIR / f"found_{started[:19].replace(':', '')}.json").write_text(json.dumps(
        [{"region": r, "date": it.properties["datetime"][:10], "id": it.id, "tile": stac.tile_of(it),
          "tile_cloud": it.properties.get("eo:cloud_cover")} for r, it in items], ensure_ascii=False), encoding="utf-8")
    # свежие даты первыми
    items.sort(key=lambda t: t[1].properties["datetime"], reverse=True)
    known = {}
    if a.reuse_screen:  # продолжение после сбоя: отбор уже сделан — не скачиваем маску SCL повторно
        for sp in sorted(LOGDIR.glob("screen_*.json")):
            for x in _load(sp, []):
                if "date" in x and "epsg" in x:
                    known[(x["region"], x["date"])] = x
    screens, results, pfuts = [], [], []
    pex = ProcessPoolExecutor(a.workers, initializer=_init_worker, initargs=(a.threads,))
    n_sub = 0
    with ThreadPoolExecutor(24) as tex:
        def scr(r, it):
            k = (r, it.properties["datetime"][:10])
            return dict(known[k]) if k in known else screen_item(r, sel[r], it, a.max_crop_cloud, a.min_valid)
        sfuts = [tex.submit(scr, r, it) for r, it in items]
        for f in as_completed(sfuts):
            row = f.result()
            screens.append(row)
            print(f"  screen {row['region']} {row['date']}: {'OK' if row['ok'] else 'skip — ' + str(row.get('why'))}",
                  flush=True)
            if len(screens) % 25 == 0:  # отбор сохраняется по ходу (продолжение после сбоя)
                (LOGDIR / f"screen_{started[:19].replace(':', '')}.json").write_text(
                    json.dumps(screens, ensure_ascii=False, indent=1), encoding="utf-8")
            if row["ok"] and (not a.max_scenes or n_sub < a.max_scenes):
                pfuts.append(pex.submit(process_scene, row["region"], row, a.keep_bands))
                n_sub += 1
    (LOGDIR / f"screen_{started[:19].replace(':', '')}.json").write_text(
        json.dumps(screens, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{now_utc()}] screened {len(screens)}, to process: {n_sub} scenes", flush=True)
    todo = pfuts
    for i, f in enumerate(as_completed(pfuts), 1):
        try:
            x = f.result()
        except Exception as e:  # noqa: BLE001
            x = {"status": "error", "error": repr(e)[:300]}
        results.append(x)
        print(f"[{now_utc()}] {i}/{len(todo)} {x.get('key')} {x['status']} {x.get('t_s')} s "
              f"zones={x.get('n_zones')} {x.get('by_status') or ''} {x.get('error') or x.get('why') or ''}", flush=True)
        if x["status"] == "error" and x.get("tb"):
            print(x["tb"], flush=True)
        if i % 5 == 0:
            (LOGDIR / f"results_{started[:19].replace(':', '')}.json").write_text(
                json.dumps([{k: v for k, v in x.items() if k != "tb"} for x in results], ensure_ascii=False),
                encoding="utf-8")
            try:  # сбой промежуточного индекса не должен останавливать обработку
                build_index()
            except Exception as e:  # noqa: BLE001
                print("build_index failed:", repr(e), flush=True)
    pex.shutdown()
    (LOGDIR / f"results_{started[:19].replace(':', '')}.json").write_text(
        json.dumps([{k: v for k, v in x.items() if k != "tb"} for x in results], ensure_ascii=False), encoding="utf-8")
    ok = [x for x in results if x["status"] == "ok"]
    run = {"started": started, "finished": now_utc(), "trigger": a.trigger, "days": a.days,
           "stac_new_items": len(items), "already_processed": len(done), "search_errors": errs,
           "screened_ok": len(todo), "rejected": sum(not s["ok"] for s in screens),
           "new_scenes": len(ok), "skip_no_pc": sum(x["status"] == "skip_no_pc" for x in results),
           "errors": sum(x["status"] == "error" for x in results),
           "failed": bool(errs) and len(errs) == len(sel)}
    compact()
    idx = build_index(run)
    print(json.dumps(run, ensure_ascii=False), flush=True)
    print(f"index: 30d scenes {idx['scenes_30d']}, zones {idx['zones_30d']}, finds {idx['finds_30d']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
