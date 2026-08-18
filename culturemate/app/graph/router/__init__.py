"""쿼리 라우터 — 문서 목표 4('필요한 Agent만 실행')의 구현체.

두 단계로 나뉜다.
  1) classify: 발화 → RequestType + TripConditions (구조화 출력)
  2) fan_out : RequestType → 실행할 노드 집합 (정적 라우팅 테이블)

정적 테이블을 쓰는 이유: MVP 단계에서 LLM 오케스트레이터는 비용·지연·비결정성을
모두 늘린다. 라우팅 규칙이 테이블 하나이므로, Agent가 늘어나 규칙이 복잡해지는
시점에 `fan_out`만 LLM 플래너로 교체하면 나머지 그래프는 손대지 않아도 된다.

---

**어디를 고칠 것인가** — 한 파일 1,093줄이던 것을 2026-08-17 에 나눴다.
동작은 바뀌지 않았다(코드를 줄 단위로 그대로 옮겼다). 여덟 중 일곱은
«발화를 어떻게 읽는가» 였고, 정작 라우팅은 표 하나였다.

| 증상 | 파일 |
|---|---|
| 시각을 오전/오후로 잘못 읽는다 · 체류시간 | `timeparse.py` |
| 출발지·도착지를 엉뚱하게 집는다 | `endpoints.py` |
| 지역·지점·개수·종류별 몫을 못 잡는다 | `detect.py` |
| 규칙 파서 전체 순서 · LLM 결과와의 병합 | `rules.py` |
| 클라이언트 값·취향·좌표 확정 | `enrich.py` |
| 어느 노드가 실행되는가 | 이 파일의 `ROUTE_TABLE` · `fan_out` |

이 파일은 **라우팅 표와 조립**만 갖는다. 파서를 여기 두면 다시 한 덩어리가 된다.
"""
from __future__ import annotations

import logging
from datetime import date

from pydantic import BaseModel, Field

from app.config import get_settings
from app.graph.router.detect import _detect_kind_quota
from app.graph.router.endpoints import _split_endpoints
from app.graph.router.enrich import (
    _apply_override,
    _apply_taste,
    _load_profile,
    _resolve_places,
)
from app.graph.router.rules import _merge_rules, _rule_conditions, _safe_rules
from app.graph.state import CultureMateState
from app.llm.prompts import ROUTER_SYSTEM
from app.llm.provider import structured
from app.schemas import PlanFlags, RequestType, TripConditions

logger = logging.getLogger(__name__)

# 밖에서 부르는 이름. 밑줄로 시작하는 것들은 회귀 테스트가 단위로 쥐고 있는
# 판단들이다 — 이름이 사적이라는 뜻이지, 검증 대상이 아니라는 뜻은 아니다.
__all__ = [
    "ROUTE_TABLE",
    "RouteDecision",
    "_apply_override",
    "_apply_taste",
    "_detect_kind_quota",
    "_merge_rules",
    "_rule_conditions",
    "_safe_rules",
    "_split_endpoints",
    "classify",
    "fan_out",
    "need_itinerary",
]

# ---------------------------------------------------------------- 라우팅 테이블
ROUTE_TABLE: dict[RequestType, PlanFlags] = {
    RequestType.ARCHIVE_QUERY: PlanFlags(use_archive=True),
    # 추천도 일정을 만든다. 장소만 나열하면 지도·동선이 비어 화면이 성립하지 않고,
    # 사용자는 "그래서 몇 시에 어디부터?"를 다시 물어야 한다.
    RequestType.PLACE_RECOMMEND: PlanFlags(
        use_archive=True, use_discovery=True, build_itinerary=True),
    RequestType.PLAN_CREATE: PlanFlags(
        use_archive=True, use_discovery=True, build_itinerary=True, nearby_fill=True),
    RequestType.PLAN_MODIFY: PlanFlags(
        use_archive=True, use_current_plan=True, build_itinerary=True),
    RequestType.REVISIT_PLAN: PlanFlags(
        use_archive=True, use_discovery=True, build_itinerary=True, freshness_diff=True),
    RequestType.WEATHER_ADJUST: PlanFlags(
        use_archive=True, use_current_plan=True, use_discovery=True, build_itinerary=True),
    RequestType.GAP_FILL: PlanFlags(
        use_archive=True, use_current_plan=True, use_discovery=True,
        build_itinerary=True, nearby_fill=True),
}

