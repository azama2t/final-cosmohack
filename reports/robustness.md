# Устойчивость инференса

Сгенерировано `scripts/robustness_report.py` за 39 с. Модели: `lgbm` (weights/lgbm, порог 0.63), `fdi_rule` (порог 0.5). Устройство: CPU.

Данные: 8 патчей MARIDA **val** с разметкой Marine Debris (14-3-20_18QYF_3, 14-3-20_18QYF_4, 18-9-20_16PCC_15, 18-9-20_16PCC_27, 18-9-20_16PCC_39, 29-12-20_18QYF_1, 29-12-20_18QYF_3, 29-12-20_18QYF_8); вырезка 512×512 живой сцены `data/live/manila/2019-05-13` (y=0, x=0, доля воды 100%). Test MARIDA не читается.

**Итог: 64 проверок — PASS 58, WARN 6, FAIL 0.**

Пороги оценки: доля *новых* срабатываний на возмущённых пикселях ≤ 0.1% — PASS, ≤ 1% — WARN, иначе FAIL; изменение F1 на размеченных пикселях val |ΔF1| ≤ 0.05 — PASS, ≤ 0.15 — WARN (и WARN при изменении числа находок > ±50%). F1 — Marine Debris против остальных размеченных классов, по порогу модели. Облако: непрозрачная часть (96 столбцов) + полупрозрачный край (32 столбца), текстура 0.25–0.55, SWIR ниже. Блик: полосы по 32 строки через одну, прибавка к B8 и B11 (+0.01 / +0.03); вариант «только B8» — справочный (это и есть сигнатура FDI). Оговорка: на вырезке живой сцены у lgbm 0 находок и до возмущения, поэтому «live 0 → 0» — слабое свидетельство; основная оценка — по val-патчам.

## 1. Пустой чип (NaN / 0)

| Случай | Ожидание | Результат | Статус |
|---|---|---|---|
| `1.lgbm.nan.direct` | no exception, prob == 0 (<0.5/255) | max prob 0.00e+00 | **PASS** |
| `1.lgbm.zero.direct` | no exception, prob == 0 (<0.5/255) | max prob 1.42e-06 | **PASS** |
| `1.fdi_rule.nan.direct` | no exception, prob == 0 (<0.5/255) | max prob 0.00e+00 | **PASS** |
| `1.fdi_rule.zero.direct` | no exception, prob == 0 (<0.5/255) | max prob 0.00e+00 | **PASS** |
| `1.lgbm.half_nan.direct` | NaN half -> prob 0 | max prob on NaN half 0 | **PASS** |
| `1.lgbm.cli` | exit 0, *_prob.tif == 0, *_mask.tif == 0 for NaN / 0 / uint16-0 chips | exit 0; nan: max prob_u8 0, mask 0; zero: max prob_u8 0, mask 0; zero_u16: max prob_u8 0, mask 0 | **PASS** |
| `1.fdi_rule.cli` | exit 0, *_prob.tif == 0, *_mask.tif == 0 for NaN / 0 / uint16-0 chips | exit 0; nan: max prob_u8 0, mask 0; zero: max prob_u8 0, mask 0; zero_u16: max prob_u8 0, mask 0 | **PASS** |

## 2. Облака (синтетика, половина чипа)

| Случай | Ожидание | Результат | Статус |
|---|---|---|---|
| `2.lgbm` | new detections under cloud <= 0.1% (WARN <= 1%) | val opaque 0.000% (0/198656), val thin edge 0.000% (0/63488), live crop 0.000% | **PASS** |
| `2.fdi_rule` | new detections under cloud <= 0.1% (WARN <= 1%) | val opaque 0.000% (0/198656), val thin edge 0.079% (50/63488), live crop 0.000% | **PASS** |

## 3. Солнечный блик (полосы +B8/B11)

| Случай | Ожидание | Результат | Статус |
|---|---|---|---|
| `3.lgbm.B8+B11` | new detections in glint stripes (non-debris px) <= 0.1% | +0.01: val 0.002%, live 0.000%; +0.03: val 0.000%, live 0.000% | **PASS** |
| `3.lgbm.B8` | new detections in glint stripes (non-debris px) <= 0.1% (B8-only = FDI signature; informative, max WARN) | +0.01: val 0.006%, live 0.000%; +0.03: val 0.000%, live 0.000% | **PASS** |
| `3.fdi_rule.B8+B11` | new detections in glint stripes (non-debris px) <= 0.1% | +0.01: val 0.209%, live 0.000%; +0.03: val 0.039%, live 0.000% | **WARN** |
| `3.fdi_rule.B8` | new detections in glint stripes (non-debris px) <= 0.1% (B8-only = FDI signature; informative, max WARN) | +0.01: val 93.819%, live 0.182%; +0.03: val 98.369%, live 100.000% | **WARN** |

