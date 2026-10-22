"""Places on demand (#29). Phase 2 unit.

When an order names a real place not yet in the world, a checked step adds it
as a PLACE entity before the referee resolves the order.
"""

from revisionist.core.ontology import Entity
from revisionist.core.state import State


def missing(state: State, names: tuple[str, ...]) -> tuple[str, ...]:
    """The place names in an order that the world does not hold yet."""
    raise NotImplementedError("Session Zero v3, Phase 2: places on demand")


def admit(state: State, name: str, region: str) -> Entity:
    """A public PLACE entity for ``name``, checked against the world's places."""
    raise NotImplementedError("Session Zero v3, Phase 2: places on demand")
