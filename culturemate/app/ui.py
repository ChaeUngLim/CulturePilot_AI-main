"""화면 명세 — `docs/PLANNING/` 의 참조 화면 23장을 **데이터**로 옮긴 것.

이 모듈은 화면을 그리지 않는다. 이 저장소의 UI 는 React Native(TS)이고, Python 이
픽셀을 만드는 지점은 어디에도 없다. 그런데도 화면 명세를 파이썬에 두는 이유는
**말과 값이 갈리는 지점이 서버에 있기 때문**이다.

기획 화면은 «배우자와»를 묻고, 서버 `TripConditions.companions` 에는 `spouse` 가
없다. 화면은 «1박 2일»을 묻고, `Itinerary` 는 날짜 하나만 갖는다. 화면은 «문화·예술·
역사»를 묻고, 후보 점수는 카탈로그 카테고리 문자열이 **글자까지 같을 때만** 움직인다
(`mobile/app/taste-cards.tsx` 의 DECK 주석과 같은 함정이다). 이 어긋남을 TSX 안에
흩어 두면 화면은 멀쩡히 동작하는데 개인화만 조용히 꺼진 상태가 된다 — 가장 찾기
어려운 고장이다.

그래서 화면의 선택지 하나하나가 **서버의 어느 필드에 어떤 값으로 쓰이는지**를
여기 한곳에 적고, `validate()` 가 그것을 실제 `TripConditions` Literal 과 대조한다.
스키마가 바뀌면 화면이 바뀌기 전에 여기서 먼저 깨진다.

쓰는 곳 세 가지:

1. **서버 주도 화면** — `spec()` 을 그대로 JSON 으로 내리면 앱이 위저드를 렌더한다.
   선택지 문구를 고치려고 앱을 새로 배포하지 않아도 된다.
2. **정합성 테스트** — `validate()` 가 빈 리스트를 돌려주는지만 보면 된다.
3. **설계 문서의 근거** — `docs/UI.md` 의 표는 전부 이 모듈에서 나온 값이다.

`supported=False` 인 선택지는 **지우지 않는다.** 지우면 «원래 없었다»로 읽혀
기획안과의 차이를 추적할 수 없다. 참조 화면에 있었고 지금 백엔드에 길이 없다는
사실 자체가 다음 작업 목록이다.
"""
from __future__ import annotations

from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel, Field

from app.schemas import KIND_GROUPS, TripConditions
from app.tools.region import SIDO_FULL

# ════════════════════════════════════════════════════════ 1. 디자인 토큰
#
# `mobile/src/theme.ts` 와 **같은 값**이어야 한다. 여기가 사본인 이유는 서버가
# 화면 명세를 내릴 때 색까지 함께 내려야 앱이 임의로 해석하지 않기 때문이다.
# 값이 갈리면 `validate()` 가 아니라 눈으로 잡힌다 — 그래서 한 곳에 모아 둔다.

COLORS: dict[str, str] = {
    "bg": "#0F1115",
    "surface": "#171A21",
    "surfaceAlt": "#1F232C",
    "border": "#2A2F3A",
    "text": "#ECEEF3",
    "textDim": "#9AA3B2",
    "textFaint": "#6B7382",
    "accent": "#7C9CF5",
    "accentSoft": "#243049",
    "warn": "#F0B457",
    "warnSoft": "#3A2E17",
    "danger": "#E8797A",
    "dangerSoft": "#3A2022",
    "ok": "#6FCF97",
    "okSoft": "#1D3226",
}

RADIUS: dict[str, int] = {"sm": 8, "md": 12, "lg": 16, "xl": 22}

#: 간격 단위. 화면 코드의 `space(3)` 은 12px 이다.
SPACE_UNIT = 4


def space(n: int) -> int:
    return n * SPACE_UNIT


TYPE_SCALE: dict[str, dict[str, Any]] = {
    "h1": {"size": 26, "weight": 700, "color": COLORS["text"]},
    "h2": {"size": 19, "weight": 700, "color": COLORS["text"]},
    "h3": {"size": 16, "weight": 600, "color": COLORS["text"]},
    "body": {"size": 15, "weight": 400, "color": COLORS["text"], "line": 22},
    "small": {"size": 13, "weight": 400, "color": COLORS["textDim"], "line": 19},
    "tiny": {"size": 11, "weight": 400, "color": COLORS["textFaint"]},
}

# 참조 화면(Triple·Wanderlog)은 흰 배경에 주황/파랑 강조다. 이 앱은 어두운 배경이라
# 그대로 옮기면 대비가 무너진다. 옮기는 것은 **레이아웃과 단계 구성**이지 색이 아니다.
REFERENCE_PALETTE_NOTE = (
    "참조 화면의 #FF5A3C(주황)·#3B82F6(파랑)은 밝은 배경 전제다. "
    "이 앱은 colors.bg 가 #0F1115 이므로 강조색은 accent(#7C9CF5)를 쓴다."
)


