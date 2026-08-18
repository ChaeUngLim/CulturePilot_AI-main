"""발화에서 «무엇을·몇 개·어디서» 를 집어낸다.

지역·지점·반경·개수·종류별 몫·시각 지정 항목. 전부 독립적으로 동작하고 서로를
부르지 않는다 — 하나가 못 잡아도 나머지는 채워야 하기 때문이다.

지역 인식이 정규식이 아니라 **목록 대조**인 이유가 여기 적혀 있다. '종로구에서'처럼
조사가 붙거나 '강남'처럼 구를 떼고 말하는 경우가 흔한데, 패턴으로 잡으면 둘 중
하나를 놓친다. 시·도 판정(UR-18)은 이것과 별개다 — 그쪽은 `app.tools.region`.
"""
from __future__ import annotations

import re

from app.graph.router.timeparse import _TIME, _to_time
from app.schemas import StopRequest

# 지역 인식은 정규식보다 목록 대조가 정확하다. '종로구에서'처럼 조사가 붙거나
# '강남'처럼 구를 떼고 말하는 경우가 흔한데, 패턴으로 잡으면 둘 중 하나를 놓친다.
_METRO = ("부산", "대구", "인천", "광주", "대전", "울산", "세종", "제주")

_SEOUL_GU = (
    "종로", "중", "용산", "성동", "광진", "동대문", "중랑", "성북", "강북", "도봉",
    "노원", "은평", "서대문", "마포", "양천", "강서", "구로", "금천", "영등포",
    "동작", "관악", "서초", "강남", "송파", "강동",
)
# 동네 이름으로 말하는 경우 — 자치구로 환원한다
_NEIGHBORHOOD = {
    "성수": "성동", "홍대": "마포", "연남": "마포", "합정": "마포", "신촌": "서대문",
    "연희": "서대문", "이태원": "용산", "한남": "용산", "삼청": "종로", "북촌": "종로",
    "인사동": "종로", "압구정": "강남", "청담": "강남", "가로수길": "강남",
    "잠실": "송파", "여의도": "영등포", "을지로": "중", "명동": "중", "익선동": "종로",
}

# 지점 표현. 접미사를 열거하면 '서울숲'·'경복궁'처럼 빠지는 게 계속 생기므로,
# '~ 근처/주변/앞' 앞에 오는 명사구를 통째로 잡고 지역명만 걸러낸다.
_LANDMARK = re.compile(
    r"(?:^|[\s,])([가-힣A-Za-z0-9]{2,15}(?:\s[가-힣A-Za-z0-9]{1,10})?)"
    r"\s*(?:근처|주변|인근|바로\s*앞|앞|일대|부근)"
)
# 지점으로 보기 어려운 말들 — 지역명이거나 의미가 없는 단어
_NOT_LANDMARK = {"이", "그", "저", "여기", "거기", "우리", "집", "회사", "학교", "지금"}
# 지점 후보에서 걸러낼 수량·시간 표현. '2시간 남는데' 같은 말이 지점으로 잡히면
# 그 문자열로 지오코딩을 시도하고, 실패하면 탐색 범위가 통째로 사라진다.
_QUANTITY = re.compile(r"\d\s*(?:시간|분|시|곳|개|군데|명|인|박|일)|남는데|남았")
# 반경 힌트 — '도보 10분', '반경 1km'
_WALK_MIN = re.compile(r"도보\s*(\d{1,2})\s*분")
_RADIUS_KM = re.compile(r"반경\s*(\d+(?:\.\d+)?)\s*(km|킬로|m|미터)")

# 목적 표현 — 시각 뒤에 오는 말로 무엇을 하려는지 구분한다
_PURPOSE = (
    (("식사", "밥", "저녁", "점심", "먹을", "맛집", "레스토랑"), "meal"),
    (("카페", "커피", "디저트", "디져트", "차 마", "빵", "베이커리"), "cafe"),
    (("쉬", "휴식", "쉴"), "rest"),
)

