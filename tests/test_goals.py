"""Layer 10 item 1a: secret goals and win conditions (README §15).

No model decides whether a goal is met: ``goal_met`` does, from State alone.
A Lasting goal stays met whatever happens after; an Undoable one counts only
while it holds.
"""

import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from revisionist.core import delta as d
from revisionist.core import events as ev
from revisionist.core import goals as g
from revisionist.core.canary import canaries_hidden_from
from revisionist.core.fold import FoldError, replay, state_hash
from revisionist.core.ids import AssetId, ClockId, CommitmentId, DeltaId, EntityId, GoalId
from revisionist.core.ontology import Clock, ClockKind, EntityStatus
from revisionist.core.project import project
from revisionist.core.sealing import needs_admin_read, seal_holder
from revisionist.core.state import State
from revisionist.core.visibility import private_to
from revisionist.engine.providers import RecordingDeliverer, ScriptedResolver, TemplateNarrator
from revisionist.engine.store import MemoryStore, load_state
from revisionist.engine.tick import Providers, run_tick
from tests import fixtures as fx
from tests.strategies import conditions

GOAL = GoalId("01ARZ3NDEKTSV4RRFFQ69G0AL0")
GOAL_CLOCK = ClockId("01ARZ3NDEKTSV4RRFFQ69G0AC0")
GOAL_CANARY = "CANARY-NORTH-GOAL-0d4e"

TAKE_GUARD = g.ControlsAsset(holder=fx.E_NORTH, asset=fx.A_SOUTH_GUARD)
STEADY = g.MeterAt(meter=fx.M_NORTH_STABILITY, op="ge", value=0)
AIDE_DEAD = g.EntityIs(entity=fx.E_SOUTH_AIDE, status=EntityStatus.DEAD)


def _goal(condition: g.WinCondition) -> g.Goal:
    return g.Goal(
        goal_id=GOAL,
        seat_id=fx.NORTH,
        text=f"Take what the South holds. {GOAL_CANARY}",
        condition=condition,
        kind=g.goal_kind(condition),
        clock_id=GOAL_CLOCK,
        scope=private_to(fx.NORTH),
        canary=GOAL_CANARY,
    )


def _with_goal(condition: g.WinCondition) -> list[ev.Event]:
    events = fx.creation_events()
    n = len(events)
    clock = Clock(
        clock_id=GOAL_CLOCK,
        kind=ClockKind.OBJECTIVE,
        name="Goal",
        owner=fx.NORTH,
        segments_total=g.parts(condition),
        scope=private_to(fx.NORTH),
    )
    return [
        *events,
        fx.envelope(n, ev.ClockCreated(clock=clock), scope=clock.scope),
        fx.envelope(n + 1, ev.GoalSet(goal=_goal(condition)), scope=private_to(fx.NORTH)),
    ]


def _world() -> State:
    return replay(fx.creation_events())


def _delta(store: MemoryStore, delta: d.Delta) -> None:
    """A change between ticks, as an earlier tick would have applied it."""
    state = load_state(store, fx.CAMPAIGN)
    seq = store.last_seq(fx.CAMPAIGN) + 1
    store.append(fx.envelope(seq, ev.DeltaApplied(intent_id=None, delta=delta), tick=state.tick))


def _tick(store: MemoryStore) -> list[ev.Event]:
    before = store.last_seq(fx.CAMPAIGN)
    run_tick(
        store, fx.CAMPAIGN, Providers(ScriptedResolver(), TemplateNarrator(), RecordingDeliverer())
    )
    return [e for e in store.read(fx.CAMPAIGN) if e.seq > before]


def _goal_kinds(events: list[ev.Event]) -> list[str]:
    return [e.kind for e in events if e.kind.startswith("goal_")]


def _store(condition: g.WinCondition) -> MemoryStore:
    store = MemoryStore()
    store.append_all(_with_goal(condition))
    return store


def _controller(asset: str, holder: str, n: int) -> d.AssetControllerSet:
    return d.AssetControllerSet(
        delta_id=DeltaId(f"01ARZ3NDEKTSV4RRFFQ69G0D{n:02d}"),
        reason="test",
        asset_id=AssetId(asset),
        controller=EntityId(holder),
    )


def _status(entity: str, status: EntityStatus, n: int) -> d.EntityStatusSet:
    return d.EntityStatusSet(
        delta_id=DeltaId(f"01ARZ3NDEKTSV4RRFFQ69G0E{n:02d}"),
        reason="test",
        entity_id=EntityId(entity),
        status=status,
    )


# --- the checks are pure -----------------------------------------------------------


