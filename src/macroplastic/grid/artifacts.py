"""Linear artefacts and ships among detections (lane L37, reports/artifacts.md).

Objects are *marked*, not removed: a kept component gets `artifact` = "seam" | "wake" | "ship" (or None). Marked
objects stay in detections.geojson (the front may draw them muted), but are not counted in the H3 index
(flagged_water_px) nor in the survey-priority zones. Same rules and numbers for every scene.

Per component (8-connected, 10 m pixels) from second moments of the pixel centres:
    length_px    extent along the major axis (+1 px)
    width_px     extent along the minor axis (+1 px)
    thick_px     n_px / length_px  (mean thickness)
    elong        length_px / max(thick_px, 1)
    dev          max(width_px - thick_px - 1, 0) / length_px - how far the object bends away from its chord
                 (1 px rasterisation slack; 0 = straight line, ~0.1+ = visibly curved / irregular)
    azimuth_deg  direction of the major axis from grid north, east positive, folded to (-90, 90]
    step_rel     brightness step across the object: |median(vis(left) - vis(right))| / scene water median vis,
                 vis = mean(B2, B3, B4), sampled 3..6 px on both sides along the minor axis on observed,
                 unflagged water
    step_cons    share of the object's pixels whose left-right difference has the sign of the median
    obj_excess   median of (vis(object) - max(vis left, vis right)) / scene water median vis: a bright filament
                 (material on the water) is brighter than both sides, an edge response is not
    ship_frac    share of the object's pixels within SHIP_NEAR_PX = 3 px of a "ship blob": bright in visible and NIR
                 (B8 >= max(0.06, 4 x water median B8) and min(B2, B3, B4) >= max(0.04, 2 x its water median)), blob <= 150 px, >= 70 % of its 2..4 px ring is observed water
                 (so bright land / shore is not a ship)

Rules (first match wins):
    wake  ship_frac > 0 and length >= 500 m and elong >= 6 and dev <= 0.10  (bright point + straight tail)
    ship  ship_frac >= 0.5                                                 (halo / fringe of a bright target)
    wake  length >= 1000 m, thick <= 6 px, elong >= 15, dev <= 0.07     (long straight narrow strip, ship gone)
    seam  length >= 1500 m, thick <= 5 px, dev <= 0.05, |azimuth| <= 20 (straight line along the S2 track)
    seam  edge rule below (checked before the ship-free wake rule)
    seam  length >= 100 m, elong >= 4, dev <= 0.08, step_rel >= 0.15, step_cons >= 0.85
          and obj_excess <= 0.10 (object lies on a sharp water-brightness boundary along its whole length and is
          not brighter than the brighter side: detector seam or the edge of a turbid plume - the model reacts to
          the step, not to floating material)
    seam  short piece (2..30 px, < 100 m): the same step test across the direction of the smoothed
          (Gaussian 3 px, water only) brightness gradient, step_rel >= 0.25, step_cons >= 0.85, obj_excess <= 0.10
    seam  continuation: unmarked object (>= 2 px) whose centre lies <= 3 px from the axis of a "seam" object and
          <= 15 px beyond its end (dashed seam lines break into collinear pieces); applied twice
Curved or irregular objects (dev > 0.08) are never marked by the line rules; big irregular objects that merely
touch a bright target (ship_frac < 0.5, no straight tail) are not marked.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

EIGHT = np.ones((3, 3), bool)
PX_M = 10.0
MIN_PX_LINE = 6  # line rules only for components with >= 6 px

SHIP_B8, SHIP_VIS = 0.06, 0.04
SHIP_REL, SHIP_REL_VIS = 4.0, 2.0  # and >= 4 x / 2 x the scene water median (turbid rivers)
SHIP_MAX_PX, SHIP_RING_WATER = 150, 0.7
SHIP_NEAR_PX = 3
SHIP_FRAC = 0.5
JOIN_PERP_PX, JOIN_GAP_PX = 3.0, 15.0
WAKE_SHIP_LEN_M, WAKE_SHIP_ELONG, WAKE_SHIP_DEV = 500.0, 6.0, 0.10
WAKE_LEN_M, WAKE_THICK_PX, WAKE_ELONG, WAKE_DEV = 1000.0, 6.0, 15.0, 0.07
SEAM_LEN_M, SEAM_THICK_PX, SEAM_DEV, SEAM_AZ = 1500.0, 5.0, 0.05, 20.0
EDGE_LEN_M, EDGE_ELONG, EDGE_DEV, EDGE_STEP, EDGE_CONS = 100.0, 4.0, 0.08, 0.15, 0.85
EDGE_EXCESS = 0.10
SMALL_STEP, SMALL_SIGMA, SMALL_MAX_PX = 0.25, 3.0, 30
STEP_OFFSETS = (3, 4, 5, 6)

RULES_TEXT = {
    "ship": "у яркой малой цели (судно, платформа, буй, островок; B8 ≥ 0.06 и B2,B3,B4 ≥ 0.04, ≤ 150 px) — ≥ 50 % пикселей в ≤ 3 px",
    "wake": "прямая узкая полоса (кильватер): у яркой цели и ≥ 500 м, или ≥ 1 км без цели",
    "seam": "прямая линия по резкой границе яркости воды (шов детекторов / край мутного шлейфа) "
            "или ≥ 1,5 км вдоль трека S2",
}


def component_shape(labels: np.ndarray, n: int) -> dict[str, np.ndarray]:
    """Shape features of components 1..n (arrays of length n)."""
    out = {k: np.zeros(n) for k in ("n_px", "length_px", "width_px", "thick_px", "elong", "dev", "azimuth_deg",
                                    "cy", "cx", "nx", "ny")}
    if n == 0:
        return out
    ys, xs = np.nonzero(labels > 0)
    lab = labels[ys, xs]
    cnt = np.bincount(lab, minlength=n + 1)[1:].astype(float)
    cnt_s = np.maximum(cnt, 1)
    # east = +x (col), north = -y (row)
    e, no = xs.astype(float), -ys.astype(float)
    me = np.bincount(lab, e, n + 1)[1:] / cnt_s
    mn = np.bincount(lab, no, n + 1)[1:] / cnt_s
    de, dn = e - me[lab - 1], no - mn[lab - 1]
    cee = np.bincount(lab, de * de, n + 1)[1:] / cnt_s
    cnn = np.bincount(lab, dn * dn, n + 1)[1:] / cnt_s
    cen = np.bincount(lab, de * dn, n + 1)[1:] / cnt_s
    theta = 0.5 * np.arctan2(2 * cen, cee - cnn)  # major-axis angle from east, counter-clockwise
    ue, un = np.cos(theta), np.sin(theta)
    pa = de * ue[lab - 1] + dn * un[lab - 1]
    pm = -de * un[lab - 1] + dn * ue[lab - 1]
    big = 1e9
    amax = np.full(n + 1, -big); amin = np.full(n + 1, big)
    bmax = np.full(n + 1, -big); bmin = np.full(n + 1, big)
    np.maximum.at(amax, lab, pa); np.minimum.at(amin, lab, pa)
    np.maximum.at(bmax, lab, pm); np.minimum.at(bmin, lab, pm)
    length = amax[1:] - amin[1:] + 1
    width = bmax[1:] - bmin[1:] + 1
    thick = cnt / np.maximum(length, 1)
    az = np.degrees(np.arctan2(ue, un))  # from north, east positive
    az = (az + 90.0) % 180.0 - 90.0
    out.update(n_px=cnt, length_px=length, width_px=width, thick_px=thick, elong=length / np.maximum(thick, 1.0),
               dev=np.maximum(width - thick - 1.0, 0) / np.maximum(length, 1), azimuth_deg=az,
               cy=-mn, cx=me, nx=-un, ny=-ue)  # normal (minor axis) in (col, row) = (-un, -ue)
    return out


def ship_blobs(b2, b3, b4, b8, water: np.ndarray) -> np.ndarray:
    """Bright compact targets surrounded by water (ships, platforms, buoys)."""
    b8 = np.nan_to_num(b8)
    vmin = np.nan_to_num(np.minimum(np.minimum(b2, b3), b4))
    wb8 = b8[water]; wv = vmin[water]
    t8 = max(SHIP_B8, SHIP_REL * float(np.median(wb8))) if wb8.size else SHIP_B8
    tv = max(SHIP_VIS, SHIP_REL_VIS * float(np.median(wv))) if wv.size else SHIP_VIS
    bright = (b8 >= t8) & (vmin >= tv)
    if not bright.any():
        return bright
    lab, n = ndimage.label(bright, structure=EIGHT)
    size = np.bincount(lab.ravel(), minlength=n + 1)
    near = ndimage.binary_dilation(bright, EIGHT, iterations=4) & ~ndimage.binary_dilation(bright, EIGHT, iterations=1)
    # each ring pixel -> the nearest blob label (grey dilation of labels is enough for separated blobs)
    ring_lab = ndimage.grey_dilation(lab, footprint=np.ones((9, 9), bool))
    rl = ring_lab[near]
    tot = np.bincount(rl, minlength=n + 1)
    wat = np.bincount(rl, weights=water[near].astype(float), minlength=n + 1)
    ok = (size <= SHIP_MAX_PX) & (tot > 0) & (wat >= SHIP_RING_WATER * np.maximum(tot, 1))
    ok[0] = False
    return ok[lab]


def brightness_step(labels: np.ndarray, n: int, shp: dict, vis: np.ndarray, valid: np.ndarray,
                    water_vis_median: float, cand: np.ndarray, normal=None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """step_rel, step_cons, obj_excess for candidate components (others 0)."""
    step = np.zeros(n); cons = np.zeros(n); exc = np.zeros(n)
    if not cand.any() or not water_vis_median or not np.isfinite(water_vis_median):
        return step, cons, exc
    H, W = labels.shape
    keep = np.zeros(n + 1, bool); keep[1:] = cand
    ys, xs = np.nonzero(keep[labels])
    lab = labels[ys, xs]
    nxa, nya = (shp["nx"], shp["ny"]) if normal is None else normal
    nx, ny = nxa[lab - 1], nya[lab - 1]
    acc = {}
    for side, sgn in (("L", 1), ("R", -1)):
        s = np.zeros(len(ys)); c = np.zeros(len(ys))
        for d in STEP_OFFSETS:
            r = np.rint(ys + sgn * d * ny).astype(int); q = np.rint(xs + sgn * d * nx).astype(int)
            inb = (r >= 0) & (r < H) & (q >= 0) & (q < W)
            r, q = np.clip(r, 0, H - 1), np.clip(q, 0, W - 1)
            ok = inb & valid[r, q]
            v = vis[r, q]
            ok &= np.isfinite(v)
            s += np.where(ok, v, 0); c += ok
        acc[side] = (s, c)
    both = (acc["L"][1] > 0) & (acc["R"][1] > 0)
    mL = acc["L"][0] / np.maximum(acc["L"][1], 1); mR = acc["R"][0] / np.maximum(acc["R"][1], 1)
    diff = np.where(both, mL - mR, 0.0)
    over = np.where(both, vis[ys, xs] - np.maximum(mL, mR), 0.0)
    for k in np.flatnonzero(cand):
        m = (lab == k + 1) & both
        if m.sum() < 3:
            continue
        dk = diff[m]
        med = float(np.median(dk))
        step[k] = abs(med) / water_vis_median
        cons[k] = float(np.mean(np.sign(dk) == np.sign(med))) if med != 0 else 0.0
        exc[k] = float(np.nanmedian(over[m])) / water_vis_median
    return step, cons, exc


def classify(labels: np.ndarray, n: int, bands: dict | None, water: np.ndarray,
             flagged: np.ndarray | None = None) -> tuple[list, dict]:
    """Return (artifact per component: None | "seam" | "wake" | "ship", features dict of arrays)."""
    shp = component_shape(labels, n)
    art: list = [None] * n
    if n == 0:
        return art, shp
    L_m = shp["length_px"] * PX_M
    line = shp["n_px"] >= MIN_PX_LINE
    ship_frac = np.zeros(n)
    seam_small = np.zeros(n, bool)
    step = np.zeros(n); cons = np.zeros(n); exc = np.zeros(n)
    if bands is not None:
        b2, b3, b4, b8 = (bands[k].astype(np.float32) for k in ("B2", "B3", "B4", "B8"))
        ships = ship_blobs(b2, b3, b4, b8, water)
        shp["ship_px"] = int(ships.sum())
        if ships.any():
            zone = ndimage.binary_dilation(ships, EIGHT, iterations=SHIP_NEAR_PX)
            hit = np.bincount(labels[zone & (labels > 0)], minlength=n + 1)[1:]
            ship_frac = hit / np.maximum(shp["n_px"], 1)
        vis = (b2 + b3 + b4) / 3.0
        fl = (labels > 0) if flagged is None else (flagged | (labels > 0))
        valid = water & ~fl
        wv = vis[water & np.isfinite(vis)]
        wmed = float(np.median(wv)) if wv.size else 0.0
        cand = line & (L_m >= EDGE_LEN_M) & (shp["elong"] >= EDGE_ELONG) & (shp["dev"] <= EDGE_DEV)
        step, cons, exc = brightness_step(labels, n, shp, vis, valid, wmed, cand)
        # short pieces (dashed seam lines): normal = direction of the smoothed brightness gradient at the object
        small = (shp["n_px"] >= 2) & (shp["n_px"] <= SMALL_MAX_PX) & (L_m < EDGE_LEN_M) & ~cand
        if small.any():
            vv = np.where(valid & np.isfinite(vis), vis, 0.0)
            wsum = ndimage.gaussian_filter(valid.astype(np.float32), SMALL_SIGMA)
            vs = ndimage.gaussian_filter(vv.astype(np.float32), SMALL_SIGMA) / np.maximum(wsum, 1e-3)
            gy, gx = np.gradient(vs)
            lab_s = np.where(small[np.maximum(labels, 1) - 1] & (labels > 0), labels, 0)
            sx = np.bincount(lab_s.ravel(), gx.ravel(), n + 1)[1:]
            sy = np.bincount(lab_s.ravel(), gy.ravel(), n + 1)[1:]
            g = np.hypot(sx, sy)
            small &= g > 0
            nrm = (np.where(g > 0, sx / np.maximum(g, 1e-12), 0), np.where(g > 0, sy / np.maximum(g, 1e-12), 0))
            s2, c2, e2 = brightness_step(labels, n, shp, vis, valid, wmed, small, normal=nrm)
            step = np.where(small, s2, step); cons = np.where(small, c2, cons); exc = np.where(small, e2, exc)
            seam_small = small & (s2 >= SMALL_STEP) & (c2 >= EDGE_CONS) & (e2 <= EDGE_EXCESS)
    shp["step_rel"], shp["step_cons"], shp["obj_excess"], shp["ship_frac"] = step, cons, exc, ship_frac
    long_tail = (L_m >= WAKE_SHIP_LEN_M) & (shp["elong"] >= WAKE_SHIP_ELONG) & (shp["dev"] <= WAKE_SHIP_DEV)
    wake_free = line & (L_m >= WAKE_LEN_M) & (shp["thick_px"] <= WAKE_THICK_PX) & (shp["elong"] >= WAKE_ELONG) \
        & (shp["dev"] <= WAKE_DEV)
    seam_track = line & (L_m >= SEAM_LEN_M) & (shp["thick_px"] <= SEAM_THICK_PX) & (shp["dev"] <= SEAM_DEV) \
        & (np.abs(shp["azimuth_deg"]) <= SEAM_AZ)
    seam_edge = line & (L_m >= EDGE_LEN_M) & (shp["elong"] >= EDGE_ELONG) & (shp["dev"] <= EDGE_DEV) \
        & (step >= EDGE_STEP) & (cons >= EDGE_CONS) & (exc <= EDGE_EXCESS)
    for k in range(n):
        if ship_frac[k] > 0 and long_tail[k]:
            art[k] = "wake"
        elif ship_frac[k] >= SHIP_FRAC:
            art[k] = "ship"
        elif seam_track[k] or seam_edge[k] or seam_small[k]:
            art[k] = "seam"
        elif wake_free[k]:
            art[k] = "wake"
    _join_seams(art, shp)
    return art, shp


def _join_seams(art: list, shp: dict, rounds: int = 2) -> None:
    """Collinear continuation of seam objects (dashed seam lines)."""
    cy, cx = shp["cy"], shp["cx"]
    for _ in range(rounds):
        seams = [k for k, a in enumerate(art) if a == "seam"]
        free = np.array([k for k, a in enumerate(art) if a is None and shp["n_px"][k] >= 2], int)
        if not seams or free.size == 0:
            return
        added = False
        for s in seams:
            ue, un = -shp["ny"][s], -shp["nx"][s]  # major axis in (east, north): normal (nx, ny) = (-un, -ue)
            de, dn = cx[free] - cx[s], -(cy[free] - cy[s])
            along = de * ue + dn * un
            perp = np.abs(-de * un + dn * ue)
            hit = free[(perp <= JOIN_PERP_PX) & (np.abs(along) <= shp["length_px"][s] / 2 + JOIN_GAP_PX)]
            for k in hit:
                if art[k] is None:
                    art[k] = "seam"
                    added = True
        if not added:
            return


__all__ = ["classify", "component_shape", "ship_blobs", "brightness_step", "RULES_TEXT"]
