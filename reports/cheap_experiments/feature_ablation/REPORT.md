# S64 / L159 -- Cheap experiment 01: feature ablation of weights/lgbm

Status: **DONE** (train/val ablation, 3 seeds, 12 variants). Domain-stress diagnostics
(Cozar/FloatingObjects/PLP/MADOS-external recall, vessels, foam/cloud/glint FPR) --
**chastichno / ne vypolneno**: skipped for time budget (deadline 00:48), see "Not done" below.
Experiments 02 (pretrained U-Net++ benchmark) and 03 (ensemble) -- **zaplanirovano, ne vypolneno**
per S64/orchestrator instruction (external weights download / heavy CPU inference before freeze).

Production (weights/lgbm, threshold 0.63) is **not modified** by this experiment, regardless of
outcome. No file under weights/ or weights_exp/ was touched; only reports/cheap_experiments/feature_ablation/**
and read-only cache files under out/l3_cache/, out/l13_cache/ (features only, no model weights).

## Hypothesis

The current weights/lgbm feature set (11 bands + 8 indices + 24 window mean/std + 5 local-median
contrasts = 48 features, src/macroplastic/features/pixel.py feature_names("win")) may contain
redundant or low-value spectral indices that could be dropped without hurting MARIDA val F1, or whose
removal would reduce false positives on real L2A scenes (the project's known transfer problem).
Historical log: 11+8 -> val F1 ~0.893; +29 window feats -> ~0.906; +MADOS training data -> 0.923
(current). This experiment re-checks that decomposition on the exact current training recipe.

## Protocol

- Training script actually used for weights/lgbm: scripts/train_lgbm_mados.py train --data combined
  (confirmed by matching weights/lgbm/meta.json config field-by-field). Combined = MARIDA train
  (official split) + MADOS train+val pixels (scene-level de-duplicated against MARIDA val/test via
  reports/mados_overlap.csv, exclude_same_place_val: true, mados_extra: false).
  Evaluation is exclusively on official MARIDA val (scripts/train_lgbm.py ALLOWED_SPLITS =
  (train, val); MARIDA test is never imported by any module used here).
- Same LightGBM hyperparameters as configs/lgbm.yaml / production config (num_leaves=63, lr=0.05,
  400 rounds, min_data_in_leaf=20, feature_fraction=0.8, bagging_fraction=0.8, lambda_l2=1.0,
  pos_weight=3.0, sampling caps 30k/60k for water). Only the feature subset passed to LightGBM
  changes between variants; sampling/weights/rounds are identical.
- 3 seeds: 0, 1, 2 (project standard for this training path, --seed).
- Threshold: (a) re-selected per variant/seed on MARIDA val via the project's own grid search
  (TL.best_threshold, grid 0.02..0.98 step 0.01 -- same code as production); (b) fixed production
  threshold 0.63 evaluated in parallel (columns *_thr063 in results.csv).
- Feature caches: out/l3_cache/{train,val}_win.npz (MARIDA, 48 features) and
  out/l13_cache/mados_{train,val}_win.npz (MADOS, 48 features) -- built once from the already-local
  MARIDA/MADOS patch datasets (data/MARIDA, data/MADOS), no Sentinel-2 scenes downloaded or
  reprocessed; this is the same local per-patch feature computation the production training script
  performs (compute_features, pure numpy/scipy on 240x240/256x256 arrays). val_win.npz pre-existed;
  train_win.npz and both MADOS caches were built by this run (one-time, ~10 min total, CPU only,
  n_jobs=4, CUDA_VISIBLE_DEVICES=-1).
- Disk checked before start and after each cache stage: 29.5 -> 28.2 GB free, always above the 20 GB
  floor; no stop triggered.
- Process ran the whole time as a single Python process launched via Start-Process set to
  BelowNormal priority; internal thread/process pools capped at n_jobs=4.

### Feature groups (from feature_names("win"), verified in code, not guessed)

- RAW (11): B1,B2,B3,B4,B5,B6,B7,B8,B8A,B11,B12
- INDICES (8): FDI, FAI, NDVI, NDWI, NDMI, SI, BSI, NRD
- WINDOW-derived (24 mean/std over B8/FDI/NDVI/NDWI at 3/7/15 px) + CONTRAST (5: B8_dmed15/31,
  FDI_dmed15/31, NDVI_dmed31) = 29 "window" features total, computed from B8/FDI/NDVI/NDWI internally
  regardless of whether the raw index column itself is kept.
- Removing an index "group" (variant E) drops its own raw column and every derived window/contrast
  feature that uses it as input (dependency-correct removal): FDI group = 9 features (1 raw + 6
  windows + 2 contrasts), NDVI group = 8 (1 + 6 + 1 contrast), NDWI group = 7 (1 + 6, no contrast),
  FAI/NDMI/SI/BSI/NRD groups = 1 feature each (standalone, not used by any window/contrast).

### Variants

A RAW_ONLY (11) - B RAW_PLUS_INDICES (19) - C RAW_PLUS_WINDOWS (11+29=40, no raw indices) -
D FULL (48, = production) - E MINUS_<INDEX> for each of the 8 indices (39-47 features).

## Results -- MARIDA val, mean +/- std over 3 seeds (best threshold per run)

| variant | n_features | F1 | precision | recall | IoU | F1 @ thr=0.63 | dF1 vs FULL |
|---|---:|---:|---:|---:|---:|---:|---:|
| A_RAW_ONLY | 11 | 0.9016 +/- 0.001 | 0.8902 | 0.9135 | 0.8208 | 0.8931 | -0.0212 |
| B_RAW_PLUS_INDICES | 19 | 0.9010 +/- 0.002 | 0.8986 | 0.9036 | 0.8199 | 0.8973 | -0.0218 |
| C_RAW_PLUS_WINDOWS | 40 | 0.9175 +/- 0.001 | 0.9177 | 0.9175 | 0.8476 | 0.9148 | -0.0053 |
| D_FULL (production feature set) | 48 | 0.9228 +/- 0.000 | 0.9208 | 0.9250 | 0.8567 | 0.9203 | 0 |
| E_MINUS_FDI | 39 | 0.9229 +/- 0.000 | 0.9215 | 0.9244 | 0.8568 | 0.9219 | +0.0001 |
| E_MINUS_FAI | 47 | 0.9246 +/- 0.001 | 0.9218 | 0.9274 | 0.8597 | 0.9221 | +0.0018 |
| E_MINUS_NDVI | 40 | 0.9139 +/- 0.001 | 0.9084 | 0.9194 | 0.8414 | 0.9086 | -0.0089 |
| E_MINUS_NDWI | 41 | 0.9156 +/- 0.001 | 0.9172 | 0.9141 | 0.8443 | 0.9134 | -0.0072 |
| E_MINUS_NDMI | 47 | 0.9220 +/- 0.001 | 0.9167 | 0.9274 | 0.8553 | 0.9181 | -0.0008 |
| E_MINUS_SI | 47 | 0.9242 +/- 0.001 | 0.9222 | 0.9262 | 0.8591 | 0.9213 | +0.0014 |
| E_MINUS_BSI | 47 | 0.9213 +/- 0.001 | 0.9153 | 0.9274 | 0.8541 | 0.9187 | -0.0015 |
| E_MINUS_NRD | 47 | 0.9243 +/- 0.001 | 0.9173 | 0.9315 | 0.8593 | 0.9218 | +0.0015 |

Full per-seed rows: results.csv (36 rows = 12 variants x 3 seeds), machine-readable summary +
config: results.json. Run log/commands: run.log, run_commands.txt.

D_FULL/seed0 reproduces the production number closely (F1=0.9226 in weights/lgbm/meta.json vs
0.9226 here at thr=0.63, i.e. exact match -- confirms the ablation harness reproduces the production
training path correctly).

## Analysis

1. Windows are still the dominant driver, confirming the historical log: RAW+INDICES alone
   (B, F1=0.901) is no better than RAW alone (A, F1=0.902) -- the 8 raw indices add essentially
   nothing without the window/contrast features. Adding windows (C, F1=0.918, no raw index columns)
   recovers most of the gap to FULL. This reproduces the prior finding "+29 window feats: 0.893 -> 0.906"
   at a similar magnitude on the current (combined MARIDA+MADOS) recipe.
2. NDVI and NDWI are the two indices that matter on MARIDA val: removing either group costs
   0.007-0.009 F1, consistently across all 3 seeds -- well above noise (seed std ~ 0.001-0.002) and
   above the 0.005 "no practical change" band.
3. FAI, NRD, SI, NDMI, BSI, and even the whole FDI group (9 features incl. FDI itself, the single
   highest-gain feature in weights/lgbm/meta.json importance_gain_top) can each be removed
   individually with no significant MARIDA-val cost (|dF1| <= 0.002, within seed noise), and 3 of
   them (FAI, SI, NRD) show a small, seed-consistent positive delta (+0.0014..+0.0018, i.e. below
   the +0.01 bar needed to call a real improvement, but not worse). This does not mean FDI is
   useless in production -- FDI has the largest tree-gain in the trained model, which typically means
   it captures signal other features would otherwise need more splits to represent; MARIDA val alone
   cannot distinguish "redundant" from "important for real-scene transfer" for a feature this central
   to the model's known logic (FDI is the physically-motivated floating-debris index). Removing it
   should not be decided from this val-only ablation.
4. False positives (ships/foam/cloud/glint) could not be checked in this run (see "Not done"
   below) -- this is exactly the axis on which a val-only ablation is known to be unreliable for this
   project (per S64 context: transfer to real L2A scenes is the current open problem).

## Verdict per variant (rule: KEEP if dF1>=+0.01 with no domain-stress regression, OR |dF1|<=0.005
with a clear practical win (fewer features/FPs/time) confirmed on domain-stress; REJECT otherwise;
NO_CHANGE if the evidence is insufficient to act)

| variant | verdict | reason |
|---|---|---|
| A_RAW_ONLY | REJECT | F1 -0.021 vs FULL, well past threshold |
| B_RAW_PLUS_INDICES | REJECT | F1 -0.022 vs FULL; indices without windows add nothing |
| C_RAW_PLUS_WINDOWS | REJECT | F1 -0.0053, just past the 0.005 band, and no domain-stress confirmation to justify dropping all indices |
| E_MINUS_FDI | NO_CHANGE | dF1 ~ 0 on val, but FDI is the top-gain feature and the physically-motivated debris index; domain-stress (ships/foam/glint) untested -- do not remove without it |
| E_MINUS_FAI / E_MINUS_SI / E_MINUS_NRD | NO_CHANGE | small, seed-consistent, positive but < +0.01 bar; candidates for a future, cheap follow-up if combined with domain-stress checks, not actioned now |
| E_MINUS_NDMI / E_MINUS_BSI | NO_CHANGE | within noise, but saving a single feature is not a practically meaningful win on its own |
| E_MINUS_NDVI / E_MINUS_NDWI | REJECT | clear, seed-consistent F1 loss (-0.007..-0.009) |

## Overall verdict: NO_CHANGE

No variant clears the +0.01 MARIDA-val-F1 bar, and no variant that stays within the noise band has a
confirmed practical win (domain-stress/FP/speed) because that diagnostic step was not completed in
time. weights/lgbm and the production threshold (0.63) are unchanged. The one actionable,
low-confidence signal worth a future (properly time-boxed) follow-up: FAI/SI/NRD/NDMI/BSI are
individually redundant on MARIDA val and FDI's redundancy-on-val does not extend to a recommendation
to remove it, given its known role in real-scene transfer.

## Not done (time budget, explicitly out of scope today)

- Domain-stress diagnostics (Cozar positive recall, FloatingObjects/PLP/MADOS-external recall, vessel
  hull FP rate, foam/cloud/glint FPR under the existing detector_v2 protocol) -- chastichno /
  ne vypolneno. Relevant local artifacts exist (data/discovery/v1/win, out/detector_v2/*,
  scripts/case/detector_v2_cozar.py) but wiring the ablation feature-subset models into that
  harness correctly (same evaluation code, no shortcuts) did not fit in the time remaining before the
  00:48 deadline. This is exactly the axis needed to safely act on the FDI/FAI/SI/NRD/NDMI/BSI
  NO_CHANGE candidates above.
- Experiment 02 (pretrained U-Net++/MarineDebrisDetector zero-training benchmark) -- zaplanirovano,
  ne vypolneno (external weights download, forbidden today per S64/orchestrator).
- Experiment 03 (LightGBM/U-Net++ ensemble) -- zaplanirovano, ne vypolneno (depends on 02; also
  forbidden today).
- Bootstrap 95% CI for FULL vs A/B/C/E: no existing scene-level bootstrap utility was found reused
  in the time available; only seed-to-seed std is reported above (3 seeds) instead of a bootstrap CI.

## Artifacts

- reports/cheap_experiments/feature_ablation/results.csv -- 36 rows (variant x seed)
- reports/cheap_experiments/feature_ablation/results.json -- same data + config/meta
- reports/cheap_experiments/feature_ablation/run_ablation.py -- the script used (only file written
  outside reports/cheap_experiments/feature_ablation/ was the feature cache under out/l3_cache/,
  out/l13_cache/, not model weights)
- reports/cheap_experiments/feature_ablation/run.log, run_commands.txt
