"""일정 생성·주변 추천 서브그래프 (문서 Flowchart 04).

    START → ⟨병렬⟩ geo / hours / weather / preference
          → assemble_constraints → schedule(순서·시각 배치)
          → detect_gaps → ⟨Send 병렬⟩ nearby ×N → rerank_nearby → fill_gaps → END

이동시간은 반드시 지도 API 행렬에서 온 값을 쓴다. LLM이 이동시간을 지어내면
'실행 가능한 일정'이라는 전제가 무너지기 때문에, 스케줄링은 결정론적 코드가 맡고
LLM은 배치 이유(reason) 서술과 취향 정렬만 담당한다.

---

**어디를 고칠 것인가** — 한 파일 1,321줄이던 것을 2026-08-17 에 나눴다.
동작은 바뀌지 않았다(코드를 줄 단위로 그대로 옮겼다).

| 증상 | 파일 |
|---|---|
| 실내/야외·날씨·취향 반영이 이상하다 | `context.py` |
| 순서·시각이 이상하다 · 개수가 안 맞는다 | `schedule.py` |
| «이 자리에 무엇을 넣을까» 판단 · 종류별 몫 | `placement.py` |
| 체류시간이 이상하다 | `dwell.py` |
| 출발·도착 안내 문장이 틀렸다 | `notes.py` |
| 이동시간·경로 선형·수단 선택 | `legs.py` |
| 빈 시간이 안 채워진다 · 카페가 안 들어온다 | `gaps.py` |
| `/routes`·`/reroute`·큐레이션 동선 | `routes.py` |

이 파일은 **조립과 공개 이름**만 갖는다. 노드 함수를 여기 두면 다시 한 덩어리가 된다.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.graph.state import ItineraryOutput, ItineraryState
from app.graph.subgraphs.itinerary.context import (
    assemble_constraints,
    ctx_geo,
    ctx_hours,
    ctx_preference,
    ctx_weather,
)
from app.graph.subgraphs.itinerary.dwell import _apply_dwell
from app.graph.subgraphs.itinerary.gaps import (
    detect_gaps,
    dispatch_nearby,
    fill_gaps,
    nearby_search,
    rerank_nearby,
)
from app.graph.subgraphs.itinerary.legs import (
    BEST_CANDIDATE_MODES,
    TRANSPORT_KO,
    _best_leg,
    _fastest_leg,
    _measure_legs,
    _mix,
    summarize_transport,
)
from app.graph.subgraphs.itinerary.notes import endpoint_notes
from app.graph.subgraphs.itinerary.placement import _match_stop
from app.graph.subgraphs.itinerary.routes import (
    measure_routes,
    reroute_itinerary,
    route_places,
)
from app.graph.subgraphs.itinerary.schedule import _reflow, schedule

# 밖에서 부르는 이름. 밑줄로 시작하는 것들은 회귀 테스트가 단위로 쥐고 있는
# 판단들이다 — 이름이 사적이라는 뜻이지, 검증 대상이 아니라는 뜻은 아니다.
__all__ = [
    "BEST_CANDIDATE_MODES",
    "TRANSPORT_KO",
    "_apply_dwell",
    "_best_leg",
    "_fastest_leg",
    "_match_stop",
    "_measure_legs",
    "_mix",
    "_reflow",
    "assemble_constraints",
    "build_itinerary_graph",
    "ctx_geo",
    "ctx_hours",
    "ctx_preference",
    "ctx_weather",
    "detect_gaps",
    "dispatch_nearby",
    "endpoint_notes",
    "fill_gaps",
    "measure_routes",
    "nearby_search",
    "rerank_nearby",
    "reroute_itinerary",
    "route_places",
    "schedule",
    "summarize_transport",
]


def build_itinerary_graph():
    g = StateGraph(ItineraryState, output_schema=ItineraryOutput)
    for name, fn in (("ctx_geo", ctx_geo), ("ctx_hours", ctx_hours),
                     ("ctx_weather", ctx_weather), ("ctx_preference", ctx_preference)):
        g.add_node(name, fn)
        g.add_edge(START, name)
        g.add_edge(name, "assemble_constraints")

    g.add_node("assemble_constraints", assemble_constraints)
    g.add_node("schedule", schedule)
    g.add_node("detect_gaps", detect_gaps)
    g.add_node("nearby_search", nearby_search)
    g.add_node("rerank_nearby", rerank_nearby)
    g.add_node("fill_gaps", fill_gaps)

    g.add_edge("assemble_constraints", "schedule")
    g.add_edge("schedule", "detect_gaps")
    g.add_conditional_edges("detect_gaps", dispatch_nearby,
                            ["nearby_search", "fill_gaps"])
    g.add_edge("nearby_search", "rerank_nearby")
    g.add_edge("rerank_nearby", "fill_gaps")
    g.add_edge("fill_gaps", END)
    return g.compile(checkpointer=False)
