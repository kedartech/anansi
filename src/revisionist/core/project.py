"""``project(state, seat) -> View``: the only read path for anything that writes prose.

spec §7. An item is in a seat's view when its scope is visible to that
seat. Entities have no scope and are always visible. Every seat appears only
as a ``SeatFace`` (spec §17: how a seat is run is never shown); the viewer
also gets its own seat in full as ``View.me``. Other seats' pending intents
are never in a view.
"""

from collections.abc import Mapping
from typing import Protocol

from revisionist.core.ids import SeatId
from revisionist.core.ontology import Seat
from revisionist.core.state import SeatFace, State, View
from revisionist.core.visibility import Scope


class HasScope(Protocol):
    @property
    def scope(self) -> Scope: ...


def _visible[K, S: HasScope](items: Mapping[K, S], seat: SeatId) -> dict[K, S]:
    return {k: v for k, v in items.items() if v.scope.visible_to(seat)}


def face(seat: Seat) -> SeatFace:
    return SeatFace(seat_id=seat.seat_id, kind=seat.kind, name=seat.name, embodies=seat.embodies)


def project(state: State, seat: SeatId) -> View:
    if seat not in state.seats:
        raise KeyError(f"unknown seat {seat!r}")
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
        me=state.seats[seat],
        seats={k: face(v) for k, v in state.seats.items()},
        own_pending=tuple(i for i in state.pending.values() if i.actor == seat),
    )
