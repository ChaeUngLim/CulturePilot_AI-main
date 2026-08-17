"""메인 그래프 조립.

                          ┌── archive ─────┐
   START → classify ──────┼── discovery ────┼── merge_context ──┬── itinerary → validation
                          └── current_plan ─┘                   │        ↑           │
                                                                │        └── hitl ◄──┘
                                                                ▼             │
                                                          compose → END        └→ finalize → persist → compose

조건부 팬아웃(classify → …)이 '필요한 Agent만 실행'을,
hitl → itinerary 역방향 엣지가 '사용자 선택 반영 후 재계획'을 만든다.
"""
from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from app.graph import nodes, router
from app.graph.serde import build_serde
from app.graph.state import CultureMateState
from app.graph.subgraphs.archive import build_archive_graph
from app.graph.subgraphs.discovery import build_discovery_graph
from app.graph.subgraphs.itinerary import build_itinerary_graph
from app.graph.subgraphs.validation import build_validation_graph

logger = logging.getLogger(__name__)


def needs_confirm(state: CultureMateState) -> str:
    """확인 카드가 실제로 있을 때만 사용자에게 묻는다.

    needs_user_confirm 만 보고 분기하면, 이슈는 있었지만 카드가 만들어지지 않은 경우
    '0/0 선택됨'인 빈 확인 화면이 뜬다. 사용자는 아무것도 할 수 없고 일정도 안 나온다.
    """
    if not state.get("needs_user_confirm"):
        return "finalize"
    return "hitl" if (state.get("advisories") or []) else "finalize"


def build_graph(checkpointer=None, store=None):
    g = StateGraph(CultureMateState)

    # --- 진입 ---
    g.add_node("classify", router.classify)

    # --- 서브그래프(공유 State 키로 직접 부착 → 체크포인트/스트리밍 유지) ---
    g.add_node("archive", build_archive_graph())
    g.add_node("discovery", build_discovery_graph())
    g.add_node("itinerary", build_itinerary_graph())
    g.add_node("validation", build_validation_graph())

    # --- 조율 노드 ---
    g.add_node("current_plan", nodes.load_current_plan)
    g.add_node("merge_context", nodes.merge_context)
    g.add_node("hitl", nodes.human_review)
    g.add_node("finalize", nodes.finalize)
    g.add_node("persist", nodes.persist)
    g.add_node("compose", nodes.compose)

    # --- 엣지 ---
    g.add_edge(START, "classify")
    g.add_conditional_edges(
        "classify", router.fan_out,
        ["archive", "discovery", "current_plan"],
    )
    for n in ("archive", "discovery", "current_plan"):
        g.add_edge(n, "merge_context")

    g.add_conditional_edges("merge_context", router.need_itinerary,
                            ["itinerary", "compose"])
    g.add_edge("itinerary", "validation")
    g.add_conditional_edges("validation", needs_confirm, ["hitl", "finalize"])
    g.add_conditional_edges("hitl", nodes.after_review, ["itinerary", "finalize"])
    g.add_edge("finalize", "persist")
    g.add_edge("persist", "compose")
    g.add_edge("compose", END)

    return g.compile(checkpointer=checkpointer, store=store)


class _PoolCM:
    """lifespan 이 그대로 닫을 수 있게 커넥션 풀을 컨텍스트 매니저 모양으로 감싼다."""

    def __init__(self, pool):
        self._pool = pool

    async def __aexit__(self, *_):
        await self._pool.close()


async def build_graph_with_postgres():
    """운영용: PostgreSQL 체크포인터.

    Postgres에 붙지 못하면 InMemorySaver로 떨어진다. 로컬 개발에서 DB 없이도
    앱을 띄울 수 있게 하되, 프로세스가 죽으면 진행 중인 HITL 스레드가 사라진다는
    점을 로그로 분명히 남긴다. 운영에서는 반드시 Postgres여야 한다.
    """
    from app.config import get_settings

    dsn = get_settings().checkpoint_dsn or get_settings().pg_dsn
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        # 단일 연결(from_conn_string)을 앱 수명 내내 붙들면, 그 연결이 한 번
        # 끊기는 순간 이후 모든 요청이 'the connection is closed' 로 죽고
        # 영영 회복하지 않는다. postgres 를 재시작하거나 유휴 연결이 끊기면
        # /chat 은 되는데 /routes·/verify 만 500 이 나는 식으로 나타난다.
        # 풀은 끊긴 연결을 버리고 새로 열므로 스스로 복구한다.
        pool = AsyncConnectionPool(
            conninfo=dsn, min_size=1, max_size=10, open=False,
            # 빌려줄 때마다 연결이 살아 있는지 확인한다. 이게 없으면 postgres 가
            # 죽을 때 끊긴 연결이 풀에 그대로 남아 다시 배급되고,
            # 'terminating connection due to administrator command' 로 실패한다.
            # 풀을 쓰는 이유가 자가 복구인데, 검사 없이는 복구되지 않는다.
            check=AsyncConnectionPool.check_connection,
            # 체크포인터는 파이프라인을 쓰지 않는다. autocommit 이 아니면
            # setup() 의 DDL 이 열린 트랜잭션에 갇힌다.
            kwargs={"autocommit": True, "row_factory": dict_row},
        )
        await pool.open()
        saver = AsyncPostgresSaver(pool, serde=build_serde())
        await saver.setup()
        return build_graph(checkpointer=saver), _PoolCM(pool)
    except Exception as exc:
        logger.warning(
            "Postgres 체크포인터 연결 실패(%s) → InMemorySaver로 폴백합니다. "
            "재시작 시 진행 중인 대화가 유실됩니다.", exc,
        )
        from langgraph.checkpoint.memory import InMemorySaver

        class _NullCM:
            async def __aexit__(self, *_):
                return None

        return build_graph(checkpointer=InMemorySaver(serde=build_serde())), _NullCM()


# 개발/테스트용 기본 그래프(체크포인터 없음 → interrupt 사용 불가)
graph = build_graph()
