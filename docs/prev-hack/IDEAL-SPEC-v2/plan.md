# plan.md — как строим

## 1. Архитектура одним взглядом

```mermaid
flowchart LR
  subgraph data[data/test или data/train]
    AFc[AF-чипы VIIRS+AUX]
    BSc[BS-чипы S2 pre/post, S1 pre/post, AUX]
    META[meta.csv / sample_submission.csv]
  end
  AFc --> IO[io.py: чтение (C,H,W)]
  BSc --> IO
  IO --> AF[AF: кандидаты день/ночь → 110 признаков → LightGBM ×5 → порог]
  IO --> BS[BS: признаки → UNet-ансамбль → стек LGBM-2 → пороги → постобработка]
  AF --> SUB[submission.py: RLE, порядок шаблона, формат примера]
  BS --> SUB
  META --> SUB
  SUB --> CSV[(submission.csv)]
  subgraph svc[service]
    BUILD[build_service_data.py: train-сцены → GeoJSON-слои, площади в UTM, контуры событий]
    API[FastAPI /api/v1/*]
    UI[Leaflet static]
  end
  IO --> BUILD
  AF -.слой Модель.-> BUILD
  BS -.слой Модель.-> BUILD
  BUILD --> API --> UI
```

Одна точка входа инференса (`inference.py`) → конвейер (`src/firemon/pipeline.py`) с реестрами бэкендов AF/BS и откатом на правила. Обучение — `train.py`. Сервис независим от инференса: читает предрассчитанные слои.

## 2. Структура репозитория

```
repo/
  README.md  LICENSE  pyproject.toml  .gitignore  .dockerignore  Dockerfile
  requirements.txt  requirements-cpu.txt  requirements-base.txt
  inference.py            # CLI, коды выхода 0/1/2
  train.py                # --module af|bs|all, --bs-stages, --weights-dir, --fast
  configs/
    inference.yaml        # af.model/fallback, bs.variant/model/fallback, workers, seed
    bs_inference.yaml     # веса UNet, стек, ворота, пороги, постобработка
    af_lgbm.yaml          # параметры LightGBM AF
    folds_af.csv  folds_bs.csv   # фиксированная валидация (+ колонка temporal_split)
  src/firemon/
    io.py  rle.py  metrics.py  submission.py  splits.py  utils.py  pipeline.py
    features/af_features.py  features/bs_features.py
    models/af/  (candidates.py, train.py, predictor.py, rules.py)
    models/bs/  (rules.py, lgbm.py, unet.py, stack.py, ensemble.py, postprocess.py, predictor.py)
  service/  app.py  core.py  vectorize.py  build.py  predictors.py  static/  data/
  scripts/  check_data.py  check_submission.py  compare_submissions.py  make_fake_test.py
            build_service_data.py  holdout_eval.py  make_report_figures.py  make_deck.py  screenshots.py
  eda/      eda_af.py  eda_bs.py  gt_recon.py  test_vs_train.py  tables/  figures/
  reports/  report.md  experiments.md  final_numbers.md  eda.md  af_module.md  bs_module.md
            baseline_review.md  service.md  speed.md  tasklog/  figures/
  tests/    fixtures/ (example_submission.csv, sample_submission.csv)
  weights/  # финальные веса (каждый файл < 50 МБ)
  submissions/
  deck/     # presentation.pptx + speech.md
data/  data_cache/  weights_exp/  out/   # вне git (в .gitignore)
```

## 3. Метрика и валидация

### 3.1. Метрика (точно по ТЗ)
Пиксели всех чипов объединяются в общий пул (микро-усреднение):
```
F1_af    = 2PR/(P+R) по пикселям класса 1 на AF-чипах
IoU_burn = IoU бинарной маски (pred>0 vs gt>0) на BS-чипах
mIoU_sev = (IoU(1)+IoU(2)+IoU(3))/3 на BS-чипах
Score    = 0.35·F1_af + 0.35·IoU_burn + 0.30·mIoU_sev
```
Пустой знаменатель (класса нет ни в эталоне, ни в предсказании) → 1. Класс есть в эталоне, нет в предсказании → 0. Пиксели nodata (255) в эталоне исключаются.