## 4. Шум / сдвиг радиометрии

| Случай | Ожидание | Результат | Статус |
|---|---|---|---|
| `4.lgbm.scale0.9` | \|dF1\| <= 0.05 (WARN <= 0.15); detections within +-50% | F1 0.970 -> 0.971 (d +0.001); val detections 1638 -> 1555 (-5%); live crop 0 -> 0 | **PASS** |
| `4.lgbm.scale1.1` | \|dF1\| <= 0.05 (WARN <= 0.15); detections within +-50% | F1 0.970 -> 0.970 (d +0.000); val detections 1638 -> 1683 (+3%); live crop 0 -> 0 | **PASS** |
| `4.lgbm.offset+0.005` | \|dF1\| <= 0.05 (WARN <= 0.15); detections within +-50% | F1 0.970 -> 0.941 (d -0.029); val detections 1638 -> 1429 (-13%); live crop 0 -> 0 | **PASS** |
| `4.lgbm.noise_s0.002` | \|dF1\| <= 0.05 (WARN <= 0.15); detections within +-50% | F1 0.970 -> 0.890 (d -0.080); val detections 1638 -> 979 (-40%); live crop 0 -> 0 | **WARN** |
| `4.fdi_rule.scale0.9` | \|dF1\| <= 0.05 (WARN <= 0.15); detections within +-50% | F1 0.612 -> 0.616 (d +0.005); val detections 2800 -> 2730 (-2%); live crop 0 -> 0 | **PASS** |
| `4.fdi_rule.scale1.1` | \|dF1\| <= 0.05 (WARN <= 0.15); detections within +-50% | F1 0.612 -> 0.595 (d -0.017); val detections 2800 -> 2877 (+3%); live crop 0 -> 0 | **PASS** |
| `4.fdi_rule.offset+0.005` | \|dF1\| <= 0.05 (WARN <= 0.15); detections within +-50% | F1 0.612 -> 0.604 (d -0.008); val detections 2800 -> 2707 (-3%); live crop 0 -> 0 | **PASS** |
| `4.fdi_rule.noise_s0.002` | \|dF1\| <= 0.05 (WARN <= 0.15); detections within +-50% | F1 0.612 -> 0.568 (d -0.044); val detections 2800 -> 16988 (+507%); live crop 0 -> 0 | **WARN** |

## 5. Другие размеры

| Случай | Ожидание | Результат | Статус |
|---|---|---|---|
| `5.lgbm.100x100.direct` | no exception, prob shape == (H,W) | shape (100, 100) | **PASS** |
| `5.fdi_rule.100x100.direct` | no exception, prob shape == (H,W) | shape (100, 100) | **PASS** |
| `5.lgbm.1000x1000.direct` | no exception, prob shape == (H,W) | shape (1000, 1000); tiled vs untiled max\|dp\| 0.00e+00, mask diff 0 px | **PASS** |
| `5.fdi_rule.1000x1000.direct` | no exception, prob shape == (H,W) | shape (1000, 1000) | **PASS** |
| `5.lgbm.300x700.direct` | no exception, prob shape == (H,W) | shape (300, 700); tiled vs untiled max\|dp\| 0.00e+00, mask diff 0 px | **PASS** |
| `5.fdi_rule.300x700.direct` | no exception, prob shape == (H,W) | shape (300, 700) | **PASS** |
| `5.lgbm.700x300.direct` | no exception, prob shape == (H,W) | shape (700, 300); tiled vs untiled max\|dp\| 0.00e+00, mask diff 0 px | **PASS** |
| `5.fdi_rule.700x300.direct` | no exception, prob shape == (H,W) | shape (700, 300) | **PASS** |
| `5.lgbm.513x40.direct` | no exception, prob shape == (H,W) | shape (513, 40); tiled vs untiled max\|dp\| 0.00e+00, mask diff 0 px | **PASS** |
| `5.fdi_rule.513x40.direct` | no exception, prob shape == (H,W) | shape (513, 40) | **PASS** |
| `5.lgbm.1x1.direct` | no exception, prob shape == (H,W) | shape (1, 1) | **PASS** |
| `5.fdi_rule.1x1.direct` | no exception, prob shape == (H,W) | shape (1, 1) | **PASS** |
| `5.lgbm.5x3.direct` | no exception, prob shape == (H,W) | shape (5, 3) | **PASS** |
| `5.fdi_rule.5x3.direct` | no exception, prob shape == (H,W) | shape (5, 3) | **PASS** |
| `5.cli` | exit 0, outputs of every size with the input's H,W | exit 0; missing -; wrong shape -; - | **PASS** |

