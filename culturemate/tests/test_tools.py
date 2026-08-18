"""외부 연동 파서·변환 테스트. 네트워크·키 없이 돈다."""
from __future__ import annotations

import os
from datetime import datetime

os.environ.setdefault("LLM_BACKEND", "fake")

from app.schemas import Candidate, GeoPoint
from app.tools.culture_api import _extract_rows, to_candidate
from app.tools.http import as_list, dig, to_float, xml_to_dict
from app.tools.maps import _estimate, _local_coords, _strip_tags
from app.tools.verify import diff_against_snapshot, snapshot_of
from app.tools.weather import (
    latest_base,
    latlng_to_grid,
    risky_hours,
)


# ------------------------------------------------------------------ 기상청
def test_grid_conversion_matches_known_points():
    """기상청 공식 격자값. 틀리면 엉뚱한 지역 날씨로 일정을 짜게 된다."""
    assert latlng_to_grid(37.5665, 126.9780) == (60, 127)   # 서울시청
    assert latlng_to_grid(35.1796, 129.0756) == (98, 76)    # 부산시청
    assert latlng_to_grid(33.4996, 126.5312) == (53, 38)    # 제주시청


def test_base_time_rolls_back_before_first_publish():
    assert latest_base(datetime(2026, 8, 7, 1, 20)) == ("20260806", "2300")
    assert latest_base(datetime(2026, 8, 7, 14, 20)) == ("20260807", "1400")
    # 발표 직후 15분은 아직 데이터가 없어 이전 회차를 써야 한다
    assert latest_base(datetime(2026, 8, 7, 14, 5)) == ("20260807", "1100")


def test_risky_hours_flags_rain_and_high_pop():
    fc = {
        "10": {"condition": "clear", "pop": 10},
        "14": {"condition": "rain", "pop": 80},
        "17": {"condition": "cloudy", "pop": 70},
        "19": {"condition": "heat", "pop": 0},
    }
    assert set(risky_hours(fc)) == {"14", "17", "19"}


# ------------------------------------------------------------------- 문화 API
XML_SAMPLE = """<response><msgHeader><resultCode>00</resultCode></msgHeader><msgBody>
<perforList><title>모네 전시</title><startDate>20260801</startDate><endDate>20260830</endDate>
<place>대림미술관</place><realmName>미술</realmName><gpsX>126.9709</gpsX><gpsY>37.5757</gpsY>
<price>15000</price><url>https://example.com</url><area>서울</area></perforList>
<perforList><title>여름 축제</title><startDate>20260805</startDate><endDate>20260810</endDate>
<place>한강공원</place><realmName>축제</realmName><gpsX>126.93</gpsX><gpsY>37.52</gpsY></perforList>
</msgBody></response>"""


def test_parses_xml_response():
    rows = _extract_rows(xml_to_dict(XML_SAMPLE))
    assert len(rows) == 2
    c = to_candidate(rows[0])
    assert c.name == "모네 전시"
    assert c.kind == "event" and c.category == "전시"
    assert c.indoor is True and c.geo.lat == 37.5757
    assert str(c.period_end) == "2026-08-30"


def test_parses_json_response_with_uppercase_keys():
    payload = {"response": {"body": {"items": {"item": [
        {"TITLE": "재즈 공연", "REALMNAME": "음악", "GPSX": "127.0", "GPSY": "37.5"}]}}}}
    rows = _extract_rows(payload)
    assert len(rows) == 1
    c = to_candidate(rows[0])
    assert c.name == "재즈 공연" and c.category == "공연"


def test_festival_is_outdoor():
    rows = _extract_rows(xml_to_dict(XML_SAMPLE))
    assert to_candidate(rows[1]).indoor is False


def test_invalid_date_becomes_none_not_crash():
    assert to_candidate({"title": "x", "startDate": "20269931"}).period_start is None


# ---------------------------------------------------------------------- 지도
def test_local_search_coords_are_descaled():
    """지역검색 mapx/mapy는 WGS84를 10^7 배한 정수로 온다."""
    p = _local_coords({"mapx": "1269780000", "mapy": "375665000"})
    assert p and abs(p.lat - 37.5665) < 1e-4 and abs(p.lng - 126.978) < 1e-4


def test_local_search_rejects_out_of_country():
    assert _local_coords({"mapx": "0", "mapy": "0"}) is None


def test_strip_tags_removes_search_highlight():
    assert _strip_tags("<b>대림</b>미술관") == "대림미술관"


def test_estimate_is_mode_aware_and_marked():
    a, b = GeoPoint(lat=37.5665, lng=126.978), GeoPoint(lat=37.5796, lng=126.977)
    walk, car = _estimate(a, b, "walk"), _estimate(a, b, "car")
    assert walk["minutes"] > car["minutes"]      # 도보가 더 오래 걸려야 한다
    assert walk["estimated"] is True             # 추정치임이 표시돼야 한다


# ---------------------------------------------------------------- 재방문 diff
def test_snapshot_roundtrip_detects_changes():
    before = Candidate(name="대림미술관", place_id="p1", fee="10000",
                       closed_days=["월"], address="종로구")
    snap = snapshot_of(before)

    after = before.model_copy(deep=True)
    after.fee = "15000"
    after.closed_days = ["월", "화"]

    diffs = diff_against_snapshot(after, snap, datetime(2025, 11, 16))
    fields = {d.field for d in diffs}
    assert "fee" in fields and "closed_days" in fields
    assert "location" not in fields          # 안 바뀐 항목은 나오면 안 된다
    fee_diff = next(d for d in diffs if d.field == "fee")
    assert fee_diff.before == "10000" and fee_diff.after == "15000"


def test_whitespace_only_change_is_not_a_diff():
    before = Candidate(name="x", place_id="p1", fee="10,000 원")
    snap = snapshot_of(before)
    after = before.model_copy(deep=True)
    after.fee = "10,000원"
    assert diff_against_snapshot(after, snap, None) == []


