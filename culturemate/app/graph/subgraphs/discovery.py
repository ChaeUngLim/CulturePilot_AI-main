"""탐색·검증 서브그래프.

    START → ⟨병렬⟩ search_events / search_always_on / search_web
          → normalize(정규화·중복제거) → ⟨Send 병렬⟩ verify ×N
          → classify(verified/needs_check/excluded) → [freshness_diff] → END

기간형 행사와 상시 문화공간을 같은 레인에서 다룬다. 행사가 없는 날짜/지역에서도
일정이 성립해야 한다는 요구(문서 목표 2)를 구조로 보장하기 위해서다.
"""
from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.config import get_settings
from app.graph.state import DiscoveryOutput, DiscoveryState
from app.memory import profile as profile_mod
from app.schemas import Candidate, Evidence
from app.tools import culture_api, region, websearch
from app.tools.base import safe_call
from app.tools.verify import diff_against_snapshot, snapshot_of, verify_candidate

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ 병렬 탐색
def _deadline(state) -> float:
    """1단계 탐색은 남은 예산까지만 기다린다.

    네 갈래가 병렬로 도는데 한 갈래가 응답하지 않으면 나머지가 다 끝나도
    그 하나 때문에 12초를 더 기다리게 된다. 소스 하나가 비는 건 감당되지만
    (다른 갈래가 후보를 채운다) 예산 초과는 감당이 안 된다.
    """
    from app.graph.budget import from_state

    return from_state(state).deadline


async def search_events(state: DiscoveryState) -> dict:
    c = state["conditions"]
    items = await safe_call("culture.events", culture_api.search_events(c), [],
                            deadline=_deadline(state))
    return {"raw_candidates": items, "trace": [f"discovery.events:{len(items)}"]}


async def search_always_on(state: DiscoveryState) -> dict:
    c = state["conditions"]
    await resolve_origin(c)
    items = await safe_call("culture.always_on", culture_api.search_always_on(c), [],
                            deadline=_deadline(state))
    return {"raw_candidates": items, "trace": [f"discovery.always_on:{len(items)}"]}


async def search_catalog(state: DiscoveryState) -> dict:
    """장소 카탈로그 — 외부 API 없이 즉시 응답하는 1차 후보.

    외부 소스만으로는 결과가 0건이 되는 경우가 잦다(키 미설정, 쿼터 초과,
    공공 API가 소규모 공간을 안 담음). 카탈로그가 바닥을 받쳐 준다.
    """
    from app.tools import local_catalog

    c = state["conditions"]
    await resolve_origin(c)
    items = await safe_call("catalog", local_catalog.search(c, limit=25), [],
                            deadline=_deadline(state))
    return {"raw_candidates": items, "trace": [f"discovery.catalog:{len(items)}"]}


async def search_web(state: DiscoveryState) -> dict:
    """웹 검색 결과는 기본적으로 '근거'다. 후보가 되려면 두 관문을 통과해야 한다.

      1) 제목이 글 제목이 아니라 장소 이름일 것 (looks_like_place)
      2) 그 이름이 실제로 지오코딩될 것

    검색 결과 제목을 그대로 후보로 쓰면 "8월 서울 전시회 추천<BEST5>" 같은
    블로그 글이 일정에 장소로 들어간다. 좌표가 없으니 지도에 찍히지도, 이동시간이
    계산되지도 않는데, 사용자에게는 갈 수 있는 곳처럼 보인다.
    """
    import asyncio

    from app.tools.maps import geocode

    c = state["conditions"]
    q = " ".join(filter(None, [c.region, " ".join(c.interests[:3]), "전시 문화공간 추천"]))
    dl = _deadline(state)
    results = await safe_call("web.search", websearch.search(q, k=8), [], deadline=dl)

    # 근거는 전부 남긴다 — 버리는 건 '후보 자격'이지 정보가 아니다
    ev = [Evidence(kind="web", title=r.get("title") or "", text=(r.get("content") or "")[:400],
                   url=r.get("url"), confidence=0.4) for r in results]

    named = [r for r in results if websearch.looks_like_place(r.get("title") or "")][:6]
    points = await asyncio.gather(*(
        safe_call(f"maps.geocode:{r['title']}", geocode(r["title"]), None, deadline=dl)
        for r in named))

    cands = [
        Candidate(source="web", kind="venue", name=r["title"], geo=point,
                  address=point.name, official_url=r.get("url"), raw=r,
                  relevance=0.45)     # 웹 출처는 공공 API·카탈로그보다 낮게 시작한다
        for r, point in zip(named, points, strict=False) if point
    ]
    dropped = len(results) - len(cands)
    if dropped:
        logger.info("웹 결과 %d건 중 %d건은 장소가 아니라 근거로만 씁니다",
                    len(results), dropped)
    return {"raw_candidates": cands, "evidence": ev,
            "trace": [f"discovery.web:{len(cands)}/{len(results)}"]}