# ════════════════════════════════════════════════════════ 2. 서버 열거형 라벨
#
# 값(key)은 서버 Literal 과 같은 집합이어야 한다. `validate()` 가 대조한다.

COMPANIONS_LABEL: dict[str, str] = {
    "solo": "혼자", "couple": "연인·배우자와", "friends": "친구와",
    "family": "가족과", "kids": "아이와", "unknown": "정하지 않음",
}

TRANSPORT_LABEL: dict[str, str] = {
    "best": "최적", "walk": "도보", "subway": "지하철", "bus": "버스",
    "car": "자동차", "bike": "자전거", "unknown": "정하지 않음",
}

INDOOR_LABEL: dict[str, str] = {"indoor": "실내", "outdoor": "야외", "any": "상관없음"}

#: `mobile/src/constants.ts` 의 KIND_LABEL 과 같아야 한다.
KIND_LABEL: dict[str, str] = {
    "event": "행사", "venue": "문화공간", "food": "식당", "cafe": "카페",
    "shop": "상점", "park": "야외", "other": "기타",
}

#: `mobile/src/constants.ts` 의 FRICTION_LABEL 과 같아야 한다.
FRICTION_LABEL: dict[str, str] = {
    "parking": "주차", "crowding": "혼잡", "accessibility": "접근성",
    "waiting": "대기", "noise": "소음", "cost": "비용",
    "reservation": "예약", "transit": "교통", "weather": "날씨",
}

#: 취향 카드 3지 반응. verdict 4값 중 dislike 는 화면에 두지 않는다 —
#: «관심 없어요»(not_interested)와 겪고 내린 «별로였어요»는 무게가 달라야 하는데,
#: 겪은 것은 방문 기록(POST /visits)이 훨씬 정확하게 받는다.
VERDICT_LABEL: dict[str, str] = {
    "recommend": "추천해요", "dislike": "별로였어요",
    "interested": "기대돼요", "not_interested": "관심 없어요",
}

#: 카탈로그가 실제로 쓰는 카테고리. `scripts/_catalog_data.py` 5번째 열과 같아야 한다.
#: 화면이 «전시»라고 묻고 카탈로그가 «전시관»이면 그 취향은 어떤 후보와도 안 만난다.
CATALOG_CATEGORIES: tuple[str, ...] = (
    "미술관", "박물관", "공연장", "독립서점", "독립영화관", "공방",
    "복합문화공간", "갤러리", "야외공연장", "거리", "도서관", "문화유산", "전시관",
)


# ════════════════════════════════════════════════════════ 3. 명세 모델

StepKind = Literal[
    "single",    # 하나만 고른다 (칩)
    "multi",     # 여러 개 고른다 (칩)
    "toggle",    # 켜고 끈다
    "calendar",  # 날짜 하나 또는 범위
    "list",      # 카드 목록에서 체크
    "search",    # 지도 + 검색 + 바텀시트
    "progress",  # 진행 표시
    "result",    # 결과(지도 + 타임라인)
]


class Choice(BaseModel):
    """선택지 한 개.

    `writes` 는 **`TripConditions` 필드명 → 값**이다. 앱은 이것을 모아
    `POST /chat` 의 `conditions_override` 로 보낸다. 그 밖의 곳(PlanFlags 등)에는
    쓸 수 없다 — 라우터가 `_apply_override()` 에서 조건만 병합하기 때문이다.
    """

    id: str
    label: str
    glyph: str = ""
    writes: dict[str, Any] = Field(default_factory=dict)
    #: 백엔드에 길이 있는가. False 여도 목록에서 지우지 않는다(모듈 docstring 참고).
    supported: bool = True
    note: str | None = None


class Step(BaseModel):
    id: str
    title: str
    subtitle: str = ""
    kind: StepKind
    choices: list[Choice] = Field(default_factory=list)
    #: 건너뛸 수 있는가 (참조 화면의 «다음에 하기»).
    optional: bool = False
    #: 근거가 된 `docs/PLANNING/` 파일. 화면 문구를 고칠 때 원본을 다시 볼 지점이다.
    source: str = ""


class Screen(BaseModel):
    id: str
    title: str
    #: expo-router 경로. 빈 문자열이면 **아직 없는 화면**이다.
    route: str = ""
    #: 이 화면이 부르는 엔드포인트.
    api: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    note: str | None = None


class Flow(BaseModel):
    id: str
    title: str
    #: 어디서 들어오는가.
    entry: str
    screens: list[Screen] = Field(default_factory=list)


