"""문화 콘텐츠 탐색.

두 레인을 같은 인터페이스로 다룬다(문서 목표 2).
  · 기간형 행사  → 한국문화정보원 공연전시정보 (공공데이터포털)
  · 상시 문화공간 → NAVER 지역검색 카테고리 질의

행사가 없는 날짜·지역에서도 일정이 성립해야 한다는 요구를 조건문이 아니라
'두 소스를 항상 병렬로 조회한다'는 구조로 보장한다.
"""
from __future__ import annotations

import logging
import re
from datetime import date as Date
from datetime import timedelta
from typing import Any

from app.config import get_settings
from app.schemas import Candidate, GeoPoint, TripConditions
from app.tools.base import cache_key, cached
from app.tools.http import as_list, get_json, to_float
from app.tools.region import SIDO_FULL as _SIDO_FULL

logger = logging.getLogger(__name__)

# 상시 이용 가능한 문화공간 — 행사가 없어도 일정을 채우는 축
ALWAYS_ON_CATEGORIES = [
    "미술관", "박물관", "독립서점", "공방", "독립영화관", "복합문화공간", "전시관",
]

# 공공 API가 돌려주는 분야명 → 내부 카테고리
REALM_MAP = {
    "미술": "전시", "전시": "전시", "사진": "전시",
    "connect": "전시", "국악": "공연", "音악": "공연", "음악": "공연",
    "연극": "공연", "무용": "공연", "뮤지컬": "공연", "오페라": "공연",
    "영화": "영화", "축제": "축제", "教육/체험": "체험", "교육/체험": "체험",
}

# 응답 스키마가 판올림될 때마다 리스트 키 이름이 바뀌어 왔다. 후보를 넓게 잡는다.
_LIST_KEYS = ("perforList", "item", "items", "row", "list")


async def search_events(c: TripConditions, limit: int = 30) -> list[Candidate]:
    """기간형 문화행사(전시·공연·축제·체험).

    공공 문화 API가 없으면 웹검색으로 대체한다. 정부 포털은 데이터가 정확하지만
    발급·엔드포인트가 까다롭고, 이 서비스의 핵심(아카이브 개인화)은 행사 출처와
    무관하다. 어느 쪽이든 뒤에 붙는 검증 단계가 공식정보를 대조하므로,
    소스가 바뀌어도 최종 품질은 검증이 지켜준다.
    """
    key = cache_key("culture.events", {**c.model_dump(mode="json"), "limit": limit})
    return await cached(key, ttl=1800, fn=lambda: _events_with_fallback(c, limit))


async def _events_with_fallback(c: TripConditions, limit: int) -> list[Candidate]:
    """공공 API → (실패하면) 웹검색.

    같은 인증키라도 서비스마다 '활용신청'이 따로다. 문화시설은 신청했지만
    공연전시는 안 한 상태처럼, 일부만 열려 있는 경우가 흔하다.
    한쪽이 막혔다고 행사 탐색 자체가 죽으면 안 되므로 자동으로 넘어간다.
    """
    s = get_settings()
    if s.culture_key and s.culture_api_endpoint:
        events = await _search_events(c, limit)
        if events:
            return events
        logger.info("공공 행사 API에서 결과가 없어 웹검색으로 대체합니다")
    return await _search_events_web(c, limit)


def _is_kcisa(endpoint: str) -> bool:
    """KCISA(문화공공데이터광장)와 공공데이터포털은 파라미터 체계가 아예 다르다.

    · data.go.kr : from/to/cPage/rows + 좌표 경계 필터
    · KCISA      : numOfRows/pageNo 만. 기간·좌표 필터가 없어 받은 뒤 걸러야 한다.
    """
    return "kcisa.kr" in endpoint