## 6. Другие dtype

| Случай | Ожидание | Результат | Статус |
|---|---|---|---|
| `6.val_f64` | same mask as val_f32 (<=0.1% px differ; 1e-4 DN quantisation and clipping of negative rhorc to 0 allowed) | max\|d prob_u8\| 0, mask differs in 0 px, detections 232 -> 232 | **PASS** |
| `6.val_u16dn` | same mask as val_f32 (<=0.1% px differ; 1e-4 DN quantisation and clipping of negative rhorc to 0 allowed) | max\|d prob_u8\| 54, mask differs in 3 px, detections 232 -> 233 | **PASS** |
| `6.live_u16dn` | same mask as live_f32 (<=0.1% px differ; 1e-4 DN quantisation and clipping of negative rhorc to 0 allowed) | max\|d prob_u8\| 0, mask differs in 0 px, detections 0 -> 0 | **PASS** |
| `6.val_u16dn_off1000` | L2A baseline>=04.00 DN (+1000 offset): same mask as reflectance (or a clear error) | max\|d prob_u8\| 54, mask differs in 3 px, detections 232 -> 233 | **PASS** |
| `6.live_u16dn_off1000` | L2A baseline>=04.00 DN (+1000 offset): same mask as reflectance (or a clear error) | max\|d prob_u8\| 0, mask differs in 0 px, detections 0 -> 0 (identical to reflectance: offset removed by --scale auto) | **WARN** |
| `6.cli` | exit 0, DN detected and scaled with a warning | exit 0; 06:41:21 WARNING inference: live_u16dn.tif looks like DN (median > 2) -> multiplying by 1e-4; dark-pixel B12 median DN=37 < 1000 -> no BOA offset \| 06:41:21 WARNING inference: val_u16dn.tif looks like DN (median > 2) -> multiplying by 1e-4; dark-pixel B12 median DN=39 < 1000 -> no BOA offset \| 06:41 | **PASS** |

## 7. Каналы: лишние / переставленные / отсутствующие

| Случай | Ожидание | Результат | Статус |
|---|---|---|---|
| `7.perm_desc` | same result as ordered (mapped by band descriptions) | exit 0; max\|d prob_u8\| 0 | **PASS** |
| `7.desc_b0x_lower` | 'b08'/'b8a' descriptions normalised -> same result | exit 0; max\|d prob_u8\| 0 | **PASS** |
| `7.extra_b10_desc` | extra band B10 ignored -> same result | exit 0; max\|d prob_u8\| 0 | **PASS** |
| `7.perm_nodesc` | cannot be detected from data; ideally a warning (no descriptions -> order assumed) | exit 0, processed with the assumed s2_l2a_12 order; max\|d prob_u8\| 20, detections 0 vs 0; warning printed: True | **WARN** |
| `7.extra_nodesc` | exit 1 (data error), message names the problem, no output | exit 1; ERROR: extra_nodesc.tif: 13 bands, cannot map to channels (use --channels) \| ERROR: 1 of 1 file(s) failed: extra_nodesc.tif | **PASS** |
| `7.missing_nodesc_10` | exit 1 (data error), message names the problem, no output | exit 1; ERROR: missing_nodesc_10.tif: 10 bands, cannot map to channels (use --channels) \| ERROR: 1 of 1 file(s) failed: missing_nodesc_10.tif | **PASS** |
| `7.missing_b8_desc` | exit 1 (data error), message names the problem, no output | exit 1; ERROR: missing_b8_desc.tif: missing bands ['B8'] required by model 'lgbm'; got ['B1', 'B2', 'B3', 'B4', 'B5', 'B6', 'B7', 'B8A', 'B9', 'B11', 'B12'] \| ERROR: 1 of 1 file(s) failed: missing_b8_desc.tif | **PASS** |
| `7.channels_flag_mismatch` | --channels marida on 12-band file -> exit 1 with message | exit 1; ERROR: ref.tif: 12 bands, cannot map to channels (set marida has 11) \| ERROR: 1 of 1 file(s) failed: ref.tif | **PASS** |

