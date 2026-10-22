"""History's clock and divergence (spec §5.4). Phase 2 unit.

On each tick, history events due by today's in-world date fire if their
condition still holds and are skipped otherwise; a skip raises divergence by
the event's weight.
"""

from datetime import date

from revisionist.core import events as ev
from revisionist.core.ontology import ScheduledEvent
from revisionist.core.state import State, View


def due(state: State, today: date) -> tuple[ScheduledEvent, ...]:
    """Pending history events with ``on_date`` on or before ``today``, in date order."""
    raise NotImplementedError("Session Zero v3, Phase 2: history's clock")


def settle(
    state: State, today: date
) -> list[ev.ScheduledEventFired | ev.ScheduledEventSkipped | ev.DivergenceChanged]:
    """Fire or skip every due event, with the divergence each skip adds."""
    raise NotImplementedError("Session Zero v3, Phase 2: history's clock")


def next_due(world: State | View, today: date | None) -> ScheduledEvent | None:
    """The earliest pending history event visible in ``world``, on or after
    ``today`` (any date when None). For the deputy context and the calendar."""
    raise NotImplementedError("Session Zero v3, Phase 2: history's clock")
