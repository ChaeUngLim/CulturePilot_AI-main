@echo off
title CultureMate - 앱
cd /d "%~dp0mobile"
echo.
echo   폴더: %CD%
echo   브라우저로 보려면 QR 이 뜬 뒤 w 키를 누르세요.
echo.
call npm start
pause
