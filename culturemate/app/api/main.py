"""FastAPI 진입점.

스트리밍은 SSE. HITL 인터럽트가 발생하면 `event: interrupt` 로 카드가 내려가고,
클라이언트는 `/resume` 으로 선택을 되돌려 그래프를 정확히 그 지점부터 재개한다.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel

from app.api.schemas import ChatRequest, PreferenceCardsIn, ResumeRequest, VisitIn
from app.config import get_settings
from app.db.session import close_pool
from app.graph.build import build_graph_with_postgres
from app.schemas import resolved_view
from app.tools.http import close_client

logger = logging.getLogger(__name__)
_state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=get_settings().log_level)
    graph, saver_cm = await build_graph_with_postgres()
    _state["graph"] = graph
    _state["saver_cm"] = saver_cm
    yield
    await _state["saver_cm"].__aexit__(None, None, None)
    await close_pool()
    # 외부 API 커넥션 풀도 함께 닫는다. 안 닫으면 종료 로그에 남은 소켓 경고가 뜨고,
    # 리로드가 잦은 개발 중에는 죽은 클라이언트가 계속 쌓인다.
    await close_client()


app = FastAPI(title="CultureMate", version="0.1.0", lifespan=lifespan)

# Expo 웹(localhost:8081)에서 다른 오리진의 API를 부르면 브라우저가 막는다.
# 네이티브 앱에는 CORS 개념이 없지만, 개발 중 `w` 로 브라우저에서 확인하는 경로가
# 사실상 기본이라 열어 둔다. 운영 배포 시에는 allow_origins 를 실제 도메인으로 좁힌다.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _cfg(req) -> dict:
    return {"configurable": {"thread_id": req.thread_id, "user_id": req.user_id}}


# 사용자에게 보여줄 텍스트를 만드는 노드. 나머지 노드의 LLM 출력(라우터의 구조화 JSON,
# 사실 추출 결과 등)은 내부 산출물이라 화면에 흘리면 안 된다.
STREAMABLE_NODES = {"compose", "narrate"}


async def _stream(payload: Any, config: dict) -> AsyncIterator[bytes]:
    """SSE 스트림.

    예외를 반드시 이벤트로 바꿔 내보낸다. 제너레이터에서 그냥 터지면 HTTP 응답이
    중간에 끊기고, 클라이언트에는 이유를 알 수 없는 '에러'만 남는다. 무엇이
    잘못됐는지 말해 줘야 사용자가 질문을 고치거나 우리가 고칠 수 있다.
    """
    try:
        async for event in _stream_events(payload, config):
            yield event
    except Exception as exc:
        logger.exception("스트림 실패: %s", exc)
        yield _sse("error", {"message": f"일정을 만들지 못했습니다 ({type(exc).__name__})"})


async def _stream_events(payload: Any, config: dict) -> AsyncIterator[bytes]:
    from time import perf_counter

    graph = _state["graph"]
    last = perf_counter()
    async for mode, chunk in graph.astream(payload, config=config,
                                           stream_mode=["updates", "messages"]):
        if mode == "messages":
            msg, meta = chunk
            if (meta or {}).get("langgraph_node") not in STREAMABLE_NODES:
                continue
            if getattr(msg, "content", None):
                yield _sse("token", {"text": msg.content})
            continue
        # 토큰 이벤트는 시계를 건드리지 않는다 — 단계 경계만 잰다.
        now = perf_counter()
        for node, update in (chunk or {}).items():
            if node == "__interrupt__":
                # update 는 Interrupt 객체의 튜플이다. .value 를 꺼내지 않으면
                # 클라이언트가 문자열을 받아 카드가 0장으로 보인다.
                yield _sse("interrupt", _interrupt_values(update))
                return
            logger.info("⏱ %-20s %5.1fs", node, now - last)
            yield _sse("update", {"node": node, "keys": list((update or {}).keys()),
                                  "elapsed_s": round(now - last, 2)})
        last = now

    snapshot = await graph.aget_state(config)
    if snapshot.interrupts:
        yield _sse("interrupt", _interrupt_values(snapshot.interrupts))
    else:
        yield _sse("done", {"answer": snapshot.values.get("answer", ""),
                            "itinerary": _jsonable(snapshot.values.get("itinerary")),
                            # 발화에서 해석한 조건을 돌려준다. "도보로 짜줘"라고 말했으면
                            # 화면의 이동수단 선택도 도보로 바뀌어야 한다.
                            "resolved": _resolved(snapshot.values),
                            "evidence": _jsonable(snapshot.values.get("evidence"))})


def _resolved(values: dict) -> dict:
    """화면 칩이 따라갈 조건. 구현은 schemas.resolved_view 한 곳에 있다."""
    return resolved_view(values.get("conditions"))


def _interrupt_values(interrupts: Any) -> list[Any]:
    """Interrupt 래퍼에서 실제 페이로드만 뽑는다."""
    out = []
    for item in interrupts or ():
        out.append(_jsonable(getattr(item, "value", item)))
    return out


def _payload(req: ChatRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "user_id": req.user_id,
        "raw_query": req.message,
        "messages": [{"role": "user", "content": req.message}],
    }
    if req.conditions_override:
        payload["conditions_override"] = req.conditions_override
    return payload


@app.post("/chat")
async def chat(req: ChatRequest):
    """SSE 스트리밍. RN에서는 react-native-sse 같은 EventSource 폴리필이 필요하다."""
    return StreamingResponse(_stream(_payload(req), _cfg(req)),
                             media_type="text/event-stream")


@app.post("/chat/sync")
async def chat_sync(req: ChatRequest):
    """비스트리밍 폴백.

    React Native의 fetch는 응답 본문 스트리밍을 지원하지 않는다. 폴리필을 쓰지 못하는
    환경을 위해 같은 그래프·같은 thread_id를 쓰되 결과만 한 번에 돌려주는 경로를 둔다.
    """
    return await _run_once(_payload(req), _cfg(req))


@app.post("/resume")
async def resume(req: ResumeRequest):
    return StreamingResponse(_stream(_resume_cmd(req), _cfg(req)),
                             media_type="text/event-stream")


@app.post("/resume/sync")
async def resume_sync(req: ResumeRequest):
    return await _run_once(_resume_cmd(req), _cfg(req))


def _resume_cmd(req: ResumeRequest) -> Command:
    return Command(resume={"decisions": [d.model_dump() for d in req.decisions]})


def _fmt_stages(stages: list[tuple[str, float, float]]) -> list[str]:
    """`["classify 5.9s @5.9s", "archive 3.3s @9.2s", …]` 형태로 정리한다.

    누적(@) 을 같이 내는 이유는 병렬 브랜치 때문이다. 델타만 보면 archive 3.3s /
    discovery 1.7s 가 각각의 소요 시간처럼 읽히지만, 둘은 같이 도므로 실제로는
    'discovery 는 9.2s 가 아니라 10.9s 에 끝났다'가 맞는 해석이다. 누적을 보면
    브랜치가 언제 합류했는지가 바로 드러난다.
    """
    return [f"{name} {sec:.1f}s @{total:.1f}s" for name, sec, total in stages]


async def _drain(graph, payload: Any, config: dict,
                 stages: list[tuple[str, float, float]]) -> None:
    """그래프를 끝까지 돌리면서 단계별 소요 시간을 모은다.

    `ainvoke` 대신 `astream(stream_mode="updates")` 를 쓰는 이유는 계측 지점 때문이다.
    노드를 감싸서 재면 정확하겠지만, 서브그래프는 **컴파일된 그래프를 그대로 부착해야**
    체크포인트와 스트리밍이 유지된다(build.py 참고). 함수로 감싸는 순간 그게 깨진다.
    업데이트가 도착하는 시점을 재면 그래프를 건드리지 않고 같은 답을 얻는다.

    측정되는 건 **업데이트가 도착한 간격**이다. 순차 노드에서는 그 노드의 소요
    시간과 같지만, 병렬 브랜치에서는 '앞 브랜치가 보고한 뒤 이만큼 더 걸렸다'는
    뜻이 된다. 그래서 누적 시간을 함께 기록한다(_fmt_stages 참고).
    """
    from time import perf_counter

    start = last = perf_counter()
    async for chunk in graph.astream(payload, config=config, stream_mode="updates"):
        now = perf_counter()
        for node in (chunk or {}):
            if not node.startswith("__"):
                stages.append((node, now - last, now - start))
                # 예산 초과로 중단되거나 예외가 나도 여기까지의 기록은 남는다.
                logger.info("⏱ %-20s %5.1fs  (누적 %5.1fs)", node, now - last, now - start)
        last = now


async def _run_once(payload: Any, config: dict) -> dict[str, Any]:
    """그래프를 끝까지 돌리고 interrupt 여부에 따라 두 형태 중 하나를 반환한다.

    예산(TOTAL_BUDGET_S)을 넘기면 중단하고 그 시점까지의 상태를 돌려준다.
    각 단계가 이미 예산을 보고 범위를 줄이므로 여기까지 오는 일은 드물지만,
    외부 API가 응답하지 않는 경우를 대비한 마지막 방어선이다.
    """
    import asyncio

    from app.config import get_settings

    graph = _state["graph"]
    limit = get_settings().total_budget_s + 5      # 각 단계의 마무리 여유
    stages: list[tuple[str, float, float]] = []
    try:
        await asyncio.wait_for(_drain(graph, payload, config, stages), timeout=limit)
    except TimeoutError:
        logger.warning("응답 예산 %.0f초 초과 — 그 시점 상태로 응답합니다", limit)
        # 잘려나간 단계를 목록에서 조용히 빼면 "합계가 안 맞는다"로만 보인다.
        # 어디서 끊겼는지가 사실 가장 중요한 정보다.
        done = stages[-1][2] if stages else 0.0
        stages.append(("(예산초과·중단)", limit - done, float(limit)))
    except Exception as exc:
        # 한 노드가 실패했다고 500을 내면 클라이언트는 이유를 모른 채 끊긴다.
        # 그 시점까지의 상태로 답하고, 무엇이 잘못됐는지는 답변에 담는다.
        logger.exception("그래프 실행 실패: %s", exc)
        snapshot = await graph.aget_state(config)
        values = snapshot.values if snapshot else {}
        return {
            "status": "done",
            "answer": values.get("answer")
            or f"일정을 만드는 중 문제가 생겼습니다 ({type(exc).__name__}). "
               "조건을 조금 바꿔서 다시 물어봐 주세요.",
            "itinerary": _jsonable(values.get("itinerary")),
            "resolved": _resolved(values),
            "evidence_ids": [e.id for e in (values.get("evidence") or [])],
            "advisories": _jsonable(values.get("advisories")),
            "timing": _fmt_stages(stages),
        }

    snapshot = await graph.aget_state(config)
    if snapshot.interrupts:
        values = _interrupt_values(snapshot.interrupts)
        return {"status": "interrupted", "interrupt": values[0] if values else None,
                "timing": _fmt_stages(stages)}
    values = snapshot.values
    return {
        "status": "done",
        "answer": values.get("answer", ""),
        "itinerary": _jsonable(values.get("itinerary")),
        "resolved": _resolved(values),
        # 모바일 페이로드 절감: 근거 원문은 id만 내리고 지연 로드한다.
        "evidence_ids": [e.id for e in (values.get("evidence") or [])],
        "advisories": _jsonable(values.get("advisories")),
        "timing": _fmt_stages(stages),
    }


@app.get("/threads/{thread_id}/state")
async def thread_state(thread_id: str):
    snapshot = await _state["graph"].aget_state({"configurable": {"thread_id": thread_id}})
    if not snapshot:
        raise HTTPException(404, "thread not found")
    return {"values": _jsonable(snapshot.values), "next": snapshot.next,
            "interrupts": _jsonable([i.value for i in snapshot.interrupts])}


class ReroutePlace(BaseModel):
    place_id: str | None = None
    name: str
    lat: float
    lng: float
    indoor: bool | None = None
    parking: str = "unknown"
    parking_note: str | None = None
    category: str | None = None


class RerouteRequest(BaseModel):
    """이동수단만 바꿔서 동선을 다시 계산한다.

    전체 그래프를 다시 돌리면 탐색·검증까지 반복되어 15초가 또 든다. 장소는
    그대로 두고 구간만 다시 재는 것이므로, 여기서 끊어 2~3초에 끝낸다.
    """
    transport: str
    thread_id: str | None = None          # 오늘의 일정 — 서버가 갖고 있는 걸 다시 잰다
    places: list[ReroutePlace] | None = None   # 큐레이션 — 좌표 목록을 받아 순서까지 정한다
    # 출발지·도착지는 선택. 이름만 주면 주소 API로 좌표를 채운다.
    origin: dict | None = None
    origin_name: str | None = None
    destination: dict | None = None
    destination_name: str | None = None
    start_time: str | None = None         # "09:00" — 도착 시각까지 계산하려면 필요


@app.post("/reroute")
async def reroute(req: RerouteRequest):
    from datetime import time as dt_time

    from app.graph.subgraphs.itinerary import reroute_itinerary, route_places
    from app.schemas import GeoPoint
    from app.tools.maps import geocode

    async def _point(raw: dict | None, name: str | None) -> GeoPoint | None:
        """좌표가 오면 그대로, 이름만 오면 주소 API로 바꾼다."""
        if raw:
            return GeoPoint(**raw)
        if name:
            return await geocode(name)
        return None

    origin = await _point(req.origin, req.origin_name)
    destination = await _point(req.destination, req.destination_name)
    start = None
    if req.start_time:
        try:
            hh, mm = req.start_time.split(":")[:2]
            start = dt_time(int(hh), int(mm))
        except ValueError:
            start = None

    if req.places:
        itinerary = await route_places(
            [p.model_dump() for p in req.places], req.transport,
            origin=origin, destination=destination, start_time=start,
            origin_name=req.origin_name, destination_name=req.destination_name)
    elif req.thread_id:
        snapshot = await _state["graph"].aget_state(
            {"configurable": {"thread_id": req.thread_id}})
        current = (snapshot.values or {}).get("itinerary") if snapshot else None
        if current is None:
            raise HTTPException(404, "itinerary not found")
        conditions = (snapshot.values or {}).get("conditions")
        itinerary = await reroute_itinerary(
            current, req.transport,
            origin=origin or getattr(conditions, "origin", None),
            destination=destination or getattr(conditions, "destination", None),
            origin_name=req.origin_name or getattr(conditions, "origin_name", None),
            destination_name=(req.destination_name
                              or getattr(conditions, "destination_name", None)),
            end_time=getattr(conditions, "end_time", None))
    else:
        raise HTTPException(400, "thread_id 또는 places 중 하나가 필요합니다")

    return {"itinerary": _jsonable(itinerary), "transport": req.transport}


@app.post("/threads/{thread_id}/routes")
async def measure_routes_endpoint(thread_id: str, transport: str | None = None):
    """확정된 일정의 구간을 실측해 **실제 경로 좌표**를 채워 돌려준다.

    첫 응답(`/chat`)과 분리한 이유: 응답 예산 15초에는 탐색·검증·편성이 다 들어가야
    해서 구간 실측이 잘린다. 그러면 지도가 장소를 직선으로 잇는데, 지하철이 한강을
    가로질러 직진하는 그림이 된다.

    그래서 두 단계로 나눈다.
      1) `/chat` — 15초 안에 일정을 낸다. 이동시간은 거리 기반 추정('(추정)' 표시)
      2) 여기 — 지도를 그린 뒤 이어서 호출해 실제 노선으로 바꾼다

    사용자는 기다리지 않는다. 이미 일정을 보고 있고, 선만 나중에 정확해진다.
    """
    from app.graph.subgraphs.itinerary import measure_routes

    snapshot = await _state["graph"].aget_state(
        {"configurable": {"thread_id": thread_id}})
    itinerary = (snapshot.values or {}).get("itinerary") if snapshot else None
    if itinerary is None or not itinerary.items:
        raise HTTPException(404, "itinerary not found")

    conditions = (snapshot.values or {}).get("conditions")
    measured = await measure_routes(
        itinerary, transport or getattr(conditions, "transport", None),
        origin=getattr(conditions, "origin", None),
        end_time=getattr(conditions, "end_time", None))

    legs = sum(1 for i in measured.items if i.travel_path)
    logger.info("경로 실측 완료: %d/%d 구간에 선형 확보", legs, len(measured.items))
    return {"itinerary": _jsonable(measured), "measured_legs": legs}


@app.post("/threads/{thread_id}/verify")
async def verify_itinerary_endpoint(thread_id: str):
    """일정에 들어간 장소를 공식정보와 대조한다(2단계).

    `/routes` 와 같은 이유로 첫 응답에서 뺐다. 15초 예산에는 탐색·편성이 먼저 들어가
    검증 한 묶음(2.5초 + 응답 예약 2.5초)이 들어갈 자리가 남지 않는다. 그대로 두면
    모든 장소가 '확인 필요'로 남아, 공식정보 검증이라는 기능이 사실상 꺼진다.

    후보 전체가 아니라 **일정의 장소만** 본다 — 화면에 뜨는 건 그것뿐이다.
    """
    from app.graph.subgraphs.discovery import verify_itinerary

    snapshot = await _state["graph"].aget_state(
        {"configurable": {"thread_id": thread_id}})
    values = (snapshot.values or {}) if snapshot else {}
    itinerary = values.get("itinerary")
    if itinerary is None or not itinerary.items:
        raise HTTPException(404, "itinerary not found")

    done, evidence = await verify_itinerary(itinerary, values.get("candidates") or [])
    counts: dict[str, int] = {}
    for i in itinerary.items:
        counts[i.verify_status] = counts.get(i.verify_status, 0) + 1
    return {"itinerary": _jsonable(itinerary), "verified": done, "status": counts,
            "evidence": _jsonable(evidence)}


@app.get("/threads/{thread_id}/evidence/{evidence_id}")
async def get_evidence(thread_id: str, evidence_id: str):
    """UR-14. 목록에서는 id만 내리고, 사용자가 '근거 보기'를 눌렀을 때만 원문을 준다."""
    snapshot = await _state["graph"].aget_state({"configurable": {"thread_id": thread_id}})
    for e in (snapshot.values.get("evidence") or []) if snapshot else []:
        if e.id == evidence_id:
            return e.model_dump(mode="json")
    raise HTTPException(404, "evidence not found")


@app.post("/visits")
async def add_visit(v: VisitIn):
    """실제 관람 기록 저장 → 아카이브 임베딩 → 프로필 갱신(개인화 순환의 입력)."""
    from app.memory.profile import rebuild_profile
    from app.memory.writer import write_experience

    try:
        await write_experience(
            user_id=v.user_id, source_type="visit", source_id=f"{v.plan_id}:{v.place_id}",
            place_id=v.place_id,
            payload={"rating": v.rating, "review": v.review, "friction": v.friction,
                     "companions": v.companions, "transport": v.transport},
            meta={"companions": v.companions, "transport": v.transport, **v.meta},
        )
        profile = await rebuild_profile(v.user_id)
    except Exception as exc:
        # 클라이언트는 실패 시 로컬 큐에 남겨 두고 재전송한다. 500으로 끊지 않는다.
        logger.error("visit 저장 실패: %s", exc)
        return {"ok": False, "reason": "archive_unavailable"}
    return {"ok": True, "profile_updated_at": profile.updated_at}


@app.get("/report/{user_id}")
async def taste_report(user_id: str):
    """취향 리포트.

    /chat/sync + /threads/state 를 두 번 왕복하는 대신 집계값을 직접 돌려준다.
    수치는 SQL 집계라 LLM 없이도 나오고, 서술은 기록이 충분할 때만 붙인다.
    """
    from app.memory.profile import rebuild_profile

    try:
        profile = await rebuild_profile(user_id)
    except Exception as exc:
        logger.error("리포트 집계 실패: %s", exc)
        raise HTTPException(503, "아카이브를 조회할 수 없습니다") from exc

    stats = {
        "preferred_categories": profile.preferred_categories,
        "indoor_bias": profile.indoor_bias,
        "avg_travel_min": profile.avg_travel_min,
        "avg_dwell_min": profile.avg_dwell_min,
        "novelty_bias": profile.novelty_bias,
        "friction_sensitivity": profile.friction_sensitivity,
        "frequent_removals": profile.frequent_removals,
    }
    has_data = bool(profile.preferred_categories or profile.friction_sensitivity)
    narrative = ""
    if has_data:
        try:
            from app.llm.prompts import REPORT_SYSTEM
            from app.llm.provider import get_chat_model

            res = await get_chat_model("writer", temperature=0.3).ainvoke([
                {"role": "system", "content": REPORT_SYSTEM},
                {"role": "user", "content": f"집계값(JSON): {stats}"},
            ])
            narrative = res.content if isinstance(res.content, str) else str(res.content)
        except Exception as exc:
            # 서술 생성 실패가 리포트 전체를 막으면 안 된다 — 수치만으로도 쓸모가 있다
            logger.warning("리포트 서술 생성 실패: %s", exc)

    return {"stats": stats, "narrative": narrative, "has_data": has_data}


# 데모 단계에서는 사용자가 하나다. 인증이 붙으면 토큰에서 꺼내 쓰면 된다.
DEMO_USER = "00000000-0000-0000-0000-000000000001"


@app.post("/preferences/cards")
async def save_preference_cards_endpoint(req: PreferenceCardsIn):
    """카드로 초기 취향을 등록한다 (UR-01 · FR-25 · UR-31).

    ★ 저장 뒤에 **재집계한 프로필을 반드시 저장**한다. `rebuild_profile()` 만 부르면
    반환값은 호출자 손 안에서 사라지고, 추천 경로(`discovery`·`archive`·`router`)는
    전부 `load_profile()` 로 **저장된 행**을 읽기 때문에 카드가 아무 효과도 내지 않는다.
    "저장은 됐는데 추천은 그대로"가 정확히 이 한 줄에서 갈린다.
    """
    from app.db.repo import save_preference_cards
    from app.memory.profile import rebuild_profile, save_profile
    from app.schemas import PreferenceCard

    if not req.cards:
        raise HTTPException(400, "평가한 카드가 없습니다")
    user_id = req.user_id or DEMO_USER
    try:
        saved = await save_preference_cards(user_id, [
            PreferenceCard(subject=c.subject, verdict=c.verdict,
                           experienced=c.experienced) for c in req.cards])
        profile = await rebuild_profile(user_id)
        await save_profile(user_id, profile)
    except Exception as exc:
        # 저장이 안 됐는데 됐다고 하면 사용자는 카드를 다시 넘기지 않는다. 실패는 밝힌다.
        logger.warning("취향 카드 저장 실패: %s", exc)
        raise HTTPException(503, "지금은 취향을 저장할 수 없습니다") from exc
    return {"ok": True, "saved": saved,
            "preferred_categories": profile.preferred_categories,
            "profile_updated_at": profile.updated_at}


@app.get("/preferences/cards/{user_id}")
async def list_preference_cards(user_id: str):
    """이미 평가한 카드 — 화면이 «다시 물어보지 않기» 위해 필요하다 (UR-01)."""
    from app.db.repo import load_preference_cards

    try:
        cards = await load_preference_cards(user_id or DEMO_USER)
    except Exception as exc:
        # 목록을 못 읽는다고 카드 화면 자체가 막히면 안 된다. 처음인 것처럼 보여준다.
        logger.warning("취향 카드 조회 실패: %s", exc)
        return {"cards": [], "count": 0, "reason": "unavailable"}
    return {"cards": [c.model_dump(mode="json") for c in cards], "count": len(cards)}


class SaveCollectionIn(BaseModel):
    """일정의 장소들을 내 컬렉션에 담는다.

    title 만 주면 새로 만들거나 같은 이름에 더한다. 클라이언트가 컬렉션 목록을
    먼저 받아 고르게 하므로, 서버는 '이름'이라는 하나의 개념만 다루면 된다.
    """
    user_id: str = ""
    title: str
    place_ids: list[str] = []
    emoji: str = "⭐"
    subtitle: str | None = None
    note: str | None = None


@app.get("/plans/{user_id}")
async def list_plans_endpoint(user_id: str, from_: str = Query("", alias="from"),
                             to: str = ""):
    """기간별 확정 일정 목록 — 캘린더가 월/주 그리드를 채울 때 쓴다 (UR-28).

    기본 범위는 **지난 3개월 ~ 앞으로 1개월**. 캘린더는 과거를 되짚는 화면이라
    앞보다 뒤가 넓다 — 기록을 남기지 않은 지난 일정이 여기서 드러나야 한다.

    응답은 요약만이다. 그날 일정 전체는 `GET /plans/{plan_id}` 로 따로 받는다.
    """
    from datetime import timedelta

    from app.db.repo import list_plans
    from app.tools.weather import today_kst

    today = today_kst()
    try:
        frm = date.fromisoformat(from_) if from_ else today - timedelta(days=92)
        end = date.fromisoformat(to) if to else today + timedelta(days=31)
    except ValueError as exc:
        raise HTTPException(400, "날짜는 YYYY-MM-DD 형식이어야 합니다") from exc
    if end < frm:
        raise HTTPException(400, "to 가 from 보다 앞섭니다")

    try:
        plans = await list_plans(user_id or DEMO_USER, frm, end)
    except Exception as exc:
        # 캘린더가 안 열려도 오늘의 일정은 돌아야 한다. 빈 목록으로 내린다.
        logger.warning("일정 목록 조회 실패: %s", exc)
        return {"from": frm.isoformat(), "to": end.isoformat(),
                "plans": [], "reason": "unavailable"}
    return {"from": frm.isoformat(), "to": end.isoformat(), "plans": plans}


@app.get("/plans/detail/{plan_id}")
async def get_plan(plan_id: str):
    """캘린더에서 날짜를 눌렀을 때 펼칠 그날 일정 전체 (UR-28).

    경로가 `/plans/{plan_id}` 가 아니라 `/plans/detail/{plan_id}` 인 이유 —
    위의 `/plans/{user_id}` 와 한 자리에서 부딪힌다. 둘 다 문자열이라
    FastAPI 가 먼저 선언된 쪽으로만 보내, 상세 조회가 영원히 목록으로 간다.
    """
    from app.db.repo import load_plan

    try:
        itinerary = await load_plan(plan_id)
    except Exception as exc:
        logger.warning("일정 조회 실패: %s", exc)
        raise HTTPException(503, "지금은 일정을 불러올 수 없습니다") from exc
    if itinerary is None:
        raise HTTPException(404, "일정을 찾을 수 없습니다")
    return {"itinerary": _jsonable(itinerary)}


@app.post("/collections")
async def create_collection(req: SaveCollectionIn):
    from app.memory.curation import save_collection

    if not req.title.strip():
        raise HTTPException(400, "이름이 필요합니다")
    if not req.place_ids:
        raise HTTPException(400, "담을 장소가 없습니다")
    try:
        return await save_collection(
            req.user_id or DEMO_USER, req.title.strip(), req.place_ids,
            emoji=req.emoji, subtitle=req.subtitle, note=req.note)
    except Exception as exc:
        logger.warning("컬렉션 저장 실패: %s", exc)
        # 저장이 안 됐는데 됐다고 하면 사용자는 다시 담지 않는다. 실패는 밝힌다.
        raise HTTPException(503, "지금은 저장할 수 없습니다") from exc


@app.delete("/collections/{collection_id}")
async def remove_collection(collection_id: str, user_id: str = ""):
    from app.memory.curation import delete_collection

    ok = await delete_collection(user_id or DEMO_USER, collection_id)
    if not ok:
        raise HTTPException(404, "컬렉션을 찾을 수 없습니다")
    return {"ok": True}


@app.delete("/collections/{collection_id}/places/{place_id}")
async def remove_collection_place(collection_id: str, place_id: str, user_id: str = ""):
    from app.memory.curation import remove_place

    ok = await remove_place(user_id or DEMO_USER, collection_id, place_id)
    if not ok:
        raise HTTPException(404, "담긴 장소가 아닙니다")
    return {"ok": True}


@app.get("/curations/{user_id}")
async def curations(user_id: str):
    """아카이브 기반 큐레이션 지도.

    남의 테마 지도를 보여주는 게 아니라 내 방문 기록에서 테마를 뽑는다.
    각 장소에는 '왜 여기 들어왔는지'(reason)가 붙는다.
    """
    from app.memory.curation import build_collections

    collections = await build_collections(user_id)
    return {"collections": collections, "count": len(collections)}


@app.get("/geocode")
async def geocode_place(q: str):
    """장소명·주소 → 좌표. 출발지를 직접 지정할 때 쓴다."""
    from app.tools.maps import geocode

    point = await geocode(q)
    if point is None:
        return {"lat": None, "lng": None, "label": None}
    return {"lat": point.lat, "lng": point.lng, "label": point.name or q}


@app.get("/whereami")
async def whereami(lat: float, lng: float):
    """좌표 → 자치구 이름.

    '현재 위치'만 보여주면 사용자는 앱이 어디를 잡았는지 알 수 없다.
    '현재 위치 (서울 종로구)'라고 해야 틀렸을 때 바로 알아챈다.
    """
    from app.schemas import GeoPoint
    from app.tools.maps import reverse_geocode

    address = await reverse_geocode(GeoPoint(lat=lat, lng=lng))
    if not address:
        return {"region": None, "address": None}

    parts = address.split()
    # '서울특별시 종로구 …' → '서울 종로구'
    region = None
    for i, token in enumerate(parts):
        if token.endswith(("구", "군")) and i > 0:
            city = parts[0].replace("특별시", "").replace("광역시", "")
            region = f"{city} {token}"
            break
    return {"region": region or (parts[0] if parts else None), "address": address}


@app.get("/health")
async def health():
    return {"status": "ok"}


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode()


def _jsonable(obj: Any) -> Any:
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, list):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    return obj


@app.get("/diagnostics")
async def diagnostics(probe: bool = False):
    """어떤 키가 설정됐고, 실제로 응답하는지 한 번에 확인한다.

    키를 넣었는데 화면이 비는 상황에서 원인을 좁히는 게 목적이다.
    `?probe=true` 를 붙이면 외부 API를 실제로 한 번씩 호출해 본다.
    """

    from app.config import get_settings
    from app.llm.provider import effective_backend
    from app.schemas import GeoPoint, TripConditions, utc_now

    s = get_settings()
    configured = {
        "llm": {
            "requested": s.llm_backend,
            "effective": effective_backend(),   # 키가 없으면 여기서 폴백된 값이 보인다
            "keys": {"nvidia": bool(s.nvidia_api_key), "openai": bool(s.openai_api_key),
                     "anthropic": bool(s.anthropic_api_key)},
        },
        "naver_maps": bool(s.naver_client_id and s.naver_client_secret),
        "naver_local_search": bool(s.naver_search_client_id and s.naver_search_client_secret),
        "culture_api": bool(s.culture_key),
        "weather": {"key": bool(s.weather_key), "source": s.weather_source},
        "websearch": bool(s.tavily_api_key or s.exa_api_key),
        "budget": {"total_s": s.total_budget_s, "router_timeout_s": s.router_timeout_s,
                   "verify_top_k": s.verify_top_k},
    }
    result: dict[str, Any] = {"configured": configured}
    if not probe:
        result["hint"] = "실제 호출까지 확인하려면 /diagnostics?probe=true"
        return result

    import asyncio

    from app.tools import culture_api, maps, weather, websearch
    from app.tools.base import _cache
    from app.tools.http import last_error

    _cache.clear()   # 진단은 항상 실제 호출로 확인한다(캐시된 실패를 재활용하지 않음)

    seoul = GeoPoint(lat=37.5665, lng=126.9780, name="서울시청")
    today = weather.today_kst()
    conditions = TripConditions(date=today, origin=seoul, region="서울")
    probes: dict[str, Any] = {}

    geo = await maps.geocode("서울특별시 중구 세종대로 110")
    probes["naver_geocode"] = {
        "ok": geo is not None,
        "sample": geo.model_dump() if geo else None,
        "error": last_error("naver.geocode"),
    }

    route = await maps.route_duration(seoul, GeoPoint(lat=37.5796, lng=126.9770), "car")
    probes["naver_directions"] = {
        "ok": not route["estimated"], "minutes": route["minutes"],
        "estimated": route["estimated"], "error": last_error("naver.directions"),
    }

    base_date, base_time = weather.latest_base(weather.now_kst())
    ultra_date, ultra_time = weather.latest_ultra_base(weather.now_kst())
    fc = await weather.hourly(seoul, today)
    now_obs = await weather.current(seoul)
    ultra_hours = [h for h, v in fc.items() if v.get("source") == "ultra_short"]
    probes["weather"] = {
        "ok": bool(fc), "hours": len(fc), "risky": weather.risky_hours(fc)[:5],
        "ultra_short_hours": ultra_hours,          # 초단기로 덮어쓴 시간대
        "now": now_obs or None,                    # 초단기실황
        "asked": {"vilage": [base_date, base_time],
                  "ultra": [ultra_date, ultra_time],
                  "grid": weather.latlng_to_grid(seoul.lat, seoul.lng),
                  "target": str(today)},
        "error": last_error(f"kma.{s.weather_source}"),
        "error_ultra": last_error(f"kma.{s.weather_source}.ultra"),
        "error_ncst": last_error(f"kma.{s.weather_source}.ncst"),
    }

    # 공공 API가 실제로 답했는지와, 폴백이 대신 채웠는지를 나눠서 본다.
    # 합쳐서 세면 키가 잘못됐거나 활용신청이 안 된 상태에서도 초록불이 떠서
    # 진단이 제 일을 못 한다 — 이 엔드포인트의 존재 이유가 바로 그 구분이다.
    events = await culture_api.search_events(conditions, limit=5)
    from_api = [e for e in events if e.source != "web"]
    probes["culture_api"] = {
        "ok": bool(from_api),
        "count": len(events),
        "from_api": len(from_api),
        "from_fallback": len(events) - len(from_api),   # 웹검색이 대신 채운 수
        "sample": [e.name[:60] for e in events[:3]],
        "endpoint": s.culture_api_endpoint,
        "error": last_error("culture.period"),
    }

    facilities = await culture_api.search_always_on(conditions, limit=5)
    from_facility = [f for f in facilities if f.source != "naver_local"]
    probes["culture_facility"] = {
        "ok": bool(from_facility),
        "count": len(facilities),
        "from_api": len(from_facility),
        "from_fallback": len(facilities) - len(from_facility),   # 네이버 지역검색 대체분
        "sample": [f.name for f in facilities[:3]],
        "endpoint": s.culture_facility_endpoint or None,
        # 오퍼레이션별로 따로 부르므로 에러도 오퍼레이션 단위로 남는다
        "error": next((last_error(f"culture.facility.{op}")
                       for op in ("artgallery", "museum", "performingplace")
                       if last_error(f"culture.facility.{op}")), None),
    }

    nearby = await maps.search_nearby(seoul, "cafe", radius_m=1000, limit=3)
    probes["naver_local_search"] = {
        "ok": bool(nearby), "count": len(nearby),
        "sample": [n["name"] for n in nearby[:3]],
        "error": last_error("naver.local") or (
            None if s.naver_search_client_id else "NAVER_SEARCH_CLIENT_ID/SECRET 미설정"),
    }

    hits = await websearch.search("대림미술관 운영시간", k=2)
    probes["websearch"] = {"ok": bool(hits), "count": len(hits),
                           "provider": "tavily" if s.tavily_api_key else
                                       ("exa" if s.exa_api_key else None),
                           "error": last_error("tavily") or last_error("exa")}

    # 도보·대중교통 경로. 여기가 비면 이동시간이 전부 '(추정)'이 되는데,
    # 그 사실이 화면에는 작게 표시돼 눈치채기 어렵다. 진단에서는 분명히 갈라 놓는다.
    seoul_stn = GeoPoint(lat=37.5547, lng=126.9707, name="서울역")
    for mode, name, key_set in (("walk", "ors", bool(s.ors_api_key)),
                                ("subway", "odsay", bool(s.odsay_api_key))):
        leg = await maps.route_duration(seoul, seoul_stn, mode)
        probes[name] = {
            # 키가 있는데도 estimated 로 내려왔다면 실측이 실패한 것이다
            "ok": bool(leg) and not leg["estimated"],
            "minutes": leg["minutes"] if leg else None,
            "source": leg["source"] if leg else None,
            "error": last_error(f"{name}.matrix") or last_error(f"{name}.directions")
                     or last_error(f"{name}.path")
                     or (None if key_set else f"{name.upper()}_API_KEY 미설정"),
        }

    # LLM 왕복. 키가 있어도 모델명이 틀리거나 쿼터가 끝나면 여기서만 드러난다.
    try:
        from app.llm.provider import get_chat_model

        res = await asyncio.wait_for(
            get_chat_model("fast", temperature=0).ainvoke(
                [{"role": "user", "content": "ping 이라고만 답하라"}]), timeout=20)
        probes["llm"] = {"ok": bool(getattr(res, "content", None)),
                         "backend": effective_backend(),
                         "sample": str(getattr(res, "content", ""))[:40]}
    except Exception as exc:
        probes["llm"] = {"ok": False, "backend": effective_backend(),
                         "error": f"{type(exc).__name__}: {exc}"[:200]}

    result["probes"] = probes
    result["summary"] = [k for k, v in probes.items() if not v.get("ok")]
    result["server_time"] = {"utc": utc_now().isoformat(timespec="seconds"),
                             "kst": weather.now_kst().isoformat(timespec="seconds")}
    return result
