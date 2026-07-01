@echo off
cd /d "%~dp0"
echo Stopping any server on port 8000...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"CommandLine LIKE '%%uvicorn%%web.app%%'\" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 2 /nobreak >nul
echo Starting web server...
echo 확인: http://127.0.0.1:8000/api/health 에서 parser_build=table-format-v2
python -m uvicorn web.app:app --host 127.0.0.1 --port 8000
