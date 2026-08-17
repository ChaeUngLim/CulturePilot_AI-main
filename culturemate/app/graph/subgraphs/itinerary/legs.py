"""구간 하나를 어느 수단으로 얼마에 가나 — 실측과 최단루트.

이동시간은 반드시 경로 API 값을 쓴다. LLM이 지어내면 «실행 가능한 일정» 이라는
전제가 무너지기 때문이다. 키가 없거나 예산이 모자라면 직선거리로 채우되
`travel_source='estimate'` 를 남긴다 — 값을 숨기는 것보다 추정이라고 밝히는 편이 낫다.

수단마다 출처가 다르다: 자동차 NAVER · 도보 OpenRouteService · 대중교통 ODsay.
"""
from __future__ import annotations

from itertools import pairwise

from app.graph.budget import COST_LEG_MEASURE
from app.schemas import GeoPoint, ItineraryItem
from app.tools import maps
from app.tools.base import safe_call
from app.tools.maps import _haversine_km


def _can_measure(mode: str) -> bool:
    """이 이동수단에 실측 경로 API가 붙어 있는지.

    자동차는 NAVER, 도보는 OpenRouteService, 대중교통은 ODsay.
    키가 없는 수단은 거리 기반 추정으로 떨어진다.
    """
    from app.tools import routing
    from app.tools.maps import _ncp_headers

    if mode == "car":
        return _ncp_headers() is not None
    if mode == "best":
        # 하나라도 실측할 수 있으면 최단루트를 계산할 가치가 있다
        return _ncp_headers() is not None or routing.available("walk") \
            or routing.available("transit")
    return routing.available(mode)


# 주차가 없는 곳은 자리를 찾고 걸어오는 시간이 붙는다. 이걸 안 넣으면
# 최단루트가 늘 자가용을 고르고, 정작 도착해서 20분을 헤매게 된다.
PARKING_PENALTY_MIN = {"none": 15, "nearby": 8, "paid": 3, "free": 0, "unknown": 5}
# 몇 분 차이 안 나면 걷는 쪽을 고른다 — 기다림도 환승도 없는 게 확실하다
WALK_PREFERENCE_MIN = 5
# 환승 1회의 부담. 계단·대기·놓칠 위험은 소요시간에 잘 안 잡힌다.
TRANSFER_PENALTY_MIN = 4


# 최단루트가 견줘 보는 수단 전부.
# '지하철+버스'는 넣지 않는다. 그건 수단이 아니라 두 수단의 조합이고,
# 조합하는 일 자체가 최단루트가 하는 일이다 — 구간마다 지하철이든 버스든
# 더 나은 쪽을 고르면 하루 전체로는 자연스럽게 섞인다.
BEST_CANDIDATE_MODES = ("walk", "subway", "bus", "car")


