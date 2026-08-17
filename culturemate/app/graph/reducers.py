"""State 리듀서.

병렬 노드와 서브그래프가 같은 키에 동시에 쓰기 때문에, 단순 `operator.add`는
서브그래프가 부모의 누적값을 되돌려줄 때 중복을 만든다. 여기서는 모두
'id 기준 멱등 병합'을 기본으로 삼는다.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


def _key_of(item: Any, key: str) -> Any:
    if isinstance(item, BaseModel):
        return getattr(item, key, None)
    if isinstance(item, dict):
        return item.get(key)
    return item


def merge_by_id(key: str = "id", pick: Callable[[Any, Any], Any] | None = None):
    """id 중복을 제거하며 리스트를 병합한다. pick(old, new)로 충돌 해소."""

    def _reducer(left: list | None, right: list | None) -> list:
        left = list(left or [])
        right = list(right or [])
        if not left:
            return right
        if not right:
            return left
        index: dict[Any, int] = {}
        out: list = []
        for item in left + right:
            k = _key_of(item, key)
            if k is None:
                out.append(item)
                continue
            if k in index:
                pos = index[k]
                out[pos] = pick(out[pos], item) if pick else item
            else:
                index[k] = len(out)
                out.append(item)
        return out

    return _reducer


def merge_candidates(left: list | None, right: list | None) -> list:
    """후보 장소 병합. 같은 canonical place는 정보가 더 풍부한 쪽을 남긴다."""
    left, right = list(left or []), list(right or [])
    if not left:
        return right
    if not right:
        return left

    def ckey(c: Any) -> Any:
        pid = _key_of(c, "place_id")
        return pid or f"name::{_key_of(c, 'name')}"

    index: dict[Any, int] = {}
    out: list = []
    for c in left + right:
        k = ckey(c)
        if k in index:
            old = out[index[k]]
            out[index[k]] = _merge_candidate_pair(old, c)
        else:
            index[k] = len(out)
            out.append(c)
    return out


def _merge_candidate_pair(old: Any, new: Any) -> Any:
    if not isinstance(old, BaseModel) or not isinstance(new, BaseModel):
        return new
    merged = old.model_copy(deep=True)
    for field, value in new.model_dump(exclude_unset=False).items():
        if value in (None, [], {}, 0.0, "unknown"):
            continue
        current = getattr(merged, field, None)
        if current in (None, [], {}, 0.0, "unknown"):
            setattr(merged, field, getattr(new, field))
    merged.relevance = max(old.relevance, new.relevance)
    merged.personal_score = max(old.personal_score, new.personal_score)
    merged.final_score = max(old.final_score, new.final_score)
    return merged


def append_unique_str(left: list[str] | None, right: list[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in list(left or []) + list(right or []):
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def merge_dict(left: dict | None, right: dict | None) -> dict:
    out = dict(left or {})
    out.update(right or {})
    return out


# ---------------------------------------------------------------------------
# 리듀서 싱글턴.
# LangGraph는 같은 채널이 여러 스키마에 나타나면 리듀서 '동일성'까지 비교한다.
# merge_by_id()를 호출할 때마다 새 함수 객체가 생기므로 반드시 아래를 재사용한다.
MERGE_BY_ID = merge_by_id()
MERGE_BY_ADVISORY_ID = merge_by_id("advisory_id")


def replace_list(left: list | None, right: list | None) -> list:
    """새 값이 오면 통째로 갈아끼운다. 검증 결과(issues·advisories)용.

    누적(merge_by_id)이 맞지 않는 자리가 있다. 검증은 **매번 현재 일정을 처음부터
    다시 본다.** 지난 라운드의 이슈는 이미 해결됐거나 그 장소가 일정에서 빠졌을 수
    있는데, 누적하면 그것들이 영원히 남는다.

    실제로 재계획(hitl → itinerary → validation)을 돌수록 카드가 불어나
    이슈 6건이 카드 25장이 됐고, 일정에서 사라진 장소의 카드가 '일정 확인 필요'
    라는 이름 없는 카드로 계속 떠 있었다.

    빈 리스트는 무시한다 — 검증을 타지 않은 경로(예: 취향 질문)가 State 를
    지나가면서 기존 카드를 지워 버리면 안 된다. 지우는 건 검증이 실제로
    돌았을 때뿐이고, 그때는 결과가 곧 전부다.
    """
    if right:
        return list(right)
    return list(left or [])
