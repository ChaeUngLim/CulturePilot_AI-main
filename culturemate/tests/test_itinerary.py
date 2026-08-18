"""일정 편성 결정론 테스트 — 이동시간/운영시간 제약이 실제로 지켜지는지."""
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from itertools import pairwise

os.environ.setdefault("LLM_BACKEND", "fake")

from app.graph.subgraphs.itinerary import detect_gaps, schedule
from app.schemas import Candidate, GeoPoint, TripConditions


def _state(**over):
    cands = [
        Candidate(id=f"c{i}", place_id=f"p{i}", name=f"장소{i}", indoor=True,
                  geo=GeoPoint(lat=37.5 + i * 0.01, lng=127.0 + i * 0.01),
                  expected_dwell_min=60, final_score=1.0 - 0.1 * i)
        for i in range(3)
    ]
    base = {
        "conditions": TripConditions(date=date(2026, 8, 8), start_time=time(10, 0),
                                     end_time=time(18, 0), transport="subway",
                                     origin=GeoPoint(lat=37.5, lng=127.0)),
        "candidates": cands,
        "context": {"points": [GeoPoint(lat=37.5, lng=127.0)] + [c.geo for c in cands],
                    "travel_matrix": [[0, 20, 30, 40], [20, 0, 15, 25],
                                      [30, 15, 0, 12], [40, 25, 12, 0]]},
    }
    base.update(over)
    return base


async def test_schedule_respects_travel_matrix():
    out = await schedule(_state())
    items = out["itinerary"].items
    assert len(items) >= 2
    for prev, nxt in pairwise(items):
        assert nxt.arrive >= prev.depart               # 시간 역전 없음
        gap = (nxt.arrive - prev.depart).total_seconds() / 60
        assert gap >= nxt.travel_min_from_prev - 1e-6  # 이동시간 확보


async def test_schedule_stops_at_day_end():
    out = await schedule(_state())
    end = out["itinerary"].items[-1].depart
    assert end.hour <= 18


async def test_schedule_honors_must_include():
    st = _state()
    st["conditions"].must_include = ["p2"]             # 점수가 가장 낮은 후보
    out = await schedule(st)
    assert "p2" in [i.place_id for i in out["itinerary"].items]


async def test_detect_gaps_finds_tail_time():
    sched = await schedule(_state())
    st = _state(itinerary=sched["itinerary"])
    gaps = (await detect_gaps(st)).get("gaps") or []
    assert any(g.minutes >= 60 for g in gaps)


# ------------------------------------------------------ 시각 지정 · 실내/야외
async def test_fixed_time_stops_are_honored():
    """'9시에 X, 13시에 Y' 는 스케줄러가 옮기면 안 된다 — 약속이다."""
    from datetime import time

    from app.schemas import StopRequest

    st = _state()
    st["conditions"].stops = [
        StopRequest(at=time(9, 0), place_hint="장소0", purpose="culture"),
        StopRequest(at=time(13, 0), place_hint="장소2", purpose="culture"),
    ]
    out = await schedule(st)
    items = out["itinerary"].items
    fixed = [i for i in items if i.fixed_time]
    assert len(fixed) == 2
    assert fixed[0].name == "장소0" and fixed[0].arrive.hour == 9
    assert fixed[1].name == "장소2" and fixed[1].arrive.hour == 13


async def test_meal_slot_is_not_filled_with_a_museum():
    """식사 자리에 미술관을 넣느니 비워 두는 게 낫다."""
    from datetime import time

    from app.graph.subgraphs.itinerary import _match_stop
    from app.schemas import StopRequest

    cands = _state()["candidates"]          # 전부 kind='venue'
    stop = StopRequest(at=time(17, 0), purpose="meal")
    assert _match_stop(stop, cands, set()) is None


async def test_indoor_request_excludes_outdoor_places():
    from app.graph.subgraphs.itinerary import assemble_constraints

    st = _state()
    st["conditions"].indoor_pref = "indoor"
    st["candidates"][1].indoor = False       # 야외 한 곳 섞기
    out = await assemble_constraints(st)
    assert all(c.indoor is not False for c in out["candidates"])


