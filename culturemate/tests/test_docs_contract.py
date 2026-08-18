"""문서와 소스가 어긋나지 않게 못을 박는다.

이 저장소에서 문서가 틀린 자리는 거의 전부 같은 유형이었다 — **코드가 앞서 나가고
문서가 안 따라온 것.** `search_catalog` 를 추가하고, ODsay·ORS 를 연동하고,
큐레이션 탭을 붙이고, 테스트가 32개에서 103개로 늘어나는 동안 문서는 그대로였다.
마크다운을 사람이 지키게 하면 다음에도 같은 일이 생긴다.

그래서 **셀 수 있는 주장만** 고정한다. 산문을 파싱하면 문장을 조금만 다듬어도
테스트가 깨지고, 그러면 결국 이 파일을 지우게 된다. 여기서 검사하는 것은
구조(노드·라우트·라우팅 테이블)와, 문서에 반드시 등장해야 하는 이름 몇 개다.

새 노드나 엔드포인트를 더하면 이 테스트가 먼저 깨진다. 그때 문서를 같이 고치면 된다.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def _doc(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


# --------------------------------------------------------------- 그래프 구조
def _nodes(compiled) -> set[str]:
    """컴파일된 그래프의 실제 노드 이름(진입점 제외)."""
    return {n for n in compiled.nodes if not n.startswith("__")}


def test_main_graph_nodes():
    """메인 그래프 11노드 = 서브그래프 4 + 조율 7 (ARCHITECTURE §3, REQUIREMENTS §8).

    `report` 서브그래프는 삭제됐다. 문서가 오래 12노드·5서브그래프로 남아 있었고
    2026-08-17 에 정정했다 — 이 테스트는 노드 **집합**만 보므로 문서의 숫자 문장은
    지켜 주지 않는다(REQUIREMENTS §8 의 경고). 숫자를 고칠 때는 `build.py` 의
    `add_node` 호출을 직접 센다.
    """
    from app.graph.build import build_graph

    nodes = _nodes(build_graph())
    subgraphs = {"archive", "discovery", "itinerary", "validation"}
    coordination = {"classify", "current_plan", "merge_context",
                    "hitl", "finalize", "persist", "compose"}
    assert nodes == subgraphs | coordination, f"메인 그래프 노드가 바뀌었다: {sorted(nodes)}"


def test_discovery_searches_four_sources():
    """탐색은 4갈래 병렬이다 (ARCHITECTURE §7.1, STRUCTURE §3.2, REQUIREMENTS §1.1②).

    예전에 문서가 3갈래로만 그려 놓아 `search_catalog` 가 통째로 빠져 있었다.
    카탈로그는 외부 API가 전부 실패해도 결과가 0건이 되지 않게 바닥을 받치는 레인이라,
    빠뜨리면 '왜 키 없이도 결과가 나오는가'를 설명할 수 없다.
    """
    from app.graph.subgraphs.discovery import build_discovery_graph

    searches = {n for n in _nodes(build_discovery_graph()) if n.startswith("search_")}
    assert searches == {"search_catalog", "search_events",
                        "search_always_on", "search_web"}, searches

    for name in ("ARCHITECTURE.md", "STRUCTURE.md", "REQUIREMENTS.md"):
        assert "search_catalog" in _doc(name), f"{name} 에 search_catalog 가 없다"


def test_validation_runs_six_checks():
    """검증은 6종 병렬이다 (ARCHITECTURE §8.1, STRUCTURE §6).

    과거 UR-12(check_archive)를 제거하면서 5종이 됐고, 그 결과 **과거 불편을
    경고로 승격하는 노드가 아무것도 없게** 됐다. `Issue.kind="past_friction"` 과
    카드 선택지는 남아 있는데 그것을 만드는 쪽이 사라져, 기획안 2.1-②(선제적
    알림)가 코드상 도달 불가능했다. 2026-08-17 에 `check_friction` 으로 되살렸다(UR-40).

    사용자 확인 카드는 여전히 validation **한 곳에서만** 만든다 — archive 가 아니라
    여기에 둔 이유가 그것이다. 두 곳에서 만들면 같은 사안이 카드 두 장으로 올라간다.
    """
    from app.graph.subgraphs.validation import build_validation_graph

    checks = {n for n in _nodes(build_validation_graph()) if n.startswith("check_")}
    assert checks == {"check_hours", "check_travel", "check_overlap",
                      "check_weather", "check_revisit", "check_friction"}, checks


# ------------------------------------------------------------- 라우팅 테이블
def test_route_table_covers_every_request_type():
    """8종 라우트가 전부 표에 있다 (ARCHITECTURE §5)."""
    from app.graph.router import ROUTE_TABLE
    from app.schemas import RequestType

    assert set(ROUTE_TABLE) == set(RequestType), "라우팅 테이블에 빠진 RequestType이 있다"


def test_place_recommend_builds_an_itinerary():
    """추천도 일정을 만든다 (ARCHITECTURE §5, REQUIREMENTS FR-13).

    문서 표에 이 ● 하나가 빠져 있었다. 장소만 나열하면 지도·동선이 비어
    화면이 성립하지 않는다.
    """
    from app.graph.router import ROUTE_TABLE
    from app.schemas import RequestType

    assert ROUTE_TABLE[RequestType.PLACE_RECOMMEND].build_itinerary


# ------------------------------------------------------------------- HITL
@pytest.mark.parametrize(
    ("auto_fixable", "severity", "expected"),
    [
        (False, 3, True),    # 못 고치고 심각 → 물어본다
        (False, 1, False),   # 못 고치지만 사소 → 안 물어본다
        (True, 3, False),    # 심각하지만 자동으로 고칠 수 있다 → 안 물어본다
        (True, 1, False),
    ],
)
async def test_hitl_requires_both_conditions(auto_fixable, severity, expected):
    """`auto_fixable=False` **이면서** `severity >= 2` 인 이슈만 올라간다 — AND다.

    validation.py 의 docstring이 한동안 이걸 OR로 적어 두었고, 그 문장이
    STRUCTURE.md 로 옮겨 가 문서 두 곳이 함께 틀렸다. 조건을 말로 적는 대신
    네 조합을 전부 돌려 고정한다.
    """
    from app.graph.subgraphs.validation import triage
    from app.schemas import Issue

    issue = Issue(kind="closed", severity=severity, auto_fixable=auto_fixable,
                  detail="테스트")
    out = await triage({"issues": [issue]})
    assert out["needs_user_confirm"] is expected


# ------------------------------------------------------------------ API 표면
EXPECTED_ROUTES = {
    "/chat", "/chat/sync", "/resume", "/resume/sync",
    "/threads/{thread_id}/state", "/threads/{thread_id}/evidence/{evidence_id}",
    "/threads/{thread_id}/routes", "/threads/{thread_id}/verify",
    "/reroute", "/visits", "/report/{user_id}",
    # UR-28 캘린더 — 목록은 요약만, 상세는 따로. 경로가 `/plans/detail/{id}` 인 이유는
    # `/plans/{user_id}` 와 한 자리에서 부딪히지 않게 하기 위해서다.
    "/plans/{user_id}", "/plans/detail/{plan_id}",
    # UR-01 취향 카드 — 저장은 **묶음**으로 받는다. 스와이프는 초당 한 장씩 넘어가고,
    # 장마다 왕복하면 어디까지 저장됐는지 사용자가 알 수 없다.
    "/preferences/cards", "/preferences/cards/{user_id}",
    "/collections", "/collections/{collection_id}",
    "/collections/{collection_id}/places/{place_id}",
    "/curations/{user_id}", "/geocode", "/whereami", "/health", "/diagnostics",
}


def test_api_surface_is_documented():
    """엔드포인트 23개 (STRUCTURE §1, FUNCTIONAL_MAP §5, REQUIREMENTS §8)."""
    from fastapi.routing import APIRoute

    from app.api.main import app

    actual = {r.path for r in app.routes if isinstance(r, APIRoute)}
    assert actual == EXPECTED_ROUTES, (
        f"추가됨: {sorted(actual - EXPECTED_ROUTES)} / "
        f"사라짐: {sorted(EXPECTED_ROUTES - actual)}")


# ------------------------------------------------------- 외부 의존(무료 전제)
def test_routing_providers_are_documented():
    """경로 API 3종이 문서에 이름으로 남아 있어야 한다 (STRUCTURE §12).

    ODsay·ORS 가 연동된 뒤에도 ARCHITECTURE.md 로드맵은 '필요해지면 나중에 추가한다'고
    적혀 있었다. 핵심 기능(무료 실측)이 아직 없는 것처럼 읽히는 상태였다.
    """
    from app.tools import routing

    assert routing.ODSAY_URL and routing.ORS_URL
    structure = _doc("STRUCTURE.md")
    for provider in ("ODsay", "OpenRouteService", "NAVER"):
        assert provider in structure, f"STRUCTURE.md 에 {provider} 가 없다"


def test_documented_line_counts_are_current():
    """STRUCTURE.md 가 '★ N줄'로 표시한 파일의 줄 수가 실제와 맞는지.

    큰 모듈을 가리키는 이정표라서 크게 어긋나면 '어디가 무거운지'라는 정보가
    거짓이 된다. 한 줄만 고쳐도 깨지면 성가시므로 ±30줄까지는 봐준다.
    """
    text = _doc("STRUCTURE.md")
    marked = re.findall(r"(\w+\.py)\s+.*?★\s*([\d,]+)줄", text)
    assert marked, "STRUCTURE.md 에서 '★ N줄' 표기를 찾지 못했다"

    src = ROOT / "app" / "graph"
    for name, claimed in marked:
        hits = list(src.rglob(name))
        assert hits, f"{name} 을 찾을 수 없다"
        actual = len(hits[0].read_text(encoding="utf-8").splitlines())
        want = int(claimed.replace(",", ""))
        assert abs(actual - want) <= 30, (
            f"{name}: 문서 {want}줄 · 실제 {actual}줄")


def test_every_domain_model_is_serializable():
    """schemas.py 의 모든 Pydantic 모델이 체크포인트 화이트리스트에 있다 (STRUCTURE §14).

    빠뜨리면 조용히 지나갔다가, 그 모델이 실제로 State 에 실리는 발화에서만
    `Type is not msgpack serializable` 로 일정 생성이 통째로 죽는다.
    실제로 `StopRequest`(TripConditions.stops)가 빠져 있었고, 시각·식사가 섞인
    요청에서만 재현돼 찾기 어려웠다. 모델을 더할 때 여기서 먼저 걸리게 한다.
    """
    import inspect

    from pydantic import BaseModel

    from app import schemas
    from app.graph.serde import ALLOWED_TYPES

    models = {
        name for name, obj in vars(schemas).items()
        if inspect.isclass(obj) and issubclass(obj, BaseModel) and obj is not BaseModel
    }
    missing = models - {t.__name__ for t in ALLOWED_TYPES}
    assert not missing, f"ALLOWED_TYPES 에 빠진 모델: {sorted(missing)}"


def test_docs_describe_only_wired_routing_providers():
    """문서가 실제로 붙어 있는 제공자만 이야기한다.

    쓰지 않는 제공자를 문서가 언급하면 두 가지가 나빠진다 — 읽는 사람은 없는 연동을
    있다고 믿고, '나중에 추가한다'는 문장은 이미 붙어 있는 기능을 없는 것처럼 보이게 한다.
    실제로 ODsay·ORS가 연동된 뒤에도 로드맵이 '필요해지면 추가'로 남아 있었다.
    """
    from tests.conftest import NON_FREE_ROUTING_PROVIDERS

    for name in ("ARCHITECTURE.md", "STRUCTURE.md", "REQUIREMENTS.md"):
        text = _doc(name).lower()
        for provider in NON_FREE_ROUTING_PROVIDERS:
            assert provider not in text, f"{name} 이 쓰지 않는 제공자를 언급한다: {provider}"
