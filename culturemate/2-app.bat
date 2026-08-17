@echo off
title CultureMate - App
cd /d "%~dp0mobile"

where node >nul 2>nul
if errorlevel 1 (
  echo.
  echo   [ERROR] Node.js not found. Install LTS from https://nodejs.org
  echo.
  pause & exit /b 1
)

if not exist node_modules (
  echo.
  echo   First run - installing packages ^(3-5 min^)...
  echo.
  call npm install
)

if not exist .env (
  copy .env.example .env >nul
  echo   Created .env from template.
)

echo.
echo   Folder : %CD%
echo.
echo   Press  w  to open in browser
echo   Or scan the QR code with Expo Go ^(same Wi-Fi required^)
echo.
call npm start
pause
