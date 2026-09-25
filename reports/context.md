# Контекст L47: объекты OSM, пересечение демо-дрейфа с объектами, повторяемость находок

Источник объектов: © OpenStreetMap contributors (ODbL), Overpass API; bbox района ± 10 км. Файлы `service/context/<район>.geojson`, всего 1166 КБ (≤ 2 МБ — хранятся в git).

Решение команды (INBOX 13:20): без численных предупреждений (часы, проценты) и без событий в ленте; метка «постоянный источник» не выдаётся, находки не связываются с устьями/выпусками.

## Объекты по видам

| район | ферм | пляж | порт | марин | охр | устье | выпуск | очистн | всего |
|---|---|---|---|---|---|---|---|---|---|
| accra | 0 | 13 | 6 | 1 | 2 | 5 | 0 | 9 | 36 |
| bali | 1 | 66 | 2 | 4 | 2 | 12 | 0 | 1 | 88 |
| danang | 103 | 29 | 0 | 0 | 2 | 6 | 1 | 1 | 142 |
| durban | 2 | 20 | 1 | 2 | 21 | 19 | 16 | 41 | 122 |
| ganges | 0 | 0 | 0 | 0 | 9 | 15 | 0 | 0 | 24 |
| guanabara | 4 | 110 | 5 | 23 | 98 | 54 | 10 | 65 | 369 |
| haiti | 0 | 10 | 1 | 4 | 0 | 36 | 0 | 1 | 52 |
| honduras | 0 | 10 | 0 | 0 | 14 | 13 | 0 | 0 | 37 |
| jakarta | 0 | 16 | 3 | 0 | 3 | 13 | 0 | 0 | 35 |
| karachi | 1 | 19 | 10 | 7 | 6 | 10 | 0 | 16 | 69 |
| lagos | 0 | 5 | 2 | 5 | 1 | 5 | 0 | 1 | 19 |
| manila | 891 | 12 | 11 | 5 | 13 | 51 | 0 | 20 | 1003 |
| mekong | 0 | 0 | 0 | 1 | 1 | 17 | 0 | 0 | 19 |
| mumbai | 29 | 19 | 1 | 1 | 11 | 8 | 0 | 24 | 93 |
| nile | 1 | 0 | 1 | 0 | 1 | 6 | 0 | 4 | 13 |
| santo_domingo | 0 | 8 | 1 | 5 | 10 | 2 | 0 | 7 | 33 |
| scotland | 0 | 64 | 5 | 3 | 11 | 5 | 25 | 10 | 123 |
| tiber | 0 | 65 | 1 | 13 | 24 | 18 | 20 | 20 | 161 |
| **всего** | 1032 | 466 | 50 | 74 | 229 | 295 | 72 | 220 | 2438 |

## Объекты, которые задевает облако частиц (демо-прогноз, не валидирован)

Только для панели дрейфа (`/api/threats`), не предупреждение и не событие ленты. объекты OSM (фермы, пляжи, охраняемые зоны, марины, порты), в буфер 300 м которых заходит облако частиц демо-прогноза дрейфа за 72 ч (не менее 1 % частиц); только для панели дрейфа, без сроков и вероятностей, не предупреждение.

