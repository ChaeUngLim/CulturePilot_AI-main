"""아카이브 기반 큐레이션 지도.

남이 만든 테마 지도를 보여주는 게 아니라, **내 기록에서 테마를 뽑아낸다.**
'서울 데이트 코스' 같은 일반 큐레이션은 검색으로도 나오지만,
'내가 주차 걱정 없이 다녀온 미술관'은 이 서비스만 만들 수 있다.

각 컬렉션은 SQL 하나로 만든다. LLM에게 "내 취향 컬렉션 만들어줘"라고 하면
그럴듯하지만 근거 없는 묶음이 나온다. 규칙이 명시적이어야 사용자가
'왜 여기 들어왔는지' 물었을 때 답할 수 있다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.db.session import acquire

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Recipe:
    key: str
    title: str
    subtitle: str
    emoji: str
    sql: str
    min_places: int = 2


# 방문 기록을 장소 단위로 접은 공통 뷰. 컬렉션마다 이걸 필터링한다.
_BASE = """
WITH agg AS (
    SELECT p.id, p.name, p.category, p.address, p.region, p.lat, p.lng, p.indoor,
           p.official_url, p.parking, p.parking_note,
           COUNT(*)                          AS visits,
           AVG(v.rating)                     AS rating,
           MAX(v.visited_at)                 AS last_visit,
           BOOL_OR(v.is_revisit)             AS revisited,
           ARRAY_AGG(DISTINCT f) FILTER (WHERE f IS NOT NULL) AS frictions,
           ARRAY_AGG(DISTINCT v.companions)  AS companions,
           ARRAY_AGG(DISTINCT v.transport)   AS transports,
           AVG(v.dwell_min)                  AS dwell
    FROM visits v
    JOIN places p ON p.id = v.place_id
    LEFT JOIN LATERAL unnest(v.friction) AS f ON TRUE
    WHERE v.user_id = %(user_id)s
    GROUP BY p.id
)
"""

RECIPES: tuple[Recipe, ...] = (
    Recipe(
        "favorites", "다시 가고 싶은 곳", "별점 4.5 이상 · 만족도가 높았던 장소", "⭐",
        _BASE + "SELECT * FROM agg WHERE rating >= 4.5 ORDER BY rating DESC, visits DESC",
    ),
    Recipe(
        "repeat", "여러 번 간 곳", "두 번 이상 방문 · 내 단골", "🔁",
        _BASE + "SELECT * FROM agg WHERE visits >= 2 OR revisited ORDER BY visits DESC",
    ),
    Recipe(
        "solo", "혼자 가기 좋았던 곳", "혼자 방문해 만족했던 장소", "🚶",
        _BASE + """SELECT * FROM agg
                   WHERE 'solo' = ANY(companions) AND rating >= 4.0
                   ORDER BY rating DESC""",
    ),
    Recipe(
        "no_parking_worry", "주차 걱정 없던 곳", "차로 갔는데 주차 불편이 없었던 장소", "🚗",
        _BASE + """SELECT * FROM agg
                   WHERE 'car' = ANY(transports)
                     AND NOT ('parking' = ANY(COALESCE(frictions, '{}')))
                   ORDER BY rating DESC NULLS LAST""",
    ),
    Recipe(
        "rainy_day", "비 오는 날 갈 곳", "실내 · 만족도가 높았던 장소", "☔",
        _BASE + """SELECT * FROM agg
                   WHERE indoor IS TRUE AND COALESCE(rating, 0) >= 4.0
                   ORDER BY rating DESC""",
    ),
    Recipe(
        "long_stay", "오래 머문 곳", "평균 90분 이상 · 시간을 들일 만했던 장소", "🕰️",
        _BASE + "SELECT * FROM agg WHERE dwell >= 90 ORDER BY dwell DESC",
    ),
    Recipe(
        "quiet", "한산했던 곳", "혼잡·대기 불편 기록이 없는 장소", "🤫",
        _BASE + """SELECT * FROM agg
                   WHERE NOT (COALESCE(frictions, '{}') && ARRAY['crowding','waiting'])
                     AND COALESCE(rating, 0) >= 4.0
                   ORDER BY rating DESC""",
    ),
    Recipe(
        "caution", "다시 갈 땐 확인할 곳", "주차·혼잡·접근성 불편을 기록한 장소", "⚠️",
        _BASE + """SELECT * FROM agg
                   WHERE COALESCE(array_length(frictions, 1), 0) > 0
                   ORDER BY array_length(frictions, 1) DESC, last_visit DESC""",
        min_places=1,
    ),
)


async def build_collections(user_id: str, limit_per: int = 12) -> list[dict[str, Any]]:
    """내가 담은 컬렉션 + 규칙으로 뽑은 테마.

    순서가 중요하다. 직접 담은 것을 맨 앞에 둔다 — 자동 테마는 기록이 바뀌면
    사라지지만 내가 담은 건 의도적으로 남긴 것이고, 찾을 때도 그쪽을 먼저 본다.
    """
    out: list[dict[str, Any]] = list(await user_collections(user_id, limit_per))
    try:
        async with acquire() as conn:
            for recipe in RECIPES:
                places = await _run(conn, recipe, user_id, limit_per)
                if len(places) >= recipe.min_places:
                    out.append({
                        "key": recipe.key, "title": recipe.title,
                        "subtitle": recipe.subtitle, "emoji": recipe.emoji,
                        "count": len(places), "places": places,
                    })
    except Exception as exc:
        logger.warning("큐레이션 생성 실패: %s", exc)
        return []

    out.extend(await _region_collections(user_id, limit_per))
    return out


async def _run(conn, recipe: Recipe, user_id: str, limit: int) -> list[dict[str, Any]]:
    async with conn.cursor() as cur:
        await cur.execute(f"{recipe.sql} LIMIT {int(limit)}", {"user_id": user_id})
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, r, strict=False)) for r in await cur.fetchall()]
    return [_to_place(r) for r in rows if r.get("lat") and r.get("lng")]


async def _region_collections(user_id: str, limit: int) -> list[dict[str, Any]]:
    """자주 간 지역별 묶음. '내 생활권'을 스스로 드러내는 게 목적이다."""
    sql = _BASE + """
        SELECT * FROM agg
        WHERE region = %(region)s
        ORDER BY rating DESC NULLS LAST, visits DESC
    """
    out: list[dict[str, Any]] = []
    try:
        async with acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT p.region, COUNT(*) AS n
                    FROM visits v JOIN places p ON p.id = v.place_id
                    WHERE v.user_id = %(user_id)s AND p.region IS NOT NULL
                    GROUP BY p.region HAVING COUNT(*) >= 2
                    ORDER BY n DESC LIMIT 4
                """, {"user_id": user_id})
                regions = [r[0] for r in await cur.fetchall()]

            for region in regions:
                async with conn.cursor() as cur:
                    await cur.execute(f"{sql} LIMIT {int(limit)}",
                                      {"user_id": user_id, "region": region})
                    cols = [c.name for c in cur.description]
                    rows = [dict(zip(cols, r, strict=False))
                            for r in await cur.fetchall()]
                places = [_to_place(r) for r in rows if r.get("lat") and r.get("lng")]
                if len(places) >= 2:
                    out.append({
                        "key": f"region:{region}", "title": f"{region}에서 간 곳",
                        "subtitle": f"{len(places)}곳 · 내 생활권", "emoji": "📍",
                        "count": len(places), "places": places,
                    })
    except Exception as exc:
        logger.warning("지역 큐레이션 실패: %s", exc)
    return out


