"""L86 — build data/search/s3/candidates.csv (one row per event x scene, plus one row per detector object in a drift zone).

  .venv/Scripts/python.exe scripts/search/s3/s3_candidates.py

Columns: event_id, scene_id, sensor, dt_h, drift_zone, usable_frac, level, reason (+ helper columns).
Levels (docs/INDEX.md): A photo<->field number linked by time/place/area/category; B confirmed satellite label; C candidate for manual
review; D rejected / checked negative. Visual verdicts for zone objects are in REVIEW below (AI-agent review of chips/*.png).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data" / "search" / "s3"

# visual review of zone detections (chips/<zone>/det_XX.png); filled after looking at every chip
# (AI-agent visual review 26.09 01:20 of chips/<zone>/contact.png; not an expert label -> D here means "rejected candidate",
#  it is NOT added to detector training as a verified D without a human check)
_Z3 = "S3_HE460_MarLitter_transect03__S2A_MSIL2A_20160411T105022_R051_T32ULF_20210211T031140"
_WC = ("whitecap/foam: white in RGB, 1 px, dozens of equally bright FDI dots around that the detector did not flag, "
       "ERA5 wind 8.1 m/s (whitecaps expected above ~4-5 m/s)")
REVIEW: dict[tuple[str, int], tuple[str, str]] = {(_Z3, k): ("D", _WC) for k in range(1, 14)}
REVIEW[(_Z3, 10)] = ("D", "vessel/wake or whitecap: 2 px next to a very bright SWIR object (small vessel) 250 m away; " + _WC)


def sensor(scene: str) -> str:
    return {"LC08": "Landsat 8 OLI", "LE07": "Landsat 7 ETM+ (SLC-off)", "S2A_": "Sentinel-2A MSI", "S2B_": "Sentinel-2B MSI"}.get(scene[:4], scene[:4])


def main():
    zones = pd.read_csv(OUT / "drift" / "zones.csv")
    zones["t"] = pd.to_datetime(zones.scene_datetime, utc=True)
    rows = []

    def zone_of(eid, sdt):
        t = pd.Timestamp(sdt).tz_convert("UTC") if pd.Timestamp(sdt).tzinfo else pd.Timestamp(sdt, tz="UTC")
        z = zones[(zones.event_id == eid) & ((zones.t - t).abs() < pd.Timedelta(minutes=1))]
        if z.empty:
            return "", None
        z = z.iloc[0]
        return (f"{z.zone_area_km2:.0f} km2; centroid shift {z.centroid_shift_km:.1f} km; particles p50/p90 "
                f"{z.dist_from_track_km_p50:.1f}/{z.dist_from_track_km_p90:.1f} km from track; wind {z.wind_ms_mean} m/s"), z

    for m in sorted((OUT / "pairs").glob("*/meta.json")):
        d = json.loads(m.read_text(encoding="utf-8"))
        q = d.get("quality") or {}
        dz, z = zone_of(d["event_id"], d.get("scene_datetime") or "1970-01-01T00:00:00Z")
        det = d.get("detector") or {}
        fl = d.get("fdi_like") or {}
        if d["decision"] == "reject":
            lvl, why = "D", (f"strip not usable: {d['reason']} (cloud {q.get('cloud_frac')}, coverage {q.get('coverage')}); "
                             f"scene cloud {d.get('scene_cloud_cover')} %")
        elif d["decision"] == "accept":
            n = det.get("n_det", fl.get("n_outlier_comp_strip"))
            lvl = "D"
            why = (f"strip usable, detector objects in strip: {n} (P max {det.get('prob_max')}); |dt| {abs(d['dt_h']):.1f} h -> "
                   f"surveyed water drifted {z.dist_from_track_km_p50 if z is not None else '?'} km (p50) from the strip; "
                   f"field {d.get('field_items_km2')} items/km2 of >2 cm items = cover ~1e-5 of a pixel -> 0 expected")
        else:
            lvl, why = "", f"error: {d.get('reason')}"
        rows.append(dict(event_id=d["event_id"], scene_id=d.get("scene_id"), sensor=sensor(d.get("scene_id", "")),
                         dt_h=d["dt_h"], drift_zone=dz, usable_frac=q.get("valid_water_frac"), level=lvl, reason=why,
                         kind="strip", field_items_km2=d.get("field_items_km2"), n_det_strip=det.get("n_det"),
                         prob_max_strip=det.get("prob_max"), scene_cloud_cover=d.get("scene_cloud_cover"),
                         image=f"pairs/{m.parent.name}/panel.png"))
    # synchronous scenes that miss the transect (footprint), from the inventory
    inv = pd.read_csv(OUT / "inventory.csv")
    miss = inv[(inv.role == "optical") & (inv.catalog == "planetary-computer") & (inv.line_in_footprint < 0.3) &
               (inv.dt_h.abs() <= 24)].sort_values("dt_h", key=abs).drop_duplicates(["event_id", "scene_id"])
    for r in miss.itertuples():
        dz, z = zone_of(r.event_id, r.scene_datetime)
        rows.append(dict(event_id=r.event_id, scene_id=r.scene_id, sensor=sensor(r.scene_id), dt_h=r.dt_h, drift_zone=dz,
                         usable_frac=0.0, level="D",
                         reason=(f"transect outside the scene footprint ({r.line_in_footprint:.0%} of the line inside, "
                                 f"~{r.dist_deg * 64:.0f}-{r.dist_deg * 111:.0f} km off); drift zone p90 "
                                 f"{z.dist_from_track_km_p90 if z is not None else '?'} km does not reach it"),
                         kind="footprint_miss", scene_cloud_cover=r.cloud_cover))
    # detector objects inside scanned drift zones
    dp = OUT / "chips" / "detections.csv"
    if dp.exists():
        for r in pd.read_csv(dp).itertuples():
            meta = json.loads((OUT / "zones" / r.zone / "meta.json").read_text(encoding="utf-8"))
            dz = meta["zone"]
            lvl, why = REVIEW.get((r.zone, r.det), ("C", "not reviewed yet"))
            rows.append(dict(event_id=r.event_id, scene_id=f"{r.scene_id}#det{r.det:02d}", sensor=sensor(r.scene_id),
                             dt_h=float(dz["dt_mid_h"]),
                             drift_zone=f"inside zone ({dz['zone_area_km2']:.0f} km2), {r.dist_track_km} km from track",
                             usable_frac=meta["quality"]["valid_water_frac"], level=lvl,
                             reason=f"{why}; {r.px} px, P {r.prob_max}, FDI {r.fdi_center} vs chip median {r.fdi_chip_water_p50}; "
                                    f"lat {r.lat} lon {r.lon}",
                             kind="zone_object", image=r.chip))
    c = pd.DataFrame(rows)
    c.to_csv(OUT / "candidates.csv", index=False)
    print(c.groupby(["kind", "level"]).size())


if __name__ == "__main__":
    main()