# --------------------------------------------------------------------- 정규화
async def region_points(conditions) -> list:
    """선택된 지역들의 좌표. 없으면 출발지 좌표 하나를 쓴다.

    상시 문화공간 탐색과 주변 추천은 전부 좌표 기준이다. 지역만 말한 경우
    좌표가 없으면 결과가 통째로 비므로, 지역명을 좌표로 바꿔 둔다.
    """
    import asyncio

    from app.tools.maps import _haversine_km, geocode

    # 출발지를 말로 지정한 경우("양재역에서 출발"). GPS 현재 위치보다 우선한다 —
    # 집에서 내일 일정을 짜면서 출발지를 말했다면 그게 사용자의 의도다.
    if conditions.origin_name:
        point = await safe_call(f"maps.geocode:{conditions.origin_name}",
                                geocode(conditions.origin_name), None)
        if point:
            conditions.origin = point
            conditions.origin_missing = False
            logger.info("출발지 '%s' → 좌표 (%.4f, %.4f)",
                        conditions.origin_name, point.lat, point.lng)
        else:
            conditions.origin_missing = True
            logger.info("출발지 '%s' 좌표를 찾지 못해 현재 위치를 씁니다",
                        conditions.origin_name)

    # 도착지를 말로만 지정한 경우("잠실역까지") 좌표가 없다. 스케줄러는 좌표로
    # 판단하므로 여기서 한 번 변환해 둔다 — 클라이언트가 좌표를 실어 보낸
    # 경우에는 건드리지 않는다.
    if conditions.destination_name and conditions.destination is None:
        point = await safe_call(f"maps.geocode:{conditions.destination_name}",
                                geocode(conditions.destination_name), None)
        if point:
            conditions.destination = point
            conditions.destination_missing = False
            logger.info("도착지 '%s' → 좌표 (%.4f, %.4f)",
                        conditions.destination_name, point.lat, point.lng)
        else:
            # 조용히 무시하면 사용자는 도착지를 말했는데 지도에 없는 이유를 알 수 없다.
            # 이름은 그대로 두고 플래그만 세운다 — 이름을 고치면 재조회 때마다 덧붙는다.
            conditions.destination_missing = True
            logger.info("도착지 '%s' 좌표를 찾지 못했습니다", conditions.destination_name)

    # 지점("신촌역 근처")이 구보다 우선한다 — 사용자가 더 좁게 말한 쪽을 따른다.
    # 다만 출발지를 따로 말했으면 출발지를 덮어쓰지 않는다. '판교역에서 출발해서
    # 신촌역 근처'는 신촌에서 출발한다는 뜻이 아니라 신촌에서 찾는다는 뜻이다.
    if conditions.landmark:
        point = await safe_call(f"maps.geocode:{conditions.landmark}",
                                geocode(conditions.landmark), None)
        if point:
            if not conditions.origin_name:
                conditions.origin = point
            logger.info("지점 '%s' → 좌표 (%.4f, %.4f) 반경 %sm%s",
                        conditions.landmark, point.lat, point.lng,
                        conditions.radius_m or "기본",
                        f" (출발지는 {conditions.origin_name} 유지)"
                        if conditions.origin_name else "")
            return [point]
        logger.info("지점 '%s' 좌표를 찾지 못해 구 단위로 넓힙니다", conditions.landmark)

    names = conditions.regions or ([conditions.region] if conditions.region else [])
    if not names:
        return [conditions.origin] if conditions.origin else []

    points = await asyncio.gather(*(
        safe_call(f"maps.geocode:{n}", geocode(n), None) for n in names))
    found = [p for p in points if p]

    # 출발지를 말로 정했으면 그건 '출발점'이지 '탐색 범위'가 아니다 —
    # 판교에서 출발해 강남을 돌 건데 판교 장소가 후보에 섞이면 안 된다.
    if conditions.origin_name:
        return found

    # 출발지를 말하지 않았다면, 말한 지역이 곧 하루가 시작되는 곳이다.
    # 서울에 앉아 "부산 일정 만들어줘"라고 했는데 서울에서 출발하는 일정을 주면
    # 그건 부산 일정이 아니다. 현재 위치는 참고일 뿐 출발점이 아니다.
    if found:
        near = _nearest(conditions.origin, found) if conditions.origin else found[0]
        away = conditions.origin is None or _haversine_km(conditions.origin, near) > 30
        conditions.origin = near if away else conditions.origin
        if away:
            logger.info("출발지를 말하지 않아 '%s'에서 시작합니다 (현재 위치는 참고)",
                        near.name or names[0])
        else:
            found = [conditions.origin, *found]     # 같은 생활권이면 현재 위치도 앵커
    logger.info("탐색 앵커 %d곳 (지역 %s)", len(found), names)
    return found


