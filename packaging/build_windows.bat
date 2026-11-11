@echo off
rem ============================================================
rem AI WrongBook - Windows one-click build (parity with build_mac.sh, R4)
rem Output : dist\<app>-v<ver>-win64.zip  (the app folder name is Chinese,
rem          it is produced by packaging/build_win.py)
rem
rem IMPORTANT: keep this file PURE ASCII.
rem   cmd.exe parses .bat with the console code page and has no BOM support;
rem   non-ASCII text in a .bat gets split at the wrong byte offset, which
rem   surfaces as "'xxx' is not recognized as an internal or external command".
rem   All Chinese messages and the Chinese app folder name live in
rem   packaging/build_win.py (Python handles Unicode paths safely).
rem
rem Usage : packaging\build_windows.bat          (build)
rem         packaging\build_windows.bat run      (build + smoke-launch the exe)
rem Prereq: Python 3.11+ (tick "Add python.exe to PATH"); Windows 10+
rem ============================================================
setlocal
cd /d "%~dp0.."

set "PY=.venv\Scripts\python.exe"

echo [1/6] Preparing venv (created on first run) and installing dependencies
if exist "%PY%" goto build

where py >nul 2>nul
if errorlevel 1 goto use_python
py -m venv .venv
goto venv_done
:use_python
python -m venv .venv
:venv_done
if errorlevel 1 goto venv_fail

"%PY%" -m pip install -q -r requirements.txt pyinstaller
if errorlevel 1 goto dep_fail

:build
"%PY%" packaging\build_win.py %*
set "RC=%errorlevel%"
if not "%RC%"=="0" ( echo [X] build failed & exit /b %RC% )

echo.
pause
exit /b 0

:venv_fail
echo [X] venv creation failed: Python 3.11+ required (tick "Add python.exe to PATH")
exit /b 1

:dep_fail
echo [X] dependency install failed, check the network and retry
exit /b 1