# ════════════════════════════════════════════════════════ 4. 선택지 묶음

#: 「1.첫 실행 화면 취향 선호도」 — 이 여행은 어떤 주제여야 하나요?
#: kind_quota 의 키는 `schemas.KIND_GROUPS` 의 그룹명이다(PlaceKind 가 아니다).
#: 여러 장을 고르면 앱이 같은 키의 값을 **더해서** 보낸다.
THEME_CARDS: list[Choice] = [
    Choice(id="landmark", label="주요 명소", glyph="🗽",
           writes={"kind_quota": {"culture": 1}}),
    Choice(id="food", label="음식 및 음료", glyph="🍧",
           writes={"kind_quota": {"food": 1, "cafe": 1}}),
    Choice(id="museum", label="박물관 및 문화", glyph="🏛",
           writes={"kind_quota": {"culture": 2}, "interests": ["박물관", "미술관"]}),
    Choice(id="nature", label="공원 및 자연", glyph="🌲",
           writes={"kind_quota": {"outdoor": 1}, "indoor_pref": "outdoor"}),
    Choice(id="shopping", label="쇼핑", glyph="🛍",
           writes={"kind_quota": {"shop": 1}}),
    Choice(id="nightlife", label="바 및 야간 활동", glyph="🍸",
           writes={"end_time": "22:00"},
           note="전용 PlaceKind 가 없다. 종료 시각을 늦춰 근사할 뿐이라 "
                "«바»가 후보에 오르지는 않는다."),
]

#: 같은 화면 아래쪽 「✨ 과거 여행에서 배우기」 토글.
LEARN_FROM_PAST = Choice(
    id="use_archive", label="과거 여행에서 배우기", glyph="✨",
    writes={}, supported=False,
    note="아카이브 사용 여부는 PlanFlags.use_archive 이고, 라우터가 정한다. "
         "conditions_override 로는 닿지 않는다. 화면에 두려면 ChatRequest 에 "
         "필드를 하나 더 만들어야 한다.",
)

#: 「5.동행자 카드」·「4.동행자 선택」.
#: 참조 화면은 «다중 선택이 가능해요»라고 적혀 있지만 서버 companions 는 단일 Literal
#: 이다. 다중으로 받으면 마지막 하나만 살아남아 사용자가 고른 것과 달라진다 —
#: 그래서 이 단계는 single 로 둔다.
COMPANION_CARDS: list[Choice] = [
    Choice(id="solo", label="혼자", writes={"companions": "solo", "party_size": 1}),
    Choice(id="friends", label="친구와", writes={"companions": "friends", "party_size": 2}),
    Choice(id="couple", label="연인과", writes={"companions": "couple", "party_size": 2}),
    Choice(id="spouse", label="배우자와", writes={"companions": "couple", "party_size": 2},
           note="서버에 spouse 가 없어 couple 로 접힌다. 취향 프로필의 "
                "companion_prefs 도 couple 키로 쌓인다."),
    Choice(id="kids", label="아이와", writes={"companions": "kids", "party_size": 3}),
    Choice(id="parents", label="부모님과", writes={"companions": "family", "party_size": 3}),
    Choice(id="etc", label="기타", writes={"companions": "unknown"}),
]

#: 「6·7.선호 문화 카드」 — 내가 선호하는 여행 스타일은?
#: interests 값은 **CATALOG_CATEGORIES 안에 있어야** 점수에 반영된다.
STYLE_CARDS: list[Choice] = [
    Choice(id="activity", label="체험·액티비티", writes={"interests": ["공방"]}),
    Choice(id="sns", label="SNS 핫플레이스",
           writes={"interests": ["복합문화공간", "갤러리"]}),
    Choice(id="nature", label="자연과 함께",
           writes={"interests": ["야외공연장", "거리"], "indoor_pref": "outdoor"}),
    Choice(id="famous", label="유명 관광지는 필수",
           writes={"interests": ["문화유산", "박물관"]}),
    Choice(id="healing", label="여유롭게 힐링",
           writes={"interests": ["도서관", "독립서점"], "dwell_min": 60}),
    Choice(id="culture", label="문화·예술·역사",
           writes={"interests": ["미술관", "박물관", "공연장", "문화유산"]}),
    Choice(id="local", label="여행지 느낌 물씬",
           writes={"interests": ["거리", "문화유산"]}),
    Choice(id="shopping", label="쇼핑은 열정적으로",
           writes={"interests": ["복합문화공간"], "kind_quota": {"shop": 2}}),
    Choice(id="foodie", label="관광보다 먹방",
           writes={"kind_quota": {"food": 2, "cafe": 1}}),
]

