"""The public world state after a tick (playtest #31). Pure.

``DeltaApplied`` events are logged with public event scope whatever they touch,
so the event's scope is not enough: a change counts only when the object it
touches is public in ``state``. Nothing private, and no roll or order, is read.
"""

import re
from collections import defaultdict
from collections.abc import Sequence

from revisionist.core import delta as d
from revisionist.core.events import DeltaApplied, Event
from revisionist.core.ids import (
    ULID_PATTERN,
    AssetId,
    ClockId,
    EntityId,
    FactId,
    MeterId,
    RelationshipId,
)
from revisionist.core.ontology import EntityStatus
from revisionist.core.referee import touched
from revisionist.core.state import State
from revisionist.core.visibility import Visibility


def _scoped(state: State, obj: str) -> object | None:
    return (
        state.assets.get(AssetId(obj))
        or state.meters.get(MeterId(obj))
        or state.clocks.get(ClockId(obj))
        or state.relationships.get(RelationshipId(obj))
        or state.facts.get(FactId(obj))
    )


def _public(state: State, obj: str) -> bool:
    if EntityId(obj) in state.entities:
        return True
    item = _scoped(state, obj)
    return item is not None and item.scope.visibility is Visibility.PUBLIC  # type: ignore[attr-defined]


def _object(state: State, obj: str) -> str | None:
    """A fact's object as words: a name if it is a public thing, free text as given.

    ``None`` for an id that names nothing public, so no raw id reaches the channel.
    """
    if EntityId(obj) in state.entities:
        return state.entities[EntityId(obj)].name
    if _scoped(state, obj) is not None:
        return _name(state, obj) if _public(state, obj) else None
    return None if re.fullmatch(ULID_PATTERN, obj) else obj


def _name(state: State, obj: str) -> str:
    entity = state.entities.get(EntityId(obj))
    if entity is not None:
        return entity.name
    asset = state.assets.get(AssetId(obj))
    if asset is not None:
        return asset.name
    meter = state.meters.get(MeterId(obj))
    if meter is not None:
        return meter.name
    clock = state.clocks.get(ClockId(obj))
    if clock is not None:
        return clock.name
    return "someone unknown"


def world_state(state: State, events: Sequence[Event], tick: int) -> list[str]:
    """Plain lines for the public channel. A line ending in ``:`` is a header."""
    made_public: set[str] = set()
    changes: list[d.Delta] = []
    for e in events:
        if e.tick != tick or not isinstance(e.payload, DeltaApplied):
            continue
        if e.scope.visibility is not Visibility.PUBLIC:
            continue
        delta = e.payload.delta
        if isinstance(delta, d.KnowledgeGrant) and delta.make_public:
            made_public.add(delta.target)
        obj = touched(delta)
        if _public(state, obj) or obj in made_public:
            changes.append(delta)

    sections: list[tuple[str, list[str]]] = []

    hands = [
        f"{_name(state, c.asset_id)} is now held by {_name(state, c.controller)}"
        for c in changes
        if isinstance(c, d.AssetControllerSet)
    ]
    sections.append(("Changed hands:", hands))

    fates = [
        f"{_name(state, c.entity_id)} is {c.status.value}"
        for c in changes
        if isinstance(c, d.EntityStatusSet) and c.status is not EntityStatus.ACTIVE
    ]
    sections.append(("Fates:", fates))

    summed: dict[str, int] = defaultdict(int)
    for c in changes:
        if isinstance(c, d.MeterDelta):
            summed[c.meter_id] += c.change
    standing = [
        f"{_name(state, meter.owner)} {meter.name}: {meter.value} ({change:+d})"
        for m, change in sorted(summed.items())
        if change and (meter := state.meters.get(MeterId(m))) is not None
    ]
    sections.append(("Standing:", standing))

    forces = [
        f"{_name(state, c.asset_id)}: tier {state.assets[c.asset_id].tier}"
        if isinstance(c, d.AssetTierDelta)
        else f"{_name(state, c.asset_id)} is {c.status.value}"
        for c in changes
        if isinstance(c, (d.AssetTierDelta, d.AssetStatusSet)) and c.asset_id in state.assets
    ]
    sections.append(("Forces:", forces))

    relations = [
        f"{_name(state, state.relationships[c.relationship_id].a)} and "
        f"{_name(state, state.relationships[c.relationship_id].b)}: "
        f"{state.relationships[c.relationship_id].disposition:+d} ({c.change:+d})"
        for c in changes
        if isinstance(c, d.DispositionDelta) and c.relationship_id in state.relationships
    ]
    sections.append(("Relations:", relations))

    known: list[str] = []
    for c in changes:
        if isinstance(c, d.FactAssert) and c.scope.visibility is Visibility.PUBLIC:
            thing = _object(state, str(c.obj))
            if thing is not None:
                known.append(f"{_name(state, c.subject)} {c.predicate} {thing}")
        granted = (
            state.facts.get(FactId(str(c.target))) if isinstance(c, d.KnowledgeGrant) else None
        )
        if isinstance(c, d.KnowledgeGrant) and c.make_public and granted is not None:
            told = _object(state, str(granted.obj))
            if told is not None:
                known.append(f"{_name(state, granted.subject)} {granted.predicate} {told}")
    sections.append(("Now public:", known))

    clocks = []
    for c in changes:
        if isinstance(c, d.ClockAdvance) and c.clock_id in state.clocks:
            clock = state.clocks[c.clock_id]
            if clock.status == "open":
                clocks.append(f"{clock.name}: {clock.segments_filled} of {clock.segments_total}")
    sections.append(("Clocks:", clocks))

    lines = [f"The world after tick {tick}:"]
    for header, items in sections:
        if items:
            lines.append(header)
            lines += items
    if len(lines) == 1:
        lines.append("No public change this tick.")
    return lines
