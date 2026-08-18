"""LLM/임베딩/리랭커 provider 추상화.

기본은 NVIDIA NIM. `LLM_BACKEND=openai|anthropic|fake`로 교체 가능하며,
그래프 코드는 절대 구체 클래스를 import 하지 않는다(역할 이름으로만 요청).
"""
from __future__ import annotations

import logging
from functools import cache
from typing import Literal

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from app.config import get_settings

logger = logging.getLogger(__name__)

Role = Literal["router", "planner", "writer", "fast"]

# 백엔드별 기본 모델. .env에 모델명을 적지 않으면 여기서 고른다.
# NIM 모델명("meta/llama-...")을 OpenAI에 그대로 넘기면 404가 나므로,
# 백엔드를 바꿀 때 모델명까지 함께 바꾸는 일을 사람이 기억하지 않아도 되게 한다.
# 출력 차원을 고를 수 있는 NIM 임베딩 모델(Matryoshka 계열).
# 여기 없는 모델에 `dimensions` 를 넘기면 400 이 돌아온다 — 2026-08-18 실측.
EMBED_DIM_CONFIGURABLE = frozenset({
    "nvidia/llama-nemotron-embed-1b-v2",
})

DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "nim": {
        # 라우팅은 분류 + 짧은 구조화 출력이다. 70B를 쓰면 첫 화면이 10초 넘게 멈춘다.
        "router": "meta/llama-3.1-8b-instruct",
        "planner": "meta/llama-3.3-70b-instruct",
        "writer": "meta/llama-3.3-70b-instruct",
        "fast": "meta/llama-3.1-8b-instruct",
        "embed": "nvidia/nv-embedqa-e5-v5",
    },
    "openai": {
        "router": "gpt-4o-mini",
        "planner": "gpt-4o",
        "writer": "gpt-4o",
        "fast": "gpt-4o-mini",
        "embed": "text-embedding-3-large",
    },
    "anthropic": {
        "router": "claude-haiku-4-5-20251001",
        "planner": "claude-sonnet-5",
        "writer": "claude-sonnet-5",
        "fast": "claude-haiku-4-5-20251001",
        "embed": "",
    },
}


def effective_backend() -> str:
    """설정된 백엔드에 실제로 쓸 수 있는 키가 있는지 확인하고, 없으면 폴백한다.

    LLM_BACKEND=nim 인데 NVIDIA 키가 없으면 그래프 전체가 폴백 경로로만 돌아
    '이유 없이 결과가 빈' 상태가 된다. 조용히 실패하느니 다른 백엔드로 넘긴다.
    """
    s = get_settings()
    have = {
        "nim": bool(s.nvidia_api_key),
        "openai": bool(s.openai_api_key),
        "anthropic": bool(s.anthropic_api_key),
        "fake": True,
    }
    wanted = s.llm_backend
    if have.get(wanted):
        return wanted
    for alt in ("nim", "openai", "anthropic"):
        if have.get(alt):
            logger.warning("LLM_BACKEND=%s 이지만 키가 없어 %s 로 폴백합니다.", wanted, alt)
            return alt
    logger.warning("LLM 키가 하나도 없습니다 — fake 백엔드로 동작합니다(빈 응답).")
    return "fake"


def resolve_role(role: Role) -> tuple[str, str]:
    """역할 → (백엔드, 모델). 역할마다 다른 공급자를 쓸 수 있게 한다.

    .env 에 `MODEL_ROUTER=openai:gpt-4o-mini` 처럼 접두사를 붙이면 그 역할만
    다른 공급자로 간다. 접두사가 없으면 전역 LLM_BACKEND 를 따른다.

    이렇게 나눈 이유: 역할마다 요구가 정반대다.
      · router/fast — 사용자가 기다리는 구간. 지연이 품질보다 중요하다.
      · planner/writer — 결과의 설득력을 좌우한다. 조금 느려도 낫다.
    출시 때 무료(NIM)로 통일하려면 접두사만 지우면 된다.
    """
    s = get_settings()
    raw = {
        "router": s.model_router, "planner": s.model_planner,
        "writer": s.model_writer, "fast": s.model_fast,
    }[role]

    if raw and ":" in raw:
        prefix, _, name = raw.partition(":")
        prefix = prefix.strip().lower()
        if prefix in ("nim", "openai", "anthropic", "fake"):
            if _has_key(prefix):
                return prefix, name.strip()
            logger.warning("%s 역할이 %s 를 지정했지만 키가 없어 기본 백엔드를 씁니다.",
                           role, prefix)

    backend = effective_backend()
    return backend, _model_name(role, backend)


def _has_key(backend: str) -> bool:
    s = get_settings()
    return {
        "nim": bool(s.nvidia_api_key),
        "openai": bool(s.openai_api_key),
        "anthropic": bool(s.anthropic_api_key),
        "fake": True,
    }.get(backend, False)