_KEYWORDS: list[tuple[RequestType, tuple[str, ...]]] = [
    (RequestType.GAP_FILL, ("일찍 끝", "시간이 비", "남는 시간", "빈 시간")),
    (RequestType.WEATHER_ADJUST, ("비 와", "비가", "날씨", "폭염", "한파", "미세먼지")),
    (RequestType.REVISIT_PLAN, ("다시 가", "재방문", "또 가")),
    (RequestType.PLAN_MODIFY, ("바꿔", "수정", "빼줘", "빼고", "추가해")),
    (RequestType.ARCHIVE_QUERY, ("갔던", "예전에", "작년", "기록", "언제 갔")),
    (RequestType.PLAN_CREATE, ("일정", "코스", "하루", "플랜", "짜줘")),
]



class RouteDecision(BaseModel):
    request_type: RequestType = RequestType.PLAN_CREATE
    conditions: TripConditions = Field(default_factory=TripConditions)
    route_reason: str = ""


async def classify(state: CultureMateState) -> dict:
    """발화를 구조화한다.

    LLM은 '있으면 좋은 것'이지 필수가 아니다. 규칙 추출이 지역·날짜·이동수단·동행자를
    이미 잡으므로, 모델이 느리면 기다리지 않고 규칙 결과로 진행한다.
    사용자 입장에서 첫 화면이 10초 이상 멈추는 것이 부정확한 분류보다 나쁘다.
    """
    import asyncio
    import time

    from app.graph.budget import Budget

    budget = Budget.start()          # 여기서부터 15초를 센다
    started = time.perf_counter()
    query = state.get("raw_query") or _last_human(state)
    rules = _safe_rules(query)
    decision = RouteDecision(
        request_type=_keyword_route(query),
        conditions=rules,
        route_reason="규칙 기반 분류",
    )

    timeout = get_settings().router_timeout_s
    llm_used = False
    skipped = _rules_suffice(_keyword_match(query), rules)
    if skipped:
        logger.info("규칙이 요청 유형·장소·시각을 모두 채워 라우터 LLM 을 건너뜁니다")
    else:
        try:
            chain = structured("router", RouteDecision)
            decision = await asyncio.wait_for(
                chain.ainvoke([
                    {"role": "system",
                     "content": ROUTER_SYSTEM + f"\n오늘 날짜: {date.today().isoformat()}"},
                    {"role": "user", "content": query},
                ]),
                timeout=timeout,
            )
            llm_used = True
        except TimeoutError:
            logger.info("라우터 LLM %.1f초 초과 — 규칙 결과로 진행합니다", timeout)
        except Exception as exc:
            logger.warning("라우터 LLM 실패(%s) — 규칙 결과로 진행합니다", exc)

    flags = ROUTE_TABLE.get(decision.request_type, PlanFlags()).model_copy()
    # LLM이 놓친 항목은 규칙으로 메운다(LLM 우선, 규칙은 빈칸만 채움)
    conditions = _merge_rules(decision.conditions, rules)
    conditions = _apply_override(conditions, state.get("conditions_override"))
    if conditions.free_text is None:
        conditions.free_text = query

    # 취향을 여기서 채운다. 아카이브 서브그래프는 탐색과 병렬로 돌기 때문에,
    # 탐색이 시작되는 시점에는 프로필이 아직 State에 없다.
    profile = await _load_profile(state.get("user_id", ""))
    conditions = _apply_taste(conditions, profile)

    # 출발지·도착지·지역을 여기서 좌표로 확정한다.
    #
    # 예전에는 탐색 서브그래프가 이 일을 했는데, 서브그래프가 conditions 를
    # 제자리에서 고쳐도 그 변경이 부모 상태로 돌아오지 않는다(출력 스키마에
    # conditions 가 없다). 인메모리 체크포인터에서는 같은 객체를 공유해 우연히
    # 동작하지만, Postgres 체크포인터는 단계마다 직렬화하므로 통째로 사라진다.
    # 그래서 일정·지도가 "판교역에서 출발"을 끝내 반영하지 못했다.
    #
    # 위치 해석은 '요청을 이해하는 일'이지 '장소를 찾는 일'이 아니다.
    # 라우터의 출력에 담기면 이후 모든 단계가 같은 값을 본다.
    await _resolve_places(conditions)

    elapsed = time.perf_counter() - started
    return {
        "raw_query": query,
        "request_type": decision.request_type,
        "conditions": conditions,
        "flags": flags,
        "route_reason": decision.route_reason,
        "taste_profile": profile,
        "deadline": budget.deadline,
        "replan_round": state.get("replan_round") or 0,
        "trace": [f"router:{decision.request_type.value}"
                  # 규칙으로 끝난 이유를 구분한다 — 건너뛴 것과 LLM 이 실패한 것은 다르다
                  f"{'' if llm_used else '(규칙·건너뜀)' if skipped else '(규칙)'}"
                  f" {elapsed:.1f}s"],
    }


