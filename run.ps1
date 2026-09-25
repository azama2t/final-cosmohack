# Macroplastic: one-command start (Windows PowerShell 5.1+).
#   powershell -ExecutionPolicy Bypass -File run.ps1 [-Port 8000] [-DataRoot service\demo] [-NoBrowser] [-Cpu]
# -Cpu: install requirements-cpu.txt (PyTorch CPU wheels, no CUDA download). Without -Cpu the CUDA build
#       (requirements.txt) is installed only if nvidia-smi is found; otherwise the CPU file is used automatically.
# 1) .venv (py -3.12) + requirements(-cpu).txt if missing; 2) frontend build if service\static\index.html is missing;
# 3) python -m service; 4) opens the browser when /health answers.
param(
    [int]$Port = 8000,
    [string]$DataRoot = "",
    [switch]$NoBrowser,
    [switch]$Cpu
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Url = "http://127.0.0.1:$Port/"

function Say([string]$msg) { Write-Host "[run] $msg" -ForegroundColor Cyan }
function Fail([string]$msg) { Write-Host "[run] ОШИБКА: $msg" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- 1. Python environment
$Req = "requirements.txt"
if ($Cpu) {
    $Req = "requirements-cpu.txt"
} elseif (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    $Req = "requirements-cpu.txt"
    Say "nvidia-smi не найден: пакеты (если их нужно ставить) - из requirements-cpu.txt, PyTorch CPU."
}
if (-not (Test-Path $Py)) {
    Say "Нет .venv - создаю окружение Python 3.12 (один раз, несколько минут)..."
    $created = $false
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -m venv .venv
        if ($LASTEXITCODE -eq 0) { $created = $true }
    }
    if (-not $created) {
        Fail "не удалось создать .venv через 'py -3.12'. Установите Python 3.12 (python.org, с py launcher) и повторите."
    }
    Say "Ставлю пакеты из $Req ..."
    & $Py -m pip install --upgrade pip
    & $Py -m pip install -r $Req
    if ($LASTEXITCODE -ne 0) { Fail "pip install -r $Req завершился с ошибкой (см. вывод выше)." }
} else {
    & $Py -c "import fastapi, uvicorn" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Say ".venv есть, но нет fastapi/uvicorn - ставлю $Req ..."
        & $Py -m pip install -r $Req
        if ($LASTEXITCODE -ne 0) { Fail "pip install -r $Req завершился с ошибкой." }
    }
}
Say "Python: $Py"

# ---------------------------------------------------------------- 2. Frontend build
$IndexHtml = Join-Path $Root "service\static\index.html"
if (-not (Test-Path $IndexHtml)) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Say "Фронтенд не собран - npm ci + npm run build в service\frontend ..."
        Push-Location (Join-Path $Root "service\frontend")
        try {
            & npm ci
            if ($LASTEXITCODE -ne 0) { throw "npm ci завершился с кодом $LASTEXITCODE" }
            & npm run build
            if ($LASTEXITCODE -ne 0) { throw "npm run build завершился с кодом $LASTEXITCODE" }
        } catch {
            Pop-Location
            Fail "сборка фронтенда не удалась: $_"
        }
        Pop-Location
        if (-not (Test-Path $IndexHtml)) { Fail "после сборки нет service\static\index.html" }
    } else {
        Write-Host "[run] ВНИМАНИЕ: npm не найден (нужен Node.js 18+). API запустится, карта - после сборки фронтенда." -ForegroundColor Yellow
    }
} else {
    Say "Фронтенд уже собран: service\static\index.html"
}

# ---------------------------------------------------------------- 3. Data hint
if (-not $DataRoot) {
    $hasData = (Test-Path (Join-Path $Root "service\data\manifest.json")) -or (Test-Path (Join-Path $Root "service\demo\manifest.json"))
    if (-not $hasData) {
        Write-Host "[run] Нет слоя данных (service\data или service\demo). Сервис покажет 'нет данных'." -ForegroundColor Yellow
        Write-Host "      Сгенерировать: .venv\Scripts\python.exe scripts\build_service_data.py --live-dir data\live --out service\data" -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------- 4. Service + browser
$svcArgs = @("-m", "service", "--port", "$Port")
if ($DataRoot) { $svcArgs += @("--data-root", $DataRoot) }
Say "Запускаю сервис: $Url  (остановить: Ctrl+C)"
$proc = Start-Process -FilePath $Py -ArgumentList $svcArgs -WorkingDirectory $Root -NoNewWindow -PassThru

$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    if ($proc.HasExited) { break }
    try {
        $r = Invoke-WebRequest -Uri ($Url + "health") -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 500 }
}
if ($proc.HasExited) {
    Fail "сервис завершился сразу (код $($proc.ExitCode)). Возможно, порт $Port занят: попробуйте -Port 8080."
}
if ($ready) {
    Say "Сервис готов: $Url"
    if (-not $NoBrowser) { Start-Process $Url }
} else {
    Write-Host "[run] Сервис ещё не ответил на /health за 30 с; откройте $Url вручную." -ForegroundColor Yellow
}

try {
    Wait-Process -Id $proc.Id
} finally {
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
}