def test_no_snapshot_means_no_diff():
    assert diff_against_snapshot(Candidate(name="x", place_id="p1"), None, None) == []


# ---------------------------------------------------------------- http 유틸
def test_dig_and_as_list_survive_shape_changes():
    assert dig({"a": {"b": {"c": 1}}}, "a", "b", "c") == 1
    assert dig({"a": {}}, "a", "b", "c", default="x") == "x"
    assert as_list({"k": 1}) == [{"k": 1}]
    assert as_list([1, 2]) == [1, 2]
    assert as_list(None) == []
    assert to_float(" 37.5 ") == 37.5
    assert to_float("없음") is None


# ---------------------------------------------------------------- 라우터 규칙
def test_region_detection_survives_particles_and_short_forms():
    """LLM이 실패해도 지역을 건져야 탐색이 0건이 되지 않는다."""
    from app.graph.router import _rule_conditions

    assert _rule_conditions("서대문구 문화생활 일정").region == "서울 서대문구"
    assert _rule_conditions("종로구에서 하루 일정").region == "서울 종로구"   # 조사
    assert _rule_conditions("강남 문화공연 추천해줘").region == "서울 강남구"  # 구 생략
    assert _rule_conditions("성수동 데이트 코스").region == "서울 성동구"      # 동네명
    assert _rule_conditions("부산 전시 추천").region == "부산"
    assert _rule_conditions("오늘 뭐하지").region is None


def test_longer_district_name_wins():
    """'중구'와 '중랑구'가 겹친다 — 긴 이름을 먼저 봐야 한다."""
    from app.graph.router import _rule_conditions

    assert _rule_conditions("중랑구 가볼만한 곳").region == "서울 중랑구"
    assert _rule_conditions("중구 전시").region == "서울 중구"


def test_explicit_date_is_parsed():
    from datetime import date

    from app.graph.router import _rule_conditions

    c = _rule_conditions("8월 11일 일정 만들어줘")
    assert c.date is not None and (c.date.month, c.date.day) == (8, 11)
    assert _rule_conditions("2026년 12월 25일").date == date(2026, 12, 25)


def test_transport_and_companion_are_extracted():
    from app.graph.router import _rule_conditions

    c = _rule_conditions("차 갖고 가족과 종로구 하루 일정")
    assert c.transport == "car" and c.companions == "family"


# ------------------------------------------------------------ 지점 vs 구 인식
def test_landmark_wins_over_district():
    """'신촌역 근처'를 서대문구 전체로 넓히면 도보로 못 가는 곳이 추천된다."""
    from app.graph.router import _rule_conditions

    c = _rule_conditions("신촌역 근처 축제 및 문화생활 추천해줘")
    assert c.landmark == "신촌역"
    assert c.radius_m == 1200          # 지점 기준이면 도보권으로 좁힌다


def test_landmark_not_swallowed_by_region_name():
    """'서울숲'이 '서울'로 뭉개지면 안 된다."""
    from app.graph.router import _rule_conditions

    c = _rule_conditions("서울숲 앞에서 오늘 뭐하지")
    assert c.landmark == "서울숲"
    assert c.regions == []


def test_landmark_suffix_is_not_hardcoded():
    """접미사 목록에 없는 지점도 잡혀야 한다."""
    from app.graph.router import _rule_conditions

    assert _rule_conditions("경복궁 근처 박물관").landmark == "경복궁"
    assert _rule_conditions("성수동 근처 데이트 코스").landmark == "성수동"


def test_explicit_radius_overrides_default():
    from app.graph.router import _rule_conditions

    assert _rule_conditions("홍대입구역 주변 도보 10분 전시").radius_m == 750
    assert _rule_conditions("경복궁 근처 반경 2km 박물관").radius_m == 2000


def test_multiple_districts_are_all_captured():
    """구는 넓은 필터 — 여러 개를 함께 고를 수 있어야 한다."""
    from app.graph.router import _rule_conditions

    c = _rule_conditions("서대문구랑 마포구 8월11일 일정")
    assert c.regions == ["서울 서대문구", "서울 마포구"]
    assert c.landmark is None


def test_transport_modes_are_distinguished():
    """지하철만 / 버스만 / 지하철+버스는 서로 다른 수단이다."""
    from app.graph.router import _rule_conditions

    cases = {
        "도보로 다닐 수 있는 일정": "walk",
        "지하철로만 이동하는 일정": "subway",
        "버스 타고 갈 만한 전시": "bus",
        "대중교통으로 종로 문화생활": "best",
        "자가용으로 강남 전시": "car",
        "뚜벅이 여행 코스": "walk",
    }
    for query, expected in cases.items():
        assert _rule_conditions(query).transport == expected, query

    # '차 마시러'는 자동차가 아니다
    assert _rule_conditions("차 마시러 갈 카페 추천").transport != "car"


def test_transit_estimate_includes_waiting_time():
    """대중교통 추정에는 대기·환승 시간이 붙는다.

    거리만으로 계산하면 '300m 옆까지 지하철 1분' 같은 불가능한 일정이 나온다.
    """
    from app.schemas import GeoPoint
    from app.tools.maps import _estimate

    near_a = GeoPoint(lat=37.4979, lng=127.0276)
    near_b = GeoPoint(lat=37.5010, lng=127.0300)     # 약 400m

    walk = _estimate(near_a, near_b, "walk")
    bus = _estimate(near_a, near_b, "bus")
    subway = _estimate(near_a, near_b, "subway")

    # 짧은 거리에서는 걷는 게 대중교통보다 빠르다 — 현실과 맞아야 한다
    assert walk["minutes"] < bus["minutes"]
    assert walk["minutes"] < subway["minutes"]
    assert all(r["estimated"] and r["source"] == "estimate" for r in (walk, bus, subway))