@given(conditions)
def test_nothing_is_met_in_an_empty_world(c: g.WinCondition) -> None:
    assert not g.goal_met(State(campaign_id=fx.CAMPAIGN), c)
    assert g.progress(State(campaign_id=fx.CAMPAIGN), c) == 0


@given(conditions)
def test_a_check_depends_only_on_state(c: g.WinCondition) -> None:
    state = _world()
    again = State.model_validate_json(state.model_dump_json())
    assert g.goal_met(state, c) == g.goal_met(again, c) == g.goal_met(state, c)
    assert g.progress(state, c) == g.progress(again, c)
    assert 0 <= g.progress(state, c) <= g.parts(c)


@given(st.lists(conditions, min_size=1, max_size=3))
def test_all_of_and_any_of_are_and_and_or(cs: list[g.WinCondition]) -> None:
    state = _world()
    met = [g.goal_met(state, c) for c in cs]
    assert g.goal_met(state, g.AllOf(conditions=tuple(cs))) == all(met)
    assert g.goal_met(state, g.AnyOf(conditions=tuple(cs))) == any(met)


def test_each_check_reads_its_own_part_of_the_world() -> None:
    state = _world()
    assert g.goal_met(state, g.ControlsAsset(holder=fx.E_SOUTH, asset=fx.A_SOUTH_GUARD))
    assert not g.goal_met(state, TAKE_GUARD)
    assert not g.goal_met(state, g.HoldsOffice(holder=fx.E_SOUTH, office=fx.A_SOUTH_GUARD))
    assert g.goal_met(state, g.EntityIs(entity=fx.E_NORTH, status=EntityStatus.ACTIVE))
    assert g.goal_met(state, STEADY)
    assert not g.goal_met(state, STEADY.model_copy(update={"op": "le", "value": -1}))
    assert g.goal_met(state, g.DispositionAt(relationship=fx.R_NORTH_SOUTH, op="ge", value=-5))
    assert not g.goal_met(state, g.CommitmentInForce(commitment=CommitmentId("X" * 26)))


def test_the_kind_comes_from_a_fixed_table() -> None:
    assert g.goal_kind(AIDE_DEAD) is g.GoalKind.LASTING
    assert g.goal_kind(TAKE_GUARD) is g.GoalKind.UNDOABLE
    assert g.goal_kind(g.AllOf(conditions=(AIDE_DEAD, AIDE_DEAD))) is g.GoalKind.LASTING
    assert g.goal_kind(g.AnyOf(conditions=(AIDE_DEAD, TAKE_GUARD))) is g.GoalKind.UNDOABLE
    with pytest.raises(ValidationError, match="kind comes from its condition"):
        g.Goal.model_validate({**_goal(TAKE_GUARD).model_dump(), "kind": "lasting"})


def test_a_condition_is_bounded() -> None:
    deep: g.WinCondition = TAKE_GUARD
    for _ in range(g.MAX_DEPTH):
        deep = g.AllOf(conditions=(deep,))
    with pytest.raises(ValidationError, match="nests at most"):
        _goal(deep)


# --- goals in the log --------------------------------------------------------------


def test_an_undoable_goal_met_then_reversed_does_not_win() -> None:
    store = _store(TAKE_GUARD)
    _delta(store, _controller(fx.A_SOUTH_GUARD, fx.E_NORTH, 1))
    assert _goal_kinds(_tick(store)) == ["goal_progressed", "goal_met"]
    state = load_state(store, fx.CAMPAIGN)
    assert state.goals[GOAL].met_tick == 0 and g.season_winners(state) == {fx.NORTH}

    _delta(store, _controller(fx.A_SOUTH_GUARD, fx.E_SOUTH, 2))
    assert _goal_kinds(_tick(store)) == ["goal_progressed", "goal_undone"]
    state = load_state(store, fx.CAMPAIGN)
    assert not state.goals[GOAL].holding and g.season_winners(state) == frozenset()
    assert state.goals[GOAL].met_tick == 0  # the record of when it first held stays

    _delta(store, _controller(fx.A_SOUTH_GUARD, fx.E_NORTH, 3))
    assert _goal_kinds(_tick(store)) == ["goal_progressed", "goal_met"]
    assert g.season_winners(load_state(store, fx.CAMPAIGN)) == {fx.NORTH}


