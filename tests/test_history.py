"""History's clock and divergence (spec §5.4)."""

from datetime import date

from revisionist.core import events as ev
from revisionist.core.fold import replay
from revisionist.core.goals import EntityIs
from revisionist.core.history import due, next_due, settle
from revisionist.core.ids import ScheduledEventId
from revisionist.core.ontology import EntityStatus, ScheduledEvent, ScheduleSource
from revisionist.core.project import project
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC, private_to
from tests import fixtures as fx

HOLDS = EntityIs(check="entity_is", entity=fx.E_NORTH, status=EntityStatus.ACTIVE)
FAILS = EntityIs(check="entity_is", entity=fx.E_SOUTH, status=EntityStatus.DEAD)

EARLY = ScheduledEventId("01ARZ3NDEKTSV4RRFFQ69G5HE0")
TIED_LATE_ID = ScheduledEventId("01ARZ3NDEKTSV4RRFFQ69G5HT0")
TIED_EARLY_ID = ScheduledEventId("01ARZ3NDEKTSV4RRFFQ69G5HS0")
LATE = ScheduledEventId("01ARZ3NDEKTSV4RRFFQ69G5HL0")
FUTURE = ScheduledEventId("01ARZ3NDEKTSV4RRFFQ69G5HF0")
PLAY = ScheduledEventId("01ARZ3NDEKTSV4RRFFQ69G5HP0")
FIRED = ScheduledEventId("01ARZ3NDEKTSV4RRFFQ69G5HI0")
SKIPPED = ScheduledEventId("01ARZ3NDEKTSV4RRFFQ69G5HK0")
SECRET = ScheduledEventId("01ARZ3NDEKTSV4RRFFQ69G5HX0")


def _history(sid: ScheduledEventId, on: date, **kw: object) -> ScheduledEvent:
    base: dict[str, object] = dict(
        scheduled_id=sid,
        fire_at_tick=0,
        name=f"event {sid}",
        scope=PUBLIC,
        source=ScheduleSource.HISTORY,
        on_date=on,
        condition=HOLDS,
        weight=1,
    )
    return ScheduledEvent.model_validate(base | kw)


def _built(*payloads: ev.Payload) -> State:
    base = fx.creation_events()
    extra = [fx.envelope(len(base) + i, p) for i, p in enumerate(payloads)]
    return replay([*base, *extra])


def _created(*events: ScheduledEvent) -> list[ev.Payload]:
    return [ev.ScheduledEventCreated(scheduled=s) for s in events]


def test_due_lists_pending_history_events_up_to_today_in_date_order() -> None:
    play = ScheduledEvent(scheduled_id=PLAY, fire_at_tick=0, name="a play event", scope=PUBLIC)
    state = _built(
        *_created(
            _history(LATE, date(1478, 5, 1)),
            _history(TIED_LATE_ID, date(1478, 4, 2)),
            _history(TIED_EARLY_ID, date(1478, 4, 2)),
            _history(EARLY, date(1478, 3, 1)),
            _history(FUTURE, date(1478, 12, 1)),
            _history(FIRED, date(1478, 2, 1)),
            _history(SKIPPED, date(1478, 2, 2)),
            play,
        ),
        ev.ScheduledEventFired(scheduled_id=FIRED),
        ev.ScheduledEventSkipped(scheduled_id=SKIPPED),
        ev.DivergenceChanged(change=1, reason="the skip"),
    )
    got = due(state, date(1478, 5, 1))
    assert [s.scheduled_id for s in got] == [EARLY, TIED_EARLY_ID, TIED_LATE_ID, LATE]


def test_a_holding_event_fires_without_divergence() -> None:
    state = _built(*_created(_history(EARLY, date(1478, 3, 1))))
    assert settle(state, date(1478, 3, 1)) == [ev.ScheduledEventFired(scheduled_id=EARLY)]


