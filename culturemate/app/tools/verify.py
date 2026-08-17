"""공식정보 교차검증 + 재방문 diff.

문서 목표 2("탐색 → 검증 → 상태 3분류")와 차별점 2("지난번과 달라진 점")를 담당한다.

검증의 뼈대는 규칙이고, LLM은 '웹 문서에서 사실을 뽑는 일'만 맡는다.
LLM에게 "이 장소가 문 여나요?"를 물으면 그럴듯한 답을 지어내지만,
"이 문서에 적힌 운영시간을 그대로 뽑아라"는 검증 가능한 작업이다.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.llm.provider import structured
from app.schemas import Candidate, Evidence, PlaceDiff, Verification, utc_now

logger = logging.getLogger(__name__)

CHECK_FIELDS = ("period", "opening_hours", "closed_days", "address",
                "fee", "reservation", "availability", "official_url")

DIFF_FIELDS = ("opening_hours", "closed_days", "fee", "reservation",
               "location", "program", "parking", "temporary_closed")


class ExtractedFacts(BaseModel):
    """웹 문서에서 뽑아낸 사실. 문서에 없으면 반드시 null로 둔다."""

    opening_hours: str | None = Field(None, description="예: '10:00-18:00'. 없으면 null")
    closed_days: list[str] = Field(default_factory=list, description="예: ['월']")
    fee: str | None = None
    reservation: str | None = None
    parking: str | None = None
    temporarily_closed: bool | None = Field(None, description="임시휴관·공사 언급이 있으면 true")
    ended: bool | None = Field(None, description="행사가 이미 종료되었으면 true")
    confidence: float = Field(0.5, ge=0.0, le=1.0)


async def verify_candidate(
    c: Candidate, *, deadline: float | None = None,
) -> tuple[Verification, list[Evidence]]:
    """공식 출처와 후보 정보를 대조해 verified / needs_check / excluded 로 분류.

    예산이 빠듯하면 LLM 사실추출을 건너뛰고 검색 결과만으로 판단한다.
    '검증이 덜 된 장소'는 needs_check 로 표시되어 사용자가 알 수 있지만,
    '아무 장소도 못 찾음'은 복구할 방법이 없다.
    """
    from app.graph.budget import Budget
    from app.tools.websearch import available, search

    checks: dict[str, str] = {}
    evidence: list[Evidence] = []

    for field in CHECK_FIELDS:
        checks[field] = "ok" if _has(c, field) else "missing"

    if not available():
        # 검색이 없으면 자체 정보만으로 판단한다. 임의로 verified를 주지 않는다.
        status = "verified" if checks.get("official_url") == "ok" and \
            sum(1 for v in checks.values() if v == "missing") <= 2 else "needs_check"
        return Verification(candidate_id=c.id, status=status, checks=checks,
                            official_url=c.official_url,
                            notes="웹검색 미설정 — 자체 정보로만 판정"), evidence

    domains = [_domain(c.official_url)] if c.official_url else None
    results = await search(f"{c.name} 운영시간 휴관일 입장료 주차", k=3, domains=domains)
    if not results and domains:
        results = await search(f"{c.name} 운영시간 휴관일", k=3)   # 공식 도메인 실패 시 전체 검색

    if not results:
        return Verification(candidate_id=c.id, status="needs_check", checks=checks,
                            official_url=c.official_url,
                            notes="공식 정보를 찾지 못함"), evidence

    budget = Budget(deadline=deadline) if deadline else None
    if budget is not None and not budget.allows(1.5):
        # 시간이 없으면 추출을 건너뛴다 — 검색 결과가 있다는 사실만으로 판정
        facts = ExtractedFacts(confidence=0.35)
    else:
        facts = await _extract(c, results)
    _reconcile(c, facts, checks)

    top = results[0]
    evidence.append(Evidence(
        kind="official" if domains else "web",
        title=top.get("title") or c.name,
        text=(top.get("content") or "")[:600],
        url=top.get("url"),
        observed_at=utc_now(),
        confidence=min(1.0, (0.8 if domains else 0.5) * (0.5 + facts.confidence)),
    ))

    status = _decide(checks, facts)
    return Verification(
        candidate_id=c.id, status=status, checks=checks,
        official_url=c.official_url or top.get("url"),
        notes=_note(facts),
    ), evidence


async def _extract(c: Candidate, results: list[dict[str, Any]]) -> ExtractedFacts:
    docs = "\n\n---\n\n".join(
        f"[{r.get('title','')}] {r.get('url','')}\n{(r.get('content') or '')[:1200]}"
        for r in results[:3]
    )
    try:
        chain = structured("fast", ExtractedFacts)
        return await chain.ainvoke([
            {"role": "system",
             "content": "아래 문서에서 장소의 사실 정보만 그대로 추출하라. "
                        "문서에 명시되지 않은 항목은 반드시 null 또는 빈 배열로 두고 추측하지 마라."},
            {"role": "user", "content": f"장소: {c.name}\n\n{docs}"},
        ])
    except Exception as exc:
        logger.warning("사실 추출 실패(%s): %s", c.name, exc)
        return ExtractedFacts(confidence=0.2)


def _reconcile(c: Candidate, facts: ExtractedFacts, checks: dict[str, str]) -> None:
    """추출한 사실로 후보를 보강하고, 불일치는 checks에 기록한다."""
    if facts.opening_hours:
        if not c.opening_hours:
            c.opening_hours = {"default_text": facts.opening_hours}
            checks["opening_hours"] = "ok"
        elif facts.opening_hours not in str(c.opening_hours):
            checks["opening_hours"] = "mismatch"
    if facts.closed_days:
        if not c.closed_days:
            c.closed_days = facts.closed_days
            checks["closed_days"] = "ok"
        elif set(facts.closed_days) != set(c.closed_days):
            checks["closed_days"] = "mismatch"
    if facts.fee and not c.fee:
        c.fee, checks["fee"] = facts.fee, "ok"
    if facts.reservation and not c.reservation:
        c.reservation, checks["reservation"] = facts.reservation, "ok"
    if facts.parking:
        c.raw["parking"] = facts.parking
    if facts.temporarily_closed:
        c.raw["temporary_closed"] = "임시휴관"
        checks["availability"] = "mismatch"
    if facts.ended:
        checks["period"] = "mismatch"


def _decide(checks: dict[str, str], facts: ExtractedFacts) -> str:
    """제외는 명확한 근거가 있을 때만. 정보 부족은 '확인 필요'로 남긴다.

    소규모 공방·독립서점은 공식 정보가 원래 부실하다. 이들을 전부 떨구면
    '상시 문화공간 추천'이라는 차별점이 사라지므로, 배제 기준을 좁게 잡는다.
    """
    if checks.get("availability") == "mismatch" or checks.get("period") == "mismatch":
        return "excluded"
    if any(v == "mismatch" for v in checks.values()):
        return "needs_check"
    missing = sum(1 for v in checks.values() if v == "missing")
    if missing == 0 and facts.confidence >= 0.5:
        return "verified"
    return "needs_check" if missing >= 3 else "verified"


def _note(facts: ExtractedFacts) -> str | None:
    bits = []
    if facts.temporarily_closed:
        bits.append("임시휴관 언급")
    if facts.ended:
        bits.append("행사 종료 가능성")
    if facts.parking:
        bits.append(f"주차: {facts.parking}")
    return " · ".join(bits) or None


def diff_against_snapshot(c: Candidate, snapshot: dict[str, Any] | None,
                          last_visited_at: datetime | None) -> list[PlaceDiff]:
    """마지막 방문 시점의 스냅샷과 현재 공식정보를 비교한다(차별점 2)."""
    if not snapshot or not c.place_id:
        return []
    current = {
        "opening_hours": _s(c.opening_hours),
        "closed_days": _s(c.closed_days),
        "fee": c.fee,
        "reservation": c.reservation,
        "location": c.address,
        "program": _s(c.raw.get("program")),
        "parking": _s(c.raw.get("parking")),
        "temporary_closed": _s(c.raw.get("temporary_closed")),
    }
    out: list[PlaceDiff] = []
    for field in DIFF_FIELDS:
        before, after = _s(snapshot.get(field)), current.get(field)
        if before and after and _normalize(before) != _normalize(after):
            out.append(PlaceDiff(place_id=c.place_id, field=field, before=before,
                                 after=after, last_visited_at=last_visited_at,
                                 source_url=c.official_url))
    return out


def snapshot_of(c: Candidate) -> dict[str, Any]:
    """다음 재방문 때 비교 기준이 될 스냅샷. 검증 직후 저장한다."""
    return {
        "opening_hours": _s(c.opening_hours), "closed_days": _s(c.closed_days),
        "fee": c.fee, "reservation": c.reservation, "location": c.address,
        "program": _s(c.raw.get("program")), "parking": _s(c.raw.get("parking")),
        "temporary_closed": _s(c.raw.get("temporary_closed")),
        "verified_at": utc_now().isoformat(),
    }


def _has(c: Candidate, field: str) -> bool:
    if field == "period":
        return bool(c.period_end or c.kind != "event")
    if field == "availability":
        return c.verify_status != "excluded"
    return bool(getattr(c, field, None))


def _normalize(text: str) -> str:
    return "".join(text.split()).lower()


def _s(v: Any) -> str | None:
    if v in (None, "", [], {}):
        return None
    return v if isinstance(v, str) else str(v)


def _domain(url: str | None) -> str:
    if not url:
        return ""
    from urllib.parse import urlparse

    return urlparse(url).netloc
