"""The envelope builder and log tail both tick orchestrators share.

``Emitter`` mints dense sequence numbers and folds each batch before it lands, so
a bad batch never reaches the store. ``tail`` reads what the log says about the
tick in progress, which is how either tick resumes after a crash.
"""

from collections.abc import Sequence

from pydantic import Field

from revisionist.core import delta as d
from revisionist.core import events as ev
from revisionist.core import intel, referee
from revisionist.core._base import Model
from revisionist.core.events import CallCost, Event, Versions
from revisionist.core.fold import replay
from revisionist.core.ids import EventId, IntentId, SeatId
from revisionist.core.resolution import TickStage
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC, Scope
from revisionist.engine.ids import new_id
from revisionist.engine.store import EventStore


class Proposal(Model):
    intent_id: IntentId | None
    deltas: tuple[d.Delta, ...]


class Emitter:
    """Builds envelopes with dense sequence numbers and folds them as they land."""

    def __init__(self, store: EventStore, state: State, versions: Versions) -> None:
        self.store = store
        self.state = state
        self.versions = versions
        self.next_seq = state.last_seq + 1
        self.roll_for: referee.Roll | None = None

    def event(
        self, payload: ev.Payload, *, actor: SeatId | None = None, scope: Scope = PUBLIC
    ) -> Event:
        e = Event(
            event_id=EventId(new_id()),
            campaign_id=self.state.campaign_id,
            seq=self.next_seq,
            tick=self.state.tick,
            actor=actor,
            scope=scope,
            payload=payload,
            versions=self.versions,
        )
        self.next_seq += 1
        return e

    def commit(self, batch: Sequence[Event]) -> None:
        """Fold first so a bad batch never reaches the store; then append atomically."""
        folded = replay(batch, initial=self.state)
        self.store.append_all(batch)
        self.state = folded


class Tail(Model):
    """What the log says about the tick in progress."""

    opened: bool = False
    completed: tuple[TickStage, ...] = ()
    proposals: tuple[Proposal, ...] = ()
    narrations: dict[SeatId, tuple[EventId, str]] = Field(default_factory=dict)
    delivered: frozenset[SeatId] = frozenset()
    costs: tuple[CallCost, ...] = ()
    intel: tuple[d.Delta, ...] = ()
    """Goal pieces and decoys the engine applied this tick, for the narrators."""


def tail(events: Sequence[Event]) -> Tail:
    start = 0
    for i, e in enumerate(events):
        if e.kind == "tick_closed":
            start = i + 1
    tail = Tail()
    for e in events[start:]:
        p = e.payload
        match p:
            case ev.TickOpened():
                tail = tail.model_copy(update={"opened": True})
            case ev.TickStageCompleted():
                tail = tail.model_copy(
                    update={
                        "completed": (*tail.completed, p.stage),
                        "costs": (*tail.costs, *p.costs),
                    }
                )
            case ev.DeltaProposed():
                prop = Proposal(intent_id=p.intent_id, deltas=p.deltas)
                tail = tail.model_copy(update={"proposals": (*tail.proposals, prop)})
            case ev.DeltaApplied(delta=d.FactAssert(predicate=pred)) if pred in intel.PIECES:
                tail = tail.model_copy(update={"intel": (*tail.intel, p.delta)})
            case ev.NarrationWritten():
                narrations = dict(tail.narrations)
                narrations[p.seat_id] = (e.event_id, p.text)
                tail = tail.model_copy(update={"narrations": narrations})
            case ev.BriefingDelivered():
                tail = tail.model_copy(update={"delivered": tail.delivered | {p.seat_id}})
            case _:
                pass
    return tail


__all__ = ["Emitter", "Proposal", "Tail", "tail"]