async def _fastest_leg(frm: GeoPoint, to: GeoPoint,
                       dest_parking: str = "unknown",
                       *, deadline: float | None = None) -> dict | None:
    """모든 수단을 재서 이 구간에 가장 좋은 것을 고른다(최단루트).

    시간만 보면 늘 자가용이 이긴다. 하지만 주차장을 찾고 거기서 걸어오는 시간은
    경로 API가 알려주지 않으므로, 도착지의 주차 사정을 시간으로 환산해 더한다.
    환승도 마찬가지다 — 갈아탈 때마다 계단을 오르고 기다리는 부담이 있는데
    소요시간에는 잘 반영되지 않아, 환승 1회당 몇 분을 얹어 견준다.
    또 몇 분 차이라면 걷는 쪽을 고른다 — 기다림도 환승도 없는 게 확실하다.
    """
    import asyncio

    # 수단 하나가 응답하지 않아도 나머지로 고를 수 있다. 예산을 넘겨 가며
    # 전부 모을 이유가 없다 — 실제로 ORS 도보가 12초를 태운 적이 있다.
    legs = await asyncio.gather(*(
        safe_call(f"maps.leg:{m}", maps.route_duration(frm, to, m), None, deadline=deadline)
        for m in BEST_CANDIDATE_MODES))
    options = [x for x in legs if x]
    if not options:
        return None

    def cost(leg: dict) -> float:
        minutes = float(leg["minutes"])
        if leg.get("mode") == "car":
            minutes += PARKING_PENALTY_MIN.get(dest_parking, 5)
        elif leg.get("mode") == "walk":
            minutes -= WALK_PREFERENCE_MIN      # 동점이면 걷는 쪽
        minutes += TRANSFER_PENALTY_MIN * int(leg.get("transfers") or 0)
        return minutes

    # 같은 수단이 여러 번 나올 수 있다(통합 조회가 '지하철'을 돌려주는 경우).
    # 수단별로 가장 좋은 것 하나만 남겨야 대안 목록이 중복되지 않는다.
    best_by_mode: dict[str, dict] = {}
    for leg in options:
        key = leg.get("mode") or "transit"
        if key not in best_by_mode or cost(leg) < cost(best_by_mode[key]):
            best_by_mode[key] = leg

    ranked = sorted(best_by_mode.values(), key=cost)
    pick = ranked[0]
    note = " / ".join(f"{TRANSPORT_KO.get(o.get('mode', ''), o.get('mode'))} {o['minutes']}분"
                      for o in ranked[1:4])
    return {**pick, "alternatives": note or None}


TRANSPORT_KO = {"walk": "도보", "car": "자가용", "subway": "지하철",
                "bus": "버스", "bike": "자전거"}


async def _best_leg(frm: GeoPoint, to: GeoPoint, preferred: str,
                    dest_parking: str = "unknown",
                    *, deadline: float | None = None) -> dict | None:
    """이 구간의 이동수단을 정한다.

    수단을 명시적으로 고른 경우에는 **바꾸지 않는다.** '도보'를 눌렀는데
    시스템이 몰래 지하철로 계산해 놓으면, 화면에는 도보라고 적혀 있으면서
    숫자는 지하철인 일정이 된다. 8km를 걸어 100분이 걸린다면 그게 도보의
    사실이고, 그 숫자를 보고 다른 수단으로 바꿀지는 사용자가 정할 일이다.

    수단을 섞는 건 '최단루트'(best)를 골랐을 때뿐이다 — 그게 그 모드의 정의다.
    """
    if preferred == "best":
        return await _fastest_leg(frm, to, dest_parking, deadline=deadline)
    return await safe_call(f"maps.leg:{preferred}",
                           maps.route_duration(frm, to, preferred), None,
                           deadline=deadline)


