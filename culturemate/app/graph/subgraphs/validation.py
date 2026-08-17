"""일정 검증 서브그래프.

    START → ⟨병렬 6종⟩ check_hours / check_travel / check_overlap
                     / check_weather / check_revisit / check_friction
          → triage(자동수정 vs 사용자확인) → build_confirm_cards → END

핵심 규칙: '자동으로 처리 가능한 것'과 '사용자가 판단해야 하는 것'을 분리한다.
auto_fixable=False **이면서** severity >= threshold 인 이슈만 HITL로 올린다.

두 조건은 AND다(`triage` 참고). OR로 읽으면 자동으로 고칠 수 있는 사소한 이슈까지
전부 사용자에게 올라가, 확인 카드가 쌓여 정작 중요한 경고가 묻힌다.
"""
from __future__ import annotations

import logging
from itertools import pairwise

from langgraph.graph import END, START, StateGraph

from app.config import get_settings
from app.graph.state import ValidationOutput, ValidationState
from app.schemas import (
    Advisory,
    ArchiveHit,
    Candidate,
    Evidence,
    Issue,
    Option,
    utc_now,
)

logger = logging.getLogger(__name__)


def _pairs(it):
    """일정 항목의 연속 쌍. 검증 규칙 대부분이 '앞 항목 → 뒤 항목' 관계를 본다."""
    items = it.items if it else []
    return pairwise(items)


async def check_hours(state: ValidationState) -> dict:
    """운영시간/휴관일 충돌."""
    it = state.get("itinerary")
    issues: list[Issue] = []
    by_id = {c.id: c for c in (state.get("candidates") or [])}
    for item in (it.items if it else []):
        cand = by_id.get(item.candidate_id or "")
        if not cand or not item.arrive:
            continue
        weekday = item.arrive.strftime("%a").lower()
        if weekday in [d.lower()[:3] for d in (cand.closed_days or [])]:
            issues.append(Issue(kind="closed", severity=3, target_seq=item.seq,
                                place_name=item.name, place_id=item.place_id, auto_fixable=False,
                                detail=f"{item.name}은(는) 해당 요일 휴관입니다."))
        if cand.verify_status == "needs_check":
            issues.append(Issue(kind="hours_conflict", severity=2, target_seq=item.seq,
                                place_name=item.name, place_id=item.place_id, auto_fixable=False,
                                detail=f"{item.name}의 운영정보가 공식 출처로 확인되지 않았습니다."))
    return {"issues": issues, "trace": [f"valid.hours:{len(issues)}"]}


async def check_travel(state: ValidationState) -> dict:
    """이동 불가(도착 전에 다음 장소 출발 필요) 검사."""
    it = state.get("itinerary")
    issues: list[Issue] = []
    for prev, nxt in _pairs(it):
        if not prev.depart or not nxt.arrive:
            continue
        available = (nxt.arrive - prev.depart).total_seconds() / 60
        if available < nxt.travel_min_from_prev:
            issues.append(Issue(
                kind="unreachable", severity=3, target_seq=nxt.seq,
                place_name=nxt.name, place_id=nxt.place_id, auto_fixable=True,
                detail=(f"{prev.name} → {nxt.name} 이동에 {nxt.travel_min_from_prev}분이 "
                        f"필요하지만 {int(available)}분만 남습니다."),
            ))
    return {"issues": issues, "trace": [f"valid.travel:{len(issues)}"]}


async def check_overlap(state: ValidationState) -> dict:
    it = state.get("itinerary")
    issues: list[Issue] = []
    for prev, nxt in _pairs(it):
        if prev.depart and nxt.arrive and nxt.arrive < prev.depart:
            issues.append(Issue(kind="overlap", severity=2, target_seq=nxt.seq,
                                place_name=nxt.name, place_id=nxt.place_id, auto_fixable=True,
                                detail=f"{prev.name}과(와) {nxt.name} 시간이 겹칩니다."))
    return {"issues": issues, "trace": [f"valid.overlap:{len(issues)}"]}