def test_mode_speed_ordering_holds_over_distance():
    """먼 거리에서는 자동차 < 지하철 < 버스 < 도보 순으로 오래 걸린다."""
    from app.schemas import GeoPoint
    from app.tools.maps import _estimate

    a, b = GeoPoint(lat=37.4979, lng=127.0276), GeoPoint(lat=37.5665, lng=126.9780)
    car, subway, bus, walk = (
        _estimate(a, b, m)["minutes"] for m in ("car", "subway", "bus", "walk"))
    assert car < subway < bus < walk


async def test_routing_falls_back_without_key():
    """경로 API 키가 없으면 조용히 추정으로 내려간다 — 앱이 멈추면 안 된다."""
    from app.schemas import GeoPoint
    from app.tools import maps

    a, b = GeoPoint(lat=37.4979, lng=127.0276), GeoPoint(lat=37.5665, lng=126.9780)
    for mode, sources in (("subway", ("odsay", "estimate")),
                          ("bus", ("odsay", "estimate")),
                          ("walk", ("ors", "estimate"))):
        leg = await maps.route_duration(a, b, mode)
        assert leg["minutes"] > 0
        assert leg["source"] in sources, (mode, leg["source"])


def test_routing_uses_only_free_providers():
    """경로 API는 NAVER · OpenRouteService · ODsay 세 가지뿐이다.

    소스뿐 아니라 스크립트·모바일·설정 파일까지 훑는다. 유료 제공자가 되살아나는
    자리는 대개 코드가 아니라 그쪽이다 — 키를 먼저 넣어 보고 배선은 나중에 하니까.
    """
    import pathlib

    from tests.conftest import NON_FREE_ROUTING_PROVIDERS

    root = pathlib.Path(__file__).resolve().parents[1]
    targets = [
        *(root / "app").rglob("*.py"),
        *(root / "scripts").rglob("*.py"),
        *(root / "mobile" / "src").rglob("*.ts"),
        *(root / "mobile" / "src").rglob("*.tsx"),
        *(root / "mobile" / "app").rglob("*.tsx"),
        *[p for p in (root / ".env.example", root / "docker-compose.yml") if p.exists()],
    ]
    hits = [
        (str(f.relative_to(root)), name)
        for f in targets if "node_modules" not in f.parts
        for name in NON_FREE_ROUTING_PROVIDERS
        if name in f.read_text(encoding="utf-8", errors="ignore").lower()
    ]
    assert not hits, f"무료가 아닌 경로 제공자 참조가 있다: {hits}"


def test_free_routing_providers_are_wired():
    """세 제공자가 실제로 배선돼 있는지 — 위 테스트의 반쪽.

    '유료가 없다'만 검사하면 전부 지워도 통과한다. 있어야 할 것이 있는지도 본다.
    """
    from app.tools import maps, routing

    assert routing.ORS_URL and routing.ORS_MATRIX_URL      # 도보·자전거
    assert routing.ODSAY_URL                                # 지하철·버스
    assert routing.ODSAY_PATH_TYPE == {"subway": 1, "bus": 2}
    assert maps.FALLBACK_SPEED and maps.DETOUR_FACTOR       # 실측 불가 시 추정


def test_bare_hour_start_is_morning_when_it_would_outrun_the_end():
    """"7시 출발 … 21시 도착"의 7시는 아침이다.

    표시 없는 1~7시를 오후로 보는 규칙이 출발 시각에서는 반대로 작동한다.
    그대로 두면 19시 출발/21시 종료가 되어 창이 2시간으로 줄고, 일정이 한 곳도
    못 들어간 채 빈 결과가 나간다 — 사용자는 왜 비었는지 알 수 없다.
    """
    from datetime import time

    from app.graph.router import _rule_conditions

    c = _rule_conditions("판교역에서 7시 출발 청계산역 21시 도착 강남 전시 3곳")
    assert c.start_time == time(7, 0), c.start_time
    assert c.end_time == time(21, 0), c.end_time

    # 되돌리는 조건은 '출발이 종료보다 늦을 때'뿐이다. 오후 약속은 건드리지 않는다.
    c2 = _rule_conditions("5시에 강남에서 만나 8시까지 전시 보기")
    assert c2.start_time is None or c2.start_time.hour >= 12


def test_until_phrase_becomes_the_end_time():
    """'저녁 7시까지'·'8시까지'도 하루의 끝이다.

    `_END_WORD`(도착·종료·귀가…)에만 기대면 '까지'로만 말한 종료 시각이 통째로 빠져,
    사용자가 시각을 말했는데도 기본값 20:00 으로 하루를 잡는다.
    """
    from datetime import time

    from app.graph.router import _rule_conditions

    c = _rule_conditions("내일 오전 10시부터 저녁 7시까지 서울에서 전시")
    assert c.end_time == time(19, 0), c.end_time

    # '8시까지'는 오전으로 읽히지만, 시작이 오후면 저녁이다.
    c2 = _rule_conditions("5시에 강남에서 만나 8시까지 전시")
    assert (c2.start_time, c2.end_time) == (time(17, 0), time(20, 0))


def test_duration_phrase_is_not_a_landmark():
    """'2시간 남는데 근처'의 '2시간 남는데'는 지점이 아니다.

    지점으로 잡히면 그 문자열로 지오코딩을 시도하고, 실패하면 탐색 범위가 사라진다.
    """
    from app.graph.router import _rule_conditions

    c = _rule_conditions("전시 일찍 끝났어 2시간 남는데 근처 뭐 있어?")
    assert c.landmark is None, c.landmark


def test_origin_and_destination_are_split_from_the_query():
    """'09시에 출발'의 09시는 방문할 장소가 아니라 하루의 시작 시각이다."""
    from app.graph.router import _rule_conditions

    c = _rule_conditions(
        "양재역 낮 09시에서 출발해서 강남역 문화 및 식사 스케쥴 5개 정도 만들어주고 "
        "밤 20시에 종로역에서 도착하는 스케쥴 만들어줘")
    from datetime import time

    assert c.origin_name == "양재역"
    assert c.start_time == time(9, 0)
    assert c.destination_name == "종로역"
    assert c.end_time == time(20, 0)
    assert c.stop_count == 5
    assert c.regions == ["서울 강남구"]
    # 출발·도착이 방문 장소로 새어 들어가면 안 된다
    hints = [s.place_hint for s in c.stops]
    assert "양재역" not in hints and "종로역" not in hints


