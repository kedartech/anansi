"""The character rule, in code (spec §5.5). Phase 2 unit.

A playable character controls a force, office or network asset of tier 2 or
more that bears on a contest. Money, information or facilities alone do not
qualify.
"""

from revisionist.core.ids import EntityId
from revisionist.core.state import State

QUALIFYING_TIER = 2


def qualifies(state: State, entity: EntityId) -> bool:
    """True when ``entity`` meets the character rule in ``state``."""
    raise NotImplementedError("Session Zero v3, Phase 2: character rule")


def nearest(state: State, entity: EntityId) -> EntityId | None:
    """The nearest qualified person to offer instead of one who fails the rule."""
    raise NotImplementedError("Session Zero v3, Phase 2: character rule")