async def check_weather(state: ValidationState) -> dict:
    """악천후 시간대의 야외 일정을 찾는다.

    '비 오니 실내로 바꾸세요'만으로는 사용자가 직접 대안을 찾아야 한다.
    같은 시간대에 갈 수 있는 **실제 실내 장소**를 후보에서 골라 카드에 실어 준다.
    """
    ctx = state.get("context") or {}
    risky = set(ctx.get("risky_hours") or [])
    forecast = ctx.get("weather") or {}
    it = state.get("itinerary")
    if not risky or not it:
        return {"trace": ["valid.weather:0"]}

    issues: list[Issue] = []
    for item in it.items:
        if item.indoor is not False or not item.arrive:
            continue
        hour = item.arrive.strftime("%H")
        if hour not in risky:
            continue
        cond = (forecast.get(hour) or {}).get("condition", "악천후")
        pop = (forecast.get(hour) or {}).get("pop")
        detail = (f"{item.name} 방문 시간대({hour}시)에 {_weather_ko(cond)}"
                  f"{f' · 강수확률 {pop}%' if pop else ''} — 야외 활동이 어렵습니다.")
        issues.append(Issue(
            kind="weather_risk", severity=2, target_seq=item.seq,
            place_name=item.name, place_id=item.place_id, auto_fixable=False, detail=detail,
        ))
    return {"issues": issues, "trace": [f"valid.weather:{len(issues)}"]}


_WEATHER_KO = {"rain": "비 예보", "snow": "눈 예보", "shower": "소나기 예보",
               "heat": "폭염", "cold": "한파", "dust": "미세먼지",
               "overcast": "흐림", "cloudy": "구름 많음"}


def _weather_ko(condition: str) -> str:
    return _WEATHER_KO.get(condition, condition)


def indoor_alternatives(state: ValidationState, seq: int | None,
                        limit: int = 3) -> list[Candidate]:
    """같은 일정 자리를 대신할 실내 후보. 이미 일정에 있는 곳은 제외한다."""
    it = state.get("itinerary")
    used = {i.place_id for i in (it.items if it else []) if i.place_id}
    target = next((i for i in (it.items if it else []) if i.seq == seq), None)

    pool = [c for c in (state.get("candidates") or [])
            if c.indoor is True and c.place_id not in used
            and c.verify_status != "excluded"]
    if target and target.geo:
        # 원래 자리에서 가까운 순 — 동선이 크게 틀어지면 대안이 되지 못한다
        pool.sort(key=lambda c: _dist(target.geo, c.geo) if c.geo else 1e9)
    else:
        pool.sort(key=lambda c: -c.final_score)
    return pool[:limit]


def _dist(a, b) -> float:
    from math import asin, cos, radians, sin, sqrt

    if a is None or b is None:
        return 1e9
    dlat, dlng = radians(b.lat - a.lat), radians(b.lng - a.lng)
    h = sin(dlat / 2) ** 2 + cos(radians(a.lat)) * cos(radians(b.lat)) * sin(dlng / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(h))


async def check_revisit(state: ValidationState) -> dict:
    """재방문 장소의 변경사항(문서 차별점 2)."""
    diffs = state.get("place_diffs") or []
    it = state.get("itinerary")
    place_seq = {i.place_id: i.seq for i in (it.items if it else []) if i.place_id}
    name_of = {i.place_id: i.name for i in (it.items if it else []) if i.place_id}
    issues = [
        Issue(kind="revisit_change", severity=2, target_seq=place_seq.get(d.place_id),
              place_name=name_of.get(d.place_id), place_id=d.place_id, auto_fixable=False, evidence_ids=[d.id],
              detail=f"{d.field}이(가) 지난 방문 이후 '{d.before}' → '{d.after}'로 변경되었습니다.")
        for d in diffs if d.place_id in place_seq
    ]
    return {"issues": issues, "trace": [f"valid.revisit:{len(issues)}"]}


# 불편 태그 → 사용자에게 보일 말. 태그 이름을 그대로 쓰면 «parking 이 있었어요»가 된다.
_FRICTION_KO: dict[str, str] = {
    "parking": "주차가 어려웠",
    "crowding": "너무 붐볐",
    "accessibility": "접근이 불편했",
    "waiting": "대기가 길었",
    "noise": "시끄러웠",
    "cost": "비용이 부담됐",
    "reservation": "예약이 까다로웠",
    "transit": "대중교통이 불편했",
    "weather": "날씨 때문에 힘들었",
}

# 이 값보다 별점이 높으면 불편 태그가 있어도 경고하지 않는다.
# 3.5점을 주고도 «조금 붐볐다»를 남기는 경우가 흔한데, 그것까지 카드로 올리면
# 정작 3.0점 이하의 진짜 불편이 카드 더미에 묻힌다.
_FRICTION_RATING_CEIL = 3.5


