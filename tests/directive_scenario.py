"""The directives the directive recording was built from: Roosevelt, after the counsel
scenario's two offline ticks, writes to five people on his card."""

from collections.abc import Callable

from revisionist.core.ids import EntityId
from revisionist.core.realism.card import ChannelKind
from revisionist.engine.store import EventStore, load_state
from tests.test_realism_tick import CAMPAIGN


def person(name: str) -> Callable[[EventStore], EntityId]:
    def find(store: EventStore) -> EntityId:
        state = load_state(store, CAMPAIGN)
        return next(e for e, x in state.entities.items() if x.name == name)

    return find


CHURCHILL = person("Winston Churchill")
KNOX = person("Frank Knox")
RAYBURN = person("Sam Rayburn")
HOPKINS = person("Harry L. Hopkins")
HULL = person("Cordell Hull")

CABLE = "Tell the Prime Minister our destroyers will take the convoys as far as Iceland."
SHOOT = "Have the Navy shoot on sight along the Iceland route."
WAR = "Declare war on Germany."
EVERYTHING = "Give the Russians everything we have."
CANAL = "Close the Panama Canal."

ASKED: tuple[tuple[Callable[[EventStore], EntityId], ChannelKind, str], ...] = (
    (CHURCHILL, ChannelKind.CABLE, CABLE),
    (KNOX, ChannelKind.LETTER, SHOOT),
    (RAYBURN, ChannelKind.TELEPHONE, WAR),
    (HOPKINS, ChannelKind.IN_PERSON, EVERYTHING),
    (HULL, ChannelKind.LETTER, CANAL),
)
