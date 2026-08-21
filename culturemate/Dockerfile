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

# 컨테이너 안에서 `python -m pytest` 를 돌리기 위한 셋. 셋 다 있어야 한다 —
#   pyproject.toml  pytest 설정(testpaths·asyncio_mode)이 여기 있다. 없으면
#                   수집 범위가 달라진다.
#   docs/*.md       test_docs_contract.py 가 문서를 직접 읽어 소스와 대조한다.
#                   .md 만 넣는다 — docs 전체는 36MB(PLANNING 캡처·pptx·pdf)이고
#                   테스트가 읽는 것은 .md 412KB 뿐이다.
#   tests           본체.
#
# 이걸 이미지에 넣지 않던 동안에는 `docker cp` 로 밀어 넣어 썼다. 그런데 대상
# 디렉터리가 이미 있으면 docker cp 는 그 **안으로** 복사한다 — `/srv/tests/tests`
# 가 그렇게 생겼고, pytest 가 같은 테스트를 두 벌(388개) 수집했다. 게다가 사본은
# `parents[1]` 이 한 단계 얕아져 `/srv/tests/docs/` 를 읽으려다 4건이 실패했다.
# 실행 시간은 두 배가 되고 실패 4건은 매번 떴다.
COPY pyproject.toml .
COPY docs/*.md ./docs/
COPY tests ./tests

COPY docker-entrypoint-all.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint-all.sh

# 5432 는 열지 않는다. API 가 같은 컨테이너 안에서 127.0.0.1 로 붙으므로
# 호스트로 내보낼 이유가 없다. DB 를 직접 보려면 `docker exec culturemate psql` 을 쓴다.
EXPOSE 8000
HEALTHCHECK --interval=30s --start-period=60s \
    CMD curl -f http://localhost:8000/health || exit 1
ENTRYPOINT ["/usr/local/bin/docker-entrypoint-all.sh"]
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
