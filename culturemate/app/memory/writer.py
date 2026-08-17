"""아카이브 쓰기 경로.

일정/방문/수정행동 → 검색 가능한 '경험 문장' → 태깅 → 임베딩 → pgvector.
문서의 핵심 주장("아카이브는 기록장이 아니라 다음 판단의 근거")을 구현하는 축.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.db.session import acquire
from app.llm.prompts import EXPERIENCE_SUMMARY_SYSTEM
from app.llm.provider import get_embeddings, structured
from app.schemas import EditSignal, FrictionTag, Itinerary, utc_now

logger = logging.getLogger(__name__)

_INSERT_SQL = """
INSERT INTO experience_embeddings
    (user_id, source_type, source_id, place_id, summary, tags, friction,
     sentiment, rating, occurred_at, meta, embedding, ts)
VALUES
    (%(user_id)s, %(source_type)s, %(source_id)s, %(place_id)s, %(summary)s,
     %(tags)s, %(friction)s, %(sentiment)s, %(rating)s, %(occurred_at)s,
     %(meta)s, %(embedding)s::vector, to_tsvector('simple', %(summary)s))
ON CONFLICT (source_type, source_id) DO UPDATE
SET summary = EXCLUDED.summary,
    tags = EXCLUDED.tags,
    friction = EXCLUDED.friction,
    sentiment = EXCLUDED.sentiment,
    embedding = EXCLUDED.embedding,
    ts = EXCLUDED.ts,
    meta = EXCLUDED.meta,
    updated_at = now()
"""


class ExperienceSummary(BaseModel):
    """LLM이 만들어내는 검색용 경험 문장."""

    summary: str = Field(description="1~2문장, 검색 대상 문서와 같은 어투")
    tags: list[str] = Field(default_factory=list)
    friction: list[FrictionTag] = Field(default_factory=list)
    sentiment: float = 0.0


async def summarize_experience(payload: dict[str, Any]) -> ExperienceSummary:
    chain = structured("fast", ExperienceSummary)
    msg = [
        {"role": "system", "content": EXPERIENCE_SUMMARY_SYSTEM},
        {"role": "user", "content": _render(payload)},
    ]
    try:
        return await chain.ainvoke(msg)
    except Exception as exc:
        logger.warning("summarize_experience fallback: %s", exc)
        return ExperienceSummary(summary=_render(payload)[:400])


async def write_experience(
    *,
    user_id: str,
    source_type: Literal["visit", "review", "plan_edit", "note"],
    source_id: str,
    place_id: str | None,
    payload: dict[str, Any],
    occurred_at: datetime | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """경험 1건을 요약·임베딩해 아카이브에 upsert."""
    summ = await summarize_experience(payload)
    vec = await get_embeddings().aembed_query(summ.summary)
    params = {
        "user_id": user_id,
        "source_type": source_type,
        "source_id": source_id,
        "place_id": place_id,
        "summary": summ.summary,
        "tags": summ.tags,
        "friction": summ.friction,
        "sentiment": summ.sentiment,
        "rating": payload.get("rating"),
        "occurred_at": occurred_at or utc_now(),
        "meta": _json(meta or {}),
        "embedding": vec,
    }
    try:
        async with acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(_INSERT_SQL, params)
            await conn.commit()
    except Exception as exc:
        logger.error("write_experience failed: %s", exc)


# ------------------------------------------------------------- 수정행동 → 신호
_ACTION_SIGNAL = {
    "remove": ("이 유형의 장소를 일정에서 제외하는 경향", 1.0),
    "replace": ("가까운/다른 유형으로 교체하는 경향", 0.9),
    "reorder": ("방문 순서 선호", 0.5),
    "dwell_up": ("이 유형에 오래 머무는 선호", 0.7),
    "dwell_down": ("이 유형을 짧게 보는 선호", 0.7),
    "transport_change": ("이동수단 선호", 0.6),
}


def signal_weight(action: str) -> float:
    """행동별 가중치. 확정 카드 경로(nodes)와 diff 경로가 같은 값을 쓰게 한다."""
    return _ACTION_SIGNAL[action][1] if action in _ACTION_SIGNAL else 1.0


def extract_edit_signals(
    before: Itinerary | None, after: Itinerary | None, *, at: datetime | None = None
) -> list[EditSignal]:
    """두 일정 버전의 diff에서 암묵적 선호 신호를 뽑는다.

    별점 같은 명시적 피드백보다 '수정 행동'이 취향을 더 정확히 드러낸다는 게
    본 서비스의 전제(문서 차별점 4).
    """
    if not before or not after:
        return []
    b = {i.place_id or i.name: i for i in before.items}
    a = {i.place_id or i.name: i for i in after.items}
    signals: list[EditSignal] = []

    removed = [k for k in b if k not in a]
    added = [k for k in a if k not in b]

    for k in removed:
        action = "replace" if added else "remove"
        text, w = _ACTION_SIGNAL[action]
        signals.append(EditSignal(
            action=action, from_place_id=b[k].place_id,
            to_place_id=a[added[0]].place_id if added and action == "replace" else None,
            signal=f"{b[k].name}: {text}", weight=w, observed_at=at,
        ))

    for k in set(b) & set(a):
        if b[k].dwell_min != a[k].dwell_min:
            action = "dwell_up" if a[k].dwell_min > b[k].dwell_min else "dwell_down"
            text, w = _ACTION_SIGNAL[action]
            signals.append(EditSignal(action=action, from_place_id=b[k].place_id,
                                      signal=f"{b[k].name}: {text}", weight=w, observed_at=at))
        if b[k].seq != a[k].seq:
            text, w = _ACTION_SIGNAL["reorder"]
            signals.append(EditSignal(action="reorder", from_place_id=b[k].place_id,
                                      signal=f"{b[k].name}: {text}", weight=w, observed_at=at))
    return signals


def _render(payload: dict[str, Any]) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in payload.items() if v not in (None, "", []))


def _json(obj: Any) -> Any:
    import json

    from psycopg.types.json import Jsonb

    try:
        return Jsonb(obj)
    except Exception:
        return json.dumps(obj, ensure_ascii=False)
