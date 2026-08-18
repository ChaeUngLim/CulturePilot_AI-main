"""도메인 저장소. SQL은 전부 여기 모으고 그래프 노드는 함수만 호출한다."""
from __future__ import annotations

import json
import logging
import re
from datetime import date as Date
from datetime import datetime
from typing import Any

from app.db.session import acquire
from app.schemas import Decision, EditSignal, Itinerary, PreferenceCard

logger = logging.getLogger(__name__)


async def load_active_itinerary(user_id: str, day: Date | None) -> Itinerary | None:
    sql = """
    SELECT payload FROM plans
    WHERE user_id = %(user_id)s AND status = 'active'
      AND (%(day)s::date IS NULL OR plan_date = %(day)s)
    ORDER BY updated_at DESC LIMIT 1
    """
    async with acquire() as conn, conn.cursor() as cur:
        await cur.execute(sql, {"user_id": user_id, "day": day})
        row = await cur.fetchone()
    return Itinerary(**row[0]) if row and row[0] else None


async def save_itinerary(user_id: str, itinerary: Itinerary) -> None:
    sql = """
    INSERT INTO plans (id, user_id, plan_date, version, status, payload, updated_at)
    VALUES (%(id)s, %(user_id)s, %(day)s, %(version)s, 'active', %(payload)s, now())
    ON CONFLICT (id) DO UPDATE
    SET version = EXCLUDED.version, payload = EXCLUDED.payload, updated_at = now()
    """
    async with acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, {
                "id": itinerary.id, "user_id": user_id, "day": itinerary.date,
                "version": itinerary.version,
                "payload": jsonb(itinerary.model_dump(mode="json")),
            })
        await conn.commit()


async def list_plans(user_id: str, frm: Date, to: Date) -> list[dict[str, Any]]:
    """기간 안의 확정 일정을 **요약만** 돌려준다 (UR-28 캘린더).

    ★ `payload` 전문을 싣지 않는다. `Itinerary` 하나가 수십 KB라 한 달치를 그대로
    내리면 모바일 페이로드 제약(NFR-09)이 깨진다. 캘린더가 필요한 건 «그날 뭔가
    있었나»와 «대표 이름» 뿐이고, 상세는 날짜를 눌렀을 때 `load_plan()` 이 준다.

    jsonb 연산으로 서버에서 요약을 만든다 — 파이썬으로 끌어와 세면 결국 전문을
    읽게 되어 아끼려던 것을 그대로 쓴다.
    """
    sql = """
    SELECT id,
           plan_date,
           status,
           jsonb_array_length(payload->'items')            AS stop_count,
           payload->'items'->0->>'name'                    AS first_place,
           payload->>'destination_name'                    AS destination_name,
           payload->'items'->0->>'arrive'                  AS starts_at,
           updated_at
    FROM plans
    WHERE user_id = %(user_id)s
      AND plan_date BETWEEN %(frm)s AND %(to)s
    ORDER BY plan_date DESC, updated_at DESC
    """
    async with acquire() as conn, conn.cursor() as cur:
        await cur.execute(sql, {"user_id": user_id, "frm": frm, "to": to})
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, r, strict=False)) for r in await cur.fetchall()]
    for row in rows:
        row["plan_date"] = row["plan_date"].isoformat() if row["plan_date"] else None
        row["updated_at"] = row["updated_at"].isoformat() if row["updated_at"] else None
    return rows


async def load_plan(plan_id: str) -> Itinerary | None:
    """캘린더에서 날짜를 눌렀을 때 펼칠 그날 일정 전체 (UR-28)."""
    async with acquire() as conn, conn.cursor() as cur:
        await cur.execute("SELECT payload FROM plans WHERE id = %(id)s",
                          {"id": plan_id})
        row = await cur.fetchone()
    return Itinerary(**row[0]) if row and row[0] else None


# `places.id` 는 uuid 다. 후보가 외부 API에서 곧장 온 경우 `external_key` 같은 다른
# 식별자를 들고 있어, 그대로 넣으면 문법 오류로 INSERT 자체가 실패한다.
UUID_RE = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")


