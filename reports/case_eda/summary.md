# EDA реестра наблюдений (task/macroplastic_marine_samples.csv)

Строк 935, полей 56, событий 318. Генерирует `scripts/case/eda_samples.py`.

## 1. Состав

| source_id | measurement_profile | item_observation/object_context | transect_density/all_litter | transect_density/fisheries_litter_category | transect_density/plastic_category | transect_density/total_plastic |
|---|---|---|---|---|---|---|
| S1_GPGP2018 | S1_aerial_GT50 | 0 | 0 | 0 | 81 | 16 |
| S1_GPGP2018 | S1_trawl_5_to_50 | 0 | 0 | 0 | 0 | 83 |
| S1_GPGP2018 | S1_trawl_GT5_F | 0 | 0 | 0 | 6 | 0 |
| S1_GPGP2018 | S1_trawl_GT5_H | 0 | 0 | 0 | 70 | 0 |
| S1_GPGP2018 | S1_trawl_GT5_N | 0 | 0 | 0 | 94 | 0 |
| S2_SARGASSO_MSM41 | S2_visual_GT2 | 197 | 0 | 0 | 70 | 63 |
| S3_SE_NORTH_SEA | S3_visual_GT2 | 140 | 41 | 41 | 0 | 0 |
| S4_BLACK_SEA_DOORS3 | S4_visual_GT2_5 | 0 | 33 | 0 | 0 | 0 |

## 2. Пропуски, % строк по источнику (ключевые поля)

| field | S1_GPGP2018 | S2_SARGASSO_MSM41 | S3_SE_NORTH_SEA | S4_BLACK_SEA_DOORS3 |
|---|---|---|---|---|
| time_start_utc | 28 | 0 | 0 | 100 |
| time_end_utc | 28 | 0 | 0 | 100 |
| latitude | 0 | 0 | 0 | 0 |
| lat_start | 0 | 0 | 0 | 100 |
| lat_end | 28 | 0 | 0 | 100 |
| sampled_area_km2 | 0 | 0 | 0 | 100 |
| transect_length_km | 28 | 0 | 0 | 100 |
| transect_width_m | 28 | 0 | 0 | 100 |
| items_count | 28 | 0 | 0 | 100 |
| density_numerator_items | 28 | 60 | 63 | 100 |
| concentration_items_km2 | 4 | 60 | 63 | 0 |
| sea_state_beaufort | 28 | 100 | 100 | 100 |
| wind_speed_kn | 29 | 100 | 100 | 100 |
| parent_sample_id | 61 | 19 | 18 | 100 |

## 3. Даты, время, география

| source_id | rows | events | date_min | date_max | n_dates | time_known_pct | lat_min | lat_max | lon_min | lon_max |
|---|---|---|---|---|---|---|---|---|---|---|
| S1_GPGP2018 | 350 | 181 | 2015-07-25 | 2016-10-06 | 26 | 72 | 28.99 | 33.79 | -143.8 | -128.8 |
| S2_SARGASSO_MSM41 | 330 | 63 | 2015-04-01 | 2015-04-26 | 26 | 100 | 22.56 | 32.24 | -70 | -58.01 |
| S3_SE_NORTH_SEA | 222 | 41 | 2014-04-03 | 2016-04-10 | 12 | 100 | 53.69 | 55.88 | 3.42 | 8.14 |
| S4_BLACK_SEA_DOORS3 | 33 | 33 | 2024-06-02 | 2024-06-18 | 12 | 0 | 41.6 | 43.61 | 29.21 | 41.68 |

Длительность наблюдения (трансекты; конец < начала = переход через полночь, +24 ч):

| measurement_profile | n | dur_known | dur_med_h | dur_max_h | midnight_wrap |
|---|---|---|---|---|---|
| S1_aerial_GT50 | 97 | 0 |  |  | 0 |
| S1_trawl_5_to_50 | 83 | 83 | 3.233 | 4.017 | 10 |
| S1_trawl_GT5_F | 6 | 6 | 2.942 | 3.533 | 0 |
| S1_trawl_GT5_H | 70 | 70 | 3.225 | 3.85 | 6 |
| S1_trawl_GT5_N | 94 | 94 | 3.258 | 4.017 | 11 |
| S2_visual_GT2 | 133 | 133 | 1 | 1.85 | 0 |
| S3_visual_GT2 | 82 | 82 | 0.75 | 3.85 | 0 |
| S4_visual_GT2_5 | 33 | 0 |  |  | 0 |

## 4. События (строки одного события зависимы)

