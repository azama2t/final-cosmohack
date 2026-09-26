"""S2 L2A рядом с точечными полевыми событиями (Earth Search + Planetary Computer, src/macroplastic/live/stac.py).
Вход CSV: id,date (YYYY-MM-DD),t_utc_h (часы UTC от начала даты),lat,lon. Выход CSV: id,source,item,tile,dt_h,cloud.
  .venv/Scripts/python.exe -X utf8 scripts/search/qpairs/stac_check.py <in.csv> <out.csv> [окно_сут=1]"""
import csv
import datetime as dt
import sys

from _common import ROOT

sys.path.insert(0, str(ROOT / "src"))
from macroplastic.live import stac  # noqa: E402

KEYS = ["id", "source", "item", "tile", "dt_h", "cloud"]


def check(rows, days=1.0, log=print):
    out = []
    for r in rows:
        t0 = dt.datetime.fromisoformat(r["date"]) + dt.timedelta(hours=float(r["t_utc_h"]))
        win = f"{(t0 - dt.timedelta(days=days)).isoformat()}Z/{(t0 + dt.timedelta(days=days)).isoformat()}Z"
        for src in ("earth-search", "planetary-computer"):
            try:
                its = stac.search(src, float(r["lon"]), float(r["lat"]), win)
            except Exception as e:  # noqa: BLE001
                out.append(dict(id=r["id"], source=src, item="ERROR " + str(e)[:80]))
                continue
            if not its:
                out.append(dict(id=r["id"], source=src, item=""))
                continue
            for it in its:
                ti = dt.datetime.fromisoformat(it.properties["datetime"].replace("Z", "+00:00")).replace(tzinfo=None)
                out.append(dict(id=r["id"], source=src, item=it.id, tile=stac.tile_of(it),
                                dt_h=round((ti - t0).total_seconds() / 3600, 2), cloud=it.properties.get("eo:cloud_cover")))
        log(r["id"], [o["item"] for o in out if o["id"] == r["id"] and o.get("item")][:4])
    return out


def write(out, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, KEYS)
        w.writeheader()
        for o in out:
            w.writerow({k: o.get(k, "") for k in KEYS})


if __name__ == "__main__":
    rows = list(csv.DictReader(open(sys.argv[1], encoding="utf-8")))
    write(check(rows, float(sys.argv[3]) if len(sys.argv) > 3 else 1.0), sys.argv[2])
