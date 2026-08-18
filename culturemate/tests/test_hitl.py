"""Human-in-the-loop 분기 로직 테스트."""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("LLM_BACKEND", "fake")

from app.graph.nodes import after_review, finalize
from app.graph.subgraphs.validation import build_confirm_cards, triage
from app.schemas import (
    Advisory,
    Decision,
    Issue,
    Itinerary,
    ItineraryItem,
    Option,
)


def _advisory(action: str) -> Advisory:
    return Advisory(kind="friction", title="t", message="m",
                    options=[Option(label="유지", action="keep"),
                             Option(label="변경", action=action)])


async def test_triage_only_escalates_non_auto_fixable():
    state = {"issues": [
        Issue(kind="overlap", severity=2, auto_fixable=True),
        Issue(kind="past_friction", severity=2, auto_fixable=False),
    ]}
    out = await triage(state)
    assert out["needs_user_confirm"] is True

    out2 = await triage({"issues": [Issue(kind="overlap", severity=2, auto_fixable=True)]})
    assert out2["needs_user_confirm"] is False


async def test_cards_carry_options_and_evidence():
    state = {
        "itinerary": Itinerary(items=[ItineraryItem(seq=1, name="A", place_id="p1")]),
        "issues": [Issue(kind="past_friction", severity=2, target_seq=1,
                         place_id="p1", auto_fixable=False, evidence_ids=["e1"])],
    }
    out = await build_confirm_cards(state)
    card = out["advisories"][0]
    assert card.kind == "friction"
    assert {o.action for o in card.options} >= {"keep", "add_parking", "replace"}
    assert card.evidence_ids == ["e1"]              # 판단 근거가 카드에 붙어 나간다


def test_after_review_replans_only_when_needed():
    keep_adv = _advisory("replace")
    state = {"advisories": [keep_adv], "replan_round": 1,
             "decisions": [Decision(advisory_id=keep_adv.id,
                                    option_id=keep_adv.options[0].id)]}
    assert after_review(state) == "finalize"        # '유지' 선택 → 재계획 불필요

    state["decisions"] = [Decision(advisory_id=keep_adv.id,
                                   option_id=keep_adv.options[1].id)]
    assert after_review(state) == "itinerary"       # '교체' 선택 → 재계획

    state["replan_round"] = 99
    assert after_review(state) == "finalize"        # 무한 루프 방지


async def test_finalize_applies_drop_decision():
    adv = Advisory(kind="conflict", title="t", message="m",
                   options=[Option(label="제외", action="drop", payload={"place_id": "p2"})])
    it = Itinerary(items=[ItineraryItem(seq=1, name="A", place_id="p1"),
                          ItineraryItem(seq=2, name="B", place_id="p2")])
    out = await finalize({"itinerary": it, "advisories": [adv],
                          "decisions": [Decision(advisory_id=adv.id,
                                                 option_id=adv.options[0].id)]})
    assert [i.place_id for i in out["itinerary"].items] == ["p1"]
    assert out["itinerary"].items[0].seq == 1


@pytest.mark.skipif(sys.version_info < (3, 11),
                    reason="LangGraph async interrupt는 Python 3.11+ 컨텍스트 전파 필요")
async def test_interrupt_roundtrip():
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    from app.graph.build import build_graph

    g = build_graph(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "hitl-test"}}
    state = await g.ainvoke({"user_id": "u1", "raw_query": "일정 짜줘"}, config=cfg)
    if "__interrupt__" not in state:
        pytest.skip("이 입력에서는 확인이 필요한 이슈가 발생하지 않음")
    payload = state["__interrupt__"][0].value
    decisions = [{"advisory_id": a["id"], "option_id": a["options"][0]["id"]}
                 for a in payload["advisories"]]
    resumed = await g.ainvoke(Command(resume={"decisions": decisions}), config=cfg)
    assert len(resumed["decisions"]) == len(decisions)


