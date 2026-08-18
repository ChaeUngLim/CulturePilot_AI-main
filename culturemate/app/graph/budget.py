"""응답 시간 예산.

타임아웃과 다르다. 타임아웃은 시간이 다 되면 **결과 없이** 끊지만, 예산은
각 단계가 남은 시간을 보고 **스스로 범위를 줄여** 어떻게든 결과를 내게 한다.

일정 추천에서 사용자가 견디는 시간은 15초쯤이다. 그 안에 '완벽한 검증을 마친
일정'을 못 만든다면, '검증이 덜 된 일정 + 확인 필요 표시'가 낫다.
아무것도 없는 화면보다는.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.config import get_settings

logger = logging.getLogger(__name__)

# 단계별 예상 소요(초). 남은 시간과 비교해 건너뛸지 판단한다.
COST_VERIFY_BATCH = 2.5      # 검증 한 묶음(동시 실행 단위)
COST_TRAVEL_MATRIX = 3.0     # 지도 이동시간 행렬(N×N 한 번에)
# 확정 구간 1개를 경로 API로 실측하는 비용(초). 병렬이라 실제론 더 짧다.
COST_LEG_MEASURE = 0.4
COST_COMPOSE = 2.5           # 최종 응답 생성 — 이건 포기할 수 없다


@dataclass(frozen=True)
class Budget:
    deadline: float          # time.monotonic() 기준 종료 시각

    @classmethod
    def start(cls) -> Budget:
        return cls(deadline=time.monotonic() + get_settings().total_budget_s)

    @property
    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def allows(self, cost: float, *, reserve: float = COST_COMPOSE) -> bool:
        """이 작업을 하고도 응답 생성 시간이 남는가."""
        return self.remaining - cost >= reserve

    def fits(self, unit_cost: float, *, reserve: float = COST_COMPOSE) -> int:
        """남은 시간에 몇 묶음이나 들어가는가."""
        usable = self.remaining - reserve
        return max(0, int(usable // unit_cost)) if unit_cost > 0 else 0


def from_state(state) -> Budget:
    """State에 실린 예산을 꺼낸다. 없으면 지금부터 새로 잡는다."""
    deadline = (state or {}).get("deadline")
    if isinstance(deadline, (int, float)) and deadline > 0:
        return Budget(deadline=float(deadline))
    return Budget.start()


def log_skip(stage: str, budget: Budget, reason: str) -> None:
    logger.info("예산 부족으로 %s 축소 (남은 %.1fs): %s", stage, budget.remaining, reason)
