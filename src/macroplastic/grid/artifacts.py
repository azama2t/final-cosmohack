"""Linear artefacts and ships among detections (artefact filter, reports/artifacts.md).

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
    wake  (wake rule, "ship at the end") length >= 250 m, elong >= 4, and a compact bright cluster ("end ship") lies within
          END_NEAR_PX = 5 px of one end cap of the major axis (object pixels within 2 px of the axis extreme):
          B8 >= max(0.04, 8 x water median B8), mean(B2,B3,B4) >= 1.3 x water median and B4 >= 0.6 x B3 (a hull /
          whitewater is grey-white; floating vegetation / algae filaments are green with a red dip, B4/B3 ~ 0.3 on the
          Nile 2025-11-11), cluster <= 60 px, >= 70 %
          of its 2..4 px ring is observed water or the object itself, and its peak B8 >= 2 x the median B8 of the
          object (the vessel is much brighter than its own wake). Softer than the ship blob above: in turbid
          water / at 10 m a small boat's visible min(B2,B3,B4) is below 2 x the water median (Mumbai 2026-01-01).
          Checked only for objects not marked by the rules above; no straightness test (two wakes merged into a
          V are still wakes), but a curved filament without a bright point at an end stays unmarked.
Curved or irregular objects (dev > 0.08) are never marked by the line rules; big irregular objects that merely
touch a bright target (ship_frac < 0.5, no straight tail) are not marked.

Cross-model artefact additions:
    collinear pieces  a model may break one strip into short pieces (LGBM on the Mumbai wake: pieces <= 160 m). Before
          the line rules, pieces (>= 2 px) are grouped when they lie on one straight line: major axes within
          GROUP_ANGLE = 15 deg (pieces shorter than 4 px have no reliable direction and only have to lie on the
          partner's axis), the centre of each piece <= GROUP_PERP_PX = 2 px from the other's axis, end-to-end gap
          along the axis <= GROUP_GAP_PX = 5 px. A group is kept only if the union is itself straight
          (dev <= GROUP_DEV = 0.08) - a curved filament broken into pieces is never merged into a "line". The group
          is classified as one object; a "wake" / "seam" of the group is given to every member that has no mark of
          its own (a group "ship" is ignored: the ship rule is not a straightness rule).
    cross-model (propagate_artifacts)  an object of model A that intersects or lies within CROSS_NEAR_PX = 2 px of an
          artefact of model B gets the same mark with `artifact_from` = B, if >= CROSS_MIN_SHARE of its pixels
          are within those 2 px (a big irregular patch that merely touches B's ship halo stays unmarked). Line marks
          (seam / wake) go only to objects that are not curved themselves (n_px < MIN_PX_LINE or dev <= GROUP_DEV):
          a curved arc continuing another model's straight seam is not a seam (Karachi 2025-11-11). Only B's
          own marks propagate (no chains). The confirmation (grid.confirm) ignores the partner's artefact pixels.

Boat-rule additions (reports/artifacts.md):
    ship  small boat: compact object (length <= 8 px, <= 30 px) on / within 2 px of a bright point relative to the
          local water (31 px window): B8 >= max(0.04, 8 x median B8), vis >= 1.3 x median vis and a SWIR response
          B11 excess >= max(0.01, 0.25 x B8 excess) (a dry hull reflects SWIR; wet floating material does not); the
          bright spot <= 40 px with >= 70 % water / detections in its 2..3 px ring (not shore). See small_boats.
    wake  "ship at the end" also looks along the continuation of the major axis of a straight object (dev <= 0.08):
          <= 2 px from the axis line, <= 20 px beyond the end (the vessel has moved on).
    wake  line in the image: a straight thin object (>= 100 m, thick <= 3 px, elong >= 6, dev <= 0.08, wiggle
          <= 0.6 px) lying on a bright line that goes on in the image beyond its ends along the major axis
          (contrast against the brighter side >= 0.5 x the object's own and above a parallel control line, gaps
          <= 3 px, <= 80 px per side, on the axis +-1 px at >= 75 % of positions); total >= 1 km - the same length
          as the ship-free wake rule, measured on the image line instead of the detection. See line_trace.
"""
from __future__ import annotations

