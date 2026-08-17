"""아카이브 서브그래프 — 개인화의 심장.

    START → plan_facets → ⟨Send 병렬⟩ facet_search ×N → fuse_rerank
          → extract_relevant → END

설계 의도
  · 하나의 질의를 3개 facet으로 분해해 병렬 검색한다. 각 facet은 회수 목적이 달라
    같은 임베딩 공간이라도 다른 이웃을 잡는다(유사장소 / 상황일치 / 불편·수정행동).
  · facet 결과는 RRF로 재융합한다. 여러 facet에 동시에 잡힌 기록 = 강한 신호.
  · 관련 경험은 archive_hits 의 meta(affects_plan·relevance_reason)에 표시만 한다.
    경고 카드 생성(build_advisories)은 제거했다 — 사용자 확인 카드는 validation 이
    단독으로 만든다. 두 곳에서 만들면 같은 사안이 카드 두 장으로 올라간다.
"""
from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field

from app.config import get_settings
from app.graph.state import ArchiveOutput, ArchiveState
from app.llm.prompts import FACET_PLANNER_SYSTEM
from app.llm.provider import structured
from app.memory import profile as profile_mod
from app.memory.retriever import FACETS, facet_search, fuse_facets, rerank
from app.schemas import ArchiveHit, Evidence

logger = logging.getLogger(__name__)


# --------------------------------------------------------------- LLM 출력 스키마
class FacetQueries(BaseModel):
    similar_place: list[str] = Field(default_factory=list)
    context_match: list[str] = Field(default_factory=list)
    friction_edit: list[str] = Field(default_factory=list)


class RelevantExperience(BaseModel):
    hit_id: str
    affects_plan: bool = Field(description="현재 일정/후보에 실제로 영향을 주는가")
    reason: str
    severity: int = Field(default=2, ge=1, le=3)


class RelevanceVerdict(BaseModel):
    items: list[RelevantExperience] = Field(default_factory=list)


# ------------------------------------------------------------------------ 노드
async def plan_facets(state: ArchiveState) -> dict:
    """검색 facet별 질의문 생성. LLM 실패 시 규칙 기반으로 폴백한다."""
    conditions = state.get("conditions")
    candidates = state.get("candidates") or []
    place_names = ", ".join(c.name for c in candidates[:8]) or "(후보 없음)"
    ctx = conditions.model_dump_json() if conditions else "{}"

    queries = _fallback_queries(state)
    try:
        chain = structured("fast", FacetQueries)
        queries = await chain.ainvoke([
            {"role": "system", "content": FACET_PLANNER_SYSTEM},
            {"role": "user", "content":
                f"사용자 발화: {state.get('raw_query', '')}\n"
                f"조건: {ctx}\n후보 장소: {place_names}"},
        ])
    except Exception as exc:
        logger.warning("plan_facets fallback: %s", exc)

    facets = [
        {"facet": f, "query": q}
        for f in FACETS
        for q in (getattr(queries, f, None) or [])[:2]
    ] or _fallback_facets(state)
    return {"facets": facets, "trace": [f"archive.plan_facets:{len(facets)}"]}


def dispatch_facets(state: ArchiveState) -> list[Send]:
    """Send API 팬아웃. facet 개수를 사전에 알 수 없으므로 동적 라우팅."""
    place_ids = [c.place_id for c in (state.get("candidates") or []) if c.place_id]
    return [
        Send("facet_search", {
            "facet": f["facet"],
            "query": f["query"],
            "user_id": state.get("user_id", ""),
            "conditions": state.get("conditions"),
            "place_ids": place_ids or None,
        })
        for f in state.get("facets", [])
    ]


async def facet_search_node(payload: dict) -> dict:
    """워커 노드. Send 페이로드를 그대로 입력으로 받는다."""
    hits = await facet_search(
        user_id=payload["user_id"],
        facet=payload["facet"],
        query=payload["query"],
        conditions=payload.get("conditions"),
        place_ids=payload.get("place_ids"),
    )
    return {"facet_hits": hits}


