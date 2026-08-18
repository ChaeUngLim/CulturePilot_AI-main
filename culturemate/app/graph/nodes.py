"""메인 그래프의 최상위 노드들.

여기 있는 노드는 '조율'만 한다. 실제 검색·추론·검증 로직은 전부 서브그래프에 있다.
"""
from __future__ import annotations

import logging

from langgraph.types import interrupt

from app.graph.state import CultureMateState
from app.llm.provider import get_chat_model
from app.memory.writer import extract_edit_signals, signal_weight
from app.schemas import (
    Advisory,
    Decision,
    EditSignal,
    Evidence,
    Option,
    resolved_view,
    utc_now,
)

logger = logging.getLogger(__name__)

MAX_REPLAN_ROUNDS = 2
REPLAN_ACTIONS = {"replace", "reorder", "drop", "change_transport",
                  "shift_time", "add_parking", "add_place"}


# ------------------------------------------------------------------ 기존 일정
async def load_current_plan(state: CultureMateState) -> dict:
    """수정·재계획 요청에서 현재 확정 일정을 불러온다."""
    from app.db.repo import load_active_itinerary

    try:
        itinerary = await load_active_itinerary(state.get("user_id", ""),
                                                state["conditions"].date)
    except Exception as exc:
        logger.warning("load_current_plan degraded: %s", exc)
        itinerary = None
    return {"current_itinerary": itinerary,
            "trace": [f"current_plan:{'hit' if itinerary else 'miss'}"]}


# ------------------------------------------------------------------- 상태 통합
def _fill_defaults(state: CultureMateState):
    """추천 요청처럼 조건이 느슨할 때 최소한의 기본값을 채운다.

    날짜가 없으면 일정 편성이 아예 불가능하고, 시간대가 없으면 하루 전체를 잡아
    이동시간이 과대평가된다. 사용자가 말하지 않은 건 '지금부터 오늘 안'으로 본다.
    """
    from datetime import timedelta

    from app.tools.weather import now_kst

    c = state.get("conditions")
    if c is None:
        return None
    now = now_kst()
    filled = c.model_copy(deep=True)
    if filled.date is None:
        filled.date = now.date()
    if filled.date == now.date() and filled.start_time is None:
        # 이미 지난 시간대에 일정을 배치하지 않는다.
        # 다만 이건 우리가 정한 값이라, 화면에는 사용자가 정한 것처럼 보이면 안 된다.
        filled.start_time = (now + timedelta(minutes=30)).time().replace(
            second=0, microsecond=0)
        filled.start_time_assumed = True
    return filled


async def merge_context(state: CultureMateState) -> dict:
    """병렬 브랜치 합류점. 문서 Flowchart 02의 '공유 상태 통합'에 해당한다.

    리듀서가 이미 병합을 끝냈으므로 여기서는 교차 검증과 요약만 한다.
    """
    conditions = _fill_defaults(state)
    hits = state.get("archive_hits") or []
    cands = state.get("candidates") or []
    diffs = state.get("place_diffs") or []
    summary = (f"archive={len(hits)} candidates={len(cands)} "
               f"verified={sum(1 for c in cands if c.verify_status == 'verified')} "
               f"diffs={len(diffs)}")
    out: dict = {"context": {"merge_summary": summary}, "trace": [f"merge:{summary}"]}
    if conditions is not None:
        out["conditions"] = conditions
    return out