def _nearest(point, candidates: list):
    """여러 지역을 말했을 때 현재 위치에서 가장 가까운 쪽을 시작점으로 본다."""
    from app.tools.maps import _haversine_km

    return min(candidates, key=lambda p: _haversine_km(point, p))


async def resolve_origin(conditions) -> None:
    """탐색에 필요한 좌표를 채운다.

    좌표가 이미 있다고 건너뛰면 안 된다. 클라이언트는 GPS 현재 위치를 늘 함께
    보내므로 origin 은 거의 항상 채워져 있는데, 그 이유로 "판교역에서 출발"의
    지오코딩을 건너뛰면 사용자가 말한 출발지가 통째로 무시된다.
    """
    # 좌표가 이미 있다고 건너뛰면 안 된다. 클라이언트는 GPS 현재 위치를 늘 함께
    # 보내므로 origin 은 거의 항상 채워져 있는데, 그 이유로 건너뛰면
    #   · "판교역에서 출발"의 지오코딩이 통째로 무시되고
    #   · "부산 일정"이라고 말해도 서울(현재 위치)에서 출발하게 된다.
    named = bool(conditions.origin_name) or bool(
        conditions.destination_name and conditions.destination is None)
    located = bool(conditions.regions or conditions.region or conditions.landmark)
    if conditions.origin is None or named or located:
        await region_points(conditions)