async def test_indoor_filter_relaxes_when_nothing_left():
    """조건이 너무 좁아 후보가 사라지면 빈 결과 대신 감점으로 완화한다."""
    from app.graph.subgraphs.itinerary import assemble_constraints

    st = _state()
    st["conditions"].indoor_pref = "indoor"
    for c in st["candidates"]:
        c.indoor = False
    out = await assemble_constraints(st)
    assert len(out["candidates"]) == len(st["candidates"])


async def test_destination_pulls_only_the_last_stop():
    """도착지는 마지막 자리에서만 작동해야 한다.

    앞자리까지 끌어당기면 '도착지 지정'이 곧 '가까운 곳만 추천'이 되어
    하루 전체가 망가진다.
    """
    from app.schemas import Candidate, GeoPoint

    far = GeoPoint(lat=37.60, lng=127.10)          # 도착지에서 먼 곳
    near = GeoPoint(lat=37.50, lng=127.00)         # 도착지 바로 옆
    cands = [
        Candidate(id="best", place_id="best", name="가장 좋은 곳", indoor=True,
                  geo=far, expected_dwell_min=60, final_score=1.0),
        Candidate(id="mid", place_id="mid", name="중간", indoor=True,
                  geo=GeoPoint(lat=37.55, lng=127.05),
                  expected_dwell_min=60, final_score=0.6),
        Candidate(id="near", place_id="near", name="도착지 옆", indoor=True,
                  geo=near, expected_dwell_min=60, final_score=0.2),
    ]
    st = _state(candidates=cands)
    st["conditions"].destination = GeoPoint(lat=37.50, lng=127.00, name="도착지")
    st["conditions"].destination_name = "도착지"
    st["conditions"].end_time = time(15, 0)        # 3자리 정도만 들어가게

    items = (await schedule(st))["itinerary"].items
    assert items[0].name == "가장 좋은 곳"          # 점수 높은 곳이 앞을 지킨다
    assert items[-1].name == "도착지 옆"            # 마지막만 도착지 쪽으로


async def test_no_destination_keeps_score_order():
    """도착지가 없으면 위치 때문에 순서가 흔들리면 안 된다."""
    items = (await schedule(_state()))["itinerary"].items
    assert items[0].name == "장소0"


def test_destination_parsing():
    from app.graph.router import _rule_conditions

    assert _rule_conditions("추천해줘 잠실역까지").destination_name == "잠실역"
    assert _rule_conditions("홍대입구역까지 가는 길에").destination_name == "홍대입구역"
    assert _rule_conditions("서울역에서 마무리하는 일정").destination_name == "서울역"
    # 시간 표현의 '까지'는 도착지가 아니다
    assert _rule_conditions("오후 6시까지 끝나는 전시").destination_name is None


def test_web_titles_are_not_places():
    """블로그 글 제목이 장소로 일정에 들어가면 안 된다."""
    from app.tools.websearch import looks_like_place

    junk = [
        "8월 서울 전시회 추천<강남,서초구 BEST5>",
        "[투어해야 할 서울 8월전시회 21곳 총정리] 영롱 끝판왕 전시부터",
        "서울소식",
        "무더위 날려주는 6~8월 서울 여름 축제 추천!",
        "국립현대미술관 다녀왔어요",
        "웹 결과",
    ]
    real = ["갤러리아이엘", "책읽는미술관 본사", "리움미술관", "예술의전당"]
    assert not any(looks_like_place(x) for x in junk)
    assert all(looks_like_place(x) for x in real)


async def test_candidates_without_coordinates_are_dropped():
    """좌표 없는 후보는 일정에 못 들어간다 — 이동시간도 지도도 불가능하다."""
    from app.graph.subgraphs.discovery import normalize
    from app.schemas import Candidate, GeoPoint

    st = {
        "conditions": _state()["conditions"],
        "user_id": "",
        "raw_candidates": [
            Candidate(id="ok", place_id="ok", name="리움미술관",
                      geo=GeoPoint(lat=37.53, lng=126.99)),
            Candidate(id="junk", place_id="junk", name="8월 전시회 추천 BEST5"),
        ],
    }
    out = await normalize(st)
    assert [c.name for c in out["candidates"]] == ["리움미술관"]