| source | events | rows_med | rows_max | multi_row_events | events_with_items | events_per_day_med | events_per_day_max |
|---|---|---|---|---|---|---|---|
| S1_GPGP2018 | 181 | 2 | 5 | 120 | 0 | 6 | 16 |
| S2_SARGASSO_MSM41 | 63 | 5 | 15 | 63 | 60 | 2 | 4 |
| S3_SE_NORTH_SEA | 41 | 5 | 13 | 41 | 39 | 4 | 6 |
| S4_BLACK_SEA_DOORS3 | 33 | 1 | 1 | 0 | 0 | 2 | 8 |

Несколько событий в один день = потенциально одна спутниковая сцена -> группа сплита «день×район», не event_id.

## 5. Кандидаты целевой величины (transect_density)

| measurement_profile | target_scope | rows | events | conc_known | zeros | area_known | numerator_known | time_known | median | p90 | max | date_min | date_max |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S1_aerial_GT50 | plastic_category | 81 | 30 | 81 | 2 | 81 | 81 | 0 | 0.3779 | 1.51 | 5.113 | 2016-10-02 | 2016-10-06 |
| S1_aerial_GT50 | total_plastic | 16 | 16 | 1 | 1 | 16 | 1 | 0 | 0 | 0 | 0 | 2016-10-02 | 2016-10-06 |
| S1_trawl_5_to_50 | total_plastic | 83 | 83 | 83 | 0 | 83 | 0 | 83 | 633.7 | 1412 | 2209 | 2015-07-25 | 2015-08-18 |
| S1_trawl_GT5_F | plastic_category | 6 | 6 | 6 | 0 | 6 | 6 | 6 | 16.58 | 23.65 | 24.76 | 2015-08-01 | 2015-08-17 |
| S1_trawl_GT5_H | plastic_category | 70 | 70 | 70 | 0 | 70 | 70 | 70 | 346.8 | 1040 | 1425 | 2015-07-25 | 2015-08-17 |
| S1_trawl_GT5_N | plastic_category | 94 | 94 | 94 | 0 | 94 | 94 | 94 | 203.7 | 648.8 | 1745 | 2015-07-27 | 2015-08-18 |
| S2_visual_GT2 | plastic_category | 70 | 63 | 70 | 0 | 70 | 70 | 70 | 31.74 | 98.87 | 222.7 | 2015-04-01 | 2015-04-26 |
| S2_visual_GT2 | total_plastic | 63 | 63 | 63 | 0 | 63 | 63 | 63 | 36.09 | 100.3 | 222.7 | 2015-04-01 | 2015-04-26 |
| S3_visual_GT2 | all_litter | 41 | 41 | 41 | 0 | 41 | 41 | 41 | 29.5 | 52.3 | 195.1 | 2014-04-03 | 2016-04-10 |
| S3_visual_GT2 | fisheries_litter_category | 41 | 41 | 41 | 35 | 41 | 41 | 41 | 0 | 4.745 | 13.25 | 2014-04-03 | 2016-04-10 |
| S4_visual_GT2_5 | all_litter | 33 | 33 | 33 | 0 | 0 | 0 | 0 | 224.9 | 597.9 | 976 | 2024-06-02 | 2024-06-18 |

Нули и их область (`zero_scope`):

| zero_scope | rows |
|---|---|
| fisheries_litter_category;S3_visual_GT2;fisheries-related litter (nets, ropes, floats) | 35 |
| plastic_category;S1_aerial_GT50;Unknown | 2 |
| total_plastic;S1_aerial_GT50;total megaplastic | 1 |

## 6. Проверка C = N/A

Контроль: 12 / 0.20 = 60 шт./км²; модель 75 -> |ошибка| = 15 шт./км².

| measurement_profile | rows | max_abs_err | max_rel_err |
|---|---|---|---|
| S1_aerial_GT50 | 82 | 4.611e-12 | 3.558e-12 |
| S1_trawl_GT5_F | 6 | 3.746e-11 | 3.259e-12 |
| S1_trawl_GT5_H | 70 | 5.203e-09 | 5.319e-12 |
| S1_trawl_GT5_N | 94 | 4.837e-09 | 4.59e-12 |
| S2_visual_GT2 | 133 | 4.332e-10 | 4.302e-12 |
| S3_visual_GT2 | 82 | 0.9667 | 0.008878 |

Строки без N или A (C = N/A проверить нельзя; published estimate):

| measurement_profile | rows_without_N_or_A |
|---|---|
| S1_aerial_GT50 | 15 |
| S1_trawl_5_to_50 | 83 |
| S4_visual_GT2_5 | 33 |

## 7. quality_flags по источникам (строки)

