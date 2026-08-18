"""지도·좌표·이동시간 (NAVER Cloud Platform Maps + NAVER 검색 API + 무료 경로 API).

이동수단마다 출처가 다르다. 한 곳에서 다 되지 않기 때문이다.
  · 자가용            NAVER Directions 5 — 네이버 지도 앱과 같은 값이 나온다.
  · 도보              OpenRouteService (무료)
  · 지하철 / 버스        ODsay LAB (무료)
  · 키가 없으면        거리 기반 추정 (estimated=True)

자동차를 굳이 NAVER로 남겨 둔 이유는, 사용자가 '네이버 지도로 길안내 열기'를 눌렀을 때
앱에 뜨는 시간과 일정에 적힌 시간이 어긋나면 안 되기 때문이다.
구간마다 source 를 남겨 어느 엔진이 낸 값인지 UI가 밝힐 수 있게 한다.

자격증명이 두 종류라는 점도 주의.
  · NCP Maps (Geocoding/Directions) → x-ncp-apigw-api-key-id / x-ncp-apigw-api-key
  · NAVER Developers 검색(지역검색)  → X-Naver-Client-Id / X-Naver-Client-Secret
"""
from __future__ import annotations

import logging
from math import asin, cos, radians, sin, sqrt
from typing import Any

from app.config import get_settings
from app.schemas import GeoPoint
from app.tools.base import cache_key, cached
from app.tools.http import as_list, dig, get_json, to_float

logger = logging.getLogger(__name__)

Mode = str  # walk | subway | bus | car | bike
# '지하철+버스'는 수단이 아니라 조합이라 목록에서 뺐다 — 최단루트가 구간마다 섞는다.
TRANSIT_MODES = ("subway", "bus")

# 실측 경로가 없을 때 쓰는 평균 이동 속도(km/h). 도심 기준 보수적 값.
# 지하철이 버스보다 빠르고, 둘을 섞으면 그 사이에 놓인다.
# 도보 4.5km/h 는 성인 평균 보행속도(4~5km/h)의 보수적인 값이다.
# 신호 대기와 오르막을 감안해 낮게 잡았고, ORS 키가 있으면 실제 보행로
# 기준 값으로 대체된다.
FALLBACK_SPEED = {
    "walk": 4.5, "bike": 14.0, "bus": 14.0, "subway": 22.0, "car": 22.0,
}
# 대기·환승에 드는 고정 시간(분). 대중교통은 거리와 무관하게 이만큼 더 걸린다.
# 도보에는 대기 시간이 없다 — 걷기 시작하면 바로 간다.
FIXED_OVERHEAD = {"bus": 8, "subway": 7}
# 직선거리 → 실제 도로거리 보정 계수
DETOUR_FACTOR = 1.35

LOCAL_SEARCH_QUERY = {
    "food": "맛집", "cafe": "카페", "shop": "편집숍",
    "venue": "문화공간", "park": "공원", "event": "전시",
}


def _ncp_headers() -> dict[str, str] | None:
    s = get_settings()
    if not (s.naver_client_id and s.naver_client_secret):
        return None
    return {
        "x-ncp-apigw-api-key-id": s.naver_client_id,
        "x-ncp-apigw-api-key": s.naver_client_secret,
        "Accept": "application/json",
    }


async def _ncp_get(path: str, params: dict, *, ttl: float, name: str) -> Any:
    """NCP Maps 호출. 콘솔 세대에 따라 엔드포인트 호스트가 달라 1회 폴백한다."""
    headers = _ncp_headers()
    if headers is None:
        return None
    s = get_settings()
    for base in (s.ncp_maps_base_url, s.ncp_maps_base_url_alt):
        data = await get_json(f"{base}{path}", params=params, headers=headers,
                              ttl=ttl, name=name)
        if data is not None:
            return data
    return None


