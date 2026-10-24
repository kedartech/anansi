"""The resolvers: four for quantities (plan, Phase 2 unit B3) and movement for
features (Batch 6).

Each module has ``resolve(state, order)`` for an enacted order whose lever names
it, and ``on_day(state, today)`` for what moves with time. Both are pure and
seeded only by ``friction``, so a replay computes the same numbers. Another
resolver needs Josh's word.
"""

import hashlib
from collections.abc import Callable
from datetime import date

from revisionist.core import events as ev
from revisionist.core.ids import QuantityId
from revisionist.core.realism.geography import Feature
from revisionist.core.realism.pipeline import Order
from revisionist.core.realism.quantity import QuantityChange, ResolverKey
from revisionist.core.realism.resolvers import movement, opinion, production, shipping, votes
from revisionist.core.state import State
from revisionist.core.world_compile import object_id

Change = QuantityChange | Feature
"""A quantity's new value, or a feature as it now stands."""
OrderFn = Callable[[State, Order], tuple[Change, ...]]
DayFn = Callable[[State, date], tuple[Change, ...]]

ON_ORDER: dict[ResolverKey, OrderFn] = {
    "votes": votes.resolve,
    "shipping": shipping.resolve,
    "production": production.resolve,
    "opinion": opinion.resolve,
    "movement": movement.resolve,
}
ON_DAY: dict[ResolverKey, DayFn] = {
    "votes": votes.on_day,
    "shipping": shipping.on_day,
    "production": production.on_day,
    "opinion": opinion.on_day,
    "movement": movement.on_day,
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


def apply_changes(
    state: State, changes: tuple[Change, ...]
) -> list[ev.QuantitySet | ev.FeatureSet]:
    """The events for ``changes``: each quantity as it stands, with the new value and
    date, and each feature as given. Raises ``KeyError`` for a quantity the world
    never declared."""
    return [
        ev.FeatureSet(feature=c)
        if isinstance(c, Feature)
        else ev.QuantitySet(
            quantity=state.quantities[c.quantity_id].model_copy(
                update={"value": c.value, "as_of": c.as_of}
            )
        )
        for c in changes
    ]


__all__ = [
    "ON_DAY",
    "ON_ORDER",
    "Change",
    "apply_changes",
    "friction",
    "qid",
    "resolver_for",
    "settled_on",
    "value",
]
