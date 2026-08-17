#!/bin/bash
# postgres 를 먼저 띄우고, 준비되면 API 를 이 프로세스로 대체한다.
#
# postgres 를 공식 엔트리포인트로 돌리는 이유: initdb, 사용자·DB 생성,
# /docker-entrypoint-initdb.d 실행(= db/001_schema.sql)을 전부 그쪽이 처리한다.
# 직접 pg_ctl 로 띄우면 첫 기동 시 스키마가 안 들어가 API 가 빈 DB 를 본다.
set -e

: "${POSTGRES_USER:=culturemate}"
: "${POSTGRES_PASSWORD:=culturemate}"
: "${POSTGRES_DB:=culturemate}"
export POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB

# 같은 컨테이너 안이므로 DSN 은 항상 로컬을 본다. .env 에 남아 있는
# @postgres:5432 를 그대로 쓰면 이름이 해석되지 않아 체크포인터가
# InMemorySaver 로 조용히 떨어진다.
export PG_DSN="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:5432/${POSTGRES_DB}"
export CHECKPOINT_DSN=""

echo "[1/2] PostgreSQL 기동"
docker-entrypoint.sh postgres &
PG_PID=$!

# 컨테이너가 죽을 때 postgres 도 같이 정리한다
trap 'kill -TERM "$PG_PID" 2>/dev/null; wait "$PG_PID" 2>/dev/null' TERM INT

for _ in $(seq 1 90); do
    if pg_isready -U "$POSTGRES_USER" -h 127.0.0.1 -q; then
        break
    fi
    # postgres 가 먼저 죽었으면 기다려도 소용없다 — 로그를 남기고 같이 끝낸다
    if ! kill -0 "$PG_PID" 2>/dev/null; then
        echo "[오류] PostgreSQL 이 기동 중 종료됐습니다." >&2
        exit 1
    fi
    sleep 1
done

if ! pg_isready -U "$POSTGRES_USER" -h 127.0.0.1 -q; then
    echo "[오류] PostgreSQL 이 90초 안에 준비되지 않았습니다." >&2
    exit 1
fi

echo "[2/2] API 기동 — $*"
exec "$@"
