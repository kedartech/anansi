"""Places on demand (#29). Phase 2 unit.

When an order names a real place not yet in the world, a checked step adds it
as a PLACE entity before the referee resolves the order.
"""

from revisionist.core.ids import EntityId, derived_id
from revisionist.core.ontology import Entity, EntityKind
from revisionist.core.state import State


def missing(state: State, names: tuple[str, ...]) -> tuple[str, ...]:
    """The place names in an order that the world does not hold yet."""
    held = {e.name.casefold() for e in state.entities.values() if e.kind is EntityKind.LOCATION}
    out: dict[str, str] = {}
    for raw in names:
        name = raw.strip()
        if name and name.casefold() not in held:
            out.setdefault(name.casefold(), name)
    return tuple(out.values())


def admit(state: State, name: str, polity: EntityId | None) -> Entity:
    """A LOCATION entity for ``name`` under ``polity``, with an ID derived from the
    campaign and the name. Raises ValueError if the world already has that place."""
    entity_id = EntityId(derived_id(state.campaign_id, "place", name.casefold()))
    if (
        any(
            e.kind is EntityKind.LOCATION and e.name.casefold() == name.casefold()
            for e in state.entities.values()
        )
        or entity_id in state.entities
    ):
        raise ValueError(f"the world already holds {name}")
    if polity is not None and (
        polity not in state.entities
        or state.entities[polity].kind not in (EntityKind.POLITY, EntityKind.FACTION)
    ):
        raise ValueError(f"{polity} is not a polity or faction in this world")
    return Entity(entity_id=entity_id, kind=EntityKind.LOCATION, name=name, parent=polity)
