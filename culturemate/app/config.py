"""전역 설정. 모든 외부 의존성은 여기서만 환경변수를 읽는다."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMBackend = Literal["nim", "openai", "anthropic", "fake"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---------- LLM / Embedding ----------
    llm_backend: LLMBackend = "nim"
    # NVIDIA NIM (self-host 또는 build.nvidia.com)
    nvidia_api_key: str | None = None
    nim_chat_base_url: str | None = None       # 예: http://nim-chat:8000/v1
    nim_embed_base_url: str | None = None      # 예: http://nim-embed:8080/v1
    nim_rerank_base_url: str | None = None     # 예: http://nim-rerank:2016/v1

    model_router: str = ""     # 비우면 백엔드별 기본값 (app/llm/provider.py)
    model_planner: str = ""    # 추론·일정 편성
    model_writer: str = ""     # 설명·리포트 생성
    model_fast: str = ""       # 요약·태깅 대량 처리
    model_embed: str = ""
    # nv-rerankqa-mistral-4b-v3 은 404, llama-3.2-nv-rerankqa-1b-v2 는 EOL(410) 이다.
    # 카탈로그에 이름이 남아 있어도 살아 있다는 뜻이 아니라서 직접 불러 보고 골랐다.
    model_rerank: str = "nvidia/llama-nemotron-rerank-1b-v2"
    embed_dim: int = 1024

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # ---------- Datastore ----------
    pg_dsn: str = "postgresql://culturemate:culturemate@localhost:5432/culturemate"
    checkpoint_dsn: str | None = None  # 미지정 시 pg_dsn 사용

    # ---------- 외부 도구 ----------
    # 공공데이터포털(data.go.kr) — 서비스별 활용신청, 인증키는 계정당 하나
    data_go_kr_key: str | None = None
    culture_api_key: str | None = None    # 미지정 시 data_go_kr_key 사용
    weather_api_key: str | None = None    # 미지정 시 data_go_kr_key 사용

    # 기상청 API허브(apihub.kma.go.kr) — 공공데이터포털과 별개 서비스.
    # 엔드포인트와 인증 파라미터(authKey)가 다르므로 키를 따로 받는다.
    # 이 값이 있으면 공공데이터포털 대신 API허브를 쓴다.
    kma_api_hub_key: str | None = None

    # 문화 API 엔드포인트 — 포털/데이터셋에 따라 경로가 달라 교체 가능하게 둔다.
    #
    # 기본값을 비워 둔다. 예전에는 그럴듯한 주소를 박아 뒀는데, 그 서비스가 폐기되어
    # 매 요청마다 400(NO_OPENAPI_SERVICE_ERROR)을 맞았다. 죽은 기본값은 없는 것보다
    # 나쁘다 — 헛호출에 예산을 쓰고, 진단에는 '키 문제'처럼 보이는 오류만 쌓인다.
    #
    # 발급처 마이페이지의 '활용신청 상세'에 적힌 요청 주소를 그대로 넣는다.
    # 비어 있으면 웹검색과 내장 카탈로그가 행사 자리를 대신 채운다.
    culture_api_endpoint: str = ""
    # 상시 문화공간(미술관·박물관·공연장). 비우면 네이버 지역검색만 쓴다.
    culture_facility_endpoint: str = ""

    # NCP Maps — Geocoding / Directions (Application key)
    naver_client_id: str | None = None
    naver_client_secret: str | None = None
    # 신규 콘솔은 maps.apigw, 구 콘솔은 naveropenapi 를 쓴다.
    # 실패 시 반대쪽으로 1회 폴백하므로 어느 세대든 동작한다.
    ncp_maps_base_url: str = "https://maps.apigw.ntruss.com"
    ncp_maps_base_url_alt: str = "https://naveropenapi.apigw.ntruss.com"

    # NAVER Developers 검색 API — 지역검색(주변 장소). NCP와 별개의 자격증명이다.
    naver_search_client_id: str | None = None
    naver_search_client_secret: str | None = None

    # 도보·대중교통 경로. NAVER Directions는 자동차만 제공하므로 따로 붙인다.
    # 둘 다 선택 — 없으면 거리 기반 추정으로 내려간다.
    ors_api_key: str | None = None        # OpenRouteService (도보) 무료 2,500건/일
    odsay_api_key: str | None = None      # ODsay LAB (대중교통) 무료 1,000건/일
    tavily_api_key: str | None = None
    exa_api_key: str | None = None

    @staticmethod
    def _decode_key(value: str | None) -> str | None:
        """공공데이터포털 인증키의 Encoding/Decoding 형식을 흡수한다.

        포털은 같은 키를 두 형태로 준다.
          Decoding: ...aVu1w==      (원본)
          Encoding: ...aVu1w%3D%3D  (URL 인코딩된 것)
        httpx가 파라미터를 다시 인코딩하므로 Encoding 형식을 그대로 넣으면
        %253D 가 되어 인증에 실패한다. 여기서 항상 원본으로 되돌린다.
        """
        if not value:
            return None
        if "%" in value:
            from urllib.parse import unquote

            return unquote(value)
        return value

    @property
    def odsay_key(self) -> str | None:
        """ODsay도 '일반 키'와 'URL 인코딩 키' 두 벌을 준다.

        콘솔에서 인코딩된 쪽을 복사해 넣는 실수가 흔한데, httpx가 한 번 더
        인코딩해 %253D 가 되면서 인증에 실패한다. 공공데이터포털과 같은 함정이라
        같은 방식으로 흡수한다.
        """
        return self._decode_key(self.odsay_api_key)

    @property
    def culture_key(self) -> str | None:
        """행사 API 용 키. 엔드포인트 발급처(KCISA 또는 공공데이터포털)에 맞춰 넣는다."""
        return self._decode_key(self.culture_api_key or self.data_go_kr_key)

    @property
    def portal_key(self) -> str | None:
        """공공데이터포털(data.go.kr) 전용 키.

        `culture_key` 와 갈라 둔 이유가 있다. 행사 API 를 KCISA 로 옮기면
        CULTURE_API_KEY 에 KCISA 키가 들어가는데, 문화시설 API 는 여전히
        data.go.kr 이라 그 키를 그대로 쓰면 403(code 30)이 난다.
        발급처가 다르면 키도 다르다 — 한 칸에 몰아넣지 않는다.
        """
        return self._decode_key(self.data_go_kr_key or self.culture_api_key)

    @property
    def weather_key(self) -> str | None:
        if self.kma_api_hub_key:
            return self.kma_api_hub_key      # API허브 키는 인코딩 형식이 아니다
        return self._decode_key(self.weather_api_key or self.data_go_kr_key)

    @property
    def weather_source(self) -> str:
        return "apihub" if self.kma_api_hub_key else "data_go_kr"

    # ---------- 검색/개인화 튜닝 ----------
    archive_top_k: int = 40          # facet별 1차 후보
    archive_final_k: int = 12        # rerank 후 최종
    rrf_k: int = 60                  # Reciprocal Rank Fusion 상수
    recency_half_life_days: float = 180.0
    friction_boost: float = 0.35     # 불편 경험 가중(경고 재현율 우선)
    dense_weight: float = 0.6
    lexical_weight: float = 0.4
    max_stops: int = 8                # 하루 일정에 넣을 장소 상한
    candidate_pool: int = 60          # 랭킹까지 남기는 후보 수(검증하지 않음)
    verify_top_k: int = 12            # 실제로 검증할 상위 후보 — 일정엔 최대 6곳만 들어간다
    verify_concurrency: int = 8

    # ---------- 운영 ----------
    request_timeout_s: float = 12.0
    # 라우팅 LLM 상한. 넘으면 규칙 기반 결과를 쓴다 — 첫 화면이 멈춰 있는 것보다 낫다.
    router_timeout_s: float = 6.0
    # 응답 전체 예산(초). 각 단계가 남은 시간을 보고 범위를 스스로 줄인다.
    total_budget_s: float = 15.0
    langsmith_tracing: bool = False
    log_level: str = "INFO"
    hitl_enabled: bool = True
    severity_threshold_for_hitl: int = Field(
        default=2, description="이 값 이상 severity 이슈는 사용자 확인 필요"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
