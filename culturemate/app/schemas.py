"""도메인 모델. 그래프 State가 실어 나르는 값의 타입 정의."""
from __future__ import annotations

import uuid
from datetime import date as Date
from datetime import datetime, time, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def _naive_time(v: time | None) -> time | None:
    """벽시계 시각에서 tz 를 떼어낸다.

    일정의 시각은 '몇 시에 거기 있는가'라는 지역 시각이지 UTC 어느 순간이 아니다.
    tz 가 붙으면 `datetime.combine()` 이 aware 를 만들어, 같은 함수 안에서 만든
    naive 시각과 비교하는 순간 'can't compare offset-naive and offset-aware' 로 터진다.

    실제로 라우터 LLM(8B)이 시각을 `"08:00:00+09:00"` 으로 뱉어 일정 편성이 통째로
    죽었다. 모델 출력은 우리가 통제할 수 없으므로 경계에서 정규화한다.
    """
    return v.replace(tzinfo=None) if v is not None and v.tzinfo is not None else v


def utc_now() -> datetime:
    """'언제 일어났는가'를 기록하는 시각. 항상 tz를 붙인 UTC다.

    `datetime.utcnow()` 는 UTC 값을 tz 없이(naive) 돌려주는 데다 폐기 예정이다.
    DB의 시각 컬럼은 전부 timestamptz 라 읽어오면 aware 로 돌아오는데,
    쓸 때만 naive 를 넣으면 같은 필드에 두 종류가 섞여 비교가 터진다.

    화면에 그릴 벽시계 시각(일정의 arrive/depart)과는 다른 개념이다.
    그쪽은 지역 시각이라 tz 없이 다루고, 오늘 날짜가 필요하면 `weather.today_kst()`
    를 쓴다 — 컨테이너는 UTC로 돌기 때문에 UTC 날짜를 쓰면 새벽 9시간이 어긋난다.
    """
    return datetime.now(timezone.utc)


def _nid() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------- 요청 분류
class RequestType(str, Enum):
    ARCHIVE_QUERY = "archive_query"        # 과거 방문 질문
    PLACE_RECOMMEND = "place_recommend"    # 새로운 장소 추천
    PLAN_CREATE = "plan_create"            # 하루 일정 생성
    PLAN_MODIFY = "plan_modify"            # 기존 일정 수정
    REVISIT_PLAN = "revisit_plan"          # 재방문 일정
    WEATHER_ADJUST = "weather_adjust"      # 날씨에 따른 변경
    GAP_FILL = "gap_fill"                  # 일정 조기 종료 / 공백 채우기


class PlanFlags(BaseModel):
    """라우터가 결정하는 실행 계획. 문서의 '주요 요청별 실행 경로'를 코드화한 것."""

    use_archive: bool = True
    use_discovery: bool = False
    use_current_plan: bool = False
    build_itinerary: bool = False
    nearby_fill: bool = False
    freshness_diff: bool = False   # 재방문 장소의 '달라진 점' 비교


# ---------------------------------------------------------------- 사용자 조건
class GeoPoint(BaseModel):
    lat: float
    lng: float
    name: str | None = None
    # 행정구역(UR-18). 지오코딩이 좌표와 **같이** 주는 값이라 같이 들고 다닌다.
    # 나중에 주소 문자열을 다시 파싱하는 쪽으로 미루면 «경기도 광주시»가
    # 광주광역시가 되는 자리가 생긴다 — 판정 규칙은 `app.tools.region`.
    sido: str | None = None          # 짧은 이름: "서울" · "경기"
    sigungu: str | None = None       # "서초구" · "성남시"


