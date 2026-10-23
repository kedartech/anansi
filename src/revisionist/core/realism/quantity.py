"""Quantities: the numbers outcomes are computed from (pillar 6).

A quantity is truth. A seat sees one only when its scope says so (a declared
vote tally, say); otherwise it reaches people as documents that estimate it.
"""

from datetime import date
from typing import TYPE_CHECKING, Literal, Protocol, get_args

from revisionist.core._base import Label, Model
from revisionist.core.ids import EntityId, QuantityId
from revisionist.core.visibility import Scope

if TYPE_CHECKING:
    from revisionist.core.realism.pipeline import Order
    from revisionist.core.state import State

ResolverKey = Literal["votes", "shipping", "production", "opinion"]
"""The four resolvers the first scenario needs (plan, B3). A fifth needs Josh's word."""

RESOLVER_KEYS: tuple[ResolverKey, ...] = get_args(ResolverKey)


class Quantity(Model):
    quantity_id: QuantityId
    owner: EntityId | None = None
    name: Label
    value: int | None = None
    """None while the figure is an unsourced gap: nothing may be computed from it."""
    unit: Label
    as_of: date
    scope: Scope | None = None
    """None: engine only, which is the usual case. Seats learn it from documents."""


class QuantityChange(Model):
    """One typed change a resolver computed. Logged as ``QuantitySet``."""

    quantity_id: QuantityId
    value: int
    as_of: date


class QuantityResolver(Protocol):
    """A pure function from the truth and a settled order to typed changes.

    No model, no dice beyond seeded friction: the same state and order give
    the same changes on every replay.
    """

    key: ResolverKey

    def __call__(self, state: "State", order: "Order") -> tuple[QuantityChange, ...]: ...


__all__ = ["RESOLVER_KEYS", "Quantity", "QuantityChange", "QuantityResolver", "ResolverKey"]