import warnings

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
# Wake rule: wake with a vessel at one end of the major axis
END_LEN_M, END_ELONG, END_CAP_PX, END_NEAR_PX = 250.0, 4.0, 2.0, 5.0
END_B8, END_REL8, END_REL_VIS, END_MAX_PX, END_CONTRAST, END_FLAT = 0.04, 8.0, 1.3, 60, 2.0, 0.6
# Boat rule: ... or on the continuation of the major axis of a straight object (corridor +-2 px, up to 20 px beyond the end)
END_AXIS_PX, END_AXIS_PERP, END_AXIS_DEV = 20.0, 2.0, 0.08
STEP_OFFSETS = (3, 4, 5, 6)
# Boat rule: straight line that continues in the image beyond the detection (an old ship wake / lane): total >= 1 km
TRACE_LEN_M, TRACE_THICK_PX, TRACE_ELONG, TRACE_DEV, TRACE_EXT_PX = 100.0, 3.0, 6.0, 0.08, 80
TRACE_ON, TRACE_BG, TRACE_CTRL, TRACE_SMOOTH, TRACE_GAP, TRACE_REL = (-1, 0, 1), (4, 5, 6), 12, 5, 3, 0.5
TRACE_TOTAL_M, TRACE_WIGGLE, TRACE_ARGMAX, TRACE_AXIS_SHARE = 1000.0, 0.6, (-3, -2, -1, 0, 1, 2, 3), 0.75
# Boat rule: small boat - a small compact detection on / next to a bright point with a SWIR response (a dry hull)
BOAT_MAX_LEN_PX, BOAT_MAX_PX, BOAT_HALF_WIN, BOAT_RING_PX = 8, 30, 15, 2
BOAT_B8, BOAT_REL8, BOAT_REL_VIS, BOAT_SWIR, BOAT_D11 = 0.04, 8.0, 1.3, 0.25, 0.01
BOAT_MIN_WATER_PX, BOAT_SPOT_MAX_PX, BOAT_SPOT_RING_WATER = 30, 40, 0.7
# Cross-model artefacts: collinear pieces of one model are one strip for the line rules; cross-model propagation of marks
GROUP_ANGLE, GROUP_PERP_PX, GROUP_GAP_PX, GROUP_MIN_DIR_PX, GROUP_DEV = 15.0, 2.0, 5.0, 4.0, 0.08
CROSS_NEAR_PX, CROSS_MIN_SHARE = 2, 0.3