async def normalize(state: DiscoveryState) -> dict:
    """중복 제거 + 조건 필터 + 개인화 사전 점수. 검증 비용을 줄이는 관문."""
    s = get_settings()
    c = state["conditions"]
    raw: list[Candidate] = state.get("raw_candidates") or []
    await resolve_origin(c)

    profile = await profile_mod.load_profile(state.get("user_id", ""))
    await _locate_missing(raw)

    # 좌표가 채워진 뒤에 카탈로그와 잇는다 — 연결에 좌표가 필요하다.
    # 이걸 빠뜨리면 외부 소스 후보의 place_id 가 계속 None 이고,
    # 방문 기록(POST /visits)이 실제 장소 행에 붙지 못한다.
    from app.tools.local_catalog import link_place_ids

    linked = await link_place_ids(raw)
    if linked:
        logger.info("외부 후보 %d건을 카탈로그 장소에 연결", linked)

    anchors = _anchors(c, raw)
    allowed_sido, wanted_gu = region.requested(c)
    kept: list[Candidate] = []
    no_geo = 0
    too_far = 0
    off_region = 0
    for cand in raw:
        # 좌표가 없으면 일정에 넣을 수 없다. 이동시간도, 지도 표시도,
        # 실내/야외 판단도 전부 좌표에서 나온다. 여기서 막지 않으면
        # '갈 수 없는 곳'이 일정에 들어간다.
        if cand.geo is None:
            no_geo += 1
            continue
        sido, gu = region.of_candidate(cand)
        # 시·도가 다르다고 **확인된** 것만 버린다. 모르는 후보는 통과시키고
        # 거리 상한이 받는다 — 여기서 모르는 것까지 버리면 주소를 안 주는
        # 공공 API 행사가 통째로 사라진다.
        if allowed_sido and sido and sido not in allowed_sido:
            off_region += 1
            continue
        if anchors and _far_from_all(cand, anchors):
            too_far += 1
            continue
        if _out_of_period(cand, c):
            continue
        if c.exclude and any(x in (cand.name or "") for x in c.exclude):
            continue
        cand.personal_score = profile_mod.personal_score(cand, profile)
        cand.final_score = 0.6 * cand.relevance + 0.4 * cand.personal_score
        # 말한 구에 실제로 있는 곳을 앞으로 올린다. 자르지 않는 이유는 구 경계가
        # 생활권과 다르기 때문이다 — 서초구 요청에 200m 건너 강남구 카페를
        # 없는 곳 취급하면 그건 그것대로 틀린 결과가 된다.
        if wanted_gu and gu in wanted_gu:
            cand.final_score += REGION_BONUS

        kept.append(cand)

    kept.sort(key=lambda x: x.final_score, reverse=True)
    kept = kept[: s.candidate_pool]
    if no_geo:
        logger.info("좌표 없는 후보 %d건 제외 (전체 %d건)", no_geo, len(raw))
    if off_region:
        logger.info("요청 시·도(%s) 밖 후보 %d건 제외",
                    "·".join(sorted(allowed_sido)), off_region)
    if too_far:
        logger.info("반경 %.0fkm 밖 후보 %d건 제외", MAX_ANCHOR_KM, too_far)
    return {"candidates": kept, "trace": [f"discovery.normalize:{len(kept)}"]}


# 요청 기준점에서 이만큼 넘게 떨어진 곳은 하루 일정에 넣을 수 없다.
# 서울·경기 전역을 덮으면서, 대구(280km)·청주(95km)·제주는 확실히 걸러지는 값.
#
# 시·도 판정(UR-18)이 붙은 뒤로 이 값은 **보조 수단**이다. 행정구역을 알 수 없는
# 후보 — 주소도 지오코딩 결과도 없는 것 — 를 여기서 받는다.
MAX_ANCHOR_KM = 60.0

# 말한 구(區)에 실제로 있는 후보에 얹는 가점. 「서초구」라고 했는데 60km 상한만
# 통과한 판교 장소가 1번으로 올라오던 자리다(UR-18).
# 0.15 는 relevance 한 단계(0.45 웹 → 0.6 공공)보다 작다 — 순서를 바꾸되
# 출처 신뢰도를 뒤집지는 않는 크기.
REGION_BONUS = 0.15


# 중앙값을 기준점으로 삼으려면 이만큼은 모여 있어야 한다. 후보가 서너 개뿐이면
# 그중 하나가 엉뚱해도 중앙값이 그쪽으로 끌려가 멀쩡한 후보를 자를 수 있다.
_MIN_CLUSTER = 5


def _anchors(c, cands: list[Candidate] | None = None) -> list:
    """이 요청이 '어디 이야기인지' 알려 주는 좌표들.

    출발지·도착지가 다르면 그 사이 어디든 정상이므로 둘 다 기준점으로 삼는다
    (판교역 출발 → 청계산역 도착 → 강남 일정처럼).

    둘 다 없으면 — "오후 1시부터 예술의전당 가는 일정"처럼 출발지를 안 밝힌
    요청 — 후보들이 모인 곳을 기준으로 삼는다. 예전에는 여기서 검사를 통째로
    건너뛰었고, 그래서 서울 일정에 대구국악원이 들어갔다. **중앙값**을 쓰는
    이유는 이상치에 끌려가지 않기 때문이다. 서울 후보 40개에 대구 1개가
    섞여 있으면 중앙값은 서울에 남고, 대구가 잘린다.
    """
    explicit = [p for p in (c.origin, c.destination) if p is not None]
    if explicit:
        return explicit

    pts = [x.geo for x in (cands or []) if x.geo]
    if len(pts) < _MIN_CLUSTER:
        return []                      # 근거가 없으면 자르지 않는다
    from statistics import median

    from app.schemas import GeoPoint

    return [GeoPoint(lat=median(p.lat for p in pts),
                     lng=median(p.lng for p in pts))]


