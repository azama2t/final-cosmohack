# Start the macroplastic web service (API + map). Usage: .\scripts\serve.ps1 [-Port 8000] [-DataRoot service\demo_fixtures]
param([int]$Port = 8000, [string]$DataRoot = ""); Set-Location (Split-Path $PSScriptRoot -Parent); if ($DataRoot) { & .\.venv\Scripts\python.exe -m service --port $Port --data-root $DataRoot } else { & .\.venv\Scripts\python.exe -m service --port $Port }