async def test_meal_request_without_time_reserves_a_slot():
    """'문화생활과 식사'처럼 시각을 안 정해도 식사 자리는 남겨야 한다."""
    from app.graph.router import _rule_conditions
    from app.schemas import Candidate, GeoPoint

    cands = [
        Candidate(id=f"v{i}", place_id=f"v{i}", name=f"전시{i}", kind="venue",
                  geo=GeoPoint(lat=37.50 + i * 0.01, lng=127.02),
                  final_score=0.9 - 0.01 * i, expected_dwell_min=60, indoor=True)
        for i in range(5)
    ] + [
        Candidate(id="f0", place_id="f0", name="식당", kind="food",
                  geo=GeoPoint(lat=37.505, lng=127.03),
                  final_score=0.3, expected_dwell_min=80, indoor=True),
    ]
    c = _rule_conditions("강남 내일 문화생활과 식사 알아서 스케줄 만들어줘")
    c.date, c.start_time, c.end_time = date(2026, 8, 11), time(10, 0), time(20, 0)
    c.origin = GeoPoint(lat=37.4979, lng=127.0276)

    items = (await schedule({"conditions": c, "candidates": cands,
                             "context": {"points": [], "travel_matrix": []}}))["itinerary"].items
    meals = [i for i in items if i.purpose == "meal"]
    assert len(meals) == 1
    assert meals[0].kind == "food"
    # 점심 시간대에 들어가야 한다
    assert time(11, 30) <= meals[0].arrive.time() <= time(13, 30)


async def test_leg_distance_is_filled():
    """지도 구간 라벨에 쓸 거리가 채워져야 한다."""
    items = (await schedule(_state()))["itinerary"].items
    assert all(i.travel_km_from_prev is not None for i in items[1:])


async def test_measured_travel_time_matches_the_clock():
    """구간에 적힌 이동시간과 실제 시각 간격이 어긋나면 안 된다.

    "이동 16분"이라 적힌 구간의 실제 간격이 15분이면, 표시된 대로 움직인
    사용자는 다음 일정에 늦는다. 숫자와 시각 중 하나는 반드시 거짓이 된다.
    """
    from app.graph.subgraphs.itinerary import _reflow
    from app.schemas import GeoPoint, ItineraryItem

    day = datetime(2026, 8, 11, 10, 0)
    items = [
        ItineraryItem(seq=1, name="A", arrive=day, depart=day + timedelta(minutes=60),
                      dwell_min=60, travel_min_from_prev=10,
                      geo=GeoPoint(lat=37.5, lng=127.0)),
        ItineraryItem(seq=2, name="B", arrive=day + timedelta(minutes=75),
                      depart=day + timedelta(minutes=135), dwell_min=60,
                      travel_min_from_prev=25,      # 실측이 추정(15분)보다 길어졌다
                      geo=GeoPoint(lat=37.52, lng=127.03)),
    ]
    out = _reflow(items, datetime(2026, 8, 11, 20, 0))
    for prev, nxt in pairwise(out):
        gap = (nxt.arrive - prev.depart).total_seconds() / 60
        assert gap >= nxt.travel_min_from_prev


async def test_reflow_keeps_fixed_times_and_warns():
    """시각을 지정한 항목은 옮기지 않되, 늦을 수 있으면 알려준다."""
    from app.graph.subgraphs.itinerary import _reflow
    from app.schemas import ItineraryItem

    day = datetime(2026, 8, 11, 10, 0)
    items = [
        ItineraryItem(seq=1, name="A", arrive=day, depart=day + timedelta(minutes=90),
                      dwell_min=90, travel_min_from_prev=0),
        ItineraryItem(seq=2, name="약속", fixed_time=True,
                      arrive=day + timedelta(minutes=100),
                      depart=day + timedelta(minutes=160),
                      dwell_min=60, travel_min_from_prev=40),   # 90+40 > 100
    ]
    out = _reflow(items, datetime(2026, 8, 11, 20, 0))
    assert out[1].arrive == day + timedelta(minutes=100)   # 약속은 그대로
    assert "늦을 수 있음" in (out[1].reason or "")