async def _search_events(c: TripConditions, limit: int) -> list[Candidate]:
    s = get_settings()
    from app.tools.weather import today_kst

    day = c.date or today_kst()   # 컨테이너 UTC와 한국 날짜가 어긋나지 않게
    kcisa = _is_kcisa(s.culture_api_endpoint)

    if kcisa:
        params: dict[str, Any] = {
            "serviceKey": s.culture_key,
            "numOfRows": min(limit * 6, 300),   # 서버 필터가 없어 넉넉히 받아 거른다
            "pageNo": 1,
        }
        if c.region:
            params["keyword"] = c.region
    else:
        params = {
            "serviceKey": s.culture_key,
            "from": day.strftime("%Y%m%d"),
            "to": (day + timedelta(days=1)).strftime("%Y%m%d"),
            "cPage": 1,
            "rows": min(limit * 2, 100),
            "sortStdr": 1,
        }
        # 좌표 범위 필터 — 출발지 반경 약 12km 의 경계 상자
        if c.origin:
            d = 0.11
            params.update({
                "gpsxfrom": round(c.origin.lng - d, 6),
                "gpsxto": round(c.origin.lng + d, 6),
                "gpsyfrom": round(c.origin.lat - d, 6),
                "gpsyto": round(c.origin.lat + d, 6),
            })
        elif c.region:
            params["keyword"] = c.region

    data = await get_json(s.culture_api_endpoint, params=params, ttl=1800,
                          retries=1, name="culture.period")
    rows = _extract_rows(data)
    if not rows:
        _log_api_error(data)
        from app.tools.http import last_error, record_error

        if data is not None and not last_error("culture.period"):
            record_error("culture.period",
                         f"응답은 왔으나 항목 없음 — 엔드포인트/데이터셋 확인 필요: "
                         f"{str(data)[:250]}")
        return []

    cands = [x for x in (to_candidate(r) for r in rows) if x is not None]
    if kcisa:
        # KCISA는 서버에서 기간·지역을 못 거르므로 여기서 처리한다.
        cands = [x for x in cands if _in_period(x, day) and _in_region(x, c)]
    out = cands[:limit]
    logger.info("공연전시 %d건 수집 (원본 %d건)", len(out), len(rows))
    return out


def _in_period(cand: Candidate, day) -> bool:
    if cand.period_start and day < cand.period_start:
        return False
    return not (cand.period_end and day > cand.period_end)


# 행사 제목·장소에 지역이 드러나는 경우가 많다 — "[대전] 말하지 못한 사랑".
# 전국 데이터를 그대로 받으므로 이 단서로 다른 지역을 걸러 낸다.
# "세종"은 일부러 뺐다. 세종특별자치시보다 '세종문화회관'·'세종대'처럼 서울 기관
# 이름의 일부로 훨씬 자주 나타나서, 넣어 두면 서울의 대표 공연장이 통째로 사라진다.
# 세종시 시설이 새어 들어오는 건 discovery 의 거리 상한(MAX_ANCHOR_KM)이 받는다.
_OTHER_REGIONS = (
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "제주",
    "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남",
    "수원", "성남", "용인", "고양", "춘천", "청주", "천안", "전주",
    "창원", "포항", "김해", "여수", "목포", "안동", "강릉", "제천",
)


def _in_region(cand: Candidate, c: TripConditions) -> bool:
    """다른 지역이라고 **명시된** 것만 버린다.

    '서울'이 안 적혀 있다고 버리면 안 된다. 장소가 '예술의전당'·'세종문화회관'처럼
    지역명 없이 오는 경우가 흔해서, 그러면 정작 서울 행사가 통째로 사라진다.
    지역 단서가 아예 없는 항목은 남겨 두고, 좌표와 이동시간이 뒤에서 걸러 준다.
    """
    if not c.region:
        return True
    city = c.region.split()[0]          # "서울 종로구" → "서울"

    # 제목 앞의 [지역] 표기가 가장 확실한 단서다. 이게 있으면 이것만 본다 —
    # '[울산] … 서울주문화센터'처럼 장소명이 다른 지역명을 부분 문자열로
    # 품고 있어서, 본문을 먼저 보면 엉뚱하게 통과한다.
    tag = re.match(r"\s*\[([^\]]{2,8})\]", cand.name or "")
    if tag and any(r in tag.group(1) for r in _OTHER_REGIONS):
        return city in tag.group(1)

    haystack = " ".join(filter(None, [cand.name, cand.address, *(cand.tags or [])]))
    if not haystack or city in haystack:
        return True
    return not any(r in haystack for r in _OTHER_REGIONS if r != city)


# 웹에서 행사를 찾을 때 쓰는 질의 틀. 장소가 아니라 '기간형 행사'를 노린다.
_WEB_EVENT_QUERIES = [
    "{region} 전시 추천 {month}월",
    "{region} 가볼만한 전시회 {month}월",
    "{region} 축제 공연 {month}월",
    "{region} 근처 문화행사 {month}월",
]