RULES_TEXT = {
    "ship": "у яркой малой цели (судно, платформа, буй, островок; B8 ≥ 0.06 и B2,B3,B4 ≥ 0.04, ≤ 150 px) — ≥ 50 % пикселей в ≤ 3 px; "
            "малый объект (≤ 80 м) на яркой точке: B8 ≥ 8× и видимые ≥ 1,3× медианы воды в окне 310 м, "
            "отклик в SWIR (B11) ≥ 0,25 отклика B8 — сухой корпус лодки (L43)",
    "wake": "прямая узкая полоса (кильватер): у яркой цели и ≥ 500 м, или ≥ 1 км без цели; "
            "вытянутый объект ≥ 250 м с яркой точкой-судном в ≤ 5 px (50 м) от торца (L41) или на продолжении оси "
            "в ≤ 20 px (коридор ±2 px, L43); прямой тонкий объект на светлой линии, которая продолжается на снимке "
            "за его концами, всего ≥ 1 км (L43)",
    "seam": "прямая линия по резкой границе яркости воды (шов детекторов / край мутного шлейфа) "
            "или ≥ 1,5 км вдоль трека S2",
    "grouping": "соседние куски одной модели на одной прямой (оси ≤ 15°, центр ≤ 2 px от оси соседа, зазор ≤ 5 px, "
                "объединение прямое: dev ≤ 0,08) проверяются правилами прямых линий как одна полоса (L42)",
    "cross_model": "объект одной модели, ≥ 30 % пикселей которого в ≤ 2 px от артефакта другой модели, получает ту же "
                   "метку (artifact_from; метку шва/кильватера — только некривой объект); пиксели артефактов другой "
                   "модели не подтверждают находку (L42)",
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


def end_bright_clusters(b2, b3, b4, b8, water: np.ndarray, own: np.ndarray) -> np.ndarray:
    """Wake rule: label image of compact bright clusters (candidate vessels at a wake's end); 0 = none.

    own = pixels of detected objects: they count as water in the ring test (a vessel sits on its own wake)."""
    b8 = np.nan_to_num(b8)
    vis = np.nan_to_num((b2 + b3 + b4) / 3.0)
    wb8, wv = b8[water], vis[water]
    t8 = max(END_B8, END_REL8 * float(np.median(wb8))) if wb8.size else END_B8
    tv = END_REL_VIS * float(np.median(wv)) if wv.size else 0.0
    bright = (b8 >= t8) & (vis >= tv) & (np.nan_to_num(b4) >= END_FLAT * np.nan_to_num(b3))  # grey/white, not green
    if not bright.any():
        return np.zeros(b8.shape, np.int32)
    lab, n = ndimage.label(bright, structure=EIGHT)
    size = np.bincount(lab.ravel(), minlength=n + 1)
    near = ndimage.binary_dilation(bright, EIGHT, iterations=4) & ~ndimage.binary_dilation(bright, EIGHT, iterations=1)
    ring_lab = ndimage.grey_dilation(lab, footprint=np.ones((9, 9), bool))
    rl = ring_lab[near]
    tot = np.bincount(rl, minlength=n + 1)
    wat = np.bincount(rl, weights=(water | own)[near].astype(float), minlength=n + 1)
    ok = (size <= END_MAX_PX) & (tot > 0) & (wat >= SHIP_RING_WATER * np.maximum(tot, 1))
    ok[0] = False
    return np.where(ok[lab], lab, 0).astype(np.int32)


def ship_at_end(labels: np.ndarray, n: int, shp: dict, b8: np.ndarray, clusters: np.ndarray,
                cand: np.ndarray, straight: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Wake rule: (end_ship bool, end_ship_b8 = peak B8 of the matched cluster) for candidate components.

    boat rule: for straight components (`straight`, None = all) the cluster may also lie on the continuation of the major
    axis: <= END_AXIS_PERP px from the axis line and <= END_AXIS_PX beyond the end."""
    hit = np.zeros(n, bool); peak = np.zeros(n)
    if not cand.any() or not clusters.any():
        return hit, peak
    H, W = labels.shape
    b8 = np.nan_to_num(b8)
    cpeak = ndimage.maximum(b8, clusters, index=np.arange(clusters.max() + 1))
    objs = ndimage.find_objects(labels)
    pad = int(max(END_NEAR_PX, END_AXIS_PX)) + 2
    for k in np.flatnonzero(cand):
        sl = objs[k]
        if sl is None:
            continue
        r0, r1 = max(sl[0].start - pad, 0), min(sl[0].stop + pad, H)
        c0, c1 = max(sl[1].start - pad, 0), min(sl[1].stop + pad, W)
        cl = clusters[r0:r1, c0:c1]
        if not cl.any():
            continue
        m = labels[r0:r1, c0:c1] == k + 1
        ys, xs = np.nonzero(m)
        ue, un = -shp["ny"][k], -shp["nx"][k]  # major axis (east, north)
        pa = xs * ue - ys * un
        med8 = float(np.median(b8[r0:r1, c0:c1][m]))
        # Boat rule: along the continuation of the major axis of a straight object: a corridor of +-END_AXIS_PERP px around
        # the axis line through the centroid, up to END_AXIS_PX beyond the end (the vessel has moved on from its wake)
        axis_ok = straight is None or bool(straight[k])
        if axis_ok:
            gy, gx = np.mgrid[0:cl.shape[0], 0:cl.shape[1]]
            ga = gx * ue - gy * un
            gp = -gx * un - gy * ue
            pm0 = float(np.mean(-xs * un - ys * ue))
            corr = (np.abs(gp - pm0) <= END_AXIS_PERP) & (cl > 0)
        for side, cap in ((1, pa >= pa.max() - END_CAP_PX), (-1, pa <= pa.min() + END_CAP_PX)):
            capm = np.zeros_like(m)
            capm[ys[cap], xs[cap]] = True
            d = ndimage.distance_transform_edt(~capm)
            sel = (d <= END_NEAR_PX) & (cl > 0)
            if axis_ok:
                beyond = (ga - pa.max()) if side > 0 else (pa.min() - ga)
                sel |= corr & (beyond > 0) & (beyond <= END_AXIS_PX)
            ids = np.unique(cl[sel])
            for i in ids:
                if cpeak[i] >= END_CONTRAST * med8 and cpeak[i] > peak[k]:
                    hit[k], peak[k] = True, float(cpeak[i])
    return hit, peak


def small_boats(labels: np.ndarray, n: int, shp: dict, b8: np.ndarray, vis: np.ndarray, b11: np.ndarray,
                water: np.ndarray, flagged: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Boat rule: (boat bool, boat_s11 = B11 excess / B8 excess at the peak) per component.

    Candidate: length <= BOAT_MAX_LEN_PX and n_px <= BOAT_MAX_PX. Local water = observed water in the 31 px window
    around the object, >= 2 px away from any detection (>= BOAT_MIN_WATER_PX pixels); medians of B8, vis =
    mean(B2,B3,B4) and B11 there. Peak = pixel of the object or its 2 px ring (the hull may sit next to the detected
    halo) with the largest B8 excess. Boat, all of:
      B8 >= max(BOAT_B8, BOAT_REL8 x median B8)    bright point in NIR (MARIDA debris: median 3.2x, 90 % 8.3x)
      vis >= BOAT_REL_VIS x median vis             not darker than water in the visible (grey / white)
      B11 excess >= max(BOAT_D11, BOAT_SWIR x B8 excess)   a dry hull reflects SWIR; floating / awash material is wet
                                                   and water absorbs SWIR (Santo Domingo 2019-01-21 debris: 0.00-0.02,
                                                   Mumbai 2026-01-01 boats: 0.29-0.71)
      the bright spot (window pixels over the B8 threshold, 8-connected with the peak) is compact
      (<= BOAT_SPOT_MAX_PX) and >= 70 % of its 2..3 px ring is water or detections (not shore / a pier)."""
    hit = np.zeros(n, bool); s11 = np.zeros(n)
    cand = (shp["length_px"] <= BOAT_MAX_LEN_PX) & (shp["n_px"] <= BOAT_MAX_PX)
    if n == 0 or not cand.any():
        return hit, s11
    H, W = labels.shape
    objs = ndimage.find_objects(labels)
    det = (labels > 0) | flagged
    for k in np.flatnonzero(cand):
        sl = objs[k]
        if sl is None:
            continue
        cy, cx = (sl[0].start + sl[0].stop) // 2, (sl[1].start + sl[1].stop) // 2
        h = BOAT_HALF_WIN
        r0, r1, c0, c1 = max(cy - h, 0), min(cy + h + 1, H), max(cx - h, 0), min(cx + h + 1, W)
        e8, v, e11 = (np.nan_to_num(x[r0:r1, c0:c1]) for x in (b8, vis, b11))
        dw = det[r0:r1, c0:c1]
        ww = water[r0:r1, c0:c1]
        w = ww & ~ndimage.binary_dilation(dw, EIGHT, iterations=2)
        if w.sum() < BOAT_MIN_WATER_PX:
            continue
        m8, mv, m11 = (float(np.median(x[w])) for x in (e8, v, e11))
        m = labels[r0:r1, c0:c1] == k + 1
        z = ndimage.binary_dilation(m, EIGHT, iterations=BOAT_RING_PX)
        iy, ix = np.unravel_index(np.argmax(np.where(z, e8, -np.inf)), e8.shape)
        d8, d11 = float(e8[iy, ix]) - m8, float(e11[iy, ix]) - m11
        s11[k] = d11 / d8 if d8 > 1e-4 else 0.0
        t8 = max(BOAT_B8, BOAT_REL8 * m8)
        if e8[iy, ix] < t8 or v[iy, ix] < BOAT_REL_VIS * mv or d11 < max(BOAT_D11, BOAT_SWIR * d8):
            continue
        lab, _ = ndimage.label(e8 >= t8, structure=EIGHT)
        spot = lab == lab[iy, ix]
        if spot.sum() > BOAT_SPOT_MAX_PX:
            continue
        ring = ndimage.binary_dilation(spot, EIGHT, iterations=3) & ~ndimage.binary_dilation(spot, EIGHT, iterations=1)
        if ring.sum() == 0 or (ww | dw)[ring].mean() < BOAT_SPOT_RING_WATER:
            continue
        hit[k] = True
    return hit, s11


def _profile(vis, valid, c0, r0, dc, dr, nc, nr, ts, on_offs, bg_offs, argmax_offs=None):
    """Line contrast along t: max vis over on_offs minus the brighter of the two side medians (vis at +bg_offs and at
    -bg_offs): a line is brighter than both sides, a brightness step is not (valid pixels only; NaN if none).
    With argmax_offs also returns the offset (of argmax_offs) with the largest vis at each t (NaN if none valid)."""
    H, W = vis.shape

    def sample(o):
        c = np.rint(c0 + ts * dc + o * nc).astype(int); r = np.rint(r0 + ts * dr + o * nr).astype(int)
        ok = (c >= 0) & (c < W) & (r >= 0) & (r < H)
        cc, rr = np.clip(c, 0, W - 1), np.clip(r, 0, H - 1)
        ok &= valid[rr, cc]
        return np.where(ok, vis[rr, cc], np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        on = np.nanmax(np.stack([sample(o) for o in on_offs]), 0)
        bg = np.fmax(np.nanmedian(np.stack([sample(o) for o in bg_offs]), 0),
                     np.nanmedian(np.stack([sample(-o) for o in bg_offs]), 0))
        if argmax_offs is None:
            return on - bg
        st = np.stack([sample(o) for o in argmax_offs])
        am = np.where(np.isfinite(st).any(0), np.array(argmax_offs)[np.argmax(np.nan_to_num(st, nan=-np.inf), 0)], np.nan)
    return on - bg, am


def line_trace(labels: np.ndarray, n: int, shp: dict, vis: np.ndarray, water: np.ndarray, own: np.ndarray,
               cand: np.ndarray) -> np.ndarray:
    """Boat rule: total length (px) of the straight bright line an object lies on: object length + how far the line goes on
    in the image beyond both ends along the major axis (0 for non-candidates).

    Only objects with wiggle <= TRACE_WIGGLE px (std of the mean perpendicular offset in 3 px bins along the axis).
    Contrast c(t) = max vis on the axis (+-1 px) - the brighter of the side medians at +4..6 / -4..6 px, smoothed over TRACE_SMOOTH px. The line
    continues while c(t) >= max(TRACE_REL x median c of the object itself, 95th percentile of the same statistic on
    a parallel control line TRACE_CTRL px away, the quieter of the two sides), gaps <= TRACE_GAP px, at most TRACE_EXT_PX; it stops at land /
    cloud (no valid water on the axis). The continuation counts only if at >= TRACE_AXIS_SHARE of its positions the
    brightest of the offsets -3..3 px is within +-1 px of the axis (straight, not a wavy filament drifting off it).
    Water = observed water or own detections (the line itself)."""
    out = np.zeros(n)
    if not cand.any():
        return out
    valid = water | own
    v = np.nan_to_num(vis)
    ker = np.ones(TRACE_SMOOTH) / TRACE_SMOOTH
    objs = ndimage.find_objects(labels)
    for k in np.flatnonzero(cand):
        sl = objs[k]
        if sl is None:
            continue
        ys, xs = np.nonzero(labels[sl] == k + 1)
        ys = ys + sl[0].start; xs = xs + sl[1].start
        ue, un = -shp["ny"][k], -shp["nx"][k]
        dc, dr = ue, -un
        nc, nr = shp["nx"][k], shp["ny"][k]
        c0, r0 = xs.mean(), ys.mean()
        pa = (xs - c0) * dc + (ys - r0) * dr
        tmin, tmax = float(pa.min()), float(pa.max())
        ts = np.arange(np.floor(tmin) - TRACE_EXT_PX, np.ceil(tmax) + TRACE_EXT_PX + 1)
        # wiggle: spread of the mean perpendicular offset in 3 px bins along the axis (a wavy filament is not a wake)
        pm = -(xs - c0) * dr + (ys - r0) * dc
        bins = np.floor((pa - tmin) / 3).astype(int)
        cnt = np.bincount(bins)
        if np.std((np.bincount(bins, pm) / np.maximum(cnt, 1))[cnt > 0]) > TRACE_WIGGLE:
            continue
        prof, am = _profile(v, valid, c0, r0, dc, dr, nc, nr, ts, TRACE_ON, TRACE_BG, TRACE_ARGMAX)
        inside = (ts >= tmin) & (ts <= tmax)
        c_obj = float(np.nanmedian(prof[inside])) if np.isfinite(prof[inside]).any() else np.nan
        if not np.isfinite(c_obj) or c_obj <= 0:
            continue
        ctrl = []
        for sgn in (1, -1):
            cc = _profile(v, valid & ~own, c0 + sgn * TRACE_CTRL * nc, r0 + sgn * TRACE_CTRL * nr, dc, dr, nc, nr, ts,
                          TRACE_ON, TRACE_BG)
            cs = np.convolve(np.nan_to_num(cc, nan=0.0), ker, "same")[np.isfinite(cc)]
            if cs.size >= 20:
                ctrl.append(float(np.percentile(cs, 95)))
        # the quieter side: a ship / another lane / shore on one side must not hide the line
        thr = max(TRACE_REL * c_obj, min(ctrl) if ctrl else np.inf)
        ok_t = np.isfinite(prof)
        sm = np.convolve(np.where(ok_t, prof, 0.0), ker, "same") / np.maximum(np.convolve(ok_t.astype(float), ker, "same"), 1e-6)
        ext = 0.0
        on_axis = []
        for side in (1, -1):
            idx = np.flatnonzero(ts > tmax) if side > 0 else np.flatnonzero(ts < tmin)[::-1]
            gap, last, seen = 0, 0.0, []
            for i in idx:
                if not ok_t[i]:
                    break
                seen.append(i)
                if sm[i] >= thr:
                    gap, last = 0, abs(ts[i] - (tmax if side > 0 else tmin))
                else:
                    gap += 1
                    if gap > TRACE_GAP:
                        break
            ext += last
            on_axis += [i for i in seen if abs(ts[i] - (tmax if side > 0 else tmin)) <= last]
        # the continuation must stay on the axis: the brightest of offsets -3..3 is within +-1 px at most positions
        a = am[on_axis] if on_axis else np.array([])
        a = a[np.isfinite(a)]
        if ext > 0 and (a.size == 0 or np.mean(np.abs(a) <= 1) < TRACE_AXIS_SHARE):
            ext = 0.0
        out[k] = (tmax - tmin + 1) + ext
    return out


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


def collinear_groups(shp: dict) -> np.ndarray:
    """Cross-model artefacts: group id (a component index, 0..n-1; the component's own index when ungrouped) of pieces on one line."""
    n = len(shp["n_px"])
    parent = np.arange(n)
    if n < 2:
        return parent
    idx = np.flatnonzero(shp["n_px"] >= 2)
    if idx.size < 2:
        return parent
    cx, cy, L = shp["cx"][idx], shp["cy"][idx], shp["length_px"][idx]
    ue, un = -shp["ny"][idx], -shp["nx"][idx]  # major axis (east, north)
    has_dir = L >= GROUP_MIN_DIR_PX
    de = cx[None, :] - cx[:, None]
    dn = -(cy[None, :] - cy[:, None])
    along = de * ue[:, None] + dn * un[:, None]  # centre of j along the axis of i
    perp = np.abs(-de * un[:, None] + dn * ue[:, None])  # centre of j off the axis of i
    gap = np.abs(along) - (L[:, None] + L[None, :]) / 2.0
    cosang = np.abs(ue[:, None] * ue[None, :] + un[:, None] * un[None, :])
    ang_ok = (cosang >= np.cos(np.radians(GROUP_ANGLE))) | ~(has_dir[:, None] & has_dir[None, :])
    on_i = np.where(has_dir[:, None], perp <= GROUP_PERP_PX, True)  # j lies on i's axis (if i has a direction)
    pair = ang_ok & on_i & on_i.T & (gap <= GROUP_GAP_PX) & (has_dir[:, None] | has_dir[None, :])
    np.fill_diagonal(pair, False)

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for i, j in zip(*np.nonzero(np.triu(pair))):
        a, b = find(idx[i]), find(idx[j])
        if a != b:
            parent[max(a, b)] = min(a, b)
    return np.array([find(k) for k in range(n)])


def _group_marks(labels: np.ndarray, n: int, shp: dict, bands, water, flagged, art: list) -> None:
    """Cross-model artefacts: classify straight groups of collinear pieces as one object; wake/seam goes to unmarked members."""
    grp = collinear_groups(shp)
    shp["group"] = grp
    shp["group_mark"] = [None] * n
    roots, counts = np.unique(grp, return_counts=True)
    multi = roots[counts >= 2]
    if multi.size == 0:
        return
    remap = np.zeros(n + 1, np.int32)
    gid = {int(r): i + 1 for i, r in enumerate(multi)}
    for k in range(n):
        if int(grp[k]) in gid:
            remap[k + 1] = gid[int(grp[k])]
    glab = remap[labels]
    gshp = component_shape(glab, len(multi))
    straight = gshp["dev"] <= GROUP_DEV
    if not straight.any():
        return
    keep = np.zeros(len(multi) + 1, np.int32)
    keep[1:][straight] = np.arange(1, int(straight.sum()) + 1)
    fl = (labels > 0) if flagged is None else (flagged | (labels > 0))
    gart, _ = classify(keep[glab], int(straight.sum()), bands, water, fl, group=False)
    for gi, r in enumerate(multi[straight]):
        a = gart[gi]
        if a not in ("wake", "seam"):
            continue
        for k in np.flatnonzero(grp == r):
            shp["group_mark"][k] = a
            if art[k] is None:
                art[k] = a


def propagate_artifacts(labels: np.ndarray, n: int, art: list, other_labels: np.ndarray, other_art: list,
                        other_model: str, near_px: int = CROSS_NEAR_PX,
                        min_share: float = CROSS_MIN_SHARE) -> list[tuple[int, str, str, float]]:
    """Cross-model artefacts: marks taken from another model: [(k (0-based component), artifact, other_model, share)] for components
    of `labels` without their own mark that intersect / lie within near_px of an artefact of the other model with
    >= min_share of their pixels. Pass the other model's own marks (not propagated ones): no chains."""
    out: list[tuple[int, str, str, float]] = []
    if n == 0 or not any(other_art):
        return out
    from .confirm import disk
    kinds = ("seam", "wake", "ship")
    lut = np.zeros(len(other_art) + 1, np.int8)
    for i, a in enumerate(other_art):
        if a in kinds:
            lut[i + 1] = kinds.index(a) + 1
    omask = lut[other_labels]
    if not omask.any():
        return out
    tot = np.bincount(labels.ravel(), minlength=n + 1)[1:]
    near_any = ndimage.binary_dilation(omask > 0, structure=disk(near_px))
    share = np.bincount(labels[near_any & (labels > 0)], minlength=n + 1)[1:] / np.maximum(tot, 1)
    best = np.zeros(n)
    best_kind = np.zeros(n, int)
    for ki in range(1, len(kinds) + 1):
        m = omask == ki
        if not m.any():
            continue
        near = ndimage.binary_dilation(m, structure=disk(near_px))
        h = np.bincount(labels[near & (labels > 0)], minlength=n + 1)[1:]
        better = h > best
        best = np.where(better, h, best)
        best_kind = np.where(better, ki, best_kind)
    shp = component_shape(labels, n)
    curved = (shp["n_px"] >= MIN_PX_LINE) & (shp["dev"] > GROUP_DEV)
    for k in np.flatnonzero((share >= min_share) & (best_kind > 0)):
        if curved[k] and kinds[best_kind[k] - 1] != "ship":
            continue
        if art[k] is None:
            out.append((int(k), kinds[best_kind[k] - 1], other_model, float(share[k])))
    return out


def classify(labels: np.ndarray, n: int, bands: dict | None, water: np.ndarray,
             flagged: np.ndarray | None = None, group: bool = True) -> tuple[list, dict]:
    """Return (artifact per component: None | "seam" | "wake" | "ship", features dict of arrays).

    group=True (cross-model artefacts): straight groups of collinear pieces are also classified as one strip (collinear_groups)."""
    shp = component_shape(labels, n)
    art: list = [None] * n
    if n == 0:
        return art, shp
    L_m = shp["length_px"] * PX_M
    line = shp["n_px"] >= MIN_PX_LINE
    ship_frac = np.zeros(n)
    seam_small = np.zeros(n, bool)
    end_ship = np.zeros(n, bool); end_peak = np.zeros(n)
    boat = np.zeros(n, bool); boat_s11 = np.zeros(n); trace = np.zeros(n)
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
        cand_tr = line & (L_m >= TRACE_LEN_M) & (shp["thick_px"] <= TRACE_THICK_PX) & (shp["elong"] >= TRACE_ELONG)             & (shp["dev"] <= TRACE_DEV)
        trace = line_trace(labels, n, shp, vis, water, fl, cand_tr)
        if "B11" in bands:
            boat, boat_s11 = small_boats(labels, n, shp, b8, vis, bands["B11"].astype(np.float32), water, fl)
        cand_end = line & (L_m >= END_LEN_M) & (shp["elong"] >= END_ELONG)
        if cand_end.any():
            clusters = end_bright_clusters(b2, b3, b4, b8, water, labels > 0)
            end_ship, end_peak = ship_at_end(labels, n, shp, b8, clusters, cand_end,
                                             straight=shp["dev"] <= END_AXIS_DEV)
    shp["step_rel"], shp["step_cons"], shp["obj_excess"], shp["ship_frac"] = step, cons, exc, ship_frac
    shp["end_ship"], shp["end_ship_b8"] = end_ship, end_peak
    shp["boat"], shp["boat_s11"], shp["trace_px"] = boat, boat_s11, trace
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
        elif ship_frac[k] >= SHIP_FRAC or boat[k]:
            art[k] = "ship"
        elif seam_track[k] or seam_edge[k] or seam_small[k]:
            art[k] = "seam"
        elif wake_free[k]:
            art[k] = "wake"
        elif end_ship[k]:
            art[k] = "wake"
        elif trace[k] * PX_M >= TRACE_TOTAL_M:
            art[k] = "wake"
    if group and n >= 2:
        _group_marks(labels, n, shp, bands, water, flagged, art)
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


__all__ = ["classify", "component_shape", "ship_blobs", "small_boats", "line_trace", "brightness_step", "end_bright_clusters", "ship_at_end",
           "collinear_groups", "propagate_artifacts", "RULES_TEXT"]
