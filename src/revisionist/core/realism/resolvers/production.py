"""The production resolver (plan, Phase 2 unit B3).

Lend-Lease findings and allocations, deliveries after a lead time. The
sector-priority levers (Churchill, Stalin, Hitler, Konoe, Tojo, Mussolini, Chiang)
and Stalin's ``aid_request`` change nothing in this wave: no quantity is declared
for them yet.
"""

from datetime import date

from revisionist.core.realism import resolvers as rs
from revisionist.core.realism.pipeline import Order, OrderStatus
from revisionist.core.realism.quantity import QuantityChange
from revisionist.core.realism.resolvers.votes import put
from revisionist.core.state import State


def resolve(state: State, order: Order) -> tuple[QuantityChange, ...]:
    """Changes from one order enacted through its pipeline."""
    recipient = order.params.get("recipient")
    if order.status is not OrderStatus.ENACTED or order.lever != "lend_lease_finding":
        return ()
    as_of = rs.settled_on(order)
    out = []
    if recipient == "ussr":
        out.append(put(state, "soviet_lend_lease_eligible", 1, as_of))
    total, share = rs.value(state, "lend_lease_second_appropriation"), order.params.get("share_pct")
    if total is not None and isinstance(share, int):
        out.append(put(state, f"lend_lease_share_{recipient}", total * share // 100, as_of))
    return tuple(c for c in out if c is not None)


def on_day(state: State, today: date) -> tuple[QuantityChange, ...]:
    """Nothing in production moves with time alone yet."""
    return ()
