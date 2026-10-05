@echo off
setlocal
cd /d "%~dp0"
set "LOG=%~dp0setup-log.txt"

echo ==========================================================
echo    TradeGuard  (Windows)
echo ==========================================================
echo.

REM ---- find Python (py launcher preferred, else python) ----
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY ( python --version >nul 2>nul && set "PY=python" )
if not defined PY goto :nopython

REM ---- find Node / npm ----
where npm >nul 2>nul || goto :nonode

REM ---- create .env from template if missing (defaults to DATA_SOURCE=yahoo) ----
if not exist ".env" copy /y ".env.example" ".env" >nul

REM ---- install only on the first run ----
if not exist "backend\.venv\Scripts\python.exe" goto :install
if not exist "webapp\node_modules" goto :install
goto :launch

:install
echo First-time setup - this can take a few minutes. Please wait...
echo (full details are written to setup-log.txt)
echo TradeGuard setup log> "%LOG%"

echo   [1/4] Creating the Python environment...
if not exist "backend\.venv\Scripts\python.exe" %PY% -m venv "backend\.venv" >>"%LOG%" 2>&1
if errorlevel 1 goto :failenv

echo   [2/4] Installing backend packages...
"backend\.venv\Scripts\python.exe" -m pip install --upgrade pip >>"%LOG%" 2>&1
"backend\.venv\Scripts\python.exe" -m pip install -r "backend\requirements-lite.txt" >>"%LOG%" 2>&1
if errorlevel 1 goto :failpip

echo   [3/4] Installing web packages...
pushd webapp
call npm install --no-fund --no-audit >>"%LOG%" 2>&1
set "NPMRC=%errorlevel%"
popd
if not "%NPMRC%"=="0" goto :failnpm

echo   [4/4] Preparing the database...
pushd backend
".venv\Scripts\python.exe" -c "from app.db.database import init_schema; init_schema()" >>"%LOG%" 2>&1
popd

echo   Setup complete.
echo.

:launch
echo Starting TradeGuard...
REM The new windows inherit THIS folder as their working directory, so the
REM relative "cd backend" / "cd webapp" below is correct even if the folder
REM path contains spaces.
start "TradeGuard Backend"  cmd /k "cd backend && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
start "TradeGuard Frontend" cmd /k "cd webapp && npm run dev"

echo Opening your browser shortly...
timeout /t 12 /nobreak >nul
start "" http://localhost:5173

echo.
echo TradeGuard is running in two new windows:
echo    App:  http://localhost:5173   (opening in your browser)
echo    API:  http://127.0.0.1:8000/docs
echo.
echo To STOP: close those two windows, or double-click STOP-TRADEGUARD.bat
echo.
pause
exit /b 0

:nopython
echo [X] Python was not found on this PC.
echo     1) Install Python 3.12:  https://www.python.org/downloads/
echo     2) On the FIRST installer screen, TICK  "Add python.exe to PATH".
echo     3) Then double-click this file again.
echo.
pause & exit /b 1

:nonode
echo [X] Node.js / npm was not found on this PC.
echo     Install Node 20+ (LTS) from  https://nodejs.org  (default options),
echo     then double-click this file again.
echo.
pause & exit /b 1

:failenv
echo [X] Could not create the Python environment.
echo     Open  setup-log.txt  (in this folder) and send it to us.
echo.
pause & exit /b 1

:failpip
echo [X] Backend package install failed.
echo     Open  setup-log.txt  and send it to us.
echo     Most common cause: a very new Python without ready-made packages -
echo     installing Python 3.12 usually fixes it.
echo.
pause & exit /b 1

:failnpm
echo [X] Web package install (npm) failed.
echo     Open  setup-log.txt  and send it to us.
echo.
pause & exit /b 1