async def test_reflow_drops_items_past_day_end():
    """다시 계산해서 하루를 넘기면 잘라낸다 — 갈 수 없는 일정은 남기지 않는다."""
    from app.graph.subgraphs.itinerary import _reflow
    from app.schemas import ItineraryItem

    day = datetime(2026, 8, 11, 18, 0)
    items = [
        ItineraryItem(seq=1, name="A", arrive=day, depart=day + timedelta(minutes=60),
                      dwell_min=60, travel_min_from_prev=0),
        ItineraryItem(seq=2, name="B", arrive=day + timedelta(minutes=70),
                      depart=day + timedelta(minutes=130), dwell_min=60,
                      travel_min_from_prev=90),
    ]
    out = _reflow(items, datetime(2026, 8, 11, 20, 0))
    assert [i.name for i in out] == ["A"]


async def test_explicit_walk_is_never_switched():
    """도보를 골랐으면 8km라도 도보로 잰다.

    몰래 대중교통으로 바꾸면 화면에는 '도보'라고 적혀 있으면서 숫자는
    지하철인 일정이 된다. 100분이 걸린다면 그게 도보의 사실이고,
    그걸 보고 수단을 바꿀지는 사용자가 정할 일이다.
    """
    from unittest.mock import patch

    from app.graph.subgraphs.itinerary import _best_leg
    from app.schemas import GeoPoint

    asked = []

    async def fake(_o, _d, mode):
        asked.append(mode)
        return {"minutes": 105, "distance_m": 8000, "mode": "walk", "source": "ors"}

    with patch("app.tools.maps.route_duration", fake):
        leg = await _best_leg(GeoPoint(lat=37.4979, lng=127.0276),
                              GeoPoint(lat=37.5665, lng=126.9780), "walk")
    assert leg["mode"] == "walk" and leg["minutes"] == 105
    assert asked == ["walk"]            # 대안을 조회조차 하지 않는다
    assert "switched_reason" not in leg


async def test_explicit_subway_is_never_switched():
    """지하철을 골랐으면 400m 구간도 지하철로 잰다 — 고른 대로 보여준다."""
    from unittest.mock import patch

    from app.graph.subgraphs.itinerary import _best_leg
    from app.schemas import GeoPoint

    asked = []

    async def fake(_o, _d, mode):
        asked.append(mode)
        return {"minutes": 14, "distance_m": 420, "mode": "subway", "source": "odsay"}

    with patch("app.tools.maps.route_duration", fake):
        leg = await _best_leg(GeoPoint(lat=37.4979, lng=127.0276),
                              GeoPoint(lat=37.5010, lng=127.0300), "subway")
    assert leg["mode"] == "subway" and asked == ["subway"]


async def test_car_is_never_switched():
    """차를 가져온 사람에게 지하철을 타라고 할 수는 없다."""
    from unittest.mock import patch

    from app.graph.subgraphs.itinerary import _best_leg
    from app.schemas import GeoPoint

    async def fake(_o, _d, mode):
        assert mode == "car", "자가용은 대안을 조회조차 하지 않는다"
        return {"minutes": 40, "distance_m": 20000, "source": "naver",
                "mode": "car", "estimated": False}

    with patch("app.tools.maps.route_duration", fake):
        leg = await _best_leg(GeoPoint(lat=37.5, lng=127.0),
                              GeoPoint(lat=37.6, lng=127.1), "car")
    assert leg["mode"] == "car"
    assert "switched_reason" not in leg


async def test_only_best_mode_mixes_transports():
    """수단을 섞는 건 '최단루트'뿐이다 — 그게 그 모드의 정의다."""
    from unittest.mock import patch

    from app.graph.subgraphs.itinerary import _best_leg
    from app.schemas import GeoPoint

    asked = []

    async def fake(_o, _d, mode):
        asked.append(mode)
        return {"walk":   {"minutes": 40, "distance_m": 3000, "mode": "walk"},
                "subway": {"minutes": 24, "distance_m": 3100, "mode": "subway"},
                "bus":    {"minutes": 30, "distance_m": 3400, "mode": "bus"},
                "car":    {"minutes": 12, "distance_m": 3500, "mode": "car"}}[mode]

    with patch("app.tools.maps.route_duration", fake):
        leg = await _best_leg(GeoPoint(lat=37.5, lng=127.0),
                              GeoPoint(lat=37.53, lng=127.03), "best", "free")
    # 네 수단을 모두 견준다 — '지하철+버스'는 수단이 아니라 조합이라 뺐다
    assert set(asked) == {"walk", "subway", "bus", "car"}
    assert leg["mode"] == "car"          # 주차가 넉넉하면 가장 빠른 것
    assert leg["alternatives"]           # 나머지 후보가 근거로 남는다