def test_a_failing_event_is_skipped_and_adds_its_weight() -> None:
    state = _built(*_created(_history(EARLY, date(1478, 3, 1), condition=FAILS, weight=3)))
    assert settle(state, date(1478, 3, 1)) == [
        ev.ScheduledEventSkipped(scheduled_id=EARLY),
        ev.DivergenceChanged(change=3, reason=f"event {EARLY} did not happen"),
    ]


def test_a_missing_condition_counts_as_holding() -> None:
    state = _built(*_created(_history(EARLY, date(1478, 3, 1), condition=None)))
    assert settle(state, date(1478, 3, 1)) == [ev.ScheduledEventFired(scheduled_id=EARLY)]


def test_a_long_event_name_is_truncated_in_the_divergence_reason() -> None:
    name = "x" * 300
    state = _built(*_created(_history(EARLY, date(1478, 3, 1), condition=FAILS, name=name)))
    [_, divergence] = settle(state, date(1478, 3, 1))
    assert isinstance(divergence, ev.DivergenceChanged)
    assert divergence.reason == f"{name} did not happen"[:200]


def test_settling_applies_cleanly_and_divergence_is_the_sum_of_skips() -> None:
    state = _built(
        *_created(
            _history(EARLY, date(1478, 3, 1), condition=FAILS, weight=2),
            _history(LATE, date(1478, 3, 2)),
            _history(FUTURE, date(1478, 3, 3), condition=FAILS, weight=3),
            _history(FIRED, date(1478, 3, 4), condition=FAILS, weight=1),
        ),
    )
    out = settle(state, date(1478, 3, 3))
    settled = replay(
        [fx.envelope(state.last_seq + 1 + i, p) for i, p in enumerate(out)],
        initial=state,
    )
    assert settled.divergence == 2 + 3
    assert settled.scheduled[EARLY].skipped and settled.scheduled[FUTURE].skipped
    assert settled.scheduled[LATE].fired
    assert not settled.scheduled[FIRED].fired and not settled.scheduled[FIRED].skipped
    assert settle(settled, date(1478, 3, 3)) == []


def test_conditions_are_read_from_the_state_as_given() -> None:
    state = _built(
        *_created(
            _history(EARLY, date(1478, 3, 1), condition=HOLDS),
            _history(LATE, date(1478, 3, 1), condition=FAILS, weight=2),
        ),
    )
    assert settle(state, date(1478, 3, 1)) == [
        ev.ScheduledEventFired(scheduled_id=EARLY),
        ev.ScheduledEventSkipped(scheduled_id=LATE),
        ev.DivergenceChanged(change=2, reason=f"event {LATE} did not happen"),
    ]


def test_next_due_is_the_earliest_pending_history_event_on_or_after_today() -> None:
    state = _built(
        *_created(
            _history(EARLY, date(1478, 3, 1)),
            _history(FUTURE, date(1478, 12, 1)),
            _history(LATE, date(1478, 6, 1)),
            _history(FIRED, date(1478, 2, 1)),
            ScheduledEvent(scheduled_id=PLAY, fire_at_tick=0, name="play", scope=PUBLIC),
        ),
        ev.ScheduledEventFired(scheduled_id=FIRED),
    )
    first = next_due(state, None)
    assert first is not None and first.scheduled_id == EARLY
    later = next_due(state, date(1478, 4, 1))
    assert later is not None and later.scheduled_id == LATE
    assert next_due(state, date(1479, 1, 1)) is None


def test_next_due_only_sees_what_the_seat_can_see() -> None:
    state = _built(
        *_created(
            _history(SECRET, date(1478, 3, 1), scope=private_to(fx.NORTH)),
            _history(LATE, date(1478, 6, 1)),
        ),
    )
    north = next_due(project(state, fx.NORTH), None)
    south = next_due(project(state, fx.SOUTH), None)
    assert north is not None and north.scheduled_id == SECRET
    assert south is not None and south.scheduled_id == LATE