class StopRequest(BaseModel):
    """시각이 지정된 요청 한 건.

    "9시에 강남역 메가박스, 13시에 예술의전당, 5시에 식사" 처럼
    사용자가 시각과 목적을 함께 말하는 경우가 흔하다. 이걸 자유 텍스트로 두면
    스케줄러가 시각을 무시하고 자기 방식대로 배치해 버린다.
    """

    at: time | None = None                  # 지정 시각
    place_hint: str | None = None           # "강남역 메가박스" 같은 장소 단서
    purpose: Literal["culture", "meal", "cafe", "rest", "any"] = "any"
    note: str | None = None                 # "예매 가능한 것", "공연 스케줄 있는 것"
    fixed: bool = True                      # 시각을 반드시 지킬 것인가

    _strip_tz = field_validator("at")(_naive_time)


class TripConditions(BaseModel):
    date: Date | None = None
    start_time: time | None = None
    end_time: time | None = None
    origin: GeoPoint | None = None
    region: str | None = None
    # 여러 구를 함께 볼 수 있다. 인접한 구는 한 코스로 묶는 편이 자연스럽고,
    # 지역마다 일정을 따로 만들면 사용자가 직접 합쳐야 한다.
    regions: list[str] = Field(default_factory=list)
    # 지점 기준 요청("신촌역 근처"). 구(區)가 '어느 동네냐'라면 이건 '어디서 출발하냐'다.
    # 지점이 있으면 구보다 우선하고 반경을 좁혀 실제 도보권 결과를 낸다.
    landmark: str | None = None
    radius_m: int | None = None
    # 도착지(선택). 일정 마지막을 여기 가깝게 끝낸다 — 귀가 동선을 고려하는 사람이 많다.
    # 출발지·도착지는 선택이다. 이름만 있으면 주소 API로 좌표를 채운다.
    origin_name: str | None = None
    destination: GeoPoint | None = None
    destination_name: str | None = None
    # 이름은 말했는데 좌표를 못 찾았다. 이름 자체를 고치면(예: "종로역(찾지 못함)")
    # 재조회 때마다 꼬리표가 덧붙어 "(찾지 못함)(찾지 못함)…"이 된다.
    origin_missing: bool = False
    destination_missing: bool = False
    # 말하지 않아서 시스템이 채운 시작 시각인지. 화면의 '시각' 칩은 사용자가
    # 정한 값만 보여야 한다 — 안 정했는데 12:27이 떠 있으면 자기가 정한 줄 안다.
    start_time_assumed: bool = False
    # "5개 정도"처럼 **전체** 개수를 말한 경우. 비우면 설정값(MAX_STOPS)을 쓴다.
    stop_count: int | None = None
    # "문화 2개 + 디저트 3개"처럼 **종류별로** 개수를 말한 경우.
    # 키는 KIND_GROUPS 의 그룹 이름, 값은 그 그룹에서 넣을 장소 수.
    #
    # stop_count 와 따로 두는 이유 — 예전에는 앞 숫자 하나만 잡아 stop_count=2 가
    # 되었고, "문화 2 + 디저트 3"이 문화 2곳으로 끝난 뒤 빈틈 채우기가 카페를
    # 여섯 곳 밀어 넣었다. 총량과 배분은 다른 정보다.
    kind_quota: dict[str, int] = Field(default_factory=dict)
    # "장소마다 1~2시간"처럼 체류시간을 말한 경우(분). 장소별 예상값을 이 범위로 맞춘다.
    dwell_min: int | None = None
    dwell_max: int | None = None
    # 시각이 지정된 요청들. 비어 있으면 스케줄러가 알아서 배치한다.
    stops: list[StopRequest] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    companions: Literal["solo", "couple", "friends", "family", "kids", "unknown"] = "unknown"
    party_size: int = 1
    # best = 구간마다 가장 빠른 수단을 조합한다(최단루트).
    # 지하철만 / 버스만을 따로 두는 이유: 소요시간과 환승 부담이 전혀 다르다.
    # '지하철+버스'는 뺐다 — 수단이 아니라 조합이고, 섞는 건 best 가 한다.
    transport: Literal["best", "walk", "subway", "bus",
                       "car", "bike", "unknown"] = "unknown"
    budget_krw: int | None = None
    indoor_pref: Literal["indoor", "outdoor", "any"] = "any"
    must_include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    free_text: str | None = None

    _strip_tz = field_validator("start_time", "end_time")(_naive_time)


