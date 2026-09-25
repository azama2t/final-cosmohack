"""True S2 tile geotransforms (10 m) for every tile of the Cózar 2024 catalogue, via Earth Search sentinel-2-l1c.
One STAC query per tile (the date + centroid of one filament of that tile), 4 parallel requests, cached CSV.
Output: data/extra/cozar2024/tile_origins.csv (tile, epsg, ul_x, ul_y, probe_item, probe_date)
"""
from __future__ import annotations
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pandas as pd
from pystac_client import Client

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data" / "extra" / "cozar2024"
OUT = D / "tile_origins.csv"


def probe(row):
    c = Client.open("https://earth-search.aws.element84.com/v1")
    for coll in ("sentinel-2-l1c", "sentinel-2-l2a"):
        for dt in (row.date, "2019-01-01/2021-12-31"):
            try:
                its = list(c.search(collections=[coll], intersects=dict(type="Point", coordinates=[row.lon_centroid, row.lat_centroid]),
                                    datetime=dt, max_items=40).items())
            except Exception as e:  # noqa
                continue
            for it in its:
                if f"_{row.tile}_" in it.id:
                    tr = it.assets["red"].extra_fields.get("proj:transform")
                    ep = it.properties.get("proj:epsg") or it.properties.get("proj:code")
                    if tr:
                        return dict(tile=row.tile, epsg=int(str(ep).split(":")[-1]), ul_x=tr[2], ul_y=tr[5], probe_item=it.id, probe_date=row.date)
    return dict(tile=row.tile, epsg=None, ul_x=None, ul_y=None, probe_item="NOT_FOUND", probe_date=row.date)


def main():
    df = pd.read_csv(D / "filaments_meta.csv")
    df["tile"] = df.s2_product.str.extract(r"_T(\d{2}[A-Z]{3})_")
    df["date"] = df.s2_product.str.extract(r"_(\d{8})T")[0].map(lambda s: f"{s[:4]}-{s[4:6]}-{s[6:]}")
    done = pd.read_csv(OUT) if OUT.exists() else pd.DataFrame(columns=["tile"])
    done = done[done.probe_item != "NOT_FOUND"] if len(done) else done
    todo = df.drop_duplicates("tile")
    todo = todo[~todo.tile.isin(set(done.tile))]
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(probe, todo.itertuples()))
    out = pd.concat([done, pd.DataFrame(res)], ignore_index=True)
    out.to_csv(OUT, index=False)
    print(len(out), "tiles;", (out.probe_item == "NOT_FOUND").sum(), "not found")


if __name__ == "__main__":
    main()
