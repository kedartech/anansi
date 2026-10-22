"""Session Zero v3, Phase 1: the contract the Phase 2 units build on (spec §5)."""

from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from revisionist.core import events as ev
from revisionist.core.fold import FoldError, apply, replay, state_hash
from revisionist.core.goals import EntityIs
from revisionist.core.ids import ContestId, EntityId, ScheduledEventId
from revisionist.core.ontology import (
    Contest,
    ContestKind,
    EntityStatus,
    ScheduledEvent,
    ScheduleSource,
)
from revisionist.core.settings import Settings
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC
from revisionist.core.world import WorldFile
from revisionist.core.world_compile import WorldError, compile_world
from revisionist.engine import session_zero as sz
from tests import fixtures as fx

WAR = ContestId("01ARZ3NDEKTSV4RRFFQ69G5KW0")
FAMINE = ContestId("01ARZ3NDEKTSV4RRFFQ69G5KF0")
SALT_STRAIT = Path(__file__).resolve().parents[1] / "worlds" / "salt-strait.yaml"
COUP = ScheduledEventId("01ARZ3NDEKTSV4RRFFQ69G5HC0")


def _war(**kw: object) -> Contest:
    base: dict[str, object] = dict(
        contest_id=WAR,
        kind=ContestKind.RIVALRY,
        name="The Strait War",
        prize="the strait",
        sides=(fx.E_NORTH, fx.E_SOUTH),
        track=10,
        momentum=-5,
        countdown_days=90,
        scope=PUBLIC,
    )
    return Contest.model_validate(base | kw)


def _coup(**kw: object) -> ScheduledEvent:
    base: dict[str, object] = dict(
        scheduled_id=COUP,
        fire_at_tick=4,
        name="The guard deposes the south",
        scope=PUBLIC,
        source=ScheduleSource.HISTORY,
        on_date=date(1478, 4, 26),
        condition=EntityIs(check="entity_is", entity=fx.E_SOUTH, status=EntityStatus.ACTIVE),
        weight=2,
    )
    return ScheduledEvent.model_validate(base | kw)


def _built(*payloads: ev.Payload) -> State:
    base = fx.creation_events()
    extra = [fx.envelope(len(base) + i, p) for i, p in enumerate(payloads)]
    return replay([*base, *extra])


# --- Contest shape ----------------------------------------------------------


def test_a_rivalry_needs_two_distinct_sides() -> None:
    with pytest.raises(ValidationError, match="at least two sides"):
        _war(sides=(fx.E_NORTH,))
    with pytest.raises(ValidationError, match="each side once"):
        _war(sides=(fx.E_NORTH, fx.E_NORTH))


def test_a_trial_has_one_power_and_a_non_negative_track() -> None:
    trial = _war(contest_id=FAMINE, kind=ContestKind.TRIAL, sides=(fx.E_NORTH,), track=0)
    assert trial.floor == 0
    with pytest.raises(ValidationError, match="one power"):
        _war(kind=ContestKind.TRIAL, track=0)
    with pytest.raises(ValidationError, match=r"0\.\.100"):
        _war(kind=ContestKind.TRIAL, sides=(fx.E_NORTH,), track=-1)


def test_only_a_decided_contest_has_a_winner_and_it_is_a_side() -> None:
    with pytest.raises(ValidationError, match="only a decided"):
        _war(winner=fx.E_NORTH)
    with pytest.raises(ValidationError, match="must be a side"):
        _war(decided=True, winner=fx.E_GUILD)


# --- ScheduledEvent: play vs history ---------------------------------------


def test_a_history_event_has_a_date_and_a_play_event_does_not() -> None:
    assert _coup().on_date == date(1478, 4, 26)
    with pytest.raises(ValidationError, match="has a date"):
        _coup(on_date=None)
    with pytest.raises(ValidationError, match="has a date"):
        _coup(source=ScheduleSource.PLAY, condition=None)


def test_an_old_scheduled_event_loads_as_a_play_event() -> None:
    old = {
        "scheduled_id": COUP,
        "fire_at_tick": 3,
        "name": "x",
        "scope": PUBLIC.model_dump(),
        "fired": False,
    }
    s = ScheduledEvent.model_validate(old)
    assert s.source is ScheduleSource.PLAY
    assert s.on_date is None and s.condition is None and not s.skipped


# --- Fold -------------------------------------------------------------------