def test_particles_are_stripped_only_from_the_tail():
    """'서울역에서' → '서울역'. 앞글자까지 깎으면 '울역'이 된다."""
    from app.graph.router import _rule_conditions

    c = _rule_conditions("홍대입구역에서 출발해서 3곳 정도 돌고 서울역에서 마무리")
    assert c.origin_name == "홍대입구역"
    assert c.destination_name == "서울역"
    assert c.stop_count == 3


def test_endpoints_are_optional():
    """출발·도착을 말하지 않아도 나머지 조건은 그대로 잡혀야 한다."""
    from app.graph.router import _rule_conditions

    c = _rule_conditions("강남 내일 문화생활과 식사 알아서 스케쥴 만들어줘")
    assert c.origin_name is None and c.destination_name is None
    assert c.regions == ["서울 강남구"]
    assert any(s.purpose == "meal" for s in c.stops)


def test_korean_number_count():
    from app.graph.router import _rule_conditions

    c = _rule_conditions("오전 10시 서울시청 출발, 다섯 곳 보고 저녁 7시 잠실역 도착")
    from datetime import time

    assert c.stop_count == 5
    assert c.origin_name == "서울시청" and c.start_time == time(10, 0)
    assert c.destination_name == "잠실역" and c.end_time == time(19, 0)


def test_return_time_is_not_a_visit():
    """'밤 10시까지 귀가'의 10시는 방문할 장소의 지정 시각이 아니다.

    이걸 놓치면 그 하나만 22:00에 고정된 채 나머지 일정이 통째로 밀려나,
    결과가 한 줄만 나온다 — 실제로 겪은 버그다.
    """
    from datetime import time

    from app.graph.router import _rule_conditions

    c = _rule_conditions(
        "양재역 아침9시부터 출발해서 강남역 근처에서 문화 및 공연 및 디져트나 식사 "
        "포함해서 5가지 추천해주고 밤 10시까지 종로역에서 귀가할꺼야")

    assert c.origin_name == "양재역" and c.start_time == time(9, 0)
    assert c.destination_name == "종로역" and c.end_time == time(22, 0)
    assert c.stop_count == 5
    assert c.landmark == "강남역"
    # 22:00 짜리 고정 항목이 생기면 안 된다
    assert all(s.at != time(22, 0) for s in c.stops)
    assert any(s.purpose == "meal" for s in c.stops)


def test_clause_time_is_the_nearest_one():
    """'9시에 나가서 … 저녁 8시에 퇴근' — 퇴근 시각은 8시다."""
    from datetime import time

    from app.graph.router import _rule_conditions

    c = _rule_conditions("9시에 나가서 3곳 보고 저녁 8시에 퇴근")
    assert c.start_time == time(9, 0)
    assert c.end_time == time(20, 0)
    assert c.stop_count == 3


def test_various_return_expressions():
    """하루의 끝을 뜻하는 말은 넉넉히 잡는다 — 빠뜨리면 일정이 무너진다."""
    from app.graph.router import _rule_conditions

    for word in ("귀가할꺼야", "복귀할래", "돌아갈 거야", "퇴근", "마무리"):
        c = _rule_conditions(f"강남 문화생활 추천, 저녁 8시에 서울역에서 {word}")
        assert c.destination_name == "서울역", word
        assert c.end_time is not None, word


async def test_spoken_origin_beats_gps_location():
    """'판교역에서 출발'이라고 말했으면 GPS 현재 위치를 이긴다.

    클라이언트는 GPS 좌표를 늘 함께 보낸다. 좌표가 이미 있다는 이유로
    지오코딩을 건너뛰면, 사용자가 말한 출발지가 통째로 무시되고
    지도에도 반영되지 않는다 — 실제로 겪은 버그다.
    """
    from unittest.mock import patch

    from app.graph.router import _apply_override, _rule_conditions
    from app.graph.subgraphs.discovery import resolve_origin
    from app.schemas import GeoPoint

    coords = {"판교역": (37.3947, 127.1112), "종로역": (37.5704, 126.9921)}

    async def fake_geocode(q):
        for name, (la, lo) in coords.items():
            if name in q:
                return GeoPoint(lat=la, lng=lo, name=name)
        return None

    c = _rule_conditions(
        "판교역에서 아침 9시에 출발해서 강남역에서 문화생활 및 식사와 디져트로 "
        "5개 정도 추천해주고 종로역에서 밤 10시에 도착하는 것으로 스케쥴 만들어줘")
    # 클라이언트가 늘 함께 보내는 GPS 현재 위치. 발화에 출발지가 있으므로
    # 이 좌표는 주입되지 않는다 — 방금 한 말이 화면 상태를 이긴다.
    c = _apply_override(c, {"origin": {"lat": 37.5794, "lng": 126.9368}})
    assert c.origin is None and c.origin_name == "판교역"

    with patch("app.tools.maps.geocode", fake_geocode):
        await resolve_origin(c)

    assert (round(c.origin.lat, 4), round(c.origin.lng, 4)) == (37.3947, 127.1112)
    assert c.destination is not None
    assert (round(c.destination.lat, 4), round(c.destination.lng, 4)) == (37.5704, 126.9921)