# ---------------------------------------------------- 내가 담은 컬렉션
_MINE = """
SELECT c.id::text AS collection_id, c.title, c.emoji, c.subtitle,
       p.id, p.name, p.category, p.address, p.region, p.lat, p.lng, p.indoor,
       p.official_url, p.parking, p.parking_note, p.dwell_min,
       ucp.note AS pick_note, ucp.added_at,
       (SELECT COUNT(*) FROM visits v
         WHERE v.user_id = c.user_id AND v.place_id = p.id) AS visits,
       (SELECT AVG(v.rating) FROM visits v
         WHERE v.user_id = c.user_id AND v.place_id = p.id) AS rating
  FROM user_collections c
  JOIN user_collection_places ucp ON ucp.collection_id = c.id
  JOIN places p ON p.id = ucp.place_id
 WHERE c.user_id = %(user_id)s
 ORDER BY c.updated_at DESC, ucp.added_at
"""


async def user_collections(user_id: str, limit_per: int = 12) -> list[dict[str, Any]]:
    """사용자가 직접 담은 컬렉션. 비어 있으면 빈 목록."""
    try:
        async with acquire() as conn, conn.cursor() as cur:
            await cur.execute(_MINE, {"user_id": user_id})
            cols = [c.name for c in cur.description]
            rows = [dict(zip(cols, r, strict=False)) for r in await cur.fetchall()]
    except Exception as exc:
        logger.warning("내 컬렉션 조회 실패: %s", exc)
        return []

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not (row.get("lat") and row.get("lng")):
            continue
        key = row["collection_id"]
        bucket = grouped.setdefault(key, {
            "key": f"mine:{key}", "title": row["title"], "emoji": row["emoji"] or "⭐",
            "subtitle": row.get("subtitle") or "내가 담은 곳",
            "mine": True, "collection_id": key, "count": 0, "places": [],
        })
        if len(bucket["places"]) >= limit_per:
            continue
        place = _to_place(row)
        place["reason"] = row.get("pick_note") or place["reason"]
        bucket["places"].append(place)
    for bucket in grouped.values():
        bucket["count"] = len(bucket["places"])
        bucket["subtitle"] = f"{bucket['count']}곳 · {bucket['subtitle']}"
    return list(grouped.values())