## 8. Битые tif / не-tif в папке

| Случай | Ожидание | Результат | Статус |
|---|---|---|---|
| `8.mixed_folder` | good files processed; broken/empty/truncated/1-band tif skipped with a per-file ERROR naming the file; .txt/.jpg/dir ignored; exit 1 (some files failed) | exit 1; good outputs True; bad files named {'broken': True, 'empty': True, 'truncated': True, 'single_band': True}; no bad outputs True; txt/jpg silent True | **PASS** |
| `8.truncated` | truncated tif -> read error, skipped | skipped with error | **PASS** |
| `8.no_images` | exit 1 'no input *.tif' | exit 1; ERROR: no input *.tif in out\robustness\8_broken\only_txt (files ending with _cl/_conf/_prob/_mask are skipped) | **PASS** |
| `8.missing_dir` | exit 1 'data directory not found' | exit 1; ERROR: data directory not found: out\robustness\8_broken\does_not_exist | **PASS** |
| `8.only_broken` | exit 1, error names x.tif | exit 1; ERROR: cannot read out\robustness\8_broken\only_broken\x.tif: RasterioIOError: 'out\robustness\8_broken\only_broken\x.tif' not recognized as being in a supported file format. \| ERROR: 1 of 1 file(s) fai | **PASS** |
| `8.output_is_file` | exit 1 with a message, no traceback | exit 1; ERROR: cannot write outputs to out\robustness\8_broken\out_is_file: FileExistsError: [WinError 183] Невозможно создать файл, так как он уже существует: 'out\\robustness\\8_broken\\out_is_file' | **PASS** |
| `8.live_scene_folder` | data/live/<region>/<date> layout: bands.tif processed; 1-band helper rasters (scl, prob_lgbm) ideally ignored | exit 0; bands processed True; 06:41:32 WARNING inference: skipped prob_lgbm.tif: 1 band: not a multispectral S2 image (mask / SCL / probability raster) \| 06:41:32 WARNING inference: skipped scl.tif: 1 band: not a multispectral S2 image (mask / SCL / probability raster) | **PASS** |

## 9. Пути: пробелы, кириллица, aux.tif

| Случай | Ожидание | Результат | Статус |
|---|---|---|---|
| `9.снимок 1` | 'снимок 1.tif' in a dir with spaces+Cyrillic -> outputs снимок 1_prob/_mask.tif | exit 0; outputs ok | **PASS** |
| `9.aux` | 'aux.tif' in a dir with spaces+Cyrillic -> outputs aux_prob/_mask.tif | exit 0; outputs ok | **PASS** |
| `9.con` | 'con.tif' in a dir with spaces+Cyrillic -> outputs con_prob/_mask.tif | exit 0; outputs ok | **PASS** |
| `9.nul` | 'nul.tif' in a dir with spaces+Cyrillic -> outputs nul_prob/_mask.tif | exit 0; outputs ok | **PASS** |
| `9.com1` | 'com1.tif' in a dir with spaces+Cyrillic -> outputs com1_prob/_mask.tif | exit 0; outputs ok | **PASS** |
| `9.a b.c` | 'a b.c.tif' in a dir with spaces+Cyrillic -> outputs a b.c_prob/_mask.tif | exit 0; outputs ok | **PASS** |
| `9.cli` | exit 0 and all 6 input files counted (files=6) | exit 0; inference.py saw files=6; - | **PASS** |

## Предлагаемые правки

- `3.fdi_rule.B8+B11` (WARN): glint mask (e.g. B11 > 0.02-0.03 over open water / SCL + sun-view geometry) or glint-augmented negatives in training
- `3.fdi_rule.B8` (WARN): glint mask (e.g. B11 > 0.02-0.03 over open water / SCL + sun-view geometry) or glint-augmented negatives in training
- `4.lgbm.noise_s0.002` (WARN): noise augmentation in training / denoised (window-mean) features; sigma 0.002 is ~10% of open-water reflectance
- `4.fdi_rule.noise_s0.002` (WARN): noise augmentation in training / denoised (window-mean) features; sigma 0.002 is ~10% of open-water reflectance
- `6.live_u16dn_off1000` (WARN): inconclusive: no detections on this crop (output identical)
- `7.perm_nodesc` (WARN): log a warning when bands have no descriptions and the order is assumed by count
