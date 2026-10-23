"""``project(state, seat) -> View``: the only read path for anything that writes prose.

spec §7. An item is in a seat's view when its scope is visible to that
seat. Entities have no scope and are always visible. Every seat appears only
as a ``SeatFace`` (spec §17: how a seat is run is never shown); the viewer
also gets its own seat in full as ``View.me``. Other seats' pending intents
are never in a view.

Realism adds three rules. A document is in view once its scope admits the seat
*and* it has arrived (``arrives <= in_world_date``); only its ``DocumentView``
half is copied. A seat sees its own orders only. Cards show as faces, with the
viewer's own person as a desk; AI policy fields never leave the state.
"""

from collections.abc import Mapping
from typing import Protocol

from revisionist.core.ids import DocumentId, SeatId
from revisionist.core.ontology import Seat
from revisionist.core.realism.document import DocumentView
from revisionist.core.state import SeatFace, State, View
from revisionist.core.visibility import Scope


class HasScope(Protocol):
    @property
    def scope(self) -> Scope: ...


def _visible[K, S: HasScope](items: Mapping[K, S], seat: SeatId) -> dict[K, S]:
    return {k: v for k, v in items.items() if v.scope.visible_to(seat)}


def face(seat: Seat) -> SeatFace:
    return SeatFace(seat_id=seat.seat_id, kind=seat.kind, name=seat.name, embodies=seat.embodies)


def _arrived(state: State, seat: SeatId) -> dict[DocumentId, DocumentView]:
    today = state.in_world_date
    if today is None:
        return {}
    return {
        k: d.view
        for k, d in state.documents.items()
        if d.view.scope.visible_to(seat) and d.view.arrives <= today
    }


def project(state: State, seat: SeatId) -> View:
    if seat not in state.seats:
        raise KeyError(f"unknown seat {seat!r}")
    me = state.seats[seat]
    card = state.cards.get(me.embodies)
    return View(
        viewer=seat,
        campaign_id=state.campaign_id,
        tick=state.tick,
        settings=state.settings,
        entities=dict(state.entities),
        assets=_visible(state.assets, seat),
        relationships=_visible(state.relationships, seat),
        meters=_visible(state.meters, seat),
        clocks=_visible(state.clocks, seat),
        facts=_visible(state.facts, seat),
        scheduled=_visible(state.scheduled, seat),
        commitments=_visible(state.commitments, seat),
        goals=_visible(state.goals, seat),
        phase=state.phase,
        roster=dict(state.roster),
        contests=_visible(state.contests, seat),
        divergence=state.divergence,
        in_world_date=state.in_world_date,
        ruleset=state.ruleset,
        me=me,
        seats={k: face(v) for k, v in state.seats.items()},
        own_pending=tuple(i for i in state.pending.values() if i.actor == seat),
        cards={k: c.face() for k, c in state.cards.items()},
        desk=card.desk() if card is not None else None,
        documents=_arrived(state, seat),
        orders={k: o for k, o in state.orders.items() if o.issuer == seat},
        quantities={
            k: q
            for k, q in state.quantities.items()
            if q.scope is not None and q.scope.visible_to(seat)
        },
    )