async def check_friction(state: ValidationState) -> dict:
    """과거 불편했던 곳이 이번 일정에 들어왔는지 (UR-40 · 기획안 2.1-②).

    **이 검사가 이 프로젝트의 한 줄 정의를 떠받친다** — "아카이브는 기록장이 아니라
    다음 판단의 근거다". 기록을 조회만 하고 경고로 승격하지 않으면, 사용자는
    지난번과 똑같은 주차난을 다시 겪는다.

    한동안 비어 있던 자리다. `Issue.kind="past_friction"` 과 `_options_for()` 의
    선택지(주차장 추가 · 이동수단 변경 · 실내 교체)는 계속 있었는데 **그 이슈를
    만드는 노드가 없어서**, 기획안의 대표 화면이 코드상 도달 불가능했다.
    (`archive.build_advisories` 와 `validation.check_archive` 를 함께 걷어낸 흔적)

    카드는 `validation` 한 곳에서만 만든다는 원칙은 지킨다 — 그래서 archive 가
    아니라 여기에 둔다. 두 곳에서 만들면 같은 사안이 카드 두 장으로 올라간다.
    """
    it = state.get("itinerary")
    hits = state.get("archive_hits") or []
    if not it or not hits:
        return {"issues": [], "trace": ["valid.friction:0"]}

    # 이번 일정에 실제로 들어온 장소만 본다. 후보로만 스쳐 간 곳을 경고하면
    # 사용자는 화면에 없는 장소에 대한 카드를 받는다.
    seq_of = {i.place_id: i.seq for i in it.items if i.place_id}
    name_of = {i.place_id: i.name for i in it.items if i.place_id}

    # 장소당 하나로 접는다. 한 곳에 방문 기록이 셋이면 카드도 셋이 된다.
    worst: dict[str, tuple[ArchiveHit, str]] = {}
    for hit in hits:
        if hit.place_id not in seq_of or not hit.friction:
            continue
        if hit.rating is not None and hit.rating > _FRICTION_RATING_CEIL:
            continue
        tag = hit.friction[0]
        prev = worst.get(hit.place_id)
        # 별점이 낮을수록(=더 나빴을수록) 대표로 삼는다. 없으면 중간값으로 친다.
        if prev is None or (hit.rating or 3.0) < (prev[0].rating or 3.0):
            worst[hit.place_id] = (hit, tag)

    issues, evidence = [], []
    for place_id, (hit, tag) in worst.items():
        phrase = _FRICTION_KO.get(tag, "불편했")
        when = f"{hit.occurred_at:%Y년 %m월} " if hit.occurred_at else ""
        detail = f"{when}방문에서 {phrase}어요"
        if hit.rating is not None:
            detail += f" (별점 {hit.rating:g})"
        # 카드에 «어떤 방문에서 나온 판단인지»를 붙인다. 근거 없이 경고만 뜨면
        # 사용자는 그 말을 믿을 근거가 없다(기획안 2.1-② 의 «5월 18일 방문 · 3.0점»).
        evidence.append(Evidence(
            kind="archive", title=f"{name_of.get(place_id, '')} 과거 기록",
            text=hit.summary, ref=hit.source_id,
            observed_at=hit.occurred_at, confidence=0.9))
        issues.append(Issue(
            kind="past_friction", severity=2, auto_fixable=False,
            target_seq=seq_of.get(place_id), place_id=place_id,
            place_name=name_of.get(place_id),
            evidence_ids=[evidence[-1].id], detail=detail))
    return {"issues": issues, "evidence": evidence,
            "trace": [f"valid.friction:{len(issues)}"]}


async def triage(state: ValidationState) -> dict:
    """자동 처리 가능 항목과 사용자 확인 항목을 가른다."""
    s = get_settings()
    issues = state.get("issues") or []
    need = [i for i in issues
            if not i.auto_fixable and i.severity >= s.severity_threshold_for_hitl]
    return {"needs_user_confirm": bool(need) and s.hitl_enabled,
            "trace": [f"valid.triage:{len(need)}/{len(issues)}"]}