async def fuse_rerank(state: ArchiveState) -> dict:
    """facet별 결과 재융합 → cross-encoder 리랭크 → 프로필 로드."""
    s = get_settings()
    hits: list[ArchiveHit] = state.get("facet_hits") or []
    groups: dict[str, list[ArchiveHit]] = {}
    for h in hits:
        groups.setdefault(h.facet, []).append(h)

    fused = fuse_facets(list(groups.values()), final_k=s.archive_final_k * 2)
    query = state.get("raw_query") or ""
    fused = (await rerank(query, fused))[: s.archive_final_k]

    profile = await profile_mod.load_profile(state.get("user_id", ""))
    evidence = [
        Evidence(kind="archive", title=h.place_name or h.source_type,
                 text=h.summary, ref=h.id, observed_at=h.occurred_at,
                 confidence=min(1.0, 0.4 + h.final_score))
        for h in fused
    ]
    return {
        "archive_hits": fused,
        "taste_profile": profile,
        "evidence": evidence,
        "trace": [f"archive.fuse:{len(fused)}/{len(hits)}"],
    }


async def extract_relevant(state: ArchiveState) -> dict:
    """'검색됐다'와 '알려야 한다'를 구분하는 게이트."""
    hits = state.get("archive_hits") or []
    if not hits:
        return {"trace": ["archive.extract:none"]}

    rendered = "\n".join(
        f"[{h.id}] ({h.source_type}/{h.facet}) {h.place_name or ''} :: {h.summary} "
        f"friction={h.friction} sentiment={h.sentiment}"
        for h in hits
    )
    conditions = state.get("conditions")
    verdict = RelevanceVerdict(items=[
        RelevantExperience(hit_id=h.id, affects_plan=bool(h.friction) or h.sentiment < -0.3,
                           reason="불편 기록 또는 부정 경험", severity=2 if h.friction else 1)
        for h in hits
    ])
    try:
        chain = structured("planner", RelevanceVerdict)
        verdict = await chain.ainvoke([
            {"role": "system",
             "content": "과거 경험 중 현재 일정에 실제로 영향을 주는 것만 affects_plan=true 로 표시하라. "
                        "단순 취향 일치는 false. 재방문 변경사항·불편·시간 충돌 가능성만 true."},
            {"role": "user",
             "content": f"현재 조건: {conditions.model_dump_json() if conditions else '{}'}\n\n{rendered}"},
        ])
    except Exception as exc:
        logger.warning("extract_relevant fallback: %s", exc)

    flagged = {v.hit_id: v for v in verdict.items if v.affects_plan}
    for h in hits:
        if h.id in flagged:
            h.meta["affects_plan"] = True
            h.meta["relevance_reason"] = flagged[h.id].reason
            h.meta["severity"] = flagged[h.id].severity
    return {"archive_hits": hits, "trace": [f"archive.extract:{len(flagged)}"]}


# ---------------------------------------------------------------------- 헬퍼
def _fallback_queries(state: ArchiveState) -> FacetQueries:
    c = state.get("conditions")
    region = (c.region if c else None) or "이 지역"
    return FacetQueries(
        similar_place=[f"{region}에서 방문했던 문화공간 경험"],
        context_match=[f"{(c.companions if c else '')} 동행 {(c.transport if c else '')} 이동 경험"],
        friction_edit=["주차 혼잡 접근성 대기시간이 불편했던 기록"],
    )


def _fallback_facets(state: ArchiveState) -> list[dict]:
    q = _fallback_queries(state)
    return [{"facet": f, "query": getattr(q, f)[0]} for f in FACETS]


# ------------------------------------------------------------------ 그래프 조립
def build_archive_graph():
    g = StateGraph(ArchiveState, output_schema=ArchiveOutput)
    g.add_node("plan_facets", plan_facets)
    g.add_node("facet_search", facet_search_node)
    g.add_node("fuse_rerank", fuse_rerank)
    g.add_node("extract_relevant", extract_relevant)

    g.add_edge(START, "plan_facets")
    g.add_conditional_edges("plan_facets", dispatch_facets, ["facet_search"])
    g.add_edge("facet_search", "fuse_rerank")
    g.add_edge("fuse_rerank", "extract_relevant")
    g.add_edge("extract_relevant", END)
    return g.compile(checkpointer=False)
