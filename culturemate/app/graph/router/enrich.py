"""규칙으로는 얻을 수 없는 것을 채운다 — 클라이언트 값 · 취향 · 좌표.

셋 다 «발화 밖» 에서 온다. 그래서 규칙 파서(`rules.py`)와 분리했다.

우선순위가 이 모듈의 전부다.
  · 클라이언트 주입값(GPS·선택한 이동수단)이 추출값을 이긴다 — 발화로는 알 수 없다.
  · 다만 **이번 발화에서 말한 것**은 클라이언트 값을 이긴다. 화면에 남아 있던
    지난 도착지가 방금 한 말을 덮으면 안 된다.
  · 취향 프로필은 **말하지 않은 것만** 채운다. '야외 축제'라고 했는데 실내 선호
    프로필이 이기면 시스템을 신뢰할 수 없게 된다.
"""
from __future__ import annotations

import logging
from datetime import time as dt_time

from app.schemas import TripConditions

logger = logging.getLogger(__name__)

def _apply_override(conditions: TripConditions,
                    override: dict | None) -> TripConditions:
    """클라이언트가 주입한 값으로 덮어쓴다.

    LLM이 발화에서 추출할 수 없는 값(GPS 현재 위치, 선택된 이동수단 등)은
    네이티브 클라이언트만 알고 있다. 추출값보다 항상 우선한다.
    """
    if not override:
        return conditions
    merged = conditions.model_dump()
    clean = {k: v for k, v in override.items() if v is not None}

    # 이번 발화에서 출발·도착을 말했으면 그게 이긴다.
    # 클라이언트는 화면에 남아 있는 이전 값을 매번 함께 보내는데, 그걸 그대로
    # 덮어쓰면 "수원역에서 출발해 수원역 도착"이라고 말해도 지난 질문의 홍대입구역이
    # 도착지로 남는다. 화면 상태가 방금 한 말을 이기는 건 말이 안 된다.
    if conditions.origin_name:
        clean.pop("origin", None)
        clean.pop("origin_name", None)
    if conditions.destination_name:
        clean.pop("destination", None)
        clean.pop("destination_name", None)
    if conditions.start_time:
        clean.pop("start_time", None)
    if conditions.end_time:
        clean.pop("end_time", None)
    # 클라이언트는 시각을 "09:00" 문자열로 보낸다. 발화에서 뽑은 값과 형식을 맞춘다.
    for key in ("start_time", "end_time"):
        raw = clean.get(key)
        if isinstance(raw, str) and ":" in raw:
            try:
                hh, mm = raw.split(":")[:2]
                clean[key] = dt_time(int(hh), int(mm))
            except ValueError:
                clean.pop(key, None)
    merged.update(clean)
    try:
        return TripConditions(**merged)
    except Exception as exc:
        logger.warning("conditions_override 무시: %s", exc)
        return conditions


async def _load_profile(user_id: str):
    if not user_id:
        return None
    try:
        from app.memory.profile import load_profile

        return await load_profile(user_id)
    except Exception as exc:
        logger.warning("취향 프로필 로드 실패: %s", exc)
        return None


def _apply_taste(conditions: TripConditions, profile) -> TripConditions:
    """사용자가 말하지 않은 취향을 프로필로 채운다.

    말한 조건은 절대 덮어쓰지 않는다. '야외 축제 가고 싶어'라고 했는데
    실내 선호 프로필 때문에 실내로 바뀌면 시스템을 신뢰할 수 없게 된다.
    """
    if profile is None:
        return conditions
    out = conditions.model_copy(deep=True)

    if not out.interests and profile.preferred_categories:
        # 양수만 관심사가 된다. 취향 카드(UR-01)가 «관심 없어요»를 음수 가중치로
        # 남기기 때문에, 거르지 않으면 **싫다고 표시한 카테고리를 검색어로 삼는다.**
        top = sorted(((k, v) for k, v in profile.preferred_categories.items() if v > 0),
                     key=lambda kv: kv[1], reverse=True)[:4]
        out.interests = [name for name, _ in top]

    if out.indoor_pref == "any" and abs(profile.indoor_bias) >= 0.3:
        out.indoor_pref = "indoor" if profile.indoor_bias > 0 else "outdoor"

    if out.companions == "unknown" and profile.companion_prefs:
        out.companions = max(profile.companion_prefs,
                             key=lambda k: sum(profile.companion_prefs[k].values()))  # type: ignore[assignment]
    return out


async def _resolve_places(conditions: TripConditions) -> None:
    """이름으로 말한 곳을 좌표로 바꾼다. 실패해도 요청을 멈추지 않는다."""
    from app.graph.subgraphs.discovery import resolve_origin

    try:
        await resolve_origin(conditions)
    except Exception:
        logger.exception("위치 해석 실패 — 현재 위치로 진행합니다")
