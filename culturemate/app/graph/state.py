"""LangGraph State 스키마.

설계 원칙
  1) 하나의 루트 State에 모든 단계의 산출물을 누적한다(설명가능성/재현성).
  2) 병렬로 쓰이는 키는 전부 멱등 리듀서를 붙인다.
  3) 서브그래프 State는 루트 State의 '부분집합 + private 키'로 정의해
     컴파일된 서브그래프를 노드로 그대로 붙일 수 있게 한다.
"""
from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages

from app.graph.reducers import (
    MERGE_BY_ADVISORY_ID,
    MERGE_BY_ID,
    append_unique_str,
    merge_candidates,
    merge_dict,
    replace_list,
)
from app.schemas import (
    Advisory,
    ArchiveHit,
    Candidate,
    Decision,
    EditSignal,
    Evidence,
    Gap,
    Issue,
    Itinerary,
    PlaceDiff,
    PlanFlags,
    RequestType,
    TasteProfile,
    TripConditions,
    Verification,
)


class CultureMateState(TypedDict, total=False):
    # ---- 입력 / 세션 ----
    user_id: str
    messages: Annotated[list, add_messages]
    raw_query: str

    # ---- 라우팅 ----
    request_type: RequestType
    flags: PlanFlags
    conditions: TripConditions
    conditions_override: dict[str, Any] | None   # 네이티브 클라이언트가 주입(GPS 등)
    route_reason: str
    deadline: float                              # 응답 예산 종료 시각(monotonic)

    # ---- 아카이브 · 개인화 ----
    archive_hits: Annotated[list[ArchiveHit], MERGE_BY_ID]
    edit_signals: Annotated[list[EditSignal], MERGE_BY_ID]
    taste_profile: TasteProfile | None
    place_diffs: Annotated[list[PlaceDiff], MERGE_BY_ID]

    # ---- 탐색 · 검증 ----
    candidates: Annotated[list[Candidate], merge_candidates]
    verifications: Annotated[list[Verification], MERGE_BY_ID]

    # ---- 컨텍스트(병렬 분석 결과) ----
    context: Annotated[dict[str, Any], merge_dict]   # geo/hours/weather/pref

    # ---- 일정 ----
    current_itinerary: Itinerary | None              # 수정 요청 시 기존 일정
    itinerary: Itinerary | None
    gaps: Annotated[list[Gap], MERGE_BY_ID]
    nearby: Annotated[list[Candidate], merge_candidates]
    replan_round: int

    # ---- 검증 · HITL ----
    # 검증 결과는 누적이 아니라 교체다. 매 라운드 현재 일정을 처음부터 다시 보므로,
    # 지난 라운드의 이슈를 남기면 이미 해결됐거나 일정에서 빠진 장소의 카드가
    # 영원히 떠 있는다(reducers.replace_list 참고).
    issues: Annotated[list[Issue], replace_list]
    advisories: Annotated[list[Advisory], replace_list]
    decisions: Annotated[list[Decision], MERGE_BY_ADVISORY_ID]
    needs_user_confirm: bool

    # ---- 출력 ----
    evidence: Annotated[list[Evidence], MERGE_BY_ID]
    answer: str
    trace: Annotated[list[str], append_unique_str]
    error: str | None


# ------------------------------------------------------------------ 서브그래프
class ArchiveState(TypedDict, total=False):
    """아카이브 서브그래프. facet_hits는 private(부모로 전파되지 않음)."""

    user_id: str
    raw_query: str
    conditions: TripConditions
    candidates: list[Candidate]
    current_itinerary: Itinerary | None
    # private
    facets: list[dict[str, Any]]
    facet_hits: Annotated[list[ArchiveHit], MERGE_BY_ID]
    # shared out
    archive_hits: Annotated[list[ArchiveHit], MERGE_BY_ID]
    edit_signals: Annotated[list[EditSignal], MERGE_BY_ID]
    taste_profile: TasteProfile | None
    evidence: Annotated[list[Evidence], MERGE_BY_ID]
    trace: Annotated[list[str], append_unique_str]