# ------------------------------------------------------------------ Geocoding
async def geocode(address: str) -> GeoPoint | None:
    """이름 또는 주소 → 좌표.

    두 단계로 찾는다.
      1) NCP Geocoding — **주소 전용**이다. '서울 강남구 영동대로 513'은 정확하지만
         '종각역'·'예술의전당'처럼 장소 이름은 검색 대상이 아니다.
      2) NAVER 지역검색(POI) — 역 이름·건물명·상호를 찾는다.

    사용자는 주소가 아니라 이름으로 말한다("판교역에서 출발"). 1단계만 두면
    말한 곳의 절반이 좌표를 못 얻고, 그러면 지도에도 일정에도 반영되지 않는다.
    """
    if not address:
        return None
    query = address.strip()

    data = await _ncp_get("/map-geocode/v2/geocode", {"query": query},
                          ttl=86400, name="naver.geocode")
    for first in as_list(dig(data, "addresses", default=[]))[:1]:
        lat, lng = to_float(first.get("y")), to_float(first.get("x"))
        if lat is not None and lng is not None:
            address = first.get("roadAddress") or first.get("jibunAddress") or query
            sido, sigungu = _admin_district(first, address)
            return GeoPoint(lat=lat, lng=lng, name=address,
                            sido=sido, sigungu=sigungu)

    return await place_lookup(query)


def _admin_district(first: dict, address: str) -> tuple[str | None, str | None]:
    """지오코딩 응답에서 행정구역을 뽑는다 (UR-18).

    `addressElements` 가 있으면 그것이 정답이다 — 주소 문자열을 다시 쪼개는 것보다
    확실하다. 다만 콘솔 세대에 따라 배열이 비어 오는 경우가 있어, 그때는 주소
    문자열을 판정한다. 둘 다 실패하면 `None` 이고, **모르는 것은 버리지 않는다**.
    """
    from app.tools import region as region_mod

    parts: dict[str, str] = {}
    for element in as_list(first.get("addressElements") or []):
        name = element.get("longName") or element.get("shortName") or ""
        for kind in as_list(element.get("types") or []):
            if name and kind in ("SIDO", "SIGUGU", "SIGUNGU"):
                parts.setdefault(kind, name)

    joined = " ".join(filter(None, [parts.get("SIDO"),
                                    parts.get("SIGUGU") or parts.get("SIGUNGU")]))
    sido, sigungu = region_mod.parse(joined)
    if sido or sigungu:
        return sido, sigungu
    return region_mod.parse(address)


async def place_lookup(query: str) -> GeoPoint | None:
    """장소 이름 → 좌표 (NAVER 지역검색).

    주소 API가 못 찾는 것들을 담당한다 — 역·공연장·미술관·상호.
    이름은 검색 결과의 정식 명칭으로 돌려준다. 사용자가 '판교역'이라고 썼는데
    화면에 '판교역 2번 출구'로 뜨면, 그게 우리가 실제로 찍은 지점이라는 뜻이다.
    """
    s = get_settings()
    if not (query and s.naver_search_client_id and s.naver_search_client_secret):
        return None

    data = await get_json(
        "https://openapi.naver.com/v1/search/local.json",
        params={"query": query, "display": 5, "sort": "random"},
        headers={
            "X-Naver-Client-Id": s.naver_search_client_id,
            "X-Naver-Client-Secret": s.naver_search_client_secret,
        },
        ttl=86400, name="naver.local.lookup",
    )
    for item in as_list(dig(data, "items", default=[])):
        point = _local_coords(item)
        if point:
            from app.tools import region as region_mod

            name = _strip_tags(item.get("title") or "") or query
            # 이름은 사용자에게 보일 정식 명칭이라 주소로 덮지 않는다. 대신
            # 행정구역만 주소에서 뽑아 따로 싣는다 — 이름으로 지역을 판정하면
            # '서울주문화센터'(울산)가 서울이 된다.
            sido, sigungu = region_mod.parse(
                item.get("roadAddress") or item.get("address"))
            logger.info("장소 '%s' → 지역검색 '%s' (%.4f, %.4f)%s",
                        query, name, point.lat, point.lng,
                        f" [{sido} {sigungu or ''}]".rstrip() if sido else "")
            return GeoPoint(lat=point.lat, lng=point.lng, name=name,
                            sido=sido, sigungu=sigungu)
    return None


async def reverse_geocode(point: GeoPoint) -> str | None:
    """좌표 → 주소. 현재 위치 표시와 지역 필터에 쓴다."""
    data = await _ncp_get("/map-reversegeocode/v2/gc", {
        "coords": f"{point.lng},{point.lat}",
        "orders": "roadaddr,addr",
        "output": "json",
    }, ttl=86400, name="naver.revgeocode")
    results = as_list(dig(data, "results", default=[]))
    if not results:
        return None
    r = results[0]
    region = r.get("region") or {}
    parts = [dig(region, f"area{i}", "name", default="") for i in range(1, 5)]
    land = r.get("land") or {}
    if land.get("name"):
        parts.append(land["name"])
    if land.get("number1"):
        parts.append(land["number1"])
    return " ".join(p for p in parts if p) or None