# ----------------------------------------------------------------------- HITL
async def human_review(state: CultureMateState) -> dict:
    """사용자 확인 지점. 그래프를 멈추고 카드·근거·선택지를 밖으로 던진다.

    `interrupt()`는 체크포인터에 상태를 저장하고 실행을 중단한다.
    클라이언트는 `Command(resume={"decisions":[...]})` 로 정확히 이 지점부터 재개한다.

    async로 선언한 이유: 동기 노드는 스레드 풀에서 실행되어 runnable config
    컨텍스트를 잃고 `interrupt()`가 동작하지 않는다.
    """
    advisories: list[Advisory] = state.get("advisories") or []
    decided = {d.advisory_id for d in (state.get("decisions") or [])}
    pending = [a for a in advisories if a.id not in decided]
    if not pending:
        return {"needs_user_confirm": False, "trace": ["hitl:skip"]}

    payload = {
        "type": "confirm_plan_changes",
        "itinerary": _dump(state.get("itinerary")),
        "advisories": [a.model_dump(mode="json") for a in pending],
        "evidence": [e.model_dump(mode="json") for e in (state.get("evidence") or [])],
        # 사람이 읽을 문장이다. 이 값은 채팅에 그대로 뜰 수 있으므로
        # 'advisory'·'option_id' 같은 내부 용어를 쓰면 안 된다.
        "instruction": _confirm_prompt(state.get("itinerary"), pending),
        # 클라이언트가 지켜야 할 계약은 따로 둔다 — 화면에 보이지 않는다
        "contract": "decisions[] = [{advisory_id, option_id, note?}]",
        # 확인 카드가 뜨는 순간에도 화면 상단 조건 칩은 갱신돼야 한다.
        # 예전에는 이 값이 done 응답에만 실려서, "판교역에서 7시 출발"이라고
        # 말해도 카드가 뜨면 칩이 이전 값 그대로 남았다. 페이로드에 담아 두면
        # SSE·sync·resume 세 경로가 한 번에 해결된다.
        "resolved": resolved_view(state.get("conditions")),
    }
    response = interrupt(payload)

    decisions = [
        Decision(advisory_id=d["advisory_id"], option_id=d["option_id"],
                 note=d.get("note"))
        for d in (response or {}).get("decisions", [])
    ]
    return {
        "decisions": decisions,
        "needs_user_confirm": False,
        "replan_round": (state.get("replan_round") or 0) + 1,
        "trace": [f"hitl:{len(decisions)}"],
    }


def after_review(state: CultureMateState) -> str:
    """사용자 선택이 일정 재계산을 요구하는지 판단한다."""
    if (state.get("replan_round") or 0) > MAX_REPLAN_ROUNDS:
        return "finalize"
    options = _option_index(state)
    for d in state.get("decisions") or []:
        opt = options.get(d.option_id)
        if opt and opt.action in REPLAN_ACTIONS:
            return "itinerary"
    return "finalize"


# ------------------------------------------------------------------- 마무리
async def finalize(state: CultureMateState) -> dict:
    """자동 수정 반영 + 사용자 선택을 일정에 적용하고 근거를 확정한다."""
    itinerary = state.get("itinerary")
    options = _option_index(state)
    applied: list[str] = []
    for d in state.get("decisions") or []:
        opt = options.get(d.option_id)
        if not opt:
            continue
        applied.append(f"{opt.action}:{opt.label}")
        if opt.action == "drop" and itinerary:
            pid = opt.payload.get("place_id")
            itinerary.items = [i for i in itinerary.items if i.place_id != pid]
            for i, item in enumerate(itinerary.items, 1):
                item.seq = i
    ev = [Evidence(kind="rule", title="사용자 확정", text=", ".join(applied),
                   observed_at=utc_now(), confidence=1.0)] if applied else []
    return {"itinerary": itinerary, "evidence": ev,
            "trace": [f"finalize:{len(applied)}"]}


async def persist(state: CultureMateState) -> dict:
    """일정·수정행동·사용자 선택을 아카이브에 저장한다(개인화 순환의 닫는 고리)."""
    from app.db.repo import save_decisions, save_itinerary, save_plan_edits

    user_id = state.get("user_id", "")
    itinerary = state.get("itinerary")
    signals = _merge_signals(
        _decision_signals(state),
        _stamped(itinerary, extract_edit_signals(state.get("current_itinerary"),
                                                 itinerary, at=utc_now())))
    try:
        if itinerary:
            await save_itinerary(user_id, itinerary)
        if state.get("decisions"):
            await save_decisions(user_id, state["decisions"])
        # 수정 행동은 일정을 저장한 **뒤에** 넣는다. plan_edits.plan_id 가 plans 를
        # 참조하므로, 순서를 바꾸면 아직 없는 일정을 가리켜 참조가 NULL 로 떨어진다.
        if signals and user_id:
            await save_plan_edits(user_id, itinerary.id if itinerary else None, signals)
    except Exception as exc:
        logger.warning("persist degraded: %s", exc)

    # 수정 행동을 취향 프로필에 바로 반영한다.
    # 이걸 빼먹으면 frequent_removals 가 영원히 비어 있어, 사용자가 몇 번을
    # 지운 장소든 다음 추천에 그대로 다시 올라온다. 아카이브가 '다음 판단의
    # 근거'가 되려면 방문 기록만이 아니라 거절 기록도 남아야 한다.
    if signals and user_id:
        await _learn_from_edits(user_id, signals)

    return {"edit_signals": signals, "trace": [f"persist:{len(signals)}"]}


