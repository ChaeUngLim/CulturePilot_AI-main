"""시각과 체류시간 표현을 읽는다.

여기만 따로 뗀 이유 — 한국어 시각 표현은 **오전/오후를 안 붙이는 쪽이 기본**이라
같은 «7시» 가 출발이면 아침, 도착이면 저녁이다. 이 판단이 한 곳에 모여 있지 않으면
호출부마다 다르게 읽혀서 «7시 출발 21시 도착» 이 두 시간짜리 창이 된다(실제로 겪었다).

체류시간(`_detect_dwell`)이 여기 있는 것도 같은 이유다. '1시간~2시간'의 '1시'가
시각 파서에 걸리므로, **시각을 읽기 전에 먼저 떼어내야 한다.** 두 파서가 같은
문자열을 두고 다투는 자리라 한 모듈에 둔다.
"""
from __future__ import annotations

import re
from datetime import time as dt_time

# 시각 표현. "9시에", "오후 1시", "13시", "5시에" 를 잡는다.
_TIME = re.compile(
    r"(오전|오후|아침|점심|저녁|밤)?\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?"
)
# 하루의 끝을 '까지'로만 말하는 경우 — "저녁 7시까지", "8시까지".
# `_TIME` 과 그룹 번호를 맞춰야 `_to_time()` 을 그대로 쓸 수 있다.
_UNTIL_TIME = re.compile(
    r"(오전|오후|아침|점심|저녁|밤)?\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?\s*까지"
)

# 체류시간. '1시간~2시간'은 '1시'로도 읽히므로 시각 파싱보다 **먼저** 떼어내야 한다.
# '시간'은 통째로 선택이어야 한다. '시간?' 으로 쓰면 '시'는 필수가 되어
# '1~2시간'이 매칭되지 않는다 — 실제로 겪은 버그다.
_DWELL_RANGE = re.compile(
    r"(\d+)\s*(?:시간)?\s*(?:~|-|–|에서|부터)\s*(\d+)\s*시간")
_DWELL_RANGE_MIN = re.compile(
    r"(\d+)\s*(?:분)?\s*(?:~|-|–|에서|부터)\s*(\d+)\s*분")
_DWELL_ONE = re.compile(
    r"(\d+)\s*시간\s*(?:씩|정도|가량|쯤|내외|이내|안팎)")
_DWELL_ONE_MIN = re.compile(
    r"(\d+)\s*분\s*(?:씩|정도|가량|쯤|내외|이내|안팎)")
_DWELL_WORD = (("한두 시간", 60, 120), ("두세 시간", 120, 180),
               ("반나절", 180, 240), ("한 시간씩", 60, 60))


def _time_in(chunk: str, *, pm_bias: bool = True) -> tuple[dt_time, int] | None:
    """절 안의 **마지막** 시각. 키워드에 가장 가까운 것이 그 절의 시각이다.

    첫 번째를 쓰면 '9시에 나가서 … 저녁 8시에 퇴근'에서 퇴근 시각이 9시가 된다.
    """
    last = None
    for m in _TIME.finditer(chunk):
        at = _to_time(m, pm_bias=pm_bias)
        if at:
            last = (at, m.start())
    return last


def _to_time(m: re.Match, *, pm_bias: bool = True) -> dt_time | None:
    """`pm_bias` — 오전/오후를 안 붙인 1~7시를 오후로 볼 것인가.

    '5시에 보자'는 대개 오후라 기본은 오후로 민다. 다만 **출발 시각에는 반대**다 —
    '7시 출발'은 아침이다. 그대로 19시로 읽으면 '7시 출발 21시 도착'이 두 시간짜리
    창이 되어 일정이 한 곳도 못 들어간 채 빈 결과가 나간다.
    """
    hour, minute = int(m.group(2)), int(m.group(3) or 0)
    marker = m.group(1) or ""
    if marker in ("오후", "저녁", "밤") and hour < 12:
        hour += 12
    elif marker in ("오전", "아침") and hour == 12:
        hour = 0
    elif pm_bias and not marker and 1 <= hour <= 7:
        hour += 12
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return dt_time(hour, minute)


def _fix_am_pm(start: dt_time | None, end: dt_time | None) -> tuple[dt_time | None, dt_time | None]:
    """오전/오후를 안 붙인 시각을 앞뒤 관계로 바로잡는다.

    `_to_time` 은 표시가 없는 1~7시를 오후로 본다. "5시에 보자"는 대개 오후이기 때문인데,
    출발 시각에는 반대로 오전이 흔하다 — "7시 출발 … 21시 도착"의 7시는 아침이다.

    그대로 두면 19시 출발 / 21시 종료가 되어 창이 2시간으로 줄고, 일정이 한 곳도
    들어가지 못한 채 빈 결과가 나간다. 사용자는 왜 비었는지 알 수 없다.

    되돌리는 조건은 하나다 — **출발이 종료보다 늦을 때.** 그건 파서가 틀렸다는
    뜻이지 사용자가 그렇게 말한 게 아니다. 밤을 넘기는 일정은 다루지 않는다.
    """
    if start is None or end is None or start <= end:
        return start, end

    # 종료를 오전으로 읽었을 가능성을 먼저 본다. "5시에 만나 8시까지"의 8시는 저녁이다.
    # 여기서 시작을 05:00 으로 끌어내리면 사용자가 말한 '5시 약속'이 사라진다.
    if end.hour <= 11 and end.hour + 12 > start.hour:
        return start, end.replace(hour=end.hour + 12)
    if 13 <= start.hour <= 19:              # 오후로 밀어 둔 1~7시
        return start.replace(hour=start.hour - 12), end
    return start, end


def _detect_dwell(query: str) -> tuple[int | None, int | None, str]:
    """장소마다 얼마나 머물지. 범위로 말하면 그대로, 하나만 말하면 그 값으로 고정한다.

    잘라낸 나머지를 함께 돌려주는 이유는 '1시간~2시간'의 '1시'가 시각 파서에
    걸려서 출발 시각이나 일정 항목으로 둔갑하기 때문이다.
    """
    for word, lo, hi in _DWELL_WORD:
        if word in query:
            return lo, hi, query.replace(word, " ")

    for pattern, unit in ((_DWELL_RANGE, 60), (_DWELL_RANGE_MIN, 1)):
        m = pattern.search(query)
        if m:
            lo, hi = int(m.group(1)) * unit, int(m.group(2)) * unit
            if lo > hi:
                lo, hi = hi, lo
            return _clamp_dwell(lo), _clamp_dwell(hi), _blank(query, m)

    for pattern, unit in ((_DWELL_ONE, 60), (_DWELL_ONE_MIN, 1)):
        m = pattern.search(query)
        if m:
            v = _clamp_dwell(int(m.group(1)) * unit)
            return v, v, _blank(query, m)

    return None, None, query


def _clamp_dwell(minutes: int) -> int:
    """상식 밖의 값은 자른다. 10분짜리 전시도, 8시간짜리 카페도 일정이 아니다."""
    return max(20, min(minutes, 300))


def _blank(query: str, m: re.Match) -> str:
    return query[:m.start()] + " " * (m.end() - m.start()) + query[m.end():]