async def test_best_prefers_fewer_transfers():
    """22분 환승 2회보다 24분 무환승이 낫다 — 계단과 대기는 시간에 안 잡힌다."""
    from unittest.mock import patch

    from app.graph.subgraphs.itinerary import _best_leg
    from app.schemas import GeoPoint

    async def fake(_o, _d, mode):
        return {"walk":   {"minutes": 90, "distance_m": 7000, "mode": "walk"},
                "subway": {"minutes": 24, "distance_m": 3100, "mode": "subway",
                           "transfers": 0},
                "bus":    {"minutes": 22, "distance_m": 3400, "mode": "bus",
                           "transfers": 2},
                "car":    {"minutes": 20, "distance_m": 3500, "mode": "car"}}[mode]

    with patch("app.tools.maps.route_duration", fake):
        # 주차 불가라 자가용은 +15분, 버스는 환승 2회로 +8분 → 지하철이 이긴다
        leg = await _best_leg(GeoPoint(lat=37.5, lng=127.0),
                              GeoPoint(lat=37.53, lng=127.03), "best", "none")
    assert leg["mode"] == "subway"


async def test_reroute_keeps_places_and_changes_only_travel():
    """수단을 바꿔도 장소와 순서는 그대로여야 한다 — 다시 탐색하지 않는다."""
    from app.graph.subgraphs.itinerary import reroute_itinerary
    from app.schemas import GeoPoint, Itinerary, ItineraryItem

    day = datetime(2026, 8, 11, 10, 0)
    base = Itinerary(date=date(2026, 8, 11), version=1, items=[
        ItineraryItem(seq=1, place_id="p1", name="A", geo=GeoPoint(lat=37.52, lng=127.02),
                      arrive=day, depart=day + timedelta(minutes=60), dwell_min=60,
                      travel_min_from_prev=12, transport="car"),
        ItineraryItem(seq=2, place_id="p2", name="B", geo=GeoPoint(lat=37.50, lng=127.03),
                      arrive=day + timedelta(minutes=80),
                      depart=day + timedelta(minutes=140), dwell_min=60,
                      travel_min_from_prev=20, transport="car"),
    ])
    out = await reroute_itinerary(base, "walk",
                                  origin=GeoPoint(lat=37.4979, lng=127.0276),
                                  end_time=time(20, 0))
    assert [i.place_id for i in out.items] == ["p1", "p2"]
    assert out.version == base.version + 1
    # 도보가 자동차보다 오래 걸려야 정상이다
    assert out.total_travel_min > base.total_travel_min
    # 고른 수단으로 가거나, 바꿨다면 왜 바꿨는지가 반드시 남아 있어야 한다.
    # 조용히 다른 수단으로 계산해 놓으면 화면이 거짓말을 하게 된다.
    for item in out.items:
        assert item.transport == "walk" or "바꿨습니다" in (item.reason or ""), item


async def test_route_places_orders_by_proximity():
    """큐레이션 묶음은 가까운 순서로 이어져야 지도에서 선이 읽힌다."""
    from app.graph.subgraphs.itinerary import route_places
    from app.schemas import GeoPoint

    places = [
        {"place_id": "far", "name": "먼 곳", "lat": 37.60, "lng": 127.10},
        {"place_id": "near", "name": "가까운 곳", "lat": 37.50, "lng": 127.01},
        {"place_id": "mid", "name": "중간", "lat": 37.55, "lng": 127.05},
    ]
    out = await route_places(places, "car", origin=GeoPoint(lat=37.4979, lng=127.0276))
    assert [i.place_id for i in out.items] == ["near", "mid", "far"]
    assert all(i.transport == "car" for i in out.items)


async def test_route_places_handles_empty_input():
    """좌표가 없으면 빈 일정 — 예외로 화면을 깨지 않는다."""
    from app.graph.subgraphs.itinerary import route_places

    out = await route_places([{"name": "좌표없음"}], "walk")
    assert out.items == []


