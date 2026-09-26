# Jury-human full rerun: tasks33 (1366, 1920) + early click + load time + API check + summary.
# Usage: powershell -File run_all.ps1 TAG
param([string]$Tag)
$root = "C:\Users\User\Documents\GitHub\final-cosmohack"
$py = "$root\.venv\Scripts\python.exe"
$d = "$root\reports\jury_human"
$env:CUDA_VISIBLE_DEVICES = ""
& $py "$d\tasks33.py" $Tag 1366 | Out-Null
& $py "$d\tasks33.py" $Tag 1920 | Out-Null
$env:JH_DELAY = "300"; & $py "$d\probe_list.py" "${Tag}early" 1366 | Out-Null
& $py "$d\loadtime.py" $Tag | Out-Null
& $py "$d\api_check.py" "$d\${Tag}_api_check.json" | Select-String 'n zones|detection_status|class mentions'
& $py "$d\summ.py" "$d\${Tag}_1366_tasks33.json" "$d\${Tag}_1920_tasks33.json" | Out-File -Encoding utf8 "$d\${Tag}_summary.txt"
"done $Tag"