_COUNT = re.compile(r"(\d+)\s*(?:개|곳|군데|가지)")
_COUNT_KO = {"두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8}


def _detect_count(query: str) -> int | None:
    """'5개 정도', '다섯 곳' — 몇 곳을 원하는지."""
    m = _COUNT.search(query)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 12 else None
    for word, n in _COUNT_KO.items():
        if re.search(rf"{word}\s*(?:개|곳|군데)", query):
            return n
    return None


# 개수 앞에 붙는 말 → KIND_GROUPS 의 그룹 이름.
#
# **순서가 규칙이다.** 위에서부터 먼저 맞는 것을 쓴다. "디저트 맛집"에는 '디저트'와
# '맛집'이 둘 다 들어 있는데, 이건 디저트를 파는 곳이지 식당이 아니다. 그래서
# 합성어를 낱말보다 위에 둔다 — 낱말만 나열하면 뒤에 오는 '맛집'이 이겨서
# 카페를 기대한 사용자에게 밥집이 나간다.
_KIND_WORDS: tuple[tuple[str, str], ...] = (
    ("디저트 맛집", "cafe"), ("디져트 맛집", "cafe"),
    ("디저트", "cafe"), ("디져트", "cafe"), ("카페", "cafe"),
    ("베이커리", "cafe"), ("빵집", "cafe"),
    ("맛집", "food"), ("식사", "food"), ("밥집", "food"),
    ("점심", "food"), ("저녁", "food"),
    ("문화", "culture"), ("전시", "culture"), ("공연", "culture"),
    ("미술관", "culture"), ("박물관", "culture"), ("갤러리", "culture"),
    ("쇼핑", "shop"), ("서점", "shop"),
    ("공원", "outdoor"), ("산책", "outdoor"),
)

# 개수 표현 바로 앞에서 종류를 찾는 창. 너무 넓히면 "성수동에서 전시 보고
# 카페까지 3곳"에서 '전시'가 잡혀 엉뚱한 그룹에 개수가 붙는다.
_KIND_WINDOW = 14


def _detect_kind_quota(query: str) -> dict[str, int]:
    """'문화 2개, 디저트 3개' → {'culture': 2, 'cafe': 3}.

    개수 표현을 모두 찾고, 각각의 **바로 앞 구간**에서 종류 낱말을 되짚는다.
    문장 전체에서 찾으면 멀리 떨어진 낱말이 붙어 버린다.

    같은 그룹이 여러 번 나오면 **마지막 값**을 쓴다 — 사람이 말을 고쳐 다시
    말하는 경우("두 개, 아니 세 개")가 앞을 덮는 게 자연스럽다.
    """
    quota: dict[str, int] = {}
    prev_end = 0            # 앞 개수 표현의 끝. 창이 그 앞으로 넘어가면 안 된다
    for m in _COUNT.finditer(query):
        n = int(m.group(1))
        if not 1 <= n <= 12:
            prev_end = m.end()
            continue
        # 창은 «앞 개수 표현 다음»부터 본다. 그러지 않으면 "문화 2개 디저트 3개"의
        # 두 번째 창이 앞 쌍까지 삼켜 종류가 둘로 보이고, 아래 총량 판정에 걸려
        # 디저트 몫이 통째로 사라진다.
        window = query[max(prev_end, m.start() - _KIND_WINDOW):m.start()]
        prev_end = m.end()
        # 창 안에 **서로 다른 종류가 둘 이상** 나열돼 있으면 그 개수는 총량이지
        # 어느 한 종류의 몫이 아니다 — "문화 및 식사 5개", "디저트나 식사 5가지".
        # 이걸 안 거르면 `_KIND_WORDS` 순서상 뒤에 걸린 하나가 개수를 독차지해
        # {'food': 5} 처럼 잘못된 몫이 된다. 총량과 값이 같아 오래 가려져 있었다.
        if len(_mentioned_kind_groups(window)) > 1:
            continue
        for word, group in _KIND_WORDS:
            if word in window:
                quota[group] = n
                break
    return quota


def _mentioned_kind_groups(query: str) -> set[str]:
    """발화에 나온 장소 종류 그룹 전부. 개수를 말했든 안 말했든 센다.

    겹치는 낱말은 **더 구체적인 쪽이 먹는다** — `_KIND_WORDS` 가 구체적인 표현을
    앞에 두므로, 먼저 걸린 낱말을 지워 뒤에서 다시 걸리지 않게 한다.
    '디저트 맛집'을 cafe 하나로 세지 않고 '맛집'까지 따로 세면, 사용자가 말한 적
    없는 food 그룹이 생겨 «개수를 말하지 않은 종류»가 있다고 잘못 판단한다.
    """
    text = query
    found: set[str] = set()
    for word, group in _KIND_WORDS:
        if word in text:
            found.add(group)
            text = text.replace(word, " " * len(word))
    return found


def _detect_stops(query: str) -> list[StopRequest]:
    """시각이 지정된 요청들을 순서대로 뽑는다.

    "9시에 강남역 메가박스에서 영화, 13시에 예술의전당 공연, 5시에 식사"
      → [09:00 culture(강남역 메가박스), 13:00 culture(예술의전당), 17:00 meal]

    시각을 무시하고 스케줄러가 알아서 배치하면 사용자가 잡아둔 약속과 어긋난다.
    """
    matches = list(_TIME.finditer(query))
    stops: list[StopRequest] = []
    for i, m in enumerate(matches):
        at = _to_time(m)          # 오전/오후 해석은 _to_time 한 곳에서만 한다
        if at is None:
            continue

        # 이 시각 뒤부터 다음 시각 전까지가 이 항목의 설명이다
        tail_end = matches[i + 1].start() if i + 1 < len(matches) else len(query)
        tail = query[m.end():tail_end].strip()

        purpose = "any"
        for words, value in _PURPOSE:
            if any(w in tail for w in words):
                purpose = value
                break
        if purpose == "any" and tail:
            purpose = "culture"

        stops.append(StopRequest(
            at=at,
            place_hint=_place_hint(tail),
            purpose=purpose,      # type: ignore[arg-type]
            note=tail[:60] or None,
        ))

    # 시각 없이 "문화생활과 식사"처럼 말한 경우. 시각을 지정하지 않았다고
    # 요청을 흘려보내면 스케줄러가 문화 일정으로 하루를 꽉 채워, 정작
    # 사용자가 말한 식사가 빠진 일정이 나온다.
    for purpose, words, note in (
        ("meal", _MEAL_WORDS, "시각 미지정 — 식사 시간대에 배치"),
        ("cafe", ("카페", "커피", "디저트", "디져트"), "시각 미지정 — 쉬어 가는 자리"),
    ):
        already = any(s.purpose == purpose for s in stops)
        if not already and any(w in query for w in words):
            stops.append(StopRequest(at=None, purpose=purpose,  # type: ignore[arg-type]
                                     fixed=False, note=note))
    return stops


_MEAL_WORDS = ("식사", "밥", "점심", "저녁", "맛집", "먹을", "레스토랑", "식당")


# 목적만 말한 경우 — 장소명이 아니다
_PURPOSE_ONLY = ("식사", "밥", "저녁", "점심", "카페", "커피", "휴식", "디저트", "맛집")


def _place_hint(tail: str) -> str | None:
    """설명에서 장소 단서를 뽑는다. '에 강남역 메가박스에서 볼 만한' → '강남역 메가박스'."""
    if not tail:
        return None

    # 시각 뒤에 붙은 조사를 먼저 떼어낸다 ('9시' + '에 강남역…')
    for lead in ("에서 ", "에 ", "부터 ", "쯤 ", "경 ", "에", "쯤", "경"):
        if tail.startswith(lead):
            tail = tail[len(lead):]
            break

    # 조사·서술어 앞까지가 장소명일 가능성이 높다
    for cut in ("에서", " 에 ", " 가", " 갈", " 볼", " 관람", " 공연", " 예매",
                " 추천", " 하기", " 좋은", " 시청"):
        idx = tail.find(cut)
        if idx > 1:
            tail = tail[:idx]
            break

    name = tail.strip(" ,.·")
    if not (2 <= len(name) <= 25):
        return None
    # '식사하기 좋은 곳' 같은 목적 표현은 장소 단서가 아니다
    if any(w in name for w in _PURPOSE_ONLY):
        return None
    return name


def _detect_landmark(query: str) -> str | None:
    """'신촌역 근처' 같은 지점 표현을 뽑는다.

    구 단위로만 해석하면 '신촌역 근처'가 서대문구 전체가 되어, 도보로 갈 수 없는
    곳까지 추천된다. 지점이 있으면 그걸 출발점으로 삼는다.
    """
    for m in _LANDMARK.finditer(query):
        name = m.group(1).strip()
        if len(name) < 2 or name in _NOT_LANDMARK:
            continue
        # 수량·시간 표현은 장소가 아니다. '2시간 남는데 근처 뭐 있어?'에서
        # '2시간 남는데'를 지점으로 집어, 탐색 앵커가 통째로 엉뚱해졌다.
        if _QUANTITY.search(name):
            continue
        # '강남구 근처'처럼 지역명을 말한 거면 지점이 아니라 구로 다룬다
        if name.endswith(("구", "시", "군", "도")) and _detect_regions(name):
            continue
        return name
    return None


def _detect_radius(query: str, landmark: str | None) -> int | None:
    """반경(m). 명시가 없으면 지점 기준일 때만 도보권으로 좁힌다."""
    w = _WALK_MIN.search(query)
    if w:
        return min(int(w.group(1)) * 75, 3000)      # 도보 1분 ≈ 75m
    r = _RADIUS_KM.search(query)
    if r:
        value = float(r.group(1))
        return int(value * 1000) if r.group(2) in ("km", "킬로") else int(value)
    if "도보" in query or "걸어서" in query:
        return 800
    return 1200 if landmark else None                # 지점 기준이면 도보권 기본값


def _detect_regions(query: str) -> list[str]:
    """언급된 지역을 모두 찾는다. 자치구 > 동네 > 광역시 순으로 좁은 범위를 우선한다.

    '서대문구랑 마포구' 처럼 여러 곳을 말하는 경우가 흔하다. 하나만 잡으면
    나머지는 조용히 무시되어 사용자가 이유를 알 수 없다.
    """
    found: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name not in seen:
            seen.add(name)
            found.append(name)

    # '중구'는 '중랑구'와 겹치므로 긴 이름부터 본다
    for gu in sorted(_SEOUL_GU, key=len, reverse=True):
        if f"{gu}구" in query:
            add(f"서울 {gu}구")
    for hood, gu in _NEIGHBORHOOD.items():
        if hood in query:
            add(f"서울 {gu}구")
    if not found:
        for gu in sorted(_SEOUL_GU, key=len, reverse=True):
            if len(gu) >= 2 and gu in query:   # '강남', '서초' 처럼 구를 뗀 표현
                add(f"서울 {gu}구")
    for metro in _METRO:
        if metro in query:
            add(metro)
    if not found and "서울" in query:
        add("서울")
    return found

