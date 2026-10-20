"""Visibility and the knowledge ACL (README §7).

Every event, fact and intent carries a ``Scope``. ``project()`` (Layer 2)
includes an item in a seat's view only when ``scope.visible_to(seat)``.
"""

from enum import StrEnum

from pydantic import model_validator

from wyrd.core._base import Model
from wyrd.core.ids import SeatId


class Visibility(StrEnum):
    PUBLIC = "public"
    """The world sees it. ``known_by`` is ignored."""

    RESTRICTED = "restricted"
    """Named seats only (a cabinet, an alliance)."""

    PRIVATE = "private"
    """The actor only. Effects may still become visible."""

    COVERT = "covert"
    """Actively concealed. Carries exposure risk."""


class Scope(Model):
    visibility: Visibility
    known_by: frozenset[SeatId] = frozenset()

    @model_validator(mode="after")
    def _non_public_needs_a_holder(self) -> "Scope":
        if self.visibility is not Visibility.PUBLIC and not self.known_by:
            raise ValueError(f"{self.visibility.value} scope must name at least one seat")
        return self

    def visible_to(self, seat: SeatId) -> bool:
        return self.visibility is Visibility.PUBLIC or seat in self.known_by

    def grant(self, *seats: SeatId) -> "Scope":
        """Return a scope with ``seats`` added. Used by KnowledgeGrant and reveal().

        A public scope is already known by everyone, so a grant on it is a no-op
        and ``known_by`` stays empty.
        """
        if self.visibility is Visibility.PUBLIC:
            return self
        return self.model_copy(update={"known_by": self.known_by | frozenset(seats)})


PUBLIC = Scope(visibility=Visibility.PUBLIC)


def private_to(seat: SeatId, *, covert: bool = False) -> Scope:
    kind = Visibility.COVERT if covert else Visibility.PRIVATE
    return Scope(visibility=kind, known_by=frozenset({seat}))
