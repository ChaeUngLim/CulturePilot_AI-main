"""취향 카드 (UR-01 · UR-31) — 저장 · 재집계 · 콜드 스타트.

기획안이 내세운 «경험 기억형 개인화»는 아카이브가 쌓인 뒤에야 작동한다. 첫 사용자는
방문 기록이 0건이라 `personal_score()` 가 0.5 로 고정되고, 개인화가 사실상 꺼진 채로
첫인상을 만든다 — 가장 많은 사용자가 이탈하는 구간이 바로 거기다.

카드는 그 구간을 «말로 밝힌 취향»으로 받는다. 여기 테스트는 그 경로가 끊기면 깨진다 —
저장하는가 · 재집계가 되읽는가 · 콜드 스타트에서 점수가 실제로 갈리는가 ·
싫다고 한 것이 검색어로 되돌아오지는 않는가.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

import pytest

os.environ.setdefault("LLM_BACKEND", "fake")

from app.db import repo
from app.memory import profile as profile_mod
from app.schemas import Candidate, PreferenceCard, TasteProfile

PLACE_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
PLACE_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


# ------------------------------------------------------------------ DB 대역
class _Cursor:
    """SQL 문자열로 응답을 고르는 대역. 실제 DB 없이 쿼리 조립만 검사한다."""

    def __init__(self, script: dict[str, list]):
        self.script = script
        self.calls: list[tuple[str, dict]] = []
        self.rowcount = 1
        self._last = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self._last = sql
        self.calls.append((sql, params or {}))

    def _rows(self):
        for marker, rows in self.script.items():
            if marker in self._last:
                return rows
        return []

    async def fetchone(self):
        rows = self._rows()
        return rows[0] if rows else None

    async def fetchall(self):
        return self._rows()


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = 0

    def cursor(self):
        return self._cursor

    async def commit(self):
        self.committed += 1


def _fake_acquire(cursor):
    @asynccontextmanager
    async def acquire():
        yield _Conn(cursor)

    return acquire


def _card_row(subject, verdict, experienced=False, is_place=False, category=None):
    """`_CARDS_SQL` 이 돌려주는 행 모양."""
    return (subject, verdict, experienced, is_place, category)


# ------------------------------------------------------------------ 저장
async def test_save_preference_cards_upserts_each_card(monkeypatch):
    """재평가는 UPSERT 다. 같은 대상에 대한 **마지막 판단**만 남아야 한다."""
    cur = _Cursor({})
    monkeypatch.setattr(repo, "acquire", _fake_acquire(cur))

    saved = await repo.save_preference_cards("u1", [
        PreferenceCard(subject="전시", verdict="interested"),
        PreferenceCard(subject=PLACE_A, verdict="recommend", experienced=True),
    ])

    assert saved == 2
    sqls = [sql for sql, _ in cur.calls]
    assert any("ON CONFLICT (user_id, subject) DO UPDATE" in s for s in sqls)
    _, params = cur.calls[-1]
    assert params["subject"] == PLACE_A
    assert params["verdict"] == "recommend"
    assert params["experienced"] is True


async def test_save_preference_cards_creates_the_user_row_first(monkeypatch):
    """카드를 넣는 사람은 아직 `users` 에 없을 수 있다.

    `preference_cards.user_id` 가 `users(id)` 를 참조하므로, 행이 없으면 첫 카드가
    FK 위반으로 통째로 튕긴다. 콜드 스타트가 UR-01 이 겨냥하는 바로 그 구간이라
    여기서 막히면 기능 전체가 성립하지 않는다.
    """
    cur = _Cursor({})
    monkeypatch.setattr(repo, "acquire", _fake_acquire(cur))

    await repo.save_preference_cards("u1", [
        PreferenceCard(subject="전시", verdict="interested")])

    first_sql, first_params = cur.calls[0]
    assert "INSERT INTO users" in first_sql
    assert first_params["user_id"] == "u1"


async def test_save_preference_cards_is_a_noop_without_user_or_cards(monkeypatch):
    cur = _Cursor({})
    monkeypatch.setattr(repo, "acquire", _fake_acquire(cur))

    assert await repo.save_preference_cards(
        "", [PreferenceCard(subject="전시", verdict="interested")]) == 0
    assert await repo.save_preference_cards("u1", []) == 0
    assert cur.calls == []


async def test_load_preference_cards_maps_every_column(monkeypatch):
    cur = _Cursor({"FROM preference_cards": [("전시", "dislike", True, None)]})
    monkeypatch.setattr(repo, "acquire", _fake_acquire(cur))

    cards = await repo.load_preference_cards("u1")

    assert [(c.subject, c.verdict, c.experienced) for c in cards] == [
        ("전시", "dislike", True)]


# ------------------------------------------------------------------ 재집계
async def test_rebuild_profile_reads_preference_cards(monkeypatch):
    """재집계가 카드를 읽지 않으면, 방문 기록 **한 건**에 등록해 둔 취향이 전부 지워진다.

    재집계는 전량 재계산이다. UR-09 에서 똑같이 밟은 함정이라 같은 자리에 못을 박는다.
    """
    cur = _Cursor({
        "FROM visits": [({"전시": 2}, 30.0, 60.0, 0.5, 1.0)],
        "experience_embeddings": [],
        "FROM plan_edits": [],
        "FROM preference_cards": [_card_row("공연", "recommend")],
    })
    monkeypatch.setattr(profile_mod, "acquire", _fake_acquire(cur))

    profile = await profile_mod.rebuild_profile("u1")

    # 방문에서 온 «전시»와 카드에서 온 «공연»이 함께 살아 있어야 한다
    assert profile.preferred_categories["전시"] == 1.0
    assert profile.preferred_categories["공연"] == pytest.approx(0.15)


async def test_cards_add_on_top_of_visit_shares(monkeypatch):
    """같은 카테고리면 방문 share 위에 카드 가중치가 더해진다."""
    cur = _Cursor({
        "FROM visits": [({"전시": 1}, None, None, 0.0, 0.5)],
        "experience_embeddings": [],
        "FROM plan_edits": [],
        "FROM preference_cards": [_card_row("전시", "interested")],
    })
    monkeypatch.setattr(profile_mod, "acquire", _fake_acquire(cur))

    profile = await profile_mod.rebuild_profile("u1")
    assert profile.preferred_categories["전시"] == pytest.approx(1.0 + 0.08)


# ------------------------------------------------------------------ 접는 규칙
def test_place_card_folds_into_that_places_category():
    """«이 전시 좋아요»는 그 전시 하나가 아니라 «전시»라는 취향의 근거다."""
    profile = profile_mod.apply_preference_cards(TasteProfile(user_id="u1"), [
        _card_row(PLACE_A, "recommend", is_place=True, category="미술관")])

    assert profile.preferred_categories == {"미술관": 0.15}


def test_experienced_verdict_outweighs_a_stated_one():
    """겪고 내린 판단(«가봤어요»)은 말로만 밝힌 기대보다 무겁다."""
    stated = profile_mod.apply_preference_cards(
        TasteProfile(user_id="u1"), [_card_row("전시", "recommend")])
    lived = profile_mod.apply_preference_cards(
        TasteProfile(user_id="u2"), [_card_row("전시", "recommend", experienced=True)])

    assert lived.preferred_categories["전시"] > stated.preferred_categories["전시"]


def test_negative_card_makes_the_category_weight_negative():
    """«관심 없어요»가 음수로 남아야 `personal_score` 가 고칠 것 없이 감점한다."""
    profile = profile_mod.apply_preference_cards(TasteProfile(user_id="u1"), [
        _card_row("공연", "not_interested")])

    assert profile.preferred_categories["공연"] < 0


def test_disliked_place_lands_in_frequent_removals():
    """특정 장소를 싫다고 한 것은 카테고리 취향과 별개다 — 그 장소만 찍어 내린다."""
    profile = profile_mod.apply_preference_cards(TasteProfile(user_id="u1"), [
        _card_row(PLACE_A, "dislike", is_place=True, category="미술관")])

    assert PLACE_A in profile.frequent_removals
    assert profile.preferred_categories["미술관"] < 0


def test_liked_place_never_lands_in_frequent_removals():
    """`frequent_removals` 는 값이 아니라 **있는지**로 읽힌다(`personal_score`).

    좋다고 한 장소를 여기 넣으면 정확히 반대로 감점된다.
    """
    profile = profile_mod.apply_preference_cards(TasteProfile(user_id="u1"), [
        _card_row(PLACE_A, "recommend", is_place=True, category="미술관")])

    assert profile.frequent_removals == {}


def test_external_ref_never_becomes_a_category_name():
    """`places` 에 아직 없는 외부 후보(`kopis:PF12345`)는 카테고리가 아니다.

    거르지 않으면 그 식별자가 `preferred_categories` 에 들어앉고, 라우터가 그것을
    관심사로 삼아 «kopis:PF12345» 를 검색어로 쓴다.
    """
    profile = profile_mod.apply_preference_cards(TasteProfile(user_id="u1"), [
        _card_row("kopis:PF12345", "dislike"),
        _card_row(PLACE_B, "not_interested")])

    assert profile.preferred_categories == {}
    assert set(profile.frequent_removals) == {"kopis:PF12345", PLACE_B}


def test_cards_cannot_outweigh_the_archive():
    """카드를 아무리 많이 넘겨도 상한을 넘지 않는다.

    없으면 등록만 한 취향이 실제로 다녀온 곳보다 세지고, «기록이 쌓일수록 추천이
    좋아진다»는 전제가 뒤집힌다.
    """
    profile = profile_mod.apply_preference_cards(TasteProfile(user_id="u1"), [
        _card_row("전시", "recommend", experienced=True) for _ in range(20)])

    assert profile.preferred_categories["전시"] == pytest.approx(profile_mod._CARD_CAP)


def test_unknown_verdict_is_skipped_not_crashed():
    """스키마에 verdict 가 늘어도 재집계가 통째로 죽지는 않는다."""
    profile = profile_mod.apply_preference_cards(TasteProfile(user_id="u1"), [
        _card_row("전시", "someday_maybe")])

    assert profile.preferred_categories == {}


# --------------------------------------------------------------- 콜드 스타트
def test_cold_start_score_moves_off_the_flat_half():
    """UR-01 이 존재하는 이유 그 자체.

    아카이브가 0건이면 `personal_score` 가 0.5 로 고정돼 후보 순위가 전혀 갈리지 않는다.
    카드 한 장이면 좋아한다고 한 것과 싫다고 한 것의 점수가 실제로 벌어져야 한다.
    """
    liked = Candidate(name="현대미술 기획전", category="미술관")
    disliked = Candidate(name="야구 경기", category="스포츠")

    # 카드를 넘기기 전 — 프로필이 없어 둘이 완전히 같다
    assert profile_mod.personal_score(liked, None) == 0.5
    assert profile_mod.personal_score(disliked, None) == 0.5

    profile = profile_mod.apply_preference_cards(TasteProfile(user_id="u1"), [
        _card_row("미술관", "recommend", experienced=True),
        _card_row("스포츠", "not_interested")])

    assert profile_mod.personal_score(liked, profile) > 0.5
    assert profile_mod.personal_score(disliked, profile) < 0.5


def test_disliked_categories_never_become_search_interests():
    """싫다고 한 것을 검색어로 되돌려주면, 카드는 개인화가 아니라 역효과가 된다."""
    from app.graph.router import _apply_taste
    from app.schemas import TripConditions

    profile = profile_mod.apply_preference_cards(TasteProfile(user_id="u1"), [
        _card_row("미술관", "recommend"),
        _card_row("스포츠", "dislike")])

    out = _apply_taste(TripConditions(), profile)

    assert "미술관" in out.interests
    assert "스포츠" not in out.interests


def test_taste_summary_only_reports_positive_categories():
    """응답 문장의 «선호 카테고리»에 싫다고 한 것이 섞이면 안 된다."""
    from app.graph.nodes import _taste_summary

    profile = profile_mod.apply_preference_cards(TasteProfile(user_id="u1"), [
        _card_row("미술관", "recommend"),
        _card_row("스포츠", "dislike")])

    assert _taste_summary(profile)["선호 카테고리"] == ["미술관"]