async def _search_events_web(c: TripConditions, limit: int) -> list[Candidate]:
    """웹검색으로 행사 후보를 만든다.

    여기서 나온 후보는 좌표도 운영시간도 없다. 뒤따르는 검증 단계가
    공식 출처를 대조해 채우고, 채우지 못하면 'needs_check'로 표시된다.
    """
    from app.tools.weather import today_kst
    from app.tools.websearch import available, search

    if not available():
        return []

    day = c.date or today_kst()
    region = c.landmark or c.region or "서울"
    seen: set[str] = set()
    out: list[Candidate] = []

    for template in _WEB_EVENT_QUERIES:
        if len(out) >= limit:
            break
        query = template.format(region=region, month=day.month)
        for r in await search(query, k=5):
            title = (r.get("title") or "").strip()
            if not title or title in seen:
                continue
            seen.add(title)
            out.append(Candidate(
                source="web", kind="event", name=_clean_title(title),
                category="전시", official_url=r.get("url") or None,
                indoor=True, expected_dwell_min=80,
                relevance=float(r.get("score") or 0.4),
                tags=[region],
                raw={"snippet": (r.get("content") or "")[:500], "query": query},
            ))
    logger.info("웹검색 기반 행사 후보 %d건", len(out))
    return out[:limit]


def _clean_title(title: str) -> str:
    """검색 결과 제목에서 사이트명 꼬리를 떼어낸다."""
    for sep in (" - ", " | ", " :: ", " – "):
        if sep in title:
            title = title.split(sep)[0]
    return title.strip()[:60]


async def search_always_on(c: TripConditions, limit: int = 30) -> list[Candidate]:
    """상시 이용 가능한 문화공간."""
    key = cache_key("culture.always_on", {**c.model_dump(mode="json"), "limit": limit})
    return await cached(key, ttl=3600, fn=lambda: _search_always_on(c, limit))