| район | дата | объектов | ферм / охр. / пляжей / марин / портов | примеры |
|---|---|---|---|---|
| accra | 2025-10-21 | 4 | 0 / 1 / 2 / 0 / 1 | пляж «Nighty Beach»; охраняемая зона «Sakumo Ramsar Site» |
| bali | 2025-05-11 | 2 | 0 / 0 / 2 / 0 / 0 | пляж «Melia Beach»; пляж «Nusa Dua Beach» |
| bali | 2025-07-20 | 0 | 0 / 0 / 0 / 0 / 0 | — |
| bali | 2026-04-28 | 8 | 1 / 0 / 7 / 0 / 0 | аквакультура / ферма «Serangan Coral Farm»; пляж «Melia Beach»; пляж «Nusa Dua Beach» |
| bali | 2026-07-05 | 0 | 0 / 0 / 0 / 0 / 0 | — |
| danang | 2025-09-15 | 4 | 0 / 1 / 3 / 0 / 0 | охраняемая зона «Khu bảo tồn thiên nhiên Cù Lao Chàm» |
| durban | 2025-07-28 | 1 | 0 / 0 / 0 / 0 / 1 | порт «Port of Durban» |
| durban | 2025-09-26 | 0 | 0 / 0 / 0 / 0 / 0 | — |
| durban | 2026-05-04 | 0 | 0 / 0 / 0 / 0 / 0 | — |
| ganges | 2025-11-19 | 0 | 0 / 0 / 0 / 0 / 0 | — |
| ganges | 2026-01-13 | 0 | 0 / 0 / 0 / 0 / 0 | — |
| ganges | 2026-02-12 | 0 | 0 / 0 / 0 / 0 / 0 | — |
| guanabara | 2025-06-26 | 38 | 2 / 7 / 22 / 3 / 4 | аквакультура / ферма «Fazenda Marinha de Mexilhões Jurujuba»; аквакультура / ферма «Fazenda Marinha de Mexilhões Jurujuba»; пляж «Praia Grossa» |
| guanabara | 2025-09-04 | 40 | 2 / 7 / 22 / 4 / 5 | аквакультура / ферма «Fazenda Marinha de Mexilhões Jurujuba»; аквакультура / ферма «Fazenda Marinha de Mexilhões Jurujuba»; пляж «Praia Grossa» |
| guanabara | 2026-07-16 | 38 | 2 / 8 / 22 / 4 / 2 | аквакультура / ферма «Fazenda Marinha de Mexilhões Jurujuba»; аквакультура / ферма «Fazenda Marinha de Mexilhões Jurujuba»; пляж «Praia da Batata» |
| honduras | 2025-01-30 | 1 | 0 / 1 / 0 / 0 / 0 | охраняемая зона «Refugio de Vida Silvestre Cuyamel» |
| honduras | 2025-02-19 | 1 | 0 / 1 / 0 / 0 / 0 | охраняемая зона «Refugio de Vida Silvestre Punta de Manabique» |
| honduras | 2025-12-28 | 2 | 0 / 1 / 1 / 0 / 0 | охраняемая зона «Refugio de Vida Silvestre Punta de Manabique» |
| honduras | 2026-02-14 | 1 | 0 / 1 / 0 / 0 / 0 | охраняемая зона «Refugio de Vida Silvestre Punta de Manabique» |
| honduras | 2026-04-10 | 3 | 0 / 2 / 1 / 0 / 0 | охраняемая зона «Refugio de Vida Silvestre Cuyamel»; охраняемая зона «Refugio de Vida Silvestre Punta de Manabique» |
| honduras | 2026-05-30 | 3 | 0 / 2 / 1 / 0 / 0 | охраняемая зона «Refugio de Vida Silvestre Cuyamel»; охраняемая зона «Refugio de Vida Silvestre Punta de Manabique» |
| karachi | 2025-11-11 | 1 | 0 / 0 / 0 / 0 / 1 | порт «ایس اے پی ٹی ٹرمینل» |
| karachi | 2026-01-15 | 17 | 0 / 0 / 9 / 1 / 7 | пляж «Beach Royal City»; пляж «Cape Mount Beach»; пляж «Nathia Gali Beach» |
| karachi | 2026-02-14 | 7 | 0 / 0 / 2 / 0 / 5 | пляж «Sandspit»; пляж «منوڑا بیچ»; порт «Keamari Breakwater» |
| lagos | 2025-11-10 | 1 | 0 / 0 / 1 / 0 / 0 | пляж «Oniru Private Beach» |
| manila | 2025-01-06 | 0 | 0 / 0 / 0 / 0 / 0 | — |
| manila | 2025-06-15 | 1 | 0 / 0 / 0 / 0 / 1 | порт «Port of Lamao» |
| manila | 2025-11-07 | 9 | 4 / 0 / 0 / 0 / 5 | порт «Manila Harbour Center»; порт «Manila International Container Terminal»; порт «Navotas Fishport Complex» |
| manila | 2025-12-27 | 10 | 2 / 0 / 6 / 0 / 2 | порт «Manila Harbour Center»; порт «Rosario Municipal Fish Port» |
| manila | 2026-04-01 | 1 | 1 / 0 / 0 / 0 / 0 | — |
| mekong | 2026-03-02 | 0 | 0 / 0 / 0 / 0 / 0 | — |
| mumbai | 2025-01-31 | 0 | 0 / 0 / 0 / 0 / 0 | — |
| mumbai | 2025-12-17 | 0 | 0 / 0 / 0 / 0 / 0 | — |
| mumbai | 2026-01-01 | 1 | 0 / 0 / 1 / 0 / 0 | — |
| nile | 2025-11-11 | 0 | 0 / 0 / 0 / 0 / 0 | — |
| nile | 2026-01-05 | 1 | 1 / 0 / 0 / 0 / 0 | — |
| nile | 2026-03-16 | 2 | 1 / 1 / 0 / 0 / 0 | охраняемая зона «محمية بحيرة البرلس» |
| santo_domingo | 2025-02-23 | 1 | 0 / 1 / 0 / 0 / 0 | охраняемая зона «Parque Nacional Submarino La Caleta» |
| santo_domingo | 2025-03-05 | 1 | 0 / 1 / 0 / 0 / 0 | охраняемая зона «Parque Nacional Submarino La Caleta» |
| santo_domingo | 2025-10-18 | 0 | 0 / 0 / 0 / 0 / 0 | — |
| santo_domingo | 2025-12-30 | 0 | 0 / 0 / 0 / 0 / 0 | — |
| santo_domingo | 2026-02-18 | 0 | 0 / 0 / 0 / 0 / 0 | — |
| scotland | 2026-07-19 | 6 | 0 / 2 / 3 / 0 / 1 | пляж «Balcomie Sands»; охраняемая зона «Isle of May National Nature Reserve»; охраняемая зона «Kilminning Coast Wildlife Reserve» |
| tiber | 2025-11-05 | 33 | 0 / 3 / 29 / 1 / 0 | пляж «Focene»; пляж «Il Curvone»; пляж «Spiaggia Libera Canale dei Pescatori» |

