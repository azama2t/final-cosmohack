"""Find actual Sentinel-2 acquisitions at ADIS segments via Earth Search STAC."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "adis"
OUT = ROOT / "results"
STAC = "https://earth-search.aws.element84.com/v1/search"


def dt(value: str) -> datetime:
    return datetime.strptime(value[:19], "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    segments = list(csv.DictReader((RAW / "Segments.csv").open(newline="", encoding="utf-8-sig")))
    # Productive, georeferenceable campaigns in the Mediterranean. Run all positives,
    # not only selected successes, so the search audit has a denominator.
    targets = [s for s in segments if s["Ship"] in {"Dallaporta", "Gaia Blu", "R/V SOCIB"}
               and float(s["n_objects>50cm"] or 0) > 0]
    session = requests.Session()
    matches = []
    errors = []
    for index, s in enumerate(targets, 1):
        when = dt(s["timestamp"])
        lon, lat = float(s["Longitude"]), float(s["Latitude"])
        body = {
            "collections": ["sentinel-2-l2a"],
            "intersects": {"type": "Point", "coordinates": [lon, lat]},
            "datetime": f"{(when - timedelta(days=5)).isoformat().replace('+00:00','Z')}/{(when + timedelta(days=5)).isoformat().replace('+00:00','Z')}",
            "limit": 100,
        }
        try:
            resp = session.post(STAC, json=body, timeout=45)
            resp.raise_for_status()
            feats = resp.json()["features"]
            for feat in feats:
                p = feat["properties"]
                acquired = datetime.fromisoformat(p["datetime"].replace("Z", "+00:00"))
                matches.append({
                    "SegmentID": s["SegmentID"], "ship": s["Ship"],
                    "field_datetime": when.isoformat(), "latitude": lat, "longitude": lon,
                    "n_objects_gt50cm": s["n_objects>50cm"], "area_scanned_km2": s["area_scanned_km2"],
                    "dhat_50cm_calibrated": s["dhat_50cm_calibrated"],
                    "scene_id": feat["id"], "acquisition_datetime": acquired.isoformat(),
                    "delta_hours": round((acquired - when).total_seconds()/3600, 3),
                    "cloud_cover_percent": p.get("eo:cloud_cover"),
                    "stac_item": feat.get("links", [{}])[0].get("href", ""),
                    "catalog": STAC,
                })
            print(f"{index}/{len(targets)} {s['SegmentID']}: {len(feats)} scenes", flush=True)
        except Exception as exc:
            errors.append({"SegmentID": s["SegmentID"], "error": str(exc)})
    with (OUT / "adis_sentinel_matches.csv").open("w", newline="") as out:
        if matches:
            writer = csv.DictWriter(out, matches[0].keys());writer.writeheader();writer.writerows(matches)
    (OUT / "adis_sentinel_search_audit.json").write_text(json.dumps({"queried": len(targets), "matched_segments": len({x['SegmentID'] for x in matches}), "errors": errors}, indent=2))
    print("matches", len(matches), "segments", len({x['SegmentID'] for x in matches}), "errors", len(errors))


if __name__ == "__main__":
    main()