# ---------------------------------------------------------------- 탐색·검증
PlaceKind = Literal["event", "venue", "food", "cafe", "shop", "park", "other"]

# 사람이 개수를 말하는 단위는 PlaceKind 와 다르다. "문화 2개"는 event 와 venue 를
# 함께 가리키고, "디저트 3개"는 cafe 하나만 가리킨다. 그래서 말하는 단위(그룹)와
# 저장하는 단위(kind)를 분리해 여기서 잇는다.
#
# 확장 지점 — 새 종류를 사람이 말하기 시작하면 여기 한 줄과 router 의 키워드
# 표 한 줄만 더하면 된다. 스케줄러·빈틈채우기는 그룹 이름만 보므로 손대지 않는다.
KIND_GROUPS: dict[str, tuple[str, ...]] = {
    "culture": ("event", "venue"),
    "food": ("food",),
    "cafe": ("cafe",),
    "shop": ("shop",),
    "outdoor": ("park",),
}


def group_of(kind: str) -> str | None:
    """PlaceKind → 그룹 이름. 어느 그룹에도 없으면 None(개수 제한을 받지 않는다)."""
    for group, kinds in KIND_GROUPS.items():
        if kind in kinds:
            return group
    return None
VerifyStatus = Literal["verified", "needs_check", "excluded", "unknown"]


class Candidate(BaseModel):
    id: str = Field(default_factory=_nid)
    place_id: str | None = None            # 정규화된 canonical place
    source: str = "unknown"                # culture_api | web | maps | archive
    kind: PlaceKind = "venue"
    name: str
    category: str | None = None
    address: str | None = None
    geo: GeoPoint | None = None
    official_url: str | None = None
    period_start: Date | None = None
    period_end: Date | None = None
    opening_hours: dict[str, Any] | None = None
    closed_days: list[str] = Field(default_factory=list)
    fee: str | None = None
    reservation: str | None = None
    indoor: bool | None = None
    expected_dwell_min: int = 60
    # 주차 정보. 차량 이동 시 가장 자주 문제가 되는 항목이라 별도 필드로 둔다.
    parking: Literal["free", "paid", "none", "nearby", "unknown"] = "unknown"
    parking_note: str | None = None
    tags: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
    # 점수
    relevance: float = 0.0
    personal_score: float = 0.0
    final_score: float = 0.0
    verify_status: VerifyStatus = "unknown"


class Verification(BaseModel):
    id: str = Field(default_factory=_nid)
    candidate_id: str
    status: VerifyStatus = "unknown"
    checks: dict[str, Literal["ok", "mismatch", "missing"]] = Field(default_factory=dict)
    official_url: str | None = None
    checked_at: datetime = Field(default_factory=utc_now)
    notes: str | None = None


class PlaceDiff(BaseModel):
    """재방문 장소의 '지난번과 달라진 점'."""

    id: str = Field(default_factory=_nid)
    place_id: str
    field: Literal[
        "opening_hours", "closed_days", "fee", "reservation",
        "location", "program", "parking", "temporary_closed"
    ]
    before: str | None = None
    after: str | None = None
    last_visited_at: datetime | None = None
    source_url: str | None = None


# ---------------------------------------------------------------- 아카이브
FrictionTag = Literal[
    "parking", "crowding", "accessibility", "waiting",
    "noise", "cost", "reservation", "transit", "weather"
]
ArchiveSourceType = Literal["visit", "review", "plan_edit", "note", "profile"]


