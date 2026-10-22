"""The character rule, in code (spec §5.5). Phase 2 unit.

A playable character controls a force, office or network asset of tier 2 or
more that bears on a contest. Money, information or facilities alone do not
qualify.
"""

from revisionist.core import contest
from revisionist.core.ids import ContestId, EntityId
from revisionist.core.ontology import AssetKind, AssetStatus, EntityKind
from revisionist.core.state import State

QUALIFYING_TIER = 2
QUALIFYING_KINDS = frozenset({AssetKind.FORCE, AssetKind.OFFICE, AssetKind.NETWORK})


def qualifies(state: State, entity: EntityId) -> bool:
    """True when ``entity`` meets the character rule in ``state``."""
    found = state.entities.get(entity)
    if found is None or found.kind is not EntityKind.PERSON:
        return False
    holds = any(
        a.controller == entity
        and a.kind in QUALIFYING_KINDS
        and a.tier >= QUALIFYING_TIER
        and a.status is not AssetStatus.LOST
        for a in state.assets.values()
    )
    if not holds:
        return False
    return not state.contests or bool(_bearing(state, entity))


def _bearing(state: State, entity: EntityId) -> tuple[ContestId, ...]:
    """Undecided contests ``entity`` stands in: through its ``parent`` chain, or
    by the side its roster entry declares (spec §5.5, "their side in each
    contest"). A governor or envoy under no side can still declare one."""
    found = set(contest.bearing(state, entity))
    listed = state.roster.get(entity)
    if listed is not None:
        for s in listed.sides:
            k = state.contests.get(s.contest_id)
            if k is not None and not k.decided and s.side in k.sides:
                found.add(s.contest_id)
    return tuple(sorted(found))


def nearest(state: State, entity: EntityId) -> EntityId | None:
    """The nearest qualified person to offer instead of one who fails the rule.

    Same parent first, then any shared contest; ties go by name. None when
    neither group has anyone.
    """
    people = _open(state, entity)
    found = state.entities.get(entity)
    if found is None:
        return min(people, key=lambda c: _order(state, c), default=None)
    same_parent = [
        c for c in people if found.parent is not None and state.entities[c].parent == found.parent
    ]
    mine = set(_bearing(state, entity))
    shared = [c for c in people if mine & set(_bearing(state, c))]
    for group in (same_parent, shared):
        if group:
            return min(group, key=lambda c: _order(state, c))
    return None


def problems(state: State) -> list[str]:
    """Why each roster character fails the rule, in name order; none without contests."""
    if not state.contests:
        return []
    failing = [c for c in state.roster if not qualifies(state, c)]
    return [
        f"character {state.entities[c].name}: controls no force, office or network "
        f"of tier {QUALIFYING_TIER}+ in a contest"
        for c in sorted(failing, key=lambda c: _order(state, c))
    ]


def _open(state: State, entity: EntityId) -> list[EntityId]:
    """Qualified, unclaimed roster people other than ``entity``."""
    return [
        c
        for c, ch in state.roster.items()
        if c != entity and ch.claimed_by is None and qualifies(state, c)
    ]


def _order(state: State, entity: EntityId) -> tuple[str, str]:
    return (state.entities[entity].name, entity)
