# Label forensics — fire BS (burn severity): S2 L2A pre+post -> severity mask

Файлов: 224, групп (сцены/события) 224, GroupKFold k=3, выборка пикселей 251,961 (≤300 на класс×файл, веса = count/taken → F1 на полной популяции). Время 114 с.

Признаки (42): pre_B2, pre_B3, pre_B4, pre_B5, pre_B6, pre_B7, pre_B8A, pre_B11, pre_B12, pre_NDVI, pre_NRD, pre_NDWI, pre_NBR, pre_NDMI, pre_FDI, pre_FAI, pre_BSI, post_B2, post_B3, post_B4, post_B5, post_B6, post_B7, post_B8A, post_B11, post_B12, post_NDVI, post_NRD, post_NDWI, post_NBR, post_NDMI, post_FDI, post_FAI, post_BSI, dNDVI(pre-post), dNRD(pre-post), dNDWI(pre-post), dNBR(pre-post), dNDMI(pre-post), dFDI(pre-post), dFAI(pre-post), dBSI(pre-post)

## Выводы (автоматически)

- **0** (0): МАЖОРИТАРНЫЙ класс (90% размеченных пикселей) — высокий F1 тривиален, смотреть AUC 0.972 и F1 остальных классов [дерево-5 0.95, LGBM 0.97]
- **1** (1): частично выводима из спектра пикселя (лучший CV F1 0.53) [дерево-3 0.37, дерево-5 0.42, LGBM 0.53, AUC 0.962]; лучший порог `dNBR(pre-post) >= 0.06993` CV F1 0.2385
- **2** (2): частично выводима из спектра пикселя (лучший CV F1 0.67) [дерево-3 0.35, дерево-5 0.59, LGBM 0.67, AUC 0.985]; лучший порог `dNBR(pre-post) >= 0.1974` CV F1 0.432
- **3** (3): хорошо выводима из спектра пикселя (лучший CV F1 0.80) [дерево-3 0.69, дерево-5 0.75, LGBM 0.80, AUC 0.997]; лучший порог `dNBR(pre-post) >= 0.396` CV F1 0.7002

## Выводимость из спектра (one-vs-rest, CV F1 на полной популяции пикселей, порог подобран на ДРУГИХ фолдах)

AUC — ROC-AUC OOF-скоров (не зависит от доли класса; 0.5 = случайно, 1 = разделимо). F1 зависит от доли класса: для редких классов (MD) даже хороший AUC даёт низкий F1 из-за ложных срабатываний на больших классах.

| класс | px (полн.) | файлов | дерево3 | дерево4 | дерево5 | AUC дерево5 | LGBM (порог CV) | AUC LGBM | лучший порог | CV F1 порога |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 0 | 52,811,255 | 224 | 0.769±0.25 | 0.945±0.01 | 0.954±0.00 | 0.953 | 0.965 | 0.972 | `post_NBR >= -0.3677` | 0.949 |
| 1 1 | 2,362,871 | 224 | 0.374±0.05 | 0.264±0.19 | 0.425±0.02 | 0.937 | 0.526 | 0.962 | `dNBR(pre-post) >= 0.06993` | 0.238 |
| 2 2 | 2,192,271 | 222 | 0.353±0.25 | 0.371±0.26 | 0.591±0.01 | 0.973 | 0.671 | 0.985 | `dNBR(pre-post) >= 0.1974` | 0.432 |
| 3 3 | 1,353,859 | 202 | 0.694±0.01 | 0.717±0.02 | 0.750±0.02 | 0.992 | 0.800 | 0.997 | `dNBR(pre-post) >= 0.396` | 0.700 |

## Мультиклассовые модели (CV F1 по классам: argmax и LGBM с порогом)

| класс | tree5_argmax | lgbm_argmax | lgbm_thr |
|---|---|---|---|
| 0 0 | 0.911 | 0.935 | 0.965 |
| 1 1 | 0.426 | 0.472 | 0.526 |
| 2 2 | 0.547 | 0.653 | 0.671 |
| 3 3 | 0.751 | 0.788 | 0.800 |

## Геометрия разметки

