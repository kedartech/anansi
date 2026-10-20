"""Canary scan over outbound text (build plan Layer 5; README §12).

Covert facts in fixtures carry a unique ``canary`` string. After a tick, every
narration and briefing is checked for canaries its recipient should not know.
Pure: takes the state and the events, returns the hits. Zero tolerance in tests.
"""

from collections.abc import Iterable, Sequence

from revisionist.core import events as ev
from revisionist.core._base import Model
from revisionist.core.events import Event
from revisionist.core.ids import EventId, FactId, SeatId
from revisionist.core.state import State


class Leak(Model):
    seat_id: SeatId
    fact_id: FactId
    canary: str
    event_id: EventId
    kind: str


def canaries_hidden_from(state: State, seat_id: SeatId) -> dict[str, FactId]:
    """Canary strings on facts whose scope excludes ``seat_id``."""
    return {
        f.canary: f.fact_id
        for f in state.facts.values()
        if f.canary and not f.scope.visible_to(seat_id)
    }


def scan_text(state: State, seat_id: SeatId, text: str) -> list[tuple[str, FactId]]:
    hidden = canaries_hidden_from(state, seat_id)
    return [(c, fid) for c, fid in hidden.items() if c in text]


def scan(state: State, events: Iterable[Event]) -> list[Leak]:
    """Every narration in ``events`` checked against what its seat may know in ``state``.

    Briefings carry no text of their own (they point at a narration), so the
    narration is the one place outbound prose exists in the log.
    """
    leaks: list[Leak] = []
    for e in events:
        p = e.payload
        if not isinstance(p, ev.NarrationWritten):
            continue
        for canary, fact_id in scan_text(state, p.seat_id, p.text):
            leaks.append(
                Leak(
                    seat_id=p.seat_id,
                    fact_id=fact_id,
                    canary=canary,
                    event_id=e.event_id,
                    kind=e.kind,
                )
            )
    return leaks


def scan_deliveries(
    state: State, deliveries: Sequence[tuple[SeatId, str]]
) -> list[tuple[SeatId, str, FactId]]:
    """The same check over what a deliverer actually sent."""
    return [
        (seat_id, canary, fact_id)
        for seat_id, text in deliveries
        for canary, fact_id in scan_text(state, seat_id, text)
    ]


__all__ = ["Leak", "canaries_hidden_from", "scan", "scan_deliveries", "scan_text"]
