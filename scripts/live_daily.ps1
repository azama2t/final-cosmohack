# §55 п.1 «Реальное время»: ежедневное обновление — новые снимки Sentinel-2 L2A за последние сутки
# (окно 3 сут.: Planetary Computer выкладывает пиксели с задержкой до 1–2 сут., уже обработанные даты пропускаются)
# -> наш детектор (CPU) и те же фильтры качества -> обновление data/case/fresh_s2/index.json (API /api/v3/fresh_s2).
#
# Вручную:      powershell -ExecutionPolicy Bypass -File scripts\live_daily.ps1
# Планировщик:  schtasks /Create /TN MacroplasticLiveDaily /SC DAILY /ST 06:30 /F /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\User\Documents\GitHub\final-cosmohack\scripts\live_daily.ps1"
# Проверка:     schtasks /Query /TN MacroplasticLiveDaily /V /FO LIST ; запуск сейчас: schtasks /Run /TN MacroplasticLiveDaily
# Удалить:      schtasks /Delete /TN MacroplasticLiveDaily /F
# Параллельный запуск (кнопка «Обновить» в API, ручной) исключён lock-файлом data_cache\fresh_s2\refresh.lock.
param([int]$Days = 3, [int]$Workers = 3)
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:CUDA_VISIBLE_DEVICES = ""
$logDir = Join-Path $root "out\live_batch"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("daily_" + (Get-Date -Format "yyyyMMdd_HHmm") + ".log")
$py = Join-Path $root ".venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"
# лог в UTF-8 (перенаправление *>> в Windows PowerShell 5.1 пишет UTF-16)
& $py -W ignore scripts\case\live_batch.py --days $Days --workers $Workers --threads 4 --trigger daily 2>&1 |
    ForEach-Object { "$_" } | Out-File -FilePath $log -Append -Encoding utf8
$code = $LASTEXITCODE
Add-Content -Path $log -Encoding utf8 -Value ("exit " + $code + " at " + (Get-Date -Format "yyyy-MM-dd HH:mm"))
exit $code
