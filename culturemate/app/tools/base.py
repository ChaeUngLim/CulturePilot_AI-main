"""외부 API 공통 유틸: 타임아웃, 재시도, TTL 캐시, 우아한 실패."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.config import get_settings

logger = logging.getLogger(__name__)
T = TypeVar("T")

_cache: dict[str, tuple[float, Any]] = {}

# 예산이 바닥나도 이만큼은 준다. 0초로 끊으면 캐시 적중조차 못 받는다.
MIN_TOOL_TIMEOUT_S = 1.0


def cache_key(prefix: str, payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return f"{prefix}:{hashlib.sha1(blob.encode()).hexdigest()[:16]}"


async def cached(key: str, ttl: float, fn: Callable[[], Awaitable[T]]) -> T:
    now = time.time()
    if key in _cache:
        exp, val = _cache[key]
        if exp > now:
            return val
    val = await fn()
    _cache[key] = (now + ttl, val)
    return val


async def safe_call(name: str, coro: Awaitable[T], default: T,
                    *, deadline: float | None = None) -> T:
    """외부 API 하나가 죽어도 그래프 전체가 죽지 않도록 감싼다.

    `deadline` 은 time.monotonic() 기준 종료 시각이다. 넘기면 남은 시간까지만
    기다린다. 이게 없으면 REQUEST_TIMEOUT_S(12초)를 그대로 쓰는데, 총예산이
    15초인 1단계에서는 도구 하나가 죽는 것만으로 예산 전체가 날아간다.
    실제로 기상청이 응답하지 않아 예산이 이미 0초인 시점에도 12초를 더 태웠다.

    2단계(/routes·/verify)는 예산이 60초라 deadline 없이 불러도 된다.
    """
    s = get_settings()
    timeout = s.request_timeout_s
    if deadline is not None:
        # 남은 시간이 없어도 최소 한 번은 시도한다 — 캐시 적중이면 즉시 돌아온다.
        timeout = min(timeout, max(MIN_TOOL_TIMEOUT_S, deadline - time.monotonic()))
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except TimeoutError:
        logger.warning("tool timeout: %s (%.1fs)", name, timeout)
    except Exception as exc:
        logger.warning("tool failed: %s (%s)", name, exc)
    return default
