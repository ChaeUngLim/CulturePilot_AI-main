"""시연용 데이터 주입.

    docker compose exec api python scripts/seed_demo.py

이 서비스의 핵심(과거 경험이 다음 일정에 개입한다)은 아카이브가 비어 있으면
아무것도 보여주지 못한다. 방문 기록·불편 경험·일정 수정 행동을 실제 스키마에 넣어
경고 카드와 취향 리포트가 동작하는 상태를 만든다.

임베딩은 실제 모델로 생성한다. 더미 벡터를 넣으면 검색 순위가 무의미해져
'아카이브가 동작한다'는 확인 자체가 성립하지 않는다.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LLM_BACKEND", "nim")

from _catalog_data import CATALOG
from generate_catalog import generate

from app.db.session import acquire, close_pool
from app.llm.provider import get_embeddings
from app.memory.profile import rebuild_profile
from app.schemas import utc_now

DEMO_USER = "00000000-0000-0000-0000-000000000001"

# 손으로 쓴 실제 장소(92곳) + 생성한 가상 장소(2,000곳).
# 실제 장소는 데모의 신뢰도를, 가상 장소는 지역·카테고리 커버리지를 담당한다.
GENERATED = generate(2000)
PLACES = CATALOG + GENERATED

# (place_key, 며칠 전, 별점, 감상, 불편태그, 동행자, 이동수단, 체류분, 이동분, 재방문)
VISITS = [
    # (place_key, 며칠 전, 별점, 감상, 불편태그, 동행자, 이동수단, 체류분, 이동분, 재방문)
    # ── 주차 불편 반복 (경고 카드의 근거) ────────────────────────────
    ("sc-sac", 140, 4.0, "공연은 훌륭했는데 주차장 진입에 30분 걸렸다. 다음엔 지하철로 와야겠다.",
     ["parking"], "couple", "car", 120, 45, False),
    ("sc-sac", 42, 3.5, "두 번째 방문. 주차는 여전히 불편해서 인근 공영주차장을 이용했다.",
     ["parking", "waiting"], "couple", "car", 110, 40, True),
    ("sp-lotte", 118, 4.0, "전시는 만족스러웠지만 몰 주차장이 복잡해 나오는 데 오래 걸렸다.",
     ["parking"], "friends", "car", 85, 40, False),
    # ── 혼잡·대기 ───────────────────────────────────────────────────
    ("gn-coex", 110, 3.0, "별마당도서관은 사진 찍는 사람이 많아 정신없었다. 앉을 자리가 없었다.",
     ["crowding"], "friends", "transit", 40, 30, False),
    ("sp-seokchon", 130, 2.5, "벚꽃 시즌이라 발 디딜 틈이 없었다. 공연을 제대로 못 봤다.",
     ["crowding"], "couple", "transit", 30, 35, False),
    ("ys-museum", 165, 4.0, "상설전은 훌륭한데 주말이라 특별전 대기줄이 길었다.",
     ["waiting", "crowding"], "family", "transit", 120, 30, False),
    ("bs-gamcheon", 200, 3.5, "골목이 예쁘지만 관광객이 너무 많고 경사가 심했다.",
     ["crowding", "accessibility"], "couple", "walk", 80, 45, False),
    # ── 만족 (개인화 가점) ──────────────────────────────────────────
    ("sd-indie", 48, 5.0, "독립영화 상영관. 좌석이 편하고 관객이 적어 몰입이 잘 됐다.",
     [], "solo", "transit", 110, 18, False),
    ("sd-indie", 12, 4.5, "재방문. 상영 시간표가 바뀌어 있어 확인하고 갔다.",
     [], "solo", "transit", 105, 18, True),
    ("sd-bookstore", 30, 4.5, "연희동 골목의 작은 책방. 큐레이션이 좋아 오래 머물렀다.",
     [], "solo", "walk", 60, 12, False),
    ("sd-nature", 205, 4.5, "아이와 갔는데 전시 구성이 알찼다. 주차도 여유로웠다.",
     [], "kids", "car", 95, 25, False),
    ("sd-prison", 150, 4.0, "역사적 무게가 있는 공간. 실내외가 섞여 있어 더운 날은 힘들 듯.",
     [], "friends", "transit", 80, 22, False),
    ("sd-sinchon", 62, 4.0, "작은 공연장인데 음향이 좋았다. 신촌역에서 걸어서 금방.",
     [], "friends", "walk", 70, 10, False),
    ("ys-leeum", 75, 4.5, "예약제라 한산하고 쾌적했다. 혼자 천천히 보기 좋았다.",
     ["reservation"], "solo", "transit", 110, 32, False),
    ("ys-hangeul", 145, 4.0, "규모는 작지만 전시 설계가 세심했다. 중앙박물관과 이어서 봤다.",
     [], "solo", "walk", 60, 8, False),
    ("gn-platform", 85, 4.0, "강남에서 이런 조용한 전시 공간은 드물다. 무료라 부담 없었다.",
     [], "solo", "transit", 55, 28, False),
    ("gn-book", 70, 4.5, "책방 겸 카페. 자리가 넉넉해 오래 있기 좋았다.",
     [], "solo", "walk", 75, 15, False),
    ("sc-hangaram", 190, 4.0, "기획전 동선이 잘 짜여 있었다. 예술의전당 안이라 접근이 편했다.",
     [], "family", "transit", 85, 40, False),
    ("sp-soma", 96, 4.5, "올림픽공원과 함께 보기 좋았다. 넓고 한산했다.",
     [], "solo", "transit", 90, 35, False),
    ("bs-f1963", 220, 5.0, "폐공장을 개조한 복합공간. 서점·카페·전시가 한곳에 있어 오래 머물렀다.",
     [], "couple", "car", 130, 35, False),
    ("bs-cinema", 218, 4.0, "독립영화 상영관. 좌석이 편했고 상영작 선택이 좋았다.",
     [], "couple", "walk", 110, 10, False),
    ("bs-book", 216, 4.5, "보수동 책방골목. 헌책 냄새가 좋았다.",
     [], "couple", "walk", 70, 15, False),
    ("dj-moa", 175, 4.0, "출장 중 들렀다. 규모는 작지만 기획이 좋았고 한산했다.",
     [], "solo", "walk", 60, 15, False),
    ("dj-ungno", 174, 4.5, "대전시립미술관 바로 옆이라 이어서 봤다. 건물 자체가 좋았다.",
     [], "solo", "walk", 50, 5, False),
    ("dg-concert", 280, 4.0, "클래식 공연. 홀 음향이 기대 이상이었다.",
     [], "couple", "transit", 120, 25, False),
    ("dg-moa", 300, 4.0, "대구미술관은 공간이 넓어 여유로웠다. 다만 접근성이 아쉬웠다.",
     ["accessibility"], "family", "car", 95, 50, False),
    ("ic-moa", 88, 4.5, "인천아트플랫폼. 개항장 건물을 살린 공간이 인상적이었다.",
     [], "couple", "transit", 80, 50, False),
    ("ic-book", 86, 4.0, "배다리 헌책방거리. 조용히 둘러보기 좋았다.",
     [], "couple", "walk", 65, 12, False),
    # ── 야외·날씨 (실내 선호의 근거) ────────────────────────────────
    ("ys-nodeul", 320, 2.5, "야외 공연이었는데 비가 와서 중간에 나왔다. 그늘이 없어 더웠다.",
     ["weather"], "couple", "transit", 40, 40, False),
    ("dg-kimgs", 240, 3.0, "여름에 갔더니 너무 더웠다. 볼거리는 있지만 야외라 오래 못 있었다.",
     ["weather"], "friends", "walk", 35, 20, False),
    ("sd-ansan", 260, 2.5, "야외 공연이었는데 소나기가 와서 중간에 내려왔다.",
     ["weather"], "couple", "walk", 35, 25, False),
    ("ic-songdo", 190, 3.0, "송도 야외무대. 바람이 세서 오래 있기 힘들었다.",
     ["weather"], "friends", "transit", 40, 55, False),
]

# 일정 수정 행동 — 별점보다 강한 개인화 신호
EDITS = [
    ("remove",  "gn-coex",     None,           {"reason": "사람 많은 곳 회피"}, 100),
    ("replace", "gn-coex",     "gn-platform",  {"reason": "조용한 전시로 교체"}, 80),
    ("remove",  "sp-seokchon", None,           {"reason": "야외·혼잡 제외"}, 125),
    ("replace", "sp-seokchon", "sp-soma",      {"reason": "실내 미술관으로 교체"}, 124),
    ("remove",  "ys-nodeul",   None,           {"reason": "야외라 제외"}, 200),
    ("remove",  "dg-kimgs",    None,           {"reason": "더운 날 야외 제외"}, 150),
    ("remove",  "sd-ansan",    None,           {"reason": "비 예보로 제외"}, 180),
    ("remove",  "bs-gamcheon", None,           {"reason": "경사·혼잡 회피"}, 190),
    ("dwell_up", "sd-bookstore", None,         {"from": 40, "to": 60}, 28),
    ("dwell_up", "ys-leeum",   None,           {"from": 90, "to": 120}, 70),
    ("dwell_up", "sd-indie",   None,           {"from": 90, "to": 110}, 40),
    ("replace", "sc-sac",      "sc-hangaram",  {"reason": "주차 불편으로 교체"}, 40),
    ("transport_change", "sc-sac", None,       {"from": "car", "to": "transit"}, 41),
]


async def main() -> int:
    print("=" * 66)
    print("  시연용 아카이브 데이터를 넣습니다")
    print("=" * 66)

    async with acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO users (id, email, display_name) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (DEMO_USER, "demo@culturemate.local", "데모 사용자"))

            curated_keys = {p[0] for p in CATALOG}
            await cur.executemany("""
                INSERT INTO places (external_key, name, kind, category, address,
                                    region, lat, lng, indoor, dwell_min,
                                    parking, parking_note, curated)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (external_key) DO UPDATE
                SET name = EXCLUDED.name, category = EXCLUDED.category,
                    address = EXCLUDED.address, region = EXCLUDED.region,
                    lat = EXCLUDED.lat, lng = EXCLUDED.lng,
                    indoor = EXCLUDED.indoor, dwell_min = EXCLUDED.dwell_min,
                    parking = EXCLUDED.parking, parking_note = EXCLUDED.parking_note,
                    curated = EXCLUDED.curated
            """, [(p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], p[9],
                   p[10], p[11], p[0] in curated_keys) for p in PLACES])

            # 방문 기록이 참조할 장소의 id 만 다시 읽는다
            await cur.execute(
                "SELECT external_key, id::text FROM places WHERE external_key = ANY(%s)",
                ([p[0] for p in CATALOG],))
            place_ids: dict[str, str] = dict(await cur.fetchall())

            from collections import Counter
            by_region = Counter(p[5] for p in PLACES)
            print(f"  카탈로그 {len(place_ids)}곳 "
                  f"(실제 {len(CATALOG)} + 생성 {len(GENERATED)})")
            print(f"    지역 {len(by_region)}개 · 지역당 "
                  f"{min(by_region.values())}~{max(by_region.values())}곳")

            visit_rows = []
            for (key, days, rating, review, friction, comp,
                 transport, dwell, travel, revisit) in VISITS:
                when = utc_now() - timedelta(days=days)
                await cur.execute("""
                    INSERT INTO visits (user_id, place_id, visited_at, rating, review,
                                        friction, companions, transport, dwell_min,
                                        travel_min, is_revisit, meta)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'{}'::jsonb)
                    RETURNING id::text
                """, (DEMO_USER, place_ids[key], when, rating, review, friction,
                      comp, transport, dwell, travel, revisit))
                vid = (await cur.fetchone())[0]
                visit_rows.append((vid, key, review, friction, rating, when,
                                   comp, transport))
            print(f"  방문 기록 {len(visit_rows)}건")

            for action, frm, to, detail, days in EDITS:
                from psycopg.types.json import Jsonb
                await cur.execute("""
                    INSERT INTO plan_edits (user_id, action, from_place_id,
                                            to_place_id, detail, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s)
                """, (DEMO_USER, action, place_ids[frm],
                      place_ids.get(to) if to else None, Jsonb(detail),
                      utc_now() - timedelta(days=days)))
            print(f"  일정 수정 행동 {len(EDITS)}건")
        await conn.commit()

    # ---- 임베딩 생성 (실제 모델) ----
    print("\n  경험 문장을 임베딩합니다…")
    embedder = get_embeddings()
    texts = [_sentence(r) for r in visit_rows]
    vectors = await embedder.aembed_documents(texts)
    print(f"  {len(vectors)}개 · 차원 {len(vectors[0])}")

    from psycopg.types.json import Jsonb

    async with acquire() as conn:
        async with conn.cursor() as cur:
            for (vid, key, _review, friction, rating, when, comp, transport), \
                    text, vec in zip(visit_rows, texts, vectors, strict=True):
                sentiment = round((rating - 3.0) / 2.0, 2)
                await cur.execute("""
                    INSERT INTO experience_embeddings
                        (user_id, source_type, source_id, place_id, summary, tags,
                         friction, sentiment, rating, occurred_at, meta, embedding, ts)
                    VALUES (%s,'visit',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,
                            to_tsvector('simple', %s))
                    ON CONFLICT (source_type, source_id) DO UPDATE
                    SET summary = EXCLUDED.summary, embedding = EXCLUDED.embedding,
                        ts = EXCLUDED.ts, updated_at = now()
                """, (DEMO_USER, vid, place_ids[key], text,
                      [c for c in (comp, transport) if c], friction, sentiment,
                      rating, when,
                      Jsonb({"region": "서울", "companions": comp,
                             "transport": transport, "season": _season(when.month)}),
                      vec, text))
        await conn.commit()
    print(f"  아카이브 인덱스 {len(vectors)}건")

    profile = await rebuild_profile(DEMO_USER)
    async with acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO taste_profiles (user_id, profile) VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE
                SET profile = EXCLUDED.profile, updated_at = now()
            """, (DEMO_USER, Jsonb(profile.model_dump(mode="json"))))
        await conn.commit()

    print("\n  취향 프로필")
    print(f"    선호 카테고리 : {profile.preferred_categories}")
    print(f"    실내 성향     : {profile.indoor_bias:.2f}")
    print(f"    평균 이동/체류: {profile.avg_travel_min:.0f}분 / {profile.avg_dwell_min:.0f}분")
    print(f"    불편 민감도   : {profile.friction_sensitivity}")

    await close_pool()
    print("\n" + "=" * 66)
    print("  완료. 앱의 EXPO_PUBLIC_USER_ID 를 아래로 바꾸면 이 기록이 보입니다:")
    print(f"    {DEMO_USER}")
    print("=" * 66)
    return 0


def _sentence(row) -> str:
    """검색 대상이 될 경험 문장. 장소·상황·감정이 한 문장에 들어가야 회수가 된다."""
    _vid, key, review, friction, rating, when, comp, transport = row
    name = next(p[1] for p in PLACES if p[0] == key)
    comp_ko = {"solo": "혼자", "couple": "연인과", "friends": "친구들과",
               "family": "가족과"}.get(comp, "")
    move_ko = {"car": "차로", "transit": "대중교통으로",
               "walk": "걸어서"}.get(transport, "")
    head = f"{when:%Y년 %m월} {comp_ko} {move_ko} {name} 방문. 별점 {rating}."
    tail = f" 불편했던 점: {', '.join(friction)}." if friction else ""
    return f"{head} {review}{tail}"


def _season(month: int) -> str:
    return {12: "winter", 1: "winter", 2: "winter"}.get(
        month, "spring" if month <= 5 else "summer" if month <= 8 else "autumn")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
