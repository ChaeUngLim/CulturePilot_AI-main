"""도보·대중교통 경로 — 무료 API만 쓴다.

왜 별도 모듈인가.
  NAVER Cloud Maps의 경로 API는 Directions 5 / 15 둘뿐이고 **자동차 전용**이다.
  도보·지하철·버스 경로 API가 없다. 그래서 나머지 수단은 여기서 채운다.

  · 도보                OpenRouteService — 무료 2,500건/일, 카드 등록 없이 키 발급
  · 지하철 / 버스        ODsay LAB       — 무료 1,000건/일
  · 키가 없으면            거리 기반 추정 (estimated=True)

두 곳 모두 키가 선택이다. 키 없이도 앱이 돌아가야 하고, 추정값에는 estimated=True 가
붙어 UI가 '~'로 구분해 보여준다. 없는 정확도를 있는 척하지 않는 게 이 서비스의 전제다.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from app.config import get_settings
from app.schemas import GeoPoint
from app.tools.base import cache_key, cached
from app.tools.http import as_list, dig, get_json, post_json, to_float

logger = logging.getLogger(__name__)

ORS_URL = "https://api.openrouteservice.org/v2/directions"
ORS_MATRIX_URL = "https://api.openrouteservice.org/v2/matrix"
ODSAY_URL = "https://api.odsay.com/v1/api/searchPubTransPathT"
# 노선 선형 좌표. 경로검색과 별개 호출이라 쿼터를 두 배로 쓴다.
ODSAY_LANE_URL = "https://api.odsay.com/v1/api/loadLane"

# ORS 프로필. 도보 외에 자전거도 같은 방식으로 얻을 수 있다.
ORS_PROFILE = {"walk": "foot-walking", "bike": "cycling-regular", "car": "driving-car"}

# ODsay searchPathType: 1=지하철, 2=버스.
# '지하철+버스'(0=전체)는 쓰지 않는다 — 수단이 아니라 조합이고,
# 섞는 일은 최단루트가 구간마다 판단한다.
ODSAY_PATH_TYPE = {"subway": 1, "bus": 2}
TransitMode = Literal["subway", "bus"]


def ors_available() -> bool:
    return bool(get_settings().ors_api_key)


def odsay_available() -> bool:
    return bool(get_settings().odsay_api_key)


def available(mode: str) -> bool:
    """이 이동수단에 실측 경로를 붙일 수 있는지."""
    if mode in ("walk", "bike"):
        return ors_available()
    if mode in ODSAY_PATH_TYPE:
        return odsay_available()
    return False


# ------------------------------------------------- OpenRouteService (도보)
async def walk_route(origin: GeoPoint, dest: GeoPoint,
                     profile: str = "walk") -> dict[str, Any] | None:
    """보행자 경로. OSM 기반이라 보행 전용길·공원길·지하도가 반영된다."""
    if not ors_available():
        return None
    key = cache_key("ors.route",
                    {"o": origin.model_dump(), "d": dest.model_dump(), "p": profile})
    return await cached(key, ttl=3600, fn=lambda: _ors(origin, dest, profile))


def _ors_headers() -> dict[str, str]:
    return {
        "Authorization": get_settings().ors_api_key or "",
        "Content-Type": "application/json",
        "Accept": "application/json, application/geo+json",
    }


async def walk_matrix(points: list[GeoPoint],
                      profile: str = "walk") -> list[list[int]] | None:
    """N×N 이동시간 행렬을 **한 번의 호출**로 받는다.

    구간마다 directions 를 부르면 8지점에서 56회가 나가 무료 한도(분당 제한)를
    바로 넘긴다. ORS Matrix API는 같은 정보를 요청 1건으로 준다.
    """
    if not ors_available() or len(points) < 2:
        return None
    key = cache_key("ors.matrix", {"p": [p.model_dump() for p in points], "pr": profile})
    return await cached(key, ttl=1800, fn=lambda: _ors_matrix(points, profile))


async def _ors_matrix(points: list[GeoPoint], profile: str) -> list[list[int]] | None:
    name = ORS_PROFILE.get(profile, "foot-walking")
    data = await post_json(
        f"{ORS_MATRIX_URL}/{name}",
        json={
            "locations": [[p.lng, p.lat] for p in points],
            "metrics": ["duration"],
            "units": "m",
        },
        headers=_ors_headers(),
        ttl=1800, name="ors.matrix",
    )
    rows = as_list(dig(data, "durations", default=[]))
    if not rows:
        return None
    out: list[list[int]] = []
    for row in rows:
        out.append([0 if v is None else max(1, round(float(v) / 60))
                    for v in as_list(row)])
    return out


async def _ors(origin: GeoPoint, dest: GeoPoint, profile: str) -> dict[str, Any] | None:
    name = ORS_PROFILE.get(profile, "foot-walking")
    data = await post_json(
        f"{ORS_URL}/{name}/geojson",
        json={"coordinates": [[origin.lng, origin.lat], [dest.lng, dest.lat]]},
        headers=_ors_headers(),
        ttl=3600, name="ors.directions",
    )
    features = as_list(dig(data, "features", default=[]))
    if not features:
        return None
    summary = dig(features[0], "properties", "summary", default={}) or {}
    seconds = to_float(summary.get("duration"))
    meters = to_float(summary.get("distance"))
    if seconds is None or meters is None:
        return None
    return {
        "minutes": max(1, round(seconds / 60)),
        "distance_m": int(meters),
        "path": dig(features[0], "geometry", "coordinates", default=[]) or [],
        "estimated": False,
        "source": "ors",
        "mode": profile,
    }


# ------------------------------------------------------- ODsay (대중교통)
async def transit_route(origin: GeoPoint, dest: GeoPoint,
                        mode: TransitMode = "subway") -> dict[str, Any] | None:
    """대중교통 경로. mode 로 지하철만 / 버스만을 가른다."""
    if not odsay_available():
        return None
    key = cache_key("odsay.route",
                    {"o": origin.model_dump(), "d": dest.model_dump(), "m": mode})
    return await cached(key, ttl=1800, fn=lambda: _odsay(origin, dest, mode))


async def _odsay(origin: GeoPoint, dest: GeoPoint, mode: TransitMode) -> dict[str, Any] | None:
    data = await get_json(
        ODSAY_URL,
        params={
            "apiKey": get_settings().odsay_key,
            "SX": origin.lng, "SY": origin.lat,
            "EX": dest.lng, "EY": dest.lat,
            "SearchPathType": ODSAY_PATH_TYPE.get(mode, 1),
            "OPT": 0,          # 0=최단시간
        },
        ttl=1800, name="odsay.path",
    )
    # 출발지·도착지가 너무 가까우면 "결과 없음"이 온다. 오류가 아니다.
    paths = as_list(dig(data, "result", "path", default=[]))
    if not paths:
        return None

    info = paths[0].get("info") or {}
    minutes = to_float(info.get("totalTime"))
    if minutes is None:
        return None
    meters = to_float(info.get("totalDistance")) or 0
    walk_m = to_float(info.get("totalWalk")) or 0
    return {
        "minutes": max(1, round(minutes)),          # ODsay는 분 단위로 준다
        "distance_m": int(meters),
        "path": await _lane_path(info.get("mapObj")),
        "estimated": False,
        "source": "odsay",
        "mode": _mode_of(paths[0].get("pathType"), mode),
        "transfers": int(info.get("busTransitCount") or 0)
                     + int(info.get("subwayTransitCount") or 0),
        "fare": int(to_float(info.get("payment")) or 0) or None,
        "walk_min": round(walk_m / 67),             # 도보 약 4km/h → 67m/분
    }


async def _lane_path(map_obj: Any) -> list[list[float]]:
    """노선 선형 좌표. 경로검색이 준 mapObj 를 loadLane 에 넘겨 받는다.

    경로검색은 소요시간·환승만 주고 '어디를 지나는지'는 알려주지 않는다.
    그것 없이 지도를 그리면 출발지와 도착지를 잇는 직선이 되어, 지하철이
    한강을 가로질러 직진하는 그림이 나온다.

    호출이 한 번 더 나가므로(무료 1,000건/일) 확정된 구간에만 쓰고 캐시를 태운다.
    실패해도 조용히 빈 배열을 준다 — 선이 없을 뿐 시간·요금은 이미 확보돼 있다.
    """
    if not map_obj:
        return []
    data = await get_json(
        ODSAY_LANE_URL,
        params={"apiKey": get_settings().odsay_key, "mapObject": f"0:0@{map_obj}"},
        ttl=86400, name="odsay.lane",       # 노선 선형은 하루에 바뀌지 않는다
    )
    out: list[list[float]] = []
    for lane in as_list(dig(data, "result", "lane", default=[])):
        for section in as_list(lane.get("section")):
            for p in as_list(section.get("graphPos")):
                x, y = to_float(p.get("x")), to_float(p.get("y"))
                if x and y:
                    out.append([x, y])       # [lng, lat] — ORS/NAVER 와 같은 순서
    return out


def _mode_of(path_type: Any, requested: str) -> str:
    """ODsay pathType(1=지하철, 2=버스, 3=버스+지하철) → 우리 수단 이름.

    3(혼합)이 와도 '지하철+버스'로 부르지 않는다. 사용자가 고른 건 지하철이나
    버스 하나였고, 결과 라벨이 고른 것과 달라지면 화면이 어긋난다.
    """
    return {1: "subway", 2: "bus"}.get(path_type, requested)