def fan_out(state: CultureMateState) -> list[str]:
    """조건부 팬아웃. 여기서 반환된 노드만 이번 실행에 참여한다."""
    flags: PlanFlags = state.get("flags") or PlanFlags()
    targets: list[str] = []
    if flags.use_archive:
        targets.append("archive")
    if flags.use_discovery:
        targets.append("discovery")
    if flags.use_current_plan:
        targets.append("current_plan")
    return targets or ["archive"]


def need_itinerary(state: CultureMateState) -> str:
    flags: PlanFlags = state.get("flags") or PlanFlags()
    return "itinerary" if flags.build_itinerary else "compose"


def _keyword_match(q: str) -> RequestType | None:
    """키워드가 실제로 맞았을 때만 타입을 준다. 안 맞으면 None.

    `_keyword_route` 의 기본값(PLACE_RECOMMEND)은 '추천 요청이다'가 아니라
    '무슨 요청인지 모르겠다'는 뜻이다. 둘을 구분해야 LLM 을 건너뛸지 판단할 수 있다.
    """
    for rtype, keys in _KEYWORDS:
        if any(k in q for k in keys):
            return rtype
    return None


def _keyword_route(q: str) -> RequestType:
    return _keyword_match(q) or RequestType.PLACE_RECOMMEND


def _rules_suffice(rt: RequestType | None, c: TripConditions) -> bool:
    """규칙만으로 충분한가 — 라우터 LLM 을 건너뛸지 판단한다.

    라우터는 중앙값 3.6초를 쓰는데 대부분이 4,730자 스키마를 채우는 시간이다.
    규칙이 이미 같은 값을 뽑았다면 그 3.6초는 통째로 낭비다.

    다만 틀리면 사용자가 의도하지 않은 일정을 받는다. 그래서 보수적으로 잡는다 —
    '무슨 요청인지'와 '어디서·언제'가 **모두** 규칙으로 잡혔을 때만 건너뛴다.
    하나라도 비면 LLM 에 맡긴다. 건너뛰어서 아끼는 건 3.6초지만,
    잘못 건너뛰면 일정 전체가 틀리기 때문이다.
    """
    if rt is None:                    # 키워드 불일치 = 무슨 요청인지 모른다
        return False
    where = bool(c.origin_name or c.destination_name or c.regions or c.landmark)
    when = bool(c.start_time or c.end_time or c.stops or c.date)
    return where and when


def _last_human(state: CultureMateState) -> str:
    for m in reversed(state.get("messages") or []):
        content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else None)
        role = getattr(m, "type", None) or (m.get("role") if isinstance(m, dict) else None)
        if content and role in ("human", "user"):
            return content if isinstance(content, str) else str(content)
    return ""
