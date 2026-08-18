"""하루의 경계와 «이 자리에 무엇을 넣을까»의 판단들.

`schedule` 과 `fill_gaps` 가 같이 쓰는 순수 함수들이다. 전부 부수효과가 없고
외부 호출도 없어서, 편성 결과가 이상할 때 여기부터 단위로 재현해 볼 수 있다.

쿼터(종류별 개수)가 여기 있는 이유 — 「문화 2 + 디저트 3」은 **배치 규칙**이지
후보의 속성이 아니다. 스케줄러가 못 채운 몫을 빈틈 채우기가 이어받아야 해서,
두 곳이 같은 함수를 봐야 «디저트 3개»가 통째로 사라지지 않는다.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from app.graph.state import ItineraryState
from app.schemas import Candidate, GeoPoint, ItineraryItem, group_of
from app.tools import maps, weather
from app.tools.maps import _haversine_km

DEFAULT_DAY_START = time(10, 0)
DEFAULT_DAY_END = time(20, 0)
MEAL_WINDOWS = ((time(11, 30), time(13, 30), "meal"), (time(17, 30), time(19, 30), "meal"))
# 이동시간 행렬은 상위 12개까지만 만든다(N² 호출). 편성은 그보다 넓게 보고,
# 행렬 밖 후보의 이동시간은 직선거리로 어림한다(`_travel_or_estimate`).
SCHEDULE_POOL = 24


def _to_item(cand: Candidate, seq: int, arrive: datetime, travel: int,
             c, ctx: dict[str, Any], *, fixed: bool = False,
             purpose: str = "any") -> ItineraryItem:
    return ItineraryItem(
        seq=seq, candidate_id=cand.id, place_id=cand.place_id, name=cand.name,
        kind=cand.kind, geo=cand.geo, arrive=arrive,
        depart=arrive + timedelta(minutes=cand.expected_dwell_min),
        dwell_min=cand.expected_dwell_min, travel_min_from_prev=travel,
        transport=c.transport if c.transport != "unknown" else "best",
        indoor=cand.indoor,
        fixed_time=fixed, purpose=purpose,          # type: ignore[arg-type]
        parking=cand.parking, parking_note=cand.parking_note,
        verify_status=cand.verify_status,
        reason=_reason(cand, ctx, travel, fixed=fixed),
    )


def _meal_slot(cursor: datetime, open_meals: set[str],
               placed: list[ItineraryItem]) -> tuple[str, ...] | None:
    """이 시각이 '식사 자리'인지 판단한다.

    사용자가 식사를 요청했고, 지금이 식사 시간대이며, 그 시간대에 아직
    식사를 배치하지 않았을 때만 참이다. 시간대마다 한 끼면 충분하다.
    """
    if not open_meals:
        return None
    for start, end, _ in MEAL_WINDOWS:
        if not (start <= cursor.time() <= end):
            continue
        served = any(
            i.purpose == "meal" and i.arrive
            and start <= i.arrive.time() <= end for i in placed)
        if served:
            return None
        return ("food",) if "meal" in open_meals else ("cafe",)
    return None


def _match_stop(stop, cands: list[Candidate], used: set[str]) -> Candidate | None:
    """시각 지정 요청에 맞는 후보를 고른다.

    장소를 이름으로 지목했으면 그걸 우선한다. 목적만 말했으면(식사·카페)
    해당 종류에서 점수가 높은 것을 쓴다.
    """
    pool = [x for x in cands if x.id not in used]
    if not pool:
        return None

    if stop.place_hint:
        hint = stop.place_hint.replace(" ", "")
        exact = [x for x in pool if hint in x.name.replace(" ", "")]
        if exact:
            return max(exact, key=lambda x: x.final_score)
        # 이름이 안 맞으면 단서의 앞부분(지역·브랜드)으로 한 번 더 본다
        head = hint[:3]
        loose = [x for x in pool if head and head in x.name.replace(" ", "")]
        if loose:
            return max(loose, key=lambda x: x.final_score)

    kinds = {"meal": ("food",), "cafe": ("cafe",), "rest": ("cafe", "park")}.get(
        stop.purpose, ())
    if kinds:
        typed = [x for x in pool if x.kind in kinds]
        if typed:
            return max(typed, key=lambda x: x.final_score)
        return None      # 식사 자리에 미술관을 넣지 않는다 — 공백으로 두는 게 낫다

    return max(pool, key=lambda x: x.final_score)


def _quota_room(quota: dict[str, int], placed: dict[str, int], kind: str) -> bool:
    """이 종류를 아직 더 넣어도 되는가.

    어느 그룹에도 속하지 않는 종류(`other`)는 제한하지 않는다 — 사용자가 말한
    적이 없는 축으로 자리를 막으면, 말한 개수를 채우지 못한 채 하루가 끝난다.
    사용자가 말하지 않은 그룹도 마찬가지로 통과시킨다(총량 max_stops 가 받는다).
    """
    group = group_of(kind)
    if group is None or group not in quota:
        return True
    return placed.get(group, 0) < quota[group]


def _quota_take(placed: dict[str, int], kind: str) -> None:
    """한 자리를 그룹 몫에서 차감한다(넣은 뒤에 부른다)."""
    group = group_of(kind)
    if group:
        placed[group] = placed.get(group, 0) + 1


def _quota_unmet(quota: dict[str, int], placed: dict[str, int], kind: str) -> bool:
    """이 종류가 «아직 몫이 남은 그룹»에 속하는가."""
    group = group_of(kind)
    return bool(group and placed.get(group, 0) < quota.get(group, 0))


def _quota_missing(quota: dict[str, int], placed: dict[str, int]) -> int:
    """말한 개수 중 아직 못 채운 자리 수."""
    return sum(max(0, n - placed.get(g, 0)) for g, n in quota.items())


def _reserve_to_dest(cands: list[Candidate], dest: GeoPoint, mode: str) -> int:
    """마지막 장소에서 도착지까지 걸릴 시간을 미리 잡아 둔다.

    어느 곳이 마지막이 될지는 아직 모르므로, 후보들의 도착지 거리 중앙값으로
    잡는다. 최악을 잡으면 일정이 지나치게 짧아지고, 최선을 잡으면 약속에 늦는다.
    """
    dists = sorted(_haversine_km(c.geo, dest) for c in cands if c.geo)
    if not dists:
        return 20
    km = dists[len(dists) // 2]
    speed = {"walk": 4.5, "bus": 14.0, "transit": 18.0,
             "subway": 22.0, "car": 22.0}.get(mode, 18.0)
    return max(10, min(int(km * 1.35 / speed * 60) + 5, 60))


def _slots_left(cands: list[Candidate], used: set[str], cursor: datetime,
                day_end: datetime, cap: int) -> float:
    """지금 고르는 자리를 포함해 앞으로 몇 곳이 더 들어갈지 추정한다.

    1에 가까울수록 '마지막 자리'라는 뜻이고, 도착지 쪽으로 강하게 끌어당긴다.
    시계 기준(하루의 몇 %가 지났나)으로 하면 후보가 적을 때 마지막 자리를
    끝까지 인식하지 못해 도착지에서 먼 곳으로 하루가 끝난다.
    """
    rest = [c for c in cands if c.id not in used]
    if not rest:
        return 1.0
    avg = sum(c.expected_dwell_min for c in rest) / len(rest) + 20   # 이동 여유
    by_time = (day_end - cursor).total_seconds() / 60 / max(avg, 1)
    return max(min(by_time, float(cap), float(len(rest))), 1.0)


def _travel_or_estimate(matrix: list[list[int]], points: list[GeoPoint],
                        pos: int | None, cand: Candidate,
                        idx: int | None) -> int:
    """이동시간. 행렬 밖 후보는 직선거리로 어림한다.

    행렬은 N² 호출이라 상위 12개까지만 만든다. 그런데 편성은 그보다 넓게 보는데,
    행렬 밖이라고 일괄 15분을 주면 먼 곳이 가까운 곳처럼 보여 엉뚱한 순서가 나온다.
    최종 선택된 구간은 뒤에서 `_measure_legs` 가 실측하므로 여기서는 거리 비교만
    맞으면 된다.
    """
    if pos is not None and idx is not None and matrix:
        try:
            return matrix[pos][idx]
        except IndexError:
            pass
    frm = points[pos] if pos is not None and pos < len(points) else None
    if frm and cand.geo:
        km = _haversine_km(frm, cand.geo) * maps.DETOUR_FACTOR
        return max(5, int(km / 18.0 * 60))     # 도심 평균 18km/h
    return 15


def _within_hours(c: Candidate, arrive: datetime, depart: datetime) -> bool:
    hours = c.opening_hours or {}
    weekday = arrive.strftime("%a").lower()
    if weekday in [d.lower()[:3] for d in (c.closed_days or [])]:
        return False
    window = hours.get(weekday) or hours.get("default")
    if not window:
        return True
    try:
        open_t = time.fromisoformat(window["open"])
        close_t = time.fromisoformat(window["close"])
    except (KeyError, ValueError, TypeError):
        return True
    return open_t <= arrive.time() and depart.time() <= close_t


def _locked_places(state: ItineraryState) -> set[str]:
    """사용자가 HITL에서 '유지'를 선택한 장소는 절대 빼지 않는다."""
    locked: set[str] = set()
    for d in state.get("decisions") or []:
        pid = (d.note or "").strip()
        if pid:
            locked.add(pid)
    for pid in (state["conditions"].must_include or []):
        locked.add(pid)
    return locked


def _purpose(t: datetime) -> str:
    for start, end, kind in MEAL_WINDOWS:
        if start <= t.time() <= end:
            return kind
    return "free"


def _reason(c: Candidate, ctx: dict[str, Any], travel: int, *,
            fixed: bool = False) -> str:
    bits = []
    if fixed:
        bits.append("지정 시각")
    bits.append(f"이동 {travel}분{' (추정)' if ctx.get('travel_estimated') else ''}")
    now = ctx.get("weather_now") or {}
    if now.get("condition") in weather.BAD_CONDITIONS and c.indoor:
        bits.append(f"현재 {now['condition']} — 실내 우선")
    elif ctx.get("risky_hours") and c.indoor:
        bits.append("악천후 시간대 실내 우선")
    if ctx.get("pref_boost", {}).get(c.place_id):
        bits.append("과거 만족 기록")
    if c.verify_status == "verified":
        bits.append("공식정보 검증됨")
    if c.parking == "free":
        bits.append("무료주차")
    elif c.parking == "none":
        bits.append("주차 불가")
    return " · ".join(bits)
