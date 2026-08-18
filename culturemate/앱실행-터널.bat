@echo off
title CultureMate - 앱 (사내망/터널)
cd /d "%~dp0mobile"
echo.
echo   PC와 폰이 다른 망일 때 사용합니다.
echo   cloudflared 가 없으면: winget install --id Cloudflare.cloudflared
echo.
call npm run tunnel
pause