#: 「7.선호하는 문화 카드」(5/5) — 빼곡 / 널널.
#: 숫자는 `subgraphs/itinerary/` 가 실제로 읽는 stop_count·dwell 범위다.
PACE_CARDS: list[Choice] = [
    Choice(id="packed", label="빼곡한 일정 선호",
           writes={"stop_count": 6, "dwell_max": 60}),
    Choice(id="relaxed", label="널널한 일정 선호",
           writes={"stop_count": 3, "dwell_min": 90}),
]

#: 「4.문화 생활 추천 일정 카드」(2/5) — 여행 기간은?
#: `Itinerary` 는 date 하나짜리다. 여러 날은 «일정 여러 건»이지 한 일정이 아니다.
DURATION_CARDS: list[Choice] = [
    Choice(id="d1", label="당일치기", writes={}),
    *[
        Choice(id=f"n{n}", label=f"{n}박 {n + 1}일", writes={}, supported=False,
               note="Itinerary 가 하루 단위다. 지금 구조에서는 날짜별로 "
                    "POST /chat 을 n+1 회 돌려 GET /plans 로 묶는 수밖에 없다.")
        for n in (1, 2, 3, 4, 5)
    ],
]

#: 「3.국내 일정만 추천 카드」(1/5)·「2.국내문화생활 지역 선택」.
#: 참조 화면은 «가평·양평»처럼 생활권 묶음이지만, 이 백엔드의 지역 판정은
#: `tools/region.py` 의 **시·도** 단위다. 묶음 칩을 그대로 쓰면 어느 시·도로
#: 보낼지 화면이 임의로 정하게 되므로 시·도로 편다.
CATALOG_COVERAGE: dict[str, int] = {"서울": 52, "인천": 10, "부산": 10, "대전": 10, "대구": 10}

REGION_CARDS: list[Choice] = [
    Choice(
        id=short,
        label=short,
        writes={"regions": [short]},
        note=(f"로컬 카탈로그 {CATALOG_COVERAGE[short]}곳" if short in CATALOG_COVERAGE
              else "로컬 카탈로그 없음 · 한국문화정보원 API 응답에만 의존한다"),
    )
    for short in SIDO_FULL
]

#: 참조 화면 1/5 아래쪽의 「유럽」 탭. 국내 전용 데이터 소스라 길이 없다.
OVERSEAS = Choice(
    id="overseas", label="해외 도시", writes={}, supported=False,
    note="탐색 소스가 한국문화정보원 API·카카오 로컬·국내 카탈로그뿐이다. "
         "지역 판정(tools/region.py)도 국내 시·도 표만 갖는다.",
)

TRANSPORT_CARDS: list[Choice] = [
    Choice(id=key, label=label, writes={"transport": key})
    for key, label in TRANSPORT_LABEL.items()
    if key != "unknown"
]


# ════════════════════════════════════════════════════════ 5. 진행 화면
#
# 「3.자동 또는 수동 선택 후 화면」은 진행 상황을 5줄로 보여 준다. 그 5줄은 이 앱의
# 노드와 1:1이 아니다. 아래 표가 **참조 문구 → 실제 노드**의 대응이고, 화면에는
# 실제 노드 라벨(NODE_LABEL)을 쓴다 — 없는 단계를 그려 놓으면 진행 표시가 멈춘 것처럼
# 보이는 순간이 반드시 온다.

#: `mobile/src/hooks/useCultureMate.ts` 의 NODE_LABEL 과 같아야 한다.
PROGRESS_STAGES: list[tuple[str, str]] = [
    ("classify", "요청 분석"),
    ("archive", "개인 아카이브 조회"),
    ("discovery", "문화 콘텐츠 탐색·검증"),
    ("current_plan", "기존 일정 확인"),
    ("merge_context", "정보 통합"),
    ("itinerary", "일정·동선 생성"),
    ("validation", "일정 검증"),
    ("hitl", "사용자 확인"),
    ("finalize", "선택 반영"),
    ("persist", "아카이브 저장"),
    ("compose", "응답 작성"),
]

REFERENCE_PROGRESS: list[tuple[str, str]] = [
    ("여행을 생성 중입니다", "classify"),
    ("선호 설정 적용 중", "archive"),
    ("N곳 지도에 표시 중", "discovery"),
    ("빈틈 채우기", "itinerary"),      # subgraphs/itinerary/gaps.py
    ("경로 최적화 중", "itinerary"),   # subgraphs/itinerary/routes.py · legs.py
]

#: 참조 화면은 「⏱ ~20초 남음」을 적는다. 실측 중앙값은 5.1초(HANDOFF §1)라
#: 남은 시간을 세는 대신 도착한 노드를 그리는 편이 정직하다.
PROGRESS_NOTE = "남은 시간 대신 도착한 노드를 그린다. 응답 중앙값 5.1초 · NFR-01 상한 15초."