interior = доля пикселей класса, переживших эрозию 3×3; opening_keeps = доля, переживших открытие 3×3 (≈1 → гладкие полигоны/морфология; ≈0 → точки/линии толщиной 1–2 px); grad edge/int = медиана градиента яркости на границе метки / внутри (≫1 → край метки идёт по краю объекта; ≈1 → край нарисован не по спектру); recall edge/int — полнота дерева-5 на краевых/внутренних пикселях; border8 = доля пикселей класса в 8-px рамке тайла / ожидаемая (≫1 — метки у краёв, ≪1 — чипы вырезаны вокруг объектов); centroid = средний сдвиг центра масс класса от центра тайла (0..0.71).

| класс | компонент | медиана размера | p90 | 1-px доля | rect мед. | interior | opening_keeps | closing_adds | grad edge/int | grad edge/all | grad ring/all | recall edge | recall int | border1 | border8 | centroid |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 0 | 2703 | 25.0 | 1703.8 | 0.20 | 0.56 | 0.97 | 1.00 | 0.00 | 1.28 | 1.30 | 1.16 | 0.88 | 0.93 | 1.05 | 1.05 | 0.03 |
| 1 1 | 41187 | 3.0 | 50.0 | 0.34 | 0.48 | 0.57 | 0.81 | 0.09 | 1.74 | 1.58 | 1.22 | 0.91 | 0.95 | 0.52 | 0.58 | 0.29 |
| 2 2 | 18816 | 5.0 | 85.0 | 0.16 | 0.57 | 0.63 | 0.89 | 0.06 | 1.51 | 1.48 | 1.24 | 0.88 | 0.96 | 0.53 | 0.57 | 0.31 |
| 3 3 | 6815 | 10.0 | 209.6 | 0.08 | 0.65 | 0.72 | 0.94 | 0.04 | 1.33 | 1.29 | 1.40 | 0.92 | 0.99 | 0.49 | 0.49 | 0.34 |

## Лучшие одиночные пороги (топ-3 признака на класс)

- 0 0: `post_NBR >= -0.3677` — CV F1 0.9489, in-sample F1 0.951
- 0 0: `post_B12 <= 1.454e+04` — CV F1 0.947, in-sample F1 0.947
- 0 0: `post_FDI >= -6262` — CV F1 0.947, in-sample F1 0.947
- 1 1: `dNBR(pre-post) >= 0.06993` — CV F1 0.2385, in-sample F1 0.2385
- 1 1: `dNDMI(pre-post) >= 0.04406` — CV F1 0.1747, in-sample F1 0.1748
- 1 1: `dFAI(pre-post) >= 157.7` — CV F1 0.1463, in-sample F1 0.1484
- 2 2: `dNBR(pre-post) >= 0.1974` — CV F1 0.432, in-sample F1 0.4329
- 2 2: `dNDMI(pre-post) >= 0.1078` — CV F1 0.3012, in-sample F1 0.3013
- 2 2: `post_NBR <= -0.2569` — CV F1 0.2585, in-sample F1 0.262
- 3 3: `dNBR(pre-post) >= 0.396` — CV F1 0.7002, in-sample F1 0.7006
- 3 3: `post_NBR <= -0.3687` — CV F1 0.494, in-sample F1 0.4958
- 3 3: `dNDMI(pre-post) >= 0.2184` — CV F1 0.4515, in-sample F1 0.4523

## Правила деревьев глубины 3

### Мультикласс

```
|--- dNBR(pre-post) <= 0.3808
|   |--- dNBR(pre-post) <= 0.1830
|   |   |--- dNBR(pre-post) <= 0.0620
|   |   |   |--- class: 0
|   |   |--- dNBR(pre-post) >  0.0620
|   |   |   |--- class: 1
|   |--- dNBR(pre-post) >  0.1830
|   |   |--- dNBR(pre-post) <= 0.2049
|   |   |   |--- class: 2
|   |   |--- dNBR(pre-post) >  0.2049
|   |   |   |--- class: 2
|--- dNBR(pre-post) >  0.3808
|   |--- post_NBR <= -0.1620
|   |   |--- dNBR(pre-post) <= 0.3989
|   |   |   |--- class: 3
|   |   |--- dNBR(pre-post) >  0.3989
|   |   |   |--- class: 3
|   |--- post_NBR >  -0.1620
|   |   |--- pre_B5 <= 1038.5000
|   |   |   |--- class: 2
|   |   |--- pre_B5 >  1038.5000
|   |   |   |--- class: 3
```

