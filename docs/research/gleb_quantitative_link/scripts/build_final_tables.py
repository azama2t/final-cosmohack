"""Build compact, auditable tables from downloaded source and scene records."""
import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def write_csv(path, rows, fields):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    with (RESULTS / "adis_pairs/pair_summary.csv").open(newline="") as stream:
        pairs = list(csv.DictReader(stream))
    examples = []
    for pair in pairs:
        examples.append({
            "source": "ADIS / The Ocean Cleanup",
            "segment_id": pair["segment_id"],
            "scene_id": pair["scene_id"],
            "field_datetime_utc": pair["field_datetime"],
            "satellite_datetime_utc": pair["acquisition_datetime"],
            "delta_hours": pair["delta_hours"],
            "observed_items_gt50cm": pair["n_objects_gt50cm"],
            "surveyed_area_km2": pair["area_scanned_km2"],
            "raw_items_km2": pair["raw_density_items_km2"],
            "adis_calibrated_items_km2": pair["calibrated_density_items_km2"],
            "survey_route_inside_crop_fraction": pair["route_inside_crop_fraction"],
            "water_scl6_fraction_on_route": pair["route_water_scl6_fraction"],
            "modeled_drift_km": pair["drift_model_displacement_km"],
            "drift_search_radius_km": pair["drift_search_radius_km"],
            "georeferenced_image": pair["raster"],
            "annotated_preview": pair["verified_preview"],
            "survey_geometry": "results/adis_pairs/selected_routes.geojson",
            "stac_item": pair["stac_item"],
            "interpretation": (
                "Cloud/cirrus over entire route; unsuitable for optical training"
                if pair["segment_id"] == "335997" else
                "Relatively clear; counted objects are below a 10 m pixel"
                if pair["segment_id"] == "335694" else
                "Scene has glint, haze, or stripe artifacts; count is route-level, not pixel-level"
            ),
        })
    fields = list(examples[0])
    write_csv(RESULTS / "concrete_examples.csv", examples, fields)

    verdicts = [
        {"source": "ADIS / The Ocean Cleanup", "verdict": "A", "quantity_geometry": "Individual validated sightings, segment count, timestamp, GPS survey route, scan width and surveyed area", "actual_image": "Object snippets; six same-day 10 m Sentinel-2 crops downloaded", "limit": "Object snippets are not full surveyed camera frames; individual objects are subpixel in Sentinel-2; use route density as weak label or obtain full calibrated imagery", "primary_url": "https://data.4tu.nl/datasets/ddede7f5-aca5-42ae-b851-e0bbb9a2c4c2"},
        {"source": "Ruiz et al. 2020 public supplement", "verdict": "D", "quantity_geometry": "Paper reports aggregate catch and sampling design", "actual_image": "Regional Sentinel-2/Landsat scenes exist, but no tow-level date/coordinate/count table in retrieved supplementary file", "limit": "Cannot form verified observation-scene pairs from downloaded public material", "primary_url": "https://doi.org/10.3389/fmars.2020.00308"},
        {"source": "PLP2019", "verdict": "C", "quantity_geometry": "63 exact-match labeled Sentinel-2 pixels with 0-55% plastic areal fraction", "actual_image": "Five dated ACOLITE Sentinel-2 NetCDF scenes, UAS photos, original L1C product IDs", "limit": "Areal fraction is not item count; simple NIR/FDI relation fails leave-one-date-out transfer", "primary_url": "https://zenodo.org/records/3752719"},
        {"source": "PLP2022/23 tested subset", "verdict": "C", "quantity_geometry": "Controlled artificial targets with specified area in source study", "actual_image": "Two matching dated Sentinel-2 NetCDF scenes and two UAS images extracted from large archive", "limit": "Need complete target geometry and date-specific labels for training; artificial targets do not define natural item count", "primary_url": "https://zenodo.org/records/10046182"},
        {"source": "FML Dataset", "verdict": "B", "quantity_geometry": "17,156 individual boxes in 5,490 frames; no calibrated surveyed area", "actual_image": "Full 2.34 GB image/annotation archive, five sampled frame previews", "limit": "No GSD, GPS or camera geometry in inspected labels/EXIF; cannot convert frame counts to items/km2", "primary_url": "https://doi.org/10.17882/106148"},
    ]
    write_csv(RESULTS / "source_verdicts.csv", verdicts, list(verdicts[0]))

    inventory = json.loads((ROOT / "data/source_inventory.json").read_text())
    summary = {
        "downloaded_primary_files": len(inventory),
        "downloaded_primary_bytes": sum(item["bytes"] for item in inventory),
        "verified_adis_sentinel_pairs": len(examples),
        "pair_segments": [row["segment_id"] for row in examples],
        "source_verdicts": {row["source"]: row["verdict"] for row in verdicts},
    }
    (RESULTS / "study_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
