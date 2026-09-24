# TROUBLESHOOTING — сломалось → симптом → причина → починка → как не повторить

| Что | Симптом | Причина | Починка | Как не повторить |
|---|---|---|---|---|
| GPU-очередь | задача сразу exit=127, `WinError 2` | CreateProcess на Windows не находит относительный `.venv/Scripts/python.exe` | раннер превращает относительный путь к exe в абсолютный от cwd задачи | в очереди всегда резолвить exe |
| Heredoc в Git Bash | `unexpected EOF` при записи Python-файла со строками `\?\` | экранирование в heredoc | писать исходники инструментом записи файлов | не писать длинный код через heredoc |
| `python -m service` | SyntaxWarning `\S` | Windows-путь в docstring | raw-строка `r"""` | — |
| Остановка сервиса | python.exe живёт после Stop-Process родителя PowerShell | дочерний процесс | убивать по порту: `Get-NetTCPConnection -LocalPort 8000 \| % { Stop-Process -Id $_.OwningProcess }` | — |