def _place_ref(value: str | None) -> str | None:
    """`places.id` 자리에 넣어도 되는 값만 통과시킨다. 아니면 None."""
    return value if value and UUID_RE.match(value) else None


# FK 를 서브셀렉트로 푸는 이유 — uuid 모양이어도 `places`·`plans` 에 아직 없는 식별자가
# 있다. 그대로 넣으면 FK 위반으로 **한 건 때문에 나머지까지** 안 들어간다.
# 없는 참조는 NULL 로 떨어뜨리고, 원본 문자열은 detail 에 남겨 나중에 이어 붙인다.
_INSERT_EDIT_SQL = """
INSERT INTO plan_edits (user_id, plan_id, action, from_place_id, to_place_id, detail, created_at)
SELECT %(user_id)s,
       (SELECT id FROM plans  WHERE id = %(plan_id)s),
       %(action)s,
       (SELECT id FROM places WHERE id = %(from_place_id)s::uuid),
       (SELECT id FROM places WHERE id = %(to_place_id)s::uuid),
       %(detail)s,
       COALESCE(%(created_at)s::timestamptz, now())
WHERE NOT EXISTS (
    SELECT 1 FROM plan_edits
    WHERE user_id = %(user_id)s AND detail->>'signal_id' = %(signal_id)s)
"""


async def save_plan_edits(user_id: str, plan_id: str | None,
                          signals: list[EditSignal]) -> int:
    """일정 수정 행동을 원자 단위로 남긴다 (UR-09). 저장한 건수를 돌려준다.

    ★ 프로필에 반영하는 것만으로는 부족하다 — `apply_edit_signals()` 는 프로필의
    숫자만 흔들고 끝나는데, 재집계(`rebuild_profile`)가 한 번 돌면 방문 기록만 보고
    프로필을 새로 만들기 때문에 그 흔적이 사라진다. 명세서 §0이 개인화의 근거로 내세운
    «방문 기록 + 일정 수정 행동» 의 뒤쪽 절반이 그렇게 증발하고 있었다.

    행 하나가 신호 하나다. 합쳐서 넣으면 "몇 번 지웠나"를 나중에 셀 수 없다.

    같은 신호 id 는 다시 넣지 않는다. 확정 카드에서 나온 신호는 스레드 상태에 남아
    다음 요청에서도 그대로 지나가므로, 막지 않으면 대화를 이어갈 때마다 같은 «싫다»가
    한 번씩 더 쌓인다.
    """
    if not user_id or not signals:
        return 0
    saved = 0
    async with acquire() as conn:
        async with conn.cursor() as cur:
            for s in signals:
                await cur.execute(_INSERT_EDIT_SQL, {
                    "user_id": user_id,
                    "plan_id": plan_id,
                    "action": s.action,
                    "from_place_id": _place_ref(s.from_place_id),
                    "to_place_id": _place_ref(s.to_place_id),
                    # 원본 식별자를 함께 남긴다. FK 로 못 건 참조도 재집계는 읽는다.
                    "detail": jsonb({"signal": s.signal, "weight": s.weight,
                                     "signal_id": s.id, "from_ref": s.from_place_id,
                                     "to_ref": s.to_place_id}),
                    "created_at": s.observed_at.isoformat() if s.observed_at else None,
                    "signal_id": s.id,
                })
                saved += max(cur.rowcount, 0)
        await conn.commit()
    return saved


# 카드는 재평가가 전제다 — «관심 없어요» 했다가 나중에 «가봤어요»로 바꾼다.
# UNIQUE (user_id, subject) 위에서 UPSERT 하면 그 갱신이 행 하나로 끝난다.
_UPSERT_CARD_SQL = """
INSERT INTO preference_cards (user_id, subject, verdict, experienced, created_at)
VALUES (%(user_id)s, %(subject)s, %(verdict)s, %(experienced)s, now())
ON CONFLICT (user_id, subject) DO UPDATE
SET verdict = EXCLUDED.verdict, experienced = EXCLUDED.experienced,
    created_at = now()
"""

