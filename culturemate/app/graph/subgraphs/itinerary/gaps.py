"""공백을 찾아 주변 추천으로 메운다 (문서 04 하단, 차별점 3).

    schedule → detect_gaps → ⟨Send 병렬⟩ nearby_search ×N → rerank_nearby → fill_gaps

식사·휴식·조기종료로 생긴 빈 시간이 대상이다. 탐색(discovery)이 아니라 지도
주변검색에서 후보를 가져오는 이유는, 카페·식당이 공공 문화 API에 없기 때문이다.
그래서 「디저트 3개」 같은 종류별 몫은 여기가 **마지막 기회**다.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from langgraph.types import Send

from app.config import get_settings
from app.graph.budget import from_state
from app.graph.state import ItineraryState
from app.graph.subgraphs.itinerary.legs import _mix
from app.graph.subgraphs.itinerary.notes import _notes_from_conditions
from app.graph.subgraphs.itinerary.placement import (
    DEFAULT_DAY_END,
    DEFAULT_DAY_START,
    _purpose,
    _quota_take,
    _quota_unmet,
    _reserve_to_dest,
)
from app.graph.subgraphs.itinerary.schedule import _reflow
from app.memory.profile import personal_score
from app.schemas import Candidate, Gap, GeoPoint, ItineraryItem
from app.tools import maps, weather
from app.tools.base import safe_call


async def detect_gaps(state: ItineraryState) -> dict:
    """식사·휴식·조기종료로 생긴 빈 시간을 찾는다(문서 차별점 3)."""
    it = state.get("itinerary")
    c = state["conditions"]
    if not it or not it.items:
        return {"trace": ["itin.gaps:0"]}

    gaps: list[Gap] = []
    day = it.date or weather.today_kst()

    # 첫 일정 **이전**도 빈틈이다. 여기를 안 보면 "7시 출발"이라고 말했는데
    # 첫 장소가 15시에 시작하는 일정이 그대로 나간다 — 오전 여덟 시간이
    # 아무 설명 없이 사라진 것처럼 보인다.
    first = it.items[0]
    if first.arrive:
        day_start = datetime.combine(day, c.start_time or DEFAULT_DAY_START)
        head = int((first.arrive - day_start).total_seconds() // 60) \
            - first.travel_min_from_prev
        if head >= 60:
            gaps.append(Gap(after_seq=0,           # 0 = 첫 항목 앞에 끼워 넣는다
                            start=day_start, end=first.arrive, minutes=head,
                            anchor=c.origin or first.geo,
                            purpose=_purpose(day_start)))

    for prev, nxt in zip(it.items, it.items[1:], strict=False):
        idle = int((nxt.arrive - prev.depart).total_seconds() // 60) - nxt.travel_min_from_prev
        if idle >= 40:
            gaps.append(Gap(after_seq=prev.seq, start=prev.depart, end=nxt.arrive,
                            minutes=idle, anchor=prev.geo,
                            purpose=_purpose(prev.depart)))
    last = it.items[-1]
    day_end = datetime.combine(day, c.end_time or DEFAULT_DAY_END)
    # 도착지가 있으면 거기까지 가는 시간은 공백이 아니다
    if it.destination and last.geo:
        day_end -= timedelta(minutes=_reserve_to_dest(
            [Candidate(id="x", name="x", geo=last.geo)], it.destination, c.transport))
    tail = int((day_end - last.depart).total_seconds() // 60)
    if tail >= 60:
        gaps.append(Gap(after_seq=last.seq, start=last.depart, end=day_end,
                        minutes=tail, anchor=last.geo, purpose=_purpose(last.depart)))
    return {"gaps": gaps, "trace": [f"itin.gaps:{len(gaps)}"]}


def dispatch_nearby(state: ItineraryState) -> list[Send]:
    """공백 × 카테고리로 팬아웃(식당/카페/흥미 장소)."""
    gaps = state.get("gaps") or []
    if not gaps:
        return ["fill_gaps"]          # 팬아웃 없이 전체 state를 그대로 통과
    # 팬아웃된 노드는 전체 state 를 못 보므로 예산을 payload 에 실어 보낸다.
    deadline = from_state(state).deadline
    sends: list[Send] = []
    for g in gaps:
        kinds = ["food", "cafe"] if g.purpose == "meal" else ["cafe", "shop", "venue"]
        for kind in kinds:
            sends.append(Send("nearby_search", {"gap": g, "kind": kind,
                                                "conditions": state["conditions"],
                                                "deadline": deadline}))
    return sends


async def nearby_search(payload: dict) -> dict:
    g: Gap = payload["gap"]
    if not g.anchor:
        return {}
    radius = 500 if g.minutes < 60 else 1200
    rows = await safe_call("maps.nearby",
                           maps.search_nearby(g.anchor, payload["kind"], radius), [],
                           deadline=payload.get("deadline"))
    cands = [
        Candidate(source="maps", kind=payload["kind"], name=r.get("name", ""),
                  category=r.get("category"), address=r.get("address"),
                  geo=GeoPoint(lat=r["lat"], lng=r["lng"]) if r.get("lat") else None,
                  expected_dwell_min=min(g.minutes - 15, 60),
                  raw={**r, "gap_id": g.id})
        for r in rows
    ]
    return {"nearby": cands}


async def rerank_nearby(state: ItineraryState) -> dict:
    """거리·남은시간·날씨·취향 기준 통합 리랭킹(문서 04 하단)."""
    ctx = state.get("context") or {}
    profile = state.get("taste_profile")
    risky = bool(ctx.get("risky_hours"))
    out: list[Candidate] = []
    for cand in state.get("nearby") or []:
        s = 0.5 + 0.5 * personal_score(cand, profile)
        if risky and cand.indoor is False:
            s -= 0.3
        cand.final_score = s
        out.append(cand)
    out.sort(key=lambda x: x.final_score, reverse=True)
    return {"nearby": out, "trace": [f"itin.nearby:{len(out)}"]}


async def fill_gaps(state: ItineraryState) -> dict:
    """공백마다 최상위 후보 1개를 일정에 끼워 넣고 시각을 재계산한다."""
    it = state.get("itinerary")
    gaps = state.get("gaps") or []
    nearby = state.get("nearby") or []
    if not it or not gaps or not nearby:
        return {"trace": ["itin.fill:skip"]}

    by_gap: dict[str, list[Candidate]] = {}
    for cand in nearby:
        by_gap.setdefault(cand.raw.get("gap_id", ""), []).append(cand)

    c = state["conditions"]
    items = list(it.items)
    for g in sorted(gaps, key=lambda x: x.after_seq, reverse=True):
        pool = by_gap.get(g.id) or []
        # 공백의 시작 시각을 모르면 끼워 넣을 수 없다. 예전엔 utcnow() 로 때웠는데,
        # 그러면 09시 일정 한가운데에 '16:28' 짜리 항목이 박혀 순서가 통째로 깨졌다.
        if not pool or g.start is None:
            continue
        insert_at = next((i for i, x in enumerate(items) if x.seq == g.after_seq), -1) + 1
        # 빈틈 길이에 맞춰 여러 곳을 넣는다. 한 곳만 넣으면 열 시간짜리 공백에
        # 한 자리만 채워지고 나머지가 그대로 빈다 — "7시 출발 21시 도착"이라고
        # 말했는데 정오에 끝나는 일정이 그렇게 나왔다.
        #
        # 몇 개가 들어갈지는 **남은 시간이 결정한다.** 어림수로 상한을 두면
        # (예: 150분당 하나) 실제로 더 들어갈 자리가 있어도 거기서 멈춘다.
        # 아래 루프가 체류·이동을 실제로 빼 가며 더 못 넣을 때 스스로 끊는다.
        #
        # 단, **사용자가 개수를 말했으면 그 수가 상한이다.** 예전에는 설정값
        # (MAX_STOPS=8)만 봐서, "문화 2 + 디저트 3"으로 5곳을 원한 요청에 빈틈
        # 채우기가 8곳까지 밀어 넣었다. 빈 시간을 채우는 것보다 말한 개수를
        # 지키는 쪽이 먼저다 — 남는 시간은 사용자가 쓸 시간이다.
        cap = min(c.stop_count or get_settings().max_stops, get_settings().max_stops)
        room = max(0, cap - len(items))
        # 사용자가 종류별 개수를 말했으면, 스케줄러가 못 채우고 비워 둔 몫부터 메운다.
        # 카페·식당은 탐색이 아니라 주변 검색에서 오므로 이 자리가 마지막 기회다.
        if c.kind_quota:
            placed: dict[str, int] = {}
            for it_item in items:
                _quota_take(placed, it_item.kind)
            pool = sorted(pool, key=lambda x: not _quota_unmet(c.kind_quota, placed, x.kind))
        budget_left = g.minutes
        cursor = g.start
        for pick in pool[:room]:
            travel = 10
            stay = pick.expected_dwell_min
            if travel + stay > budget_left:
                break
            arrive = cursor + timedelta(minutes=travel)
            items.insert(insert_at, ItineraryItem(
                seq=0, candidate_id=pick.id, place_id=pick.place_id, name=pick.name,
                kind=pick.kind, geo=pick.geo, arrive=arrive,
                depart=arrive + timedelta(minutes=stay),
                dwell_min=stay, travel_min_from_prev=travel,
                # 'unknown' 을 그대로 실으면 화면에 수단이 'unknown' 으로 찍힌다
                transport=c.transport if c.transport != "unknown" else "best",
                indoor=pick.indoor,
                parking=pick.parking, parking_note=pick.parking_note,
                reason=f"{g.minutes}분 공백을 {_GAP_KO.get(g.purpose, g.purpose)}(으)로 채움",
            ))
            insert_at += 1
            cursor = arrive + timedelta(minutes=stay)
            budget_left -= travel + stay

    # 끼워 넣은 뒤에는 반드시 시각을 다시 흘려보낸다. 이 단계가 없으면 삽입된
    # 항목의 시각이 앞뒤와 어긋난 채 남아, 목록이 시간순이 아니게 된다.
    day = it.date or (items[0].arrive.date() if items and items[0].arrive else None)
    day_end = (datetime.combine(day, c.end_time or DEFAULT_DAY_END) if day
               else datetime.max)
    items = _reflow(items, day_end)

    it.items = items
    it.total_dwell_min = sum(i.dwell_min for i in items)
    it.total_travel_min = sum(i.travel_min_from_prev for i in items)
    it.map_path = [i.geo for i in items if i.geo]
    # 마지막 장소가 바뀌었으므로 도착 안내를 다시 만든다. 이 줄이 없으면
    # schedule 이 2곳으로 만든 "2번째 장소 출발 → 부평역 도착"이 8곳이 된 뒤에도
    # 그대로 남아, 화면의 마지막 장소와 안내가 서로 다른 곳을 가리킨다.
    it.notes = _notes_from_conditions(c, items, it.destination)
    mix, fare = _mix(items)
    it.transport_mix, it.total_fare = mix, fare
    return {"itinerary": it, "trace": [f"itin.fill:{len(items)}"]}


_GAP_KO = {"meal": "식사", "rest": "휴식", "free": "여유 시간"}