# ------------------------------------------------------------- Directions 5
async def route_duration(origin: GeoPoint, dest: GeoPoint, mode: Mode = "car") -> dict[str, Any]:
    """두 지점 사이 소요시간(분)·거리(m)·경로 좌표.

    반환: {"minutes", "distance_m", "path", "estimated", "source", ...}
    이동수단에 따라 호출처가 갈린다 — 모듈 최상단 설명 참고.
    """
    from app.tools import routing

    if mode in ("walk", "bike"):
        hit = await routing.walk_route(origin, dest, mode)
        return hit or _estimate(origin, dest, mode)

    if mode in TRANSIT_MODES:
        hit = await routing.transit_route(origin, dest, mode)  # type: ignore[arg-type]
        if hit:
            return hit
        # 경로가 없다고 몰래 도보로 바꾸지 않는다. 사용자가 '지하철'을 골랐는데
        # 결과에 도보가 섞여 있으면, 화면의 선택과 숫자가 어긋난다.
        # 대신 대기·환승을 포함한 추정으로 그 수단을 그대로 계산한다.
        return _estimate(origin, dest, mode)

    if mode != "car":
        return _estimate(origin, dest, mode)

    data = await _ncp_get("/map-direction/v1/driving", {
        "start": f"{origin.lng},{origin.lat}",
        "goal": f"{dest.lng},{dest.lat}",
        "option": "traoptimal",
    }, ttl=1800, name="naver.directions")

    if dig(data, "code") != 0:
        return _estimate(origin, dest, mode)

    routes = as_list(dig(data, "route", "traoptimal", default=[]))
    if not routes:
        return _estimate(origin, dest, mode)

    summary = routes[0].get("summary") or {}
    duration_ms = summary.get("duration") or 0
    toll = summary.get("tollFare")
    return {
        "minutes": max(1, round(duration_ms / 60000)),   # 응답 단위는 밀리초
        "distance_m": int(summary.get("distance") or 0),
        "path": routes[0].get("path") or [],
        "estimated": False,
        "source": "naver",
        "mode": "car",
        "fare": int(toll) if toll else None,          # 통행료
    }


async def travel_matrix(points: list[GeoPoint], mode: Mode = "subway",
                        *, estimate_only: bool = False) -> list[list[int]]:
    """N×N 이동시간(분) 행렬. 좌표 순서를 그대로 유지한다.

    자동차 모드에서는 실제 호출이 N² 회 발생하므로 상한을 둔다. 후보가 많을 때는
    거리 추정으로 1차 정렬한 뒤, 최종 일정에 들어간 구간만 실측하는 편이 비용상 낫다.
    """
    n = len(points)
    if n == 0:
        return []
    key = cache_key("maps.matrix",
                    {"p": [p.model_dump() for p in points], "m": mode, "e": estimate_only})
    return await cached(key, ttl=1800,
                        fn=lambda: _travel_matrix(points, mode, estimate_only))


async def _travel_matrix(points: list[GeoPoint], mode: Mode,
                         estimate_only: bool = False) -> list[list[int]]:
    import asyncio

    from app.tools import routing

    n = len(points)
    matrix = [[0] * n for _ in range(n)]
    # 실측을 쓸 수 있는 조건. N² 호출이라 지점 수에 상한을 둔다.
    use_api = not estimate_only and n <= 8 and (
        (mode == "car" and _ncp_headers() is not None)
        or routing.available(mode)
    )

    # 도보·자전거는 ORS Matrix 로 한 번에 받는다. N² 호출이 1회로 줄어
    # 무료 한도를 지키면서도 실측을 쓸 수 있다.
    if use_api and mode in ("walk", "bike"):
        grid = await routing.walk_matrix(points, mode)
        if grid:
            return grid
        logger.info("ORS 행렬 실패 — 거리 추정으로 대체합니다")
        use_api = False

    if not use_api:
        for i in range(n):
            for j in range(n):
                if i != j:
                    matrix[i][j] = _estimate(points[i], points[j], mode)["minutes"]
        return matrix

    pairs = [(i, j) for i in range(n) for j in range(n) if i != j]
    sem = asyncio.Semaphore(get_settings().verify_concurrency)

    async def one(i: int, j: int) -> None:
        async with sem:
            leg = await route_duration(points[i], points[j], mode)
            matrix[i][j] = leg["minutes"]

    await asyncio.gather(*(one(i, j) for i, j in pairs))
    return matrix