| flag | S1_GPGP2018 | S2_SARGASSO_MSM41 | S3_SE_NORTH_SEA | S4_BLACK_SEA_DOORS3 |
|---|---|---|---|---|
| (пусто) | 84 | 0 | 6 | 0 |
| all_litter_not_plastic | 0 | 0 | 41 | 33 |
| author_area_replaces_endpoint_distance | 0 | 330 | 0 | 0 |
| category_dictionary_corrected | 76 | 0 | 0 | 0 |
| companion_count_differs_sum_float_items | 0 | 51 | 0 | 0 |
| interrupted_transect_summary_area | 0 | 23 | 0 | 0 |
| numerator_area_unavailable | 0 | 0 | 0 | 33 |
| object_row_match_ambiguous_or_unresolved | 0 | 29 | 0 | 0 |
| object_time_is_parent_interval | 0 | 197 | 140 | 0 |
| size_filter_corrected | 29 | 0 | 0 | 0 |
| source_not_revalidated_in_repair | 0 | 0 | 0 | 33 |
| source_object_row_not_reconstructed | 0 | 0 | 140 | 0 |
| source_total_vs_object_count_conflict | 94 | 0 | 0 | 0 |
| start_coordinate_corrected_to_midpoint | 170 | 0 | 0 | 0 |
| summary_area_rounded_differs_from_segment_sum | 0 | 23 | 0 | 0 |
| zero_only_for_defined_scope | 3 | 0 | 35 | 0 |

## 8. Календарь миссий (без сети, верхняя граница)

E_pm{k} — ожидаемое число событий, у которых есть хотя бы один номинальный пролёт S2/Landsat в окне ±k сут (1 - exp(-λ), λ = (2k+1)·Σ 1/revisit; S2 10 сут на спутник, Landsat 16 сут). Не учитывает план съёмки (открытый океан S2 не снимает систематически), облака, блик и дрейф.

| source | events | s2_calendar | landsat_calendar | E_pm0 | E_pm1 | E_pm3 | E_pm5 |
|---|---|---|---|---|---|---|---|
| S1_GPGP2018 | 181 | 181 | 181 | 27.1 | 69.8 | 123 | 150.7 |
| S2_SARGASSO_MSM41 | 63 | 0 | 63 | 3.8 | 10.8 | 22.3 | 31.3 |
| S3_SE_NORTH_SEA | 41 | 3 | 41 | 2.8 | 7.7 | 15.5 | 21.4 |
| S4_BLACK_SEA_DOORS3 | 33 | 33 | 33 | 9.2 | 20.6 | 29.6 | 32.1 |
| ИТОГО | 318 | 217 | 318 | 42.9 | 108.9 | 190.4 | 235.5 |

## 9. Размер предмета против пикселя 10 м

| profile | min_item_cm | pixel_10m_area_m2 | item_to_pixel_area_ratio_at_min | items_per_10m_px_median | items_per_10m_px_max |
|---|---|---|---|---|---|
| S1_trawl_5_to_50 | 5 | 100 | 2.5e-05 | 0.06337 | 0.2209 |
| S1_trawl_GT5_H | 5 | 100 | 2.5e-05 | 0.03468 | 0.1425 |
| S1_trawl_GT5_N | 5 | 100 | 2.5e-05 | 0.02037 | 0.1745 |
| S1_trawl_GT5_F | 5 | 100 | 2.5e-05 | 0.001658 | 0.002476 |
| S1_aerial_GT50 | 50 | 100 | 0.0025 | 3.755e-05 | 0.0005113 |
| S2_visual_GT2 | 2 | 100 | 4e-06 | 0.003303 | 0.02227 |
| S3_visual_GT2 | 2 | 100 | 4e-06 | 0.00082 | 0.01951 |
| S4_visual_GT2_5 | 2.5 | 100 | 6.25e-06 | 0.02249 | 0.0976 |

Даже максимум реестра (~2200 шт./км²) даёт ~0.22 предмета на пиксель 10×10 м: отдельные предметы невидимы, связь «пиксель -> плотность» возможна только через скопления/полосы.

## 10. Поля-утечки (исключены из признаков)

| field | non_null_rows |
|---|---|
| concentration_items_km2 | 583 |
| concentration_g_km2 | 335 |
| concentration_value_orig | 935 |
| concentration_unit_orig | 935 |
| items_count | 804 |
| density_numerator_items | 467 |
| source_object_filtered_items | 427 |
| source_reported_total_items | 427 |
| reported_concentration_items_km2 | 935 |
| reported_concentration_g_km2 | 350 |
| parent_concentration_items_km2 | 337 |
| parent_sample_id | 584 |
| zero_scope | 38 |