async def test_best_route_mixes_modes_per_leg():
    """최단루트는 구간마다 다른 수단을 골라 섞을 수 있어야 한다."""
    from unittest.mock import patch

    from app.graph.subgraphs.itinerary import _fastest_leg
    from app.schemas import GeoPoint

    a, b = GeoPoint(lat=37.50, lng=127.02), GeoPoint(lat=37.51, lng=127.03)

    async def fake(_o, _d, mode):
        return {"walk":   {"minutes": 9,  "distance_m": 700,  "mode": "walk"},
                "subway": {"minutes": 18, "distance_m": 1200, "mode": "subway"},
                "bus":    {"minutes": 20, "distance_m": 1400, "mode": "bus"},
                "car":    {"minutes": 6,  "distance_m": 1500, "mode": "car"}}[mode]

    with patch("app.tools.maps.route_duration", fake):
        # 주차가 넉넉하면 자가용(6분)이 도보(9-5=4점)와 비슷 → 도보 우대로 도보
        near = await _fastest_leg(a, b, "free")
        # 주차가 불가하면 자가용에 15분이 붙어 도보가 확실히 이긴다
        none = await _fastest_leg(a, b, "none")
    assert none["mode"] == "walk"
    assert near["mode"] in ("walk", "car")
    assert none["alternatives"]          # 왜 이걸 골랐는지 대안이 남는다


async def test_parking_penalty_pushes_car_out():
    """주차 불가인 곳에 자가용을 추천하면 도착해서 20분을 헤맨다."""
    from unittest.mock import patch

    from app.graph.subgraphs.itinerary import _fastest_leg
    from app.schemas import GeoPoint

    async def fake(_o, _d, mode):
        return {"walk":   {"minutes": 40, "distance_m": 3000, "mode": "walk"},
                "subway": {"minutes": 22, "distance_m": 3200, "mode": "subway"},
                "bus":    {"minutes": 30, "distance_m": 3400, "mode": "bus"},
                "car":    {"minutes": 12, "distance_m": 3500, "mode": "car"}}[mode]

    with patch("app.tools.maps.route_duration", fake):
        free = await _fastest_leg(GeoPoint(lat=37.5, lng=127.0),
                                  GeoPoint(lat=37.53, lng=127.03), "free")
        none = await _fastest_leg(GeoPoint(lat=37.5, lng=127.0),
                                  GeoPoint(lat=37.53, lng=127.03), "none")
    assert free["mode"] == "car"          # 12분 + 주차 0 → 자가용
    assert none["mode"] == "subway"       # 12분 + 주차 15 = 27분 → 지하철


def test_transport_mix_summary():
    """무엇을 얼마나 탔는지 한 줄로 읽혀야 한다."""
    from app.graph.subgraphs.itinerary import _mix, summarize_transport
    from app.schemas import ItineraryItem

    items = [
        ItineraryItem(seq=1, name="A", travel_min_from_prev=0),
        ItineraryItem(seq=2, name="B", travel_min_from_prev=12, transport="walk"),
        ItineraryItem(seq=3, name="C", travel_min_from_prev=20, transport="subway",
                      travel_fare=1500),
        ItineraryItem(seq=4, name="D", travel_min_from_prev=8, transport="walk"),
    ]
    mix, fare = _mix(items)
    assert mix == {"walk": 20, "subway": 20}
    assert fare == 1500
    assert "도보 20분" in summarize_transport(mix)


async def test_gap_fill_keeps_chronological_order():
    """공백을 채운 뒤에도 목록은 시간순이어야 한다.

    예전에는 공백 시작 시각을 모를 때 utcnow() 로 때웠고, 그 결과 09시 일정
    한가운데에 '16:28' 짜리 항목이 박혀 순서가 통째로 깨졌다.
    """
    from app.graph.subgraphs.itinerary import detect_gaps, fill_gaps
    from app.schemas import Candidate, GeoPoint

    st = _state()
    st["conditions"].end_time = time(22, 0)
    sched = await schedule(st)
    it = sched["itinerary"]

    gaps = (await detect_gaps({"conditions": st["conditions"], "itinerary": it})).get("gaps")
    assert gaps

    filler = Candidate(id="fill", place_id="fill", name="채운 카페", kind="cafe",
                       geo=GeoPoint(lat=37.51, lng=127.03), expected_dwell_min=45,
                       raw={"gap_id": gaps[0].id})
    out = await fill_gaps({"conditions": st["conditions"], "itinerary": it,
                           "gaps": [gaps[0]], "nearby": [filler]})
    items = out["itinerary"].items
    arrivals = [i.arrive for i in items if i.arrive]
    assert arrivals == sorted(arrivals), [f"{a:%H:%M}" for a in arrivals]
    assert [i.seq for i in items] == list(range(1, len(items) + 1))