async def save_collection(user_id: str, title: str, place_ids: list[str], *,
                          emoji: str = "⭐", subtitle: str | None = None,
                          note: str | None = None) -> dict[str, Any]:
    """일정의 장소들을 컬렉션에 담는다. 같은 이름이 있으면 거기에 더한다.

    같은 장소를 다시 담아도 오류가 아니다 — 두 번 갔다는 뜻이지 실수가 아니므로
    조용히 무시하고 담은 시각만 남긴다.
    """
    async with acquire() as conn, conn.cursor() as cur:
        await cur.execute("""
            INSERT INTO user_collections (user_id, title, emoji, subtitle)
            VALUES (%(user_id)s, %(title)s, %(emoji)s, %(subtitle)s)
            ON CONFLICT (user_id, title) DO UPDATE
               SET emoji = EXCLUDED.emoji,
                   subtitle = COALESCE(EXCLUDED.subtitle, user_collections.subtitle),
                   updated_at = now()
            RETURNING id::text
        """, {"user_id": user_id, "title": title, "emoji": emoji,
              "subtitle": subtitle})
        collection_id = (await cur.fetchone())[0]

        added = 0
        for place_id in place_ids:
            if not place_id:
                continue
            await cur.execute("""
                INSERT INTO user_collection_places (collection_id, place_id, note)
                VALUES (%s, %s, %s)
                ON CONFLICT (collection_id, place_id) DO NOTHING
            """, (collection_id, place_id, note))
            added += cur.rowcount or 0
        await conn.commit()

    logger.info("컬렉션 '%s'에 %d곳 추가", title, added)
    return {"collection_id": collection_id, "title": title, "added": added}


async def delete_collection(user_id: str, collection_id: str) -> bool:
    async with acquire() as conn, conn.cursor() as cur:
        await cur.execute(
            "DELETE FROM user_collections WHERE id = %s AND user_id = %s",
            (collection_id, user_id))
        deleted = (cur.rowcount or 0) > 0
        await conn.commit()
    return deleted


async def remove_place(user_id: str, collection_id: str, place_id: str) -> bool:
    async with acquire() as conn, conn.cursor() as cur:
        await cur.execute("""
            DELETE FROM user_collection_places ucp
             USING user_collections c
             WHERE ucp.collection_id = c.id AND c.user_id = %s
               AND c.id = %s AND ucp.place_id = %s
        """, (user_id, collection_id, place_id))
        removed = (cur.rowcount or 0) > 0
        await conn.commit()
    return removed


def _to_place(row: dict[str, Any]) -> dict[str, Any]:
    frictions = list(row.get("frictions") or [])
    return {
        "place_id": str(row["id"]),
        "name": row["name"],
        "category": row.get("category"),
        "address": row.get("address"),
        "region": row.get("region"),
        "lat": float(row["lat"]),
        "lng": float(row["lng"]),
        "indoor": row.get("indoor"),
        # 일정 화면과 같은 사실을 같은 이름으로 내보낸다. 화면마다 다른 필드를
        # 만들면 표시가 갈리고, 사용자는 어느 쪽이 맞는지 알 수 없게 된다.
        "parking": row.get("parking") or "unknown",
        "parking_note": row.get("parking_note"),
        "url": row.get("official_url"),
        "visits": int(row.get("visits") or 0),
        "rating": round(float(row["rating"]), 1) if row.get("rating") else None,
        "dwell_min": (int(row["dwell"]) if row.get("dwell")
                      else int(row["dwell_min"]) if row.get("dwell_min") else None),
        "friction": frictions,
        "last_visit": row["last_visit"].isoformat() if row.get("last_visit") else None,
        # 왜 이 컬렉션에 들어왔는지 — 사용자가 물었을 때 답할 수 있어야 한다
        "reason": _reason(row, frictions),
    }


def _reason(row: dict[str, Any], frictions: list[str]) -> str:
    bits: list[str] = []
    if row.get("rating"):
        bits.append(f"별점 {float(row['rating']):.1f}")
    if (row.get("visits") or 0) >= 2:
        bits.append(f"{int(row['visits'])}번 방문")
    if row.get("dwell"):
        bits.append(f"평균 {int(row['dwell'])}분 체류")
    if frictions:
        labels = {"parking": "주차", "crowding": "혼잡", "waiting": "대기",
                  "accessibility": "접근성", "weather": "날씨", "transit": "교통",
                  "reservation": "예약", "noise": "소음", "cost": "비용"}
        bits.append("불편: " + "·".join(labels.get(f, f) for f in frictions))
    return " · ".join(bits) or "방문 기록 있음"
