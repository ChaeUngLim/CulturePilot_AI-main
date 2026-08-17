"""체크포인트 직렬화 설정.

LangGraph는 State에 담긴 커스텀 타입을 msgpack으로 저장하는데, 기본값이 '전부 허용 +
경고'다. 향후 버전에서 차단 예정이므로 우리 도메인 타입만 명시적으로 허용한다.
체크포인트 DB에 쓰기 권한을 얻은 공격자가 역직렬화로 코드 실행을 노리는 경로를 막는 설정이기도 하다.
"""
from __future__ import annotations

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app import schemas

ALLOWED_TYPES = (
    schemas.RequestType,
    schemas.PlanFlags,
    schemas.GeoPoint,
    schemas.StopRequest,      # TripConditions.stops 에 실린다 — 빠뜨리면 일정 생성이 통째로 죽는다
    schemas.TripConditions,
    schemas.Candidate,
    schemas.Verification,
    schemas.PlaceDiff,
    schemas.ArchiveHit,
    schemas.EditSignal,
    schemas.PreferenceCard,
    schemas.TasteProfile,
    schemas.ItineraryItem,
    schemas.Gap,
    schemas.Itinerary,
    schemas.Issue,
    schemas.Option,
    schemas.Advisory,
    schemas.Decision,
    schemas.Evidence,
)


def build_serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=ALLOWED_TYPES)
