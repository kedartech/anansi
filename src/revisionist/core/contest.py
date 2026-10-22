"""Contests: the pressures that keep a world moving (spec §5.3). Phase 2 unit.

Pure functions over ``Contest``; the tick emits their events.
"""

from revisionist.core import events as ev
from revisionist.core.ids import ContestId, EntityId
from revisionist.core.ontology import Contest
from revisionist.core.state import State


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


def bearing(state: State, entity: EntityId) -> tuple[ContestId, ...]:
    """The contests an entity's assets bear on, for the odds edge and the roster."""
    raise NotImplementedError("Session Zero v3, Phase 2: contests")
