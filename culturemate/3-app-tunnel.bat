@echo off
title CultureMate - App (tunnel)
cd /d "%~dp0mobile"

echo.
echo   TUNNEL MODE
echo   Use this when your PC and phone are on different networks
echo   ^(office Wi-Fi, guest network, etc^).
echo.
echo   It exposes the backend through a public https address and
echo   writes that address into mobile\.env automatically.
echo.

where cloudflared >nul 2>nul
if errorlevel 1 (
  echo   [WARN] cloudflared not found.
  echo          Install:  winget install --id Cloudflare.cloudflared
  echo          Without it the app runs in MOCK mode ^(UI only^).
  echo.
)

call npm run tunnel
pause