def _far_from_all(cand: Candidate, anchors: list) -> bool:
    """어느 기준점에서도 반경 밖이면 버린다.

    지역명 필터(_in_region)를 빠져나온 것까지 여기서 막는다. 실제로 강남 요청에
    280km 떨어진 대구 갤러리가 2번째 장소로 들어간 적이 있다 — 이름·주소 기반
    필터만으로는 새고, 그때 아무도 '280km 구간'을 이상하게 보지 않았다.
    """
    return all(_km(cand.geo, a) > MAX_ANCHOR_KM for a in anchors)


def _km(a, b) -> float:
    """하버사인 거리(km). 상한 검사용이라 정밀도는 이 정도면 충분하다."""
    import math

    r = 6371.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp = p2 - p1
    dl = math.radians(b.lng - a.lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


async def _locate_missing(cands: list[Candidate], limit: int = 10) -> None:
    """주소는 있는데 좌표가 없는 후보를 구제한다.

    공공 문화 API는 행사에 좌표를 안 주는 경우가 많다. 좌표가 없다고 바로
    버리면 실제로 열리는 행사를 놓친다. 반대로 주소도 이름도 장소 같지 않은
    것(블로그 글)은 여기서도 살아나지 못한다 — 그게 이 관문의 목적이다.
    """
    import asyncio

    from app.tools.maps import geocode

    targets = [c for c in cands if c.geo is None and (c.address or c.name)][:limit]
    if not targets:
        return
    queries = [c.address or c.name for c in targets]
    points = await asyncio.gather(*(
        safe_call(f"maps.geocode:{q}", geocode(q), None) for q in queries))
    found = 0
    for cand, point in zip(targets, points, strict=False):
        if point:
            cand.geo = point
            found += 1
    if found:
        logger.info("좌표 없는 후보 %d건 중 %d건 지오코딩 성공", len(targets), found)


def dispatch_verify(state: DiscoveryState) -> list[Send]:
    """검증 팬아웃 — 상위 후보만.

    검증 1건 = 웹검색 1회 + LLM 사실추출 1회다. 후보 60개를 모두 검증하면
    외부 호출 120회가 나가는데, 최종 일정에는 최대 6곳만 들어간다.
    나머지 54개는 결과에 영향을 주지 않으면서 응답 시간만 늘린다.

    검증하지 않은 후보도 버리지 않는다. verify_status='unknown' 으로 남아
    상위 후보가 제외될 때 대체재로 쓰이고, UI에는 '확인 필요'로 표시된다.
    """
    from app.graph.budget import COST_VERIFY_BATCH, from_state, log_skip

    s = get_settings()
    budget = from_state(state)

    # 남은 시간에 들어가는 만큼만 검증한다. 검증은 한 묶음(=동시 실행 수)씩 끝난다.
    batches = budget.fits(COST_VERIFY_BATCH)
    allowed = min(s.verify_top_k, batches * s.verify_concurrency)
    if allowed < s.verify_top_k:
        log_skip("검증", budget, f"{s.verify_top_k}건 → {allowed}건")

    targets = (state.get("candidates") or [])[: max(allowed, 0)]
    if not targets:
        return ["classify"]           # 검증할 후보가 없으면 바로 분류 단계로
    return [Send("verify", {
        "candidate": c, "user_id": state.get("user_id", ""),
        "deadline": state.get("deadline"),
        "freshness": bool(state.get("flags") and state["flags"].freshness_diff),
    }) for c in targets]


async def verify_node(payload: dict) -> dict:
    c: Candidate = payload["candidate"]
    verification, evidence = await verify_candidate(c, deadline=payload.get("deadline"))
    diffs = []
    if payload.get("freshness") and c.place_id:
        snapshot, last_visit = await _load_snapshot(payload["user_id"], c.place_id)
        diffs = diff_against_snapshot(c, snapshot, last_visit)
    c.verify_status = verification.status
    if verification.status != "excluded":
        # 다음 재방문 때 '달라진 점'을 비교할 기준점을 지금 남겨 둔다.
        c.raw["snapshot"] = snapshot_of(c)
    return {"verifications": [verification], "candidates": [c],
            "place_diffs": diffs, "evidence": evidence}


async def classify(state: DiscoveryState) -> dict:
    """검증 결과를 후보에 반영하고 제외 대상을 떨어뜨린다."""
    status = {v.candidate_id: v.status for v in (state.get("verifications") or [])}
    out: list[Candidate] = []
    for c in state.get("candidates") or []:
        c.verify_status = status.get(c.id, c.verify_status)
        if c.verify_status == "excluded":
            continue
        if c.verify_status == "needs_check":
            c.final_score *= 0.8      # 완전 배제 대신 감점 → 사용자에게 '확인 필요'로 노출
        out.append(c)
    out.sort(key=lambda x: x.final_score, reverse=True)
    return {"candidates": out, "trace": [f"discovery.classify:{len(out)}"]}


async def _load_snapshot(user_id: str, place_id: str):
    from app.db.repo import load_place_snapshot

    try:
        return await load_place_snapshot(user_id, place_id)
    except Exception as exc:
        logger.warning("snapshot load degraded: %s", exc)
        return None, None


def _out_of_period(c: Candidate, cond) -> bool:
    if not cond.date:
        return False
    if c.period_start and cond.date < c.period_start:
        return True
    return bool(c.period_end and cond.date > c.period_end)


async def verify_itinerary(itinerary, candidates: list[Candidate], *,
                           budget_s: float = 60.0) -> tuple[int, list[Evidence]]:
    """**일정에 실제로 들어간 장소만** 공식정보와 대조한다.

    첫 응답(15초)에서는 검증이 거의 늘 잘린다 — 탐색·편성이 예산을 먼저 쓰고,
    한 묶음에 2.5초 + 응답 예약 2.5초가 필요한데 그만큼이 안 남는다.
    그래서 경로 실측과 같은 방식으로 뒤로 뺐다.

    후보 12개가 아니라 일정의 6~8곳을 본다. 후보 대부분은 일정에 못 들어가므로
    검증해도 화면에 나타나지 않는다 — 같은 호출 수로 훨씬 값진 결과가 나온다.

    `itinerary.items[*].verify_status` 를 제자리에서 갱신하고, 근거를 함께 돌려준다.
    """
    import asyncio
    import time as _t

    from app.graph.budget import COST_VERIFY_BATCH, Budget, log_skip

    items = [i for i in (itinerary.items if itinerary else []) if i.candidate_id]
    if not items:
        return 0, []

    by_id = {c.id: c for c in (candidates or [])}
    targets = [(i, by_id[i.candidate_id]) for i in items if i.candidate_id in by_id]
    budget = Budget(deadline=_t.monotonic() + budget_s)
    allowed = budget.fits(COST_VERIFY_BATCH) * get_settings().verify_concurrency
    if allowed < len(targets):
        log_skip("일정 검증", budget, f"{len(targets)}곳 → {allowed}곳")
        targets = targets[:max(allowed, 0)]
    if not targets:
        return 0, []

    results = await asyncio.gather(*(
        safe_call(f"verify:{c.name}", verify_candidate(c, deadline=budget.deadline),
                  (None, []))
        for _, c in targets))

    evidence: list[Evidence] = []
    done = 0
    for (item, cand), (verification, ev) in zip(targets, results, strict=False):
        if verification is None:
            continue
        item.verify_status = verification.status
        cand.verify_status = verification.status
        evidence.extend(ev or [])
        done += 1
    logger.info("일정 검증 %d곳 완료", done)
    return done, evidence


def build_discovery_graph():
    g = StateGraph(DiscoveryState, output_schema=DiscoveryOutput)
    g.add_node("search_catalog", search_catalog)
    g.add_node("search_events", search_events)
    g.add_node("search_always_on", search_always_on)
    g.add_node("search_web", search_web)
    g.add_node("normalize", normalize)
    g.add_node("verify", verify_node)
    g.add_node("classify", classify)

    for n in ("search_catalog", "search_events", "search_always_on", "search_web"):
        g.add_edge(START, n)
        g.add_edge(n, "normalize")
    g.add_conditional_edges("normalize", dispatch_verify, ["verify", "classify"])
    g.add_edge("verify", "classify")
    g.add_edge("classify", END)
    return g.compile(checkpointer=False)
