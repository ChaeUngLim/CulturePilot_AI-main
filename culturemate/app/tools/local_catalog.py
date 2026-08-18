"""장소 카탈로그 조회.

`places` 테이블을 탐색 소스로 쓴다. 외부 API와 역할이 다르다.

  공공 문화 API — 기간형 행사. 최신이지만 상시 공간을 거의 담지 못한다.
  네이버 지역검색 — 상호는 많지만 문화공간인지 구분이 안 되고 호출량 제한이 있다.
  웹검색 — 좌표도 운영시간도 없다.
  **카탈로그** — 좌표·카테고리·체류시간이 정리돼 있고 즉시 응답한다.

그래서 카탈로그를 1차 후보로 깔고, 외부 소스로 보강하는 구조가 안정적이다.
응답 시간 예산(15초) 안에서 결과를 보장하는 축이기도 하다.
"""
from __future__ import annotations

import logging
from typing import Any

from app.db.session import acquire
from app.schemas import Candidate, GeoPoint, TripConditions

logger = logging.getLogger(__name__)

_BY_REGION = """
SELECT id::text AS place_id, external_key, name, kind, category, address,
       region, lat, lng, indoor, official_url, dwell_min, parking, parking_note
FROM places
WHERE region = ANY(%(regions)s)
  AND lat IS NOT NULL AND lng IS NOT NULL
ORDER BY random()
LIMIT %(limit)s
"""

# 반경 검색. 위경도 1도 ≈ 111km 로 근사해 경계 상자를 만든 뒤 거리로 정렬한다.
_BY_POINT = """
SELECT id::text AS place_id, external_key, name, kind, category, address,
       region, lat, lng, indoor, official_url, dwell_min, parking, parking_note,
       (6371000 * acos(LEAST(1, cos(radians(%(lat)s)) * cos(radians(lat))
        * cos(radians(lng) - radians(%(lng)s)) + sin(radians(%(lat)s))
        * sin(radians(lat))))) AS distance_m
FROM places
WHERE lat BETWEEN %(lat)s - %(dlat)s AND %(lat)s + %(dlat)s
  AND lng BETWEEN %(lng)s - %(dlng)s AND %(lng)s + %(dlng)s
ORDER BY distance_m
LIMIT %(limit)s
"""


async def search(c: TripConditions, limit: int = 20) -> list[Candidate]:
    """조건에 맞는 카탈로그 장소. 지점이 있으면 반경, 없으면 지역 기준."""
    try:
        if c.landmark and c.origin:
            # 지점 기준 — 도보권만. 지역으로 넓히면 걸어갈 수 없는 곳이 섞인다.
            rows = await _by_point(c.origin, c.radius_m or 2000, limit)
        else:
            rows = []
            names = [n for n in (c.regions or ([c.region] if c.region else [])) if n]
            if names:
                rows += await _by_region(names, limit)
                if not rows:
                    rows += await _by_region_prefix(names[0], limit)  # '서울' → 전체
            # 지역을 골랐어도 현재 위치 주변은 함께 본다(범위 합산).
            if c.origin:
                rows += await _by_point(c.origin, c.radius_m or 3000, limit)
            if not rows:
                return []
    except Exception as exc:
        logger.warning("카탈로그 조회 실패: %s", exc)
        return []

    # 여러 소스를 합쳤으므로 중복 제거
    seen: set[str] = set()
    unique = []
    for r in rows:
        key = r["external_key"]
        if key not in seen:
            seen.add(key)
            unique.append(r)
    rows = unique[: limit * 2]

    out = [_to_candidate(r) for r in rows]
    logger.info("카탈로그 %d곳", len(out))
    return out


async def _by_region(regions: list[str], limit: int) -> list[dict[str, Any]]:
    async with acquire() as conn, conn.cursor() as cur:
        await cur.execute(_BY_REGION, {"regions": regions, "limit": limit})
        return await _rows(cur)


async def _by_region_prefix(region: str, limit: int) -> list[dict[str, Any]]:
    """'서울' 처럼 넓게 말한 경우 — 구 단위 레코드를 접두사로 찾는다."""
    async with acquire() as conn, conn.cursor() as cur:
        await cur.execute(
            _BY_REGION.replace("region = ANY(%(regions)s)",
                               "region LIKE %(prefix)s"),
            {"prefix": f"{region}%", "limit": limit})
        return await _rows(cur)