# 확정 카드의 선택 → 수정 행동. 카드에 없는 행동(keep·add_parking·add_place·
# shift_time)은 EditSignal 의 어휘에 대응하는 것이 없어 남기지 않는다.
_DECISION_SIGNAL = {
    "drop": "remove",
    "replace": "replace",
    "reorder": "reorder",
    "change_transport": "transport_change",
}


def _decision_signals(state: CultureMateState) -> list[EditSignal]:
    """확정 카드에서 사용자가 고른 것을 수정 행동으로 읽는다 (UR-09).

    ★ diff 만으로는 부족하다 — 처음 만든 일정에서 카드로 장소를 빼면 비교 대상
    (`current_itinerary`)이 없어 `extract_edit_signals` 가 빈 목록을 낸다. 사용자가
    가장 분명하게 «싫다»고 말한 순간이 바로 그때인데 그게 통째로 사라지고 있었다.

    id 를 결정에서 만드는(`dec-…`) 이유 — 같은 스레드에서 다음 요청을 보내면 예전
    결정이 상태에 그대로 남아 다시 지나간다. 저장 쿼리가 이 id 로 중복을 거르므로,
    같은 선택은 몇 번을 지나가도 한 번만 기록된다.
    """
    options = _option_index(state)
    signals: list[EditSignal] = []
    for d in state.get("decisions") or []:
        opt = options.get(d.option_id)
        action = _DECISION_SIGNAL.get(opt.action) if opt else None
        if not action:
            continue
        signals.append(EditSignal(
            id=f"dec-{d.advisory_id}-{d.option_id}",
            action=action,
            from_place_id=opt.payload.get("place_id"),
            to_place_id=opt.payload.get("to_place_id"),
            signal=f"확정 카드: {opt.label}",
            weight=signal_weight(action),
            observed_at=d.decided_at,
        ))
    return signals


def _stamped(itinerary, signals: list[EditSignal]) -> list[EditSignal]:
    """diff 로 뽑은 신호에 되풀이해도 같은 id 를 준다.

    저장 쿼리가 id 로 중복을 거르는데 기본 id 는 매번 새로 생긴다. 그래서 같은 일정이
    두 번 저장되면 — 그래프 재시도, 또는 예전 `current_itinerary` 를 든 채로 다음 턴을
    도는 경우 — 같은 삭제가 한 번씩 더 쌓여 회피 가중치가 부풀려진다.
    한 일정에서 같은 장소를 두 번 지울 수는 없으니 (일정·행동·대상)이면 사건 하나를
    가리키기에 충분하다. 체류시간을 두 번에 걸쳐 늘린 경우가 한 번으로 접히는데,
    부풀리는 쪽보다 이쪽이 안전하다.
    """
    plan = itinerary.id if itinerary else "noplan"
    for s in signals:
        s.id = f"{plan}:{s.action}:{s.from_place_id or '-'}:{s.to_place_id or '-'}"
    return signals


def _merge_signals(from_cards: list[EditSignal],
                   from_diff: list[EditSignal]) -> list[EditSignal]:
    """카드 신호와 diff 신호를 합치되 같은 행동을 두 번 세지 않는다.

    수정 요청에서 카드로 장소를 빼면 두 경로가 같은 사건을 본다 — 카드도 '뺐다'고 하고,
    저장된 일정과의 diff 도 '없어졌다'고 한다. 둘 다 남기면 회피 가중치가 두 배가 된다.
    """
    seen = {(s.action, s.from_place_id, s.to_place_id) for s in from_cards}
    return list(from_cards) + [
        s for s in from_diff if (s.action, s.from_place_id, s.to_place_id) not in seen]


async def _learn_from_edits(user_id: str, signals: list) -> None:
    try:
        from app.memory.profile import (
            apply_edit_signals,
            load_profile,
            rebuild_profile,
            save_profile,
        )

        profile = await load_profile(user_id)
        if profile is None:
            # 첫 일정이면 프로필 행이 아직 없다. 예전에는 여기서 그냥 돌아가서,
            # 아카이브가 빈 사용자의 **첫 수정 행동만 통째로 버려졌다**.
            # 이제는 재집계로 만들어 저장한다 — plan_edits 는 위에서 이미 저장했으므로
            # 재집계가 그 신호를 담고 있다. 여기서 또 더하면 두 번 세는 것이다.
            await save_profile(user_id, await rebuild_profile(user_id))
            return
        await save_profile(user_id, apply_edit_signals(profile, signals))
    except Exception as exc:
        logger.warning("취향 반영 실패(무시): %s", exc)


