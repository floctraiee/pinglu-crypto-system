@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "DB=%CD%\_tmp\round4\center.db"

echo ============================================================
echo  Test center - ROUND 4 (clean database, WAL, no access log)
echo  db     : %DB%
echo  listen : 0.0.0.0:8150
echo  stats  : http://127.0.0.1:8150/stats
echo ============================================================
echo  expected: devices=100, batches=0, records=0, audits=0
echo  press Ctrl+C to stop
echo.

if not exist "%DB%" (
    echo [X] database not found: %DB%
    goto :eof
)

set PINGLU_CENTER_DB=%DB%
python -m uvicorn center_platform.main:app --host 0.0.0.0 --port 8150 --no-access-log --log-level warning