async def _by_point(origin: GeoPoint, radius_m: int, limit: int) -> list[dict[str, Any]]:
    d = radius_m / 111_000
    async with acquire() as conn, conn.cursor() as cur:
        await cur.execute(_BY_POINT, {
            "lat": origin.lat, "lng": origin.lng,
            "dlat": d, "dlng": d / max(0.3, abs(__import__("math").cos(
                __import__("math").radians(origin.lat)))),
            "limit": limit,
        })
        rows = await _rows(cur)
    return [r for r in rows if (r.get("distance_m") or 0) <= radius_m * 1.2]


async def _rows(cur) -> list[dict[str, Any]]:
    cols = [c.name for c in cur.description] if cur.description else []
    return [dict(zip(cols, r, strict=False)) for r in await cur.fetchall()]


def _to_candidate(row: dict[str, Any]) -> Candidate:
    return Candidate(
        # places.id(uuid) 를 쓴다. external_key 를 넣었더니 visits·plan_edits 가
        # 참조하는 uuid 와 식별자 공간이 달라, 과거 불편 기록이 이번 일정의 같은
        # 장소 식별이 어긋난다.
        place_id=row["place_id"],
        source="catalog",
        kind=row.get("kind") or "venue",
        name=row["name"],
        category=row.get("category"),
        address=row.get("address"),
        geo=GeoPoint(lat=float(row["lat"]), lng=float(row["lng"]), name=row["name"]),
        official_url=row.get("official_url"),
        indoor=row.get("indoor"),
        expected_dwell_min=int(row.get("dwell_min") or 60),
        parking=row.get("parking") or "unknown",
        parking_note=row.get("parking_note"),
        # 큐레이션된 데이터라 웹 후보보다 기본 신뢰도가 높다
        relevance=0.7,
        verify_status="verified",
        tags=[t for t in (row.get("category"), row.get("region")) if t],
        raw={"catalog": True, "distance_m": row.get("distance_m")},
    )


# 이름이 비슷하고 이만큼 안쪽이면 같은 장소로 본다. 좌표만으로는 한 건물의
# 서로 다른 시설이 뭉치고, 이름만으로는 체인점('스타벅스')이 전부 붙는다.
_LINK_RADIUS_DEG = 0.005          # 위도 0.005° ≈ 555m
_LINK_NAME_SIMILARITY = 0.35

_LINK_SQL = """
SELECT q.idx - 1 AS pos, p.id::text AS place_id
FROM unnest(%(names)s::text[], %(lats)s::float8[], %(lngs)s::float8[])
     WITH ORDINALITY AS q(qname, qlat, qlng, idx)
CROSS JOIN LATERAL (
    SELECT id
    FROM places
    WHERE lat BETWEEN q.qlat - %(d)s AND q.qlat + %(d)s
      AND lng BETWEEN q.qlng - %(d)s AND q.qlng + %(d)s
      AND similarity(name, q.qname) > %(sim)s
    ORDER BY similarity(name, q.qname) DESC
    LIMIT 1
) p
"""


async def link_place_ids(cands: list[Candidate]) -> int:
    """외부 소스 후보를 카탈로그의 places 행에 연결한다.

    공공 문화 API·네이버·웹에서 온 후보는 place_id 가 없다. 그러면 과거 방문
    기록(visits)·수정 행동(plan_edits)이 이번 일정의 같은 장소에 붙지 못해,
    '예술의전당 주차가 불편했다'는 기록을 갖고도 경고가 뜨지 않는다.
    이 서비스의 전제(과거 경험이 다음 일정에 개입한다)가 거기서 끊긴다.

    좌표와 이름을 함께 본다 — 둘 중 하나만으로는 오연결이 난다.
    실패해도 조용히 넘어간다. 연결이 안 되면 경고가 없을 뿐, 일정은 성립한다.
    """
    targets = [c for c in cands if not c.place_id and c.geo and c.name]
    if not targets:
        return 0
    try:
        async with acquire() as conn, conn.cursor() as cur:
            await cur.execute(_LINK_SQL, {
                "names": [c.name for c in targets],
                "lats": [c.geo.lat for c in targets],
                "lngs": [c.geo.lng for c in targets],
                "d": _LINK_RADIUS_DEG,
                "sim": _LINK_NAME_SIMILARITY,
            })
            rows = await cur.fetchall()
    except Exception as exc:
        logger.warning("카탈로그 연결 실패(무시): %s", exc)
        return 0

    linked = 0
    for row in rows:
        pos, place_id = (row["pos"], row["place_id"]) if isinstance(row, dict) else row
        if 0 <= pos < len(targets) and place_id:
            targets[pos].place_id = place_id
            linked += 1
    return linked
