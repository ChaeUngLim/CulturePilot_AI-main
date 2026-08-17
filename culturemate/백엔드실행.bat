@echo off
title CultureMate - 백엔드
cd /d "%~dp0"

REM 컨테이너 하나에 PostgreSQL + API 를 함께 담는다(Dockerfile 참고).
REM
REM `docker compose` 가 아니라 `docker build`/`docker run` 을 쓰는 이유 -
REM compose 로 빌드하면 이미지에 com.docker.compose.project 라벨이 구워지고,
REM Docker Desktop 이 그 라벨로 컨테이너를 프로젝트 그룹 행 아래로 접는다.
REM 그룹 행은 Container ID-Image-Port 를 집계하지 않아 전부 '-' 로 보인다.
REM 라벨 없이 띄우면 값이 그대로 보이는 한 줄이 된다.

echo.
echo   폴더: %CD%
echo.
echo   [1/3] 이전 컨테이너 정리 (볼륨은 유지)
docker rm -f culturemate > nul 2>&1

echo   [2/3] 이미지 빌드
docker build -t culturemate-api .
if errorlevel 1 (
  echo.
  echo   [오류] 이미지 빌드 실패. Docker Desktop 이 켜져 있나요?
  echo.
  pause & exit /b 1
)

echo   [3/3] 기동 (이름: culturemate / 포트: 8000)
docker run -d --name culturemate ^
  --env-file .env ^
  -e POSTGRES_USER=culturemate ^
  -e POSTGRES_PASSWORD=culturemate ^
  -e POSTGRES_DB=culturemate ^
  -e WATCHFILES_FORCE_POLLING=true ^
  -e WATCHFILES_POLL_DELAY=2 ^
  -p 8000:8000 ^
  -v culturemate_pgdata:/var/lib/postgresql ^
  -v "%CD%\app:/srv/app" ^
  -v "%CD%\scripts:/srv/scripts" ^
  -v "%CD%\db:/docker-entrypoint-initdb.d:ro" ^
  culturemate-api ^
  uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload
if errorlevel 1 (
  echo.
  echo   [오류] 기동 실패. 8000 포트를 다른 것이 쓰고 있는지 확인하세요.
  echo.
  pause & exit /b 1
)

echo.
echo   PostgreSQL 준비를 기다립니다...
:wait
timeout /t 3 /nobreak > nul
curl -s -m 3 http://localhost:8000/health > nul 2>&1
if errorlevel 1 goto wait

echo.
docker ps --filter name=culturemate --format "table {{.Names}}\t{{.ID}}\t{{.Image}}\t{{.Ports}}"
echo.
echo   백엔드: http://localhost:8000/health
echo   앱    : http://localhost:19006  (앱실행.bat 으로 따로 띄웁니다)
echo.
echo   시드 데이터를 넣으려면 아무 키나 누르세요. (이미 있으면 건너뛰고 창을 닫으세요)
pause > nul
docker exec culturemate python scripts/seed_demo.py
echo.
pause