# ════════════════════════════════════════════════════════ 6. 엔드포인트 지도
#
# `app/api/main.py` 의 23개. 화면 명세가 부를 수 있는 것의 전량이다.

ENDPOINTS: list[tuple[str, str, str]] = [
    ("POST", "/chat", "일정 생성·수정 (SSE 스트리밍)"),
    ("POST", "/chat/sync", "일정 생성·수정 (동기)"),
    ("POST", "/resume", "확인 카드 결정 반영 (SSE)"),
    ("POST", "/resume/sync", "확인 카드 결정 반영 (동기)"),
    ("GET", "/threads/{thread_id}/state", "스레드 현재 상태 · 조건 칩"),
    ("POST", "/reroute", "출발·도착·수단을 바꿔 동선 재계산"),
    ("POST", "/threads/{thread_id}/routes", "구간 실측 경로 재조회"),
    ("POST", "/threads/{thread_id}/verify", "일정 재검증"),
    ("GET", "/threads/{thread_id}/evidence/{evidence_id}", "판단 근거 열람 (UR-14)"),
    ("POST", "/visits", "방문 기록 등록"),
    ("GET", "/report/{user_id}", "취향 리포트"),
    ("POST", "/preferences/cards", "취향 카드 묶음 저장"),
    ("GET", "/preferences/cards/{user_id}", "취향 카드 조회"),
    ("GET", "/plans/{user_id}", "기간별 일정 목록 (캘린더)"),
    ("GET", "/plans/detail/{plan_id}", "일정 상세"),
    ("POST", "/collections", "저장 목록 만들기"),
    ("DELETE", "/collections/{collection_id}", "저장 목록 삭제"),
    ("DELETE", "/collections/{collection_id}/places/{place_id}", "목록에서 장소 빼기"),
    ("GET", "/curations/{user_id}", "큐레이션"),
    ("GET", "/geocode", "장소명 → 좌표"),
    ("GET", "/whereami", "좌표 → 자치구"),
    ("GET", "/health", "생존 확인"),
    ("GET", "/diagnostics", "외부 API 프로브 11종"),
]


# ════════════════════════════════════════════════════════ 7. 흐름

FLOW_ONBOARDING = Flow(
    id="onboarding",
    title="첫 실행 · 비로그인 카드 선택",
    entry="앱 최초 실행 (아카이브 0건)",
    screens=[
        Screen(
            id="taste",
            title="여행 선호도",
            route="/taste-cards",
            api=["POST /preferences/cards", "GET /preferences/cards/{user_id}"],
            note="화면은 이미 있다. 지금 DECK 은 카탈로그 카테고리 12장이고 "
                 "참조 화면은 주제 6장이다. 둘을 합치면 카드가 18장이 되어 "
                 "첫 실행에서 이탈한다 — 주제 6장을 앞에 두고 카테고리는 "
                 "«취향» 탭에서 이어 받는 편이 낫다.",
            steps=[
                Step(id="theme", title="이 여행은 어떤 주제여야 하나요?", kind="multi",
                     choices=THEME_CARDS,
                     source="첫번째 화면 비로그인 카드 선택 화면/"
                            "1.첫 실행 화면 취향 선호도(주제 별 나이 별 장소 별).jpg"),
                Step(id="learn", title="과거 여행에서 배우기", kind="toggle",
                     choices=[LEARN_FROM_PAST], optional=True,
                     source="첫번째 화면 비로그인 카드 선택 화면/"
                            "1.첫 실행 화면 취향 선호도(주제 별 나이 별 장소 별).jpg"),
            ],
        ),
        Screen(
            id="branch",
            title="일별 일정표를 원하시나요?",
            route="",
            steps=[
                Step(id="mode", title="누가 짤까요", kind="single",
                     subtitle="저희가 계획을 세울게요. 언제든지 모든 것을 수정할 수 있습니다.",
                     choices=[
                         Choice(id="auto", label="나를 위한 일일 일정 만들기", glyph="✨"),
                         Choice(id="manual", label="제가 직접 할게요"),
                     ],
                     source="첫번째 화면 비로그인 카드 선택 화면/"
                            "2.나를 위한 일일 일정 만들기 또는 제가 직접 할게요.jpg"),
                Step(id="gapfill", title="하루를 채울 추가 장소를 추가하세요",
                     kind="toggle",
                     choices=[Choice(
                         id="nearby_fill", label="빈틈 채우기", writes={},
                         supported=False,
                         note="PlanFlags.nearby_fill 은 라우터가 정한다. "
                              "conditions_override 로는 닿지 않는다 — "
                              "LEARN_FROM_PAST 와 같은 제약이다.")],
                     optional=True,
                     source="첫번째 화면 비로그인 카드 선택 화면/"
                            "2.나를 위한 일일 일정 만들기 또는 제가 직접 할게요.jpg"),
            ],
        ),
        Screen(
            id="pick",
            title="직접 장소 선택하기",
            route="",
            api=["GET /curations/{user_id}"],
            note="«모두 선택» 토스트가 목록 위에 뜬다. 고른 장소는 "
                 "TripConditions.must_include 로 보낸다.",
            steps=[
                Step(id="places", title="직접 장소 선택하기", kind="list",
                     source="첫번째 화면 비로그인 카드 선택 화면/"
                            "2.제가 직접  할게요 선택 시 화면.jpg"),
            ],
        ),
        Screen(
            id="progress",
            title="일일 일정표 작성 중",
            route="/(tabs)",
            api=["POST /chat"],
            note="ProgressTrace 컴포넌트가 이미 이 역할을 한다. 참조 화면처럼 "
                 "전체 화면으로 띄우려면 그 컴포넌트를 그대로 키우면 된다.",
            steps=[
                Step(id="stages", title="일일 일정표 작성 중", kind="progress",
                     subtitle=PROGRESS_NOTE,
                     source="첫번째 화면 비로그인 카드 선택 화면/3.자동 또는 수동 선택 후 화면.jpg"),
            ],
        ),
        Screen(
            id="result",
            title="일정 및 지도",
            route="/(tabs)",
            api=["GET /threads/{thread_id}/state", "POST /threads/{thread_id}/routes"],
            steps=[
                Step(id="map", title="지도 + 타임라인", kind="result",
                     source="첫번째 화면 비로그인 카드 선택 화면/"
                            "4.카드 선택 완료 후 일정 및 지도 추천 화면.jpg"),
            ],
        ),
    ],
)

