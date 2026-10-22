"""History's clock and divergence (spec §5.4). Phase 2 unit.

On each tick, history events due by today's in-world date fire if their
condition still holds and are skipped otherwise; a skip raises divergence by
the event's weight.
"""

from collections.abc import Iterable
from datetime import date

from revisionist.core import events as ev
from revisionist.core.goals import goal_met
from revisionist.core.ontology import ScheduledEvent, ScheduleSource
from revisionist.core.state import State, View


def _pending(events: Iterable[ScheduledEvent]) -> list[ScheduledEvent]:
    """Unsettled history events, earliest first, ties broken by id."""
    pending = [
        s
        for s in events
        if s.source is ScheduleSource.HISTORY
        and not s.fired
        and not s.skipped
        and s.on_date is not None
    ]
    return sorted(pending, key=lambda s: (s.on_date, s.scheduled_id))


def due(state: State, today: date) -> tuple[ScheduledEvent, ...]:
    """Pending history events with ``on_date`` on or before ``today``, in date order."""
    return tuple(s for s in _pending(state.scheduled.values()) if s.on_date and s.on_date <= today)


def settle(
    state: State, today: date
) -> list[ev.ScheduledEventFired | ev.ScheduledEventSkipped | ev.DivergenceChanged]:
    """Fire or skip every due event, with the divergence each skip adds.

    Conditions are read from ``state`` as given, not from the events this batch
    produces, so the result does not depend on the order of the due events.
    """
    out: list[ev.ScheduledEventFired | ev.ScheduledEventSkipped | ev.DivergenceChanged] = []
    for s in due(state, today):
        if s.condition is None or goal_met(state, s.condition):
            out.append(ev.ScheduledEventFired(scheduled_id=s.scheduled_id))
            continue
        out.append(ev.ScheduledEventSkipped(scheduled_id=s.scheduled_id))
        out.append(ev.DivergenceChanged(change=s.weight, reason=f"{s.name} did not happen"[:200]))
    return out


def next_due(world: State | View, today: date | None) -> ScheduledEvent | None:
    """The earliest pending history event visible in ``world``, on or after
    ``today`` (any date when None). For the deputy context and the calendar."""
    upcoming = [
        s
        for s in _pending(world.scheduled.values())
        if today is None or (s.on_date and s.on_date >= today)
    ]
    return upcoming[0] if upcoming else None
