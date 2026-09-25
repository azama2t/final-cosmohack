"""L16: paired scene bootstrap of the MARIDA-val gain final (MARIDA+MADOS) vs backup (MARIDA only).

  .venv/Scripts/python.exe scripts/experiments/l16_paired.py   (needs weights_exp/l16/val_pred_*.npz from l16_recheck)
"""
from __future__ import annotations

import numpy as np

import l16_common as C


def main():
    a = np.load(C.OUT / "val_pred_final.npz")
    b = np.load(C.OUT / "val_pred_backup_marida_only.npz")
    assert np.array_equal(a["y"], b["y"])
    names = C.split_names("val")
    scene = np.array([C.parse(n)["scene"] for n in names])[a["pid"]]
    t = a["y"] == 1
    pa, pb = a["prob"] >= 0.63, b["prob"] >= 0.37
    us = np.unique(scene)

    def cnt(pm):
        return np.array([C.confusion(t[scene == s], pm[scene == s])[:3] for s in us])

    ca, cb = cnt(pa), cnt(pb)
    f = lambda c: 2 * c[0] / (2 * c[0] + c[1] + c[2])
    rng = np.random.default_rng(0)
    d, fa_s = [], []
    for _ in range(5000):
        k = rng.integers(0, len(us), len(us))
        fa, fb = f(ca[k].sum(0)), f(cb[k].sum(0))
        d.append(fa - fb); fa_s.append(fa)
    d = np.array(d)
    per_scene = {s: {"n_md": int(ca[i][0] + ca[i][2]), "f1_final": round(f(ca[i]), 4) if ca[i][0] + ca[i][2] else None,
                     "f1_backup": round(f(cb[i]), 4) if cb[i][0] + cb[i][2] else None,
                     "fp_final": int(ca[i][1]), "fp_backup": int(cb[i][1])} for i, s in enumerate(us)}
    out = {"delta_point": round(f(ca.sum(0)) - f(cb.sum(0)), 4),
           "delta_ci95": [round(float(np.percentile(d, 2.5)), 4), round(float(np.percentile(d, 97.5)), 4)],
           "p_delta_le_0": round(float(np.mean(d <= 0)), 4),
           "final_f1_ci95": [round(float(np.percentile(fa_s, 2.5)), 4), round(float(np.percentile(fa_s, 97.5)), 4)],
           "per_scene": per_scene}
    C.dump(C.OUT / "paired.json", out)
    print(out)


if __name__ == "__main__":
    main()
