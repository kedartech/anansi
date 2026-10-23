"""Canary scan over outbound text (build plan Layer 5; spec §12).

Covert facts in fixtures carry a unique ``canary`` string. After a tick, every
narration and briefing is checked for canaries its recipient should not know.
Pure: takes the state and the events, returns the hits. Zero tolerance in tests.

Layer 9b adds the human boundary (spec §7): the opacity boundary is the
person, not the seat, so a person's check is against the union of what all
their seats may know, over everything that person can read.

Realism adds two outbound texts, documents and counsel, and one kind of
secret: a canary on a document's ``truth_refs``, hidden from every seat,
because truth never reaches a seat except as an estimate.
"""

from collections.abc import Iterable, Mapping, Sequence

from revisionist.core import events as ev
from revisionist.core._base import Model
from revisionist.core.events import Event
from revisionist.core.ids import DocumentId, EventId, FactId, GoalId, SeatId
from revisionist.core.state import State
from revisionist.core.visibility import Visibility

Secret = FactId | GoalId | DocumentId
"""What a canary marks: a fact, a goal's text, or the truth behind a document."""


class Leak(Model):
    seat_id: SeatId
    fact_id: Secret
    canary: str
    event_id: EventId
    kind: str


def canaries_hidden_from(state: State, seat_id: SeatId) -> dict[str, Secret]:
    """Canary strings on facts and goals whose scope excludes ``seat_id``."""
    return canaries_hidden_from_human(state, (seat_id,))


def scan_text(state: State, seat_id: SeatId, text: str) -> list[tuple[str, Secret]]:
    hidden = canaries_hidden_from(state, seat_id)
    return [(c, fid) for c, fid in hidden.items() if c in text]


def _outbound(state: State, p: ev.Payload) -> list[tuple[SeatId, str]]:
    """Who reads this payload's text, and the text."""
    if isinstance(p, ev.NarrationWritten | ev.CounselHeld):
        return [(p.seat_id, p.text)]
    if isinstance(p, ev.DocumentIssued):
        scope = p.document.scope
        readers = state.seats if scope.visibility is Visibility.PUBLIC else scope.known_by
        text = p.document.model_dump_json()
        return [(seat, text) for seat in sorted(readers)]
    if isinstance(p, ev.DocumentWritten) and p.doc_id in state.documents:
        scope = state.documents[p.doc_id].view.scope
        readers = state.seats if scope.visibility is Visibility.PUBLIC else scope.known_by
        return [(seat, p.prose) for seat in sorted(readers)]
    return []


def scan(state: State, events: Iterable[Event]) -> list[Leak]:
    """Every narration, document and counsel in ``events`` checked against what its
    reader may know in ``state``.

    Briefings carry no text of their own (they point at a narration). A
    document is scanned whole, body and prose, for each seat its scope names;
    prose written later (``document_written``) is scanned the same way.
    """
    leaks: list[Leak] = []
    for e in events:
        for seat, text in _outbound(state, e.payload):
            for canary, fact_id in scan_text(state, seat, text):
                leaks.append(
                    Leak(
                        seat_id=seat,
                        fact_id=fact_id,
                        canary=canary,
                        event_id=e.event_id,
                        kind=e.kind,
                    )
                )
    return leaks


def scan_deliveries(
    state: State, deliveries: Sequence[tuple[SeatId, str]]
) -> list[tuple[SeatId, str, Secret]]:
    """The same check over what a deliverer actually sent."""
    return [
        (seat_id, canary, fact_id)
        for seat_id, text in deliveries
        for canary, fact_id in scan_text(state, seat_id, text)
    ]


# --- the human boundary (Layer 9b) ------------------------------------------------


class HumanLeak(Model):
    human: str
    """Who read it: a Discord user ID, or ``host``."""
    surface: str
    """Where they read it, e.g. ``narration <event id>`` or ``dm``."""
    canary: str
    fact_id: Secret


def canaries_hidden_from_human(state: State, seats: Iterable[SeatId]) -> dict[str, Secret]:
    """Canaries on facts and goals that none of one person's seats may see, and every
    canary on a document's truth."""
    held = frozenset(seats)
    out: dict[str, Secret] = {
        f.canary: f.fact_id
        for f in state.facts.values()
        if f.canary and not any(f.scope.visible_to(s) for s in held)
    }
    for g in state.goals.values():
        if g.canary and not any(g.scope.visible_to(s) for s in held):
            out[g.canary] = g.goal_id
    for d in state.documents.values():
        for ref in d.truth.truth_refs:
            if ref.canary:
                out[ref.canary] = d.view.doc_id
    return out


def surfaces_from_log(
    events: Iterable[Event], holdings: Mapping[str, frozenset[SeatId]]
) -> dict[str, list[tuple[str, str]]]:
    """What each person can read in the log: their seats' narrations, questions,
    counsel and documents, every public narration or document (the press) and
    every statement."""
    seen: dict[str, list[tuple[str, str]]] = {human: [] for human in holdings}
    for e in events:
        p = e.payload
        own: frozenset[SeatId] = frozenset()
        if isinstance(p, ev.NarrationWritten | ev.ClarificationAsked | ev.CounselHeld):
            own = frozenset({p.seat_id})
        text = (
            p.text
            if isinstance(p, ev.NarrationWritten | ev.StatementIssued | ev.CounselHeld)
            else ""
        )
        if isinstance(p, ev.ClarificationAsked):
            text = p.question
        if isinstance(p, ev.DocumentIssued):
            own = p.document.scope.known_by
            text = p.document.model_dump_json()
        if isinstance(p, ev.DocumentWritten):
            own = e.scope.known_by
            text = p.prose
        if not text:
            continue
        public = isinstance(p, ev.StatementIssued) or e.scope.visibility is Visibility.PUBLIC
        for human, seats in holdings.items():
            if public or own & seats:
                seen[human].append((f"{e.kind} {e.event_id}", text))
    return seen


def scan_humans(
    state: State,
    holdings: Mapping[str, frozenset[SeatId]],
    seen: Mapping[str, Sequence[tuple[str, str]]],
) -> list[HumanLeak]:
    """Every text each person read, checked against the union of their seats' knowledge."""
    leaks: list[HumanLeak] = []
    for human, texts in seen.items():
        hidden = canaries_hidden_from_human(state, holdings.get(human, frozenset()))
        for surface, text in texts:
            leaks += [
                HumanLeak(human=human, surface=surface, canary=c, fact_id=fid)
                for c, fid in hidden.items()
                if c in text
            ]
    return leaks


__all__ = [
    "HumanLeak",
    "Leak",
    "canaries_hidden_from",
    "canaries_hidden_from_human",
    "scan",
    "scan_deliveries",
    "scan_humans",
    "scan_text",
    "surfaces_from_log",
]
