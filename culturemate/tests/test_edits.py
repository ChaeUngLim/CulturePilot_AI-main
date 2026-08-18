"""수정 행동 학습 (UR-09) — 저장 · 재집계 · 콜드스타트.

명세서 §0은 개인화의 근거를 «방문 기록 + 일정 수정 행동» 두 가지로 든다.
뒤쪽 절반이 오래 비어 있었다 — 신호를 뽑는 함수(`extract_edit_signals`)와 프로필에
반영하는 함수(`apply_edit_signals`)는 있었지만 **어디에도 저장되지 않아서**,
재집계가 한 번 돌면 방문 기록만 남고 수정 행동은 사라졌다.

여기 테스트는 그 경로가 다시 끊기면 깨진다 — persist 가 저장하는가 · 재집계가 읽는가 ·
프로필 행이 없는 첫 사용자에게도 남는가.
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

import pytest

os.environ.setdefault("LLM_BACKEND", "fake")

from app.db import repo
from app.memory import profile as profile_mod
from app.schemas import EditSignal, Itinerary, ItineraryItem, TasteProfile

PLACE_A = "11111111-1111-1111-1111-111111111111"
PLACE_B = "22222222-2222-2222-2222-222222222222"


# ------------------------------------------------------------------ DB 대역
class _Cursor:
    """SQL 문자열로 응답을 고르는 대역. 실제 DB 없이 쿼리 조립만 검사한다."""

    def __init__(self, script: dict[str, list]):
        self.script = script
        self.calls: list[tuple[str, dict]] = []
        self.rowcount = 1        # INSERT 한 건이 들어갔다는 뜻
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


def _detail(value):
    """`jsonb()` 는 psycopg 가 있으면 Jsonb 를, 없으면 문자열을 돌려준다."""
    obj = getattr(value, "obj", value)
    return json.loads(obj) if isinstance(obj, str) else obj


async def _noop(*args, **kwargs):
    return None


# ------------------------------------------------------------------ 저장
async def test_save_plan_edits_writes_one_row_per_signal(monkeypatch):
    """신호 하나가 행 하나다. 합쳐 넣으면 '몇 번 지웠나'를 나중에 셀 수 없다."""
    cur = _Cursor({})
    monkeypatch.setattr(repo, "acquire", _fake_acquire(cur))

    signals = [
        EditSignal(action="remove", from_place_id=PLACE_A, signal="s1", weight=1.0),
        EditSignal(action="dwell_up", from_place_id=PLACE_B, signal="s2", weight=0.7),
    ]
    saved = await repo.save_plan_edits("u1", "plan-1", signals)

    assert saved == 2
    assert len(cur.calls) == 2
    _, params = cur.calls[0]
    assert params["action"] == "remove"
    assert params["plan_id"] == "plan-1"
    assert params["signal_id"] == signals[0].id
    assert params["from_place_id"] == PLACE_A
    assert _detail(params["detail"])["weight"] == 1.0


async def test_save_plan_edits_keeps_unknown_place_ref_out_of_the_fk(monkeypatch):
    """외부 API 후보는 `places.id` 가 아닌 식별자를 들고 있다.

    그대로 넣으면 uuid 문법 오류로 INSERT 가 통째로 죽는다 — 한 건 때문에 그날의
    수정 행동이 전부 사라지는 게 실제 위험이다. FK 자리에서는 빼되 원본은 남긴다.
    """
    cur = _Cursor({})
    monkeypatch.setattr(repo, "acquire", _fake_acquire(cur))

    await repo.save_plan_edits("u1", "plan-1", [
        EditSignal(action="replace", from_place_id="kopis:PF12345",
                   to_place_id=PLACE_B, signal="s", weight=0.9)])

    _, params = cur.calls[0]
    assert params["from_place_id"] is None
    assert params["to_place_id"] == PLACE_B
    assert _detail(params["detail"])["from_ref"] == "kopis:PF12345"


async def test_save_plan_edits_is_a_noop_without_user_or_signals(monkeypatch):
    cur = _Cursor({})
    monkeypatch.setattr(repo, "acquire", _fake_acquire(cur))

    assert await repo.save_plan_edits("", "p", [EditSignal(action="remove", signal="s")]) == 0
    assert await repo.save_plan_edits("u1", "p", []) == 0
    assert cur.calls == []


# ------------------------------------------------------------------ 재집계
async def test_rebuild_profile_reads_plan_edits(monkeypatch):
    """재집계가 수정 행동을 읽지 않으면, 지운 장소가 다음 추천에 그대로 돌아온다."""
    cur = _Cursor({
        "FROM visits": [({"전시": 2}, 30.0, 60.0, 0.5, 1.0)],
        "experience_embeddings": [],
        "FROM plan_edits": [
            ("remove", PLACE_A, 2.0, 2),
            ("dwell_up", PLACE_B, 0.7, 1),
            ("reorder", PLACE_B, 0.5, 1),        # 프로필 수치에는 반영되지 않는 종류
        ],
    })
    monkeypatch.setattr(profile_mod, "acquire", _fake_acquire(cur))

    profile = await profile_mod.rebuild_profile("u1")

    assert profile.frequent_removals == {PLACE_A: 2.0}
    assert profile.avg_dwell_min == pytest.approx(60.0 * 1.02)


async def test_rebuild_profile_falls_back_to_the_raw_place_ref(monkeypatch):
    """FK 로 못 건 참조도 재집계는 읽는다 (`detail->>'from_ref'`)."""
    cur = _Cursor({
        "FROM visits": [({}, None, None, 0.0, 0.5)],
        "experience_embeddings": [],
        "FROM plan_edits": [("remove", "kopis:PF12345", 1.0, 1)],
    })
    monkeypatch.setattr(profile_mod, "acquire", _fake_acquire(cur))

    profile = await profile_mod.rebuild_profile("u1")
    assert profile.frequent_removals == {"kopis:PF12345": 1.0}


def test_rebuild_and_online_update_agree():
    """재집계와 온라인 반영이 같은 수를 내야 한다.

    둘이 갈리면 «리포트에서 본 수치»와 «추천에 실제로 쓰인 수치»가 달라진다.
    """
    online = profile_mod.apply_edit_signals(
        TasteProfile(user_id="u1"),
        [EditSignal(action="remove", from_place_id=PLACE_A, signal="s", weight=1.0),
         EditSignal(action="remove", from_place_id=PLACE_A, signal="s", weight=1.0)])
    rebuilt = profile_mod.apply_edit_signals(
        TasteProfile(user_id="u1"),
        profile_mod._edit_signals([("remove", PLACE_A, 2.0, 2)]))

    assert online.frequent_removals == rebuilt.frequent_removals


# ------------------------------------------------------------------ 그래프 배선
def _plan(names: tuple[str, ...]) -> Itinerary:
    return Itinerary(id="plan-1", items=[
        ItineraryItem(seq=i + 1, name=n, place_id=(PLACE_A if n == "A" else PLACE_B))
        for i, n in enumerate(names)])


async def test_persist_saves_edit_signals(monkeypatch):
    """persist 가 저장하지 않으면 UR-09 는 실행 중 메모리에만 존재한다."""
    from app.graph import nodes

    captured: dict = {}

    async def fake_save_plan_edits(user_id, plan_id, signals):
        captured.update(user_id=user_id, plan_id=plan_id, signals=signals)
        return len(signals)

    monkeypatch.setattr(repo, "save_plan_edits", fake_save_plan_edits)
    monkeypatch.setattr(repo, "save_itinerary", _noop)
    monkeypatch.setattr(nodes, "_learn_from_edits", _noop)

    out = await nodes.persist({
        "user_id": "u1",
        "current_itinerary": _plan(("A", "B")),
        "itinerary": _plan(("A",)),              # B 를 지웠다
    })

    assert captured["plan_id"] == "plan-1"
    assert [s.action for s in captured["signals"]] == ["remove"]
    assert captured["signals"][0].from_place_id == PLACE_B
    assert out["edit_signals"]


async def test_cold_start_user_still_keeps_the_edit(monkeypatch):
    """프로필 행이 없다고 첫 수정 행동을 버리면, 아카이브가 빈 사용자는 영영 못 배운다."""
    from app.graph import nodes

    rebuilt = TasteProfile(user_id="u1", frequent_removals={PLACE_B: 1.0})
    saved: dict = {}

    async def fake_load_profile(user_id):
        return None

    async def fake_rebuild_profile(user_id):
        return rebuilt

    async def fake_save_profile(user_id, profile):
        saved["profile"] = profile

    monkeypatch.setattr(profile_mod, "load_profile", fake_load_profile)
    monkeypatch.setattr(profile_mod, "rebuild_profile", fake_rebuild_profile)
    monkeypatch.setattr(profile_mod, "save_profile", fake_save_profile)

    await nodes._learn_from_edits("u1", [
        EditSignal(action="remove", from_place_id=PLACE_B, signal="s", weight=1.0)])

    # 저장한 신호를 재집계가 이미 담고 있다. 여기서 또 더하면 두 번 세는 것이다.
    assert saved["profile"].frequent_removals == {PLACE_B: 1.0}


# --------------------------------------------------------------- 확정 카드 경로
def _card_state(action: str, place_id: str = PLACE_A, **payload):
    from app.schemas import Advisory, Decision, Option

    opt = Option(label=f"라벨:{action}", action=action,
                 payload={"place_id": place_id, **payload})
    adv = Advisory(kind="friction", title="t", message="m", options=[opt])
    return {"advisories": [adv],
            "decisions": [Decision(advisory_id=adv.id, option_id=opt.id)]}, adv, opt


def test_confirm_card_choice_becomes_an_edit_signal():
    """첫 일정에서 카드로 장소를 빼면 diff 는 아무것도 못 본다 — 비교 대상이 없다.

    사용자가 가장 분명하게 «싫다»고 말한 순간이라 여기서 놓치면 남는 기록이 없다.
    """
    from app.graph import nodes

    state, _, _ = _card_state("drop")
    signals = nodes._decision_signals(state)

    assert [s.action for s in signals] == ["remove"]
    assert signals[0].from_place_id == PLACE_A
    assert signals[0].weight == 1.0


def test_decision_signal_id_is_stable_across_turns():
    """같은 결정은 몇 번을 지나가도 같은 id 여야 저장 쿼리가 중복을 거른다."""
    from app.graph import nodes

    state, adv, opt = _card_state("replace", to_place_id=PLACE_B)
    first = nodes._decision_signals(state)
    second = nodes._decision_signals(state)

    assert first[0].id == second[0].id == f"dec-{adv.id}-{opt.id}"
    assert first[0].to_place_id == PLACE_B


def test_keep_and_unmapped_actions_leave_no_signal():
    """«유지»는 수정이 아니다. 남기면 아무것도 안 한 사용자가 취향을 갖게 된다."""
    from app.graph import nodes

    for action in ("keep", "add_parking", "add_place", "shift_time"):
        state, _, _ = _card_state(action)
        assert nodes._decision_signals(state) == []


def test_card_and_diff_do_not_count_the_same_removal_twice():
    """수정 요청에서는 카드와 diff 가 같은 사건을 본다. 둘 다 남기면 가중치가 두 배가 된다."""
    from app.graph import nodes
    from app.schemas import EditSignal as ES

    cards = [ES(action="remove", from_place_id=PLACE_A, signal="card")]
    diff = [ES(action="remove", from_place_id=PLACE_A, signal="diff"),
            ES(action="remove", from_place_id=PLACE_B, signal="diff")]

    merged = nodes._merge_signals(cards, diff)
    assert [(s.action, s.from_place_id) for s in merged] == [
        ("remove", PLACE_A), ("remove", PLACE_B)]


def test_diff_signal_id_is_stable_for_the_same_plan():
    """같은 일정을 두 번 저장해도 같은 삭제가 두 번 쌓이면 안 된다."""
    from app.graph import nodes
    from app.memory.writer import extract_edit_signals

    before, after = _plan(("A", "B")), _plan(("A",))
    first = nodes._stamped(after, extract_edit_signals(before, after))
    second = nodes._stamped(after, extract_edit_signals(before, after))

    assert [s.id for s in first] == [s.id for s in second]
    assert first[0].id.startswith("plan-1:remove:")
