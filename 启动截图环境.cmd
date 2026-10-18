@echo off
chcp 936 >nul
rem One-click screenshot environment: broker + center(demo db) + gateway + simulator + browser
rem After it runs, take the 8 screenshots per 截图执行单_20261018.md, then run:
rem     .venv\Scripts\python.exe 打包提交材料.py
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PY="
if exist ".venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"
if not defined PY if exist "_tmp\venv\Scripts\python.exe" set "PY=%CD%\_tmp\venv\Scripts\python.exe"
if not defined PY set "PY=python"

set "MQ="
if exist "%ProgramFiles%\mosquitto\mosquitto.exe" set "MQ=%ProgramFiles%\mosquitto\mosquitto.exe"
if not defined MQ set "MQ=mosquitto"

set "DEMO_DB=%CD%\center_platform\data\demo.db"

echo [1/5] python = %PY%
echo [2/5] building demo database (7 devices + anomalies) ...
if not exist "%DEMO_DB%" "%PY%" 验证脚本_搭建页面数据.py "%DEMO_DB%"
if not exist "%DEMO_DB%" (
  echo   demo db missing, fallback: build under _tmp
  set "DEMO_DB=%CD%\_tmp\demo.db"
  "%PY%" 验证脚本_搭建页面数据.py "%DEMO_DB%"
)

echo [3/5] starting MQTT broker ...
start "broker" cmd /k ""%MQ%" -c mosquitto.conf -v"
timeout /t 2 >nul

echo [4/5] starting center (demo db) ...
set "PINGLU_CENTER_DB=%DEMO_DB%"
start "center" cmd /k ""%PY%" -m uvicorn center_platform.main:app --host 127.0.0.1 --port 8000"
timeout /t 4 >nul

echo [5/5] starting gateway + simulator ...
start "gateway" cmd /k ""%PY%" -m edge_gateway.main"
timeout /t 2 >nul
start "simulator" cmd /k ""%PY%" -m device_simulator.runner --devices 7 --rate 1 --batch-size 10 --rounds 3 --keys-dir keys --registry test_data/registry_public.json --db-path simulator.db --broker 127.0.0.1 --port 1883"
timeout /t 3 >nul

start "" "http://127.0.0.1:8000/#panel-overview"

echo.
echo All started. Take screenshots per 截图执行单_20261018.md:
echo   01 overview, 02 types, 03 audits, 04 proof, 05 offline, 06 performance,
echo   07 four terminals, 08 pytest.
echo Save PNGs into .\screenshots\ then run: "%PY%" 打包提交材料.py
pause