### 0 0 (1 = класс)

```
|--- dNBR(pre-post) <= 0.0657
|   |--- dNBR(pre-post) <= 0.0620
|   |   |--- dNBR(pre-post) <= 0.0505
|   |   |   |--- class: True
|   |   |--- dNBR(pre-post) >  0.0505
|   |   |   |--- class: True
|   |--- dNBR(pre-post) >  0.0620
|   |   |--- post_BSI <= 0.0858
|   |   |   |--- class: True
|   |   |--- post_BSI >  0.0858
|   |   |   |--- class: True
|--- dNBR(pre-post) >  0.0657
|   |--- post_NBR <= 0.2200
|   |   |--- pre_B11 <= 1013.5000
|   |   |   |--- class: True
|   |   |--- pre_B11 >  1013.5000
|   |   |   |--- class: False
|   |--- post_NBR >  0.2200
|   |   |--- post_NBR <= 0.2685
|   |   |   |--- class: True
|   |   |--- post_NBR >  0.2685
|   |   |   |--- class: True
```

### 1 1 (1 = класс)

```
|--- dNBR(pre-post) <= 0.0620
|   |--- dNBR(pre-post) <= 0.0504
|   |   |--- dNBR(pre-post) <= 0.0378
|   |   |   |--- class: False
|   |   |--- dNBR(pre-post) >  0.0378
|   |   |   |--- class: False
|   |--- dNBR(pre-post) >  0.0504
|   |   |--- dFDI(pre-post) <= -116.2292
|   |   |   |--- class: False
|   |   |--- dFDI(pre-post) >  -116.2292
|   |   |   |--- class: False
|--- dNBR(pre-post) >  0.0620
|   |--- dNBR(pre-post) <= 0.2235
|   |   |--- post_NBR <= 0.2217
|   |   |   |--- class: True
|   |   |--- post_NBR >  0.2217
|   |   |   |--- class: False
|   |--- dNBR(pre-post) >  0.2235
|   |   |--- dNBR(pre-post) <= 0.2995
|   |   |   |--- class: False
|   |   |--- dNBR(pre-post) >  0.2995
|   |   |   |--- class: False
```

### 2 2 (1 = класс)

```
|--- dNBR(pre-post) <= 0.1688
|   |--- dNBR(pre-post) <= 0.1500
|   |   |--- dNBR(pre-post) <= 0.1241
|   |   |   |--- class: False
|   |   |--- dNBR(pre-post) >  0.1241
|   |   |   |--- class: False
|   |--- dNBR(pre-post) >  0.1500
|   |   |--- pre_FDI <= -307.6552
|   |   |   |--- class: False
|   |   |--- pre_FDI >  -307.6552
|   |   |   |--- class: False
|--- dNBR(pre-post) >  0.1688
|   |--- pre_B12 <= 873.5000
|   |   |--- pre_B11 <= 843.5000
|   |   |   |--- class: False
|   |   |--- pre_B11 >  843.5000
|   |   |   |--- class: True
|   |--- pre_B12 >  873.5000
|   |   |--- dNBR(pre-post) <= 0.4115
|   |   |   |--- class: True
|   |   |--- dNBR(pre-post) >  0.4115
|   |   |   |--- class: True
```

### 3 3 (1 = класс)

```
|--- dNBR(pre-post) <= 0.3518
|   |--- dNBR(pre-post) <= 0.3227
|   |   |--- dNBR(pre-post) <= 0.2832
|   |   |   |--- class: False
|   |   |--- dNBR(pre-post) >  0.2832
|   |   |   |--- class: False
|   |--- dNBR(pre-post) >  0.3227
|   |   |--- dNDMI(pre-post) <= 0.1690
|   |   |   |--- class: True
|   |   |--- dNDMI(pre-post) >  0.1690
|   |   |   |--- class: False
|--- dNBR(pre-post) >  0.3518
|   |--- post_FDI <= -176.1449
|   |   |--- pre_B2 <= 4046.5000
|   |   |   |--- class: True
|   |   |--- pre_B2 >  4046.5000
|   |   |   |--- class: False
|   |--- post_FDI >  -176.1449
|   |   |--- post_NDWI <= -0.3664
|   |   |   |--- class: True
|   |   |--- post_NDWI >  -0.3664
|   |   |   |--- class: False
```
