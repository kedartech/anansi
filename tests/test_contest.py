"""Contests: momentum, decisions and the odds edge (spec §5.3)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from revisionist.core import contest
from revisionist.core import events as ev
from revisionist.core.fold import apply, replay
from revisionist.core.ids import AssetId, ContestId, EntityId, IntentId, SeatId
from revisionist.core.intent import Intent, Verb
from revisionist.core.ontology import (
    TRACK_MAX,
    AssetStatus,
    Contest,
    ContestKind,
)
from revisionist.core.project import project
from revisionist.core.referee import FORCE_VERBS, INFO_VERBS
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC
from tests import fixtures as fx

WAR = ContestId("01ARZ3NDEKTSV4RRFFQ69G5CW0")
PEACE = ContestId("01ARZ3NDEKTSV4RRFFQ69G5CP0")
TRIAL = ContestId("01ARZ3NDEKTSV4RRFFQ69G5CT0")


def _contest(
    cid: ContestId = WAR,
    *,
    kind: ContestKind = ContestKind.RIVALRY,
    sides: tuple[EntityId, ...] = (fx.E_NORTH, fx.E_SOUTH),
    track: int = 0,
    momentum: int = 0,
    countdown: int = 30,
) -> Contest:
    return Contest(
        contest_id=cid,
        kind=kind,
        name="War",
        prize="the strait",
        sides=sides,
        track=track,
        momentum=momentum,
        countdown_days=countdown,
        scope=PUBLIC,
    )


def _with(*contests: Contest) -> State:
    state = replay(fx.creation_events())
    for k in contests:
        state = apply(state, fx.envelope(state.last_seq + 1, ev.ContestCreated(contest=k)))
    return state


def _fold(state: State, payload: ev.Payload) -> State:
    return apply(state, fx.envelope(state.last_seq + 1, payload))


def _asset(state: State, asset_id: AssetId, **update: object) -> State:
    asset = state.assets[asset_id]
    return state.model_copy(
        update={"assets": {**state.assets, asset_id: asset.model_copy(update=update)}}
    )


def _order(verb: Verb, actor: SeatId = fx.NORTH) -> Intent:
    return Intent(
        intent_id=IntentId("01ARZ3NDEKTSV4RRFFQ69I00000"),
        actor=actor,
        verb=verb,
        scope=PUBLIC,
        filed_tick=0,
    )


@pytest.mark.parametrize(
    ("momentum", "days", "change"),
    [(3, 7, 3), (3, 3, 1), (-3, 3, -1), (2, 3, 1), (1, 3, 0), (20, 14, 40), (-20, 1, -3)],
)
def test_momentum_is_scaled_per_seven_days_and_rounded(
    momentum: int, days: int, change: int
) -> None:
    (move,) = contest.momentum_moves(_with(_contest(momentum=momentum)), days)
    assert move == ev.ContestMoved(contest_id=WAR, change=change, days=days, cause="momentum")


def test_momentum_is_clamped_to_the_track() -> None:
    rivalry = _with(_contest(track=90, momentum=20))
    (up,) = contest.momentum_moves(rivalry, 14)
    assert up.change == TRACK_MAX - 90
    assert _fold(rivalry, up).contests[WAR].track == TRACK_MAX

    low = _with(_contest(track=-95, momentum=-20))
    (down,) = contest.momentum_moves(low, 14)
    assert down.change == -5
    assert _fold(low, down).contests[WAR].track == -100

    trial = _with(
        _contest(TRIAL, kind=ContestKind.TRIAL, sides=(fx.E_NORTH,), track=5, momentum=-20)
    )
    (drop,) = contest.momentum_moves(trial, 14)
    assert drop.change == -5
    assert _fold(trial, drop).contests[TRIAL].track == 0


def test_countdown_is_capped_at_the_days_left() -> None:
    state = _with(_contest(countdown=4, momentum=1))
    (move,) = contest.momentum_moves(state, 10)
    assert move.days == 4
    assert _fold(state, move).contests[WAR].countdown_days == 0


def test_decided_contests_and_zero_days_emit_nothing() -> None:
    state = _with(_contest(countdown=0))
    decided = _fold(state, ev.ContestDecided(contest_id=WAR, winner=None))
    assert contest.momentum_moves(decided, 7) == []
    assert contest.momentum_moves(state, 0) == []
    assert contest.momentum_moves(state, -3) == []


def test_zero_change_still_runs_the_countdown() -> None:
    state = _with(_contest(momentum=0, countdown=9))
    (move,) = contest.momentum_moves(state, 5)
    assert move.change == 0 and move.days == 5


def test_moves_come_in_contest_id_order() -> None:
    state = _with(_contest(WAR, momentum=1), _contest(PEACE, momentum=-1))
    assert [m.contest_id for m in contest.momentum_moves(state, 7)] == [PEACE, WAR]


def test_decisions_wait_for_the_countdown() -> None:
    assert contest.decisions(_with(_contest(countdown=1))) == []


def test_rivalry_decided_by_the_track_side() -> None:
    def winner(track: int) -> EntityId | None:
        (d,) = contest.decisions(_with(_contest(track=track, countdown=0)))
        assert d.contest_id == WAR
        return d.winner

    assert winner(0) is None
    assert winner(10) == fx.E_NORTH
    assert winner(-10) == fx.E_SOUTH


def test_a_three_side_rivalry_has_no_leader_below_zero() -> None:
    sides = (fx.E_NORTH, fx.E_SOUTH, fx.E_GUILD)
    below = contest.decisions(_with(_contest(sides=sides, track=-10, countdown=0)))
    above = contest.decisions(_with(_contest(sides=sides, track=10, countdown=0)))
    assert below[0].winner is None
    assert above[0].winner == fx.E_NORTH


def test_trial_passes_only_at_the_top() -> None:
    def winner(track: int) -> EntityId | None:
        k = _contest(TRIAL, kind=ContestKind.TRIAL, sides=(fx.E_NORTH,), track=track, countdown=0)
        (d,) = contest.decisions(_with(k))
        return d.winner

    assert winner(TRACK_MAX) == fx.E_NORTH
    assert winner(99) is None


def test_decided_contests_are_not_decided_again() -> None:
    state = _with(_contest(track=10, countdown=0))
    state = _fold(state, ev.ContestDecided(contest_id=WAR, winner=fx.E_NORTH))
    assert contest.decisions(state) == []


def test_stronger_side_gets_a_positive_edge_and_weaker_a_negative() -> None:
    # North: army 2 + hill irregulars 1 = 3. South: guard lost, so 0.
    state = _asset(_with(_contest()), fx.A_SOUTH_GUARD, status=AssetStatus.LOST)
    assert contest.edge(state, _order(Verb.ATTACK)) == 1
    assert contest.edge(state, _order(Verb.ATTACK, fx.SOUTH)) == -1


def test_edge_is_clamped_to_two_steps() -> None:
    state = _asset(_with(_contest()), fx.A_NORTH_ARMY, tier=5)
    state = _asset(state, fx.A_SOUTH_GUARD, status=AssetStatus.LOST)
    assert contest.edge(state, _order(Verb.ATTACK)) == 2
    assert contest.edge(state, _order(Verb.ATTACK, fx.SOUTH)) == -2


def test_degraded_assets_count_half() -> None:
    # North: degraded army 2 // 2 = 1, plus 1 = 2. South: 3. (2 - 3) // 3 = -1.
    state = _asset(_with(_contest()), fx.A_NORTH_ARMY, status=AssetStatus.DEGRADED)
    assert contest.edge(state, _order(Verb.ATTACK)) == -1


def test_equal_strength_at_level_track_gives_no_edge() -> None:
    assert contest.edge(_with(_contest()), _order(Verb.ATTACK)) == 0


def test_track_favour_adds_or_takes_a_step() -> None:
    state = _with(_contest(track=60))
    assert contest.edge(state, _order(Verb.NEGOTIATE)) == 1
    assert contest.edge(state, _order(Verb.NEGOTIATE, fx.SOUTH)) == -1

    state = _with(_contest(track=-60))
    assert contest.edge(state, _order(Verb.NEGOTIATE)) == -1
    assert contest.edge(state, _order(Verb.NEGOTIATE, fx.SOUTH)) == 1


def test_trial_pressure_scales_to_tiers() -> None:
    # North: 3 against pressure 100 // 20 = 5: (3 - 5) // 3 = -1, then -1 at track 0.
    trial = _contest(TRIAL, kind=ContestKind.TRIAL, sides=(fx.E_NORTH,), track=0)
    assert contest.edge(_with(trial), _order(Verb.ATTACK)) == -2
    # Pressure at track 80 is 20 // 20 = 1, so (3 - 1) // 3 = 0, then +1 for a trial at 75 or above.
    passing = _contest(TRIAL, kind=ContestKind.TRIAL, sides=(fx.E_NORTH,), track=80)
    assert contest.edge(_with(passing), _order(Verb.ATTACK)) == 1


def test_an_unrelated_actor_or_a_travel_order_gets_no_edge() -> None:
    state = _asset(_with(_contest()), fx.A_SOUTH_GUARD, status=AssetStatus.LOST)
    assert contest.edge(state, _order(Verb.ATTACK, fx.GUILD)) == 0
    assert contest.edge(state, _order(Verb.TRAVEL)) == 0


def test_only_force_info_negotiate_and_fund_orders_get_an_edge() -> None:
    state = _asset(_with(_contest()), fx.A_SOUTH_GUARD, status=AssetStatus.LOST)
    edged = FORCE_VERBS | INFO_VERBS | {Verb.NEGOTIATE, Verb.FUND}
    for verb in Verb:
        expected = 1 if verb in edged else 0
        assert contest.edge(state, _order(verb)) == expected, verb


def test_a_view_reads_its_own_seat() -> None:
    state = _asset(_with(_contest()), fx.A_SOUTH_GUARD, status=AssetStatus.LOST)
    assert contest.edge(project(state, fx.NORTH), _order(Verb.ATTACK)) == 1


@given(
    momentum=st.integers(-20, 20),
    track=st.integers(-100, 100),
    days=st.integers(0, 60),
    trial=st.booleans(),
)
def test_every_momentum_move_folds_cleanly(
    momentum: int, track: int, days: int, trial: bool
) -> None:
    if trial:
        k = _contest(
            TRIAL, kind=ContestKind.TRIAL, sides=(fx.E_NORTH,), track=abs(track), momentum=momentum
        )
    else:
        k = _contest(track=track, momentum=momentum)
    state = _with(k)
    for move in contest.momentum_moves(state, days):
        state = _fold(state, move)
    (after,) = state.contests.values()
    assert after.floor <= after.track <= TRACK_MAX
