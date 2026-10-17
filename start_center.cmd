@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo  Pinglu center platform
echo  listen : 0.0.0.0:8000   (LAN reachable)
echo  page   : http://127.0.0.1:8000/       (this machine)
echo  page   : http://YOUR-LAN-IP:8000/     (teammates)
echo  docs   : http://127.0.0.1:8000/docs
echo ============================================================
echo  press Ctrl+C to stop
echo.

python -m uvicorn center_platform.main:app --host 0.0.0.0 --port 8000
