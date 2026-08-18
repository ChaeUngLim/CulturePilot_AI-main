from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    user_id: str
    thread_id: str
    message: str
    # 모바일 클라이언트가 네이티브에서만 얻을 수 있는 값을 덮어쓴다.
    # 대표적으로 GPS 현재 위치 → gap_fill(일정 조기 종료) 라우트의 필수 입력.
    conditions_override: dict[str, Any] | None = None


class DecisionIn(BaseModel):
    advisory_id: str
    option_id: str
    note: str | None = None


class ResumeRequest(BaseModel):
    user_id: str
    thread_id: str
    decisions: list[DecisionIn] = Field(default_factory=list)


class PreferenceCardIn(BaseModel):
    """카드 한 장의 평가 (UR-01 · UR-31)."""

    subject: str
    verdict: Literal["recommend", "dislike", "interested", "not_interested"]
    experienced: bool = False


class PreferenceCardsIn(BaseModel):
    """카드 화면은 한 장씩이 아니라 **한 묶음**을 보낸다.

    스와이프는 초당 한 장씩 넘어간다. 장마다 왕복하면 지하철에서 절반이 유실되고,
    사용자는 어디까지 저장됐는지 알 수 없다. 화면을 빠져나갈 때 한 번에 올린다.
    """

    user_id: str = ""
    cards: list[PreferenceCardIn] = Field(default_factory=list)


class VisitIn(BaseModel):
    user_id: str
    place_id: str
    plan_id: str | None = None
    visited_at: str | None = None
    rating: float | None = None
    review: str | None = None
    friction: list[str] = Field(default_factory=list)
    companions: str | None = None
    transport: str | None = None
    photos: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
