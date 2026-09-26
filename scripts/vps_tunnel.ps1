# §52 п.3 — постоянный обратный туннель ПК → VPS 5.231.59.204
# Публикует локальный :8070 (сервис для людей) на VPS как 127.0.0.1:18080,
# откуда Caddy на VPS проксирует https://5-231-59-204.sslip.io и http://5.231.59.204.
# Существующие процессы (:8070, cloudflared) не трогает — только читает.
# Запуск вручную:  powershell -ExecutionPolicy Bypass -File scripts\vps_tunnel.ps1
# Регистрация в Планировщике — на будущее (см. §52 п.3), для текущего сеанса
# туннель также можно запускать в фоне вручную.

$ErrorActionPreference = 'Continue'
$key = "$env:USERPROFILE\.ssh\cosmohack_vps"
$logDir = Join-Path $PSScriptRoot '..\logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = Join-Path $logDir 'vps_tunnel.log'

while ($true) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $log -Value "[$ts] connecting..."
    & ssh -N -i $key `
        -o ServerAliveInterval=15 `
        -o ServerAliveCountMax=3 `
        -o ExitOnForwardFailure=yes `
        -o StrictHostKeyChecking=accept-new `
        -o BatchMode=yes `
        -R 127.0.0.1:18080:127.0.0.1:8070 `
        root@5.231.59.204 2>>$log
    $ts2 = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $log -Value "[$ts2] tunnel exited (code $LASTEXITCODE), retry in 5s"
    Start-Sleep -Seconds 5
}
