@echo off
title CultureMate - Backend
cd /d "%~dp0"

REM All launch logic lives in 백엔드실행.bat - this file only forwards to it.
REM
REM This used to run `docker compose up -d --build` directly. The project now
REM ships a single container (PostgreSQL + API) started with `docker run`, so
REM compose would bring up postgres alone and leave the API down - the backend
REM looked started but was not. Keeping the logic in one file prevents that.

if not exist "%~dp0백엔드실행.bat" (
  echo.
  echo   [ERROR] 백엔드실행.bat not found next to this file.
  echo.
  pause & exit /b 1
)

call "%~dp0백엔드실행.bat"
