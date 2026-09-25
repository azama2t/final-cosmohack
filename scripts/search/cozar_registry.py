"""Registry of the Cózar/Arias et al. 2024 Mediterranean litter-windrow catalogue (Zenodo 11045944) + leakage check.

Input : data/extra/cozar2024/filaments_meta.csv (scripts/search/cozar_remote.py meta; 14,374 filaments, remote range reads)
Output: data/extra/registry_cozar2024.csv (+ copy reports/extra_data/registry_cozar2024.csv)
        data/extra/cozar2024/scene_overlap.csv  (per S2 scene: tile, date, n filaments, overlap flags)
        prints a summary (numbers go to the task report)

Geometry: image coords of the S2 L1C 10 m grid of the MGRS tile. The file's x is the ROW and y the COLUMN
(numpy [x, y]); `limits` = [y_lo, x_lo, y_hi, x_hi]. Tile origin = Earth Search proj:transform (tile_origins.csv),
for 7 tiles without a STAC hit estimated from centroids (georef column). bbox -> WGS84 polygon (bbox_wkt);
lat_pix/lon_pix = centroid from pixel coords (differs from the file's lat/lon_centroid, see origin_resid_m).
"""
from __future__ import annotations

import datetime as dt
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from check_mados_overlap import scan_mados, scan_marida  # noqa: E402

D = ROOT / "data" / "extra" / "cozar2024"
RE_P = re.compile(r"(S2[AB])_MSIL1C_(\d{8})T(\d{6})_N(\d{4})_R(\d{3})_T(\d{2}[A-Z]{3})_")
LICENSE = "CC BY 4.0 (Zenodo 10.5281/zenodo.11045944; Arias, Cózar et al. 2024)"
# Full justification: reports/tasklog/98_cozar_sargasso.md §2 (Cózar et al. 2024 Methods + SI S2.3.3)
LEVEL_REASON = "human-supervised filament (6 operators + 2nd check, 14374 of 708742 candidates); auto WSI pixel mask; mixed floating matter"


def dec2dt(x: float) -> dt.datetime:
    y = int(x)
    a = dt.datetime(y, 1, 1)
    b = dt.datetime(y + 1, 1, 1)
    return a + (b - a) * (x - y)


def tile_epsg(tile: str, lat: float) -> int:
    z = int(tile[:2])
    return (32600 if lat >= 0 else 32700) + z