async def build_confirm_cards(state: ValidationState) -> dict:
    """이슈 → 사용자 확인 카드. 근거와 선택지를 함께 싣는다(UR-13, UR-14)."""
    s = get_settings()
    advisories: list[Advisory] = []
    evidence: list[Evidence] = []

    for issue in state.get("issues") or []:
        if issue.auto_fixable or issue.severity < s.severity_threshold_for_hitl:
            continue
        # 이슈에 박아 둔 이름을 쓴다. seq 로 다시 찾으면 재편성 뒤에 다른 장소가
        # 딸려와 'A 확인 필요' 제목 아래 B 이야기가 적힌 카드가 된다.
        target = issue.place_name or "일정"
        options = _options_for(issue.kind, issue.place_id)
        if issue.kind == "weather_risk":
            # 추상적인 '실내로 교체' 대신 실제 장소를 제시한다
            alts = indoor_alternatives(state, issue.target_seq)
            options = [options[0]] + [
                Option(label=f"{a.name}(으)로 교체", action="replace",
                       payload={"place_id": issue.place_id, "to_place_id": a.place_id},
                       predicted_effect=f"실내 {a.category or '문화공간'} · "
                                        f"체류 {a.expected_dwell_min}분"
                                        f"{' · 무료주차' if a.parking == 'free' else ''}")
                for a in alts
            ] + [o for o in options[1:] if o.action in ("shift_time", "drop")]

        advisories.append(Advisory(
            # 카드 id 를 '무슨 문제 + 어느 장소'로 고정한다.
            #
            # Issue.id 는 검사할 때마다 새로 생성되므로 그대로 쓰면, 재계획
            # (hitl → itinerary → validation)을 돌 때마다 카드 id 가 바뀌어
            # MERGE_BY_ID 가 같은 카드로 못 알아본다. 실제로 이슈 6건이
            # 카드 21장으로 불어났다. seq 는 재편성마다 흔들리니 키에 넣지 않는다.
            id=f"adv-{issue.kind}-{issue.place_id or issue.place_name or issue.target_seq}",
            kind=_advisory_kind(issue.kind),
            title=f"{target} 확인 필요",
            message=issue.detail,
            place_id=issue.place_id,
            target_seq=issue.target_seq,
            severity=issue.severity,
            evidence_ids=issue.evidence_ids,
            options=options,
        ))
        evidence.append(Evidence(kind="rule", title=f"검증 규칙 {issue.kind}",
                                 text=issue.detail, ref=issue.id,
                                 observed_at=utc_now(), confidence=0.9))
    return {"advisories": advisories, "evidence": evidence,
            "trace": [f"valid.cards:{len(advisories)}"]}


def _advisory_kind(issue_kind: str) -> str:
    return {"weather_risk": "weather", "past_friction": "friction",
            "revisit_change": "revisit_diff", "budget_over": "budget"}.get(issue_kind, "conflict")


def _options_for(kind: str, place_id: str | None) -> list[Option]:
    base = [Option(label="그대로 진행", action="keep",
                   predicted_effect="일정 변동 없음")]
    if kind in ("closed", "revisit_change"):
        base.append(Option(label="다른 날짜/시간으로 이동", action="shift_time",
                           predicted_effect="이후 일정 시각 재계산"))
    if kind in ("past_friction",):
        base.append(Option(label="인근 주차장 추가", action="add_parking",
                           payload={"place_id": place_id},
                           predicted_effect="주차 대기 감소, 도보 이동 추가"))
        base.append(Option(label="이동수단 변경", action="change_transport",
                           predicted_effect="주차 문제 제거, 이동시간 변동"))
    if kind in ("weather_risk", "closed", "past_friction", "hours_conflict"):
        base.append(Option(label="비슷한 실내 장소로 교체", action="replace",
                           payload={"place_id": place_id},
                           predicted_effect="동선 재계산 후 대체 후보 배치"))
    if kind in ("unreachable", "overlap"):
        base.append(Option(label="방문 순서 변경", action="reorder",
                           predicted_effect="이동시간 최소화 순서로 재배치"))
        base.append(Option(label="이 장소 제외", action="drop",
                           payload={"place_id": place_id},
                           predicted_effect="일정 여유 확보"))
    return base


def build_validation_graph():
    g = StateGraph(ValidationState, output_schema=ValidationOutput)
    # 6종 병렬. 서로 의존이 없어 한 슈퍼스텝에서 같이 돈다.
    # check_friction 은 2026-08-17 에 되살렸다 — 그전에는 past_friction 이슈를
    # 만드는 노드가 없어 기획안 2.1-②(선제적 알림)가 도달 불가능했다.
    checks = {
        "check_hours": check_hours, "check_travel": check_travel,
        "check_overlap": check_overlap, "check_weather": check_weather,
        "check_revisit": check_revisit, "check_friction": check_friction,
    }
    for name, fn in checks.items():
        g.add_node(name, fn)
        g.add_edge(START, name)
        g.add_edge(name, "triage")
    g.add_node("triage", triage)
    g.add_node("build_confirm_cards", build_confirm_cards)
    g.add_edge("triage", "build_confirm_cards")
    g.add_edge("build_confirm_cards", END)
    return g.compile(checkpointer=False)
