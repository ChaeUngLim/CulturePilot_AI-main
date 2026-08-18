"""취향 프로필 집계. 임베딩 검색이 '개별 경험'을 다룬다면 여기는 '누적 경향'."""
from __future__ import annotations

import logging
from typing import Any, get_args

from app.db.repo import UUID_RE, jsonb
from app.db.session import acquire
from app.schemas import EditSignal, TasteProfile, utc_now

logger = logging.getLogger(__name__)

_LOAD_SQL = "SELECT profile FROM taste_profiles WHERE user_id = %(user_id)s"

_AGG_SQL = """
SELECT
  COALESCE(jsonb_object_agg(cat, cnt) FILTER (WHERE cat IS NOT NULL), '{}'::jsonb) AS cats,
  AVG(travel_min)::float  AS avg_travel,
  AVG(dwell_min)::float   AS avg_dwell,
  AVG(CASE WHEN indoor THEN 1.0 ELSE -1.0 END)::float AS indoor_bias,
  AVG(CASE WHEN is_revisit THEN 0.0 ELSE 1.0 END)::float AS novelty
FROM (
  SELECT p.category AS cat, COUNT(*) AS cnt, v.travel_min, v.dwell_min,
         p.indoor, v.is_revisit
  FROM visits v JOIN places p ON p.id = v.place_id
  WHERE v.user_id = %(user_id)s
  GROUP BY p.category, v.travel_min, v.dwell_min, p.indoor, v.is_revisit
) t
"""

_SAVE_SQL = """
INSERT INTO taste_profiles (user_id, profile, updated_at)
VALUES (%(user_id)s, %(profile)s, now())
ON CONFLICT (user_id) DO UPDATE
   SET profile = EXCLUDED.profile, updated_at = now()
"""

_FRICTION_SQL = """
SELECT f AS tag, COUNT(*)::float AS n
FROM experience_embeddings e, unnest(e.friction) AS f
WHERE e.user_id = %(user_id)s
GROUP BY f
"""

# 수정 행동 재집계 (UR-09). FK 로 걸린 참조가 없으면 원본 문자열(detail->>'from_ref')을
# 쓴다 — 외부 API 후보는 아직 `places` 에 없는 식별자를 들고 있을 수 있다.
_EDITS_SQL = """
SELECT action,
       COALESCE(from_place_id::text, detail->>'from_ref')    AS ref,
       SUM(COALESCE((detail->>'weight')::float, 1.0))::float AS weight,
       COUNT(*)::int                                         AS n
FROM plan_edits
WHERE user_id = %(user_id)s
GROUP BY action, ref
"""

# Literal 을 다시 적지 않는다. 스키마에 액션이 하나 늘 때 여기만 안 고쳐서
# 조용히 무시되는 일을 막는다.
_EDIT_ACTIONS = set(get_args(EditSignal.model_fields["action"].annotation))

# 카드 재집계 (UR-01). `subject` 가 장소면 그 장소의 카테고리까지 끌어온다 —
# «이 전시 좋아요»는 그 전시 하나가 아니라 «전시»라는 취향의 근거다.
#
# 조인을 `p.id::text = c.subject` 로 거는 이유 — 반대로 `c.subject::uuid` 로 캐스팅하면
# 카테고리 이름("전시")이 든 행에서 uuid 문법 오류가 나고, 그 순간 재집계 전체가
# except 로 떨어져 **프로필이 통째로 빈 값이 된다.** 사용자 입력을 캐스팅하지 않는다.
_CARDS_SQL = """
SELECT c.subject,
       c.verdict,
       c.experienced,
       (p.id IS NOT NULL) AS is_place,
       p.category         AS place_category
FROM preference_cards c
LEFT JOIN places p ON p.id::text = c.subject
WHERE c.user_id = %(user_id)s
"""

# 카드 한 장의 무게. 방문 기록에서 나온 share(합이 1)와 같은 축에 더해지므로,
# 카테고리 하나가 방문 몇 건에 맞먹는 수준으로 잡았다.
_CARD_WEIGHTS = {
    "recommend": 0.15,
    "interested": 0.08,
    "not_interested": -0.08,
    "dislike": -0.15,
}

# 겪고 내린 판단(«가봤어요»)은 말로만 밝힌 기대보다 무겁다. 같은 verdict 라도 가른다.
_EXPERIENCED_BOOST = 1.5

# 카드가 방문 기록을 덮지 못하게 하는 상한. 없으면 카드 20장을 넘긴 사용자에게
# **실제로 다녀온 곳보다 등록만 한 취향이 더 세지고**, 아카이브가 쌓일수록
# 추천이 좋아진다는 전제가 뒤집힌다.
_CARD_CAP = 0.30


