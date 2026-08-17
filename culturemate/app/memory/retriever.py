"""개인 아카이브 검색기 — 이 프로젝트의 핵심 차별점.

기존 RAG와 다른 점 3가지
  1) Facet 분해: 하나의 질의를 '유사 장소 / 상황 일치 / 불편·수정행동' 세 축으로
     쪼개 각각 독립 검색한 뒤 융합한다(LangGraph Send로 병렬 실행).
  2) 하이브리드 + RRF: dense(pgvector HNSW) 와 lexical(tsvector) 랭킹을
     Reciprocal Rank Fusion으로 합치고, 있으면 cross-encoder로 리랭크한다.
  3) 개인화 사후보정: 최신성 감쇠 × 불편 가중 × 상황 일치도.
     '경고 재현율'이 정확도보다 중요하므로 friction 기록은 의도적으로 부스트한다.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.db.session import acquire
from app.llm.provider import get_embeddings, get_reranker
from app.schemas import ArchiveHit, TripConditions

logger = logging.getLogger(__name__)

FACETS = ("similar_place", "context_match", "friction_edit")

_DENSE_SQL = """
SELECT e.id::text, e.source_type, e.source_id::text, e.place_id::text,
       p.name AS place_name, e.summary, e.tags, e.friction, e.sentiment,
       e.rating, e.occurred_at, e.meta,
       1 - (e.embedding <=> %(qvec)s::vector) AS score
FROM experience_embeddings e
LEFT JOIN places p ON p.id = e.place_id
WHERE e.user_id = %(user_id)s
  AND (%(source_types)s::text[] IS NULL OR e.source_type = ANY(%(source_types)s))
  AND (%(region)s::text IS NULL OR e.meta->>'region' = %(region)s)
  AND (%(place_ids)s::uuid[] IS NULL OR e.place_id = ANY(%(place_ids)s))
ORDER BY e.embedding <=> %(qvec)s::vector
LIMIT %(k)s
"""

_LEXICAL_SQL = """
SELECT e.id::text, e.source_type, e.source_id::text, e.place_id::text,
       p.name AS place_name, e.summary, e.tags, e.friction, e.sentiment,
       e.rating, e.occurred_at, e.meta,
       ts_rank_cd(e.ts, plainto_tsquery('simple', %(q)s)) AS score
FROM experience_embeddings e
LEFT JOIN places p ON p.id = e.place_id
WHERE e.user_id = %(user_id)s
  AND e.ts @@ plainto_tsquery('simple', %(q)s)
  AND (%(source_types)s::text[] IS NULL OR e.source_type = ANY(%(source_types)s))
