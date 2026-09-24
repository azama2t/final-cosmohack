# FILEMAP — карта проекта (читать первым)

| Путь | Что делает | Кто вызывает |
|---|---|---|
| `docs/CONTRACTS.md` | форматы файлов слоя данных сервиса (manifest, prob, detections, h3, zones, drift, timeseries) | все дорожки |
| `scripts/make_fixtures.py` | валидные фейковые данные по контрактам за секунды | фронт/API/тесты |
| `scripts/gpu_queue.py` | единственная очередь GPU-задач, лог `out/gpu_queue.log` | обучение, MDD-инференс |
