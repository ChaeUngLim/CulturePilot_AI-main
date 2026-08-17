"""행정구역(시·도 · 시군구) 판정 — UR-18.

지역 정확도를 지금까지는 **거리**로만 지켰다(`discovery.MAX_ANCHOR_KM` 60km).
거리는 수도권을 한 덩어리로 묶기 위한 근사치이고, 근사치라서 양쪽으로 틀린다.

  · **좁게 말해도 넘어온다** — 「서초구」라고 명시해도 판교(경기 성남시)는 25km라
    상한을 통과한다. 실제로 서초구 요청의 1번 장소가 판교로 나온 적이 있다.
  · **넓은 도(道)는 아예 못 잡는다** — 충남처럼 중심이 없는 광역도는 어느 점을
    기준으로 재도 반경이 맞지 않는다(TEST.md D-01 · R-05).

그래서 좌표가 아니라 **이름**으로 판정한다. 거리 상한은 그대로 두고 보조로 쓴다 —
행정구역을 알 수 없는 후보(주소도 지오코딩 결과도 없는 것)는 여기서 통과하고
거리가 받아 주기 때문이다. **모르는 것은 버리지 않는다**가 이 모듈의 규칙이다.

`culture_api._in_region` 과 역할이 다르다. 그쪽은 응답 **본문의 지역명 단서**로
명백한 타지역을 쳐내는 1차 관문(«[대전] 말하지 못한 사랑»)이고, 여기는 주소·지오코딩이
준 **행정구역 값**으로 판정하는 2차 관문이다. 이름 휴리스틱은 '세종문화회관'에서
새고, 행정구역 값은 주소가 없는 후보에서 비므로 둘 다 필요하다.
"""
from __future__ import annotations

import re

# 정규 짧은 이름 → 정식 명칭. 공공 API 파라미터(`sido=`)가 정식 명칭을 요구한다.
SIDO_FULL = {
    "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시",
    "인천": "인천광역시", "광주": "광주광역시", "대전": "대전광역시",
    "울산": "울산광역시", "세종": "세종특별자치시", "제주": "제주특별자치도",
    "경기": "경기도", "강원": "강원특별자치도", "충북": "충청북도",
    "충남": "충청남도", "전북": "전북특별자치도", "전남": "전라남도",
    "경북": "경상북도", "경남": "경상남도",
}

# 정식 명칭만 모은 것. 문자열 **아무 곳**에서나 찾아도 오탐이 나지 않는 표기다.
# 짧은 이름("서울")은 '서울주문화센터'·'세종문화회관'에 걸리므로 여기 넣지 않는다.
_FULL_FORMS = {
    **{full: short for short, full in SIDO_FULL.items()},
    # 개편 전 명칭도 데이터에 그대로 남아 있다
    "강원도": "강원", "전라북도": "전북", "제주도": "제주",
}

# 문장 맨 앞에 올 수 있는 표기 전부. 주소는 반드시 시·도로 시작하므로,
# 시작 위치에서만 보면 짧은 이름을 써도 안전하다.
_HEAD_FORMS = {
    **_FULL_FORMS,
    **{short: short for short in SIDO_FULL},
    **{f"{short}시": short for short in
       ("서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종")},
}

# 시군구 토큰. 주소에서 시·도를 떼면 그다음 한두 토큰 안에 반드시 나온다.
# 세 번째 토큰부터는 도로명("○○대로")·법정동이라 보지 않는다.
# 앞 글자를 1자부터 받는 이유는 '중구'다 — 2자 이상으로 잡으면 서울·부산·대구·
# 인천·대전·울산의 중구가 통째로 빠진다.
_SIGUNGU = re.compile(r"^[가-힣]{1,5}(?:시|군|구)$")
_SIGUNGU_SCAN = 2

