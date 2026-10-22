"""Session Zero v3 in the tick: the date moves, contests run, history fires (spec §5.3-5.7)."""

from datetime import date

from revisionist.core import events as ev
from revisionist.core.goals import EntityIs
from revisionist.core.ids import ContestId, ScheduledEventId
from revisionist.core.ontology import (
    Contest,
    ContestKind,
    EntityStatus,
    ScheduledEvent,
    ScheduleSource,
)
from revisionist.core.visibility import PUBLIC
from revisionist.engine.providers import (
    KeywordSafety,
    RecordingDeliverer,
    ScriptedResolver,
    TemplateNarrator,
)
from revisionist.engine.store import MemoryStore, load_state
from revisionist.engine.tick import Providers, run_tick
from tests import fixtures as fx

START = date(1478, 4, 1)
RACE = ContestId("01ARZ3NDEKTSV4RRFFQ69G5KR0")
DUE = ScheduledEventId("01ARZ3NDEKTSV4RRFFQ69G5HD0")
PREVENTED = ScheduledEventId("01ARZ3NDEKTSV4RRFFQ69G5HP0")
LATER = ScheduledEventId("01ARZ3NDEKTSV4RRFFQ69G5HL0")


def _providers() -> Providers:
    return Providers(
        resolver=ScriptedResolver(lambda state, intent, roll: []),
        narrator=TemplateNarrator(),
        deliverer=RecordingDeliverer(),
        safety=KeywordSafety(),
    )


def _history(sid: ScheduledEventId, on: date, alive: bool, weight: int = 2) -> ScheduledEvent:
    who = fx.E_NORTH if alive else fx.E_SOUTH
    status = EntityStatus.ACTIVE if alive else EntityStatus.DEAD
    return ScheduledEvent(
        scheduled_id=sid,
        fire_at_tick=0,
        name=f"event {sid[-2:]}",
        scope=PUBLIC,
        source=ScheduleSource.HISTORY,
        on_date=on,
        condition=EntityIs(check="entity_is", entity=who, status=status),
        weight=weight,
    )


def _store(*payloads: ev.Payload) -> MemoryStore:
    store = MemoryStore()
    base = fx.creation_events()
    store.append_all(base)
    if payloads:
        store.append_all([fx.envelope(len(base) + i, p) for i, p in enumerate(payloads)])
    return store


def _dated() -> MemoryStore:
    race = Contest(
        contest_id=RACE,
        kind=ContestKind.RIVALRY,
        name="the succession",
        prize="the crown",
        sides=(fx.E_NORTH, fx.E_SOUTH),
        track=10,
        momentum=7,
        countdown_days=10,
        scope=PUBLIC,
    )
    return _store(
        ev.DateAdvanced(to=START),
        ev.ContestCreated(contest=race),
        ev.ScheduledEventCreated(scheduled=_history(DUE, date(1478, 4, 3), alive=True)),
        ev.ScheduledEventCreated(scheduled=_history(PREVENTED, date(1478, 4, 4), alive=False)),
        ev.ScheduledEventCreated(scheduled=_history(LATER, date(1479, 1, 1), alive=True)),
    )


def test_a_dated_world_moves_its_date_contests_and_history_each_tick() -> None:
    store = _dated()
    span = load_state(store, fx.CAMPAIGN).settings.world_days_per_tick
    assert span >= 4
    run_tick(store, fx.CAMPAIGN, _providers())
    state = load_state(store, fx.CAMPAIGN)
    assert state.in_world_date is not None and state.in_world_date > START
    race = state.contests[RACE]
    assert race.track > 10 or race.decided
    assert state.scheduled[DUE].fired and not state.scheduled[DUE].skipped
    assert state.scheduled[PREVENTED].skipped
    assert not state.scheduled[LATER].fired and not state.scheduled[LATER].skipped
    assert state.divergence == 2


def test_a_contest_is_decided_when_its_countdown_runs_out() -> None:
    store = _dated()
    for _ in range(5):
        run_tick(store, fx.CAMPAIGN, _providers())
        if load_state(store, fx.CAMPAIGN).contests[RACE].decided:
            break
    race = load_state(store, fx.CAMPAIGN).contests[RACE]
    assert race.decided and race.winner == fx.E_NORTH and race.countdown_days == 0


def test_an_undated_world_gets_no_date_contest_or_history_events() -> None:
    store = _store()
    run_tick(store, fx.CAMPAIGN, _providers())
    kinds = {e.kind for e in store.read(fx.CAMPAIGN)}
    assert not kinds & {
        "date_advanced",
        "contest_moved",
        "contest_decided",
        "scheduled_event_skipped",
        "divergence_changed",
    }