class ArchiveHit(BaseModel):
    """개인 아카이브에서 회수된 경험 조각."""

    id: str = Field(default_factory=_nid)
    source_type: ArchiveSourceType
    source_id: str
    place_id: str | None = None
    place_name: str | None = None
    summary: str
    tags: list[str] = Field(default_factory=list)
    friction: list[FrictionTag] = Field(default_factory=list)
    sentiment: float = 0.0            # -1.0 ~ 1.0
    rating: float | None = None
    occurred_at: datetime | None = None
    facet: str = "unknown"            # 어떤 검색 facet에서 나왔는지
    dense_rank: int | None = None
    lexical_rank: int | None = None
    fused_score: float = 0.0
    rerank_score: float | None = None
    final_score: float = 0.0
    meta: dict[str, Any] = Field(default_factory=dict)


class EditSignal(BaseModel):
    """일정 수정 행동에서 추출한 암묵적 선호 신호."""

    id: str = Field(default_factory=_nid)
    action: Literal["remove", "replace", "reorder", "dwell_up", "dwell_down", "transport_change"]
    from_place_id: str | None = None
    to_place_id: str | None = None
    signal: str                        # 예: "혼잡한 대형 전시 회피"
    weight: float = 1.0
    observed_at: datetime | None = None


class PreferenceCard(BaseModel):
    """카드로 등록한 초기 취향 (UR-01) · 3지 반응 (UR-31).

    `EditSignal` 이 «행동으로 드러난» 취향이라면 이쪽은 «말로 밝힌» 취향이다.
    아카이브가 0건인 첫 사용자에게는 이것만이 유일한 근거라, 방문 기록이 쌓이기 전
    구간의 개인화를 여기서 받는다.

    `subject` 는 카테고리 이름이거나 `places.id` 다. 둘을 나눈 컬럼을 두지 않은 이유 —
    카드 화면은 «전시 좋아하세요?»(카테고리)와 «이 전시 어때요?»(장소)를 같은 더미에
    섞어 넘긴다. 나누면 두 목록을 따로 셔플해 합쳐야 하고, UNIQUE (user_id, subject)
    하나로 끝나던 재평가가 컬럼별 분기로 갈라진다.

    `verdict` 4값과 `experienced` 의 조합이 기획안 2.4-③의 3지 반응이다.
      기대돼요   → interested,     experienced=False
      가봤어요   → recommend/dislike, experienced=True   (좋았는지까지 받는다)
      관심 없어요 → not_interested,  experienced=False
    """

    subject: str
    verdict: Literal["recommend", "dislike", "interested", "not_interested"]
    # «가봤다»는 강도를 가른다. 겪고 나서 좋다고 한 것이 말로만 기대된다고 한 것보다
    # 무겁다 — 가중치를 `_CARD_WEIGHTS` 에서 그렇게 준다.
    experienced: bool = False
    created_at: datetime | None = None


class TasteProfile(BaseModel):
    user_id: str
    preferred_categories: dict[str, float] = Field(default_factory=dict)
    indoor_bias: float = 0.0           # -1(야외) ~ 1(실내)
    avg_travel_min: float | None = None
    avg_dwell_min: float | None = None
    companion_prefs: dict[str, dict[str, float]] = Field(default_factory=dict)
    novelty_bias: float = 0.0          # -1(재방문) ~ 1(신규 탐색)
    frequent_removals: dict[str, float] = Field(default_factory=dict)
    friction_sensitivity: dict[str, float] = Field(default_factory=dict)
    updated_at: datetime | None = None