async def test_landmark_does_not_override_spoken_origin():
    """'판교역에서 출발해서 신촌역 근처'는 신촌에서 출발한다는 뜻이 아니다."""
    from unittest.mock import patch

    from app.graph.router import _rule_conditions
    from app.graph.subgraphs.discovery import resolve_origin
    from app.schemas import GeoPoint

    coords = {"판교역": (37.3947, 127.1112), "신촌역": (37.5551, 126.9368)}

    async def fake_geocode(q):
        for name, (la, lo) in coords.items():
            if name in q:
                return GeoPoint(lat=la, lng=lo, name=name)
        return None

    c = _rule_conditions("판교역에서 출발해서 신촌역 근처 전시 추천해줘")
    assert c.origin_name == "판교역" and c.landmark == "신촌역"

    with patch("app.tools.maps.geocode", fake_geocode):
        await resolve_origin(c)
    assert round(c.origin.lat, 4) == 37.3947          # 출발지는 판교역 그대로


def test_transport_survives_endpoint_cutting():
    """'지하철로 출발'에서 이동수단과 출발지가 서로를 잡아먹으면 안 된다.

    출발 절을 잘라내면 그 안의 '지하철'도 같이 사라져 수단이 unknown 이 되고,
    반대로 장소를 마지막 낱말로 고르면 출발지가 '지하철'이 된다.
    """
    from datetime import time

    from app.graph.router import _rule_conditions

    c = _rule_conditions(
        "내일 판교역에서 아침 6시에 지하철로 출발해서 강남구과 서초구에서 문화생활 3곳과 "
        "디져트 식사 추천해주고 밤 22시에 지하철로 종로역에서 도착해")

    assert c.origin_name == "판교역"          # '지하철'이 아니다
    assert c.transport == "subway"           # 잘려 나가지 않았다
    assert c.start_time == time(6, 0)
    assert c.destination_name == "종로역" and c.end_time == time(22, 0)
    assert set(c.regions) == {"서울 강남구", "서울 서초구"}

    # "문화생활 3곳과 디져트 식사" — 개수가 붙은 건 문화뿐이고 디저트·식사에는
    # 없다. 이럴 때 총량을 3으로 못 박으면 그 세 자리를 문화가 다 가져가
    # 디저트·식사가 통째로 사라진다. 실제로 "문화생활 + 디저트 2개"에서
    # 일정이 0곳으로 나왔다(2026-08-18). 그래서 몫만 확정하고 총량은 열어 둔다.
    assert c.kind_quota == {"culture": 3}
    assert c.stop_count is None


async def test_missing_place_name_is_not_mangled():
    """좌표를 못 찾아도 이름을 더럽히지 않는다.

    예전엔 이름에 '(찾지 못함)'을 덧붙였는데, 탐색 노드가 지오코딩을 여러 번
    부르는 바람에 '종로3가역(찾지 못함)(찾지 못함)(찾지 못함)'이 화면에 떴다.
    """
    from unittest.mock import patch

    from app.graph.router import _rule_conditions
    from app.graph.subgraphs.discovery import resolve_origin
    from app.schemas import GeoPoint

    async def fake_geocode(q):
        return GeoPoint(lat=37.3947, lng=127.1112, name="판교역") if "판교역" in q else None

    c = _rule_conditions("판교역에서 6시에 출발해서 강남 문화생활, 22시에 종로3가역 도착")
    with patch("app.tools.maps.geocode", fake_geocode):
        for _ in range(4):          # 탐색 노드가 여러 번 부른다
            await resolve_origin(c)

    assert c.destination_name == "종로3가역"
    assert c.destination_missing is True
    assert c.destination is None


async def test_geocode_falls_back_to_place_search():
    """주소 API가 못 찾는 장소 이름은 지역검색이 받는다.

    NCP Geocoding 은 주소 전용이라 '종각역'·'예술의전당'을 못 찾는다.
    사용자는 주소가 아니라 이름으로 말하므로, 여기서 막히면 말한 곳의
    절반이 지도에도 일정에도 반영되지 않는다.
    """
    from unittest.mock import AsyncMock, patch

    from app.tools import maps

    # 주소 조회는 빈손, 지역검색은 좌표를 준다
    local = {"items": [{"title": "<b>종각역</b>", "mapx": "1269830000",
                        "mapy": "375700000"}]}
    with patch.object(maps, "_ncp_get", AsyncMock(return_value={"addresses": []})), \
         patch.object(maps, "get_json", AsyncMock(return_value=local)), \
         patch.object(maps, "get_settings") as settings:
        settings.return_value.naver_search_client_id = "id"
        settings.return_value.naver_search_client_secret = "secret"
        point = await maps.geocode("종각역")

    assert point is not None
    assert point.name == "종각역"          # 태그가 벗겨진 정식 명칭
    assert 37.5 < point.lat < 37.6 and 126.9 < point.lng < 127.1


async def test_geocode_prefers_address_when_available():
    """주소로 찾히면 지역검색까지 가지 않는다 — 호출을 아낀다."""
    from unittest.mock import AsyncMock, patch

    from app.tools import maps

    addr = {"addresses": [{"y": "37.5665", "x": "126.9780",
                           "roadAddress": "서울특별시 중구 세종대로 110"}]}
    search = AsyncMock()
    with patch.object(maps, "_ncp_get", AsyncMock(return_value=addr)), \
         patch.object(maps, "get_json", search):
        point = await maps.geocode("서울특별시 중구 세종대로 110")

    assert point.name == "서울특별시 중구 세종대로 110"
    search.assert_not_awaited()


def test_address_is_kept_whole():
    """주소는 여러 낱말이라 마지막 낱말만 뽑으면 번지만 남는다.

    '영동대로'의 '로'를 조사로 떼면 '영동대'가 되어 주소 API가 못 찾는다 —
    실제로 겪은 버그다.
    """
    from app.graph.router import _rule_conditions

    cases = {
        "서울 강남구 영동대로 513 에서 10시에 출발해서 강남 문화생활": "서울 강남구 영동대로 513",
        "세종대로 110 에서 출발": "세종대로 110",
        "강남구 역삼동 에서 9시에 출발": "강남구 역삼동",
    }
    for query, expected in cases.items():
        assert _rule_conditions(query).origin_name == expected, query


