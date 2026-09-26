"""L117 P2: which share of REAL floating items can be resolved / counted at a given sensor GSD?

Data: ADIS Objects.csv (The Ocean Cleanup, 4TU, CC-BY-4.0): per item projected top-view bbox area (m2),
major/minor axis (m); classes 1 buoy, 2 fibrous, 3 hard plastic (0 animal, 4 plant excluded).
Bbox area is an UPPER bound of the true object area -> all shares below are optimistic.

Criteria per GSD g (m):
  fill_px    : item area / g^2 (sub-pixel fill fraction if < 1)
  countable  : minor axis >= 2 g AND area >= 4 g^2   (item spans >= 2x2 pixels: minimal for per-object detection)
  one_px     : area >= g^2 (item covers >= one full pixel)
  fill20     : area >= 0.2 g^2 (sub-pixel fill >= 20 %, lower bound of f used in QUANTITY.md scenario)
Densities: ADIS ΣN/ΣA over all 21 444 segments (>5 cm), per-segment p50/p90/p99/max of dhat_5cm, and published
windrow densities from our own registry (Ashmore Reef UAV fronts 560 and 3 450 items/km2, L105).
Required image area for a count with relative 95 % Poisson half-width <= 50 %: N_needed = 16 visible items
(1.96/sqrt(N) <= 0.5 -> N >= 15.4) -> A = 16 / (density * countable share).
Output: results/resolution_physics.json, results/resolution_physics.csv, results/resolution_countable.png
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
RES = ROOT / "docs/research/marine_quantity/results"
SENSORS = [  # name, gsd_m (panchromatic where available; nominal published values)
    ("UAV 10 m alt. (~0.003 m)", 0.003),
    ("aerial 0.1 m", 0.1),
    ("WorldView-3/Pleiades Neo pan 0.3 m", 0.3),
    ("SkySat/Pleiades pan 0.5 m", 0.5),
    ("1 m", 1.0),
    ("Pleiades MS 2 m", 2.0),
    ("PlanetScope 3 m", 3.0),
    ("Sentinel-2 10 m", 10.0),
    ("Landsat 30 m", 30.0),
]


def main():
    o = pd.read_csv(ROOT / "data/extra/field/adis/Objects.csv")
    n_all = len(o)
    o = o[o["class"].isin([1, 2, 3])].copy()
    s = pd.read_csv(ROOT / "data/extra/field/adis/Segments.csv")
    dens_all = s["n_objects>5cm"].sum() / s["area_scanned_km2"].sum()
    q = s["dhat_5cm"].quantile([0.5, 0.9, 0.99]).to_dict()
    dens = {"ADIS mean ΣN/ΣA (>5 cm)": dens_all, "ADIS segment p90": q[0.9], "ADIS segment p99": q[0.99],
            "ADIS segment max": s["dhat_5cm"].max(), "Ashmore Reef front (UAV, L105) low": 560.0,
            "Ashmore Reef front (UAV, L105) high": 3450.0}
    rows = []
    for name, g in SENSORS:
        area, amin = o["area"].values, o["amin"].values
        countable = ((amin >= 2 * g) & (area >= 4 * g * g)).mean()
        one_px = (area >= g * g).mean()
        fill20 = (area >= 0.2 * g * g).mean()
        med_fill = float(np.median(area / (g * g)))
        r = {"sensor": name, "gsd_m": g, "share_countable_2x2px": countable, "share_ge_1px": one_px,
             "share_fill_ge_20pct": fill20, "median_fill_fraction": med_fill,
             "max_fill_fraction": float((area / (g * g)).max())}
        for dn, dv in dens.items():
            vis = dv * countable
            r[f"visible_per_km2 | {dn}"] = vis
            r[f"km2_for_±50% | {dn}"] = (16 / vis) if vis > 0 else None
        rows.append(r)
    df = pd.DataFrame(rows)
    df.to_csv(RES / "resolution_physics.csv", index=False)
    size = {"n_objects_total": int(n_all), "n_plastic_classes_1_3": int(len(o)),
            "area_m2_quantiles": o["area"].quantile([0.1, 0.25, 0.5, 0.75, 0.9, 0.99]).round(4).to_dict(),
            "amaj_m_quantiles": o["amaj"].quantile([0.1, 0.5, 0.9, 0.99]).round(3).to_dict(),
            "amin_m_quantiles": o["amin"].quantile([0.1, 0.5, 0.9, 0.99]).round(3).to_dict(),
            "share_area_ge_1m2": float((o["area"] >= 1).mean()), "share_area_ge_20m2": float((o["area"] >= 20).mean())}
    out = {"size_distribution": size, "densities_items_per_km2": dens, "table": rows,
           "note": "bbox area = upper bound of object area; ADIS detects >5 cm from ship camera, so items <5 cm and "
                   "submerged items are absent -> shares are for the ADIS-visible population only"}
    (RES / "resolution_physics.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    pd.set_option("display.width", 250)
    print(json.dumps(size, indent=1))
    print(df[["sensor", "share_countable_2x2px", "share_ge_1px", "share_fill_ge_20pct", "median_fill_fraction",
              "km2_for_±50% | ADIS mean ΣN/ΣA (>5 cm)", "km2_for_±50% | Ashmore Reef front (UAV, L105) low"]].to_string())

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6.4, 3.6), dpi=110)
    x = df["gsd_m"].values
    ax.plot(x, df["share_countable_2x2px"] * 100, "o-", color="#1f5fa8", label="≥ 2×2 пикселя (можно считать поштучно)")
    ax.plot(x, df["share_fill_ge_20pct"] * 100, "s--", color="#c2571a", label="заполняет ≥ 20 % пикселя")
    ax.set_xscale("log")
    ax.set_xlabel("размер пикселя, м (лог)")
    ax.set_ylabel("% предметов ADIS (> 5 см)")
    for name, g in [("БПЛА", 0.003), ("WV-3", 0.3), ("PlanetScope", 3), ("S2", 10)]:
        ax.axvline(g, color="#999", lw=0.6)
        ax.text(g, 102, name, ha="center", fontsize=7, color="#555")
    ax.set_ylim(0, 108)
    ax.legend(fontsize=7, loc="center left")
    ax.set_title("Какая доля реальных предметов различима на снимке", fontsize=9)
    fig.tight_layout()
    fig.savefig(RES / "resolution_countable.png")


if __name__ == "__main__":
    main()
