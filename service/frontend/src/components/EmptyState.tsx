const CMD = String.raw`# быстрый синтетический набор (секунды)
.venv\Scripts\python.exe scripts\make_fixtures.py --out service\demo_fixtures

# реальные предсказания: см. README (service\data\ или service\demo\)`;

export default function EmptyState() {
  return (
    <div className="empty" data-testid="empty-state">
      <div className="empty-card glass">
        <div className="eyebrow">Нет данных</div>
        <h1>Слой данных сервиса пуст</h1>
        <p>
          Не найден <code>/data/manifest.json</code> или в нём нет регионов. Сгенерируйте данные и обновите страницу:
        </p>
        <pre>
          <code>{CMD}</code>
        </pre>
        <p className="muted">Показатель сервиса — доля наблюдаемой воды с признаками мусора (‰): индекс по снимку, не масса пластика.</p>
      </div>
    </div>
  );
}
