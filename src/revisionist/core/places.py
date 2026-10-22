"""Places on demand (#29). Phase 2 unit.

When an order names a real place not yet in the world, a checked step adds it
as a PLACE entity before the referee resolves the order.
"""

from revisionist.core.ids import EntityId
from revisionist.core.ontology import Entity
from revisionist.core.state import State


def missing(state: State, names: tuple[str, ...]) -> tuple[str, ...]:
    """The place names in an order that the world does not hold yet."""
    raise NotImplementedError("Session Zero v3, Phase 2: places on demand")


def admit(state: State, name: str, polity: EntityId | None) -> Entity:
    """A LOCATION entity for ``name`` under ``polity``, with an ID derived from the
    campaign and the name. Raises ValueError if the world already has that place."""
    raise NotImplementedError("Session Zero v3, Phase 2: places on demand")