def main():
    df = pd.read_csv(D / "filaments_meta.csv")
    p = df["s2_product"].str.extract(RE_P)
    p.columns = ["mission", "date8", "time6", "baseline", "rel_orbit", "tile"]
    df = pd.concat([df, p], axis=1)
    assert df["tile"].notna().all(), "unparsed product names"
    df["date"] = pd.to_datetime(df["date8"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    df["datetime_utc"] = [dec2dt(v).strftime("%Y-%m-%dT%H:%M:%SZ") for v in df["dec_time"]]
    df["product_time_utc"] = pd.to_datetime(df["date8"] + df["time6"], format="%Y%m%d%H%M%S").dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    # Axis convention (verified): the file's "x" is the image ROW (north->south) and "y" the COLUMN, i.e. numpy [x, y]
    # indexing; `limits` = [y_lo, x_lo, y_hi, x_hi] -> [col_lo, row_lo, col_hi, row_hi]; pixel_x within row range.
    df = df.rename(columns={"x_lower": "L0", "y_lower": "L1", "x_upper": "L2", "y_upper": "L3"})
    df["col_lo"], df["row_lo"], df["col_hi"], df["row_hi"] = df.L0, df.L1, df.L2, df.L3
    df["row_centroid"], df["col_centroid"] = df.x_centroid, df.y_centroid
    ok_ax = ((df.row_centroid >= df.row_lo) & (df.row_centroid <= df.row_hi) & (df.col_centroid >= df.col_lo) & (df.col_centroid <= df.col_hi))
    print(f"centroid inside bbox (row=x, limits=[col_lo,row_lo,col_hi,row_hi]): {ok_ax.mean():.4f}")

    # true tile geotransform (Earth Search, scripts/search/cozar_tile_origins.py); fallback: robust estimate from centroids
    org = pd.read_csv(D / "tile_origins.csv")
    org = org[org.probe_item != "NOT_FOUND"].set_index("tile")
    df["epsg"] = [int(org.epsg[t]) if t in org.index else tile_epsg(t, la) for t, la in zip(df.tile, df.lat_centroid)]
    df["ul_x"] = df.tile.map(org.ul_x)
    df["ul_y"] = df.tile.map(org.ul_y)
    df["georef"] = np.where(df.ul_x.notna(), "stac_transform", "estimated_from_centroids")
    for ep, g in df.groupby("epsg"):
        tr = Transformer.from_crs(4326, ep, always_xy=True)
        e, n = tr.transform(g.lon_centroid.values, g.lat_centroid.values)
        est_x = pd.Series(e - (g.col_centroid.values + 0.5) * 10, index=g.index).groupby(g.tile).transform("median")
        est_y = pd.Series(n + (g.row_centroid.values + 0.5) * 10, index=g.index).groupby(g.tile).transform("median")
        df.loc[g.index, "ul_x"] = df.loc[g.index, "ul_x"].fillna(est_x.round(-1))
        df.loc[g.index, "ul_y"] = df.loc[g.index, "ul_y"].fillna(est_y.round(-1))
        ex = df.loc[g.index, "ul_x"] + (g.col_centroid + 0.5) * 10
        ny = df.loc[g.index, "ul_y"] - (g.row_centroid + 0.5) * 10
        df.loc[g.index, "origin_resid_m"] = np.hypot(ex - e, ny - n)
    print("lat/lon centroid vs pixel-centroid position, m: median %.0f p90 %.0f p99 %.0f" % (
        df.origin_resid_m.median(), df.origin_resid_m.quantile(.9), df.origin_resid_m.quantile(.99)))
    polys = []
    for ep, g in df.groupby("epsg"):
        tr = Transformer.from_crs(ep, 4326, always_xy=True)
        e0 = g.ul_x + g.col_lo * 10
        e1 = g.ul_x + (g.col_hi + 1) * 10
        n0 = g.ul_y - (g.row_hi + 1) * 10
        n1 = g.ul_y - g.row_lo * 10
        for i, a, b, c, d in zip(g.index, e0, e1, n0, n1):
            xs, ys = tr.transform([a, b, b, a, a], [d, d, c, c, d])
            polys.append((i, "POLYGON((" + ",".join(f"{x:.6f} {y:.6f}" for x, y in zip(xs, ys)) + "))"))
        ce, cn = g.ul_x + (g.col_centroid + 0.5) * 10, g.ul_y - (g.row_centroid + 0.5) * 10
        lo, la = tr.transform(ce.values, cn.values)
        df.loc[g.index, "lon_pix"], df.loc[g.index, "lat_pix"] = lo, la
    df["bbox_wkt"] = pd.Series(dict(polys))
    df["bbox_len_m"] = np.hypot((df.col_hi - df.col_lo + 1) * 10, (df.row_hi - df.row_lo + 1) * 10)
    df["area_m2"] = df["n_pixels_fil"] * 100.0

    # leakage / overlap with MARIDA, MADOS and our own scenes
    mar = scan_marida(ROOT / "data" / "MARIDA", "MARIDA")
    mad = scan_mados(ROOT / "data" / "MADOS", "MADOS")
    ours = []
    cand = pd.read_csv(ROOT / "data" / "pairs" / "candidates.csv", low_memory=False)
    c = cand[cand["tile"].notna() & cand["scene_datetime"].notna()]
    for t, s in zip(c["tile"], c["scene_datetime"]):
        ours.append((str(t).lstrip("T"), str(s)[:10], "pairs"))
    for sj in (ROOT / "data" / "live").glob("*/*/scene.json"):
        import json
        j = json.loads(sj.read_text(encoding="utf-8"))
        t = str(j.get("tile") or j.get("mgrs_tile") or "").lstrip("T")
        ours.append((t, sj.parent.name, "live:" + sj.parent.parent.name))
    ours = pd.DataFrame(ours, columns=["tile", "date", "src"]).drop_duplicates()
    key = df.tile + "_" + df.date
    mk = dict(zip(mar.tile + "_" + mar.date, mar.splits))
    dk = dict(zip(mad.tile + "_" + mad.date, mad.splits))
    ok_ = dict(zip(ours.tile + "_" + ours.date, ours.src))
    df["marida_same_scene"] = key.map(mk).fillna("")
    df["mados_same_scene"] = key.map(dk).fillna("")
    df["ours_same_scene"] = key.map(ok_).fillna("")
    df["marida_same_tile"] = df.tile.isin(set(mar.tile))
    df["mados_same_tile"] = df.tile.isin(set(mad.tile))
    df["ours_same_tile"] = df.tile.isin(set(ours.tile))
    print("MARIDA scenes", len(mar), "tiles", mar.tile.nunique(), "| MADOS scenes", len(mad), "tiles", mad.tile.nunique(),
          "| ours scenes", len(ours))

    df["level"] = "B"
    df["level_reason"] = LEVEL_REASON
    df["source"] = "Cozar2024_WASP_LW_Med (Zenodo 11045944, fil_idx)"
    df["license"] = LICENSE
    df["scene_id"] = df.s2_product.str.replace(".SAFE", "", regex=False)
    cols = ["source", "fil_idx", "scene_id", "mission", "tile", "date", "datetime_utc", "rel_orbit", "baseline",
            "lat_centroid", "lon_centroid", "lat_pix", "lon_pix", "row_centroid", "col_centroid", "col_lo", "row_lo", "col_hi", "row_hi", "epsg", "ul_x", "ul_y", "georef",
            "bbox_wkt", "bbox_len_m", "n_pixels_fil", "area_m2", "level", "level_reason", "license",
            "marida_same_scene", "mados_same_scene", "ours_same_scene", "marida_same_tile", "mados_same_tile",
            "ours_same_tile", "origin_resid_m"]
    reg = df[cols]
    out = ROOT / "data" / "extra" / "registry_cozar2024.csv"
    reg.to_csv(out, index=False, float_format="%.6f")
    (ROOT / "reports" / "extra_data").mkdir(parents=True, exist_ok=True)
    shutil.copy(out, ROOT / "reports" / "extra_data" / "registry_cozar2024.csv")

    sc = df.groupby(["scene_id", "tile", "date"]).agg(n_fil=("fil_idx", "size"), px=("n_pixels_fil", "sum"),
                                                     max_px=("n_pixels_fil", "max"),
                                                     marida=("marida_same_scene", "first"), mados=("mados_same_scene", "first"),
                                                     ours=("ours_same_scene", "first"), marida_tile=("marida_same_tile", "first"),
                                                     mados_tile=("mados_same_tile", "first")).reset_index()
    sc.to_csv(D / "scene_overlap.csv", index=False)
    print(f"filaments {len(df)}; scenes {len(sc)}; tiles {df.tile.nunique()}; dates {df.date.min()}..{df.date.max()}")
    print(f"pixels total {df.n_pixels_fil.sum()} -> area {df.area_m2.sum()/1e6:.3f} km2 (paper: 94.5 km2)")
    print("n_pixels quantiles", df.n_pixels_fil.quantile([.05, .25, .5, .75, .95, 1]).to_dict())
    print("same scene MARIDA", (df.marida_same_scene != "").sum(), "MADOS", (df.mados_same_scene != "").sum(),
          "ours", (df.ours_same_scene != "").sum())
    print("same tile (other date) MARIDA tiles:", sorted(set(df.tile) & set(mar.tile)), "MADOS tiles:", sorted(set(df.tile) & set(mad.tile)),
          "ours tiles:", sorted(set(df.tile) & set(ours.tile)))
    print("by mission", df.mission.value_counts().to_dict(), "by year", df.date.str[:4].value_counts().sort_index().to_dict())


if __name__ == "__main__":
    main()