def test_station_name_is_not_read_as_address():
    """'종로3가역'의 '종로3'이 도로명+번지로 잡히면 역 이름이 주소가 된다."""
    from app.graph.router import _rule_conditions

    c = _rule_conditions(
        "내일 판교역에서 아침 6시에 지하철로 출발해서 강남 3곳, "
        "밤 22시에 지하철로 종로3가역에서 도착")
    assert c.origin_name == "판교역"
    assert c.destination_name == "종로3가역"
    assert c.transport == "subway"


def test_label_word_order_is_understood():
    """'출발 판교역 도착 회기역'처럼 라벨이 앞에 오는 어순도 읽어야 한다.

    한국어는 '판교역에서 출발'도 '출발 판교역'도 자연스럽다. 키워드 앞만
    보면 뒤에 온 장소를 놓치고, 도착 절의 시각을 출발 시각으로 읽는다.
    """
    from datetime import time

    from app.graph.router import _rule_conditions

    c = _rule_conditions(
        "출발 아침 9시 판교역와 도착 회기역 밤 9시 강남에서 문화공연 및 식사 "
        "추천해주는 일정 만들어줘")
    assert c.origin_name == "판교역" and c.start_time == time(9, 0)
    assert c.destination_name == "회기역" and c.end_time == time(21, 0)
    assert c.regions == ["서울 강남구"]

    # 콜론을 쓴 형태도
    c2 = _rule_conditions("출발: 서울역 10시, 도착: 홍대입구역 20시, 강남 전시 3곳")
    assert c2.origin_name == "서울역" and c2.destination_name == "홍대입구역"
    assert c2.stop_count == 3

    # 시각 없이 장소만
    c3 = _rule_conditions("출발 판교역 도착 회기역 강남 문화생활")
    assert c3.origin_name == "판교역" and c3.destination_name == "회기역"


def test_label_clause_does_not_eat_the_next_one():
    """'출발 … 판교역와 도착 회기역'에서 출발 절이 도착 절을 먹으면 안 된다."""
    from app.graph.router import _split_endpoints

    found, rest = _split_endpoints("출발 아침 9시 판교역와 도착 회기역 밤 9시 강남에서 전시")
    assert found["origin_name"] == "판교역"
    assert found["destination_name"] == "회기역"
    assert "강남" in rest        # 나머지 조건은 살아남는다


def test_spoken_endpoints_beat_leftover_screen_state():
    """이번 발화에서 말한 출발·도착이 화면에 남은 이전 값을 이긴다.

    클라이언트는 화면 상태를 매번 함께 보낸다. 그걸 그대로 덮어쓰면
    '수원역에서 출발해 수원역 도착'이라고 말해도 지난 질문의 홍대입구역이
    도착지로 남는다 — 실제로 겪은 버그다.
    """
    from datetime import time

    from app.graph.router import _apply_override, _rule_conditions

    leftover = {
        "destination": {"lat": 37.5572, "lng": 126.9245},
        "destination_name": "홍대입구역 공항철도",
        "origin": {"lat": 37.3947, "lng": 127.1112},
        "start_time": "08:00", "end_time": "21:00", "transport": "best",
    }
    c = _rule_conditions(
        "수원역에서 아침 8시에 출발해서 강남의 공연문화 추천해주고 밤 9시에 수원역에 도착")
    out = _apply_override(c, leftover)

    assert out.destination_name == "수원역"
    assert out.destination is None          # 이전 좌표가 주입되면 안 된다
    assert out.origin_name == "수원역" and out.origin is None
    assert out.start_time == time(8, 0) and out.end_time == time(21, 0)
    assert out.transport == "best"          # 발화와 무관한 값은 그대로 쓴다


def test_screen_state_still_applies_when_unspoken():
    """말하지 않은 조건은 화면 값이 그대로 쓰인다."""
    from app.graph.router import _apply_override, _rule_conditions

    c = _rule_conditions("강남 문화생활 3곳 추천")
    out = _apply_override(c, {
        "origin": {"lat": 37.5, "lng": 127.0},
        "destination_name": "서울역", "transport": "walk",
    })
    assert out.origin is not None
    assert out.destination_name == "서울역"
    assert out.transport == "walk"


def test_combined_transit_is_not_a_transport_option():
    """'지하철+버스'는 수단이 아니라 조합이다 — 고를 수 있는 목록에 없어야 한다.

    섞는 일은 최단루트(best)가 구간마다 판단한다. 조합을 별도 수단으로 두면
    사용자는 '지하철', '버스', '지하철+버스' 중 무엇을 골라야 할지 매번 고민한다.
    """
    from app.graph.subgraphs.itinerary import BEST_CANDIDATE_MODES, TRANSPORT_KO
    from app.schemas import TripConditions
    from app.tools.maps import FALLBACK_SPEED, TRANSIT_MODES
    from app.tools.routing import ODSAY_PATH_TYPE

    assert "transit" not in BEST_CANDIDATE_MODES
    assert "transit" not in TRANSIT_MODES
    assert "transit" not in ODSAY_PATH_TYPE
    assert "transit" not in FALLBACK_SPEED
    assert "transit" not in TRANSPORT_KO

    # 스키마도 더 이상 받지 않는다
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError):
        TripConditions(transport="transit")


def test_mixed_odsay_result_is_labeled_by_requested_mode():
    """ODsay가 혼합 경로(pathType=3)를 줘도 '지하철+버스'로 부르지 않는다."""
    from app.tools.routing import _mode_of

    assert _mode_of(3, "subway") == "subway"
    assert _mode_of(3, "bus") == "bus"
    assert _mode_of(1, "bus") == "subway"      # 실제로 지하철이면 그대로 말한다
    assert _mode_of(2, "subway") == "bus"


def test_public_transit_utterance_maps_to_best():
    """'대중교통으로'는 특정 수단이 아니다 — 최단루트가 판단한다."""
    from app.graph.router import _rule_conditions

    assert _rule_conditions("대중교통으로 종로 문화생활").transport == "best"
    assert _rule_conditions("지하철로 종로 문화생활").transport == "subway"
    assert _rule_conditions("버스로 종로 문화생활").transport == "bus"