# ---------------------------------------------------------------- 일정
class ItineraryItem(BaseModel):
    seq: int
    # 사용자가 시각을 지정한 항목인지 — UI가 '고정'으로 표시하고 스케줄러가 안 옮긴다
    fixed_time: bool = False
    parking: Literal["free", "paid", "none", "nearby", "unknown"] = "unknown"
    parking_note: str | None = None
    purpose: Literal["culture", "meal", "cafe", "rest", "any"] = "any"
    candidate_id: str | None = None
    place_id: str | None = None
    name: str
    kind: PlaceKind = "venue"
    geo: GeoPoint | None = None
    arrive: datetime | None = None
    depart: datetime | None = None
    dwell_min: int = 60
    travel_min_from_prev: int = 0
    # 직전 장소와의 거리(km). 지도에 구간 라벨을 그리려면 시간만으론 부족하다
    travel_km_from_prev: float | None = None
    # 이 구간을 계산한 엔진.
    #   naver(자동차) | ors(도보) | odsay(대중교통) | estimate(거리 기반 추정)
    travel_source: Literal["naver", "ors", "odsay", "estimate"] = "estimate"
    travel_transfers: int | None = None       # 환승 횟수
    travel_fare: int | None = None            # 대중교통 요금 · 자동차 통행료(원)
    # 직전 장소에서 여기까지의 **실제 경로 선형**. [[lng, lat], …]
    # GeoPoint 리스트가 아니라 숫자쌍인 이유는 크기다 — 구간당 수백 점이라
    # 모델로 감싸면 페이로드가 몇 배가 된다. 지도는 좌표만 있으면 된다.
    travel_path: list[list[float]] = Field(default_factory=list)
    transport: str | None = None
    indoor: bool | None = None
    # 공식정보 대조 결과. 첫 응답에서는 대개 'unknown' 이고(예산이 모자라 검증을
    # 건너뛴다) `POST /threads/{id}/verify` 가 뒤이어 채운다. 화면은 이 값으로
    # '확인 필요' 칩을 띄운다 — reason 문자열만으로는 UI가 판단할 수 없다.
    verify_status: VerifyStatus = "unknown"
    reason: str | None = None          # 설명가능성: 왜 이 자리에 배치됐는가
    evidence_ids: list[str] = Field(default_factory=list)


class Gap(BaseModel):
    id: str = Field(default_factory=_nid)
    after_seq: int
    start: datetime | None = None
    end: datetime | None = None
    minutes: int = 0
    anchor: GeoPoint | None = None
    purpose: Literal["meal", "rest", "free"] = "free"


class Itinerary(BaseModel):
    id: str = Field(default_factory=_nid)
    date: Date | None = None
    items: list[ItineraryItem] = Field(default_factory=list)
    total_travel_min: int = 0
    total_dwell_min: int = 0
    map_path: list[GeoPoint] = Field(default_factory=list)
    # 출발·도착 지점. 방문할 장소가 아니라 하루의 양 끝이라 items 와 따로 둔다.
    # 이게 없으면 "판교역에서 출발"이라고 말해도 지도에 판교역이 나타나지 않는다.
    origin: GeoPoint | None = None
    destination: GeoPoint | None = None
    origin_name: str | None = None
    destination_name: str | None = None
    version: int = 1
    notes: list[str] = Field(default_factory=list)
    # 어떤 기준으로 계산했는지. best 는 구간마다 다른 수단이 섞여 있다는 뜻이다.
    transport_mode: str = "unknown"
    # 수단별 소요시간(분) 합. "도보 22분 · 지하철 31분"처럼 구성을 보여주기 위한 것.
    transport_mix: dict[str, int] = Field(default_factory=dict)
    total_fare: int = 0                # 대중교통 요금 · 통행료 합계(원)


# ---------------------------------------------------------------- 검증 / HITL
class Issue(BaseModel):
    id: str = Field(default_factory=_nid)
    kind: Literal[
        "hours_conflict", "unreachable", "overlap", "weather_risk",
        "past_friction", "revisit_change", "budget_over", "closed"
    ]
    severity: int = 1                  # 1=정보, 2=확인필요, 3=치명
    target_seq: int | None = None
    # 이슈를 만든 시점의 장소 이름. seq 는 재편성(_reflow)마다 흔들려서,
    # 카드를 만들 때 seq 로 이름을 다시 찾으면 다른 장소가 딸려온다 —
    # 'A 확인 필요'라는 제목 아래 B 이야기가 적힌 카드가 실제로 나왔다.
    place_name: str | None = None
    place_id: str | None = None
    detail: str = ""
    auto_fixable: bool = False
    evidence_ids: list[str] = Field(default_factory=list)