def test_personal_average_dwell_is_applied_when_unspecified():
    """avg_dwell_min 을 계산해 두고도 일정 편성에 안 쓰고 있었다."""
    from app.graph.subgraphs.itinerary import _apply_dwell
    from app.schemas import Candidate, TasteProfile, TripConditions

    def pool():
        return [Candidate(source="catalog", kind="venue", name=n, expected_dwell_min=d)
                for n, d in (("미술관", 90), ("카페", 40), ("서점", 60))]

    # 78분 프로필 → 1.3배. 상대 순서는 그대로여야 한다.
    cands = pool()
    _apply_dwell(cands, TripConditions(), TasteProfile(user_id="u", avg_dwell_min=78))
    got = [x.expected_dwell_min for x in cands]
    assert got[0] > got[2] > got[1], got          # 미술관 > 서점 > 카페
    assert all(a > b for a, b in zip(got, [90, 40, 60], strict=False)), got

    # 사용자가 말했으면 개인 평균이 이를 덮으면 안 된다
    cands = pool()
    _apply_dwell(cands, TripConditions(dwell_min=30, dwell_max=30),
                 TasteProfile(user_id="u", avg_dwell_min=200))
    assert [x.expected_dwell_min for x in cands] == [30, 30, 30]

    # 기록이 없으면 손대지 않는다
    cands = pool()
    _apply_dwell(cands, TripConditions(), None)
    assert [x.expected_dwell_min for x in cands] == [90, 40, 60]

    # 극단적인 평균에도 하루가 무너지지 않도록 배율에 상한이 있다
    cands = pool()
    _apply_dwell(cands, TripConditions(), TasteProfile(user_id="u", avg_dwell_min=600))
    assert max(x.expected_dwell_min for x in cands) <= 90 * 1.6 + 5


# ---------------------------------------------------- 종류별 개수 (FR-33 / UR-02)
def test_kind_quota_parses_two_counts():
    """'문화 2개 + 디저트 3개' — 앞 숫자 하나만 잡던 버그의 회귀 테스트.

    예전에는 `_detect_count` 가 첫 매치만 봐서 stop_count=2 로 끝났고,
    문화 2곳이 배치된 뒤 빈틈 채우기가 카페를 여섯 곳 밀어 넣었다.
    """
    from app.graph.router import _detect_kind_quota

    q = _detect_kind_quota("서초구 문화일정 2개 추천와 디져트 맛집 3개 추천")
    assert q == {"culture": 2, "cafe": 3}


def test_dessert_matjip_is_a_cafe_not_a_restaurant():
    """'디저트 맛집'은 밥집이 아니다 — 합성어가 낱말보다 먼저 걸려야 한다."""
    from app.graph.router import _detect_kind_quota

    assert _detect_kind_quota("디저트 맛집 2개") == {"cafe": 2}
    assert _detect_kind_quota("맛집 2개") == {"food": 2}


def test_count_without_a_kind_word_stays_a_total():
    """'5개 정도'처럼 종류를 말하지 않으면 그룹 할당이 아니라 총량이다."""
    from app.graph.router import _detect_kind_quota

    assert _detect_kind_quota("5개 정도 추천해줘") == {}