def _edit_signals(rows: list[Any]) -> list[EditSignal]:
    """집계한 수정 행동을 `apply_edit_signals()` 가 아는 형태로 되돌린다.

    반영 규칙을 여기서 다시 쓰지 않는 이유 — 온라인 반영(persist)과 재집계가 서로 다른
    계산을 하면 «리포트에서 본 수치»와 «추천에 쓰인 수치»가 갈린다. 삭제·교체는 가중치가
    더해지므로 합계 한 건으로 접고, 체류시간은 건당 배율이 곱해지므로 건수만큼 편다.
    """
    signals: list[EditSignal] = []
    for action, ref, weight, n in rows:
        if action not in _EDIT_ACTIONS:
            continue
        if action in ("remove", "replace"):
            if not ref:
                continue
            signals.append(EditSignal(action=action, from_place_id=ref,
                                      signal="수정 행동 재집계", weight=float(weight or 1.0)))
        elif action in ("dwell_up", "dwell_down"):
            signals.extend(
                EditSignal(action=action, from_place_id=ref, signal="수정 행동 재집계")
                for _ in range(int(n or 0)))
    return signals


def _is_place_ref(subject: str) -> bool:
    """카테고리 이름이 아니라 «어떤 장소 하나»를 가리키는 문자열인가.

    `places` 에 아직 없는 외부 후보(`kopis:PF12345`)도 걸러야 한다. 안 그러면
    그 식별자가 카테고리 이름 행세를 하며 `preferred_categories` 에 들어앉고,
    라우터가 그것을 관심사로 삼아 «kopis:PF12345» 를 검색하게 된다.
    """
    return bool(UUID_RE.match(subject)) or ":" in subject


def apply_preference_cards(profile: TasteProfile, rows: list[Any]) -> TasteProfile:
    """카드 평가를 프로필에 접는다 (UR-01 · UR-31).

    ★ 새 필드를 만들지 않는다. 카드를 `preferred_categories` / `frequent_removals`
    두 기존 필드로 접으면 `personal_score()` 는 **한 줄도 고치지 않고** 카드를 반영한다.
    개인화 점수 계산이 «어디서 온 취향인가»를 알 필요가 없다는 뜻이고, 그래야 다음에
    또 다른 취향 입력이 생겨도 점수 함수가 그때마다 늘어나지 않는다.

    싫다는 카드는 카테고리 가중치를 **음수로** 만든다. `personal_score` 의
    `score += 0.4 * preferred_categories.get(...)` 가 그대로 감점으로 동작한다.

    행 모양은 `_CARDS_SQL` 의 것이다 — (subject, verdict, experienced, is_place,
    place_category).
    """
    deltas: dict[str, float] = {}
    for subject, verdict, experienced, is_place, place_category in rows:
        weight = _CARD_WEIGHTS.get(verdict)
        if weight is None:            # 스키마에 verdict 가 늘면 조용히 무시하지 않는다
            logger.warning("알 수 없는 verdict 를 건너뛴다: %s", verdict)
            continue
        if experienced:
            weight *= _EXPERIENCED_BOOST

        # 장소 카드는 그 장소의 카테고리로, 카테고리 카드는 자기 이름으로 접힌다.
        category = place_category if is_place else (
            None if _is_place_ref(subject) else subject)
        if category:
            deltas[category] = deltas.get(category, 0.0) + weight

        # 특정 장소를 싫다고 한 것은 카테고리 취향과 별개다 — 그 장소만 찍어 내린다.
        # `frequent_removals` 는 값이 아니라 **있는지**로 읽히므로(personal_score)
        # 양수 카드는 여기에 넣지 않는다. 넣으면 좋다고 한 장소가 감점된다.
        if weight < 0 and (is_place or _is_place_ref(subject)):
            profile.frequent_removals[subject] = (
                profile.frequent_removals.get(subject, 0.0) + abs(weight))

    for category, delta in deltas.items():
        capped = max(-_CARD_CAP, min(_CARD_CAP, delta))
        base = profile.preferred_categories.get(category, 0.0)
        # 상한이 1.0 이 아니라 `1.0 + _CARD_CAP` 인 이유 — 방문 share 는 최대 1.0 이고
        # (한 카테고리만 다닌 사용자), 거기서 1.0 으로 자르면 **가장 뚜렷한 취향에
        # 매긴 카드만 조용히 무시된다.** 카드 몫은 이미 `_CARD_CAP` 으로 잡혀 있으니
        # 그만큼을 위로 열어 둬야 «share + 카드»가 모든 구간에서 같은 뜻을 갖는다.
        profile.preferred_categories[category] = round(
            max(-1.0, min(1.0 + _CARD_CAP, base + capped)), 4)
    profile.updated_at = utc_now()
    return profile


