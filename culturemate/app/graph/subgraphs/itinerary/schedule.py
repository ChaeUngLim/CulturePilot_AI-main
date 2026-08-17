"""방문 순서와 시각을 결정론적으로 정한다 (문서 04 가운데).

LLM은 배치 이유(reason) 서술만 맡고, 무엇을 언제 가는지는 전부 이 코드가 정한다.
지어낸 이동시간으로 짠 하루는 실행할 수 없기 때문이다.

두 종류를 함께 다룬다 — 사용자가 시각을 지정한 **고정 항목**(옮기지 않는다)과
남는 시간을 greedy nearest-feasible 로 채우는 **자유 항목**. 규모가 커지면
자유 배치만 OR-Tools VRPTW 로 교체하면 된다.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.config import get_settings
from app.graph.budget import from_state
from app.graph.state import ItineraryState
from app.graph.subgraphs.itinerary.dwell import _apply_dwell
from app.graph.subgraphs.itinerary.legs import _measure_legs, _mix
from app.graph.subgraphs.itinerary.notes import _notes_from_conditions
from app.graph.subgraphs.itinerary.placement import (
    DEFAULT_DAY_END,
    DEFAULT_DAY_START,
    SCHEDULE_POOL,
    _locked_places,
    _match_stop,
    _meal_slot,
    _quota_missing,
    _quota_room,
    _quota_take,
    _quota_unmet,
    _reserve_to_dest,
    _slots_left,
    _to_item,
    _travel_or_estimate,
    _within_hours,
)
from app.schemas import Evidence, GeoPoint, Itinerary, ItineraryItem
from app.tools import weather
from app.tools.maps import _haversine_km


# --------------------------------------------------------------------- 스케줄
async def schedule(state: ItineraryState) -> dict:
    """방문 순서와 시각을 결정론적으로 만든다.

    두 종류를 함께 다룬다.
      · 고정 항목 — 사용자가 "9시에 강남역 메가박스"처럼 시각을 지정한 것.
        스케줄러가 옮기지 않는다. 약속을 시스템이 임의로 바꾸면 쓸 수 없다.
      · 자유 항목 — 남는 시간에 greedy nearest-feasible 로 채운다.

    규모가 커지면 자유 배치만 OR-Tools VRPTW 로 교체하면 된다.
    """
    c = state["conditions"]
    ctx = state.get("context") or {}
    matrix: list[list[int]] = ctx.get("travel_matrix") or []
    points: list[GeoPoint] = ctx.get("points") or []
    # 편성은 행렬(상위 12개)보다 넓게 본다. 12개로 자르면 운영시간·이동 제약에
    # 걸려 서너 개만 통과했을 때 하루의 나머지를 채울 후보가 남지 않는다 —
    # 07~21시 열네 시간에 세 곳만 들어간 일정이 실제로 그렇게 나왔다.
    cands = (state.get("candidates") or [])[:SCHEDULE_POOL]

    # 날짜를 안 정했으면 '오늘'이다. UTC 날짜를 쓰면 컨테이너가 UTC로 도는 탓에
    # 자정~오전 9시 사이에 어제 날짜로 일정을 짠다.
    day = c.date or weather.today_kst()
    cursor = datetime.combine(day, c.start_time or DEFAULT_DAY_START)
    day_end = datetime.combine(day, c.end_time or DEFAULT_DAY_END)

    locked = _locked_places(state)
    # 행렬은 앞쪽 후보만 담고 있다. 그 범위를 넘는 후보는 인덱스를 주지 않아야
    # `_travel_or_estimate` 가 거리 추정으로 넘어간다 — 없는 칸을 읽으면 15분 고정이 된다.
    matrix_span = max(0, len(matrix) - (1 if c.origin else 0))
    idx_of = {cand.id: i + (1 if c.origin else 0)
              for i, cand in enumerate(cands[:matrix_span])}

    items: list[ItineraryItem] = []
    used: set[str] = set()
    pos = 0 if c.origin else None
    total_travel = 0

    # "5개 정도"라고 말했으면 그걸 따른다. 설정값은 상한일 뿐이다.
    max_stops = min(c.stop_count or get_settings().max_stops,
                    get_settings().max_stops)
    # 종류별 개수("문화 2개 + 디저트 3개"). 비어 있으면 그룹 제한 없이 총량만 본다.
    quota: dict[str, int] = dict(c.kind_quota or {})
    placed: dict[str, int] = {}
    # "장소마다 1~2시간"이라고 했으면 후보의 예상 체류시간을 그 범위로 맞춘다.
    # 카탈로그의 45분·130분을 그대로 쓰면 사용자가 말한 리듬과 어긋난 하루가 된다.
    # 아무 말도 없으면 과거 방문 기록의 평균 체류로 리듬을 맞춘다.
    _apply_dwell(cands, c, state.get("taste_profile"))
    dest = c.destination
    # 사용자가 식사를 원했는데 시각을 안 정한 경우. 문화 일정으로 하루를 꽉 채우면
    # 밥 먹을 자리가 사라지므로, 식사 시간대에 들어서면 식당을 우선한다.
    open_meals = {s.purpose for s in c.stops if s.at is None and s.purpose in ("meal", "cafe")}

    # 1) 시각이 지정된 요청을 먼저 배치한다. 이건 협상 대상이 아니다 —
    #    사용자가 잡아둔 약속을 시스템이 옮기면 일정을 신뢰할 수 없게 된다.
    for stop in sorted((s for s in c.stops if s.at), key=lambda s: s.at):
        if len(items) >= max_stops:
            break
        pick = _match_stop(stop, cands, used)
        if pick is None:
            continue
        arrive = datetime.combine(day, stop.at)
        travel = _travel_or_estimate(matrix, points, pos, pick, idx_of.get(pick.id))
        items.append(_to_item(pick, len(items) + 1, arrive, travel, c, ctx,
                              fixed=True, purpose=stop.purpose))
        used.add(pick.id)
        total_travel += travel
        pos = idx_of.get(pick.id)
        cursor = max(cursor, arrive + timedelta(minutes=pick.expected_dwell_min))

    # 도착지가 있으면 거기까지 가는 시간을 미리 빼 둔다.
    # "20시에 종로역 도착"이라고 했는데 19:50까지 일정을 채우면, 종로역에는
    # 20시에 도착할 수 없다. 마지막 이동시간만큼 하루를 일찍 끝내야 한다.
    plan_end = day_end
    if dest:
        reserve = _reserve_to_dest(cands, dest, c.transport)
        plan_end = day_end - timedelta(minutes=reserve)

    # 2) 남는 시간을 자유 항목으로 채운다
    while cursor < plan_end and len(items) < max_stops:
        best, best_score, best_travel = None, float("-inf"), 0
        # 앞으로 몇 자리가 더 남았는지. 도착지 인력의 세기를 여기서 정한다.
        slots_left = _slots_left(cands, used, cursor, day_end, max_stops - len(items))
        # 지금이 식사 시간대이고 아직 못 먹었다면 이 자리는 식당 자리다.
        # 남은 후보로만 판단해야 한다 — 이미 쓴 식당을 보고 "식당은 있다"고 여기면
        # 그 자리를 아무것도 채우지 못한 채 하루가 끝나 버린다.
        want = _meal_slot(cursor, open_meals, items)
        free = [x for x in cands if x.id not in used]
        # 사용자가 종류별 개수를 말했으면(문화 2 + 디저트 3) 다 찬 그룹은 뺀다.
        # 이 걸림이 없으면 점수만 높은 그룹이 자리를 다 가져가, "문화 2개"라고
        # 말했는데 문화만 여섯 곳이 나온다.
        if quota:
            free = [x for x in free if _quota_room(quota, placed, x.kind)]
            # 아직 못 채운 그룹의 후보가 남아 있으면 **그쪽을 먼저** 쓴다.
            hungry = [x for x in free if _quota_unmet(quota, placed, x.kind)]
            if hungry:
                free = hungry
            elif _quota_missing(quota, placed):
                # 못 채운 몫이 남았는데 후보가 하나도 없다 — 카페처럼 탐색이 아니라
                # 주변 검색에서 오는 종류다. 그 자리를 **비워 둔 채 끝낸다.**
                # 안 그러면 점수 높은 문화·상점이 남은 칸을 다 가져가고,
                # "디저트 3개"가 통째로 사라진다(빈틈 채우기가 채울 자리도 없다).
                break
        pool = [x for x in free if x.kind in want] if want else free
        if want and not pool:
            pool = free                 # 식당 후보가 없으면 자리를 비우기보다 채운다
            want = None
        for cand in pool:
            travel = _travel_or_estimate(matrix, points, pos, cand,
                                         idx_of.get(cand.id))
            arrive = cursor + timedelta(minutes=travel)
            depart = arrive + timedelta(minutes=cand.expected_dwell_min)
            if depart > plan_end or not _within_hours(cand, arrive, depart):
                continue
            score = cand.final_score - travel / 120.0
            # 도착지가 있으면 하루가 끝나갈수록 그쪽으로 끌어당긴다.
            # 가중치가 시간의 제곱으로 커지므로 오전에는 사실상 영향이 없고,
            # 마지막 자리에서만 결정적이 된다 — 좋은 장소를 거리만으로
            # 일찍부터 배제하면 '도착지 지정'이 일정 전체를 망친다.
            if dest and cand.geo and slots_left < 2.0:
                # 마지막 한 자리가 남았을 때만 작동한다. 그 전까지는 정확히 0 —
                # 도착지는 '하루를 어디서 끝낼지'의 제약이지, 후보 선별 기준이 아니다.
                pull = (2.0 - slots_left) * 1.5
                score -= _haversine_km(cand.geo, dest) / 20.0 * pull
            if cand.place_id in locked:
                score += 10.0
            if score > best_score:
                best, best_score, best_travel = cand, score, travel
        if best is None:
            break
        arrive = cursor + timedelta(minutes=best_travel)
        items.append(_to_item(best, len(items) + 1, arrive, best_travel, c, ctx,
                              purpose="meal" if want == ("food",) else
                                      "cafe" if want == ("cafe",) else "any"))
        used.add(best.id)
        _quota_take(placed, best.kind)
        total_travel += best_travel
        pos = idx_of.get(best.id)
        cursor = arrive + timedelta(minutes=best.expected_dwell_min)

    # 시각 순으로 정렬하고 번호를 다시 매긴다(고정 항목이 뒤늦게 끼어들 수 있다)
    items.sort(key=lambda i: i.arrive or datetime.max)
    for n, item in enumerate(items, 1):
        item.seq = n
    # 구간 실측. 일정에 남은 건 최대 6곳이라 5번만 호출하면 된다.
    # 후보 12개 전체에 대한 N² 행렬과 달리 값이 싸고, 여기서 나온 수치가
    # 사용자가 실제로 보는 '이동 22분 · 4.1km · 환승 1'이다.
    await _measure_legs(items, ctx.get("mode") or c.transport, from_state(state),
                        origin=c.origin)
    # 실측이 행렬 추정과 다르면 시각을 다시 흘려보낸다. 이 단계가 없으면
    # "이동 16분"이라고 적힌 구간의 실제 간격이 15분이 되어, 표시된 숫자대로
    # 움직이면 다음 일정에 늦는다. 숫자와 시각 중 하나는 반드시 거짓이 된다.
    items = _reflow(items, day_end)
    total_travel = sum(i.travel_min_from_prev for i in items)

    notes = _notes_from_conditions(c, items, dest)
    mix, fare = _mix(items)
    itinerary = Itinerary(
        date=day, items=items, total_travel_min=total_travel, notes=notes,
        total_dwell_min=sum(i.dwell_min for i in items),
        map_path=[i.geo for i in items if i.geo],
        version=(state.get("replan_round") or 0) + 1,
        transport_mode=ctx.get("mode") or c.transport,
        transport_mix=mix, total_fare=fare,
        origin=c.origin, destination=dest,
        origin_name=None if c.origin_missing else c.origin_name,
        destination_name=None if c.destination_missing else c.destination_name,
    )
    ev = [Evidence(kind="maps", title="이동시간 행렬",
                   text=f"{len(points)}개 지점, mode={c.transport}", confidence=0.9)]
    if dest and items:
        tail_km = _haversine_km(items[-1].geo, dest) if items[-1].geo else None
        ev.append(Evidence(
            kind="maps", title="도착지 정렬",
            text=(f"{c.destination_name or '도착지'} 기준 마지막 일정까지 "
                  f"{tail_km:.1f}km" if tail_km is not None
                  else f"{c.destination_name or '도착지'} 방향으로 정렬"),
            confidence=0.7))
    return {"itinerary": itinerary, "evidence": ev,
            "trace": [f"itin.schedule:{len(items)}"]}


def _reflow(items: list[ItineraryItem], day_end: datetime) -> list[ItineraryItem]:
    """실측 이동시간에 맞춰 도착·출발 시각을 다시 계산한다.

    고정 항목(사용자가 시각을 지정한 것)은 옮기지 않는다. 약속이기 때문이다.
    대신 그 앞 구간이 늘어나 도착이 늦어질 수 있는데, 그건 사용자가 알아야 할
    사실이므로 감추지 않고 reason 에 남긴다.

    다시 계산한 결과 하루 끝을 넘기는 항목은 잘라낸다 — 갈 수 없는 일정을
    남겨 두는 것보다 짧은 일정을 주는 편이 낫다.
    """
    if not items:
        return items

    out: list[ItineraryItem] = []
    cursor: datetime | None = None
    for item in items:
        travel = timedelta(minutes=item.travel_min_from_prev)
        if item.fixed_time and item.arrive:
            arrive = item.arrive
            if cursor is not None and cursor + travel > arrive:
                late = int(((cursor + travel) - arrive).total_seconds() // 60)
                item.reason = f"{item.reason or ''} · 앞 일정이 길어 {late}분 늦을 수 있음".strip(" ·")
        else:
            base = cursor if cursor is not None else (item.arrive or day_end)
            arrive = base + travel if cursor is not None else base
        depart = arrive + timedelta(minutes=item.dwell_min)
        if depart > day_end and not item.fixed_time:
            break                     # 하루를 넘기면 여기서 끊는다
        item.arrive, item.depart = arrive, depart
        out.append(item)
        cursor = depart

    for n, item in enumerate(out, 1):
        item.seq = n
    return out
