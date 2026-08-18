# DB 와 API 를 한 컨테이너에 담는다.
#
# 베이스를 python 이 아니라 pgvector/pgvector:pg18 로 잡은 이유 —
# 기존 culturemate_pgdata 볼륨이 pg18 레이아웃(/var/lib/postgresql/18/docker)으로
# 만들어져 있다. 같은 베이스를 쓰면 시드 데이터를 그대로 이어받는다.
# python 베이스에 postgres 를 따로 깔면 레이아웃이 어긋나 볼륨을 못 읽는다.
#
# 컨테이너 하나에 프로세스 둘은 일반적인 배포 방식은 아니다. 데모·캡스톤처럼
# '한 번에 켜지는 것'이 중요할 때 쓰고, 운영에서는 DB 를 분리하는 편이 낫다.
FROM pgvector/pgvector:pg18

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Debian 의 시스템 파이썬을 직접 건드리지 않도록 가상환경을 쓴다
# (PEP 668 externally-managed-environment 오류 회피).
ENV VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH
RUN python3 -m venv "$VIRTUAL_ENV"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY db ./db
COPY scripts ./scripts
COPY docker-entrypoint-all.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint-all.sh

# 5432 는 열지 않는다. API 가 같은 컨테이너 안에서 127.0.0.1 로 붙으므로
# 호스트로 내보낼 이유가 없다. DB 를 직접 보려면 `docker exec culturemate psql` 을 쓴다.
EXPOSE 8000
HEALTHCHECK --interval=30s --start-period=60s \
    CMD curl -f http://localhost:8000/health || exit 1
ENTRYPOINT ["/usr/local/bin/docker-entrypoint-all.sh"]
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
