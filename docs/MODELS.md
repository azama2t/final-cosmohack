# MODELS — какие модели, где веса, что они умеют и чего не умеют

Короткий указатель. Метрики — [EVALUATION.md](EVALUATION.md), ограничения — [LIMITATIONS.md](LIMITATIONS.md).

| Модель | Задача | Веса / код | Проверка | Статус |
|---|---|---|---|---|
| **LightGBM, пиксельный детектор** | Sentinel-2 → вероятность «плавающий материал» (класс MARIDA Marine Debris против остальных) | `weights/lgbm/` (`model.txt`, `meta.json`); порог — только по val MARIDA | test MARIDA F1 0.871 | основная модель карты |
| Бейзлайны детектора | RandomForest, окно FDI × NDVI, U-Net авторов MARIDA (не переобучали) | `scripts/case/detector_compare.py`, `scripts/case/unet_baseline.py` | F1 0.771 / 0.042 / 0.501 | только для сравнения |
| Концентрация шт./км² | полевые данные → C = N/A с интервалом; прогноз для нового места | `src/macroplastic/case/concentration.py`, `weights/case_conc/*.json` | отложенный test: модель не лучше медианы профиля | на карте — **медиана профиля** |
| Счётчик предметов по фото | Faster R-CNN, фото с воды/воздуха → число предметов на кадре | `weights/photo_count/` | test FML MAE 0.59 шт. на кадр | отдельный режим «Счёт по фото», не спутник |
| Флаг «вероятно органика» | NDVI / FAI поверх бинарного детектора | [ORGANIC.md](ORGANIC.md) | не проверен на разметке | эксперимент, не классификация водорослей |
| Дрейф | сценарий переноса по течениям и ветру (HYCOM ESPC-D-V02 + NCEP GFS), горизонт ≤ 72 ч | `scripts/run_drift.py`, `src/macroplastic/drift/` (OpenDrift, ансамбль частиц; коридор 50 % / 90 %) | 7/18 против «нулевого дрейфа» 7/18 | модельный сценарий, не наблюдаемое перемещение |
| Нефть | пятна нефти по MADOS | [OIL.md](OIL.md) | эксперимент | выключено по умолчанию |

Отвергнутые и недоказанные варианты (детектор v2 на новых метках, калибровка по ячейкам, синтетика) — [HYPOTHESES.md](HYPOTHESES.md), [PIPELINE.md](PIPELINE.md) и отчёт [../reports/report.pdf](../reports/report.pdf).