# 짧은 이름 뒤에 이것 말고 다른 글자가 붙으면 지역명이 아니다.
# '세종문화회관'(서울)·'서울주문화센터'(울산)가 여기서 갈린다 —
# `culture_api._OTHER_REGIONS` 가 '세종'을 목록에서 빼야 했던 것과 같은 함정이다.
_BOUNDARY = " \t,"


def parse(text: str | None) -> tuple[str | None, str | None]:
    """주소 또는 지역 표현 → (시·도 짧은 이름, 시군구).

    «서울특별시 서초구 반포대로 2» → ("서울", "서초구")
    «경기도 성남시 분당구 판교역로» → ("경기", "성남시")
    «서초구 반포대로 2»            → (None, "서초구")   ← 시·도를 모른다고 지어내지 않는다
    «세종문화회관»                 → (None, None)

    시·도를 **맨 앞에서만** 짧은 이름으로 찾는 이유가 «경기도 광주시»다.
    문자열 전체에서 짧은 이름을 찾으면 이 주소가 광주광역시가 된다.
    """
    if not text:
        return None, None
    rest = text.strip()

    sido = None
    for form in sorted(_HEAD_FORMS, key=len, reverse=True):
        tail = rest[len(form):]
        # 경계를 확인하지 않으면 '세종문화회관'이 세종시가 된다. 길이가 긴 표기가
        # 경계에서 걸려도 짧은 표기를 계속 본다 — '서울시청'은 '서울'로도 안 된다.
        if rest.startswith(form) and (not tail or tail[0] in _BOUNDARY):
            sido, rest = _HEAD_FORMS[form], tail
            break
    if sido is None:
        # 맨 앞이 아니어도 정식 명칭이면 믿는다 — «리움미술관, 서울특별시 용산구».
        # 여기서도 경계를 본다. 안 보면 '제주도립미술관'·'경기도자박물관'처럼
        # 시·도 이름을 앞머리로 쓰는 고유명사가 전부 지역 판정에 걸린다.
        # 여러 개가 걸리면 **가장 앞의 것**이 이 주소의 시·도다.
        hits = [(at, form) for form in _FULL_FORMS
                if (at := rest.find(form)) >= 0
                and rest[at + len(form):][:1] in ("", *_BOUNDARY)]
        if hits:
            at, form = min(hits)
            sido, rest = _FULL_FORMS[form], rest[at + len(form):]

    gu = next((t for t in rest.split()[:_SIGUNGU_SCAN] if _SIGUNGU.match(t)), None)
    return sido, gu


def of_point(point) -> tuple[str | None, str | None]:
    """좌표에 실린 행정구역. 지오코딩이 채워 두지 않았으면 주소 문자열에서 뽑는다."""
    if point is None:
        return None, None
    if point.sido or point.sigungu:
        return point.sido, point.sigungu
    return parse(point.name)


def of_candidate(cand) -> tuple[str | None, str | None]:
    """후보의 행정구역. 지오코딩 값 → 주소 → 좌표에 실린 주소 순으로 본다.

    **이름은 보지 않는다.** '서울주문화센터'(울산)·'세종문화회관'(서울)처럼 이름에
    든 지역명은 틀리는 쪽이 더 많다. 그 판정은 `culture_api._in_region` 의 몫이고,
    거기서도 «[지역]» 태그가 있을 때만 이름을 믿는다.
    """
    sido, gu = of_point(cand.geo)
    if sido or gu:
        return sido, gu
    return parse(cand.address)


def requested(conditions) -> tuple[set[str], set[str]]:
    """요청이 지목한 (시·도 집합, 시군구 집합).

    출발지·도착지는 **일부러 넣지 않는다.** 「판교역에서 출발해서 서초구」는 판교에서
    출발한다는 뜻이지 판교에서 찾아 달라는 뜻이 아니다 —
    `discovery.region_points()` 가 앵커를 고를 때 쓰는 판단과 같다.
    """
    names = conditions.regions or ([conditions.region] if conditions.region else [])
    parsed = [parse(n) for n in names]
    return {s for s, _ in parsed if s}, {g for _, g in parsed if g}