def test_contests_fold_create_move_clamp_and_decide() -> None:
    state = _built(
        ev.ContestCreated(contest=_war()),
        ev.ContestMoved(contest_id=WAR, change=-200, days=30),
    )
    war = state.contests[WAR]
    assert (war.track, war.countdown_days) == (-100, 60)
    state = apply(
        state,
        fx.envelope(state.last_seq + 1, ev.ContestDecided(contest_id=WAR, winner=fx.E_SOUTH)),
    )
    assert state.contests[WAR].decided and state.contests[WAR].winner == fx.E_SOUTH
    with pytest.raises(FoldError, match="is decided"):
        apply(state, fx.envelope(state.last_seq + 1, ev.ContestMoved(contest_id=WAR, change=1)))


def test_a_contest_side_must_be_a_known_entity() -> None:
    ghost = EntityId("01ARZ3NDEKTSV4RRFFQ69G5ZZ0")
    with pytest.raises(FoldError):
        _built(ev.ContestCreated(contest=_war(sides=(fx.E_NORTH, ghost))))


def test_a_trial_track_never_drops_below_zero() -> None:
    trial = _war(contest_id=FAMINE, kind=ContestKind.TRIAL, sides=(fx.E_NORTH,), track=5)
    state = _built(ev.ContestCreated(contest=trial), ev.ContestMoved(contest_id=FAMINE, change=-50))
    assert state.contests[FAMINE].track == 0


def test_history_events_fire_or_skip_once_and_skips_feed_divergence() -> None:
    state = _built(
        ev.ScheduledEventCreated(scheduled=_coup()),
        ev.ScheduledEventSkipped(scheduled_id=COUP),
        ev.DivergenceChanged(change=2, reason="the coup did not happen"),
    )
    assert state.scheduled[COUP].skipped and state.divergence == 2
    for payload in (
        ev.ScheduledEventFired(scheduled_id=COUP),
        ev.ScheduledEventSkipped(scheduled_id=COUP),
    ):
        with pytest.raises(FoldError):
            apply(state, fx.envelope(state.last_seq + 1, payload))


def test_divergence_floors_at_zero_and_the_date_never_runs_backwards() -> None:
    state = _built(
        ev.DivergenceChanged(change=-5, reason="x"),
        ev.DateAdvanced(to=date(1478, 4, 1)),
        ev.DateAdvanced(to=date(1478, 4, 8)),
    )
    assert state.divergence == 0 and state.in_world_date == date(1478, 4, 8)
    with pytest.raises(FoldError, match="backwards"):
        apply(state, fx.envelope(state.last_seq + 1, ev.DateAdvanced(to=date(1478, 4, 7))))


def test_an_old_campaign_replays_to_the_same_hash() -> None:
    """The new State fields are late fields: a log with none of the v3 events
    hashes exactly as it did before schema 10."""
    state = replay(fx.creation_events())
    assert state.contests == {} and state.divergence == 0 and state.in_world_date is None
    assert "contests" not in state.model_dump(exclude_defaults=True)
    assert state_hash(state) == state_hash(replay(fx.creation_events()))


# --- World file and draft ---------------------------------------------------


def test_v3_world_sections_are_refused_until_phase_2_compiles_them() -> None:
    raw: dict[str, object] = yaml.safe_load(SALT_STRAIT.read_text())
    raw["contests"] = [
        {
            "key": "war",
            "kind": "rivalry",
            "name": "War",
            "prize": "p",
            "sides": ["a", "b"],
            "countdown_days": 30,
        }
    ]
    world = WorldFile.model_validate(raw)
    with pytest.raises(WorldError) as info:
        compile_world(world, fx.CAMPAIGN, fx.VERSIONS)
    assert any("contests: not compiled yet" in p for p in info.value.problems)


def test_the_draft_schema_hides_v3_sections_until_phase_2() -> None:
    props = sz.DRAFT_SCHEMA["properties"]
    assert {"start_date", "contests", "schedule"}.isdisjoint(props)
    assert "sides" not in sz.DRAFT_SCHEMA["$defs"]["CharacterDecl"]["properties"]
    assert {"ContestDecl", "HistoryDecl", "SideDecl"}.isdisjoint(sz.DRAFT_SCHEMA["$defs"])
    assert {"start_date", "contests", "schedule"} <= set(
        WorldFile.model_json_schema()["properties"]
    )


def test_establish_passes_effort_to_the_model() -> None:
    req = sz._request("Florence, April 1478", Settings(), (), effort=1)
    assert req.effort == 1
    assert sz._request("Florence, April 1478", Settings(), ()).effort is None