def test_confirm_prompt_has_no_internal_jargon():
    """확인 문구는 대화에 그대로 뜬다. 내부 용어가 새면 안 된다.

    'advisory마다 option_id를 하나씩 선택해 주세요' 가 채팅에 노출됐던 적이 있다.
    사용자는 자기가 무엇을 해야 하는지가 아니라 시스템이 무엇을 원하는지를 읽게 된다.
    """
    from app.graph.nodes import _confirm_prompt
    from app.schemas import Advisory, Itinerary, ItineraryItem

    it = Itinerary(items=[ItineraryItem(seq=i, name=f"장소{i}") for i in range(1, 8)])
    pending = [Advisory(id=f"a{i}", kind="friction", title="t", message="m")
               for i in (1, 2)]

    text = _confirm_prompt(it, pending)
    assert "7곳" in text and "2개" in text
    for jargon in ("advisory", "option_id", "payload", "schema"):
        assert jargon not in text.lower(), text

    one = _confirm_prompt(it, pending[:1])
    assert "하나" in one and "option" not in one.lower()


def test_confirm_cards_are_stable_across_replans():
    """재계획을 돌아도 같은 문제는 카드 한 장이어야 한다.

    Issue.id 가 검사마다 새로 생성돼서, 카드 id 를 거기서 따오면 MERGE_BY_ID 가
    중복을 못 걸러냈다. 이슈 6건이 카드 21장이 되고 제목/내용이 어긋났다.
    """
    import asyncio

    from app.graph.subgraphs.validation import build_confirm_cards
    from app.schemas import Issue, Itinerary, ItineraryItem

    def issue():                       # 같은 문제를 두 번 검사한 상황
        return Issue(kind="hours_conflict", severity=2, target_seq=3,
                     place_name="예술의전당", place_id="p1", auto_fixable=False,
                     detail="예술의전당의 운영정보가 확인되지 않았습니다.")

    it = Itinerary(items=[ItineraryItem(seq=3, name="다른 장소", place_id="p9")])
    out = asyncio.run(build_confirm_cards({"itinerary": it,
                                           "issues": [issue(), issue()]}))
    advs = out["advisories"]
    assert len({a.id for a in advs}) == 1, [a.id for a in advs]

    # 제목은 seq 로 다시 찾지 않고 이슈에 박힌 이름을 쓴다
    assert advs[0].title.startswith("예술의전당"), advs[0].title
    assert "예술의전당" in advs[0].message


def test_validation_results_replace_instead_of_accumulating():
    """재계획을 돌아도 검증 결과는 '이번 라운드의 것'이어야 한다.

    누적하면 이미 해결된 이슈와 일정에서 빠진 장소의 카드가 영원히 남는다.
    실제로 이슈 6건이 카드 25장이 되고, 이름이 사라진 '일정 확인 필요' 카드가
    10장 떠 있었다.
    """
    from app.graph.reducers import replace_list
    from app.schemas import Advisory

    def card(cid: str) -> Advisory:
        return Advisory(id=cid, kind="conflict", title="t", message="m")

    old = [card("a"), card("b"), card("c")]
    assert [x.id for x in replace_list(old, [card("d")])] == ["d"]

    # 검증을 타지 않은 경로가 지나갈 때 기존 카드를 지우면 안 된다
    assert [x.id for x in replace_list(old, [])] == ["a", "b", "c"]
    assert [x.id for x in replace_list(old, None)] == ["a", "b", "c"]
    assert replace_list(None, None) == []


def test_interrupt_payload_carries_resolved_conditions():
    """확인 카드가 떠도 화면 상단 조건 칩이 갱신돼야 한다.

    예전에는 resolved 가 done 응답에만 실려서, "판교역에서 7시 출발"이라고 말해도
    카드가 뜨는 순간 칩이 이전 값에 멈췄다. 일정은 정상인데 화면만 어긋났다.
    """
    import asyncio
    from datetime import time

    from app.graph.nodes import human_review
    from app.schemas import Advisory, TripConditions, resolved_view

    c = TripConditions(origin_name="판교역", destination_name="청계산역",
                       start_time=time(7, 0), end_time=time(21, 0), transport="walk")
    view = resolved_view(c)
    assert view["origin_name"] == "판교역"
    assert view["start_time"] == "07:00"
    assert view["transport"] == "walk"

    # 카드가 없으면 interrupt 자체를 하지 않는다(빈 확인 화면 방지)
    out = asyncio.run(human_review({"conditions": c, "advisories": []}))
    assert out["needs_user_confirm"] is False

    # 시스템이 임의로 채운 시각은 칩에 올리지 않는다
    assumed = TripConditions(start_time=time(12, 27), start_time_assumed=True)
    assert resolved_view(assumed)["start_time"] is None
    assert resolved_view(None) == {}

    # 좌표를 못 찾은 이름도 올리지 않는다 — 올리면 반영된 줄 안다
    missing = TripConditions(origin_name="없는역", origin_missing=True)
    assert resolved_view(missing)["origin_name"] is None

    assert Advisory  # 임포트 유지(카드 경로 회귀 시 여기서 먼저 깨진다)