async def load_profile(user_id: str) -> TasteProfile | None:
    try:
        async with acquire() as conn, conn.cursor() as cur:
            await cur.execute(_LOAD_SQL, {"user_id": user_id})
            row = await cur.fetchone()
            if row and row[0]:
                return TasteProfile(**row[0])
    except Exception as exc:
        logger.warning("load_profile degraded: %s", exc)
    return None


async def rebuild_profile(user_id: str) -> TasteProfile:
    """방문/불편 기록을 재집계한다. 야간 배치 또는 방문 기록 저장 후 호출."""
    profile = TasteProfile(user_id=user_id, updated_at=utc_now())
    try:
        async with acquire() as conn, conn.cursor() as cur:
            await cur.execute(_AGG_SQL, {"user_id": user_id})
            row = await cur.fetchone()
            if row:
                cats, avg_travel, avg_dwell, indoor_bias, novelty = row
                total = sum((cats or {}).values()) or 1
                profile.preferred_categories = {
                    k: round(v / total, 4) for k, v in (cats or {}).items()
                }
                profile.avg_travel_min = avg_travel
                profile.avg_dwell_min = avg_dwell
                profile.indoor_bias = indoor_bias or 0.0
                profile.novelty_bias = (novelty or 0.5) * 2 - 1
            await cur.execute(_FRICTION_SQL, {"user_id": user_id})
            rows = await cur.fetchall()
            tot = sum(n for _, n in rows) or 1
            profile.friction_sensitivity = {t: round(n / tot, 4) for t, n in rows}
            # 수정 행동도 함께 읽는다 (UR-09). 이걸 빼면 재집계가 방문 기록만 보고
            # 프로필을 새로 만들어, 일정에서 지운 장소가 다음 추천에 그대로 돌아온다.
            await cur.execute(_EDITS_SQL, {"user_id": user_id})
            profile = apply_edit_signals(profile, _edit_signals(await cur.fetchall()))
            # 카드도 여기서 읽는다 (UR-01). 재집계는 전량 재계산이라, 이 한 줄이 없으면
            # 방문 기록 **한 건**이 들어온 순간 등록해 둔 취향 카드가 전부 지워진다.
            # UR-09 에서 똑같이 밟았던 함정이다.
            await cur.execute(_CARDS_SQL, {"user_id": user_id})
            profile = apply_preference_cards(profile, await cur.fetchall())
    except Exception as exc:
        logger.warning("rebuild_profile degraded: %s", exc)
    return profile


async def save_profile(user_id: str, profile: TasteProfile) -> None:
    """프로필을 덮어쓴다. 실패는 삼킨다 — 개인화는 일정 생성의 전제가 아니다."""
    if not user_id:
        return
    try:
        async with acquire() as conn, conn.cursor() as cur:
            await cur.execute(_SAVE_SQL, {
                "user_id": user_id,
                "profile": jsonb(profile.model_dump(mode="json")),
            })
    except Exception as exc:
        logger.warning("save_profile degraded: %s", exc)


def apply_edit_signals(profile: TasteProfile, signals: list[EditSignal]) -> TasteProfile:
    """수정 행동 신호를 프로필에 반영(온라인 업데이트).

    집계(rebuild_profile)는 '방문한 뒤'에만 배울 수 있다. 하지만 사용자가 추천을
    지우거나 바꾼 순간에도 배울 것이 있다 — 오히려 그쪽이 더 분명한 신호다.
    그래서 일정을 저장할 때마다 이 함수로 즉시 반영한다.
    """
    for s in signals:
        if s.action in ("remove", "replace") and s.from_place_id:
            key = s.from_place_id
            profile.frequent_removals[key] = profile.frequent_removals.get(key, 0.0) + s.weight
        if s.action == "dwell_up":
            profile.avg_dwell_min = (profile.avg_dwell_min or 60) * 1.02
        if s.action == "dwell_down":
            profile.avg_dwell_min = (profile.avg_dwell_min or 60) * 0.98
    profile.updated_at = utc_now()
    return profile


def personal_score(candidate, profile: TasteProfile | None) -> float:
    """후보 장소에 프로필 기반 개인화 점수를 매긴다(0~1)."""
    if profile is None:
        return 0.5
    score = 0.5
    if candidate.category:
        score += 0.4 * profile.preferred_categories.get(candidate.category, 0.0)
    if candidate.indoor is not None:
        score += 0.1 * (profile.indoor_bias if candidate.indoor else -profile.indoor_bias)
    if candidate.place_id and candidate.place_id in profile.frequent_removals:
        score -= 0.25
    return max(0.0, min(1.0, score))