async def _measure_legs(items: list[ItineraryItem], mode: str, budget,
                        *, origin: GeoPoint | None = None) -> None:
    """확정된 구간만 경로 API로 실측한다.

    자동차는 NAVER, 도보는 OpenRouteService, 대중교통은 ODsay. 예산이나 키가 없으면
    직선거리로 채우고 travel_source='estimate' 를 남긴다 — 값을 숨기는 것보다
    '이건 추정입니다'라고 밝히는 편이 낫다.
    """
    import asyncio

    # 출발지 → 첫 장소도 구간이다. 여기가 비어 있으면 "집에서 얼마나 걸리나"를
    # 알 수 없어, 일정을 실행할지 판단하는 첫 번째 정보가 사라진다.
    pairs: list[tuple[GeoPoint, ItineraryItem]] = []
    if origin and items and items[0].geo:
        pairs.append((origin, items[0]))
    pairs += [(prev.geo, nxt) for prev, nxt in pairwise(items)
              if prev.geo and nxt.geo]
    if not pairs:
        return

    # best 는 모든 수단을 다 잰다. 단일 수단은 그 수단만 재므로 1회면 된다.
    per_leg = len(BEST_CANDIDATE_MODES) if mode == "best" else 1
    measurable = _can_measure(mode) and budget.allows(COST_LEG_MEASURE * per_leg * len(pairs))
    if not measurable:
        for frm, nxt in pairs:
            nxt.travel_km_from_prev = round(_haversine_km(frm, nxt.geo), 1)
        return

    results = await asyncio.gather(*(
        _best_leg(frm, n.geo, mode, n.parking, deadline=budget.deadline)
        for frm, n in pairs))

    for (frm, nxt), leg in zip(pairs, results, strict=False):
        if not leg:
            nxt.travel_km_from_prev = round(_haversine_km(frm, nxt.geo), 1)
            continue
        nxt.travel_min_from_prev = leg["minutes"]
        nxt.travel_km_from_prev = round(leg["distance_m"] / 1000, 1)
        nxt.travel_source = leg.get("source", "estimate")  # type: ignore[assignment]
        nxt.travel_transfers = leg.get("transfers")
        nxt.travel_fare = leg.get("fare")
        # 실제 경로 선형. 이게 없으면 지도가 두 점을 직선으로 이어, 지하철이
        # 한강을 가로질러 직진하는 그림이 된다.
        nxt.travel_path = _thin_path(leg.get("path") or [])
        # 고른 수단이 이 구간에 맞지 않아 바꾼 경우, 왜 바꿨는지까지 남긴다
        nxt.transport = leg.get("mode") or mode
        if leg.get("switched_reason"):
            nxt.reason = f"{nxt.reason or ''} · {leg['switched_reason']}".strip(" ·")
        # 왜 이 수단이 뽑혔는지 — 나머지 선택지의 소요시간을 같이 보여준다
        if leg.get("alternatives"):
            nxt.reason = f"{nxt.reason or ''} · 대안 {leg['alternatives']}".strip(" ·")


# 구간 하나가 수백 점이라, 4구간이면 좌표만 1,000개를 넘는다. 모바일에서
# 그만큼을 통째로 받으면 페이로드가 몇 배가 되는데 화면에서는 차이가 안 보인다.
MAX_PATH_POINTS = 120


def _thin_path(path: list) -> list[list[float]]:
    """경로 좌표를 [[lng, lat], …] 로 정규화하고 개수를 줄인다.

    제공자마다 형태가 다르다 — NAVER/ORS 는 [lng, lat] 배열, ODsay 는 이미
    우리가 [lng, lat] 로 맞춰 둔다. 어느 쪽이든 여기서 한 형태로 모은다.

    솎아낼 때 **처음과 끝은 반드시 남긴다.** 양 끝이 잘리면 선이 장소에서
    떨어진 채 시작해, 다른 길을 그린 것처럼 보인다.
    """
    pts: list[list[float]] = []
    for p in path:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            lng, lat = p[0], p[1]
        elif isinstance(p, dict):
            lng, lat = p.get("x") or p.get("lng"), p.get("y") or p.get("lat")
        else:
            continue
        if isinstance(lng, (int, float)) and isinstance(lat, (int, float)):
            pts.append([round(float(lng), 6), round(float(lat), 6)])

    if len(pts) <= MAX_PATH_POINTS:
        return pts
    step = len(pts) / (MAX_PATH_POINTS - 1)
    thinned = [pts[int(i * step)] for i in range(MAX_PATH_POINTS - 1)]
    thinned.append(pts[-1])
    return thinned


def _mix(items: list[ItineraryItem]) -> tuple[dict[str, int], int]:
    """수단별 소요시간 합과 요금 합. '무엇으로 얼마나 움직이는지'를 한 줄로 만들 재료."""
    mix: dict[str, int] = {}
    fare = 0
    for i in items:
        if i.travel_min_from_prev <= 0:
            continue
        key = i.transport or "unknown"
        mix[key] = mix.get(key, 0) + i.travel_min_from_prev
        fare += i.travel_fare or 0
    return mix, fare


def summarize_transport(mix: dict[str, int]) -> str:
    """'도보 22분 · 지하철 31분'. 최단루트가 실제로 무엇을 조합했는지 보여준다."""
    if not mix:
        return ""
    parts = sorted(mix.items(), key=lambda kv: kv[1], reverse=True)
    return " · ".join(f"{TRANSPORT_KO.get(k, k)} {v}분" for k, v in parts)
