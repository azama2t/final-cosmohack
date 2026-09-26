"""L115: summary tables from out/l115/plp/<date>/meta.json (scripts/count_bridge/plp_bridge_s2.py).

  .venv/Scripts/python.exe scripts/count_bridge/plp_bridge_summary.py   -> out/l115/plp_bridge_summary.json + stdout (markdown)

Item counts (published, verified in the papers):
  PLP2018: 3600 PET 1.5 L bottles in a 10x10 m target (36 /m2), 138 LDPE bags in a 10x10 m target (1.38 /m2)
           (Topouzelis et al. 2019, JAG 79:175, sect. 2.1);
  PLP2019: 416 PET bottles per 5x5 m target at 100 % coverage (16.64 /m2); bag count not published
           (Topouzelis et al. 2020, Remote Sens. 12:2013, sect. 2.1).
PLP2019 bottles per S2 pixel = drone bottle fraction * 100 m2 * 16.64 /m2 -- an ESTIMATE (packing density x area),
not a per-item count on the drone image.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PLP = ROOT / "out" / "l115" / "plp"
BOTTLES_PER_M2_2019 = 416 / 25.0
STATE_2021 = {  # ancillary_data_log.pdf, Zenodo 7085112 (state of targets)
    "20210611": "floating", "20210621": "floating", "20210626": "floating", "20210701": "floating",
    "20210706": "floating", "20210711": "floating", "20210716": "floating", "20210721": "floating",
    "20210726": "floating", "20210731": "floating", "20210805": "floating", "20210810": "floating",
    "20210815": "submerged", "20210820": "submerged", "20210825": "part sub", "20210830": "part sub",
    "20210904": "mix floating", "20210909": "mix part sub", "20210914": "mix mostly sub",
    "20210919": "mix mostly sub", "20210924": "mostly sub", "20211004": "mostly sub"}


def _mx(v):
    v = [x for x in v if x is not None and np.isfinite(x)]
    return round(float(max(v)), 4) if v else None


def main():
    out = dict(dates=[], plp2019_targets=[], plp2021_22_targets=[])
    L = []
    for d in sorted(PLP.iterdir()):
        mp = d / "meta.json"
        if not mp.exists():
            continue
        m = json.loads(mp.read_text(encoding="utf-8"))
        if m.get("status") != "ok":
            out["dates"].append(dict(date=d.name, status=m.get("status")))
            continue
        thr = m["threshold"]
        det = m["detector"]
        rec = dict(date=d.name, scene_id=m["scene_id"], source=m.get("source"), baseline=m.get("processing_baseline"),
                   scene_datetime=m["scene_datetime"], sun_zenith=m["sun_zenith"], water_b3=m["water_b3"],
                   evaluable=m["evaluable"], decision=m["decision"], reason=m["reason"],
                   n_det_crop=det["n_det_crop"], det_px_water=m["det_px_water"], water_px=m["water_px"],
                   near=m.get("near_targets"), shift=m.get("shift_best"), n_target_px=m["n_target_px"])
        T = m.get("targets") or []
        if T:
            p = np.array([t["p"] if t["p"] is not None else np.nan for t in T], float)
            fr = np.array([t["frac_plastic"] for t in T], float)
            rec.update(target_px=len(T), plastic_m2=round(float(fr.sum() * 100), 1), frac_max=round(float(fr.max()), 2),
                       p_max_target=_mx(list(p)), n_px_ge_thr=int((p >= thr).sum()),
                       fdi_contrast_max=_mx([t["fdi_contrast"] for t in T]))
        out["dates"].append(rec)
        year = d.name[:4]
        if year == "2019":
            groups = {}
            for t in T:
                groups.setdefault(t["target"], []).append(t)
            for g, tt in sorted(groups.items()):
                fb = sum(t["frac_bottles"] for t in tt)
                fbag = sum(t["frac_bags"] for t in tt)
                ps = [t["p"] for t in tt if t["p"] is not None]
                out["plp2019_targets"].append(dict(
                    date=d.name, target=g, n_px=len(tt), bottles_m2=round(fb * 100, 1), bags_m2=round(fbag * 100, 1),
                    bottles_est=int(round(fb * 100 * BOTTLES_PER_M2_2019)), frac_max=round(max(t["frac_plastic"] for t in tt), 2),
                    p_max=round(max(ps), 4) if ps else None, n_ge_thr=int(sum(x >= thr for x in ps)),
                    fdi_contrast_max=_mx([t["fdi_contrast"] for t in tt]),
                    evaluable=m["evaluable"]))
        elif year in ("2021", "2022"):
            groups = {}
            for t in T:
                groups.setdefault(t["material"], []).append(t)
            for g, tt in sorted(groups.items()):
                ps = [t["p"] for t in tt if t["p"] is not None]
                fr = [max(t["frac_plastic"], t.get("frac_wood") or 0) for t in tt]
                out["plp2021_22_targets"].append(dict(
                    date=d.name, material=g, n_px=len(tt), area_m2=round(sum(fr) * 100, 1), frac_max=round(max(fr), 2),
                    p_max=round(max(ps), 4) if ps else None, n_ge_thr=int(sum(x >= thr for x in ps)),
                    fdi_contrast_max=_mx([t["fdi_contrast"] for t in tt]),
                    state=STATE_2021.get(d.name, ""), note=";".join(sorted({str(t.get("note") or "") for t in tt} - {"", "nan"})),
                    evaluable=m["evaluable"]))
    (ROOT / "out" / "l115" / "plp_bridge_summary.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str),
                                                                    encoding="utf-8")
    thr = 0.63
    print("| дата | снимок | зенит | B3 воды | оценивается | пикс. мишени | пластик, м² (дрон) | max доля | P max на мишени | пикс. ≥ порога | FDI-контраст max | объектов в вырезке |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in out["dates"]:
        if "scene_id" not in r:
            print(f"| {r['date']} | {r.get('status')} |")
            continue
        print(f"| {r['date']} | {r['scene_id']} | {r['sun_zenith']} | {r['water_b3']} | {'да' if r['evaluable'] else 'нет'} | "
              f"{r.get('target_px', '—')} | {r.get('plastic_m2', '—')} | {r.get('frac_max', '—')} | {r.get('p_max_target', '—')} | "
              f"{r.get('n_px_ge_thr', '—')} | {r.get('fdi_contrast_max', '—')} | {r['n_det_crop']} |")
    print()
    print("| дата | мишень | пикс. | бутылки, м² | пакеты, м² | бутылок (оценка) | max доля | P max | ≥ порога | FDI-контраст max |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in out["plp2019_targets"]:
        print(f"| {r['date']} | {r['target']} | {r['n_px']} | {r['bottles_m2']} | {r['bags_m2']} | {r['bottles_est']} | "
              f"{r['frac_max']} | {r['p_max']} | {r['n_ge_thr']} | {r['fdi_contrast_max']} |")
    print()
    for r in out["dates"]:
        if r.get("near") or r.get("shift"):
            print(r["date"], "shift:", r.get("shift"), "near:", json.dumps(r.get("near"))[:400])


if __name__ == "__main__":
    main()
