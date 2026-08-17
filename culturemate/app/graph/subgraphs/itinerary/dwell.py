"""체류시간을 정한다 — 사용자가 말한 값이 항상 이긴다.

따로 떼어 둔 이유가 있다. 이 값 하나가 하루의 장소 수를 정한다 — 60분이면 여덟 곳,
120분이면 네 곳이다. 그래서 «말한 값 > 개인 평균 > 카탈로그 기본값» 의 우선순위가
무너지면 증상이 «일정이 이상하다» 로만 보이고 원인을 짚기 어렵다.
"""
from __future__ import annotations

import logging

from app.schemas import Candidate

logger = logging.getLogger(__name__)

# 개인 평균으로 보정할 때 허용하는 배율 범위.
# 기록이 몇 건뿐이면 평균이 20분이나 200분으로 튈 수 있는데, 그대로 곱하면
# 하루가 두 곳으로 줄거나 스무 곳으로 늘어난다. 리듬만 옮기고 형태는 지킨다.
_DWELL_SCALE_MIN, _DWELL_SCALE_MAX = 0.7, 1.6
_DWELL_BASELINE_MIN = 60.0        # 카탈로그·API 기본 체류의 기준값


def _apply_personal_dwell(cands: list[Candidate], profile) -> None:
    """말하지 않았을 때, 과거 방문 기록의 평균 체류로 리듬을 맞춘다.

    avg_dwell_min 은 방문 기록에서 계산해 두고도 일정 편성에는 쓰이지 않았다.
    '평균 78분 머무는 사람'이라는 걸 알면서 60분짜리 하루를 짜 주고 있었다.

    장소별 상대 순서는 건드리지 않는다 — 미술관이 카페보다 오래 걸린다는 건
    이 사용자가 오래 머무는 편이라는 사실과 별개다. 그래서 개별 값을 갈아끼우지
    않고 전체에 같은 배율을 곱한다.
    """
    avg = getattr(profile, "avg_dwell_min", None) if profile else None
    if not avg or avg <= 0:
        return
    scale = min(_DWELL_SCALE_MAX, max(_DWELL_SCALE_MIN, avg / _DWELL_BASELINE_MIN))
    if abs(scale - 1.0) < 0.05:       # 기본값과 사실상 같으면 건드리지 않는다
        return
    for cand in cands:
        base = cand.expected_dwell_min or 60
        cand.expected_dwell_min = max(20, int(round(base * scale / 5) * 5))
    logger.info("개인 평균 체류 %.0f분 → 예상 체류 ×%.2f", avg, scale)


def _apply_dwell(cands: list[Candidate], c, profile=None) -> None:
    """체류시간을 정한다. 사용자가 말한 값이 항상 이긴다.

    범위를 주면 그 안으로 자르고(clamp), 하나만 말하면 그 값으로 고정한다.
    자를 때 원래 값의 대소는 유지한다 — 미술관이 카페보다 오래 걸린다는 사실은
    사용자가 범위를 정했다고 없어지지 않는다.

    아무 말도 없을 때만 과거 기록의 평균으로 보정한다. 순서가 중요하다 —
    말한 값을 개인 평균으로 덮으면 "1시간씩만"이라고 한 요청이 무시된다.
    """
    lo, hi = c.dwell_min, c.dwell_max
    if lo is None and hi is None:
        _apply_personal_dwell(cands, profile)
        return
    lo = lo or hi
    hi = hi or lo
    if lo == hi:
        for cand in cands:
            cand.expected_dwell_min = lo
        return

    # 후보들의 원래 체류시간을 [lo, hi] 구간으로 선형 사상한다.
    values = [x.expected_dwell_min or 60 for x in cands]
    lowest, highest = min(values), max(values)
    span = highest - lowest
    for cand, value in zip(cands, values, strict=False):
        ratio = 0.5 if span == 0 else (value - lowest) / span
        # 10분 단위로 반올림. 괄호를 빠뜨리면 /10 이 보간값에만 걸려
        # 660분짜리 일정이 나온다 — 실제로 겪은 버그다.
        cand.expected_dwell_min = int(round((lo + ratio * (hi - lo)) / 10) * 10)