# --------------------------------------------------------------- 지역검색
async def search_nearby(anchor: GeoPoint, kind: str, radius_m: int = 800,
                        limit: int = 10) -> list[dict]:
    """주변 장소 검색. **카카오 Local 우선, NAVER 지역검색 폴백.**

    카카오는 `x`·`y`·`radius` 를 서버가 받고 `distance` 를 함께 준다 — 호출 한 번으로
    끝나고 반경이 정확하다. NAVER 지역검색에는 반경 파라미터가 없어서, 아래 폴백은
    좌표를 주소로 되돌린 뒤 키워드로 찾고 결과를 코드에서 거리로 잘라낸다.
    그래서 동(洞) 경계 밖의 가까운 가게가 목록에 아예 없을 수 있다.

    **둘을 합치지 않고 순차로 간다.** 같은 가게를 두 제공자가 다르게 표기해
    («스타벅스 서초점» / «스타벅스 서초») `merge_candidates` 의 이름 키가 갈리면
    같은 곳이 일정에 두 번 들어간다. 웹검색(Tavily → Exa)과 같은 방식이다.
    """
    from app.tools import kakao_local

    if kakao_local.enabled():
        rows = await kakao_local.search_nearby(anchor, kind, radius_m, limit)
        if rows:
            return rows
        # 0건이면 폴백으로 내려간다 — 반경이 좁아 비었을 수 있고,
        # 그때 NAVER 의 넓은 키워드 검색이 받아 주면 빈틈이 덜 남는다.

    s = get_settings()
    if not (s.naver_search_client_id and s.naver_search_client_secret):
        return []
    address = await reverse_geocode(anchor)
    region = " ".join((address or "").split()[:3])
    query = f"{region} {LOCAL_SEARCH_QUERY.get(kind, kind)}".strip()

    data = await get_json(
        "https://openapi.naver.com/v1/search/local.json",
        params={"query": query, "display": min(limit * 2, 30), "sort": "random"},
        headers={
            "X-Naver-Client-Id": s.naver_search_client_id,
            "X-Naver-Client-Secret": s.naver_search_client_secret,
        },
        ttl=1800, name="naver.local",
    )
    out: list[dict] = []
    for item in as_list(dig(data, "items", default=[])):
        point = _local_coords(item)
        if point is None:
            continue
        distance = _haversine_km(anchor, point) * 1000
        if distance > radius_m * 1.5:
            continue
        out.append({
            "name": _strip_tags(item.get("title", "")),
            "category": item.get("category"),
            "address": item.get("roadAddress") or item.get("address"),
            "lat": point.lat, "lng": point.lng,
            "url": item.get("link") or None,
            "distance_m": int(distance),
        })
    out.sort(key=lambda x: x["distance_m"])
    return out[:limit]


def _local_coords(item: dict) -> GeoPoint | None:
    """지역검색의 mapx/mapy는 WGS84를 10^7 배한 정수로 온다."""
    x, y = to_float(item.get("mapx")), to_float(item.get("mapy"))
    if x is None or y is None:
        return None
    if abs(x) > 1000:      # 정수 스케일
        x, y = x / 1e7, y / 1e7
    if not (124 < x < 132 and 33 < y < 39):   # 대한민국 범위 밖이면 버린다
        return None
    return GeoPoint(lat=y, lng=x)


def _strip_tags(text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", text)


# ---------------------------------------------------------------------- 추정
def _estimate(a: GeoPoint, b: GeoPoint, mode: Mode) -> dict[str, Any]:
    """실측 경로가 없을 때의 추정. 대중교통은 대기·환승 시간을 더한다.

    거리만으로 계산하면 '300m 옆 미술관까지 지하철 1분'처럼 실제로는 불가능한
    일정이 나온다. 승강장까지 걷고 기다리는 시간이 거리와 무관하게 붙기 때문이다.
    """
    km = _haversine_km(a, b) * DETOUR_FACTOR
    speed = FALLBACK_SPEED.get(mode, 18.0)
    minutes = int(km / speed * 60) + FIXED_OVERHEAD.get(mode, 0)
    return {
        "minutes": max(1, minutes),
        "distance_m": int(km * 1000),
        "path": [],
        "estimated": True,
        "source": "estimate",
        "mode": mode,
    }


def _haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    dlat, dlng = radians(b.lat - a.lat), radians(b.lng - a.lng)
    h = sin(dlat / 2) ** 2 + cos(radians(a.lat)) * cos(radians(b.lat)) * sin(dlng / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(h))