FLOW_AI_PLAN = Flow(
    id="ai_plan",
    title="AI 일정 추천받기 (5단계 위저드)",
    entry="캘린더 탭의 + 버튼 → «AI 일정 추천받기»",
    screens=[
        Screen(
            id="wizard",
            title="AI 일정 추천받기",
            route="",
            api=["POST /chat"],
            note="다섯 단계의 선택을 한 번에 모아 conditions_override 로 보낸다. "
                 "단계마다 서버를 부르면 4단계에서 되돌아갔을 때 앞 단계 결과가 "
                 "이미 쓰인 뒤가 된다.",
            steps=[
                Step(id="region", title="어디로 떠나시나요?", kind="single",
                     subtitle="1/5", choices=[*REGION_CARDS, OVERSEAS],
                     source="두번째 화면 선택 AI 일정 추천받기/3.국내 일정만 추천 카드.jpg"),
                Step(id="duration", title="여행 기간은?", kind="single",
                     subtitle="2/5 · 원하는 기간을 선택해 주세요.", choices=DURATION_CARDS,
                     source="두번째 화면 선택 AI 일정 추천받기/4.문화 생활 추천 일정 카드.jpg"),
                Step(id="companions", title="누구와 떠나나요?", kind="single",
                     subtitle="3/5", choices=COMPANION_CARDS,
                     source="두번째 화면 선택 AI 일정 추천받기/5.동행자 카드.jpg"),
                Step(id="style", title="내가 선호하는 여행 스타일은?", kind="multi",
                     subtitle="4/5 · 다중 선택이 가능해요.", choices=STYLE_CARDS,
                     source="두번째 화면 선택 AI 일정 추천받기/6.선호 문화 카드.jpg"),
                Step(id="pace", title="선호하는 여행 일정은?", kind="single",
                     subtitle="5/5 · 선택해주신 스타일로 일정을 만들어드려요.",
                     choices=PACE_CARDS,
                     source="두번째 화면 선택 AI 일정 추천받기/7.선호하는 문화 카드.jpg"),
            ],
        ),
        Screen(
            id="proposal",
            title="추천 일정",
            route="",
            api=["POST /chat", "POST /threads/{thread_id}/routes"],
            note="«새로운 추천받기»는 같은 thread 로 다시 부르면 앞 결과가 "
                 "current_plan 으로 딸려 들어가 수정으로 분류된다. 새 thread_id 로 "
                 "부른다.",
            steps=[
                Step(id="cards", title="{지역}, {기간} 추천일정입니다.", kind="result",
                     choices=[
                         Choice(id="save", label="내 일정으로 담기"),
                         Choice(id="again", label="새로운 추천받기"),
                         Choice(id="restart", label="다시하기"),
                     ],
                     source="두번째 화면 선택 AI 일정 추천받기/8.카드 완료 후 추천 일정 화면.jpg"),
            ],
        ),
        Screen(
            id="startdate",
            title="일정 선택",
            route="/(tabs)/calendar",
            api=["GET /plans/{user_id}"],
            steps=[
                Step(id="date", title="여행 시작일을 선택해주세요.", kind="calendar",
                     source="두번째 화면 선택 AI 일정 추천받기/"
                            "9.완료 추천 일정 캘린터 일정 선택 화면.jpg"),
            ],
        ),
        Screen(
            id="detail",
            title="최종 일정",
            route="/(tabs)/calendar",
            api=["GET /plans/detail/{plan_id}"],
            steps=[
                Step(id="days", title="day 1", kind="result",
                     source="두번째 화면 선택 AI 일정 추천받기/10.최종 일정 완료 화면.jpg"),
            ],
        ),
    ],
)