async def test_schedule_respects_kind_quota():
    """할당량을 넘겨 같은 그룹이 자리를 독식하지 않는다."""
    cands = [
        Candidate(id=f"v{i}", place_id=f"pv{i}", name=f"미술관{i}", kind="venue",
                  indoor=True, geo=GeoPoint(lat=37.5 + i * 0.002, lng=127.0),
                  expected_dwell_min=60, final_score=1.0)          # 문화가 점수 우위
        for i in range(4)
    ] + [
        Candidate(id=f"f{i}", place_id=f"pf{i}", name=f"카페{i}", kind="cafe",
                  indoor=True, geo=GeoPoint(lat=37.51 + i * 0.002, lng=127.0),
                  expected_dwell_min=45, final_score=0.4)
        for i in range(3)
    ]
    conditions = TripConditions(
        date=date(2026, 8, 8), start_time=time(10, 0), end_time=time(20, 0),
        transport="subway", origin=GeoPoint(lat=37.5, lng=127.0),
        stop_count=3, kind_quota={"culture": 2, "cafe": 1})

    out = await schedule({"conditions": conditions, "candidates": cands,
                          "context": {"points": [], "travel_matrix": []}})
    kinds = [i.kind for i in out["itinerary"].items]
    assert kinds.count("venue") <= 2, f"문화가 할당량을 넘었다: {kinds}"
    assert kinds.count("cafe") <= 1, f"카페가 할당량을 넘었다: {kinds}"
    assert len(kinds) <= 3


async def test_unavailable_quota_kind_does_not_empty_the_day():
    """몫의 후보가 없어도 **다른 종류는 계속 배치한다.**

    "문화생활 추천해주고 디저트 맛집 2개" 는 `kind_quota={'cafe': 2}` 가 되는데,
    카페는 탐색(discovery)이 아니라 빈틈 채우기의 주변 검색에서 온다. 그래서
    편성 시점의 후보에는 카페가 하나도 없다.

    예전에는 이때 `_quota_missing` 을 보고 그냥 `break` 해서, **첫 반복에 탈출해
    문화까지 한 곳도 못 넣고 일정이 0곳으로 나왔다**(2026-08-18). 몫만큼 자리를
    남기는 것과 편성을 멈추는 것은 다르다.
    """
    st = _state(conditions=TripConditions(
        date=date(2026, 8, 8), start_time=time(10, 0), end_time=time(18, 0),
        transport="subway", origin=GeoPoint(lat=37.5, lng=127.0),
        kind_quota={"cafe": 2},          # 후보에 카페는 없다
    ))
    out = await schedule(st)
    items = out["itinerary"].items
    assert items, "몫의 후보가 없다고 일정 전체가 비면 안 된다"
    assert all(i.kind != "cafe" for i in items)


async def test_unfilled_quota_still_reserves_its_seats():
    """못 채운 몫만큼 **자리는 남긴다.**

    총 3곳인데 디저트 2곳을 말했고 카페 후보가 없다면, 남은 1곳만 다른 종류로
    채우고 멈춘다. 그래야 나중에 빈틈 채우기가 카페를 넣을 자리가 남는다.
    """
    st = _state(conditions=TripConditions(
        date=date(2026, 8, 8), start_time=time(10, 0), end_time=time(18, 0),
        transport="subway", origin=GeoPoint(lat=37.5, lng=127.0),
        stop_count=3, kind_quota={"cafe": 2},
    ))
    items = (await schedule(st))["itinerary"].items
    assert len(items) == 1, "카페 몫 2자리를 남기고 1곳만 채워야 한다"


async def test_quota_is_filled_before_other_kinds():
    """몫이 남은 그룹의 후보가 있으면 그쪽을 먼저 쓴다."""
    cands = [
        Candidate(id="v0", place_id="pv0", name="전시관", kind="venue", indoor=True,
                  geo=GeoPoint(lat=37.51, lng=127.01),
                  expected_dwell_min=60, final_score=0.9),
        Candidate(id="f0", place_id="pf0", name="디저트카페", kind="cafe", indoor=True,
                  geo=GeoPoint(lat=37.52, lng=127.02),
                  expected_dwell_min=60, final_score=0.1),   # 점수는 낮다
    ]
    st = _state(candidates=cands, conditions=TripConditions(
        date=date(2026, 8, 8), start_time=time(10, 0), end_time=time(18, 0),
        transport="subway", origin=GeoPoint(lat=37.5, lng=127.0),
        kind_quota={"cafe": 1},
    ))
    out = await schedule(st)
    kinds = [i.kind for i in out["itinerary"].items]
    # 점수가 낮아도 몫이 걸린 카페가 먼저 들어간다
    assert kinds and kinds[0] == "cafe"
