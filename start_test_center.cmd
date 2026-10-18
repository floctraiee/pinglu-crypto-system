@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "DB=%~1"
if "%DB%"=="" set "DB=%CD%\_tmp\perf100\center.db"

echo ============================================================
echo  Test center (resume round)
echo  db     : %DB%
echo  listen : 0.0.0.0:8150
echo  page   : http://127.0.0.1:8150/
echo  stats  : http://172.24.108.95:8150/stats
echo ============================================================
echo  press Ctrl+C to stop
echo.

if not exist "%DB%" (
    echo [X] database not found: %DB%
    goto :eof
)

set PINGLU_CENTER_DB=%DB%
python -m uvicorn center_platform.main:app --host 0.0.0.0 --port 8150