ORDER BY score DESC
LIMIT %(k)s
"""

_FACET_SOURCE_TYPES: dict[str, list[str] | None] = {
    "similar_place": ["visit", "review", "note"],
    "context_match": ["visit", "review", "plan_edit"],
    "friction_edit": ["visit", "review", "plan_edit"],
}


async def facet_search(
    *,
    user_id: str,
    facet: str,
    query: str,
    conditions: TripConditions | None = None,
    place_ids: list[str] | None = None,
) -> list[ArchiveHit]:
    """단일 facet 검색: dense + lexical → RRF → 개인화 보정."""
    s = get_settings()
    k = s.archive_top_k
    source_types = _FACET_SOURCE_TYPES.get(facet)

    dense_rows: list[dict[str, Any]] = []
    lex_rows: list[dict[str, Any]] = []
    try:
        # 임베딩 호출도 이 가드 안에 있어야 한다. 바깥에 두었더니 NIM 이 502 를 낸
        # 순간 예외가 archive 노드를 뚫고 나가 요청 전체가 죽었다. archive 는
        # 개인화를 얹는 선택적 단계라, 실패하면 조용히 비우고 일정은 나가야 한다.
        qvec = await get_embeddings().aembed_query(query)

        async with acquire() as conn, conn.cursor() as cur:
            await cur.execute(_DENSE_SQL, {
                "qvec": qvec, "user_id": user_id, "k": k,
                "source_types": source_types,
                "region": conditions.region if conditions else None,
                "place_ids": place_ids,
            })
            dense_rows = await _rows_as_dicts(cur)
            await cur.execute(_LEXICAL_SQL, {
                "q": query, "user_id": user_id, "k": k,
                "source_types": source_types,
            })
            lex_rows = await _rows_as_dicts(cur)
    except Exception as exc:  # DB 미구성·임베딩 장애에도 그래프는 계속 돌아야 한다
        logger.warning("archive facet_search degraded (facet=%s): %s", facet, exc)
        return []

    fused = rrf_fuse(dense_rows, lex_rows, k=s.rrf_k)
    hits = [_to_hit(row, facet) for row in fused]
    return personalize(hits, conditions)


def rrf_fuse(
    dense: Iterable[dict], lexical: Iterable[dict], k: int = 60
) -> list[dict]:
    """Reciprocal Rank Fusion.  score = Σ w_i / (k + rank_i)"""
    s = get_settings()
    table: dict[str, dict] = {}
    for weight, rows, rank_field in (
        (s.dense_weight, list(dense), "dense_rank"),
        (s.lexical_weight, list(lexical), "lexical_rank"),
    ):
        for rank, row in enumerate(rows, start=1):
            rid = row["id"]
            entry = table.setdefault(rid, {**row, "fused_score": 0.0})
            entry["fused_score"] += weight / (k + rank)
            entry[rank_field] = rank
    return sorted(table.values(), key=lambda r: r["fused_score"], reverse=True)


def personalize(
    hits: list[ArchiveHit], conditions: TripConditions | None
) -> list[ArchiveHit]:
    """최신성 감쇠 · 불편 가중 · 상황(동행자/이동수단) 일치 보정."""
    s = get_settings()
    now = datetime.now(timezone.utc)
    for h in hits:
        decay = 1.0
        if h.occurred_at:
            occurred = h.occurred_at
            if occurred.tzinfo is None:
                occurred = occurred.replace(tzinfo=timezone.utc)
            age_days = max((now - occurred).days, 0)
            decay = 0.5 ** (age_days / s.recency_half_life_days)
        friction_boost = 1.0 + s.friction_boost * min(len(h.friction), 3)
        ctx = _context_match(h, conditions)
        base = h.rerank_score if h.rerank_score is not None else h.fused_score
        h.final_score = base * (0.4 + 0.6 * decay) * friction_boost * ctx
    return sorted(hits, key=lambda h: h.final_score, reverse=True)


def _context_match(h: ArchiveHit, c: TripConditions | None) -> float:
    if c is None:
        return 1.0
    score = 1.0
    meta = h.meta or {}
    if c.companions != "unknown" and meta.get("companions") == c.companions:
        score += 0.15
    if c.transport != "unknown" and meta.get("transport") == c.transport:
        score += 0.15
    if c.region and meta.get("region") == c.region:
        score += 0.10
    if c.date and meta.get("season") == _season(c.date.month):
        score += 0.05
    return score


def _season(month: int) -> str:
    return {12: "winter", 1: "winter", 2: "winter"}.get(
        month, "spring" if month <= 5 else "summer" if month <= 8 else "autumn"
    )


async def rerank(query: str, hits: list[ArchiveHit]) -> list[ArchiveHit]:
    """cross-encoder 리랭크. 리랭커가 없으면 RRF 순서를 유지한다."""
    reranker = get_reranker()
    if not reranker or not hits:
        return hits
    from langchain_core.documents import Document

    docs = [Document(page_content=h.summary, metadata={"hid": h.id}) for h in hits]
    try:
        ranked = await reranker.acompress_documents(docs, query)
    except Exception as exc:
        # 조용히 삼키면 리랭크가 꺼진 걸 아무도 모른다. 실제로 모델이 404 가 된 뒤로도
        # 로그 한 줄 없이 RRF 순서만 나가고 있었다. 폴백은 유지하되 이유는 남긴다.
        logger.warning("리랭크 실패 — RRF 순서를 유지합니다 (모델=%s): %s",
                       get_settings().model_rerank, exc)
        return hits
    by_id = {h.id: h for h in hits}
    out: list[ArchiveHit] = []
    for rank, d in enumerate(ranked, start=1):
        h = by_id.get(d.metadata.get("hid"))
        if not h:
            continue
        h.rerank_score = float(d.metadata.get("relevance_score", 1.0 / rank))
        out.append(h)
    seen = {h.id for h in out}
    out.extend(h for h in hits if h.id not in seen)
    return out


def fuse_facets(groups: list[list[ArchiveHit]], final_k: int) -> list[ArchiveHit]:
    """facet별 결과를 다시 RRF로 합친다. 여러 facet에 동시에 잡힌 기록이 강한 신호."""
    s = get_settings()
    table: dict[str, ArchiveHit] = {}
    scores: dict[str, float] = {}
    for group in groups:
        for rank, h in enumerate(sorted(group, key=lambda x: x.final_score, reverse=True), 1):
            table.setdefault(h.id, h)
            scores[h.id] = scores.get(h.id, 0.0) + 1.0 / (s.rrf_k + rank)
    for hid, sc in scores.items():
        table[hid].final_score = sc
    return sorted(table.values(), key=lambda h: h.final_score, reverse=True)[:final_k]


async def _rows_as_dicts(cur) -> list[dict[str, Any]]:
    cols = [c.name for c in cur.description] if cur.description else []
    return [dict(zip(cols, r, strict=False)) for r in await cur.fetchall()]


def _to_hit(row: dict[str, Any], facet: str) -> ArchiveHit:
    return ArchiveHit(
        id=row["id"],
        source_type=row["source_type"],
        source_id=row["source_id"],
        place_id=row.get("place_id"),
        place_name=row.get("place_name"),
        summary=row.get("summary") or "",
        tags=list(row.get("tags") or []),
        friction=list(row.get("friction") or []),
        sentiment=float(row.get("sentiment") or 0.0),
        rating=row.get("rating"),
        occurred_at=row.get("occurred_at"),
        facet=facet,
        dense_rank=row.get("dense_rank"),
        lexical_rank=row.get("lexical_rank"),
        fused_score=float(row.get("fused_score") or 0.0),
        meta=dict(row.get("meta") or {}),
    )
