"""Registry of new labelled Sentinel-2 scenes (PLP 2019/2021/2022, FloatingObjects, RefinedFloatingObjects) +
overlap check with OUR data (MARIDA train/val/test, live scenes, pair scenes) by tile + date + place.

  .venv/Scripts/python.exe scripts/extra_data/build_registry.py
-> data/extra/registry.csv (+ copy reports/extra_data/registry.csv), reports/extra_data/overlap.csv

One row per (source, scene, label class). Levels (docs/INDEX.md, §11): B = confirmed satellite label of floating
material (no item count); D = verified negative; U = unverified background (NOT D, never a checked negative).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXTRA = ROOT / "data" / "extra"
FO = EXTRA / "floatingobjects"
PLP = EXTRA / "plp"
OUT_REP = ROOT / "reports" / "extra_data"

import geopandas as gpd  # noqa: E402
import rasterio  # noqa: E402
from rasterio.warp import transform_bounds  # noqa: E402
from shapely.geometry import box  # noqa: E402

LIC = {
    "PLP2019": ("CC-BY-4.0", "https://doi.org/10.5281/zenodo.3752719",
                "Topouzelis K. et al., PLP2019 dataset, Zenodo 3752719 (Univ. Aegean MRSG)"),
    "PLP2021": ("unspecified (targets: Papageorgiou et al. 2022; mirror in MIT repo marinedebrisdetector)",
                "https://marinedebrisdetector.s3.eu-central-1.amazonaws.com/data/PLP.zip",
                "Papageorgiou D., Topouzelis K., Suaria G., Aliani S., Corradi P. 2022 (PLP 2021); Russwurm et al. 2023"),
    "PLP2022": ("unspecified (mirror in MIT repo marinedebrisdetector)",
                "https://marinedebrisdetector.s3.eu-central-1.amazonaws.com/data/PLP.zip",
                "Plastic Litter Project 2022 (Univ. Aegean); Russwurm et al. 2023"),
    "FloatingObjects": ("Apache-2.0 (github.com/ESA-PhiLab/floatingobjects); cite Mifdal et al. 2021",
                        "https://marinedebrisdetector.s3.eu-central-1.amazonaws.com/data/floatingobjects.zip",
                        "Mifdal J., Longepe N., Russwurm M. 2021, ISPRS Annals V-3-2021, 285-293"),
    "RefinedFloatingObjects": ("MIT (github.com/MarcCoru/marinedebrisdetector); cite Russwurm et al. 2023",
                               "https://marinedebrisdetector.s3.eu-central-1.amazonaws.com/data/refinedfloatingobjects.zip",
                               "Russwurm M., Venkatesa S.J., Tuia D. 2023, Large-scale detection of marine debris in "
                               "coastal areas with Sentinel-2, iScience 26(12) 108402"),
}
# measured flags (L89, fo_features + FDI_dmed15 medians of line px vs background; SCL of line px)
NOTES = {
    "tangshan_20180130": "январь, Бохайский залив: вероятен морской лёд; FDI-контраст линий 0.0018 (низкий); 29 % всех "
                         "пикселей линий - вес ограничить или исключить до просмотра",
    "toledo_20191221": "декабрь, оз. Эри (пресная вода): FDI-контраст линий 0.0002 ~ фон; вероятно лёд/шуга - исключить до просмотра",
    "tunisia_20180715": "0 % пикселей линий - вода по SCL (мелководье/осушка у Джербы) - проверить",
    "mandaluyong_20180314": "то же место (51PTS), что MARIDA test S2_17-7-16 и S2_18-5-19 - при оценке на MARIDA test не "
                            "обучать на нём (утечки пикселей нет, но то же место)",
    "durban_20190424": "та же съёмка, что MARIDA train S2_24-4-19_36JUN и live durban/2019-04-24 - пиксели не читались",
}
RE_MARIDA = re.compile(r"S2_(\d{1,2})-(\d{1,2})-(\d{2})_(\d{2}[A-Z]{3})")


# ------------------------------------------------------------------ our data (tile, date, bbox, what)
def ours() -> pd.DataFrame:
    rows = []
    splits = {}
    for s in ("train", "val", "test"):
        for ln in (ROOT / "data/MARIDA/splits" / f"{s}_X.txt").read_text().split():
            splits[ln.strip()] = s
    for d in sorted((ROOT / "data/MARIDA/patches").iterdir()):
        m = RE_MARIDA.match(d.name)
        if not m:
            continue
        dd, mm, yy, tile = m.groups()
        sp = sorted({splits.get(f.stem[3:], "none") for f in d.glob("S2_*.tif") if not f.stem.endswith(("_cl", "_conf"))})
        bb = None
        for f in d.glob("S2_*.tif"):
            if f.stem.endswith(("_cl", "_conf")):
                continue
            with rasterio.open(f) as s:
                b = transform_bounds(s.crs, "EPSG:4326", *s.bounds)
            bb = b if bb is None else (min(bb[0], b[0]), min(bb[1], b[1]), max(bb[2], b[2]), max(bb[3], b[3]))
        rows.append(dict(ours="MARIDA", ours_id=d.name, tile=tile, date=dt.date(2000 + int(yy), int(mm), int(dd)).isoformat(),
                         split="+".join(sp), bbox=bb))
    for sj in sorted((ROOT / "data/live").glob("*/*/scene.json")):
        j = json.loads(sj.read_text(encoding="utf-8"))
        rows.append(dict(ours="live", ours_id=f"{j['region']}/{j['date']}", tile=j.get("tile"), date=j["date"],
                         split="live(demo/inference)", bbox=tuple(j["bounds_wgs84"])))
    bp = ROOT / "data/pairs/best_per_event.csv"
    if bp.is_file():
        p = pd.read_csv(bp)
        for _, r in p.iterrows():
            if isinstance(r.get("tile"), str) and isinstance(r.get("scene_datetime"), str):
                rows.append(dict(ours="pairs", ours_id=r.event_id, tile=str(r.tile), date=r.scene_datetime[:10],
                                 split="pairs(case)", bbox=(r.lon - 0.2, r.lat - 0.2, r.lon + 0.2, r.lat + 0.2)))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ new scenes
def fo_rows(items: dict, ledger: pd.DataFrame, feat_counts: dict) -> list[dict]:
    rows = []
    for p in sorted((FO / "shapefiles").glob("*.shp")):
        g = gpd.read_file(p)
        g = g[g.geometry.notna()]
        region = p.stem
        it = items.get(region, {})
        b = g.to_crs(4326).total_bounds
        u = g.to_crs(g.estimate_utm_crs())
        rows.append(dict(source="FloatingObjects", scene_id=region, tile=it.get("tile", ""), date=_d(region),
                         item_l2a=it.get("item_id", ""), bbox=tuple(b), label_geom="LineString (hand-drawn along objects)",
                         cls="floating objects (debris/litter windrows, mixed natural+anthropogenic, not plastic-only)",
                         kind="natural_accumulation", level="B", n_labels=len(g),
                         label_size=f"{u.length.sum() / 1000:.1f} km of lines",
                         pixels_fetched=_fetched(ledger, region), **feat_counts.get((region, "lines"), {})))
    for p in sorted((FO / "refined").glob("*.shp")):
        if "qualitative" in p.stem:
            continue
        g = gpd.read_file(p)
        region = p.stem
        it = items.get(region, {})
        for t, lvl, kind, cls in ((1, "B", "natural_accumulation", "marine debris point (refined, accurate)"),
                                  (0, "D", "verified_negative", "no-debris point (refined: water/ships/wakes/coast, "
                                                                "annotated as negative)")):
            gg = g[g["type"] == t]
            if not len(gg):
                continue
            rows.append(dict(source="RefinedFloatingObjects", scene_id=region, tile=it.get("tile", ""), date=_d(region),
                             item_l2a=it.get("item_id", ""), bbox=tuple(gg.to_crs(4326).total_bounds),
                             label_geom="Point (1 px)", cls=cls, kind=kind, level=lvl, n_labels=len(gg),
                             label_size="1 px per point", pixels_fetched=_fetched(ledger, region),
                             **feat_counts.get((region, f"points{t}"), {})))
    return rows


def _d(region):
    s = region.rsplit("_", 1)[1]
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def _fetched(ledger, region):
    if ledger is None or not len(ledger):
        return "no"
    d = ledger[ledger.region == region]
    if not len(d):
        return "no (excluded: duplicate of our data)" if region == "durban_20190424" else "no (budget)"
    n = sum(len(str(c).split(";")) for c in d.cells)
    return f"yes: {n} cells x 10.24 km (L2A Earth Search)"


def plp_rows() -> list[dict]:
    lab = pd.read_csv(PLP / "labels_plp.csv", dtype={"date": str})
    rows = []
    for (year, date), d in lab.groupby(["year", "date"]):
        src = f"PLP{year}"
        dd = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        xs, ys = d.x.values, d.y_utm.values
        b = transform_bounds("EPSG:32635", "EPSG:4326", xs.min() - 5, ys.min() - 5, xs.max() + 5, ys.max() + 5)
        for (kind, mat, lvl), q in d.groupby(["kind", "material", "level"]):
            if kind == "background_unverified":
                continue
            ntarg = q.target_id.nunique()
            rows.append(dict(source=src, scene_id=q.scene.iloc[0], tile="35SMD", date=dd, item_l2a="",
                             bbox=tuple(b),
                             label_geom=("pixel % cover (drone-verified)" if year == 2019 else
                                         "target point + radius/width (GPS, deployed)"),
                             cls=f"PLP target {mat}" if kind != "plp2019_pixel_no_plastic" else "PLP2019 labelled pixel, 0 % plastic",
                             kind="artificial_target" if lvl == "B" else "verified_negative", level=lvl,
                             n_labels=int(ntarg), label_size=_plp_size(year, mat),
                             pixels_fetched="yes (dataset GeoTIFF/netCDF)", n_px=len(q),
                             n_px_frac_ge_05=int((q.frac >= 0.5).sum()),
                             notes="; ".join(sorted({str(n) for n in q.note.dropna() if str(n) and year != 2019}))))
    return rows


def _plp_size(year, mat):
    if year == 2019:
        return "labelled 10 m pixels, plastic share 0-0.43"
    if year == 2021:
        return "circle r=14 m (~616 m2, ~6 px)"
    return "PVC square 5x5 m (25 m2)" if mat == "PVC" else "HDPE circle r=3.5 m (~38 m2, sub-pixel)"


# ------------------------------------------------------------------ overlap
def overlap(reg: pd.DataFrame, our: pd.DataFrame) -> pd.DataFrame:
    out = []
    for _, r in reg.drop_duplicates(["source", "scene_id"]).iterrows():
        rb = box(*r.bbox)
        for _, o in our.iterrows():
            ob = box(*o.bbox) if o.bbox is not None else None
            same_date = r.date == o.date
            same_tile = bool(r.tile) and r.tile == o.tile
            inter = ob is not None and rb.intersects(ob)
            near = ob is not None and rb.distance(ob) < 0.5  # ~50 km
            kind = None
            if same_date and (same_tile or inter):
                kind = "SAME_ACQUISITION"
            elif same_date and near:
                kind = "same_date_nearby"
            elif inter or (same_tile and not same_date):
                kind = "same_place_other_date"
            elif near:
                kind = "nearby_other_date(<50km)"
            if kind:
                out.append(dict(source=r.source, scene_id=r.scene_id, tile=r.tile, date=r.date, ours=o.ours,
                                ours_id=o.ours_id, ours_tile=o.tile, ours_date=o.date, ours_split=o.split, kind=kind))
    return pd.DataFrame(out)


def feat_counts_fo() -> dict:
    p = EXTRA / "features_floatingobjects.npz"
    if not p.is_file():
        return {}
    z = np.load(p)
    sc = z["scenes"][z["group"]]
    kind = z["kind"]
    res = {}
    for region in np.unique(sc):
        m = sc == region
        res[(region, "lines")] = dict(n_px=int((m & (kind == "fo_line")).sum()))
        res[(region, "points1")] = dict(n_px=int((m & (kind == "refined_pos")).sum()))
        res[(region, "points0")] = dict(n_px=int((m & (kind == "refined_neg")).sum()))
    return res


def main():
    items = json.loads((FO / "items.json").read_text(encoding="utf-8")) if (FO / "items.json").is_file() else {}
    ledger = pd.read_csv(FO / "fetch_ledger.csv") if (FO / "fetch_ledger.csv").is_file() else None
    reg = pd.DataFrame(plp_rows() + fo_rows(items, ledger, feat_counts_fo()))
    our = ours()
    ov = overlap(reg, our)
    # verdict per scene
    verdict = {}
    for (s, sid), d in ov.groupby(["source", "scene_id"]) if len(ov) else []:
        k = set(d.kind)
        if "SAME_ACQUISITION" in k:
            w = d[d.kind == "SAME_ACQUISITION"]
            verdict[(s, sid)] = "EXCLUDE: same acquisition as " + ", ".join(f"{a}:{b}({c})" for a, b, c in
                                                                           zip(w.ours, w.ours_id, w.ours_split))
        elif "same_date_nearby" in k:
            verdict[(s, sid)] = "check: same date nearby"
        elif "same_place_other_date" in k:
            w = d[d.kind == "same_place_other_date"]
            verdict[(s, sid)] = "ok (no pixel leak); same place other date: " + ", ".join(
                sorted({f"{a}:{b}({c})" for a, b, c in zip(w.ours, w.ours_id, w.ours_split)}))[:300]
    reg["overlap_ours"] = [verdict.get((s, i), "none") for s, i in zip(reg.source, reg.scene_id)]
    reg["notes"] = [(str(n) + "; " if isinstance(n, str) and n else "") + NOTES.get(i, "") if NOTES.get(i) else n
                    for n, i in zip(reg.get("notes", pd.Series([""] * len(reg))), reg.scene_id)]
    reg["use_for_training"] = np.where(reg.overlap_ours.str.startswith("EXCLUDE"), "no (duplicate of our data)",
                                       np.where(reg.level == "U", "no (unverified)", "yes"))
    review = {"tangshan_20180130": "review first (likely sea ice)", "toledo_20191221": "review first (likely lake ice)",
              "tunisia_20180715": "review first (SCL not water)",
              "mandaluyong_20180314": "yes, but not when the judge is MARIDA test (same place)"}
    reg["use_for_training"] = [review.get(i, u) if u == "yes" else u for i, u in zip(reg.scene_id, reg.use_for_training)]
    lic = reg.source.map(lambda s: LIC["PLP2021" if s.startswith("PLP2021") else s][0] if s in LIC or s.startswith("PLP") else "")
    reg["license"] = [LIC[s][0] for s in reg.source]
    reg["link"] = [LIC[s][1] for s in reg.source]
    reg["citation"] = [LIC[s][2] for s in reg.source]
    reg["geometry_wgs84_bbox"] = [f"POLYGON(({b[0]:.5f} {b[1]:.5f},{b[2]:.5f} {b[1]:.5f},{b[2]:.5f} {b[3]:.5f},"
                                  f"{b[0]:.5f} {b[3]:.5f},{b[0]:.5f} {b[1]:.5f}))" for b in reg.bbox]
    cols = ["source", "scene_id", "tile", "date", "kind", "level", "cls", "label_geom", "label_size", "n_labels", "n_px",
            "n_px_frac_ge_05", "geometry_wgs84_bbox", "item_l2a", "pixels_fetched", "overlap_ours", "use_for_training",
            "license", "link", "citation", "notes"]
    for c in cols:
        if c not in reg:
            reg[c] = ""
    reg = reg[cols]
    EXTRA.mkdir(parents=True, exist_ok=True)
    OUT_REP.mkdir(parents=True, exist_ok=True)
    reg.to_csv(EXTRA / "registry.csv", index=False)
    shutil.copy(EXTRA / "registry.csv", OUT_REP / "registry.csv")
    ov.to_csv(OUT_REP / "overlap.csv", index=False)
    print(reg.groupby(["source", "kind", "level"]).agg(scenes=("scene_id", "nunique"), labels=("n_labels", "sum")).to_string())
    print(ov.kind.value_counts().to_string() if len(ov) else "no overlaps")
    print(reg[reg.overlap_ours != "none"][["source", "scene_id", "overlap_ours"]].drop_duplicates().to_string())


if __name__ == "__main__":
    main()
