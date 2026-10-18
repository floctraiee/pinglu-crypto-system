@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

if /i "%~1"=="--check" goto check

echo ============================================================
echo  Pinglu repo sync : commit - merge - push
echo  dir: %CD%
echo ============================================================
echo.

git rev-parse --git-dir >nul 2>nul
if errorlevel 1 goto nogit
goto run

:check
echo [check] git status:
git status --short
echo.
echo [check] branch:
git rev-parse --abbrev-ref HEAD
echo [check] recent commits:
git log --oneline -3
echo.
echo check only, nothing changed.
goto end

:nogit
echo [X] not a git repository.
goto end

:run
echo [1/6] status
git status --short
echo.

echo [2/6] stage
git add -A
git diff --cached --quiet
if errorlevel 1 goto docommit
echo    nothing to commit
goto fetch
:docommit
git commit -m "feat: center fallback page, gateway verdict in audits, verify scripts and docs"
echo    committed

:fetch
echo.
echo [3/6] fetch origin
set FETCHED=0
for /l %%i in (1,1,6) do (
    if "!FETCHED!"=="0" (
        echo    attempt %%i
        git fetch origin
        if not errorlevel 1 (set FETCHED=1) else (timeout /t 4 /nobreak >nul)
    )
)
if "!FETCHED!"=="0" goto fetchfail
echo    fetch ok

echo.
echo [4/6] merge origin/main
git merge origin/main --no-edit
set UNMERGED=
for /f "delims=" %%f in ('git diff --name-only --diff-filter=U') do set UNMERGED=!UNMERGED! %%f
if "!UNMERGED!"=="" goto merged
echo    conflicts: !UNMERGED!
set BAD=0
for /f "delims=" %%f in ('git diff --name-only --diff-filter=U') do call :resolve "%%f"
if "!BAD!"=="1" goto badconflict
git commit -m "merge: merge remote A-side docs and verified samples"
echo    merge committed
goto merged

:resolve
if /i "%~1"==".gitignore" goto keepours
if /i "%~1"=="README.md" goto keeptheirs
echo    [X] unresolved: %~1
set BAD=1
goto :eof
:keepours
git checkout --ours -- "%~1"
git add "%~1"
echo    keep local: %~1
goto :eof
:keeptheirs
git checkout --theirs -- "%~1"
git add "%~1"
echo    keep remote: %~1
goto :eof

:merged
echo.
echo [5/6] align registry
if exist "test_data\verified_wl001\registry_public.json" (
    copy /y "test_data\verified_wl001\registry_public.json" "test_data\registry_public.json" >nul
    git add "test_data/registry_public.json"
    git diff --cached --quiet
    if errorlevel 1 (
        git commit -m "chore: align registry with verified_wl001"
        echo    registry committed
    ) else (
        echo    already aligned
    )
)

echo.
echo [6/6] push
set PUSHED=0
for /l %%i in (1,1,8) do (
    if "!PUSHED!"=="0" (
        echo    attempt %%i
        git push origin main
        if not errorlevel 1 (set PUSHED=1) else (timeout /t 5 /nobreak >nul)
    )
)
if "!PUSHED!"=="0" goto pushfail
echo.
echo ============================================================
echo  DONE
echo ============================================================
git log --oneline -3
git status -sb
goto end

:fetchfail
echo [X] fetch failed (network). Turn on VPN and re-run.
goto end
:badconflict
echo [X] unresolved conflicts. Fix manually then re-run.
goto end
:pushfail
echo [X] push failed. Re-run, or run: git push origin main
goto end

:end
endlocal
