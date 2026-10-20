"""``project(state, seat) -> View``: the only read path for anything that writes prose.

README §7. An item is in a seat's view when its scope is visible to that
seat. Entities and seats have no scope and are always visible, but another
seat's private fields (objectives, standing orders) are stripped. Other
seats' pending intents are never in a view.
"""

from collections.abc import Mapping
from typing import Protocol

from wyrd.core.ids import SeatId
from wyrd.core.ontology import Seat
from wyrd.core.state import State, View
from wyrd.core.visibility import Scope


class HasScope(Protocol):
    @property
    def scope(self) -> Scope: ...


def _visible[K, S: HasScope](items: Mapping[K, S], seat: SeatId) -> dict[K, S]:
    return {k: v for k, v in items.items() if v.scope.visible_to(seat)}


def _redact(seat: Seat) -> Seat:
    """What other seats may know about a seat: everything but its private aims."""
    return seat.model_copy(update={"objectives": frozenset(), "standing_orders": ()})


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
        seats={k: (v if k == seat else _redact(v)) for k, v in state.seats.items()},
        own_pending=tuple(i for i in state.pending.values() if i.actor == seat),
    )