def test_a_lasting_goal_met_then_reversed_still_wins() -> None:
    store = _store(AIDE_DEAD)
    _delta(store, _status(fx.E_SOUTH_AIDE, EntityStatus.DEAD, 1))
    assert _goal_kinds(_tick(store)) == ["goal_progressed", "goal_met"]
    _delta(store, _status(fx.E_SOUTH_AIDE, EntityStatus.ACTIVE, 2))
    assert _goal_kinds(_tick(store)) == []
    assert g.season_winners(load_state(store, fx.CAMPAIGN)) == {fx.NORTH}


def test_progress_moves_the_objective_clock() -> None:
    store = _store(g.AllOf(conditions=(STEADY, TAKE_GUARD, AIDE_DEAD)))
    assert load_state(store, fx.CAMPAIGN).clocks[GOAL_CLOCK].segments_total == 3
    _tick(store)
    assert load_state(store, fx.CAMPAIGN).clocks[GOAL_CLOCK].segments_filled == 1
    _delta(store, _controller(fx.A_SOUTH_GUARD, fx.E_NORTH, 1))
    events = _tick(store)
    assert _goal_kinds(events) == ["goal_progressed"]  # 2 of 3 is not met
    assert all(e.scope == private_to(fx.NORTH) for e in events if e.kind.startswith("goal_"))
    assert load_state(store, fx.CAMPAIGN).clocks[GOAL_CLOCK].segments_filled == 2


def test_nothing_is_logged_when_nothing_changes() -> None:
    store = _store(TAKE_GUARD)
    assert _goal_kinds(_tick(store)) == []
    assert _goal_kinds(_tick(store)) == []


def test_the_fold_refuses_bad_goal_events() -> None:
    events = _with_goal(TAKE_GUARD)
    n = len(events)
    with pytest.raises(FoldError, match="lasting and cannot be undone"):
        lasting = _with_goal(AIDE_DEAD)
        replay([*lasting, fx.envelope(n, ev.GoalUndone(goal_id=GOAL))])
    with pytest.raises(FoldError, match="already has a goal"):
        other = _goal(TAKE_GUARD).model_copy(
            update={"goal_id": GoalId("01ARZ3NDEKTSV4RRFFQ69G0AL1")}
        )
        replay([*events, fx.envelope(n, ev.GoalSet(goal=other))])
    with pytest.raises(FoldError, match="not its objective"):
        stray = _goal(TAKE_GUARD).model_copy(
            update={"goal_id": GoalId("01ARZ3NDEKTSV4RRFFQ69G0AL1"), "seat_id": fx.SOUTH}
        )
        replay([*events, fx.envelope(n, ev.GoalSet(goal=stray))])


# --- secrecy -----------------------------------------------------------------------


def test_a_seat_sees_only_its_own_goal() -> None:
    state = replay(_with_goal(TAKE_GUARD))
    assert list(project(state, fx.NORTH).goals) == [GOAL]
    for seat in (fx.SOUTH, fx.GUILD, fx.HERALD):
        view = project(state, seat)
        assert view.goals == {} and GOAL_CANARY not in view.model_dump_json()
        assert GOAL_CANARY in canaries_hidden_from(state, seat)
    assert GOAL_CANARY not in canaries_hidden_from(state, fx.NORTH)


def test_goal_records_take_the_campaign_key_and_count_as_admin_reads() -> None:
    goal_set = _with_goal(TAKE_GUARD)[-1]
    assert seal_holder(goal_set) is None  # erasing a seat key must keep the game replayable
    assert needs_admin_read(goal_set)


# --- older logs and recordings are untouched ---------------------------------------


def test_a_state_without_goals_hashes_and_serialises_as_before() -> None:
    """Hashes computed on the Layer 9b build (schema 5), before goals existed."""
    store = MemoryStore()
    store.append_all(fx.creation_events())
    assert state_hash(replay(fx.creation_events())) == "2bea5cd31efcb64b979771db0a621a21"
    run_tick(
        store, fx.CAMPAIGN, Providers(ScriptedResolver(), TemplateNarrator(), RecordingDeliverer())
    )
    state = load_state(store, fx.CAMPAIGN)
    assert state_hash(state) == "fa408b72a7ba064f351d0e34a05c1af7"
    view = project(state, fx.NORTH).model_dump_json()
    assert '"goals":{' not in view  # the prompts that embed a view are unchanged
    assert hashlib.blake2b(view.encode(), digest_size=16).hexdigest() == (
        "67c49c959505b8c9437d39533737fcb7"
    )


def test_a_state_with_goals_round_trips() -> None:
    state = replay(_with_goal(g.AnyOf(conditions=(TAKE_GUARD, AIDE_DEAD))))
    assert State.model_validate_json(state.model_dump_json()) == state
    assert '"goals":{' in state.model_dump_json()
