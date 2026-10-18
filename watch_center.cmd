@echo off
chcp 65001 >nul
setlocal
set "URL=http://172.24.108.95:8150"
if not "%~1"=="" set "URL=%~1"
set "LOG=验证结果_测试监控_100台.log"
if not "%~2"=="" set "LOG=%~2"

echo ============================================================
echo  watch center : %URL%/stats
echo  interval     : 10 s   (Ctrl+C to stop)
echo  log          : %LOG%
echo ============================================================
echo.

if exist "%LOG%" (
    echo [%TIME%] ===== new watch session =====>>"%LOG%"
)

:loop
echo [%TIME%]>>"%LOG%"
curl.exe -s "%URL%/stats">>"%LOG%"
echo.>>"%LOG%"
echo [%TIME%]
curl.exe -s "%URL%/stats"
echo.
timeout /t 10 /nobreak >nul
goto loop
