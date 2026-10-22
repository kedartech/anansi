"""Contests: the pressures that keep a world moving (spec §5.3). Phase 2 unit.

Pure functions over ``Contest``; the tick emits their events.
"""

from revisionist.core import events as ev
from revisionist.core.ids import ContestId, EntityId
from revisionist.core.intent import Intent
from revisionist.core.ontology import Contest
from revisionist.core.state import State, View


def momentum_moves(state: State, days: int) -> list[ev.ContestMoved]:
    """One ``ContestMoved`` per undecided contest: its momentum, scaled to the
    in-world ``days`` the tick covers, and its countdown run down by ``days``."""
    raise NotImplementedError("Session Zero v3, Phase 2: contests")


def decisions(state: State) -> list[ev.ContestDecided]:
    """A ``ContestDecided`` for every undecided contest whose countdown is zero."""
    raise NotImplementedError("Session Zero v3, Phase 2: contests")


def leader(contest: Contest) -> EntityId | None:
    """The side the track favours now; None when level or a trial not yet passed."""
    raise NotImplementedError("Session Zero v3, Phase 2: contests")


def side_of(world: State | View, entity: EntityId, contest: Contest) -> EntityId | None:
    """The side of ``contest`` that ``entity`` stands on: itself, or the nearest
    entity up its ``parent`` chain that is a side. None when it stands on none."""
    seen: set[EntityId] = set()
    at: EntityId | None = entity
    while at is not None and at not in seen:
        if at in contest.sides:
            return at
        seen.add(at)
        found = world.entities.get(at)
        at = found.parent if found is not None else None
    return None


def bearing(world: State | View, entity: EntityId) -> tuple[ContestId, ...]:
    """The undecided contests ``entity`` stands in (``side_of`` is not None), in
    ID order. Used by the odds edge, the character rule and the goal rule."""
    return tuple(
        k.contest_id
        for k in sorted(world.contests.values(), key=lambda k: k.contest_id)
        if not k.decided and side_of(world, entity, k) is not None
    )


def edge(world: State | View, intent: Intent) -> int:
    """-2..+2 feasibility steps for an order that bears on a contest: strength
    against strength on that contest (spec §5.3, §13). 0 when it bears on none."""
    raise NotImplementedError("Session Zero v3, Phase 2: contests")
