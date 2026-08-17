"""아카이브 검색 로직(순수 함수) 테스트 — DB/LLM 없이 돈다."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("LLM_BACKEND", "fake")

from app.memory.retriever import fuse_facets, personalize, rrf_fuse
from app.schemas import ArchiveHit, TripConditions


def _hit(hid: str, **kw) -> ArchiveHit:
    base = dict(id=hid, source_type="visit", source_id=hid, summary=f"기록 {hid}")
    base.update(kw)
    return ArchiveHit(**base)


def test_rrf_prefers_documents_ranked_high_by_both():
    dense = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    lexical = [{"id": "c"}, {"id": "a"}, {"id": "z"}]
    fused = rrf_fuse(dense, lexical)
    assert fused[0]["id"] == "a"                    # 양쪽 상위 → 최고점
    assert {r["id"] for r in fused} == {"a", "b", "c", "z"}
    assert fused[0]["dense_rank"] == 1 and fused[0]["lexical_rank"] == 2


def test_recency_decay_and_friction_boost():
    now = datetime.now(timezone.utc)
    old_clean = _hit("old", fused_score=1.0, occurred_at=now - timedelta(days=720))
    recent_friction = _hit("new", fused_score=1.0, friction=["parking"],
                           occurred_at=now - timedelta(days=10))
    ranked = personalize([old_clean, recent_friction], None)
    assert ranked[0].id == "new"                    # 최신 + 불편 기록이 앞으로
    assert ranked[0].final_score > ranked[1].final_score


def test_context_match_boosts_same_situation():
    now = datetime.now(timezone.utc)
    same = _hit("same", fused_score=1.0, occurred_at=now,
                meta={"companions": "family", "transport": "car"})
    other = _hit("other", fused_score=1.0, occurred_at=now, meta={})
    cond = TripConditions(companions="family", transport="car")
    ranked = personalize([same, other], cond)
    assert ranked[0].id == "same"


def test_multi_facet_hit_wins():
    shared_a = _hit("a", facet="similar_place", final_score=0.5)
    shared_b = _hit("a", facet="friction_edit", final_score=0.5)
    only = _hit("b", facet="similar_place", final_score=0.9)
    fused = fuse_facets([[shared_a, only], [shared_b]], final_k=5)
    assert fused[0].id == "a"                       # 두 facet 모두에 잡힌 기록이 강한 신호


def test_dwell_range_is_applied_to_candidates():
    """'장소마다 1~2시간'이면 후보 체류시간이 그 범위 안으로 들어와야 한다.

    다만 원래 값의 대소는 유지한다 — 미술관이 카페보다 오래 걸린다는 사실은
    사용자가 범위를 정했다고 사라지지 않는다.
    """
    from app.graph.router import _rule_conditions
    from app.graph.subgraphs.itinerary import _apply_dwell
    from app.schemas import Candidate

    cands = [
        Candidate(id="a", name="대형 미술관", expected_dwell_min=150),
        Candidate(id="b", name="작은 갤러리", expected_dwell_min=40),
        Candidate(id="c", name="카페", expected_dwell_min=45),
    ]
    c = _rule_conditions("강남 문화생활 추천, 장소마다 1시간~2시간 사이로 머물꺼야")
    assert (c.dwell_min, c.dwell_max) == (60, 120)

    _apply_dwell(cands, c)
    values = [x.expected_dwell_min for x in cands]
    assert all(60 <= v <= 120 for v in values), values
    assert values[0] > values[1]          # 대소 관계는 그대로


def test_single_dwell_is_fixed():
    from app.graph.router import _rule_conditions
    from app.graph.subgraphs.itinerary import _apply_dwell
    from app.schemas import Candidate

    cands = [Candidate(id=str(i), name=f"p{i}", expected_dwell_min=d)
             for i, d in enumerate((150, 40, 45))]
    c = _rule_conditions("서대문구 전시 추천, 한 곳에 90분 정도씩")
    _apply_dwell(cands, c)
    assert all(x.expected_dwell_min == 90 for x in cands)


def test_dwell_is_stripped_before_time_parsing():
    """'1시간~2시간'의 '1시'가 출발 시각으로 새어 들어가면 안 된다."""
    from app.graph.router import _rule_conditions

    c = _rule_conditions("강남 문화생활 추천, 장소마다 1시간~2시간 머물꺼야")
    assert c.start_time is None
    assert c.stops == []
