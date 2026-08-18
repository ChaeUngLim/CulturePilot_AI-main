"""외부 API 공용 HTTP 클라이언트.

노드는 실패를 신경 쓰지 않는다. 여기서 타임아웃·재시도·캐시·우아한 실패를 모두 흡수하고,
호출부에는 '값 또는 기본값'만 돌려준다. 도구 하나가 죽어서 그래프가 멈추면 안 되기 때문이다.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import get_settings
from app.tools.base import cache_key, cached

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None
# 이 클라이언트를 만든 이벤트 루프. 커넥션 풀은 만들어진 루프에 묶여 있어서,
# 루프가 바뀐 뒤 같은 클라이언트를 쓰면 'Event loop is closed'로 터진다.
# 운영에서는 uvicorn 루프가 하나뿐이라 드러나지 않지만, 테스트는 함수마다
# 새 루프를 쓰므로 앞선 테스트가 연 클라이언트가 다음 테스트를 깨뜨렸다.
_client_loop: asyncio.AbstractEventLoop | None = None
RETRY_STATUS = {429, 500, 502, 503, 504}

# 도구별 마지막 실패 내용. 진단 엔드포인트가 '왜 안 되는지'를 그대로 보여주기 위한 것.
# 키가 틀렸는지, 활용신청이 안 됐는지, 네트워크가 막혔는지는 응답 본문에만 적혀 있다.
LAST_ERROR: dict[str, str] = {}


def record_error(name: str, message: str) -> None:
    LAST_ERROR[name] = message[:400]


def last_error(name: str) -> str | None:
    return LAST_ERROR.get(name)


async def get_client() -> httpx.AsyncClient | None:
    """현재 이벤트 루프의 공용 클라이언트. 생성 자체가 실패할 수 있다(프록시 설정 등)."""
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is not None and not _client.is_closed and _client_loop is loop:
        return _client
    # 루프가 바뀌었으면 이전 클라이언트는 쓸 수 없다. aclose()조차 옛 루프를
    # 건드리므로 부르지 않고 버린다 — 그 루프가 닫힐 때 소켓도 함께 정리됐다.
    if _client is not None and _client_loop is not loop:
        logger.debug("이벤트 루프가 바뀌어 HTTP 클라이언트를 새로 만듭니다")
        _client = None
    s = get_settings()
    try:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(s.request_timeout_s),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=True,
            headers={"User-Agent": "CultureMate/0.1"},
        )
    except Exception as exc:
        # 환경 문제(프록시 등)는 요청 전에 터진다. 여기서 잡지 않으면
        # 노드가 통째로 죽어 그래프가 멈춘다 — 도구 하나의 실패로 끝내야 한다.
        logger.error("HTTP 클라이언트 생성 실패: %s", exc)
        record_error("http.client", f"{type(exc).__name__}: {exc}")
        _client, _client_loop = None, None
        return None
    _client_loop = loop
    return _client


async def close_client() -> None:
    """커넥션 풀을 닫는다. 앱 종료(lifespan)와 스크립트 종료에서 부른다."""
    global _client, _client_loop
    # 만든 루프가 아니면 닫을 수 없다 — 그 루프는 이미 닫혔고 소켓도 정리됐다.
    same_loop = _client_loop is asyncio.get_running_loop()
    if _client is not None and not _client.is_closed and same_loop:
        await _client.aclose()
    _client, _client_loop = None, None


async def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    ttl: float | None = None,
    retries: int = 2,
    name: str = "http",
) -> Any:
    """GET → JSON. 실패하면 None. ttl을 주면 응답을 캐시한다."""
    if ttl:
        key = cache_key(f"http.{name}", {"u": url, "p": params})
        return await cached(key, ttl, lambda: _get_json(url, params, headers, retries, name))
    return await _get_json(url, params, headers, retries, name)


async def post_json(
    url: str,
    *,
    json: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    ttl: float | None = None,
    retries: int = 1,
    name: str = "http",
) -> Any:
    """POST → JSON. OpenRouteService처럼 본문으로 좌표를 받는 곳에 쓴다."""
    if ttl:
        key = cache_key(f"http.{name}", {"u": url, "b": json})
        return await cached(key, ttl, lambda: _post_json(url, json, headers, retries, name))
    return await _post_json(url, json, headers, retries, name)


async def _post_json(url, body, headers, retries, name) -> Any:
    client = await get_client()
    if client is None:
        record_error(name, last_error("http.client") or "HTTP 클라이언트를 만들 수 없습니다")
        return None
    delay = 0.4
    for attempt in range(retries + 1):
        try:
            res = await client.post(url, json=body, headers=headers)
            if res.status_code in RETRY_STATUS and attempt < retries:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if res.status_code >= 400:
                msg = f"HTTP {res.status_code}: {res.text[:300]}"
                logger.warning("%s %s", name, msg)
                record_error(name, msg)
                return None
            parsed = _parse(res, name)
            if parsed is not None:
                LAST_ERROR.pop(name, None)
            return parsed
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt < retries:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            logger.warning("%s 실패: %s", name, exc)
            record_error(name, f"{type(exc).__name__}: {exc}")
            return None
    return None


async def _get_json(url, params, headers, retries, name) -> Any:
    client = await get_client()
    if client is None:
        record_error(name, last_error("http.client") or "HTTP 클라이언트를 만들 수 없습니다")
        return None
    delay = 0.4
    for attempt in range(retries + 1):
        try:
            res = await client.get(url, params=params, headers=headers)
            if res.status_code in RETRY_STATUS and attempt < retries:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if res.status_code >= 400:
                msg = f"HTTP {res.status_code}: {res.text[:300]}"
                logger.warning("%s %s", name, msg)
                record_error(name, msg)
                return None
            parsed = _parse(res, name)
            if parsed is not None:
                LAST_ERROR.pop(name, None)
            return parsed
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt < retries:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            logger.warning("%s 실패: %s", name, exc)
            record_error(name, f"{type(exc).__name__}: {exc}")
            return None
    return None


def _parse(res: httpx.Response, name: str) -> Any:
    """공공데이터포털은 dataType 무시하고 XML을 주는 경우가 있어 둘 다 처리한다."""
    ctype = res.headers.get("content-type", "")
    text = res.text.strip()
    if "json" in ctype or text.startswith(("{", "[")):
        try:
            return res.json()
        except ValueError:
            pass
    if text.startswith("<"):
        return xml_to_dict(text)
    logger.warning("%s: 알 수 없는 응답 형식 (%s)", name, ctype)
    record_error(name, f"알 수 없는 응답 형식({ctype}): {text[:200]}")
    return None


def xml_to_dict(text: str) -> dict | None:
    """XML → dict. 같은 태그가 반복되면 리스트로 접는다."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        logger.warning("XML 파싱 실패: %s", exc)
        return None

    def walk(el) -> Any:
        children = list(el)
        if not children:
            return (el.text or "").strip()
        out: dict[str, Any] = {}
        for child in children:
            value = walk(child)
            if child.tag in out:
                if not isinstance(out[child.tag], list):
                    out[child.tag] = [out[child.tag]]
                out[child.tag].append(value)
            else:
                out[child.tag] = value
        return out

    return {root.tag: walk(root)}


def dig(obj: Any, *path: str, default: Any = None) -> Any:
    """중첩 dict를 안전하게 파고든다. 공공 API 응답 깊이가 제각각이라 필요하다."""
    cur = obj
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return default
    return cur if cur is not None else default


def as_list(value: Any) -> list:
    """단건이면 dict, 복수면 list로 오는 공공 API 응답을 항상 리스트로 만든다."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def to_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None