async def _search_always_on(c: TripConditions, limit: int) -> list[Candidate]:
    """상시 문화공간.

    두 소스를 합친다.
      · 공공 문화시설 API — 미술관·박물관·공연장. 공식 데이터라 신뢰도가 높다.
      · 네이버 지역검색   — 독립서점·공방·편집숍처럼 공공 데이터에 없는 곳.
    둘 다 있으면 합쳐서 중복을 제거한다. 후자만 있어도 서비스는 성립한다.
    """
    from app.tools.maps import search_nearby

    official = await _search_facilities(c, limit)

    # 지점("신촌역 근처")이면 그 좌표 하나만. 구 목록으로 넓히면 도보권을 벗어난다.
    anchors = [c.origin] if c.origin else []
    if c.regions and not c.landmark:
        from app.tools.maps import geocode

        extra = [await geocode(name) for name in c.regions[:4]]
        # 현재 위치 + 선택 지역을 모두 본다. 지역 선택은 범위를 넓히는 행위다.
        anchors = anchors + [p for p in extra if p]
    if not anchors:
        return official[:limit]
    per_category = max(1, limit // (len(ALWAYS_ON_CATEGORIES) * len(anchors)))
    out: list[Candidate] = list(official)
    seen = {x.name for x in out}
    radius = c.radius_m or 3000
    for anchor in anchors:
      for category in ALWAYS_ON_CATEGORIES:
        rows = await search_nearby(anchor, category, radius_m=radius, limit=per_category)
        for r in rows:
            if r["name"] in seen:
                continue
            seen.add(r["name"])
            out.append(Candidate(
                source="naver_local", kind="venue",
                name=r["name"], category=category, address=r.get("address"),
                geo=GeoPoint(lat=r["lat"], lng=r["lng"], name=r["name"]),
                official_url=r.get("url"),
                indoor=category not in ("공원",),
                expected_dwell_min=60,
                relevance=_interest_match(category, c.interests),
                raw=r,
            ))
    logger.info("상시 문화공간 %d건 (공공 %d + 지역검색 %d)",
                len(out), len(official), len(out) - len(official))
    return out[:limit]


# 문화시설 유형 코드 → 내부 카테고리
_FACILITY_KIND = {
    "미술관": "미술관", "박물관": "박물관", "도서관": "도서관",
    "공연장": "공연장", "문예회관": "복합문화공간", "복합": "복합문화공간",
    "전시": "전시관", "영화": "독립영화관",
}


# 문화시설조회서비스는 시설 종류마다 오퍼레이션이 따로다. 설정값은 Base URL 이므로
# 여기에 이 경로들을 붙여 부른다. Base URL 만 부르면 404 가 아니라
# 'NO_OPENAPI_SERVICE_ERROR(code 12)' 가 돌아와서, 주소가 폐기된 것처럼 보인다.
_FACILITY_OPS = ("artgallery", "museum", "performingplace")

# 이 서비스의 sido 는 정식 명칭만 받는다. '서울'로 보내면 오류 없이 0건이 돌아와
# 키나 주소 문제로 오해하기 쉽다. 표 자체는 `app.tools.region` 이 갖고 있다 —
# 여기서 다시 적으면 개편(강원도→강원특별자치도)에 한쪽만 고쳐진다.


async def _search_facilities(c: TripConditions, limit: int) -> list[Candidate]:
    """한국문화정보원 문화시설조회서비스 (미술관·박물관·공연장)."""
    import asyncio

    s = get_settings()
    # 이 API 는 공공데이터포털이라 포털 키를 쓴다. 행사 API 가 KCISA 로 바뀌어도
    # 여기까지 KCISA 키가 따라오면 403(code 30) 이 난다.
    key = s.portal_key
    if not (s.culture_facility_endpoint and key):
        return []

    base = s.culture_facility_endpoint.rstrip("/")
    params: dict[str, Any] = {
        "serviceKey": key,
        "cPage": 1,
        "rows": min(limit * 3, 100),
        "numOfRows": min(limit * 3, 100),   # 엔드포인트 세대에 따라 이름이 다르다
        "pageNo": 1,
    }
    if c.region:
        # 지역명은 '서울 종로구' 형태다. 앞 토큰만 떼어 정식 시·도명으로 바꿔 넘긴다.
        head = c.region.split()[0]
        params["sido"] = _SIDO_FULL.get(head, head)
        if len(c.region.split()) > 1:
            params["gugun"] = c.region.split()[1]

    async def one(op: str) -> list[dict]:
        data = await get_json(f"{base}/{op}", params=params, ttl=3600,
                              retries=1, name=f"culture.facility.{op}")
        rows = _extract_rows(data)
        if not rows:
            _log_api_error(data)
        return rows

    batches = await asyncio.gather(*(one(op) for op in _FACILITY_OPS),
                                   return_exceptions=True)
    out: list[Candidate] = []
    seen: set[str] = set()
    for batch in batches:
        if isinstance(batch, BaseException):
            continue
        for raw in batch:
            cand = _to_facility(raw)
            if cand and cand.name not in seen:
                seen.add(cand.name)
                out.append(cand)
    # sido/gugun 을 넘겨도 이 API 는 전국 결과를 섞어 준다. 실제로 강남 요청에
    # '대구 메트로 갤러리'·'국립청주박물관'이 따라왔다. 행사 쪽(_in_region)과
    # 같은 검사를 여기에도 건다 — 서버 필터를 믿을 수 없다.
    before = len(out)
    out = [x for x in out if _in_region(x, c)]
    logger.info("공공 문화시설 %d건 수집 (%s)%s", len(out), ", ".join(_FACILITY_OPS),
                f" — 타지역 {before - len(out)}건 제외" if before != len(out) else "")
    return out


def _to_facility(raw: dict[str, Any]) -> Candidate | None:
    # 문화시설조회서비스는 culName/culGrpName/culHomeUrl 를 쓴다. 이걸 빠뜨리면
    # 응답은 정상(resultCode 00, 581건)인데 후보가 0건이 되어, 키나 주소 문제로 오해한다.
    name = _text(raw, "culName", "fcltyNm", "FCLTYNM", "title", "TITLE", "name")
    if not name:
        return None
    lat = to_float(_text(raw, "gpsY", "GPSY", "la", "LA"))
    lng = to_float(_text(raw, "gpsX", "GPSX", "lo", "LO"))
    raw_kind = _text(raw, "culGrpName", "clNm", "CLNM", "fcltyType", "type", "realmName")
    category = next((v for k, v in _FACILITY_KIND.items() if k in raw_kind), None)

    return Candidate(
        source="culture_facility",
        kind="venue",
        name=name,
        category=category or (raw_kind or "복합문화공간"),
        address=_text(raw, "addr", "ADDR", "rdnmadr", "adres", "place"),
        geo=GeoPoint(lat=lat, lng=lng, name=name) if lat and lng else None,
        official_url=_text(raw, "culHomeUrl", "hmpgUrl", "url", "URL", "homepage") or None,
        indoor=True,
        expected_dwell_min=70,
        relevance=0.6,     # 공식 데이터라 웹 후보보다 기본 신뢰도를 높게 둔다
        tags=[t for t in (raw_kind, _text(raw, "areaNm", "AREANM", "area")) if t],
        raw=raw,
    )


# ------------------------------------------------------------------- 매핑
def to_candidate(raw: dict[str, Any]) -> Candidate | None:
    # 포털마다 필드명이 다르다: data.go.kr(camelCase/UPPER) vs KCISA(UPPER_SNAKE)
    name = _text(raw, "title", "TITLE", "fcltyNm")
    if not name:
        return None
    lat, lng = to_float(_text(raw, "gpsY", "GPSY")), to_float(_text(raw, "gpsX", "GPSX"))
    realm = _text(raw, "realmName", "REALMNAME", "GENRE", "SUBJECT") or ""

    period_start = _date(_text(raw, "startDate", "STARTDATE"))
    period_end = _date(_text(raw, "endDate", "ENDDATE"))
    if not period_start:
        # KCISA는 "20260801~20260830" 처럼 한 필드에 기간을 담는다
        period_start, period_end = _parse_period(
            _text(raw, "EVENT_PERIOD", "PERIOD", "eventPeriod"))

    return Candidate(
        source="culture_api",
        kind="event" if period_start else "venue",
        name=name,
        category=REALM_MAP.get(realm, realm or "전시"),
        # eventSite(카멜)를 빠뜨리면 장소가 통째로 비고, 그러면 지오코딩이 공연 '제목'으로
        # 시도돼 전부 실패한다 — 좌표 없는 후보는 뒤에서 전량 폐기되므로 행사가 0건이 된다.
        address=_text(raw, "place", "PLACE", "SPATIAL_COVERAGE",
                      "EVENT_SITE", "eventSite"),
        geo=GeoPoint(lat=lat, lng=lng, name=name) if lat and lng else None,
        official_url=_text(raw, "url", "URL", "REFERENCE_IDENTIFIER") or None,
        period_start=period_start,
        period_end=period_end,
        fee=_text(raw, "price", "PRICE", "CHARGE", "charge") or None,
        indoor=realm not in ("축제",),
        expected_dwell_min=90 if realm in ("연극", "뮤지컬", "음악", "영화") else 70,
        tags=[t for t in (realm, _text(raw, "area", "AREA", "LOCAL_ID")) if t],
        raw=raw,
    )


def _extract_rows(data: Any) -> list[dict]:
    """응답 깊이와 리스트 키가 버전마다 달라, 트리를 훑어 첫 dict 리스트를 찾는다."""
    if not isinstance(data, dict):
        return []
    for key in _LIST_KEYS:
        found = _find_key(data, key)
        if found:
            rows = as_list(found)
            if rows and isinstance(rows[0], dict):
                return rows
    return []


def _find_key(node: Any, target: str) -> Any:
    if isinstance(node, dict):
        if target in node:
            return node[target]
        for value in node.values():
            found = _find_key(value, target)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_key(item, target)
            if found is not None:
                return found
    return None


def _log_api_error(data: Any) -> None:
    if not isinstance(data, dict):
        return
    code = _find_key(data, "resultCode") or _find_key(data, "returnReasonCode")
    msg = _find_key(data, "resultMsg") or _find_key(data, "returnAuthMsg")
    if code and str(code) not in ("00", "0"):
        logger.warning("문화 API 오류 %s: %s", code, msg)


def _text(raw: dict, *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _parse_period(value: str) -> tuple[Date | None, Date | None]:
    """'20260801~20260830', '2026.08.01 - 2026.08.30' 등을 두 날짜로 나눈다."""
    if not value:
        return None, None
    import re

    parts = re.split(r"[~\-–—]|부터|까지", value)
    dates = [d for d in (_date(p) for p in parts) if d]
    if not dates:
        return None, None
    return dates[0], (dates[-1] if len(dates) > 1 else None)


def _date(value: str) -> Date | None:
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) < 8:
        return None
    try:
        return Date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def _interest_match(category: str, interests: list[str]) -> float:
    if not interests:
        return 0.5
    return 0.9 if any(i in category or category in i for i in interests) else 0.4
