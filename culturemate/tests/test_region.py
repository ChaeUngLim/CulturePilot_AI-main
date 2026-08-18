"""행정구역 기반 지역 판정 (UR-18).

이 테스트가 지키는 것은 두 가지다.

  1. **거리로는 못 잡던 것을 잡는다.** 「서초구」 요청에 판교(경기 성남시)는
     25km라 반경 상한 60km를 통과한다. 실제로 서초구 요청의 1번 장소가 판교로
     나왔고, 그게 UR-18의 출발점이다.
  2. **이름을 지역으로 착각하지 않는다.** '세종문화회관'(서울)·'서울주문화센터'(울산)
     ·'제주도립미술관'. 이 함정은 이 저장소에서 이미 한 번 밟았다 —
     `culture_api._OTHER_REGIONS` 가 '세종'을 목록에서 빼야 했던 이유가 그것이다.
"""
from __future__ import annotations

from datetime import date, time

import pytest

from app.schemas import Candidate, GeoPoint, TripConditions
from app.tools import region

# 서울 서초구 예술의전당 / 경기 성남시 판교역. 직선거리 12km — 반경 상한(60km)은
# 둘을 구분하지 못한다. 그래서 좌표가 아니라 행정구역으로 가른다.
SEOCHO = GeoPoint(lat=37.4797, lng=127.0116, name="서울특별시 서초구 남부순환로 2406")
PANGYO = GeoPoint(lat=37.3947, lng=127.1113, name="경기도 성남시 분당구 판교역로 160")
GANGNAM = GeoPoint(lat=37.4979, lng=127.0276, name="서울특별시 강남구 테헤란로 1")


@pytest.mark.parametrize(("text", "expected"), [
    ("서울특별시 서초구 반포대로 2", ("서울", "서초구")),
    ("경기도 성남시 분당구 판교역로 235", ("경기", "성남시")),
    ("충청남도 천안시 서북구 불당대로", ("충남", "천안시")),
    ("대구광역시 중구 공평로", ("대구", "중구")),        # '중구'는 2자다
    ("강원도 춘천시", ("강원", "춘천시")),                # 개편 전 명칭도 데이터에 남아 있다
    ("서울 서초구", ("서울", "서초구")),                  # 요청 표현도 같은 파서로 읽는다
    ("서울", ("서울", None)),
    ("서초구 반포대로 2", (None, "서초구")),              # 시·도를 지어내지 않는다
    ("리움미술관, 서울특별시 용산구 이태원로", ("서울", "용산구")),
])
def test_parses_administrative_district(text, expected):
    assert region.parse(text) == expected


@pytest.mark.parametrize("name", [
    "세종문화회관",      # 서울 종로구. '세종'으로 읽으면 서울의 대표 공연장이 사라진다
    "서울주문화센터",    # 울산 울주군. '서울'로 읽으면 울산 시설이 서울에 들어온다
    "제주도립미술관",    # 붙여 쓴 고유명사. 우연히 맞더라도 규칙으로 삼으면 안 된다
    "경기도자박물관",
    "서울시립미술관",
])
def test_place_names_are_never_read_as_regions(name):
    """지역명을 앞머리로 쓰는 고유명사는 '모름'이어야 한다.

    모르면 통과하고 거리 상한이 받는다. 잘못 알면 멀쩡한 후보가 사라진다 —
    이 방향의 오류가 훨씬 비싸다.
    """
    assert region.parse(name) == (None, None)


def test_gyeonggi_gwangju_is_not_the_metro_city():
    """«경기도 광주시»가 광주광역시가 되면 안 된다.

    짧은 이름을 문자열 아무 곳에서나 찾으면 이 주소가 광주로 판정된다.
    시·도는 주소 맨 앞에서만, 또는 정식 명칭으로만 읽는 이유가 이것이다.
    """
    assert region.parse("경기도 광주시 오포읍")[0] == "경기"


def test_requested_regions_ignore_the_origin():
    """「판교역에서 출발해서 서초구」의 탐색 범위는 서초구다.

    출발지는 '어디서 시작하냐'이지 '어디서 찾냐'가 아니다.
    `discovery.region_points()` 가 앵커를 고를 때 쓰는 판단과 같아야 한다.
    """
    c = TripConditions(region="서울 서초구", regions=["서울 서초구"],
                       origin_name="판교역", origin=PANGYO)
    assert region.requested(c) == ({"서울"}, {"서초구"})


def test_no_requested_region_means_no_gate():
    """지역을 말하지 않으면 이 관문은 아무것도 하지 않는다 — 거리가 판단한다."""
    assert region.requested(TripConditions()) == (set(), set())