def test_label_with_particle_is_recognized():
    """'출발은 부산역'처럼 조사가 붙은 라벨도 읽어야 한다.

    조사를 허용하지 않으면 '출발은'이 라벨로 안 잡히고, 앞쪽을 뒤지다가
    '만들어줘' 같은 엉뚱한 말을 출발지로 집는다 — 실제로 겪은 버그다.
    """
    from datetime import time

    from app.graph.router import _rule_conditions

    c = _rule_conditions("부산에서만 일정 만들어줘 출발은 부산역 아침 9시야")
    assert c.origin_name == "부산역"
    assert c.start_time == time(9, 0)
    assert c.regions == ["부산"]

    for query, expected in {
        "출발지는 판교역, 도착은 종각역": ("판교역", "종각역"),
        "출발 부산역 도착 서울역": ("부산역", "서울역"),
        "목적지 서울역, 강남 문화생활": (None, "서울역"),
    }.items():
        got = _rule_conditions(query)
        assert (got.origin_name, got.destination_name) == expected, query


async def test_current_location_is_only_a_default():
    """현재 위치는 출발지가 아니라 '정하지 않았을 때 쓰는 값'이다.

    부산 일정을 짜면서 '출발은 부산역'이라고 말했는데 서울에 서 있다는 이유로
    서울에서 출발하면, 그건 다른 사람의 하루다.
    """
    from unittest.mock import patch

    from app.graph.router import _apply_override, _rule_conditions
    from app.graph.subgraphs.discovery import resolve_origin
    from app.schemas import GeoPoint

    async def fake_geocode(q):
        return GeoPoint(lat=35.1151, lng=129.0413, name="부산역") if "부산역" in q else None

    # 말한 경우 — GPS(서울)를 무시하고 부산역에서 출발한다
    spoken = _apply_override(
        _rule_conditions("부산에서만 일정 만들어줘 출발은 부산역 아침 9시야"),
        {"origin": {"lat": 37.5794, "lng": 126.9368}})
    with patch("app.tools.maps.geocode", fake_geocode):
        await resolve_origin(spoken)
    assert round(spoken.origin.lat, 3) == 35.115

    # 말하지 않은 경우 — 현재 위치가 출발점이 된다
    silent = _apply_override(_rule_conditions("부산 문화생활 3곳 추천"),
                             {"origin": {"lat": 35.1580, "lng": 129.1600}})
    assert silent.origin_name is None
    assert round(silent.origin.lat, 3) == 35.158


async def test_named_region_becomes_the_starting_point():
    """지역만 말해도 그 지역에서 하루가 시작된다.

    서울에 앉아 '부산 일정 만들어줘'라고 했는데 서울에서 출발하는 일정을 주면
    그건 부산 일정이 아니다. 현재 위치는 참고일 뿐 출발점이 아니다.
    """
    from unittest.mock import patch

    from app.graph.router import _apply_override, _rule_conditions
    from app.graph.subgraphs.discovery import resolve_origin
    from app.schemas import GeoPoint

    coords = {"부산": (35.1796, 129.0756), "서울 강남구": (37.5172, 127.0473),
              "부산역": (35.1151, 129.0413)}

    async def fake_geocode(q):
        # 긴 이름부터 본다 — '부산'이 '부산역'보다 먼저 걸리면 안 된다
        for name in sorted(coords, key=len, reverse=True):
            if name in q:
                la, lo = coords[name]
                return GeoPoint(lat=la, lng=lo, name=name)
        return None

    seoul = {"origin": {"lat": 37.5794, "lng": 126.9368}}     # 지금 서울에 있다

    # ① 먼 지역을 말했다 → 그 지역에서 시작
    far = _apply_override(_rule_conditions("부산에서만 문화생활 3곳 일정 만들어줘"), seoul)
    with patch("app.tools.maps.geocode", fake_geocode):
        await resolve_origin(far)
    assert far.origin.lat < 36, "부산에서 시작해야 한다"

    # ② 같은 생활권이면 현재 위치를 그대로 쓴다 — 굳이 옮길 이유가 없다
    near = _apply_override(_rule_conditions("강남 문화생활 3곳"), seoul)
    with patch("app.tools.maps.geocode", fake_geocode):
        await resolve_origin(near)
    assert abs(near.origin.lat - 37.5794) < 0.01

    # ③ 출발지를 말했으면 지역보다 그쪽이 우선
    spoken = _apply_override(
        _rule_conditions("부산에서만 일정 만들어줘 출발은 부산역 아침 9시야"), seoul)
    with patch("app.tools.maps.geocode", fake_geocode):
        await resolve_origin(spoken)
    assert abs(spoken.origin.lat - 35.1151) < 0.01


def test_connective_ending_is_not_the_day_end():
    """'끝나고 다시 수원역 밤 8시 도착'의 '끝나고'는 하루의 끝이 아니다."""
    from datetime import time

    from app.graph.router import _rule_conditions

    c = _rule_conditions(
        "수원역 아침 7시부터 출발해서 강남근처 일정 만들어줘 끝나고 다시 수원역 밤 8시 도착")
    assert c.origin_name == "수원역" and c.start_time == time(7, 0)
    assert c.destination_name == "수원역" and c.end_time == time(20, 0)
    assert c.landmark == "강남"


def test_common_words_are_not_read_as_city_names():
    """'다시'·'역시'의 '시'가 시(市)로 잡히면 문장 전체가 주소로 둔갑한다."""
    from app.graph.router import _rule_conditions

    c = _rule_conditions("수원역에서 출발해서 강남 보고 다시 수원역 도착")
    assert c.destination_name == "수원역"
    # 주소는 여전히 통째로 잡혀야 한다
    assert _rule_conditions("서울 강남구 영동대로 513 에서 출발").origin_name \
        == "서울 강남구 영동대로 513"


