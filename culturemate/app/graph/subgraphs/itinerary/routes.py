"""이미 만들어진 일정을 다시 재는 것 — 그래프 밖에서 부른다.

셋 다 `api/main.py` 의 엔드포인트가 직접 호출하고, 그래프 노드가 아니다.
첫 응답과 분리한 이유가 전부다 — 응답 예산 15초 안에는 탐색·검증·편성이 다
들어가야 해서 구간 실측까지 넣을 자리가 없다. 그래서 첫 응답은 추정으로 빠르게
내보내고, 지도를 그린 다음 여기가 실제 선형을 채운다.

  · `reroute_itinerary` 장소는 그대로, 이동수단만 바꿔 다시 잰다
  · `measure_routes`   실제 경로 좌표까지 채운다 (자기 예산 60초)
  · `route_places`     좌표 묶음(큐레이션)을 하나의 동선으로 엮는다
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from time import monotonic

from app.graph.subgraphs.itinerary.legs import _measure_legs, _mix
from app.graph.subgraphs.itinerary.notes import endpoint_notes
from app.graph.subgraphs.itinerary.placement import DEFAULT_DAY_END
from app.graph.subgraphs.itinerary.schedule import _reflow
from app.schemas import GeoPoint, Itinerary, ItineraryItem
from app.tools.maps import _haversine_km


async def reroute_itinerary(itinerary: Itinerary, mode: str, *,
                            origin: GeoPoint | None = None,
                            destination: GeoPoint | None = None,
                            origin_name: str | None = None,
                            destination_name: str | None = None,
                            end_time: time | None = None) -> Itinerary:
    """장소는 그대로 두고 이동수단만 바꿔 동선을 다시 잰다.

    사용자가 '자가용 → 지하철'로 바꿨을 때 전체 그래프를 다시 돌리면 탐색·검증까지
    반복되어 15초가 또 든다. 바뀐 건 구간뿐이므로 구간만 다시 잰다.
    """
    from copy import deepcopy

    from app.graph.budget import Budget

    items = deepcopy(itinerary.items)
    if not items:
        return itinerary

    for item in items:
        item.transport = mode
    day = itinerary.date or (items[0].arrive.date() if items[0].arrive else None)
    day_end = (datetime.combine(day, end_time or DEFAULT_DAY_END) if day
               else (items[-1].depart or datetime.max))

    await _measure_legs(items, mode, Budget.start(), origin=origin)
    items = _reflow(items, day_end)

    mix, fare = _mix(items)
    dest_geo = destination or itinerary.destination
    dest_name = destination_name or itinerary.destination_name
    return Itinerary(
        id=itinerary.id, date=itinerary.date, items=items,
        total_travel_min=sum(i.travel_min_from_prev for i in items),
        total_dwell_min=sum(i.dwell_min for i in items),
        map_path=[i.geo for i in items if i.geo],
        # 수단이 바뀌면 도착지까지 걸리는 시간도 바뀐다. 옛 안내를 그대로 들고
        # 오면 '지하철로 바꿨는데 자가용 기준 도착 시각'이 남는다.
        notes=endpoint_notes(
            items, origin_name=origin_name or itinerary.origin_name,
            destination_name=dest_name, dest=dest_geo,
            transport=mode, end_time=end_time),
        version=itinerary.version + 1,
        transport_mode=mode, transport_mix=mix, total_fare=fare,
        origin=origin or itinerary.origin,
        destination=destination or itinerary.destination,
        origin_name=origin_name or itinerary.origin_name,
        destination_name=destination_name or itinerary.destination_name,
    )


async def measure_routes(itinerary: Itinerary, mode: str | None = None, *,
                         origin: GeoPoint | None = None,
                         end_time: time | None = None,
                         budget_s: float = 60.0) -> Itinerary:
    """이미 만들어진 일정의 구간을 실측해 **실제 경로 좌표까지** 채운다.

    첫 응답과 분리한 이유가 전부다. 응답 예산 15초 안에는 탐색·검증·편성이 다 들어가야
    해서 구간 실측까지 넣을 자리가 없고, 넣으면 예산이 밀려 실측이 통째로 잘린다.
    그래서 첫 응답은 거리 기반 추정으로 빠르게 내보내고, 지도를 그린 다음 이 함수가
    뒤이어 실제 선형을 채운다 — 화면의 직선이 노선을 따라가는 곡선으로 바뀐다.

    자기 예산을 따로 잡는다. 여기서 오래 걸려도 사용자는 이미 일정을 보고 있다.
    """
    from copy import deepcopy

    from app.graph.budget import Budget

    items = deepcopy(itinerary.items)
    if not items:
        return itinerary

    # 'unknown' 은 '수단을 안 골랐다'는 뜻이지 수단 이름이 아니다. 그대로 넘기면
    # `_can_measure('unknown')` 이 False 라 실측을 통째로 건너뛰고, 화면에는
    # 이동수단이 'unknown' 으로 찍힌다. ctx_geo 와 같은 규칙으로 맞춘다.
    mode = mode if mode and mode != "unknown" else (
        itinerary.transport_mode if itinerary.transport_mode not in (None, "unknown")
        else "best")
    day = itinerary.date or (items[0].arrive.date() if items[0].arrive else None)
    day_end = (datetime.combine(day, end_time or DEFAULT_DAY_END) if day
               else (items[-1].depart or datetime.max))

    await _measure_legs(items, mode, Budget(deadline=monotonic() + budget_s),
                        origin=origin or itinerary.origin)
    # 실측 결과로 시계를 다시 흘린다. 재는 것만 하고 시각을 그대로 두면 화면의
    # 도착 시각과 구간 시간이 서로 어긋난 채 남는다(§5.4).
    items = _reflow(items, day_end)

    mix, fare = _mix(items)
    return itinerary.model_copy(update={
        "items": items,
        "total_travel_min": sum(i.travel_min_from_prev for i in items),
        "total_dwell_min": sum(i.dwell_min for i in items),
        "map_path": [i.geo for i in items if i.geo],
        "transport_mix": mix, "total_fare": fare,
        "version": itinerary.version + 1,
    })


async def route_places(places: list[dict], mode: str, *,
                       origin: GeoPoint | None = None,
                       destination: GeoPoint | None = None,
                       start_time: time | None = None,
                       origin_name: str | None = None,
                       destination_name: str | None = None,
                       dwell_min: int | None = None) -> Itinerary:
    """좌표 묶음(큐레이션)을 하나의 동선으로 엮는다.

    큐레이션은 원래 '묶음'이지 '코스'가 아니다. 하지만 사용자가 이동수단을 고르면
    "이 테마를 이 수단으로 돌면 얼마나 걸리나"가 궁금해진 것이므로, 그때만
    가까운 순서로 이어 붙여 코스로 만든다.
    """
    from app.graph.budget import Budget

    pts = [p for p in places if p.get("lat") and p.get("lng")]
    if not pts:
        return Itinerary(items=[], map_path=[])

    geo = [GeoPoint(lat=p["lat"], lng=p["lng"], name=p["name"]) for p in pts]
    # 도착지가 있으면 거기서 가장 먼 곳부터 돌아 도착지 근처에서 끝나게 한다.
    order = _nearest_order(geo, origin, destination)

    items: list[ItineraryItem] = []
    for seq, idx in enumerate(order, 1):
        p = pts[idx]
        items.append(ItineraryItem(
            seq=seq, place_id=p.get("place_id"), name=p["name"],
            kind="venue", geo=geo[idx],
            dwell_min=dwell_min or int(p.get("dwell_min") or 0),
            travel_min_from_prev=0,
            transport=mode, indoor=p.get("indoor"),
            parking=p.get("parking") or "unknown",     # type: ignore[arg-type]
            parking_note=p.get("parking_note"),
        ))

    await _measure_legs(items, mode, Budget.start(), origin=origin)

    # 시작 시각을 주면 실제 도착 시각까지 채운다. 큐레이션도 "몇 시에 나가면
    # 몇 시에 끝나는지"를 알아야 실행할 수 있는 계획이 된다.
    if start_time:
        day = date.today()
        cursor = datetime.combine(day, start_time)
        for item in items:
            cursor += timedelta(minutes=item.travel_min_from_prev)
            item.arrive = cursor
            cursor += timedelta(minutes=item.dwell_min)
            item.depart = cursor

    # 문장은 endpoint_notes 한 곳에서만 만든다. 예전에는 여기에 같은 로직이
    # 한 벌 더 있었고, 한쪽만 고치면 화면마다 다른 문장이 나왔다.
    notes = endpoint_notes(items, origin_name=origin_name,
                           destination_name=destination_name,
                           dest=destination, transport=mode)

    mix, fare = _mix(items)
    return Itinerary(
        items=items, notes=notes,
        total_travel_min=sum(i.travel_min_from_prev for i in items),
        total_dwell_min=sum(i.dwell_min for i in items),
        map_path=[i.geo for i in items if i.geo],
        transport_mode=mode, transport_mix=mix, total_fare=fare,
        origin=origin, destination=destination,
        origin_name=origin_name, destination_name=destination_name,
    )


def _nearest_order(points: list[GeoPoint], origin: GeoPoint | None,
                   destination: GeoPoint | None = None) -> list[int]:
    """가까운 곳부터 잇는 greedy 순서. 최적해는 아니지만 지도에서 읽히는 선이 된다.

    도착지가 있으면 마지막 한 곳은 도착지에서 가장 가까운 곳으로 남겨 둔다.
    그래야 하루의 끝이 목적지 근처가 된다.
    """
    remaining = set(range(len(points)))
    last: int | None = None
    if destination and len(points) > 1:
        last = min(remaining, key=lambda i: _haversine_km(destination, points[i]))
        remaining.discard(last)

    here = origin or points[next(iter(remaining))]
    order: list[int] = []
    while remaining:
        nxt = min(remaining, key=lambda i: _haversine_km(here, points[i]))
        order.append(nxt)
        remaining.discard(nxt)
        here = points[nxt]
    if last is not None:
        order.append(last)
    return order
