@echo off
echo Stopping TradeGuard (freeing ports 8000 and 5173)...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr LISTENING') do taskkill /F /PID %%p >nul 2>nul
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5173" ^| findstr LISTENING') do taskkill /F /PID %%p >nul 2>nul
echo Done. (You can also just close the two TradeGuard windows.)
echo.
pause