FLOW_MANUAL_PLAN = Flow(
    id="manual_plan",
    title="직접 일정 만들기",
    entry="캘린더 탭의 + 버튼 → «직접 일정 만들기»",
    screens=[
        Screen(
            id="region",
            title="여행, 어디로 떠나시나요?",
            route="",
            api=["GET /geocode"],
            note="«해외도시 / 국내도시» 탭 중 국내만 산다(OVERSEAS 참고). "
                 "모바일 RegionPicker 가 이미 칩 토글 + 직접 입력을 한다.",
            steps=[
                Step(id="city", title="국내도시", kind="search", choices=REGION_CARDS,
                     source="두번째 화면 선택 직접 일정 만들기/2.국내문화생활 지역 선택.jpg"),
            ],
        ),
        Screen(
            id="dates",
            title="여행일정 등록",
            route="",
            api=["GET /plans/{user_id}"],
            note="참조 화면은 «일정에 따른 날씨예보를 알려드립니다»라고 적는다. "
                 "이 앱의 날씨(tools/weather.py)는 단기예보라 오늘부터 3일까지만 "
                 "실제 값이다. 그 밖의 날짜는 예보 없이 조건만 받는다.",
            steps=[
                Step(id="range", title="여행일정 등록", kind="calendar",
                     subtitle="일정에 따른 날씨예보, 여행 정보를 알려드립니다.",
                     source="두번째 화면 선택 직접 일정 만들기/3.문화일정 등록.jpg"),
            ],
        ),
        Screen(
            id="style",
            title="어떤 스타일의 여행을 할 계획인가요?",
            route="",
            steps=[
                Step(id="companions", title="누구와", kind="single",
                     choices=COMPANION_CARDS,
                     source="두번째 화면 선택 직접 일정 만들기/4.동행자 선택.jpg"),
                Step(id="style", title="여행 스타일", kind="multi", choices=STYLE_CARDS,
                     optional=True,
                     source="두번째 화면 선택 직접 일정 만들기/4.동행자 선택.jpg"),
            ],
        ),
        Screen(
            id="add_places",
            title="Day 별 장소 추가",
            route="",
            api=["GET /curations/{user_id}", "POST /collections", "GET /geocode"],
            note="바텀시트의 «Day N 추천 / 최근 저장 / 내 숙소 / 나만의 장소» 중 "
                 "이 앱에 있는 것은 앞의 둘이다. 숙소는 데이터 소스가 없다.",
            steps=[
                Step(id="pick", title="관광지/맛집/숙소 검색", kind="search",
                     subtitle="고른 장소는 must_include 로 보낸다.",
                     source="두번째 화면 선택 직접 일정 만들기/5.Day 별 장소 추가.jpg"),
                Step(id="multi", title="day N 일정에 M개의 장소 담기", kind="list",
                     source="두번째 화면 선택 직접 일정 만들기/6.Day 다중 일정 3개 선택.jpg"),
            ],
        ),
        Screen(
            id="detail",
            title="Day 1 · Day 2 일정",
            route="/(tabs)/calendar",
            api=["GET /plans/detail/{plan_id}", "POST /threads/{thread_id}/routes"],
            note="구간 거리(9.4km·11.0km)는 이미 ItineraryItem.travel_km_from_prev "
                 "에 있다. Day 2 는 별도 일정 건이다(DURATION_CARDS 참고).",
            steps=[
                Step(id="days", title="day 1 / day 2", kind="result",
                     source="두번째 화면 선택 직접 일정 만들기/1 Day 2 Day 일정 최종 화면.jpg"),
            ],
        ),
    ],
)

FLOWS: list[Flow] = [FLOW_ONBOARDING, FLOW_AI_PLAN, FLOW_MANUAL_PLAN]


# ════════════════════════════════════════════════════════ 8. 내보내기 · 검증