# ------------------------------------------------ UR-40 과거 불편의 선제 경고
async def test_past_friction_becomes_a_confirm_card():
    """기획안 2.1-② — 지난번 불편했던 곳이 일정에 들어오면 «출발 전에» 알린다.

    한동안 이 경로가 통째로 끊겨 있었다(생산자 없음). 이슈가 만들어지고
    카드까지 이어지는지, 그리고 카드에 «그대로 진행»이 첫 선택지로 남는지를 고정한다.
    """
    from datetime import datetime

    from app.graph.subgraphs.validation import build_confirm_cards, check_friction
    from app.schemas import ArchiveHit, Itinerary, ItineraryItem

    it = Itinerary(items=[ItineraryItem(
        seq=1, place_id="p1", name="미들그라운드", candidate_id="c1",
        arrive=datetime(2026, 8, 17, 14, 0), depart=datetime(2026, 8, 17, 15, 0))])
    hit = ArchiveHit(source_type="visit", source_id="v1", place_id="p1",
                     place_name="미들그라운드", summary="주차장 만차로 20분 기다렸다",
                     friction=["parking"], rating=3.0,
                     occurred_at=datetime(2026, 5, 18, 15, 0))

    out = await check_friction({"itinerary": it, "archive_hits": [hit]})
    issues = out["issues"]
    assert len(issues) == 1, issues
    assert issues[0].kind == "past_friction"
    assert issues[0].auto_fixable is False          # 사용자가 판단할 일이다
    assert issues[0].place_name == "미들그라운드"
    assert "주차가 어려웠" in issues[0].detail       # 태그 이름을 그대로 쓰지 않는다

    cards = await build_confirm_cards({"itinerary": it, "issues": issues})
    adv = cards["advisories"]
    assert len(adv) == 1
    assert adv[0].options[0].action == "keep"       # 첫 선택지는 항상 '그대로'
    assert {o.action for o in adv[0].options} >= {"add_parking", "change_transport"}


async def test_high_rating_visit_is_not_warned():
    """별점이 높으면 불편 태그가 있어도 경고하지 않는다 — 카드 피로를 막는다."""
    from app.graph.subgraphs.validation import check_friction
    from app.schemas import ArchiveHit, Itinerary, ItineraryItem

    it = Itinerary(items=[ItineraryItem(seq=1, place_id="p1", name="좋았던 곳")])
    hit = ArchiveHit(source_type="visit", source_id="v1", place_id="p1",
                     summary="조금 붐볐지만 좋았다", friction=["crowding"], rating=4.5)
    out = await check_friction({"itinerary": it, "archive_hits": [hit]})
    assert out["issues"] == []


async def test_friction_of_a_place_not_in_the_plan_is_ignored():
    """일정에 없는 장소는 경고하지 않는다 — 화면에 없는 곳의 카드가 뜨면 안 된다."""
    from app.graph.subgraphs.validation import check_friction
    from app.schemas import ArchiveHit, Itinerary, ItineraryItem

    it = Itinerary(items=[ItineraryItem(seq=1, place_id="p1", name="이번 장소")])
    hit = ArchiveHit(source_type="visit", source_id="v1", place_id="p9",
                     summary="지난번 그 카페", friction=["waiting"], rating=2.0)
    out = await check_friction({"itinerary": it, "archive_hits": [hit]})
    assert out["issues"] == []