def test_facility_search_drops_other_regions():
    """공공 문화시설 API 는 sido 를 넘겨도 전국 결과를 섞어 준다.

    실제로 강남 요청에 '대구 메트로 갤러리'·'국립청주박물관'이 따라와
    일정 2번째 장소가 대구가 된 적이 있다. 서버 필터를 믿지 않는다.
    """
    from app.schemas import Candidate, TripConditions
    from app.tools.culture_api import _in_region

    c = TripConditions(region="서울 강남구")
    far = [Candidate(source="culture_facility", kind="venue", name=n)
           for n in ("대구 메트로 갤러리", "국립청주박물관", "국립제주박물관", "인디 [제주]")]
    assert not any(_in_region(x, c) for x in far)

    # 지역명이 없는 서울 시설은 살아남아야 한다 — 여기서 버리면 정작 서울이 사라진다
    near = [Candidate(source="culture_facility", kind="venue", name=n)
            for n in ("예술의전당", "세종문화회관", "아트선재센터")]
    assert all(_in_region(x, c) for x in near)


def test_candidates_far_from_the_trip_are_dropped():
    """이름·주소 필터를 빠져나온 것은 거리로 막는다.

    280km 떨어진 구간을 계산해 놓고도 아무도 이상하다고 하지 않았던 게 문제였다.
    """
    from app.graph.subgraphs.discovery import MAX_ANCHOR_KM, _far_from_all
    from app.schemas import Candidate, GeoPoint

    pangyo = GeoPoint(lat=37.3947, lng=127.1112)      # 판교역
    cheonggye = GeoPoint(lat=37.4475, lng=127.0554)   # 청계산입구역
    anchors = [pangyo, cheonggye]

    def at(lat: float, lng: float) -> Candidate:
        return Candidate(source="maps", kind="venue", name="x",
                         geo=GeoPoint(lat=lat, lng=lng))

    assert _far_from_all(at(35.8520, 128.5272), anchors)      # 대구 280km
    assert _far_from_all(at(36.6561, 127.4924), anchors)      # 청주 95km
    assert not _far_from_all(at(37.5795, 126.9818), anchors)  # 아트선재센터(종로)
    assert not _far_from_all(at(37.4839, 127.0104), anchors)  # 예술의전당
    assert MAX_ANCHOR_KM >= 60                               # 수도권은 덮어야 한다


def test_distance_cap_falls_back_to_the_candidate_cluster():
    """출발지를 안 밝힌 요청에도 거리 상한이 걸려야 한다.

    "오후 1시부터 예술의전당 가는 일정"처럼 기준점이 없으면 예전에는 검사를
    통째로 건너뛰었고, 그래서 서울 일정에 대구국악원이 들어갔다.
    """
    from app.graph.subgraphs.discovery import _anchors, _far_from_all
    from app.schemas import Candidate, GeoPoint, TripConditions

    def at(lat: float, lng: float) -> Candidate:
        return Candidate(source="maps", kind="venue", name="x",
                         geo=GeoPoint(lat=lat, lng=lng))

    seoul = [at(37.48 + i * 0.01, 127.00 + i * 0.01) for i in range(8)]
    daegu = at(35.8520, 128.5272)
    c = TripConditions()                      # 출발지·도착지 없음

    anchors = _anchors(c, [*seoul, daegu])
    assert anchors, "후보가 모여 있으면 기준점을 만들어야 한다"
    assert _far_from_all(daegu, anchors)               # 대구는 잘리고
    assert not any(_far_from_all(s, anchors) for s in seoul)   # 서울은 남는다

    # 근거가 부족하면 자르지 않는다 — 후보 2개로 중앙값을 믿을 수 없다
    assert _anchors(c, [at(37.5, 127.0), daegu]) == []

    # 명시된 출발지가 있으면 그쪽이 우선이다
    c2 = TripConditions(origin=GeoPoint(lat=37.3947, lng=127.1112))
    assert _anchors(c2, [*seoul, daegu]) == [c2.origin]


def test_count_without_kind_does_not_cap_the_day():
    """개수를 말하지 않은 종류가 섞이면 총량을 못 박지 않는다.

    "문화생활 추천해주고 디저트 맛집 2개" 에서 총량을 2로 확정하면 그 두 자리를
    디저트가 다 가져가 문화생활이 통째로 밀려난다 — 실제로 일정이 0곳이었다.
    몫만 확정하고 총량은 스케줄러에 맡긴다.
    """
    from app.graph.router import _rule_conditions

    c = _rule_conditions("서초역에서 문화 생활 추천해주고 디저트 맛집 2개 추천해줘")
    assert c.kind_quota == {"cafe": 2}
    assert c.stop_count is None


def test_listed_kinds_with_one_count_are_a_total():
    """여러 종류를 나열하고 개수를 하나만 말하면 그건 **총량**이다.

    "문화 및 식사 5개" 는 문화 5 + 식사 5 도, 식사만 5 도 아니다. 예전에는 개수 앞
    창에서 낱말 하나만 집어 {'food': 5} 로 잡았고, 합이 총량과 우연히 같아 오래
    가려져 있었다.
    """
    from app.graph.router import _rule_conditions

    c = _rule_conditions("강남역 문화 및 식사 스케쥴 5개 정도 만들어줘")
    assert c.kind_quota == {}
    assert c.stop_count == 5


def test_per_kind_counts_are_not_swallowed_by_the_previous_pair():
    """"문화 2개 디저트 3개" — 뒤 개수의 창이 앞 쌍까지 삼키면 안 된다.

    창을 앞 개수 표현에서 끊지 않으면 '문화'와 '디저트'가 한 창에 들어와
    «여러 종류 + 개수 하나 = 총량» 규칙에 걸리고, 디저트 몫이 사라진다.
    """
    from app.graph.router import _rule_conditions

    c = _rule_conditions("성수동에서 문화 2개 디저트 3개 추천해줘")
    assert c.kind_quota == {"culture": 2, "cafe": 3}
    assert c.stop_count == 5