class DiscoveryState(TypedDict, total=False):
    user_id: str
    deadline: float
    conditions: TripConditions
    flags: PlanFlags
    archive_hits: list[ArchiveHit]
    # private
    raw_candidates: Annotated[list[Candidate], merge_candidates]
    verify_targets: list[dict[str, Any]]
    # shared out
    candidates: Annotated[list[Candidate], merge_candidates]
    verifications: Annotated[list[Verification], MERGE_BY_ID]
    place_diffs: Annotated[list[PlaceDiff], MERGE_BY_ID]
    evidence: Annotated[list[Evidence], MERGE_BY_ID]
    trace: Annotated[list[str], append_unique_str]


class ItineraryState(TypedDict, total=False):
    user_id: str
    deadline: float
    conditions: TripConditions
    flags: PlanFlags
    candidates: list[Candidate]
    archive_hits: list[ArchiveHit]
    taste_profile: TasteProfile | None
    current_itinerary: Itinerary | None
    decisions: list[Decision]
    replan_round: int
    # private
    context: Annotated[dict[str, Any], merge_dict]
    gap_queries: list[dict[str, Any]]
    # shared out
    itinerary: Itinerary | None
    gaps: Annotated[list[Gap], MERGE_BY_ID]
    nearby: Annotated[list[Candidate], merge_candidates]
    evidence: Annotated[list[Evidence], MERGE_BY_ID]
    trace: Annotated[list[str], append_unique_str]


class ValidationState(TypedDict, total=False):
    user_id: str
    conditions: TripConditions
    itinerary: Itinerary | None
    candidates: list[Candidate]
    archive_hits: list[ArchiveHit]
    place_diffs: list[PlaceDiff]
    context: dict[str, Any]
    # shared out — 입력으로 받지 않는다(부모의 누적분이 다시 섞여 나온다).
    issues: Annotated[list[Issue], replace_list]
    advisories: Annotated[list[Advisory], replace_list]
    needs_user_confirm: bool
    evidence: Annotated[list[Evidence], MERGE_BY_ID]
    trace: Annotated[list[str], append_unique_str]


# ------------------------------------------------------------- 서브그래프 출력
# 서브그래프를 노드로 붙이면 '입력으로 받은 키'까지 그대로 반환되어,
# 병렬 브랜치가 동시에 같은 LastValue 채널(user_id, conditions ...)에 쓰게 된다.
# 출력 스키마를 명시해 부모로 흘려보낼 키를 정확히 통제한다.
class ArchiveOutput(TypedDict, total=False):
    archive_hits: Annotated[list[ArchiveHit], MERGE_BY_ID]
    edit_signals: Annotated[list[EditSignal], MERGE_BY_ID]
    taste_profile: TasteProfile | None
    evidence: Annotated[list[Evidence], MERGE_BY_ID]
    trace: Annotated[list[str], append_unique_str]


class DiscoveryOutput(TypedDict, total=False):
    # 탐색 중에 좌표가 채워질 수 있다(지점 지오코딩 등). 이걸 내보내지 않으면
    # 부모 상태는 옛 conditions 를 그대로 들고 있어, 일정·지도가 해석 결과를
    # 영영 보지 못한다 — 서브그래프의 제자리 수정은 부모로 돌아오지 않는다.
    conditions: TripConditions
    candidates: Annotated[list[Candidate], merge_candidates]
    verifications: Annotated[list[Verification], MERGE_BY_ID]
    place_diffs: Annotated[list[PlaceDiff], MERGE_BY_ID]
    evidence: Annotated[list[Evidence], MERGE_BY_ID]
    trace: Annotated[list[str], append_unique_str]


class ItineraryOutput(TypedDict, total=False):
    itinerary: Itinerary | None
    gaps: Annotated[list[Gap], MERGE_BY_ID]
    nearby: Annotated[list[Candidate], merge_candidates]
    context: Annotated[dict[str, Any], merge_dict]
    evidence: Annotated[list[Evidence], MERGE_BY_ID]
    trace: Annotated[list[str], append_unique_str]


class ValidationOutput(TypedDict, total=False):
    issues: Annotated[list[Issue], replace_list]
    advisories: Annotated[list[Advisory], replace_list]
    needs_user_confirm: bool
    evidence: Annotated[list[Evidence], MERGE_BY_ID]
    trace: Annotated[list[str], append_unique_str]
