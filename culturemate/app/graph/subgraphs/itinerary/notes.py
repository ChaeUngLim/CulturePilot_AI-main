"""출발·도착을 문장으로 남긴다.

한 곳에 모아 둔 이유는 실제로 겪은 결함 때문이다 — 이 문장은 `items[0]` 과
`items[-1]` 에 묶여 있는데, 예전에는 만드는 코드가 세 벌 있었고 그중 하나만
고치면 화면마다 다른 문장이 나왔다. 게다가 `fill_gaps` 가 2곳을 8곳으로 늘린 뒤
다시 만들지 않아 «2번째 장소 출발 → 부평역 도착» 이 그대로 남았다.

**`items` 가 바뀌는 모든 지점에서 다시 부른다.** 그게 이 모듈의 유일한 규칙이다.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

from app.graph.subgraphs.itinerary.placement import _reserve_to_dest
from app.schemas import Candidate, GeoPoint, ItineraryItem


def endpoint_notes(items: list[ItineraryItem], *,
                   origin_name: str | None = None,
                   destination_name: str | None = None,
                   dest: GeoPoint | None = None,
                   transport: str = "best",
                   end_time: time | None = None,
                   origin_missing: bool = False,
                   destination_missing: bool = False) -> list[str]:
    """출발·도착을 문장으로 남긴다.

    사용자가 "20시에 종로역 도착"이라고 했으면, 그 약속이 지켜지는지가 일정을
    받아들일지 말지를 가른다. 지도에도 목록에도 안 나오는 값이므로 여기서 말한다.

    **`items` 가 바뀌면 반드시 다시 부른다.** 이 문장은 `items[0]` 과 `items[-1]` 에
    묶여 있어서, 한 번 만들고 들고 다니면 조용히 거짓이 된다. 실제로 `fill_gaps`
    가 공백을 채워 2곳이 8곳이 된 뒤에도 "2번째 장소 출발 → 부평역 도착"이 그대로
    남아 있었다. 그래서 조건 객체(TripConditions)가 아니라 **필요한 값만** 받도록
    풀어 두었다 — 조건을 들고 있지 않은 reroute·route_places 에서도 부를 수 있어야
    세 곳이 같은 문장을 쓰게 된다.
    """
    if not items:
        return []
    notes: list[str] = []
    # 이름은 말했는데 좌표를 못 찾은 경우. 지도에 안 나오는 이유를 밝힌다.
    if destination_missing and destination_name:
        notes.append(f"'{destination_name}' 을(를) 찾지 못해 도착지를 반영하지 "
                     "못했습니다 — 정확한 역·건물 이름이나 주소로 다시 말씀해 주세요")
    if origin_missing and origin_name:
        notes.append(f"'{origin_name}' 을(를) 찾지 못해 현재 위치로 계산했습니다 "
                     "— 정확한 역·건물 이름이나 주소로 다시 말씀해 주세요")
    first = items[0]
    if origin_name and first.arrive:
        notes.append(f"{origin_name} 출발 → {first.arrive:%H:%M} {first.name} 도착"
                     f" (이동 {first.travel_min_from_prev}분)")
    if dest and destination_name and items[-1].depart:
        last = items[-1]
        back = _reserve_to_dest([Candidate(id="x", name="x", geo=last.geo)], dest,
                                transport) if last.geo else 20
        eta = last.depart + timedelta(minutes=back)
        line = f"{last.name} 출발 → {eta:%H:%M} {destination_name} 도착 (이동 약 {back}분)"
        if end_time and eta.time() > end_time:
            over = int((eta - datetime.combine(eta.date(), end_time)).total_seconds() // 60)
            line += f" · 목표보다 {over}분 늦습니다"
        notes.append(line)
    return notes


def _notes_from_conditions(c, items: list[ItineraryItem],
                           dest: GeoPoint | None) -> list[str]:
    """조건 객체를 갖고 있는 호출부(schedule·fill_gaps)를 위한 얇은 래퍼."""
    return endpoint_notes(
        items, origin_name=c.origin_name, destination_name=c.destination_name,
        dest=dest, transport=c.transport, end_time=c.end_time,
        origin_missing=c.origin_missing, destination_missing=c.destination_missing)