# 카드를 넣는 사용자는 «아직 아무것도 없는» 사용자다. 그런데 preference_cards.user_id 는
# users(id) 를 참조하므로, 행이 없으면 첫 카드가 FK 위반으로 통째로 튕긴다.
# 콜드 스타트가 바로 UR-01 이 겨냥하는 구간이라 여기서 막히면 기능 전체가 성립하지 않는다.
# 지금까지 users 행을 만드는 곳은 시드 스크립트뿐이었다.
_ENSURE_USER_SQL = "INSERT INTO users (id) VALUES (%(user_id)s) ON CONFLICT (id) DO NOTHING"


async def save_preference_cards(user_id: str, cards: list[PreferenceCard]) -> int:
    """카드 평가를 남긴다 (UR-01 · UR-31). 저장한 건수를 돌려준다.

    `save_plan_edits()` 와 달리 신호 id 로 중복을 거르지 않는다 — 저쪽은 «같은 사건이
    두 번 세어지면 안 된다»가 문제였지만, 카드는 같은 대상에 대한 **마지막 판단**만
    남으면 되고 그것이 UNIQUE 제약의 뜻이다. 두 번 평가하면 덮어쓰는 게 맞다.
    """
    if not user_id or not cards:
        return 0
    saved = 0
    async with acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_ENSURE_USER_SQL, {"user_id": user_id})
            for c in cards:
                await cur.execute(_UPSERT_CARD_SQL, {
                    "user_id": user_id, "subject": c.subject, "verdict": c.verdict,
                    "experienced": c.experienced,
                })
                saved += max(cur.rowcount, 0)
        await conn.commit()
    return saved


async def load_preference_cards(user_id: str) -> list[PreferenceCard]:
    """등록한 카드를 최신순으로 돌려준다 — 카드 화면이 «이미 평가한 것»을 표시한다."""
    sql = """
    SELECT subject, verdict, experienced, created_at
    FROM preference_cards WHERE user_id = %(user_id)s
    ORDER BY created_at DESC
    """
    async with acquire() as conn, conn.cursor() as cur:
        await cur.execute(sql, {"user_id": user_id})
        rows = await cur.fetchall()
    return [PreferenceCard(subject=s, verdict=v, experienced=e, created_at=t)
            for s, v, e, t in rows]


async def save_decisions(user_id: str, decisions: list[Decision]) -> None:
    sql = """
    INSERT INTO hitl_decisions (user_id, advisory_id, option_id, note, decided_at)
    VALUES (%(user_id)s, %(advisory_id)s, %(option_id)s, %(note)s, %(decided_at)s)
    ON CONFLICT (user_id, advisory_id) DO UPDATE
    SET option_id = EXCLUDED.option_id, note = EXCLUDED.note, decided_at = EXCLUDED.decided_at
    """
    async with acquire() as conn:
        async with conn.cursor() as cur:
            for d in decisions:
                await cur.execute(sql, {
                    "user_id": user_id, "advisory_id": d.advisory_id,
                    "option_id": d.option_id, "note": d.note, "decided_at": d.decided_at,
                })
        await conn.commit()


async def load_place_snapshot(user_id: str, place_id: str
                              ) -> tuple[dict[str, Any] | None, datetime | None]:
    """마지막 방문 시점에 기록해 둔 공식정보 스냅샷과 방문 시각."""
    sql = """
    SELECT s.payload, v.visited_at
    FROM visits v
    JOIN place_snapshots s ON s.id = v.snapshot_id
    WHERE v.user_id = %(user_id)s AND v.place_id = %(place_id)s
    ORDER BY v.visited_at DESC LIMIT 1
    """
    async with acquire() as conn, conn.cursor() as cur:
        await cur.execute(sql, {"user_id": user_id, "place_id": place_id})
        row = await cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


def jsonb(obj: Any):
    try:
        from psycopg.types.json import Jsonb

        return Jsonb(obj)
    except Exception:
        return json.dumps(obj, ensure_ascii=False)