def spec() -> dict[str, Any]:
    """앱에 내려보낼 화면 명세 전량."""
    return {
        "version": 1,
        "theme": {
            "colors": COLORS,
            "radius": RADIUS,
            "spaceUnit": SPACE_UNIT,
            "type": TYPE_SCALE,
            "note": REFERENCE_PALETTE_NOTE,
        },
        "labels": {
            "companions": COMPANIONS_LABEL,
            "transport": TRANSPORT_LABEL,
            "indoor": INDOOR_LABEL,
            "kind": KIND_LABEL,
            "friction": FRICTION_LABEL,
            "verdict": VERDICT_LABEL,
        },
        "progress": {
            "stages": [{"node": n, "label": label} for n, label in PROGRESS_STAGES],
            "note": PROGRESS_NOTE,
        },
        "endpoints": [
            {"method": m, "path": p, "purpose": why} for m, p, why in ENDPOINTS
        ],
        "flows": [f.model_dump() for f in FLOWS],
    }


def _literal_values(field: str) -> set[Any] | None:
    """`TripConditions.<field>` 가 Literal 이면 그 값 집합, 아니면 None."""
    info = TripConditions.model_fields.get(field)
    if info is None:
        return None
    ann = info.annotation
    return set(get_args(ann)) if get_origin(ann) is Literal else None


def all_choices() -> list[tuple[str, Choice]]:
    """(경로, 선택지) 전량. 경로는 `flow/screen/step/choice` 다."""
    out: list[tuple[str, Choice]] = []
    for flow in FLOWS:
        for screen in flow.screens:
            for step in screen.steps:
                for choice in step.choices:
                    out.append((f"{flow.id}/{screen.id}/{step.id}/{choice.id}", choice))
    return out


def validate() -> list[str]:
    """명세와 서버 스키마가 갈린 지점을 전부 모아 돌려준다.

    빈 리스트면 정합한 것이다. 예외를 던지지 않고 목록으로 돌려주는 이유는
    한 번에 다 보기 위해서다 — 첫 번째에서 멈추면 고칠 때마다 다시 돌려야 한다.
    """
    problems: list[str] = []
    fields = set(TripConditions.model_fields)

    for path, choice in all_choices():
        if not choice.supported:
            continue
        for key, value in choice.writes.items():
            if key not in fields:
                problems.append(f"{path}: TripConditions 에 «{key}» 필드가 없다")
                continue
            allowed = _literal_values(key)
            if allowed is not None and value not in allowed:
                problems.append(
                    f"{path}: {key}={value!r} 가 Literal 밖이다 (허용: {sorted(allowed)})")
            if key == "kind_quota":
                for group in value:
                    if group not in KIND_GROUPS:
                        problems.append(
                            f"{path}: kind_quota 키 «{group}» 가 KIND_GROUPS 에 없다 "
                            f"(허용: {sorted(KIND_GROUPS)})")
            if key == "interests":
                for cat in value:
                    if cat not in CATALOG_CATEGORIES:
                        problems.append(
                            f"{path}: interests «{cat}» 가 카탈로그 카테고리에 없다 — "
                            "고른 취향이 어떤 후보와도 매칭되지 않는다")

    # 라벨 표는 서버 Literal 과 **같은 집합**이어야 한다. 한쪽만 늘면 화면에
    # 원시 값("kids")이 그대로 노출되거나, 없는 값을 고를 수 있게 된다.
    for field, table in (("companions", COMPANIONS_LABEL),
                         ("transport", TRANSPORT_LABEL),
                         ("indoor_pref", INDOOR_LABEL)):
        allowed = _literal_values(field)
        if allowed is None:
            problems.append(f"labels: TripConditions.{field} 가 더 이상 Literal 이 아니다")
            continue
        if set(table) != allowed:
            problems.append(
                f"labels[{field}]: 라벨 {sorted(table)} ≠ Literal {sorted(allowed)}")

    # 선택지 id 는 화면 안에서 유일해야 한다. 겹치면 뒤엣것이 앞엣것을 덮어쓴다.
    for flow in FLOWS:
        for screen in flow.screens:
            for step in screen.steps:
                ids = [c.id for c in step.choices]
                dupes = {i for i in ids if ids.count(i) > 1}
                if dupes:
                    problems.append(
                        f"{flow.id}/{screen.id}/{step.id}: 중복 id {sorted(dupes)}")

    return problems


if __name__ == "__main__":  # pragma: no cover
    import json

    found = validate()
    if found:
        print(f"❌ 불일치 {len(found)}건")
        for line in found:
            print("  -", line)
    else:
        print("✅ 화면 명세와 서버 스키마가 정합하다")
    unsupported = [(p, c) for p, c in all_choices() if not c.supported]
    print(f"\n미지원 선택지 {len(unsupported)}건 (지우지 않고 남겨 둔 것):")
    for path, choice in unsupported:
        print(f"  - {path}: {choice.label} — {choice.note}")
    print(f"\n명세 크기: {len(json.dumps(spec(), ensure_ascii=False))} bytes")