Сцен с дрейфом: 44; из них задевают ≥ 1 объект: 29; всего пар «сцена–объект»: 238.

## Повторяющиеся находки (повторяемость, требует проверки)

Внутренний эндпоинт `/api/repeats`. Правило: находки (не артефакты, любая модель) в одной ячейке H3 res 8 на ≥ 2 надёжных датах района (надёжность даты — то же правило, что в /api/calendar); площадь — сумма по датам, на дату — максимум по моделям.

| район | ячеек (≥ 2 дат) | ≥ 3 дат | находки обеих моделей | только lgbm | только mdd |
|---|---|---|---|---|---|
| accra | 0 | 0 | 0 | 0 | 0 |
| bali | 13 | 1 | 4 | 9 | 0 |
| danang | 0 | 0 | 0 | 0 | 0 |
| durban | 5 | 3 | 0 | 5 | 0 |
| ganges | 0 | 0 | 0 | 0 | 0 |
| guanabara | 20 | 0 | 1 | 18 | 1 |
| haiti | 0 | 0 | 0 | 0 | 0 |
| honduras | 29 | 1 | 10 | 19 | 0 |
| jakarta | 0 | 0 | 0 | 0 | 0 |
| karachi | 0 | 0 | 0 | 0 | 0 |
| lagos | 0 | 0 | 0 | 0 | 0 |
| manila | 0 | 0 | 0 | 0 | 0 |
| mekong | 0 | 0 | 0 | 0 | 0 |
| mumbai | 0 | 0 | 0 | 0 | 0 |
| nile | 6 | 0 | 2 | 4 | 0 |
| santo_domingo | 35 | 1 | 11 | 24 | 0 |
| scotland | 0 | 0 | 0 | 0 | 0 |
| tiber | 0 | 0 | 0 | 0 | 0 |

Всего ячеек с повторяющимися находками: 108. Это повторяемость находок в ячейке, требует проверки; повторы одной модели (особенно lgbm) часто дают стационарные объекты (причалы, суда у порта), а не мусор.
