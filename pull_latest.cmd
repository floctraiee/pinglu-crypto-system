@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

if /i "%~1"=="--check" goto check

echo ============================================================
echo  Pull A-side latest code (fetch + merge, no push)
echo  dir: %CD%
echo ============================================================
echo.

git rev-parse --git-dir >nul 2>nul
if errorlevel 1 goto nogit

echo [1/5] commit local changes first
git add -A
git diff --cached --quiet
if errorlevel 1 (git commit -m "wip: local changes before pulling A-side code") else (echo    nothing to commit)

echo.
echo [2/5] fetch origin
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
echo [3/5] merge origin/main
git merge origin/main --no-edit
set UNMERGED=
for /f "delims=" %%f in ('git diff --name-only --diff-filter=U') do set UNMERGED=!UNMERGED! %%f
if "!UNMERGED!"=="" goto done
echo    conflicts: !UNMERGED!
set BAD=0
for /f "delims=" %%f in ('git diff --name-only --diff-filter=U') do call :resolve "%%f"
if "!BAD!"=="1" goto badconflict
git commit -m "merge: pull A-side latest code"
echo    merge committed

:done
echo.
echo [4/5] recent commits
git log --oneline -5
echo.
echo [5/5] status
git status -sb
goto end

:check
git status --short
echo.
echo branch:
git rev-parse --abbrev-ref HEAD
echo recent commits:
git log --oneline -5
echo.
echo check only, nothing changed.
goto end

:resolve
if /i "%~1"==".gitignore" goto ours
if /i "%~1"=="README.md" goto theirs
echo    [X] unresolved: %~1
set BAD=1
goto :eof
:ours
git checkout --ours -- "%~1"
git add "%~1"
echo    keep local: %~1
goto :eof
:theirs
git checkout --theirs -- "%~1"
git add "%~1"
echo    keep remote: %~1
goto :eof

:nogit
echo [X] not a git repository
goto end
:fetchfail
echo [X] fetch failed (network). Turn on VPN and re-run.
goto end
:badconflict
echo [X] unresolved conflict. Fix manually then re-run.
goto end

:end
endlocal