def _model_name(role: Role, backend: str) -> str:
    """.env에 명시된 값이 우선. 없으면 백엔드 기본값."""
    s = get_settings()
    explicit = {
        "router": s.model_router,
        "planner": s.model_planner,
        "writer": s.model_writer,
        "fast": s.model_fast,
    }[role]
    if explicit and ":" in explicit:
        explicit = explicit.partition(":")[2].strip()
    defaults = DEFAULT_MODELS.get(backend, {})
    if not explicit:
        return defaults.get(role, "")
    # NIM 모델명을 OpenAI/Anthropic에 넘기면 404가 난다 — 형식이 어긋나면 기본값을 쓴다.
    looks_like_nim = "/" in explicit
    if backend != "nim" and looks_like_nim:
        logger.warning("모델명 %r 은 %s 백엔드와 맞지 않아 기본값을 씁니다.", explicit, backend)
        return defaults.get(role, explicit)
    if backend == "nim" and not looks_like_nim:
        return defaults.get(role, explicit)
    return explicit


@cache
def get_chat_model(role: Role = "planner", temperature: float = 0.2) -> BaseChatModel:
    s = get_settings()
    backend, model = resolve_role(role)

    if backend == "nim":
        from langchain_nvidia_ai_endpoints import ChatNVIDIA

        kwargs: dict = {"model": model, "temperature": temperature,
                        "api_key": s.nvidia_api_key}
        if s.nim_chat_base_url:
            kwargs["base_url"] = s.nim_chat_base_url      # self-host NIM
        return ChatNVIDIA(**kwargs)

    if backend == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, temperature=temperature, api_key=s.openai_api_key)

    if backend == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, temperature=temperature,
                             api_key=s.anthropic_api_key)

    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    return FakeListChatModel(responses=["{}"])


@cache
def get_embeddings() -> Embeddings:
    """임베딩 차원은 pgvector 스키마(vector(1024))에 묶여 있다.

    모델을 바꾸면 차원이 달라져 인덱스를 재생성해야 한다. OpenAI는 dimensions
    파라미터로 1024에 맞출 수 있어 스키마를 건드리지 않는다.
    """
    s = get_settings()
    backend = effective_backend()
    model = s.model_embed or DEFAULT_MODELS.get(backend, {}).get("embed", "")

    if backend == "nim":
        from langchain_nvidia_ai_endpoints import NVIDIAEmbeddings

        kwargs: dict = {"model": model, "truncate": "END", "api_key": s.nvidia_api_key}
        if s.nim_embed_base_url:
            kwargs["base_url"] = s.nim_embed_base_url
        # 출력 차원을 고를 수 있는 모델에만 넘긴다. 고정 차원 모델은 이 값을 받으면
        # 400 "This model does not support 'dimensions'" 로 임베딩이 통째로 죽는다
        # (nv-embedqa-e5-v5 에서 실측 — 2026-08-18). 반대로 넘기지 않으면
        # llama-nemotron-embed-1b-v2 는 2048 을 돌려줘 vector(1024) 삽입이 실패한다.
        if model in EMBED_DIM_CONFIGURABLE:
            kwargs["dimensions"] = s.embed_dim
        return NVIDIAEmbeddings(**kwargs)

    if backend in ("openai", "anthropic"):
        # Anthropic은 임베딩 API가 없어 OpenAI로 대체한다.
        if not s.openai_api_key:
            from langchain_core.embeddings import DeterministicFakeEmbedding

            logger.warning("임베딩용 OpenAI 키가 없어 더미 임베딩을 씁니다.")
            return DeterministicFakeEmbedding(size=s.embed_dim)
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(model=model or "text-embedding-3-large",
                                dimensions=s.embed_dim, api_key=s.openai_api_key)

    from langchain_core.embeddings import DeterministicFakeEmbedding

    return DeterministicFakeEmbedding(size=s.embed_dim)


@cache
def get_reranker():
    """Cross-encoder 리랭커. 없으면 None을 반환하고 호출부는 RRF 점수를 그대로 쓴다."""
    s = get_settings()
    if effective_backend() != "nim":
        return None   # cross-encoder 리랭커는 NIM에만 있다. 없으면 RRF 순서를 유지한다.
    try:
        from langchain_nvidia_ai_endpoints import NVIDIARerank
    except ImportError:
        return None
    kwargs: dict = {"model": s.model_rerank, "top_n": s.archive_final_k}
    if s.nim_rerank_base_url:
        kwargs["base_url"] = s.nim_rerank_base_url
    if s.nvidia_api_key:
        kwargs["api_key"] = s.nvidia_api_key
    try:
        return NVIDIARerank(**kwargs)
    except Exception:
        return None


def structured(role: Role, schema, temperature: float = 0.0):
    """구조화 출력 헬퍼. 모델이 미지원이면 JSON 파서로 폴백."""
    llm = get_chat_model(role, temperature=temperature)
    try:
        return llm.with_structured_output(schema)
    except NotImplementedError:
        from langchain_core.output_parsers import PydanticOutputParser

        return llm | PydanticOutputParser(pydantic_object=schema)
