"""LLM 없이도 지역·날짜·이동수단·시각을 건지는 규칙 파서.

구조화 출력이 실패했을 때 조건이 통째로 비면 탐색 결과가 0건이 되고, 사용자는
'아무것도 안 나온다'만 보게 된다. 규칙으로 잡히는 것만이라도 채운다 — 그래서
`_safe_rules` 가 예외를 삼킨다. **거들어 주는 장치이지 요청을 막는 관문이 아니다.**

`_rule_conditions` 의 **순서가 곧 규칙이다.** 체류시간 → 출발·도착 절 → 나머지.
'1시간~2시간'의 '1시'와 '09시에 출발'의 09시를 먼저 걷어내지 않으면, 그것들이
방문할 장소의 지정 시각으로 둔갑해 일정 전체가 무너진다.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from app.graph.router.detect import (
    _NOT_LANDMARK,
    _detect_count,
    _detect_kind_quota,
    _detect_landmark,
    _detect_radius,
    _detect_regions,
    _detect_stops,
    _mentioned_kind_groups,
)
from app.graph.router.endpoints import _split_endpoints
from app.graph.router.timeparse import _UNTIL_TIME, _detect_dwell, _fix_am_pm, _to_time
from app.schemas import TripConditions

logger = logging.getLogger(__name__)

_DATE = re.compile(r"(?:(\d{4})년\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일")

# 이동수단. 긴 표현을 먼저 본다 — '자가용'이 '차'로 먹히면 안 된다.
# 사용자가 '도보로 짜줘'라고 말하면 UI의 이동수단 선택도 같이 바뀌어야 하므로,
# 여기서 정한 값이 그대로 클라이언트로 돌아간다.
_TRANSPORT = (
    ("최단루트", "best"), ("최단 루트", "best"), ("가장 빠른", "best"),
    ("제일 빠른", "best"), ("최적 경로", "best"), ("최적경로", "best"),
    ("빠른 길", "best"), ("알아서", "best"),
    # '지하철+버스'는 수단이 아니라 두 수단의 조합일 뿐이다. 사용자가 고를 수
    # 있는 건 '지하철'과 '버스' 각각이고, 섞는 건 최단루트가 할 일이다.
    ("대중교통", "best"), ("아무거나", "best"),
    ("지하철", "subway"), ("전철", "subway"), ("메트로", "subway"),
    ("버스", "bus"), ("마을버스", "bus"), ("광역버스", "bus"),
    ("자가용", "car"), ("자차", "car"), ("운전", "car"), ("차로", "car"),
    ("차량", "car"), ("드라이브", "car"), ("주차", "car"),
    # '차'만 보면 '차 마시러'(카페)까지 자동차로 잡힌다. 동사를 붙여서 가른다.
    ("차 갖", "car"), ("차 가지", "car"), ("차 끌", "car"), ("차 타", "car"),
    ("차 몰", "car"),
    ("도보", "walk"), ("걸어", "walk"), ("걷", "walk"), ("뚜벅", "walk"),
    ("자전거", "bike"), ("따릉이", "bike"),
)
_COMPANION = (("혼자", "solo"), ("연인", "couple"), ("남친", "couple"), ("여친", "couple"),
              ("친구", "friends"), ("가족", "family"), ("아이", "kids"), ("애들", "kids"))

# 도착지 표현. 공백을 포함하면 '…추천해줘 잠실역까지'에서 앞 문장까지 통째로
# 삼켜 버리므로, '까지' 바로 앞의 한 낱말만 본다.
_DESTINATION = re.compile(
    r"(?:^|[\s,])([가-힣A-Za-z0-9]{2,12})(?:까지|으로 가는|로 가는|에서 끝|에서 마무리)"
)
# '3시까지'·'내일까지'처럼 시간 표현에 붙는 '까지'는 도착지가 아니다
_NOT_DESTINATION = re.compile(r"^(?:\d+(?:시|분|시간|일|주)|오늘|내일|모레|저녁|밤|오후|오전|주말)$")


def _safe_rules(query: str) -> TripConditions:
    """규칙 파서를 감싼다.

    이건 '거들어 주는' 장치다. 정규식 몇 개가 예상 못 한 문장에서 터졌다고
    질문 전체가 실패하면, 사용자는 조건을 조금 다르게 썼을 뿐인데 아무 답도
    받지 못한다. 파싱이 깨지면 조건 없이 진행해서 최소한 결과는 낸다.
    """
    try:
        return _rule_conditions(query)
    except Exception:
        logger.exception("규칙 파싱 실패 — 조건 없이 진행합니다: %r", query[:120])
        return TripConditions(free_text=query)


def _rule_conditions(query: str) -> TripConditions:
    """LLM 없이도 지역·날짜·이동수단을 건진다.

    구조화 출력이 실패했을 때 조건이 통째로 비면 탐색 결과가 0건이 되고,
    사용자는 '아무것도 안 나온다'만 보게 된다. 규칙으로 잡히는 것만이라도 채운다.
    """
    c = TripConditions(free_text=query)
    # 원문을 보관한다. 아래에서 출발·도착 절을 잘라내는데, 이동수단·동행자는
    # 그 절 안에 들어 있는 경우가 많다("지하철로 출발"). 잘린 문장에서 찾으면 놓친다.
    raw = query

    # 체류시간을 가장 먼저 떼어낸다. '1시간~2시간'을 남겨 두면 '1시'가 시각으로
    # 읽혀서 출발 시각이나 방문 항목으로 둔갑한다.
    c.dwell_min, c.dwell_max, query = _detect_dwell(query)

    # 출발·도착 절을 떼어낸다. 이걸 남겨 두면 '09시에 출발'의 09시가
    # 방문할 장소로 잡히고, 출발지가 첫 번째 일정 항목이 되어 버린다.
    ends, query = _split_endpoints(query)
    c.origin_name = ends.get("origin_name")
    c.destination_name = ends.get("destination_name")
    c.start_time = ends.get("start_time")
    c.end_time = ends.get("end_time")
    # "저녁 7시까지"·"8시까지" — 종료를 '까지'로만 말하는 경우가 흔한데
    # _END_WORD(도착·종료·귀가…)에는 걸리지 않아 end_time 이 통째로 비었다.
    # 그러면 하루 끝을 기본값 20:00 으로 잡아 사용자가 말한 시각이 무시된다.
    if c.end_time is None:
        m = _UNTIL_TIME.search(query)
        if m:
            c.end_time = _to_time(m)
    c.stop_count = _detect_count(query)
    # 종류별 개수가 잡히면 총량도 그 합으로 바꾼다. 그러지 않으면 앞 숫자
    # 하나(_detect_count 는 첫 매치만 본다)가 총량이 되어, "문화 2 + 디저트 3"이
    # 2곳에서 끊긴 뒤 빈틈 채우기가 나머지를 제멋대로 채운다.
    c.kind_quota = _detect_kind_quota(query)
    if c.kind_quota:
        # 다만 **말한 종류 전부에 개수가 붙었을 때만** 총량을 확정한다.
        # "문화생활 추천해주고 디저트 맛집 2개"처럼 개수를 말하지 않은 종류가
        # 섞이면, 합(2)을 총량으로 박는 순간 그 2자리를 디저트가 다 가져가
        # 문화생활이 통째로 밀려난다 — 실제로 일정이 0곳으로 나왔다(2026-08-18).
        # 스케줄러는 몫에 없는 종류를 이미 통과시키므로(`placement._quota_room`),
        # 총량만 열어 두면 말한 몫은 보장되고 나머지는 알아서 채워진다.
        unquoted = _mentioned_kind_groups(query) - set(c.kind_quota)
        c.stop_count = None if unquoted else sum(c.kind_quota.values())

    c.landmark = _detect_landmark(query)
    # 지점 이름이 지역명을 포함할 수 있다('서울숲'). 지역 탐지에서는 빼고 본다.
    region_text = query.replace(c.landmark, " ") if c.landmark else query
    found = _detect_regions(region_text)
    c.regions = found
    c.region = found[0] if found else None
    c.radius_m = _detect_radius(query, c.landmark)

    d = _DATE.search(query)
    if d:
        today = date.today()
        year = int(d.group(1)) if d.group(1) else today.year
        try:
            parsed = date(year, int(d.group(2)), int(d.group(3)))
            # 연도를 안 적었는데 이미 지난 날짜면 내년으로 본다
            c.date = parsed if d.group(1) or parsed >= today else date(
                year + 1, int(d.group(2)), int(d.group(3)))
        except ValueError:
            pass
    elif "내일" in query:
        c.date = date.today() + timedelta(days=1)
    elif "오늘" in query:
        c.date = date.today()

    if any(w in query for w in ("실내", "비 오", "비오", "비 올", "비올", "장마", "폭염", "한파")):
        c.indoor_pref = "indoor"
    elif any(w in query for w in ("야외", "바깥", "산책", "피크닉")):
        c.indoor_pref = "outdoor"

    c.stops = _detect_stops(query)
    # 하루의 시작·끝 시각과 겹치는 '정류'는 방문할 장소가 아니다.
    # 출발/귀가 표현을 못 잡았을 때 이 시각들이 고정 항목으로 새어 들어가는데,
    # 그러면 그 하나만 남고 일정 전체가 무너진다.
    # 시각이 있는 항목만 대상이다. 시각 없는 자유 항목(식사 요청 등)은
    # start/end 가 둘 다 None 일 때 {None} 에 걸려 통째로 사라진다.
    c.stops = [s for s in c.stops
               if s.at is None
               or s.at not in {c.start_time, c.end_time}
               or s.place_hint]
    if c.stops and c.stops[0].at and c.start_time is None:
        c.start_time = c.stops[0].at         # 첫 일정 시각이 곧 시작 시각

    if c.destination_name is None:
        dest = _DESTINATION.search(query)
        if dest:
            name = dest.group(1).strip()
            if (len(name) >= 2 and name not in _NOT_LANDMARK
                    and not _NOT_DESTINATION.match(name)):
                c.destination_name = name

    # 이동수단·동행자는 원문에서 찾는다 — 잘라낸 절 안에 있을 수 있다
    for word, value in _TRANSPORT:
        if word in raw:
            c.transport = value  # type: ignore[assignment]
            break
    for word, value in _COMPANION:
        if word in raw:
            c.companions = value  # type: ignore[assignment]
            break

    # 오전/오후 보정은 **맨 마지막에** 한다. start_time 은 위에서 stops 로도 채워지므로
    # 중간에 부르면 그때 정해진 값이 검사를 못 받고 그대로 나간다 —
    # "5시에 만나 8시까지"가 17:00~08:00 인 채로 통과했다.
    c.start_time, c.end_time = _fix_am_pm(c.start_time, c.end_time)
    return c


def _merge_rules(primary: TripConditions, rules: TripConditions) -> TripConditions:
    merged = primary.model_copy(deep=True)
    if not merged.region and rules.region:
        merged.region = rules.region
    if not merged.regions and rules.regions:
        merged.regions = rules.regions
    if not merged.regions and merged.region:
        merged.regions = [merged.region]
    if not merged.landmark and rules.landmark:
        merged.landmark = rules.landmark
    if merged.radius_m is None and rules.radius_m:
        merged.radius_m = rules.radius_m
    if merged.date is None and rules.date:
        merged.date = rules.date
    if merged.transport == "unknown":
        merged.transport = rules.transport
    if merged.companions == "unknown":
        merged.companions = rules.companions
    if merged.indoor_pref == "any" and rules.indoor_pref != "any":
        merged.indoor_pref = rules.indoor_pref
    if not merged.stops and rules.stops:
        merged.stops = rules.stops
    elif merged.stops and not any(s.at for s in rules.stops):
        # 규칙 파서가 발화에서 시각을 하나도 못 찾았다면 사용자는 시각을 말하지 않은 것이다.
        # 그런데 LLM 은 "식사 포함"에 12:00 을, "전시"에 15:00 을 임의로 붙인다.
        # 그 시각이 고정 항목으로 박히면 앞뒤가 통째로 비어, 07~21시 열네 시간이
        # 두세 곳짜리 일정이 된다. 목적만 살리고 시각은 스케줄러에게 돌려준다.
        for s in merged.stops:
            s.at = None
            s.fixed = False
    if not merged.destination_name and rules.destination_name:
        merged.destination_name = rules.destination_name
    if merged.start_time is None and rules.start_time:
        merged.start_time = rules.start_time

    # LLM 도 표시 없는 시각을 오후로 밀어 놓는다("7시 출발" → 19:00).
    # 프롬프트로 일러 두었지만 모델 출력은 통제할 수 없으므로 여기서 한 번 더 막는다.
    # 규칙 파서가 같은 발화에서 더 이른 출발 시각을 뽑았다면 그쪽을 믿는다 —
    # 정규식은 '출발 절'과 '도착 절'을 구분해서 읽지만 모델은 그러지 않는다.
    if (merged.start_time and rules.start_time and merged.end_time
            and rules.start_time < merged.start_time
            and merged.start_time.hour == rules.start_time.hour + 12):
        merged.start_time = rules.start_time
    merged.start_time, merged.end_time = _fix_am_pm(merged.start_time, merged.end_time)
    return merged
