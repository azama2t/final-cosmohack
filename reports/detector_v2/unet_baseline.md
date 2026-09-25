# Официальный U-Net MARIDA (Kikaki et al. 2022) — бейзлайн на той же проверке, что LightGBM

Скрипт: `scripts/case/unet_baseline.py` (CPU, 8 потоков torch). Числа: `reports/detector_v2/unet_baseline.json`.
**Здесь только MARIDA val.** MARIDA test не считался — команда для оркестратора ниже.

## Откуда веса (ничего не обучали)
- Официальные веса авторов, ссылка из README репозитория github.com/marine-debris/marine-debris.github.io
  («To download the pretrained Unet model on MARIDA press here») → `https://pithos.okeanos.grnet.gr/public/lxh8hL4zvuSKds2BdVnMd2`,
  архив `MARIDA_models.zip` 90 776 944 Б (sha256 `c17d6771…c6e7`), скачивается без авторизации (проверено 26.09 01:00).
- Взят член `MARIDA_models/unet/trained_models/44/model.pth` (3,4 МБ) → `weights_exp/unet_marida/model_epoch44.pth`
  (sha256 `7a690027…1b37`). Эпоха 44 — путь по умолчанию в официальном `evaluation.py`. В `train.py` модель сохраняется
  каждую эпоху (45 эпох, lr 2e-4, batch 5, веса классов 1/ln(1.03+p)), val считается каждую эпоху; как авторы выбрали 44,
  в коде не записано.
- Своё обучение не понадобилось → GPU-очередь не использовалась.

## Модель и предобработка = официальный код
`semantic_segmentation/unet/unet.py` (UNet 11 каналов → 11 классов, hidden 16, те же имена слоёв — `load_state_dict` строгий, прошёл),
`dataloader.py` (классы 12–15 → Marine Water; NaN → среднее канала; (x − mean)/std со статистикой авторов),
`evaluation.py` (softmax, argmax; класс 0 = Marine Debris). Патчи 256×256 подаются целиком, как у авторов.

## Постановка оценки — та же, что у LightGBM
Функция `evaluate` из `scripts/case/detector_compare.py` (не новая метрика): пиксели Marine Debris против остального размеченного
фона (cl > 0), суммирование по патчам; пиксели с NaN — «не мусор»; неразмеченные — отдельно; ДИ 95 % — бутстреп по сценам,
2000 повторов, `default_rng(0)`. LightGBM и RF — их сохранённые маски val (`data/case/detector_preds/val_preds.npz`), посчитанные
той же функцией: **LightGBM val F1 0.9226 [0.877, 0.9701] совпал с `reports/lgbm_final_test.json` до знака** (флаг
`reproducibility_lgbm_val.equal = true`).

## MARIDA val (328 патчей, 12 сцен, 1 075 пикселей мусора)

| Модель | Настройка (откуда) | P | R | F1 | ДИ 95 % F1 | IoU | Помечено неразмеч. |
|---|---|---|---|---|---|---|---|
| **LightGBM + окна (основная)** | P ≥ 0.63 (val) | 0.930 | 0.915 | **0.923** | [0.877, 0.970] | 0.856 | 0.025 % (5 365) |
| RandomForest, параметры статьи | argmax | 0.890 | 0.841 | 0.865 | [0.772, 0.949] | 0.762 | 0.070 % |
| **U-Net MARIDA, официальные веса** | argmax (официально, без настройки) | 0.686 | 0.708 | **0.697** | [0.490, 0.858] | 0.534 | 0.059 % (12 481) |
| U-Net MARIDA, порог | P(MD) ≥ 0.34 (подобран на этом же val → оптимистично) | 0.730 | 0.726 | 0.728 | [0.535, 0.877] | 0.572 | 0.028 % (5 918) |

Парный бутстреп по сценам val:
- LightGBM − U-Net(argmax): ΔF1 = **+0.226**, ДИ [+0.106, +0.394], доля повторов с ΔF1 ≤ 0 — 0 %; LightGBM лучше в 9 сценах из 10 с мусором, хуже в 1.
- LightGBM − U-Net(порог): ΔF1 = +0.195, ДИ [+0.086, +0.343].
- U-Net(argmax) − RF(argmax): ΔF1 = −0.168, ДИ [−0.293, −0.084] — U-Net слабее и RF, как в статье MARIDA (RF с индексами сильнее U-Net).

Ложные срабатывания на val (пикселей, доля класса): U-Net(argmax) — судна 127 (9.5 %), Natural Organic Material 78 (85 %),
Sparse Sargassum 41 (10.7 %), облака 48, Mixed Water 14, пена 11, волны 9, кильватеры 8; LightGBM — NatOM 53, судна 10,
Sparse Sargassum 6, Mixed Water 5. Главный источник ошибок U-Net — судна и саргассум, которых у LightGBM почти нет.

Время: инференс val 328 патчей — 13 с на CPU (8 потоков), весь прогон 17 с.

## MARIDA test — НЕ посчитан, команда для оркестратора (один раз)
```
$env:CUDA_VISIBLE_DEVICES=""; .venv\Scripts\python.exe scripts\case\unet_baseline.py test --once
```
Порог U-Net(порог) берётся из `unet_baseline.json` (val), sha256 весов сверяется с val; результат → `reports/detector_v2/unet_test.json`
(повторный запуск запрещён скриптом). В тот же файл пишутся LightGBM/RF на test по маскам `test_preds.npz` и флаг совпадения их F1/ДИ
с `reports/case_detector/metrics.json`, плюс парный ΔF1 LightGBM − U-Net на test.

## 22 сцены пар кейса (без гармонизации)
Команда: `unet_baseline.py pairs`. Тот же конвейер, что у LightGBM в `reports/case_pairs/detector_current.md`: функция
`pair_quality.process_s2` с U-Net в роли предсказателя — те же вырезки S2 L2A, маски качества, пригодная вода (`water_ok`),
8-связные объекты, отброс объектов у облаков/теней, растровая полоса обследования; `harmonize = none`. Крупные вырезки —
тайлами 512 с перекрытием 64. Решения accept/reject по качеству совпали с LightGBM во всех 22 вырезках (11 accept).

| Детектор | Объектов на вырезках | В полосах | Вырезок с объектами | Пикселей |
|---|---|---|---|---|
| LightGBM (текущий, P ≥ 0.63) | 5 | 0 | 1 | — |
| U-Net MARIDA, argmax | **253** | **0** | 3 | 393 |
| U-Net MARIDA, P ≥ 0.34 | 266 | 0 | 3 | 395 |

- 250 из 253 — одна сцена `S3_HE460_MarLitter_transect03` (Северное море, 2016; там же 5 объектов LightGBM): рассыпанные по всей
  открытой воде одиночные пиксели (размеры 1 px — 157, 2 px — 58, 3 px — 26, 4–5 px — 9; 0.009 % пригодной воды; медиана P(MD) 0.43),
  картина «соль-перец», ни одного в полосе. Остальные 3 — S4_DOORS3_T2 (2) и T25 (1), вне полос.
- Вывод: на реальных сценах кейса официальный U-Net даёт в 50 раз больше срабатываний, чем LightGBM, и тоже 0 в полосах;
  это согласуется с его более высокой долей помеченных неразмеченных пикселей на val (0.059 % против 0.025 %).
- По вырезкам: `out/l99_unet_pairs/pairs.json`, маски/вероятности — `out/l99_unet_pairs/{argmax,prob}/<событие>/` (не для git, 65 МБ).
