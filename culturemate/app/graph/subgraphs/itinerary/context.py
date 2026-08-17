"""병렬 컨텍스트 4갈래와 제약 구성 (문서 04 상단).

    START → ⟨병렬⟩ geo / hours / weather / preference → assemble_constraints

넷을 병렬로 두는 이유는 서로 의존하지 않기 때문이다. 하나가 비어도 나머지로
일정이 성립해야 한다 — 날씨는 있으면 좋은 정보지 일정의 전제가 아니다.
"""
from __future__ import annotations

import logging

from app.graph.budget import from_state
from app.graph.state import ItineraryState
from app.graph.subgraphs.itinerary.legs import _can_measure
from app.schemas import Candidate
from app.tools import maps, weather
from app.tools.base import safe_call

logger = logging.getLogger(__name__)

# --------------------------------------------------------------- 병렬 컨텍스트
async def ctx_geo(state: ItineraryState) -> dict:
    from app.graph.budget import COST_TRAVEL_MATRIX, from_state, log_skip

    c = state["conditions"]
    cands = (state.get("candidates") or [])[:12]
    points = [c.origin] if c.origin else []
    points += [x.geo for x in cands if x.geo]

    # 실측 행렬은 N² 호출이라 비싸다. 시간이 없으면 거리 추정으로 간다.
    budget = from_state(state)
    mode = c.transport if c.transport != "unknown" else "best"
    # 순서를 정하는 N² 행렬은 중립 기준으로 한 번만 만든다.
    # 최종 구간은 뒤에서 수단별로 다시 재므로 여기서 정밀할 필요가 없다.
    matrix_mode = "subway" if mode == "best" else mode
    estimate_only = not budget.allows(COST_TRAVEL_MATRIX)
    if estimate_only:
        log_skip("이동시간 실측", budget, "거리 기반 추정으로 대체")

    matrix = await safe_call(
        "maps.matrix",
        maps.travel_matrix(points, matrix_mode, estimate_only=estimate_only), [],
        deadline=budget.deadline)
    estimated = estimate_only or not matrix or not _can_measure(mode)
    return {"context": {"points": points, "travel_matrix": matrix, "mode": mode,
                        "travel_estimated": estimated},
            "trace": [f"itin.geo:{len(points)}:{mode}{'~' if estimated else ''}"]}


async def ctx_hours(state: ItineraryState) -> dict:
    hours = {
        c.id: {"opening_hours": c.opening_hours, "closed_days": c.closed_days,
               "dwell": c.expected_dwell_min}
        for c in (state.get("candidates") or [])
    }
    return {"context": {"hours": hours}, "trace": [f"itin.hours:{len(hours)}"]}


async def ctx_weather(state: ItineraryState) -> dict:
    """시간대별 예보 + 현재 실황.

    예보는 일정 배치에, 실황은 '지금 나가도 되나'에 쓴다. 현장 재계획 시점에는
    3시간 전 예보보다 방금 관측값이 맞다.
    """
    import asyncio

    c = state["conditions"]
    flags = state.get("flags")
    need_now = bool(flags and flags.nearby_fill)     # gap_fill·일정 조기 종료 경로

    # 날씨는 있으면 좋은 정보지 일정의 전제가 아니다. 예산을 넘겨 가며 기다릴 이유가 없다.
    dl = from_state(state).deadline
    fc, now = await asyncio.gather(
        safe_call("weather.hourly", weather.hourly(c.origin, c.date), {}, deadline=dl),
        safe_call("weather.current", weather.current(c.origin), {}, deadline=dl)
        if need_now else _empty(),
    )
    return {"context": {"weather": fc, "risky_hours": weather.risky_hours(fc),
                        "weather_now": now},
            "trace": [f"itin.weather:{len(fc)}{'+now' if now else ''}"]}


async def _empty() -> dict:
    return {}


async def ctx_preference(state: ItineraryState) -> dict:
    profile = state.get("taste_profile")
    hits = state.get("archive_hits") or []
    penalty = {h.place_id: -0.3 for h in hits if h.friction and h.place_id}
    boost = {h.place_id: 0.2 for h in hits if h.sentiment > 0.4 and h.place_id}
    return {"context": {"pref_penalty": penalty, "pref_boost": boost,
                        "profile": profile.model_dump() if profile else None},
            "trace": ["itin.pref"]}


# ------------------------------------------------------------------- 제약 구성
async def assemble_constraints(state: ItineraryState) -> dict:
    """병렬 결과를 하나의 제약조건 묶음으로 합친다(문서 04의 마름모 노드)."""
    ctx = state.get("context") or {}
    c = state["conditions"]
    risky = set(ctx.get("risky_hours") or [])
    scored: list[Candidate] = []
    for cand in state.get("candidates") or []:
        # 사용자가 '실내'를 명시했으면 야외는 아예 뺀다.
        # 점수만 깎으면 후보가 부족할 때 야외가 그대로 올라와, 요청을 무시한 결과가 된다.
        if c.indoor_pref == "indoor" and cand.indoor is False:
            continue
        if c.indoor_pref == "outdoor" and cand.indoor is True:
            continue

        s = cand.final_score
        s += ctx.get("pref_boost", {}).get(cand.place_id, 0.0)
        s += ctx.get("pref_penalty", {}).get(cand.place_id, 0.0)
        if risky and cand.indoor is False:
            s -= 0.3                      # 악천후 → 야외 감점
        if risky and cand.indoor is True:
            s += 0.2
        cand.final_score = s
        scored.append(cand)

    # 조건이 너무 좁아 후보가 사라지면 필터를 풀고 감점만 적용한다.
    # '조건에 딱 맞는 것이 없음'보다 '가까운 것이라도 보여주기'가 낫다.
    if not scored and (state.get("candidates") or []):
        logger.info("실내/야외 조건으로 후보가 모두 걸러져 필터를 완화합니다")
        for cand in state["candidates"]:
            cand.final_score -= 0.4 if cand.indoor is False else 0.0
            scored.append(cand)
    scored.sort(key=lambda x: x.final_score, reverse=True)
    return {"candidates": scored,
            "context": {"constraints_ready": True},
            "trace": [f"itin.constraints:{len(scored)}"]}
