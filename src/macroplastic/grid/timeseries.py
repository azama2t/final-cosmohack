"""Per-region timeseries rows (docs/CONTRACTS.md, timeseries.json)."""
from __future__ import annotations

import numpy as np


def mean_index(stats: list[dict]) -> float | None:
    """Mean share_permille over cells with a defined index (observed_frac >= 0.5); None if no such cell."""
    vals = [s["share_permille"] for s in stats if s["share_permille"] is not None]
    return round(float(np.mean(vals)), 4) if vals else None


def timeseries_row(date: str, model: str, detections: list[dict], stats: list[dict], cloud_frac: float | None) -> dict:
    return {"date": date, "model": model,
            "total_debris_area_m2": round(float(sum(d["props"]["area_m2"] for d in detections)), 1),
            "mean_index": mean_index(stats), "n_detections": len(detections),
            "cloud_frac": None if cloud_frac is None else round(float(cloud_frac), 4),
            "n_cells_valid": sum(1 for s in stats if s["share_permille"] is not None)}


def sort_rows(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (r["date"], r["model"]))