### 3.2. Валидация (фиксируется в первый час, не меняется)
- AF: `GroupKFold(5)` по тайлу — группа = одинаковые `(epsg, x_min, y_min, x_max, y_max)` (70 тайлов, каждый снят многократно; без групп утечка).
- BS: `GroupKFold(5)` по пространственным блокам 50×50 км (центр чипа в UTM // 50 000 + epsg).
- Временной сплит: val = чипы 2024 года (по `acq_datetime` / `date_post`), train = остальные годы. В train нет 2025 года, тест — «другая территория и время», поэтому временной сплит важнее группового. Размеры: AF — 78 из 420 чипов (19 %) в val; BS — 82 из 224 (37 %): модели временного сплита BS учатся всего на 142 чипах, temporal-оценки BS шумнее групповых.
- Отчётные числа — OOF по 5 фолдам (групповой) и val-2024 (временной). Правило принятия — constitution §6.

### 3.3. Holdout для сравнения версий
`scripts/holdout_eval.py`: `make_fake_test.py` раскладывает чипы 2024 года из train в тестовую раскладку (`data/test_fake/` + шаблон) → инференс через тот же `pipeline.py`, но с **весами временного сплита** (обучены без 2024 в T2 и T3b, отдельный конфиг `configs/inference_temporal.yaml`) → `out/holdout_submission.csv` (формат сабмита) + `out/holdout_sample.csv` + Score. Повторно ничего не обучать.
- AF: модель временного сплита. BS: UNet временного сплита → вероятности на 2024; стек обучен на групповых OOF-вероятностях только чипов не-2024; пороги и постобработка — из OOF (как для временной колонки журнала; небольшая утечка порогов допустима и записывается).
- Используется для внешнего сравнения решений на одной и той же выборке: все версии проекта оцениваются на одинаковом наборе чипов 2024 года.

## 4. AF — активные очаги (рецепт, без поиска)

Почему так: эталон AF почти совпадает с пороговым алгоритмом VIIRS VNP14 (день и ночь раздельно); 99.6 % огня попадает в кандидаты, которые занимают 2.2 % пикселей. Ложные срабатывания простого порога — на 100 % дневные (пашня 58.5 %, трава 35.4 %), а не техноген. Поэтому «кандидаты по порогам → бустинг на кандидатах», а не CNN (CNN проверена: F1 0.917 против 0.968).

### 4.1. Правило-бейзлайн и fallback (контекстное правило день/ночь)
- День (`solar_zenith ≤ 90`): `I4 > 330 K` и `I4 − I5` выше фона окна; ночь: `I4 > 300 K` и `I4 − I5 > 10 K`. Точные пороги подобрать на OOF за 10 минут; ожидаемо F1 ≈ 0.897 (группой), 0.916 (2024).
- Заглушка первого часа (`I4 > 320 K & I4 − I5 > 15 K`) даёт F1 0.244 (P 0.14, R 0.91) — её цифры идут в отчёт как baseline.

### 4.2. Кандидаты (запас порогов обязателен — тест теплее)
- День: `I4 > 314 K и I4 − I5 > 17 K`; ночь: `I4 > 290 K и I4 − I5 > 5 K`. Полнота кандидатов на train — 99.57 % (не хватает 42 px дневного огня с горячим фоном I5 и ΔT < 17 K); кандидаты — 0.8 % пикселей train (≈ 2 % на тесте, он теплее). Дополнительное правило `I4 > 318 и I5 > 318` добирает 100 %, но даёт лишь +0.002 F1 — ниже порога принятия, не включать.
- NaN в I4/I5 (bow-tie) не заполнять; окна считать с нормировкой на число валидных пикселей; ночью I1–I3 = 0.

### 4.3. Признаки (≈ 110, на каждого кандидата)
- сырые каналы, `I4−I5`, `I4/I5`, зениты, флаг ночи (≈ 10);
- aux: landcover (one-hot ключевых классов), dem, t2m, rh2m, ветер (≈ 9);
- контекст I4 и ΔT=I4−I5 в окнах 5/7/11/15: mean, std, z-score; робастные rmean/rz без горячих соседей; dmax; доля горячих; доля валидных (≈ 56);
- медианы 7/15, окно 3×3, контекст I5 (≈ 10);
- уровень I4 относительно p50/p90/p99 чипа и статистики чипа (≈ 8);
- блик днём: I3, I3/I2, видимые, NDVI; морфология (размер кластера кандидатов), расстояния.

### 4.4. Обучение
- Строки — только кандидаты; **hard negatives** в кольце r = 2 вокруг огня обязательны (без них −0.040 F1).
- LightGBM binary: `learning_rate 0.05, num_leaves 63, min_child_samples 50, feature_fraction 0.8, lambda_l2 1.0, scale_pos_weight 1, deterministic, force_row_wise, seed 42`, до 2000 раундов с early stopping (фактически 105–198 деревьев).
- GroupKFold-5 по тайлу → 5 моделей; инференс — среднее 5 моделей. Отдельно — модель временного сплита (без 2024) для отчёта и holdout.
- Порог по микро-F1 на OOF (ожидаемо ≈ 0.48), один для дня и ночи. Постобработки нет (гистерезис, удаление одиночных, «вода днём» — проверены, прироста нет).

### 4.5. Ожидаемое
OOF F1 0.968 (P 0.958 / R 0.979), temporal 0.960; обучение — 2–3 минуты на CPU (кэш признаков `data_cache/af_train.npz`); инференс 180 чипов ≈ 4 с; веса 5 × ≈ 1 МБ. Абляции для отчёта: без hard negatives −0.040 / −0.049; без контекста −0.010 / −0.013; без aux −0.008 / −0.026.

## 5. BS — гари и степень (рецепт, без поиска)

Почему так: (1) степень в эталоне = пороги **медианного dNBR 3×3**, свои для типа покрова — правило воспроизводит 96–98 % степеней внутри гари; (2) эталон размечает только «свой» пожар и обрезан буфером ≈ 4 км вокруг события, центр которого смещён от центра чипа (медиана 140–160 px в зависимости от определения центра, до ±170 px и больше). Оракул-окружность даёт IoU_burn ≈ 0.62–0.68 против 0.36–0.42 без неё (две независимые оценки с разными определениями; вывод одинаков). Вывод: трудность — выбрать «своё» событие среди всех гарей на снимке. **UNet выбирает регион, бустинг восстанавливает границы и степень, постобработка режет всё дальше R от оценённого центра события.**

### 5.1. Признаки пикселя (`bs_features.py`)
Отражения = uint16 / 10000. Индексы: `NBR=(B8A−B12)/(B8A+B12)`, `NDVI=(B8A−B4)/(B8A+B4)`, `NDWI=(B3−B8A)/(B3+B8A)`, `NBR2=(B11−B12)/(B11+B12)`, `MIRBI=10·B12−9.8·B11+2`, `SAVI=1.5(B8A−B4)/(B8A+B4+0.5)`, `bright=(B2+B3+B4)/3`, `BAI` (post). Группы:
- `spectral`: 9 полос pre + 9 post + индексы pre/post + post_BAI;
- `diff`: dNBR, RdNBR=dNBR/√|NBR_pre|, RBR, dNDVI, dNBR2, dB12, dB8A, dBright, dRedEdge, dMIRBI, dNDWI;
- `scl`: one-hot pre/post {nodata(0,1), dark 2, shadow 3, water 6, cloud_med 8, cloud_high 9, cirrus 10, snow 11}, `scl_bad_any`, `dist_cloud` (до облака, насыщение 64 px), `invalid`;
- `sar`: VV/VH pre/post после медианы 5×5 (дБ = int16/100), dVV, dVH, VH/VV pre/post/diff, `sar_nodata`;
- `aux`: dem нормированный, slope; `lc`: landcover + one-hot {10,20,30,40,50,60,80,90};
- `ctx`: среднее dNBR/dNDVI в окнах 3/7/15, std dNBR 7, **`dNBR_med3`**, `dNBR_med5`;
- `rule`: **`rule_sev`** (§5.2); `geom`: `dist_center` = расстояние до центра чипа / половина диагонали;
- `event` (локализация события): расстояние до крупнейшей связной компоненты «правило ≥ 2» и до её центроида, до dNBR-взвешенного центроида (всё /256), ранг компоненты правила, её относительная и log-площадь.
- Всё без NaN/inf: пропуски → 0 + флаги `invalid`, `sar_nodata`.

### 5.2. Правило степени (fallback и признак)
```python
RULE_THR_DEFAULT = (0.10, 0.27, 0.44)                     # USGS — для прочих покровов
RULE_THR = {10: (0.125, 0.27, 0.575),  # деревья
            30: (0.065, 0.205, 0.385), # трава
            40: (0.07, 0.18, 0.38),    # пашня
            90: (0.08, 0.33, 0.61)}    # болото
sev = digitize(dNBR_med3, thr[landcover]);  sev[SCL_pre ∈ {0,3,6,11} or SCL_post ∈ {0,3,9,11}] = 0
```
Проверить на train за 5 минут: внутри эталонной гари совпадение степени 96–98 %. Само по себе правило даёт BS-часть ≈ 0.29 на всём train и на 2024 (USGS — 0.22): оно размечает все гари на снимке (13.5 % пикселей вне эталона), а эталон — одну. (Встречающаяся цифра 0.257 — от ранней версии правила без SCL-маски; ориентироваться на 0.29.)

### 5.3. UNet
- `segmentation_models_pytorch.Unet(encoder_name="resnet34", encoder_weights="imagenet", in_channels=C, classes=4)`.
- Каналы **v1 (47)**: 9 полос pre + 9 post, dNBR, RdNBR, dNDVI, dNBR2, SCL-pre {shadow, water, cloud_med, cloud_high, cirrus}, SCL-post {dark, shadow, water, cloud_med, cloud_high, cirrus}, invalid, VV/VH pre/post, sar_nodata, dem_norm, slope, lc one-hot {10,30,40,50,80,90}. **v4 (50)** = v1 + `dNBR_med3`, `rule_sev`, `dist_center`.
- SAR в каналах UNet — **без** медианы 5×5 (иначе не уложиться в бюджет ≈ 60 мс/чип на каналы); медиана 5×5 — только в признаках стека.
- Нормировка: поканальные mean/std по train → `weights/bs_norm_<name>.json`; постоянные каналы — std = 1.
- Ориентир качества одной сети (OOF, argmax без стека): IoU_burn ≈ 0.63, BS-часть ≈ 0.40. Это нормально — выбор события и границы делает стек (после стека IoU_burn ≈ 0.71, до постобработки). Не принимать низкий argmax за поломку и не тратить время на «диагностику разрыва».
- Веса сохранять в fp16 (constitution §4): 50 входов → 98 МБ в fp32, ≈ 49 МБ в fp16.
- Лосс: CE с весами классов 0.5 / 1 / 1 / 1.2 **через one-hot** (`-(onehot·w·log_softmax).sum()/norm` — детерминированно) + Dice по классам 1–3.
- AdamW lr 3e-4 + OneCycle, AMP при обучении, batch 8, 60 эпох, берётся последняя эпоха (без early stopping); аугментации — флипы и rot90. `max_lr = 1e-3` проверен: групповой сплит +0.008…+0.013 BS-части одной сети, временной −0.012…−0.019 — **не менять** (закрытый тупик §6).
- Для каждой сети: 5 групповых фолдов (OOF-вероятности для стека) + модель временного сплита (без 2024) + модель на всех данных (для инференса).
- Время на RTX 4070: ≈ 4.5 мин на фолд при 60 эпохах; одна сеть целиком (5 + 1 + 1) ≈ 32 мин.
- Тензоры входа кэшировать один раз (`data_cache/bs/x_<set>.npy`, float16) — не пересчитывать признаки на каждой эпохе.

### 5.4. Режимы по бюджету
| Режим | Состав | Ожидаемая BS-часть (group / temporal) | GPU-время |
|---|---|---|---|
| **min** | UNet v4 → стек → пороги → постобработка | 0.4745 / 0.4870 (измерено при пересборке по этому ТЗ) | 27 мин |
| **base (v4)** | UNet v1 + UNet v4 (среднее вероятностей) → стек | 0.4912 / 0.5008 (первая сборка); 0.4843 / 0.4945 (пересборка) | 27 + 34 мин |
| **ms (v5)** | + UNet v4 на 0.75× в среднее; p_burn UNet v4 на 0.5× — признаки стека; ворота 0.01 | 0.4965 / 0.5116 (первая сборка, принято); 0.4920 / 0.4963 (пересборка, отклонено: temporal +0.0018) | + 30 + 20 мин |

Разброс между двумя независимыми сборками по одному рецепту — до 0.007 BS-части: этого порядка шум обучения, ориентиры таблицы — не гарантия.
`--fast` (для пересборки с урезанным временем): 30 эпох вместо 60 — записать как урезание; ожидаемая потеря — до −0.01 BS-части (оценка).

### 5.5. Стек LightGBM 2-го уровня
- Обучается на OOF-вероятностях UNet только в «воротах»: пиксели с `p_burn = 1 − p0 > gate` (0.03 для base, 0.01 для ms); остальное — фон. Берётся случайная доля 0.35 пикселей ворот каждого чипа (seed 42 + индекс чипа).
- Признаки: все группы §5.1, кроме `lcrel` + 10 признаков UNet: p0..p3, p_burn, сглаженные p_burn в окнах 5/11 и гауссом σ 20/40, ожидаемая степень `p1+2p2+3p3`, расстояние до оценённого центра события (§5.6). В ms — ещё p_burn сети 0.5×.
- LightGBM multiclass (4 класса): `num_leaves 63, learning_rate 0.05, min_data_in_leaf 300, deterministic, seed 42`; CV по тем же фолдам (60–100 итераций), финальная модель на всех — 300–360 деревьев. ≈ 11 мин CPU.
- **Не урезать признаки события**: без них −0.027 / −0.043.

### 5.6. Декодирование и постобработка (подбираются жадно по OOF, пишутся в `weights/bs_decode.json`)
- Гарь: `1 − p0 > 0.44`; степень: `argmax(0.6·p1, 1.0·p2, 0.7·p3)`.
- Центр события: argmax гауссова сглаживания (σ = 40 px) ожидаемой степени `p1+2p2+3p3`.
- Постобработка по порядку: SCL_post ∈ {0,3,9,11} → 0 и SCL_pre ∈ {0,3,6,11} → 0; вода по landcover (80) → 0; **обнулить всё дальше 300 px от центра события**; удалить компоненты < 25 px (MMU); залить дыры ≤ 32 px; приор непустого чипа: если после всего пусто — оставить крупнейшую компоненту ≥ 25 px (в train нет чипов без гари).
- Вклад постобработки ≈ +0.006…+0.010 BS-части.

### 5.7. Многомасштабный ансамбль (только режим ms)
- UNet v4, обученная на входе, уменьшенном до 0.75× (384×384), — третий член среднего вероятностей (на инференсе вход уменьшается, выход увеличивается билинейно).
- UNet v4 на 0.5× (256×256) — её p_burn (+ сглаживания) идут в стек отдельными признаками, в среднее не входят.
- Прирост +0.0053 / +0.0107 (облачные +0.012); на другом seed temporal подтвердился слабее (+0.0018). Делать, только если остаётся ≥ 1.5 ч.

### 5.8. Ожидаемое для финала
Score OOF 0.8353 / temporal 0.8478 (ms) или 0.8300 / 0.8367 (base). Инференс 269 чипов: GPU ≈ 21–24 с, CPU 52–69 с. Веса BS: 47 МБ на UNet.

## 6. Закрытые тупики — не повторять

| Направление | Результат | Почему закрыто |
|---|---|---|
| Резать гарь кругом от центра **чипа** | −0.159 … −0.004 | центр события не в центре чипа |
| Классификатор компонент «своя/чужая гарь», snap-to-rule, SAR для выбора события, диски вокруг ядер класса 3, «зона» | AUC 0.49–0.72; Δ ≤ +0.0005 | регион события по содержимому чипа не восстанавливается; оракул компонент даёт лишь +0.025 |
| Другие лоссы/энкодеры UNet: focal, Lovász, CE+Lovász, efficientnet-b3, SWA | ±0.005 или хуже (SWA −0.02) | в шуме; шум обучения до 0.02 без one-hot CE |
| Ансамбль 4 UNet ради стабильности | 0.000 / −0.012 | не даёт прироста |
| Регрессия степени, ординальный стек, стек 3-го уровня, смесь с правилом | −0.003 … −0.04 | оракул идеальной степени даёт лишь +0.006 |
| Заполнение облаков | −0.079 / −0.130 | ломает правило dNBR |
| Координатные каналы в UNet | −0.021 (temporal) | переобучение на геометрию train |
| TTA и fp16 на инференсе | +≤ 0.004, но результат зависит от устройства | детерминизм важнее |
| Пиксельный LightGBM 1-го уровня в финале | 0 при use_lgbm=false | не обучать (−17 мин) |
| AF: CNN, две модели день/ночь, гистерезис, удаление одиночных, стекинг по соседям, признаки VNP14 | ≤ ±0.003 F1 | потолок AF почти достигнут (F1 0.968) |
| Внешние данные, дообучение на истории | — | правила + нет времени |
| UNet с max_lr 1e-3 вместо 3e-4 | group ↑ (+0.008…0.013), temporal ↓ (−0.012…−0.019) | переобучение на групповой сплит |
| Многомасштабный ансамбль (ms) на другом запуске | +0.0077 / +0.0018 | прирост на временном сплите в шуме; брать только если есть ≥ 1.5 ч и он проходит правило на своих данных |

Протокол для новых идей — constitution §6: оракул-проверка < +0.01 → не делать.

## 7. Интерфейсы между частями

| Модуль | Интерфейс | Строк (ориентир) |
|---|---|---:|
| `rle.py` | `rle_encode(mask) -> str` ('' для пустой); `rle_decode(rle, shape, validate=True) -> uint8`; `validate_rle(rle, shape) -> None` (ValueError) | 80 |
| `submission.py` | `chip_kind(chip_id) -> 'af'|'bs'`; `read_submission(path)`; `write_submission(rows, out_csv)` — без кавычек, CRLF; `build_submission(af_preds, bs_preds, sample_csv, out_csv, strict=True) -> int`; `validate_submission(csv, sample_csv, shapes=None) -> list[str]`; `shapes_from_meta(data_dir)` | 180 |
| `io.py` | `win_path(path) -> str`; `chip_path(data_dir, kind, layer, chip_id)`; `read_meta(data_dir, kind) -> DataFrame` (обе раскладки); `list_chips(data_dir, kind)`; `read_af_chip(data_dir, chip_id, with_mask=True) -> {'viirs': (8,256,256) f32, 'aux': (5,256,256) f32, 'mask'?, 'bands'}`; `read_bs_chip(...) -> {'s2_pre','s2_post': (10,512,512) u16, 's1_pre','s1_post': (2,512,512) i16, 'aux': (3,512,512) i16, 'mask'?}` | 185 |
| `metrics.py` | `af_f1(preds, gts) -> dict`; `bs_metrics(preds, gts) -> dict` (IoU_burn, IoU(k), mIoU_sev); `score(af_f1, iou_burn, miou_sev)`; `evaluate_submission(csv, data_dir_with_masks, sample_csv) -> dict`; CLI `python -m firemon.metrics` | 145 |
| `splits.py` | `make_af_folds(meta, n_folds=5, seed=42)`; `make_bs_folds(...)`; `make_{af,bs}_temporal_split(meta)`; `load_folds(kind) -> DataFrame` | 130 |
| `utils.py` | `seed_everything(seed=42)`; `available_cpus()` (cgroup/affinity); `auto_threads(value)`; `cuda_usable()`; `get_logger(name)`; `load_config(path)` | 150 |
| `pipeline.py` | `Backend = Callable[[Path, list[str], dict, RunContext], tuple[dict[str, ndarray], int]]` → (preds, n_failed); `AF_BACKENDS = {'lgbm', 'rules'}`, `BS_BACKENDS = {'unet', 'rules'}`; `RunContext(shapes, workers, device, profiler)`; `map_chips(...)` (ThreadPool); `run_backend(kind, …) -> (preds, n_failed, used_name)` с откатом на fallback; `Profiler.stage(name)`; фоновой прогрев `import torch` | 265 |
| `inference.py` | `main(argv) -> int` (0/1/2); `--data-dir --output [--sample-submission --config --device --workers --profile]` | 145 |
| Модели | `AFPredictor(cfg).load().predict(chip) -> uint8 (256,256)`; `BSPredictor(cfg).load().predict(chip) -> uint8 (512,512) 0..3`; правила `predict_af_context(chip)`, `predict_bs_rules(chip, thresholds)` | — |
| `service/core.py` | `parse_bbox`, `parse_geometry`, `parse_date`; `Store.load(dir)` — **лениво, не при импорте**; `query_hotspots(...)`, `query_scars(...)`, `summarize(...)`; `*_geojson`, `to_shapefile_zip`, `summary_csv` | 375 |
| `service/build.py` + `vectorize.py` | `build_layers(data_dir, sources) -> dict[str, GeoDataFrame]`; `write_layers`, `read_layers`; `hotspots_from_chip(chip, mask, source)`; `scars_from_mask(mask, transform, epsg, chip_id, meta, source)` + контур события | 300 |
| `service/app.py` | `create_app(data_dir=None) -> FastAPI`; маршруты spec F9 | 225 |

Итого бэкенд без ML и тестов — 2–3 тыс. строк. ML: AF ≈ 1 тыс., BS ≈ 2.5 тыс.

Правила склейки: модули не знают друг о друге; общий только `io.py` и `utils.py`. Изменение интерфейса — только владельцем infra с уведомлением в отчёте.

## 8. Сервис — данные и расчёты

- `scripts/build_service_data.py --source gt|model|both`: по train-сценам (у теста нет геопривязки) строит слои. Эталон — из масок; «Модель» — прогон наших модулей по train (демонстрация, не оценка).
- Термоточки: центры пикселей маски AF → координаты по `x_min, y_max, gsd, epsg` → EPSG:4326; атрибуты из каналов (I4, I5) и meta (`acq_datetime`, `satellite`, день/ночь по solar_zenith).
- Гари: векторизация маски по классам (`rasterio.features.shapes`) → полигоны в UTM → площадь в га в UTM → перепроекция в 4326; `contour_id`, `severity_class`, `area_ha`, `date_pre/post`, `fire_event_id`. Контур события — `unary_union` всех классов события, класс 0.
- Проверка: сумма площадей по чипу = `burn_area_ha` из meta ± 0.1 %.
- Хранение: `service/data/*.geojson.gz` (эталон — в git) + `manifest.json`; слой «Модель» — `service/data/model/*.geojson.gz`. Гари хранить **MultiPolygon на «сцена × класс»** (≈ 870 объектов, ≈ 5.6 МБ), упрощение геометрии 15 м — после расчёта площади: 99 тыс. отдельных полигонов = 14.8 МБ и тормоза браузера. Выгрузка по запросу может раскрывать MultiPolygon в полигоны с `contour_id`.
- Слой «Модель» — **только честные предсказания**: OOF (AF — фолд-модели на своих фолдах; BS — OOF стека + декодирование) или holdout 2024 на весах временного сплита. Прогон финальных моделей по train — утечка (почти идеальные карты), так делать нельзя.
- `win_path`: абсолютный путь строить через `os.path.normpath(os.path.join(os.getcwd(), p))`, а не `os.path.abspath` — на Windows `abspath('…\\aux')` превращается в устройство `\\\\.\\aux`.
- Запрос: пересечение с bbox/полигоном через shapely STRtree; фильтр по датам; справка по площадям — по обрезанным полигонам (площадь пересчитывается в UTM).
- Выгрузка SHP: geopandas → zip (shp, shx, dbf, prj, cpg), имена полей ≤ 10 символов.

## 9. Скорость инференса (бюджет < 30 с на 269 чипов)

| Этап | Бюджет | Как |
|---|---|---|
| Импорт torch/lightgbm | 3–5 с | фоновой прогрев импорта в отдельном потоке, пока читаются чипы; AF и BS — параллельно |
| Чтение + признаки AF (180) | ≤ 3 с | ThreadPool, workers = min(ядра по cgroup, 16) |
| LightGBM AF | ≤ 1 с | только на кандидатах |
| Чтение + признаки BS (89) | ≤ 5 с | ThreadPool |
| UNet на GPU | ≤ 7 с | batch 8, fp32, без TTA |
| Стек + постобработка | ≤ 4 с | **пиксельные признаки стека считать только в «воротах»** (p_burn > gate), а не на всём чипе; медиана 5×5 — через `np.partition` по окну, а не `scipy.ndimage.median_filter`; LightGBM стека — кусками в общем пуле потоков. Без этого стек+постобработка — 12.4 с (измерено) |
| RLE + запись | ≤ 1 с | |

Профилировщик по этапам (`--profile`) — с первого дня.
