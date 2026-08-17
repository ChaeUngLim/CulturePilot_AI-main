"""그래프 구조 회귀 테스트. 외부 의존성 없이 컴파일과 라우팅만 검증한다."""
from __future__ import annotations

import os

os.environ.setdefault("LLM_BACKEND", "fake")

from app.graph.build import build_graph
from app.graph.router import ROUTE_TABLE, fan_out
from app.graph.subgraphs.archive import build_archive_graph
from app.schemas import PlanFlags, RequestType


def test_main_graph_compiles():
    g = build_graph()
    nodes = set(g.get_graph().nodes)
    for expected in ("classify", "archive", "discovery", "itinerary",
                     "validation", "hitl", "finalize", "persist", "compose"):
        assert expected in nodes


def test_every_request_type_has_route():
    for rt in RequestType:
        assert rt in ROUTE_TABLE


def test_fan_out_selects_minimal_agents():
    assert fan_out({"flags": ROUTE_TABLE[RequestType.ARCHIVE_QUERY]}) == ["archive"]
    assert set(fan_out({"flags": ROUTE_TABLE[RequestType.PLAN_CREATE]})) == {
        "archive", "discovery"}
    assert fan_out({"flags": PlanFlags()}) == ["archive"]


def test_archive_subgraph_compiles():
    nodes = set(build_archive_graph().get_graph().nodes)
    assert {"plan_facets", "facet_search", "fuse_rerank"} <= nodes


def test_conditions_override_takes_precedence():
    """GPS 등 네이티브 값이 LLM 추출값을 덮어쓰는지 (React Native 클라이언트 계약)."""
    from app.graph.router import _apply_override
    from app.schemas import GeoPoint, TripConditions

    base = TripConditions(region="성수동", transport="subway")
    merged = _apply_override(base, {"origin": {"lat": 37.5445, "lng": 127.0557},
                                    "transport": "car"})
    assert merged.origin == GeoPoint(lat=37.5445, lng=127.0557)
    assert merged.transport == "car"
    assert merged.region == "성수동"                 # 덮어쓰지 않은 값은 보존
    assert _apply_override(base, None) is base
    assert _apply_override(base, {"transport": "무효값"}).transport == "subway"  # 검증 실패 시 무시
