"""카카오 Local — 좌표 기준 반경 검색.

**왜 NAVER 지역검색 대신 여기를 먼저 쓰는가.**
NAVER 지역검색에는 반경 파라미터가 없다. 그래서 기존 경로는 좌표를 주소로 되돌린 뒤
(`reverse_geocode`) «서울 서초구 반포동 카페» 같은 키워드로 찾고, 받은 결과를 코드에서
거리로 잘라냈다. 문제가 셋이었다.

  1) 호출이 두 번이다 (역지오코딩 + 검색).
  2) 동(洞) 단위 질의라 **앵커가 경계에 있으면 200m 옆 가게가 결과에 아예 없다.**
     반경 필터는 그걸 되살릴 수 없다 — 애초에 목록에 없기 때문이다.
  3) 반경을 `radius_m * 1.5` 로 느슨하게 잘랐다. 문서가 말한 «60분 미만 500m» 가
     실제로는 750m 였다.

카카오는 `x`·`y`·`radius` 를 서버가 받고 응답에 `distance` 를 함께 준다. 호출 한 번으로
끝나고 반경이 정확해진다 — 「가면 못 돌아오는 추천을 막는다」는 규칙이 문서대로 작동한다.

**두 엔드포인트를 나눠 쓰는 이유.** `category.json` 은 카테고리 코드가 있는 종류에만
쓸 수 있다. `shop`·`event` 처럼 대응 코드가 없는 종류는 `keyword.json` 으로 가는데,
이쪽도 `radius` 를 받으므로 위 이점은 그대로다.
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.schemas import GeoPoint
from app.tools.http import as_list, dig, get_json, to_float

logger = logging.getLogger(__name__)

_BASE = "https://dapi.kakao.com/v2/local/search"

# 카카오 category_group_code. 여기 있는 종류는 카테고리 검색이 훨씬 정확하다 —
# 키워드는 상호에 그 낱말이 있어야 걸리지만 코드는 업종 자체로 잡는다.
_CATEGORY_CODE = {
    "food": "FD6",     # 음식점
    "cafe": "CE7",     # 카페
    "venue": "CT1",    # 문화시설
    "park": "AT4",     # 관광명소 — 카카오에 '공원' 코드는 없다. 가장 가까운 축이다.
}

# 코드가 없는 종류는 키워드로 간다. maps.LOCAL_SEARCH_QUERY 와 같은 낱말을 쓴다 —
# 두 곳이 다른 말로 찾으면 제공자를 바꿨을 때 결과가 조용히 달라진다.
_KEYWORD = {
    "shop": "편집숍",
    "event": "전시",
}

# 키워드 검색에 업종 코드를 **함께** 걸어 좁히는 카테고리.
# 상호에 낱말만 있으면 걸리는 키워드 검색의 약점을 막는다 —
# «박물관» 으로 찾으면 «바움 제주커피박물관»(카페)이, «전시관» 으로 찾으면
# 상시 공간이 아닌 행사가 섞여 들어온다.
#
# ⚠️ **전부에 걸면 안 된다.** 독립서점과 공방은 문화시설(CT1) 업종이 아니라
#    서비스업·소매업으로 분류된다. 실측(서초역 3km, 2026-08-18):
#      독립서점 7건 → CT1 적용 시 **0건**
#      공방   362건 → CT1 적용 시 **0건**
#    이 둘은 «공공 데이터에 없는 곳»을 메우려고 넣은 카테고리라
#    (culture_api._search_always_on 의 설계 의도), 걸러 내면 그 자리가 통째로 빈다.
_KEYWORD_WITH_CT1 = frozenset({
    "미술관", "박물관", "전시관", "복합문화공간", "독립영화관",
})

# 카카오 category_name 은 «음식점 > 카페 > 커피전문점» 처럼 계층으로 온다.
# 마지막 조각이 가장 구체적이라 그걸 쓴다.
def _leaf_category(raw: str | None) -> str | None:
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(">") if p.strip()]
    return parts[-1] if parts else None


def enabled() -> bool:
    return bool(get_settings().kakao_rest_api_key)


async def search_nearby(anchor: GeoPoint, kind: str, radius_m: int = 800,
                        limit: int = 10) -> list[dict[str, Any]]:
    """앵커 반경 안의 장소. 반환 형태는 `maps.search_nearby` 와 같다.

    같은 dict 모양을 지키는 이유 — 호출부(`gaps.nearby_search`,
    `culture_api.search_always_on`)가 제공자를 몰라야 교체가 한 파일로 끝난다.
    """
    s = get_settings()
    if not s.kakao_rest_api_key:
        return []

    code = _CATEGORY_CODE.get(kind)
    params: dict[str, Any] = {
        "x": anchor.lng, "y": anchor.lat,
        # 카카오 상한은 20,000m. 넘겨 보내면 400 이라 여기서 자른다.
        "radius": min(max(int(radius_m), 1), 20000),
        "sort": "distance",
        "size": min(max(limit, 1), 15),      # 페이지당 상한 15
    }
    if code:
        path, params["category_group_code"] = "category", code
    else:
        query = _KEYWORD.get(kind, kind)
        path, params["query"] = "keyword", query
        # 문화시설 업종인 카테고리만 코드를 함께 건다. 나머지(공방·독립서점)는
        # 걸면 0건이 되므로 키워드만으로 간다 — 위 _KEYWORD_WITH_CT1 주석 참고.
        if query in _KEYWORD_WITH_CT1:
            params["category_group_code"] = "CT1"

    data = await get_json(
        f"{_BASE}/{path}.json",
        params=params,
        headers={"Authorization": f"KakaoAK {s.kakao_rest_api_key}"},
        ttl=1800, name=f"kakao.{path}",
    )

    out: list[dict[str, Any]] = []
    for item in as_list(dig(data, "documents", default=[])):
        lat, lng = to_float(item.get("y")), to_float(item.get("x"))
        if lat is None or lng is None:
            continue
        out.append({
            "name": item.get("place_name", ""),
            "category": _leaf_category(item.get("category_name")),
            "address": item.get("road_address_name") or item.get("address_name"),
            "lat": lat, "lng": lng,
            "url": item.get("place_url") or None,
            # 카카오가 계산해서 준다. 우리가 다시 재면 값이 갈린다.
            "distance_m": int(to_float(item.get("distance")) or 0),
        })
    # sort=distance 로 이미 정렬돼 오지만, 응답 형태가 바뀌어도 계약이 깨지지 않게 한 번 더 본다.
    out.sort(key=lambda x: x["distance_m"])
    return out[:limit]