class Option(BaseModel):
    id: str = Field(default_factory=_nid)
    label: str
    action: Literal[
        "keep", "replace", "add_parking", "change_transport",
        "reorder", "shift_time", "drop", "add_place"
    ]
    payload: dict[str, Any] = Field(default_factory=dict)
    predicted_effect: str | None = None


class Advisory(BaseModel):
    """사용자에게 보여줄 경고 + 근거 + 선택지 (HITL 카드)."""

    id: str = Field(default_factory=_nid)
    kind: Literal["friction", "revisit_diff", "conflict", "weather", "budget"]
    title: str
    message: str
    place_id: str | None = None
    target_seq: int | None = None
    severity: int = 2
    evidence_ids: list[str] = Field(default_factory=list)
    options: list[Option] = Field(default_factory=list)


class Decision(BaseModel):
    advisory_id: str
    option_id: str
    note: str | None = None
    decided_at: datetime = Field(default_factory=utc_now)


class Evidence(BaseModel):
    """AI 판단 근거. UR-14(판단 근거 확인)의 데이터 소스."""

    id: str = Field(default_factory=_nid)
    kind: Literal["archive", "official", "web", "weather", "maps", "profile", "rule"]
    title: str
    text: str
    url: str | None = None
    ref: str | None = None
    observed_at: datetime | None = None
    confidence: float = 0.5


def resolved_view(c: TripConditions | None) -> dict:
    """서버가 최종적으로 해석한 조건 중 화면이 반영해야 할 것만.

    조건 전체를 내리면 클라이언트가 무엇을 신뢰할지 애매해진다. UI 컨트롤과
    1:1로 대응하는 값만 골라서 내린다.

    api 가 아니라 여기 두는 이유: 확인 카드(interrupt) 페이로드에도 같은 값이
    필요한데, 그래프 노드가 api 를 임포트하면 계층이 뒤집힌다. 예전에는 이 값이
    `done` 응답에만 실려서, **확인 카드가 뜨는 순간 화면 상단 조건 칩이 갱신되지
    않았다** — 사용자가 "판교역에서 7시 출발"이라고 말해도 이전 값이 그대로 남았다.
    """
    if c is None:
        return {}

    def hhmm(value):
        return value.strftime("%H:%M") if value else None

    return {
        "transport": getattr(c, "transport", None),
        "regions": list(getattr(c, "regions", []) or []),
        "landmark": getattr(c, "landmark", None),
        # 화면의 출발·도착·시각 칩이 이 값을 그대로 따라간다.
        # 발화로 정한 조건과 화면에 켜진 조건이 다르면 다음 질문이 엉뚱하게 나간다.
        # 좌표를 못 찾은 이름은 칩에 올리지 않는다. 올리면 사용자는 반영된 줄 안다.
        "origin_name": None if getattr(c, "origin_missing", False)
                       else getattr(c, "origin_name", None),
        "destination_name": None if getattr(c, "destination_missing", False)
                            else getattr(c, "destination_name", None),
        # 시스템이 알아서 채운 시각은 내리지 않는다. 사용자가 말한 것만 칩에 오른다.
        "start_time": None if getattr(c, "start_time_assumed", False)
                      else hhmm(getattr(c, "start_time", None)),
        "end_time": hhmm(getattr(c, "end_time", None)),
        "stop_count": getattr(c, "stop_count", None),
        "dwell_min": getattr(c, "dwell_min", None),
        "dwell_max": getattr(c, "dwell_max", None),
        "indoor_pref": getattr(c, "indoor_pref", None),
    }
