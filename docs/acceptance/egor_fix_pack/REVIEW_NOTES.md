# REVIEW NOTES ПО СКРИНШОТАМ

## 01 — Globe / список сцен

Файл: `screenshots/01_globe_main_and_list.png`

### Сильное
- глобус — явный визуальный центр;
- есть реальные сцены/находки;
- хорошая общая структура;
- информация по сценам доступна быстро.

### Нужно исправить
- нет явного timeline;
- список очень плотный;
- важные функции могут теряться в навигации;
- нужно продумать, какие capability жюри должно увидеть за первые 30 секунд.

## 02 — Overlay clipping

Файл: `screenshots/02_list_overlay_bug.png`

### Ошибка
Info overlay открывается вправо и оказывается clipped соседним layout/container.

### Fix
Collision-aware floating overlay:
- flip;
- shift;
- viewport padding;
- portal;
- правильный z-index;
- no overflow hidden clipping.

## 03 — Drift simulation

Файл: `screenshots/03_drift_simulation_problem.png`

### Ошибка
Очень много model particles визуально читаются как внезапно появившиеся новые куски мусора.

Это опасно.

### Нужно
Показывать simulation как:
- trajectory / plume;
- uncertainty envelope;
- representative particles;
- explicit experimental state.

Никогда не использовать количество model particles как количество мусора.

## 04 — Quality service unavailable

Файл: `screenshots/04_quality_service_unavailable.png`

### Ошибка
Unavailable endpoint визуально превращает целую вкладку в сломанную страницу.

### Нужно
Graceful degradation:
- compact offline banner;
- cached last result;
- retry;
- locally available metadata;
- основная карта продолжает работать.

## 05 — Photo Counter

Файл: `screenshots/05_photo_counter_module.png`

### Сильное
Очень важная функциональность:
- real detailed photo;
- bbox detector;
- count;
- confidence;
- threshold;
- area/GSD;
- возможность items/km².

### Проблема
Ощущается как отдельное приложение.

### Нужно
Связать с:
- selected observation;
- selected zone;
- detailed evidence;
- основной navigation/demo path.

Пользователь должен быстро понимать:

Satellite → zone detection  
Detailed image → direct item count  
Field → independent measurement
