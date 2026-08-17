"""웹 검색 (Tavily / Exa).

용도를 좁게 유지한다 — 공식 출처 확인과 최신 정보 보강에만 쓴다.
장소 발견 자체를 웹검색에 의존하면 출처가 불분명한 후보가 섞이고, 검증 비용이 커진다.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.config import get_settings
from app.tools.base import cache_key, cached, safe_call

logger = logging.getLogger(__name__)


async def search(query: str, *, k: int = 5,
                 domains: list[str] | None = None) -> list[dict[str, Any]]:
    if not query.strip():
        return []
    key = cache_key("web.search", {"q": query, "k": k, "d": domains})
    return await cached(key, ttl=900, fn=lambda: _search(query, k, domains))


async def _search(query: str, k: int, domains: list[str] | None) -> list[dict[str, Any]]:
    """설정된 제공자를 순서대로 시도하고, 결과가 나온 첫 번째를 쓴다.

    예전에는 Tavily 키가 있으면 거기서 끝났다. 그러면 두 번째 키를 넣어 둔 의미가 없다 —
    무료 쿼터가 떨어져 0건이 돌아와도 대체가 일어나지 않고, 사용자는 '왜 아무것도
    안 나오지'만 보게 된다. 빈 결과도 실패로 보고 다음 제공자로 넘어간다.
    """
    s = get_settings()
    providers = []
    if s.tavily_api_key:
        providers.append(("tavily", _tavily))
    if s.exa_api_key:
        providers.append(("exa", _exa))

    for name, fn in providers:
        hits = await safe_call(name, fn(query, k, domains), [])
        if hits:
            return hits
    return []


async def _tavily(query: str, k: int, domains: list[str] | None) -> list[dict[str, Any]]:
    from langchain_tavily import TavilySearch

    kwargs: dict[str, Any] = {
        "max_results": k,
        "tavily_api_key": get_settings().tavily_api_key,
        "search_depth": "basic",
    }
    if domains:
        kwargs["include_domains"] = [d for d in domains if d]
    res = await TavilySearch(**kwargs).ainvoke({"query": query})

    if isinstance(res, dict):
        items = res.get("results", [])
    elif isinstance(res, list):
        items = res
    else:
        return []
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""),
         "content": r.get("content", ""), "score": r.get("score", 0.0)}
        for r in items if isinstance(r, dict)
    ]


async def _exa(query: str, k: int, domains: list[str] | None) -> list[dict[str, Any]]:
    """Tavily 대체재. exa-py 는 동기 클라이언트라 별도 스레드에서 돌린다.

    async 함수 안에서 그냥 부르면 그동안 이벤트 루프가 멈춘다. 탐색은 네 갈래가
    동시에 도는 자리라, 여기서 막히면 병렬이 직렬이 되고 15초 예산이 통째로 날아간다.
    """
    import asyncio

    from exa_py import Exa

    exa = Exa(api_key=get_settings().exa_api_key)
    res = await asyncio.to_thread(
        exa.search_and_contents, query,
        num_results=k, include_domains=domains or None, text=True)
    return [{"title": r.title or "", "url": r.url or "",
             "content": (r.text or "")[:2000], "score": 0.5} for r in res.results]


def available() -> bool:
    s = get_settings()
    return bool(s.tavily_api_key or s.exa_api_key)


# --------------------------------------------------- 블로그 제목 ≠ 장소 이름
# 웹 검색 결과 제목은 대부분 블로그 글 제목이다. 그대로 후보로 쓰면
# "8월 서울 전시회 추천<강남,서초구 BEST5>" 같은 글이 일정에 장소로 들어간다.
# 좌표도 운영시간도 없으니 지도에 찍히지 않고 이동시간도 계산되지 않는다.
_LISTICLE = (
    "추천", "총정리", "정리", "모음", "베스트", "best", "top", "순위", "리스트",
    "가볼만한", "갈만한", "가볼 만한", "갈 만한", "하기 좋은", "좋은 곳", "명소",
    "후기", "리뷰", "블로그", "소식", "알아보기", "총집합", "핫플", "코스",
)
# 장소임을 알려주는 토큰. 접미사가 아니라 '포함' 으로 본다 —
# '책읽는미술관 본사', '국립현대미술관 서울관'처럼 뒤에 말이 더 붙는 게 흔하다.
_VENUE_TOKEN = (
    "미술관", "박물관", "갤러리", "뮤지엄", "극장", "공연장", "아트센터",
    "문화센터", "복합문화", "도서관", "문화관", "문화원", "전시관", "기념관",
    "아트홀", "전당", "스퀘어", "공원", "수목원", "식물원", "서점", "책방",
    "공방", "스튜디오", "시네마", "아트스페이스", "예술회관", "센터", "회관",
)
# 글 제목의 흔적. 장소 이름은 이렇게 끝나지 않는다.
_SENTENCE_TAIL = ("요", "다", "죠", "함", "음", "임", "네", "봄", "가요", "까지")


def looks_like_place(title: str) -> bool:
    """이 제목이 '장소 이름'인지 '글 제목'인지 가른다.

    보수적으로 판정한다 — 애매하면 버린다. 잘못 통과시키면 사용자가
    존재하지 않는 곳으로 이동하는 일정을 받게 되는데, 그건 이 서비스가
    피해야 할 단 하나의 실패다. 반대로 잘못 버려도 카탈로그·공공 API가
    후보를 채우므로 손실이 작다.
    """
    name = (title or "").strip()
    if len(name) < 3 or len(name) > 25:
        return False
    if name in _VENUE_TOKEN:
        return False        # '센터'·'공원'처럼 종류만 남은 건 장소가 아니다
    low = name.lower()
    if any(w in low for w in _LISTICLE):
        return False
    # 목록형 신호: 괄호·구분자·문장부호·개수 표현
    if any(ch in name for ch in "[]<>《》!?｜|·,"):
        return False
    if re.search(r"\d+\s*(곳|선|가지|위)", name):
        return False
    if name.count(" ") >= 3:
        return False
    if name.endswith(_SENTENCE_TAIL):
        return False
    return any(tok in name for tok in _VENUE_TOKEN)