def test_candidate_district_comes_from_geo_then_address():
    """지오코딩 값이 우선, 없으면 주소. **이름은 보지 않는다.**"""
    geocoded = Candidate(id="a", name="세종문화회관",
                         geo=GeoPoint(lat=37.57, lng=126.97, sido="서울", sigungu="종로구"))
    assert region.of_candidate(geocoded) == ("서울", "종로구")

    addressed = Candidate(id="b", name="어떤 갤러리", address="경기도 성남시 분당구 판교역로",
                          geo=GeoPoint(lat=37.39, lng=127.11))
    assert region.of_candidate(addressed) == ("경기", "성남시")

    nameless = Candidate(id="c", name="세종문화회관", geo=GeoPoint(lat=37.57, lng=126.97))
    assert region.of_candidate(nameless) == (None, None)


def _conditions(**over) -> TripConditions:
    base = {
        "date": date(2026, 8, 20), "start_time": time(10, 0), "end_time": time(18, 0),
        "region": "서울 서초구", "regions": ["서울 서초구"],
        # 출발지를 판교로 둔다 — 반경 상한만 있으면 판교 장소가 오히려 유리한 배치다.
        "origin": PANGYO, "origin_name": "판교역",
    }
    base.update(over)
    return TripConditions(**base)


async def test_normalize_drops_other_sido_that_distance_lets_through():
    """UR-18의 본안 — 「서초구」 요청에 판교 장소가 남으면 안 된다.

    거리는 12km라 `MAX_ANCHOR_KM`(60km)를 통과한다. 걸러 내는 것은 시·도다.
    """
    from app.graph.subgraphs.discovery import normalize

    raw = [
        Candidate(id="pangyo", place_id="pangyo", name="판교 갤러리", geo=PANGYO,
                  address=PANGYO.name, relevance=0.9),
        Candidate(id="seocho", place_id="seocho", name="예술의전당", geo=SEOCHO,
                  address=SEOCHO.name, relevance=0.5),
    ]
    out = await normalize({"conditions": _conditions(), "user_id": "", "raw_candidates": raw})
    assert [c.name for c in out["candidates"]] == ["예술의전당"]


async def test_normalize_keeps_candidates_with_unknown_district():
    """행정구역을 모르는 후보는 남긴다.

    공공 문화 API는 주소를 안 주는 행사가 많다. 모르는 것까지 버리면 실제로 열리는
    행사가 통째로 사라지고, 그건 이 관문이 막으려던 것보다 나쁜 결과다.
    """
    from app.graph.subgraphs.discovery import normalize

    raw = [Candidate(id="x", place_id="x", name="어느 전시",
                     geo=GeoPoint(lat=SEOCHO.lat, lng=SEOCHO.lng))]
    out = await normalize({"conditions": _conditions(), "user_id": "", "raw_candidates": raw})
    assert [c.name for c in out["candidates"]] == ["어느 전시"]


async def test_named_gu_outranks_a_neighbouring_gu():
    """말한 구에 있는 곳이 앞에 온다. 다만 옆 구를 **자르지는** 않는다.

    구 경계는 생활권과 다르다. 서초구 요청에 200m 건너 강남구를 없는 곳으로
    취급하면 그것대로 틀린 결과가 된다 — 순서만 바꾼다.
    """
    from app.graph.subgraphs.discovery import normalize

    raw = [
        Candidate(id="gangnam", place_id="gangnam", name="강남 갤러리", geo=GANGNAM,
                  address=GANGNAM.name, relevance=0.6),
        Candidate(id="seocho", place_id="seocho", name="서초 갤러리", geo=SEOCHO,
                  address=SEOCHO.name, relevance=0.6),
    ]
    out = await normalize({"conditions": _conditions(), "user_id": "", "raw_candidates": raw})
    names = [c.name for c in out["candidates"]]
    assert names == ["서초 갤러리", "강남 갤러리"]


def test_geocoding_response_carries_the_district():
    """NCP 지오코딩의 `addressElements` 를 읽는다. 비어 오면 주소 문자열로 되돌린다."""
    from app.tools.maps import _admin_district

    payload = {
        "roadAddress": "서울특별시 서초구 남부순환로 2406",
        "addressElements": [
            {"types": ["SIDO"], "longName": "서울특별시"},
            {"types": ["SIGUGU"], "longName": "서초구"},
            {"types": ["ROAD_NAME"], "longName": "남부순환로"},
        ],
    }
    assert _admin_district(payload, payload["roadAddress"]) == ("서울", "서초구")

    empty = {"roadAddress": "경기도 성남시 분당구 판교역로 160", "addressElements": []}
    assert _admin_district(empty, empty["roadAddress"]) == ("경기", "성남시")