async def compose(state: CultureMateState) -> dict:
    """최종 응답 생성. 근거를 반드시 함께 서술한다(UR-14)."""
    itinerary = state.get("itinerary")
    cands = state.get("candidates") or []
    advisories = state.get("advisories") or []

    profile = state.get("taste_profile")
    context = {
        "request_type": (state.get("request_type").value
                         if state.get("request_type") else "unknown"),
        "taste": _taste_summary(profile),
        "itinerary": _dump(itinerary),
        "top_candidates": [c.model_dump(mode="json") for c in cands[:5]],
        "advisories": [a.model_dump(mode="json") for a in advisories],
        "archive": [h.summary for h in (state.get("archive_hits") or [])[:5]],
    }
    try:
        llm = get_chat_model("writer", temperature=0.3)
        res = await llm.ainvoke([
            {"role": "system", "content":
                "CultureMate의 응답을 한국어로 작성한다. 일정이 있으면 시간순으로 간결히 제시하고, "
                "각 장소마다 선택 이유를 한 줄로 붙인다. 과거 기록을 근거로 쓴 경우 반드시 명시한다. "
                "확정되지 않은 정보는 '확인 필요'로 표시한다."},
            {"role": "user", "content": str(context)},
        ])
        answer = res.content if isinstance(res.content, str) else str(res.content)
    except Exception as exc:
        logger.warning("compose fallback: %s", exc)
        answer = _fallback_answer(state)
    return {"answer": answer, "messages": [{"role": "assistant", "content": answer}],
            "trace": ["compose"]}


# ---------------------------------------------------------------------- 헬퍼
def _taste_summary(profile) -> dict | None:
    """응답 문장이 '무엇을 근거로 골랐는지' 말할 수 있게 요약을 넘긴다."""
    if profile is None:
        return None
    # 음수는 «선호»가 아니다 — 취향 카드의 «관심 없어요»가 음수로 남는다 (UR-01).
    top = sorted(((k, v) for k, v in (profile.preferred_categories or {}).items() if v > 0),
                 key=lambda kv: kv[1], reverse=True)[:3]
    frictions = sorted((profile.friction_sensitivity or {}).items(),
                       key=lambda kv: kv[1], reverse=True)[:2]
    return {
        "선호 카테고리": [k for k, _ in top],
        "실내 선호도": round(profile.indoor_bias, 2),
        "평균 체류": profile.avg_dwell_min,
        "주의할 불편": [k for k, _ in frictions],
    }


def _option_index(state: CultureMateState) -> dict[str, Option]:
    return {o.id: o for a in (state.get("advisories") or []) for o in a.options}


def _dump(obj) -> dict | None:
    return obj.model_dump(mode="json") if obj is not None else None


def _fallback_answer(state: CultureMateState) -> str:
    it = state.get("itinerary")
    if not it or not it.items:
        cands = state.get("candidates") or []
        if not cands:
            return "조건에 맞는 결과를 찾지 못했습니다. 날짜나 지역을 조금 넓혀볼까요?"
        return "추천 장소: " + ", ".join(c.name for c in cands[:5])
    lines = [f"{i.arrive:%H:%M} {i.name} ({i.dwell_min}분) — {i.reason or ''}".rstrip(" —")
             for i in it.items if i.arrive]
    return "\n".join(lines)


def _confirm_prompt(itinerary, pending) -> str:
    """확인 카드에 붙일 한 문장.

    이 값은 대화에 그대로 뜬다. 내부 용어가 새어 나가면 사용자는 자기가
    무엇을 해야 하는지가 아니라 시스템이 무엇을 원하는지를 읽게 된다.
    """
    stops = len(getattr(itinerary, "items", []) or [])
    n = len(pending)
    head = f"{stops}곳으로 일정을 짰어요. " if stops else ""
    if n == 1:
        return f"{head}확인할 것이 하나 있습니다 — 아래에서 골라 주세요."
    return f"{head}확인할 것이 {n}개 있습니다 — 아래에서 하나씩 골라 주세요."
