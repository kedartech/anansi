"""The four quantity resolvers (plan, Phase 2 unit B3).

Each module has ``resolve(state, order)`` for an enacted order whose lever names
it, and ``on_day(state, today)`` for what moves with time. Both are pure and
seeded only by ``friction``, so a replay computes the same numbers. A fifth
resolver needs Josh's word.
"""

import hashlib
from collections.abc import Callable
from datetime import date

from revisionist.core import events as ev
from revisionist.core.ids import QuantityId
from revisionist.core.realism.pipeline import Order
from revisionist.core.realism.quantity import QuantityChange, ResolverKey
from revisionist.core.realism.resolvers import opinion, production, shipping, votes
from revisionist.core.state import State
from revisionist.core.world_compile import object_id

OrderFn = Callable[[State, Order], tuple[QuantityChange, ...]]
DayFn = Callable[[State, date], tuple[QuantityChange, ...]]

ON_ORDER: dict[ResolverKey, OrderFn] = {
    "votes": votes.resolve,
    "shipping": shipping.resolve,
    "production": production.resolve,
    "opinion": opinion.resolve,
}
ON_DAY: dict[ResolverKey, DayFn] = {
    "votes": votes.on_day,
    "shipping": shipping.on_day,
    "production": production.on_day,
    "opinion": opinion.on_day,
}


def qid(state: State, key: str) -> QuantityId:
    """The id a world-file quantity key compiles to in this campaign."""
    return QuantityId(object_id(state.campaign_id, "quantity", key))


def value(state: State, key: str) -> int | None:
    """A quantity's current value by world-file key; None if absent or a gap."""
    q = state.quantities.get(qid(state, key))
    return None if q is None else q.value


def resolver_for(state: State, order: Order) -> ResolverKey:
    """The resolver the issuer's card names for the order's lever."""
    seat = state.seats[order.issuer]
    card = state.cards[seat.embodies]
    for lever in card.levers:
        if lever.name == order.lever:
            return lever.resolver
    raise KeyError(f"order {order.order_id}: lever {order.lever} is not on the issuer's card")


def settled_on(order: Order) -> date:
    """The day the order left its last step (its filing day if it had none)."""
    return order.outcomes[-1].on if order.outcomes else order.filed


def friction(state: State, *parts: object, n: int) -> int:
    """A deterministic integer in [0, n) from the campaign and ``parts``: the only
    randomness a resolver may use."""
    key = "\x1f".join([str(state.campaign_id), *map(str, parts)])
    return int.from_bytes(hashlib.blake2b(key.encode(), digest_size=8).digest(), "big") % n


def apply_changes(state: State, changes: tuple[QuantityChange, ...]) -> list[ev.QuantitySet]:
    """The ``QuantitySet`` events for ``changes``: each quantity as it stands, with
    the new value and date. Raises ``KeyError`` for a quantity the world never declared."""
    return [
        ev.QuantitySet(
            quantity=state.quantities[c.quantity_id].model_copy(
                update={"value": c.value, "as_of": c.as_of}
            )
        )
        for c in changes
    ]


__all__ = [
    "ON_DAY",
    "ON_ORDER",
    "apply_changes",
    "friction",
    "qid",
    "resolver_for",
    "settled_on",
    "value",
]
